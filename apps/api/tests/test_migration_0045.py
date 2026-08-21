"""Migration chain coverage for additive shot groups and QC policies."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from scripts.migrate import migrate

from tests.test_migration_0042 import _connect, _project, _season_episode_shot, _upgrade_to


def test_empty_database_reaches_0045_with_0044_and_qc_constraints(tmp_path: Path) -> None:
    path = tmp_path / "head.sqlite3"
    migrate(path)
    import sqlite3

    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "apps" / "api" / "alembic"))
    expected_head = ScriptDirectory.from_config(config).get_current_head()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == expected_head
        shot_columns = {row[1] for row in connection.execute("PRAGMA table_info(shots)")}
        assert "scene_id" in shot_columns
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        assert {
            "shot_groups", "shot_group_members", "generation_qc_policy_sets",
            "generation_qc_policy_versions", "variant_qc_links",
        } <= tables
        indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='index'")}
        assert {
            "ix_shots_scene_order", "ix_shot_groups_episode_order",
            "ix_generation_qc_policy_resolution", "ix_variant_qc_links_variant_created",
        } <= indexes


def test_0043_to_0044_does_not_guess_existing_shot_scene(tmp_path: Path) -> None:
    path = tmp_path / "existing-0043.sqlite3"
    _upgrade_to("0043_generation_preferences", path)
    with _connect(path) as connection:
        project_id = _project(connection, "shot_group_upgrade")
        _, _, shot_id = _season_episode_shot(connection, project_id)
    _upgrade_to("0044_shot_groups", path)
    with _connect(path) as connection:
        row = connection.execute("SELECT scene_id FROM shots WHERE id=?", (shot_id,)).fetchone()
        assert row is not None and row["scene_id"] is None
