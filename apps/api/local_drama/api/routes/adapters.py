from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.infrastructure.adapters import AdapterContractRegistry

router = APIRouter(tags=["adapters"])


@router.get("/adapters/contracts", operation_id="getAdapterContracts")
async def get_adapter_contracts(request: Request) -> dict[str, object]:
    """Return static adapter declarations; this endpoint never probes a runtime."""

    return {"registry": AdapterContractRegistry(request.app.state.settings).inspect()}
