from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["provider-execution-events"])


@router.websocket("/jobs/{job_id}/events/ws", name="streamJobProviderEvents")
async def stream_job_provider_events(websocket: WebSocket, job_id: str) -> None:
    await websocket.accept()
    database = websocket.app.state.database
    cursor = 0
    try:
        while True:
            with database.connect() as connection:
                job = connection.execute("SELECT id,state,progress_json,updated_at FROM jobs WHERE id=?", (job_id,)).fetchone()
                if job is None:
                    await websocket.send_json({"type": "ERROR", "error": {"code": "JOB_NOT_FOUND", "message": "Job 不存在"}})
                    await websocket.close(code=4404)
                    return
                rows = connection.execute(
                    """SELECT e.rowid AS event_cursor,e.* FROM provider_execution_events e
                    JOIN job_attempts a ON a.id=e.job_attempt_id
                    WHERE a.job_id=? AND e.rowid>? ORDER BY e.rowid LIMIT 200""",
                    (job_id, cursor),
                ).fetchall()
            for row in rows:
                item = dict(row)
                cursor = max(cursor, int(item.pop("event_cursor")))
                item["payload"] = json.loads(item.pop("payload_redacted_json") or "{}")
                await websocket.send_json({"type": "PROVIDER_EVENT", "event": item})
            await websocket.send_json({"type": "JOB_STATE", "job": {"id": job["id"], "state": job["state"], "progress": json.loads(job["progress_json"] or "{}"), "updated_at": job["updated_at"]}})
            if str(job["state"]) in {"SUCCEEDED", "FAILED", "CANCELLED", "DEAD", "NEEDS_ATTENTION"}:
                await websocket.close(code=1000)
                return
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        return
