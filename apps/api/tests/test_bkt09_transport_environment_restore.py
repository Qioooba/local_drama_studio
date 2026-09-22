"""Regression tests for BKT-09: the UAT transport context must restore every env key.

The original ``_hostile_proxy_environment`` set ``LOCAL_DRAMA_COMFY_ACCESS`` but
never restored it, so a UAT run left Comfy access ``enabled`` for every later
test in the same process.  These tests exercise the real context manager and
assert the CORRECT post-condition: the environment is byte-for-byte identical to
what it was before entry, for all four documented cases.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "local_adapter_transport_windows_uat.py"

WATCHED_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "LOCAL_DRAMA_COMFY_ACCESS",
)


def _load_uat_module():  # type: ignore[no-untyped-def]
    """Import the UAT script as a module.

    Its work happens inside ``run_uat``/``main``, so importing it has no side
    effects beyond the imports it needs.
    """

    spec = importlib.util.spec_from_file_location("local_adapter_transport_windows_uat", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["local_adapter_transport_windows_uat"] = module
    spec.loader.exec_module(module)
    return module


uat = _load_uat_module()


@pytest.fixture()
def clean_environment() -> Iterator[None]:
    """Remove every watched key before and after each case."""

    snapshot = {key: os.environ.get(key) for key in WATCHED_KEYS}
    for key in WATCHED_KEYS:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in snapshot.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _watched() -> dict[str, Any]:
    return {key: os.environ.get(key) for key in WATCHED_KEYS}


@pytest.mark.parametrize("original", ["disabled", "enabled", None])
def test_environment_is_restored_exactly(clean_environment: None, original: str | None) -> None:
    """Cases 1-3: original value disabled, enabled, or entirely unset."""

    if original is not None:
        os.environ["LOCAL_DRAMA_COMFY_ACCESS"] = original
    os.environ["HTTP_PROXY"] = "http://proxy.before.example:8080"
    before = _watched()

    with uat._hostile_proxy_environment():
        # In-context semantics must stay exactly as they were: hostile proxies
        # plus a loopback-only allowance for the UAT's own test server.
        assert os.environ["LOCAL_DRAMA_COMFY_ACCESS"] == "enabled"
        assert os.environ["HTTP_PROXY"] == "http://203.0.113.66:3128"
        assert os.environ["http_proxy"] == "http://203.0.113.66:3128"

    assert _watched() == before
    if original is None:
        assert "LOCAL_DRAMA_COMFY_ACCESS" not in os.environ
    else:
        assert os.environ["LOCAL_DRAMA_COMFY_ACCESS"] == original


def test_environment_is_restored_when_the_body_raises(clean_environment: None) -> None:
    """Case 4: an exception inside the context must not leak either."""

    os.environ["LOCAL_DRAMA_COMFY_ACCESS"] = "disabled"
    before = _watched()

    with pytest.raises(RuntimeError, match="transport uat failed"):
        with uat._hostile_proxy_environment():
            assert os.environ["LOCAL_DRAMA_COMFY_ACCESS"] == "enabled"
            raise RuntimeError("transport uat failed")

    assert _watched() == before
    assert os.environ["LOCAL_DRAMA_COMFY_ACCESS"] == "disabled"


def test_originally_unset_key_is_deleted_even_after_an_exception(clean_environment: None) -> None:
    assert "LOCAL_DRAMA_COMFY_ACCESS" not in os.environ
    with pytest.raises(ValueError):
        with uat._hostile_proxy_environment():
            raise ValueError("boom")
    assert "LOCAL_DRAMA_COMFY_ACCESS" not in os.environ


def test_proxy_keys_that_were_unset_are_removed(clean_environment: None) -> None:
    for key in WATCHED_KEYS:
        os.environ.pop(key, None)
    with uat._hostile_proxy_environment():
        assert os.environ["http_proxy"] == "http://203.0.113.66:3128"
    assert _watched() == dict.fromkeys(WATCHED_KEYS)
    for key in WATCHED_KEYS:
        assert key not in os.environ


def test_uat_result_records_no_public_network_contact() -> None:
    """The UAT result contract still refuses to claim runtime contact."""

    source = SCRIPT.read_text(encoding="utf-8")
    assert '"public_network_contacted": False' in source
    assert '"runtime_contacted": False' in source
