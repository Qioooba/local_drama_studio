from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["platform"])


@router.get("/system/platform-capabilities", operation_id="getPlatformCapabilities")
async def platform_capabilities(request: Request) -> dict[str, object]:
    return request.app.state.platform.public_capabilities()
