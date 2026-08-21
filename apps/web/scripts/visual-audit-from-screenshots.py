import argparse
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone

from PIL import Image
import cv2
import numpy as np


def safe_float(value):
    return float(value) if np.isfinite(value) else 0.0


def list_runs(root_dir: str):
    if not os.path.isdir(root_dir):
        return []
    return sorted(
        [
            os.path.join(root_dir, name)
            for name in os.listdir(root_dir)
            if os.path.isdir(os.path.join(root_dir, name))
        ],
        reverse=True,
    )


def analyze_image(path: str):
    try:
        pil_image = Image.open(path).convert("RGB")
        image = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
    except Exception:
        return {
            "path": path,
            "status": "fail",
            "risk": "high",
            "resolution": "0x0",
            "issues": ["图片读取失败"],
            "metrics": {
                "entropy": 0.0,
                "contrast": 0.0,
                "content_ratio": 0.0,
                "blur": 0.0,
                "edge_density": 0.0,
                "mean_rgb": [0.0, 0.0, 0.0],
                "std_rgb": [0.0, 0.0, 0.0],
                "saturation_mean": 0.0,
                "saturation_std": 0.0,
                "dominant_ratio": 0.0,
                "dominant_color_rgb": [0.0, 0.0, 0.0],
                "second_ratio": 0.0,
                "non_ortho_ratio": 0.0,
                "bright_ratio": 0.0,
                "dark_ratio": 0.0,
                "long_lines": 0,
            },
            "suggestion": "图片无法读取，需检查路径或文件是否损坏",
        }

    h, w = image.shape[:2]
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # 基础指标
    mean_bgr = np.mean(image, axis=(0, 1))
    std_bgr = np.std(image, axis=(0, 1))
    min_val, max_val = np.min(gray), np.max(gray)
    contrast = float(max_val - min_val)

    # 亮度/饱和度
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    sat_mean = float(np.mean(sat))
    sat_std = float(np.std(sat))

    # 颜色分布与主色占比（缩略图级采样，避免全图高成本统计）
    sample = cv2.resize(
        rgb,
        (80, 80),
        interpolation=cv2.INTER_AREA,
    )
    quant = (sample // 16) * 16
    flat = quant.reshape(-1, 3)
    colors, counts = np.unique(flat, axis=0, return_counts=True)
    dominant_ratio = float(counts.max() / counts.sum())
    dominant_color = colors[counts.argmax()].tolist()
    second_ratio = float(np.sort(counts)[-2] / counts.sum()) if len(counts) > 1 else 0.0

    # 熵（视觉复杂度）
    hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
    prob = hist.ravel() / hist.sum()
    entropy = -float(np.sum(prob * np.log2(prob + 1e-12)))

    # 内容占比（与纯白背景差异）
    _, bg_mask = cv2.threshold(gray, 245, 255, cv2.THRESH_BINARY)
    inv = cv2.bitwise_not(bg_mask)
    content_ratio = safe_float(np.count_nonzero(inv) / (h * w))

    # 模糊检测（Laplacian 方差）
    blur = safe_float(cv2.Laplacian(gray, cv2.CV_64F).var())

    # 边缘密度
    edges = cv2.Canny(gray, 80, 200)
    edge_density = safe_float(np.count_nonzero(edges) / (h * w))

    # 亮暗比例
    bright_ratio = float(np.count_nonzero(gray > 245) / (h * w))
    dark_ratio = float(np.count_nonzero(gray < 20) / (h * w))

    # 文字与界面结构可见度（基于边缘密度变化）
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    dilated = cv2.dilate(edges, kernel, iterations=1)
    edge_stability = safe_float(np.count_nonzero(dilated) / (h * w))

    # 颜色偏移（例如过度偏红/过度偏蓝）
    channel_mean_delta = safe_float(np.ptp(mean_bgr))

    issues = []
    risk = "low"

    # 视觉一致性规则（保守：只标记高可疑）
    if content_ratio < 0.05 and entropy < 3.5:
        issues.append("画面内容极少且信息复杂度很低，可能为空白/未渲染页")
        risk = "high"

    if entropy < 2.8 and (edge_density < 0.012 or content_ratio < 0.05):
        issues.append("亮度熵过低且缺乏可见结构，疑似空白/未渲染页")
        risk = "high" if risk == "low" else risk

    if blur < 35:
        issues.append("模糊评分偏低，疑似未清晰渲染或截图模糊")
        risk = "high" if risk == "low" else risk

    if dominant_ratio > 0.93 and edge_density < 0.01 and content_ratio < 0.15:
        issues.append("单一主色占比过高且几乎无边缘内容，疑似背景覆盖/遮挡")
        risk = "high" if risk != "high" else risk

    if contrast < 30:
        issues.append("对比度过低，界面层次和可读性可能受损")
        if risk == "low":
            risk = "medium"

    if sat_mean < 15 and sat_std < 8 and edge_density < 0.02 and content_ratio < 0.5:
        issues.append("饱和度整体偏低且画面大面积趋平，可能存在色彩偏灰/过曝")
        if risk == "low":
            risk = "medium"

    if channel_mean_delta > 90:
        issues.append("RGB 通道差异过大，存在明显色彩偏色风险")
        if risk == "low":
            risk = "medium"

    if edge_density < 0.001:
        issues.append("边缘密度极低，组件边界不清晰或截图可能模糊")
        if risk == "low":
            risk = "medium"

    if bright_ratio > 0.97:
        issues.append("高亮像素占比过高，可能存在过曝/白屏感")

    if dark_ratio > 0.60:
        issues.append("暗色像素占比过高，界面可能整体过暗")
        if risk == "low":
            risk = "medium"

    status = "ok" if not issues else "review"
    if risk == "low" and len(issues) >= 3:
        risk = "medium"

    return {
        "path": path,
        "status": status,
        "risk": risk,
        "resolution": f"{w}x{h}",
        "metrics": {
            "entropy": safe_float(entropy),
            "contrast": contrast,
            "content_ratio": content_ratio,
            "blur": blur,
            "edge_density": edge_density,
            "mean_rgb": [safe_float(x) for x in mean_bgr[::-1]],
            "std_rgb": [safe_float(x) for x in std_bgr[::-1]],
            "saturation_mean": sat_mean,
            "saturation_std": sat_std,
            "dominant_ratio": dominant_ratio,
            "dominant_color_rgb": [safe_float(x) for x in dominant_color],
            "second_ratio": second_ratio,
            "edge_stability": edge_stability,
            "bright_ratio": bright_ratio,
            "dark_ratio": dark_ratio,
            "long_lines": 0,
        },
        "issues": issues,
        "suggestion": "建议人工逐图复核" if issues else "看起来健康",
    }


def infer_default_run(audit_root: str):
    runs = list_runs(audit_root)
    if not runs:
        raise RuntimeError(f"未找到审核目录: {audit_root}")
    return runs[0]


def ensure_thumbnail(source_path: str, output_root: str, relative_path: str, thumb_max_width: int):
    base_name = os.path.splitext(os.path.basename(relative_path))[0] + ".jpg"
    thumb_rel_path = os.path.join(os.path.dirname(relative_path), base_name)
    thumb_abs_path = os.path.join(output_root, thumb_rel_path)
    if os.path.exists(thumb_abs_path):
        return thumb_abs_path

    os.makedirs(os.path.dirname(thumb_abs_path), exist_ok=True)
    try:
        img = Image.open(source_path).convert("RGB")
        w, h = img.size
        target_w = max(320, min(thumb_max_width, w))
        target_h = int(h * (target_w / w))
        img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
        img.save(thumb_abs_path, format="JPEG", quality=85, optimize=True, progressive=True)
        return thumb_abs_path
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description="逐张截图视觉审核")
    parser.add_argument(
        "--audit-root",
        default=os.path.join("apps", "web", "screenshots", "codex-ui-audit"),
        help="根目录，如 apps/web/screenshots/codex-ui-audit",
    )
    parser.add_argument(
        "--run",
        default="",
        help="指定具体批次目录名，空则使用最新目录",
    )
    parser.add_argument(
        "--thumb-max-width",
        default=1280,
        type=int,
        help="缩略图最大宽度（像素）",
    )
    args = parser.parse_args()

    base = os.path.abspath(args.audit_root)
    run_dir = os.path.join(base, args.run) if args.run else infer_default_run(base)

    pages_dir = os.path.join(run_dir, "pages")
    if not os.path.isdir(pages_dir):
        raise RuntimeError(f"未找到截图页面目录: {pages_dir}")

    image_paths = []
    for root, _, files in os.walk(pages_dir):
        for file in files:
            if file.lower().endswith(".png"):
                image_paths.append(os.path.join(root, file))

    if not image_paths:
        raise RuntimeError("未找到 PNG 截图")

    output_dir = os.path.join(run_dir, "visual-review")
    thumbnail_dir = os.path.join(output_dir, "thumbnails")
    os.makedirs(thumbnail_dir, exist_ok=True)

    results = []
    counts = defaultdict(int)
    risk_counter = defaultdict(int)
    thumbnail_failures = 0

    total_images = len(image_paths)
    for i, p in enumerate(sorted(image_paths), 1):
        rel_path = os.path.relpath(p, run_dir)
        thumb = ensure_thumbnail(p, thumbnail_dir, rel_path, args.thumb_max_width)
        review_source = "缩略图" if thumb else "原图"
        if thumb is None:
            thumbnail_failures += 1
            reviewed_path = p
        else:
            reviewed_path = thumb

        r = analyze_image(reviewed_path)
        r["index"] = i
        r["relative_path"] = rel_path
        r["reviewed_path"] = os.path.relpath(reviewed_path, run_dir)
        r["review_source"] = review_source
        counts[r["status"]] += 1
        risk_counter[r["risk"]] += 1
        results.append(r)
        if i == 1 or i % 50 == 0 or i == total_images:
            print(f"AUDIT_PROGRESS {i}/{total_images} ({i/total_images:.1%})")

    report = {
        "run_dir": run_dir,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "image_count": len(results),
        "status_counts": dict(counts),
        "risk_counts": dict(risk_counter),
        "thumb_max_width": args.thumb_max_width,
        "thumbnail_dir": os.path.relpath(thumbnail_dir, run_dir),
        "thumbnail_failures": thumbnail_failures,
        "results": results,
    }

    json_path = os.path.join(output_dir, "visual-audit.json")
    md_path = os.path.join(output_dir, "visual-audit-report.md")
    thumbs_link = os.path.join(output_dir, "thumbnail-index.md")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    lines = [
        "# 截图视觉审核报告",
        "",
        f"- 生成时间: {report['generated_at']}",
        f"- 目录: {run_dir}",
        f"- 总截图数: {len(results)}",
        f"- 状态汇总: {dict(counts)}",
        f"- 风险汇总: {dict(risk_counter)}",
        f"- 缩略图上限宽度: {args.thumb_max_width}px",
        f"- 缩略图目录: {report['thumbnail_dir']}",
        f"- 缩略图生成失败数: {thumbnail_failures}",
        "",
        "## 逐张截图审核",
    ]

    for item in results:
        lines.extend(
            [
                f"{item['index']:03d}. `{item['relative_path']}`",
                f"   - 复核素材: `{item['reviewed_path']}`（{item['review_source']}）",
                f"   - 状态: {item['status']} | 风险: {item['risk']}",
                f"   - 分辨率: {item['resolution']} | 熵: {item['metrics']['entropy']:.2f} | 对比度: {item['metrics']['contrast']:.1f} | 清晰度: {item['metrics']['blur']:.1f} | 边缘密度: {item['metrics']['edge_density']:.4f}",
                f"   - 内容占比: {item['metrics']['content_ratio']:.3f} | 主色占比: {item['metrics']['dominant_ratio']:.3f} (RGB={item['metrics']['dominant_color_rgb']})",
            ]
        )

        if item["issues"]:
            for issue in item["issues"]:
                lines.append(f"   - ⚠️ {issue}")
        else:
            lines.append("   - ✅ 无明显可疑视觉问题")

        lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    index_lines = ["# 缩略图索引", ""]
    for item in results:
        index_lines.append(f"{item['index']:03d}. {item['relative_path']} -> {item['reviewed_path']}")

    with open(thumbs_link, "w", encoding="utf-8") as f:
        f.write("\n".join(index_lines))

    print(f"VISUAL_REVIEW_DONE={run_dir}")
    print(f"VISUAL_REVIEW_JSON={json_path}")
    print(f"VISUAL_REVIEW_MD={md_path}")
    print(f"VISUAL_REVIEW_THUMBS={thumbs_link}")


if __name__ == "__main__":
    main()
