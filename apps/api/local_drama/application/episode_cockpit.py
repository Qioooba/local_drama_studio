"""Read-only creator projection for one episode's production facts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


class EpisodeCockpitService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def inspect(self, episode_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            episode = connection.execute(
                """SELECT e.id,e.code,e.title,s.project_id FROM episodes e
                JOIN seasons s ON s.id=e.season_id WHERE e.id=?""",
                (episode_id,),
            ).fetchone()
            if episode is None:
                raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
            shots = connection.execute(
                """SELECT COUNT(*) AS total,
                SUM(CASE WHEN current_revision_id IS NOT NULL OR status='DIRECTED' THEN 1 ELSE 0 END) AS directed
                FROM shots WHERE episode_id=? AND archived_at IS NULL""",
                (episode_id,),
            ).fetchone()
            candidate = connection.execute(
                """WITH candidate_media AS (
                  SELECT DISTINCT gi.owner_id AS shot_id,mv.id AS media_version_id,ma.id AS media_asset_id,
                  gv.is_stale,ma.selected_version_id,ma.approved_version_id
                  FROM generation_intents gi JOIN generation_variants gv ON gv.intent_id=gi.id
                  JOIN media_assets ma ON ma.owner_type='GENERATION_VARIANT' AND ma.owner_id=gv.id
                  JOIN media_versions mv ON mv.media_asset_id=ma.id
                  JOIN shots sh ON sh.id=gi.owner_id AND sh.episode_id=? AND sh.archived_at IS NULL
                  WHERE gi.owner_type='SHOT'
                )
                SELECT COUNT(DISTINCT shot_id) AS with_candidates,
                COUNT(DISTINCT CASE WHEN selected_version_id IS NOT NULL OR EXISTS(
                  SELECT 1 FROM selections se WHERE se.media_asset_id=candidate_media.media_asset_id
                ) THEN shot_id END) AS selected,
                COUNT(DISTINCT CASE WHEN approved_version_id IS NOT NULL THEN shot_id END) AS approved,
                COUNT(DISTINCT CASE WHEN is_stale=1 THEN shot_id END) AS stale,
                COUNT(DISTINCT media_version_id) AS candidate_versions
                FROM candidate_media""",
                (episode_id,),
            ).fetchone()
            failures = connection.execute(
                """WITH failed AS (
                  SELECT j.id AS job_id,j.subject_id AS shot_id FROM jobs j JOIN shots sh ON sh.id=j.subject_id
                  WHERE j.subject_type='SHOT' AND sh.episode_id=? AND j.state IN ('FAILED','DEAD','ORPHANED')
                  UNION ALL
                  SELECT j.id,gi.owner_id FROM jobs j JOIN generation_variants gv ON gv.id=j.subject_id
                  JOIN generation_intents gi ON gi.id=gv.intent_id JOIN shots sh ON sh.id=gi.owner_id
                  WHERE j.subject_type='GENERATION_VARIANT' AND gi.owner_type='SHOT' AND sh.episode_id=?
                  AND j.state IN ('FAILED','DEAD','ORPHANED')
                ) SELECT COUNT(DISTINCT job_id) AS failed_jobs,COUNT(DISTINCT shot_id) AS failed_shots FROM failed""",
                (episode_id, episode_id),
            ).fetchone()
            bridges = connection.execute(
                """SELECT COUNT(*) AS total,
                SUM(CASE WHEN stc.is_stale=0 AND stc.compatibility_status NOT IN ('BLOCKED','CONFLICT','INCOMPATIBLE') THEN 1 ELSE 0 END) AS ready,
                SUM(CASE WHEN stc.is_stale=1 THEN 1 ELSE 0 END) AS stale
                FROM shot_transition_constraints stc JOIN shots sh ON sh.id=stc.from_shot_id
                WHERE sh.episode_id=?""",
                (episode_id,),
            ).fetchone()
            audio = connection.execute(
                """SELECT COUNT(*) AS bindings,
                SUM(CASE WHEN source_license_status IN ('VERIFIED_LOCAL','USER_OWNED','PUBLIC_DOMAIN') THEN 1 ELSE 0 END) AS verified
                FROM audio_bindings WHERE episode_id=?""",
                (episode_id,),
            ).fetchone()
            qc = connection.execute(
                """WITH candidate_media AS (
                  SELECT DISTINCT mv.id FROM generation_intents gi JOIN generation_variants gv ON gv.intent_id=gi.id
                  JOIN media_assets ma ON ma.owner_type='GENERATION_VARIANT' AND ma.owner_id=gv.id
                  JOIN media_versions mv ON mv.media_asset_id=ma.id JOIN shots sh ON sh.id=gi.owner_id
                  WHERE gi.owner_type='SHOT' AND sh.episode_id=? AND sh.archived_at IS NULL
                ), latest AS (
                  SELECT mcr.subject_id,mcr.status FROM machine_check_runs mcr JOIN candidate_media cm ON cm.id=mcr.subject_id
                  WHERE mcr.subject_type='MEDIA_VERSION' AND NOT EXISTS (
                    SELECT 1 FROM machine_check_runs newer WHERE newer.subject_type=mcr.subject_type
                    AND newer.subject_id=mcr.subject_id AND (newer.created_at>mcr.created_at OR (newer.created_at=mcr.created_at AND newer.id>mcr.id))
                  )
                ) SELECT COUNT(*) AS checked,
                SUM(CASE WHEN status='PASS' THEN 1 ELSE 0 END) AS passed,
                SUM(CASE WHEN status='FAIL' THEN 1 ELSE 0 END) AS failed FROM latest""",
                (episode_id,),
            ).fetchone()

        def number(row: Any, key: str) -> int:
            return int(row[key] or 0)

        total = number(shots, "total")
        directed = number(shots, "directed")
        with_candidates = number(candidate, "with_candidates")
        failed_shots = number(failures, "failed_shots")
        stale_shots = number(candidate, "stale")
        blockers: list[dict[str, object]] = []
        if directed < total:
            blockers.append({"code": "UNDIRECTED_SHOTS", "count": total - directed, "label": "仍有镜头未完成导演意图"})
        if failed_shots:
            blockers.append({"code": "FAILED_SHOTS", "count": failed_shots, "label": "失败镜头需要处理"})
        if stale_shots or number(bridges, "stale"):
            blockers.append({"code": "STALE_OUTPUTS", "count": stale_shots + number(bridges, "stale"), "label": "存在过期候选或镜头桥"})
        return {
            "episode": dict(episode),
            "shots": {
                "total": total,
                "directed": directed,
                "with_candidates": with_candidates,
                "remaining_generation": max(0, directed - with_candidates),
                "selected": number(candidate, "selected"),
                "approved": number(candidate, "approved"),
                "failed": failed_shots,
                "stale": stale_shots,
            },
            "jobs": {"failed": number(failures, "failed_jobs")},
            "bridges": {"total": number(bridges, "total"), "ready": number(bridges, "ready"), "stale": number(bridges, "stale")},
            "audio": {"bindings": number(audio, "bindings"), "verified": number(audio, "verified")},
            "qc": {"candidate_versions": number(candidate, "candidate_versions"), "checked": number(qc, "checked"), "passed": number(qc, "passed"), "failed": number(qc, "failed")},
            "blockers": blockers,
            "observed_at": datetime.now(UTC).isoformat(),
            "read_only": True,
            "mutated": False,
        }
