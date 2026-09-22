"""Legacy Comfy jobs honour a Profile's own recorded ComfyUI runtime.

The existing production pages read the V1 Profile chain, whose executor used to
address one process-wide ``comfy_base_url``.  That made it impossible to run a
Profile whose graph needs nodes the production server does not have -- notably
Qwen-Image-2.1 -- without upgrading the shared runtime.

The executor now resolves a *recorded* runtime per Job and falls back to the
process-wide client when none is recorded, so existing routes are untouched.
These tests pin both halves of that contract, including the fail-closed paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_drama.application.comfy_jobs import ComfyGenerationService
from local_drama.application.jobs import JobService
from local_drama.domain.errors import DomainRuleError

_LOOPBACK_RUNTIME_VERSION = "11111111-1111-4111-8111-111111111111"
_RUNTIME_INSTALLATION = "44444444-4444-4444-8444-444444444444"
_COMPUTE_NODE = "66666666-6666-4666-8666-666666666666"
_PROFILE_VERSION = "22222222-2222-4222-8222-222222222222"
_PROFILE_ID = "33333333-3333-4333-8333-333333333333"
_STAMP = "2026-09-22T00:00:00+00:00"


def _insert_runtime_version(connection, configuration: dict, *, version_id: str = _LOOPBACK_RUNTIME_VERSION) -> None:
    connection.execute(
        """INSERT OR REPLACE INTO mp_compute_nodes
           (id,code,display_name,fingerprint,host_json,last_seen_at,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (_COMPUTE_NODE, "routing-test-node", "路由测试节点", "routing-test", "{}", _STAMP, _STAMP, _STAMP),
    )
    connection.execute(
        """INSERT OR REPLACE INTO mp_runtime_installations
           (id,node_id,code,kind,owner_mode,display_name,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (_RUNTIME_INSTALLATION, _COMPUTE_NODE, "comfyui-routing-test", "COMFYUI", "SERVICE_MANAGED", "路由测试运行时", _STAMP, _STAMP),
    )
    connection.execute(
        """INSERT OR REPLACE INTO mp_runtime_installation_versions
           (id,runtime_installation_id,version_no,adapter_code,adapter_version,transport,configuration_json,fingerprint,status,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            version_id, _RUNTIME_INSTALLATION, 1, "comfy.workflow.v1", "v1", "LOOPBACK_HTTP",
            json.dumps(configuration), "f" * 64, "ACTIVE", _STAMP, _STAMP,
        ),
    )


def _insert_profile_version(connection, *, runtime_version_id: str | None) -> None:
    connection.execute(
        """INSERT OR REPLACE INTO execution_profiles (id,code,title,created_at,updated_at,created_by,revision,schema_version)
           VALUES (?,?,?,?,?,'test',1,'v2')""",
        (_PROFILE_ID, "test-routing-profile", "路由测试", "2026-09-22T00:00:00+00:00", "2026-09-22T00:00:00+00:00"),
    )
    connection.execute(
        """INSERT OR REPLACE INTO execution_profile_versions
           (id,execution_profile_id,version_no,capability,runtime_version_id,workflow_version_id,model_bundle_json,
            input_contract_json,parameter_schema_json,output_contract_json,resource_policy_json,status,manifest_sha256,
            capability_json,worker_policy,created_at,updated_at,created_by,revision,schema_version)
           VALUES (?,?,1,'IMAGE_CONCEPT',?,NULL,'{}','{}','{}','{}','{}','PUBLISHED',NULL,'{}',NULL,?,?,'test',1,'v2')""",
        (_PROFILE_VERSION, _PROFILE_ID, runtime_version_id, "2026-09-22T00:00:00+00:00", "2026-09-22T00:00:00+00:00"),
    )


def _snapshot(profile_version_id: str | None) -> dict:
    return {"workflow_version_id": "w", "execution_snapshot": {"profile_version_id": profile_version_id}}


def test_job_without_a_recorded_runtime_uses_the_process_default(workspace, database) -> None:
    service = ComfyGenerationService(database, workspace)
    service._bind_runtime_from_snapshot(_snapshot(None))
    assert service.comfy.base_url == workspace.comfy_base_url


def test_job_without_a_profile_snapshot_uses_the_process_default(workspace, database) -> None:
    service = ComfyGenerationService(database, workspace)
    service._bind_runtime_from_snapshot({"workflow_version_id": "w"})
    assert service.comfy.base_url == workspace.comfy_base_url


