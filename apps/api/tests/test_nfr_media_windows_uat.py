from __future__ import annotations

from scripts.nfr_media_windows_uat import _check_range_result, _p95


def _response(*, status: int = 206, content_range: str = "bytes 0-65535/505208", length: int = 65536) -> dict[str, object]:
    return {
        "status": status,
        "bytes": length,
        "headers": {"accept-ranges": "bytes", "content-range": content_range},
        "error": None,
    }


def test_windows_media_uat_range_contract_rejects_unbounded_or_mismatched_reads() -> None:
    assert _check_range_result(_response(), expected_status=206, expected_bytes=65536, total_bytes=505208)
    assert not _check_range_result(
        _response(status=200, content_range="", length=505208),
        expected_status=206,
        expected_bytes=65536,
        total_bytes=505208,
    )
    assert not _check_range_result(
        _response(content_range="bytes 0-505207/505208", length=505208),
        expected_status=206,
        expected_bytes=65536,
        total_bytes=505208,
    )


def test_windows_media_uat_p95_is_bounded_and_deterministic() -> None:
    assert _p95([]) == 0.0
    assert _p95([12.5]) == 12.5
    assert round(_p95([1.0, 2.0, 3.0, 4.0]), 3) == 3.85
