"""Explainer production orchestration: preflight, frozen plan, DAG, projection.

This module owns design §5.2 (preflight freezes input/policy/capability/budget and
the task skeleton), §12 (the one-click task graph), §13.1 (run/step bindings as a
projection) and §14 (persisted generation commands).

The rules it exists to enforce:

* A run is a **projection**.  ``explainer_runs`` stores business scope, frozen
  snapshots and the ``automation_workflow_run_id`` link.  Execution truth stays in
  the existing ``automation_workflow_runs`` / ``jobs`` / ``job_attempts`` tables.
  There is no second claim queue and no independent terminal state.
* The initial ``plan_hash`` covers the input, channel/policy versions, required
  capabilities, a coarse budget and the task skeleton.  It does **not** cover the
  not-yet-written script or TTS.  Internal expansion of research/TTS sub-plans is
  authorized by the initial plan and therefore never makes itself ``STALE_PLAN``.
  Only an external edit to input/policy/dependency does.
* A step completes only when *all* of: the job reached a successful terminal
  state, the file really exists, its hash/probe is valid, and the business result
  was registered.  HTTP 200, a Comfy queue submission or a filename is never
  completion.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping, Sequence

from local_drama.domain.explainers.contracts import (
    SCHEMA_VERSION,
    AutomationMode,
    Budget,
    ContentKind,
    DurationMode,
    DurationSpec,
    ExplainerContractError,
    ExplainerErrorCode,
    FallbackPolicy,
    InferenceMode,
    InputKind,
    PreflightBlocker,
    ProductKind,
    Ratio,
    ResearchMode,
    RunStatus,
    classify_blocker,
    content_hash,
    estimate_shot_budget,
    utc_now_iso,
)
from local_drama.domain.explainers.policies import (
    POLICY_PROCESSOR_NAME,
    POLICY_RULE_VERSION,
    BudgetLedger,
    Thresholds,
    project_run_status,
)
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository

# --------------------------------------------------------------------------- #
# task skeleton
# --------------------------------------------------------------------------- #
#: Ordered production graph (design §12).  ``depends_on`` is the DAG edge set;
#: ``required`` steps cannot be skipped, optional ones must record a skip reason.
TASK_SKELETON: tuple[dict[str, Any], ...] = (
    {
        "step_code": "RESEARCH_ACQUIRE",
        "title": "来源与资料包",
        "stage_code": "RESEARCH_ACQUIRE",
        "output_kind": "RESEARCH_PACKET",
        "required": True,
        "depends_on": (),
        "requires_capabilities": (),
        "can_parallelise": True,
    },
    {
        "step_code": "FACT_EXTRACT",
        "title": "事实、事件与实体",
        "stage_code": "FACT_EXTRACT",
        "output_kind": "CLAIM_LEDGER",
        "required": True,
        "depends_on": ("RESEARCH_ACQUIRE",),
        "requires_capabilities": ("text.generation",),
        "can_parallelise": False,
    },
    {
        "step_code": "NARRATION_WRITE",
        "title": "解说稿与章节",
        "stage_code": "NARRATION_WRITE",
        "output_kind": "SCRIPT_REVISION",
        "required": True,
        "depends_on": ("FACT_EXTRACT",),
        "requires_capabilities": ("text.generation",),
        "can_parallelise": False,
    },
    {
        "step_code": "IDENTITY_ASSETS",
        "title": "参考资产与栏目风格",
        "stage_code": "EXPLAINER_STORYBOARD",
        "output_kind": "IDENTITY_INPUTS",
        "required": True,
        "depends_on": ("FACT_EXTRACT",),
        "requires_capabilities": ("image.text_to_image",),
        "can_parallelise": True,
    },
    {
        "step_code": "NARRATION_TTS",
        "title": "旁白合成",
        "stage_code": "NARRATION_TTS",
        "output_kind": "NARRATION_TAKES",
        "required": True,
        "depends_on": ("NARRATION_WRITE",),
        "requires_capabilities": ("audio.tts",),
        "can_parallelise": True,
    },
    {
        "step_code": "NARRATION_ALIGN",
        "title": "强制对齐与复核",
        "stage_code": "NARRATION_ALIGN",
        "output_kind": "ALIGNMENT_REVISIONS",
        "required": True,
        "depends_on": ("NARRATION_TTS",),
        "requires_capabilities": ("audio.forced_alignment",),
        "can_parallelise": True,
    },
    {
        "step_code": "EXPLAINER_STORYBOARD",
        "title": "实测时长分镜计划",
        "stage_code": "EXPLAINER_STORYBOARD",
        "output_kind": "VISUAL_BEATS",
        "required": True,
        "depends_on": ("NARRATION_ALIGN", "IDENTITY_ASSETS", "NARRATION_WRITE"),
        "requires_capabilities": (),
        "can_parallelise": False,
    },
    {
        "step_code": "VISUAL_GENERATION",
        "title": "图像与视频候选",
        "stage_code": "EXPLAINER_STORYBOARD",
        "output_kind": "MEDIA_CANDIDATES",
        "required": True,
        "depends_on": ("EXPLAINER_STORYBOARD", "NARRATION_ALIGN"),
        "requires_capabilities": ("image.text_to_image", "video.image_to_video"),
        "can_parallelise": True,
    },
    {
        "step_code": "EXPLAINER_VISUAL_QC",
        "title": "内容与视觉质检",
        "stage_code": "EXPLAINER_VISUAL_QC",
        "output_kind": "QC_REPORT",
        "required": True,
        "depends_on": ("VISUAL_GENERATION",),
        "requires_capabilities": ("vision.qa",),
        "can_parallelise": True,
    },
    {
        "step_code": "SUBTITLE_BUILD",
        "title": "字幕与排版",
        "stage_code": "AUDIO_SUBTITLE",
        "output_kind": "SUBTITLE_REVISIONS",
        "required": True,
        "depends_on": ("NARRATION_ALIGN", "NARRATION_WRITE"),
        "requires_capabilities": (),
        "can_parallelise": True,
    },
    {
        "step_code": "COMPOSITION_RENDER",
        "title": "分块合成与全片渲染",
        "stage_code": "COMPOSITION_RENDER",
        "output_kind": "COMPOSITION_RENDER",
        "required": True,
        "depends_on": ("VISUAL_GENERATION", "SUBTITLE_BUILD", "EXPLAINER_VISUAL_QC"),
        "requires_capabilities": ("video.render",),
        "can_parallelise": False,
    },
    {
        "step_code": "COMPOSITION_QC",
        "title": "成片技术与会话质检",
        "stage_code": "COMPOSITION_QC",
        "output_kind": "QC_REPORT",
        "required": True,
        "depends_on": ("COMPOSITION_RENDER",),
        "requires_capabilities": (),
        "can_parallelise": False,
    },
    {
        "step_code": "EXPLAINER_POLICY_EVALUATE",
        "title": "政策判定",
        "stage_code": "EXPLAINER_POLICY_EVALUATE",
        "output_kind": "POLICY_DECISION",
        "required": True,
        "depends_on": ("COMPOSITION_QC", "EXPLAINER_VISUAL_QC"),
        "requires_capabilities": (),
        "can_parallelise": False,
    },
    {
        "step_code": "EXPLAINER_EXPORT",
        "title": "交付包与导出",
        "stage_code": "EXPLAINER_EXPORT",
        "output_kind": "PUBLICATION_PACKAGE",
        "required": True,
        "depends_on": ("EXPLAINER_POLICY_EVALUATE",),
        "requires_capabilities": (),
        "can_parallelise": True,
    },
)

#: The capability keys the explainer production graph depends on.
REQUIRED_CAPABILITIES: tuple[str, ...] = (
    "text.generation",
    "audio.tts",
    "audio.forced_alignment",
    "image.text_to_image",
    "image.reference_edit",
    "video.image_to_video",
    "vision.qa",
    "video.render",
)

#: Capabilities without which a run cannot complete but which may be reported as a
#: gap instead of a hard preflight failure, because a fallback path exists.
FALLBACKABLE_CAPABILITIES: frozenset[str] = frozenset({"video.image_to_video", "vision.qa"})


def skeleton_payload() -> list[dict[str, Any]]:
    return [
        {
            "step_code": step["step_code"],
            "title": step["title"],
            "output_kind": step["output_kind"],
            "required": step["required"],
            "depends_on": list(step["depends_on"]),
            "requires_capabilities": list(step["requires_capabilities"]),
        }
        for step in TASK_SKELETON
    ]


def _task_key(step_code: str) -> str:
    return f"explainer:{step_code}"


#: Which blocker code is reported first when a preflight blocks.  Ordered by the
#: operator's cheapest fix first so the surfaced error is actionable.
_BLOCKER_PRIORITY: tuple[str, ...] = (
    ExplainerErrorCode.SCHEMA_INVALID.value,
    ExplainerErrorCode.SOURCE_EVIDENCE_MISSING.value,
    ExplainerErrorCode.CLAIM_CONFLICT.value,
    ExplainerErrorCode.LICENSE_SCOPE_UNVERIFIED.value,
    ExplainerErrorCode.CAPABILITY_UNAVAILABLE.value,
    ExplainerErrorCode.INFERENCE_EGRESS_DENIED.value,
    ExplainerErrorCode.BUDGET_EXCEEDED.value,
    ExplainerErrorCode.GPU_CAPACITY_UNAVAILABLE.value,
)


def _capability_identity(capability_snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Deterministic capability identity for the plan hash.

    Two preflights that resolve the same capability set to the same workflow and
    profile versions produce the same plan hash even though they observed the
    world at different instants.
    """

    entries = capability_snapshot.get("capabilities") or []
    return {
        "probed": bool(capability_snapshot.get("probed")),
        "capabilities": [
            {
                "capability": entry.get("capability"),
                "status": entry.get("status"),
                "workflow_version_id": entry.get("workflow_version_id"),
                "profile_version_id": entry.get("profile_version_id"),
                "execution_class": entry.get("execution_class"),
            }
            for entry in sorted(entries, key=lambda item: str(item.get("capability")))
        ],
    }


