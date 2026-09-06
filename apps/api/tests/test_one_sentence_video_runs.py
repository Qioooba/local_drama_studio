from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_drama.application.generation_model_catalog import build_generation_model_catalog
from local_drama.application.jobs import JobService
from local_drama.application.local_llm import LocalLLMService
from local_drama.application.one_sentence_video_runs import OneSentenceVideoRunService
from local_drama.application.profiles import ProfileService
from local_drama.application.quick_generation_presets import QuickGenerationPresetService
from local_drama.application.workflows import WorkflowService
from local_drama.domain.errors import DomainRuleError


def _profiles(workspace, database, monkeypatch):
    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.probe",
        lambda self, load_test=False: {
            "status": "PASS",
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "probe_level_passed": 4,
            "probe_levels": {},
            "model_present": True,
            "load_test": load_test,
        },
    )
    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.chat_json",
        lambda self, system, user, **kwargs: {
            "title": "雨夜橘猫",
            "video_prompt": "雨夜霓虹街道，橘猫撑伞前行，稳定向前推进",
            "keyframe_prompt": "An orange cat holds a transparent umbrella on a neon-lit rainy street, medium cinematic composition",
            "subject_action": "橘猫撑伞前行",
            "environment": "雨夜霓虹街道",
            "shot_type": "MEDIUM",
            "camera_movement": "DOLLY_IN",
        },
    )
    llm_service = LocalLLMService(database, workspace)
    llm = llm_service.sync_candidate(
        model="qwen-test",
        capability="LLM_STORY_PARSE",
        provider="OLLAMA_LOOPBACK",
        base_url="http://127.0.0.1:11434",
        allow_remote_outbound=False,
    )
    llm_service.publish(str(llm["profile_version_id"]), allow_remote_outbound=False)

    t2v = next(item for item in ProfileService(database, workspace.manifest_path).sync_manifest()["profiles"] if item["capability"] == "VIDEO_T2V")
    graph = {
        "1": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"prompt": "", "width": 480, "height": 832, "length": 107}},
        "2": {"class_type": "RandomNoise", "inputs": {"noise_seed": 1}},
        "3": {"class_type": "CreateVideo", "inputs": {"images": ["1", 0], "fps": 24.0}},
    }
    workflow = WorkflowService(database, workspace).register_package(
        "one_sentence_test",
        "One sentence test",
        graph,
        {"local_only": True},
        {"PROMPT": {"node_id": "1", "input": "prompt"}, "SEED": {"node_id": "2", "input": "noise_seed"}},
    )
    with database.transaction() as connection:
        connection.execute("UPDATE workflow_versions SET status='PUBLISHED' WHERE id=?", (workflow["id"],))
        parameter_schema = {
            "seed": {"required": True, "determinism": "profile_declared"},
            "capabilities": {"camera": {"support": "PROMPT_FALLBACK", "prompt_fallback": True}},
        }
        connection.execute(
            """UPDATE execution_profile_versions SET status='PUBLISHED',workflow_version_id=?,
            input_contract_json=?,parameter_schema_json=?,revision=revision+1 WHERE id=?""",
            (workflow["id"], json.dumps({"input_slots": {}}), json.dumps(parameter_schema), t2v["version_id"]),
        )
    return str(llm["profile_version_id"]), str(t2v["version_id"])


