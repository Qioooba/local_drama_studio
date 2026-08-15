from __future__ import annotations

from types import SimpleNamespace

from local_drama.application import local_picker


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
