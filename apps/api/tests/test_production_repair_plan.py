from __future__ import annotations

from local_drama.application.production_session_review import ProductionSessionReviewService


def test_missing_video_choices_requires_full_episode_recovery() -> None:
    plan = ProductionSessionReviewService.repair_plan(
        item_state="WAITING",
        current_stage="WAITING_REVIEW",
        blockers=[
            {
                "code": "SESSION_VIDEO_CHOICES_MISSING",
                "message": "本集没有会话视频选择证据",
            }
        ],
    )

    assert plan["recommended_strategy"] == "FULL_EPISODE"
    assert plan["can_retry_now"] is True
    assert "FILL_MISSING_GENERATION" in plan["effects"]


def test_preview_failure_recomposes_without_regenerating_media() -> None:
    plan = ProductionSessionReviewService.repair_plan(
        item_state="BLOCKED",
        current_stage="TIMELINE_PREVIEW",
        blockers=[
            {
                "code": "EPISODE_PREVIEW_RENDER_MISSING",
                "message": "本集还没有已验证预览成片",
            }
        ],
    )

    assert plan["recommended_strategy"] == "RECOMPOSE_ONLY"
    assert plan["effects"] == ["KEEP_GENERATED_MEDIA", "REBUILD_TIMELINE", "RENDER_PREVIEW"]
    assert plan["can_retry_now"] is True


def test_asset_review_is_a_prerequisite_and_does_not_fake_a_retry() -> None:
    plan = ProductionSessionReviewService.repair_plan(
        item_state="WAITING",
        current_stage="WAITING_REVIEW",
        blockers=[
            {
                "code": "SESSION_ASSET_IDENTITIES_REVIEW_REQUIRED",
                "message": "机器临时资产仍需人工确认",
            }
        ],
    )

    assert plan["recommended_strategy"] == "RECOMPOSE_ONLY"
    assert plan["can_retry_now"] is False
    assert plan["prerequisites"] == [
        {
            "action": "REVIEW_ASSET_IDENTITIES",
            "message": "先确认本次生产实际使用的机器临时资产",
        }
    ]


def test_generation_failure_retries_only_the_failed_stage() -> None:
    plan = ProductionSessionReviewService.repair_plan(
        item_state="FAILED",
        current_stage="VIDEO",
        blockers=[{"code": "JOB_DEPENDENCY_FAILED", "message": "视频依赖失败"}],
    )

    assert plan["recommended_strategy"] == "RETRY_FAILED_STAGE"
    assert plan["can_retry_now"] is True
    assert plan["effects"] == ["REUSE_SUCCEEDED_OUTPUTS", "RETRY_FAILED_STAGE"]


def test_blocked_actions_follow_repair_prerequisites() -> None:
    plan = ProductionSessionReviewService.repair_plan(
        item_state="WAITING",
        current_stage="WAITING_REVIEW",
        blockers=[
            {
                "code": "SESSION_ASSET_IDENTITIES_REVIEW_REQUIRED",
                "message": "机器临时资产仍需人工确认",
            },
            {
                "code": "PRODUCTION_SESSION_DURATION_BUDGET_EXHAUSTED",
                "message": "生产时长预算已耗尽",
            },
        ],
    )

    assert ProductionSessionReviewService.allowed_actions(
        review_status="BLOCKED",
        repair_plan=plan,
    ) == ["REVIEW_ASSET_IDENTITIES", "EXTEND_BUDGET"]


def test_ready_review_only_allows_confirmation() -> None:
    assert ProductionSessionReviewService.allowed_actions(
        review_status="READY_FOR_HUMAN_REVIEW",
        repair_plan={"prerequisites": [], "can_retry_now": True},
    ) == ["CONFIRM_CHOICES"]