def _image_first_profiles(workspace, database) -> tuple[str, str]:
    candidates = ProfileService(database, workspace.manifest_path).sync_manifest()["profiles"]
    image = next(item for item in candidates if item["capability"] == "IMAGE_CONCEPT")
    i2v = next(item for item in candidates if item["capability"] == "VIDEO_I2V")
    image_workflow = WorkflowService(database, workspace).register_package(
        "one_sentence_image_test",
        "One sentence image candidates",
        {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "test.safetensors"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
            "3": {"class_type": "EmptyLatentImage", "inputs": {"width": 768, "height": 1344, "batch_size": 1}},
            "4": {"class_type": "KSampler", "inputs": {"seed": 1, "positive": ["2", 0], "latent_image": ["3", 0]}},
            "5": {"class_type": "VAEDecode", "inputs": {"samples": ["4", 0], "vae": ["1", 2]}},
            "6": {"class_type": "SaveImage", "inputs": {"images": ["5", 0], "filename_prefix": "candidate"}},
        },
        {"local_only": True},
        {"PROMPT": {"node_id": "2", "input": "text"}, "SEED": {"node_id": "4", "input": "seed"}},
    )
    i2v_workflow = WorkflowService(database, workspace).register_package(
        "one_sentence_i2v_test",
        "One sentence selected keyframe I2V",
        {
            "1": {"class_type": "LoadImage", "inputs": {"image": "pending.png"}},
            "2": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"image": ["1", 0], "prompt": "", "width": 480, "height": 832, "length": 107}},
            "3": {"class_type": "RandomNoise", "inputs": {"noise_seed": 1}},
            "4": {"class_type": "CreateVideo", "inputs": {"images": ["2", 0], "fps": 24.0}},
        },
        {"local_only": True},
        {
            "FIRST_FRAME": {"node_id": "1", "input": "image"},
            "PROMPT": {"node_id": "2", "input": "prompt"},
            "SEED": {"node_id": "3", "input": "noise_seed"},
        },
    )
    parameter_schema = {
        "seed": {"required": True, "determinism": "profile_declared"},
        "capabilities": {"camera": {"support": "PROMPT_FALLBACK", "prompt_fallback": True}},
    }
    with database.transaction() as connection:
        connection.execute(
            "UPDATE workflow_versions SET status='PUBLISHED' WHERE id IN (?,?)",
            (image_workflow["id"], i2v_workflow["id"]),
        )
        connection.execute(
            """UPDATE execution_profile_versions SET status='PUBLISHED',workflow_version_id=?,
            input_contract_json=?,parameter_schema_json=?,revision=revision+1 WHERE id=?""",
            (image_workflow["id"], json.dumps({"input_slots": {}}), json.dumps(parameter_schema), image["version_id"]),
        )
        connection.execute(
            """UPDATE execution_profile_versions SET status='PUBLISHED',workflow_version_id=?,
            input_contract_json=?,parameter_schema_json=?,revision=revision+1 WHERE id=?""",
            (
                i2v_workflow["id"],
                json.dumps({"input_slots": {"FIRST_FRAME": {"min": 1, "max": 1, "media_kinds": ["IMAGE"]}}}),
                json.dumps(parameter_schema),
                i2v["version_id"],
            ),
        )
    return str(image["version_id"]), str(i2v["version_id"])


def test_plan_and_commit_never_create_project_production_entities(workspace, database, monkeypatch) -> None:
    llm_id, t2v_id = _profiles(workspace, database, monkeypatch)
    service = OneSentenceVideoRunService(database, workspace, runtime_probe=lambda: {"status": "READY", "endpoint": "http://127.0.0.1:8188"})
    payload = {
        "story": "雨夜霓虹灯下，一只橘猫撑伞穿过街道。",
        "language": "zh-CN",
        "mode": "TEXT_TO_VIDEO",
        "llm_profile_version_id": llm_id,
        "image_profile_version_id": None,
        "video_profile_version_id": t2v_id,
        "image_candidate_count": 4,
        "allow_remote_outbound": False,
        "idempotency_key": "one-sentence-plan-1",
    }
    first = service.plan(**payload)["run"]
    replay = service.plan(**payload)["run"]

    assert replay["id"] == first["id"]
    assert first["state"] == "PLANNED"
    assert first["plan"]["output_spec"] == {
        "width": 480,
        "height": 832,
        "frame_count": 107,
        "fps": 24,
        "duration_seconds": 4.458,
        "target_duration_ms": 4458,
        "aspect_ratio": "15:26",
        "source": "PUBLISHED_WORKFLOW",
        "editable": True,
    }
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0

    with pytest.raises(DomainRuleError) as reused:
        service.plan(**{**payload, "story": "同一个 key 不应静默接受另一段描述。"})
    assert reused.value.code == "IDEMPOTENCY_KEY_REUSED"

    committed = service.commit(first["id"])["run"]
    assert committed["state"] == "GENERATING"
    assert committed["job_id"]
    assert committed["links"] == {"workspace": f"/quick-create?run={committed['id']}"}
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM seasons").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM shots").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM generation_intents").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM jobs WHERE id=?", (committed["job_id"],)).fetchone()[0] == 1
        job_scope = connection.execute("SELECT project_id,scope_kind FROM jobs WHERE id=?", (committed["job_id"],)).fetchone()
    assert dict(job_scope) == {"project_id": None, "scope_kind": "QUICK_GENERATION"}


