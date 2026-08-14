"""Serve a real, isolated local G9 canvas fixture for browser UAT.

The fixture is deliberately separate from production ``data/local_drama.sqlite3``.
It creates one real project with one episode and 62 real shot rows, so the browser
benchmark exercises the actual API, React app, and React Flow renderer without
creating jobs or contacting ComfyUI.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.projects import ProjectService
from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database
from local_drama.main import create_app

from scripts.migrate import migrate


def build_settings(root: Path) -> Settings:
    return Settings(
        data_root=root / "data",
        projects_root=root / "projects",
        work_root=root / "work",
        cache_root=root / "cache",
        logs_root=root / "logs",
        backups_root=root / "backups",
        port=3221,
    )


def ensure_fixture(settings: Settings) -> tuple[str, str]:
    settings.ensure_roots()
    database_path = settings.database_path
    if not database_path.exists():
        migrate(database_path)
    database = Database(database_path)
    service = ProjectService(database, settings.projects_root)
    projects = service.list_projects(limit=10)
    if not projects:
        project = service.create_project(
            code="g9_browser_fixture",
            title="G9 browser fixture",
            episode_count=1,
            aspect_ratio="16:9",
            fps_num=24,
            fps_den=1,
            target_duration_ms=62_000,
            allow_unconfigured_capabilities=True,
        )
    else:
        project = projects[0]
    seasons = service.list_seasons(str(project["id"]))
    episodes = service.list_episodes(str(seasons[0]["id"]))
    episode = episodes[0]
    shots = service.list_shots(str(episode["id"]))
    for number in range(len(shots) + 1, 63):
        service.create_shot(str(episode["id"]), f"S{number:03d}", 1_000)
    return str(project["id"]), str(episode["id"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=3221)
    args = parser.parse_args()
    settings = build_settings(args.root.resolve())
    settings.port = args.port
    project_id, episode_id = ensure_fixture(settings)
    print(f"g9 fixture ready project={project_id} episode={episode_id} port={args.port}", flush=True)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
