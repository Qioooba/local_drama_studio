"""Regression tests for BKT-01: declared dependencies must exist in both lock files.

These assert the CORRECT behaviour (every runtime dependency in
``apps/api/pyproject.toml`` is pinned in both lock files and satisfies the
declared range) rather than re-demonstrating the original "pypdf is missing"
bug.
"""

from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts"
PYPROJECT = REPO_ROOT / "apps" / "api" / "pyproject.toml"
RUNTIME_LOCK = REPO_ROOT / "apps" / "api" / "requirements-runtime.lock"
DEV_LOCK = REPO_ROOT / "apps" / "api" / "requirements.lock"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load(name: str, filename: str):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dependency_locks = _load("check_dependency_locks", "check_dependency_locks.py")


def test_declared_dependencies_are_locked_in_both_files() -> None:
    """The real gate: pyproject, runtime lock and dev lock must agree."""

    findings = dependency_locks.check_locks(PYPROJECT, (RUNTIME_LOCK, DEV_LOCK))
    assert findings == [], f"dependency lock drift: {findings}"


def test_pypdf_is_pinned_in_both_lock_files() -> None:
    """BKT-01: the PDF parser is imported at module top level by the app."""

    for lock_path in (RUNTIME_LOCK, DEV_LOCK):
        pinned = dependency_locks.parse_lock(lock_path)
        assert "pypdf" in pinned, f"{lock_path.name} does not pin pypdf"
        assert dependency_locks.satisfies(pinned["pypdf"], dependency_locks.parse_requirement("pypdf>=5,<7").specifiers)


def test_pypdf_has_no_unlocked_runtime_requirement() -> None:
    """pypdf 6.x is dependency-free; if that changes the lock must grow too."""

    import importlib.metadata as metadata

    from packaging.markers import default_environment
    from packaging.requirements import Requirement

    try:
        requires = metadata.requires("pypdf") or []
    except metadata.PackageNotFoundError:  # pragma: no cover - lock-only environments
        pytest.skip("pypdf is not installed in this environment")
    environment = default_environment()
    pinned = dependency_locks.parse_lock(RUNTIME_LOCK)
    for raw in requires:
        requirement = Requirement(raw)
        if requirement.marker is not None and not requirement.marker.evaluate(environment):
            continue
        name = dependency_locks.normalize_name(requirement.name)
        assert name in pinned, f"pypdf requires {raw!r}, which is missing from {RUNTIME_LOCK.name}"


def test_lock_files_are_alphabetically_ordered() -> None:
    for lock_path in (RUNTIME_LOCK, DEV_LOCK):
        dependency_locks.parse_lock(lock_path)


def test_specifier_satisfaction_covers_the_declared_range() -> None:
    satisfies = dependency_locks.satisfies
    specifiers = dependency_locks.parse_requirement("pypdf>=5,<7").specifiers
    assert satisfies(dependency_locks.Version.parse("5.0.0"), specifiers)
    assert satisfies(dependency_locks.Version.parse("6.16.2"), specifiers)
    assert not satisfies(dependency_locks.Version.parse("4.3.1"), specifiers)
    assert not satisfies(dependency_locks.Version.parse("7.0.0"), specifiers)


def test_specifier_operator_matrix() -> None:
    parse_requirement = dependency_locks.parse_requirement
    satisfies = dependency_locks.satisfies
    version = dependency_locks.Version.parse

    assert satisfies(version("2.13.4"), parse_requirement("pydantic>=2.8,<3").specifiers)
    assert satisfies(version("2.13.4"), parse_requirement("pydantic==2.13.4").specifiers)
    assert not satisfies(version("2.13.4"), parse_requirement("pydantic==2.13.5").specifiers)
    assert not satisfies(version("2.13.4"), parse_requirement("pydantic!=2.13.4").specifiers)
    assert satisfies(version("1.19.1"), parse_requirement("alembic~=1.19.0").specifiers)
    assert not satisfies(version("2.0.0"), parse_requirement("alembic~=1.19.0").specifiers)
    assert satisfies(version("0.28.1"), parse_requirement("httpx>=0.27,<1").specifiers)
    assert not satisfies(version("1.0.0"), parse_requirement("httpx>=0.27,<1").specifiers)
    # Extras and environment markers are not part of the version constraint.
    assert parse_requirement("uvicorn[standard]>=0.30,<1").normalized_name == "uvicorn"
    assert satisfies(version("0.52.1"), parse_requirement('colorama>=0.4; sys_platform == "win32"').specifiers)


def test_missing_dependency_is_reported(tmp_path: Path) -> None:
    """A future runtime dependency added to pyproject without a lock must fail."""

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\ndependencies = [\n  "pypdf>=5,<7",\n  "brand-new-runtime-dep>=1,<2",\n]\n',
        encoding="utf-8",
    )
    lock = tmp_path / "requirements.lock"
    lock.write_text("pypdf==6.16.2\n", encoding="utf-8")
    findings = dependency_locks.check_locks(pyproject, (lock,))
    assert any("brand-new-runtime-dep" in finding and "missing" in finding for finding in findings)


def test_unsatisfied_pin_is_reported(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\ndependencies = ["pypdf>=5,<7"]\n', encoding="utf-8")
    lock = tmp_path / "requirements.lock"
    lock.write_text("pypdf==7.1.0\n", encoding="utf-8")
    findings = dependency_locks.check_locks(pyproject, (lock,))
    assert any("does not satisfy" in finding for finding in findings)


def test_unsorted_lock_is_reported(tmp_path: Path) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text("websockets==17.0.1\nalembic==1.19.1\n", encoding="utf-8")
    with pytest.raises(dependency_locks.LockCheckError, match="not alphabetically sorted"):
        dependency_locks.parse_lock(lock)


def test_unpinned_lock_entry_is_reported(tmp_path: Path) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text("pypdf>=5\n", encoding="utf-8")
    with pytest.raises(dependency_locks.LockCheckError, match="must pin"):
        dependency_locks.parse_lock(lock)


def test_cli_exits_non_zero_and_names_the_problem(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\ndependencies = ["pypdf>=5,<7"]\n', encoding="utf-8")
    lock = tmp_path / "requirements.lock"
    lock.write_text("fastapi==0.141.1\n", encoding="utf-8")
    exit_code = dependency_locks.main(["--pyproject", str(pyproject), "--lock", str(lock)])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "pypdf" in captured.err


def test_cli_is_clean_for_the_real_repository(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = dependency_locks.main(["--pyproject", str(PYPROJECT), "--lock", str(RUNTIME_LOCK), "--lock", str(DEV_LOCK)])
    assert exit_code == 0
    assert "dependency_locks=PASS" in capsys.readouterr().out


def test_pyproject_declares_the_pdf_parser() -> None:
    document = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    declared = " ".join(str(item) for item in document["project"]["dependencies"])
    assert "pypdf" in declared