def test_run_parameters_are_validated_frozen_and_applied_to_output_spec(workspace, database, monkeypatch) -> None:
    llm_id, t2v_id = _profiles(workspace, database, monkeypatch)
    service = OneSentenceVideoRunService(database, workspace, runtime_probe=lambda: {"status": "READY", "endpoint": "http://127.0.0.1:8188"})
    parameters = {
        "width": 832,
        "height": 480,
        "frame_count": 121,
        "fps": 30,
        "steps": 32,
        "cfg": 5.5,
        "sampler_name": "res_multistep",
        "scheduler": "simple",
        "denoise": 0.85,
    }
    planned = service.plan(
        story="一列火车穿过清晨的山谷。",
        language="zh-CN",
        mode="TEXT_TO_VIDEO",
        llm_profile_version_id=llm_id,
        image_profile_version_id=None,
        video_profile_version_id=t2v_id,
        image_candidate_count=4,
        allow_remote_outbound=False,
        llm_parameters={"temperature": 0.2, "top_p": 0.9, "max_tokens": 1024},
        image_parameters={},
        video_parameters=parameters,
        idempotency_key="quick-parameters-1",
    )["run"]
    assert planned["plan"]["output_spec"]["width"] == 832
    assert planned["plan"]["output_spec"]["height"] == 480
    assert planned["plan"]["output_spec"]["frame_count"] == 121
    assert planned["plan"]["output_spec"]["fps"] == 30
    assert planned["plan"]["output_spec"]["source"] == "RUN_PARAMETERS"
    committed = service.commit(planned["id"])["run"]
    with database.connect() as connection:
        snapshot = json.loads(connection.execute("SELECT input_snapshot_json FROM jobs WHERE id=?", (committed["job_id"],)).fetchone()[0])
    assert snapshot["execution_snapshot"]["effective_configuration"]["effective_settings"] == parameters


def test_quick_generation_presets_are_model_scoped_and_mutable(workspace, database, monkeypatch) -> None:
    _llm_id, t2v_id = _profiles(workspace, database, monkeypatch)
    service = QuickGenerationPresetService(database, ProfileService(database, workspace.manifest_path))
    created = service.create(
        name="竖屏快速预览",
        capability="VIDEO_T2V",
        execution_profile_version_id=t2v_id,
        parameters={"width": 480, "height": 832, "steps": 24, "scheduler": "simple"},
    )["preset"]
    assert created["favorite"] is True
    assert service.list("VIDEO_T2V")["items"][0]["parameters"]["steps"] == 24
    updated = service.update(
        created["id"],
        name="竖屏精细预览",
        execution_profile_version_id=t2v_id,
        parameters={"width": 480, "height": 832, "steps": 36, "scheduler": "simple"},
        favorite=False,
        expected_revision=created["revision"],
    )["preset"]
    assert updated["name"] == "竖屏精细预览"
    assert updated["parameters"]["steps"] == 36
    assert updated["favorite"] is False
    assert service.delete(created["id"])["deleted"] is True


