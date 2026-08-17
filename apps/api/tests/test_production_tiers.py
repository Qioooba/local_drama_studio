"""P1-7 production tiers: table integrity, resolve_tier, tier-aware builders, API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from local_drama.application.h3_workflows import PRODUCTION_TIERS, H3WorkflowFactory
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app

TIER_CODES = ("FAST", "DRAFT", "SCREEN", "PRODUCTION", "MASTER")


def test_production_tiers_table_completeness_and_grid() -> None:
    assert list(PRODUCTION_TIERS) == list(TIER_CODES)
    for code in TIER_CODES:
        tier = PRODUCTION_TIERS[code]
        # frames must sit on the model's 17k+5 grid inside the trained range
        assert (tier["frames"] - 5) % 17 == 0, code
        assert 107 <= tier["frames"] <= 362, code
        assert tier["default_takes"] >= 1, code
        assert set(tier["resolution"]) == {"9:16", "16:9"}, code
        for size in tier["resolution"].values():
            assert size[0] % 32 == 0 and size[1] % 32 == 0, code
            assert size == (480, 832) or size == (864, 480), code
        assert 0.95 <= tier["denoise"] <= 1.0, code
        assert tier["steps"] >= 1, code
        assert tier["cfg"] > 0, code
    # openclaw take-count experience: 2/4/6/8/12
    assert [PRODUCTION_TIERS[code]["default_takes"] for code in TIER_CODES] == [2, 4, 6, 8, 12]
    # frame progression: 107/107/124, then the nearest 17k+5 grid values for
    # the ~7s/~8.6s production/master shots (171/207 are off-grid)
    assert [PRODUCTION_TIERS[code]["frames"] for code in TIER_CODES] == [107, 107, 124, 175, 209]


def test_resolve_tier_rejects_unknown_tier_and_aspect(workspace) -> None:
    factory = H3WorkflowFactory(workspace)
    with pytest.raises(DomainRuleError) as raised:
        factory.resolve_tier("ULTRA", "9:16")
    assert raised.value.code == "H3_TIER_UNSUPPORTED"
    with pytest.raises(DomainRuleError) as raised:
        factory.resolve_tier("", "9:16")
    assert raised.value.code == "H3_TIER_UNSUPPORTED"
    with pytest.raises(DomainRuleError) as raised:
        factory.resolve_tier("FAST", "4:3")
    assert raised.value.code == "H3_ASPECT_RATIO_UNSUPPORTED"


def test_resolve_tier_resolves_geometry_and_metadata(workspace) -> None:
    factory = H3WorkflowFactory(workspace)
    draft = factory.resolve_tier("DRAFT", "9:16")
    assert draft["frames"] == 107
    assert (draft["width"], draft["height"]) == (480, 832)
    assert draft["default_takes"] == 4
    assert draft["duration_seconds"] == pytest.approx(107 / 24.0, abs=0.001)
    assert draft["resolution"]["16:9"] == (864, 480)
    # case-insensitive code and auto aspect
    auto = factory.resolve_tier("fast", "auto")
    assert (auto["width"], auto["height"]) == (480, 832)
    wide = factory.resolve_tier("MASTER", "16:9")
    assert wide["frames"] == 209
    assert (wide["width"], wide["height"]) == (864, 480)


def test_build_t2va_with_tier_overrides_length_and_resolution(workspace) -> None:
    factory = H3WorkflowFactory(workspace)
    workflow = factory.build_t2va("tier shot", seed=1, duration_seconds=5.0, aspect_ratio="9:16", tier="PRODUCTION")
    h3 = workflow["8"]["inputs"]
    assert h3["length"] == 175  # duration-derived length (124) is overridden
    assert h3["width"] == 480
    assert h3["height"] == 832

    master_wide = factory.build_t2va("tier shot", seed=1, duration_seconds=5.0, aspect_ratio="16:9", tier="MASTER")
    assert master_wide["8"]["inputs"]["length"] == 209
    assert master_wide["8"]["inputs"]["width"] == 864
    assert master_wide["8"]["inputs"]["height"] == 480


def test_build_fl2va_with_tier_overrides_length_and_resolution(workspace) -> None:
    factory = H3WorkflowFactory(workspace)
    workflow = factory.build_fl2va("tier i2v", first_frame="keyframe.png", seed=1, duration_seconds=5.0, aspect_ratio="auto", tier="SCREEN")
    h3 = workflow["7"]["inputs"]
    assert h3["length"] == 124
    assert h3["width"] == 480
    assert h3["height"] == 832
    assert workflow["6"]["inputs"]["width"] == 480


def test_build_without_tier_keeps_legacy_behaviour(workspace) -> None:
    factory = H3WorkflowFactory(workspace)
    legacy = factory.build_t2va("legacy", seed=1, duration_seconds=4.0, aspect_ratio="9:16", sigma_points=2)
    assert legacy["8"]["inputs"]["length"] == 107
    assert legacy["8"]["inputs"]["width"] == 480
    assert legacy["8"]["inputs"]["height"] == 832
    assert legacy["7"]["inputs"]["steps"] == 2  # sigma_points still governs steps
    # tier=None must be byte-identical to the pre-P1-7 graph
    assert legacy == factory.build_t2va("legacy", seed=1, duration_seconds=4.0, aspect_ratio="9:16", sigma_points=2, tier=None)
    # duration validation is preserved even when a tier is present
    with pytest.raises(DomainRuleError) as raised:
        factory.build_t2va("tier shot", seed=1, duration_seconds=3.0, tier="FAST")
    assert raised.value.code == "H3_DURATION_INVALID"


def test_production_tiers_endpoint(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/production-tiers")
        assert response.status_code == 200
        payload = response.json()
        assert payload["default_tier"] == "DRAFT"
        assert [item["code"] for item in payload["items"]] == list(TIER_CODES)
        production = next(item for item in payload["items"] if item["code"] == "PRODUCTION")
        assert production["frames"] == 175
        assert production["resolution"]["9:16"] == [480, 832]
        assert production["resolution"]["16:9"] == [864, 480]
        assert production["default_takes"] == 8
        assert production["duration_seconds"] == pytest.approx(175 / 24.0, abs=0.001)


def test_h3_candidate_registration_accepts_tier(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.post(
            "/api/v1/workflow-packages:h3-candidate",
            json={"code": "p17_tier_api", "title": "P17 tier", "prompt": "tier shot", "seed": 7, "tier": "MASTER", "aspect_ratio": "16:9"},
        )
        assert response.status_code == 201
        version = response.json()["workflow_version"]
        assert version["workflow"]["8"]["inputs"]["length"] == 209
        assert version["workflow"]["8"]["inputs"]["width"] == 864
        assert version["workflow"]["8"]["inputs"]["height"] == 480
        # node ids are unchanged, so the semantic bindings stay valid
        assert version["node_bindings"]["FRAME_COUNT"] == {"node_id": "8", "input": "length"}
        assert version["node_bindings"]["PROMPT"] == {"node_id": "8", "input": "prompt"}

        i2v = client.post(
            "/api/v1/workflow-packages:h3-i2v-candidate",
            json={"code": "p17_tier_i2v", "title": "P17 tier i2v", "prompt": "tier i2v", "seed": 8, "tier": "SCREEN"},
        )
        assert i2v.status_code == 201
        assert i2v.json()["workflow_version"]["workflow"]["7"]["inputs"]["length"] == 124

        # invalid tier fails closed with H3_TIER_UNSUPPORTED and no package row
        bad = client.post(
            "/api/v1/workflow-packages:h3-candidate",
            json={"code": "p17_bad_tier", "title": "bad tier", "prompt": "x", "seed": 1, "tier": "NOPE"},
        )
        assert bad.status_code == 422
        assert bad.json()["error"]["code"] == "H3_TIER_UNSUPPORTED"
        with database.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM workflows WHERE code='p17_bad_tier'").fetchone()[0] == 0
