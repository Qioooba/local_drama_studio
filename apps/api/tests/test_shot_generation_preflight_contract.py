from fastapi.testclient import TestClient

from local_drama.api.routes import shot_studio_v2
from local_drama.api.schemas.shot_studio import ShotGenerationPreflightResponse
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def test_video_preflight_response_accepts_resolved_production_spec_snapshot() -> None:
    response = ShotGenerationPreflightResponse.model_validate(
        {
            "preflight": {
                "shot_id": "shot-1",
                "shot_revision": 3,
                "intent_id": "intent-1",
                "status": "READY",
                "plan_hash": "a" * 64,
                "variant_plan_hash": "b" * 64,
                "recipe_hash": "c" * 64,
                "dependencies": {},
                "resource_estimate": {},
                "disk_gate": {},
                "blockers": [],
                "effective_configuration": {},
                "production_spec": {
                    "schema_version": "localdrama.production-spec-snapshot.v1",
                    "status": "READY",
                    "delivery": {
                        "aspect_ratio": "9:16",
                        "width": 1440,
                        "height": 2560,
                        "fps": {"numerator": 24, "denominator": 1},
                    },
                    "generation": {
                        "mode": "UPSCALE_COMPOSE",
                        "semantic_inputs": {"FPS": 24.0},
                        "actual": {"width": 480, "height": 832, "fps": 24.0},
                        "upscale": {
                            "enabled": True,
                            "required": True,
                            "stage": "COMPOSE_QC",
                            "executor": "builtin:ffmpeg",
                            "target": "PRESENTATION_SPEC",
                        },
                    },
                    "blockers": [],
                    "warnings": [],
                },
                "would_persist_variant": False,
                "would_create_job": False,
                "reproducibility": {},
            }
        }
    )

    assert response.preflight.production_spec is not None
    assert response.preflight.production_spec["generation"]["mode"] == "UPSCALE_COMPOSE"


def _request_payload() -> dict[str, object]:
    return {
        "operation": "BASE",
        "stage_code": "VIDEO",
        "intent_id": "intent-1",
        "variant_type": "BASE",
        "parent_variant_id": None,
        "branch_reason": "contract test",
        "profile_version_id": "profile-version-1",
        "parameter_set": {},
        "seed_policy": "EXPLICIT",
        "explicit_seed": 7,
        "bindings": [],
        "expected_shot_revision": 3,
    }


def _service_result() -> dict[str, object]:
    return {
        "shot_id": "shot-1",
        "shot_revision": 3,
        "intent_id": "intent-1",
        "status": "READY",
        "plan_hash": "a" * 64,
        "variant_plan_hash": "b" * 64,
        "recipe_hash": "c" * 64,
        "dependencies": {},
        "resource_estimate": {},
        "disk_gate": {},
        "blockers": [],
        "effective_configuration": {},
        "production_spec": {"status": "READY", "generation": {"mode": "UPSCALE_COMPOSE"}},
        "would_persist_variant": False,
        "would_create_job": False,
        "reproducibility": {},
    }


def test_preflight_route_serializes_service_production_spec_without_http_500(monkeypatch, workspace) -> None:
    class FakeGenerationService:
        def preflight_shot_base_variant(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            return _service_result()

    monkeypatch.setattr(shot_studio_v2, "generation_service", lambda _request: FakeGenerationService())

    with TestClient(create_app(workspace)) as client:
        response = client.post("/api/v2/shots/shot-1/generations:preflight", json=_request_payload())

    assert response.status_code == 200, response.text
    assert response.json()["preflight"]["production_spec"]["generation"]["mode"] == "UPSCALE_COMPOSE"


def test_preflight_route_keeps_resolver_blocker_as_structured_4xx(monkeypatch, workspace) -> None:
    class FakeGenerationService:
        def preflight_shot_base_variant(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            raise DomainRuleError(
                "PRODUCTION_SPEC_BLOCKED",
                "生产规格无法执行",
                {"production_spec": {"status": "BLOCKED"}},
            )

    monkeypatch.setattr(shot_studio_v2, "generation_service", lambda _request: FakeGenerationService())

    with TestClient(create_app(workspace)) as client:
        response = client.post("/api/v2/shots/shot-1/generations:preflight", json=_request_payload())

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "PRODUCTION_SPEC_BLOCKED"
