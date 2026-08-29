from __future__ import annotations

import json

import pytest

from local_drama.application.job_resources import GpuRuntime, gpu_runtime_for_job
from local_drama.application.jobs import JobService
from local_drama.application.worker import LocalMediaWorker
from local_drama.domain.errors import DomainRuleError
from local_drama.model_platform.application.assignment_catalog import CapabilityAssignmentCatalogService
from local_drama.model_platform.application.business_selection_rollouts import (
    BusinessSelectionRolloutRequest,
    BusinessSelectionRolloutService,
)
from local_drama.model_platform.application.business_selection_shadow import BusinessSelectionShadowService
from local_drama.model_platform.application.capability_resolution import (
    CapabilityAssignmentRequest,
    CapabilityAssignmentService,
    CapabilityScopeContext,
)
from local_drama.model_platform.application.execution_handlers import ExecutionHandlerDescriptor, ExecutionHandlerRegistry
from local_drama.model_platform.application.execution_job_links import ExecutionJobLink, ExecutionJobLinkService
from local_drama.model_platform.application.execution_planning import ExecutionPlanningService, ExecutionPreviewRequest
from local_drama.model_platform.application.execution_snapshots import ExecutionSnapshotDraft, ExecutionSnapshotService
from local_drama.model_platform.application.execution_submission import ExecutionSubmissionService
from local_drama.model_platform.application.generation_capability_configuration_facade import (
    GenerationCapabilityConfigurationFacade,
)
from local_drama.model_platform.application.parameters import ParameterSource, ResolvedParameter
from local_drama.model_platform.application.profile_publication import ProfilePublicationService, ProfileVersionDraft
from local_drama.model_platform.application.profile_version_crosswalks import (
    ProfileVersionCrosswalkRequest,
    ProfileVersionCrosswalkService,
)
from local_drama.model_platform.application.worker_execution_handlers import (
    WorkerExecutionHandlerDescriptor,
    WorkerExecutionHandlerRegistry,
)