def _primary_blocker_code(blockers: Sequence[Mapping[str, Any]]) -> str:
    present = {str(item.get("code")) for item in blockers}
    for code in _BLOCKER_PRIORITY:
        if code in present:
            return code
    return next(iter(present), ExplainerErrorCode.INVALID_REQUEST.value)


class ExplainerProductionService:
    """Create videos, freeze preflight plans and submit/project explainer runs."""

    def __init__(
        self,
        database: Any,
        *,
        capability_probe: Any | None = None,
        workflow_service: Any | None = None,
        repository: ExplainerRepository | None = None,
        settings: Any | None = None,
    ) -> None:
        self.database = database
        self._capability_probe = capability_probe
        self._workflow_service = workflow_service
        self._repository = repository
        self._settings = settings

    #: Explainer masters are generated at the configured proxy height (480p) and a
    #: later super-resolution step lifts them to the delivery height.  The value is
    #: read from the machine config, so an operator can change the canvas without a
    #: code change; the domain default applies when no settings were injected.
    def _generation_height(self) -> int:
        from local_drama.domain.explainers.contracts import DEFAULT_GENERATION_HEIGHT

        value = getattr(self._settings, "explainer_generation_height", None)
        try:
            height = int(value)
        except (TypeError, ValueError):
            return DEFAULT_GENERATION_HEIGHT
        return height if height >= 64 else DEFAULT_GENERATION_HEIGHT

    # ------------------------------------------------------------------ helpers
    def _repo(self, connection: sqlite3.Connection) -> ExplainerRepository:
        return self._repository if self._repository is not None else ExplainerRepository(connection)

    @staticmethod
    def _json(value: Any) -> str:
        from local_drama.domain.explainers.contracts import canonical_json

        return canonical_json(value)

    # ------------------------------------------------------------------ create
    def create_video(
        self,
        *,
        project_id: str,
        title: str,
        topic: str,
        content_kind: str,
        input_kind: str,
        input_payload: Mapping[str, Any],
        duration_mode: str,
        target_seconds: int,
        tolerance_percent: float,
        source_locale: str,
        automation_mode: str,
        inference_mode: str,
        research_mode: str,
        allowed_domains: Sequence[str] = (),
        channel_profile_id: str | None = None,
        channel_profile_version_id: str | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        """Create the one explainer video of a project in its own transaction.

        Callers that must create the project and the video atomically (the
        composite EXPLAINER creation command) use
        :meth:`insert_video_in_transaction` instead, so the two writes cannot be
        split across two independently committed transactions.
        """

        with self.database.transaction() as connection:
            return self.insert_video_in_transaction(
                connection,
                project_id=project_id,
                title=title,
                topic=topic,
                content_kind=content_kind,
                input_kind=input_kind,
                input_payload=input_payload,
                duration_mode=duration_mode,
                target_seconds=target_seconds,
                tolerance_percent=tolerance_percent,
                source_locale=source_locale,
                automation_mode=automation_mode,
                inference_mode=inference_mode,
                research_mode=research_mode,
                allowed_domains=allowed_domains,
                channel_profile_id=channel_profile_id,
                channel_profile_version_id=channel_profile_version_id,
                actor=actor,
            )

    def insert_video_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: str,
        title: str,
        topic: str,
        content_kind: str,
        input_kind: str,
        input_payload: Mapping[str, Any],
        duration_mode: str,
        target_seconds: int,
        tolerance_percent: float,
        source_locale: str,
        automation_mode: str,
        inference_mode: str,
        research_mode: str,
        allowed_domains: Sequence[str] = (),
        channel_profile_id: str | None = None,
        channel_profile_version_id: str | None = None,
        actor: str = "local-user",
        require_no_existing_video: bool = True,
    ) -> dict[str, Any]:
        """Validate and insert one explainer video inside the caller's transaction.

        ``require_no_existing_video`` stays ``True`` for the ordinary single-video
        API; the composite creation command passes ``False`` because it has just
        created the project itself and re-checks the composite idempotency receipt.
        """

        duration = DurationSpec(DurationMode(duration_mode), int(target_seconds), float(tolerance_percent))
        if content_kind not in {item.value for item in ContentKind}:
            raise ExplainerContractError("SCHEMA_INVALID", "内容类型不合法", {"content_kind": content_kind})
        if input_kind not in {item.value for item in InputKind}:
            raise ExplainerContractError("SCHEMA_INVALID", "输入方式不合法", {"input_kind": input_kind})
        if automation_mode not in {item.value for item in AutomationMode}:
            raise ExplainerContractError("SCHEMA_INVALID", "自动程度不合法", {"automation_mode": automation_mode})
        if inference_mode not in {item.value for item in InferenceMode}:
            raise ExplainerContractError("SCHEMA_INVALID", "推理模式不合法", {"inference_mode": inference_mode})
        if research_mode not in {item.value for item in ResearchMode}:
            raise ExplainerContractError("SCHEMA_INVALID", "资料模式不合法", {"research_mode": research_mode})
        if research_mode == ResearchMode.OFFLINE_IMPORT.value and allowed_domains:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "纯离线导入模式不能声明允许域名", {"allowed_domains": list(allowed_domains)}
            )

        repo = self._repo(connection)
        project = repo.project_row(project_id)
        if str(project.get("product_kind") or ProductKind.DRAMA.value) != ProductKind.EXPLAINER.value:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "该接口只创建解说作品；请先以 EXPLAINER 类型创建项目",
                {"project_id": project_id, "product_kind": project.get("product_kind")},
            )
        if require_no_existing_video and repo.video_for_project(project_id) is not None:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "一个解说项目默认对应一个解说作品；多语言/多画幅请创建 edition",
                {"project_id": project_id},
            )
        if not title or len(title) > 200:
            raise ExplainerContractError("SCHEMA_INVALID", "标题必须是 1–200 个字符")

        resolved_profile_version_id = channel_profile_version_id
        if resolved_profile_version_id is None and channel_profile_id:
            profile = repo.get("channel_profiles", channel_profile_id)
            resolved_profile_version_id = profile.get("current_version_id")
        if resolved_profile_version_id is not None:
            profile_version = repo.get("channel_profile_versions", resolved_profile_version_id)
            if str(profile_version.get("status")) not in {"DRAFT", "FROZEN"}:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "栏目版本不可用", {"channel_profile_version_id": resolved_profile_version_id}
                )

        video = repo.insert(
            "explainer_videos",
            {
                "project_id": project_id,
                "title": title,
                "topic": topic,
                "content_kind": content_kind,
                "source_locale": source_locale,
                "input_kind": input_kind,
                "input_payload_json": dict(input_payload),
                "duration_mode": duration.mode.value,
                "target_seconds": duration.target_seconds,
                "tolerance_percent": duration.tolerance_percent,
                "automation_mode": automation_mode,
                "inference_mode": inference_mode,
                "research_mode": research_mode,
                "research_allowed_domains_json": list(allowed_domains),
                "current_channel_profile_version_id": resolved_profile_version_id,
                "status": "DRAFT",
            },
            actor=actor,
        )
        connection.execute(
            "UPDATE projects SET target_duration_ms = ?, updated_at = ? WHERE id = ?",
            (duration.target_seconds * 1000, utc_now_iso(), project_id),
        )
        return video

    # ------------------------------------------------------------------ profile
    def create_channel_profile(
        self,
        *,
        project_id: str | None,
        code: str,
        title: str,
        version: Mapping[str, Any],
        actor: str = "local-user",
    ) -> dict[str, Any]:
        with self.database.transaction() as connection:
            repo = self._repo(connection)
            profile = repo.insert(
                "channel_profiles",
                {
                    "project_id": project_id,
                    "code": code,
                    "title": title,
                    "status": "ACTIVE",
                    "scope": "PROJECT" if project_id else "GLOBAL",
                },
                actor=actor,
            )
            version_no = repo.count("channel_profile_versions", {"channel_profile_id": profile["id"]}) + 1
            payload = {
                "render_style": version.get("render_style", ""),
                "palette_json": version.get("palette", {}),
                "camera_grammar_json": version.get("camera_grammar", {}),
                "lighting_json": version.get("lighting", {}),
                "typography_json": version.get("typography", {}),
                "subtitle_safe_area_json": version.get("subtitle_safe_area", {}),
                "transition_set_json": version.get("transition_set", []),
                "voice_json": version.get("voice", {}),
                "bgm_policy_json": version.get("bgm_policy", {}),
                "negative_constraints_json": version.get("negative_constraints", []),
                "license_policy_json": version.get("license_policy", {}),
            }
            profile_version = repo.insert(
                "channel_profile_versions",
                {
                    "channel_profile_id": profile["id"],
                    "version_no": version_no,
                    "title": version.get("title") or f"{title} v{version_no}",
                    "status": "FROZEN",
                    "content_hash": content_hash(payload),
                    **payload,
                },
                actor=actor,
            )
            repo.update("channel_profiles", profile["id"], {"current_version_id": profile_version["id"]})
            return {"channel_profile": repo.get("channel_profiles", profile["id"]), "version": profile_version}

    # ------------------------------------------------------------------ capabilities
    def capability_snapshot(self, *, project_id: str, required: Sequence[str] | None = None) -> dict[str, Any]:
        """Resolve which required capabilities are actually available.

        Uses an injected probe when the host provides one.  Without a probe every
        capability is reported ``UNKNOWN`` — never optimistically ``AVAILABLE``.
        """

        wanted = tuple(required or REQUIRED_CAPABILITIES)
        entries: list[dict[str, Any]] = []
        probe = self._capability_probe
        for capability in wanted:
            record: dict[str, Any] = {"capability": capability}
            if probe is None:
                record.update(
                    {
                        "status": "UNKNOWN",
                        "available": False,
                        "reason": "NO_CAPABILITY_PROBE_WIRED",
                        "fallbackable": capability in FALLBACKABLE_CAPABILITIES,
                    }
                )
            else:
                try:
                    resolved = probe(capability, project_id=project_id)
                except Exception as error:  # a probe failure is a gap, not a green light
                    record.update(
                        {
                            "status": "UNKNOWN",
                            "available": False,
                            "reason": f"PROBE_FAILED:{type(error).__name__}",
                            "fallbackable": capability in FALLBACKABLE_CAPABILITIES,
                        }
                    )
                else:
                    available = bool(resolved.get("available")) if isinstance(resolved, Mapping) else False
                    record.update(
                        {
                            "status": "AVAILABLE" if available else "UNAVAILABLE",
                            "available": available,
                            "workflow_version_id": (resolved or {}).get("workflow_version_id") if isinstance(resolved, Mapping) else None,
                            "profile_version_id": (resolved or {}).get("profile_version_id") if isinstance(resolved, Mapping) else None,
                            "execution_class": (resolved or {}).get("execution_class", "LOCAL") if isinstance(resolved, Mapping) else "LOCAL",
                            "reason": (resolved or {}).get("reason") if isinstance(resolved, Mapping) else None,
                            "fallbackable": capability in FALLBACKABLE_CAPABILITIES,
                            # The canonical name and the concrete resolution source
                            # are part of the frozen capability identity an operator
                            # needs in order to answer "which model/tool does this
                            # requirement really use".
                            "canonical_capability": (resolved or {}).get("canonical_capability") if isinstance(resolved, Mapping) else None,
                            "resolution": (resolved or {}).get("resolution") if isinstance(resolved, Mapping) else None,
                            "source": (resolved or {}).get("source") if isinstance(resolved, Mapping) else None,
                        }
                    )
            entries.append(record)
        return {
            "probed": probe is not None,
            "capabilities": entries,
            "unknown_count": sum(1 for entry in entries if entry["status"] == "UNKNOWN"),
            "unavailable_count": sum(1 for entry in entries if entry["status"] == "UNAVAILABLE"),
            "resolved_at": utc_now_iso(),
        }

    # ------------------------------------------------------------------ preflight
    def preflight(
        self,
        *,
        project_id: str,
        outputs: Sequence[Mapping[str, Any]] | None = None,
        budget: Mapping[str, Any] | None = None,
        fallback_policy: Mapping[str, Any] | None = None,
        parent_plan_id: str | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            repo = self._repo(connection)
            repo.require_explainer_project(project_id)
            video = repo.require_video_for_project(project_id)
            blockers: list[PreflightBlocker] = []

            duration = DurationSpec(
                DurationMode(str(video["duration_mode"])),
                int(video["target_seconds"]),
                float(video["tolerance_percent"]),
            )
            resolved_outputs = [dict(item) for item in (outputs or [])]
            if not resolved_outputs:
                existing_editions = repo.editions(str(video["id"]))
                if existing_editions:
                    resolved_outputs = [
                        {
                            "edition_key": str(item["edition_key"]),
                            "voice_locale": str(item["voice_locale"]),
                            "subtitle_locales": list(item.get("subtitle_locales_json") or [item["voice_locale"]]),
                            "subtitle_mode": str(item.get("subtitle_mode") or "BURNED"),
                            "aspect_ratio": str(item.get("aspect_ratio") or "16:9"),
                            "fps": {"num": int(item.get("fps_num") or 25), "den": int(item.get("fps_den") or 1)},
                            "duration_policy": str(item.get("duration_policy") or "NATURAL_NARRATION"),
                            "allow_soft_subtitle_fallback": bool(item.get("allow_soft_subtitle_fallback")),
                        }
                        for item in existing_editions
                    ]
                else:
                    project = repo.project_row(project_id)
                    aspect = str(project.get("aspect_ratio") or "16:9")
                    voice_locale = str(video.get("source_locale") or project.get("primary_language") or "zh-CN")
                    subtitle_mode = str(project.get("subtitle_mode") or "BURNED")
                    lang = voice_locale.split("-")[0].lower()
                    sub_tag = (
                        "clean"
                        if subtitle_mode == "NONE"
                        else "bilingual"
                        if subtitle_mode == "BILINGUAL_BURNED"
                        else "captioned"
                    )
                    asp = aspect.replace(":", "")
                    key = f"{lang}-{sub_tag}-{asp}"
                    resolved_outputs = [
                        {
                            "edition_key": key,
                            "voice_locale": voice_locale,
                            "subtitle_locales": [voice_locale] if subtitle_mode != "NONE" else [],
                            "subtitle_mode": subtitle_mode,
                            "aspect_ratio": aspect,
                            "fps": {
                                "num": int(project.get("fps_num") or 25),
                                "den": int(project.get("fps_den") or 1),
                            },
                            "duration_policy": "NATURAL_NARRATION",
                            "allow_soft_subtitle_fallback": False,
                        }
                    ]
            if not resolved_outputs:
                blockers.append(
                    PreflightBlocker(
                        "SCHEMA_INVALID",
                        "至少需要一个输出 edition（语言/画幅/字幕组合）",
                        scope="OUTPUTS",
                        next_step="在创建或预检请求中声明至少一个输出。",
                    )
                )
            seen_keys: set[str] = set()
            for output in resolved_outputs:
                key = str(output.get("edition_key") or "")
                if not key:
                    blockers.append(
                        PreflightBlocker("SCHEMA_INVALID", "输出缺少 edition_key", scope="OUTPUTS")
                    )
                    continue
                if key in seen_keys:
                    blockers.append(
                        PreflightBlocker(
                            "SCHEMA_INVALID",
                            "同一作品的 edition_key 必须唯一",
                            scope="OUTPUTS",
                            details={"edition_key": key},
                        )
                    )
                seen_keys.add(key)
                voice_locale = str(output.get("voice_locale") or video["source_locale"])
                if str(output.get("subtitle_mode") or "NONE") == "NONE" and output.get("subtitle_locales"):
                    blockers.append(
                        PreflightBlocker(
                            "SCHEMA_INVALID",
                            "subtitle_mode=NONE 与字幕语言冲突",
                            scope="OUTPUTS",
                            details={"edition_key": key},
                        )
                    )
                if output.get("aspect_ratio") not in {None, "16:9", "9:16", "3:4", "1:1"}:
                    blockers.append(
                        PreflightBlocker(
                            "SCHEMA_INVALID",
                            "不支持的画幅",
                            scope="OUTPUTS",
                            details={"edition_key": key, "aspect_ratio": output.get("aspect_ratio")},
                        )
                    )
                fps_num = int((output.get("fps") or {}).get("num") or 25)
                fps_den = int((output.get("fps") or {}).get("den") or 1)
                if fps_num <= 0 or fps_den <= 0:
                    blockers.append(
                        PreflightBlocker(
                            "SCHEMA_INVALID", "帧率有理数必须为正", scope="OUTPUTS", details={"edition_key": key}
                        )
                    )
                if voice_locale and voice_locale != video["source_locale"]:
                    # An independently clocked edition is supported; record it.
                    pass

            # ``or`` is the wrong default operator here: an explicitly supplied
            # ``0`` or ``[]`` is a legitimate instruction ("no repairs", "no
            # fallback allowed"), and treating it as "not provided" widened the
            # frozen authorization snapshot beyond what the caller granted.
            resolved_budget = dict(budget or {})
            budget_obj = Budget(
                max_gpu_seconds=int(_explicit(resolved_budget, "max_gpu_seconds", 36_000)),
                max_wall_seconds=int(_explicit(resolved_budget, "max_wall_seconds", 43_200)),
                initial_candidates_per_ordinary_beat=int(
                    _explicit(resolved_budget, "initial_candidates_per_ordinary_beat", 1)
                ),
                initial_candidates_per_key_identity=int(
                    _explicit(resolved_budget, "initial_candidates_per_key_identity", 2)
                ),
                max_creative_repairs_per_beat=int(
                    _explicit(resolved_budget, "max_creative_repairs_per_beat", 2)
                ),
                max_technical_retries_per_step=int(
                    _explicit(resolved_budget, "max_technical_retries_per_step", 2)
                ),
                max_script_revisions=int(_explicit(resolved_budget, "max_script_revisions", 0)),
            )
            resolved_fallback = dict(fallback_policy or {})
            fallback = FallbackPolicy(
                allowed_visual_fallbacks=tuple(
                    _explicit(
                        resolved_fallback,
                        "allowed_visual_fallbacks",
                        ("I2V_TO_MOTION_STILL", "I2V_TO_INFORMATION_GRAPHIC"),
                    )
                ),
                script_rewrite_policy=str(
                    _explicit(resolved_fallback, "script_rewrite_policy", "NO_AUTOMATIC_REWRITE")
                ),
                max_script_revisions=int(
                    _explicit(resolved_fallback, "max_script_revisions", budget_obj.max_script_revisions)
                ),
            )

            # Source material gate (design §5.2): a topic/import input must have
            # real, same-project source material before an executable plan exists.
            input_kind = str(video["input_kind"])
            sources = repo.list_where("explainer_sources", {"video_id": video["id"]}, order_by="created_at", descending=False)
            if input_kind in {InputKind.DOCUMENT_IMPORT.value, InputKind.REFERENCE_LINKS.value} and not sources:
                blockers.append(
                    PreflightBlocker(
                        ExplainerErrorCode.SOURCE_EVIDENCE_MISSING.value,
                        "尚未导入任何来源资料",
                        scope="SOURCES",
                        next_step="先导入资料或参考链接，再重新预检。",
                    )
                )
            if input_kind == InputKind.PASTED_SCRIPT.value and not repo.list_where(
                "explainer_script_revisions", {"video_id": video["id"]}
            ):
                blockers.append(
                    PreflightBlocker(
                        "SCHEMA_INVALID",
                        "粘贴讲解稿模式尚未创建讲稿版本",
                        scope="SCRIPT",
                        next_step="先保存讲稿 revision，再重新预检。",
                    )
                )

            # Content gate: unresolved core conflicts block an executable plan.
            core_conflicts = repo.open_core_conflicts(video["id"])
            if core_conflicts:
                blockers.append(
                    PreflightBlocker(
                        ExplainerErrorCode.CLAIM_CONFLICT.value,
                        "存在未解决的核心事实冲突，不能自动生成成片",
                        scope="CLAIMS",
                        details={"claim_codes": [str(item["code"]) for item in core_conflicts]},
                    )
                )

            # Local capability gate.
            capability_snapshot = self.capability_snapshot(project_id=project_id)
            if capability_snapshot["probed"]:
                for entry in capability_snapshot["capabilities"]:
                    if entry["available"]:
                        continue
                    if entry["fallbackable"]:
                        # Allowed to proceed: the fallback path must be pre-authorized.
                        if str(video["content_kind"]) == ContentKind.ORIGINAL_FICTION.value and entry["capability"] == "video.image_to_video":
                            pass
                        if not fallback.allowed_visual_fallbacks:
                            blockers.append(
                                PreflightBlocker(
                                    ExplainerErrorCode.CAPABILITY_UNAVAILABLE.value,
                                    f"缺少能力 {entry['capability']}，且未预先授权任何视觉回退路径",
                                    scope="CAPABILITY",
                                    details=entry,
                                )
                            )
                    else:
                        blockers.append(
                            PreflightBlocker(
                                ExplainerErrorCode.CAPABILITY_UNAVAILABLE.value,
                                f"本地缺少必需能力 {entry['capability']}",
                                scope="CAPABILITY",
                                details=entry,
                            )
                        )
            else:
                blockers.append(
                    PreflightBlocker(
                        ExplainerErrorCode.CAPABILITY_UNAVAILABLE.value,
                        "尚未接入能力探查，无法确认本地能力与工作流版本",
                        scope="CAPABILITY",
                        details={"reason": "NO_CAPABILITY_PROBE_WIRED"},
                        next_step="在模型中心完成能力注册与 smoke 后再预检。",
                    )
                )

            # License gate for the requested distribution scope.
            if str(video["inference_mode"]) == InferenceMode.ALLOW_CONFIGURED_CLOUD.value:
                blockers.append(
                    PreflightBlocker(
                        ExplainerErrorCode.INFERENCE_EGRESS_DENIED.value,
                        "本次交付默认仅支持纯本地推理；如需云端能力须显式配置并重新预检",
                        scope="POLICY",
                    )
                )

            # TTS clock gate: the final shot plan depends on real audio.
            has_script = bool(repo.list_where("explainer_script_revisions", {"video_id": video["id"]}))
            estimate = self._estimate(
                video=video,
                duration=duration,
                has_script=has_script,
                measured_takes=repo.selected_takes(video["id"]),
            )

            frozen_inputs = {
                "video_id": video["id"],
                "project_id": project_id,
                "title": video["title"],
                "topic": video["topic"],
                "content_kind": video["content_kind"],
                "input_kind": input_kind,
                "input_payload": video.get("input_payload_json") or {},
                "source_locale": video["source_locale"],
                "duration": duration.as_dict(),
                "outputs": resolved_outputs,
                "channel_profile_version_id": video.get("current_channel_profile_version_id"),
                # Only the *identity* of the frozen source set is hashed.  Counting
                # revisions here would make an otherwise unchanged plan look stale
                # as soon as the authorized internal expansion adds a revision.
                "source_ids": sorted(str(item["id"]) for item in sources),
                "source_hashes": sorted(str(item["body_sha256"]) for item in sources),
                "inference_mode": video["inference_mode"],
                "research_mode": video["research_mode"],
                "allowed_domains": list(video.get("research_allowed_domains_json") or []),
            }
            policy_snapshot = {
                "automation_mode": video["automation_mode"],
                "policy_rule_version": POLICY_RULE_VERSION,
                "policy_processor": POLICY_PROCESSOR_NAME,
                "thresholds": Thresholds().as_dict(),
                "fallback_policy": fallback.as_dict(),
                "per_shot_human_approval_required": str(video["automation_mode"]) == AutomationMode.MANUAL_REVIEW.value,
                "machine_may_write_human_approval": False,
            }
            plan_basis = {
                "schema_version": SCHEMA_VERSION,
                "frozen_inputs": frozen_inputs,
                "policy_snapshot": policy_snapshot,
                # Only the *identity* of the resolved capabilities is hashed.  The
                # observation timestamp is audit metadata and must not make an
                # otherwise unchanged plan look stale.
                "capability_identity": _capability_identity(capability_snapshot),
                "budget": budget_obj.as_dict(),
                "task_skeleton": skeleton_payload(),
                "parent_plan_id": parent_plan_id,
            }
            plan_hash = content_hash(plan_basis)

        categories: dict[str, tuple[PreflightBlocker, ...]] = {
            category: tuple(item for item in blockers if classify_blocker(item.code) == category)
            for category in (
                "EXECUTABLE",
                "NEEDS_SOURCE_MATERIAL",
                "MISSING_LOCAL_CAPABILITY",
                "INSUFFICIENT_ESTIMATED_RESOURCES",
                "LICENSE_SCOPE_UNCONFIRMED",
            )
        }
        executable = not blockers
        return {
            "project_id": project_id,
            "video_id": str(video["id"]),
            "status": "EXECUTABLE" if executable else "BLOCKED",
            "executable": executable,
            "would_create_jobs": False,
            "categories": {
                category: [item.as_dict() for item in items] for category, items in categories.items()
            },
            "blockers": [item.as_dict() for item in blockers],
            "plan_hash": plan_hash,
            "parent_plan_id": parent_plan_id,
            "frozen_inputs": frozen_inputs,
            "policy_snapshot": policy_snapshot,
            "capability_snapshot": capability_snapshot,
            "budget": budget_obj.as_dict(),
            "task_skeleton": skeleton_payload(),
            "estimate": estimate,
            "generated_at": utc_now_iso(),
            "plan_scope": {
                "covers": ["input", "channel_and_policy_versions", "required_capabilities", "coarse_budget", "task_skeleton"],
                "does_not_cover": ["script_revision", "narration_takes", "final_shot_plan"],
                "internal_expansion_authorized": True,
                "internal_expansion_can_self_stale": False,
            },
        }

    def _estimate(
        self,
        *,
        video: Mapping[str, Any],
        duration: DurationSpec,
        has_script: bool,
        measured_takes: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        measured_total_ms = sum(int(item.get("measured_duration_ms") or 0) for item in measured_takes)
        if measured_takes and measured_total_ms > 0:
            stage = "MEASURED_TTS"
            basis = {
                "measured_segment_count": len(measured_takes),
                "measured_total_ms": measured_total_ms,
                "timing_status": "MEASURED_FROM_REAL_AUDIO",
            }
        elif has_script:
            stage = "SCRIPT_ESTIMATE"
            basis = {
                "timing_status": "SCRIPT_ESTIMATE_NOT_MEASURED_TTS",
                "measured_total_ms": None,
            }
        else:
            stage = "TOPIC_ROUGH_ESTIMATE"
            basis = {"timing_status": "TOPIC_ROUGH_ESTIMATE", "measured_total_ms": None}

        shot_budget = estimate_shot_budget(
            target_seconds=duration.target_seconds,
            average_shot_seconds=7.5,
            motion_ratio=0.3,
        )
        fps = Ratio(25, 1)
        return {
            "stage": stage,
            "duration_mode": duration.mode.value,
            "target_seconds": duration.target_seconds,
            "natural_bounds_seconds": list(duration.natural_bounds_seconds()),
            "target_frames_at_25fps": fps.frames_for_seconds(duration.target_seconds)
            if duration.mode is DurationMode.FIXED
            else None,
            "shot_arithmetic": shot_budget,
            "timing_basis": basis,
            "measured_samples": len(measured_takes) or None,
            "note": "这是镜头数算术与文本估算，不是生成速度承诺；真实耗时必须在预检后由本机 profile 实测。",
        }

    # ------------------------------------------------------------------ run
    def submit_run(
        self,
        *,
        project_id: str,
        plan_hash: str,
        idempotency_key: str,
        outputs: Sequence[Mapping[str, Any]] | None = None,
        budget: Mapping[str, Any] | None = None,
        fallback_policy: Mapping[str, Any] | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        if not idempotency_key or len(idempotency_key) > 200:
            raise ExplainerContractError(
                "IDEMPOTENCY_KEY_REQUIRED", "提交生产必须提供有效 Idempotency-Key"
            )
        with self.database.transaction() as connection:
            repo = self._repo(connection)
            video = repo.require_video_for_project(project_id)
            replay = repo.run_by_idempotency(project_id, idempotency_key)
            if replay is not None:
                return {**replay, "idempotent_replay": True}

        fresh = self.preflight(
            project_id=project_id,
            outputs=outputs,
            budget=budget,
            fallback_policy=fallback_policy,
            actor=actor,
        )
        if fresh["plan_hash"] != plan_hash:
            if not outputs:
                # The frozen plan is defined by its outputs; a submission without
                # them can only produce an opaque hash mismatch.  Say what is
                # missing so the caller can resend the plan it preflighted.
                raise ExplainerContractError(
                    "PLAN_INPUTS_REQUIRED",
                    "提交必须带上预检时冻结的完整计划输入（outputs/budget/fallback_policy）",
                    {
                        "submitted_plan_hash": plan_hash,
                        "current_plan_hash": fresh["plan_hash"],
                        "missing": ["outputs"],
                    },
                )
            raise ExplainerContractError(
                ExplainerErrorCode.STALE_PLAN.value,
                "计划已过期：输入、政策或能力发生变化，请基于新的预检结果重新提交",
                {
                    "submitted_plan_hash": plan_hash,
                    "current_plan_hash": fresh["plan_hash"],
                    "submitted_outputs": [str(item.get("edition_key")) for item in (outputs or [])],
                },
            )
        if not fresh["executable"]:
            raise ExplainerContractError(
                _primary_blocker_code(fresh["blockers"]),
                "预检未通过，不能提交生产",
                {"blockers": fresh["blockers"]},
            )

        with self.database.transaction() as connection:
            repo = self._repo(connection)
            video = repo.require_video_for_project(project_id)
            # The frozen plan names its output editions, and every later stage needs
            # the edition row to exist (language clock, canvas, subtitle mode).  The
            # write belongs here, in the same transaction as the run that consumes
            # it, so a submitted plan can never point at an edition that was never
            # created.
            effective_outputs = outputs or fresh.get("frozen_inputs", {}).get("outputs") or []
            if effective_outputs:
                from local_drama.application.explainers.production_pipeline import (
                    ensure_editions_for_outputs,
                )

                ensure_editions_for_outputs(
                    repo,
                    video=video,
                    outputs=effective_outputs,
                    generation_height=self._generation_height(),
                )
            run = repo.insert(
                "explainer_runs",
                {
                    "project_id": project_id,
                    "video_id": video["id"],
                    "status": RunStatus.QUEUED.value,
                    "automation_mode": video["automation_mode"],
                    "plan_hash": plan_hash,
                    "plan_json": {
                        "frozen_inputs": fresh["frozen_inputs"],
                        "policy_snapshot": fresh["policy_snapshot"],
                        "estimate": fresh["estimate"],
                    },
                    "frozen_inputs_json": fresh["frozen_inputs"],
                    "policy_snapshot_json": fresh["policy_snapshot"],
                    "capability_snapshot_json": fresh["capability_snapshot"],
                    "budget_json": fresh["budget"],
                    "inference_mode": video["inference_mode"],
                    "research_mode": video["research_mode"],
                    "current_stage_code": "RESEARCH_ACQUIRE",
                    "progress_json": {"completed_steps": 0, "total_steps": len(TASK_SKELETON)},
                    "budget_used_json": BudgetLedger(Budget(**fresh["budget"])).as_dict(),
                    "idempotency_key": idempotency_key,
                    "started_at": utc_now_iso(),
                },
                actor=actor,
            )
            for ordinal, step in enumerate(TASK_SKELETON):
                repo.insert(
                    "explainer_step_bindings",
                    {
                        "run_id": run["id"],
                        "video_id": video["id"],
                        "planned_step_code": step["step_code"],
                        "task_key": _task_key(step["step_code"]),
                        "status": "PENDING" if not step["depends_on"] else "BLOCKED",
                        "output_kind": step["output_kind"],
                        "planned_inputs_json": {
                            "ordinal": ordinal,
                            "depends_on": list(step["depends_on"]),
                            "requires_capabilities": list(step["requires_capabilities"]),
                            "required": step["required"],
                        },
                    },
                    actor=actor,
                )
        return self.get_run(run_id=str(run["id"]))

    # ------------------------------------------------------------------ projection
    def get_run(self, *, run_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            repo = self._repo(connection)
            run = repo.get("explainer_runs", run_id)
            steps = repo.steps(run_id)
            workflow_run = None
            if run.get("automation_workflow_run_id") and self._workflow_service is not None:
                try:
                    workflow_run = self._workflow_service.get_run(str(run["automation_workflow_run_id"]))
                except Exception:
                    workflow_run = None
            video = repo.get("explainer_videos", str(run["video_id"]))
        step_statuses = {str(step["planned_step_code"]): str(step["status"]) for step in steps}
        blockers = list(run.get("blockers_json") or [])
        # Design §7.5: a run may only read COMPLETED when every required step is
        # settled *and* the export step succeeded.  Checking the export step alone
        # let a run look finished while another required step was still pending or
        # stale.
        settled_success = {"SUCCEEDED", "SKIPPED_WITH_REASON"}
        all_required_settled = all(
            step_statuses.get(step["step_code"]) in settled_success for step in TASK_SKELETON
        )
        projected = project_run_status(
            step_statuses=step_statuses,
            has_blockers=bool(blockers),
            cancel_requested=bool(run.get("cancel_requested_at")),
            ready_to_export=all(
                step_statuses.get(step["step_code"]) == "SUCCEEDED" for step in TASK_SKELETON[: -1]
            )
            and step_statuses.get("EXPLAINER_EXPORT") in {None, "PENDING", "RUNNING"},
            exporting=step_statuses.get("EXPLAINER_EXPORT") == "RUNNING",
            completed=all_required_settled and step_statuses.get("EXPLAINER_EXPORT") == "SUCCEEDED",
        )
        if workflow_run is not None and workflow_run.get("status") == "PAUSED_HITL":
            projected = RunStatus.WAITING_INPUT.value
        return {
            **run,
            "projected_status": projected,
            "steps": steps,
            "step_statuses": step_statuses,
            "workflow_run": {
                "id": workflow_run.get("id"),
                "status": workflow_run.get("status"),
                "human_approval_status": workflow_run.get("human_approval_status"),
                "pending_gate": workflow_run.get("pending_gate"),
            }
            if workflow_run
            else None,
            "video": {
                "id": video["id"],
                "title": video["title"],
                "content_kind": video["content_kind"],
                "target_seconds": video["target_seconds"],
                "duration_mode": video["duration_mode"],
                "automation_mode": video["automation_mode"],
            },
            "execution_authority": {
                "source_of_truth": "automation_workflow_runs+jobs+job_attempts",
                "explainer_runs_is_projection": True,
                "second_claim_queue": False,
            },
        }

    def list_runs(self, *, project_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            repo = self._repo(connection)
            return repo.list_where(
                "explainer_runs", {"project_id": project_id}, order_by="created_at", descending=True, limit=limit
            )

    def set_step_status(
        self,
        *,
        run_id: str,
        step_code: str,
        status: str,
        job_id: str | None = None,
        output_ref: Mapping[str, Any] | None = None,
        skip_reason: str | None = None,
        blocker_code: str | None = None,
        error_detail: str | None = None,
        actor: str = "system",
    ) -> dict[str, Any]:
        if status == "SKIPPED_WITH_REASON" and not (skip_reason or "").strip():
            raise ExplainerContractError(
                "SCHEMA_INVALID", "跳过的可选步骤必须写明原因", {"step_code": step_code}
            )
        with self.database.transaction() as connection:
            repo = self._repo(connection)
            step = repo.step_by_task_key(run_id, _task_key(step_code))
            if step is None:
                raise ExplainerContractError(
                    "NOT_FOUND", "运行中没有该步骤绑定", {"run_id": run_id, "step_code": step_code}
                )
            payload: dict[str, Any] = {"status": status}
            if job_id:
                payload["job_id"] = job_id
            if output_ref is not None:
                payload["output_ref_json"] = output_ref
            if skip_reason is not None:
                payload["skip_reason"] = skip_reason
            if blocker_code is not None:
                payload["blocker_code"] = blocker_code
            if error_detail is not None:
                payload["error_detail_redacted"] = error_detail
            if status in {"SUCCEEDED", "SKIPPED_WITH_REASON", "TERMINAL_FAILED", "CANCELLED"}:
                payload["finished_at"] = utc_now_iso()
            if status == "RUNNING":
                payload["started_at"] = utc_now_iso()
            if status in {"RETRYABLE_FAILED", "TERMINAL_FAILED"}:
                payload["attempt_count"] = int(step.get("attempt_count") or 0) + 1
            updated = repo.update("explainer_step_bindings", str(step["id"]), payload)
            self._unblock_dependents(repo, run_id=run_id)
            progress = self._progress(repo, run_id)
            repo.update("explainer_runs", run_id, {"progress_json": progress, "current_stage_code": step_code})
            return {"step": updated, "progress": progress}

    @staticmethod
    def _unblock_dependents(repo: ExplainerRepository, *, run_id: str) -> None:
        steps = {str(item["planned_step_code"]): item for item in repo.steps(run_id)}
        for code, step in steps.items():
            if str(step["status"]) != "BLOCKED":
                continue
            spec = next((item for item in TASK_SKELETON if item["step_code"] == code), None)
            if spec is None:
                continue
            satisfied = all(
                str(steps.get(dependency, {}).get("status")) in {"SUCCEEDED", "SKIPPED_WITH_REASON"}
                for dependency in spec["depends_on"]
            )
            if satisfied:
                repo.update("explainer_step_bindings", str(step["id"]), {"status": "PENDING"})

    @staticmethod
    def _progress(repo: ExplainerRepository, run_id: str) -> dict[str, Any]:
        steps = repo.steps(run_id)
        counts: dict[str, int] = {}
        for step in steps:
            key = str(step["status"])
            counts[key] = counts.get(key, 0) + 1
        return {
            "total_steps": len(steps),
            "completed_steps": counts.get("SUCCEEDED", 0) + counts.get("SKIPPED_WITH_REASON", 0),
            "running_steps": counts.get("RUNNING", 0),
            "blocked_steps": counts.get("BLOCKED", 0),
            "failed_steps": counts.get("RETRYABLE_FAILED", 0) + counts.get("TERMINAL_FAILED", 0),
            "status_counts": counts,
            "by_step": {str(step["planned_step_code"]): str(step["status"]) for step in steps},
        }

    def start_run(
        self,
        *,
        project_id: str,
        plan_hash: str,
        idempotency_key: str,
        outputs: Sequence[Mapping[str, Any]] | None = None,
        budget: Mapping[str, Any] | None = None,
        fallback_policy: Mapping[str, Any] | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        """Submit the plan and hand execution to the existing declarative workflow.

        The explainer graph is expressed as one automation workflow run with one
        batch item per step, so cancel/pause/resume/recovery keep using the
        existing job and workflow facilities instead of a parallel executor.

        ``outputs`` / ``budget`` / ``fallback_policy`` are forwarded verbatim.  They
        used to be dropped here while ``submit_run`` re-ran the preflight with an
        *empty* output list, so the frozen plan hash could never match the submitted
        one and every legitimate "check and one-click generate" ended in
        ``409 STALE_PLAN`` with ``submitted_outputs=[]``.
        """

        submitted = self.submit_run(
            project_id=project_id,
            plan_hash=plan_hash,
            idempotency_key=idempotency_key,
            outputs=outputs,
            budget=budget,
            fallback_policy=fallback_policy,
            actor=actor,
        )
        if submitted.get("idempotent_replay"):
            return submitted
        if self._workflow_service is None:
            return {
                **submitted,
                "workflow_started": False,
                "workflow_reason": "NO_WORKFLOW_SERVICE_WIRED",
                "steps": self.get_run(run_id=str(submitted["id"]))["steps"],
            }
        workflow = self._ensure_workflow(project_id=project_id, video_id=str(submitted["video_id"]), actor=actor)
        run = self._workflow_service.start_run(
            str(workflow["id"]), plan_hash=str(workflow["plan_hash"]), idempotency_key=f"explainer-run:{submitted['id']}", actor=actor
        )
        with self.database.transaction() as connection:
            ExplainerRepository(connection).update(
                "explainer_runs",
                str(submitted["id"]),
                {
                    "automation_workflow_run_id": run["id"],
                    "automation_workflow_id": workflow["id"],
                    "status": RunStatus.RUNNING.value,
                    "plan_revision": 1,
                },
            )
        return {**self.get_run(run_id=str(submitted["id"])), "workflow_started": True}

    def ensure_workflow(self, *, project_id: str, video_id: str, actor: str) -> dict[str, Any]:
        """Public accessor for the explainer production workflow of one video.

        Reuses the workflow already bound to the video, so a repeated submission
        or a scheduled trigger never forks a second production graph.
        """

        if self._workflow_service is None:
            raise ExplainerContractError(
                ExplainerErrorCode.CAPABILITY_UNAVAILABLE.value,
                "尚未接入 automation workflow 服务，无法提交生产",
                {"project_id": project_id},
            )
        return self._ensure_workflow(project_id=project_id, video_id=video_id, actor=actor)

    def _ensure_workflow(self, *, project_id: str, video_id: str, actor: str) -> dict[str, Any]:
        existing = self._workflow_service.list_workflows(project_id)
        for item in existing.get("items", []):
            if str(item.get("code")) == f"EXPLAINER_{video_id}":
                return item
        definition_conditions = [
            {"field": "task.status", "operator": "EQ", "value": "FAILED", "action": "PAUSE_HITL"},
            {"field": "machine_check.status", "operator": "EQ", "value": "BLOCKED", "action": "PAUSE_HITL"},
            {"field": "task_count", "operator": "GTE", "value": len(TASK_SKELETON) + 1, "action": "STOP"},
        ]
        return self._workflow_service.create_workflow(
            project_id,
            code=f"EXPLAINER_{video_id}",
            title="解说一键生产",
            mode="BATCH_AUTOMATED",
            nodes=[{"id": step["step_code"], "type": step["stage_code"]} for step in TASK_SKELETON],
            batch_items=[
                {"key": _task_key(step["step_code"]), "payload": {"step_code": step["step_code"], "output_kind": step["output_kind"]}}
                for step in TASK_SKELETON
            ],
            conditions=definition_conditions,
            max_iterations=len(TASK_SKELETON) * 4,
            max_tasks=len(TASK_SKELETON),
            max_disk_bytes=64 * 1024 * 1024 * 1024,
            human_gate="ON_CONDITION",
            repeat_batch=False,
            template_code="EXPLAINER_FULL_PRODUCTION",
            actor=actor,
        )

    # ------------------------------------------------------------------ control
    def request_control(self, *, run_id: str, action: str, reason: str = "", actor: str = "local-user") -> dict[str, Any]:
        normalized = action.strip().lower()
        if normalized not in {"pause", "resume", "cancel"}:
            raise ExplainerContractError("INVALID_REQUEST", "支持的控制动作是 pause/resume/cancel", {"action": action})
        with self.database.transaction() as connection:
            repo = self._repo(connection)
            run = repo.get("explainer_runs", run_id)
            workflow_run_id = run.get("automation_workflow_run_id")
            now = utc_now_iso()
            if normalized == "pause":
                repo.update("explainer_runs", run_id, {"status": RunStatus.PAUSING.value})
            elif normalized == "cancel":
                repo.update("explainer_runs", run_id, {"status": RunStatus.CANCELLING.value, "cancel_requested_at": now})
            else:
                repo.update("explainer_runs", run_id, {"status": RunStatus.RUNNING.value})
        workflow_result: dict[str, Any] | None = None
        if workflow_run_id and self._workflow_service is not None:
            try:
                if normalized == "pause":
                    workflow_result = self._workflow_service.pause_run(str(workflow_run_id), reason=reason or "USER_PAUSE", actor=actor)
                elif normalized == "cancel":
                    workflow_result = self._workflow_service.cancel_run(str(workflow_run_id), actor=actor)
                else:
                    # ``resume_run`` is the HITL decision entry point: its vocabulary
                    # is HUMAN_APPROVED / HUMAN_REJECTED.  Passing "CONTINUE" (the
                    # internal action word) raised AUTOMATION_HITL_DECISION_INVALID
                    # inside the workflow service, so the operator's 继续 click only
                    # looked like it resumed while the run stayed PAUSED_HITL.
                    workflow_result = self._workflow_service.resume_run(
                        str(workflow_run_id),
                        decision="HUMAN_APPROVED",
                        note=reason or "USER_RESUME",
                        actor=actor,
                    )
            except Exception as error:
                workflow_result = {"error": type(error).__name__, "detail": str(error)[:200]}
        return {
            "run": self.get_run(run_id=run_id),
            "action": normalized,
            "workflow_control": workflow_result,
            "note": "取消只停止后续调度并向底层作业发送取消；迟到结果会被隔离，不会被采用或发布。"
            if normalized == "cancel"
            else "",
        }

    # ------------------------------------------------------------------ repairs
    def plan_repairs(
        self,
        *,
        project_id: str,
        issue_ids: Sequence[str],
        budget: Mapping[str, Any] | None = None,
        expected_revision: int,
    ) -> dict[str, Any]:
        if not issue_ids:
            raise ExplainerContractError("SCHEMA_INVALID", "局部返工必须指定 issue_ids")
        with self.database.connect() as connection:
            repo = self._repo(connection)
            video = repo.require_video_for_project(project_id)
            issues = [repo.get("explainer_qc_issues", issue_id) for issue_id in issue_ids]
            for issue in issues:
                if str(issue["video_id"]) != str(video["id"]):
                    raise ExplainerContractError(
                        "INVALID_REQUEST", "问题不属于该解说作品", {"issue_id": issue["id"]}
                    )
            current_revision = int(video.get("revision") or 1)
            if current_revision != int(expected_revision):
                raise ExplainerContractError(
                    ExplainerErrorCode.STALE_REVISION.value,
                    "作品已被其他操作更新，请基于最新 revision 提交返工",
                    {"expected_revision": expected_revision, "actual_revision": current_revision},
                )
            beats = {str(item["id"]): item for item in repo.beats(str(video["id"]))}
            locked = {beat_id for beat_id, beat in beats.items() if repo.has_human_lock(beat_id)}

        impacted_beats: list[str] = []
        responsible_steps: set[str] = set()
        for issue in issues:
            step = str(issue.get("responsible_step_code") or "")
            if step:
                responsible_steps.add(step)
            if issue.get("beat_id"):
                impacted_beats.append(str(issue["beat_id"]))
        skipped_locked = sorted({beat_id for beat_id in impacted_beats if beat_id in locked})
        runnable = sorted({beat_id for beat_id in impacted_beats if beat_id not in locked})
        return {
            "project_id": project_id,
            "video_id": str(video["id"]),
            "issue_ids": list(issue_ids),
            "revision": current_revision,
            "responsible_steps": sorted(responsible_steps) or ["EXPLAINER_STORYBOARD"],
            "beats": runnable,
            "locked_beats_skipped": skipped_locked,
            "invalidates": ["BEAT_SELECTION", "COMPOSITION_REVISION"],
            "preserves": ["FACT_LEDGER", "SCRIPT_FACTS", "OFFSCREEN_SEGMENT", "INDEPENDENT_NARRATION"],
            "task_count": len(runnable) + len(responsible_steps),
            "budget": dict(budget or {}),
            "would_create_jobs": False,
            "note": "只重跑受影响闭包；人工锁定镜头不会被批次操作改动，局部素材哈希不变。",
        }

    # ------------------------------------------------------------------ summary
    def overview(self, *, project_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            repo = self._repo(connection)
            repo.require_explainer_project(project_id)
            project = repo.project_row(project_id)
            video = repo.require_video_for_project(project_id)
            editions = repo.editions(str(video["id"]))
            runs = repo.list_where(
                "explainer_runs", {"project_id": project_id}, order_by="created_at", descending=True, limit=1
            )
            beats = repo.beats(str(video["id"]))
            issues: list[dict[str, Any]] = []
            for edition in editions:
                issues.extend(repo.open_issues_for_edition(str(edition["id"])))
            decisions = repo.active_decisions(
                subject_kind="EDITION",
                subject_revision_id=str(editions[0]["id"]) if editions else "",
            ) if editions else []
            input_payload: dict[str, Any] = {}
            if video.get("input_payload_json"):
                try:
                    raw_payload = video["input_payload_json"]
                    input_payload = json.loads(raw_payload) if isinstance(raw_payload, str) else dict(raw_payload)
                except Exception:
                    input_payload = {}
        return {
            "project_id": project_id,
            "video": {
                "id": video["id"],
                "title": video["title"],
                "topic": video["topic"],
                "content_kind": video["content_kind"],
                "status": video["status"],
                # The browser sent ``Number(overview.data?.video?.revision ?? 1)``
                # as its expected revision; without this field a video at
                # revision 3 always submitted a stale "1" and the server answered
                # 409.  The real revision is part of the projection now.
                "revision": int(video.get("revision") or 1),
                "target_seconds": video["target_seconds"],
                "duration_mode": video["duration_mode"],
                "tolerance_percent": video["tolerance_percent"],
                "automation_mode": video["automation_mode"],
                "inference_mode": video["inference_mode"],
                "research_mode": video["research_mode"],
                "channel_profile_version_id": video.get("current_channel_profile_version_id"),
                "input_kind": video.get("input_kind"),
                "source_locale": video.get("source_locale"),
                "aspect_ratio": project.get("aspect_ratio") or "16:9",
                "story_text": str(input_payload.get("story_text") or input_payload.get("pasted_text") or ""),
                "input_payload": input_payload,
            },
            "editions": editions,
            "beat_count": len(beats),
            "render_type_counts": _count(beats, "render_type"),
            "latest_run": self.get_run(run_id=str(runs[0]["id"])) if runs else None,
            "open_issues": issues,
            "open_issue_count": len(issues),
            "blocking_issue_count": sum(1 for item in issues if str(item["severity"]) == "BLOCKER"),
            "active_decisions": decisions,
            "authority_labels": {
                "machine": "自动检查结果",
                "human": "人工确认",
                "publication": "发布授权",
            },
            "capability_snapshot": self.capability_snapshot(project_id=project_id),
        }


def _count(items: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "UNKNOWN")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _explicit(source: Mapping[str, Any], key: str, default: Any) -> Any:
    """Default only for a *missing* or ``None`` field.

    ``source.get(key) or default`` cannot tell "the caller did not send this" from
    "the caller sent ``0``/``[]``", so an explicit zero budget or an empty fallback
    list was silently replaced by the product default and the frozen authorization
    snapshot grew without the caller asking for it.
    """

    if key not in source:
        return default
    value = source[key]
    if value is None:
        return default
    return value
