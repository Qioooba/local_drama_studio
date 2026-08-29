from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MODEL_ROOT = Path(r"F:\AI_Models\LocalDramaStudio")
RUNTIME_EVIDENCE = {
    "qwen3-embedding-8b": "qwen3-embedding-8b-runtime-20260828.json",
    "voxcpm2": "voxcpm2-runtime-20260828.json",
    "qwen3-asr-1.7b": "qwen3-asr-runtime-20260828.json",
    "qwen3-forced-aligner-0.6b": "qwen3-forced-aligner-runtime-20260828.json",
    "latentsync-1.6": "latentsync-runtime-20260829.json",
}


def _stable_id(value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"local-drama:{value}"))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _directory_receipt(path: Path) -> tuple[str, int, list[dict[str, Any]]]:
    files = sorted(
        item
        for item in path.rglob("*")
        if item.is_file()
        and ".cache" not in item.parts
        and item.suffix not in {".lock", ".metadata"}
    )
    aggregate = hashlib.sha256()
    total = 0
    records: list[dict[str, Any]] = []
    for item in files:
        digest = hashlib.sha256()
        with item.open("rb") as handle:
            for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
        size = item.stat().st_size
        relative = item.relative_to(path).as_posix()
        sha256 = digest.hexdigest()
        aggregate.update(f"{relative}\0{size}\0{sha256}\n".encode())
        total += size
        records.append({"path": relative, "bytes": size, "sha256": sha256})
    if not records:
        raise RuntimeError(f"model directory is empty: {path}")
    return aggregate.hexdigest(), total, records


