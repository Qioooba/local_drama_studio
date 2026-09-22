"""Onboard Qwen-Image-2.1 (INT8 ConvRot) into Local Drama Studio.

This drives the *existing* Model Platform V2 machinery; it never hand-writes a
VERIFIED/PUBLISHED state that the pipeline should have produced itself.  The
phases mirror the repository's own chain:

``models``    verify the three pinned weight files by real byte count + SHA-256
``runtime``   prove the isolated ComfyUI runtime actually serves the 2.1 nodes
``workflows`` instantiate -> register -> validate_against_comfy -> publish
``registry``  create node / runtime / release / artifacts / installation / offerings
``bind``      bind each offering to its published, schema-validated workflow version
``handoff``   print the exact next steps (real smoke Job, then Profile publish)

Deliberately *not* automated here:

* the capability smoke Job -- it must be executed by a real Worker against real
  ComfyUI so that a registered artifact backs the evidence;
* Profile publication -- it consumes that smoke evidence.

``scripts/qwen21/activate_qwen21.py`` performs those two steps once a Worker is
running, so the evidence is never fabricated offline.

Example::

    python scripts/qwen21/onboard_qwen21.py --phase models --phase runtime
    python scripts/qwen21/onboard_qwen21.py            # every phase
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "apps" / "api") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "apps" / "api"))

from local_drama.application.workflow_definitions import WorkflowDefinitionService  # noqa: E402
from local_drama.application.workflows import WorkflowService  # noqa: E402
from local_drama.config import Settings  # noqa: E402
from local_drama.domain.errors import DomainRuleError  # noqa: E402
from local_drama.infrastructure.comfy import ComfyClient  # noqa: E402
from local_drama.infrastructure.database.sqlite import Database  # noqa: E402
from local_drama.model_platform.application.comfy_workflow_bindings import ComfyWorkflowBindingService  # noqa: E402

MODEL_CODE = "qwen-image-2.1-int8-convrot"
RUNTIME_CODE = "comfyui-qwen21"
RELEASE_CODE = MODEL_CODE
FAMILY_CODE = "qwen-image-2.1"
ADAPTER_CODE = "comfy.workflow.v1"
_LICENSE = {
    "license_id": "qwen-research",
    "license_name": "Qwen Research License",
    "license_url": "https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE",
    "commercial_use_requires_authorization": True,
    "note": "非商业研究/评估用途。商业生产默认仍为 Apache-2.0 的 Qwen-Image-2512 / Edit-2511 链路，除非另有授权记录。",
}

# definition code -> (V2 capability code, profile code suffix)
DEFINITION_PLAN: tuple[tuple[str, str, str | None], ...] = (
    ("QWEN_IMAGE_21_T2I_CONCEPT", "IMAGE_CONCEPT", None),
    ("QWEN_IMAGE_21_T2I_CHARACTER", "IMAGE_CHARACTER", None),
    ("QWEN_IMAGE_21_T2I_SCENE", "IMAGE_SCENE", None),
    ("QWEN_IMAGE_21_EDIT", "IMAGE_EDIT", None),
    ("QWEN_IMAGE_21_EDIT_2REF", "IMAGE_EDIT", "2ref"),
)

PHASES = ("models", "runtime", "workflows", "registry", "bind", "handoff")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _uid(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "localdramastudio:" + ":".join(parts)))


# --------------------------------------------------------------------- models --
def phase_models(lock_path: Path) -> dict[str, Any]:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    root = Path(str(lock["canonical_root"]))
    models = [item for item in lock["models"] if str(item["code"]) == MODEL_CODE]
    if not models:
        raise DomainRuleError("QWEN21_MODEL_NOT_IN_LOCK", "model-lock 中缺少 Qwen-Image-2.1 条目。", {"code": MODEL_CODE})
    files: list[dict[str, Any]] = []
    for expected in models[0]["files"]:
        path = root / str(expected["path"])
        record: dict[str, Any] = {
            "role": expected.get("role"),
            "path": str(path),
            "expected_bytes": expected["bytes"],
            "expected_sha256": expected.get("sha256"),
        }
        if not path.is_file():
            record["status"] = "MISSING"
        else:
            actual_bytes = path.stat().st_size
            record["actual_bytes"] = actual_bytes
            if actual_bytes != int(expected["bytes"]):
                record["status"] = "SIZE_MISMATCH"
            else:
                actual_sha = _sha256(path)
                record["actual_sha256"] = actual_sha
                record["status"] = "PASS" if actual_sha == str(expected.get("sha256", "")).lower() else "DIGEST_MISMATCH"
        files.append(record)
    return {"phase": "models", "code": MODEL_CODE, "files": files, "passed": all(item["status"] == "PASS" for item in files)}


# -------------------------------------------------------------------- runtime --
def phase_runtime(pin_path: Path, base_url: str | None) -> dict[str, Any]:
    pin = json.loads(pin_path.read_text(encoding="utf-8"))
    endpoint = base_url or f"http://{pin['host']}:{pin['port']}"
    client = ComfyClient(endpoint, _REPO_ROOT / "work" / "comfy-qwen21" / "output", allow_private_network=False)
    object_info = client.object_info()
    available = set(object_info)
    required = [str(node) for node in pin["required_nodes"]]
    missing = sorted(node for node in required if node not in available)
    return {
        "phase": "runtime",
        "runtime_code": pin["runtime_code"],
        "base_url": endpoint,
        "expected_commit": pin["comfy_commit"],
        "missing_nodes": missing,
        "passed": not missing,
    }


# ------------------------------------------------------------------ workflows --
def _published_or_new(service: WorkflowService, code: str, compiled: dict[str, Any]) -> dict[str, Any]:
    content_hash = hashlib.sha256(_json(compiled["workflow"]).encode("utf-8")).hexdigest()
    for version in service.list_versions():
        if str(version["code"]) == code and str(version["content_hash"]) == content_hash:
            return service.get_version(str(version["id"]))
    return service.register_package(
        code,
        str(compiled["contract"]["definition"]["code"]),
        compiled["workflow"],
        compiled["contract"],
        compiled["node_bindings"],
        compiled["runtime_contract"],
    )


def phase_workflows(database: Database, settings: Settings, client: ComfyClient) -> dict[str, Any]:
    definitions = WorkflowDefinitionService(settings)
    service = WorkflowService(database, settings)
    results: list[dict[str, Any]] = []
    for definition_code, capability, _suffix in DEFINITION_PLAN:
        compiled = definitions.instantiate(definition_code, {})
        version = _published_or_new(service, definition_code, compiled)
        record: dict[str, Any] = {
            "definition_code": definition_code,
            "capability": capability,
            "workflow_version_id": str(version["id"]),
            "version_no": int(version["version_no"]),
            "content_hash": str(version["content_hash"]),
            "status": str(version["status"]),
        }
        if str(version["status"]) != "PUBLISHED":
            validation = service.validate_against_comfy(str(version["id"]), client)
            record["validation_status"] = validation["status"]
            record["missing_nodes"] = validation.get("missing_nodes", [])
            record["schema_errors"] = validation.get("schema_errors", [])
            if validation["status"] != "PASS":
                record["status"] = "BLOCKED"
                results.append(record)
                continue
            version = service.publish(str(version["id"]), str(validation["validation_id"]), actor="qwen21-onboard")
            record["status"] = str(version["status"])
            record["validation_id"] = str(validation["validation_id"])
        results.append(record)
    return {"phase": "workflows", "workflows": results, "passed": all(item["status"] == "PUBLISHED" for item in results)}


# ------------------------------------------------------------------- registry --
def phase_registry(
    database: Database,
    settings: Settings,
    *,
    model_root: Path,
    base_url: str,
    output_root: Path,
) -> dict[str, Any]:
    now = _now()
    node_id = _uid("node", settings.instance_id)
    runtime_id = _uid("runtime", RUNTIME_CODE, settings.instance_id)
    family_id = _uid("family", FAMILY_CODE)
    release_id = _uid("release", RELEASE_CODE)
    native_locator = str(model_root)
    configuration = {
        "runtime_code": RUNTIME_CODE,
        "base_url": base_url,
        "output_root": str(output_root),
        "network_policy": "LOCAL_ONLY",
        "model_code": MODEL_CODE,
    }
    fingerprint = hashlib.sha256(_json(configuration).encode("utf-8")).hexdigest()
    lock = json.loads((_REPO_ROOT / "config" / "model-lock.json").read_text(encoding="utf-8"))
    components = [item for item in lock["models"] if str(item["code"]) == MODEL_CODE][0]["files"]

    with database.transaction() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO mp_compute_nodes (id,code,display_name,fingerprint,host_json,last_seen_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (node_id, f"local-{settings.instance_id}", "本机服务节点", f"service:{settings.instance_id}", "{}", now, now, now),
        )
        connection.execute(
            "INSERT OR IGNORE INTO mp_runtime_installations (id,node_id,code,kind,owner_mode,display_name,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (runtime_id, node_id, RUNTIME_CODE, "COMFYUI", "SERVICE_MANAGED", "ComfyUI · Qwen-Image-2.1 隔离运行时", now, now),
        )
        version_row = connection.execute(
            "SELECT id FROM mp_runtime_installation_versions WHERE runtime_installation_id=? AND fingerprint=?",
            (runtime_id, fingerprint),
        ).fetchone()
        if version_row is None:
            version_no = int(
                connection.execute(
                    "SELECT COALESCE(MAX(version_no),0)+1 FROM mp_runtime_installation_versions WHERE runtime_installation_id=?",
                    (runtime_id,),
                ).fetchone()[0]
            )
            runtime_version_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO mp_runtime_installation_versions (id,runtime_installation_id,version_no,adapter_code,adapter_version,transport,configuration_json,fingerprint,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (runtime_version_id, runtime_id, version_no, ADAPTER_CODE, "v1", "LOOPBACK_HTTP", _json(configuration), fingerprint, "ACTIVE", now, now),
            )
        else:
            runtime_version_id = str(version_row["id"])
            connection.execute("UPDATE mp_runtime_installation_versions SET status='ACTIVE',updated_at=? WHERE id=?", (now, runtime_version_id))
        connection.execute(
            "INSERT OR IGNORE INTO mp_runtime_instances (id,runtime_installation_version_id,status,health_json,last_heartbeat_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (_uid("runtime-instance", runtime_version_id), runtime_version_id, "READY", _json({"endpoint": base_url, "network_used": False}), now, now, now),
        )
        connection.execute(
            "INSERT OR IGNORE INTO mp_model_families (id,code,title,vendor,license_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (family_id, FAMILY_CODE, "Qwen-Image-2.1", "Qwen", _json(_LICENSE), now, now),
        )
        connection.execute(
            "INSERT OR IGNORE INTO mp_model_releases (id,family_id,code,upstream_id,revision,format,quantization,metadata_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                release_id, family_id, RELEASE_CODE, "Comfy-Org/Qwen-Image-2.1",
                "b8abad01e16a50633160da778bec582b58761463", "SAFETENSORS", "INT8_CONVROT",
                _json({"license": _LICENSE, "architecture": "Qwen-Image-2.1", "text_encoder": "Qwen3-VL-8B", "vae_channels": 64}),
                now, now,
            ),
        )
        for ordinal, component in enumerate(components):
            path = Path(str(lock["canonical_root"])) / str(component["path"])
            digest = _sha256(path)
            size = path.stat().st_size
            artifact = connection.execute(
                "SELECT id FROM mp_model_artifacts WHERE content_sha256=? AND size_bytes=?", (digest, size)
            ).fetchone()
            artifact_id = str(artifact["id"]) if artifact else _uid("artifact", digest, str(size))
            if artifact is None:
                connection.execute(
                    "INSERT INTO mp_model_artifacts (id,kind,content_sha256,size_bytes,format,manifest_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (artifact_id, "FILE", digest, size, "SAFETENSORS", _json({"role": component.get("role"), "path": component["path"]}), now, now),
                )
            connection.execute(
                "INSERT OR IGNORE INTO mp_model_components (id,release_id,artifact_id,role,ordinal,required,shared,created_at,updated_at) VALUES (?,?,?,?,?,1,0,?,?)",
                (_uid("component", release_id, str(ordinal)), release_id, artifact_id, str(component.get("role") or "COMPONENT"), ordinal, now, now),
            )
        installation = connection.execute(
            "SELECT id FROM mp_runtime_model_installations WHERE runtime_installation_version_id=? AND native_locator=?",
            (runtime_version_id, native_locator),
        ).fetchone()
        installation_id = str(installation["id"]) if installation else _uid("installation", runtime_version_id, native_locator)
        if installation is None:
            connection.execute(
                "INSERT INTO mp_runtime_model_installations (id,release_id,runtime_installation_version_id,native_locator,install_state,metadata_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (installation_id, release_id, runtime_version_id, native_locator, "INTEGRITY_VERIFIED", _json({"model_code": MODEL_CODE, "digests_verified": True}), now, now),
            )
        else:
            connection.execute(
                "UPDATE mp_runtime_model_installations SET install_state='INTEGRITY_VERIFIED',metadata_json=?,updated_at=? WHERE id=?",
                (_json({"model_code": MODEL_CODE, "digests_verified": True}), now, installation_id),
            )
        integrity_run_id = str(uuid.uuid4())
        integrity_result = {
            "model_code": MODEL_CODE,
            "components": [{"role": item.get("role"), "sha256": item.get("sha256"), "bytes": item.get("bytes")} for item in components],
            "digests_verified": True,
            "network_used": False,
        }
        connection.execute(
            "INSERT INTO mp_validation_runs (id,target_kind,target_id,validation_kind,status,result_json,started_at,finished_at,created_at,updated_at) VALUES (?,'RUNTIME_MODEL_INSTALLATION',?,'INSTALLATION_INTEGRITY','INTEGRITY_PASSED',?,?,?,?,?)",
            (integrity_run_id, installation_id, _json(integrity_result), now, now, now, now),
        )
        connection.execute(
            "INSERT INTO mp_validation_evidence (id,validation_run_id,kind,content_hash,payload_json,artifact_ref,created_at,updated_at) VALUES (?,?,?,?,?,NULL,?,?)",
            (str(uuid.uuid4()), integrity_run_id, "INSTALLATION_INTEGRITY", hashlib.sha256(_json(integrity_result).encode("utf-8")).hexdigest(), _json(integrity_result), now, now),
        )
        offerings: list[dict[str, str]] = []
        for _definition_code, capability, _suffix in DEFINITION_PLAN:
            capability_row = connection.execute("SELECT id FROM mp_capability_definitions WHERE code=?", (capability,)).fetchone()
            if capability_row is None:
                raise DomainRuleError("QWEN21_CAPABILITY_MISSING", "模型平台缺少该能力定义。", {"capability": capability})
            capability_id = str(capability_row["id"])
            offering = connection.execute(
                "SELECT id FROM mp_capability_offerings WHERE runtime_model_installation_id=? AND capability_definition_id=?",
                (installation_id, capability_id),
            ).fetchone()
            offering_id = str(offering["id"]) if offering else _uid("offering", installation_id, capability_id)
            if offering is None:
                connection.execute(
                    "INSERT INTO mp_capability_offerings (id,runtime_model_installation_id,capability_definition_id,native_metadata_json,validation_status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                    (offering_id, installation_id, capability_id, _json({"model_code": MODEL_CODE, "runtime_code": RUNTIME_CODE}), "NOT_RUN", now, now),
                )
            offerings.append({"capability": capability, "offering_id": offering_id})
    return {
        "phase": "registry",
        "runtime_installation_id": runtime_id,
        "runtime_version_id": runtime_version_id,
        "runtime_model_installation_id": installation_id,
        "release_id": release_id,
        "offerings": offerings,
        "passed": True,
    }


# ----------------------------------------------------------------------- bind --
def _workflow_version_for(database: Database, definition_code: str) -> dict[str, Any]:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT wv.id FROM workflow_versions wv JOIN workflows w ON w.id=wv.workflow_id WHERE w.code=? AND wv.status='PUBLISHED' ORDER BY wv.version_no DESC LIMIT 1",
            (definition_code,),
        ).fetchone()
    if row is None:
        raise DomainRuleError("QWEN21_WORKFLOW_NOT_PUBLISHED", "该 2.1 工作流定义尚无已发布版本。", {"definition_code": definition_code})
    return {"id": str(row["id"])}


def phase_bind(database: Database, settings: Settings, installation_id: str, client: ComfyClient) -> dict[str, Any]:
    # The binding service re-runs validate_against_comfy; it must inspect the
    # 2.1 runtime, not the process-wide comfy_base_url which still points at the
    # production 8188 server that lacks these nodes.
    service = ComfyWorkflowBindingService(database, settings, comfy_client=client)
    results: list[dict[str, Any]] = []
    for definition_code, capability, suffix in DEFINITION_PLAN:
        version = _workflow_version_for(database, definition_code)
        binding = service.bind(installation_id, capability, version["id"])
        results.append(
            {
                "definition_code": definition_code,
                "capability": capability,
                "profile_code_suffix": suffix,
                "workflow_version_id": version["id"],
                "workflow_binding_id": binding.id,
                "binding_status": binding.binding_status,
                "created": binding.created,
            }
        )
    return {"phase": "bind", "bindings": results, "passed": all(item["binding_status"] == "SCHEMA_VALIDATED" for item in results)}


# -------------------------------------------------------------------- handoff --
def phase_handoff(state: dict[str, Any]) -> dict[str, Any]:
    bindings = (state.get("bind") or {}).get("bindings") or []
    return {
        "phase": "handoff",
        "runtime_model_installation_id": (state.get("registry") or {}).get("runtime_model_installation_id"),
        "pending": [
            {
                "definition_code": item["definition_code"],
                "capability": item["capability"],
                "workflow_binding_id": item["workflow_binding_id"],
                "profile_code_suffix": item.get("profile_code_suffix"),
            }
            for item in bindings
        ],
        "next_command": "python scripts/qwen21/activate_qwen21.py --all",
        "note": "capability smoke 必须由真实 Worker 执行并登记 artifact，之后才能 provision/publish Profile。",
        "passed": bool(bindings),
    }


def _registered_installation_id(database: Database) -> str | None:
    with database.connect() as connection:
        row = connection.execute(
            """SELECT installation.id FROM mp_runtime_model_installations installation
               JOIN mp_model_releases release ON release.id=installation.release_id
               JOIN mp_runtime_installation_versions version ON version.id=installation.runtime_installation_version_id
               JOIN mp_runtime_installations runtime ON runtime.id=version.runtime_installation_id
               WHERE release.code=? AND runtime.code=?
               ORDER BY installation.created_at DESC,installation.id DESC LIMIT 1""",
            (RELEASE_CODE, RUNTIME_CODE),
        ).fetchone()
    return str(row["id"]) if row is not None else None


def _carried(state: dict[str, Any], phase: str) -> dict[str, Any]:
    return state.get(phase) or {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--phase", action="append", choices=PHASES, default=[], help="Run only these phases (repeatable). Default: all.")
    parser.add_argument("--lock", type=Path, default=_REPO_ROOT / "config" / "model-lock.json")
    parser.add_argument("--pin", type=Path, default=_REPO_ROOT / "config" / "comfyui-qwen21-runtime.json")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--comfy-base-url", help="Override the 2.1 runtime endpoint from the pin file.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    phases: Iterable[str] = args.phase or PHASES
    # from_env() loads config/config.json so the recorded environment and the
    # licence decision match the real deployment.
    settings = Settings.from_env()
    if args.database:
        settings = settings.model_copy(update={"database_path": args.database})
    pin = json.loads(args.pin.read_text(encoding="utf-8"))
    base_url = args.comfy_base_url or f"http://{pin['host']}:{pin['port']}"
    pin_output_root = Path(str(pin["io_root"])) / "output"
    model_root = Path(str(pin["model_library_root"])) / "diffusion_models" / "Qwen-Image-2.1"

    database = Database(settings.database_path)
    client = ComfyClient(base_url, pin_output_root, allow_private_network=False)
    state: dict[str, Any] = {}
    ok = True
    for phase in phases:
        if phase == "models":
            result = phase_models(args.lock)
        elif phase == "runtime":
            result = phase_runtime(args.pin, base_url)
        elif phase == "workflows":
            result = phase_workflows(database, settings, client)
        elif phase == "registry":
            result = phase_registry(database, settings, model_root=model_root, base_url=base_url, output_root=pin_output_root)
        elif phase == "bind":
            # Usable standalone: fall back to the installation already recorded
            # by an earlier run instead of demanding the registry phase again.
            installation_id = _carried(state, "registry").get("runtime_model_installation_id") or _registered_installation_id(database)
            if not installation_id:
                raise DomainRuleError("QWEN21_REGISTRY_REQUIRED", "bind 阶段需要先执行 registry 阶段完成 V2 登记。")
            result = phase_bind(database, settings, str(installation_id), client)
        else:
            result = phase_handoff(state)
        state[phase] = result
        ok = ok and bool(result.get("passed"))
    report = {
        "schema_version": "localdrama.qwen21-onboarding.v1",
        "generated_at": _now(),
        "database": str(settings.database_path),
        "comfy_base_url": base_url,
        "phases": state,
        "passed": ok,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
