"""Regression tests for BKT-06: the maintainability report path must render anywhere.

The original ``_relative`` raised ``ValueError`` for an ``--output`` outside the
repository, after the JSON had already been written, so the summary never
printed and the gate exit code became meaningless.  These tests cover
repo-relative, repo-absolute and outside-repo-absolute output paths, and assert
the CORRECT behaviour: the file is written, the summary prints, and the exit
code reflects the deterministic hard failures.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path, PurePath

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load(name: str, filename: str):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


audit = _load("maintainability_audit", "maintainability_audit.py")


def test_relative_renders_repo_paths_relative() -> None:
    assert audit._relative(REPO_ROOT / "docs" / "evidence" / "report.json") == "docs/evidence/report.json"


def test_relative_renders_outside_repo_paths_absolutely() -> None:
    """The BKT-06 fix: no ValueError, the absolute path is returned instead."""

    outside = REPO_ROOT.parent / "outside-audit-output" / "report.json"
    rendered = audit._relative(outside)
    assert rendered.endswith("outside-audit-output/report.json")
    assert PurePath(rendered).is_absolute()
    assert rendered == outside.as_posix()


def test_relative_handles_arbitrary_absolute_paths() -> None:
    assert PurePath(audit._relative(Path("C:/totally/elsewhere/report.json"))).is_absolute()


def _run_audit(output: Path) -> subprocess.CompletedProcess[str]:
    # One bounded retry: a concurrent agent rewriting a source file can produce
    # a transient PermissionError inside the static scan on Windows.
    for attempt in range(2):
        completed = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "maintainability_audit.py"), "--output", str(output)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        if completed.stdout.strip() or attempt == 1:
            return completed
    raise AssertionError("unreachable")


def assert_summary_and_exit_code(completed: subprocess.CompletedProcess[str], output: Path) -> dict[str, object]:
    assert completed.stdout.strip(), f"the audit printed no summary; stderr={completed.stderr[:2000]}"
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    # The audit echoes a repo-relative path for outputs inside the repo and an
    # absolute path otherwise; either form must resolve to the written file.
    echoed = Path(str(payload["output"]))
    assert (echoed if echoed.is_absolute() else REPO_ROOT / echoed).resolve() == output.resolve()
    assert output.is_file()
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["status"] == payload["status"]
    expected_exit = 1 if payload["hard_failures"] else 0
    assert completed.returncode == expected_exit
    return payload


@pytest.mark.parametrize("case", ["repo-relative", "repo-absolute", "outside-repo-absolute"])
def test_output_paths_all_write_and_report(tmp_path: Path, case: str) -> None:
    unique = f"bkt06-{case}-{tmp_path.name}"
    if case == "repo-relative":
        output = Path("work") / f"{unique}.json"
        resolved = REPO_ROOT / output
    elif case == "repo-absolute":
        output = REPO_ROOT / "work" / f"{unique}.json"
        resolved = output
    else:
        output = tmp_path / f"{unique}.json"
        resolved = output
    if resolved.exists():
        resolved.unlink()
    try:
        completed = _run_audit(output)
        assert_summary_and_exit_code(completed, resolved)
    finally:
        resolved.unlink(missing_ok=True)


def test_outside_repo_output_records_the_absolute_path(tmp_path: Path) -> None:
    output = tmp_path / "outside" / "maintainability.json"
    completed = _run_audit(output)
    payload = assert_summary_and_exit_code(completed, output)
    assert "ValueError" not in completed.stderr
    assert "Traceback" not in completed.stderr
    assert PurePath(str(payload["output"])).is_absolute()
