"""Surface Qwen-Image-2.1 on the existing creator / batch pages.

The production pages read the legacy V1 Profile chain, whose executor used to
address one process-wide ComfyUI endpoint.  Two things were therefore needed:

1. the legacy executor must be able to run a Profile on its *own* recorded
   runtime (implemented in ``ComfyGenerationService``), because the production
   server on 8188 does not have the native 2.1 nodes; and
2. a published legacy Profile bound to a 2.1 graph.

This script performs (2): for each image capability it derives a new Profile
version from the current one, re-binds it to the published Qwen-Image-2.1
workflow, records the 2.1 runtime endpoint and the model code, then -- exactly
like the V2 path -- runs a real evidence job and publishes with
``publish_from_evidence``.

Licence behaviour, stated explicitly: every Profile this script publishes
records ``model_code = qwen-image-2.1-int8-convrot``.  Automatic default
selection skips models without a recorded commercial authorization, so 2.1
becomes *selectable* on those pages without silently becoming the production
default.  That separation is deliberate: Qwen-Image-2.1 is Qwen Research
Licensed.

Example::

    python scripts/qwen21/publish_qwen21_legacy_profiles.py --dry-run
    python scripts/qwen21/publish_qwen21_legacy_profiles.py --capability IMAGE_CHARACTER
"""

from __future__ import annotations

import argparse
import hashlib
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
from local_drama.config import Settings  # noqa: E402
from local_drama.domain.errors import DomainRuleError  # noqa: E402
from local_drama.infrastructure.database.sqlite import Database  # noqa: E402

ACTOR = "qwen21-legacy-route"
MODEL_CODE = "qwen-image-2.1-int8-convrot"
RUNTIME_CODE = "comfyui-qwen21"

