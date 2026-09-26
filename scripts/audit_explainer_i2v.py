"""真实图生视频（I2V）与多次抽卡审计。

背景（实测）：本机装有 MiniMax-H3 与已发布工作流
``workflow_packages/h3-i2v-16x9-silent-final/v3.json``（输入 image/prompt/length/noise_seed/filename_prefix），
但**没有任何 VIDEO_* 的 V2 Profile，也没有该工作流的能力绑定**，所以产品路径（步骤 5 的 AI 动态）连计划都拿不到 Profile。
本脚本因此分两部分：
  1. 用同一个工作流真实出片，判断“能不能用于解说、值不值得接线”；
  2. 同一画面段多次抽卡（不同 seed），看方差与“再生成”的语义。

    python scripts/audit_explainer_i2v.py --first-frame <png> [--draws 3]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

COMFY = "http://127.0.0.1:8188"
OUT = Path("artifacts/i2v-audit")
I2V_WORKFLOW_VERSION = "b5167cb5-4e0f-4ac7-9a49-3e9d3a8f9c1e"


def _workflow(version_id: str) -> tuple[dict, dict]:
    con = sqlite3.connect(r"data\local_drama.sqlite3")
    con.row_factory = sqlite3.Row
    row = con.execute(
        "select id, content_json, node_bindings_json from workflow_versions where status='PUBLISHED' and package_rel_path like '%h3-i2v%' order by version_no desc limit 1"
    ).fetchone()
    if row is None:
        raise SystemExit("no published h3-i2v workflow")
    print("workflow:", row["id"], "requested:", version_id)
    return json.loads(row["content_json"]), json.loads(row["node_bindings_json"])


def _upload(image: Path) -> str:
    boundary = "----t2iAuditBoundary"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="image"; filename="{image.name}"\r\n'.encode(),
            b"Content-Type: image/png\r\n\r\n",
            image.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    request = urllib.request.Request(
        f"{COMFY}/upload/image",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read().decode("utf-8"))
    name = payload.get("name")
    if payload.get("subfolder"):
        name = f"{payload['subfolder']}/{name}"
    return str(name)


def _run(graph: dict, bindings: dict, values: dict, timeout: int = 1800) -> list[Path]:
    for name, binding in bindings.items():
        if name not in values:
            continue
        node = graph.get(str(binding["node_id"]))
        if isinstance(node, dict):
            node.setdefault("inputs", {})[str(binding["input"])] = values[name]
    request = urllib.request.Request(
        f"{COMFY}/prompt",
        data=json.dumps({"prompt": graph, "client_id": "i2v-audit"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            queued = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        print(f"comfy HTTP {error.code}: {body[:600]}")
        return []
    prompt_id = str(queued.get("prompt_id") or "")
    if not prompt_id:
        print("comfy refused:", json.dumps(queued, ensure_ascii=False)[:400])
        return []
    deadline = time.time() + timeout
    saved: list[Path] = []
    while time.time() < deadline:
        time.sleep(10)
        with urllib.request.urlopen(f"{COMFY}/history/{prompt_id}", timeout=120) as response:
            history = json.loads(response.read().decode("utf-8"))
        entry = history.get(prompt_id)
        if not entry:
            continue
        for node_output in (entry.get("outputs") or {}).values():
            for key in ("gifs", "videos", "images"):
                for item in node_output.get(key) or []:
                    query = urllib.parse.urlencode(
                        {
                            "filename": item["filename"],
                            "subfolder": item.get("subfolder", ""),
                            "type": item.get("type", "output"),
                        }
                    )
                    with urllib.request.urlopen(f"{COMFY}/view?{query}", timeout=300) as response:
                        data = response.read()
                    suffix = item["filename"].rsplit(".", 1)[-1].lower()
                    target = OUT / f"i2v_{len(saved)}_{item['filename'].rsplit('.', 1)[0][-12:]}.{suffix}"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    saved.append(target)
        status = (entry.get("status") or {}).get("status_str")
        if saved or status in {"error", "failed"}:
            if status in {"error", "failed"}:
                print("comfy failed:", json.dumps(entry.get("status"), ensure_ascii=False)[:400])
            return saved
    print("timeout waiting for", prompt_id)
    return saved


def _probe(path: Path) -> dict:
    ffprobe = Path("tools/ffmpeg/bin/ffprobe.exe")
    if not ffprobe.exists():
        for candidate in Path(".").rglob("ffprobe.exe"):
            ffprobe = candidate
            break
    if not ffprobe.exists():
        return {"note": "ffprobe not found"}
    try:
        raw = subprocess.run(
            [str(ffprobe), "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,nb_frames,r_frame_rate,duration,codec_name", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=120, check=False,
        )
        return json.loads(raw.stdout or "{}")
    except Exception as error:  # noqa: BLE001
        return {"error": str(error)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--first-frame", required=True)
    parser.add_argument("--draws", type=int, default=3)
    parser.add_argument("--prompt", default="阳光从海面射入水下并逐渐衰减，水面轻微波动，镜头缓慢下移")
    parser.add_argument("--frames", type=int, default=49)
    args = parser.parse_args()

    graph, bindings = _workflow(I2V_WORKFLOW_VERSION)
    image_name = _upload(Path(args.first_frame))
    print("uploaded first frame:", image_name)

    report = []
    for draw in range(1, args.draws + 1):
        seed = 770000 + draw * 131
        values = {
            "PROMPT": args.prompt,
            # The published h3-i2v workflow binds these platform names (measured by
            # reading node_bindings_json; a wrong name fails ComfyUI validation).
            "FIRST_FRAME": image_name,
            "FRAME_COUNT": args.frames,
            "SEED": seed,
            "OUTPUT_PREFIX": f"local_drama/i2v_audit/draw{draw}",
        }
        print(f"\n=== draw {draw} seed={seed}")
        started = time.time()
        files = _run(graph, bindings, values)
        elapsed = round(time.time() - started, 1)
        entries = []
        for path in files:
            probe = _probe(path)
            print(f"    {path} ({path.stat().st_size} bytes, {elapsed}s) probe={json.dumps(probe, ensure_ascii=False)[:200]}")
            entries.append({"file": str(path), "bytes": path.stat().st_size, "probe": probe})
        report.append({"draw": draw, "seed": seed, "seconds": elapsed, "outputs": entries})

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nreport:", OUT / "report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
