"""Explainer delivery and publication: package manifest, licenses, presets, receipts.

The delivery layer turns one *frozen* composition revision into a package that can
be handed to an operator or uploaded by a configured publisher adapter
(《解说工厂完整设计与开发规范》§19, §22).

What this module deliberately does NOT do:

* it never regenerates, re-renders or re-exports an upstream artifact in order to
  publish it: publication consumes an existing frozen render/package, and the
  package binds the exact ``render_id`` + ``composition_revision_id`` revision —
  never "the latest";
* it never renames a preview into a publishable state: a package whose license
  scope is unverified, or which is missing a recorded platform preset, becomes
  ``BLOCKED`` with its blockers written down;
* it never invents a successful upload: without authorization or adapter
  capability it records ``NEEDS_MANUAL_PUBLISH`` with an outbound attempt count of
  zero and a complete manual handoff record; on timeout without query capability it
  records ``PUBLICATION_RESULT_UNKNOWN`` and waits for reconciliation;
* it never sends before querying an existing remote session, and never re-sends a
  changed package hash as if it were the same object;
* it never presents a corner watermark as a platform AI-disclosure field — the two
  are returned separately and the watermark field is explicitly false;
* it never claims a built-in platform preset is verified: every built-in preset is
  an ``UNVERIFIED_PLACEHOLDER`` that must be re-checked against official
  documentation and the real account capability before going live;
* it never states that all platforms share one duration limit or one audio-track
  limit, and it never calls a license evaluation a legal conclusion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from local_drama.domain.explainers.contracts import (
    AspectRatio,
    ContentKind,
    ExplainerContractError,
    ExplainerErrorCode,
    PublicationReceiptStatus,
    PublicationStatus,
    is_sha256,
    utc_now_iso,
)
from local_drama.domain.explainers.policies import (
    AssetLicense,
    LicenseEvaluation,
    evaluate_license_scope,
)
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository, decode_rows

#: Base roles of the package roster (design §19 / §22).  A role may carry a
#: qualifier as ``BASE:qualifier`` — for example ``SUBTITLE_SRT:zh-CN`` — so one
#: 6-field :class:`PackageItem` can still describe per-language subtitle files.
CLEAN_MASTER = "CLEAN_MASTER"
BURNED_MASTER = "BURNED_MASTER"
SUBTITLE = "SUBTITLE"
NARRATION_STEM = "NARRATION_STEM"
MUSIC_STEM = "MUSIC_STEM"
SFX_STEM = "SFX_STEM"
COVER = "COVER"
TITLE = "TITLE"
SUMMARY = "SUMMARY"
CHAPTERS = "CHAPTERS"
CITATIONS = "CITATIONS"
QC_REPORT = "QC_REPORT"
LICENSE_LIST = "LICENSE_LIST"

SUBTITLE_FORMATS: tuple[str, ...] = ("SRT", "VTT", "ASS")

#: Roles whose item must bind a real ``media_versions`` row and a matching sha256.
MEDIA_ITEM_ROLES: frozenset[str] = frozenset(
    {CLEAN_MASTER, BURNED_MASTER, NARRATION_STEM, MUSIC_STEM, SFX_STEM, COVER}
)

#: Roles the package roster requires (design §19 / §22).
REQUIRED_PACKAGE_ROLES: tuple[str, ...] = (
    CLEAN_MASTER,
    BURNED_MASTER,
    COVER,
    TITLE,
    SUMMARY,
    CHAPTERS,
    CITATIONS,
    QC_REPORT,
    LICENSE_LIST,
)

#: Roles that are part of the roster vocabulary but delivery-optional.
OPTIONAL_PACKAGE_ROLES: tuple[str, ...] = (NARRATION_STEM, MUSIC_STEM, SFX_STEM)

KNOWN_PACKAGE_ROLES: frozenset[str] = frozenset(
    {SUBTITLE, *REQUIRED_PACKAGE_ROLES, *OPTIONAL_PACKAGE_ROLES}
)

#: Requested publication actions this service accepts.
PUBLICATION_ACTIONS: frozenset[str] = frozenset(
    {"PUBLISH", "UPLOAD_ONLY", "UPLOAD_AND_PUBLISH", "SCHEDULED_PUBLISH"}
)

#: Terminal receipt statuses that still need an operator to reconcile them.
RECONCILIATION_STATUSES: tuple[str, ...] = (
    PublicationReceiptStatus.PUBLICATION_RESULT_UNKNOWN.value,
    PublicationReceiptStatus.TIMEOUT.value,
)

#: Platform codes with a built-in (unverified) preset.
BUILTIN_PLATFORM_CODES: tuple[str, ...] = ("YOUTUBE", "TIKTOK", "BILIBILI", "DOUYIN", "WEIXIN_CHANNELS")

PRESET_VERSION = "builtin-placeholder-v1"

_PLACEHOLDER_NOTE = (
    "内置预设只是占位数据：容器/编码/时长/字幕与音轨上限必须在上线前"
    "依据平台官方文档与当前账号实际能力重新核验，核验后写入 preset 与核验日期。"
)


def _placeholder_preset(
    *,
    platform_code: str,
    container: str,
    video_codec: str,
    audio_codec: str,
    preferred_aspect_ratios: Sequence[str],
    subtitle_handling: str,
    safe_area: Mapping[str, Any],
    cover: Mapping[str, Any],
    ai_disclosure_field: str,
    publish_permission: str,
    notes: Sequence[str],
) -> dict[str, Any]:
    """Build one versioned preset whose unverifiable limits stay explicitly unknown."""

    return {
        "preset_version": PRESET_VERSION,
        "platform_code": platform_code,
        "verification_status": "UNVERIFIED_PLACEHOLDER",
        "verification_date": None,
        "requires_operator_verification": True,
        "note": _PLACEHOLDER_NOTE,
        "container": {"value": container, "value_status": "PLACEHOLDER_UNVERIFIED"},
        "video_codec": {"value": video_codec, "value_status": "PLACEHOLDER_UNVERIFIED"},
        "audio_codec": {"value": audio_codec, "value_status": "PLACEHOLDER_UNVERIFIED"},
        "dimensions": {
            "preferred_aspect_ratios": list(preferred_aspect_ratios),
            "max_width": None,
            "max_height": None,
            "value_status": "PLACEHOLDER_UNVERIFIED",
            "note": "分辨率上限必须按账号与平台当前规则核验，不在此处写死。",
        },
        "duration_range_seconds": {
            "min": None,
            "max": None,
            "value_status": "PLACEHOLDER_UNVERIFIED",
            "note": "各平台时长上限不同且会变化，必须上线前核验；不得假定平台之间一致。",
        },
        "audio_track_limit": {
            "max_tracks": None,
            "value_status": "PLACEHOLDER_UNVERIFIED",
            "note": "各平台音轨数量上限不同，必须上线前核验；不得假定平台之间一致。",
        },
        "subtitle_handling": {
            "value": subtitle_handling,
            "soft_subtitle_supported": None,
            "burned_in_allowed": None,
            "value_status": "PLACEHOLDER_UNVERIFIED",
        },
        "safe_area": dict(safe_area),
        "cover": dict(cover),
        "ai_disclosure_field": {
            "field_name": ai_disclosure_field,
            "value_status": "PLACEHOLDER_UNVERIFIED",
            "note": "字段名必须按平台当前上传界面/接口核验。",
        },
        "publish_permission": {
            "value": publish_permission,
            "value_status": "PLACEHOLDER_UNVERIFIED",
        },
        "platform_notes": list(notes),
    }


#: Built-in presets.  Every one is an unverified placeholder carrying the exact
#: fields the design requires, with the unverifiable limits left explicitly null.
PLATFORM_PRESETS: dict[str, dict[str, Any]] = {
    "YOUTUBE": _placeholder_preset(
        platform_code="YOUTUBE",
        container="MP4",
        video_codec="H.264",
        audio_codec="AAC",
        preferred_aspect_ratios=[AspectRatio.WIDE.value, AspectRatio.PORTRAIT.value, AspectRatio.SQUARE.value],
        subtitle_handling="SOFT_SUBTITLE_SIDECAR_PREFERRED",
        safe_area={"left_px": 96, "top_px": 54, "right_px": 1824, "bottom_px": 900},
        cover={"width_px": 1280, "height_px": 720, "aspect_ratio": AspectRatio.WIDE.value},
        ai_disclosure_field="altered_or_synthetic_content_disclosure",
        publish_permission="REQUIRES_ACCOUNT_AUTHORIZATION",
        notes=[
            "长视频与 Shorts 的时长/规格不同，必须按目标投放位核验。",
            "AI 披露字段在账号级/视频级设置，角落水印不能替代该字段。",
        ],
    ),
    "TIKTOK": _placeholder_preset(
        platform_code="TIKTOK",
        container="MP4",
        video_codec="H.264",
        audio_codec="AAC",
        preferred_aspect_ratios=[AspectRatio.PORTRAIT.value, AspectRatio.SQUARE.value],
        subtitle_handling="BURNED_OR_PLATFORM_CAPTION",
        safe_area={"left_px": 120, "top_px": 200, "right_px": 960, "bottom_px": 1520},
        cover={"width_px": 1080, "height_px": 1920, "aspect_ratio": AspectRatio.PORTRAIT.value},
        ai_disclosure_field="ai_generated_content_label",
        publish_permission="REQUIRES_ACCOUNT_AUTHORIZATION",
        notes=[
            "竖屏优先；封面与安全区随界面版本变化，必须核验当前界面。",
            "时长上限按普通视频/其他投放位分别核验。",
        ],
    ),
    "BILIBILI": _placeholder_preset(
        platform_code="BILIBILI",
        container="MP4",
        video_codec="H.264",
        audio_codec="AAC",
        preferred_aspect_ratios=[AspectRatio.WIDE.value, AspectRatio.PORTRAIT.value],
        subtitle_handling="SOFT_SUBTITLE_OR_BURNED",
        safe_area={"left_px": 96, "top_px": 54, "right_px": 1824, "bottom_px": 960},
        cover={"width_px": 1146, "height_px": 717, "aspect_ratio": AspectRatio.WIDE.value},
        ai_disclosure_field="ai_content_declaration",
        publish_permission="REQUIRES_COOKIE_OR_OPENAPI_AUTHORIZATION",
        notes=[
            "开放平台接口需要单独申请；未获授权时必须走人工发布交接。",
            "稿件类型（自制/转载）与 AI 声明字段必须在上传界面确认。",
        ],
    ),
    "DOUYIN": _placeholder_preset(
        platform_code="DOUYIN",
        container="MP4",
        video_codec="H.264",
        audio_codec="AAC",
        preferred_aspect_ratios=[AspectRatio.PORTRAIT.value, AspectRatio.SQUARE.value],
        subtitle_handling="BURNED_SUBTITLE_PREFERRED",
        safe_area={"left_px": 90, "top_px": 180, "right_px": 990, "bottom_px": 1560},
        cover={"width_px": 1080, "height_px": 1440, "aspect_ratio": AspectRatio.THREE_FOUR.value},
        ai_disclosure_field="ai_generated_content_declaration",
        publish_permission="REQUIRES_ACCOUNT_AUTHORIZATION",
        notes=[
            "竖屏优先；挂载组件与安全区随版本变化，必须核验当前界面。",
            "AI 声明是上传时的独立字段，不等于画面水印。",
        ],
    ),
    "WEIXIN_CHANNELS": _placeholder_preset(
        platform_code="WEIXIN_CHANNELS",
        container="MP4",
        video_codec="H.264",
        audio_codec="AAC",
        preferred_aspect_ratios=[AspectRatio.PORTRAIT.value, AspectRatio.WIDE.value],
        subtitle_handling="BURNED_SUBTITLE_PREFERRED",
        safe_area={"left_px": 108, "top_px": 192, "right_px": 972, "bottom_px": 1632},
        cover={"width_px": 1080, "height_px": 1260, "aspect_ratio": AspectRatio.THREE_FOUR.value},
        ai_disclosure_field="ai_content_declaration",
        publish_permission="REQUIRES_ACCOUNT_AUTHORIZATION",
        notes=[
            "视频号发布能力与接口权限按账号而定，必须核验账号实际能力。",
            "时长与音轨上限必须按官方文档核验。",
        ],
    ),
}


@dataclass(frozen=True)
class PackageItem:
    """One package entry.  ``role`` may carry a qualifier as ``BASE:qualifier``."""

    role: str
    rel_path: str
    media_version_id: str | None
    sha256: str
    byte_size: int
    required: bool

    def __post_init__(self) -> None:
        if not str(self.role or "").strip():
            raise ExplainerContractError("SCHEMA_INVALID", "包条目必须声明 role", {})
        if not str(self.rel_path or "").strip():
            raise ExplainerContractError("SCHEMA_INVALID", "包条目必须声明 rel_path", {"role": self.role})
        if not is_sha256(self.sha256):
            raise ExplainerContractError(
                ExplainerErrorCode.OUTPUT_VALIDATION_FAILED.value,
                "包条目必须携带真实 sha256",
                {"role": self.role, "rel_path": self.rel_path, "sha256": self.sha256},
            )
        if int(self.byte_size) < 0:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "包条目字节数不能为负", {"role": self.role, "byte_size": self.byte_size}
            )

    @property
    def base_role(self) -> str:
        return self.role.split(":", 1)[0].strip().upper()

    @property
    def qualifier(self) -> str | None:
        parts = self.role.split(":", 1)
        return parts[1].strip() if len(parts) > 1 and parts[1].strip() else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "base_role": self.base_role,
            "qualifier": self.qualifier,
            "rel_path": self.rel_path,
            "media_version_id": self.media_version_id,
            "sha256": self.sha256,
            "byte_size": int(self.byte_size),
            "required": bool(self.required),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> PackageItem:
        if not isinstance(payload, Mapping):
            raise ExplainerContractError("SCHEMA_INVALID", "包条目必须是对象", {})
        media_version_id = payload.get("media_version_id")
        return cls(
            role=str(payload.get("role") or ""),
            rel_path=str(payload.get("rel_path") or ""),
            media_version_id=None if media_version_id in (None, "") else str(media_version_id),
            sha256=str(payload.get("sha256") or "").lower(),
            byte_size=int(payload.get("byte_size") or 0),
            required=bool(payload.get("required", False)),
        )


@dataclass(frozen=True)
class PublisherCapability:
    """One (adapter, platform, account) capability record.  Declared, never assumed."""

    adapter_code: str
    platform_code: str
    supports_upload: bool
    supports_query: bool
    supports_idempotency: bool
    authorized: bool
    account_ref: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "adapter_code": self.adapter_code,
            "platform_code": self.platform_code,
            "supports_upload": bool(self.supports_upload),
            "supports_query": bool(self.supports_query),
            "supports_idempotency": bool(self.supports_idempotency),
            "authorized": bool(self.authorized),
            "account_ref": self.account_ref,
            "idempotency_scope": ["platform_code", "account_ref", "package_hash"],
        }


def _capability_from_ref(ref: Any) -> PublisherCapability:
    """Normalise a declared capability mapping (or an existing capability value)."""

    if isinstance(ref, PublisherCapability):
        return ref
    if not isinstance(ref, Mapping):
        raise ExplainerContractError("SCHEMA_INVALID", "账号授权引用必须是对象", {"value": repr(ref)[:100]})
    return PublisherCapability(
        adapter_code=str(ref.get("adapter_code") or "UNCONFIGURED_ADAPTER"),
        platform_code=str(ref.get("platform_code") or ""),
        supports_upload=bool(ref.get("supports_upload", False)),
        supports_query=bool(ref.get("supports_query", False)),
        supports_idempotency=bool(ref.get("supports_idempotency", False)),
        authorized=bool(ref.get("authorized", False)),
        account_ref=None if ref.get("account_ref") in (None, "") else str(ref.get("account_ref")),
    )


class ExplainerDeliveryService:
    """Package building, license gating, presets and publication receipts."""

    def __init__(self, repo: ExplainerRepository) -> None:
        self.repo = repo

    # ------------------------------------------------------------------ scope
    def _require_scope(self, *, project_id: str, video_id: str, edition_id: str) -> dict[str, Any]:
        self.repo.require_explainer_project(project_id)
        video = self.repo.require_video_for_project(project_id)
        if str(video["id"]) != str(video_id):
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "视频与项目不匹配",
                {"project_id": project_id, "video_id": video_id, "video_project_id": video["project_id"]},
            )
        edition = self.repo.find("explainer_editions", edition_id)
        if edition is None:
            raise ExplainerContractError(
                ExplainerErrorCode.NOT_FOUND.value, "edition 不存在", {"edition_id": edition_id}
            )
        if str(edition["video_id"]) != str(video_id):
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "edition 不属于该视频",
                {"edition_id": edition_id, "edition_video_id": edition["video_id"]},
            )
        return {"video": video, "edition": edition}

    def _require_frozen_binding(
        self, *, project_id: str, video_id: str, edition_id: str, render_id: str, composition_revision_id: str
    ) -> dict[str, Any]:
        """The package binds the frozen revision that was rendered, never 'the latest'."""

        render = self.repo.get("composition_renders", render_id)
        for field_name, expected in (
            ("project_id", project_id),
            ("video_id", video_id),
            ("edition_id", edition_id),
        ):
            if str(render.get(field_name)) != str(expected):
                raise ExplainerContractError(
                    ExplainerErrorCode.OUTPUT_VALIDATION_FAILED.value,
                    "渲染产物不属于该作品范围，不能打包",
                    {field_name: render.get(field_name), "expected": expected, "render_id": render_id},
                )
        if str(render.get("composition_revision_id")) != str(composition_revision_id):
            raise ExplainerContractError(
                "STALE_REVISION",
                "渲染产物对应的合成 revision 与请求不一致，包必须绑定被渲染的确切 revision",
                {
                    "render_id": render_id,
                    "render_composition_revision_id": render.get("composition_revision_id"),
                    "requested_composition_revision_id": composition_revision_id,
                },
            )
        if str(render.get("status") or "") != "SUCCEEDED":
            raise ExplainerContractError(
                ExplainerErrorCode.OUTPUT_VALIDATION_FAILED.value,
                "只有成功完成的渲染才能打包",
                {"render_id": render_id, "status": render.get("status")},
            )
        if str(render.get("integrity_status") or "") != "VERIFIED":
            raise ExplainerContractError(
                ExplainerErrorCode.OUTPUT_VALIDATION_FAILED.value,
                "渲染产物完整性未验证，不能打包",
                {"render_id": render_id, "integrity_status": render.get("integrity_status")},
            )
        composition = self.repo.get("composition_revisions", composition_revision_id)
        if str(composition.get("status") or "") != "FROZEN":
            raise ExplainerContractError(
                "STALE_REVISION",
                "包只能绑定已冻结的合成 revision",
                {"composition_revision_id": composition_revision_id, "status": composition.get("status")},
            )
        if str(composition.get("edition_id")) != str(edition_id):
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "合成 revision 不属于该 edition",
                {"composition_revision_id": composition_revision_id, "edition_id": edition_id},
            )
        if str(render.get("manifest_hash") or "") != str(composition.get("manifest_hash") or ""):
            raise ExplainerContractError(
                "STALE_REVISION",
                "渲染产物的 manifest 与冻结 revision 不一致",
                {
                    "render_manifest_hash": render.get("manifest_hash"),
                    "composition_manifest_hash": composition.get("manifest_hash"),
                },
            )
        return {"render": render, "composition": composition}

    def _verify_items(
        self, *, project_id: str, items: Sequence[PackageItem]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        verified: list[dict[str, Any]] = []
        caller_declared: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for item in items:
            if item.rel_path in seen_paths:
                raise ExplainerContractError(
                    ExplainerErrorCode.OUTPUT_VALIDATION_FAILED.value,
                    "包内 rel_path 重复",
                    {"rel_path": item.rel_path},
                )
            seen_paths.add(item.rel_path)
            record = item.as_dict()
            if item.base_role not in KNOWN_PACKAGE_ROLES:
                record["roster_warning"] = "ROLE_NOT_IN_DOCUMENTED_ROSTER"
            if not item.media_version_id:
                if item.base_role in MEDIA_ITEM_ROLES:
                    raise ExplainerContractError(
                        ExplainerErrorCode.OUTPUT_VALIDATION_FAILED.value,
                        "媒体类条目必须绑定属于本项目的 media_version_id 才能校验哈希",
                        {"role": item.role, "rel_path": item.rel_path},
                    )
                record["hash_source"] = "CALLER_DECLARED_NOT_REPO_VERIFIED"
                caller_declared.append(record)
                records.append(record)
                continue
            media = self.repo.require_same_project_media(
                project_id=project_id, media_version_id=item.media_version_id
            )
            if str(media.get("sha256") or "") != item.sha256:
                raise ExplainerContractError(
                    ExplainerErrorCode.OUTPUT_VALIDATION_FAILED.value,
                    "条目的 sha256 与记录的媒体版本不一致",
                    {
                        "role": item.role,
                        "rel_path": item.rel_path,
                        "declared_sha256": item.sha256,
                        "recorded_sha256": media.get("sha256"),
                        "media_version_id": item.media_version_id,
                    },
                )
            if media.get("byte_size") is not None and int(media["byte_size"]) != int(item.byte_size):
                raise ExplainerContractError(
                    ExplainerErrorCode.OUTPUT_VALIDATION_FAILED.value,
                    "条目的字节数与记录的媒体版本不一致",
                    {
                        "role": item.role,
                        "rel_path": item.rel_path,
                        "declared_byte_size": int(item.byte_size),
                        "recorded_byte_size": int(media["byte_size"]),
                    },
                )
            if str(media.get("integrity_status") or "UNKNOWN") != "VERIFIED":
                raise ExplainerContractError(
                    ExplainerErrorCode.OUTPUT_VALIDATION_FAILED.value,
                    "媒体版本完整性未验证，不能进入交付包",
                    {
                        "role": item.role,
                        "media_version_id": item.media_version_id,
                        "integrity_status": media.get("integrity_status"),
                    },
                )
            record.update(
                {
                    "hash_source": "MEDIA_VERSION_RECORD",
                    "media_asset_id": media.get("media_asset_id"),
                    "integrity_status": media.get("integrity_status"),
                }
            )
            verified.append(record)
            records.append(record)
        return verified, caller_declared, records

    def _roster_gaps(
        self, *, items: Sequence[PackageItem], edition: Mapping[str, Any]
    ) -> dict[str, Any]:
        present_roles = sorted({item.base_role for item in items})
        missing = [role for role in REQUIRED_PACKAGE_ROLES if role not in present_roles]
        subtitle_mode = str(edition.get("subtitle_mode") or "NONE")
        locales = [str(item) for item in (edition.get("subtitle_locales_json") or [])]
        missing_subtitle_locales: list[str] = []
        if subtitle_mode != "NONE" and locales:
            for locale in locales:
                covered = any(
                    item.base_role == SUBTITLE
                    and (item.qualifier or "").lower() == locale.lower()
                    for item in items
                )
                if not covered:
                    missing_subtitle_locales.append(locale)
        return {
            "present_roles": present_roles,
            "missing_required_roles": missing,
            "subtitle_mode": subtitle_mode,
            "subtitle_locales": locales,
            "missing_subtitle_locales": missing_subtitle_locales,
            "complete": not missing and not missing_subtitle_locales,
        }

    def build_package_manifest(
        self,
        *,
        project_id: str,
        video_id: str,
        edition_id: str,
        render_id: str,
        composition_revision_id: str,
        items: Sequence[PackageItem] | Sequence[Mapping[str, Any]],
        platform_code: str | None = None,
        preset_version: str | None = None,
        preset_verified_at: str | None = None,
    ) -> dict[str, Any]:
        """Roster + hash-verified manifest bound to the frozen revision."""

        scope = self._require_scope(project_id=project_id, video_id=video_id, edition_id=edition_id)
        binding = self._require_frozen_binding(
            project_id=project_id,
            video_id=video_id,
            edition_id=edition_id,
            render_id=render_id,
            composition_revision_id=composition_revision_id,
        )
        normalised: list[PackageItem] = [
            item if isinstance(item, PackageItem) else PackageItem.from_mapping(item) for item in items
        ]
        if not normalised:
            raise ExplainerContractError(
                ExplainerErrorCode.OUTPUT_VALIDATION_FAILED.value, "交付包条目为空", {"render_id": render_id}
            )
        if platform_code and not (preset_version and preset_verified_at):
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "平台交付必须携带预设版本与核验日期；未核验的预设不得静默使用",
                {"platform_code": platform_code, "preset_version": preset_version},
            )
        verified, caller_declared, records = self._verify_items(project_id=project_id, items=normalised)
        roster = self._roster_gaps(items=normalised, edition=scope["edition"])
        if not roster["complete"]:
            raise ExplainerContractError(
                ExplainerErrorCode.OUTPUT_VALIDATION_FAILED.value,
                "交付包清单不完整：缺少必需条目",
                {
                    "missing_required_roles": roster["missing_required_roles"],
                    "missing_subtitle_locales": roster["missing_subtitle_locales"],
                    "present_roles": roster["present_roles"],
                },
            )
        files = sorted(
            (
                {
                    "rel_path": item.rel_path,
                    "sha256": item.sha256,
                    "byte_size": int(item.byte_size),
                    "role": item.role,
                    "base_role": item.base_role,
                    "qualifier": item.qualifier,
                    "required": bool(item.required),
                }
                for item in normalised
            ),
            key=lambda entry: str(entry["rel_path"]),
        )
        editor = scope["edition"]
        return {
            "project_id": project_id,
            "video_id": video_id,
            "edition_id": edition_id,
            "render_id": render_id,
            "composition_revision_id": composition_revision_id,
            "frozen_binding": {
                "render_id": render_id,
                "composition_revision_id": composition_revision_id,
                "render_sha256": binding["render"].get("sha256"),
                "render_frame_count": binding["render"].get("frame_count"),
                "composition_manifest_hash": binding["composition"].get("manifest_hash"),
                "composition_status": binding["composition"].get("status"),
                "edition_frozen_subtitle_revision_id": editor.get("frozen_subtitle_revision_id"),
                "edition_frozen_script_revision_id": editor.get("frozen_script_revision_id"),
                "resolves_latest": False,
            },
            "platform_code": platform_code,
            "platform_preset_version": preset_version,
            "preset_verified_at": preset_verified_at,
            "items": [item.as_dict() for item in normalised],
            "files": files,
            "per_file_hashes": {str(entry["rel_path"]): entry["sha256"] for entry in files},
            "manifest_hash": self.repo.package_files_hash(files),
            "file_count": len(files),
            "total_bytes": sum(int(entry["byte_size"]) for entry in files),
            "roster": roster,
            "hash_verified_items": [entry["rel_path"] for entry in verified],
            "caller_declared_hash_items": [entry["rel_path"] for entry in caller_declared],
            "unverified_hash_items": [entry["rel_path"] for entry in caller_declared],
            "item_records": records,
            "official_roster_roles": list(REQUIRED_PACKAGE_ROLES),
            "optional_roster_roles": list(OPTIONAL_PACKAGE_ROLES),
            "subtitle_formats": list(SUBTITLE_FORMATS),
        }

    # ------------------------------------------------------------------ licenses
    def evaluate_licenses(
        self,
        *,
        project_id: str,
        video_id: str,
        assets: Sequence[Mapping[str, Any] | AssetLicense],
        intended_territories: Sequence[str],
    ) -> dict[str, Any]:
        """Per-asset, per-use license scope; derived assets inherit their source."""

        self.repo.require_explainer_project(project_id)
        video = self.repo.require_video_for_project(project_id)
        if str(video["id"]) != str(video_id):
            raise ExplainerContractError(
                "INVALID_REQUEST", "视频与项目不匹配", {"video_id": video_id}
            )
        normalised: list[AssetLicense] = []
        asset_ids: set[str] = set()
        for entry in assets or ():
            if isinstance(entry, AssetLicense):
                normalised.append(entry)
                asset_ids.add(entry.asset_id)
                continue
            if not isinstance(entry, Mapping):
                raise ExplainerContractError("SCHEMA_INVALID", "assets 的每一项必须是对象", {})
            normalised.append(
                AssetLicense(
                    asset_kind=str(entry.get("asset_kind") or "UNKNOWN"),
                    asset_id=str(entry.get("asset_id") or entry.get("id") or ""),
                    asset_label=str(entry.get("asset_label") or entry.get("label") or ""),
                    scope=str(entry.get("scope") or "UNKNOWN").upper(),
                    territories=tuple(str(item) for item in (entry.get("territories") or [])),
                    evidence_ref=entry.get("evidence_ref"),
                    derived_from_asset_id=entry.get("derived_from_asset_id"),
                    note=str(entry.get("note") or ""),
                )
            )
            asset_ids.add(normalised[-1].asset_id)
        missing_derivation_sources = [
            asset.asset_id
            for asset in normalised
            if asset.derived_from_asset_id and asset.derived_from_asset_id not in asset_ids
        ]
        evaluation: LicenseEvaluation = evaluate_license_scope(
            assets=normalised, intended_territories=intended_territories
        )
        blockers = [dict(item) for item in evaluation.blockers]
        if not normalised:
            blockers.append(
                {
                    "code": ExplainerErrorCode.LICENSE_SCOPE_UNVERIFIED.value,
                    "asset_id": None,
                    "reason": "NO_LICENSE_ASSETS_RECORDED",
                    "note": "本次没有记录任何资产的许可范围；若无第三方素材，请显式声明自制素材的范围。",
                    "is_legal_conclusion": False,
                }
            )
        for asset_id in missing_derivation_sources:
            blockers.append(
                {
                    "code": ExplainerErrorCode.LICENSE_SCOPE_UNVERIFIED.value,
                    "asset_id": asset_id,
                    "reason": "DERIVED_SOURCE_LICENSE_NOT_INCLUDED",
                    "note": "派生资产继承来源限制，但本次未提供来源资产许可，无法判定继承范围。",
                }
            )
        for blocker in blockers:
            blocker.setdefault("is_legal_conclusion", False)
        return {
            "project_id": project_id,
            "video_id": video_id,
            "publishable": bool(evaluation.publishable and not missing_derivation_sources),
            "blockers": blockers,
            "per_asset": [dict(item) for item in evaluation.per_asset],
            "license_scope": {
                "intended_territories": [str(item) for item in intended_territories],
                "asset_count": len(normalised),
                "derived_asset_count": sum(1 for asset in normalised if asset.derived_from_asset_id),
                "derived_assets_inherit_source_restriction": True,
                "unverified_asset_count": sum(
                    1 for item in evaluation.per_asset if item.get("verdict") != "ALLOWED"
                ),
            },
            "missing_derivation_sources": missing_derivation_sources,
            "scope_is_legal_conclusion": False,
            "not_a_legal_conclusion": True,
            "note": "该结果是基于已记录许可数据的一致性检查，不是法律意见。",
        }

    # ------------------------------------------------------------------ AI disclosure
    def ai_disclosure(
        self, *, content_kind: str, has_ai_visuals: bool, platforms: Sequence[str]
    ) -> dict[str, Any]:
        """In-frame marker and per-platform disclosure field, returned separately."""

        kind = str(content_kind or "").upper()
        if kind not in {item.value for item in ContentKind}:
            raise ExplainerContractError(
                "INVALID_REQUEST", "content_kind 取值不合法", {"content_kind": content_kind}
            )
        if kind == ContentKind.FACTUAL_EXPLAINER.value:
            marker_text = "AI 生成画面 / 非纪实影像" if has_ai_visuals else None
            marker_required = bool(has_ai_visuals)
            placement = "FRAME_CORNER_PERSISTENT" if has_ai_visuals else None
            rationale = (
                "事实类解说中使用 AI 生成/重建画面时，画面内需要持续提示其非纪实属性。"
                if has_ai_visuals
                else "本片未声明使用 AI 生成画面，画面内不添加生成提示。"
            )
        else:
            marker_text = "AI 生成作品"
            marker_required = True
            placement = "FRAME_CORNER_PERSISTENT"
            rationale = "虚构作品整体由 AI 生成，画面内持续提示生成属性。"
        in_frame = {
            "required": marker_required,
            "marker_text": marker_text,
            "placement": placement,
            "persistent": True,
            "rationale": rationale,
            "covers_platform_field": False,
        }
        platform_fields: dict[str, Any] = {}
        for code in platforms or ():
            platform_code = str(code).upper()
            preset = PLATFORM_PRESETS.get(platform_code)
            if preset is None:
                platform_fields[platform_code] = {
                    "platform_code": platform_code,
                    "field_name": None,
                    "value": None,
                    "required": True,
                    "known": False,
                    "requires_operator_verification": True,
                    "note": "未知平台：必须在核验该平台上传播放界面/接口后填写披露字段。",
                }
                continue
            field = preset["ai_disclosure_field"]
            platform_fields[platform_code] = {
                "platform_code": platform_code,
                "field_name": field["field_name"],
                "value": {"disclosed": True, "content_kind": kind, "has_ai_visuals": bool(has_ai_visuals)},
                "required": True,
                "known": True,
                "preset_version": preset["preset_version"],
                "verification_status": preset["verification_status"],
                "requires_operator_verification": True,
                "note": field["note"],
            }
        return {
            "content_kind": kind,
            "has_ai_visuals": bool(has_ai_visuals),
            "in_frame": in_frame,
            "platform_fields": platform_fields,
            "watermark_is_not_platform_field": True,
            "in_frame_does_not_satisfy_platform_field": True,
            "note": "角落水印只服务观众提示，不能替代平台上传时的 AI 披露字段。",
        }

    # ------------------------------------------------------------------ presets
    def platform_preset(self, *, platform_code: str, preset: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Versioned preset data with a verification date (built-ins are placeholders)."""

        code = str(platform_code or "").upper()
        if preset is not None:
            if not isinstance(preset, Mapping):
                raise ExplainerContractError("SCHEMA_INVALID", "preset 必须是对象", {})
            required_keys = {
                "container",
                "video_codec",
                "dimensions",
                "duration_range_seconds",
                "subtitle_handling",
                "safe_area",
                "cover",
                "ai_disclosure_field",
                "publish_permission",
            }
            missing = sorted(key for key in required_keys if key not in preset)
            if missing:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "preset 缺少必需字段", {"missing": missing, "platform_code": code}
                )
            supplied = dict(preset)
            supplied.setdefault("platform_code", code)
            if not supplied.get("preset_version"):
                raise ExplainerContractError(
                    "INVALID_REQUEST", "preset 必须声明 preset_version", {"platform_code": code}
                )
            if not supplied.get("verification_date"):
                raise ExplainerContractError(
                    "INVALID_REQUEST",
                    "preset 必须记录核验日期；未核验的预设不得作为平台事实使用",
                    {"platform_code": code},
                )
            supplied.setdefault("verification_status", "OPERATOR_VERIFIED")
            supplied.setdefault("requires_operator_verification", False)
            supplied.setdefault("note", "由操作者提供的平台预设。")
            supplied["builtin"] = False
            return supplied
        if code not in PLATFORM_PRESETS:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "没有该平台的内置预设；请提供已核验的 preset 数据",
                {"platform_code": code, "builtin_platform_codes": list(BUILTIN_PLATFORM_CODES)},
            )
        resolved = dict(PLATFORM_PRESETS[code])
        resolved["builtin"] = True
        resolved["limits_require_verification"] = True
        resolved["all_platforms_share_duration_limit"] = False
        resolved["all_platforms_share_audio_track_limit"] = False
        return resolved

    # ------------------------------------------------------------------ freeze
    def freeze_package(
        self,
        *,
        project_id: str,
        video_id: str,
        edition_id: str,
        render_id: str,
        composition_revision_id: str,
        items: Sequence[PackageItem] | Sequence[Mapping[str, Any]],
        files: Sequence[Mapping[str, Any]],
        platform_code: str | None = None,
        intended_territories: Sequence[str] = (),
        assets: Sequence[Mapping[str, Any] | AssetLicense] = (),
        ai_disclosure: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        zip_sha256: str | None = None,
        rel_path: str | None = None,
        byte_size: int | None = None,
        package_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist the FULL file list first, then mark the package READY or BLOCKED.

        ``package_id`` names an existing ``BUILDING`` row to fill in.  The export
        command durably records the operator's intent before any worker is asked to
        build it, so the worker must complete *that* row rather than insert a second
        one: otherwise one click leaves an eternally ``BUILDING`` package behind
        while the real bundle hides under a different id.
        """

        warnings: list[str] = []
        metadata_payload = dict(metadata or {})
        preset_version_value: str | None = None
        preset_verified_at_value: str | None = None
        platform_preset_payload: dict[str, Any] | None = None
        if platform_code:
            supplied_preset = metadata_payload.get("platform_preset")
            platform_preset_payload = self.platform_preset(
                platform_code=platform_code,
                preset=supplied_preset if isinstance(supplied_preset, Mapping) else None,
            )
            preset_version_value = str(platform_preset_payload["preset_version"])
            preset_verified_at_value = str(
                metadata_payload.get("preset_verified_at")
                or platform_preset_payload.get("verification_date")
                or ""
            )
            if not preset_verified_at_value:
                raise ExplainerContractError(
                    "INVALID_REQUEST",
                    "冻结平台交付包必须记录预设核验日期（preset_verified_at）；未核验的预设不得作为平台事实",
                    {"platform_code": platform_code, "preset_version": preset_version_value},
                )
            metadata_payload["platform_preset"] = platform_preset_payload
            metadata_payload["preset_verified_at"] = preset_verified_at_value
        manifest = self.build_package_manifest(
            project_id=project_id,
            video_id=video_id,
            edition_id=edition_id,
            render_id=render_id,
            composition_revision_id=composition_revision_id,
            items=items,
            platform_code=platform_code,
            preset_version=preset_version_value,
            preset_verified_at=preset_verified_at_value,
        )
        file_list = self._normalise_file_list(files)
        manifest_paths = {str(entry["rel_path"]): str(entry["sha256"]) for entry in manifest["files"]}
        for entry in file_list:
            expected = manifest_paths.get(str(entry["rel_path"]))
            if expected is None:
                if not entry.get("role"):
                    raise ExplainerContractError(
                        ExplainerErrorCode.OUTPUT_VALIDATION_FAILED.value,
                        "包内出现未在产物清单中声明且没有 role 的文件",
                        {"rel_path": entry["rel_path"]},
                    )
                continue
            if str(entry["sha256"]) != expected:
                raise ExplainerContractError(
                    ExplainerErrorCode.OUTPUT_VALIDATION_FAILED.value,
                    "包内文件与产物清单的哈希不一致",
                    {
                        "rel_path": entry["rel_path"],
                        "file_sha256": entry["sha256"],
                        "manifest_sha256": expected,
                    },
                )
        missing_in_zip = [path for path in manifest_paths if path not in {str(item["rel_path"]) for item in file_list}]
        if missing_in_zip:
            raise ExplainerContractError(
                ExplainerErrorCode.OUTPUT_VALIDATION_FAILED.value,
                "包内文件清单缺少产物条目",
                {"missing_in_zip": missing_in_zip},
            )
        if zip_sha256 is not None and not is_sha256(str(zip_sha256)):
            raise ExplainerContractError(
                ExplainerErrorCode.OUTPUT_VALIDATION_FAILED.value,
                "zip_sha256 必须是调用方计算的真实 sha256",
                {"zip_sha256": zip_sha256},
            )
        license_result = self.evaluate_licenses(
            project_id=project_id,
            video_id=video_id,
            assets=assets,
            intended_territories=intended_territories,
        )
        blockers: list[dict[str, Any]] = [dict(item) for item in license_result["blockers"]]
        if ai_disclosure is None:
            disclosure_payload: dict[str, Any] = {
                "status": "NOT_RECORDED",
                "in_frame": None,
                "platform_fields": None,
                "watermark_is_not_platform_field": True,
                "note": "本次冻结未记录 AI 披露字段；上线前必须补齐。",
            }
            warnings.append("AI_DISCLOSURE_NOT_RECORDED")
        else:
            disclosure_payload = dict(ai_disclosure)
            disclosure_payload.setdefault("watermark_is_not_platform_field", True)
        if platform_code is None:
            warnings.append("NO_PLATFORM_PRESET_BOUND_PACKAGE_IS_PLATFORM_NEUTRAL")
        files_hash = self.repo.package_files_hash(file_list)
        metadata_payload.update(
            {
                "manifest_hash": manifest["manifest_hash"],
                "frozen_binding": manifest["frozen_binding"],
                "roster": manifest["roster"],
                "warnings": warnings,
                "media_regenerated_for_packaging": False,
            }
        )
        payload_values = {
            "video_id": video_id,
            "project_id": project_id,
            "edition_id": edition_id,
            "render_id": render_id,
            "composition_revision_id": composition_revision_id,
            "manifest_hash": files_hash,
            "platform_code": platform_code,
            "platform_preset_version": preset_version_value,
            "preset_verified_at": preset_verified_at_value,
            "status": PublicationStatus.BUILDING.value,
            "rel_path": rel_path,
            "zip_sha256": None if zip_sha256 is None else str(zip_sha256),
            "byte_size": None if byte_size is None else int(byte_size),
            "files_json": file_list,
            "license_scope_json": license_result["license_scope"],
            "license_blockers_json": blockers,
            "ai_disclosure_json": disclosure_payload,
            "metadata_json": metadata_payload,
            "requested_territories_json": [str(item) for item in intended_territories],
        }
        if package_id:
            existing = self.repo.get("publication_packages", str(package_id))
            if str(existing.get("edition_id")) != str(edition_id):
                raise ExplainerContractError(
                    "INVALID_REQUEST",
                    "待完成的发布包不属于该输出版本",
                    {"package_id": str(package_id), "edition_id": edition_id},
                )
            package = self.repo.update("publication_packages", str(package_id), payload_values)
        else:
            package = self.repo.insert("publication_packages", payload_values)
        publishable = not blockers and (zip_sha256 is not None)
        if blockers:
            status = PublicationStatus.BLOCKED.value
        elif zip_sha256 is None:
            status = PublicationStatus.BUILDING.value
            warnings.append("ZIP_HASH_MISSING_PACKAGE_NOT_READY")
        else:
            status = PublicationStatus.READY.value
        updated = self.repo.update(
            "publication_packages",
            package["id"],
            {
                "status": status,
                "ready_at": utc_now_iso() if status == PublicationStatus.READY.value else None,
                "metadata_json": {**metadata_payload, "warnings": warnings},
            },
        )
        return {
            "package": updated,
            "package_id": updated["id"],
            "status": status,
            "publishable": publishable and status == PublicationStatus.READY.value,
            "blockers": blockers,
            "warnings": warnings,
            "manifest_hash": files_hash,
            "files": file_list,
            "file_count": len(file_list),
            "license_scope": license_result["license_scope"],
            "ai_disclosure": disclosure_payload,
            "preset_binding": {
                "platform_code": platform_code,
                "platform_preset_version": updated.get("platform_preset_version"),
                "preset_verified_at": updated.get("preset_verified_at"),
            },
            "frozen_binding": manifest["frozen_binding"],
            "media_regenerated": False,
            "blocked_package_never_renamed_publishable": True,
        }

    def _normalise_file_list(self, files: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        if not files:
            raise ExplainerContractError(
                ExplainerErrorCode.OUTPUT_VALIDATION_FAILED.value, "包内文件清单不能为空", {}
            )
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in files:
            if not isinstance(entry, Mapping):
                raise ExplainerContractError("SCHEMA_INVALID", "files 的每一项必须是对象", {})
            rel = str(entry.get("rel_path") or "")
            sha = str(entry.get("sha256") or "").lower()
            if not rel:
                raise ExplainerContractError("SCHEMA_INVALID", "files 条目缺少 rel_path", {})
            if rel in seen:
                raise ExplainerContractError(
                    ExplainerErrorCode.OUTPUT_VALIDATION_FAILED.value,
                    "包内文件清单 rel_path 重复",
                    {"rel_path": rel},
                )
            seen.add(rel)
            if not is_sha256(sha):
                raise ExplainerContractError(
                    ExplainerErrorCode.OUTPUT_VALIDATION_FAILED.value,
                    "包内文件条目缺少真实 sha256",
                    {"rel_path": rel, "sha256": entry.get("sha256")},
                )
            out.append(
                {
                    "rel_path": rel,
                    "sha256": sha,
                    "byte_size": int(entry.get("byte_size") or 0),
                    "role": entry.get("role"),
                }
            )
        return sorted(out, key=lambda item: str(item["rel_path"]))

    # ------------------------------------------------------------------ publication planning
    def plan_publication(
        self,
        *,
        package_id: str,
        requested_action: str,
        target_platforms: Sequence[str],
        account_authorization_refs: Sequence[Mapping[str, Any] | PublisherCapability],
    ) -> dict[str, Any]:
        """Plan uploads; without authorization or capability it is manual handoff only."""

        action = str(requested_action or "").upper()
        if action not in PUBLICATION_ACTIONS:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "requested_action 取值不合法",
                {"requested_action": requested_action, "allowed": sorted(PUBLICATION_ACTIONS)},
            )
        package = self.repo.get("publication_packages", package_id)
        package_status = str(package.get("status") or PublicationStatus.DRAFT.value)
        package_hash = str(package.get("manifest_hash") or "")
        platforms = [str(item).upper() for item in target_platforms or ()]
        if not platforms:
            raise ExplainerContractError("INVALID_REQUEST", "必须声明目标平台", {})
        handoff_base = {
            "package_id": package_id,
            "package_hash": package_hash,
            "files": list(package.get("files_json") or []),
            "rel_path": package.get("rel_path"),
            "zip_sha256": package.get("zip_sha256"),
            "byte_size": package.get("byte_size"),
            "platform_code": package.get("platform_code"),
            "target_platforms": platforms,
            "requested_action": action,
            "manual_steps": [
                "使用已冻结的交付包文件，不要重新渲染或重新生成任何上游内容。",
                "按平台当前上传界面核对时长/分辨率/字幕/音轨限制与 AI 披露字段。",
                "上传后回填平台资源 id 与链接，使发布回执可核对。",
            ],
            "created_at": utc_now_iso(),
        }
        if package_status == PublicationStatus.BLOCKED.value:
            return {
                "attempts": [
                    {
                        "platform_code": platform,
                        "account_ref": None,
                        "adapter_code": None,
                        "package_hash": package_hash,
                        "status": "BLOCKED",
                        "would_send": False,
                        "outbound_attempts": 0,
                        "reason": "PACKAGE_BLOCKED_RESOLVE_LICENSE_OR_PRESET_FIRST",
                    }
                    for platform in platforms
                ],
                "manual_handoff": False,
                "reason": "PACKAGE_BLOCKED",
                "outbound_attempts": 0,
                "would_send": False,
                "package_status": package_status,
                "blockers": list(package.get("license_blockers_json") or []),
            }
        if package_status in {
            PublicationStatus.DRAFT.value,
            PublicationStatus.BUILDING.value,
            PublicationStatus.WITHDRAWN.value,
            PublicationStatus.FAILED.value,
        }:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "交付包尚未 READY，不能规划发布",
                {"package_id": package_id, "status": package_status},
            )
        capabilities = [_capability_from_ref(ref) for ref in account_authorization_refs or ()]
        attempts: list[dict[str, Any]] = []
        manual_reasons: list[str] = []
        for platform in platforms:
            platform_caps = [cap for cap in capabilities if str(cap.platform_code).upper() == platform]
            if not platform_caps:
                attempts.append(
                    {
                        "platform_code": platform,
                        "account_ref": None,
                        "adapter_code": None,
                        "package_hash": package_hash,
                        "status": PublicationStatus.NEEDS_MANUAL_PUBLISH.value,
                        "would_send": False,
                        "outbound_attempts": 0,
                        "reason": "NO_ACCOUNT_AUTHORIZATION_REFERENCE",
                    }
                )
                manual_reasons.append(f"{platform}:NO_ACCOUNT_AUTHORIZATION_REFERENCE")
                if package_status == PublicationStatus.READY.value:
                    package_status = PublicationStatus.NEEDS_MANUAL_PUBLISH.value
                continue
            for cap in platform_caps:
                planned_attempt_no = (
                    self._next_attempt_no(
                        package_id=package_id,
                        platform_code=cap.platform_code,
                        account_ref=cap.account_ref,
                    )
                    if cap.supports_upload and cap.authorized
                    else None
                )
                if not cap.authorized:
                    attempts.append(
                        {
                            **cap.as_dict(),
                            "package_hash": package_hash,
                            "status": PublicationReceiptStatus.AUTHORIZATION_MISSING.value,
                            "would_send": False,
                            "outbound_attempts": 0,
                            "planned_attempt_no": None,
                            "reason": "ACCOUNT_NOT_AUTHORIZED",
                        }
                    )
                    manual_reasons.append(f"{platform}:{cap.account_ref}:ACCOUNT_NOT_AUTHORIZED")
                    if package_status == PublicationStatus.READY.value:
                        package_status = PublicationStatus.NEEDS_MANUAL_PUBLISH.value
                    continue
                if not cap.supports_upload:
                    attempts.append(
                        {
                            **cap.as_dict(),
                            "package_hash": package_hash,
                            "status": PublicationStatus.NEEDS_MANUAL_PUBLISH.value,
                            "would_send": False,
                            "outbound_attempts": 0,
                            "planned_attempt_no": None,
                            "reason": "ADAPTER_UPLOAD_UNSUPPORTED",
                        }
                    )
                    manual_reasons.append(f"{platform}:{cap.account_ref}:ADAPTER_UPLOAD_UNSUPPORTED")
                    if package_status == PublicationStatus.READY.value:
                        package_status = PublicationStatus.NEEDS_MANUAL_PUBLISH.value
                    continue
                if action == "UPLOAD_ONLY" and not cap.supports_upload:
                    attempts.append(
                        {
                            **cap.as_dict(),
                            "package_hash": package_hash,
                            "status": PublicationStatus.NEEDS_MANUAL_PUBLISH.value,
                            "would_send": False,
                            "outbound_attempts": 0,
                            "reason": "ACTION_REQUIRES_UPLOAD_CAPABILITY",
                        }
                    )
                    continue
                attempts.append(
                    {
                        **cap.as_dict(),
                        "package_hash": package_hash,
                        "status": "PLANNED",
                        "would_send": True,
                        "outbound_attempts": 0,
                        "planned_attempt_no": planned_attempt_no,
                        "idempotency_guaranteed": bool(cap.supports_idempotency),
                        "idempotency_scope": [cap.platform_code, cap.account_ref, package_hash],
                        "reason": "AUTHORIZED_CAPABLE_ADAPTER",
                    }
                )
        manual_handoff = any(
            str(attempt["status"]) in {PublicationStatus.NEEDS_MANUAL_PUBLISH.value, "BLOCKED"}
            for attempt in attempts
        )
        if manual_handoff:
            handoff = {
                **handoff_base,
                "reason": ";".join(manual_reasons) or "MANUAL_HANDOFF_REQUIRED",
                "attempt_statuses": [str(attempt["status"]) for attempt in attempts],
            }
            metadata = dict(package.get("metadata_json") or {})
            metadata["manual_handoff"] = handoff
            self.repo.update(
                "publication_packages",
                package_id,
                {
                    "status": (
                        PublicationStatus.NEEDS_MANUAL_PUBLISH.value
                        if package_status
                        in {PublicationStatus.READY.value, PublicationStatus.NEEDS_MANUAL_PUBLISH.value}
                        else package_status
                    ),
                    "metadata_json": metadata,
                },
            )
            package_status = (
                PublicationStatus.NEEDS_MANUAL_PUBLISH.value
                if package_status
                in {PublicationStatus.READY.value, PublicationStatus.NEEDS_MANUAL_PUBLISH.value}
                else package_status
            )
        else:
            handoff = None
        return {
            "attempts": attempts,
            "manual_handoff": manual_handoff,
            "manual_handoff_record": handoff,
            "reason": ";".join(manual_reasons) if manual_reasons else "AUTHORIZED_CAPABILITIES_PLANNED",
            "outbound_attempts": 0,
            "would_send": any(bool(attempt.get("would_send")) for attempt in attempts),
            "package_id": package_id,
            "package_hash": package_hash,
            "package_status": package_status,
            "planning_never_sends": True,
        }

    def _next_attempt_no(self, *, package_id: str, platform_code: str | None, account_ref: str | None) -> int:
        rows = self.repo.query_all(
            """
            SELECT attempt_no FROM publication_receipts
            WHERE package_id = ? AND platform_code IS ? AND account_ref IS ?
            """,
            (package_id, platform_code, account_ref),
        )
        return max((int(row["attempt_no"]) for row in rows), default=0) + 1

    # ------------------------------------------------------------------ receipts
    def record_attempt(
        self,
        *,
        package_id: str,
        capability: PublisherCapability,
        package_hash: str,
        outcome: str,
        remote_session_id: str | None = None,
        remote_resource_id: str | None = None,
        error: str | None = None,
        handoff_note: str | None = None,
        query_result: Mapping[str, Any] | None = None,
        remote_url: str | None = None,
    ) -> dict[str, Any]:
        """Record one upload attempt following the receipt rules (design §22)."""

        package = self.repo.get("publication_packages", package_id)
        package_status = str(package.get("status") or "")
        if package_status in {PublicationStatus.BLOCKED.value, PublicationStatus.WITHDRAWN.value}:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "包处于 BLOCKED/WITHDRAWN，不能记录上传尝试；先解决许可与预设问题",
                {"package_id": package_id, "status": package_status},
            )
        if str(package_hash) != str(package.get("manifest_hash") or ""):
            raise ExplainerContractError(
                "STALE_REVISION",
                "包内容已变化：包哈希与当前文件清单不一致，不能按同一 hash 重发",
                {"package_hash": package_hash, "current_package_hash": package.get("manifest_hash")},
            )
        cap = _capability_from_ref(capability)
        normalized_outcome = str(outcome or "").upper()
        if normalized_outcome in {"SUCCESS", "SUCCEEDED"}:
            normalized_outcome = "SUCCEEDED"
        elif normalized_outcome not in {"SUCCEEDED", "TIMEOUT", "UNKNOWN", "FAILED", "NEEDS_MANUAL_PUBLISH"}:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "outcome 取值不合法",
                {"outcome": outcome, "allowed": ["SUCCEEDED", "TIMEOUT", "UNKNOWN", "FAILED", "NEEDS_MANUAL_PUBLISH"]},
            )
        attempt_no = self._next_attempt_no(
            package_id=package_id, platform_code=cap.platform_code, account_ref=cap.account_ref
        )
        started_at = utc_now_iso()
        outbound_attempts = 0
        requires_reconciliation = False
        may_resend = False
        query_confirmed_absent = False
        receipt_status: str
        error_code: str | None = None
        handoff = handoff_note
        if not cap.authorized:
            receipt_status = PublicationReceiptStatus.AUTHORIZATION_MISSING.value
            error_code = "AUTHORIZATION_MISSING"
            handoff = handoff or "账号未授权：本次没有发出任何请求，需要人工发布或补齐账号授权。"
        elif not cap.supports_upload:
            receipt_status = PublicationReceiptStatus.NEEDS_MANUAL_PUBLISH.value
            error_code = "ADAPTER_UPLOAD_UNSUPPORTED"
            handoff = handoff or "适配器不支持上传：需要人工按冻结包发布，并回填平台资源 id。"
        elif normalized_outcome == "NEEDS_MANUAL_PUBLISH":
            receipt_status = PublicationReceiptStatus.NEEDS_MANUAL_PUBLISH.value
            error_code = "OPERATOR_REQUESTED_MANUAL_PUBLISH"
            handoff = handoff or "按操作者要求转为人工发布。"
        elif normalized_outcome == "SUCCEEDED":
            outbound_attempts = 1
            if remote_resource_id:
                receipt_status = PublicationReceiptStatus.SUCCEEDED.value
            elif query_result is not None and bool(query_result.get("exists")):
                receipt_status = PublicationReceiptStatus.QUERIED_EXISTING.value
                remote_resource_id = query_result.get("remote_resource_id") or remote_resource_id
                remote_url = remote_url or query_result.get("remote_url")
            else:
                receipt_status = PublicationReceiptStatus.PUBLICATION_RESULT_UNKNOWN.value
                error_code = "UNVERIFIED_SUCCESS_WITHOUT_REMOTE_RESOURCE_ID"
                requires_reconciliation = True
                handoff = handoff or "适配器报告成功但没有平台资源 id：不得据此认定上传成功，需人工核对。"
        elif normalized_outcome in {"TIMEOUT", "UNKNOWN"}:
            outbound_attempts = 1
            if cap.supports_query and query_result is not None:
                exists = bool(query_result.get("exists"))
                if exists:
                    receipt_status = PublicationReceiptStatus.QUERIED_EXISTING.value
                    remote_resource_id = query_result.get("remote_resource_id") or remote_resource_id
                    remote_url = remote_url or query_result.get("remote_url")
                else:
                    query_confirmed_absent = True
                    receipt_status = (
                        PublicationReceiptStatus.TIMEOUT.value
                        if normalized_outcome == "TIMEOUT"
                        else PublicationReceiptStatus.PUBLICATION_RESULT_UNKNOWN.value
                    )
                    may_resend = normalized_outcome == "TIMEOUT"
                    error_code = "SESSION_QUERIED_NOTHING_CREATED"
            elif cap.supports_query:
                receipt_status = PublicationReceiptStatus.PUBLICATION_RESULT_UNKNOWN.value
                error_code = "QUERY_CAPABLE_SESSION_NOT_QUERIED_YET"
                requires_reconciliation = True
                handoff = handoff or "具备查询能力的会话必须先查询原会话，确认没有创建后才能重发。"
            else:
                receipt_status = PublicationReceiptStatus.PUBLICATION_RESULT_UNKNOWN.value
                error_code = "NO_QUERY_CAPABILITY_RESULT_UNKNOWN"
                requires_reconciliation = True
                handoff = handoff or "适配器没有查询能力，上传结果未知，必须人工核对后再决定是否重发。"
        else:  # FAILED
            outbound_attempts = 1
            receipt_status = PublicationReceiptStatus.FAILED.value
            error_code = "UPLOAD_FAILED"
            may_resend = True
        response_payload = {
            "capability": cap.as_dict(),
            "outcome": normalized_outcome,
            "outbound_attempts": outbound_attempts,
            "query_result": dict(query_result) if query_result else None,
            "query_confirmed_absent": query_confirmed_absent,
            "requires_reconciliation": requires_reconciliation,
            "may_resend": may_resend,
            "idempotency_guaranteed": bool(cap.supports_idempotency),
            "idempotency_scope": [cap.platform_code, cap.account_ref, str(package_hash)],
            "media_regenerated": False,
            "upstream_regeneration_attempted": False,
        }
        receipt = self.repo.insert(
            "publication_receipts",
            {
                "package_id": package_id,
                "video_id": package["video_id"],
                "attempt_no": attempt_no,
                "publisher_adapter": cap.adapter_code,
                "platform_code": cap.platform_code,
                "account_ref": cap.account_ref,
                "package_hash": str(package_hash),
                "status": receipt_status,
                "remote_upload_session_id": remote_session_id,
                "remote_resource_id": remote_resource_id,
                "remote_url": remote_url,
                "response_json": response_payload,
                "error_code": error_code,
                "error_detail_redacted": error,
                "handoff_note": handoff,
                "started_at": started_at,
                "finished_at": utc_now_iso(),
            },
        )
        new_package_status = package_status
        if receipt_status == PublicationReceiptStatus.SUCCEEDED.value:
            new_package_status = PublicationStatus.PUBLISHED.value
        elif receipt_status in {
            PublicationReceiptStatus.PUBLICATION_RESULT_UNKNOWN.value,
            PublicationReceiptStatus.TIMEOUT.value,
        }:
            if receipt_status == PublicationReceiptStatus.PUBLICATION_RESULT_UNKNOWN.value or requires_reconciliation:
                new_package_status = PublicationStatus.PUBLICATION_RESULT_UNKNOWN.value
        elif receipt_status == PublicationReceiptStatus.NEEDS_MANUAL_PUBLISH.value:
            if package_status == PublicationStatus.READY.value:
                new_package_status = PublicationStatus.NEEDS_MANUAL_PUBLISH.value
        elif receipt_status == PublicationReceiptStatus.FAILED.value:
            new_package_status = PublicationStatus.FAILED.value
        elif receipt_status == PublicationReceiptStatus.AUTHORIZATION_MISSING.value:
            if package_status == PublicationStatus.READY.value:
                new_package_status = PublicationStatus.NEEDS_MANUAL_PUBLISH.value
        if new_package_status != package_status:
            self.repo.update(
                "publication_packages", package_id, {"status": new_package_status}
            )
        return {
            "receipt": receipt,
            "receipt_id": receipt["id"],
            "attempt_no": attempt_no,
            "status": receipt_status,
            "outbound_attempts": outbound_attempts,
            "would_send": outbound_attempts > 0,
            "requires_reconciliation": requires_reconciliation,
            "may_resend": may_resend,
            "query_confirmed_absent": query_confirmed_absent,
            "idempotency_guaranteed": bool(cap.supports_idempotency),
            "idempotency_scope": [cap.platform_code, cap.account_ref, str(package_hash)],
            "package_status": new_package_status,
            "media_regenerated": False,
            "handoff_note": handoff,
        }

    def reconcile_unknown(self, *, package_id: str) -> dict[str, Any]:
        """List the receipts that need operator reconciliation with their remote ids."""

        package = self.repo.get("publication_packages", package_id)
        placeholders = ", ".join("?" for _ in RECONCILIATION_STATUSES)
        rows = self.repo.query_all(
            f"""
            SELECT * FROM publication_receipts
            WHERE package_id = ? AND status IN ({placeholders})
            ORDER BY attempt_no
            """,
            (package_id, *RECONCILIATION_STATUSES),
        )
        receipts = decode_rows("publication_receipts", rows)
        items = [
            {
                "receipt_id": receipt["id"],
                "attempt_no": receipt["attempt_no"],
                "platform_code": receipt["platform_code"],
                "account_ref": receipt["account_ref"],
                "publisher_adapter": receipt["publisher_adapter"],
                "status": receipt["status"],
                "package_hash": receipt["package_hash"],
                "remote_session_id": receipt["remote_upload_session_id"],
                "remote_resource_id": receipt["remote_resource_id"],
                "error_code": receipt["error_code"],
                "requires_reconciliation": bool(
                    (receipt.get("response_json") or {}).get("requires_reconciliation")
                ),
                "may_resend": bool((receipt.get("response_json") or {}).get("may_resend")),
                "handoff_note": receipt["handoff_note"],
            }
            for receipt in receipts
        ]
        return {
            "package_id": package_id,
            "package_hash": package["manifest_hash"],
            "package_status": package["status"],
            "receipts": items,
            "count": len(items),
            "requires_reconciliation": bool(items),
            "next_step": (
                "先用原 remote_session_id 查询原会话：已创建则记录平台资源 id；"
                "确认未创建才允许重发，且不得重新生成任何媒体。"
            ),
            "resend_requires_prior_query": True,
            "regeneration_never_allowed": True,
        }


__all__ = [
    "BUILTIN_PLATFORM_CODES",
    "ExplainerDeliveryService",
    "MEDIA_ITEM_ROLES",
    "PLATFORM_PRESETS",
    "PRESET_VERSION",
    "PackageItem",
    "PublisherCapability",
    "REQUIRED_PACKAGE_ROLES",
    "SUBTITLE_FORMATS",
]