def _published_profile(
    database,
    *,
    code: str,
    payload: dict[str, object],
    allowed_scopes: tuple[str, ...] = ("RUN",),
) -> str:
    with database.transaction() as connection:
        capability_id = connection.execute("SELECT id FROM mp_capability_definitions WHERE code='EMBEDDING_TEXT'").fetchone()[0]
        connection.execute(
            """INSERT OR IGNORE INTO mp_compute_nodes (id,code,display_name,fingerprint,host_json,last_seen_at,created_at,updated_at)
            VALUES ('resolution-node','resolution-node','Resolution Node','resolution-node-fingerprint','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_runtime_installations (id,node_id,code,kind,owner_mode,display_name,created_at,updated_at)
            VALUES ('resolution-runtime','resolution-node','pytorch','PYTORCH_PROCESS','SERVICE_MANAGED','PyTorch',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_runtime_installation_versions
            (id,runtime_installation_id,version_no,adapter_code,adapter_version,transport,configuration_json,fingerprint,status,created_at,updated_at)
            VALUES ('resolution-runtime-v1','resolution-runtime',1,'pytorch.embedding.qwen3','v1','LOCAL_PROCESS','{}','resolution-runtime-fingerprint','ACTIVE',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_model_families (id,code,title,vendor,license_json,created_at,updated_at)
            VALUES ('resolution-family','resolution-family','Resolution test model',NULL,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_model_releases
            (id,family_id,code,upstream_id,revision,format,quantization,metadata_json,created_at,updated_at)
            VALUES ('resolution-release','resolution-family','resolution-release','resolution-release','v1','TEST',NULL,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_runtime_model_installations
            (id,release_id,runtime_installation_version_id,native_locator,install_state,metadata_json,created_at,updated_at)
            VALUES ('resolution-runtime-model','resolution-release','resolution-runtime-v1','resolution-native','READY','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_capability_offerings
            (id,runtime_model_installation_id,capability_definition_id,native_metadata_json,validation_status,created_at,updated_at)
            VALUES ('resolution-offering','resolution-runtime-model',?,'{}','SMOKE_PASSED',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (capability_id,),
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_validation_runs
            (id,target_kind,target_id,validation_kind,status,result_json,started_at,finished_at,created_at,updated_at)
            VALUES ('resolution-source-smoke','CAPABILITY_OFFERING','resolution-offering','CAPABILITY_SMOKE','SMOKE_PASSED','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_validation_evidence
            (id,validation_run_id,kind,content_hash,payload_json,artifact_ref,created_at,updated_at)
            VALUES ('resolution-source-evidence','resolution-source-smoke','CAPABILITY_SMOKE','resolution-hash','{}',NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_parameter_contract_versions
            (id,capability_definition_id,version_no,schema_json,ui_schema_json,content_hash,created_at,updated_at)
            VALUES ('resolution-parameter-v1',?,1,
                    '{"type":"object","properties":{"max_length":{"type":"integer","minimum":1,"default":4096}}}',
                    ?,
                    'resolution-parameter-hash',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (capability_id, json.dumps({"properties": {"max_length": {"scopes": list(allowed_scopes)}}})),
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_adapter_binding_contract_versions
            (id,runtime_kind,adapter_code,version_no,binding_json,content_hash,created_at,updated_at)
            VALUES ('resolution-binding-v1','PYTORCH_PROCESS','pytorch.embedding.qwen3',1,'{}','resolution-binding-hash',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO mp_resource_policy_versions
            (id,code,version_no,policy_json,content_hash,created_at,updated_at)
            VALUES ('resolution-resource-v1','gpu-heavy',1,'{}','resolution-resource-hash',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
    service = ProfilePublicationService(database)
    created = service.create_candidate(
        ProfileVersionDraft(
            profile_code=code,
            profile_title=code,
            capability_definition_id=str(capability_id),
            runtime_installation_version_id="resolution-runtime-v1",
            parameter_contract_version_id="resolution-parameter-v1",
            adapter_binding_contract_version_id="resolution-binding-v1",
            resource_policy_version_id="resolution-resource-v1",
            payload={"runtime_model_installation_ids": ["resolution-runtime-model"], **payload},
        )
    )
    validation = service.record_validation(
        created.profile_version_id,
        validation_kind="PROFILE_SMOKE",
        status="SMOKE_PASSED",
        result={"payload_hash": created.payload_hash, "source_capability_validation_run_id": "resolution-source-smoke"},
        evidence={"status": "pass"},
    )
    service.publish(created.profile_version_id, validation_run_id=validation.validation_run_id, reason="test")
    return created.profile_version_id


def _seed_scope_hierarchy(database, project_id: str = "project-1") -> dict[str, str]:
    """Create one real project lineage for polymorphic V2 scope tests."""
    suffix = project_id.removeprefix("project-")
    ids = {
        "project": project_id,
        "episode": f"episode-{suffix}",
        "shot": f"shot-{suffix}",
        "character": f"character-{suffix}",
    }
    season_id = f"season-{suffix}"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO projects (id,code,title,status,template_version,root_rel)
            VALUES (?,?,?,'ACTIVE','v2',?)""",
            (ids["project"], f"scope-{suffix}", f"Scope {suffix}", f"projects/scope-{suffix}"),
        )
        connection.execute(
            "INSERT INTO seasons (id,project_id,number,display_order,code) VALUES (?,?,1,1,?)",
            (season_id, ids["project"], f"S{suffix}"),
        )
        connection.execute(
            """INSERT INTO episodes
            (id,season_id,number,display_order,code,narrative_status,production_status,target_duration_ms)
            VALUES (?,?,1,1,?,'DRAFT','DRAFT',60000)""",
            (ids["episode"], season_id, f"E{suffix}"),
        )
        connection.execute(
            """INSERT INTO shots (id,episode_id,code,order_key,target_duration_ms,shot_type,status)
            VALUES (?,?,?,'1',1000,'MEDIUM','DRAFT')""",
            (ids["shot"], ids["episode"], f"SH{suffix}"),
        )
        connection.execute(
            """INSERT INTO story_assets
            (id,project_id,kind,code,name,created_at,updated_at,created_by)
            VALUES (?,?,'CHARACTER',?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test')""",
            (ids["character"], ids["project"], f"CHAR{suffix}", f"Character {suffix}"),
        )
    return ids


def test_capability_resolution_uses_scope_precedence_and_auto_inherits(database) -> None:
    _seed_scope_hierarchy(database)
    first = _published_profile(database, code="embedding-default", payload={"model": "first"})
    second = _published_profile(database, code="embedding-project", payload={"model": "second"})
    assignments = CapabilityAssignmentService(database)
    assignments.put(CapabilityAssignmentRequest("SYSTEM", "", "EMBEDDING_TEXT", "EXPLICIT", first))
    assignments.put(CapabilityAssignmentRequest("PROJECT", "project-1", "EMBEDDING_TEXT", "EXPLICIT", second))

    project = assignments.resolve("EMBEDDING_TEXT", CapabilityScopeContext(project_id="project-1"))
    assert project.execution_profile_version_id == second
    assert project.resolution_reason == "EXPLICIT_ASSIGNMENT"

    assignments.put(CapabilityAssignmentRequest("SHOT", "shot-1", "EMBEDDING_TEXT", "AUTO"))
    inherited = assignments.resolve(
        "EMBEDDING_TEXT", CapabilityScopeContext(project_id="project-1", shot_id="shot-1")
    )
    assert inherited.execution_profile_version_id == second
    assert [item["scope_type"] for item in inherited.assignment_chain] == ["SHOT", "PROJECT"]

    assignments.put(CapabilityAssignmentRequest("SHOT", "shot-1", "EMBEDDING_TEXT", "EXPLICIT", first))
    shot = assignments.resolve("EMBEDDING_TEXT", CapabilityScopeContext(project_id="project-1", shot_id="shot-1"))
    assert shot.execution_profile_version_id == first


