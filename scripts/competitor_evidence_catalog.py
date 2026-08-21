from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
THUMB_SIZE = (320, 180)
LABEL_HEIGHT = 48
GAP = 12
TILES_PER_SHEET = 18


def _font(size: int) -> ImageFont.ImageFont:
    for candidate in (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _is_source_image(path: Path) -> bool:
    name = path.name.lower()
    return (
        path.is_file()
        and path.suffix.lower() in IMAGE_SUFFIXES
        and not name.startswith("contact-sheet")
        and not name.startswith("contact_sheet")
        and not name.startswith("_inspection")
    )


def _safe_thumb_name(path: Path) -> str:
    digest = hashlib.sha1(path.name.encode("utf-8")).hexdigest()[:8]
    return f"{path.stem[:72]}-{digest}.jpg"


def _write_sheet(
    product: str,
    rows: list[dict[str, object]],
    product_dir: Path,
    target: Path,
) -> None:
    columns = 3
    sheet_rows = max(1, (len(rows) + columns - 1) // columns)
    width = GAP + columns * (THUMB_SIZE[0] + GAP)
    height = 44 + GAP + sheet_rows * (THUMB_SIZE[1] + LABEL_HEIGHT + GAP)
    sheet = Image.new("RGB", (width, height), "#1f2625")
    draw = ImageDraw.Draw(sheet)
    draw.text((GAP, 8), f"{product} · public evidence thumbnails", fill="#fffdf8", font=_font(20))
    label_font = _font(14)
    meta_font = _font(11)
    for index, row in enumerate(rows):
        grid_row, column = divmod(index, columns)
        x = GAP + column * (THUMB_SIZE[0] + GAP)
        y = 44 + GAP + grid_row * (THUMB_SIZE[1] + LABEL_HEIGHT + GAP)
        thumb_path = product_dir / str(row["thumbnail"])
        with Image.open(thumb_path) as thumb:
            sheet.paste(thumb.convert("RGB"), (x, y))
        filename = str(row["file"])
        short_name = filename if len(filename) <= 42 else f"{filename[:39]}..."
        draw.text((x, y + THUMB_SIZE[1] + 3), short_name, fill="#fffdf8", font=label_font)
        draw.text(
            (x, y + THUMB_SIZE[1] + 24),
            f"{row['width']}x{row['height']} · {row['bytes']} bytes",
            fill="#b9c0bc",
            font=meta_font,
        )
    sheet.save(target, "JPEG", quality=68, optimize=True)


def _build_product(product_dir: Path) -> dict[str, object]:
    product = product_dir.name
    thumbs_dir = product_dir / "thumbs-evidence"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    source_images = sorted(path for path in product_dir.iterdir() if _is_source_image(path))
    records: list[dict[str, object]] = []
    for image_path in source_images:
        with Image.open(image_path) as source:
            rgb = source.convert("RGB")
            thumb = ImageOps.contain(rgb, THUMB_SIZE, Image.Resampling.LANCZOS)
            tile = Image.new("RGB", THUMB_SIZE, "#151b1a")
            tile.paste(thumb, ((THUMB_SIZE[0] - thumb.width) // 2, (THUMB_SIZE[1] - thumb.height) // 2))
            thumb_name = _safe_thumb_name(image_path)
            thumb_path = thumbs_dir / thumb_name
            tile.save(thumb_path, "JPEG", quality=66, optimize=True)
            records.append(
                {
                    "file": image_path.name,
                    "width": rgb.width,
                    "height": rgb.height,
                    "bytes": image_path.stat().st_size,
                    "thumbnail": thumb_path.relative_to(product_dir).as_posix(),
                }
            )

    sheet_names: list[str] = []
    for chunk_index, start in enumerate(range(0, len(records), TILES_PER_SHEET), start=1):
        target = product_dir / f"contact-sheet-evidence-{chunk_index:02d}.jpg"
        _write_sheet(product, records[start : start + TILES_PER_SHEET], product_dir, target)
        sheet_names.append(target.name)

    manifest = {
        "schema_version": "localdrama.competitor-evidence.v1",
        "product": product,
        "note": "This manifest inventories files only. UI authenticity and evidence grade are assigned in research notes, never inferred from filenames.",
        "image_count": len(records),
        "contact_sheets": sheet_names,
        "images": records,
    }
    (product_dir / "evidence-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _write_overview(root: Path, manifests: list[dict[str, object]]) -> str | None:
    rows: list[tuple[str, Path, int]] = []
    for manifest in manifests:
        sheets = manifest["contact_sheets"]
        if not sheets:
            continue
        product = str(manifest["product"])
        rows.append((product, root / product / str(sheets[0]), int(manifest["image_count"])))
    if not rows:
        return None

    columns = 3
    tile_size = (360, 230)
    label_height = 42
    sheet_rows = (len(rows) + columns - 1) // columns
    width = GAP + columns * (tile_size[0] + GAP)
    height = 52 + GAP + sheet_rows * (tile_size[1] + label_height + GAP)
    sheet = Image.new("RGB", (width, height), "#151b1a")
    draw = ImageDraw.Draw(sheet)
    draw.text((GAP, 10), "AI comic-drama tool public evidence overview", fill="#fffdf8", font=_font(22))
    for index, (product, source_path, image_count) in enumerate(rows):
        grid_row, column = divmod(index, columns)
        x = GAP + column * (tile_size[0] + GAP)
        y = 52 + GAP + grid_row * (tile_size[1] + label_height + GAP)
        with Image.open(source_path) as source:
            thumb = ImageOps.fit(source.convert("RGB"), tile_size, Image.Resampling.LANCZOS)
        sheet.paste(thumb, (x, y))
        draw.text((x, y + tile_size[1] + 4), product, fill="#fffdf8", font=_font(16))
        draw.text((x, y + tile_size[1] + 23), f"{image_count} source image(s)", fill="#b9c0bc", font=_font(12))
    target = root / "competitor-overview-contact-sheet.jpg"
    sheet.save(target, "JPEG", quality=70, optimize=True)
    return target.name


def build(root: Path) -> dict[str, object]:
    manifests = [_build_product(path) for path in sorted(root.iterdir()) if path.is_dir()]
    overview = _write_overview(root, manifests)
    catalog = {
        "schema_version": "localdrama.competitor-catalog.v1",
        "warning": "Inventory is not popularity or authenticity proof. See final research document and per-product source records.",
        "product_count": len(manifests),
        "source_image_count": sum(int(item["image_count"]) for item in manifests),
        "overview_contact_sheet": overview,
        "products": manifests,
    }
    (root / "evidence-index.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "product_count": catalog["product_count"],
        "source_image_count": catalog["source_image_count"],
        "overview_contact_sheet": overview,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build thumbnail contact sheets for competitor UI evidence.")
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.root.resolve()), ensure_ascii=False))


if __name__ == "__main__":
    main()
