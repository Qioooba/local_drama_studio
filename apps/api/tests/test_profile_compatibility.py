from __future__ import annotations

import pytest

from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError


def _complete(service: ProfileService, workspace, database) -> dict[str, object]:
    source = service.sync_manifest()["profiles"][0]
    with database.transaction() as connection:
        connection.execute("UPDATE execution_profile_versions SET status='PUBLISHED' WHERE id=?", (source["version_id"],))
    draft = service.derive_contract_version(
        str(source["version_id"]),
        1,
        {"transport": "LOOPBACK_HTTP", "input_slots": {"FIRST_FRAME": {"min": 1, "max": 1}, "MOTION_REFERENCE": {"min": 0, "max": 1}}},
        {"seed": {"determinism": "EXPLICIT"}, "capabilities": {
            "extend": {"support": "UNSUPPORTED", "required_inputs": []},
            "V2V": {"support": "UNSUPPORTED", "required_inputs": []},
            "reference": {"support": "PROMPT_FALLBACK", "required_inputs": ["MOTION_REFERENCE"], "prompt_fallback": True},
            "motion": {"support": "NATIVE", "required_inputs": []},
        }},
        {"media_kind": "VIDEO", "container": "mp4"},
        {"gpu_heavy_concurrency": 1, "worker_policy": "ONE_H3_WORKER_ONE_GPU_TASK"},
    )
    return draft


def test_capability_compatibility_matrix_passes_without_runtime(workspace, database) -> None:
    service = ProfileService(database, workspace.manifest_path)
    draft = _complete(service, workspace, database)
    result = service.validate_compatibility(str(draft["id"]))
    assert result["status"] == "PASS"
    assert result["runtime_contacted"] is False
    assert result["network_contacted"] is False
    assert len(result["checks"]) == 9


def test_capability_compatibility_rejects_missing_reference_input_and_bad_fallback(workspace, database) -> None:
    service = ProfileService(database, workspace.manifest_path)
    source = service.sync_manifest()["profiles"][0]
    draft = service.derive_contract_version(
        str(source["version_id"]), 1,
        {"transport": "LOOPBACK_HTTP", "input_slots": {}},
        {"seed": {"determinism": "EXPLICIT"}, "capabilities": {
            "extend": {"support": "PROMPT_FALLBACK", "required_inputs": []},
            "V2V": {"support": "UNSUPPORTED", "required_inputs": []},
            "reference": {"support": "PROMPT_FALLBACK", "required_inputs": ["MISSING"]},
            "motion": {"support": "NATIVE", "required_inputs": []},
        }},
        {"media_kind": "VIDEO"}, {"gpu_heavy_concurrency": 1},
    )
    result = service.validate_compatibility(str(draft["id"]))
    assert result["status"] == "FAIL"
    failed = {item["code"] for item in result["checks"] if not item["passed"]}
    assert {"CAPABILITY_EXTEND", "CAPABILITY_REFERENCE"} <= failed


def test_retire_rejects_bound_or_job_referenced_version(workspace, database) -> None:
    service = ProfileService(database, workspace.manifest_path)
    source = service.sync_manifest()["profiles"][0]
    project = ProjectService(database, workspace.projects_root).create_project(
        code="compat_retire", title="compat retire", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    with database.transaction() as connection:
        connection.execute("UPDATE execution_profile_versions SET status='PUBLISHED' WHERE id=?", (source["version_id"],))
    from local_drama.application.configuration import ConfigurationService
    ConfigurationService(database).bind_profile(str(project["id"]), str(source["capability"]), str(source["version_id"]))
    with pytest.raises(DomainRuleError) as raised:
        service.retire_version(str(source["version_id"]))
    assert raised.value.code == "PROFILE_VERSION_IN_USE"