def test_prompt_regeneration_branches_without_rewriting_completed_evidence(workspace, database, monkeypatch) -> None:
    llm_id, _ = _profiles(workspace, database, monkeypatch)
    image_id, i2v_id = _image_first_profiles(workspace, database)
    service = OneSentenceVideoRunService(database, workspace, runtime_probe=lambda: {"status": "READY", "endpoint": "http://127.0.0.1:8188"})
    source = service.plan(
        story="丝袜美女骑摩托车",
        language="zh-CN",
        mode="TEXT_TO_IMAGE_TO_VIDEO",
        llm_profile_version_id=llm_id,
        image_profile_version_id=image_id,
        video_profile_version_id=i2v_id,
        image_candidate_count=4,
        allow_remote_outbound=False,
        idempotency_key="prompt-source-1",
    )["run"]
    source_keyframe = source["plan"]["video_plan"]["keyframe_prompt"]
    source_video = source["plan"]["video_plan"]["video_prompt"]
    fresh = {
        **source["plan"]["video_plan"],
        "keyframe_prompt": "A fashionable woman in stockings sits on a motorcycle, cinematic city street",
        "video_prompt": "全新的骑行视频提示词",
    }
    monkeypatch.setattr(service.llm, "expand_video_prompt", lambda *args, **kwargs: fresh)

    keyframe_branch = service.regenerate_prompt(source["id"], "KEYFRAME", idempotency_key="prompt-keyframe-branch-1")["run"]
    replay = service.regenerate_prompt(source["id"], "KEYFRAME", idempotency_key="prompt-keyframe-branch-1")["run"]
    assert replay["id"] == keyframe_branch["id"]
    assert keyframe_branch["id"] != source["id"]
    assert keyframe_branch["state"] == "PLANNED"
    assert "project_id" not in keyframe_branch
    assert keyframe_branch["plan"]["video_plan"]["keyframe_prompt"] == fresh["keyframe_prompt"]
    assert keyframe_branch["plan"]["video_plan"]["video_prompt"] == source_video
    assert keyframe_branch["plan"]["regenerated_from"] == {"run_id": source["id"], "target": "KEYFRAME"}

    video_branch = service.regenerate_prompt(source["id"], "VIDEO", idempotency_key="prompt-video-branch-1")["run"]
    assert video_branch["plan"]["video_plan"]["video_prompt"] == fresh["video_prompt"]
    assert video_branch["plan"]["video_plan"]["keyframe_prompt"] == source_keyframe
    unchanged = service.get(source["id"], reconcile=False)["run"]
    assert unchanged["plan"]["video_plan"]["keyframe_prompt"] == source_keyframe
    assert unchanged["plan"]["video_plan"]["video_prompt"] == source_video


def test_cancel_reaches_backend_job_and_new_seed_creates_a_branch(workspace, database, monkeypatch) -> None:
    llm_id, t2v_id = _profiles(workspace, database, monkeypatch)
    service = OneSentenceVideoRunService(database, workspace, runtime_probe=lambda: {"status": "READY", "endpoint": "http://127.0.0.1:8188"})
    planned = service.plan(
        story="海边清晨，一只白鸟贴着水面飞行。",
        language="zh-CN",
        mode="TEXT_TO_VIDEO",
        llm_profile_version_id=llm_id,
        image_profile_version_id=None,
        video_profile_version_id=t2v_id,
        image_candidate_count=4,
        allow_remote_outbound=False,
        idempotency_key="one-sentence-plan-2",
    )["run"]
    committed = service.commit(planned["id"])["run"]
    cancelled = service.cancel(planned["id"])["run"]
    assert cancelled["state"] == "CANCELLED"
    assert cancelled["job"]["state"] == "CANCELLED"

    branched = service.retry(planned["id"], "NEW_SEED")["run"]
    assert branched["state"] == "GENERATING"
    assert branched["job_id"] != committed["job_id"]
    assert branched["seed"] != committed["seed"]


def test_resume_requeues_locally_failed_video_job(workspace, database, monkeypatch) -> None:
    llm_id, t2v_id = _profiles(workspace, database, monkeypatch)
    service = OneSentenceVideoRunService(database, workspace, runtime_probe=lambda: {"status": "READY", "endpoint": "http://127.0.0.1:8188"})
    planned = service.plan(
        story="海边清晨，一只白鸟贴着水面飞行。",
        language="zh-CN",
        mode="TEXT_TO_VIDEO",
        llm_profile_version_id=llm_id,
        image_profile_version_id=None,
        video_profile_version_id=t2v_id,
        image_candidate_count=4,
        allow_remote_outbound=False,
        idempotency_key="one-sentence-resume-1",
    )["run"]
    committed = service.commit(planned["id"])["run"]
    jobs = JobService(database, workspace)
    claim = jobs.claim("resume-worker", ["GPU_H3"])
    assert claim is not None and str(claim["job"]["id"]) == committed["job_id"]
    jobs.complete(
        str(claim["attempt"]["id"]),
        str(claim["attempt"]["lease_token"]),
        "resume-worker",
        success=False,
        error_code="SIMULATED_RUNTIME_FAILURE",
    )
    failed = service.get(planned["id"])["run"]
    assert failed["state"] == "FAILED"

    resumed = service.resume(planned["id"])["run"]
    assert resumed["state"] == "GENERATING"
    assert resumed["job_id"] == committed["job_id"]
    with database.connect() as connection:
        job_state = connection.execute("SELECT state FROM jobs WHERE id=?", (committed["job_id"],)).fetchone()[0]
    assert job_state == "QUEUED"
    reclaimed = jobs.claim("resume-worker", ["GPU_H3"])
    assert reclaimed is not None and str(reclaimed["job"]["id"]) == committed["job_id"]


