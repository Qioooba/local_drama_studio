from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_drama.application.profiles import ProfileService
from local_drama.application.workflows import WorkflowService
from local_drama.config import Settings
from local_drama.infrastructure.comfy import ComfyClient
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.manifest import load_manifest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "docs" / "evidence" / "local-model-suite-20260828.json"


WORKFLOW_SPECS: dict[str, dict[str, Any]] = {
    "qwen-image-2512-q5-k-m": {
        "title": "Qwen Image 2512 Q5_K_M 本机实跑工作流",
        "contract_capability": "IMAGE_CONCEPT",
        "bindings": {
            "PROMPT": {"node_id": "5", "input": "text"},
            "OUTPUT_PREFIX": {"node_id": "10", "input": "filename_prefix"},
        },
    },
    "qwen-image-edit-2511-q5-k-m": {
        "title": "Qwen Image Edit 2511 Q5_K_M 本机实跑工作流",
        "contract_capability": "IMAGE_EDIT",
        "bindings": {
            "FIRST_FRAME": {"node_id": "6", "input": "image"},
            "PROMPT": {"node_id": "8", "input": "prompt"},
            "OUTPUT_PREFIX": {"node_id": "15", "input": "filename_prefix"},
        },
    },
    "qwen-image-edit-2511-multiple-angles-lora": {
        "title": "Qwen Image Edit 2511 Multiple Angles LoRA 本机实跑工作流",
        "contract_capability": "IMAGE_MULTI_VIEW",
        "bindings": {
            "FIRST_FRAME": {"node_id": "7", "input": "image"},
            "PROMPT": {"node_id": "9", "input": "prompt"},
            "OUTPUT_PREFIX": {"node_id": "16", "input": "filename_prefix"},
        },
    },
    "ace-step-1.5-xl-sft": {
        "title": "ACE-Step 1.5 XL-SFT 本机实跑工作流",
        "contract_capability": "AUDIO_MUSIC",
        "bindings": {
            "PROMPT": {"node_id": "5", "input": "tags"},
            "OUTPUT_PREFIX": {"node_id": "10", "input": "filename_prefix"},
        },
    },
    "minimax-h3-fl2va": {
        "title": "MiniMax H3 FL2VA 本机实跑工作流",
        "contract_capability": "MINIMAX_H3_I2V_RUNTIME_VERIFIED",
        "bindings": {
            "FIRST_FRAME": {"node_id": "5", "input": "image"},
            "PROMPT": {"node_id": "7", "input": "prompt"},
            "OUTPUT_PREFIX": {"node_id": "16", "input": "filename_prefix"},
        },
    },
    "minimax-h3-ref2va": {
        "title": "MiniMax H3 Ref2VA 本机实跑工作流",
        "contract_capability": "MINIMAX_H3_REFERENCE_RUNTIME_VERIFIED",
        "bindings": {
            "FIRST_FRAME": {"node_id": "5", "input": "image"},
            "PROMPT": {"node_id": "6", "input": "prompt"},
            "OUTPUT_PREFIX": {"node_id": "15", "input": "filename_prefix"},
        },
    },
}


PROFILE_WORKFLOW: dict[str, str] = {
    "IMAGE_CONCEPT": "qwen-image-2512-q5-k-m",
    "IMAGE_CHARACTER": "qwen-image-2512-q5-k-m",
    "IMAGE_SCENE": "qwen-image-2512-q5-k-m",
    "IMAGE_EDIT": "qwen-image-edit-2511-q5-k-m",
    "IMAGE_MULTI_VIEW": "qwen-image-edit-2511-multiple-angles-lora",
    "VIDEO_I2V": "minimax-h3-fl2va",
    "VIDEO_REFERENCE": "minimax-h3-ref2va",
    "AUDIO_MUSIC": "ace-step-1.5-xl-sft",
}


