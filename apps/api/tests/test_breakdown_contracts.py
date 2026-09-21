from __future__ import annotations

from local_drama.application.breakdown_contracts import director_intent_fields, explicit_camera_movement


def test_explicit_camera_motion_does_not_confuse_subject_actions() -> None:
    assert explicit_camera_movement("角色推门进入，随后摇头") is None
    assert explicit_camera_movement("镜头跟随角色背影前进") == "TRACKING"


def test_director_intent_prefers_explicit_camera_prose_over_static_default() -> None:
    fields = director_intent_fields(
        {
            "action": "镜头从街道全景缓慢推向修表铺门口",
            "visual": "雨后的旧街",
            "camera": "固定",
        },
        {"title": "修表铺"},
        duration_ms=5000,
        source_revision_id=None,
    )

    assert fields["camera_plan"]["movement"] == "PUSH_IN"