def test_cancelling_keyframe_batch_syncs_candidate_rows(workspace, database, monkeypatch) -> None:
    llm_id, _ = _profiles(workspace, database, monkeypatch)
    image_id, i2v_id = _image_first_profiles(workspace, database)
    service = OneSentenceVideoRunService(database, workspace, runtime_probe=lambda: {"status": "READY", "endpoint": "http://127.0.0.1:8188"})
    planned = service.plan(
        story="黄昏的沙漠公路，一辆复古跑车疾驰而去。",
        language="zh-CN",
        mode="TEXT_TO_IMAGE_TO_VIDEO",
        llm_profile_version_id=llm_id,
        image_profile_version_id=image_id,
        video_profile_version_id=i2v_id,
        image_candidate_count=2,
        allow_remote_outbound=False,
        idempotency_key="one-sentence-cancel-batch-1",
    )["run"]
    committed = service.commit(planned["id"])["run"]
    assert committed["state"] == "GENERATING"
    assert all(item["state"] in {"SUBMITTED", "QUEUED"} for item in committed["candidates"])
    queued = service.get(planned["id"])["run"]
    assert queued["state"] == "GENERATING"
    assert all(item["state"] == "QUEUED" for item in queued["candidates"])

    cancelled = service.cancel(planned["id"])["run"]
    assert cancelled["state"] == "CANCELLED"
    assert all(item["state"] == "CANCELLED" for item in cancelled["candidates"])
    with database.connect() as connection:
        stale = connection.execute(
            "SELECT COUNT(*) FROM quick_generation_candidates WHERE run_id=? AND state NOT IN ('CANCELLED','FAILED')",
            (planned["id"],),
        ).fetchone()[0]
        job_states = connection.execute(
            "SELECT DISTINCT j.state FROM jobs j JOIN quick_generation_candidates c ON c.job_id=j.id WHERE c.run_id=?",
            (planned["id"],),
        ).fetchall()
    assert stale == 0
    assert [str(row["state"]) for row in job_states] == ["CANCELLED"]

    # A terminal run may retain its last generation stage after a runtime
    # failure. Reading recent history must never reconcile or touch it again,
    # otherwise an old failure repeatedly jumps to the top of the list.
    frozen_updated_at = "2026-01-01T00:00:00+00:00"
    with database.transaction() as connection:
        connection.execute(
            "UPDATE quick_generation_runs SET state='FAILED',stage='IMAGE_GENERATING',updated_at=? WHERE id=?",
            (frozen_updated_at, planned["id"]),
        )
    terminal = service.get(planned["id"])["run"]
    assert terminal["state"] == "FAILED"
    assert terminal["updated_at"] == frozen_updated_at


