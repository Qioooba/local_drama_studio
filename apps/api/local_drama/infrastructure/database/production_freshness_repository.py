"""Bounded read-only SQLite facts for production freshness."""

from __future__ import annotations

import sqlite3
from typing import Any


class SqliteProductionFreshnessRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.query_count = 0

    def _all(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        self.query_count += 1
        return [dict(row) for row in self.connection.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        self.query_count += 1
        row = self.connection.execute(sql, params).fetchone()
        return dict(row) if row else None

    def load_facts(self, *, scope_type: str, scope_id: str, limit: int) -> dict[str, Any]:
        scope, shots, episode_ids = self._resolve_scope(scope_type, scope_id, limit)
        if scope is None:
            return {"scope": None, "variants": [], "frame_bridges": [], "timeline": [], "query_count": self.query_count}
        shot_ids = [str(row["shot_id"]) for row in shots]
        variants = self._variants(shot_ids, limit)
        frames = self._frame_bridges(shot_ids, limit)
        timeline = self._timeline(episode_ids, shot_ids, limit)
        return {
            "scope": scope, "variants": variants, "frame_bridges": frames, "timeline": timeline,
            "truncated": any(len(items) >= limit for items in (variants, frames, timeline)),
            "query_count": self.query_count,
        }

    def _resolve_scope(self, scope_type: str, scope_id: str, limit: int) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
        if scope_type == "PROJECT":
            owner = self._one("SELECT id FROM projects WHERE id=?", (scope_id,))
            if owner is None:
                return None, [], []
            shots = self._all(
                """SELECT sh.id AS shot_id,e.id AS episode_id FROM shots sh
                JOIN episodes e ON e.id=sh.episode_id JOIN seasons se ON se.id=e.season_id
                WHERE se.project_id=? AND sh.archived_at IS NULL ORDER BY e.display_order,sh.order_key,sh.id LIMIT ?""", (scope_id, limit),
            )
            episodes = self._all("SELECT e.id FROM episodes e JOIN seasons se ON se.id=e.season_id WHERE se.project_id=? ORDER BY e.display_order,e.id LIMIT ?", (scope_id, limit))
            return {"type": "PROJECT", "id": scope_id, "project_id": scope_id}, shots, [str(row["id"]) for row in episodes]
        if scope_type == "EPISODE":
            owner = self._one(
                "SELECT e.id,se.project_id FROM episodes e JOIN seasons se ON se.id=e.season_id WHERE e.id=?", (scope_id,),
            )
            if owner is None:
                return None, [], []
            shots = self._all("SELECT id AS shot_id,episode_id FROM shots WHERE episode_id=? AND archived_at IS NULL ORDER BY order_key,id LIMIT ?", (scope_id, limit))
            return {"type": "EPISODE", "id": scope_id, "project_id": str(owner["project_id"]), "episode_id": scope_id}, shots, [scope_id]
        owner = self._one(
            """SELECT sh.id AS shot_id,sh.episode_id,se.project_id FROM shots sh
            JOIN episodes e ON e.id=sh.episode_id JOIN seasons se ON se.id=e.season_id
            WHERE sh.id=? AND sh.archived_at IS NULL""", (scope_id,),
        )
        if owner is None:
            return None, [], []
        return ({"type": "SHOT", "id": scope_id, "project_id": str(owner["project_id"]),
                 "episode_id": str(owner["episode_id"]), "shot_id": scope_id}, [owner], [str(owner["episode_id"])])

    def _variants(self, shot_ids: list[str], limit: int) -> list[dict[str, Any]]:
        if not shot_ids:
            return []
        marks = ",".join("?" for _ in shot_ids)
        return self._all(
            f"""SELECT DISTINCT gv.id AS variant_id,gi.project_id,sh.episode_id,sh.id AS shot_id,
            gv.is_stale,gv.stale_reason,vib.media_version_id,
            gv.prompt_revision_id AS source_prompt_revision_id,spr.revision_no AS source_prompt_revision_no,
            cpr.id AS current_prompt_revision_id,cpr.revision_no AS current_prompt_revision_no,
            gv.capability_profile_version_id AS source_profile_version_id,source_profile.version_no AS source_profile_version_no,
            CASE
              WHEN shot_pref.id IS NOT NULL THEN CASE WHEN shot_pref_version.resolution_mode='EXPLICIT' AND shot_profile.status='PUBLISHED' THEN shot_pref_version.execution_profile_version_id WHEN shot_pref_version.resolution_mode='AUTO' THEN auto_profile.id END
              WHEN episode_pref.id IS NOT NULL THEN CASE WHEN episode_pref_version.resolution_mode='EXPLICIT' AND episode_profile.status='PUBLISHED' THEN episode_pref_version.execution_profile_version_id WHEN episode_pref_version.resolution_mode='AUTO' THEN auto_profile.id END
              WHEN project_pref.id IS NOT NULL THEN CASE WHEN project_pref_version.resolution_mode='EXPLICIT' AND project_profile.status='PUBLISHED' THEN project_pref_version.execution_profile_version_id WHEN project_pref_version.resolution_mode='AUTO' THEN auto_profile.id END
              ELSE auto_profile.id
            END AS current_profile_version_id,
            CASE
              WHEN shot_pref.id IS NOT NULL THEN CASE WHEN shot_pref_version.resolution_mode='EXPLICIT' AND shot_profile.status='PUBLISHED' THEN shot_profile.version_no WHEN shot_pref_version.resolution_mode='AUTO' THEN auto_profile.version_no END
              WHEN episode_pref.id IS NOT NULL THEN CASE WHEN episode_pref_version.resolution_mode='EXPLICIT' AND episode_profile.status='PUBLISHED' THEN episode_profile.version_no WHEN episode_pref_version.resolution_mode='AUTO' THEN auto_profile.version_no END
              WHEN project_pref.id IS NOT NULL THEN CASE WHEN project_pref_version.resolution_mode='EXPLICIT' AND project_profile.status='PUBLISHED' THEN project_profile.version_no WHEN project_pref_version.resolution_mode='AUTO' THEN auto_profile.version_no END
              ELSE auto_profile.version_no
            END AS current_profile_version_no,
            sr.id AS source_reference_id,sr.status AS source_reference_status,sr.revision AS source_revision,
            sr.asset_state_id AS source_asset_state_id,ss.revision AS source_state_revision,
            COALESCE(sab.asset_state_id,easb.asset_state_id) AS current_asset_state_id,cs.revision AS current_state_revision,
            cr.id AS current_reference_id,cr.revision AS current_revision
            FROM generation_intents gi JOIN generation_variants gv ON gv.intent_id=gi.id
            JOIN shots sh ON gi.owner_type='SHOT' AND sh.id=gi.owner_id
            LEFT JOIN prompt_revisions spr ON spr.id=gv.prompt_revision_id
            LEFT JOIN prompt_revisions cpr ON cpr.id=(
              SELECT latest_prompt.id FROM prompt_revisions latest_prompt
              WHERE latest_prompt.prompt_id=spr.prompt_id
              ORDER BY latest_prompt.revision_no DESC,latest_prompt.id DESC LIMIT 1)
            LEFT JOIN execution_profile_versions source_profile ON source_profile.id=gv.capability_profile_version_id
            LEFT JOIN generation_preference_sets shot_pref ON shot_pref.project_id=gi.project_id
              AND shot_pref.owner_type='SHOT' AND shot_pref.owner_id=sh.id
              AND UPPER(shot_pref.capability)=UPPER(gi.purpose) AND shot_pref.status='ACTIVE'
            LEFT JOIN generation_preference_versions shot_pref_version ON shot_pref_version.id=shot_pref.current_version_id
            LEFT JOIN execution_profile_versions shot_profile ON shot_profile.id=shot_pref_version.execution_profile_version_id
            LEFT JOIN generation_preference_sets episode_pref ON episode_pref.project_id=gi.project_id
              AND episode_pref.owner_type='EPISODE' AND episode_pref.owner_id=sh.episode_id
              AND UPPER(episode_pref.capability)=UPPER(gi.purpose) AND episode_pref.status='ACTIVE'
            LEFT JOIN generation_preference_versions episode_pref_version ON episode_pref_version.id=episode_pref.current_version_id
            LEFT JOIN execution_profile_versions episode_profile ON episode_profile.id=episode_pref_version.execution_profile_version_id
            LEFT JOIN generation_preference_sets project_pref ON project_pref.project_id=gi.project_id
              AND project_pref.owner_type='PROJECT' AND project_pref.owner_id=gi.project_id
              AND UPPER(project_pref.capability)=UPPER(gi.purpose) AND project_pref.status='ACTIVE'
            LEFT JOIN generation_preference_versions project_pref_version ON project_pref_version.id=project_pref.current_version_id
            LEFT JOIN execution_profile_versions project_profile ON project_profile.id=project_pref_version.execution_profile_version_id
            LEFT JOIN execution_profile_versions auto_profile ON auto_profile.id=(
              SELECT candidate.id FROM execution_profile_versions candidate
              JOIN execution_profiles candidate_profile ON candidate_profile.id=candidate.execution_profile_id
              WHERE candidate.status='PUBLISHED' AND UPPER(candidate.capability)=UPPER(gi.purpose)
              ORDER BY candidate.updated_at DESC,candidate_profile.code,candidate.version_no DESC LIMIT 1)
            LEFT JOIN variant_input_bindings vib ON vib.variant_id=gv.id
            LEFT JOIN story_asset_references sr ON sr.media_version_id=vib.media_version_id
            LEFT JOIN story_asset_states ss ON ss.id=sr.asset_state_id
            LEFT JOIN shot_asset_bindings sab ON sab.shot_id=sh.id AND sab.asset_id=sr.story_asset_id
            LEFT JOIN episode_asset_state_bindings easb ON easb.episode_id=sh.episode_id AND easb.story_asset_id=sr.story_asset_id
            LEFT JOIN story_asset_states cs ON cs.id=COALESCE(sab.asset_state_id,easb.asset_state_id)
            LEFT JOIN story_asset_references cr ON cr.story_asset_id=sr.story_asset_id
              AND cr.reference_kind=sr.reference_kind AND cr.status='ACTIVE'
              AND (cr.asset_state_id=COALESCE(sab.asset_state_id,easb.asset_state_id) OR cr.asset_state_id IS NULL)
            WHERE gi.owner_type='SHOT' AND gi.owner_id IN ({marks})
            ORDER BY sh.id,gv.created_at DESC,gv.id LIMIT ?""", (*shot_ids, limit),
        )

    def _frame_bridges(self, shot_ids: list[str], limit: int) -> list[dict[str, Any]]:
        if not shot_ids:
            return []
        marks = ",".join("?" for _ in shot_ids)
        params: tuple[Any, ...] = (*shot_ids, *shot_ids, *shot_ids, limit)
        return self._all(
            f"""SELECT DISTINCT fa.id AS frame_anchor_id,ma.project_id,
            COALESCE(gi.owner_id,stc.to_shot_id,stc.from_shot_id) AS shot_id,
            sh.episode_id,fa.is_stale,fa.stale_reason,fa.source_media_version_id,
            source_mv.version_no AS source_media_version_no,gv.id AS variant_id,
            current_mv.id AS current_media_version_id,current_mv.version_no AS current_media_version_no
            FROM frame_anchors fa
            JOIN media_versions source_mv ON source_mv.id=fa.source_media_version_id
            JOIN media_assets ma ON ma.id=source_mv.media_asset_id
            LEFT JOIN generation_variants gv ON ma.owner_type='GENERATION_VARIANT' AND gv.id=ma.owner_id
            LEFT JOIN generation_intents gi ON gi.id=gv.intent_id AND gi.owner_type='SHOT'
            LEFT JOIN shot_transition_constraints stc ON stc.from_anchor_id=fa.id OR stc.to_anchor_id=fa.id
            LEFT JOIN shots sh ON sh.id=COALESCE(gi.owner_id,stc.to_shot_id,stc.from_shot_id)
            LEFT JOIN media_versions current_mv ON current_mv.id=(
              SELECT se.media_version_id FROM generation_intents cgi
              JOIN generation_variants cgv ON cgv.intent_id=cgi.id
              JOIN media_assets cma ON cma.owner_type='GENERATION_VARIANT' AND cma.owner_id=cgv.id
              JOIN selections se ON se.media_asset_id=cma.id
              WHERE cgi.owner_type='SHOT' AND cgi.owner_id=COALESCE(gi.owner_id,stc.to_shot_id,stc.from_shot_id)
              ORDER BY se.created_at DESC,se.id DESC LIMIT 1)
            WHERE gi.owner_id IN ({marks}) OR stc.from_shot_id IN ({marks}) OR stc.to_shot_id IN ({marks})
            ORDER BY shot_id,fa.id LIMIT ?""", params,
        )

    def _timeline(self, episode_ids: list[str], shot_ids: list[str], limit: int) -> list[dict[str, Any]]:
        if not episode_ids:
            return []
        marks = ",".join("?" for _ in episode_ids)
        return self._all(
            f"""SELECT ti.id AS timeline_item_id,tr.episode_id,se.project_id,tr.status AS timeline_status,
            ti.media_version_id,mv.version_no AS media_version_no,gv.id AS variant_id,gi.owner_id AS shot_id,
            source_recipe.id AS source_post_process_recipe_id,source_recipe.version_no AS source_post_process_version_no,
            current_recipe.id AS current_post_process_recipe_id,current_recipe.version_no AS current_post_process_version_no,
            current_mv.id AS current_media_version_id,current_mv.version_no AS current_media_version_no
            FROM timeline_revisions tr JOIN episodes e ON e.id=tr.episode_id JOIN seasons se ON se.id=e.season_id
            JOIN timeline_items ti ON ti.timeline_revision_id=tr.id
            LEFT JOIN media_versions mv ON mv.id=ti.media_version_id
            LEFT JOIN media_assets ma ON ma.id=mv.media_asset_id
            LEFT JOIN generation_variants gv ON ma.owner_type='GENERATION_VARIANT' AND gv.id=ma.owner_id
            LEFT JOIN generation_intents gi ON gi.id=gv.intent_id AND gi.owner_type='SHOT'
            LEFT JOIN enhancement_runs enhancement ON enhancement.output_media_version_id=ti.media_version_id AND enhancement.status='SUCCEEDED'
            LEFT JOIN post_process_recipes source_recipe ON source_recipe.id=enhancement.recipe_id
            LEFT JOIN post_process_recipes current_recipe ON current_recipe.id=(
              SELECT active_recipe.id FROM post_process_recipes active_recipe
              WHERE active_recipe.recipe_key=source_recipe.recipe_key AND active_recipe.status='ACTIVE'
              ORDER BY active_recipe.version_no DESC,active_recipe.id DESC LIMIT 1)
            LEFT JOIN media_versions current_mv ON current_mv.id=(
              SELECT selection.media_version_id FROM generation_intents cgi
              JOIN generation_variants cgv ON cgv.intent_id=cgi.id
              JOIN media_assets cma ON cma.owner_type='GENERATION_VARIANT' AND cma.owner_id=cgv.id
              JOIN selections selection ON selection.media_asset_id=cma.id
              WHERE cgi.owner_type='SHOT' AND cgi.owner_id=gi.owner_id
              ORDER BY selection.created_at DESC,selection.id DESC LIMIT 1)
            WHERE tr.episode_id IN ({marks})
            ORDER BY tr.revision_no DESC,ti.start_us,ti.id LIMIT ?""", (*episode_ids, limit),
        )
