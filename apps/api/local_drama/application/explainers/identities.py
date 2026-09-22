"""Explainer identity service: entities, states, reference slots, bindings.

Characters/objects/places of a 解说作品 (Explainer Factory) video: canonical
entity registry, story-time state revisions, verified reference slots, the
run-scoped ``MACHINE_TEMPORARY`` identity input convention, per-beat identity
bindings and the review list for uncertain identifications.

Reuse of the shipping identity-pack rules
-----------------------------------------
The legacy Character Identity Pack service (``application/character_identity_packs.py``)
owns slot validation.  Its slot-kind vocabulary and required-slot sets are
importable *without* a database, so they are imported here unchanged
(``SlotKind``, ``REQUIRED_THREE_VIEW_SLOTS``, ``STANDARD_CHARACTER_SLOTS``,
``PackVersionStatus``).  Its ``_slot_rows`` / ``_validate_slot_row`` /
``_validated_content`` helpers are class methods that need a legacy
``character_identity_pack_slots`` row plus a live ``workspace_asset_authorizations``
row, so the *same rule* is re-implemented in the small dedicated function
:func:`_verify_slot_media` on top of ``ExplainerRepository.require_same_project_media``
(project match, ``media_kind == IMAGE``, ``integrity_status == VERIFIED``,
sha256 identity).  The hash rule is not re-implemented at all: the legacy
``_digest`` is ``sha256(canonical_json(...))``, which is exactly
``local_drama.domain.explainers.contracts.content_hash``.

Deliberately NOT done here
--------------------------
* No identity detection, no face recognition and no model call.  Detections are
  supplied as facts by the caller; this module records and queues them.
* No claim that identification is 100% correct: uncertain and ``UNKNOWN``
  detections become a small human review list (:meth:`identity_review_queue`).
* No human approval is fabricated.  ``MACHINE_TEMPORARY`` inputs always carry
  ``human_approval_id=None`` and ``human_approved=False``.
* No writing to the legacy Character Identity Pack tables: an approved pack
  version stays immutable and is only *referenced*.
* No declaration without consumption: a declared reference slot that the
  workflow never consumed raises ``CAPABILITY_UNAVAILABLE`` instead of being
  reported as used.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from local_drama.domain.character_identity_packs import (
    REQUIRED_THREE_VIEW_SLOTS,
    STANDARD_CHARACTER_SLOTS,
    PackVersionStatus,
    SlotKind,
)
from local_drama.domain.explainers.contracts import (
    EntityType,
    ExplainerContractError,
    content_hash,
    utc_now_iso,
)
from local_drama.domain.explainers.policies import staleness_plan
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository

#: Reserved marker for a slot whose hash could not be verified.  A slot is never
#: assumed present: it is either verified or recorded as missing.
MISSING_SLOT = "MISSING_SLOT"
VERIFIED = "VERIFIED"
DRIFTED = "DRIFTED"
UNVERIFIED = "UNVERIFIED"

#: Snapshot kinds available on ``run_identity_inputs``.
MACHINE_TEMPORARY = "MACHINE_TEMPORARY"

SNAPSHOT_KINDS: frozenset[str] = frozenset({"MACHINE_TEMPORARY", "HUMAN_APPROVED", "SHARED_PACK"})
SOURCE_KINDS: frozenset[str] = frozenset({"MACHINE_POLICY", "HUMAN", "SHARED_PACK"})

#: ``FRONT`` only, for a one-shot background character.
SINGLE_REFERENCE_SLOTS: tuple[str, ...] = (SlotKind.FRONT.value,)

REQUIREMENTS: frozenset[str] = frozenset({"AUTO", "THREE_VIEW", "SINGLE_REFERENCE"})

_YEAR_RE = re.compile(r"^(-?\d{1,6})")


def _slot_kind(value: str) -> str:
    cleaned = str(value or "").strip().upper()
    try:
        return SlotKind(cleaned).value
    except ValueError as error:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            "身份参考槽位类型不受支持",
            {"slot_kind": cleaned, "allowed": [item.value for item in SlotKind]},
        ) from error


def _story_year(value: str, *, field: str) -> int:
    match = _YEAR_RE.match(str(value or "").strip())
    if not match:
        raise ExplainerContractError(
            "SCHEMA_INVALID", f"{field} 必须是可解析的故事时间（如 1937 或 1937-07-07）", {"value": value}
        )
    return int(match.group(1))


def _slot_entry(role: str, media: Mapping[str, Any]) -> dict[str, Any]:
    """The exact JSON shape the slot hash is computed over.

    Mirrors the legacy identity-pack slot projection (slot kind + immutable
    media version + content hash) and is hashed with the shared
    ``content_hash`` rule.
    """

    return {
        "slot_kind": role,
        "media_version_id": str(media["media_version_id"]),
        "media_asset_id": str(media.get("media_asset_id") or ""),
        "sha256": str(media.get("sha256") or ""),
        "media_kind": str(media.get("media_kind") or ""),
        "integrity_status": str(media.get("integrity_status") or ""),
    }


def _verify_slot_media(repo: ExplainerRepository, *, project_id: str, role: str, media_version_id: str) -> dict[str, Any]:
    """Apply the shared identity-pack slot media rule to one declared slot.

    Re-implemented (see module docstring) from ``CharacterIdentityPackService._validate_slot_row``:
    the media must belong to the same project, be an ``IMAGE`` media version and
    be ``VERIFIED``.  A media version that cannot be read is reported as
    ``MISSING_SLOT`` by the caller instead of being assumed present.
    """

    media = repo.require_same_project_media(project_id=project_id, media_version_id=str(media_version_id))
    if str(media.get("media_kind") or "").upper() != "IMAGE":
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            "身份参考槽只能绑定 IMAGE 媒体版本",
            {"slot_kind": role, "media_version_id": str(media_version_id), "media_kind": media.get("media_kind")},
        )
    return {
        **dict(media),
        "slot_kind": role,
        "verifiable": str(media.get("integrity_status") or "").upper() == "VERIFIED",
    }


def _resolve_declared_slots(
    repo: ExplainerRepository, *, project_id: str, slots: Mapping[str, str]
) -> tuple[dict[str, str], list[dict[str, Any]], list[str], dict[str, str]]:
    """Resolve a declared slot map into verifiable media rows.

    Returns ``(declared, entries, missing_roles, missing_reasons)`` where
    ``entries`` carries the verified media facts and ``missing_roles`` names
    slots whose hash could not be verified (missing media version or a media
    version that belongs to another project).  A media version of the wrong kind
    is a caller error and is raised instead of being silently downgraded.
    """

    if not isinstance(slots, Mapping) or not slots:
        raise ExplainerContractError("SCHEMA_INVALID", "参考槽位映射不能为空", {"slots": slots})
    declared: dict[str, str] = {}
    entries: list[dict[str, Any]] = []
    missing_roles: list[str] = []
    missing_reasons: dict[str, str] = {}
    for raw_role, media_version_id in slots.items():
        role = _slot_kind(str(raw_role))
        if role in declared:
            raise ExplainerContractError("SCHEMA_INVALID", f"参考槽位重复声明：{role}", {"slot_kind": role})
        declared[role] = str(media_version_id)
        try:
            media = _verify_slot_media(
                repo, project_id=project_id, role=role, media_version_id=str(media_version_id)
            )
        except ExplainerContractError as error:
            if error.code not in {"NOT_FOUND", "INVALID_REQUEST"}:
                raise
            missing_roles.append(role)
            missing_reasons[role] = error.code
            continue
        if not media["verifiable"]:
            missing_roles.append(role)
            missing_reasons[role] = "MEDIA_INTEGRITY_NOT_VERIFIED"
            continue
        entries.append(_slot_entry(role, media))
    return declared, entries, missing_roles, missing_reasons


def _slot_hashes(slots: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Recorded slot projection: which media version, and its content hash."""

    return {
        str(role): {
            "media_version_id": str(entry.get("media_version_id") or ""),
            "sha256": str(entry.get("sha256") or ""),
        }
        for role, entry in slots.items()
    }


