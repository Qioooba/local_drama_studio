from __future__ import annotations

import json

from scripts.local_adapter_transport_windows_uat import run_uat


def test_local_adapter_transport_windows_uat_exercises_real_loopback_transport(tmp_path) -> None:
    result = run_uat(tmp_path / "isolated-transport-uat")

    assert result["status"] == "PARTIAL"
    assert result["loopback_network_contacted"] is True
    assert result["public_network_contacted"] is False
    assert all(check["passed"] for check in result["checks"])
    assert "secret-input" not in json.dumps(result, ensure_ascii=False)
    assert "do-not-disclose" not in json.dumps(result, ensure_ascii=False)