# legacy profile code -> (capability, published 2.1 workflow code, probe inputs)
PLAN: dict[str, tuple[str, str, dict[str, Any]]] = {
    "local-suite-image-concept": (
        "IMAGE_CONCEPT", "QWEN_IMAGE_21_T2I_CONCEPT",
        {"size_preset": "square", "seed": 9184201,
         "prompt": "电影级概念图：雨后旧街，暖色路灯，湿润路面反光，写实摄影"},
    ),
    "local-suite-image-character": (
        "IMAGE_CHARACTER", "QWEN_IMAGE_21_T2I_CHARACTER",
        {"size_preset": "portrait", "seed": 9184202,
         "prompt": "电影级角色设定图：一位中年女性，短发，深蓝色棉布外套，正面半身，柔和侧光，中性灰背景"},
    ),
    "local-suite-image-scene": (
        "IMAGE_SCENE", "QWEN_IMAGE_21_T2I_SCENE",
        {"size_preset": "landscape", "seed": 9184203,
         "prompt": "电影级场景空镜：清晨的木质厨房，红陶茶壶与绿植，窗边柔光，无人物"},
    ),
    "local-suite-image-edit": (
        "IMAGE_EDIT", "QWEN_IMAGE_21_EDIT",
        {"seed": 9184204, "prompt": "把背景替换为纯蓝色摄影棚背景，保持主体形状、视角与光照不变",
         # The edit graph conditions on LoadImage, so the probe must supply a
         # reference; scripts/qwen21/seed_qwen21_inputs.py writes this into the
         # 2.1 runtime's input directory.
         "REFERENCE_IMAGE_1": "local_drama_qwen21_smoke_base.png"},
    ),
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _qwen21_runtime_version_id(database: Database) -> str:
    with database.connect() as connection:
        row = connection.execute(
            """SELECT version.id FROM mp_runtime_installation_versions version
               JOIN mp_runtime_installations runtime ON runtime.id=version.runtime_installation_id
               WHERE runtime.code=? ORDER BY version.version_no DESC LIMIT 1""",
            (RUNTIME_CODE,),
        ).fetchone()
    if row is None:
        raise DomainRuleError("QWEN21_RUNTIME_NOT_REGISTERED", "尚未登记 2.1 隔离运行时，请先运行 onboard_qwen21.py。")
    return str(row["id"])


def _workflow_version(database: Database, code: str) -> str:
    with database.connect() as connection:
        row = connection.execute(
            """SELECT wv.id FROM workflow_versions wv JOIN workflows w ON w.id=wv.workflow_id
               WHERE w.code=? AND wv.status='PUBLISHED' ORDER BY wv.version_no DESC LIMIT 1""",
            (code,),
        ).fetchone()
    if row is None:
        raise DomainRuleError("QWEN21_WORKFLOW_NOT_PUBLISHED", "该 2.1 工作流尚未发布。", {"code": code})
    return str(row["id"])


def _source_version(database: Database, profile_code: str) -> dict[str, Any]:
    with database.connect() as connection:
        row = connection.execute(
            """SELECT v.* FROM execution_profile_versions v JOIN execution_profiles p ON p.id=v.execution_profile_id
               WHERE p.code=? ORDER BY (v.status='PUBLISHED') DESC, v.version_no DESC LIMIT 1""",
            (profile_code,),
        ).fetchone()
    if row is None:
        raise DomainRuleError("QWEN21_PROFILE_MISSING", "缺少该能力的 legacy Profile。", {"code": profile_code})
    return dict(row)


def _existing(database: Database, profile_code: str, workflow_version_id: str) -> dict[str, Any] | None:
    with database.connect() as connection:
        row = connection.execute(
            """SELECT v.id, v.status FROM execution_profile_versions v JOIN execution_profiles p ON p.id=v.execution_profile_id
               WHERE p.code=? AND v.workflow_version_id=? ORDER BY v.version_no DESC LIMIT 1""",
            (profile_code, workflow_version_id),
        ).fetchone()
    return dict(row) if row else None


def ensure(database: Database, settings: Settings, profile_code: str, workflow_version_id: str, runtime_version_id: str) -> dict[str, Any]:
    found = _existing(database, profile_code, workflow_version_id)
    if found is not None and str(found["status"]) == "PUBLISHED":
        return {"profile_version_id": str(found["id"]), "reused": True, "created": False}
    source = _source_version(database, profile_code)
    service = ProfileService(database, settings.manifest_path)
    if found is not None:
        version_id, created = str(found["id"]), False
    else:
        derived = service.derive_contract_version(
            str(source["id"]), int(source["revision"]),
            json.loads(str(source["input_contract_json"])),
            json.loads(str(source["parameter_schema_json"])),
            json.loads(str(source["output_contract_json"])),
            json.loads(str(source["resource_policy_json"])),
            actor=ACTOR,
        )
        version_id, created = str(derived["id"]), True
        bundle = json.loads(str(source["model_bundle_json"] or "{}"))
        bundle.update({
            "workflow_version_id": workflow_version_id,
            "runtime_id": runtime_version_id,
            "model_code": MODEL_CODE,
            "route_status": "production_workflow_verified",
        })
        with database.transaction() as connection:
            connection.execute(
                """UPDATE execution_profile_versions
                   SET workflow_version_id=?, runtime_version_id=?, model_bundle_json=?, updated_at=?, revision=revision+1
                   WHERE id=?""",
                (workflow_version_id, runtime_version_id, _json(bundle), _now(), version_id),
            )
    service.validate_contract_version(version_id, actor=ACTOR)
    service.validate_compatibility(version_id, actor=ACTOR)
    return {"profile_version_id": version_id, "created": created, "reused": False}


def _fingerprint(database: Database, profile_version_id: str) -> str:
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM execution_profile_versions WHERE id=?", (profile_version_id,)).fetchone()
    if row is None:
        raise DomainRuleError("PROFILE_VERSION_NOT_FOUND", "ExecutionProfileVersion 不存在")
    return ProfileService._execution_fingerprint(row)


def run_evidence(database: Database, settings: Settings, profile_version_id: str, workflow_version_id: str, probe: dict[str, Any]) -> dict[str, Any]:
    with database.connect() as connection:
        project = connection.execute("SELECT id FROM projects ORDER BY created_at LIMIT 1").fetchone()
    if project is None:
        raise DomainRuleError("QWEN21_EVIDENCE_PROJECT_REQUIRED", "需要一个项目才能登记真实证据产物。")
    semantic = {"PROMPT": probe["prompt"], "SEED": probe["seed"], "OUTPUT_PREFIX": "local_drama/qwen21_legacy_evidence"}
    for role in ("REFERENCE_IMAGE_1", "REFERENCE_IMAGE_2"):
        if probe.get(role):
            semantic[role] = probe[role]
    snapshot = {
        "purpose": "QWEN21_LEGACY_ROUTE_PROBE",
        "workflow_version_id": workflow_version_id,
        "execution_snapshot": {
            "profile_version_id": profile_version_id,
            "workflow_version_id": workflow_version_id,
            "profile_execution_fingerprint": _fingerprint(database, profile_version_id),
        },
        "semantic_inputs": semantic,
        "media_bindings": [],
        "network_policy": "LOOPBACK_ONLY",
    }
    jobs = JobService(database, settings)
    # The key must change when the probe changes, otherwise a corrected retry
    # collides with the rejected attempt's frozen payload.
    probe_key = hashlib.sha256(_json(snapshot).encode("utf-8")).hexdigest()[:16]
    job = jobs.create_job(
        str(project["id"]), "PROFILE_EVIDENCE_PROBE", "EXECUTION_PROFILE_VERSION", profile_version_id, "GPU_H3",
        snapshot, f"{ACTOR}:{profile_version_id}:{probe_key}", execution_profile_version_id=profile_version_id,
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
                "QWEN21_EVIDENCE_JOB_FAILED", "2.1 legacy 路由的真实证据任务失败。",
                {"job_id": str(job["id"]), "status": status, "error_code": outcome.get("error_code"),
                 "error_detail": outcome.get("error_detail_redacted")},
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
    parser.add_argument("--capability", action="append", choices=tuple(item[0] for item in PLAN.values()), default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path, default=_REPO_ROOT / "work" / "qwen21" / "qwen21-legacy-routes.json")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    database = Database(settings.database_path)
    runtime_version_id = _qwen21_runtime_version_id(database)
    wanted = set(args.capability)
    selected = {code: item for code, item in PLAN.items() if not wanted or item[0] in wanted}

    report: dict[str, Any] = {
        "schema_version": "localdrama.qwen21-legacy-routes.v1",
        "generated_at": _now(),
        "model_code": MODEL_CODE,
        "runtime_version_id": runtime_version_id,
        "note": "记录 model_code 后，未记录商用授权的模型不会被自动选为默认路由；仍需显式选择。",
        "routes": [],
    }
    for profile_code, (capability, workflow_code, probe) in selected.items():
        workflow_version_id = _workflow_version(database, workflow_code)
        entry: dict[str, Any] = {
            "profile_code": profile_code, "capability": capability,
            "workflow_code": workflow_code, "workflow_version_id": workflow_version_id,
        }
        if args.dry_run:
            entry["status"] = "WOULD_PUBLISH"
            entry["source_version"] = {k: str(v) for k, v in _source_version(database, profile_code).items() if k in {"id", "version_no", "status"}}
            report["routes"].append(entry)
            continue
        ensured = ensure(database, settings, profile_code, workflow_version_id, runtime_version_id)
        entry.update({"profile_version_id": ensured["profile_version_id"], "created": ensured["created"], "reused": ensured["reused"]})
        if ensured["reused"]:
            entry["status"] = "ALREADY_PUBLISHED"
            report["routes"].append(entry)
            continue
        job = run_evidence(database, settings, str(ensured["profile_version_id"]), workflow_version_id, probe)
        media = MediaService(database, settings).promote_job_artifact(
            _artifact_id(job), purpose="PROFILE_EVIDENCE", media_kind="IMAGE", stage="KEYFRAME", actor=ACTOR
        )
        published = ProfileService(database, settings.manifest_path).publish_from_evidence(
            str(ensured["profile_version_id"]), str(media["media_version_id"]), workflow_version_id, actor=ACTOR
        )
        entry.update({
            "status": str(published.get("status")), "job_id": str(job["id"]),
            "media_version_id": str(media["media_version_id"]),
        })
        report["routes"].append(entry)

    report["passed"] = bool(report["routes"]) and all(
        item["status"] in {"PUBLISHED", "ALREADY_PUBLISHED", "WOULD_PUBLISH"} for item in report["routes"]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