def _identity_draft_hash(
    *,
    run_id: str,
    video_id: str,
    entity_id: str,
    entity_state_revision_id: str | None,
    snapshot_kind: str,
    slot_hashes: Mapping[str, Mapping[str, Any]],
) -> str:
    """Draft hash of a machine-temporary identity input.

    Deterministic and reproducible from the stored row, which is what makes the
    legacy "re-validate the draft hash on every read" convention possible.
    """

    return content_hash(
        {
            "schema_version": "localdrama.explainer.identity-input.v1",
            "run_id": run_id,
            "video_id": video_id,
            "entity_id": entity_id,
            "entity_state_revision_id": entity_state_revision_id,
            "snapshot_kind": snapshot_kind,
            "slot_hashes": {
                str(role): {
                    "media_version_id": str(entry.get("media_version_id") or ""),
                    "sha256": str(entry.get("sha256") or ""),
                }
                for role, entry in sorted(slot_hashes.items())
            },
        }
    )


class ExplainerIdentityService:
    """Identity-stage application service over one :class:`ExplainerRepository`."""

    def __init__(self, repo: ExplainerRepository) -> None:
        self.repo = repo

    # ------------------------------------------------------------------ entities
    def register_entity(
        self,
        *,
        project_id: str,
        video_id: str,
        code: str,
        entity_type: str,
        name: str,
        latin_name: str | None = None,
        aliases: Sequence[str] = (),
        fictional: bool = False,
        descriptive_only: bool = False,
        disambiguation: Mapping[str, Any] | None = None,
        story_asset_id: str | None = None,
    ) -> dict[str, Any]:
        """Register one story entity of a video (person, place, prop, concept...)."""

        self.repo.require_explainer_project(project_id)
        video = self.repo.get("explainer_videos", video_id)
        if str(video["project_id"]) != project_id:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "解说作品不属于该项目",
                {"video_id": video_id, "project_id": project_id},
            )
        clean_code = str(code or "").strip()
        if not clean_code:
            raise ExplainerContractError("SCHEMA_INVALID", "实体 code 不能为空", {"video_id": video_id})
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ExplainerContractError("SCHEMA_INVALID", "实体名称不能为空", {"code": clean_code})
        clean_type = str(entity_type or "").strip().upper()
        if clean_type not in {item.value for item in EntityType}:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                f"实体类型不合法：{clean_type!r}",
                {"entity_type": clean_type, "allowed": [item.value for item in EntityType]},
            )
        is_person = clean_type in {EntityType.REAL_PERSON.value, EntityType.FICTIONAL_CHARACTER.value}
        if fictional and clean_type == EntityType.REAL_PERSON.value:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "真实人物不能被标记为虚构", {"code": clean_code, "entity_type": clean_type}
            )
        if not fictional and clean_type == EntityType.FICTIONAL_CHARACTER.value:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "虚构角色必须标记 fictional=true",
                {"code": clean_code, "entity_type": clean_type},
            )
        if descriptive_only and is_person and fictional:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "虚构人物需要形象设定，不能只做文字描述",
                {"code": clean_code},
            )
        existing = self.repo.query_one(
            "SELECT id FROM explainer_entities WHERE video_id = ? AND code = ?", (video_id, clean_code)
        )
        if existing is not None:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                f"该解说作品已存在实体 code：{clean_code}",
                {"video_id": video_id, "code": clean_code, "entity_id": str(existing["id"])},
            )
        if not isinstance(disambiguation, Mapping) and disambiguation is not None:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "disambiguation 必须是对象", {"code": clean_code}
            )
        clean_aliases = [str(item).strip() for item in (aliases or ()) if str(item).strip()]
        if len(set(clean_aliases)) != len(clean_aliases):
            raise ExplainerContractError("SCHEMA_INVALID", "别名不能重复", {"code": clean_code})
        if story_asset_id is not None:
            asset = self.repo.query_one(
                "SELECT id, project_id, kind FROM story_assets WHERE id = ?", (str(story_asset_id),)
            )
            if asset is None:
                raise ExplainerContractError(
                    "NOT_FOUND", "关联的剧情资产不存在", {"story_asset_id": str(story_asset_id)}
                )
            if str(asset["project_id"]) != project_id:
                raise ExplainerContractError(
                    "INVALID_REQUEST",
                    "关联的剧情资产不属于该项目",
                    {"story_asset_id": str(story_asset_id), "project_id": project_id},
                )
        row = self.repo.insert(
            "explainer_entities",
            {
                "video_id": video_id,
                "project_id": project_id,
                "code": clean_code,
                "entity_type": clean_type,
                "name": clean_name,
                "latin_name": str(latin_name).strip() if latin_name else None,
                "aliases_json": clean_aliases,
                "fictional": bool(fictional),
                "descriptive_only": bool(descriptive_only),
                "disambiguation_json": dict(disambiguation or {}),
                "story_asset_id": str(story_asset_id) if story_asset_id else None,
                "status": "ACTIVE",
            },
        )
        return {
            "entity": row,
            "entity_id": str(row["id"]),
            "video_id": video_id,
            "project_id": project_id,
            "code": clean_code,
            "entity_type": clean_type,
            "fictional": bool(fictional),
            "descriptive_only": bool(descriptive_only),
            "aliases": clean_aliases,
            "canonical_state_revision_id": None,
            "reference_slots_registered": False,
            "identity_claim": "REGISTERED_NOT_YET_VERIFIED",
        }

    # ------------------------------------------------------------------ state
    def create_state_revision(
        self,
        *,
        entity_id: str,
        label: str,
        age: int | None = None,
        wardrobe: str = "",
        condition: str = "",
        carried_prop_entity_codes: Sequence[str] = (),
        valid_from_story_time: str | None = None,
        valid_to_story_time: str | None = None,
        reference_media_version_ids: Sequence[str] = (),
        identity_pack_version_id: str | None = None,
    ) -> dict[str, Any]:
        """Append a state revision (costume/age/condition) for one entity.

        ``revision_no`` is ``max + 1`` and ``content_hash`` covers the state
        content only, so two identical states hash identically regardless of
        ordering.
        """

        entity = self.repo.get("explainer_entities", entity_id)
        project_id = str(entity["project_id"])
        video_id = str(entity["video_id"])
        clean_label = str(label or "").strip()
        if not clean_label:
            raise ExplainerContractError("SCHEMA_INVALID", "状态版本必须提供 label", {"entity_id": entity_id})
        if age is not None:
            age = int(age)
            if age < 0 or age > 200:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "人物年龄必须在 0–200 之间", {"entity_id": entity_id, "age": age}
                )
        if valid_from_story_time and valid_to_story_time:
            if str(valid_to_story_time) < str(valid_from_story_time):
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "状态版本的有效时间窗起止颠倒",
                    {
                        "entity_id": entity_id,
                        "valid_from_story_time": str(valid_from_story_time),
                        "valid_to_story_time": str(valid_to_story_time),
                    },
                )
        carried_ids: list[str] = []
        for code in carried_prop_entity_codes or ():
            prop = self.repo.query_one(
                "SELECT id, video_id FROM explainer_entities WHERE video_id = ? AND code = ?",
                (video_id, str(code)),
            )
            if prop is None:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    f"状态版本引用了不存在的随身道具实体：{code}",
                    {"entity_id": entity_id, "entity_code": str(code)},
                )
            if str(prop["id"]) not in carried_ids:
                carried_ids.append(str(prop["id"]))
        reference_ids = [str(item) for item in dict.fromkeys(reference_media_version_ids or ())]
        reference_entries: list[dict[str, Any]] = []
        for media_version_id in reference_ids:
            media = self.repo.require_same_project_media(
                project_id=project_id, media_version_id=media_version_id
            )
            reference_entries.append({"media_version_id": media_version_id, "sha256": str(media["sha256"])})
        pack_status = None
        if identity_pack_version_id:
            pack_version = self._pack_version(str(identity_pack_version_id))
            if pack_version is None:
                raise ExplainerContractError(
                    "NOT_FOUND",
                    "引用的角色身份包版本不存在",
                    {"identity_pack_version_id": str(identity_pack_version_id)},
                )
            if str(pack_version["project_id"]) != project_id:
                raise ExplainerContractError(
                    "INVALID_REQUEST",
                    "引用的角色身份包版本不属于该项目",
                    {"identity_pack_version_id": str(identity_pack_version_id), "project_id": project_id},
                )
            pack_status = str(pack_version["status"])
        revision_row = self.repo.query_one(
            "SELECT MAX(revision_no) AS max_revision FROM entity_state_revisions WHERE entity_id = ?",
            (entity_id,),
        )
        revision_no = int((revision_row["max_revision"] if revision_row else 0) or 0) + 1
        content = {
            "entity_id": entity_id,
            "label": clean_label,
            "age": age,
            "wardrobe": str(wardrobe or ""),
            "condition": str(condition or ""),
            "carried_prop_entity_ids": carried_ids,
            "valid_from_story_time": str(valid_from_story_time) if valid_from_story_time else None,
            "valid_to_story_time": str(valid_to_story_time) if valid_to_story_time else None,
            "identity_pack_version_id": str(identity_pack_version_id) if identity_pack_version_id else None,
            "reference_media": reference_entries,
        }
        state_hash = content_hash(content)
        row = self.repo.insert(
            "entity_state_revisions",
            {
                "entity_id": entity_id,
                "revision_no": revision_no,
                "label": clean_label,
                "age": age,
                "wardrobe": str(wardrobe or ""),
                "condition": str(condition or ""),
                "carried_prop_entity_ids_json": carried_ids,
                "valid_from_story_time": str(valid_from_story_time) if valid_from_story_time else None,
                "valid_to_story_time": str(valid_to_story_time) if valid_to_story_time else None,
                "identity_pack_version_id": str(identity_pack_version_id) if identity_pack_version_id else None,
                "reference_media_version_ids_json": reference_ids,
                "content_hash": state_hash,
            },
        )
        canonical = False
        if not entity.get("canonical_state_revision_id"):
            self.repo.update(
                "explainer_entities", entity_id, {"canonical_state_revision_id": str(row["id"])}
            )
            canonical = True
        return {
            "state_revision": row,
            "entity_state_revision_id": str(row["id"]),
            "entity_id": entity_id,
            "video_id": video_id,
            "revision_no": revision_no,
            "content_hash": state_hash,
            "age": age,
            "carried_prop_entity_ids": carried_ids,
            "reference_media_version_ids": reference_ids,
            "identity_pack_version_id": str(identity_pack_version_id) if identity_pack_version_id else None,
            "identity_pack_version_status": pack_status,
            "is_canonical": canonical,
            "age_changes_across_long_spans_required": True,
        }

    def _revisions(self, entity_id: str) -> list[dict[str, Any]]:
        return self.repo.list_where(
            "entity_state_revisions", {"entity_id": entity_id}, order_by="revision_no", descending=False
        )

    def assert_state_matches_story_time(
        self, *, entity_id: str, story_time: str, tolerance_years: int = 1
    ) -> None:
        """Fail closed when no state revision window covers ``story_time``.

        Also rejects two revisions that cover the same story time with
        contradictory ages, which is how "age must change across long time
        spans" is enforced: a long span needs a new state, not a reused one.
        """

        if tolerance_years < 0:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "tolerance_years 不能为负", {"tolerance_years": tolerance_years}
            )
        self.repo.get("explainer_entities", entity_id)
        story_year = _story_year(story_time, field="story_time")
        revisions = self._revisions(entity_id)
        if not revisions:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "该实体还没有人物状态版本，不能校验故事时间",
                {"entity_id": entity_id, "story_time": str(story_time)},
            )
        matching: list[dict[str, Any]] = []
        for revision in revisions:
            lower = (
                _story_year(str(revision["valid_from_story_time"]), field="valid_from_story_time")
                - tolerance_years
                if revision.get("valid_from_story_time")
                else None
            )
            upper = (
                _story_year(str(revision["valid_to_story_time"]), field="valid_to_story_time")
                + tolerance_years
                if revision.get("valid_to_story_time")
                else None
            )
            if (lower is None or story_year >= lower) and (upper is None or story_year <= upper):
                matching.append(revision)
        if not matching:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "人物状态版本的时间窗不覆盖该故事时间，请先补充该时期的状态版本",
                {
                    "entity_id": entity_id,
                    "story_time": str(story_time),
                    "story_year": story_year,
                    "tolerance_years": tolerance_years,
                    "revision_windows": [
                        {
                            "entity_state_revision_id": str(item["id"]),
                            "valid_from_story_time": item.get("valid_from_story_time"),
                            "valid_to_story_time": item.get("valid_to_story_time"),
                            "age": item.get("age"),
                        }
                        for item in revisions
                    ],
                },
            )
        ages = {int(item["age"]) for item in matching if item.get("age") is not None}
        if len(ages) > 1 and (max(ages) - min(ages)) > tolerance_years:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "同一故事时间段内的人物年龄互相矛盾，长时间跨度必须使用新的状态版本",
                {
                    "entity_id": entity_id,
                    "story_time": str(story_time),
                    "conflicting_ages": sorted(ages),
                    "revision_ids": [str(item["id"]) for item in matching],
                },
            )

    # ------------------------------------------------------------------ slots
    def register_reference_slots(
        self,
        *,
        entity_id: str,
        identity_pack_version_id: str | None,
        slots: Mapping[str, str],
        requirement: str = "AUTO",
        entity_state_revision_id: str | None = None,
    ) -> dict[str, Any]:
        """Validate and register the reference slots of one entity.

        ``FRONT``/``LEFT``/``RIGHT`` are required for a three-view character;
        ``BACK``/``FACE`` stay optional and a one-shot background character needs
        only a single reference.  A slot whose hash cannot be verified is
        recorded as ``MISSING_SLOT`` instead of being assumed present.
        """

        entity = self.repo.get("explainer_entities", entity_id)
        project_id = str(entity["project_id"])
        video_id = str(entity["video_id"])
        requirement_value = str(requirement or "AUTO").strip().upper()
        if requirement_value not in REQUIREMENTS:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                f"参考槽位要求不合法：{requirement_value!r}",
                {"requirement": requirement_value, "allowed": sorted(REQUIREMENTS)},
            )
        if requirement_value == "AUTO":
            is_person = str(entity["entity_type"]) in {
                EntityType.REAL_PERSON.value,
                EntityType.FICTIONAL_CHARACTER.value,
            }
            resolved_requirement = (
                "SINGLE_REFERENCE" if bool(entity.get("descriptive_only")) or not is_person else "THREE_VIEW"
            )
        else:
            resolved_requirement = requirement_value
        required_slots = (
            list(REQUIRED_THREE_VIEW_SLOTS) if resolved_requirement == "THREE_VIEW" else list(SINGLE_REFERENCE_SLOTS)
        )
        optional_slots = [
            item for item in STANDARD_CHARACTER_SLOTS if item not in required_slots
        ]
        pack_version = None
        if identity_pack_version_id:
            pack_version = self._pack_version(str(identity_pack_version_id))
            if pack_version is None:
                raise ExplainerContractError(
                    "NOT_FOUND",
                    "引用的角色身份包版本不存在",
                    {"identity_pack_version_id": str(identity_pack_version_id)},
                )
            if str(pack_version["project_id"]) != project_id:
                raise ExplainerContractError(
                    "INVALID_REQUEST",
                    "引用的角色身份包版本不属于该项目",
                    {"identity_pack_version_id": str(identity_pack_version_id)},
                )
        declared, entries, missing_roles, missing_reasons = _resolve_declared_slots(
            self.repo, project_id=project_id, slots=slots
        )
        missing_required = [role for role in required_slots if role not in declared]
        if missing_required:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "参考槽位不满足该角色的必需视角",
                {
                    "entity_id": entity_id,
                    "requirement": resolved_requirement,
                    "missing_required_slots": missing_required,
                    "required_slots": required_slots,
                },
            )
        unverified_required = [role for role in required_slots if role in missing_roles]
        slot_hashes = _slot_hashes({item["slot_kind"]: item for item in entries})
        verified_slot_hashes: dict[str, Any] = {}
        for role in declared:
            entry = next((item for item in entries if item["slot_kind"] == role), None)
            verified_slot_hashes[role] = str(entry["sha256"]) if entry else MISSING_SLOT
        unexpected_slots = [
            role for role in declared if role not in required_slots and role not in optional_slots
        ]
        target_revision = self._target_state_revision(
            entity_id=entity_id, entity_state_revision_id=entity_state_revision_id
        )
        reference_ids = [str(item["media_version_id"]) for item in entries]
        self.repo.update(
            "entity_state_revisions",
            str(target_revision["id"]),
            {
                "reference_media_version_ids_json": reference_ids,
                "identity_pack_version_id": (
                    str(identity_pack_version_id) if identity_pack_version_id else None
                ),
            },
        )
        status = VERIFIED if not missing_roles else MISSING_SLOT
        return {
            "entity_id": entity_id,
            "entity_code": str(entity["code"]),
            "video_id": video_id,
            "project_id": project_id,
            "identity_pack_version_id": str(identity_pack_version_id) if identity_pack_version_id else None,
            "identity_pack_version_status": str(pack_version["status"]) if pack_version else None,
            "identity_pack_version_approved": bool(
                pack_version and str(pack_version["status"]) == PackVersionStatus.APPROVED.value
            ),
            "requirement": resolved_requirement,
            "required_slots": required_slots,
            "optional_slots": optional_slots,
            "declared_slots": declared,
            "declared_media_version_ids": [declared[role] for role in declared],
            "reference_media_version_ids": reference_ids,
            "slot_hashes_json": slot_hashes,
            "verified_slot_hashes_json": verified_slot_hashes,
            "verified_entries": entries,
            "missing_slots": sorted(missing_roles),
            "missing_slot_reasons": dict(missing_reasons),
            "missing_required_slots": sorted(unverified_required),
            "unexpected_slots": sorted(unexpected_slots),
            "entity_state_revision_id": str(target_revision["id"]),
            "status": status,
            "slot_rules_source": "local_drama.domain.character_identity_packs",
            "generation_inputs_are_single_view_files": True,
            "three_view_sheet_is_display_only": True,
        }

    def _target_state_revision(
        self, *, entity_id: str, entity_state_revision_id: str | None
    ) -> dict[str, Any]:
        if entity_state_revision_id:
            revision = self.repo.get("entity_state_revisions", str(entity_state_revision_id))
            if str(revision["entity_id"]) != entity_id:
                raise ExplainerContractError(
                    "INVALID_REQUEST",
                    "状态版本不属于该实体",
                    {"entity_id": entity_id, "entity_state_revision_id": str(entity_state_revision_id)},
                )
            return revision
        entity = self.repo.get("explainer_entities", entity_id)
        canonical_id = entity.get("canonical_state_revision_id")
        if canonical_id:
            return self.repo.get("entity_state_revisions", str(canonical_id))
        revisions = self._revisions(entity_id)
        if not revisions:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "该实体还没有人物状态版本，请先创建状态版本再登记参考槽位",
                {"entity_id": entity_id},
            )
        return revisions[-1]

    # ------------------------------------------------------------------ run inputs
    def register_machine_temporary_input(
        self,
        *,
        run_id: str,
        project_id: str,
        video_id: str,
        entity_id: str,
        slots: Mapping[str, str],
        entity_state_revision_id: str | None = None,
        expected_draft_hash: str | None = None,
    ) -> dict[str, Any]:
        """Freeze a draft identity input for one run under ``MACHINE_TEMPORARY``.

        Mirrors the legacy production-session convention: the draft hash is
        revalidated on every read, no human approval id is ever written, and a
        previous ACTIVE input for the same (run, entity) is superseded instead of
        being deleted.
        """

        run = self.repo.get("explainer_runs", run_id)
        if str(run["project_id"]) != project_id or str(run["video_id"]) != video_id:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "运行不属于该项目或该解说作品",
                {"run_id": run_id, "project_id": project_id, "video_id": video_id},
            )
        entity = self.repo.get("explainer_entities", entity_id)
        if str(entity["video_id"]) != video_id or str(entity["project_id"]) != project_id:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "实体不属于该项目或该解说作品",
                {"entity_id": entity_id, "video_id": video_id, "project_id": project_id},
            )
        state_revision = None
        if entity_state_revision_id:
            state_revision = self.repo.get("entity_state_revisions", str(entity_state_revision_id))
            if str(state_revision["entity_id"]) != entity_id:
                raise ExplainerContractError(
                    "INVALID_REQUEST",
                    "状态版本不属于该实体",
                    {"entity_id": entity_id, "entity_state_revision_id": str(entity_state_revision_id)},
                )
        declared, entries, missing_roles, missing_reasons = _resolve_declared_slots(
            self.repo, project_id=project_id, slots=slots
        )
        slot_hashes = _slot_hashes({item["slot_kind"]: item for item in entries})
        verified_slot_hashes: dict[str, Any] = {}
        for role in declared:
            entry = next((item for item in entries if item["slot_kind"] == role), None)
            verified_slot_hashes[role] = str(entry["sha256"]) if entry else MISSING_SLOT
        identity_pack_version_id = (
            str(state_revision["identity_pack_version_id"])
            if state_revision and state_revision.get("identity_pack_version_id")
            else None
        )
        draft_hash = _identity_draft_hash(
            run_id=run_id,
            video_id=video_id,
            entity_id=entity_id,
            entity_state_revision_id=str(state_revision["id"]) if state_revision else None,
            snapshot_kind=MACHINE_TEMPORARY,
            slot_hashes=slot_hashes,
        )
        if missing_roles:
            verification_status = MISSING_SLOT
        elif expected_draft_hash is not None and str(expected_draft_hash) != draft_hash:
            verification_status = DRIFTED
        else:
            verification_status = VERIFIED
        previous = self.repo.list_where(
            "run_identity_inputs", {"run_id": run_id, "entity_id": entity_id, "status": "ACTIVE"}
        )
        superseded_ids: list[str] = []
        for item in previous:
            self.repo.update("run_identity_inputs", str(item["id"]), {"status": "SUPERSEDED"})
            superseded_ids.append(str(item["id"]))
        row = self.repo.insert(
            "run_identity_inputs",
            {
                "run_id": run_id,
                "video_id": video_id,
                "project_id": project_id,
                "entity_id": entity_id,
                "entity_state_revision_id": str(state_revision["id"]) if state_revision else None,
                "identity_pack_id": None,
                "identity_pack_version_id": identity_pack_version_id,
                "snapshot_kind": MACHINE_TEMPORARY,
                "slot_hashes_json": slot_hashes,
                "verified_slot_hashes_json": verified_slot_hashes,
                "draft_hash": draft_hash,
                "verified_at": utc_now_iso(),
                "verification_status": verification_status,
                # A machine input never carries a human approval.
                "human_approval_id": None,
                "status": "ACTIVE",
            },
        )
        return {
            "run_identity_input_id": str(row["id"]),
            "run_id": run_id,
            "video_id": video_id,
            "project_id": project_id,
            "entity_id": entity_id,
            "entity_state_revision_id": str(state_revision["id"]) if state_revision else None,
            "snapshot_kind": MACHINE_TEMPORARY,
            "declared_slots": declared,
            "slot_hashes_json": slot_hashes,
            "verified_slot_hashes_json": verified_slot_hashes,
            "missing_slots": sorted(missing_roles),
            "missing_slot_reasons": dict(missing_reasons),
            "draft_hash": draft_hash,
            "expected_draft_hash": str(expected_draft_hash) if expected_draft_hash else None,
            "draft_hash_revalidated": expected_draft_hash is not None,
            "verification_status": verification_status,
            "human_approval_id": None,
            "human_approved": False,
            "superseded_run_identity_input_ids": superseded_ids,
            "ready": verification_status == VERIFIED,
        }

    def verify_identity_inputs(self, *, run_id: str) -> dict[str, Any]:
        """Re-validate every registered slot of a run against its recorded hash."""

        rows = self.repo.list_where("run_identity_inputs", {"run_id": run_id, "status": "ACTIVE"})
        entities: list[dict[str, Any]] = []
        overall: str = VERIFIED if rows else MISSING_SLOT
        for row in rows:
            recorded = dict(row.get("slot_hashes_json") or {})
            previously_verified = dict(row.get("verified_slot_hashes_json") or {})
            project_id = str(row["project_id"])
            recomputed: dict[str, Any] = {}
            drifted_slots: list[str] = []
            missing_slots: list[str] = []
            for role in sorted({*recorded, *previously_verified}):
                recorded_entry = recorded.get(role)
                expected_sha = (
                    str(recorded_entry.get("sha256") or "")
                    if isinstance(recorded_entry, Mapping)
                    else str(recorded_entry or "")
                )
                media_version_id = self._recorded_media_version_id(row, role)
                if not media_version_id:
                    recomputed[role] = MISSING_SLOT
                    missing_slots.append(role)
                    continue
                try:
                    media = _verify_slot_media(
                        self.repo,
                        project_id=project_id,
                        role=role,
                        media_version_id=media_version_id,
                    )
                except ExplainerContractError:
                    recomputed[role] = MISSING_SLOT
                    missing_slots.append(role)
                    continue
                if not media["verifiable"]:
                    recomputed[role] = MISSING_SLOT
                    missing_slots.append(role)
                    continue
                actual_sha = str(media["sha256"])
                if expected_sha and actual_sha != expected_sha:
                    recomputed[role] = actual_sha
                    drifted_slots.append(role)
                    continue
                recomputed[role] = actual_sha
            recomputed_draft_hash = _identity_draft_hash(
                run_id=str(row["run_id"]),
                video_id=str(row["video_id"]),
                entity_id=str(row["entity_id"]),
                entity_state_revision_id=(
                    str(row["entity_state_revision_id"]) if row.get("entity_state_revision_id") else None
                ),
                snapshot_kind=str(row["snapshot_kind"]),
                slot_hashes=recorded,
            )
            draft_hash_unchanged = recomputed_draft_hash == str(row["draft_hash"])
            if drifted_slots or not draft_hash_unchanged:
                status = DRIFTED
            elif missing_slots:
                status = MISSING_SLOT
            else:
                status = VERIFIED
            self.repo.update(
                "run_identity_inputs",
                str(row["id"]),
                {
                    "verified_slot_hashes_json": recomputed,
                    "verified_at": utc_now_iso(),
                    "verification_status": status,
                },
            )
            entities.append(
                {
                    "run_identity_input_id": str(row["id"]),
                    "entity_id": str(row["entity_id"]),
                    "entity_state_revision_id": row.get("entity_state_revision_id"),
                    "snapshot_kind": str(row["snapshot_kind"]),
                    "status": status,
                    "slot_hashes_json": recorded,
                    "verified_slot_hashes_json": recomputed,
                    "drifted_slots": sorted(set(drifted_slots)),
                    "missing_slots": sorted(set(missing_slots)),
                    "draft_hash": str(row["draft_hash"]),
                    "recomputed_draft_hash": recomputed_draft_hash,
                    "draft_hash_unchanged": draft_hash_unchanged,
                    "human_approval_id": row.get("human_approval_id"),
                }
            )
            if status == DRIFTED:
                overall = DRIFTED
            elif status == MISSING_SLOT and overall != DRIFTED:
                overall = MISSING_SLOT
        return {
            "run_id": run_id,
            "status": overall,
            "entities": entities,
            "active_input_count": len(rows),
            "drifted_count": sum(1 for item in entities if item["status"] == DRIFTED),
            "missing_slot_count": sum(1 for item in entities if item["status"] == MISSING_SLOT),
            "identity_consistency_claim": "NO_100_PERCENT_CLAIM",
        }

    @staticmethod
    def _recorded_media_version_id(row: Mapping[str, Any], role: str) -> str:
        for key in ("slot_hashes_json", "verified_slot_hashes_json"):
            value = row.get(key)
            if isinstance(value, Mapping) and isinstance(value.get(role), Mapping):
                media_version_id = value[role].get("media_version_id")
                if media_version_id:
                    return str(media_version_id)
        return ""

    # ------------------------------------------------------------------ bindings
    def bind_beat_identity(
        self,
        *,
        project_id: str,
        video_id: str,
        beat_id: str,
        entity_id: str,
        identity_pack_version_id: str | None = None,
        entity_state_revision_id: str | None = None,
        reference_media_version_ids: Sequence[str] = (),
        reference_slot_roles: Sequence[str] = (),
        consumed_slot_roles: Sequence[str] = (),
        edition_id: str | None = None,
        source: str = "MACHINE_POLICY",
        human_approval_id: str | None = None,
        workflow_consumed_roles: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Bind one entity (and its references) to one visual beat.

        The declared reference slots must have been *consumed* by the workflow:
        when ``workflow_consumed_roles`` is supplied, a declared-but-unconsumed
        role raises ``CAPABILITY_UNAVAILABLE`` (the anti-fake-reference rule).
        """

        self.repo.require_explainer_project(project_id)
        beat = self.repo.get("explainer_visual_beats", beat_id)
        if str(beat["video_id"]) != video_id:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "画面段不属于该解说作品",
                {"beat_id": beat_id, "video_id": video_id},
            )
        entity = self.repo.get("explainer_entities", entity_id)
        if str(entity["video_id"]) != video_id or str(entity["project_id"]) != project_id:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "实体不属于该项目或该解说作品",
                {"entity_id": entity_id, "video_id": video_id, "project_id": project_id},
            )
        if edition_id is not None:
            edition = self.repo.get("explainer_editions", str(edition_id))
            if str(edition["video_id"]) != video_id:
                raise ExplainerContractError(
                    "INVALID_REQUEST",
                    "输出 edition 不属于该解说作品",
                    {"edition_id": str(edition_id), "video_id": video_id},
                )
        source_value = str(source or "MACHINE_POLICY").upper()
        if source_value not in SOURCE_KINDS:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                f"绑定来源不合法：{source_value!r}",
                {"source": source_value, "allowed": sorted(SOURCE_KINDS)},
            )
        if source_value == "HUMAN" and not str(human_approval_id or "").strip():
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "HUMAN 绑定必须引用真实的人工批准记录",
                {"beat_id": beat_id, "entity_id": entity_id},
            )
        declared_roles = [_slot_kind(str(role)) for role in (reference_slot_roles or ())]
        consumed_roles = [_slot_kind(str(role)) for role in (consumed_slot_roles or ())]
        if workflow_consumed_roles is not None:
            self.assert_references_consumed(
                beat_id=beat_id,
                declared_slot_roles=declared_roles,
                workflow_consumed_roles=workflow_consumed_roles,
            )
            consumed_roles = sorted({*consumed_roles, *(_slot_kind(str(role)) for role in workflow_consumed_roles)})
        reference_ids = [str(item) for item in dict.fromkeys(reference_media_version_ids or ())]
        reference_entries: list[dict[str, Any]] = []
        for media_version_id in reference_ids:
            media = self.repo.require_same_project_media(
                project_id=project_id, media_version_id=media_version_id
            )
            reference_entries.append({"media_version_id": media_version_id, "sha256": str(media["sha256"])})
        binding_body = {
            "schema_version": "localdrama.explainer.identity-binding.v1",
            "video_id": video_id,
            "edition_id": str(edition_id) if edition_id else None,
            "beat_id": beat_id,
            "entity_id": entity_id,
            "identity_pack_version_id": str(identity_pack_version_id) if identity_pack_version_id else None,
            "entity_state_revision_id": str(entity_state_revision_id) if entity_state_revision_id else None,
            "reference_media": reference_entries,
            "reference_slot_roles": declared_roles,
            "consumed_slot_roles": consumed_roles,
        }
        binding_hash = content_hash(binding_body)
        previous = self.repo.list_where(
            "entity_identity_bindings",
            {"video_id": video_id, "entity_id": entity_id, "beat_id": beat_id, "status": "ACTIVE"},
        )
        superseded_ids: list[str] = []
        for item in previous:
            self.repo.update("entity_identity_bindings", str(item["id"]), {"status": "SUPERSEDED"})
            superseded_ids.append(str(item["id"]))
        row = self.repo.insert(
            "entity_identity_bindings",
            {
                "video_id": video_id,
                "edition_id": str(edition_id) if edition_id else None,
                "entity_id": entity_id,
                "beat_id": beat_id,
                "identity_pack_version_id": (
                    str(identity_pack_version_id) if identity_pack_version_id else None
                ),
                "entity_state_revision_id": (
                    str(entity_state_revision_id) if entity_state_revision_id else None
                ),
                "reference_media_version_ids_json": reference_ids,
                "reference_slot_roles_json": declared_roles,
                "consumed_slot_roles_json": consumed_roles,
                "binding_hash": binding_hash,
                "source": source_value,
                "human_approval_id": str(human_approval_id) if human_approval_id else None,
                "status": "ACTIVE",
            },
        )
        return {
            "binding": row,
            "binding_id": str(row["id"]),
            "binding_hash": binding_hash,
            "video_id": video_id,
            "beat_id": beat_id,
            "beat_code": str(beat["code"]),
            "entity_id": entity_id,
            "entity_code": str(entity["code"]),
            "edition_id": str(edition_id) if edition_id else None,
            "identity_pack_version_id": str(identity_pack_version_id) if identity_pack_version_id else None,
            "entity_state_revision_id": (
                str(entity_state_revision_id) if entity_state_revision_id else None
            ),
            "reference_media_version_ids": reference_ids,
            "reference_slot_roles": declared_roles,
            "consumed_slot_roles": consumed_roles,
            "references_actually_consumed": set(declared_roles) <= set(consumed_roles),
            "source": source_value,
            "human_approval_id": str(human_approval_id) if human_approval_id else None,
            "human_approved": bool(human_approval_id) and source_value == "HUMAN",
            "superseded_binding_ids": superseded_ids,
        }

    def assert_references_consumed(
        self,
        *,
        beat_id: str,
        declared_slot_roles: Sequence[str],
        workflow_consumed_roles: Sequence[str],
    ) -> None:
        """The anti-fake-reference rule: declared slots must really be consumed."""

        declared = {_slot_kind(str(role)) for role in (declared_slot_roles or ())}
        consumed = {_slot_kind(str(role)) for role in (workflow_consumed_roles or ())}
        missing = sorted(declared - consumed)
        if missing:
            raise ExplainerContractError(
                "CAPABILITY_UNAVAILABLE",
                "参考图槽位未被工作流实际消费，不能声明已使用这些参考图",
                {
                    "beat_id": beat_id,
                    "declared_slot_roles": sorted(declared),
                    "workflow_consumed_roles": sorted(consumed),
                    "missing_slot_roles": missing,
                },
            )

    # ------------------------------------------------------------------ impact
    def impact_of_reference_change(
        self, *, video_id: str, identity_pack_version_id: str, actor: str = "local-user"
    ) -> dict[str, Any]:
        """Report and record what an identity reference change invalidates."""

        video = self.repo.get("explainer_videos", video_id)
        project_id = str(video["project_id"])
        plan = staleness_plan("IDENTITY_REFERENCE")
        bindings = self.repo.beats_referencing_identity(video_id, str(identity_pack_version_id))
        beats_by_id = {str(item["id"]): item for item in self.repo.beats(video_id)}
        beat_entries: list[dict[str, Any]] = []
        edition_ids: list[str] = []
        binding_hashes: list[str] = []
        for binding in bindings:
            beat_id = str(binding.get("beat_id") or "")
            beat = beats_by_id.get(beat_id)
            beat_entries.append(
                {
                    "binding_id": str(binding["id"]),
                    "beat_id": beat_id,
                    "beat_code": str(beat["code"]) if beat else None,
                    "entity_id": str(binding["entity_id"]),
                    "edition_id": binding.get("edition_id"),
                    "binding_hash": str(binding.get("binding_hash") or ""),
                }
            )
            if binding.get("edition_id") and str(binding["edition_id"]) not in edition_ids:
                edition_ids.append(str(binding["edition_id"]))
            if binding.get("binding_hash"):
                binding_hashes.append(str(binding["binding_hash"]))
        upstream_hash = content_hash(
            {
                "identity_pack_version_id": str(identity_pack_version_id),
                "binding_hashes": sorted(binding_hashes),
            }
        )
        recorded_edges: list[str] = []
        for entry in beat_entries:
            if not entry["beat_id"]:
                continue
            edge = self.repo.add_dependency(
                video_id=video_id,
                project_id=project_id,
                upstream_kind="IDENTITY_REFERENCE",
                upstream_id=str(identity_pack_version_id),
                upstream_hash=upstream_hash,
                downstream_kind="BEAT_SELECTION",
                downstream_id=entry["beat_id"],
                edition_id=entry["edition_id"],
            )
            recorded_edges.append(str(edge["id"]))
        stale_edges = self.repo.mark_dependents_stale(
            upstream_kind="IDENTITY_REFERENCE",
            upstream_id=str(identity_pack_version_id),
            downstream_kinds=["BEAT_SELECTION"],
            reason=f"IDENTITY_REFERENCE_CHANGED:{identity_pack_version_id}",
            invalidated_by=actor,
        )
        return {
            "video_id": video_id,
            "project_id": project_id,
            "identity_pack_version_id": str(identity_pack_version_id),
            "beats": beat_entries,
            "edition_ids": edition_ids,
            "invalidates": list(plan["invalidates"]),
            "preserves": list(plan["preserves"]),
            "dependency_edges_recorded": len(recorded_edges),
            "dependency_edge_ids": recorded_edges,
            "upstream_hash": upstream_hash,
            "stale_edges": [
                {"id": str(item.get("id")), "downstream_kind": item.get("downstream_kind"), "downstream_id": item.get("downstream_id")}
                for item in stale_edges
            ],
            "stale_edge_count": len(stale_edges),
            "offline_segments_preserved": "OFFSCREEN_SEGMENT" in plan["preserves"],
        }

    # ------------------------------------------------------------------ policy
    def three_view_policy(
        self, *, entity_code: str, appears_in_beat_count: int, needs_side_or_back: bool
    ) -> dict[str, Any]:
        """Decide whether a character needs a three-view sheet.

        The sheet is display-only: it must never be the default I2V input because
        a sheet contains several people in one frame.  Generation inputs are the
        separate single-view files.
        """

        clean_code = str(entity_code or "").strip()
        if not clean_code:
            raise ExplainerContractError("SCHEMA_INVALID", "entity_code 不能为空", {})
        count = int(appears_in_beat_count)
        if count < 0:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "appears_in_beat_count 不能为负", {"entity_code": clean_code}
            )
        require_three_view = count >= 2 or bool(needs_side_or_back)
        required_slots = list(REQUIRED_THREE_VIEW_SLOTS) if require_three_view else list(SINGLE_REFERENCE_SLOTS)
        if needs_side_or_back and SlotKind.BACK.value not in required_slots:
            required_slots.append(SlotKind.BACK.value)
        if require_three_view and bool(needs_side_or_back):
            reason = "SIDE_OR_BACK_VIEW_REQUIRED"
        elif require_three_view:
            reason = "RECURRING_CHARACTER_MULTI_BEAT"
        else:
            reason = "SINGLE_APPEARANCE_SINGLE_REFERENCE"
        return {
            "entity_code": clean_code,
            "appears_in_beat_count": count,
            "needs_side_or_back": bool(needs_side_or_back),
            "require_three_view": require_three_view,
            "required_slots": required_slots,
            "reason": reason,
            "optional_slots": [item for item in STANDARD_CHARACTER_SLOTS if item not in required_slots],
            "separate_single_view_files": True,
            "three_view_sheet_is_display_only": True,
            "generation_input_is_three_view_sheet": False,
            "three_view_sheet_multi_person_risk": "SHEET_CONTAINS_MULTIPLE_VIEWS_IN_ONE_FRAME",
        }

    # ------------------------------------------------------------------ review
    def identity_review_queue(
        self,
        *,
        video_id: str,
        confidence_threshold: float = 0.6,
        detections: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """Build the small human review list for uncertain identifications.

        The detector never claims 100% identification: low-confidence and
        ``UNKNOWN`` detections are queued for a human instead of being asserted.
        """

        self.repo.get("explainer_videos", video_id)
        threshold = float(confidence_threshold)
        if threshold < 0 or threshold > 1:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "confidence_threshold 必须在 0–1 之间", {"confidence_threshold": threshold}
            )
        review: list[dict[str, Any]] = []
        for row in self.repo.list_where("run_identity_inputs", {"video_id": video_id}):
            status = str(row.get("verification_status") or UNVERIFIED)
            if status == VERIFIED:
                continue
            review.append(
                {
                    "kind": "IDENTITY_INPUT_UNCERTAIN",
                    "reason_code": status,
                    "entity_id": str(row["entity_id"]),
                    "run_id": str(row["run_id"]),
                    "run_identity_input_id": str(row["id"]),
                    "snapshot_kind": str(row.get("snapshot_kind") or ""),
                    "human_approval_id": row.get("human_approval_id"),
                    "confidence": None,
                }
            )
        for binding in self.repo.list_where(
            "entity_identity_bindings", {"video_id": video_id, "status": "ACTIVE"}
        ):
            if binding.get("identity_pack_version_id"):
                continue
            review.append(
                {
                    "kind": "UNKNOWN_IDENTITY_REFERENCE",
                    "reason_code": "UNKNOWN",
                    "entity_id": str(binding["entity_id"]),
                    "beat_id": binding.get("beat_id"),
                    "binding_id": str(binding["id"]),
                    "source": str(binding.get("source") or ""),
                    "confidence": None,
                }
            )
        for index, detection in enumerate(detections or ()):
            if not isinstance(detection, Mapping):
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "detections 必须是对象列表", {"index": index}
                )
            identifier = str(detection.get("detection_id") or detection.get("id") or f"D{index + 1}")
            status = str(detection.get("status") or detection.get("identity_status") or "").upper()
            confidence = detection.get("confidence")
            numeric_confidence = None if confidence is None else float(confidence)
            if numeric_confidence is not None and (numeric_confidence < 0 or numeric_confidence > 1):
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "检测置信度必须在 0–1 之间",
                    {"detection_id": identifier, "confidence": numeric_confidence},
                )
            if status == "UNKNOWN":
                reason = "UNKNOWN"
            elif numeric_confidence is None:
                reason = "CONFIDENCE_NOT_MEASURED"
            elif numeric_confidence < threshold:
                reason = "LOW_CONFIDENCE"
            else:
                continue
            review.append(
                {
                    "kind": "DETECTION_UNCERTAIN",
                    "reason_code": reason,
                    "detection_id": identifier,
                    "entity_id": detection.get("entity_id"),
                    "entity_code": detection.get("entity_code"),
                    "beat_id": detection.get("beat_id"),
                    "beat_code": detection.get("beat_code"),
                    "media_version_id": detection.get("media_version_id"),
                    "confidence": numeric_confidence,
                }
            )
        return {
            "video_id": video_id,
            "confidence_threshold": threshold,
            "review_list": review,
            "review_count": len(review),
            "auto_asserted_count": 0,
            "detector_claims_full_identification": False,
            "identity_consistency_claim": "NO_100_PERCENT_CLAIM",
            "limitations": (
                "机器不会声称 100% 识别一致；低置信度与 UNKNOWN 判定只进入人工小清单，"
                "在人工确认前不作为已确认身份使用。"
            ),
        }

    # ------------------------------------------------------------------ helpers
    def _pack_version(self, identity_pack_version_id: str) -> dict[str, Any] | None:
        row = self.repo.query_one(
            "SELECT id, pack_id, project_id, story_asset_id, version_no, status FROM character_identity_pack_versions WHERE id = ?",
            (str(identity_pack_version_id),),
        )
        return dict(row) if row is not None else None
