from __future__ import annotations

from scripts.audit_architecture_debt import audit


def test_video_upscale_adds_no_application_or_route_architecture_debt() -> None:
    categories = audit()["categories"]
    assert not [
        entry
        for entry in categories["concrete_database_dependencies"]
        if "/video_upscale/" in entry["file"]
    ]
    assert not [
        entry
        for entry in categories["cross_service_construction"]
        if "/video_upscale/" in entry["file"]
        or (entry["file"].endswith("application/worker.py") and entry["service"] == "VideoUpscalePlanService")
    ]
    assert not [
        entry
        for entry in categories["routes_without_response_model"]
        if entry["file"].endswith("api/routes/video_upscale.py")
        or entry["operation_id"] == "configureAndPublishModelPlatformNcnnVideoUpscale"
    ]
