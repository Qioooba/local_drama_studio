from fastapi.testclient import TestClient

from local_drama.application.breakdown_apply import BreakdownApplyService
from local_drama.application.story_assets import StoryAssetService
from local_drama.main import create_app
from tests.test_breakdown_apply import _persisted_draft


def test_breakdown_asset_proposals_require_non_destructive_human_decisions(workspace, database) -> None:
    project, episode, draft_id = _persisted_draft(workspace, database)
    existing = StoryAssetService(database, workspace).create_asset(
        str(project["id"]), "CHARACTER", "CHAR_MOTHER", "母亲",
    )
    BreakdownApplyService(database, workspace).apply_draft(draft_id, str(episode["id"]))
    with TestClient(create_app(workspace)) as client:
        proposals = client.get(f"/api/v1/projects/{project['id']}/asset-proposals").json()["items"]
        assert len(proposals) == 3
        mother = next(item for item in proposals if item["name"] == "母亲")
        assert mother["suggested_asset_id"] == existing["id"]
        merged = client.post(f"/api/v1/asset-proposals/{mother['id']}:decide", json={
            "action": "MERGE_EXISTING", "expected_revision": mother["revision"],
            "target_asset_id": existing["id"], "decision_note": "同一角色",
        })
        assert merged.status_code == 200
        assert merged.json()["proposal"]["status"] == "ACCEPTED_MERGE"

        child = next(item for item in proposals if item["name"] == "孩子")
        created = client.post(f"/api/v1/asset-proposals/{child['id']}:decide", json={
            "action": "CREATE_NEW", "expected_revision": child["revision"], "new_asset_code": "CHAR_CHILD",
        })
        assert created.status_code == 200, created.text
        assert created.json()["proposal"]["status"] == "ACCEPTED_NEW"

        neighbor = next(item for item in proposals if item["name"] == "邻居")
        rejected = client.post(f"/api/v1/asset-proposals/{neighbor['id']}:decide", json={
            "action": "REJECT", "expected_revision": neighbor["revision"], "decision_note": "暂不建档",
        })
        assert rejected.status_code == 200
        assert rejected.json()["proposal"]["status"] == "REJECTED"
        repeated = client.post(f"/api/v1/asset-proposals/{neighbor['id']}:decide", json={
            "action": "CREATE_NEW", "expected_revision": neighbor["revision"], "new_asset_code": "CHAR_NEIGHBOR",
        })
        assert repeated.status_code == 409

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM story_assets WHERE project_id=?", (project["id"],)).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='STORY_ASSET_PROPOSAL_DECIDED'",
        ).fetchone()[0] == 3