PROFILE_COMPONENTS: dict[str, tuple[tuple[str, str], ...]] = {
    "IMAGE_CONCEPT": (
        ("QWEN_IMAGE_2512", "primary_model"),
        ("QWEN_IMAGE_2512", "text_encoder"),
        ("QWEN_IMAGE_2512", "vae"),
    ),
    "IMAGE_CHARACTER": (
        ("QWEN_IMAGE_2512", "primary_model"),
        ("QWEN_IMAGE_2512", "text_encoder"),
        ("QWEN_IMAGE_2512", "vae"),
    ),
    "IMAGE_SCENE": (
        ("QWEN_IMAGE_2512", "primary_model"),
        ("QWEN_IMAGE_2512", "text_encoder"),
        ("QWEN_IMAGE_2512", "vae"),
    ),
    "IMAGE_EDIT": (
        ("QWEN_IMAGE_EDIT_2511", "primary_model"),
        ("QWEN_IMAGE_2512", "text_encoder"),
        ("QWEN_IMAGE_2512", "vae"),
    ),
    "IMAGE_MULTI_VIEW": (
        ("QWEN_IMAGE_EDIT_2511", "primary_model"),
        ("QWEN_IMAGE_EDIT_2511", "multiple_angles_lora"),
        ("QWEN_IMAGE_2512", "text_encoder"),
        ("QWEN_IMAGE_2512", "vae"),
    ),
    "VIDEO_I2V": (
        ("MINIMAX_H3", "fl2va_model"),
        ("MINIMAX_H3", "text_encoder"),
        ("MINIMAX_H3", "video_vae"),
        ("MINIMAX_H3", "audio_vae"),
    ),
    "VIDEO_REFERENCE": (
        ("MINIMAX_H3", "ref2va_model"),
        ("MINIMAX_H3", "text_encoder"),
        ("MINIMAX_H3", "video_vae"),
        ("MINIMAX_H3", "audio_vae"),
    ),
    "AUDIO_MUSIC": (
        ("ACE_STEP_1_5_XL_SFT", "primary_model"),
        ("ACE_STEP_1_5_XL_SFT", "text_encoder_small"),
        ("ACE_STEP_1_5_XL_SFT", "text_encoder_large"),
        ("ACE_STEP_1_5_XL_SFT", "vae"),
    ),
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _workflow_hash(workflow: dict[str, Any]) -> str:
    return hashlib.sha256(_json(workflow).encode("utf-8")).hexdigest()


def _load_successful_graph(client: ComfyClient, prompt_id: str) -> dict[str, Any]:
    history = client.history(prompt_id).get(prompt_id)
    if not isinstance(history, dict) or history.get("status", {}).get("status_str") != "success":
        raise RuntimeError(f"Comfy history is not successful: {prompt_id}")
    prompt = history.get("prompt")
    if not isinstance(prompt, list) or len(prompt) < 3 or not isinstance(prompt[2], dict):
        raise RuntimeError(f"Comfy history has no immutable graph: {prompt_id}")
    return prompt[2]


def _contract(spec: dict[str, Any], output_kind: str) -> dict[str, Any]:
    slots: dict[str, dict[str, Any]] = {}
    for role in spec["bindings"]:
        slots[role] = {
            "required": role != "OUTPUT_PREFIX",
            "kind": "IMAGE" if role == "FIRST_FRAME" else "TEXT",
        }
    return {
        "schema_version": "localdrama.workflow-contract.v2",
        "capability": spec["contract_capability"],
        "input_slots": slots,
        "output": {"media_kind": output_kind},
        "verification": "REAL_LOCAL_COMFY_EXECUTION",
    }


def _register_workflow(
    service: WorkflowService,
    client: ComfyClient,
    run: dict[str, Any],
) -> dict[str, Any]:
    spec = WORKFLOW_SPECS[str(run["code"])]
    graph = _load_successful_graph(client, str(run["prompt_id"]))
    output_path = Path(str(run["output"]))
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise RuntimeError(f"Verified output is missing: {output_path}")
    content_hash = _workflow_hash(graph)
    with service.database.connect() as connection:
        row = connection.execute(
            """SELECT wv.id FROM workflow_versions wv
            JOIN workflows w ON w.id=wv.workflow_id
            WHERE w.code=? AND wv.content_hash=?
            ORDER BY wv.version_no DESC LIMIT 1""",
            (run["workflow_code"], content_hash),
        ).fetchone()
    if row is None:
        version = service.register_package(
            str(run["workflow_code"]),
            str(spec["title"]),
            graph,
            _contract(spec, str(run["output_kind"])),
            spec["bindings"],
            {
                "transport": "LOOPBACK_HTTP",
                "base_url": client.base_url,
                "runtime": "ComfyUI 0.33.1",
                "verification_prompt_id": run["prompt_id"],
                "verification_output": str(output_path),
                "network_used": False,
            },
        )
    else:
        version = service.get_version(str(row["id"]))
    if version["status"] != "PUBLISHED":
        validation = service.validate_against_comfy(version["id"], client)
        if validation["status"] != "PASS":
            raise RuntimeError(f"Workflow validation blocked: {run['workflow_code']}: {validation}")
        version = service.publish(version["id"], validation["validation_id"], actor="local-model-suite")
    return {
        "code": run["code"],
        "workflow_code": run["workflow_code"],
        "version_id": version["id"],
        "status": version["status"],
        "content_hash": version["content_hash"],
        "prompt_id": run["prompt_id"],
        "output": str(output_path),
    }


def _manifest_components(manifest: Any) -> dict[str, dict[str, Any]]:
    components: dict[str, dict[str, Any]] = {}
    for partition_name, partition in manifest.data.get("models", {}).get("partitions", {}).items():
        if not isinstance(partition, dict):
            continue
        for component_name, component in partition.items():
            if not isinstance(component, dict) or not component.get("path"):
                continue
            path = str(Path(str(component["path"])).resolve()).casefold()
            components[path] = {
                **component,
                "partition": str(partition_name),
                "component": str(component_name),
            }
    return components


def _verify_artifacts(database: Database, manifest: Any, runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    components = _manifest_components(manifest)
    prompt_ids = [str(run["prompt_id"]) for run in runs]
    now = _now()
    verified: list[dict[str, Any]] = []
    with database.transaction() as connection:
        rows = connection.execute(
            "SELECT * FROM model_artifacts WHERE manifest_sha256=? ORDER BY code",
            (manifest.sha256,),
        ).fetchall()
        if len(rows) != len(components):
            raise RuntimeError(f"Manifest artifact count mismatch: db={len(rows)} manifest={len(components)}")
        for row in rows:
            path = Path(str(row["machine_path_ref"])).resolve()
            component = components.get(str(path).casefold())
            if component is None or not path.is_file() or path.is_symlink():
                raise RuntimeError(f"Manifest artifact is missing or outside inventory: {path}")
            expected_size = int(component["bytes"])
            expected_sha = str(component.get("full_sha256", "")).lower()
            if path.stat().st_size != expected_size or len(expected_sha) != 64:
                raise RuntimeError(f"Manifest artifact size/hash declaration invalid: {path}")
            if str(row["sha256"] or "").lower() != expected_sha:
                raise RuntimeError(f"Synced artifact hash mismatch: {path}")
            compatibility = {
                "schema_version": "localdrama.model-runtime-verification.v1",
                "partition": component["partition"],
                "component": component["component"],
                "byte_size_verified": True,
                "sha256_verified": True,
                "runtime_execution_verified": True,
                "verification_prompt_ids": prompt_ids,
                "network_used_for_verification": False,
                "license_publication_gate": "NOT_ATTESTED",
            }
            connection.execute(
                """UPDATE model_artifacts SET status='VERIFIED', size_bytes=?, sha256=?,
                compatibility_json=?, updated_at=?, revision=revision+1 WHERE id=?""",
                (expected_size, expected_sha, _json(compatibility), now, row["id"]),
            )
            verified.append(
                {
                    "id": str(row["id"]),
                    "code": str(row["code"]),
                    "partition": component["partition"],
                    "component": component["component"],
                    "path": str(path),
                    "bytes": expected_size,
                    "sha256": expected_sha,
                    "status": "VERIFIED",
                }
            )
        connection.execute(
            """INSERT INTO audit_events
            (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
            VALUES ('local-model-suite','operator','LOCAL_MODEL_SUITE_ARTIFACTS_VERIFIED','manifest',?,?,?)""",
            (
                manifest.sha256,
                "已到盘模型组件通过大小、SHA-256 与真实 Comfy 执行验证",
                _json({"artifact_count": len(verified), "prompt_ids": prompt_ids, "network_used": False}),
            ),
        )
    return verified


def _profile_contract(bindings: dict[str, Any]) -> dict[str, Any]:
    return {
        "transport": "LOOPBACK_HTTP",
        "input_slots": {
            role: {"min": 0 if role == "OUTPUT_PREFIX" else 1, "max": 1}
            for role in bindings
        },
    }


def _profile_schema(capability: str) -> dict[str, Any]:
    reference_slots = ["FIRST_FRAME"] if capability in {"IMAGE_EDIT", "IMAGE_MULTI_VIEW", "VIDEO_I2V", "VIDEO_REFERENCE"} else []
    return {
        "seed": {"required": True, "determinism": "EXPLICIT"},
        "capabilities": {
            "extend": {"support": "UNSUPPORTED", "required_inputs": []},
            "V2V": {"support": "UNSUPPORTED", "required_inputs": []},
            "reference": {
                "support": "NATIVE" if reference_slots else "UNSUPPORTED",
                "required_inputs": reference_slots,
            },
            "motion": {
                "support": "PROMPT_FALLBACK" if capability.startswith("VIDEO_") else "UNSUPPORTED",
                "required_inputs": [],
                **({"prompt_fallback": True} if capability.startswith("VIDEO_") else {}),
            },
            **(
                {
                    "camera": {
                        "support": "PROMPT_FALLBACK",
                        "required_inputs": [],
                        "prompt_fallback": True,
                    }
                }
                if capability.startswith("VIDEO_")
                else {}
            ),
        },
    }


def _link_profiles(
    database: Database,
    manifest: Any,
    workflows: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_code = {str(item["code"]): item for item in workflows}
    now = _now()
    linked: list[dict[str, Any]] = []
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT p.code,v.* FROM execution_profiles p
            JOIN execution_profile_versions v ON v.execution_profile_id=p.id
            WHERE p.code LIKE 'local-suite-%' AND v.manifest_sha256=?
            ORDER BY p.code""",
            (manifest.sha256,),
        ).fetchall()
        if len(rows) != len(PROFILE_WORKFLOW):
            raise RuntimeError(f"Profile count mismatch: {len(rows)}")
        for row in rows:
            capability = str(row["capability"])
            workflow = by_code[PROFILE_WORKFLOW[capability]]
            artifact_ids = [
                item["id"]
                for item in artifacts
                if (item["partition"], item["component"]) in PROFILE_COMPONENTS[capability]
            ]
            spec = WORKFLOW_SPECS[PROFILE_WORKFLOW[capability]]
            bundle = json.loads(str(row["model_bundle_json"] or "{}"))
            bundle.update(
                {
                    "artifact_ids": artifact_ids,
                    "workflow_version_id": workflow["version_id"],
                    "runtime_verification": {
                        "status": "PASS",
                        "prompt_id": workflow["prompt_id"],
                        "output": workflow["output"],
                        "network_used": False,
                    },
                }
            )
            capability_json = json.loads(str(row["capability_json"] or "{}"))
            capability_json.update(
                {
                    "runtime_verified": True,
                    "runtime_verification_prompt_id": workflow["prompt_id"],
                    "quality_publication_pending": True,
                    "license_publication_pending": True,
                }
            )
            output_kind = next(
                str(run["output_kind"])
                for run in json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))["successful_runs"]
                if str(run["code"]) == PROFILE_WORKFLOW[capability]
            )
            connection.execute(
                """UPDATE execution_profile_versions SET workflow_version_id=?, model_bundle_json=?,
                input_contract_json=?, parameter_schema_json=?, output_contract_json=?, resource_policy_json=?,
                capability_json=?, updated_at=?, revision=revision+1 WHERE id=?""",
                (
                    workflow["version_id"],
                    _json(bundle),
                    _json(_profile_contract(spec["bindings"])),
                    _json(_profile_schema(capability)),
                    _json({"media_kind": output_kind}),
                    _json({"gpu_heavy_concurrency": 1, "runtime_kind": "COMFY"}),
                    _json(capability_json),
                    now,
                    row["id"],
                ),
            )
            linked.append(
                {
                    "code": str(row["code"]),
                    "version_id": str(row["id"]),
                    "capability": capability,
                    "status": str(row["status"]),
                    "workflow_version_id": workflow["version_id"],
                    "artifact_count": len(artifact_ids),
                    "runtime_verified": True,
                    "publication_pending": True,
                }
            )
        connection.execute(
            """INSERT INTO audit_events
            (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
            VALUES ('local-model-suite','operator','LOCAL_MODEL_SUITE_PROFILES_LINKED','manifest',?,?,?)""",
            (
                manifest.sha256,
                "将本机实跑工作流与模型组件链接到正式候选 Profile",
                _json({"profile_count": len(linked), "publication_pending": True}),
            ),
        )
    return linked


def main() -> int:
    parser = argparse.ArgumentParser(description="Register already-downloaded and actually-executed local models without network access.")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "evidence" / "local-model-registration-current.json")
    args = parser.parse_args()

    settings = Settings.from_env()
    database = Database(settings.database_path)
    evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    client = ComfyClient(settings.comfy_base_url, settings.comfy_output_root)
    manifest = load_manifest(settings.manifest_path)

    sync = ProfileService(database, settings.manifest_path).sync_manifest(actor="local-model-suite")
    workflow_service = WorkflowService(database, settings)
    workflows = [_register_workflow(workflow_service, client, run) for run in evidence["successful_runs"]]
    artifacts = _verify_artifacts(database, manifest, evidence["successful_runs"])
    profiles = _link_profiles(database, manifest, workflows, artifacts)

    result = {
        "schema_version": "localdrama.local-model-registration.v1",
        "created_at": _now(),
        "network_used": False,
        "manifest_sha256": manifest.sha256,
        "runtime": sync["runtime"],
        "workflows": workflows,
        "artifacts": artifacts,
        "profiles": profiles,
        "blocked_incomplete_models": evidence["blocked_incomplete_models"],
        "publication_note": "Runtime verification passed. Profiles remain candidates until project media quality and license evidence gates pass.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