def test_capability_resolution_falls_back_to_latest_published_profile(database) -> None:
    profile = _published_profile(database, code="embedding-default", payload={"model": "first"})

    resolved = CapabilityAssignmentService(database).resolve("EMBEDDING_TEXT", CapabilityScopeContext())

    assert resolved.execution_profile_version_id == profile
    assert resolved.resolution_reason == "LATEST_PUBLISHED_DEFAULT"


def test_assignment_validates_profile_bound_scope_overrides_and_preview_preserves_provenance(database) -> None:
    _seed_scope_hierarchy(database)
    assignments = CapabilityAssignmentService(database)
    profile = _published_profile(
        database,
        code="embedding-assignment-override",
        payload={"allowed_override_fields": ["max_length"]},
        allowed_scopes=("PROJECT", "RUN"),
    )

    assignments.put(CapabilityAssignmentRequest(
        "PROJECT", "project-1", "EMBEDDING_TEXT", "EXPLICIT", profile, {"max_length": 2048},
        "项目知识索引长度已审核", "release-operator",
    ))
    resolution = assignments.resolve("EMBEDDING_TEXT", CapabilityScopeContext(project_id="project-1"))
    assert resolution.assignment_overrides == {"max_length": 2048}
    assert resolution.assignment_override_scope == "PROJECT"

    preview = ExecutionPlanningService(database).preview(ExecutionPreviewRequest(
        capability_code="EMBEDDING_TEXT",
        scope=CapabilityScopeContext(project_id="project-1"),
        semantic_inputs={"document_id": "document-1"},
        run_overrides={},
    ))
    assert preview.resolved_parameters["max_length"].value == 2048
    assert preview.resolved_parameters["max_length"].source is ParameterSource.PROJECT_OVERRIDE

    assignments.put(CapabilityAssignmentRequest(
        "PROJECT", "project-1", "EMBEDDING_TEXT", "EXPLICIT", profile, {"max_length": 3072},
        "项目索引策略第二次审核", "release-operator",
    ))
    with database.connect() as connection:
        versions = connection.execute(
            "SELECT version_no,values_json FROM mp_scope_override_set_versions ORDER BY version_no"
        ).fetchall()
        assignment = connection.execute(
            "SELECT override_json,override_set_version_id FROM mp_capability_assignments WHERE scope_type='PROJECT'"
        ).fetchone()
    assert [(int(row["version_no"]), str(row["values_json"])) for row in versions] == [
        (1, '{"max_length":2048}'),
        (2, '{"max_length":3072}'),
    ]
    assert assignment["override_json"] == "{}"
    assert assignment["override_set_version_id"] is not None

    with pytest.raises(DomainRuleError) as raised:
        assignments.put(
            CapabilityAssignmentRequest("SYSTEM", "", "EMBEDDING_TEXT", "AUTO", overrides={"batch_size": 8})
        )

    assert raised.value.code == "MP_ASSIGNMENT_AUTO_OVERRIDE_FORBIDDEN"

    with pytest.raises(DomainRuleError) as unknown_field:
        assignments.put(CapabilityAssignmentRequest(
            "PROJECT", "project-1", "EMBEDDING_TEXT", "EXPLICIT", profile, {"unknown": 1},
            "非法字段验证", "release-operator",
        ))
    assert unknown_field.value.code == "MP_PARAMETER_UNKNOWN"


def test_assignment_scope_targets_must_exist_and_resolution_context_must_share_one_project(database) -> None:
    first = _seed_scope_hierarchy(database)
    second = _seed_scope_hierarchy(database, "project-2")
    assignments = CapabilityAssignmentService(database)

    for scope_type, key in (("PROJECT", "project"), ("EPISODE", "episode"), ("SHOT", "shot"), ("CHARACTER", "character")):
        assignments.put(CapabilityAssignmentRequest(scope_type, first[key], "EMBEDDING_TEXT", "AUTO"))

    with pytest.raises(DomainRuleError) as missing:
        assignments.put(CapabilityAssignmentRequest("PROJECT", "project-missing", "EMBEDDING_TEXT", "AUTO"))
    assert missing.value.code == "MP_ASSIGNMENT_SCOPE_NOT_FOUND"

    with pytest.raises(DomainRuleError) as inconsistent:
        assignments.resolve(
            "EMBEDDING_TEXT",
            CapabilityScopeContext(project_id=first["project"], episode_id=second["episode"]),
        )
    assert inconsistent.value.code == "MP_ASSIGNMENT_SCOPE_CONTEXT_MISMATCH"


