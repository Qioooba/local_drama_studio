from __future__ import annotations

from fastapi import APIRouter, Body, Request, Response

from local_drama.api.schemas.provider_connections import (
    ProviderConnectionCreateRequest,
    ProviderConnectionUpdateRequest,
    ProviderProbeRequest,
    ProviderSecretRequest,
)
from local_drama.application.errors import api_error_from_domain
from local_drama.application.provider_connections import ProviderConnectionService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["provider-connections"])


def service(request: Request) -> ProviderConnectionService:
    return ProviderConnectionService(
        request.app.state.database,
        request.app.state.settings,
        request.app.state.platform.secret_store,
    )


@router.get("/provider-connections", operation_id="listProviderConnections")
async def list_connections(request: Request) -> dict[str, object]:
    try:
        return {"items": service(request).list_connections()}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/provider-connections", status_code=201, operation_id="createProviderConnection")
async def create_connection(payload: ProviderConnectionCreateRequest, request: Request) -> dict[str, object]:
    try:
        return {"connection": service(request).create(**payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/provider-connections/{connection_id}", operation_id="getProviderConnection")
async def get_connection(connection_id: str, request: Request) -> dict[str, object]:
    try:
        return {"connection": service(request).get_connection(connection_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.patch("/provider-connections/{connection_id}", operation_id="updateProviderConnection")
async def update_connection(connection_id: str, payload: ProviderConnectionUpdateRequest, request: Request) -> dict[str, object]:
    try:
        return {"connection": service(request).update(connection_id, **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/provider-connections/{connection_id}:reveal-secret", operation_id="revealProviderSecret")
async def reveal_secret(connection_id: str, request: Request, response: Response) -> dict[str, object]:
    try:
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Pragma"] = "no-cache"
        return service(request).reveal(connection_id)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.put("/provider-connections/{connection_id}/secret", operation_id="replaceProviderSecret")
async def replace_secret(connection_id: str, payload: ProviderSecretRequest, request: Request) -> dict[str, object]:
    try:
        return {"connection": service(request).replace_secret(connection_id, payload.secret)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.delete("/provider-connections/{connection_id}/secret", operation_id="deleteProviderSecret")
async def delete_secret(connection_id: str, request: Request) -> dict[str, object]:
    try:
        return {"connection": service(request).delete_secret(connection_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.delete("/provider-connections/{connection_id}", operation_id="deleteProviderConnection")
async def delete_connection(connection_id: str, request: Request) -> dict[str, object]:
    try:
        return {"connection": service(request).delete_connection(connection_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/provider-connections/{connection_id}:probe", operation_id="probeProviderConnection")
async def probe_connection(
    connection_id: str,
    request: Request,
    payload: ProviderProbeRequest = Body(default_factory=ProviderProbeRequest),  # noqa: B008 - FastAPI request body declaration
) -> dict[str, object]:
    try:
        return service(request).probe(connection_id, **payload.model_dump())
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