def test_profile_runtime_overrides_endpoint_and_output_root(workspace, database) -> None:
    output_root = workspace.work_root / "comfy-qwen21" / "output"
    with database.transaction() as connection:
        _insert_runtime_version(connection, {"base_url": "http://127.0.0.1:8189", "output_root": str(output_root)})
        _insert_profile_version(connection, runtime_version_id=_LOOPBACK_RUNTIME_VERSION)

    service = ComfyGenerationService(database, workspace)
    service._bind_runtime_from_snapshot(_snapshot(_PROFILE_VERSION))
    assert service.comfy.base_url == "http://127.0.0.1:8189"
    assert Path(str(service.comfy.output_root)) == output_root.resolve()


def test_unrecorded_runtime_id_falls_back_to_the_default(workspace, database) -> None:
    """A dangling runtime_version_id must not silently point at another server."""

    with database.transaction() as connection:
        _insert_profile_version(connection, runtime_version_id="99999999-9999-4999-8999-999999999999")
    service = ComfyGenerationService(database, workspace)
    service._bind_runtime_from_snapshot(_snapshot(_PROFILE_VERSION))
    assert service.comfy.base_url == workspace.comfy_base_url


def test_non_loopback_runtime_is_refused(workspace, database) -> None:
    with database.transaction() as connection:
        _insert_runtime_version(connection, {"base_url": "http://10.0.0.5:8188"})
        _insert_profile_version(connection, runtime_version_id=_LOOPBACK_RUNTIME_VERSION)
    service = ComfyGenerationService(database, workspace)
    with pytest.raises(DomainRuleError) as blocked:
        service._bind_runtime_from_snapshot(_snapshot(_PROFILE_VERSION))
    assert blocked.value.code == "COMFY_PROFILE_RUNTIME_NOT_LOOPBACK"


def test_output_root_outside_work_root_is_refused(workspace, database, tmp_path) -> None:
    with database.transaction() as connection:
        _insert_runtime_version(connection, {"base_url": "http://127.0.0.1:8189", "output_root": str(tmp_path / "elsewhere")})
        _insert_profile_version(connection, runtime_version_id=_LOOPBACK_RUNTIME_VERSION)
    service = ComfyGenerationService(database, workspace)
    with pytest.raises(DomainRuleError) as blocked:
        service._bind_runtime_from_snapshot(_snapshot(_PROFILE_VERSION))
    assert blocked.value.code == "COMFY_PROFILE_RUNTIME_OUTPUT_OUTSIDE_WORK_ROOT"


def test_binding_is_recomputed_per_job(workspace, database) -> None:
    """A 2.1 Job must not leave the next Job pointed at 8189."""

    output_root = workspace.work_root / "comfy-qwen21" / "output"
    with database.transaction() as connection:
        _insert_runtime_version(connection, {"base_url": "http://127.0.0.1:8189", "output_root": str(output_root)})
        _insert_profile_version(connection, runtime_version_id=_LOOPBACK_RUNTIME_VERSION)
    service = ComfyGenerationService(database, workspace)
    service._bind_runtime_from_snapshot(_snapshot(_PROFILE_VERSION))
    assert service.comfy.base_url == "http://127.0.0.1:8189"
    service._bind_runtime_from_snapshot(_snapshot(None))
    assert service.comfy.base_url == workspace.comfy_base_url


def test_bind_runtime_from_job_reads_the_frozen_snapshot(workspace, database) -> None:
    output_root = workspace.work_root / "comfy-qwen21" / "output"
    with database.transaction() as connection:
        _insert_runtime_version(connection, {"base_url": "http://127.0.0.1:8189", "output_root": str(output_root)})
        _insert_profile_version(connection, runtime_version_id=_LOOPBACK_RUNTIME_VERSION)
    job = JobService(database, workspace).create_job(
        None,
        "PROFILE_EVIDENCE_PROBE",
        "EXECUTION_PROFILE_VERSION",
        _PROFILE_VERSION,
        "GPU_H3",
        _snapshot(_PROFILE_VERSION),
        "routing-test",
        execution_profile_version_id=_PROFILE_VERSION,
        priority=1,
        max_attempts=1,
    )
    service = ComfyGenerationService(database, workspace)
    service._bind_runtime_from_job(str(job["id"]))
    assert service.comfy.base_url == "http://127.0.0.1:8189"
    service._bind_runtime_from_job(None)
    assert service.comfy.base_url == workspace.comfy_base_url