def test_system_assignment_catalog_exposes_only_profile_allowed_safe_override_fields(database) -> None:
    profile = _published_profile(
        database,
        code="embedding-system-assignment",
        payload={"allowed_override_fields": ["max_length"]},
        allowed_scopes=("SYSTEM", "RUN"),
    )
    CapabilityAssignmentService(database).put(CapabilityAssignmentRequest(
        "SYSTEM", "", "EMBEDDING_TEXT", "EXPLICIT", profile, {"max_length": 2048},
        "系统向量索引参数已审核", "release-operator",
    ))

    item = next(
        item for item in CapabilityAssignmentCatalogService(database).list_system()
        if item["capability_code"] == "EMBEDDING_TEXT"
    )

    assert item["assignment"] == {
        "resolution_mode": "EXPLICIT",
        "execution_profile_version_id": profile,
        "revision": 1,
        "overrides": {"max_length": 2048},
        "has_unrenderable_override": False,
    }
    selected = next(option for option in item["profiles"] if option["profile_version_id"] == profile)
    assert selected["system_override_fields"] == [{
        "name": "max_length",
        "label": "max_length",
        "help": "",
        "schema": {"type": "integer", "minimum": 1, "default": 4096},
    }]


def test_system_assignment_catalog_hides_historical_camel_case_runtime_wiring_fields(database) -> None:
    profile = _published_profile(
        database,
        code="embedding-system-historical-wiring",
        payload={"allowed_override_fields": ["max_length"]},
        allowed_scopes=("SYSTEM", "RUN"),
    )
    with database.transaction() as connection:
        payload = json.loads(connection.execute(
            "SELECT payload_json FROM mp_execution_profile_versions WHERE id=?", (profile,)
        ).fetchone()["payload_json"])
        payload["allowed_override_fields"] = ["max_length", "baseUrl"]
        connection.execute(
            "UPDATE mp_execution_profile_versions SET payload_json=? WHERE id=?",
            (json.dumps(payload), profile),
        )
        connection.execute(
            """UPDATE mp_parameter_contract_versions SET schema_json=?,ui_schema_json=?
            WHERE id='resolution-parameter-v1'""",
            (
                json.dumps({"type": "object", "properties": {
                    "max_length": {"type": "integer", "minimum": 1, "default": 4096},
                    "baseUrl": {"type": "string"},
                }}),
                json.dumps({"properties": {
                    "max_length": {"scopes": ["SYSTEM", "RUN"]},
                    "baseUrl": {"scopes": ["SYSTEM"], "label": "历史 endpoint"},
                }}),
            ),
        )

    item = next(
        item for item in CapabilityAssignmentCatalogService(database).list_system()
        if item["capability_code"] == "EMBEDDING_TEXT"
    )
    selected = next(option for option in item["profiles"] if option["profile_version_id"] == profile)

    assert [field["name"] for field in selected["system_override_fields"]] == ["max_length"]


def test_scope_assignment_catalog_uses_verified_scope_and_declarative_override_fields_only(database) -> None:
    scopes = _seed_scope_hierarchy(database)
    profile = _published_profile(
        database,
        code="embedding-project-assignment-catalog",
        payload={"allowed_override_fields": ["max_length"]},
        allowed_scopes=("PROJECT", "RUN"),
    )

    items = CapabilityAssignmentCatalogService(database).list_scope("PROJECT", scopes["project"])
    item = next(item for item in items if item["capability_code"] == "EMBEDDING_TEXT")
    selected = next(option for option in item["profiles"] if option["profile_version_id"] == profile)

    assert selected["override_fields"] == [{
        "name": "max_length",
        "label": "max_length",
        "help": "",
        "schema": {"type": "integer", "minimum": 1, "default": 4096},
    }]
    with pytest.raises(DomainRuleError) as missing:
        CapabilityAssignmentCatalogService(database).list_scope("PROJECT", "project-missing")
    assert missing.value.code == "MP_ASSIGNMENT_SCOPE_NOT_FOUND"


