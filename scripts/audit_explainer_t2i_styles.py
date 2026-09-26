"""Real text-to-image style audit for the explainer factory.

What it does
------------
For one beat it runs the product's own generation path (PATCH visual preferences →
`/generations:plan` → `/generations` → real V2 job on ComfyUI) once per style, then
downloads the produced image so a human (and the local vision model) can look at it.

Nothing is mocked: the plan comes from the real capability/profile resolution, the
prompt is the compiled prompt the worker used, and the image is the media version the
worker registered.  A style whose plan is blocked is recorded with its real blockers.

    python scripts/audit_explainer_t2i_styles.py --project <project_id> [--beat <beat_id>]
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API = "http://127.0.0.1:3210"
CONTRACT = "localdrama.api.2026-08-29.3"
OUT = Path("artifacts/t2i-style-audit")

#: One entry per tested explainer style.  ``override=None`` is the product default
#: (no channel profile exists yet, so the compiler falls back to its内置 default style).
STYLES: list[dict[str, Any]] = [
    {
        "key": "00_default",
        "label": "产品默认（未选风格）",
        "override": None,
        "negative": None,
        "genre": "科普",
    },
    {
        "key": "01_documentary",
        "label": "写实纪录片实拍",
        "override": "纪实摄影风格，真实材质与自然光，浅景深，35mm 镜头质感，冷调环境色，无插画感",
        "negative": "插画、卡通、3D 渲染、文字、水印",
        "genre": "历史/调查",
    },
    {
        "key": "02_photo_clean",
        "label": "写实干净（科普常用）",
        "override": "纯净写实三维渲染，柔和棚拍布光，主体清晰居中，背景干净渐变，细节克制",
        "negative": "文字、字幕、水印、多余人物、杂乱背景",
        "genre": "科普/医学",
    },
    {
        "key": "03_flat_infographic",
        "label": "扁平信息图",
        "override": "扁平矢量插画，几何色块，无渐变阴影，留白充足，信息图风格，配色克制",
        "negative": "照片质感、3D 高光、文字、水印",
        "genre": "财经/技术教程",
    },
    {
        "key": "04_watercolor",
        "label": "水彩手绘",
        "override": "水彩手绘插画，纸张纹理，柔和晕染边缘，淡彩配色，笔触可见",
        "negative": "照片写实、3D 渲染、文字、水印",
        "genre": "人文/美食",
    },
    {
        "key": "05_tech_neon",
        "label": "暗色科技霓虹",
        "override": "暗色科技感 CG 渲染，霓虹蓝青高光，体积光与粒子，未来感，高对比",
        "negative": "日光写实、插画、文字、水印",
        "genre": "航天/技术",
    },
    {
        "key": "06_retro_film",
        "label": "复古胶片",
        "override": "复古胶片摄影，可见颗粒与轻微漏光，暖黄色调，70 年代质感，低饱和",
        "negative": "数码锐利、霓虹、文字、水印",
        "genre": "历史/人物",
    },
    {
        "key": "07_ink_wash",
        "label": "国风水墨",
        "override": "中国水墨画风格，大量留白，墨色浓淡层次，写意笔触，宣纸质感",
        "negative": "西式油画、照片写实、文字、水印",
        "genre": "历史/文化",
    },
    {
        "key": "08_children_book",
        "label": "儿童绘本",
        "override": "儿童绘本插画，粗线条勾边，明快原色，造型圆润可爱，平涂上色",
        "negative": "写实照片、恐怖元素、文字、水印",
        "genre": "儿童故事",
    },
    {
        "key": "09_science_viz",
        "label": "科学可视化（显微/荧光）",
        "override": "科学可视化插画，深色背景，荧光染色发光质感，柔和体积散射，微观尺度感",
        "negative": "日常摄影、卡通、文字、水印",
        "genre": "科普硬核",
    },
]


def _request(
    method: str,
    path: str,
    token: str | None = None,
    payload: dict | None = None,
    *,
    idempotency_key: str | None = None,
) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"X-API-Contract-Version": CONTRACT}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token and method in {"POST", "PATCH", "PUT", "DELETE"}:
        headers["X-Local-Instance-Token"] = token
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(f"{API}{path}", data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def _download(url: str, target: Path) -> bool:
    try:
        with urllib.request.urlopen(f"{API}{url}", timeout=180) as response:
            data = response.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
        print(f"    download failed: {error}")
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return True


def _session_token() -> str:
    return str(_request("GET", "/api/v1/session/bootstrap").get("token") or "")


def _copy_registered_media(project_id: str, media_version_id: str, target: Path) -> Path | None:
    """Copy the registered media file out of controlled project storage.

    Reading the produced file is what lets the audit judge real pixels; the API's
    only image read surface is the derived thumbnail cache.
    """

    import sqlite3

    database = Path(__file__).resolve().parents[1] / "data" / "local_drama.sqlite3"
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT mv.rel_path, p.root_rel FROM media_versions mv "
            "JOIN media_assets ma ON ma.id = mv.media_asset_id "
            "JOIN projects p ON p.id = ma.project_id WHERE mv.id = ?",
            (media_version_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    source = Path(__file__).resolve().parents[1] / "projects" / str(row["root_rel"]) / str(row["rel_path"])
    if not source.is_file():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--beat", default="")
    parser.add_argument("--styles", default="", help="comma separated style keys (default: all)")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--timeout", type=int, default=900, help="seconds to wait per candidate")
    args = parser.parse_args()

    token = _session_token()
    wanted = {item.strip() for item in args.styles.split(",") if item.strip()}
    styles = [item for item in STYLES if not wanted or item["key"] in wanted]

    overview = _request("GET", f"/api/v2/explainers/{args.project}")
    video = overview.get("video") or {}
    video_id = str(video.get("id") or "")
    video_revision = int(video.get("revision") or 0)
    beats = _request("GET", f"/api/v2/explainers/{args.project}/beats")
    beat_rows = beats.get("beats") or beats.get("items") or []
    if not beat_rows:
        print("no beats in this workspace")
        return 2
    beat = next((item for item in beat_rows if str(item.get("id")) == args.beat), beat_rows[0])
    beat_id = str(beat["id"])
    print(f"project={args.project} video={video_id} beat={beat_id} code={beat.get('code')}")
    print(f"beat intent: {str(beat.get('visual_intent') or beat.get('prompt_intent') or '')[:120]}")

    report: list[dict[str, Any]] = []
    for style in styles:
        print(f"\n=== {style['key']} {style['label']} [{style['genre']}]")
        entry: dict[str, Any] = {"style": style, "beat_id": beat_id}

        # 1. the real style path: write this film's visual preferences.
        #
        # This runs for *every* style, including the product default: the
        # preferences are persisted on the film, so skipping the reset for
        # ``override is None`` made the "default" run reuse whichever style the
        # previous iteration had written.  The first version of this audit
        # therefore reported a science-visualisation ocean render as the product
        # default, purely because ``09_science_viz`` had run before it.
        try:
            patched = _request(
                "PATCH",
                f"/api/v2/explainers/{args.project}/visual-preferences",
                token,
                {
                    "expected_revision": video_revision,
                    "visual_preferences": {
                        "style_prompt_override": style["override"] or None,
                        "negative_prompt_override": style["negative"] or None,
                    },
                },
            )
            video_revision = int((patched.get("video") or {}).get("revision") or video_revision + 1)
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", "replace")[:300]
            print(f"    PATCH failed: {error.code} {body}")
            entry["error"] = f"PATCH {error.code}: {body}"
            report.append(entry)
            continue

        current_beat = next(
            (item for item in (_request("GET", f"/api/v2/explainers/{args.project}/beats").get("beats") or []) if str(item.get("id")) == beat_id),
            beat,
        )
        operation_id = f"t2i-style-{style['key']}-{int(time.time())}"
        command = {
            "operation_id": operation_id,
            "purpose": "KEYFRAME",
            "mode": "TEXT_TO_IMAGE",
            "candidate_count": 1,
            "expected_beat_revision": int(current_beat.get("revision") or 0),
        }

        # 2. read-only plan (shows the frozen prompt, profile and blockers)
        try:
            plan = _request(
                "POST", f"/api/v2/explainers/{args.project}/beats/{beat_id}/generations:plan", token, command
            )
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", "replace")[:400]
            print(f"    plan failed: {error.code} {body}")
            entry["error"] = f"plan {error.code}: {body}"
            report.append(entry)
            continue
        entry["plan"] = {
            "status": plan.get("status"),
            "plan_hash": plan.get("plan_hash"),
            "profile": plan.get("profile_title") or plan.get("execution_profile_version_id"),
            "blockers": plan.get("blockers"),
            "budget": plan.get("budget"),
            "prompt": plan.get("prompt"),
            "negative_prompt": plan.get("negative_prompt"),
            "frozen_inputs": plan.get("frozen_inputs"),
            "profile_parameters": plan.get("profile_parameters"),
        }
        prompt = str(plan.get("prompt") or "")
        print(f"    plan status={plan.get('status')} profile={entry['plan']['profile']}")
        print(f"    compiled prompt: {prompt[:260]}")
        print(f"    negative: {str(plan.get('negative_prompt') or '')[:160]}")
        if plan.get("status") != "EXECUTABLE":
            print(f"    blockers: {json.dumps(plan.get('blockers'), ensure_ascii=False)[:220]}")
            report.append(entry)
            continue

        # 3. submit the real job
        try:
            submitted = _request(
                "POST",
                f"/api/v2/explainers/{args.project}/beats/{beat_id}/generations",
                token,
                command,
                idempotency_key=operation_id,
            )
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", "replace")[:400]
            print(f"    submit failed: {error.code} {body}")
            entry["error"] = f"submit {error.code}: {body}"
            report.append(entry)
            continue
        entry["submit"] = submitted
        candidate_ids = [str(item.get("candidate_id") or item.get("id")) for item in (submitted.get("items") or [])]
        print(f"    submitted: {json.dumps(submitted, ensure_ascii=False)[:200]}")

        # 4. wait for the worker to register the media
        deadline = time.time() + args.timeout
        candidates: list[dict[str, Any]] = []
        while time.time() < deadline:
            time.sleep(10)
            page = _request(
                "GET",
                f"/api/v2/explainers/{args.project}/beats/{beat_id}/candidates?purpose=KEYFRAME",
            )
            rows = [item for item in (page.get("candidates") or []) if str(item.get("id")) in candidate_ids]
            states = [str(item.get("status")) for item in rows]
            if rows and all(state in {"READY", "FAILED"} for state in states):
                candidates = rows
                break
            print(f"    waiting… {states}")
        entry["candidates"] = candidates
        ready = [item for item in candidates if str(item.get("status")) == "READY" and item.get("media_version_id")]
        if not ready:
            print(f"    no READY candidate: {json.dumps(candidates, ensure_ascii=False)[:300]}")
            report.append(entry)
            continue

        # 5. keep the real image for review.  The product refuses to serve an
        # image original on purpose (``IMAGE_CONTENT_REQUIRES_THUMBNAIL``), so the
        # audit reads the derived cache exactly like the page does; the registered
        # media file itself is additionally copied from controlled storage so the
        # review sees the true output pixels, not a re-encode.
        media_version_id = str(ready[0]["media_version_id"])
        target = args.out / f"{style['key']}.png"
        ok = _download(f"/api/v1/media-versions/{media_version_id}/thumbnail?size=medium&frame=poster", target)
        entry["image"] = str(target) if ok else None
        entry["media_version_id"] = media_version_id
        original = _copy_registered_media(args.project, media_version_id, args.out / f"{style['key']}_original.png")
        entry["original"] = str(original) if original else None
        print(f"    image -> {target if ok else 'download failed'}; original -> {original or 'unavailable'}")
        report.append(entry)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nreport: {args.out / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