def test_image_first_candidates_become_approved_keyframe_and_real_i2v_binding(workspace, database, monkeypatch) -> None:
    llm_id, _ = _profiles(workspace, database, monkeypatch)
    image_id, i2v_id = _image_first_profiles(workspace, database)
    service = OneSentenceVideoRunService(database, workspace, runtime_probe=lambda: {"status": "READY", "endpoint": "http://127.0.0.1:8188"})
    planned = service.plan(
        story="雨夜霓虹灯下，一只橘猫撑伞穿过街道。",
        language="zh-CN",
        mode="TEXT_TO_IMAGE_TO_VIDEO",
        llm_profile_version_id=llm_id,
        image_profile_version_id=image_id,
        video_profile_version_id=i2v_id,
        image_candidate_count=3,
        allow_remote_outbound=False,
        idempotency_key="one-sentence-image-first-1",
    )["run"]
    assert planned["plan"]["image_spec"] == {
        "width": 768,
        "height": 1344,
        "aspect_ratio": "4:7",
        "source": "PUBLISHED_WORKFLOW",
        "editable": True,
    }
    committed = service.commit(planned["id"])["run"]
    assert committed["state"] == "GENERATING"
    assert committed["stage"] == "IMAGE_GENERATING"
    assert len(committed["candidates"]) == 3
    assert committed["job_id"] is None

    jobs = JobService(database, workspace)
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415408d763f8cfc0f01f00050001ff89993d1d0000000049454e44ae426082"
    )
    candidate_job_ids = {str(item["job_id"]) for item in committed["candidates"]}
    for ordinal in range(1, 4):
        claim = jobs.claim("one-sentence-image-worker", ["GPU_H3"])
        assert claim is not None and str(claim["job"]["id"]) in candidate_job_ids
        output = workspace.work_root / f"one-sentence-candidate-{ordinal}.png"
        output.write_bytes(png)
        jobs.register_artifact(
            str(claim["attempt"]["id"]),
            "COMFY_OUTPUT",
            output.relative_to(workspace.work_root).as_posix(),
        )
        jobs.complete(
            str(claim["attempt"]["id"]),
            str(claim["attempt"]["lease_token"]),
            "one-sentence-image-worker",
            success=True,
        )

    awaiting = service.get(planned["id"])["run"]
    assert awaiting["state"] == "AWAITING_SELECTION"
    assert awaiting["stage"] == "IMAGE_SELECTION"
    assert all(item["state"] == "READY" and item["output"]["media_kind"] == "IMAGE" for item in awaiting["candidates"])
    chosen = awaiting["candidates"][1]
    with pytest.raises(DomainRuleError) as missing_review:
        service.select_image_candidate(planned["id"], chosen["id"], confirm_review_checks=False)
    assert missing_review.value.code == "QUICK_GENERATION_IMAGE_CONFIRMATION_REQUIRED"

    generating = service.select_image_candidate(planned["id"], chosen["id"], confirm_review_checks=True)["run"]
    assert generating["state"] == "GENERATING"
    assert generating["stage"] == "VIDEO_GENERATING"
    assert generating["selected_candidate_id"] == chosen["id"]
    assert generating["selected_image_output_id"] == chosen["output_id"]
    assert generating["job_id"]
    with database.connect() as connection:
        snapshot = json.loads(connection.execute("SELECT input_snapshot_json FROM jobs WHERE id=?", (generating["job_id"],)).fetchone()[0])
        project_count = connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        media_count = connection.execute("SELECT COUNT(*) FROM media_versions").fetchone()[0]
    assert snapshot["artifact_bindings"] == [{"role": "FIRST_FRAME", "artifact_id": chosen["output"]["source_artifact_id"]}]
    assert project_count == 0
    assert media_count == 0


def test_text_to_image_finishes_with_selected_standalone_image(workspace, database, monkeypatch) -> None:
    llm_id, _ = _profiles(workspace, database, monkeypatch)
    image_id, _ = _image_first_profiles(workspace, database)
    service = OneSentenceVideoRunService(database, workspace, runtime_probe=lambda: {"status": "READY", "endpoint": "http://127.0.0.1:8188"})
    planned = service.plan(
        story="薄雾森林中的白鹿，柔和晨光。",
        language="zh-CN",
        mode="TEXT_TO_IMAGE",
        llm_profile_version_id=llm_id,
        image_profile_version_id=image_id,
        video_profile_version_id=None,
        image_candidate_count=1,
        allow_remote_outbound=False,
        idempotency_key="quick-text-to-image-1",
    )["run"]
    assert planned["plan"]["result_kind"] == "IMAGE"
    assert planned["plan"]["video"] is None
    assert planned["plan"]["camera_resolution"] is None
    committed = service.commit(planned["id"])["run"]
    assert committed["job_id"] is None
    assert len(committed["candidates"]) == 1

    jobs = JobService(database, workspace)
    claim = jobs.claim("quick-image-worker", ["GPU_H3"])
    assert claim is not None
    output = workspace.work_root / "quick-image-only.png"
    output.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415408d763f8cfc0f01f00050001ff89993d1d0000000049454e44ae426082"
        )
    )
    jobs.register_artifact(str(claim["attempt"]["id"]), "COMFY_OUTPUT", output.relative_to(workspace.work_root).as_posix())
    jobs.complete(str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), "quick-image-worker", success=True)
    awaiting = service.get(planned["id"])["run"]
    selected = service.select_image_candidate(
        planned["id"], awaiting["candidates"][0]["id"], confirm_review_checks=True
    )["run"]
    assert selected["state"] == "SUCCEEDED"
    assert selected["output"]["media_kind"] == "IMAGE"
    assert selected["output"]["thumbnail_url"].endswith("/thumbnail?size=small&frame=poster")
    artifact = selected["output"]["artifact"]
    assert artifact["scope"] == "DATA"
    assert artifact["kind"] == "FILE"
    assert artifact["download_url"].endswith(f"/{selected['output']['id']}/download")
    assert artifact["download_filename"].startswith("快速生成-")
    assert artifact["download_filename"].endswith(".png")
    assert Path(artifact["server_absolute_path"]).is_file()
    thumbnail, thumbnail_mime = service.thumbnail_path(str(selected["output"]["id"]))
    assert thumbnail.is_file()
    assert thumbnail_mime == "image/webp"
    assert selected["video_profile_version_id"] is None


