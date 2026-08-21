from fastapi import APIRouter, Request

from local_drama.api.schemas.asset_proposals import AssetProposalDecisionRequest
from local_drama.application.asset_proposals import AssetProposalService
from local_drama.application.errors import api_error_from_domain
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["asset-proposals"])


@router.get("/projects/{project_id}/asset-proposals", operation_id="listAssetProposals")
async def list_asset_proposals(project_id: str, request: Request, status: str | None = None) -> dict[str, object]:
    return {"items": AssetProposalService(request.app.state.database).list(project_id, status)}


@router.post("/asset-proposals/{proposal_id}:decide", operation_id="decideAssetProposal")
async def decide_asset_proposal(
    proposal_id: str, payload: AssetProposalDecisionRequest, request: Request,
) -> dict[str, object]:
    try:
        return {"proposal": AssetProposalService(request.app.state.database).decide(proposal_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
