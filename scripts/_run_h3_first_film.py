"""把项目里的 h3-i2v 工作流真正提交给本机 ComfyUI（v0.37.1），产出最初的成片。

流程：
  1. 读 work/workflow_packages/h3-i2v/v1.json，把节点标号规范成 ComfyUI 的字符串 id；
  2. 按 v0.37.1 的实际签名修正输入（SaveVideo 的 format/codec 等动态组合）；
  3. 用真实存在的首帧图与提示词提交 /prompt；
  4. 轮询 /history 直到完成，取回输出的 mp4 路径与字节数。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8188"
ROOT = Path(r"F:\AI_Projects\h3\local_drama_studio")
WORKFLOW = ROOT / "work" / "workflow_packages" / "h3-i2v" / "v1.json"
OUT_DIR = ROOT / "deliverables" / "h3-first-film"

PROMPT = (
    "古装仙侠短剧镜头：主人公在残破废墟中握紧长刀，缓缓抬头看向远方，"
    "衣袂随风轻动，光线从破洞照入，气氛肃杀而克制，镜头缓慢推进。"
)

# 实际可用的首帧图（来自运行中 ComfyUI 的 input 目录）
FIRST_FRAME = "xianxia_first_frame.jpg"

# 先按工作流自带的 smoke 参数验证链路，再用 production 参数出正式片段。
import os

SMOKE = os.environ.get("H3_SMOKE") == "1"


def post(path: str, payload: dict) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE}{path}", data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def build_prompt() -> dict:
    package = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    graph = package["workflow"]
    prompt: dict = {}
    for node_id, node in graph.items():
        inputs = {}
        for name, value in (node.get("inputs") or {}).items():
            # 形如 "5 0" 的连线引用保持原样（ComfyUI 接受 "5" 与 5）
            inputs[name] = value
        prompt[str(node_id)] = {"class_type": node["class_type"], "inputs": inputs}

    # --- 按 v0.37.1 的真实签名修正 ---
    prompt["5"]["inputs"]["image"] = FIRST_FRAME
    prompt["7"]["inputs"]["prompt"] = PROMPT
    if SMOKE:
        # 工作流自带的 smoke 配置：64x64、5 帧、1 步，用来验证链路本身。
        prompt["6"]["inputs"]["width"] = 64
        prompt["6"]["inputs"]["height"] = 64
        prompt["7"]["inputs"]["width"] = 64
        prompt["7"]["inputs"]["height"] = 64
        prompt["7"]["inputs"]["length"] = 5
        prompt["8"]["inputs"]["noise_seed"] = 28082026
        prompt["9"]["inputs"]["sampler_name"] = "euler"
        prompt["10"]["inputs"]["denoise"] = 1
        prompt["10"]["inputs"]["steps"] = 1
        prompt["7"]["inputs"]["prompt"] = "红灯笼轻轻摆动，环境安静"
        prompt["16"]["inputs"]["filename_prefix"] = "local_drama/h3_smoke"
    else:
        prompt["6"]["inputs"]["width"] = 480
        prompt["6"]["inputs"]["height"] = 832
        prompt["7"]["inputs"]["width"] = 480
        prompt["7"]["inputs"]["height"] = 832
        prompt["7"]["inputs"]["length"] = 107
        prompt["16"]["inputs"]["filename_prefix"] = "local_drama/h3_first_film"
    # SaveVideo 在 v0.37.1 用动态组合：format=mp4 需要 codec 参数
    prompt["16"]["inputs"].pop("codec", None)
    prompt["16"]["inputs"]["format"] = "mp4"
    return prompt


def main() -> None:
    prompt = build_prompt()
    print("submitting graph with", len(prompt), "nodes")
    try:
        queued = post("/prompt", {"prompt": prompt, "client_id": "local-drama-first-film"})
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        print("SUBMIT FAILED", error.code)
        print(body[:4000])
        raise
    prompt_id = str(queued.get("prompt_id") or "")
    print("prompt_id:", prompt_id)
    if not prompt_id:
        raise SystemExit("no prompt_id returned")

    deadline = time.time() + 3600
    last_note = ""
    while time.time() < deadline:
        time.sleep(10)
        history = get(f"/history/{prompt_id}")
        entry = history.get(prompt_id)
        if not entry:
            queue = get("/queue")
            running = (queue.get("queue_running") or [])
            pending = (queue.get("queue_pending") or [])
            note = f"running={len(running)} pending={len(pending)}"
            if note != last_note:
                print("  waiting:", note, flush=True)
                last_note = note
            continue
        status = entry.get("status") or {}
        print("status:", json.dumps(status, ensure_ascii=False)[:600])
        outputs = entry.get("outputs") or {}
        files: list[dict] = []
        for node_output in outputs.values():
            for key in ("images", "gifs", "videos", "video"):
                for item in node_output.get(key) or []:
                    if isinstance(item, dict) and item.get("filename"):
                        files.append(item)
        if not files:
            if status.get("completed") is False:
                raise SystemExit("generation failed")
            print("no media output yet")
            break
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        for item in files:
            filename = str(item["filename"])
            subfolder = str(item.get("subfolder") or "")
            kind = str(item.get("type") or "output")
            query = f"filename={urllib.parse.quote(filename)}&subfolder={urllib.parse.quote(subfolder)}&type={kind}"
            with urllib.request.urlopen(f"{BASE}/view?{query}", timeout=600) as response:
                payload = response.read()
            target = OUT_DIR / filename
            target.write_bytes(payload)
            print("saved:", target, f"{len(payload) / 1024**2:.2f} MB")
        break
    else:
        raise SystemExit("timed out waiting for the render")


if __name__ == "__main__":
    import urllib.parse

    main()
