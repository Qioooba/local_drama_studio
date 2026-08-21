"""Asset Bible commands: state/reference/binding mutations.

Application layer depends only on the AssetBibleRepository port; the SQLite
adapter is injected.  Invariants enforced here:

- media version must belong to the same project as the asset;
- state must belong to the same asset;
- shot/episode binding must be project-scoped;
- archive never deletes media history;
- setting a HERO reference keeps canonical_media_version_id in sync.
"""

from __future__ import annotations

from typing import Any

from local_drama.application.ports import AssetBibleRepository
from local_drama.domain.errors import DomainRuleError

REFERENCE_KINDS = {
    "HERO", "FRONT", "LEFT", "RIGHT", "BACK", "THREE_QUARTER_LEFT", "THREE_QUARTER_RIGHT",
    "THREE_VIEW_SHEET", "FULL_BODY", "MEDIUM", "CLOSEUP", "EXPRESSION_GRID", "ACTION",
    "OUTFIT", "DETAIL", "SCENE_WIDE", "SCENE_REVERSE", "PANORAMA", "LIGHTING_REFERENCE",
    "STYLE_REFERENCE", "OTHER",
}

STATE_KINDS = {
    "BASE", "OUTFIT", "AGE", "INJURY", "EMOTION", "TIME_OF_DAY", "WEATHER", "LIGHTING", "DAMAGE", "CUSTOM",
}


