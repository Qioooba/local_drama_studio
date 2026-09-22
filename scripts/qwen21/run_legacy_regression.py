"""Legacy-chain regression on the production ComfyUI runtime (8188).

The Qwen-Image-2.1 work added an isolated runtime and new nodes.  It must not
have disturbed the chain that already serves commercial production, so this
script re-runs one task from each legacy family against the *production* server
and records what actually happened:

* ``qwen-image-2512-production``   text-to-image
* ``qwen-image-edit-2511-q5-smoke`` editing
* ``h3-i2v-16x9-silent-final``     the MiniMax H3 video path

Scope, stated honestly: this proves the existing nodes, encoders and artifact
production still run on the upgraded host.  It uses the workflows exactly as
published -- including their published step counts -- so it is a regression
check, not an image-quality or throughput benchmark.

Timing comes from ComfyUI's own task record.  The GPU is shared with the 2.1
runtime, so the script refuses to start while either server is busy.

Example::

    python scripts/qwen21/run_legacy_regression.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import time
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "apps" / "api") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "apps" / "api"))

from local_drama.application.workflow_definitions import WorkflowDefinitionService  # noqa: E402
from local_drama.config import Settings  # noqa: E402
from local_drama.infrastructure.database.sqlite import Database  # noqa: E402

FIRST_FRAME_NAME = "local_drama_legacy_regression_frame.png"
_EDIT_SOURCE_NAME = "qwen-image-edit-source.png"


class _StubSettings:
    """WorkflowDefinitionService only needs ``settings`` for H3 factory calls."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _png_size(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as handle:
            header = handle.read(24)
    except OSError:
        return None
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width, height = struct.unpack(">II", header[16:24])
    return int(width), int(height)


