"""Generate one real image per explainer style, using the product's own workflow.

Why this drives ComfyUI directly
--------------------------------
The explainer's V2 submit path is blocked on this machine (every IMAGE_* profile was
published with an empty parameter contract, so any semantic input is refused with
``MP_PARAMETER_UNKNOWN``).  The *style* question — what our text-to-image actually
produces, and whether it suits an explainer — can still be answered with the real
models: this script takes the **compiled prompt the product produced** (through the
read-only plan route, so the style override, hard constraints and geometry are the
product's own) and runs it on the **workflow the profile binds**, then keeps the image.

    python scripts/audit_explainer_t2i_comfy.py --project <id> [--styles key,key]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

COMFY = "http://127.0.0.1:8188"
API = "http://127.0.0.1:3210"
CONTRACT = "localdrama.api.2026-08-29.3"
OUT = Path("artifacts/t2i-style-audit")
WORKFLOW_VERSION_ID = "0cc3707f-4300-4d71-9e1f-65151ec68a21"

STYLES: list[dict[str, Any]] = [
    {"key": "00_default", "label": "产品默认（未选风格）", "override": None, "negative": None, "genre": "科普"},
    {
        "key": "01_documentary", "label": "写实纪录片实拍", "genre": "历史/调查",
        "override": "纪实摄影风格，真实材质与自然光，浅景深，35mm 镜头质感，冷调环境色，无插画感",
        "negative": "插画、卡通、3D 渲染、文字、水印",
    },
    {
        "key": "02_photo_clean", "label": "写实干净（科普常用）", "genre": "科普/医学",
        "override": "纯净写实三维渲染，柔和棚拍布光，主体清晰居中，背景干净渐变，细节克制",
        "negative": "文字、字幕、水印、多余人物、杂乱背景",
    },
    {
        "key": "03_flat_infographic", "label": "扁平信息图", "genre": "财经/技术教程",
        "override": "扁平矢量插画，几何色块，无渐变阴影，留白充足，信息图风格，配色克制",
        "negative": "照片质感、3D 高光、文字、水印",
    },
    {
        "key": "04_watercolor", "label": "水彩手绘", "genre": "人文/美食",
        "override": "水彩手绘插画，纸张纹理，柔和晕染边缘，淡彩配色，笔触可见",
        "negative": "照片写实、3D 渲染、文字、水印",
    },
    {
        "key": "05_tech_neon", "label": "暗色科技霓虹", "genre": "航天/技术",
        "override": "暗色科技感 CG 渲染，霓虹蓝青高光，体积光与粒子，未来感，高对比",
        "negative": "日光写实、插画、文字、水印",
    },
    {
        "key": "06_retro_film", "label": "复古胶片", "genre": "历史/人物",
        "override": "复古胶片摄影，可见颗粒与轻微漏光，暖黄色调，70 年代质感，低饱和",
        "negative": "数码锐利、霓虹、文字、水印",
    },
    {
        "key": "07_ink_wash", "label": "国风水墨", "genre": "历史/文化",
        "override": "中国水墨画风格，大量留白，墨色浓淡层次，写意笔触，宣纸质感",
        "negative": "西式油画、照片写实、文字、水印",
    },
    {
        "key": "08_children_book", "label": "儿童绘本", "genre": "儿童故事",
        "override": "儿童绘本插画，粗线条勾边，明快原色，造型圆润可爱，平涂上色",
        "negative": "写实照片、恐怖元素、文字、水印",
    },
    {
        "key": "09_science_viz", "label": "科学可视化（显微/荧光）", "genre": "科普硬核",
        "override": "科学可视化插画，深色背景，荧光染色发光质感，柔和体积散射，微观尺度感",
        "negative": "日常摄影、卡通、文字、水印",
    },
]


def _api(method: str, path: str, token: str | None = None, payload: dict | None = None) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"X-API-Contract-Version": CONTRACT}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token and method in {"POST", "PATCH"}:
        headers["X-Local-Instance-Token"] = token
    request = urllib.request.Request(f"{API}{path}", data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def _comfy(method: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    request = urllib.request.Request(f"{COMFY}{path}", data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def _workflow() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    con = sqlite3.connect(r"data\local_drama.sqlite3")
    con.row_factory = sqlite3.Row
    row = con.execute(
        "select content_json, node_bindings_json, contract_json from workflow_versions where id=?",
        (WORKFLOW_VERSION_ID,),
    ).fetchone()
    return json.loads(row["content_json"]), json.loads(row["node_bindings_json"]), json.loads(row["contract_json"])


def _generate(prompt: str, negative: str, seed: int, width: int, height: int) -> Path | None:
    graph, bindings, contract = _workflow()
    defaults = contract.get("authoring_parameters") or {}
    values = {
        "PROMPT": prompt,
        "NEGATIVE_PROMPT": negative or str(defaults.get("negative_prompt") or ""),
        "WIDTH": width,
        "HEIGHT": height,
        "SEED": seed,
        "STEPS": int(defaults.get("steps") or 20),
        "CFG": float(defaults.get("cfg") or 4.0),
        "DENOISE": float(defaults.get("denoise") or 1.0),
        "SAMPLER": str(defaults.get("sampler") or "euler"),
        "SCHEDULER": str(defaults.get("scheduler") or "simple"),
        "RESOLUTION": str(defaults.get("resolution") or "1024"),
        "OUTPUT_PREFIX": f"local_drama/t2i_style_audit/{seed}",
    }
    for name, binding in bindings.items():
        if name not in values:
            continue
        node = graph.get(str(binding["node_id"]))
        if not isinstance(node, dict):
            continue
        node.setdefault("inputs", {})[str(binding["input"])] = values[name]
    client_id = f"t2i-audit-{seed}"
    queued = _comfy("POST", "/prompt", {"prompt": graph, "client_id": client_id})
    prompt_id = str(queued.get("prompt_id") or "")
    if not prompt_id:
        print("    comfy refused:", json.dumps(queued, ensure_ascii=False)[:300])
        return None
    deadline = time.time() + 900
    while time.time() < deadline:
        time.sleep(5)
        history = _comfy("GET", f"/history/{prompt_id}")
        entry = history.get(prompt_id)
        if not entry:
            continue
        outputs = entry.get("outputs") or {}
        for node_output in outputs.values():
            for image in node_output.get("images") or []:
                query = urllib.parse.urlencode(
                    {"filename": image["filename"], "subfolder": image.get("subfolder", ""), "type": image.get("type", "output")}
                )
                with urllib.request.urlopen(f"{COMFY}/view?{query}", timeout=180) as response:
                    data = response.read()
                target = OUT / f"comfy_{seed}.png"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                return target
        status = (entry.get("status") or {}).get("status_str")
        if status in {"error", "failed"}:
            print("    comfy failed:", json.dumps(entry.get("status"), ensure_ascii=False)[:300])
            return None
    print("    comfy timeout")
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--styles", default="")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    token = str(_api("GET", "/api/v1/session/bootstrap").get("token") or "")
    overview = _api("GET", f"/api/v2/explainers/{args.project}")
    video = overview.get("video") or {}
    revision = int(video.get("revision") or 0)
    beats = _api("GET", f"/api/v2/explainers/{args.project}/beats").get("beats") or []
    beat = beats[0]
    wanted = {item.strip() for item in args.styles.split(",") if item.strip()}
    report: list[dict[str, Any]] = []

    for style in STYLES:
        if wanted and style["key"] not in wanted:
            continue
        print(f"\n=== {style['key']} {style['label']} [{style['genre']}]")
        if style["override"]:
            patched = _api(
                "PATCH",
                f"/api/v2/explainers/{args.project}/visual-preferences",
                token,
                {
                    "expected_revision": revision,
                    "visual_preferences": {
                        "style_prompt_override": style["override"],
                        "negative_prompt_override": style["negative"],
                    },
                },
            )
            revision = int((patched.get("video") or {}).get("revision") or revision + 1)
        else:
            # The product default: clear any override so the compiler uses its own
            # versioned default style (the state a fresh install is in).
            patched = _api(
                "PATCH",
                f"/api/v2/explainers/{args.project}/visual-preferences",
                token,
                {
                    "expected_revision": revision,
                    "visual_preferences": {"style_prompt_override": None, "negative_prompt_override": None},
                },
            )
            revision = int((patched.get("video") or {}).get("revision") or revision + 1)

        plan = _api(
            "POST",
            f"/api/v2/explainers/{args.project}/beats/{beat['id']}/generations:plan",
            token,
            {
                "operation_id": f"comfy-style-{style['key']}",
                "purpose": "KEYFRAME",
                "mode": "TEXT_TO_IMAGE",
                "candidate_count": 1,
                "expected_beat_revision": int(beat.get("revision") or 0),
            },
        )
        prompt = str(plan.get("prompt") or "")
        negative = str(plan.get("negative_prompt") or "")
        print("    product prompt:", prompt[:200])
        if not prompt:
            report.append({"style": style, "error": "empty prompt"})
            continue
        image = _generate(prompt, negative, seed=20260925 + abs(hash(style["key"])) % 100000, width=1024, height=576)
        report.append(
            {
                "style": style,
                "product_prompt": prompt,
                "negative_prompt": negative,
                "image": str(image) if image else None,
            }
        )
        print("    image:", image)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "comfy-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nreport:", args.out / "comfy-report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
