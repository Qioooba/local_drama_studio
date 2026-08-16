"""Run a real local H3 artifact through the isolated formal media pipeline.

This script intentionally uses a temporary SQLite/project root.  It never
touches the repository production database or the user's production project.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.reviews import ReviewService
from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database

from scripts.migrate import migrate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _checks(template: dict[str, Any]) -> list[dict[str, str]]:
    return [{"item_id": str(item["id"]), "result": "PASS"} for item in template["items"]]


def run(artifact: Path) -> dict[str, Any]:
    source = artifact.resolve(strict=True)
    if not source.is_file() or source.is_symlink():
        raise ValueError("artifact must be a regular local file")
    # Alembic's SQLite/WAL handle can remain locked briefly on Windows after
    # the isolated run.  Ignore only cleanup failure; the UAT itself still
    # completes and the temporary root is never a production path.
    with tempfile.TemporaryDirectory(prefix="h3-formal-pipeline-", ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        settings = Settings(
            data_root=root / "data",
            projects_root=root / "projects",
            work_root=root / "work",
            cache_root=root / "cache",
            logs_root=root / "logs",
            backups_root=root / "backups",
        )
        settings.ensure_roots()
        migrate(settings.database_path)
        database = Database(settings.database_path)
        project = ProjectService(database, settings.projects_root).create_project(
            code="h3_formal_pipeline_uat",
            title="H3 formal pipeline isolated UAT",
            episode_count=1,
            aspect_ratio="9:16",
            fps_num=24,
            fps_den=1,
            target_duration_ms=5_167,
            allow_unconfigured_capabilities=True,
        )
        project_id = str(project["id"])
        media = MediaService(database, settings).import_file(
            project_id,
            source,
            purpose="SHOT_VIDEO",
            media_kind="VIDEO",
            stage="FORMAL",
        )
        media_id = str(media["media_version_id"])
        reviews = ReviewService(database, settings)
        reviews.ensure_templates()
        formal_template = next(item for item in reviews.templates() if item["code"] == "formal_video")
        machine = reviews.machine_check(media_id)
        selected = reviews.select_version(media_id, "FORMAL_SELECTION")
        approved = reviews.submit_review(
            media_id,
            str(formal_template["id"]),
            "APPROVED",
            expected_subject_revision=2,
            checks=_checks(formal_template),
        )
        plan = reviews.formal_selection_preflight(project_id, [media_id])
        committed = reviews.commit_formal_selection(project_id, [media_id], str(plan["plan_hash"]))
        with database.connect() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            project_root = connection.execute("SELECT root_rel FROM projects WHERE id=?", (project_id,)).fetchone()[0]
        return {
            "schema_version": "g10.h3_formal_pipeline_uat.v1",
            "status": "PARTIAL",
            "source": {
                "path": str(source),
                "sha256": _sha256(source),
                "bytes": source.stat().st_size,
            },
            "isolated": {
                "project_id": project_id,
                "project_root_rel": str(project_root),
                "database_integrity": integrity,
                "production_database_contacted": False,
                "production_project_tree_mutated": False,
                "runtime_contacted": False,
                "network_contacted": False,
            },
            "checks": {
                "media_registered": media_id,
                "formal_machine_qc": machine,
                "formal_selection": selected,
                "human_review": {"decision": approved["decision"], "template_code": formal_template["code"]},
                "formal_selection_preflight": plan,
                "formal_selection_commit": committed,
            },
            "limitations": [
                "这是隔离 SQLite/项目根中的平台接入验收，不写入生产数据库。",
                "未执行正式交付包、下载审计或多镜头整集门禁，因此整体仍为 PARTIAL。",
            ],
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = run(args.artifact)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