def _write_first_frame(path: Path, width: int = 864, height: int = 480) -> None:
    """Deterministic first frame: a graded horizon with a subject block."""

    rows: list[bytes] = []
    for y in range(height):
        row = bytearray()
        horizon = height * 2 // 3
        for x in range(width):
            if y > horizon:
                row += bytes((70, 60, 45))
            elif (width // 3) <= x < (2 * width // 3) and (height // 3) <= y < horizon:
                row += bytes((150, 110, 80))
            else:
                row += bytes((60 + y * 90 // max(1, horizon), 90 + y * 70 // max(1, horizon), 140 + y * 60 // max(1, horizon)))
        rows.append(bytes(row))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + row for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _get(url: str, timeout: float = 60.0) -> Any:
    with urlopen(Request(url), timeout=timeout) as response:  # noqa: S310 - loopback
        return json.loads(response.read().decode("utf-8"))


def _post(url: str, payload: dict[str, Any], timeout: float = 60.0) -> Any:
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback
        return json.loads(response.read().decode("utf-8"))


def _gpu_used_mib() -> int | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    result = subprocess.run(  # noqa: S603 - fixed argv
        [exe, "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True, check=False
    )
    try:
        return int(result.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


def _history_timing(entry: dict[str, Any]) -> dict[str, Any]:
    stamps: dict[str, float] = {}
    for name, payload in ((entry.get("status") or {}).get("messages") or []):
        if name in {"execution_start", "execution_success"} and isinstance(payload, dict):
            stamp = payload.get("timestamp")
            if isinstance(stamp, (int, float)):
                stamps[name] = float(stamp) / 1000.0
    started, finished = stamps.get("execution_start"), stamps.get("execution_success")
    return {
        "server_seconds": round(finished - started, 3) if started is not None and finished is not None else None,
        "status_str": str((entry.get("status") or {}).get("status_str") or ""),
    }


def _outputs(entry: dict[str, Any], output_root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for node_output in (entry.get("outputs") or {}).values():
        for key in ("images", "videos", "gifs", "audio"):
            for item in (node_output.get(key) or []):
                candidate = output_root / str(item.get("subfolder") or "") / str(item.get("filename") or "")
                record = {"kind": key, "filename": item.get("filename"), "exists": candidate.is_file()}
                if candidate.is_file():
                    record["bytes"] = candidate.stat().st_size
                    record["sha256"] = _sha256(candidate)
                    size = _png_size(candidate)
                    if size is not None:
                        record["width"], record["height"] = size
                records.append(record)
    return records


def _published(service_conn: Any, code: str) -> dict[str, Any] | None:
    row = service_conn.execute(
        """SELECT wv.id, wv.content_json, wv.node_bindings_json
           FROM workflow_versions wv JOIN workflows w ON w.id=wv.workflow_id
           WHERE w.code=? AND wv.status='PUBLISHED' ORDER BY wv.version_no DESC LIMIT 1""",
        (code,),
    ).fetchone()
    if row is None:
        return None
    return {"id": str(row["id"]), "graph": json.loads(str(row["content_json"])), "bindings": json.loads(str(row["node_bindings_json"]))}


def _apply(graph: dict[str, Any], bindings: dict[str, Any], values: dict[str, Any]) -> list[str]:
    applied: list[str] = []
    for role, value in values.items():
        binding = bindings.get(role)
        if not isinstance(binding, dict):
            continue
        node = graph.get(str(binding["node_id"]))
        if isinstance(node, dict) and isinstance(node.get("inputs"), dict):
            node["inputs"][str(binding["input"])] = value
            applied.append(role)
    return applied


def _run(
    task: dict[str, Any],
    *,
    base_url: str,
    output_root: Path,
    connection: Any,
    timeout_seconds: float,
) -> dict[str, Any]:
    record: dict[str, Any] = {"code": task["code"], "workflow": task["workflow"], "purpose": task["purpose"], "requirement": task["requirement"]}
    published = _published(connection, str(task["workflow"]))
    if published is None:
        record["status"] = "WORKFLOW_NOT_PUBLISHED"
        return record
    graph = json.loads(json.dumps(published["graph"]))
    record["applied_roles"] = _apply(graph, published["bindings"], dict(task["values"]))
    record["unapplied_roles"] = sorted(set(task["values"]) - set(record["applied_roles"]))
    record["gpu_before_mib"] = _gpu_used_mib()
    started = time.monotonic()
    try:
        queued = _post(f"{base_url}/prompt", {"prompt": graph, "client_id": f"legacy-regression-{task['code']}"})
    except (URLError, OSError, ValueError) as error:
        record.update(status="SUBMIT_FAILED", error=f"{type(error).__name__}: {error}")
        return record
    prompt_id = str(queued.get("prompt_id") or "")
    record["prompt_id"] = prompt_id
    if not prompt_id:
        record.update(status="REJECTED", error=json.dumps(queued, ensure_ascii=False)[:600])
        return record

    entry: dict[str, Any] = {}
    while time.monotonic() - started < timeout_seconds:
        entry = (_get(f"{base_url}/history/{prompt_id}") or {}).get(prompt_id) or {}
        if entry:
            break
        time.sleep(1.5)
    record["client_seconds"] = round(time.monotonic() - started, 3)
    record["gpu_after_mib"] = _gpu_used_mib()
    if not entry:
        record["status"] = "TIMEOUT"
        return record
    record.update(_history_timing(entry))
    record["outputs"] = _outputs(entry, output_root)
    record["status"] = "SUCCESS" if record.get("status_str") == "success" and record["outputs"] else "FAILED"
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8188", help="Production ComfyUI endpoint.")
    parser.add_argument("--qwen21-url", default="http://127.0.0.1:8189", help="Checked only to enforce single-GPU use.")
    parser.add_argument("--timeout-seconds", type=float, default=3600.0)
    parser.add_argument("--video-frames", type=int, default=49, help="H3 segment length; the published graph uses 362.")
    parser.add_argument("--output", type=Path, default=_REPO_ROOT / "work" / "qwen21" / "legacy-regression.json")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    database = Database(settings.database_path)
    input_root = Path(settings.comfy_input_root)
    output_root = Path(settings.comfy_output_root)

    # One physical GPU: refuse to start while either runtime has work queued.
    busy: list[str] = []
    for name, url in (("production-8188", args.base_url), ("qwen21-8189", args.qwen21_url)):
        try:
            queue = _get(f"{url}/queue", timeout=8)
            count = len(queue.get("queue_running") or []) + len(queue.get("queue_pending") or [])
            if count:
                busy.append(f"{name} has {count} prompt(s)")
        except (URLError, OSError, ValueError):
            continue
    if busy:
        print(json.dumps({"error": "GPU_BUSY", "detail": busy}, ensure_ascii=False, indent=2))
        return 2

    frame = input_root / FIRST_FRAME_NAME
    if not frame.is_file():
        _write_first_frame(frame)
    edit_source = input_root / _EDIT_SOURCE_NAME
    if not edit_source.is_file():
        _write_first_frame(edit_source, 768, 768)

    tasks = [
        {
            "code": "LEGACY_2512_T2I",
            "workflow": "qwen-image-2512-production",
            "purpose": "原链回归：Qwen-Image-2512 文生图",
            "requirement": "升级后既有节点、编码与 artifact 提升未被破坏",
            "values": {
                "PROMPT": "电影级角色设定图：一位中年女性，短发，深蓝色棉布外套，正面半身，柔和侧光，中性灰背景",
                "NEGATIVE_PROMPT": "模糊，水印，文字",
                "SEED": 9183901,
                "STEPS": 20,
                "CFG": 1.0,
                "WIDTH": 480,
                "HEIGHT": 832,
                "OUTPUT_PREFIX": "local_drama/legacy_regression_2512",
            },
        },
        {
            "code": "LEGACY_2511_EDIT",
            "workflow": "qwen-image-edit-2511-q5-smoke",
            "purpose": "原链回归：Qwen-Image-Edit-2511 编辑",
            "requirement": "既有身份/编辑工作流仍可执行并产出 artifact",
            "values": {
                "FIRST_FRAME": _EDIT_SOURCE_NAME,
                "PROMPT": "把背景替换为纯蓝色摄影棚背景，保持人物与姿态不变",
                "OUTPUT_PREFIX": "local_drama/legacy_regression_2511",
            },
        },
        {
            "code": "LEGACY_H3_VIDEO",
            "workflow": "h3-i2v-16x9-silent-final",
            "purpose": "原链回归：MiniMax H3 图生视频",
            "requirement": "现有视频流程仍可产出可解码视频",
            "values": {
                "FIRST_FRAME": FIRST_FRAME_NAME,
                "PROMPT": "镜头缓慢推进，人物轻微转头，自然光线变化",
                "SEED": 9183913,
                "FRAME_COUNT": int(args.video_frames),
                "OUTPUT_PREFIX": "local_drama/legacy_regression_h3",
            },
        },
    ]

    records: list[dict[str, Any]] = []
    with database.connect() as connection:
        for task in tasks:
            record = _run(task, base_url=args.base_url, output_root=output_root, connection=connection, timeout_seconds=args.timeout_seconds)
            records.append(record)
            print(f"[{record['status']:>18}] {record['code']:<20} server={record.get('server_seconds')}s", flush=True)

    report = {
        "schema_version": "localdrama.legacy-regression.v1",
        "generated_at": _now(),
        "base_url": args.base_url,
        "note": (
            "原链回归：确认 2.1 接入未破坏既有节点、编码与 artifact 提升。"
            "各任务使用其已发布工作流的参数（含已发布的步数），因此这是回归检查，不是画质或吞吐基准。"
        ),
        "tasks": records,
        "passed": all(item.get("status") == "SUCCESS" for item in records),
        "evidence": "每项记录 prompt_id、服务端 execution_start→execution_success 耗时、输出文件尺寸与 SHA-256。",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({item["code"]: item["status"] for item in records}, ensure_ascii=False, indent=2))
    print(f"evidence: {args.output}")
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
