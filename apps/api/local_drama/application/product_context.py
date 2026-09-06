from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from local_drama.application.ports.product_context import ProductContextReadPort

MILESTONE_ORDER = (
    "reviewable_story_draft_count",
    "episode_count",
    "active_story_asset_count",
    "production_plan_count",
    "published_profile_binding_count",
    "shot_intent_count",
    "shot_generation_job_count",
)

_EPISODE_EVIDENCE_KEYS = (
    "_render_review_decision",
    "_render_review_stale",
    "_delivery_status",
    "_delivery_human_review_status",
    "_production_attention",
)


def derive_episode_production_status(episode: dict[str, Any]) -> str:
    """Project immutable production evidence into the episode card state.

    ``episodes.production_status`` is a legacy authoring field and is not
    updated by the render/delivery workflows.  The overview therefore derives
    the current state from the latest verified render and its linked review or
    delivery package.  Evidence takes precedence when present; otherwise the
    persisted authoring status remains the compatibility fallback.
    """
    stored = str(episode.get("production_status") or "NOT_STARTED").strip().upper() or "NOT_STARTED"
    render_id = episode.get("preview_render_id")
    media_id = episode.get("preview_media_version_id")
    review_decision = str(episode.get("_render_review_decision") or "").strip().upper() or None
    review_stale = bool(episode.get("_render_review_stale"))
    delivery_status = str(episode.get("_delivery_status") or "").strip().upper() or None
    delivery_review = str(episode.get("_delivery_human_review_status") or "").strip().upper() or None
    has_evidence = bool(render_id or media_id or delivery_status)
    if not has_evidence:
        return stored

    # A historical render/package is not current delivery evidence when the
    # episode's present production facts require attention.  The project home
    # must never turn a previously delivered artifact into a false green state
    # after its working media, timeline, or production plan has changed.
    if bool(episode.get("_production_attention")):
        return "NEEDS_UPDATE"

    render_approved = bool(render_id) and review_decision == "APPROVED" and not review_stale
    delivery_verified = delivery_status == "VERIFIED"
    delivery_approved = delivery_verified and delivery_review == "APPROVED"
    if delivery_approved and (render_approved or review_decision is None):
        return "DELIVERED"
    if render_approved:
        return "APPROVED"
    return "IN_PROGRESS"


