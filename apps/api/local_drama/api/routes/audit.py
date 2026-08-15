from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query, Request

from local_drama.application.audit import AuditService

router = APIRouter(prefix="/audit-events", tags=["audit"])


@router.get("", operation_id="listAuditEvents")
async def list_audit_events(
    request: Request,
    project_id: str | None = None,
    occurred_after: datetime | None = None,
    occurred_before: datetime | None = None,
    action: str | None = None,
    actor: str | None = None,
    subject_type: str | None = None,
    subject_id: str | None = None,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, object]:
    return AuditService(request.app.state.database).list_page(
        project_id=project_id,
        occurred_after=occurred_after,
        occurred_before=occurred_before,
        action=action,
        actor=actor,
        subject_type=subject_type,
        subject_id=subject_id,
        cursor=cursor,
        limit=limit,
    )
