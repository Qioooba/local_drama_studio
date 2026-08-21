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
        has_hero = bool(state_refs.get(None)) or any(str(item.get("reference_kind")) == "HERO" for items in state_refs.values() for item in items)
        missing: list[str] = []
        for required in ("FRONT", "LEFT", "RIGHT"):
            present = any(str(item.get("reference_kind")) == required for items in state_refs.values() for item in items)
            if not present:
                missing.append(required)
        if not has_hero:
            missing.insert(0, "HERO")
        level = "READY" if not missing else "BASIC" if has_hero else "EMPTY"
        if str(asset["status"]) == "ARCHIVED":
            level = "STALE"
        return {"level": level, "missing": missing}
