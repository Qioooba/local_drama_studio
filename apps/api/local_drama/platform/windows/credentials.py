from __future__ import annotations

import ctypes
import os
import re
from ctypes import wintypes

from local_drama.platform.contracts import SecretRef


class _CredentialW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168


def _safe(value: str) -> str:
    normalized = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", normalized):
        raise ValueError("secret reference format is invalid")
    return normalized


class WindowsCredentialStore:
    name = "WINDOWS_CREDENTIAL_MANAGER"

    @property
    def available(self) -> bool:
        return os.name == "nt"

    @staticmethod
    def _target(ref: SecretRef) -> str:
        if ref.namespace == "DeepSeekAPI" and ref.key == "default":
            return "LocalDramaStudio/DeepSeekAPI"
        return f"LocalDramaStudio/{_safe(ref.namespace)}/{_safe(ref.key)}"

    @staticmethod
    def _library():
        if os.name != "nt":
            return None
        library = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        library.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_CredentialW))]
        library.CredReadW.restype = wintypes.BOOL
        library.CredWriteW.argtypes = [ctypes.POINTER(_CredentialW), wintypes.DWORD]
        library.CredWriteW.restype = wintypes.BOOL
        library.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        library.CredDeleteW.restype = wintypes.BOOL
        library.CredFree.argtypes = [ctypes.c_void_p]
        library.CredFree.restype = None
        return library

    def get(self, ref: SecretRef) -> str | None:
        library = self._library()
        if library is None:
            return None
        pointer = ctypes.POINTER(_CredentialW)()
        if not library.CredReadW(self._target(ref), _CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == _ERROR_NOT_FOUND:
                return None
            raise OSError(ctypes.get_last_error(), "cannot read Windows credential")
        try:
            credential = pointer.contents
            raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
            return raw.decode("utf-8").strip() or None
        finally:
            library.CredFree(pointer)

    def put(self, ref: SecretRef, value: str) -> None:
        library = self._library()
        if library is None:
            raise OSError("Windows Credential Manager is unavailable")
        secret = value.strip()
        if not secret:
            raise ValueError("secret cannot be empty")
        raw = secret.encode("utf-8")
        if len(raw) > 2560:
            raise ValueError("secret exceeds Windows Credential Manager limit")
        blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        credential = _CredentialW()
        credential.Type = _CRED_TYPE_GENERIC
        credential.TargetName = self._target(ref)
        credential.Comment = "LocalDramaStudio managed secret"
        credential.CredentialBlobSize = len(raw)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = _CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "local-drama-runtime"
        if not library.CredWriteW(ctypes.byref(credential), 0):
            raise OSError(ctypes.get_last_error(), "cannot write Windows credential")

    def delete(self, ref: SecretRef) -> bool:
        library = self._library()
        if library is None:
            return False
        if library.CredDeleteW(self._target(ref), _CRED_TYPE_GENERIC, 0):
            return True
        if ctypes.get_last_error() == _ERROR_NOT_FOUND:
            return False
        raise OSError(ctypes.get_last_error(), "cannot delete Windows credential")
