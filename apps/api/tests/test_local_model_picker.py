from __future__ import annotations

from types import SimpleNamespace

import pytest

import local_drama.platform.windows.file_picker as windows_picker
from local_drama.domain.errors import DomainRuleError
from local_drama.platform.contracts import FilePickerRequest


def _request(kind: str) -> FilePickerRequest:
    if kind == "MODEL":
        return FilePickerRequest("MODEL", "选择模型", (".safetensors", ".ckpt", ".bin", ".pt", ".pth"))
    return FilePickerRequest("DOCUMENT", "选择文档", (".txt", ".md", ".markdown", ".docx", ".pdf", ".epub"))


def test_picker_returns_existing_path_without_copy_or_upload(workspace, monkeypatch) -> None:
    model = workspace.work_root / "picked.safetensors"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"local-model")
    monkeypatch.setattr(windows_picker.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=str(model)))
    result = windows_picker.WindowsFilePicker().choose(_request("MODEL")).public()
    assert result == {"selected": True, "path": str(model.resolve()), "uploaded": False, "copied": False}


def test_picker_cancel_is_non_mutating(monkeypatch) -> None:
    monkeypatch.setattr(windows_picker.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=""))
    assert windows_picker.WindowsFilePicker().choose(_request("MODEL")).public() == {"selected": False, "path": None, "uploaded": False, "copied": False}


def test_document_picker_returns_supported_local_path(workspace, monkeypatch) -> None:
    document = workspace.work_root / "script.docx"
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_bytes(b"local-document")
    monkeypatch.setattr(windows_picker.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=str(document)))
    assert windows_picker.WindowsFilePicker().choose(_request("DOCUMENT")).public() == {
        "selected": True,
        "path": str(document.resolve()),
        "uploaded": False,
        "copied": False,
    }


def test_document_picker_timeout_is_bounded_and_actionable(monkeypatch) -> None:
    def timeout(*args, **kwargs):
        raise windows_picker.subprocess.TimeoutExpired("powershell.exe", kwargs["timeout"])

    monkeypatch.setattr(windows_picker.subprocess, "run", timeout)
    with pytest.raises(DomainRuleError) as captured:
        windows_picker.WindowsFilePicker().choose(_request("DOCUMENT"))
    assert captured.value.code == "LOCAL_FILE_PICKER_UNAVAILABLE"