def test_model_catalog_groups_one_video_model_under_both_actions() -> None:
    profiles = [
        {
            "id": "t2v-profile",
            "version_id": "t2v-v1",
            "version_no": 1,
            "title": "Wan VIDEO_T2V Profile",
            "capability": "VIDEO_T2V",
            "status": "PUBLISHED",
            "workflow_version_id": "workflow-t2v",
            "model_bundle": {"model_family": "Wan 2.2"},
        },
        {
            "id": "i2v-profile",
            "version_id": "i2v-v1",
            "version_no": 1,
            "title": "Wan VIDEO_I2V Profile",
            "capability": "VIDEO_I2V",
            "status": "PUBLISHED",
            "workflow_version_id": "workflow-i2v",
            "model_bundle": {"model_family": "Wan 2.2"},
        },
    ]
    catalog = build_generation_model_catalog(profiles)
    assert len(catalog) == 1
    assert catalog[0]["name"] == "Wan 2.2"
    assert catalog[0]["actions"] == ["IMAGE_TO_VIDEO", "TEXT_TO_VIDEO"]
    assert {route["profile_version_id"] for route in catalog[0]["routes"]} == {"t2v-v1", "i2v-v1"}


def test_model_catalog_marks_published_text_provider_executable_without_comfy_workflow() -> None:
    catalog = build_generation_model_catalog(
        [
            {
                "id": "llm-profile",
                "version_id": "llm-v1",
                "version_no": 1,
                "title": "Local LLM story planner",
                "capability": "LLM_STORY_PARSE",
                "status": "PUBLISHED",
                "workflow_version_id": None,
                "model_bundle": {"provider": "OLLAMA_LOOPBACK", "model": "qwen3.8:27b"},
            }
        ]
    )

    assert len(catalog) == 1
    assert catalog[0]["executable"] is True
    assert catalog[0]["routes"][0]["action"] == "TEXT_PLANNING"
    assert catalog[0]["routes"][0]["executable"] is True


def test_model_catalog_prefers_configured_text_model_and_rejects_explicitly_unverified_media_route() -> None:
    profiles = [
        {
            "id": "stale-llm",
            "version_id": "stale-llm-v1",
            "version_no": 1,
            "title": "Old planner",
            "capability": "LLM_STORY_PARSE",
            "status": "PUBLISHED",
            "workflow_version_id": None,
            "model_bundle": {"provider": "OLLAMA_LOOPBACK", "model": "old:14b"},
        },
        {
            "id": "current-llm",
            "version_id": "current-llm-v1",
            "version_no": 1,
            "title": "Current planner",
            "capability": "LLM_STORY_PARSE",
            "status": "PUBLISHED",
            "workflow_version_id": None,
            "model_bundle": {"provider": "OLLAMA_LOOPBACK", "model": "current:27b"},
        },
        {
            "id": "retired-image",
            "version_id": "retired-image-v1",
            "version_no": 1,
            "title": "Retired image route",
            "capability": "IMAGE_CONCEPT",
            "status": "PUBLISHED",
            "workflow_version_id": "old-workflow",
            "model_bundle": {"model_family": "Retired image", "route_status": "static_workflow_present_runtime_unverified"},
        },
    ]

    catalog = build_generation_model_catalog(profiles, preferred_llm_model="current:27b")

    text_models = [model for model in catalog if model["category"] == "TEXT"]
    assert text_models[0]["name"] == "current:27b"
    retired = next(model for model in catalog if model["category"] == "IMAGE")
    assert retired["executable"] is False
    assert retired["routes"][0]["executable"] is False
