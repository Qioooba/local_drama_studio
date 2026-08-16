"""Serve an isolated snapshot for the core browser read-only UAT.

The production SQLite file is copied with SQLite's online backup API and the
selected project's files are copied into a temporary root.  The browser then
uses the real FastAPI routes and the real React application, while an
accidental mutation cannot reach the production database or project tree.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.config import Settings
from local_drama.main import create_app


def _backup_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    source_uri = f"file:{source.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source_connection, sqlite3.connect(target) as target_connection:
        source_connection.backup(target_connection)


def _copy_selected_project(source_db: Path, source_projects: Path, target_projects: Path) -> tuple[str, str, str]:
    with sqlite3.connect(f"file:{source_db.resolve().as_posix()}?mode=ro", uri=True) as connection:
        row = connection.execute(
            "SELECT id, root_rel FROM projects WHERE code='g2_smoke2' ORDER BY updated_at DESC LIMIT 1",
        ).fetchone()
        if row is None:
            row = connection.execute("SELECT id, root_rel FROM projects ORDER BY updated_at DESC LIMIT 1").fetchone()
        if row is None:
            raise RuntimeError("CORE_UAT_PROJECT_MISSING: production snapshot has no project")
        project_id, root_rel = str(row[0]), str(row[1])
        episode = connection.execute(
            "SELECT e.id FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE s.project_id=? ORDER BY e.number, e.id LIMIT 1",
            (project_id,),
        ).fetchone()
        if episode is None:
            raise RuntimeError(f"CORE_UAT_EPISODE_MISSING: project {project_id} has no episode")
        episode_id = str(episode[0])

    source_project = (source_projects / root_rel).resolve()
    if not source_project.is_dir():
        raise RuntimeError(f"CORE_UAT_PROJECT_ROOT_MISSING: {source_project}")
    target_project = (target_projects / root_rel).resolve()
    target_project.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_project, target_project, dirs_exist_ok=True)
    return project_id, episode_id, root_rel


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
    parser.add_argument("--source-db", type=Path, default=ROOT / "data" / "local_drama.sqlite3")
    parser.add_argument("--source-projects", type=Path, default=ROOT / "projects")
    parser.add_argument("--port", type=int, default=3222)
    args = parser.parse_args()

    root = args.root.resolve()
    source_db = args.source_db.resolve()
    source_projects = args.source_projects.resolve()
    if not source_db.is_file():
        raise RuntimeError(f"CORE_UAT_SOURCE_DB_MISSING: {source_db}")
    root.mkdir(parents=True, exist_ok=True)
    settings = build_settings(root, args.port)
    settings.ensure_roots()
    _backup_database(source_db, settings.database_path)
    project_id, episode_id, root_rel = _copy_selected_project(source_db, source_projects, settings.projects_root)
    print(
        f"core browser UAT snapshot ready project={project_id} episode={episode_id} "
        f"root_rel={root_rel} port={args.port} source_db={source_db}",
        flush=True,
    )
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