def _normalize_episode(episode: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(episode)
    normalized["production_status"] = derive_episode_production_status(episode)
    for key in _EPISODE_EVIDENCE_KEYS:
        normalized.pop(key, None)
    return normalized


def _normalize_seasons(seasons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {**season, "episodes": [_normalize_episode(episode) for episode in season.get("episodes", [])]}
        for season in seasons
    ]


class ProductContextQueryService:
    """Creator-facing projections with deterministic routing decisions."""

    def __init__(self, reader: ProductContextReadPort) -> None:
        self.reader = reader

    def app_context(self, project_id: str | None, episode_id: str | None) -> dict[str, Any]:
        facts = self.reader.app_context(project_id, episode_id)
        return {
            **facts,
            "capabilities": {
                "can_edit": True,
                "can_run_jobs": True,
                "can_manage_system": True,
            },
            "read_only": True,
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }

    def project_overview(self, project_id: str) -> dict[str, Any]:
        facts = self.reader.project_overview_facts(project_id)
        milestones = facts["milestones"]
        seasons = _normalize_seasons(facts["seasons"])
        episodes = [episode for season in seasons for episode in season["episodes"]]
        unfinished = next(
            (episode for episode in episodes if str(episode.get("production_status") or "").upper() not in {"DELIVERED", "APPROVED"}),
            episodes[0] if episodes else None,
        )
        next_action = self._next_action(project_id, milestones, unfinished)
        blockers = [
            {
                "code": key.upper(),
                "label": self._blocker_label(key),
                "owner": next_action["target"] if key == next_action.get("reason_code") else self._owner(project_id, key, unfinished),
            }
            for key in MILESTONE_ORDER
            if not milestones[key]["ready"]
        ]
        return {
            "project": facts["project"],
            "next_action": next_action,
            "blockers": blockers,
            "seasons": seasons,
            "recent_activity": facts["recent_activity"],
            "observed_at": datetime.now(UTC).isoformat(),
            "read_only": True,
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }

    @staticmethod
    def _owner(project_id: str, key: str, episode: dict[str, Any] | None) -> dict[str, Any]:
        if key == "episode_count":
            return {"kind": "PROJECT_STRUCTURE", "project_id": project_id}
        if key == "reviewable_story_draft_count":
            return {"kind": "STORY", "project_id": project_id}
        if key == "active_story_asset_count":
            return {"kind": "ASSETS", "project_id": project_id}
        if key == "production_plan_count":
            return {"kind": "SETTINGS", "project_id": project_id, "section": "production"}
        if key == "published_profile_binding_count":
            return {"kind": "SETTINGS", "project_id": project_id, "section": "capabilities"}
        if episode:
            return {"kind": "SHOT_STUDIO", "project_id": project_id, "episode_id": episode["id"], "focus": "generate" if key == "shot_generation_job_count" else "design"}
        return {"kind": "PROJECT_STRUCTURE", "project_id": project_id}

    def _next_action(self, project_id: str, milestones: dict[str, Any], episode: dict[str, Any] | None) -> dict[str, Any]:
        for key in MILESTONE_ORDER:
            if milestones[key]["ready"]:
                continue
            titles = {
                "episode_count": ("建立季度与分集", "先确定系列结构，后续故事与生产才有明确归属。", "管理系列结构"),
                "reviewable_story_draft_count": ("导入小说并生成全剧草案", "从原文一次生成可审阅的分集、故事圣经、资产档案与镜头草稿。", "开始一键分析"),
                "active_story_asset_count": ("建立核心角色和场景", "把拆解提案确认成全剧唯一资产。", "进入资产"),
                "production_plan_count": ("确认生产规格", "选择当前项目的制作默认值和交付约束。", "配置生产"),
                "published_profile_binding_count": ("绑定创作能力", "为项目选择已发布的图像、视频和声音能力。", "配置能力"),
                "shot_intent_count": (f"完成 {episode['code']} 的第一镜" if episode else "建立第一镜", "保存镜头意图并标记为可生产。", "打开镜头工作台"),
                "shot_generation_job_count": (f"生成 {episode['code']} 的第一组候选" if episode else "生成第一组候选", "在镜头上下文中预检并启动生成。", "检查并生成"),
            }
            title, description, label = titles[key]
            return {"title": title, "description": description, "label": label, "reason_code": key, "target": self._owner(project_id, key, episode)}
        if episode:
            return {
                "title": f"继续制作 {episode['code']} · {episode['title']}",
                "description": "处理本集当前的阻塞、失败和待确认事项。",
                "label": "继续生产",
                "target": {"kind": "EPISODE_PRODUCTION", "project_id": project_id, "episode_id": episode["id"]},
            }
        return {
            "title": "建立第一集", "description": "当前项目还没有分集。", "label": "管理系列结构",
            "reason_code": "episode_count", "target": {"kind": "PROJECT_STRUCTURE", "project_id": project_id},
        }

    @staticmethod
    def _blocker_label(key: str) -> str:
        return {
            "episode_count": "尚未建立季度与分集",
            "reviewable_story_draft_count": "尚无可审阅的故事拆解",
            "active_story_asset_count": "尚未确认核心故事资产",
            "production_plan_count": "尚未绑定生产规格",
            "published_profile_binding_count": "尚未绑定已发布创作能力",
            "shot_intent_count": "尚无已保存的镜头意图",
            "shot_generation_job_count": "尚未提交镜头生成任务",
        }[key]
