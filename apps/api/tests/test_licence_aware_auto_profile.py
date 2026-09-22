"""A research-licensed model must never win automatic default selection.

Qwen-Image-2.1 Profiles are published so the existing creator and batch pages
can *offer* them, but automatic selection is production routing.  A Profile that
records the model it runs is therefore skipped while that model has no recorded
commercial authorization; explicit selection is unaffected.
"""

from __future__ import annotations

import json

from local_drama.infrastructure.database.generation_preference_repository import SqliteGenerationPreferenceRepository

_PROFILE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_VERSION = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
_STAMP = "2026-09-22T00:00:00+00:00"

# A graph that is unambiguously production scale, so only the licence can
# decide the outcome.
_PRODUCTION_GRAPH = {
    "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": 768, "height": 1376, "batch_size": 1}},
    "8": {"class_type": "KSampler", "inputs": {"steps": 40, "cfg": 1.0, "seed": 1}},
    "10": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x"}},
}


def _seed(connection, *, model_code: str | None, capability: str = "IMAGE_CHARACTER") -> None:
    connection.execute(
        "INSERT OR IGNORE INTO workflows (id,code,title,created_at,updated_at,created_by,revision,schema_version) VALUES ('w','licence-test','licence',?,?,'test',1,'v2')",
        (_STAMP, _STAMP),
    )
    connection.execute(
        """INSERT OR REPLACE INTO workflow_versions
           (id,workflow_id,version_no,content_hash,status,contract_json,content_json,package_rel_path,
            node_bindings_json,runtime_contract_json,created_at,updated_at,created_by,revision,schema_version)
           VALUES ('wv','w',1,'hash','PUBLISHED','{}',?,'p','{}','{}',?,?,'test',1,'v2')""",
        (json.dumps(_PRODUCTION_GRAPH), _STAMP, _STAMP),
    )
    bundle = {"workflow_version_id": "wv"}
    if model_code is not None:
        bundle["model_code"] = model_code
    connection.execute(
        "INSERT OR REPLACE INTO execution_profiles (id,code,title,created_at,updated_at,created_by,revision,schema_version) VALUES (?,?,?,?,?,'test',1,'v2')",
        (_PROFILE, "licence-test-profile", "许可测试", _STAMP, _STAMP),
    )
    connection.execute(
        """INSERT OR REPLACE INTO execution_profile_versions
           (id,execution_profile_id,version_no,capability,runtime_version_id,workflow_version_id,model_bundle_json,
            input_contract_json,parameter_schema_json,output_contract_json,resource_policy_json,status,manifest_sha256,
            capability_json,worker_policy,created_at,updated_at,created_by,revision,schema_version)
           VALUES (?,?,1,?,NULL,'wv',?,'{}','{}','{}','{}','PUBLISHED',NULL,'{}',NULL,?,?,'test',1,'v2')""",
        (_VERSION, _PROFILE, capability, json.dumps(bundle), _STAMP, _STAMP),
    )


def test_unlicensed_model_is_skipped_as_the_automatic_default(database) -> None:
    with database.transaction() as connection:
        _seed(connection, model_code="qwen-image-2.1-int8-convrot")
    with database.connect() as connection:
        assert SqliteGenerationPreferenceRepository(connection).auto_profile("IMAGE_CHARACTER") is None


def test_licensed_model_still_wins_the_automatic_default(database) -> None:
    with database.transaction() as connection:
        _seed(connection, model_code="qwen-image-2512-q5-k-m")
    with database.connect() as connection:
        selected = SqliteGenerationPreferenceRepository(connection).auto_profile("IMAGE_CHARACTER")
    assert selected is not None and str(selected["id"]) == _VERSION


def test_profile_without_a_recorded_model_keeps_previous_behaviour(database) -> None:
    """Profiles that never recorded a model must not become unmatchable."""

    with database.transaction() as connection:
        _seed(connection, model_code=None)
    with database.connect() as connection:
        selected = SqliteGenerationPreferenceRepository(connection).auto_profile("IMAGE_CHARACTER")
    assert selected is not None and str(selected["id"]) == _VERSION