def test_business_selection_rollout_is_audited_eligibility_not_an_execution_switch(database) -> None:
    service = BusinessSelectionRolloutService(database)

    default = service.get("quick-create", "IMAGE_CONCEPT", "PROJECT")
    assert default.as_dict() == {
        "id": None,
        "business_surface": "quick-create",
        "capability_code": "IMAGE_CONCEPT",
        "scope_type": "PROJECT",
        "state": "SHADOW",
        "approval_reason": None,
        "approved_by": None,
        "approved_at": None,
        "persisted": False,
        "execution_switched": False,
    }

    saved = service.put(BusinessSelectionRolloutRequest(
        business_surface="quick-create",
        capability_code="IMAGE_CONCEPT",
        scope_type="PROJECT",
        state="CUTOVER_APPROVED",
        approval_reason="已完成目标 surface 的双读与 Profile 合同复核",
        approved_by="release-operator",
    ))

    assert saved.state == "CUTOVER_APPROVED"
    assert saved.persisted is True
    assert saved.as_dict()["execution_switched"] is False
    assert service.get("quick-create", "IMAGE_CONCEPT", "PROJECT").as_dict()["state"] == "CUTOVER_APPROVED"
    with database.connect() as connection:
        audit = connection.execute(
            "SELECT action,metadata_redacted_json FROM audit_events WHERE subject_id=?", (saved.id,)
        ).fetchone()
    assert audit["action"] == "MP_BUSINESS_SELECTION_ROLLOUT_SET"
    assert '"state":"CUTOVER_APPROVED"' in str(audit["metadata_redacted_json"])

    with pytest.raises(DomainRuleError) as mismatched_surface:
        service.put(BusinessSelectionRolloutRequest(
            business_surface="quick-create",
            capability_code="TTS",
            scope_type="PROJECT",
            state="CUTOVER_APPROVED",
            approval_reason="不应允许跨业务页的 Capability",
            approved_by="release-operator",
        ))
    assert mismatched_surface.value.code == "MP_BUSINESS_ROLLOUT_SURFACE_CAPABILITY_MISMATCH"


