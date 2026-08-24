from __future__ import annotations

from types import SimpleNamespace

import pytest

from local_drama.application import local_picker
from local_drama.domain.errors import DomainRuleError


def test_picker_returns_existing_path_without_copy_or_upload(workspace, monkeypatch) -> None:
    model = workspace.work_root / "picked.safetensors"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"local-model")
    monkeypatch.setattr(local_picker.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=str(model)))
    result = local_picker.pick_local_model_file()
    assert result == {"selected": True, "path": str(model.resolve()), "uploaded": False, "copied": False}


def test_picker_cancel_is_non_mutating(monkeypatch) -> None:
    monkeypatch.setattr(local_picker.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=""))
    assert local_picker.pick_local_model_file() == {"selected": False, "path": None, "uploaded": False, "copied": False}


def test_document_picker_returns_supported_local_path(workspace, monkeypatch) -> None:
    document = workspace.work_root / "script.docx"
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_bytes(b"local-document")
    monkeypatch.setattr(local_picker.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=str(document)))
    assert local_picker.pick_local_document_file() == {
        "selected": True,
        "path": str(document.resolve()),
        "uploaded": False,
        "copied": False,
    }


def test_document_picker_timeout_is_bounded_and_actionable(monkeypatch) -> None:
    def timeout(*args, **kwargs):
        raise local_picker.subprocess.TimeoutExpired("powershell.exe", kwargs["timeout"])

    monkeypatch.setattr(local_picker.subprocess, "run", timeout)
    with pytest.raises(DomainRuleError) as captured:
        local_picker.pick_local_document_file()
    assert captured.value.code == "LOCAL_DOCUMENT_PICKER_UNAVAILABLE"
    assert "已自动关闭" in captured.value.message
