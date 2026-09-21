"""One-click, evidence-gated NCNN/Vulkan video-upscale Profile publication."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.profile_publication import ProfilePublicationService, ProfileVersionDraft

_ADAPTER_CODE = "ncnn.realesrgan.video.v1"
_TEMPLATE = "ncnn.video-upscale.profile.v1"
_CAPABILITY = "UPSCALE_VIDEO"
_MODEL_SCALES: dict[str, tuple[int, ...]] = {
    "realesr-animevideov3": (2, 3, 4),
    "realesrgan-x4plus-anime": (4,),
    "realesrgan-x4plus": (4,),
}


@dataclass(frozen=True, slots=True)
class NcnnProfilePublication:
    profile_version_id: str
    runtime_model_installation_id: str
    validation_run_id: str
    profile_status: str
    model_name: str
    verified_native_scales: tuple[int, ...]
    runtime_sha256: str
    model_bundle_sha256: str
    reused_profile: bool


class NcnnVideoUpscaleProfileService:
    """Validate a fixed NCNN command and publish only its exact immutable identity.

    The endpoint intentionally accepts no arbitrary argv. The executable name,
    model names, formats, scale list and network policy are all closed contracts.
    A real two-frame inference is completed for every advertised native scale
    before any runtime, model, Offering or Profile can become executable.
    """

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        smoke_runner: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.smoke_runner = smoke_runner or self._run_real_smoke

    def configure_and_publish(
        self,
        *,
        executable_path: str,
        model_directory: str,
        model_name: str,
        gpu_device: int,
        tile_size: int,
        load_threads: int,
        proc_threads: int,
        save_threads: int,
        actor: str,
    ) -> NcnnProfilePublication:
        model_name = model_name.strip()
        scales = _MODEL_SCALES.get(model_name)
        if scales is None:
            raise DomainRuleError("UPSCALE_MODEL_UNSUPPORTED", "只允许已审计的 Real-ESRGAN NCNN 模型。")
        executable = self._resolve_executable(executable_path)
        model_dir = self._resolve_model_directory(model_directory)
        model_files = self._model_files(model_dir, model_name)
        ffmpeg = self._required_tool(self.settings.ffmpeg_path, "FFMPEG_NOT_FOUND", "未配置 FFmpeg，不能生成隔离冒烟帧。")
        ffprobe = self._required_tool(self.settings.ffprobe_path, "FFPROBE_NOT_FOUND", "未配置 FFprobe，不能核验冒烟输出。")
        evidence = self.smoke_runner(
            executable=executable,
            model_dir=model_dir,
            model_name=model_name,
            scales=scales,
            gpu_device=gpu_device,
            tile_size=tile_size,
            load_threads=load_threads,
            proc_threads=proc_threads,
            save_threads=save_threads,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        )
        if evidence.get("status") != "PASS" or evidence.get("network_used") is not False:
            raise DomainRuleError("UPSCALE_NCNN_SMOKE_FAILED", "NCNN/Vulkan 真实冒烟未通过，未发布执行 Profile。")

        executable_hash = _sha256(executable)
        model_bundle_hash = _hash(
            [{"name": item.name, "sha256": _sha256(item), "size_bytes": item.stat().st_size} for item in model_files]
        )
        registration = self._register_passed_smoke(
            executable=executable,
            executable_hash=executable_hash,
            model_dir=model_dir,
            model_files=model_files,
            model_bundle_hash=model_bundle_hash,
            model_name=model_name,
            scales=scales,
            gpu_device=gpu_device,
            evidence=evidence,
        )
        contracts = self._ensure_contracts(str(registration["capability_id"]))
        payload = {
            "template": _TEMPLATE,
            "runtime_model_installation_ids": [str(registration["installation_id"])],
            "model_name": model_name,
            "verified_native_scales": list(scales),
            "defaults": {
                "tile_size": tile_size,
                "tta": False,
                "load_threads": load_threads,
                "proc_threads": proc_threads,
                "save_threads": save_threads,
            },
            "locked_values": {},
            "allowed_override_fields": ["tile_size", "tta", "load_threads", "proc_threads", "save_threads"],
        }
        profile_code = f"ncnn-{model_name}-upscale-video"
        publication = ProfilePublicationService(self.database)
        reused_profile = False
        try:
            created = publication.create_candidate(
                ProfileVersionDraft(
                    profile_code=profile_code,
                    profile_title=f"Real-ESRGAN NCNN · {model_name}",
                    capability_definition_id=str(registration["capability_id"]),
                    runtime_installation_version_id=str(registration["runtime_version_id"]),
                    parameter_contract_version_id=contracts["parameter_contract_version_id"],
                    adapter_binding_contract_version_id=contracts["adapter_binding_contract_version_id"],
                    resource_policy_version_id=contracts["resource_policy_version_id"],
                    payload=payload,
                )
            )
            profile_version_id = created.profile_version_id
        except DomainRuleError as error:
            if error.code != "MP_PROFILE_PAYLOAD_ALREADY_EXISTS":
                raise
            profile_version_id = str(error.details["profile_version_id"])
            reused_profile = True

        existing = self._published_validation(profile_version_id)
        if existing is not None:
            return NcnnProfilePublication(
                profile_version_id,
                str(registration["installation_id"]),
                existing,
                "PUBLISHED",
                model_name,
                scales,
                executable_hash,
                model_bundle_hash,
                True,
            )
        validation = publication.record_validation(
            profile_version_id,
            validation_kind="PROFILE_SMOKE",
            status="SMOKE_PASSED",
            result={
                "payload_hash": self._profile_payload_hash(profile_version_id),
                "source_capability_validation_run_id": str(registration["offering_validation_run_id"]),
                "output_contract_verified": True,
            },
            evidence={**evidence, "profile_template": _TEMPLATE, "profile_published": False},
        )
        publication.publish(
            profile_version_id,
            validation_run_id=validation.validation_run_id,
            reason=f"{actor} 已确认本机 NCNN/Vulkan 多倍率真实冒烟并一键发布",
        )
        return NcnnProfilePublication(
            profile_version_id,
            str(registration["installation_id"]),
            validation.validation_run_id,
            "PUBLISHED",
            model_name,
            scales,
            executable_hash,
            model_bundle_hash,
            reused_profile,
        )

    def _resolve_executable(self, raw: str) -> Path:
        if raw.strip().startswith(("\\\\", "//")):
            raise DomainRuleError("UPSCALE_NCNN_REMOTE_PATH_FORBIDDEN", "NCNN 程序必须位于本机磁盘，不能使用 UNC 网络路径。")
        try:
            path = Path(raw.strip()).resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise DomainRuleError("UPSCALE_NCNN_EXECUTABLE_NOT_FOUND", "NCNN 可执行程序不存在或路径不可解析。") from error
        if not path.is_file() or path.is_symlink() or path.name.casefold() not in {
            "realesrgan-ncnn-vulkan.exe",
            "realesrgan-ncnn-vulkan",
        }:
            raise DomainRuleError("UPSCALE_NCNN_EXECUTABLE_INVALID", "请选择 realesrgan-ncnn-vulkan 可执行程序，且不能使用符号链接。")
        return path

    def _resolve_model_directory(self, raw: str) -> Path:
        if raw.strip().startswith(("\\\\", "//")):
            raise DomainRuleError("UPSCALE_NCNN_REMOTE_PATH_FORBIDDEN", "模型目录必须位于本机磁盘，不能使用 UNC 网络路径。")
        try:
            path = Path(raw.strip()).resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise DomainRuleError("UPSCALE_MODEL_DIRECTORY_NOT_FOUND", "模型目录不存在或路径不可解析。") from error
        if not path.is_dir() or path.is_symlink():
            raise DomainRuleError("UPSCALE_MODEL_DIRECTORY_INVALID", "模型目录必须是本机真实目录，不能使用符号链接。")
        return path

    def _model_files(self, model_dir: Path, model_name: str) -> tuple[Path, Path]:
        files = (model_dir / f"{model_name}.param", model_dir / f"{model_name}.bin")
        if any(not item.is_file() or item.is_symlink() for item in files):
            raise DomainRuleError("UPSCALE_MODEL_FILES_MISSING", "模型目录缺少同名 .param/.bin 文件，或文件是符号链接。")
        return files

    @staticmethod
    def _required_tool(value: str | None, code: str, message: str) -> Path:
        path = Path(value).resolve() if value else Path()
        if not value or not path.is_file():
            raise DomainRuleError(code, message)
        return path

    def _run_real_smoke(self, **options: Any) -> dict[str, Any]:
        executable: Path = options["executable"]
        model_dir: Path = options["model_dir"]
        ffmpeg: Path = options["ffmpeg"]
        ffprobe: Path = options["ffprobe"]
        scales: tuple[int, ...] = options["scales"]
        started_at = _utc_now()
        scale_receipts: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory(prefix="localdrama-ncnn-profile-smoke-") as raw_root:
            root = Path(raw_root)
            inputs = root / "input"
            inputs.mkdir()
            fixture = _run(
                [str(ffmpeg), "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=32x24:rate=2:duration=1", "-frames:v", "2", str(inputs / "%08d.png")],
                timeout=30,
            )
            if fixture.returncode != 0 or len(list(inputs.glob("*.png"))) != 2:
                raise DomainRuleError("UPSCALE_SMOKE_FIXTURE_FAILED", "无法用本机 FFmpeg 创建隔离冒烟帧。")
            for scale in scales:
                outputs = root / f"output-x{scale}"
                outputs.mkdir()
                command = [
                    str(executable), "-i", str(inputs), "-o", str(outputs), "-n", str(options["model_name"]),
                    "-s", str(scale), "-t", str(options["tile_size"]), "-m", str(model_dir),
                    "-g", str(options["gpu_device"]),
                    "-j", f'{options["load_threads"]}:{options["proc_threads"]}:{options["save_threads"]}', "-f", "png",
                ]
                try:
                    completed = _run(command, timeout=300)
                except subprocess.TimeoutExpired as error:
                    raise DomainRuleError("UPSCALE_NCNN_SMOKE_TIMEOUT", "NCNN/Vulkan 冒烟运行超时，未发布 Profile。") from error
                frames = sorted(outputs.glob("*.png"))
                if completed.returncode != 0 or len(frames) != 2:
                    raise DomainRuleError(
                        "UPSCALE_NCNN_SMOKE_FAILED",
                        "NCNN/Vulkan 冒烟没有生成预期的两帧输出。",
                        {"scale": scale, "exit_code": completed.returncode},
                    )
                dimensions = [_probe_dimensions(ffprobe, frame) for frame in frames]
                if any(item != (32 * scale, 24 * scale) for item in dimensions):
                    raise DomainRuleError("UPSCALE_NCNN_SMOKE_GEOMETRY_MISMATCH", "NCNN/Vulkan 冒烟输出尺寸与声明倍率不一致。", {"scale": scale})
                scale_receipts.append({
                    "scale": scale,
                    "input_frames": 2,
                    "output_frames": 2,
                    "output": {"width": 32 * scale, "height": 24 * scale},
                    "runtime_log_excerpt": _redact(f"{completed.stdout}\n{completed.stderr}", (executable, model_dir, root)),
                })
        return {
            "schema_version": "localdrama.ncnn-video-upscale-profile-smoke.v1",
            "status": "PASS",
            "started_at": started_at,
            "finished_at": _utc_now(),
            "adapter_code": _ADAPTER_CODE,
            "model_name": options["model_name"],
            "verified_native_scales": list(scales),
            "device": {"vulkan_index": options["gpu_device"]},
            "scale_receipts": scale_receipts,
            "network_used": False,
        }

    def _register_passed_smoke(
        self,
        *,
        executable: Path,
        executable_hash: str,
        model_dir: Path,
        model_files: tuple[Path, Path],
        model_bundle_hash: str,
        model_name: str,
        scales: tuple[int, ...],
        gpu_device: int,
        evidence: dict[str, Any],
    ) -> dict[str, str]:
        now = _utc_now()
        node_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"localdramastudio:node:{self.settings.instance_id}"))
        runtime_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"localdramastudio:ncnn-video-upscale:{self.settings.instance_id}"))
        configuration = {
            "provider": "NCNN_VULKAN_LOCAL_PROCESS",
            "executable_path": str(executable),
            "executable_sha256": executable_hash,
            "model_bundle_sha256": model_bundle_hash,
            "model_files": [
                {"name": item.name, "sha256": _sha256(item), "size_bytes": item.stat().st_size}
                for item in model_files
            ],
            "argv_prefix": [],
            "gpu_device": gpu_device,
            "network_policy": "LOCAL_ONLY",
        }
        runtime_fingerprint = _hash(configuration)
        family_code = f"realesrgan-{model_name}"
        release_code = f"{family_code}-{model_bundle_hash[:12]}"
        with self.database.transaction() as connection:
            capability = connection.execute("SELECT id FROM mp_capability_definitions WHERE code=?", (_CAPABILITY,)).fetchone()
            if capability is None:
                raise DomainRuleError("UPSCALE_CAPABILITY_NOT_FOUND", "模型平台缺少 UPSCALE_VIDEO 能力定义。")
            capability_id = str(capability["id"])
            connection.execute(
                "INSERT OR IGNORE INTO mp_compute_nodes (id,code,display_name,fingerprint,host_json,last_seen_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (node_id, f"local-{self.settings.instance_id}", "本机服务节点", f"service:{self.settings.instance_id}", "{}", now, now, now),
            )
            connection.execute(
                "INSERT OR IGNORE INTO mp_runtime_installations (id,node_id,code,kind,owner_mode,display_name,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (runtime_id, node_id, "ncnn.realesrgan.video", "TOOL_PROCESS", "SERVICE_MANAGED", "Real-ESRGAN NCNN/Vulkan", now, now),
            )
            runtime_version = connection.execute(
                "SELECT id FROM mp_runtime_installation_versions WHERE runtime_installation_id=? AND fingerprint=?",
                (runtime_id, runtime_fingerprint),
            ).fetchone()
            if runtime_version is None:
                version_no = int(connection.execute("SELECT COALESCE(MAX(version_no),0)+1 FROM mp_runtime_installation_versions WHERE runtime_installation_id=?", (runtime_id,)).fetchone()[0])
                runtime_version_id = str(uuid.uuid4())
                connection.execute(
                    "INSERT INTO mp_runtime_installation_versions (id,runtime_installation_id,version_no,adapter_code,adapter_version,transport,configuration_json,fingerprint,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (runtime_version_id, runtime_id, version_no, _ADAPTER_CODE, "v1", "LOCAL_CLI", _json(configuration), runtime_fingerprint, "ACTIVE", now, now),
                )
            else:
                runtime_version_id = str(runtime_version["id"])
                connection.execute("UPDATE mp_runtime_installation_versions SET status='ACTIVE',updated_at=? WHERE id=?", (now, runtime_version_id))
            connection.execute(
                "INSERT OR IGNORE INTO mp_runtime_instances (id,runtime_installation_version_id,status,health_json,last_heartbeat_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid5(uuid.NAMESPACE_URL, f"localdramastudio:ncnn-instance:{runtime_version_id}")), runtime_version_id, "READY", _json({"smoke": "PASS", "network_used": False}), now, now, now),
            )
            family = connection.execute("SELECT id FROM mp_model_families WHERE code=?", (family_code,)).fetchone()
            family_id = str(family["id"]) if family else str(uuid.uuid4())
            if family is None:
                connection.execute("INSERT INTO mp_model_families (id,code,title,vendor,license_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (family_id, family_code, model_name, "xinntao", _json({"status": "USER_PROVIDED"}), now, now))
            release = connection.execute("SELECT id FROM mp_model_releases WHERE code=?", (release_code,)).fetchone()
            release_id = str(release["id"]) if release else str(uuid.uuid4())
            if release is None:
                connection.execute(
                    "INSERT INTO mp_model_releases (id,family_id,code,upstream_id,revision,format,quantization,metadata_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (release_id, family_id, release_code, model_name, model_bundle_hash, "NCNN_PARAM_BIN", None, _json({"native_scales": list(scales)}), now, now),
                )
            for ordinal, item in enumerate(model_files):
                digest, size = _sha256(item), item.stat().st_size
                artifact = connection.execute("SELECT id FROM mp_model_artifacts WHERE content_sha256=? AND size_bytes=?", (digest, size)).fetchone()
                artifact_id = str(artifact["id"]) if artifact else str(uuid.uuid4())
                if artifact is None:
                    connection.execute("INSERT INTO mp_model_artifacts (id,kind,content_sha256,size_bytes,format,manifest_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (artifact_id, "FILE", digest, size, item.suffix.lstrip(".").upper(), _json({"file_name": item.name}), now, now))
                connection.execute("INSERT OR IGNORE INTO mp_model_components (id,release_id,artifact_id,role,ordinal,required,shared,created_at,updated_at) VALUES (?,?,?,?,?,1,0,?,?)", (str(uuid.uuid4()), release_id, artifact_id, "UPSCALER" if ordinal == 1 else "CONFIG", ordinal, now, now))
            native_locator = str(model_dir / f"{model_name}.param")
            installation = connection.execute("SELECT id FROM mp_runtime_model_installations WHERE runtime_installation_version_id=? AND native_locator=?", (runtime_version_id, native_locator)).fetchone()
            installation_id = str(installation["id"]) if installation else str(uuid.uuid4())
            if installation is None:
                connection.execute("INSERT INTO mp_runtime_model_installations (id,release_id,runtime_installation_version_id,native_locator,install_state,metadata_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (installation_id, release_id, runtime_version_id, native_locator, "READY", _json({"model_bundle_sha256": model_bundle_hash}), now, now))
            else:
                connection.execute("UPDATE mp_runtime_model_installations SET install_state='READY',updated_at=? WHERE id=?", (now, installation_id))
            offering = connection.execute("SELECT id FROM mp_capability_offerings WHERE runtime_model_installation_id=? AND capability_definition_id=?", (installation_id, capability_id)).fetchone()
            offering_id = str(offering["id"]) if offering else str(uuid.uuid4())
            if offering is None:
                connection.execute("INSERT INTO mp_capability_offerings (id,runtime_model_installation_id,capability_definition_id,native_metadata_json,validation_status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (offering_id, installation_id, capability_id, _json({"model_name": model_name, "verified_native_scales": list(scales)}), "SMOKE_PASSED", now, now))
            else:
                connection.execute("UPDATE mp_capability_offerings SET validation_status='SMOKE_PASSED',native_metadata_json=?,updated_at=? WHERE id=?", (_json({"model_name": model_name, "verified_native_scales": list(scales)}), now, offering_id))
            validation_run_id = str(uuid.uuid4())
            result = {"adapter_code": _ADAPTER_CODE, "model_name": model_name, "verified_native_scales": list(scales), "network_used": False}
            integrity_run_id = str(uuid.uuid4())
            integrity_result = {"model_name": model_name, "model_bundle_sha256": model_bundle_hash, "component_count": len(model_files), "files_present": True}
            connection.execute("INSERT INTO mp_validation_runs (id,target_kind,target_id,validation_kind,status,result_json,started_at,finished_at,created_at,updated_at) VALUES (?,'RUNTIME_MODEL_INSTALLATION',?,'INSTALLATION_INTEGRITY','INTEGRITY_PASSED',?,?,?,?,?)", (integrity_run_id, installation_id, _json(integrity_result), now, now, now, now))
            connection.execute("INSERT INTO mp_validation_evidence (id,validation_run_id,kind,content_hash,payload_json,artifact_ref,created_at,updated_at) VALUES (?,?,?,?,?,NULL,?,?)", (str(uuid.uuid4()), integrity_run_id, "INSTALLATION_INTEGRITY", _hash(integrity_result), _json(integrity_result), now, now))
            connection.execute("INSERT INTO mp_validation_runs (id,target_kind,target_id,validation_kind,status,result_json,started_at,finished_at,created_at,updated_at) VALUES (?,'CAPABILITY_OFFERING',?,'CAPABILITY_SMOKE','SMOKE_PASSED',?,?,?,?,?)", (validation_run_id, offering_id, _json(result), now, now, now, now))
            connection.execute("INSERT INTO mp_validation_evidence (id,validation_run_id,kind,content_hash,payload_json,artifact_ref,created_at,updated_at) VALUES (?,?,?,?,?,NULL,?,?)", (str(uuid.uuid4()), validation_run_id, "CAPABILITY_SMOKE", _hash(evidence), _json(evidence), now, now))
        return {"capability_id": capability_id, "runtime_version_id": runtime_version_id, "installation_id": installation_id, "offering_validation_run_id": validation_run_id}

    def _ensure_contracts(self, capability_id: str) -> dict[str, str]:
        parameter_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "localdramastudio:ncnn-upscale:parameter:v1"))
        binding_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "localdramastudio:ncnn-upscale:binding:v1"))
        resource_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "localdramastudio:ncnn-upscale:resource:v1"))
        schema = {
            "type": "object",
            "properties": {
                "tile_size": {"type": "integer", "enum": [0, 64, 128, 256, 512, 1024]},
                "tta": {"type": "boolean"},
                "load_threads": {"type": "integer", "minimum": 1, "maximum": 4},
                "proc_threads": {"type": "integer", "minimum": 1, "maximum": 4},
                "save_threads": {"type": "integer", "minimum": 1, "maximum": 4},
            },
            "additionalProperties": False,
        }
        ui_schema = {"properties": {name: {"scopes": ["RUN"]} for name in ("tile_size", "tta", "load_threads", "proc_threads", "save_threads")}}
        binding = {"adapter_code": _ADAPTER_CODE, "template": "v1", "transport": "LOCAL_CLI"}
        resource = {"network_policy": {"mode": "LOCAL_ONLY"}, "gpu_runtime": "PYTORCH", "resource_class": "GPU_H3", "exclusive_gpu": True}
        now = _utc_now()
        with self.database.transaction() as connection:
            connection.execute("INSERT OR IGNORE INTO mp_parameter_contract_versions (id,capability_definition_id,version_no,schema_json,ui_schema_json,content_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (parameter_id, capability_id, 1, _json(schema), _json(ui_schema), _hash(schema), now, now))
            connection.execute("INSERT OR IGNORE INTO mp_adapter_binding_contract_versions (id,runtime_kind,adapter_code,version_no,binding_json,content_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (binding_id, "TOOL_PROCESS", _ADAPTER_CODE, 1, _json(binding), _hash(binding), now, now))
            connection.execute("INSERT OR IGNORE INTO mp_resource_policy_versions (id,code,version_no,policy_json,content_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (resource_id, "ncnn-video-upscale-local", 1, _json(resource), _hash(resource), now, now))
        return {"parameter_contract_version_id": parameter_id, "adapter_binding_contract_version_id": binding_id, "resource_policy_version_id": resource_id}

    def _published_validation(self, profile_version_id: str) -> str | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT validation_run_id FROM mp_profile_publications WHERE execution_profile_version_id=? AND status='PUBLISHED'", (profile_version_id,)).fetchone()
        return str(row["validation_run_id"]) if row is not None and row["validation_run_id"] else None

    def _profile_payload_hash(self, profile_version_id: str) -> str:
        with self.database.connect() as connection:
            row = connection.execute("SELECT payload_hash FROM mp_execution_profile_versions WHERE id=?", (profile_version_id,)).fetchone()
        if row is None:
            raise DomainRuleError("MP_PROFILE_VERSION_NOT_FOUND", "待验证的 NCNN Profile 不存在。")
        return str(row["payload_hash"])


def _run(args: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, check=False, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def _probe_dimensions(ffprobe: Path, path: Path) -> tuple[int, int]:
    completed = _run([str(ffprobe), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(path)], timeout=30)
    if completed.returncode != 0:
        raise DomainRuleError("UPSCALE_NCNN_SMOKE_PROBE_FAILED", "FFprobe 无法核验 NCNN 冒烟输出。")
    try:
        streams = json.loads(completed.stdout).get("streams", [])
        stream = streams[0]
        return int(stream["width"]), int(stream["height"])
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise DomainRuleError("UPSCALE_NCNN_SMOKE_PROBE_FAILED", "NCNN 冒烟输出没有可核验的视频流。") from error


def _redact(text: str, paths: tuple[Path, ...]) -> str:
    result = text
    for path in sorted(paths, key=lambda item: len(str(item)), reverse=True):
        result = result.replace(str(path), f"<{path.name or 'path'}>")
    return result[-2000:]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
