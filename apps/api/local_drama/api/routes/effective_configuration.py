from __future__ import annotations

from fastapi import APIRouter, Request

from local_drama.api.schemas.effective_configuration import EffectiveConfigurationResolveRequest
from local_drama.application.effective_configuration import EffectiveConfigurationService
from local_drama.application.errors import api_error_from_domain
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["effective-configuration"])


@router.post("/generation/effective-configuration:resolve", operation_id="resolveEffectiveConfiguration")
async def resolve_effective_configuration(payload: EffectiveConfigurationResolveRequest, request: Request) -> dict[str, object]:
    try:
        result = EffectiveConfigurationService(request.app.state.database, request.app.state.settings.manifest_path).resolve(**payload.model_dump())
        return {"configuration": result}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
