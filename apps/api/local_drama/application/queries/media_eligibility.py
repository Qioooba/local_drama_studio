"""Shared current-media eligibility queries for production and automation.

History is immutable, but production completion is about a candidate that still
matches the current shot lineage and the checker policy that applies to it.
This module deliberately owns the storage-level half of that decision; the
episode production read model adds dynamic prompt/profile/reference freshness.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any, Iterable


def current_video_qc_policy(stage: str) -> str:
    return "g6_formal_video_qc_v1" if stage == "FORMAL" else "g4_media_qc_v1"


def eligible_shot_media(
    connection: sqlite3.Connection,
    shot_ids: Iterable[str],
) -> list[dict[str, Any]]:
    ids = list(dict.fromkeys(str(value) for value in shot_ids if str(value)))
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    rows = connection.execute(
        f"""WITH candidates AS (
          SELECT gi.owner_id AS shot_id,mv.id AS media_version_id,mv.media_asset_id,
          ma.media_kind,mv.stage,mv.integrity_status,ma.selected_version_id,ma.approved_version_id,
          gv.id AS variant_id,gv.is_stale,mv.created_at
          FROM generation_intents gi JOIN generation_variants gv ON gv.intent_id=gi.id
          JOIN media_assets ma ON ma.owner_type='GENERATION_VARIANT' AND ma.owner_id=gv.id
          JOIN media_versions mv ON mv.media_asset_id=ma.id
          WHERE gi.owner_type='SHOT' AND gi.owner_id IN ({marks})
          UNION ALL
          SELECT ma.owner_id,mv.id,mv.media_asset_id,ma.media_kind,mv.stage,mv.integrity_status,
          ma.selected_version_id,ma.approved_version_id,NULL,0,mv.created_at
          FROM media_assets ma JOIN media_versions mv ON mv.media_asset_id=ma.id
          WHERE ma.owner_type='SHOT' AND ma.owner_id IN ({marks})
        )
        SELECT c.*,
        CASE WHEN c.media_kind='IMAGE' AND c.stage='KEYFRAME' THEN 'KEYFRAME'
             WHEN c.media_kind='VIDEO' AND c.stage IN ('PROXY','FORMAL') THEN 'VIDEO' END AS slot_type,
        CASE WHEN c.media_kind='VIDEO' THEN
          (SELECT mc.status FROM machine_check_runs mc
           WHERE mc.subject_type='MEDIA_VERSION' AND mc.subject_id=c.media_version_id
             AND mc.policy_version=CASE WHEN c.stage='FORMAL'
               THEN 'g6_formal_video_qc_v1' ELSE 'g4_media_qc_v1' END
           ORDER BY mc.created_at DESC,mc.id DESC LIMIT 1)
        END AS current_qc_status
        FROM candidates c
        WHERE c.integrity_status='VERIFIED' AND c.is_stale=0
          AND ((c.media_kind='IMAGE' AND c.stage='KEYFRAME')
            OR (c.media_kind='VIDEO' AND c.stage IN ('PROXY','FORMAL')))""",
        (*ids, *ids),
    ).fetchall()
    return [dict(row) for row in rows]


def eligible_candidate_counts(
    connection: sqlite3.Connection,
    shot_ids: Iterable[str],
) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for row in eligible_shot_media(connection, shot_ids):
        counts[(str(row["shot_id"]), str(row["slot_type"]))] += 1
    return dict(counts)


def best_current_video(connection: sqlite3.Connection, shot_id: str) -> dict[str, Any] | None:
    candidates = [
        row for row in eligible_shot_media(connection, [shot_id])
        if row["slot_type"] == "VIDEO"
    ]
    candidates.sort(
        key=lambda row: (
            0 if str(row.get("approved_version_id") or "") == str(row["media_version_id"])
            else 1 if str(row.get("selected_version_id") or "") == str(row["media_version_id"])
            else 2,
            0 if row.get("current_qc_status") == "PASS"
            else 1 if row.get("current_qc_status") is None
            else 2,
            str(row.get("created_at") or ""),
            str(row["media_version_id"]),
        ),
        reverse=False,
    )
    if not candidates:
        return None
    # Authority/QC ranks are ascending, while recency within a rank is
    # descending. Keep the policy explicit rather than relying on SQL aliases.
    best_rank = (
        0 if str(candidates[0].get("approved_version_id") or "") == str(candidates[0]["media_version_id"])
        else 1 if str(candidates[0].get("selected_version_id") or "") == str(candidates[0]["media_version_id"])
        else 2,
        0 if candidates[0].get("current_qc_status") == "PASS"
        else 1 if candidates[0].get("current_qc_status") is None
        else 2,
    )
    same_rank = [
        row for row in candidates
        if (
            0 if str(row.get("approved_version_id") or "") == str(row["media_version_id"])
            else 1 if str(row.get("selected_version_id") or "") == str(row["media_version_id"])
            else 2,
            0 if row.get("current_qc_status") == "PASS"
            else 1 if row.get("current_qc_status") is None
            else 2,
        ) == best_rank
    ]
    return max(same_rank, key=lambda row: (str(row.get("created_at") or ""), str(row["media_version_id"])))
