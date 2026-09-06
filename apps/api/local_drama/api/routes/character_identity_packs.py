"""FastAPI router for Character Identity Packs (PR-CUR-007)."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from local_drama.application.character_identity_packs import CharacterIdentityPackService
from local_drama.application.errors import api_error_from_domain
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["character-identity-packs"])


def service(request: Request) -> CharacterIdentityPackService:
    return CharacterIdentityPackService(request.app.state.database)


class CreateIdentityPackRequest(BaseModel):
    project_id: str
    code: str
    name: str
    description: str = ""
    asset_state_id: str | None = None


class SetSlotRequest(BaseModel):
    slot_kind: str
    media_version_id: str
    generation_profile_version_id: str | None = None
    is_primary: bool = True


class ApprovePackVersionRequest(BaseModel):
    comment: str = Field(min_length=1, max_length=2000)


class RetirePackVersionRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class BindShotIdentityPackRequest(BaseModel):
    story_asset_id: str
    pack_version_id: str


@router.get("/story-assets/{story_asset_id}/identity-packs", operation_id="listCharacterIdentityPacks")
async def list_packs(story_asset_id: str, request: Request) -> dict[str, object]:
    try:
        return {"items": service(request).list_packs(story_asset_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/story-assets/{story_asset_id}/identity-packs", status_code=201, operation_id="createCharacterIdentityPack")
async def create_pack(story_asset_id: str, payload: CreateIdentityPackRequest, request: Request) -> dict[str, object]:
    try:
        pack = service(request).create_pack(
            project_id=payload.project_id,
            story_asset_id=story_asset_id,
            code=payload.code,
            name=payload.name,
            description=payload.description,
            asset_state_id=payload.asset_state_id,
        )
        return {"pack": pack}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/character-identity-packs/{pack_id}", operation_id="getCharacterIdentityPack")
async def get_pack(pack_id: str, request: Request) -> dict[str, object]:
    try:
        return {"pack": service(request).get_pack(pack_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/character-identity-packs/{pack_id}/versions", status_code=201, operation_id="createCharacterIdentityPackVersion")
async def create_version(pack_id: str, request: Request, from_version_id: str | None = Query(default=None)) -> dict[str, object]:
    try:
        return {"version": service(request).create_version_draft(pack_id, from_version_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.put("/character-identity-pack-versions/{pack_version_id}/slots", operation_id="setCharacterIdentityPackSlot")
async def set_slot(pack_version_id: str, payload: SetSlotRequest, request: Request) -> dict[str, object]:
    try:
        return {
            "version": service(request).set_version_slot(
                pack_version_id=pack_version_id,
                slot_kind=payload.slot_kind,
                media_version_id=payload.media_version_id,
                generation_profile_version_id=payload.generation_profile_version_id,
                is_primary=payload.is_primary,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.delete("/character-identity-pack-versions/{pack_version_id}/slots/{slot_kind}", operation_id="removeCharacterIdentityPackSlot")
async def remove_slot(pack_version_id: str, slot_kind: str, request: Request) -> dict[str, object]:
    try:
        return {"version": service(request).remove_version_slot(pack_version_id, slot_kind)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/character-identity-pack-versions/{pack_version_id}:approve", operation_id="approveCharacterIdentityPackVersion")
async def approve_version(pack_version_id: str, payload: ApprovePackVersionRequest, request: Request) -> dict[str, object]:
    try:
        return {"version": service(request).approve_pack_version(pack_version_id, payload.comment)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/character-identity-pack-versions/{pack_version_id}:compare", operation_id="compareCharacterIdentityPackVersions")
async def compare_versions(
    pack_version_id: str,
    request: Request,
    target_version_id: str = Query(min_length=1),
) -> dict[str, object]:
    try:
        return {"comparison": service(request).compare_versions(pack_version_id, target_version_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/character-identity-pack-versions/{pack_version_id}/impact", operation_id="getCharacterIdentityPackVersionImpact")
async def get_version_impact(pack_version_id: str, request: Request) -> dict[str, object]:
    try:
        return {"impact": service(request).version_impact(pack_version_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/character-identity-pack-versions/{pack_version_id}:retire", operation_id="retireCharacterIdentityPackVersion")
async def retire_version(
    pack_version_id: str,
    payload: RetirePackVersionRequest,
    request: Request,
) -> dict[str, object]:
    try:
        return {"version": service(request).retire_pack_version(pack_version_id, payload.reason)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


# Keep the catch-all GET route after the suffix action routes.  Starlette
# matches routes in declaration order; if this is declared first it consumes
# ``<uuid>:compare`` as the version id and the real compare route is unreachable.
@router.get("/character-identity-pack-versions/{pack_version_id}", operation_id="getCharacterIdentityPackVersion")
async def get_version(pack_version_id: str, request: Request) -> dict[str, object]:
    try:
        return {"version": service(request).get_version(pack_version_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/shots/{shot_id}/character-identity-packs:bind", operation_id="bindShotCharacterIdentityPack")
async def bind_shot_pack(shot_id: str, payload: BindShotIdentityPackRequest, request: Request) -> dict[str, object]:
    try:
        return {
            "binding": service(request).bind_shot_identity_pack(
                shot_id=shot_id,
                story_asset_id=payload.story_asset_id,
                pack_version_id=payload.pack_version_id,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(
    "/episodes/{episode_id}/character-identity-packs:sync",
    operation_id="syncEpisodeCharacterIdentityPacks",
)
async def sync_episode_packs(episode_id: str, request: Request) -> dict[str, object]:
    try:
        return {"sync": service(request).sync_episode_identity_packs(episode_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/shots/{shot_id}/character-identity-packs", operation_id="getShotCharacterIdentityPacks")
async def get_shot_packs(shot_id: str, request: Request) -> dict[str, object]:
    try:
        return {"items": service(request).get_shot_character_packs(shot_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
