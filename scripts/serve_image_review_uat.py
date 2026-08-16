"""Serve an isolated snapshot seeded with real IMAGE review candidates.

Creates a fresh SQLite database and project tree, imports two real local PNG
files as IMAGE PROXY candidates owned by a shot, then serves the real FastAPI
app on a loopback port so a read-only three-viewport browser UAT can exercise
the image review grid/compare/selection surfaces with real data.  The
production database and project tree are never touched.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.media import MediaService  # type: ignore[import-not-found]
from local_drama.application.projects import (
    ProjectService,  # type: ignore[import-not-found]
)
from local_drama.config import Settings  # type: ignore[import-not-found]
from local_drama.infrastructure.database.sqlite import (
    Database,  # type: ignore[import-not-found]
)
from local_drama.main import create_app  # type: ignore[import-not-found]

from scripts.migrate import migrate


def build_settings(root: Path, port: int) -> Settings:
    return Settings(
        data_root=root / "data",
        projects_root=root / "projects",
        work_root=root / "work",
        cache_root=root / "cache",
        logs_root=root / "logs",
        backups_root=root / "backups",
        comfy_output_root=root / "work" / "comfy-production" / "output",
        comfy_input_root=root / "work" / "comfy-production" / "input",
        port=port,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True, help="isolated writable root")
    parser.add_argument("--port", type=int, default=3223)
    parser.add_argument("--images", nargs="+", type=Path, required=True, help="real PNG files to import as IMAGE PROXY candidates")
    args = parser.parse_args()

    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    settings = build_settings(root, args.port)
    settings.ensure_roots()
    migrate(settings.database_path)
    database = Database(settings.database_path)

    project = ProjectService(database, settings.projects_root).create_project(
        code="image_review_uat",
        title="Image review three-viewport UAT",
        episode_count=1,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=5_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    seasons = ProjectService(database, settings.projects_root).list_seasons(project_id)
    episode = ProjectService(database, settings.projects_root).list_episodes(str(seasons[0]["id"]))[0]
    shot_id = str(ProjectService(database, settings.projects_root).create_shot(str(episode["id"]), "SHOT_001", 5_000)["id"])

    media = MediaService(database, settings)
    imported: list[dict[str, str]] = []
    for source in args.images:
        resolved = source.resolve()
        if not resolved.is_file() or resolved.is_symlink():
            raise RuntimeError(f"IMAGE_UAT_SOURCE_INVALID: {resolved}")
        result = media.import_file(
            project_id,
            resolved,
            purpose="SHOT_REFERENCE",
            owner_type="SHOT",
            owner_id=shot_id,
            media_kind="IMAGE",
            stage="PROXY",
        )
        imported.append(
            {"media_version_id": str(result["media_version_id"]), "sha256": str(result["sha256"]), "stage": str(result.get("stage", "PROXY"))}
        )

    with database.connect() as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    print(
        f"image review UAT snapshot ready project={project_id} shot={shot_id} "
        f"port={args.port} imported={len(imported)} integrity={integrity}",
        flush=True,
    )
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
