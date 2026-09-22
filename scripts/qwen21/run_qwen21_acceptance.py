"""Run the single Qwen-Image-2.1 acceptance pass and record real evidence.

This implements the acceptance table in
``docs/RTX3090Ti_Qwen_Image_2_1_配置与实施方案.md`` section 9.  It is not an
open-ended model comparison: the components, versions and parameters are already
fixed, so a failure here is a deployment defect to diagnose, not a reason to
start sweeping samplers or quantizations.

Measurement rules the record deliberately follows:

* Timing comes from ComfyUI's own task record (``execution_start`` ->
  ``execution_success`` in ``/history``), never from the HTTP submit call.
* Cold start and subsequent warm tasks are reported separately.
* Every task uses a distinct seed, so a Comfy graph-cache hit cannot be
  mistaken for model inference speed.
* GPU memory is sampled around each task from ``nvidia-smi``.
* Output PNGs are decoded and their real pixel dimensions recorded.

Example::

    python scripts/qwen21/run_qwen21_acceptance.py --base-url http://127.0.0.1:8189
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

from seed_qwen21_inputs import REFERENCE_1, REFERENCE_2  # noqa: E402

# Ordered acceptance tasks.  "group" separates the 2.1 pass from the legacy
# regression pass, which must run against the production runtime instead.
TASKS: tuple[dict[str, Any], ...] = (
    {
        "code": "T2I_CHARACTER_1MP",
        "group": "qwen21",
        "definition": "QWEN_IMAGE_21_T2I_CHARACTER",
        "parameters": {"size_preset": "portrait", "seed": 9183801},
        "prompt": "电影级角色设定图：一位中年女性，短发，深蓝色棉布外套，正面半身，柔和侧光，中性灰背景，写实皮肤质感",
        "expects": {"width": 768, "height": 1376, "steps": 40, "cfg": 1.0},
        "requirement": "输出可解码，人物正常，尺寸与实际参数相符",
    },
    {
        "code": "T2I_SCENE_1MP",
        "group": "qwen21",
        "definition": "QWEN_IMAGE_21_T2I_SCENE",
        "parameters": {"size_preset": "landscape", "seed": 9183802},
        "prompt": "电影级场景空镜：雨后旧街，湿滑石板路反光，暖色路灯，远处雾气，无人物，写实摄影",
        "expects": {"width": 1376, "height": 768, "steps": 40, "cfg": 1.0},
        "requirement": "输出可解码，场景正常，无人物混入",
    },
    {
        "code": "EDIT_SINGLE_REFERENCE",
        "group": "qwen21",
        "definition": "QWEN_IMAGE_21_EDIT",
        "parameters": {"seed": 9183803, "reference_image_1": REFERENCE_1},
        "prompt": "Use <image1> as the base image. Replace only the background with a plain blue studio wall. Keep the subject, its shape, viewpoint and lighting unchanged.",
        "expects": {"steps": 40, "cfg": 1.0},
        "requirement": "指令生效且主体无明显身份漂移或噪点",
    },
    {
        "code": "EDIT_TWO_REFERENCES",
        "group": "qwen21",
        "definition": "QWEN_IMAGE_21_EDIT_2REF",
        "parameters": {"seed": 9183805, "reference_image_1": REFERENCE_1, "reference_image_2": REFERENCE_2},
        "prompt": "Use <image1> as the composition base and <image2> as the colour reference. Change only the subject's colour to match <image2>. Keep the shape, viewpoint and background from <image1>.",
        "expects": {"steps": 40, "cfg": 1.0},
        "requirement": "两参考顺序正确，人物来源正确，输出比例跟随 image_1",
    },
    {
        "code": "T2I_HIRES_2_36MP",
        "group": "qwen21",
        "definition": "QWEN_IMAGE_21_T2I_CONCEPT",
        "parameters": {"size_preset": "hires_landscape", "seed": 9183806},
        "prompt": "高分辨率电影概念图：山谷中的古寺建筑群，晨雾，飞鸟，细腻材质，宽银幕构图，写实摄影",
        "expects": {"width": 2048, "height": 1152, "steps": 50, "cfg": 1.0},
        "requirement": "完成且无 OOM；不据此宣布高分编辑同样通过",
    },
    {
        "code": "QUEUE_SEED_1",
        "group": "qwen21_queue",
        "definition": "QWEN_IMAGE_21_T2I_CONCEPT",
        "parameters": {"size_preset": "square", "seed": 9183811},
        "prompt": "电影画面：雨后的旧街，暖色灯光，湿润路面反光，细腻光影，写实摄影",
        "expects": {"width": 1024, "height": 1024, "steps": 40, "cfg": 1.0},
        "requirement": "连续任务不重复提交、不串结果、显存不持续累积",
    },
    {
        "code": "QUEUE_SEED_2",
        "group": "qwen21_queue",
        "definition": "QWEN_IMAGE_21_T2I_CONCEPT",
        "parameters": {"size_preset": "square", "seed": 9183812},
        "prompt": "电影画面：清晨的木质厨房，红陶茶壶与绿植，窗边柔光，写实摄影",
        "expects": {"width": 1024, "height": 1024, "steps": 40, "cfg": 1.0},
        "requirement": "连续任务不重复提交、不串结果、显存不持续累积",
    },
    {
        "code": "QUEUE_SEED_3",
        "group": "qwen21_queue",
        "definition": "QWEN_IMAGE_21_T2I_CONCEPT",
        "parameters": {"size_preset": "square", "seed": 9183813},
        "prompt": "电影画面：夜晚的旧书店内部，暖黄台灯，书架纵深，写实摄影",
        "expects": {"width": 1024, "height": 1024, "steps": 40, "cfg": 1.0},
        "requirement": "连续任务不重复提交、不串结果、显存不持续累积",
    },
    {
        "code": "QUEUE_SEED_4",
        "group": "qwen21_queue",
        "definition": "QWEN_IMAGE_21_T2I_CONCEPT",
        "parameters": {"size_preset": "square", "seed": 9183814},
        "prompt": "电影画面：山间小径的清晨薄雾，远处古塔轮廓，写实摄影",
        "expects": {"width": 1024, "height": 1024, "steps": 40, "cfg": 1.0},
        "requirement": "连续任务不重复提交、不串结果、显存不持续累积",
    },
    {
        "code": "QUEUE_SEED_5",
        "group": "qwen21_queue",
        "definition": "QWEN_IMAGE_21_T2I_CONCEPT",
        "parameters": {"size_preset": "square", "seed": 9183815},
        "prompt": "电影画面：黄昏的海边石阶，退潮的湿沙与远处渔船，写实摄影",
        "expects": {"width": 1024, "height": 1024, "steps": 40, "cfg": 1.0},
        "requirement": "连续任务不重复提交、不串结果、显存不持续累积",
    },
)

GROUPS = ("qwen21", "qwen21_queue", "legacy")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _get(url: str, timeout: float = 60.0) -> Any:
    with urlopen(Request(url), timeout=timeout) as response:  # noqa: S310 - loopback endpoint
        return json.loads(response.read().decode("utf-8"))


def _post(url: str, payload: dict[str, Any], timeout: float = 60.0) -> Any:
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback endpoint
        return json.loads(response.read().decode("utf-8"))


def _gpu_memory() -> dict[str, Any] | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [exe, "--query-gpu=memory.used,memory.total,utilization.gpu,power.draw", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    parts = [item.strip() for item in result.stdout.strip().splitlines()[0].split(",")]
    try:
        return {"used_mib": int(parts[0]), "total_mib": int(parts[1]), "utilization_pct": int(parts[2]), "power_w": float(parts[3])}
    except (ValueError, IndexError):
        return None


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _history_timing(entry: dict[str, Any]) -> dict[str, Any]:
    """Read ComfyUI's own task record rather than the client's wall clock."""

    messages = ((entry.get("status") or {}).get("messages") or [])
    stamps: dict[str, float] = {}
    for name, payload in messages:
        if name in {"execution_start", "execution_success", "execution_error"} and isinstance(payload, dict):
            stamp = payload.get("timestamp")
            if isinstance(stamp, (int, float)):
                stamps[name] = float(stamp) / 1000.0
    started, finished = stamps.get("execution_start"), stamps.get("execution_success")
    server_seconds = round(finished - started, 3) if started is not None and finished is not None else None
    return {
        "execution_start": started,
        "execution_success": finished,
        "server_seconds": server_seconds,
        "status": str((entry.get("status") or {}).get("status_str") or ""),
    }


def _output_files(entry: dict[str, Any], output_root: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for node_output in (entry.get("outputs") or {}).values():
        for image in (node_output.get("images") or []):
            candidate = output_root / str(image.get("subfolder") or "") / str(image.get("filename") or "")
            record: dict[str, Any] = {
                "filename": image.get("filename"),
                "subfolder": image.get("subfolder") or "",
                "type": image.get("type"),
                "exists": candidate.is_file(),
            }
            if candidate.is_file():
                record["bytes"] = candidate.stat().st_size
                record["sha256"] = _sha256(candidate)
                size = _png_size(candidate)
                if size is not None:
                    record["width"], record["height"] = size
            files.append(record)
    return files


def run_task(
    task: dict[str, Any],
    *,
    base_url: str,
    output_root: Path,
    service: WorkflowDefinitionService,
    timeout_seconds: float,
) -> dict[str, Any]:
    compiled = service.instantiate(str(task["definition"]), dict(task["parameters"]))
    graph = compiled["workflow"]
    # The prompt is a task input, not a definition default: inject it through
    # the registered semantic binding so the executed graph is the frozen one.
    prompt_binding = compiled["node_bindings"]["PROMPT"]
    graph[str(prompt_binding["node_id"])]["inputs"][str(prompt_binding["input"])] = str(task["prompt"])

    record: dict[str, Any] = {
        "code": task["code"],
        "group": task["group"],
        "definition": task["definition"],
        "requirement": task["requirement"],
        "prompt": task["prompt"],
        "parameters": task["parameters"],
        "expected": task.get("expects", {}),
        "workflow_content_hash": hashlib.sha256(json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
    }
    record["gpu_before"] = _gpu_memory()
    client_started = time.monotonic()
    try:
        queued = _post(f"{base_url}/prompt", {"prompt": graph, "client_id": f"qwen21-acceptance-{task['code']}"})
    except (URLError, OSError, ValueError) as error:
        record.update(status="SUBMIT_FAILED", error=f"{type(error).__name__}: {error}")
        return record
    prompt_id = str(queued.get("prompt_id") or "")
    record["prompt_id"] = prompt_id
    if not prompt_id:
        record.update(status="REJECTED", error=json.dumps(queued, ensure_ascii=False)[:500])
        return record

    entry: dict[str, Any] = {}
    while time.monotonic() - client_started < timeout_seconds:
        history = _get(f"{base_url}/history/{prompt_id}") or {}
        entry = history.get(prompt_id) or {}
        if entry:
            break
        time.sleep(1.0)
    record["client_seconds"] = round(time.monotonic() - client_started, 3)
    record["gpu_after"] = _gpu_memory()
    if not entry:
        record["status"] = "TIMEOUT"
        return record
    record.update(_history_timing(entry))
    record["outputs"] = _output_files(entry, output_root)
    record["status"] = "SUCCESS" if record.get("status") == "success" and record["outputs"] else "FAILED"
    return record


def _deferred_tasks(group: str) -> list[dict[str, Any]]:
    """Tasks this runner cannot execute itself, reported as explicit debt."""

    if group != "legacy":
        return []
    return [
        {
            "code": "LEGACY_REGRESSION",
            "group": "legacy",
            "status": "NOT_RUN_BY_THIS_SCRIPT",
            "requirement": "2512 一张、2511 一张、现有视频流程一段：必须对 8188 生产运行时单独执行，确认升级后既有节点、编码与 artifact 提升未被破坏。",
        }
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pin", type=Path, default=_REPO_ROOT / "config" / "comfyui-qwen21-runtime.json")
    parser.add_argument("--base-url")
    parser.add_argument("--group", action="append", choices=GROUPS, default=[])
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--output", type=Path, default=_REPO_ROOT / "work" / "qwen21" / "acceptance.json")
    args = parser.parse_args(argv)

    pin = json.loads(args.pin.read_text(encoding="utf-8"))
    base_url = args.base_url or f"http://{pin['host']}:{pin['port']}"
    output_root = Path(str(pin["io_root"])) / "output"
    groups = args.group or ["qwen21", "qwen21_queue"]
    tasks = [task for task in TASKS if task["group"] in groups]
    service = WorkflowDefinitionService(Settings())

    started = time.monotonic()
    records: list[dict[str, Any]] = []
    for task in tasks:
        record = run_task(task, base_url=base_url, output_root=output_root, service=service, timeout_seconds=args.timeout_seconds)
        cold = not records
        record["phase"] = "COLD_START" if cold else "WARM"
        records.append(record)
        print(f"[{record['status']:>13}] {record['code']:<22} server={record.get('server_seconds')}s client={record.get('client_seconds')}s", flush=True)

    for task in _deferred_tasks(groups[0] if len(groups) == 1 else ""):
        records.append(task)

    warm = [item.get("server_seconds") for item in records if item.get("phase") == "WARM" and item.get("server_seconds")]
    report = {
        "schema_version": "localdrama.qwen21-acceptance.v1",
        "generated_at": _now(),
        "comfy_base_url": base_url,
        "runtime_code": pin["runtime_code"],
        "expected_commit": pin["comfy_commit"],
        "groups": groups,
        "wall_seconds": round(time.monotonic() - started, 3),
        "summary": {
            "total": len(records),
            "succeeded": sum(1 for item in records if item.get("status") == "SUCCESS"),
            "failed": [item["code"] for item in records if item.get("status") not in {"SUCCESS", "NOT_RUN_BY_THIS_SCRIPT"}],
            "cold_start_seconds": next((item.get("server_seconds") for item in records if item.get("phase") == "COLD_START"), None),
            "warm_median_seconds": round(sorted(warm)[len(warm) // 2], 3) if warm else None,
        },
        "tasks": records,
        "notes": [
            "计时全部取自 ComfyUI /history 的 execution_start→execution_success，不含 HTTP 提交返回时刻。",
            "冷启动与后续任务分开记录；每个任务使用不同 seed，避免命中图缓存后误记为推理速度。",
            "本记录只覆盖 Qwen-Image-2.1 验收；原链回归必须在 8188 生产运行时单独执行。",
            "RGBA 未纳入本次发布范围，因此未验证非恒定 alpha 通道；如需开放须另做缩略图/素材库/合成链验证。",
        ],
        "passed": all(item.get("status") == "SUCCESS" for item in records if item.get("status") != "NOT_RUN_BY_THIS_SCRIPT"),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"evidence: {args.output}")
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