def test_business_selection_shadow_compares_real_legacy_resolution_without_claiming_identity_equivalence(database) -> None:
    v2_profile = _published_profile(database, code="embedding-v2", payload={"model": "v2"})
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO projects
            (id,code,title,status,template_version,root_rel,created_at,updated_at,created_by,revision,schema_version)
            VALUES ('shadow-project','shadow-project','Shadow project','ACTIVE','v2','projects/shadow',
                    CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'v2')"""
        )
        connection.execute(
            """INSERT INTO execution_profiles
            (id,code,title,created_at,updated_at,created_by,revision,schema_version)
            VALUES ('shadow-legacy-profile','shadow-legacy-profile','Legacy embedding',
                    CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'v2')"""
        )
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id,execution_profile_id,version_no,capability,model_bundle_json,input_contract_json,
             parameter_schema_json,status,capability_json,created_at,updated_at,created_by,revision,schema_version)
            VALUES ('shadow-legacy-profile-v1','shadow-legacy-profile',1,'EMBEDDING_TEXT',
                    '{"defaults":{"max_length":4096},"override_schema":{"fields":{"max_length":{"type":"integer","minimum":1,"default":4096,"scopes":["RUN"]}}}}','{}','{}',
                    'PUBLISHED','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'v2')"""
        )
        connection.execute(
            """INSERT INTO project_profile_bindings
            (id,project_id,capability,execution_profile_version_id,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES ('shadow-legacy-binding','shadow-project','EMBEDDING_TEXT','shadow-legacy-profile-v1','ACTIVE',
                    CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'v2')"""
        )

    comparison = BusinessSelectionShadowService(database).compare(
        "EMBEDDING_TEXT", CapabilityScopeContext(project_id="shadow-project")
    ).as_dict()

    assert comparison["legacy_resolution"] == {
        "execution_profile_version_id": "shadow-legacy-profile-v1",
        "source": "AUTO",
        "resolution_mode": "AUTO",
        "blocked_reason": None,
        "ready": True,
    }
    assert comparison["legacy_project_binding"] == {
        "execution_profile_version_id": "shadow-legacy-profile-v1",
        "binding_status": "ACTIVE",
        "profile_status": "PUBLISHED",
    }
    assert comparison["v2_resolution"]["execution_profile_version_id"] == v2_profile
    assert comparison["profile_version_crosswalk"] is None
    assert comparison["parameter_contract_comparison"] == {
        "status": "SHAPE_MATCH",
        "matches": True,
        "legacy_field_count": 1,
        "v2_field_count": 1,
        "common_fields": ["max_length"],
        "legacy_only_fields": [],
        "v2_only_fields": [],
        "differences": {"type": [], "required": [], "scope": [], "constraint": [], "default": []},
        "unsafe_field_names": [],
        "values_exposed": False,
    }
    assert comparison["comparison"] == {
        "status": "BOTH_PRESENT_UNMAPPED",
        "comparable": False,
        "reason": "两侧均解析到已发布 Profile，但 V1/V2 ProfileVersion 身份不同，尚未建立版本迁移映射，不能宣称等价。",
        "profile_version_crosswalk_available": False,
    }

    crosswalk = ProfileVersionCrosswalkService(database).approve(
        ProfileVersionCrosswalkRequest(
            legacy_execution_profile_version_id="shadow-legacy-profile-v1",
            v2_execution_profile_version_id=v2_profile,
            approval_reason="已完成 Profile 合同人工核对",
            approved_by="release-operator",
        )
    )
    mapped = BusinessSelectionShadowService(database).compare(
        "EMBEDDING_TEXT", CapabilityScopeContext(project_id="shadow-project")
    ).as_dict()

    assert mapped["profile_version_crosswalk"] == crosswalk.as_dict()
    assert mapped["comparison"] == {
        "status": "MAPPED_EQUIVALENT",
        "comparable": True,
        "reason": "存在人工批准、能力一致且两侧均为已发布 Profile 的版本映射；仍须在业务 surface 验证参数 preview 后才能切换。",
        "profile_version_crosswalk_available": True,
    }

    before_approval = GenerationCapabilityConfigurationFacade(database).evaluate(
        business_surface="project-knowledge",
        capability_code="EMBEDDING_TEXT",
        scope=CapabilityScopeContext(project_id="shadow-project"),
    )
    assert before_approval.as_dict() == {
        "business_surface": "project-knowledge",
        "capability_code": "EMBEDDING_TEXT",
        "scope_type": "PROJECT",
        "legacy_execution_profile_version_id": "shadow-legacy-profile-v1",
        "v2_execution_profile_version_id": v2_profile,
        "rollout_state": "SHADOW",
        "decision": "LEGACY_ONLY",
        "blockers": ["ROLLOUT_NOT_APPROVED"],
        "execution_owner": "LEGACY_V1",
        "execution_switched": False,
    }
    BusinessSelectionRolloutService(database).put(BusinessSelectionRolloutRequest(
        business_surface="project-knowledge",
        capability_code="EMBEDDING_TEXT",
        scope_type="PROJECT",
        state="CUTOVER_APPROVED",
        approval_reason="双读 crosswalk、参数合同和 V2 execution preview 已复核",
        approved_by="release-operator",
    ))
    eligible = GenerationCapabilityConfigurationFacade(database).evaluate(
        business_surface="project-knowledge",
        capability_code="EMBEDDING_TEXT",
        scope=CapabilityScopeContext(project_id="shadow-project"),
    )
    assert eligible.as_dict() == {
        "business_surface": "project-knowledge",
        "capability_code": "EMBEDDING_TEXT",
        "scope_type": "PROJECT",
        "legacy_execution_profile_version_id": "shadow-legacy-profile-v1",
        "v2_execution_profile_version_id": v2_profile,
        "rollout_state": "CUTOVER_APPROVED",
        "decision": "CUTOVER_CANDIDATE",
        "blockers": [],
        "execution_owner": "LEGACY_V1",
        "execution_switched": False,
    }

    mismatched_v2 = _published_profile(database, code="embedding-v2-contract-mismatch", payload={"model": "v2"})
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO execution_profiles
            (id,code,title,created_at,updated_at,created_by,revision,schema_version)
            VALUES ('shadow-legacy-parameter-mismatch','shadow-legacy-parameter-mismatch','Legacy parameter mismatch',
                    CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'v2')"""
        )
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id,execution_profile_id,version_no,capability,model_bundle_json,input_contract_json,
             parameter_schema_json,status,capability_json,created_at,updated_at,created_by,revision,schema_version)
            VALUES ('shadow-legacy-parameter-mismatch-v1','shadow-legacy-parameter-mismatch',1,'EMBEDDING_TEXT',
                    '{"override_schema":{"fields":{"max_length":{"type":"integer","minimum":1,"default":1024,"scopes":["RUN"]}}}}','{}','{}',
                    'PUBLISHED','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'v2')"""
        )

    with pytest.raises(DomainRuleError) as raised:
        ProfileVersionCrosswalkService(database).approve(
            ProfileVersionCrosswalkRequest(
                legacy_execution_profile_version_id="shadow-legacy-parameter-mismatch-v1",
                v2_execution_profile_version_id=mismatched_v2,
                approval_reason="这条映射必须被参数门禁拒绝",
                approved_by="release-operator",
            )
        )

    assert raised.value.code == "MP_PROFILE_CROSSWALK_PARAMETER_CONTRACT_MISMATCH"


def test_execution_snapshot_freezes_published_profile_runtime_and_parameter_provenance(database) -> None:
    profile = _published_profile(database, code="embedding-default", payload={"model": "first"})
    snapshot_service = ExecutionSnapshotService(database)
    draft = ExecutionSnapshotDraft(
        capability_code="EMBEDDING_TEXT",
        execution_profile_version_id=profile,
        resolved_parameters={"max_length": ResolvedParameter(8192, ParameterSource.RUN_OVERRIDE, False)},
        semantic_inputs={"document_id": "document-1"},
        resolution={"reason": "EXPLICIT_ASSIGNMENT", "assignment_chain": [{"scope_type": "PROJECT"}]},
        network_policy={"mode": "OFFLINE_ONLY"},
    )

    first = snapshot_service.create(draft)
    second = snapshot_service.create(draft)

    assert first == second
    with database.connect() as connection:
        row = connection.execute(
            "SELECT resolved_parameters_json,parameter_provenance_json,network_policy_json,model_bindings_json FROM mp_execution_snapshots WHERE id=?",
            (first.id,),
        ).fetchone()
    assert row["resolved_parameters_json"] == '{"max_length":8192}'
    assert row["parameter_provenance_json"] == '{"max_length":{"locked":false,"source":"RUN_OVERRIDE"}}'
    assert row["network_policy_json"] == '{"mode":"OFFLINE_ONLY"}'
    assert row["model_bindings_json"] == '[{"model_release_code":"resolution-release","native_locator":"resolution-native","runtime_model_installation_id":"resolution-runtime-model"}]'


