"""Regression tests for BKT-07: the default api:test must not contact live hardware.

The README claimed ``pnpm run api:test`` never touches a live ComfyUI while the
script actually collected the ``comfyui``-marked cases.  These tests assert the
CORRECT configuration: a safe default command that deselects the live markers,
plus a real-hardware command that keeps those cases present and runnable.

The marker expressions are verified as the exact strings pytest is given, and
the live cases are verified to still exist rather than having been deleted or
skipped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_JSON = REPO_ROOT / "package.json"
SAFE_SCRIPT = REPO_ROOT / "scripts" / "test_api_safe.ps1"
LIVE_SCRIPT = REPO_ROOT / "scripts" / "test_api_live.ps1"
PYPROJECT = REPO_ROOT / "apps" / "api" / "pyproject.toml"
README = REPO_ROOT / "README.md"

SAFE_MARKERS = "'not comfyui and not video_upscale_gpu'"
LIVE_MARKERS = "'comfyui or video_upscale_gpu'"


def _scripts() -> dict[str, str]:
    return json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["scripts"]


def test_default_api_test_runs_the_safe_script() -> None:
    scripts = _scripts()
    assert "test_api_safe.ps1" in scripts["api:test"]
    assert "pytest" not in scripts["api:test"], "the default command must not invoke pytest directly"
    assert scripts["api:test:safe"] == scripts["api:test"]


def test_live_api_test_is_exposed_separately() -> None:
    scripts = _scripts()
    assert "test_api_live.ps1" in scripts["api:test:live"]
    assert scripts["api:test:live"] != scripts["api:test"]


def test_safe_script_disables_comfy_access_and_filters_markers() -> None:
    text = SAFE_SCRIPT.read_text(encoding="utf-8")
    assert SAFE_MARKERS in text
    assert "$env:LOCAL_DRAMA_COMFY_ACCESS = 'disabled'" in text
    # The previous value must be restored, and an originally-unset key removed.
    assert "$previousComfyAccess = $env:LOCAL_DRAMA_COMFY_ACCESS" in text
    assert "Remove-Item Env:LOCAL_DRAMA_COMFY_ACCESS" in text


def test_safe_script_runs_pytest_from_the_api_directory() -> None:
    text = SAFE_SCRIPT.read_text(encoding="utf-8")
    assert "Push-Location (Join-Path $repoRoot 'apps/api')" in text
    assert "-m pytest" in text
    assert "Pop-Location" in text


def test_live_script_selects_exactly_the_live_markers() -> None:
    text = LIVE_SCRIPT.read_text(encoding="utf-8")
    assert LIVE_MARKERS in text
    assert SAFE_MARKERS not in text


def test_live_script_documents_its_prerequisites() -> None:
    text = LIVE_SCRIPT.read_text(encoding="utf-8")
    for prerequisite in ("ComfyUI", "NVIDIA", "NCNN", "manifest"):
        assert prerequisite in text, f"live script does not document the {prerequisite} prerequisite"
    assert "Windows" in text


def test_live_marker_set_matches_the_declared_pytest_markers() -> None:
    declared = PYPROJECT.read_text(encoding="utf-8")
    for marker in ("comfyui", "video_upscale_gpu"):
        assert re.search(rf'^\s+"?{marker}:', declared, re.MULTILINE), f"{marker} is not declared in [tool.pytest.ini_options] markers"


def test_live_cases_still_exist_and_are_not_skipped() -> None:
    """The live cases must remain in the suite, just not run by default."""

    tests_dir = REPO_ROOT / "apps" / "api" / "tests"
    marked: list[Path] = []
    for path in sorted(tests_dir.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue  # this file necessarily mentions the marker names
        lines = path.read_text(encoding="utf-8").splitlines()
        marker_lines = [
            index
            for index, line in enumerate(lines)
            if "pytest.mark.comfyui" in line or "pytest.mark.video_upscale_gpu" in line
        ]
        if not marker_lines:
            continue
        marked.append(path)
        for index in marker_lines:
            following = "\n".join(lines[index + 1 : index + 4])
            assert "pytest.mark.skip" not in following, f"{path.name}:{index + 1} hides a live case behind skip"
    assert marked, "no comfyui/video_upscale_gpu-marked tests remain in the suite"


def test_readme_documents_both_commands_and_prerequisites() -> None:
    text = README.read_text(encoding="utf-8")
    assert "pnpm run api:test:live" in text
    assert "not comfyui and not video_upscale_gpu" in text
    assert "test_api_safe.ps1" in text
    assert "test_api_live.ps1" in text
    # The old, false claim that the default command excludes live ComfyUI
    # without actually doing so must be gone.
    assert "默认不连接实时 ComfyUI" not in text


def test_check_ps1_uses_the_safe_suite_by_default() -> None:
    text = (REPO_ROOT / "scripts" / "check.ps1").read_text(encoding="utf-8")
    assert "scripts/test_api_safe.ps1" in text
    assert "scripts/test_api_live.ps1" not in text
