from __future__ import annotations

import pytest

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError


def _project(database, workspace) -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code="security_boundary",
        title="Security boundary",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )


@pytest.mark.parametrize("root_rel", ["../outside", "../../outside"])
def test_media_project_root_rejects_traversal_before_resolution(database, workspace, root_rel: str) -> None:
    project = _project(database, workspace)
    with database.transaction() as connection:
        connection.execute("UPDATE projects SET root_rel=? WHERE id=?", (root_rel, project["id"]))

    with pytest.raises(DomainRuleError, match="相对路径") as error:
        MediaService(database, workspace)._project_root(str(project["id"]))
    assert error.value.code == "PATH_ESCAPE"


def test_media_project_root_rejects_absolute_path_even_when_inside_projects_root(database, workspace) -> None:
    project = _project(database, workspace)
    absolute_root = str((workspace.projects_root / str(project["root_rel"])).resolve())
    with database.transaction() as connection:
        connection.execute("UPDATE projects SET root_rel=? WHERE id=?", (absolute_root, project["id"]))

    with pytest.raises(DomainRuleError, match="相对路径") as error:
        MediaService(database, workspace)._project_root(str(project["id"]))
    assert error.value.code == "PATH_ESCAPE"


def test_media_project_root_rejects_symlink_root_inside_projects_root(database, workspace) -> None:
    project = _project(database, workspace)
    target = workspace.projects_root / "security-target"
    target.mkdir()
    alias = workspace.projects_root / "security-alias"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this Windows test host")
    with database.transaction() as connection:
        connection.execute("UPDATE projects SET root_rel=? WHERE id=?", (alias.name, project["id"]))

    with pytest.raises(DomainRuleError, match="symlink") as error:
        MediaService(database, workspace)._project_root(str(project["id"]))
    assert error.value.code == "PATH_ESCAPE"
