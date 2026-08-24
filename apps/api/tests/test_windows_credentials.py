from __future__ import annotations

import ctypes

from local_drama.infrastructure import windows_credentials as credentials


class _FakeCredentialApi:
    def __init__(self) -> None:
        self.read_targets: list[str] = []
        self.delete_targets: list[str] = []
        self._buffers: list[object] = []

    def CredReadW(self, target: str, *_args: object) -> int:
        out_pointer = _args[-1]
        self.read_targets.append(target)
        raw = ctypes.create_string_buffer(b"scoped-secret")
        record = credentials._CredentialW()
        record.CredentialBlobSize = len(b"scoped-secret")
        record.CredentialBlob = ctypes.cast(raw, ctypes.POINTER(ctypes.c_ubyte))
        record_pointer = ctypes.pointer(record)
        pointer_value = out_pointer._obj  # type: ignore[attr-defined]
        ctypes.memmove(ctypes.addressof(pointer_value), ctypes.byref(record_pointer), ctypes.sizeof(pointer_value))
        self._buffers.extend([raw, record, record_pointer])
        return 1

    def CredDeleteW(self, target: str, *_args: object) -> int:
        self.delete_targets.append(target)
        return 1

    def CredFree(self, _pointer: object) -> None:
        return None


def test_provider_credential_read_and_delete_use_scoped_target(monkeypatch) -> None:
    fake = _FakeCredentialApi()
    monkeypatch.setattr(credentials, "_advapi32", lambda: fake)

    assert credentials._read_credential("LocalDramaStudio/ProviderConnection/connection-a", "Provider 密钥") == "scoped-secret"
    assert credentials._delete_credential("LocalDramaStudio/ProviderConnection/connection-a", "Provider 密钥") is True
    assert fake.read_targets == ["LocalDramaStudio/ProviderConnection/connection-a"]
    assert fake.delete_targets == ["LocalDramaStudio/ProviderConnection/connection-a"]