def test_execution_preview_freezes_v2_profile_resolution_and_rejects_runtime_inputs(database) -> None:
    _seed_scope_hierarchy(database)
    profile = _published_profile(
        database,
        code="embedding-preview",
        payload={
            "defaults": {"max_length": 4096},
            "allowed_override_fields": ["max_length"],
        },
    )
    planner = ExecutionPlanningService(database)
    preview = planner.preview(
        ExecutionPreviewRequest(
            capability_code="EMBEDDING_TEXT",
            scope=CapabilityScopeContext(project_id="project-1"),
            semantic_inputs={"document_id": "document-1"},
            run_overrides={"max_length": 8192},
        )
    )

    assert preview.execution_profile_version_id == profile
    assert preview.executable is True
    assert preview.resolved_parameters["max_length"].value == 8192
    assert preview.resolved_parameters["max_length"].source is ParameterSource.RUN_OVERRIDE
    planner.assert_submit_fresh(preview, preview.resolution_hash)

    with pytest.raises(DomainRuleError) as stale:
        planner.assert_submit_fresh(preview, "old-preview")
    assert stale.value.code == "MP_EXECUTION_RESOLUTION_STALE"

    with pytest.raises(DomainRuleError) as leaked_runtime_input:
        planner.preview(
            ExecutionPreviewRequest(
                capability_code="EMBEDDING_TEXT",
                scope=CapabilityScopeContext(),
                semantic_inputs={"path": "F:/AI_Models/private"},
                run_overrides={},
            )
        )
    assert leaked_runtime_input.value.code == "MP_EXECUTION_RUNTIME_INPUT_FORBIDDEN"


def test_worker_can_only_load_a_v2_snapshot_through_a_frozen_execution_job_link(database) -> None:
    profile = _published_profile(database, code="embedding-worker", payload={"model": "worker"})
    snapshot = ExecutionSnapshotService(database).create(
        ExecutionSnapshotDraft(
            capability_code="EMBEDDING_TEXT",
            execution_profile_version_id=profile,
            resolved_parameters={"max_length": ResolvedParameter(4096, ParameterSource.PROFILE_DEFAULT, False)},
            semantic_inputs={"document_id": "document-1"},
            resolution={"reason": "LATEST_PUBLISHED_DEFAULT"},
            network_policy={"mode": "LOCAL_ONLY"},
        )
    )
    job = JobService(database).create_job(
        None,
        "MODEL_PLATFORM_EXECUTION",
        "MODEL_PLATFORM_EXECUTION",
        snapshot.id,
        "CPU",
        {"execution_snapshot_id": snapshot.id, "content_hash": snapshot.content_hash},
        "mp-worker-link-1",
        subject_kind="MODEL_PLATFORM_EXECUTION",
        scope_kind="SYSTEM",
        stage_code="MODEL_PLATFORM_EXECUTION",
    )
    links = ExecutionJobLinkService(database)
    links.link(ExecutionJobLink(str(job["id"]), snapshot.id, "pytorch.embedding", "v1"))

    worker_snapshot = links.load_for_worker(str(job["id"]))

    assert worker_snapshot.execution_snapshot_id == snapshot.id
    assert worker_snapshot.capability_code == "EMBEDDING_TEXT"
    assert worker_snapshot.semantic_inputs == {"document_id": "document-1"}
    assert worker_snapshot.resolved_parameters == {"max_length": 4096}
    assert worker_snapshot.model_bindings == ({
        "runtime_model_installation_id": "resolution-runtime-model",
        "model_release_code": "resolution-release",
        "native_locator": "resolution-native",
    },)


def test_v2_handler_registry_rejects_published_profiles_without_an_exact_adapter_handler() -> None:
    registry = ExecutionHandlerRegistry(
        [ExecutionHandlerDescriptor("ollama.story", "v1", "LLM_STORY_PARSE", frozenset({"ollama.chat.v1"}), "CPU")]
    )
    assert registry.resolve("LLM_STORY_PARSE", "ollama.chat.v1").worker_channel == "CPU"
    with pytest.raises(DomainRuleError) as raised:
        registry.resolve("LLM_STORY_PARSE", "comfy.workflow.v1")
    assert raised.value.code == "MP_EXECUTION_HANDLER_UNAVAILABLE"


