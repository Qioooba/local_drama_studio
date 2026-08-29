from __future__ import annotations

import hashlib
import io

import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.model_platform.application.trusted_download_execution import (
    HostTrustedDownloadExecutor,
    TrustedDownloadRequest,
)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Opener:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def open(self, url: str, *, timeout: int):
        assert timeout == 60
        self.urls.append(url)
        return _Response(self.payload)


def _request(payload: bytes, *, url: str = "https://models.example.test/releases/model.bin") -> TrustedDownloadRequest:
    return TrustedDownloadRequest(
        source_url=url,
        bundle_reference="qwen3-download",
        relative_path="Embedding/model.safetensors",
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )


def test_host_trusted_download_writes_verified_content_only_to_downloads(workspace, tmp_path) -> None:
    payload = b"verified-model-bytes"
    settings = workspace.model_copy(update={"model_root": tmp_path / "models", "model_download_source_hosts": ("models.example.test",)})
    settings.ensure_roots()
    opener = _Opener(payload)

    destination = HostTrustedDownloadExecutor(settings, opener=opener).download(_request(payload))

    assert destination == settings.model_root / "downloads" / "qwen3-download" / "Embedding" / "model.safetensors"
    assert destination.read_bytes() == payload
    assert opener.urls == ["https://models.example.test/releases/model.bin"]


def test_host_trusted_download_removes_partial_file_when_integrity_fails(workspace, tmp_path) -> None:
    expected = b"expected"
    settings = workspace.model_copy(update={"model_root": tmp_path / "models", "model_download_source_hosts": ("models.example.test",)})
    settings.ensure_roots()

    with pytest.raises(DomainRuleError) as failed:
        HostTrustedDownloadExecutor(settings, opener=_Opener(b"tampered")).download(_request(expected))

    assert failed.value.code == "MP_DOWNLOAD_INTEGRITY_FAILED"
    assert not list((settings.model_root / "downloads").rglob("*"))


def test_host_trusted_download_rejects_untrusted_source_before_transport(workspace, tmp_path) -> None:
    settings = workspace.model_copy(update={"model_root": tmp_path / "models", "model_download_source_hosts": ()})
    settings.ensure_roots()
    opener = _Opener(b"never-read")

    with pytest.raises(DomainRuleError) as rejected:
        HostTrustedDownloadExecutor(settings, opener=opener).download(_request(b"expected"))

    assert rejected.value.code == "MP_DOWNLOAD_SOURCE_UNTRUSTED"
    assert opener.urls == []


def test_host_trusted_download_rejects_non_hex_digest_before_transport(workspace, tmp_path) -> None:
    settings = workspace.model_copy(update={"model_root": tmp_path / "models", "model_download_source_hosts": ("models.example.test",)})
    settings.ensure_roots()
    opener = _Opener(b"never-read")
    request = TrustedDownloadRequest(
        source_url="https://models.example.test/releases/model.bin",
        bundle_reference="qwen3-download",
        relative_path="Embedding/model.safetensors",
        sha256="g" * 64,
        size_bytes=1,
    )

    with pytest.raises(DomainRuleError) as rejected:
        HostTrustedDownloadExecutor(settings, opener=opener).download(request)

    assert rejected.value.code == "MP_DOWNLOAD_EXPECTATION_INVALID"
    assert opener.urls == []


def test_host_promotes_download_bundle_to_staging_without_copying(workspace, tmp_path) -> None:
    settings = workspace.model_copy(update={"model_root": tmp_path / "models", "model_download_source_hosts": ("models.example.test",)})
    settings.ensure_roots()
    source = settings.model_root / "downloads" / "qwen3-download" / "Embedding"
    source.mkdir(parents=True)
    (source / "model.safetensors").write_bytes(b"verified")

    destination = HostTrustedDownloadExecutor(settings).promote_bundle_to_staging("qwen3-download")

    assert destination == settings.model_root / "staging" / "qwen3-download"
    assert (destination / "Embedding" / "model.safetensors").read_bytes() == b"verified"
    assert not (settings.model_root / "downloads" / "qwen3-download").exists()
