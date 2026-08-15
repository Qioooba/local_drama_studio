from __future__ import annotations

from fastapi import APIRouter, Header, Request

from local_drama.api.schemas.automation import AutomationClientRequest, WebhookDeliverRequest, WebhookSubscriptionRequest
from local_drama.application.automation import AutomationService, _bearer
from local_drama.application.errors import api_error_from_domain
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["automation"])


def service(request: Request) -> AutomationService:
    return AutomationService(request.app.state.database)


@router.post("/automation-clients", status_code=201, operation_id="createAutomationClient")
async def create_automation_client(payload: AutomationClientRequest, request: Request, idempotency_key: str = Header(..., alias="Idempotency-Key")) -> dict[str, object]:
    try:
        return {"client": service(request).create_client(**payload.model_dump(), idempotency_key=idempotency_key)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/webhook-subscriptions", status_code=201, operation_id="createWebhookSubscription")
async def create_webhook_subscription(payload: WebhookSubscriptionRequest, request: Request, authorization: str | None = Header(default=None), idempotency_key: str = Header(..., alias="Idempotency-Key")) -> dict[str, object]:
    try:
        return {"subscription": service(request).create_subscription(_bearer(authorization), **payload.model_dump(), idempotency_key=idempotency_key)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/webhook-subscriptions", operation_id="listWebhookSubscriptions")
async def list_webhook_subscriptions(request: Request, authorization: str | None = Header(default=None)) -> dict[str, object]:
    try:
        return service(request).list_subscriptions(_bearer(authorization))
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/webhook-deliveries:deliver", operation_id="deliverWebhookEvents")
async def deliver_webhook_events(payload: WebhookDeliverRequest, request: Request, authorization: str | None = Header(default=None)) -> dict[str, object]:
    try:
        return {"delivery": service(request).deliver(_bearer(authorization), **payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/webhook-deliveries", operation_id="listWebhookDeliveries")
async def list_webhook_deliveries(request: Request, subscription_id: str | None = None, status: str | None = None, limit: int = 100, authorization: str | None = Header(default=None)) -> dict[str, object]:
    try:
        return service(request).list_deliveries(_bearer(authorization), subscription_id=subscription_id, status=status, limit=limit)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/webhook-deliveries/{delivery_id}:retry", operation_id="retryWebhookDelivery")
async def retry_webhook_delivery(delivery_id: str, request: Request, authorization: str | None = Header(default=None)) -> dict[str, object]:
    try:
        return {"delivery": service(request).retry_delivery(_bearer(authorization), delivery_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
