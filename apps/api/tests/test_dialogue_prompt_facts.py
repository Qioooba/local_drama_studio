from __future__ import annotations

import json

from local_drama.application.dialogue import DialogueService
from local_drama.application.episode_worker_actions import EpisodeWorkerActionService
from local_drama.infrastructure.service_composition import build_shot_keyframe_batch
from tests.test_episode_worker_actions import _project_and_shot
from tests.test_shot_keyframe_generation import _setup


def test_episode_prompt_uses_latest_canonical_dialogue_identity(workspace, database) -> None:
    _, episode, shot = _project_and_shot(workspace, database, "canonical_dialogue_prompt")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE shot_revisions SET fields_json=? WHERE id=?",
            (
                json.dumps(
                    {
                        "subject_action": "两人隔桌对峙",
                        "dialogue": [{"speaker": "旧人物", "text": "过期文本"}],
                    },
                    ensure_ascii=False,
                ),
                shot["current_revision_id"],
            ),
        )
    dialogue = DialogueService(database, workspace)
    first = dialogue.create_line(
        str(episode["id"]),
        code="DLG-A",
        speaker="阿宁",
        text="第一版",
        pronunciation={},
        shot_id=str(shot["id"]),
    )
    second = dialogue.create_line(
        str(episode["id"]),
        code="DLG-B",
        speaker="阿宁",
        text="你真的要走？",
        pronunciation={},
        shot_id=str(shot["id"]),
    )
    revised = dialogue.revise_text(
        str(first["id"]),
        expected_revision_no=1,
        text='“今晚。”\n别回头。',
        pronunciation={},
    )

    service = EpisodeWorkerActionService(database, workspace)
    _, shots = service._episode(str(episode["id"]))
    fields = service._fields(shots[0])
    prompt = service.video_generation_preflight(
        str(episode["id"]), target_shot_ids=(str(shot["id"]),)
    )["items"][0]["prompt"]

    assert [line["dialogue_line_id"] for line in fields["dialogue"]] == [
        first["id"],
        second["id"],
    ]
    assert fields["dialogue"][0]["text_revision_id"] == revised["text_revisions"][-1]["id"]
    assert fields["dialogue"][0]["text_revision_no"] == 2
    assert '阿宁：“今晚。”\n别回头。' in prompt
    assert "阿宁：你真的要走？" in prompt
    assert "过期文本" not in prompt
    assert "第一版" not in prompt


def test_unresolved_canonical_speaker_blocks_video_and_keyframe_plans(workspace, database) -> None:
    _, episode, shot, profile_id = _setup(workspace, database)
    line = DialogueService(database, workspace).create_line(
        str(episode["id"]),
        code="DLG-UNRESOLVED",
        speaker="待确认说话人",
        text="不要替我猜名字。",
        pronunciation={},
        shot_id=str(shot["id"]),
    )

    video_item = EpisodeWorkerActionService(database, workspace).video_generation_preflight(
        str(episode["id"]), target_shot_ids=(str(shot["id"]),)
    )["items"][0]
    video_blocker = next(
        blocker
        for blocker in video_item["blockers"]
        if blocker["code"] == "DIALOGUE_SPEAKER_CONFIRMATION_REQUIRED"
    )
    assert video_blocker["dialogue_line_ids"] == [line["id"]]

    plan = build_shot_keyframe_batch(database, workspace).plan(
        str(episode["id"]),
        targets=[
            {
                "shot_id": str(shot["id"]),
                "expected_revision": int(shot["revision"]),
            }
        ],
        candidate_count=1,
        profile_version_id=profile_id,
    )
    keyframe_item = plan["items"][0]
    assert keyframe_item["status"] == "BLOCKED"
    assert "待确认说话人：不要替我猜名字。" in keyframe_item["prompt"]
    keyframe_blocker = next(
        blocker
        for blocker in keyframe_item["blockers"]
        if blocker["code"] == "DIALOGUE_SPEAKER_CONFIRMATION_REQUIRED"
    )
    assert keyframe_blocker["dialogue_line_ids"] == [line["id"]]
