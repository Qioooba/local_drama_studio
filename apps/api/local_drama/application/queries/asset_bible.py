"""Asset Bible aggregate read model.

Read-only projection over the 0042 schema.  It never writes; it only
aggregates asset + states + references + voice + usage into the DTO the
AssetBiblePage consumes.
"""

from __future__ import annotations

from typing import Any

from local_drama.application.ports import AssetBibleRepository


class AssetBibleQueryService:
    def __init__(self, repository: AssetBibleRepository) -> None:
        self.repository = repository

    def asset_bible(self, project_id: str) -> dict[str, Any]:
        assets = self.repository.list_assets(project_id)
        items: list[dict[str, Any]] = []
        for asset in assets:
            items.append(self._asset_detail(str(asset["id"])))
        return {
            "project_id": project_id,
            "asset_count": len(items),
            "items": items,
        }

    def asset_detail(self, asset_id: str) -> dict[str, Any]:
        return self._asset_detail(asset_id)

    def list_references(self, asset_id: str, asset_state_id: str | None = None) -> list[dict[str, Any]]:
        return self.repository.list_references(asset_id, asset_state_id)

    def list_states(self, asset_id: str) -> list[dict[str, Any]]:
        return self.repository.list_states(asset_id)

    def _asset_detail(self, asset_id: str) -> dict[str, Any]:
        asset = self.repository.get_asset(asset_id)
        states = self.repository.list_states(asset_id)
        # Group references by state; references with no state belong to BASE.
        state_refs: dict[str | None, list[dict[str, Any]]] = {}
        for reference in self.repository.list_references(asset_id):
            key = str(reference["asset_state_id"]) if reference.get("asset_state_id") else None
            state_refs.setdefault(key, []).append(reference)
        active_state_id = None
        for state in states:
            if str(state["state_kind"]) == "BASE":
                active_state_id = str(state["id"])
        return {
            "asset": {
                "id": str(asset["id"]),
                "kind": str(asset["kind"]),
                "code": str(asset["code"]),
                "name": str(asset["name"]),
                "description": str(asset["description"]),
                "status": str(asset["status"]),
                "revision": int(asset["revision"]),
                "canonical_media_version_id": asset.get("canonical_media_version_id"),
            },
            "states": [
                {
                    "id": str(state["id"]),
                    "code": str(state["code"]),
                    "label": str(state["label"]),
                    "state_kind": str(state["state_kind"]),
                    "description": str(state["description"]),
                    "state": state.get("state", {}),
                    "references": state_refs.get(str(state["id"]), []),
                }
                for state in states
            ],
            "base_references": state_refs.get(None, []),
            "active_state_id": active_state_id,
            "voice": self._voice(asset_id),
            "usage": self._usage(asset_id),
            "readiness": self._readiness(asset, states, state_refs),
            "multiview_generations": self.repository.multiview_generations(asset_id),
            "expression_generations": self.repository.expression_generations(asset_id),
            "detail_generations": self.repository.detail_generations(asset_id),
        }

    def _voice(self, asset_id: str) -> dict[str, Any] | None:
        row = self.repository.voice_binding(asset_id)
        if row is None:
            return None
        return {
            "voice_profile_version_id": str(row["voice_profile_version_id"]),
            "voice_code": str(row["voice_code"]),
            "voice_title": str(row["voice_title"]),
            "status": str(row["status"]),
        }

    def _usage(self, asset_id: str) -> dict[str, Any]:
        shots = self.repository.asset_shots(asset_id)
        return {
            "episode_ids": sorted({str(item["episode_id"]) for item in shots}),
            "episodes": sorted({str(item["episode_code"]) for item in shots}),
            "shots": shots,
            "shot_count": len(shots),
        }

    def _readiness(self, asset: dict[str, Any], states: list[dict[str, Any]], state_refs: dict[str | None, list[dict[str, Any]]]) -> dict[str, Any]:
        required_by_kind = {
            "CHARACTER": ("HERO", "FRONT", "LEFT", "RIGHT"),
            "SCENE": ("HERO",),
            "PROP": ("HERO",),
            "COSTUME": ("HERO", "FRONT", "BACK"),
        }
        required_kinds = required_by_kind.get(str(asset.get("kind") or "").upper(), ("HERO",))
        references = [item for items in state_refs.values() for item in items]
        has_hero = bool(asset.get("canonical_media_version_id")) or any(str(item.get("reference_kind")) == "HERO" for item in references)
        missing: list[str] = []
        for required in required_kinds:
            if required == "HERO":
                if not has_hero:
                    missing.append(required)
                continue
            present = any(str(item.get("reference_kind")) == required for items in state_refs.values() for item in items)
            if not present:
                missing.append(required)
        level = "READY" if not missing else "BASIC" if has_hero else "EMPTY"
        if str(asset["status"]) == "ARCHIVED":
            level = "STALE"
        kind = str(asset.get("kind") or "").upper()
        identity = self.repository.identity_readiness(str(asset["id"])) if kind == "CHARACTER" else None
        blockers: list[dict[str, str]] = []
        if not has_hero:
            blockers.append({"code": "ASSET_HERO_MISSING", "message": "缺少主参考图"})
        if identity and identity["approved_pack_count"] == 0:
            blockers.append({
                "code": "IDENTITY_PACK_NOT_APPROVED",
                "message": "身份包待补齐或批准" if identity["active_pack_count"] else "尚未创建身份包",
            })
        if identity and identity["missing_shot_binding_count"]:
            blockers.append({"code": "SHOT_IDENTITY_PACK_MISSING", "message": f"{identity['missing_shot_binding_count']} 个镜头尚未绑定身份包"})
        if identity and identity["stale_shot_binding_count"]:
            blockers.append({"code": "SHOT_IDENTITY_PACK_STALE", "message": f"{identity['stale_shot_binding_count']} 个镜头仍绑定旧版身份包"})
        if kind != "CHARACTER":
            identity_state = "NOT_APPLICABLE"
            binding_state = "NOT_APPLICABLE"
        else:
            identity_state = (
                "MISSING" if not identity or identity["active_pack_count"] == 0
                else "PENDING" if identity["approved_pack_count"] == 0
                else "APPROVED_MULTIPLE" if identity["approved_pack_count"] > 1
                else "APPROVED"
            )
            binding_state = (
                "NOT_REFERENCED" if not identity or identity["referenced_shot_count"] == 0
                else "MIXED" if identity["missing_shot_binding_count"] and identity["stale_shot_binding_count"]
                else "MISSING" if identity["missing_shot_binding_count"]
                else "STALE" if identity["stale_shot_binding_count"]
                else "CURRENT"
            )
        return {
            "level": level,
            "required": list(required_kinds),
            "missing": missing,
            "asset_visual_state": "READY" if has_hero else "MISSING",
            "identity_pack_state": identity_state,
            "episode_binding_state": binding_state,
            "generation_gate_state": "NOT_CHECKED",
            "checked_revision": int(asset["revision"]),
            "blockers": blockers,
            "repair_target": {"section": "IDENTITY_PACK" if kind == "CHARACTER" and identity_state != "APPROVED" else "HERO"},
        }
