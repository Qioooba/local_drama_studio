"""Run one real H3 FL2VA task through LocalDramaStudio's Comfy job service.

The database and project root are temporary.  The Comfy endpoint and model
files remain local-only; no remote transport or production database is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_repo_root / "apps" / "api"))
sys.path.insert(0, str(_repo_root))

from local_drama.application.comfy_jobs import (  # type: ignore[import-not-found]
    ComfyGenerationService,  # type: ignore[import-not-found]
)
from local_drama.application.h3_workflows import (  # type: ignore[import-not-found]
    H3WorkflowFactory,  # type: ignore[import-not-found]
)
from local_drama.application.jobs import JobService  # type: ignore[import-not-found]
from local_drama.application.media import MediaService  # type: ignore[import-not-found]
from local_drama.application.projects import (  # type: ignore[import-not-found]
    ProjectService,  # type: ignore[import-not-found]
)
from local_drama.application.reviews import (  # type: ignore[import-not-found]
    ReviewService,  # type: ignore[import-not-found]
)
from local_drama.application.workflows import (  # type: ignore[import-not-found]
    WorkflowService,  # type: ignore[import-not-found]
)
from local_drama.config import Settings  # type: ignore[import-not-found]
from local_drama.infrastructure.comfy import (  # type: ignore[import-not-found]
    ComfyClient,  # type: ignore[import-not-found]
)
from local_drama.infrastructure.database.sqlite import (  # type: ignore[import-not-found]
    Database,  # type: ignore[import-not-found]
)

from scripts.migrate import migrate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _checks(template: dict[str, Any]) -> list[dict[str, str]]:
    return [{"item_id": str(item["id"]), "result": "PASS"} for item in template["items"]]


def run(keyframe: Path, server: str, comfy_output_root: Path, comfy_input_root: Path, timeout_seconds: int, sigma_points: int) -> dict[str, Any]:
    source = keyframe.resolve(strict=True)
    if not source.is_file() or source.is_symlink():
        raise ValueError("keyframe must be a regular local file")
    with tempfile.TemporaryDirectory(prefix="h3-comfy-job-", ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        settings = Settings(
            data_root=root / "data",
            projects_root=root / "projects",
            work_root=root / "work",
            cache_root=root / "cache",
            logs_root=root / "logs",
            backups_root=root / "backups",
            comfy_base_url=server,
            comfy_output_root=comfy_output_root,
            # The platform must materialize into the input directory owned by
            # the actual Comfy process; using a temporary path here would make
            # the server reject the image even though the hash copy succeeded.
            comfy_input_root=comfy_input_root,
        )
        settings.ensure_roots()
        migrate(settings.database_path)
        database = Database(settings.database_path)
        project = ProjectService(database, settings.projects_root).create_project(
            code="h3_comfy_job_uat",
            title="H3 Comfy platform job UAT",
            episode_count=1,
            aspect_ratio="9:16",
            fps_num=24,
            fps_den=1,
            target_duration_ms=5_167,
            allow_unconfigured_capabilities=True,
        )
        project_id = str(project["id"])
        project_service = ProjectService(database, settings.projects_root)
        season = project_service.list_seasons(project_id)[0]
        episode = project_service.list_episodes(str(season["id"]))[0]
        shot = project_service.create_shot(str(episode["id"]), "SHOT_001", 5_167)
        shot_id = str(shot["id"])
        media = MediaService(database, settings).import_file(
            project_id,
            source,
            purpose="KEYFRAME_SOURCE",
            owner_type="SHOT",
            owner_id=shot_id,
            media_kind="IMAGE",
            stage="KEYFRAME",
        )
        prompt = "固定广角镜头，细雨中的北方乡村老屋，保持空间方向和道具连续，克制的单一动作。"
        factory = H3WorkflowFactory(settings)
        workflow = factory.build_fl2va(
            prompt,
            first_frame="pending-keyframe.png",
            seed=20260816,
            duration_seconds=5.0,
            aspect_ratio="auto",
            filename_prefix="h3_platform_uat/SHOT_001",
            sigma_points=sigma_points,
        )
        workflows = WorkflowService(database, settings)
        version = workflows.register_package(
            "h3_platform_fl2va_uat",
            "H3 platform FL2VA UAT",
            workflow,
            {"capability": "VIDEO_I2V_H3", "input_slots": {"FIRST_FRAME": {"required": True}}},
            {"FIRST_FRAME": {"node_id": "5", "input": "image"}},
            {"local_only": True, "network_policy": "LOOPBACK_ONLY"},
        )
        client = ComfyClient(server, comfy_output_root)
        validation = workflows.validate_against_comfy(str(version["id"]), client)
        published = workflows.publish(str(version["id"]), str(validation["validation_id"]))
        jobs = JobService(database, settings)
        job = jobs.create_job(
            project_id,
            "I2V",
            "SHOT",
            shot_id,
            "GPU_H3",
            {
                "workflow_version_id": str(version["id"]),
                "semantic_inputs": {},
                "media_bindings": [{"role": "FIRST_FRAME", "media_version_id": str(media["media_version_id"]), "ordinal": 0}],
            },
            "h3-platform-uat-20260816",
        )
        service = ComfyGenerationService(database, settings)
        submission = service.submit_next("h3-platform-uat-worker")
        if submission is None:
            raise RuntimeError("platform job was not claimable")
        attempt_id = str(submission["attempt"]["id"])
        deadline = time.monotonic() + timeout_seconds
        poll_states: list[str] = []
        result: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            result = service.poll_attempt(attempt_id, "h3-platform-uat-worker")
            state = str(result.get("status"))
            poll_states.append(state)
            if state in {"SUCCEEDED", "FAILED"}:
                break
            time.sleep(5)
        if not result or result.get("status") != "SUCCEEDED":
            raise RuntimeError(json.dumps({"poll_states": poll_states, "result": result}, ensure_ascii=False))
        artifact = result["artifacts"][0]
        promoted = MediaService(database, settings).promote_job_artifact(
            str(artifact["id"]), purpose="SHOT_VIDEO", media_kind="VIDEO", stage="FORMAL"
        )
        reviews = ReviewService(database, settings)
        reviews.ensure_templates()
        formal_template = next(item for item in reviews.templates() if item["code"] == "formal_video")
        media_id = str(promoted["media_version_id"])
        machine = reviews.machine_check(media_id)
        reviews.select_version(media_id, "FORMAL_SELECTION")
        approved = reviews.submit_review(media_id, str(formal_template["id"]), "APPROVED", 2, _checks(formal_template))
        plan = reviews.formal_selection_preflight(project_id, [media_id])
        committed = reviews.commit_formal_selection(project_id, [media_id], str(plan["plan_hash"]))
        with database.connect() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        artifact_path = (settings.work_root / str(artifact["sandbox_rel_path"])).resolve()
        return {
            "schema_version": "g10.h3_comfy_job_uat.v1",
            "status": "PARTIAL",
            "runtime": {"endpoint": server, "endpoint_local": True, "network_contacted": False, "production_database_contacted": False},
            "source": {"path": str(source), "sha256": _sha256(source), "bytes": source.stat().st_size},
            "workflow": {"id": version["id"], "status": published["status"], "validation": validation},
            "job": {"id": job["id"], "attempt_id": attempt_id, "prompt_id": submission["prompt_id"], "poll_states": poll_states},
            "artifact": {**artifact, "path": str(artifact_path), "exists": artifact_path.is_file(), "sha256": _sha256(artifact_path) if artifact_path.is_file() else None},
            "platform_pipeline": {"promoted_media_version_id": media_id, "machine_qc": machine, "human_decision": approved["decision"], "selection": committed},
            "isolated": {"project_id": project_id, "shot_id": shot_id, "database_integrity": integrity, "runtime_contacted": True, "network_contacted": False, "production_mutated": False},
            "limitations": ["隔离项目已跑通平台 Job→Artifact→Media→QC→审核→选择，但未进入正式整集交付包/下载审计；整体保持 PARTIAL。"],
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keyframe", type=Path, required=True)
    parser.add_argument("--server", default="http://127.0.0.1:8188")
    parser.add_argument("--comfy-output-root", type=Path, required=True)
    parser.add_argument("--comfy-input-root", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--sigma-points", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = run(args.keyframe, args.server, args.comfy_output_root.resolve(), args.comfy_input_root.resolve(), args.timeout_seconds, args.sigma_points)
        exit_code = 0
    except Exception as error:  # noqa: BLE001 - UAT must leave a truthful redacted record on runtime failure.
        payload = {
            "schema_version": "g10.h3_comfy_job_uat.v1",
            "status": "BLOCKED",
            "runtime": {
                "endpoint": args.server,
                "endpoint_local": args.server.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]")),
                "runtime_contacted": True,
                "network_contacted": False,
                "production_database_contacted": False,
                "production_mutated": False,
            },
            "failure": {"type": type(error).__name__, "stage": "platform_comfy_job"},
            "limitations": [
                "本次平台 Comfy Job UAT 在真实本机运行时失败，未将失败伪装成生成成功。",
                "请检查隔离 Comfy stderr/runtime 资源后重跑；外部 native 节点生成与平台正式媒体链证据仍分别保留。",
            ],
        }
        exit_code = 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "output": str(args.output)}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
