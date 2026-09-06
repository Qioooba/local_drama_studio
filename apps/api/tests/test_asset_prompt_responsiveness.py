"""Long local prompt inference must not block unrelated page requests."""

import asyncio
import threading
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from local_drama.api.routes import asset_bible


def test_multiview_prompt_inference_keeps_other_requests_responsive(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def draft(*args, **kwargs):
        assert kwargs["revision_guidance"] == "keep the short haircut"
        entered.set()
        release.wait(3)
        return {"slots": []}

    monkeypatch.setattr(asset_bible, "_multiview", lambda request: SimpleNamespace(draft_prompts=draft))
    app = FastAPI()
    app.include_router(asset_bible.router)

    @app.get("/page-status")
    async def page_status():
        return {"responsive": True}

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            pending = asyncio.create_task(client.post("/story-assets/character/generate-multiview:prompts", json={"requested_slots": ["FRONT"], "revision_guidance": "keep the short haircut"}))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                assert not pending.done(), "Prompt inference blocked the event loop until completion"
                status = await asyncio.wait_for(client.get("/page-status"), timeout=1)
                assert status.json() == {"responsive": True}
            finally:
                release.set()
                response = await pending
            assert response.status_code == 200

    asyncio.run(exercise())


def test_revision_guidance_is_preserved_in_frozen_prompt_bundle():
    from local_drama.application.asset_multiview import AssetMultiViewService, _digest

    original = {"source": "LOCAL_LLM", "items": {"LEFT": {"positive_prompt": "left view", "negative_prompt": "long hair"}}}
    revised = {**original, "revision_guidance": "  keep the short haircut  "}
    first = AssetMultiViewService._normalize_prompt_bundle(original, ["LEFT"])
    second = AssetMultiViewService._normalize_prompt_bundle(revised, ["LEFT"])
    assert second["revision_guidance"] == "keep the short haircut"
    assert _digest(first) != _digest(second)