class AssetBibleCommandService:
    def __init__(self, repository: AssetBibleRepository) -> None:
        self.repository = repository

    # ---- states ----------------------------------------------------------

    def create_state(
        self,
        project_id: str,
        story_asset_id: str,
        code: str,
        label: str,
        state_kind: str = "CUSTOM",
        description: str = "",
        state_json: dict[str, Any] | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        code, label = code.strip(), label.strip()
        if not code or not label:
            raise DomainRuleError("STORY_ASSET_STATE_FIELDS_REQUIRED", "状态 code 与 label 必填")
        if state_kind not in STATE_KINDS:
            raise DomainRuleError("STORY_ASSET_STATE_KIND_INVALID", "state_kind 不在允许集合内", {"allowed": sorted(STATE_KINDS)})
        asset_project = self.repository.asset_project_id(story_asset_id)
        if asset_project != project_id:
            raise DomainRuleError("STORY_ASSET_NOT_FOUND", "故事资产不存在或不属于当前项目", {"story_asset_id": story_asset_id})
        return self.repository.create_state(
            project_id=project_id,
            story_asset_id=story_asset_id,
            code=code,
            label=label,
            state_kind=state_kind,
            description=description,
            state_json=state_json or {},
            actor=actor,
        )

    def update_state(
        self,
        state_id: str,
        expected_revision: int,
        *,
        label: str | None = None,
        description: str | None = None,
        state_json: dict[str, Any] | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        return self.repository.update_state(
            state_id,
            expected_revision,
            label=label,
            description=description,
            state_json=state_json,
            actor=actor,
        )

    def archive_state(self, state_id: str, expected_revision: int, actor: str = "local-user") -> dict[str, Any]:
        return self.repository.archive_state(state_id, expected_revision, actor)

    def list_states(self, story_asset_id: str) -> list[dict[str, Any]]:
        return self.repository.list_states(story_asset_id)

    # ---- references ------------------------------------------------------

    def add_reference(
        self,
        project_id: str,
        story_asset_id: str,
        media_version_id: str,
        reference_kind: str,
        *,
        asset_state_id: str | None = None,
        label: str = "",
        priority: int = 100,
        is_locked: bool = False,
        yaw_deg: float | None = None,
        pitch_deg: float | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        if reference_kind not in REFERENCE_KINDS:
            raise DomainRuleError("STORY_ASSET_REFERENCE_KIND_INVALID", "reference_kind 不在允许集合内", {"allowed": sorted(REFERENCE_KINDS)})
        if not media_version_id.strip():
            raise DomainRuleError("STORY_ASSET_REFERENCE_MEDIA_REQUIRED", "参考图必须引用一个媒体版本")
        asset_project = self.repository.asset_project_id(story_asset_id)
        if asset_project != project_id:
            raise DomainRuleError("STORY_ASSET_NOT_FOUND", "故事资产不存在或不属于当前项目", {"story_asset_id": story_asset_id})
        media_project = self.repository.media_project_id(media_version_id)
        if media_project != project_id:
            raise DomainRuleError("STORY_ASSET_MEDIA_SCOPE_INVALID", "媒体版本必须属于当前项目", {"media_version_id": media_version_id, "project_id": project_id})
        if asset_state_id:
            state = self.repository.get_state(asset_state_id)
            if state is None:
                raise DomainRuleError("STORY_ASSET_STATE_NOT_FOUND", "资产状态不存在", {"asset_state_id": asset_state_id})
            if str(state["story_asset_id"]) != story_asset_id:
                raise DomainRuleError("STORY_ASSET_STATE_ASSET_MISMATCH", "状态与资产不一致", {"asset_state_id": asset_state_id, "story_asset_id": story_asset_id})
        reference = self.repository.add_reference(
            project_id=project_id,
            story_asset_id=story_asset_id,
            asset_state_id=asset_state_id,
            media_version_id=media_version_id,
            reference_kind=reference_kind,
            label=label,
            priority=priority,
            is_locked=is_locked,
            yaw_deg=yaw_deg,
            pitch_deg=pitch_deg,
            actor=actor,
        )
        if reference_kind == "HERO":
            self._sync_canonical_projection(story_asset_id, media_version_id)
        return reference

    def update_reference(
        self,
        reference_id: str,
        expected_revision: int,
        *,
        label: str | None = None,
        priority: int | None = None,
        is_locked: bool | None = None,
        yaw_deg: float | None = None,
        pitch_deg: float | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        return self.repository.update_reference(
            reference_id,
            expected_revision,
            label=label,
            priority=priority,
            is_locked=is_locked,
            yaw_deg=yaw_deg,
            pitch_deg=pitch_deg,
            actor=actor,
        )

    def archive_reference(self, reference_id: str, expected_revision: int, actor: str = "local-user") -> dict[str, Any]:
        reference = self.repository.get_reference(reference_id)
        if reference is None:
            raise DomainRuleError("STORY_ASSET_REFERENCE_NOT_FOUND", "资产参考图不存在", {"reference_id": reference_id})
        archived = self.repository.archive_reference(reference_id, expected_revision, actor)
        if str(reference["reference_kind"]) == "HERO":
            # Recompute the canonical projection from the next-best reference.
            remaining = [item for item in self.repository.list_references(str(reference["story_asset_id"])) if str(item["reference_kind"]) == "HERO"]
            if remaining:
                self._sync_canonical_projection(str(reference["story_asset_id"]), str(remaining[0]["media_version_id"]))
        return archived

    def list_references(self, story_asset_id: str, asset_state_id: str | None = None) -> list[dict[str, Any]]:
        return self.repository.list_references(story_asset_id, asset_state_id)

    # ---- bindings --------------------------------------------------------

    def set_episode_asset_state(self, *, episode_id: str, story_asset_id: str, asset_state_id: str, actor: str = "local-user") -> dict[str, Any]:
        episode_project = self.repository.episode_project_id(episode_id)
        if episode_project is None:
            raise DomainRuleError("EPISODE_NOT_FOUND", "分集不存在", {"episode_id": episode_id})
        asset_project = self.repository.asset_project_id(story_asset_id)
        if asset_project != episode_project:
            raise DomainRuleError("STORY_ASSET_PROJECT_MISMATCH", "资产与分集必须属于同一项目")
        state = self.repository.get_state(asset_state_id)
        if state is None:
            raise DomainRuleError("STORY_ASSET_STATE_NOT_FOUND", "资产状态不存在", {"asset_state_id": asset_state_id})
        if str(state["story_asset_id"]) != story_asset_id:
            raise DomainRuleError("STORY_ASSET_STATE_ASSET_MISMATCH", "状态与资产不一致", {"asset_state_id": asset_state_id, "story_asset_id": story_asset_id})
        return self.repository.set_episode_asset_state(episode_id=episode_id, story_asset_id=story_asset_id, asset_state_id=asset_state_id, actor=actor)

    def episode_asset_state(self, episode_id: str, story_asset_id: str) -> dict[str, Any] | None:
        return self.repository.episode_asset_state(episode_id, story_asset_id)

    def set_shot_asset_state(self, *, shot_id: str, asset_id: str, asset_state_id: str, actor: str = "local-user") -> dict[str, Any]:
        shot_project = self.repository.shot_project_id(shot_id)
        if shot_project is None:
            raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在", {"shot_id": shot_id})
        asset_project = self.repository.asset_project_id(asset_id)
        if asset_project != shot_project:
            raise DomainRuleError("STORY_ASSET_PROJECT_MISMATCH", "资产与镜头必须属于同一项目")
        state = self.repository.get_state(asset_state_id)
        if state is None:
            raise DomainRuleError("STORY_ASSET_STATE_NOT_FOUND", "资产状态不存在", {"asset_state_id": asset_state_id})
        if str(state["story_asset_id"]) != asset_id:
            raise DomainRuleError("STORY_ASSET_STATE_ASSET_MISMATCH", "状态与资产不一致", {"asset_state_id": asset_state_id, "asset_id": asset_id})
        return self.repository.set_shot_asset_state(shot_id=shot_id, asset_id=asset_id, asset_state_id=asset_state_id, actor=actor)

    def shot_asset_states(self, shot_id: str) -> list[dict[str, Any]]:
        return self.repository.shot_asset_states(shot_id)

    # ---- canonical projection ---------------------------------------------

    def _sync_canonical_projection(self, story_asset_id: str, media_version_id: str) -> None:
        """Keep story_assets.canonical_media_version_id as the HERO projection.

        This is the compatibility projection required by docs/xinjihua/02 §30.3;
        historical variant input bindings are never rewritten.
        """
        self.repository.update_asset_canonical(story_asset_id, media_version_id)
