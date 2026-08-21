from __future__ import annotations

from pathlib import Path

from tests.test_migration_0042 import _connect, _project, _season_episode_shot, _upgrade_to


def test_0046_to_0047_preserves_existing_shot_as_active_root(tmp_path: Path) -> None:
    path = tmp_path / "existing-0046.sqlite3"
    _upgrade_to("0046_director_recipes", path)
    with _connect(path) as connection:
        project_id = _project(connection, "shot_edit_upgrade")
        _, _, shot_id = _season_episode_shot(connection, project_id)
    _upgrade_to("0047_shot_editing", path)
    with _connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(shots)")}
        assert {"source_shot_id", "archived_at"} <= columns
        row = connection.execute(
            "SELECT source_shot_id,archived_at FROM shots WHERE id=?", (shot_id,),
        ).fetchone()
        assert row is not None
        assert row["source_shot_id"] is None
        assert row["archived_at"] is None
