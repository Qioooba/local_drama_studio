"""Read-only integrity audit for the LocalDramaStudio V2 fact chain.

The audit is intentionally usable both before and after additive migrations.
Checks whose tables/columns are not present at the current Alembic revision are
reported as ``NOT_APPLICABLE`` instead of making an old-project preflight fail.
No repair mode is provided here: release repairs must be explicit, reviewed
commands rather than a side effect of an audit.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / "data" / "local_drama.sqlite3"


@dataclass(frozen=True)
class Invariant:
    code: str
    description: str
    sql: str
    required: tuple[tuple[str, str | None], ...]


INVARIANTS = (
    Invariant(
        "SHOT_CURRENT_REVISION",
        "current shot revision exists and belongs to the same shot",
        """SELECT s.id AS shot_id, s.current_revision_id
        FROM shots s LEFT JOIN shot_revisions r ON r.id=s.current_revision_id
        WHERE s.current_revision_id IS NOT NULL
          AND (r.id IS NULL OR r.shot_id<>s.id)""",
        (("shots", "current_revision_id"), ("shot_revisions", "shot_id")),
    ),
    Invariant(
        "VARIANT_PARENT_LINEAGE",
        "generation variant parent exists and belongs to the same intent",
        """SELECT child.id AS variant_id, child.intent_id, child.parent_variant_id,
          parent.intent_id AS parent_intent_id
        FROM generation_variants child
        LEFT JOIN generation_variants parent ON parent.id=child.parent_variant_id
        WHERE child.parent_variant_id IS NOT NULL
          AND (parent.id IS NULL OR parent.intent_id<>child.intent_id)""",
        (("generation_variants", "parent_variant_id"),),
    ),
    Invariant(
        "VARIANT_INPUT_MEDIA",
        "variant input bindings reference existing variants and media versions",
        """SELECT b.id AS binding_id, b.variant_id, b.media_version_id
        FROM variant_input_bindings b
        LEFT JOIN generation_variants v ON v.id=b.variant_id
        LEFT JOIN media_versions mv ON mv.id=b.media_version_id
        WHERE v.id IS NULL OR mv.id IS NULL""",
        (("variant_input_bindings", "media_version_id"), ("generation_variants", "id"), ("media_versions", "id")),
    ),
    Invariant(
        "ASSET_REFERENCE_PROJECT",
        "asset references, assets, states, and media belong to one project",
        """SELECT r.id AS reference_id, r.project_id AS reference_project_id,
          a.project_id AS asset_project_id, st.project_id AS state_project_id,
          ma.project_id AS media_project_id
        FROM story_asset_references r
        LEFT JOIN story_assets a ON a.id=r.story_asset_id
        LEFT JOIN story_asset_states st ON st.id=r.asset_state_id
        LEFT JOIN media_versions mv ON mv.id=r.media_version_id
        LEFT JOIN media_assets ma ON ma.id=mv.media_asset_id
        WHERE a.id IS NULL OR mv.id IS NULL OR ma.id IS NULL
          OR r.project_id<>a.project_id OR r.project_id<>ma.project_id
          OR (r.asset_state_id IS NOT NULL AND
              (st.id IS NULL OR st.story_asset_id<>r.story_asset_id OR st.project_id<>r.project_id))""",
        (("story_asset_references", "asset_state_id"), ("story_asset_states", "story_asset_id"), ("story_assets", "project_id"), ("media_versions", "media_asset_id"), ("media_assets", "project_id")),
    ),
    Invariant(
        "ASSET_STATE_SCOPE",
        "story asset states belong to their declared asset and project",
        """SELECT st.id AS state_id, st.story_asset_id, st.project_id,
          a.project_id AS asset_project_id
        FROM story_asset_states st LEFT JOIN story_assets a ON a.id=st.story_asset_id
        WHERE a.id IS NULL OR st.project_id<>a.project_id""",
        (("story_asset_states", "story_asset_id"), ("story_assets", "project_id")),
    ),
    Invariant(
        "EPISODE_ASSET_STATE_SCOPE",
        "episode asset state binding agrees on asset and project",
        """SELECT b.id AS binding_id, b.episode_id, b.story_asset_id, b.asset_state_id
        FROM episode_asset_state_bindings b
        LEFT JOIN story_assets a ON a.id=b.story_asset_id
        LEFT JOIN story_asset_states st ON st.id=b.asset_state_id
        LEFT JOIN episodes e ON e.id=b.episode_id
        LEFT JOIN seasons se ON se.id=e.season_id
        WHERE a.id IS NULL OR st.id IS NULL OR e.id IS NULL OR se.id IS NULL
          OR st.story_asset_id<>b.story_asset_id
          OR a.project_id<>se.project_id OR st.project_id<>a.project_id""",
        (("episode_asset_state_bindings", "asset_state_id"), ("story_asset_states", "story_asset_id"), ("episodes", "season_id"), ("seasons", "project_id")),
    ),
    Invariant(
        "SHOT_ASSET_STATE_SCOPE",
        "shot asset state agrees with the bound asset and shot project",
        """SELECT b.id AS binding_id, b.shot_id, b.asset_id, b.asset_state_id
        FROM shot_asset_bindings b
        LEFT JOIN story_assets a ON a.id=b.asset_id
        LEFT JOIN story_asset_states st ON st.id=b.asset_state_id
        LEFT JOIN shots sh ON sh.id=b.shot_id
        LEFT JOIN episodes e ON e.id=sh.episode_id
        LEFT JOIN seasons se ON se.id=e.season_id
        WHERE a.id IS NULL OR sh.id IS NULL OR e.id IS NULL OR se.id IS NULL
          OR a.project_id<>se.project_id
          OR (b.asset_state_id IS NOT NULL AND
              (st.id IS NULL OR st.story_asset_id<>b.asset_id OR st.project_id<>a.project_id))""",
        (("shot_asset_bindings", "asset_state_id"), ("story_asset_states", "story_asset_id"), ("shots", "episode_id"), ("episodes", "season_id"), ("seasons", "project_id")),
    ),
    Invariant(
        "CANONICAL_HERO_PROJECTION",
        "active canonical media is represented by exactly one matching active base HERO",
        """SELECT a.id AS asset_id, a.canonical_media_version_id,
          SUM(CASE WHEN r.status='ACTIVE' AND r.reference_kind='HERO'
                    AND r.asset_state_id IS NULL
                    AND r.media_version_id=a.canonical_media_version_id THEN 1 ELSE 0 END) AS matching_heroes,
          SUM(CASE WHEN r.status='ACTIVE' AND r.reference_kind='HERO'
                    AND r.asset_state_id IS NULL THEN 1 ELSE 0 END) AS active_base_heroes
        FROM story_assets a
        LEFT JOIN story_asset_references r ON r.story_asset_id=a.id
        WHERE a.status='ACTIVE' AND a.canonical_media_version_id IS NOT NULL
        GROUP BY a.id, a.canonical_media_version_id
        HAVING matching_heroes<>1 OR active_base_heroes<>1""",
        (("story_assets", "canonical_media_version_id"), ("story_asset_references", "reference_kind")),
    ),
    Invariant(
        "MEDIA_SELECTION_LINEAGE",
        "selection and media asset current pointers reference versions of that asset",
        """SELECT 'selection' AS source, s.id AS source_id, s.media_asset_id, s.media_version_id
        FROM selections s LEFT JOIN media_versions mv ON mv.id=s.media_version_id
        WHERE mv.id IS NULL OR mv.media_asset_id<>s.media_asset_id
        UNION ALL
        SELECT 'selected_pointer', ma.id, ma.id, ma.selected_version_id
        FROM media_assets ma LEFT JOIN media_versions mv ON mv.id=ma.selected_version_id
        WHERE ma.selected_version_id IS NOT NULL
          AND (mv.id IS NULL OR mv.media_asset_id<>ma.id)
        UNION ALL
        SELECT 'approved_pointer', ma.id, ma.id, ma.approved_version_id
        FROM media_assets ma LEFT JOIN media_versions mv ON mv.id=ma.approved_version_id
        WHERE ma.approved_version_id IS NOT NULL
          AND (mv.id IS NULL OR mv.media_asset_id<>ma.id)""",
        (("selections", "media_version_id"), ("media_assets", "selected_version_id"), ("media_assets", "approved_version_id"), ("media_versions", "media_asset_id")),
    ),
    Invariant(
        "FRAME_ANCHOR_MEDIA",
        "frame anchor source and extracted media versions exist in one project",
        """SELECT f.id AS anchor_id, f.source_media_version_id, f.extracted_media_version_id,
          source_asset.project_id AS source_project_id,
          extracted_asset.project_id AS extracted_project_id
        FROM frame_anchors f
        LEFT JOIN media_versions source ON source.id=f.source_media_version_id
        LEFT JOIN media_assets source_asset ON source_asset.id=source.media_asset_id
        LEFT JOIN media_versions extracted ON extracted.id=f.extracted_media_version_id
        LEFT JOIN media_assets extracted_asset ON extracted_asset.id=extracted.media_asset_id
        WHERE source.id IS NULL OR extracted.id IS NULL
          OR source_asset.id IS NULL OR extracted_asset.id IS NULL
          OR source_asset.project_id<>extracted_asset.project_id""",
        (("frame_anchors", "extracted_media_version_id"), ("media_versions", "media_asset_id"), ("media_assets", "project_id")),
    ),
    Invariant(
        "WORKFLOW_TASK_JOB",
        "automation task job links resolve and stay in the workflow run project",
        """SELECT t.id AS task_id, t.run_id, t.job_id, r.project_id AS run_project_id,
          j.project_id AS job_project_id
        FROM automation_workflow_run_tasks t
        LEFT JOIN automation_workflow_runs r ON r.id=t.run_id
        LEFT JOIN jobs j ON j.id=t.job_id
        WHERE r.id IS NULL OR (t.job_id IS NOT NULL AND
          (j.id IS NULL OR j.project_id<>r.project_id))""",
        (("automation_workflow_run_tasks", "job_id"), ("automation_workflow_runs", "project_id"), ("jobs", "project_id")),
    ),
    Invariant(
        "SHOT_SCENE_SCOPE",
        "shot scene belongs to the same project as the shot episode",
        """SELECT sh.id AS shot_id, sh.scene_id, se.project_id AS episode_project_id,
          sc.project_id AS scene_project_id
        FROM shots sh
        LEFT JOIN episodes e ON e.id=sh.episode_id
        LEFT JOIN seasons se ON se.id=e.season_id
        LEFT JOIN scenes sc ON sc.id=sh.scene_id
        WHERE e.id IS NULL OR se.id IS NULL OR
          (sh.scene_id IS NOT NULL AND (sc.id IS NULL OR sc.project_id<>se.project_id))""",
        (("shots", "scene_id"), ("scenes", "project_id"), ("episodes", "season_id"), ("seasons", "project_id")),
    ),
    Invariant(
        "SHOT_SPLIT_LINEAGE",
        "split-shot source exists in the same episode and never self-references",
        """SELECT child.id AS shot_id, child.episode_id, child.source_shot_id,
          source.episode_id AS source_episode_id
        FROM shots child LEFT JOIN shots source ON source.id=child.source_shot_id
        WHERE child.source_shot_id IS NOT NULL AND
          (source.id IS NULL OR source.id=child.id OR source.episode_id<>child.episode_id)""",
        (("shots", "source_shot_id"), ("shots", "episode_id")),
    ),
    Invariant(
        "SHOT_GROUP_SCOPE",
        "shot groups, optional scenes, and member shots remain episode/project scoped",
        """SELECT g.id AS group_id, g.episode_id, g.scene_id, m.shot_id,
          sh.episode_id AS shot_episode_id
        FROM shot_groups g
        LEFT JOIN episodes e ON e.id=g.episode_id
        LEFT JOIN seasons se ON se.id=e.season_id
        LEFT JOIN scenes sc ON sc.id=g.scene_id
        LEFT JOIN shot_group_members m ON m.group_id=g.id
        LEFT JOIN shots sh ON sh.id=m.shot_id
        WHERE e.id IS NULL OR se.id IS NULL
          OR (g.scene_id IS NOT NULL AND (sc.id IS NULL OR sc.project_id<>se.project_id))
          OR (m.shot_id IS NOT NULL AND (sh.id IS NULL OR sh.episode_id<>g.episode_id))""",
        (("shot_groups", "scene_id"), ("shot_group_members", "shot_id"), ("shots", "episode_id"), ("scenes", "project_id")),
    ),
    Invariant(
        "CHARACTER_IDENTITY_PACK_SCOPE",
        "character identity packs, versions, and slots stay scoped to the same project and character asset",
        """SELECT p.id AS pack_id, p.project_id, p.story_asset_id, v.id AS version_id,
          s.slot_kind, s.media_version_id
        FROM character_identity_packs p
        LEFT JOIN story_assets a ON a.id=p.story_asset_id
        LEFT JOIN character_identity_pack_versions v ON v.pack_id=p.id
        LEFT JOIN character_identity_pack_slots s ON s.pack_version_id=v.id
        LEFT JOIN media_versions mv ON mv.id=s.media_version_id
        WHERE (a.id IS NULL OR a.project_id<>p.project_id)
          OR (v.id IS NOT NULL AND (v.project_id<>p.project_id OR v.story_asset_id<>p.story_asset_id))
          OR (s.id IS NOT NULL AND mv.id IS NULL)""",
        (("character_identity_packs", "story_asset_id"), ("character_identity_pack_versions", "pack_id"), ("character_identity_pack_slots", "pack_version_id")),
    ),
    Invariant(
        "ASSET_PROPOSAL_SCOPE",
        "asset proposals and optional suggested/resolved assets stay in one project and type",
        """SELECT p.id AS proposal_id,p.project_id,p.kind,p.status,
          suggested.project_id AS suggested_project_id,resolved.project_id AS resolved_project_id
        FROM story_asset_proposals p
        LEFT JOIN story_assets suggested ON suggested.id=p.suggested_asset_id
        LEFT JOIN story_assets resolved ON resolved.id=p.resolved_asset_id
        WHERE p.status NOT IN ('PENDING','ACCEPTED_NEW','ACCEPTED_MERGE','REJECTED')
          OR (p.suggested_asset_id IS NOT NULL AND
              (suggested.id IS NULL OR suggested.project_id<>p.project_id OR suggested.kind<>p.kind))
          OR (p.resolved_asset_id IS NOT NULL AND
              (resolved.id IS NULL OR resolved.project_id<>p.project_id OR resolved.kind<>p.kind))
          OR (p.status IN ('ACCEPTED_NEW','ACCEPTED_MERGE') AND p.resolved_asset_id IS NULL)
          OR (p.status IN ('PENDING','REJECTED') AND p.resolved_asset_id IS NOT NULL)""",
        (("story_asset_proposals", "suggested_asset_id"), ("story_asset_proposals", "resolved_asset_id"),
         ("story_assets", "project_id"), ("story_assets", "kind")),
    ),
    Invariant(
        "QC_POLICY_SCOPE",
        "QC policy owners and current frozen versions remain in the declared project scope",
        """SELECT s.id AS policy_set_id, s.project_id, s.owner_type, s.owner_id, s.current_version_id
        FROM generation_qc_policy_sets s
        LEFT JOIN generation_qc_policy_versions v ON v.id=s.current_version_id
        LEFT JOIN episodes e ON s.owner_type='EPISODE' AND e.id=s.owner_id
        LEFT JOIN seasons se ON se.id=e.season_id
        LEFT JOIN shots sh ON s.owner_type='SHOT' AND sh.id=s.owner_id
        LEFT JOIN episodes she ON she.id=sh.episode_id
        LEFT JOIN seasons shse ON shse.id=she.season_id
        WHERE (s.owner_type='PROJECT' AND s.owner_id<>s.project_id)
          OR (s.owner_type='EPISODE' AND (e.id IS NULL OR se.project_id<>s.project_id))
          OR (s.owner_type='SHOT' AND (sh.id IS NULL OR shse.project_id<>s.project_id))
          OR s.owner_type NOT IN ('PROJECT','EPISODE','SHOT')
          OR (s.current_version_id IS NOT NULL AND
              (v.id IS NULL OR v.policy_set_id<>s.id OR v.is_frozen<>1))""",
        (("generation_qc_policy_sets", "current_version_id"), ("generation_qc_policy_versions", "policy_set_id"), ("shots", "episode_id")),
    ),
    Invariant(
        "QC_POLICY_VERSION_LINEAGE",
        "every immutable QC policy version belongs to an existing policy set",
        """SELECT v.id AS policy_version_id, v.policy_set_id, v.is_frozen
        FROM generation_qc_policy_versions v
        LEFT JOIN generation_qc_policy_sets s ON s.id=v.policy_set_id
        WHERE s.id IS NULL OR v.is_frozen<>1""",
        (("generation_qc_policy_versions", "is_frozen"), ("generation_qc_policy_sets", "id")),
    ),
    Invariant(
        "VARIANT_QC_LINEAGE",
        "variant QC evidence references one project and preserves child lineage",
        """SELECT q.id AS qc_link_id, q.variant_id, q.machine_check_run_id,
          q.policy_version_id, q.child_variant_id
        FROM variant_qc_links q
        LEFT JOIN generation_variants v ON v.id=q.variant_id
        LEFT JOIN generation_intents i ON i.id=v.intent_id
        LEFT JOIN machine_check_runs m ON m.id=q.machine_check_run_id
        LEFT JOIN generation_qc_policy_versions pv ON pv.id=q.policy_version_id
        LEFT JOIN generation_qc_policy_sets ps ON ps.id=pv.policy_set_id
        LEFT JOIN generation_variants child ON child.id=q.child_variant_id
        WHERE v.id IS NULL OR i.id IS NULL OR m.id IS NULL OR pv.id IS NULL OR ps.id IS NULL
          OR ps.project_id<>i.project_id
          OR (q.child_variant_id IS NOT NULL AND (child.id IS NULL OR child.intent_id<>v.intent_id))""",
        (("variant_qc_links", "policy_version_id"), ("generation_qc_policy_sets", "project_id"), ("generation_variants", "intent_id"), ("machine_check_runs", "id")),
    ),
    Invariant(
        "DIRECTOR_RECIPE_SCOPE",
        "Director Recipe versions and project binding remain immutable and project scoped",
        """SELECT b.project_id, b.recipe_version_id, v.recipe_id, r.project_id AS recipe_project_id
        FROM project_director_recipe_bindings b
        LEFT JOIN director_recipe_versions v ON v.id=b.recipe_version_id
        LEFT JOIN director_recipes r ON r.id=v.recipe_id
        WHERE v.id IS NULL OR r.id IS NULL OR v.is_frozen<>1 OR r.project_id<>b.project_id
        UNION ALL
        SELECT r.project_id, v.id, v.recipe_id, r.project_id
        FROM director_recipe_versions v LEFT JOIN director_recipes r ON r.id=v.recipe_id
        WHERE r.id IS NULL OR v.is_frozen<>1""",
        (("project_director_recipe_bindings", "recipe_version_id"), ("director_recipe_versions", "is_frozen"), ("director_recipes", "project_id")),
    ),
    Invariant(
        "VARIANT_DIRECTOR_RECIPE",
        "variant Director Recipe provenance resolves with matching hash and project",
        """SELECT gv.id AS variant_id, gv.director_recipe_version_id, gv.director_recipe_hash,
          drv.recipe_hash AS frozen_recipe_hash
        FROM generation_variants gv
        LEFT JOIN generation_intents gi ON gi.id=gv.intent_id
        LEFT JOIN director_recipe_versions drv ON drv.id=gv.director_recipe_version_id
        LEFT JOIN director_recipes dr ON dr.id=drv.recipe_id
        WHERE gv.director_recipe_version_id IS NOT NULL AND
          (drv.id IS NULL OR dr.id IS NULL OR drv.is_frozen<>1
           OR gv.director_recipe_hash<>drv.recipe_hash OR dr.project_id<>gi.project_id)""",
        (("generation_variants", "director_recipe_version_id"), ("generation_variants", "director_recipe_hash"), ("director_recipe_versions", "recipe_hash"), ("generation_intents", "project_id")),
    ),
    Invariant(
        "TIMELINE_MEDIA_LINEAGE",
        "timeline media exists and belongs to the timeline episode project",
        """SELECT i.id AS item_id, i.timeline_revision_id, i.media_version_id,
          se.project_id AS timeline_project_id, ma.project_id AS media_project_id
        FROM timeline_items i
        LEFT JOIN timeline_revisions tr ON tr.id=i.timeline_revision_id
        LEFT JOIN episodes e ON e.id=tr.episode_id
        LEFT JOIN seasons se ON se.id=e.season_id
        LEFT JOIN media_versions mv ON mv.id=i.media_version_id
        LEFT JOIN media_assets ma ON ma.id=mv.media_asset_id
        WHERE tr.id IS NULL OR e.id IS NULL OR se.id IS NULL
          OR (i.media_version_id IS NOT NULL AND
              (mv.id IS NULL OR ma.id IS NULL OR ma.project_id<>se.project_id))""",
        (("timeline_items", "media_version_id"), ("timeline_revisions", "episode_id"), ("episodes", "season_id"), ("seasons", "project_id"), ("media_versions", "media_asset_id"), ("media_assets", "project_id")),
    ),
)


def _schema(connection: sqlite3.Connection) -> dict[str, set[str]]:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")
    }
    return {
        table: {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}
        for table in tables
    }


def _missing_requirements(invariant: Invariant, schema: dict[str, set[str]]) -> list[str]:
    missing: list[str] = []
    for table, column in invariant.required:
        if table not in schema:
            missing.append(table)
        elif column is not None and column not in schema[table]:
            missing.append(f"{table}.{column}")
    return missing


def audit(database: Path, *, sample_limit: int = 20) -> dict[str, Any]:
    uri = f"{database.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        schema = _schema(connection)
        version_row = connection.execute(
            "SELECT version_num FROM alembic_version LIMIT 1"
        ).fetchone() if "alembic_version" in schema else None
        results: list[dict[str, Any]] = []
        for invariant in INVARIANTS:
            missing = _missing_requirements(invariant, schema)
            if missing:
                results.append({
                    "code": invariant.code,
                    "status": "NOT_APPLICABLE",
                    "description": invariant.description,
                    "missing_schema": missing,
                    "violation_count": 0,
                    "samples": [],
                })
                continue
            violation_count = int(
                connection.execute(f"SELECT COUNT(*) FROM ({invariant.sql}) AS violations").fetchone()[0]
            )
            rows = (
                connection.execute(f"SELECT * FROM ({invariant.sql}) AS violations LIMIT ?", (sample_limit,)).fetchall()
                if sample_limit and violation_count
                else []
            )
            results.append({
                "code": invariant.code,
                "status": "PASS" if violation_count == 0 else "FAIL",
                "description": invariant.description,
                "violation_count": violation_count,
                "samples": [dict(row) for row in rows],
            })

    failed = sum(result["status"] == "FAIL" for result in results)
    return {
        "schema_version": "local-drama.refactor-invariants.v1",
        "observed_at": datetime.now(UTC).isoformat(),
        "database_path": str(database.resolve()),
        "alembic_revision": str(version_row[0]) if version_row else None,
        "mode": "READ_ONLY",
        "status": "PASS" if failed == 0 else "FAIL",
        "summary": {
            "passed": sum(result["status"] == "PASS" for result in results),
            "failed": failed,
            "not_applicable": sum(result["status"] == "NOT_APPLICABLE" for result in results),
        },
        "checks": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sample-limit", type=int, default=20)
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error(f"database does not exist: {args.database}")
    if args.sample_limit < 0:
        parser.error("--sample-limit must be non-negative")

    result = audit(args.database, sample_limit=args.sample_limit)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
