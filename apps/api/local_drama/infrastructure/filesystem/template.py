from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from local_drama.infrastructure.filesystem.atomic import replace_path

TEMPLATE_VERSION = "project-template.v2.0"
TEMPLATE_DIRECTORIES = (
    "00_admin/decisions",
    "00_admin/licenses",
    "00_admin/imports",
    "00_admin/reports",
    "01_story/source_documents",
    "01_story/series_bible",
    "01_story/seasons/SEASON_001",
    "01_story/scenes",
    "01_story/episodes/EPISODE_001/script",
    "01_story/episodes/EPISODE_001/storyboard",
    "01_story/episodes/EPISODE_001/exports",
    "02_assets/characters",
    "02_assets/locations",
    "02_assets/props",
    "02_assets/costumes",
    "02_assets/styles",
    "02_assets/voices",
    "03_prompts/templates",
    "03_prompts/dictionaries",
    "03_prompts/frozen/SEASON_001/EPISODE_001",
    "04_media/images/assets",
    "04_media/images/episodes/SEASON_001/EPISODE_001/SHOT_001",
    "04_media/images/frame_anchors",
    "04_media/images/masks",
    "04_media/videos/episodes/SEASON_001/EPISODE_001/SHOT_001",
    "04_media/controls",
    "04_media/audio/dialogue/SEASON_001/EPISODE_001/SHOT_001",
    "04_media/audio/ambience",
    "04_media/audio/sfx",
    "04_media/audio/music",
    "04_media/subtitles/SEASON_001/EPISODE_001",
    "04_media/documents",
    "05_timelines/SEASON_001/EPISODE_001/revisions",
    "05_timelines/SEASON_001/EPISODE_001/renders",
    "06_delivery/SEASON_001/EPISODE_001",
    "07_workflows/snapshots",
    "07_workflows/compatibility",
    "99_legacy",
)


def build_project_tree(projects_root: Path, project_id: str, code: str, title: str, episode_count: int,
                       *, season_count: int = 1) -> tuple[Path, Path]:
    final_root = projects_root / code
    if final_root.exists():
        raise FileExistsError(f"project root already exists: {code}")
    temporary_root = projects_root / f".{code}.partial-{uuid.uuid4().hex}"
    try:
        for relative in TEMPLATE_DIRECTORIES:
            (temporary_root / relative).mkdir(parents=True, exist_ok=True)
        total_episodes = episode_count * season_count
        for season_number in range(1, season_count + 1):
            season_code = f"SEASON_{season_number:03d}"
            (temporary_root / "01_story" / "seasons" / season_code).mkdir(parents=True, exist_ok=True)
            for episode_number in range(1, episode_count + 1):
                global_episode_number = (season_number - 1) * episode_count + episode_number
                episode_code = f"EPISODE_{global_episode_number:03d}"
                if season_number == 1 and episode_number == 1:
                    continue
                for relative in (
                    f"01_story/episodes/{episode_code}/script",
                    f"01_story/episodes/{episode_code}/storyboard",
                    f"01_story/episodes/{episode_code}/exports",
                    f"03_prompts/frozen/{season_code}/{episode_code}",
                    f"04_media/images/episodes/{season_code}/{episode_code}",
                    f"04_media/videos/episodes/{season_code}/{episode_code}",
                    f"04_media/audio/dialogue/{season_code}/{episode_code}",
                    f"04_media/subtitles/{season_code}/{episode_code}",
                    f"05_timelines/{season_code}/{episode_code}/revisions",
                    f"05_timelines/{season_code}/{episode_code}/renders",
                    f"06_delivery/{season_code}/{episode_code}",
                ):
                    (temporary_root / relative).mkdir(parents=True, exist_ok=True)
        project_json = {
            "schema_version": "localdrama.project.v2",
            "project_id": project_id,
            "project_code": code,
            "title": title,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "template_version": TEMPLATE_VERSION,
            "initial_season": "SEASON_001",
            "season_count": season_count,
            "episode_count_per_season": episode_count,
            "total_episode_count": total_episodes,
            "legacy_refs": [],
        }
        (temporary_root / "project.json").write_text(json.dumps(project_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        replace_path(temporary_root, final_root)
        return final_root, final_root / "project.json"
    except Exception:
        if temporary_root.exists():
            shutil.rmtree(temporary_root, ignore_errors=True)
        raise
