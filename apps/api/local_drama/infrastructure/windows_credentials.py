"""Windows Credential Manager adapter for local provider secrets.

The secret belongs to the current Windows user. It is never written to SQLite,
project packages, job snapshots, logs, or application config files.
"""

from __future__ import annotations

import ctypes
import os
import re
from ctypes import wintypes
from typing import Any

DEEPSEEK_CREDENTIAL_TARGET = "LocalDramaStudio/DeepSeekAPI"


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


def provider_credential_target(connection_id: str) -> str:
    """Return a stable target name without allowing path-like injection."""

    normalized = str(connection_id).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", normalized):
        raise ValueError("Provider Connection ID 格式无效")
    return f"LocalDramaStudio/ProviderConnection/{normalized}"


def _advapi32() -> Any | None:
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


def _read_credential(target: str, label: str) -> str | None:
    library = _advapi32()
    if library is None:
        return None
    pointer = ctypes.POINTER(_CredentialW)()
    if not library.CredReadW(target, _CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        if ctypes.get_last_error() == _ERROR_NOT_FOUND:
            return None
        raise OSError(ctypes.get_last_error(), f"无法从 Windows 凭据管理器读取 {label}")
    try:
        credential = pointer.contents
        raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return raw.decode("utf-8").strip() or None
    finally:
        library.CredFree(pointer)


def _write_credential(target: str, secret: str, label: str) -> None:
    library = _advapi32()
    if library is None:
        raise OSError("当前系统不支持 Windows 凭据管理器")
    value = secret.strip()
    if not value:
        raise ValueError(f"{label} 不能为空")
    raw = value.encode("utf-8")
    if len(raw) > 2560:
        raise ValueError(f"{label} 超过 Windows 凭据长度限制")
    blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    credential = _CredentialW()
    credential.Type = _CRED_TYPE_GENERIC
    credential.TargetName = target
    credential.Comment = f"Local Drama Studio {label}"
    credential.CredentialBlobSize = len(raw)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = _CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = "local-user"
    if not library.CredWriteW(ctypes.byref(credential), 0):
        raise OSError(ctypes.get_last_error(), f"无法写入 Windows 凭据管理器：{label}")


def _delete_credential(target: str, label: str) -> bool:
    library = _advapi32()
    if library is None:
        return False
    if library.CredDeleteW(target, _CRED_TYPE_GENERIC, 0):
        return True
    if ctypes.get_last_error() == _ERROR_NOT_FOUND:
        return False
    raise OSError(ctypes.get_last_error(), f"无法从 Windows 凭据管理器删除 {label}")


def read_provider_secret(connection_id: str) -> str | None:
    return _read_credential(provider_credential_target(connection_id), "Provider 密钥")


def write_provider_secret(connection_id: str, secret: str) -> None:
    _write_credential(provider_credential_target(connection_id), secret, "Provider 密钥")


def delete_provider_secret(connection_id: str) -> bool:
    return _delete_credential(provider_credential_target(connection_id), "Provider 密钥")


def read_deepseek_api_key() -> str | None:
    return _read_credential(DEEPSEEK_CREDENTIAL_TARGET, "DeepSeek API Key")


def write_deepseek_api_key(api_key: str) -> None:
    _write_credential(DEEPSEEK_CREDENTIAL_TARGET, api_key, "DeepSeek API Key")


def delete_deepseek_api_key() -> bool:
    return _delete_credential(DEEPSEEK_CREDENTIAL_TARGET, "DeepSeek API Key")
