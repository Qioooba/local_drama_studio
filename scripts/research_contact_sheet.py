from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

DEFAULT_SEGMENT_HEIGHT = 720
THUMB_SIZE = (320, 180)
LABEL_HEIGHT = 34
GAP = 12


def _font(size: int) -> ImageFont.ImageFont:
    candidates = (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    )
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def build(
    source_dir: Path,
    *,
    pattern: str = "*-full.png",
    viewport_width: int = 1280,
    viewport_height: int = 720,
    segment_height: int = DEFAULT_SEGMENT_HEIGHT,
) -> dict[str, object]:
    segment_dir = source_dir / "segments"
    thumb_dir = source_dir / "thumbs"
    segment_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir.mkdir(parents=True, exist_ok=True)

    manifest: list[dict[str, object]] = []
    for image_path in sorted(source_dir.glob(pattern)):
        with Image.open(image_path) as source:
            rgb = source.convert("RGB")
            page_width, page_height = rgb.size
            segment_count = max(1, (page_height + segment_height - 1) // segment_height)
            page_record: dict[str, object] = {
                "page": image_path.stem.removesuffix("-full"),
                "source": image_path.name,
                "width": page_width,
                "height": page_height,
                "segments": [],
            }
            for index in range(segment_count):
                top = index * segment_height
                bottom = min(page_height, top + segment_height)
                crop = rgb.crop((0, top, page_width, bottom))
                if crop.height < segment_height:
                    padded = Image.new("RGB", (page_width, segment_height), "#f4f1eb")
                    padded.paste(crop, (0, 0))
                    crop = padded

                segment_name = f"{page_record['page']}-seg-{index + 1:02d}.jpg"
                segment_path = segment_dir / segment_name
                crop.save(segment_path, "JPEG", quality=84, optimize=True)

                thumb = ImageOps.contain(crop, THUMB_SIZE, Image.Resampling.LANCZOS)
                tile = Image.new("RGB", THUMB_SIZE, "#151b1a")
                tile.paste(thumb, ((THUMB_SIZE[0] - thumb.width) // 2, (THUMB_SIZE[1] - thumb.height) // 2))
                thumb_path = thumb_dir / segment_name
                tile.save(thumb_path, "JPEG", quality=64, optimize=True)
                page_record["segments"].append(
                    {
                        "index": index + 1,
                        "y_start": top,
                        "y_end": bottom,
                        "segment": segment_path.relative_to(source_dir).as_posix(),
                        "thumbnail": thumb_path.relative_to(source_dir).as_posix(),
                    }
                )
            manifest.append(page_record)

    tiles = [
        (record, segment)
        for record in manifest
        for segment in record["segments"]  # type: ignore[index]
    ]
    label_font = _font(16)
    meta_font = _font(12)

    def write_sheet(sheet_tiles: list[tuple[dict[str, object], dict[str, object]]], target: Path) -> None:
        columns = 3
        rows = max(1, (len(sheet_tiles) + columns - 1) // columns)
        sheet_width = GAP + columns * (THUMB_SIZE[0] + GAP)
        sheet_height = GAP + rows * (THUMB_SIZE[1] + LABEL_HEIGHT + GAP)
        sheet = Image.new("RGB", (sheet_width, sheet_height), "#1f2625")
        draw = ImageDraw.Draw(sheet)
        for tile_index, (record, segment) in enumerate(sheet_tiles):
            row, column = divmod(tile_index, columns)
            x = GAP + column * (THUMB_SIZE[0] + GAP)
            y = GAP + row * (THUMB_SIZE[1] + LABEL_HEIGHT + GAP)
            with Image.open(source_dir / str(segment["thumbnail"])) as thumb:
                sheet.paste(thumb.convert("RGB"), (x, y))
            label = f"{record['page']} · {segment['index']}/{len(record['segments'])}"  # type: ignore[arg-type]
            draw.text((x, y + THUMB_SIZE[1] + 3), label, fill="#fffdf8", font=label_font)
            draw.text(
                (x, y + THUMB_SIZE[1] + 20),
                f"y={segment['y_start']}–{segment['y_end']}  {record['width']}×{record['height']}",
                fill="#b9c0bc",
                font=meta_font,
            )
        sheet.save(target, "JPEG", quality=66, optimize=True)

    contact_sheet_path = source_dir / "contact-sheet.jpg"
    write_sheet(tiles, contact_sheet_path)
    chunk_paths: list[str] = []
    for chunk_index, start in enumerate(range(0, len(tiles), 18), start=1):
        chunk_path = source_dir / f"contact-sheet-{chunk_index:02d}.jpg"
        write_sheet(tiles[start : start + 18], chunk_path)
        chunk_paths.append(chunk_path.name)
    manifest_path = source_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "localdrama.ui-evidence.v1",
                "viewport": {"width": viewport_width, "height": viewport_height},
                "page_count": len(manifest),
                "segment_count": len(tiles),
                "contact_sheet_chunks": chunk_paths,
                "pages": manifest,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "page_count": len(manifest),
        "segment_count": len(tiles),
        "contact_sheet": str(contact_sheet_path),
        "contact_sheet_chunks": chunk_paths,
        "manifest": str(manifest_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build segmented UI evidence thumbnails and a contact sheet.")
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--pattern", default="*-full.png", help="Source image glob within source_dir.")
    parser.add_argument("--viewport-width", type=int, default=1280)
    parser.add_argument("--viewport-height", type=int, default=720)
    parser.add_argument("--segment-height", type=int, default=DEFAULT_SEGMENT_HEIGHT)
    args = parser.parse_args()
    print(
        json.dumps(
            build(
                args.source_dir.resolve(),
                pattern=args.pattern,
                viewport_width=args.viewport_width,
                viewport_height=args.viewport_height,
                segment_height=args.segment_height,
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
