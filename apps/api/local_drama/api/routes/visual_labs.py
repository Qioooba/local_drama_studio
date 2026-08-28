from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, Request

from local_drama.api.schemas.visual_labs import (
    VisualLabBatchDeleteRequest,
    VisualLabCreateRequest,
    VisualLabDeleteNodeRequest,
    VisualLabDuplicateRequest,
    VisualLabEdgeCreateRequest,
    VisualLabMoveRequest,
    VisualLabNodeCreateRequest,
    VisualLabNodeRevisionRequest,
    VisualLabPromotionPreflightRequest,
    VisualLabPromotionRequest,
    VisualLabRunRequest,
    VisualLabSnapshotRestoreRequest,
    VisualLabViewportRequest,
)
from local_drama.application.errors import api_error_from_domain
from local_drama.application.visual_labs import VisualLabService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["visual-labs"])
T = TypeVar("T")


def service(request: Request) -> VisualLabService:
    return VisualLabService(request.app.state.database, request.app.state.settings)


def guarded(call: Callable[[], T]) -> T:
    try:
        return call()
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/visual-labs", operation_id="listVisualLabs")
async def list_labs(project_id: str, request: Request) -> dict[str, object]:
    return {"items": service(request).list(project_id)}


@router.post("/projects/{project_id}/visual-labs", status_code=201, operation_id="createVisualLab")
async def create_lab(project_id: str, payload: VisualLabCreateRequest, request: Request) -> dict[str, object]:
    return guarded(lambda: {"document": service(request).create(project_id, **payload.model_dump())})


@router.get("/visual-labs/{lab_id}", operation_id="getVisualLab")
async def get_lab(lab_id: str, request: Request) -> dict[str, object]:
    return guarded(lambda: service(request).get(lab_id))


@router.post("/visual-labs/{lab_id}/nodes", status_code=201, operation_id="createVisualLabNode")
async def create_node(lab_id: str, payload: VisualLabNodeCreateRequest, request: Request) -> dict[str, object]:
    return guarded(lambda: {"node": service(request).add_node(lab_id, payload.model_dump())})


@router.post("/visual-lab-nodes/{node_id}/revisions", status_code=201, operation_id="reviseVisualLabNode")
async def revise_node(node_id: str, payload: VisualLabNodeRevisionRequest, request: Request) -> dict[str, object]:
    return guarded(lambda: {"node": service(request).revise_node(node_id, **payload.model_dump())})


@router.post("/visual-labs/{lab_id}/nodes:batch-move", operation_id="moveVisualLabNodes")
async def move_nodes(lab_id: str, payload: VisualLabMoveRequest, request: Request) -> dict[str, object]:
    return guarded(lambda: {"nodes": service(request).move_nodes(lab_id, [item.model_dump() for item in payload.nodes])})


@router.put("/visual-labs/{lab_id}/viewport", operation_id="saveVisualLabViewport")
async def save_viewport(lab_id: str, payload: VisualLabViewportRequest, request: Request) -> dict[str, object]:
    return guarded(lambda: {"viewport": service(request).save_viewport(lab_id, payload.model_dump())})


@router.post("/visual-labs/{lab_id}/nodes:duplicate", status_code=201, operation_id="duplicateVisualLabNodes")
async def duplicate_nodes(lab_id: str, payload: VisualLabDuplicateRequest, request: Request) -> dict[str, object]:
    return guarded(lambda: service(request).duplicate_nodes(lab_id, **payload.model_dump()))


@router.post("/visual-labs/{lab_id}/nodes:batch-delete", operation_id="deleteVisualLabNodes")
async def delete_nodes(lab_id: str, payload: VisualLabBatchDeleteRequest, request: Request) -> dict[str, object]:
    return guarded(lambda: {"result": service(request).delete_nodes(lab_id, payload.node_ids, payload.expected_topology_revision)})


@router.post("/visual-lab-nodes/{node_id}:delete", operation_id="deleteVisualLabNode")
async def delete_node(node_id: str, payload: VisualLabDeleteNodeRequest, request: Request) -> dict[str, object]:
    return guarded(lambda: {"result": service(request).delete_node(node_id, payload.expected_topology_revision)})


@router.post("/visual-labs/{lab_id}/edges", status_code=201, operation_id="createVisualLabEdge")
async def create_edge(lab_id: str, payload: VisualLabEdgeCreateRequest, request: Request) -> dict[str, object]:
    return guarded(lambda: {"edge": service(request).connect(lab_id, payload.model_dump())})


@router.delete("/visual-lab-edges/{edge_id}", status_code=204, operation_id="deleteVisualLabEdge")
async def delete_edge(edge_id: str, request: Request) -> None:
    return guarded(lambda: service(request).delete_edge(edge_id))


@router.post("/visual-labs/{lab_id}/snapshots", status_code=201, operation_id="snapshotVisualLab")
async def snapshot(lab_id: str, request: Request) -> dict[str, object]:
    return guarded(lambda: {"snapshot": service(request).snapshot(lab_id)})


@router.get("/visual-labs/{lab_id}/snapshots", operation_id="listVisualLabSnapshots")
async def list_snapshots(lab_id: str, request: Request, limit: int = 50) -> dict[str, object]:
    return guarded(lambda: {"items": service(request).list_snapshots(lab_id, limit)})


@router.post("/visual-lab-snapshots/{snapshot_id}:restore-preflight", operation_id="preflightVisualLabSnapshotRestore")
async def restore_preflight(snapshot_id: str, request: Request) -> dict[str, object]:
    return guarded(lambda: {"plan": service(request).restore_preflight(snapshot_id)})


@router.post("/visual-lab-snapshots/{snapshot_id}:restore", operation_id="restoreVisualLabSnapshot")
async def restore_snapshot(snapshot_id: str, payload: VisualLabSnapshotRestoreRequest, request: Request) -> dict[str, object]:
    return guarded(lambda: service(request).restore_snapshot(snapshot_id, payload.plan_hash))


@router.post("/visual-lab-nodes/{node_id}/runs:preflight", operation_id="preflightVisualLabNodeRun")
async def run_preflight(node_id: str, request: Request) -> dict[str, object]:
    return guarded(lambda: {"plan": service(request).run_preflight(node_id)})


@router.post("/visual-lab-nodes/{node_id}/runs", status_code=201, operation_id="runVisualLabNode")
async def run_node(node_id: str, payload: VisualLabRunRequest, request: Request) -> dict[str, object]:
    return guarded(lambda: service(request).run(node_id, payload.plan_hash, payload.idempotency_key))


@router.post("/visual-lab-nodes/{node_id}/promotions:preflight", operation_id="preflightVisualLabPromotion")
async def promotion_preflight(node_id: str, payload: VisualLabPromotionPreflightRequest, request: Request) -> dict[str, object]:
    return guarded(lambda: {"plan": service(request).promotion_preflight(node_id, payload.source_media_version_id, payload.target_type, payload.target_id)})


@router.post("/visual-lab-nodes/{node_id}/promotions", status_code=201, operation_id="promoteVisualLabCandidate")
async def promote(node_id: str, payload: VisualLabPromotionRequest, request: Request) -> dict[str, object]:
    return guarded(lambda: {"promotion": service(request).promote(node_id, **payload.model_dump())})
