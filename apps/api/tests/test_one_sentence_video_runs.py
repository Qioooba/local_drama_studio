from __future__ import annotations

import json

import pytest

from local_drama.application.jobs import JobService
from local_drama.application.local_llm import LocalLLMService
from local_drama.application.one_sentence_video_runs import OneSentenceVideoRunService
from local_drama.application.profiles import ProfileService
from local_drama.application.worker import LocalMediaWorker
from local_drama.application.workflows import WorkflowService
from local_drama.domain.errors import DomainRuleError


def _profiles(workspace, database, monkeypatch):
    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.probe",
        lambda self, load_test=False: {
            "status": "PASS", "provider": self.provider, "base_url": self.base_url, "model": self.model,
            "probe_level_passed": 4, "probe_levels": {}, "model_present": True, "load_test": load_test,
        },
    )
    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.chat_json",
        lambda self, system, user, **kwargs: {
            "title": "雨夜橘猫", "video_prompt": "雨夜霓虹街道，橘猫撑伞前行，稳定向前推进",
            "keyframe_prompt": "雨夜霓虹街道中，橘猫撑着透明雨伞准备前行，中景电影构图",
            "subject_action": "橘猫撑伞前行", "environment": "雨夜霓虹街道",
            "shot_type": "MEDIUM", "camera_movement": "DOLLY_IN",
        },
    )
    llm_service = LocalLLMService(database, workspace)
    llm = llm_service.sync_candidate(
        model="qwen-test", capability="LLM_STORY_PARSE", provider="OLLAMA_LOOPBACK",
        base_url="http://127.0.0.1:11434", allow_remote_outbound=False,
    )
    llm_service.publish(str(llm["profile_version_id"]), allow_remote_outbound=False)

    t2v = next(
        item for item in ProfileService(database, workspace.manifest_path).sync_manifest()["profiles"]
        if item["capability"] == "VIDEO_T2V"
    )
    graph = {
        "1": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"prompt": "", "width": 480, "height": 832, "length": 107}},
        "2": {"class_type": "RandomNoise", "inputs": {"noise_seed": 1}},
        "3": {"class_type": "CreateVideo", "inputs": {"images": ["1", 0], "fps": 24.0}},
    }
    workflow = WorkflowService(database, workspace).register_package(
        "one_sentence_test", "One sentence test", graph, {"local_only": True},
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


def test_plan_is_idempotent_and_does_not_create_project_until_commit(workspace, database, monkeypatch) -> None:
    llm_id, t2v_id = _profiles(workspace, database, monkeypatch)
    service = OneSentenceVideoRunService(
        database, workspace, runtime_probe=lambda: {"status": "READY", "endpoint": "http://127.0.0.1:8188"}
    )
    payload = {
        "story": "雨夜霓虹灯下，一只橘猫撑伞穿过街道。", "language": "zh-CN",
        "mode": "DIRECT_T2V",
        "llm_profile_version_id": llm_id,
        "image_profile_version_id": None,
        "video_profile_version_id": t2v_id,
        "image_candidate_count": 4,
        "allow_remote_outbound": False, "idempotency_key": "one-sentence-plan-1",
    }
    first = service.plan(**payload)["run"]
    replay = service.plan(**payload)["run"]

    assert replay["id"] == first["id"]
    assert first["state"] == "PLANNED"
    assert first["plan"]["output_spec"] == {
        "width": 480, "height": 832, "frame_count": 107, "fps": 24,
        "duration_seconds": 4.458, "target_duration_ms": 4458,
        "aspect_ratio": "15:26", "source": "PUBLISHED_WORKFLOW", "editable": False,
    }
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0

    with pytest.raises(DomainRuleError) as reused:
        service.plan(**{**payload, "story": "同一个 key 不应静默接受另一段描述。"})
    assert reused.value.code == "IDEMPOTENCY_KEY_REUSED"

    committed = service.commit(first["id"])["run"]
    assert committed["state"] == "GENERATING"
    assert committed["project_id"] and committed["episode_id"] and committed["shot_id"]
    assert committed["variant_id"] and committed["job_id"]
    assert committed["links"]["generation"].endswith(f"/generation/{committed['shot_id']}")
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM jobs WHERE id=?", (committed["job_id"],)).fetchone()[0] == 1


def test_cancel_reaches_backend_job_and_new_seed_creates_a_branch(workspace, database, monkeypatch) -> None:
    llm_id, t2v_id = _profiles(workspace, database, monkeypatch)
    service = OneSentenceVideoRunService(
        database, workspace, runtime_probe=lambda: {"status": "READY", "endpoint": "http://127.0.0.1:8188"}
    )
    planned = service.plan(
        story="海边清晨，一只白鸟贴着水面飞行。", language="zh-CN",
        mode="DIRECT_T2V",
        llm_profile_version_id=llm_id,
        image_profile_version_id=None,
        video_profile_version_id=t2v_id,
        image_candidate_count=4,
        allow_remote_outbound=False, idempotency_key="one-sentence-plan-2",
    )["run"]
    committed = service.commit(planned["id"])["run"]
    cancelled = service.cancel(planned["id"])["run"]
    assert cancelled["state"] == "CANCELLED"
    assert cancelled["job"]["state"] == "CANCELLED"

    branched = service.retry(planned["id"], "NEW_SEED")["run"]
    assert branched["state"] == "GENERATING"
    assert branched["job_id"] != committed["job_id"]
    assert branched["variant_id"] != committed["variant_id"]
    assert branched["seed"] != committed["seed"]


def test_resume_requeues_locally_failed_video_job(workspace, database, monkeypatch) -> None:
    llm_id, t2v_id = _profiles(workspace, database, monkeypatch)
    service = OneSentenceVideoRunService(
        database, workspace, runtime_probe=lambda: {"status": "READY", "endpoint": "http://127.0.0.1:8188"}
    )
    planned = service.plan(
        story="海边清晨，一只白鸟贴着水面飞行。", language="zh-CN",
        mode="DIRECT_T2V",
        llm_profile_version_id=llm_id,
        image_profile_version_id=None,
        video_profile_version_id=t2v_id,
        image_candidate_count=4,
        allow_remote_outbound=False, idempotency_key="one-sentence-resume-1",
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
    service = OneSentenceVideoRunService(
        database, workspace, runtime_probe=lambda: {"status": "READY", "endpoint": "http://127.0.0.1:8188"}
    )
    planned = service.plan(
        story="黄昏的沙漠公路，一辆复古跑车疾驰而去。", language="zh-CN",
        mode="KEYFRAME_I2V",
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
            "SELECT COUNT(*) FROM one_sentence_video_candidates WHERE run_id=? AND state NOT IN ('CANCELLED','FAILED')",
            (planned["id"],),
        ).fetchone()[0]
        job_states = connection.execute(
            "SELECT DISTINCT j.state FROM jobs j JOIN one_sentence_video_candidates c ON c.job_id=j.id WHERE c.run_id=?",
            (planned["id"],),
        ).fetchall()
    assert stale == 0
    assert [str(row["state"]) for row in job_states] == ["CANCELLED"]


def test_image_first_candidates_become_approved_keyframe_and_real_i2v_binding(workspace, database, monkeypatch) -> None:
    llm_id, _ = _profiles(workspace, database, monkeypatch)
    image_id, i2v_id = _image_first_profiles(workspace, database)
    service = OneSentenceVideoRunService(
        database, workspace, runtime_probe=lambda: {"status": "READY", "endpoint": "http://127.0.0.1:8188"}
    )
    planned = service.plan(
        story="雨夜霓虹灯下，一只橘猫撑伞穿过街道。",
        language="zh-CN",
        mode="KEYFRAME_I2V",
        llm_profile_version_id=llm_id,
        image_profile_version_id=image_id,
        video_profile_version_id=i2v_id,
        image_candidate_count=3,
        allow_remote_outbound=False,
        idempotency_key="one-sentence-image-first-1",
    )["run"]
    assert planned["plan"]["image_spec"] == {
        "width": 768, "height": 1344, "aspect_ratio": "4:7",
        "source": "PUBLISHED_WORKFLOW", "editable": False,
    }
    committed = service.commit(planned["id"])["run"]
    assert committed["state"] == "GENERATING"
    assert committed["stage"] == "IMAGE_GENERATING"
    assert len(committed["candidates"]) == 3
    assert committed["job_id"] is None

    jobs = JobService(database, workspace)
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d4944415408d763f8cfc0f01f00050001ff89993d1d0000000049454e44ae426082"
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

    processing = service.get(planned["id"])["run"]
    assert processing["state"] == "GENERATING"
    assert all(item["state"] == "PROCESSING" and item["media_version_id"] for item in processing["candidates"])
    LocalMediaWorker(database, workspace).run_until_idle("one-sentence-thumbnail-worker")
    awaiting = service.get(planned["id"])["run"]
    assert awaiting["state"] == "AWAITING_SELECTION"
    assert awaiting["stage"] == "IMAGE_SELECTION"
    assert all(item["state"] == "READY" and item["media_version_id"] for item in awaiting["candidates"])
    chosen = awaiting["candidates"][1]
    with pytest.raises(DomainRuleError) as missing_review:
        service.select_image_candidate(planned["id"], chosen["id"], confirm_review_checks=False)
    assert missing_review.value.code == "ONE_SENTENCE_KEYFRAME_REVIEW_REQUIRED"

    generating = service.select_image_candidate(
        planned["id"], chosen["id"], confirm_review_checks=True
    )["run"]
    assert generating["state"] == "GENERATING"
    assert generating["stage"] == "VIDEO_GENERATING"
    assert generating["selected_candidate_id"] == chosen["id"]
    assert generating["selected_image_media_version_id"] == chosen["media_version_id"]
    assert generating["job_id"] and generating["variant_id"]
    with database.connect() as connection:
        evidence = connection.execute(
            """SELECT vib.role,vib.media_version_id,vib.source_approval_id,
            ma.owner_type,ma.owner_id,ma.purpose,ma.approved_version_id,mv.stage
            FROM variant_input_bindings vib
            JOIN media_versions mv ON mv.id=vib.media_version_id
            JOIN media_assets ma ON ma.id=mv.media_asset_id
            WHERE vib.variant_id=?""",
            (generating["variant_id"],),
        ).fetchone()
        review = connection.execute(
            "SELECT decision,is_stale FROM review_decisions WHERE id=?",
            (generating["keyframe_review_id"],),
        ).fetchone()
    assert dict(evidence) == {
        "role": "FIRST_FRAME",
        "media_version_id": chosen["media_version_id"],
        "source_approval_id": generating["keyframe_review_id"],
        "owner_type": "SHOT",
        "owner_id": generating["shot_id"],
        "purpose": "KEYFRAME",
        "approved_version_id": chosen["media_version_id"],
        "stage": "KEYFRAME",
    }
    assert dict(review) == {"decision": "APPROVED", "is_stale": 0}
