from __future__ import annotations

import hashlib
import html
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class ContactSheetExportService:
    """Build a self-contained, read-only-to-SQLite episode selection export."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def _selected_items(self, episode_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        with self.database.connect() as connection:
            episode = connection.execute(
                """SELECT e.id, e.code, e.title, p.id AS project_id, p.code AS project_code, p.root_rel
                FROM episodes e JOIN seasons se ON se.id=e.season_id JOIN projects p ON p.id=se.project_id
                WHERE e.id=?""",
                (episode_id,),
            ).fetchone()
            if episode is None:
                raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
            rows = connection.execute(
                """SELECT sh.id AS shot_id, sh.code AS shot_code, sh.order_key, ma.id AS media_asset_id,
                ma.purpose, ma.media_kind, sl.selection_type, mv.id AS media_version_id, mv.version_no,
                mv.rel_path, mv.mime_type, mv.byte_size, mv.sha256
                FROM shots sh
                JOIN media_assets ma ON ma.project_id=? AND ma.owner_type='SHOT' AND ma.owner_id=sh.id
                JOIN media_versions mv ON mv.id=ma.selected_version_id
                JOIN selections sl ON sl.id=(
                    SELECT sl2.id FROM selections sl2
                    WHERE sl2.media_asset_id=ma.id AND sl2.media_version_id=ma.selected_version_id
                    ORDER BY sl2.created_at DESC, sl2.id DESC LIMIT 1
                )
                WHERE sh.episode_id=? AND ma.media_kind IN ('IMAGE','VIDEO')
                ORDER BY CAST(sh.order_key AS REAL), sh.code, ma.purpose, sl.selection_type""",
                (episode["project_id"], episode_id),
            ).fetchall()
        return dict(episode), [dict(row) for row in rows]

    def _thumbnail(self, source: Path, destination: Path, media_kind: str) -> None:
        ffmpeg = self.settings.ffmpeg_path
        if not ffmpeg or not Path(ffmpeg).is_file():
            raise DomainRuleError("FFMPEG_UNAVAILABLE", "本机 FFmpeg 不可用，无法生成联系表缩略图")
        seek = ["-ss", "0"] if media_kind == "VIDEO" else []
        result = subprocess.run(
            [ffmpeg, *seek, "-i", str(source), "-frames:v", "1", "-vf", "scale=320:-2", "-c:v", "libwebp", "-quality", "55", "-y", str(destination)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0 or not destination.is_file():
            raise DomainRuleError("CONTACT_SHEET_THUMBNAIL_FAILED", "联系表缩略图生成失败", {"stderr_redacted": result.stderr[-300:]})

    @staticmethod
    def _verify_existing(final: Path, export_hash: str) -> int:
        try:
            manifest = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
            if manifest.get("export_hash") != export_hash or manifest.get("schema_version") != "localdrama.contact-sheet.v1":
                raise ValueError("manifest identity mismatch")
            items = manifest["items"]
            if not isinstance(items, list) or not items:
                raise ValueError("manifest has no items")
            for item in items:
                original = (final / item["original_rel_path"]).resolve()
                thumbnail = (final / item["thumbnail_rel_path"]).resolve()
                if not original.is_relative_to(final) or not thumbnail.is_relative_to(final):
                    raise ValueError("manifest path escapes export")
                if (
                    not original.is_file()
                    or original.is_symlink()
                    or original.stat().st_size != int(item["byte_size"])
                    or _sha256(original) != item["sha256"]
                    or not thumbnail.is_file()
                    or thumbnail.is_symlink()
                    or thumbnail.stat().st_size != int(item["thumbnail_byte_size"])
                    or _sha256(thumbnail) != item["thumbnail_sha256"]
                ):
                    raise ValueError("exported file integrity mismatch")
            if not (final / "contact-sheet.html").is_file():
                raise ValueError("contact sheet document missing")
            return len(items)
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise DomainRuleError(
                "CONTACT_SHEET_EXPORT_TAMPERED",
                "已有联系表导出不完整或已被修改，请保留现场并人工移走该目录后重试",
                {"export_hash": export_hash},
            ) from error

    def export_episode(self, episode_id: str) -> dict[str, Any]:
        episode, items = self._selected_items(episode_id)
        if not items:
            raise DomainRuleError("CONTACT_SHEET_SELECTION_REQUIRED", "当前集没有已选择的图片或视频版本")
        project_root = (self.settings.projects_root / str(episode["root_rel"])).resolve()
        if not project_root.is_relative_to(self.settings.projects_root.resolve()) or not project_root.is_dir() or project_root.is_symlink():
            raise DomainRuleError("PROJECT_ROOT_INVALID", "项目根目录无效")
        identity = [{key: item[key] for key in ("shot_id", "media_version_id", "selection_type", "sha256", "byte_size")} for item in items]
        export_hash = hashlib.sha256(_canonical({"schema_version": "localdrama.contact-sheet.v1", "episode_id": episode_id, "items": identity})).hexdigest()
        base = project_root / "01_story" / "episodes" / str(episode["code"]) / "exports"
        final = base / f"contact-sheet-{export_hash[:12]}"
        if final.exists():
            if not final.is_dir() or final.is_symlink():
                raise DomainRuleError("CONTACT_SHEET_EXPORT_TAMPERED", "联系表导出目标不是安全目录")
            verified_count = self._verify_existing(final, export_hash)
            return self._result(project_root, final, export_hash, verified_count, reused=True)
        partial = base / f".contact-sheet.partial-{uuid.uuid4().hex}"
        try:
            originals = partial / "originals"
            thumbnails = partial / "thumbnails"
            originals.mkdir(parents=True)
            thumbnails.mkdir(parents=True)
            manifest_items: list[dict[str, Any]] = []
            cards: list[str] = []
            for index, item in enumerate(items, start=1):
                source = (project_root / str(item["rel_path"])).resolve()
                if not source.is_relative_to(project_root) or source.is_symlink() or not source.is_file():
                    raise DomainRuleError("MEDIA_PATH_INVALID", "已选媒体路径无效", {"media_version_id": item["media_version_id"]})
                if source.stat().st_size != int(item["byte_size"]) or _sha256(source) != str(item["sha256"]):
                    raise DomainRuleError("SOURCE_INTEGRITY_FAILED", "已选媒体完整性校验失败", {"media_version_id": item["media_version_id"]})
                stem = f"{index:03d}-{item['shot_code']}-{str(item['media_version_id'])[:8]}"
                original = originals / f"{stem}{source.suffix.lower()}"
                shutil.copyfile(source, original)
                if _sha256(original) != str(item["sha256"]):
                    raise DomainRuleError("CONTACT_SHEET_COPY_MISMATCH", "导出原文件 hash 校验失败")
                thumbnail = thumbnails / f"{stem}.webp"
                self._thumbnail(source, thumbnail, str(item["media_kind"]))
                manifest_item = {
                    **identity[index - 1],
                    "shot_code": item["shot_code"],
                    "purpose": item["purpose"],
                    "media_kind": item["media_kind"],
                    "mime_type": item["mime_type"],
                    "original_rel_path": original.relative_to(partial).as_posix(),
                    "thumbnail_rel_path": thumbnail.relative_to(partial).as_posix(),
                    "thumbnail_sha256": _sha256(thumbnail),
                    "thumbnail_byte_size": thumbnail.stat().st_size,
                }
                manifest_items.append(manifest_item)
                cards.append(f'<article><img src="{html.escape(manifest_item["thumbnail_rel_path"])}" alt="{html.escape(str(item["shot_code"]))} 已选媒体缩略图"><strong>{html.escape(str(item["shot_code"]))}</strong><span>{html.escape(str(item["selection_type"]))} · {html.escape(str(item["purpose"]))}</span><code>{html.escape(str(item["sha256"])[:16])}…</code></article>')
            manifest = {"schema_version": "localdrama.contact-sheet.v1", "project_id": episode["project_id"], "episode_id": episode_id, "episode_code": episode["code"], "export_hash": export_hash, "database_mutated": False, "items": manifest_items}
            (partial / "manifest.json").write_bytes(_canonical(manifest) + b"\n")
            document = f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{html.escape(str(episode["title"]))} 联系表</title><style>body{{font:14px system-ui;margin:24px;background:#f4f1eb;color:#20242b}}main{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}}article{{display:grid;gap:8px;padding:12px;background:#fffdf8;border:1px solid #d8d2c7;border-radius:10px}}img{{width:100%;height:auto;max-width:320px;aspect-ratio:16/9;object-fit:contain;background:#111715}}span,code{{font-size:12px;color:#666b66}}</style><h1>{html.escape(str(episode["title"]))} · 已选媒体联系表</h1><p>本地只读导出 · {len(items)} 项 · manifest {export_hash[:16]}…</p><main>{"".join(cards)}</main></html>'
            (partial / "contact-sheet.html").write_text(document, encoding="utf-8")
            os.replace(partial, final)
        except Exception:
            shutil.rmtree(partial, ignore_errors=True)
            raise
        return self._result(project_root, final, export_hash, len(items), reused=False)

    @staticmethod
    def _result(project_root: Path, final: Path, export_hash: str, item_count: int, *, reused: bool) -> dict[str, Any]:
        return {"schema_version": "localdrama.contact-sheet.v1", "status": "EXPORTED", "rel_path": final.relative_to(project_root).as_posix(), "manifest_rel_path": (final / "manifest.json").relative_to(project_root).as_posix(), "contact_sheet_rel_path": (final / "contact-sheet.html").relative_to(project_root).as_posix(), "export_hash": export_hash, "item_count": item_count, "reused": reused, "database_mutated": False, "runtime_contacted": False, "network_contacted": False}
