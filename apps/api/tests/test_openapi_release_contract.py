from __future__ import annotations

import json
from pathlib import Path

from local_drama.main import app

ROOT = Path(__file__).resolve().parents[3]


def test_committed_openapi_snapshot_matches_registered_routes() -> None:
    snapshot = json.loads((ROOT / "docs" / "openapi" / "openapi.json").read_text(encoding="utf-8"))
    assert snapshot == app.openapi()


def test_openapi_covers_0044_through_0047_contracts() -> None:
    paths = app.openapi()["paths"]
    expected_operations = {
        ("/api/v1/episodes/{episode_id}/shot-groups", "get"),
        ("/api/v1/episodes/{episode_id}/shot-groups", "post"),
        ("/api/v1/shot-groups/{group_id}/members", "put"),
        ("/api/v1/shots/{shot_id}:assign-scene", "post"),
        ("/api/v1/projects/{project_id}/qc-policies", "get"),
        ("/api/v1/projects/{project_id}/qc-policies", "put"),
        ("/api/v1/generation/variants/{variant_id}/qc:decide", "post"),
        ("/api/v1/projects/{project_id}/director-recipes", "get"),
        ("/api/v1/projects/{project_id}/director-recipes", "post"),
        ("/api/v1/projects/{project_id}/director-recipe-binding", "put"),
        ("/api/v1/episodes/{episode_id}/shot-edit", "get"),
        ("/api/v1/episodes/{episode_id}/shot-edit:plan", "post"),
        ("/api/v1/episodes/{episode_id}/shot-edit:commit", "post"),
    }
    assert {(path, method) for path, method in expected_operations if method not in paths.get(path, {})} == set()
