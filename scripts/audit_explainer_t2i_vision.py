"""识图测试：把真实生成的画面交给本机视觉模型判读，核对风格与可用性。

用法：python scripts/audit_explainer_t2i_vision.py [--limit N]
输出：artifacts/t2i-style-audit/vision-report.json
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[0]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.infrastructure.local_llm import LocalLLMClient

OUT = Path("artifacts/t2i-style-audit")

SYSTEM = """你是解说视频的画面质检助手。你只能描述和判断你实际收到的这张图片，不能根据文件名、提示词或生成成功状态推测。
按给定标准输出 JSON：style_match 取 PASS/FAIL/UNKNOWN，evidence 写你在画面里实际看到的依据。
画面里若出现可读文字、字幕、水印、编号、logo 一律算文字瑕疵。看不清或无法判断就用 UNKNOWN，不要猜。"""

USER_TEMPLATE = """期望风格：{style}
画面意图：{intent}
请判断这张画面是否适合用在中文解说视频里，并输出 JSON：
{{"describes":"一句话描述你看到的内容","style_match":"PASS/FAIL/UNKNOWN","style_evidence":"判断依据","has_readable_text":true/false,"text_seen":"看到的文字或空字符串","suits_explainer":"PASS/FAIL/UNKNOWN","problems":["列出真实问题，没有就空数组"]}}"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--report", default="report.json", help="report file inside the audit output directory")
    args = parser.parse_args()

    # The product-path audit report is the primary source: every entry there was
    # produced by the product's own plan/submit/job chain.  ``comfy-report.json``
    # (the earlier direct-ComfyUI audit) stays as a fallback for older runs.
    report_path = OUT / args.report
    if not report_path.exists():
        report_path = OUT / "comfy-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(f"report: {report_path} entries={len(report)}")
    client = LocalLLMClient(
        base_url="http://127.0.0.1:11434", model="qwen3.8:27b", provider="OLLAMA_LOOPBACK", timeout_seconds=900
    )
    results = []
    for entry in report[: args.limit]:
        # Prefer the registered original file over the derived thumbnail: the
        # product refuses image originals through the API on purpose, but the
        # audit must judge the true output pixels.
        image = entry.get("original") or entry.get("image")
        if not image or not Path(image).exists():
            print(f"   skip {entry.get('style', {}).get('key')}: no readable image")
            continue
        payload = base64.b64encode(Path(image).read_bytes()).decode("ascii")
        style = entry["style"]
        intent = entry.get("product_prompt") or (entry.get("plan") or {}).get("prompt") or ""
        user = USER_TEMPLATE.format(style=style.get("override") or "写实解说画面风格（产品默认）", intent=str(intent)[:200])
        print(f"=== {style['key']} {style['label']}")
        try:
            verdict = client.chat_json(SYSTEM, user, images=[payload], json_schema=None, inference_options={"temperature": 0, "max_tokens": 700})
        except Exception as error:  # noqa: BLE001 - a transport failure is recorded, never guessed away
            print("   vision failed:", type(error).__name__, error)
            results.append({"style": style, "image": image, "error": str(error)})
            continue
        print("   ", json.dumps(verdict, ensure_ascii=False)[:400])
        results.append({"style": style, "image": image, "verdict": verdict})

    (OUT / "vision-report.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nreport:", OUT / "vision-report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
