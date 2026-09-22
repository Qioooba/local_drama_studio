"""Publish a production-grade IMAGE_CHARACTER route on the legacy (V1) chain.

The audit found that the only PUBLISHED ``local-suite-image-character`` version
(v4) was bound to ``qwen-image-2512-q5-smoke`` -- a 256x256 / 1-step verification
graph -- so the asset-image pages could plan real production work at smoke
scale.  A later guard now rejects that route, which is correct but leaves
IMAGE_CHARACTER without a usable default.

This script fixes the data, not just the guard:

1. create and publish ``qwen-image-2512-character-1mp`` -- the same verified
   2512 chain, but frozen at ~1MP (768x1376) with the full semantic bindings a
   production route needs (PROMPT / NEGATIVE_PROMPT / SEED / STEPS / CFG /
   WIDTH / HEIGHT / OUTPUT_PREFIX);
2. derive a new ``local-suite-image-character`` version from v4 and re-bind it
   to that workflow, following the repository's existing
   ``derive -> re-link -> validate -> compatibility`` sequence;
3. run a real ComfyUI evidence job through that exact Profile version, promote
   the artifact to a media version, and publish with
   ``ProfileService.publish_from_evidence`` -- the chain the plan names.

Nothing is fabricated: if the evidence job does not produce a verified
artifact, no Profile is published.

Example::

    python scripts/qwen21/publish_legacy_image_profiles.py --dry-run
    python scripts/qwen21/publish_legacy_image_profiles.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "apps" / "api") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "apps" / "api"))

from local_drama.application.comfy_jobs import ComfyGenerationService  # noqa: E402
from local_drama.application.jobs import JobService  # noqa: E402
from local_drama.application.media import MediaService  # noqa: E402
from local_drama.application.profiles import ProfileService  # noqa: E402
from local_drama.application.workflows import WorkflowService  # noqa: E402
from local_drama.config import Settings  # noqa: E402
from local_drama.domain.errors import DomainRuleError  # noqa: E402
from local_drama.infrastructure.comfy import ComfyClient  # noqa: E402
from local_drama.infrastructure.database.sqlite import Database  # noqa: E402

WORKFLOW_CODE = "qwen-image-2512-character-1mp"
PROFILE_CODE = "local-suite-image-character"
ACTOR = "qwen21-character-route"
WIDTH, HEIGHT, STEPS = 768, 1376, 20
SOURCE_WORKFLOW_CODE = "qwen-image-2512-production"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_graph(database: Database) -> dict[str, Any]:
    """Clone the verified 2512 production graph and re-freeze it at ~1MP."""

    with database.connect() as connection:
        row = connection.execute(
            """SELECT wv.content_json FROM workflow_versions wv JOIN workflows w ON w.id=wv.workflow_id
               WHERE w.code=? AND wv.status='PUBLISHED' ORDER BY wv.version_no DESC LIMIT 1""",
            (SOURCE_WORKFLOW_CODE,),
        ).fetchone()
    if row is None:
        raise DomainRuleError("QWEN21_SOURCE_WORKFLOW_MISSING", "缺少已验证的 2512 生产工作流。", {"code": SOURCE_WORKFLOW_CODE})
    graph = json.loads(str(row["content_json"]))
    graph["7"]["inputs"].update(width=WIDTH, height=HEIGHT)
    graph["8"]["inputs"]["steps"] = STEPS
    graph["10"]["inputs"]["filename_prefix"] = "local_drama/qwen_image_2512_character"
    return graph


BINDINGS = {
    "PROMPT": {"node_id": "5", "input": "text"},
    "NEGATIVE_PROMPT": {"node_id": "6", "input": "text"},
    "SEED": {"node_id": "8", "input": "seed"},
    "STEPS": {"node_id": "8", "input": "steps"},
    "CFG": {"node_id": "8", "input": "cfg"},
    "DENOISE": {"node_id": "8", "input": "denoise"},
    "SAMPLER_NAME": {"node_id": "8", "input": "sampler_name"},
    "SCHEDULER": {"node_id": "8", "input": "scheduler"},
    "WIDTH": {"node_id": "7", "input": "width"},
    "HEIGHT": {"node_id": "7", "input": "height"},
    "OUTPUT_PREFIX": {"node_id": "10", "input": "filename_prefix"},
}


def ensure_workflow(database: Database, settings: Settings) -> dict[str, Any]:
    service = WorkflowService(database, settings)
    graph = build_graph(database)
    for version in service.list_versions():
        if str(version["code"]) == WORKFLOW_CODE and str(version["status"]) == "PUBLISHED":
            existing = service.get_version(str(version["id"]))
            if _json(existing["workflow"]) == _json(graph):
                return {"version": existing, "created": False}
    version = service.register_package(
        WORKFLOW_CODE,
        "Qwen Image 2512 角色生产工作流（约 1MP）",
        graph,
        {
            "schema_version": "localdrama.workflow-contract.v2",
            "capability": "IMAGE_CHARACTER",
            "input_slots": {
                "PROMPT": {"kind": "TEXT", "min": 1, "max": 1, "required": True},
                "OUTPUT_PREFIX": {"kind": "TEXT", "min": 0, "max": 1, "required": False},
            },
            "output": {"media_kind": "IMAGE"},
            "verification": "CHARACTER_PRODUCTION_ROUTE_1MP",
        },
        BINDINGS,
        {"transport": "LOOPBACK_HTTP", "base_url": settings.comfy_base_url, "network_policy": "LOOPBACK_ONLY"},
    )
    validation = service.validate_against_comfy(str(version["id"]), ComfyClient(settings.comfy_base_url, settings.comfy_output_root))
    if validation["status"] != "PASS":
        raise DomainRuleError(
            "QWEN21_CHARACTER_WORKFLOW_BLOCKED",
            "角色生产工作流未通过本机 Comfy 节点/schema 验证。",
            {"missing_nodes": validation.get("missing_nodes"), "schema_errors": validation.get("schema_errors")},
        )
    published = service.publish(str(version["id"]), str(validation["validation_id"]), actor=ACTOR)
    return {"version": published, "created": True, "validation": validation}


def _source_profile_version(database: Database) -> dict[str, Any]:
    with database.connect() as connection:
        row = connection.execute(
            """SELECT v.* FROM execution_profile_versions v JOIN execution_profiles p ON p.id=v.execution_profile_id
               WHERE p.code=? AND v.status='PUBLISHED' ORDER BY v.version_no DESC LIMIT 1""",
            (PROFILE_CODE,),
        ).fetchone()
    if row is None:
        raise DomainRuleError("QWEN21_CHARACTER_PROFILE_MISSING", "缺少已发布的角色 Profile。", {"code": PROFILE_CODE})
    return dict(row)


def ensure_profile(database: Database, settings: Settings, workflow_version_id: str) -> dict[str, Any]:
    source = _source_profile_version(database)
    with database.connect() as connection:
        existing = connection.execute(
            """SELECT v.id, v.workflow_version_id, v.status FROM execution_profile_versions v
               JOIN execution_profiles p ON p.id=v.execution_profile_id
               WHERE p.code=? AND v.workflow_version_id=? AND v.status IN ('DRAFT','PUBLISHED')
               ORDER BY v.version_no DESC LIMIT 1""",
            (PROFILE_CODE, workflow_version_id),
        ).fetchone()
    if existing is not None and str(existing["status"]) == "PUBLISHED":
        return {"profile_version_id": str(existing["id"]), "created": False, "reused": True}

    service = ProfileService(database, settings.manifest_path)
    if existing is not None:
        version_id = str(existing["id"])
        created = False
    else:
        derived = service.derive_contract_version(
            str(source["id"]),
            int(source["revision"]),
            json.loads(str(source["input_contract_json"])),
            json.loads(str(source["parameter_schema_json"])),
            json.loads(str(source["output_contract_json"])),
            json.loads(str(source["resource_policy_json"])),
            actor=ACTOR,
        )
        version_id = str(derived["id"])
        created = True
        # The derived version inherits the source's workflow; re-bind it to the
        # production 1MP workflow, exactly as the existing activation script does.
        bundle = json.loads(str(source["model_bundle_json"] or "{}"))
        bundle.update({"workflow_version_id": workflow_version_id, "route_status": "production_workflow_verified"})
        with database.transaction() as connection:
            connection.execute(
                "UPDATE execution_profile_versions SET workflow_version_id=?,model_bundle_json=?,updated_at=?,revision=revision+1 WHERE id=?",
                (workflow_version_id, _json(bundle), _now(), version_id),
            )
    service.validate_contract_version(version_id, actor=ACTOR)
    service.validate_compatibility(version_id, actor=ACTOR)
    return {"profile_version_id": version_id, "created": created, "reused": False}


def _profile_fingerprint(database: Database, profile_version_id: str) -> str:
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM execution_profile_versions WHERE id=?", (profile_version_id,)).fetchone()
    if row is None:
        raise DomainRuleError("PROFILE_VERSION_NOT_FOUND", "ExecutionProfileVersion 不存在")
    return ProfileService._execution_fingerprint(row)


def run_evidence_job(database: Database, settings: Settings, profile_version_id: str, workflow_version_id: str) -> dict[str, Any]:
    with database.connect() as connection:
        project = connection.execute("SELECT id FROM projects ORDER BY created_at LIMIT 1").fetchone()
    if project is None:
        raise DomainRuleError("QWEN21_EVIDENCE_PROJECT_REQUIRED", "需要一个项目才能登记真实证据产物。")
    snapshot = {
        "purpose": "CHARACTER_PRODUCTION_ROUTE_PROBE",
        "workflow_version_id": workflow_version_id,
        "execution_snapshot": {
            "profile_version_id": profile_version_id,
            "workflow_version_id": workflow_version_id,
            "profile_execution_fingerprint": _profile_fingerprint(database, profile_version_id),
        },
        "semantic_inputs": {
            "PROMPT": "电影级角色设定图：一位中年女性，短发，深蓝色棉布外套，正面半身，柔和侧光，中性灰背景，写实皮肤质感",
            "NEGATIVE_PROMPT": "模糊，水印，文字，多余人像",
            "SEED": 9184001,
            "STEPS": STEPS,
            "CFG": 1.0,
            "DENOISE": 1.0,
            "SAMPLER_NAME": "euler",
            "SCHEDULER": "simple",
            "WIDTH": WIDTH,
            "HEIGHT": HEIGHT,
            "OUTPUT_PREFIX": "local_drama/character_route_evidence",
        },
        "media_bindings": [],
        "network_policy": "LOOPBACK_ONLY",
    }
    jobs = JobService(database, settings)
    job = jobs.create_job(
        str(project["id"]), "PROFILE_EVIDENCE_PROBE", "EXECUTION_PROFILE_VERSION", profile_version_id, "GPU_H3",
        snapshot, f"{ACTOR}:{profile_version_id}:{workflow_version_id}", execution_profile_version_id=profile_version_id,
        priority=1, max_attempts=1,
    )
    if job["state"] == "SUCCEEDED":
        return job
    runner = ComfyGenerationService(database, settings)
    submitted = runner.submit_next(ACTOR)
    if submitted is None or str(submitted["job"]["id"]) != str(job["id"]):
        raise DomainRuleError("QWEN21_EVIDENCE_JOB_NOT_NEXT", "证据任务不是下一个 GPU 任务。", {"job_id": str(job["id"])})
    attempt_id = str(submitted["attempt"]["id"])
    deadline = time.monotonic() + 3600
    while time.monotonic() < deadline:
        outcome = runner.poll_attempt(attempt_id, ACTOR)
        status = str(outcome["status"])
        if status == "SUCCEEDED":
            return jobs.get_job(str(job["id"]))
        if status in {"FAILED", "CANCELLED"}:
            raise DomainRuleError(
                "QWEN21_EVIDENCE_JOB_FAILED",
                "角色生产路由的真实证据任务失败。",
                {"job_id": str(job["id"]), "status": status, "error_code": outcome.get("error_code")},
            )
        time.sleep(2.0)
    raise DomainRuleError("QWEN21_EVIDENCE_JOB_TIMEOUT", "证据任务超时。")


def _artifact_id(job: dict[str, Any]) -> str:
    for attempt in reversed(job.get("attempts", [])):
        for artifact in reversed(attempt.get("artifacts", [])):
            if artifact.get("kind") == "COMFY_OUTPUT" and artifact.get("status") == "VERIFIED":
                return str(artifact["id"])
    raise DomainRuleError("QWEN21_EVIDENCE_ARTIFACT_MISSING", "证据任务没有已验证的 Comfy 产物。", {"job_id": str(job.get("id"))})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Show the plan without publishing anything.")
    parser.add_argument("--output", type=Path, default=_REPO_ROOT / "work" / "qwen21" / "character-route.json")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    database = Database(settings.database_path)
    report: dict[str, Any] = {
        "schema_version": "localdrama.qwen21-character-route.v1",
        "generated_at": _now(),
        "workflow_code": WORKFLOW_CODE,
        "profile_code": PROFILE_CODE,
        "frozen": {"width": WIDTH, "height": HEIGHT, "steps": STEPS, "pixels": WIDTH * HEIGHT},
    }
    if args.dry_run:
        graph = build_graph(database)
        report["plan"] = {
            "workflow_nodes": sorted({str(node["class_type"]) for node in graph.values()}),
            "bindings": sorted(BINDINGS),
            "source_profile": {k: str(v) for k, v in _source_profile_version(database).items() if k in {"id", "version_no", "status", "workflow_version_id"}},
        }
        report["passed"] = True
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    workflow = ensure_workflow(database, settings)
    report["workflow"] = {
        "workflow_version_id": str(workflow["version"]["id"]),
        "version_no": int(workflow["version"]["version_no"]),
        "content_hash": str(workflow["version"]["content_hash"]),
        "created": workflow["created"],
        "validation_status": (workflow.get("validation") or {}).get("status", "REUSED_PUBLISHED"),
    }
    profile = ensure_profile(database, settings, str(workflow["version"]["id"]))
    report["profile"] = profile
    if not profile.get("reused"):
        job = run_evidence_job(database, settings, str(profile["profile_version_id"]), str(workflow["version"]["id"]))
        media = MediaService(database, settings).promote_job_artifact(
            _artifact_id(job), purpose="PROFILE_EVIDENCE", media_kind="IMAGE", stage="KEYFRAME", actor=ACTOR
        )
        published = ProfileService(database, settings.manifest_path).publish_from_evidence(
            str(profile["profile_version_id"]), str(media["media_version_id"]), str(workflow["version"]["id"]), actor=ACTOR
        )
        report["evidence"] = {
            "job_id": str(job["id"]),
            "media_version_id": str(media["media_version_id"]),
            "published_status": str(published.get("status")),
        }
    report["passed"] = (
        report["workflow"]["validation_status"] == "PASS" or report["workflow"]["created"] is False
    ) and (bool(report.get("profile", {}).get("reused")) or report.get("evidence", {}).get("published_status") == "PUBLISHED")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