def _load_evidence(evidence_root: Path, code: str) -> dict[str, Any]:
    path = evidence_root / RUNTIME_EVIDENCE[code]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") != "PASS" or payload.get("network_used") is not False:
        raise RuntimeError(f"runtime evidence did not pass offline: {path}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Register runtime-verified F-drive service models."
    )
    parser.add_argument(
        "--database", type=Path, default=Path("data/local_drama.sqlite3")
    )
    parser.add_argument("--evidence-root", type=Path, default=Path("docs/evidence"))
    parser.add_argument("--model-root", type=Path, default=MODEL_ROOT)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/evidence/local-ai-service-registration-20260829.json"),
    )
    args = parser.parse_args()

    specifications = {
        "qwen3-embedding-8b": (
            "EMBEDDING_MODEL",
            args.model_root / "PyTorch" / "Qwen3-Embedding-8B",
            "pytorch-local-ai",
        ),
        "voxcpm2": (
            "VOICE_MODEL",
            args.model_root / "PyTorch" / "VoxCPM2",
            "pytorch-local-ai",
        ),
        "qwen3-asr-1.7b": (
            "ASR_MODEL",
            args.model_root / "PyTorch" / "Qwen3-ASR-1.7B-hf",
            "pytorch-local-ai",
        ),
        "qwen3-forced-aligner-0.6b": (
            "ALIGNMENT_MODEL",
            args.model_root / "PyTorch" / "Qwen3-ForcedAligner-0.6B-hf",
            "pytorch-local-ai",
        ),
        "latentsync-1.6": (
            "LIPSYNC_MODEL",
            args.model_root / "PyTorch" / "LatentSync-1.6",
            "pytorch-latentsync",
        ),
    }
    evidence = {
        code: _load_evidence(args.evidence_root, code) for code in specifications
    }
    ollama_evidence_path = (
        args.evidence_root / "qwen3.8-27b-ollama-runtime-20260829.json"
    )
    ollama_evidence = json.loads(ollama_evidence_path.read_text(encoding="utf-8"))
    if (
        ollama_evidence.get("status") != "PASS"
        or ollama_evidence.get("network_used") is not False
    ):
        raise RuntimeError(
            f"Ollama runtime evidence did not pass offline: {ollama_evidence_path}"
        )
    receipts = {
        code: _directory_receipt(spec[1]) for code, spec in specifications.items()
    }
    now = datetime.now(UTC).isoformat()
    runtime_ids = {
        "pytorch-local-ai": _stable_id("runtime:pytorch-local-ai"),
        "pytorch-latentsync": _stable_id("runtime:pytorch-latentsync"),
    }
    runtimes = {
        "pytorch-local-ai": {
            "title": "F盘本地 AI 服务 Runtime",
            "executable": args.model_root / "Runtimes" / "QwenVox" / "site-packages",
            "version": "torch-2.10.0+cu130",
        },
        "pytorch-latentsync": {
            "title": "LatentSync 1.6 本地 Runtime",
            "executable": args.model_root
            / "Runtimes"
            / "LatentSync"
            / ".venv"
            / "Scripts"
            / "python.exe",
            "version": "python-3.11/torch-2.5.1+cu121",
        },
    }
    artifact_ids: dict[str, str] = {}
    profiles = {
        "local-suite-voxcpm2-tts": ("TTS", "voxcpm2"),
        "local-suite-voxcpm2-voice-clone": ("VOICE_CLONE", "voxcpm2"),
        "local-suite-qwen3-asr": ("ASR", "qwen3-asr-1.7b"),
        "local-suite-qwen3-alignment": ("AUDIO_ALIGNMENT", "qwen3-forced-aligner-0.6b"),
        "local-suite-latentsync": ("LIPSYNC", "latentsync-1.6"),
    }

    database = sqlite3.connect(args.database)
    database.row_factory = sqlite3.Row
    try:
        database.execute("PRAGMA foreign_keys=ON")
        for code, runtime in runtimes.items():
            runtime_id = runtime_ids[code]
            database.execute(
                """INSERT INTO local_runtimes
                (id,code,title,transport,base_url,executable_ref,runtime_version,status,details_json,
                 created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,'LOCAL_PROCESS',NULL,?,?,'AVAILABLE',?,?,?,?,1,'v2')
                ON CONFLICT(code) DO UPDATE SET executable_ref=excluded.executable_ref,
                runtime_version=excluded.runtime_version,status='AVAILABLE',details_json=excluded.details_json,
                updated_at=excluded.updated_at,revision=local_runtimes.revision+1""",
                (
                    runtime_id,
                    code,
                    runtime["title"],
                    str(runtime["executable"]),
                    runtime["version"],
                    _json(
                        {
                            "model_root": str(args.model_root),
                            "network_policy": "OFFLINE_ONLY",
                        }
                    ),
                    now,
                    now,
                    "codex-local-model-install",
                ),
            )
        for code, (kind, path, runtime_code) in specifications.items():
            receipt_sha, size_bytes, files = receipts[code]
            artifact_id = _stable_id(f"artifact:local-service:{code}")
            artifact_ids[code] = artifact_id
            database.execute(
                """INSERT INTO model_artifacts
                (id,runtime_id,code,kind,machine_path_ref,sha256,size_bytes,license_note,compatibility_json,
                 status,manifest_sha256,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?,?,?,'VERIFIED',?,?,?, ?,1,'v2')
                ON CONFLICT(code) DO UPDATE SET runtime_id=excluded.runtime_id,kind=excluded.kind,
                machine_path_ref=excluded.machine_path_ref,sha256=excluded.sha256,size_bytes=excluded.size_bytes,
                compatibility_json=excluded.compatibility_json,status='VERIFIED',updated_at=excluded.updated_at,
                revision=model_artifacts.revision+1""",
                (
                    artifact_id,
                    runtime_ids[runtime_code],
                    f"local-service-{code}",
                    kind,
                    str(path),
                    receipt_sha,
                    size_bytes,
                    "upstream repository metadata retained locally; review license before redistribution",
                    _json({"runtime_evidence": RUNTIME_EVIDENCE[code], "files": files}),
                    receipt_sha,
                    now,
                    now,
                    "codex-local-model-install",
                ),
            )
        for profile_code, (capability, artifact_code) in profiles.items():
            profile_id = _stable_id(f"profile:{profile_code}")
            version_id = _stable_id(
                f"profile-version:{profile_code}:runtime-verified-20260829"
            )
            runtime_code = specifications[artifact_code][2]
            bundle = {
                "schema_version": "localdrama.execution-profile-bundle.v1",
                "runtime_id": runtime_ids[runtime_code],
                "artifact_ids": [artifact_ids[artifact_code]],
                "components": [
                    {
                        "artifact_id": artifact_ids[artifact_code],
                        "role": specifications[artifact_code][0],
                        "required": True,
                    }
                ],
                "route_status": "runtime_verified",
            }
            database.execute(
                """INSERT INTO execution_profiles
                (id,code,title,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,'codex-local-model-install',1,'v2')
                ON CONFLICT(code) DO UPDATE SET title=excluded.title,updated_at=excluded.updated_at,
                revision=execution_profiles.revision+1""",
                (profile_id, profile_code, f"本机主力模型 {capability}", now, now),
            )
            if (
                database.execute(
                    "SELECT 1 FROM execution_profile_versions WHERE id=?", (version_id,)
                ).fetchone()
                is None
            ):
                version_no = database.execute(
                    "SELECT COALESCE(MAX(version_no),0)+1 FROM execution_profile_versions WHERE execution_profile_id=?",
                    (profile_id,),
                ).fetchone()[0]
                database.execute(
                    """INSERT INTO execution_profile_versions
                    (id,execution_profile_id,version_no,capability,runtime_version_id,workflow_version_id,
                     model_bundle_json,input_contract_json,parameter_schema_json,status,created_at,updated_at,
                     created_by,revision,schema_version,manifest_sha256,capability_json,worker_policy,
                     output_contract_json,resource_policy_json)
                    VALUES (?,?,?,?,?,NULL,?,?,?,'CANDIDATE_UNVERIFIED',?,?,'codex-local-model-install',1,'v2',?,?,?,?,?)""",
                    (
                        version_id,
                        profile_id,
                        version_no,
                        capability,
                        runtime_ids[runtime_code],
                        _json(bundle),
                        _json({"transport": "LOCAL_PROCESS", "input_slots": {}}),
                        _json(
                            {"seed": {"required": False, "determinism": "BEST_EFFORT"}}
                        ),
                        now,
                        now,
                        receipts[artifact_code][0],
                        _json(
                            {
                                "runtime_evidence": evidence[artifact_code],
                                "published": False,
                            }
                        ),
                        _json({"gpu_heavy_concurrency": 1, "runtime": "PYTORCH"}),
                        _json(
                            {
                                "media_kind": "VIDEO"
                                if capability == "LIPSYNC"
                                else "AUDIO"
                            }
                        ),
                        _json({"gpu_heavy_concurrency": 1}),
                    ),
                )
        database.execute(
            """UPDATE local_runtimes SET status='AVAILABLE',details_json=?,updated_at=?,
            revision=revision+1 WHERE code='ollama-loopback'""",
            (
                _json(
                    {
                        "runtime_evidence": str(ollama_evidence_path),
                        "model": "qwen3.8:27b",
                        "network_policy": "LOOPBACK_ONLY",
                    }
                ),
                now,
            ),
        )
        database.execute(
            """INSERT INTO audit_events
            (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
            VALUES ('codex-local-model-install','operator','LOCAL_AI_SERVICE_MODELS_REGISTERED','runtime',?, ?,?)""",
            (
                runtime_ids["pytorch-local-ai"],
                "登记 F 盘离线服务模型与实跑证据",
                _json({"artifacts": list(artifact_ids), "profiles": list(profiles)}),
            ),
        )
        database.commit()
        foreign_key_errors = list(database.execute("PRAGMA foreign_key_check"))
        if foreign_key_errors:
            raise RuntimeError(
                f"foreign key errors after registration: {foreign_key_errors}"
            )
    finally:
        database.close()

    result = {
        "status": "PASS",
        "network_used": False,
        "database": str(args.database.resolve()),
        "runtimes": runtime_ids,
        "artifacts": {
            code: {
                "id": artifact_ids[code],
                "sha256": receipts[code][0],
                "size_bytes": receipts[code][1],
            }
            for code in artifact_ids
        },
        "profiles": list(profiles),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
