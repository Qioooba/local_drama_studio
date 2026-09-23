"""探测 H3 采样器对 width/height/length 的实际约束。

做法：直接调用 MiniMaxH3ImageToVideo 与 SamplerCustomAdvanced，只做 1 步采样，
逐个组合测试，记录哪些组合通过、哪些报形状错，从而得出可用参数集合。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8188"


def post(path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE}{path}", data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def graph(width: int, height: int, length: int) -> dict:
    return {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": "MiniMax-H3\\minimax_h3_fl2va_pruned_int8_convrot.safetensors",
            "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "MiniMax-H3\\qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
            "device": "default", "type": "minimax"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "MiniMax-H3\\minimax_h3_video_vae_fp16.safetensors"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "MiniMax-H3\\minimax_h3_audio_vae_fp32.safetensors"}},
        "5": {"class_type": "LoadImage", "inputs": {"image": "xianxia_first_frame.jpg"}},
        "6": {"class_type": "ImageScale", "inputs": {
            "crop": "disabled", "height": height, "image": ["5", 0],
            "upscale_method": "lanczos", "width": width}},
        "7": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
            "clip": ["2", 0], "first_frame": ["6", 0], "height": height, "length": length,
            "prompt": "红灯笼轻轻摆动，环境安静", "vae": ["3", 0], "width": width}},
        "8": {"class_type": "RandomNoise", "inputs": {"noise_seed": 7}},
        "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "10": {"class_type": "BasicScheduler", "inputs": {
            "denoise": 1, "model": ["1", 0], "scheduler": "simple", "steps": 1}},
        "11": {"class_type": "BasicGuider", "inputs": {"conditioning": ["7", 0], "model": ["1", 0]}},
        "12": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "guider": ["11", 0], "latent_image": ["7", 1], "noise": ["8", 0],
            "sampler": ["9", 0], "sigmas": ["10", 0]}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["3", 0]}},
        "14": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["12", 0], "vae": ["4", 0]}},
        "15": {"class_type": "CreateVideo", "inputs": {"audio": ["14", 0], "bit_depth": 8, "fps": 24, "images": ["13", 0]}},
        "16": {"class_type": "SaveVideo", "inputs": {
            "video": ["15", 0], "filename_prefix": "local_drama/h3_probe", "format": "mp4"}},
    }


def try_combo(width: int, height: int, length: int) -> tuple[bool, str]:
    try:
        queued = post("/prompt", {"prompt": graph(width, height, length), "client_id": "h3-probe"})
    except urllib.error.HTTPError as error:
        return False, f"HTTP {error.code}: {error.read().decode('utf-8', 'replace')[:200]}"
    prompt_id = str(queued.get("prompt_id") or "")
    deadline = time.time() + 900
    while time.time() < deadline:
        time.sleep(5)
        entry = (get(f"/history/{prompt_id}") or {}).get(prompt_id)
        if not entry:
            continue
        status = entry.get("status") or {}
        if status.get("completed"):
            return True, "ok"
        for message in status.get("messages") or []:
            if message[0] == "execution_error":
                info = message[1]
                return False, f"{info.get('node_type')}: {str(info.get('exception_message'))[:160]}"
        return False, json.dumps(status, ensure_ascii=False)[:200]
    return False, "timeout"


def main() -> None:
    combos = [(int(a), int(b), int(c)) for a, b, c in (item.split(",") for item in sys.argv[1:])]
    for width, height, length in combos:
        ok, note = try_combo(width, height, length)
        print(f"{width}x{height} len={length}: {'PASS' if ok else 'FAIL'} {note}", flush=True)


if __name__ == "__main__":
    main()
