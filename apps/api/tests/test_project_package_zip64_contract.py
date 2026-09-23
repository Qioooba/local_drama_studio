"""Large files in a project package must not fail the ZIP64 decision (PKG-03).

Reproduced defect on the audit snapshot.  ``_write_path`` replaced
``ZipFile.write`` with ``archive.open(info, "w")`` to keep a fixed timestamp, but a
fresh ``ZipInfo`` carries no ``file_size``, so the entry could not be recognised as
ZIP64-sized and a plain local header was written.  Closing the stream then raised
``RuntimeError: File size too large, try using force_zip64`` — with the *audit's*
threshold experiment (``zipfile.ZIP64_LIMIT`` lowered to 1 KiB) the new path failed
on a 4 KiB file while both ``archive.write()`` and ``force_zip64=True`` succeeded.

The real limit is 2 GiB per entry, which a render or an intermediate video in a
project easily reaches, so the protocol allowed up to 2 TiB expanded while the
implementation failed at one ordinary video.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from local_drama.application.project_packages import _write_path  # noqa: SLF001


def _archive(tmp_path: Path, *, allow_zip64: bool = True) -> tuple[zipfile.ZipFile, Path]:
    target = tmp_path / "package.zip"
    return zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, allowZip64=allow_zip64), target


def test_a_file_over_the_zip64_limit_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The audit's threshold experiment, reproduced exactly."""

    monkeypatch.setattr(zipfile, "ZIP64_LIMIT", 1024)
    source = tmp_path / "render.mp4"
    payload = bytes(range(256)) * 16  # 4096 bytes, deterministic
    source.write_bytes(payload)
    assert len(payload) > zipfile.ZIP64_LIMIT

    archive, target = _archive(tmp_path)
    with archive:
        _write_path(archive, "media/render.mp4", source)
    with zipfile.ZipFile(target) as check:
        assert check.read("media/render.mp4") == payload
        assert check.getinfo("media/render.mp4").file_size == len(payload)


def test_a_file_just_below_the_limit_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zipfile, "ZIP64_LIMIT", 1024)
    source = tmp_path / "small.txt"
    payload = b"x" * 512
    source.write_bytes(payload)
    archive, target = _archive(tmp_path)
    with archive:
        _write_path(archive, "notes/small.txt", source)
    with zipfile.ZipFile(target) as check:
        assert check.read("notes/small.txt") == payload


def test_a_file_exactly_on_the_limit_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zipfile, "ZIP64_LIMIT", 1024)
    source = tmp_path / "exact.bin"
    payload = b"y" * 1024
    source.write_bytes(payload)
    archive, target = _archive(tmp_path)
    with archive:
        _write_path(archive, "media/exact.bin", source)
    with zipfile.ZipFile(target) as check:
        assert check.read("media/exact.bin") == payload


def test_the_archives_stay_deterministic(tmp_path: Path) -> None:
    """Two exports of identical content must still produce identical bytes."""

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"deterministic-payload" * 100)
    digests = []
    for index in range(2):
        directory = tmp_path / f"pkg{index}"
        directory.mkdir()
        archive_path = directory / "package.zip"
        archive = zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED)
        with archive:
            _write_path(archive, "media/clip.mp4", source)
        digests.append(hashlib.sha256(archive_path.read_bytes()).hexdigest())
    assert digests[0] == digests[1]


def test_readback_digest_matches_the_source(tmp_path: Path) -> None:
    source = tmp_path / "audio.wav"
    payload = b"RIFF" + b"\x00" * 4096
    source.write_bytes(payload)
    target = tmp_path / "pkg.zip"
    archive = zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED)
    with archive:
        _write_path(archive, "media/audio.wav", source)
    with zipfile.ZipFile(target) as check:
        data = check.read("media/audio.wav")
    assert hashlib.sha256(data).hexdigest() == hashlib.sha256(payload).hexdigest()