def test_execution_submission_atomically_freezes_preview_queues_job_and_replays(database) -> None:
    _published_profile(
        database,
        code="embedding-submit",
        payload={"defaults": {"max_length": 4096}, "allowed_override_fields": ["max_length"]},
    )
    planner = ExecutionPlanningService(database)
    initial = planner.preview(
        ExecutionPreviewRequest(
            capability_code="EMBEDDING_TEXT",
            scope=CapabilityScopeContext(),
            semantic_inputs={"document_id": "document-1"},
            run_overrides={"max_length": 8192},
        )
    )
    request = ExecutionPreviewRequest(
        capability_code="EMBEDDING_TEXT",
        scope=CapabilityScopeContext(),
        semantic_inputs={"document_id": "document-1"},
        run_overrides={"max_length": 8192},
        expected_resolution_hash=initial.resolution_hash,
    )
    service = ExecutionSubmissionService(
        database,
        ExecutionHandlerRegistry(
            [
                ExecutionHandlerDescriptor(
                    "pytorch.embedding.qwen3",
                    "v1",
                    "EMBEDDING_TEXT",
                    frozenset({"pytorch.embedding.qwen3"}),
                    "CPU",
                    "PYTORCH",
                )
            ]
        ),
    )

    linked: list[tuple[str, str]] = []
    submitted = service.submit(
        request,
        "mp-submit-1",
        after_linked_in_transaction=lambda _connection, job, snapshot: linked.append((str(job["id"]), snapshot.id)),
    )
    replay = service.submit(request, "mp-submit-1")

    assert submitted.job["type"] == "MODEL_PLATFORM_EXECUTION"
    assert submitted.job["channel"] == "CPU"
    assert submitted.job["input_snapshot"]["scheduler_runtime"] == "PYTORCH"
    assert linked == [(str(submitted.job["id"]), submitted.execution_snapshot_id)]
    assert gpu_runtime_for_job(submitted.job) is GpuRuntime.PYTORCH
    assert replay.job["id"] == submitted.job["id"]
    assert replay.idempotent_replay is True
    worker_snapshot = ExecutionJobLinkService(database).load_for_worker(str(submitted.job["id"]))
    assert worker_snapshot.execution_snapshot_id == submitted.execution_snapshot_id
    assert worker_snapshot.handler_code == "pytorch.embedding.qwen3"
    assert worker_snapshot.resolved_parameters == {"max_length": 8192}


def test_worker_executes_only_the_linked_v2_snapshot_with_an_exact_implementation(workspace, database) -> None:
    profile = _published_profile(database, code="embedding-worker-implementation", payload={"model": "worker"})
    snapshot = ExecutionSnapshotService(database).create(
        ExecutionSnapshotDraft(
            capability_code="EMBEDDING_TEXT",
            execution_profile_version_id=profile,
            resolved_parameters={"max_length": ResolvedParameter(4096, ParameterSource.PROFILE_DEFAULT, False)},
            semantic_inputs={"document_id": "document-1"},
            resolution={"reason": "LATEST_PUBLISHED_DEFAULT"},
            network_policy={"mode": "LOCAL_ONLY"},
        )
    )
    job = JobService(database, workspace).create_job(
        None,
        "MODEL_PLATFORM_EXECUTION",
        "MODEL_PLATFORM_EXECUTION",
        snapshot.id,
        "CPU",
        {"execution_snapshot_id": snapshot.id, "content_hash": snapshot.content_hash},
        "mp-worker-implementation-1",
        subject_kind="MODEL_PLATFORM_EXECUTION",
        scope_kind="SYSTEM",
        stage_code="MODEL_PLATFORM_EXECUTION",
    )
    ExecutionJobLinkService(database).link(
        ExecutionJobLink(str(job["id"]), snapshot.id, "pytorch.embedding.qwen3", "v1")
    )

    def run_embedding(worker_snapshot, output_root):
        assert worker_snapshot.semantic_inputs == {"document_id": "document-1"}
        result = output_root / "embedding-result.json"
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text('{"dimensions":1024}', encoding="utf-8")
        return "JSON_RESULT", result.relative_to(workspace.work_root).as_posix()

    worker = LocalMediaWorker(
        database,
        workspace,
        model_execution_handlers=WorkerExecutionHandlerRegistry(
            [
                WorkerExecutionHandlerDescriptor(
                    "pytorch.embedding.qwen3",
                    "v1",
                    "EMBEDDING_TEXT",
                    frozenset({"pytorch.embedding.qwen3"}),
                    run_embedding,
                )
            ]
        ),
    )
    result = worker.run_once("model-platform-worker", ["CPU"])

    assert result is not None
    assert result.get("error") is None, result
    assert result["result"]["job_state"] == "SUCCEEDED"
    assert result["artifact"]["kind"] == "JSON_RESULT"
