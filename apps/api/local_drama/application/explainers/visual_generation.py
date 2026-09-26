"""Thin explainer business adapter over the Model Platform V2 execution chain.

Design reference: 解说工厂-体验功能与代码改造实施规范 §D4 (§D4.1 plan/submit,
§D4.2 keyframe → video, §D4.3 the controlled media input bridge) and §D2.1
(candidate registration has exactly one entry point).

Why this module exists
----------------------
The explainer graph needs "generate N candidates for this beat / this entity" as a
first-class, auditable command.  Before this module the only entry point was a
long synchronous stage handler that wrote candidate rows directly, which meant no
budget accounting, no plan/execute split, no idempotent receipts and no way to
tell a technical retry from a fresh creative draw.

Three rules are enforced here, and they are the reason the code is shaped this
way:

* **The plan is read-only.**  ``plan()`` never creates media, candidates or jobs.
  It freezes the prompt, the resolved style, the reference set, the capability
  profile, the geometry and the seeds, and returns them with a ``plan_hash``.
  ``submit()`` re-computes the same plan and refuses to run when the caller's hash
  no longer matches, so a UI decision cannot silently drift between plan and
  execution.
* **One owner, resolved locally.**  ``owner_ref`` is either a beat or an entity.
  This is *not* a new Model Platform scope: both owners still submit with
  ``CapabilityScopeContext(project_id=…)``, and no fake episode/shot/beat is ever
  created to borrow another product's endpoint.
* **Reference images are business references, never paths.**  A reference enters
  the snapshot as ``{"media_version_id": …, "sha256": …}``; the worker's controlled
  bridge verifies and materialises it.  Nothing here writes a filesystem path or a
  URL into a job.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository, encode_row

__all__ = [
    "ExplainerVisualGenerationService",
    "ExplainerVisualOwner",
    "ExplainerVisualPlan",
    "MODE_CAPABILITY",
    "MODE_RENDER_TYPE",
    "OWNER_KINDS",
    "PURPOSES",
    "build_explainer_visual_generation_service",
    "resolve_explainer_style",
]


#: Which local object a candidate belongs to.  Exactly one of the two is set.
OWNER_KINDS = ("BEAT", "ENTITY")

#: Candidate layers.  REFERENCE = 定妆参考, KEYFRAME = beat 原图, VISUAL = 可合成片段.
PURPOSES = ("REFERENCE", "KEYFRAME", "VISUAL")

#: Generation modes and the real capability each one executes through.  The
#: deterministic ``STILL_MOTION`` mode is gone: a still image plus an FFmpeg camera
#: move is not a generation and must never be offered as one.
MODE_CAPABILITY: Mapping[str, str] = {
    "TEXT_TO_IMAGE": "IMAGE_CONCEPT",
    "IMAGE_EDIT": "IMAGE_EDIT",
    "IMAGE_TO_VIDEO": "VIDEO_I2V",
}

#: The mode's honest render type.  Image modes produce a *picture* (the keyframe /
#: reference a beat is built from), so they declare no render type at all; only a real
#: image-to-video execution may claim ``I2V``.
MODE_RENDER_TYPE: Mapping[str, str] = {
    "IMAGE_TO_VIDEO": "I2V",
}

#: Which modes may produce which purpose.  A reference cannot be a video, and a
#: final clip cannot be a bare text-to-image request: the only way to produce a
#: composable moving clip is a real image-to-video generation.
PURPOSE_MODES: Mapping[str, frozenset[str]] = {
    "REFERENCE": frozenset({"TEXT_TO_IMAGE", "IMAGE_EDIT"}),
    "KEYFRAME": frozenset({"TEXT_TO_IMAGE", "IMAGE_EDIT"}),
    "VISUAL": frozenset({"IMAGE_TO_VIDEO"}),
}

#: Stage codes whose job is a *collection* step: it writes nothing itself and only
#: gathers what the dispatched candidate jobs produced, so a blocked collection is
#: exactly the case "用现有结果继续" exists for (design §D5.1).
_COLLECTION_TASK_CODES = frozenset(
    {
        "IMAGE_COLLECT",
        "VIDEO_COLLECT",
        "IDENTITY_COLLECT",
        "VISUAL_CLIP_COLLECT",
        # The pre-dispatch graph names its single visual stage this way; it is a
        # collection stage in the sense that a failed candidate must not strand the
        # whole run when every required owner already has a usable result.
        "VISUAL_GENERATION",
    }
)


@dataclass(frozen=True, slots=True)
class ExplainerVisualOwner:
    """The local object a generation command targets."""

    kind: str
    id: str
    video_id: str
    project_id: str
    code: str
    revision: int
    record: Mapping[str, Any]


class ExplainerVisualGenerationError(DomainRuleError):
    """A generation command was refused with a specific, user-explainable reason."""

    def __init__(self, code: str, message: str, detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(code, message, dict(detail or {}))
        self.code = code
        self.message = message
        self.detail = dict(detail or {})


@dataclass(frozen=True, slots=True)
class ExplainerVisualPlan:
    """A frozen, read-only description of exactly what ``submit`` will do."""

    status: str
    owner: ExplainerVisualOwner
    purpose: str
    mode: str
    candidate_count: int
    plan_hash: str
    candidate_seeds: tuple[int, ...]
    capability_code: str
    execution_profile_version_id: str | None
    #: The Profile the *client* pinned on the RUN scope, if any — distinct from the
    #: Profile that resolution happened to select.  Submit must replay the same
    #: request: pinning an already-resolved Profile turns a SYSTEM-scope resolution
    #: into an ``EXPLICIT_RUN_PROFILE`` one, which changes ``assignment_chain`` and
    #: therefore the preview's ``resolution_hash``.  Passing the resolved id back at
    #: submit time made every submit answer ``MP_EXECUTION_RESOLUTION_STALE`` against
    #: the plan it had just produced (measured: plan hash ``45439dce…`` vs submit
    #: hash ``9cc5fe55…`` for the same Profile, same semantic inputs, same seed).
    requested_execution_profile_version_id: str | None
    profile_title: str | None
    resolved_parameters: Mapping[str, Any]
    expected_resolution_hash: str | None
    #: Per-candidate execution-preview hash, keyed by the frozen seed.  A candidate's run
    #: overrides carry its own seed, so one plan-level hash could never match the
    #: submit-time resolution for every seed (measured: ``MP_EXECUTION_RESOLUTION_STALE``
    #: on a submit whose read-only plan had just passed).  Submit passes the hash of the
    #: seed it is about to use, which keeps the "operator confirmed this preflight" guard
    #: intact instead of relaxing it.
    resolution_hash_by_seed: Mapping[str, str]
    #: The exact semantic inputs each frozen candidate will submit, keyed by seed.
    #: They carry that candidate's SEED (a required slot of every published image
    #: workflow) and are already narrowed to the slots the resolved Profile's
    #: workflow declares, so submit replays them verbatim instead of re-deriving
    #: a shape the worker would refuse.
    semantic_inputs_by_seed: Mapping[str, Mapping[str, Any]]
    prompt: str
    negative_prompt: str
    prompt_bundle: Mapping[str, Any]
    reference_capacity: Mapping[str, Any]
    resolved_references: tuple[Mapping[str, Any], ...]
    #: The render type a real execution of this plan produces.  An image mode
    #: (reference / keyframe) declares ``None``: a still picture is not a render type.
    render_type_planned: str | None
    media_kind: str
    planned_duration_ms: int | None
    allowed_durations_ms: tuple[int, ...] | None
    budget: Mapping[str, Any] | None
    blockers: tuple[Mapping[str, Any], ...]
    frozen_inputs: Mapping[str, Any]

    def as_response(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "owner_kind": self.owner.kind,
            "owner_id": self.owner.id,
            "purpose": self.purpose,
            "mode": self.mode,
            "candidate_count": self.candidate_count,
            "plan_hash": self.plan_hash,
            "candidate_seeds": list(self.candidate_seeds),
            "execution_profile_version_id": self.execution_profile_version_id,
            "requested_execution_profile_version_id": self.requested_execution_profile_version_id,
            "profile_title": self.profile_title,
            "expected_resolution_hash": self.expected_resolution_hash,
            "resolution_hash_by_seed": dict(self.resolution_hash_by_seed),
            "semantic_inputs_by_seed": {
                str(seed): dict(values) for seed, values in self.semantic_inputs_by_seed.items()
            },
            "media_kind": self.media_kind,
            "render_type_planned": self.render_type_planned,
            "render_type_actual": None,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "reference_capacity": dict(self.reference_capacity),
            "resolved_references": [dict(item) for item in self.resolved_references],
            "frozen_inputs": dict(self.frozen_inputs),
            "blockers": [dict(item) for item in self.blockers],
            "budget": dict(self.budget) if self.budget else None,
            "planned_duration_ms": self.planned_duration_ms,
            "allowed_durations_ms": list(self.allowed_durations_ms) if self.allowed_durations_ms else None,
            "profile_parameters": dict(self.resolved_parameters),
            "is_read_only_plan": True,
            "creates_jobs_on_plan": False,
        }


class ExplainerVisualGenerationService:
    """Plan and submit one explainer candidate batch."""

    def __init__(
        self,
        repo: ExplainerRepository,
        *,
        database: Any,
        settings: Any,
        capability_service: Any = None,
        submission_service_factory: Callable[[], Any] | None = None,
        picture_runtime: Any = None,
    ) -> None:
        self.repo = repo
        self.database = database
        self.settings = settings
        self._capability_service = capability_service
        self._submission_factory = submission_service_factory
        self._picture_runtime = picture_runtime

    # ------------------------------------------------------------------ owners
    def resolve_owner(self, project_id: str, owner_ref: Mapping[str, Any]) -> ExplainerVisualOwner:
        """Validate a ``{kind, id}`` reference without creating a platform scope."""

        kind = str((owner_ref or {}).get("kind") or "").strip().upper()
        owner_id = str((owner_ref or {}).get("id") or "").strip()
        if kind not in OWNER_KINDS or not owner_id:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_OWNER_INVALID",
                "生成命令必须声明 beat 或 entity 之一作为归属对象。",
                {"owner_ref": dict(owner_ref or {})},
            )
        video = self.repo.require_video_for_project(project_id)
        if kind == "BEAT":
            beat = self.repo.find("explainer_visual_beats", owner_id)
            if beat is None or str(beat.get("video_id")) != str(video["id"]):
                raise ExplainerVisualGenerationError(
                    "EXPLAINER_BEAT_NOT_FOUND",
                    "该画面段不属于当前解说作品。",
                    {"beat_id": owner_id},
                )
            return ExplainerVisualOwner(
                kind="BEAT",
                id=owner_id,
                video_id=str(video["id"]),
                project_id=project_id,
                code=str(beat.get("code") or ""),
                revision=int(beat.get("revision") or 0),
                record=beat,
            )
        entity = self.repo.find("explainer_entities", owner_id)
        if entity is None or str(entity.get("video_id")) != str(video["id"]):
            raise ExplainerVisualGenerationError(
                "EXPLAINER_ENTITY_NOT_FOUND",
                "该人物/场景/道具不属于当前解说作品。",
                {"entity_id": owner_id},
            )
        return ExplainerVisualOwner(
            kind="ENTITY",
            id=owner_id,
            video_id=str(video["id"]),
            project_id=project_id,
            code=str(entity.get("code") or ""),
            revision=int(entity.get("revision") or 0),
            record=entity,
        )

    # ------------------------------------------------------------------ plan
    def plan(self, project_id: str, owner_ref: Mapping[str, Any], command: Mapping[str, Any]) -> ExplainerVisualPlan:
        owner = self.resolve_owner(project_id, owner_ref)
        purpose = str(command.get("purpose") or ("REFERENCE" if owner.kind == "ENTITY" else "VISUAL")).upper()
        if purpose not in PURPOSES:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_PURPOSE_INVALID", "候选用途不在允许范围内。", {"purpose": purpose, "allowed": list(PURPOSES)}
            )
        if owner.kind == "ENTITY" and purpose != "REFERENCE":
            raise ExplainerVisualGenerationError(
                "EXPLAINER_PURPOSE_OWNER_MISMATCH",
                "人物/场景/道具只能产生定妆参考候选，画面候选必须挂在画面段上。",
                {"owner_kind": owner.kind, "purpose": purpose},
            )
        if owner.kind == "BEAT" and purpose == "REFERENCE":
            raise ExplainerVisualGenerationError(
                "EXPLAINER_PURPOSE_OWNER_MISMATCH",
                "定妆参考候选必须挂在人物/场景/道具上，不能挂在画面段上。",
                {"owner_kind": owner.kind, "purpose": purpose},
            )

        mode = str(command.get("mode") or ("TEXT_TO_IMAGE" if purpose != "VISUAL" else "IMAGE_TO_VIDEO")).upper()
        if mode not in PURPOSE_MODES[purpose]:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_MODE_INVALID",
                "该候选用途不支持所选生成方式。",
                {"purpose": purpose, "mode": mode, "allowed": sorted(PURPOSE_MODES[purpose])},
            )

        self._assert_expected_revision(owner, purpose, command)

        candidate_count = self._candidate_count(mode, command.get("candidate_count"))
        references = self._resolve_references(owner, command)
        prompt_bundle = self._compile_prompt(owner, purpose, command, references)
        seeds = self._candidate_seeds(owner, purpose, mode, candidate_count, command)
        duration = self._planned_duration(owner, purpose)

        blockers: list[dict[str, Any]] = []
        capability_code = MODE_CAPABILITY.get(mode)
        profile_version_id: str | None = None
        profile_title: str | None = None
        resolved_parameters: dict[str, Any] = {}
        resolution_hash: str | None = None
        resolution_hash_by_seed: dict[str, str] = {}
        semantic_inputs_by_seed: dict[str, dict[str, Any]] = {}

        explicit_profile = str(command.get("execution_profile_version_id") or "").strip() or None
        if capability_code is None:
            blockers.append(
                {
                    "code": "EXPLAINER_MODE_UNSUPPORTED",
                    "message": "该生成方式没有已声明的执行能力。",
                    "retryable": False,
                }
            )
        else:
            base_overrides = dict(command.get("run_overrides") or {})
            # The Profile that will run the request declares which semantic slots
            # its workflow accepts and which are required, so the inputs are
            # shaped for that workflow instead of guessed.  A probe preview first
            # resolves the Profile (resolution does not depend on the inputs).
            probe = self._semantic_inputs(mode, prompt_bundle, references, seeds[0], duration)
            preview = self._preview(
                capability_code=capability_code,
                project_id=project_id,
                semantic_inputs=probe,
                run_overrides=base_overrides,
                execution_profile_version_id=explicit_profile,
            )
            profile_version_id = preview.get("execution_profile_version_id")
            profile_title = preview.get("profile_title")
            resolved_parameters = dict(preview.get("resolved_parameters") or {})
            declared = self._declared_semantic_slots(profile_version_id)
            missing_required: list[str] = []
            # One preview per frozen candidate: SEED is a *required* semantic
            # input of every published image workflow, so the candidates differ in
            # their semantic inputs and each one needs its own resolution hash for
            # the submit-time freshness guard.  A single batch preview used to be
            # computed without any seed at all, which matched no submit.
            resolution_hash = None
            for candidate_seed in seeds:
                candidate_inputs = self._semantic_inputs(mode, prompt_bundle, references, candidate_seed, duration)
                if declared is not None:
                    slots, required = declared
                    missing_required = sorted(name for name in required if not _bounded_scalar_input(candidate_inputs.get(name)))
                    # Optional slots the command left empty are omitted rather than
                    # sent as "" — the worker's contract accepts only non-empty
                    # bounded scalars, so an empty negative prompt is refused
                    # (measured MP_COMFY_EXECUTION_INPUT_INVALID), and the
                    # workflow's own authoring default already covers the slot.
                    candidate_inputs = {
                        key: value
                        for key, value in candidate_inputs.items()
                        if key in slots and (key in required or _bounded_scalar_input(value))
                    }
                semantic_inputs_by_seed[str(candidate_seed)] = candidate_inputs
                seeded = self._preview(
                    capability_code=capability_code,
                    project_id=project_id,
                    semantic_inputs=candidate_inputs,
                    run_overrides=base_overrides,
                    execution_profile_version_id=explicit_profile,
                )
                seeded_hash = seeded.get("resolution_hash")
                if seeded_hash:
                    resolution_hash_by_seed[str(candidate_seed)] = str(seeded_hash)
                    resolution_hash = seeded_hash if resolution_hash is None else resolution_hash
            if missing_required:
                # The Profile's workflow cannot be fed by this command.  Failing
                # at plan time names the exact slots instead of queueing a job
                # that the worker must refuse.
                blockers.append(
                    {
                        "code": "EXPLAINER_WORKFLOW_INPUT_UNRESOLVED",
                        "message": "当前 Profile 的工作流需要本命令没有提供的输入，无法生成。",
                        "retryable": False,
                        "details": {"missing_semantic_inputs": missing_required},
                    }
                )
            for blocker in preview.get("blockers") or ():
                blockers.append(dict(blocker))

        if mode == "IMAGE_EDIT" and not references:
            blockers.append(
                {
                    "code": "EXPLAINER_REFERENCE_REQUIRED",
                    "message": "按参考重绘必须至少采用一张人物或场景参考图。",
                    "retryable": False,
                }
            )
        if mode == "IMAGE_TO_VIDEO" and not str(command.get("input_keyframe_selection_id") or "").strip():
            blockers.append(
                {
                    "code": "EXPLAINER_KEYFRAME_REQUIRED",
                    "message": "AI 动态片段必须先采用一张首帧图片。",
                    "retryable": False,
                }
            )
        # §C4.4 blocks a *conflicting* requirement set.  A film with no published channel
        # profile is not a conflict: the compiler already substituted its own versioned
        # default style (see ``_compile_prompt``), and the default is an explicitly
        # supported choice in §B3.2.  Treating the substitution as unresolved made every
        # text-to-image plan BLOCKED on a workspace without a channel profile, which is
        # the state of a fresh install — measured with the real ComfyUI runtime: plan
        # status=BLOCKED, code EXPLAINER_PROMPT_UNRESOLVED, message "已使用…默认风格描述"
        # followed by a refusal to generate.
        conflicts = [
            item
            for item in (prompt_bundle.get("unresolved_constraints") or [])
            if str((item or {}).get("kind") or "") != "STYLE_MISSING"
        ]
        if conflicts:
            blockers.append(
                {
                    "code": "EXPLAINER_PROMPT_UNRESOLVED",
                    "message": "画面设定存在相互冲突的要求，请先解决后再生成。",
                    "retryable": False,
                    "details": {"unresolved_constraints": conflicts},
                }
            )

        frozen_inputs = {
            "owner_kind": owner.kind,
            "owner_id": owner.id,
            "owner_revision": owner.revision,
            "purpose": purpose,
            "mode": mode,
            "prompt_hash": _hash_text(json.dumps(prompt_bundle, ensure_ascii=False, sort_keys=True)),
            "style_hash": prompt_bundle.get("style_hash"),
            "reference_hash": _hash_text(
                json.dumps([item.get("media_version_id") for item in references], ensure_ascii=False)
            ),
            "capability_code": capability_code,
            "execution_profile_version_id": profile_version_id,
            "requested_execution_profile_version_id": explicit_profile,
            "expected_resolution_hash": resolution_hash,
            "semantic_inputs_by_seed": {str(seed): dict(values) for seed, values in semantic_inputs_by_seed.items()},
            "candidate_seeds": list(seeds),
            "candidate_count": candidate_count,
            "geometry": prompt_bundle.get("geometry"),
            "duration_ms": duration,
        }
        # The hash must be reproducible for the same frozen input: the client echoes
        # it back on submit, so anything time-dependent here would make every plan
        # look stale.  ``generated_at`` is therefore reported separately, not hashed.
        plan_hash = _hash_text(json.dumps(frozen_inputs, ensure_ascii=False, sort_keys=True))
        frozen_inputs = {**frozen_inputs, "generated_at": _now_stamp()}

        return ExplainerVisualPlan(
            status="BLOCKED" if blockers else "EXECUTABLE",
            owner=owner,
            purpose=purpose,
            mode=mode,
            candidate_count=candidate_count,
            plan_hash=plan_hash,
            candidate_seeds=tuple(seeds),
            capability_code=capability_code or MODE_CAPABILITY["IMAGE_TO_VIDEO"],
            execution_profile_version_id=profile_version_id,
            requested_execution_profile_version_id=explicit_profile,
            profile_title=profile_title,
            resolved_parameters=resolved_parameters,
            expected_resolution_hash=resolution_hash,
            resolution_hash_by_seed=dict(resolution_hash_by_seed),
            semantic_inputs_by_seed={
                str(seed): dict(values) for seed, values in semantic_inputs_by_seed.items()
            },
            prompt=str(prompt_bundle.get("prompt") or ""),
            negative_prompt=str(prompt_bundle.get("negative_prompt") or ""),
            prompt_bundle=prompt_bundle,
            reference_capacity={
                "max_image_references": prompt_bundle.get("max_image_references"),
                "resolved_image_references": len(references),
            },
            resolved_references=tuple(references),
            render_type_planned=MODE_RENDER_TYPE.get(mode),
            media_kind="VIDEO" if purpose == "VISUAL" and mode == "IMAGE_TO_VIDEO" else "IMAGE",
            planned_duration_ms=duration,
            allowed_durations_ms=prompt_bundle.get("allowed_durations_ms"),
            budget=self._budget(),
            blockers=tuple(blockers),
            frozen_inputs=frozen_inputs,
        )

    # ------------------------------------------------------------------ submit
    def submit(
        self,
        project_id: str,
        owner_ref: Mapping[str, Any],
        command: Mapping[str, Any],
        *,
        idempotency_key: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        """Reserve candidates and submit one real Job per candidate.

        The receipt is intentionally per-item: a batch can be
        ``PARTIALLY_ACCEPTED``, and the accepted items carry the real Job IDs.
        Reporting the whole batch as accepted when only one submission succeeded is
        exactly the failure mode the design forbids.
        """

        if not str(idempotency_key or "").strip():
            raise ExplainerVisualGenerationError(
                "EXPLAINER_IDEMPOTENCY_KEY_REQUIRED", "提交生成任务必须携带 Idempotency-Key。"
            )
        plan = self.plan(project_id, owner_ref, command)
        if plan.status != "EXECUTABLE":
            return {
                "operation_id": str(command.get("operation_id") or ""),
                "status": "REJECTED",
                "requested_count": plan.candidate_count,
                "accepted_count": 0,
                "items": [],
                "blockers": [dict(item) for item in plan.blockers],
                "plan_hash": plan.plan_hash,
                "idempotent_replay": False,
            }

        expected_plan_hash = str(command.get("expected_plan_hash") or "").strip()
        if expected_plan_hash and expected_plan_hash != plan.plan_hash:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_STALE_PLAN",
                "生成条件已经变化，请重新检查生成条件后再提交。",
                {"expected_plan_hash": expected_plan_hash, "plan_hash": plan.plan_hash},
            )
        return self._submit_model_execution(plan, idempotency_key=idempotency_key, actor=actor, command=command)

    # ------------------------------------------------------------------ internals
    def _submit_model_execution(
        self,
        plan: ExplainerVisualPlan,
        *,
        idempotency_key: str,
        actor: str,
        command: Mapping[str, Any],
    ) -> dict[str, Any]:
        operation_id = str(command.get("operation_id") or "").strip() or uuid.uuid4().hex
        existing = self._existing_operation(plan, operation_id)
        if existing:
            # Same operation replayed: extend the missing ordinals instead of
            # creating a second batch with new seeds.
            return self._extend_operation(plan, existing, operation_id, command=command)

        submission_service = self._submission_service()
        items: list[dict[str, Any]] = []
        accepted = 0
        for ordinal, seed in enumerate(plan.candidate_seeds):
            candidate_id = str(uuid.uuid4())
            job_key = f"{idempotency_key}:{operation_id}:{ordinal}"
            # The frozen per-candidate inputs, not a re-derivation: they already
            # carry this seed and the slot set the resolved workflow declares.
            semantic_inputs = self._frozen_semantic_inputs(plan, seed)
            overrides = dict(command.get("run_overrides") or {})
            try:
                submission = submission_service.submit(
                    self._preview_request(
                        capability_code=plan.capability_code,
                        project_id=plan.owner.project_id,
                        semantic_inputs=semantic_inputs,
                        run_overrides=overrides,
                        # Replay the RUN-scope Profile the *plan* was asked for, not the
                        # one resolution returned: pinning the resolved id would move the
                        # assignment chain from SYSTEM to EXPLICIT_RUN_PROFILE and change
                        # the preview hash the plan just handed back.
                        execution_profile_version_id=plan.requested_execution_profile_version_id,
                        # The plan computed this hash with the same seed in the run
                        # overrides, so the freshness guard compares like with like.
                        expected_resolution_hash=(
                            plan.resolution_hash_by_seed.get(str(seed)) or plan.expected_resolution_hash
                        ),
                    ),
                    job_key,
                    after_linked_in_transaction=self._candidate_hook(
                        plan=plan,
                        candidate_id=candidate_id,
                        ordinal=ordinal,
                        seed=seed,
                        operation_id=operation_id,
                        actor=actor,
                        job_key=job_key,
                    ),
                )
            except DomainRuleError as error:
                items.append(
                    {
                        "ordinal": ordinal,
                        "submission_status": "NOT_SUBMITTED",
                        "candidate_id": None,
                        "candidate_status": None,
                        "job_id": None,
                        "job_state": None,
                        "seed": seed,
                        "error": {"code": getattr(error, "code", "EXPLAINER_SUBMIT_FAILED"), "message": str(error), "retryable": True},
                    }
                )
                continue
            accepted += 1
            items.append(
                {
                    "ordinal": ordinal,
                    "submission_status": "ACCEPTED",
                    "candidate_id": candidate_id,
                    "candidate_status": "PENDING",
                    "job_id": str(submission.job.get("id")),
                    "job_state": str(submission.job.get("status") or "QUEUED"),
                    "seed": seed,
                    "error": None,
                }
            )

        status = "ACCEPTED" if accepted == plan.candidate_count else ("PARTIALLY_ACCEPTED" if accepted else "REJECTED")
        return {
            "operation_id": operation_id,
            "status": status,
            "requested_count": plan.candidate_count,
            "accepted_count": accepted,
            "items": items,
            "plan_hash": plan.plan_hash,
            "idempotent_replay": False,
        }

    def _candidate_hook(
        self,
        *,
        plan: ExplainerVisualPlan,
        candidate_id: str,
        ordinal: int,
        seed: int,
        operation_id: str,
        actor: str,
        job_key: str,
    ) -> Callable[[Any, Mapping[str, Any], Any], None]:
        def hook(connection: Any, job: Mapping[str, Any], snapshot: Any) -> None:
            payload = {
                "id": candidate_id,
                "video_id": plan.owner.video_id,
                "beat_id": plan.owner.id if plan.owner.kind == "BEAT" else None,
                "entity_id": plan.owner.id if plan.owner.kind == "ENTITY" else None,
                "edition_id": plan.frozen_inputs.get("edition_id"),
                "variant_no": self._next_variant_no(plan),
                "candidate_kind": "CREATIVE",
                "purpose": plan.purpose,
                "status": "PENDING",
                "job_id": str(job.get("id")),
                "render_type_planned": plan.render_type_planned,
                "render_type_actual": None,
                "execution_snapshot_json": {
                    "execution_snapshot_id": getattr(snapshot, "id", None),
                    "execution_snapshot_hash": getattr(snapshot, "content_hash", None),
                    "plan_hash": plan.plan_hash,
                    "mode": plan.mode,
                    "prompt_bundle": dict(plan.prompt_bundle),
                    "reference_media_version_ids": [
                        item.get("media_version_id") for item in plan.resolved_references
                    ],
                },
                "lineage_json": {
                    "operation_id": operation_id,
                    "ordinal": ordinal,
                    "seed": seed,
                    "parent_candidate_id": plan.frozen_inputs.get("parent_candidate_id"),
                    "idempotency_key": job_key,
                    "actor": actor,
                    "generator": "explainer_visual_generation",
                },
                "qc_summary_json": {},
                "adopted": False,
            }
            encoded = encode_row("explainer_media_candidates", payload)
            columns = ", ".join(encoded)
            placeholders = ", ".join("?" for _ in encoded)
            connection.execute(
                f"INSERT INTO explainer_media_candidates ({columns}) VALUES ({placeholders})",
                tuple(encoded.values()),
            )

        return hook

    def _existing_operation(self, plan: ExplainerVisualPlan, operation_id: str) -> list[dict[str, Any]]:
        rows = self.repo.list_where(
            "explainer_media_candidates",
            {"video_id": plan.owner.video_id},
            order_by="created_at",
            descending=False,
        )
        return [
            row
            for row in rows
            if isinstance(row.get("lineage_json"), Mapping)
            and str(row["lineage_json"].get("operation_id") or "") == operation_id
        ]

    def _extend_operation(
        self, plan: ExplainerVisualPlan, existing: Sequence[Mapping[str, Any]], operation_id: str, *, command: Mapping[str, Any]
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        accepted = 0
        submission_service = self._submission_service()
        for ordinal, seed in enumerate(plan.candidate_seeds):
            row = next((item for item in existing if int(item["lineage_json"].get("ordinal") or 0) == ordinal), None)
            if row is not None:
                items.append(
                    {
                        "ordinal": ordinal,
                        "submission_status": "REPLAYED",
                        "candidate_id": str(row["id"]),
                        "candidate_status": str(row.get("status") or "PENDING"),
                        "job_id": str(row.get("job_id") or "") or None,
                        "job_state": None,
                        "seed": seed,
                        "error": None,
                    }
                )
                accepted += 1
                continue
            candidate_id = str(uuid.uuid4())
            job_key = f"explainer:{plan.owner.project_id}:{operation_id}:{ordinal}"
            semantic_inputs = self._frozen_semantic_inputs(plan, seed)
            overrides = dict(command.get("run_overrides") or {})
            try:
                submission = submission_service.submit(
                    self._preview_request(
                        capability_code=plan.capability_code,
                        project_id=plan.owner.project_id,
                        semantic_inputs=semantic_inputs,
                        run_overrides=overrides,
                        execution_profile_version_id=plan.requested_execution_profile_version_id,
                        # The plan computed this hash with the same seed in the run
                        # overrides, so the freshness guard compares like with like.
                        expected_resolution_hash=(
                            plan.resolution_hash_by_seed.get(str(seed)) or plan.expected_resolution_hash
                        ),
                    ),
                    job_key,
                    after_linked_in_transaction=self._candidate_hook(
                        plan=plan,
                        candidate_id=candidate_id,
                        ordinal=ordinal,
                        seed=seed,
                        operation_id=operation_id,
                        actor="local-user",
                        job_key=job_key,
                    ),
                )
            except DomainRuleError as error:
                items.append(
                    {
                        "ordinal": ordinal,
                        "submission_status": "NOT_SUBMITTED",
                        "candidate_id": None,
                        "candidate_status": None,
                        "job_id": None,
                        "job_state": None,
                        "seed": seed,
                        "error": {"code": getattr(error, "code", "EXPLAINER_SUBMIT_FAILED"), "message": str(error), "retryable": True},
                    }
                )
                continue
            accepted += 1
            items.append(
                {
                    "ordinal": ordinal,
                    "submission_status": "ACCEPTED",
                    "candidate_id": candidate_id,
                    "candidate_status": "PENDING",
                    "job_id": str(submission.job.get("id")),
                    "job_state": str(submission.job.get("status") or "QUEUED"),
                    "seed": seed,
                    "error": None,
                }
            )
        status = "ACCEPTED" if accepted == plan.candidate_count else ("PARTIALLY_ACCEPTED" if accepted else "REJECTED")
        return {
            "operation_id": operation_id,
            "status": status,
            "requested_count": plan.candidate_count,
            "accepted_count": accepted,
            "items": items,
            "plan_hash": plan.plan_hash,
            "idempotent_replay": True,
        }

    # ------------------------------------------------------------------ collection recovery
    def continue_collection_with_available(
        self,
        *,
        run_id: str,
        step_binding_id: str,
        selected_candidate_ids: Sequence[str],
        failed_candidate_ids: Sequence[str] = (),
        expected_task_revision: int | None = None,
        expected_old_job_id: str | None = None,
        actor: str = "local-user",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        """Replace a blocked collect job for the *same* workflow task.

        This is the fixed plan of design §D5.1 and deliberately not a new queue:

        1. the receipt is persisted first, so a crash between steps is replayable;
        2. the old collect job is cancelled through ``JobService.cancel`` (it has no
           active attempt when it is blocked on a failed dependency, so this is a
           real cancellation, not a pre-emption);
        3. one transaction re-verifies the task pointer, creates the replacement CPU
           job with only the genuinely successful dependencies, and swaps
           ``automation_workflow_run_tasks.job_id`` under a compare-and-set so two
           tabs cannot both replace it;
        4. the step projection is reset to ``PENDING`` and only this technical
           blocker is cleared.

        Failure and success are both reported truthfully: the original failed jobs and
        candidates stay as they are, and nothing is marked SUCCEEDED by hand.
        """

        run = self.repo.find("explainer_runs", run_id)
        if run is None:
            raise ExplainerVisualGenerationError("EXPLAINER_RUN_NOT_FOUND", "该制作运行不存在。", {"run_id": run_id})
        if str(run.get("status") or "").upper() in {"CANCELLED", "PAUSED", "PAUSING"}:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_RUN_NOT_CONTINUABLE",
                "本次运行已暂停或取消，不能自动继续；请先恢复运行。",
                {"run_id": run_id, "status": str(run.get("status") or "")},
            )
        step = self.repo.find("explainer_step_bindings", step_binding_id)
        if step is None or str(step.get("run_id")) != run_id:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_STEP_NOT_FOUND",
                "该制作步骤不属于本次运行。",
                {"run_id": run_id, "step_binding_id": step_binding_id},
            )
        task_code = str(step.get("planned_step_code") or "")
        if task_code not in _COLLECTION_TASK_CODES:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_NOT_A_COLLECTION",
                "只有图像/视频/身份收集阶段可以用现有结果继续。",
                {"task_code": task_code, "allowed": sorted(_COLLECTION_TASK_CODES)},
            )

        workflow_run_id = str(run.get("automation_workflow_run_id") or "")
        if not workflow_run_id:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_WORKFLOW_NOT_LINKED",
                "本次运行还没有关联的工作流，无法替换收集任务。",
                {"run_id": run_id},
            )

        # Replay first.  A completed replacement moves the step's pointer to the new
        # job, so looking the task up by the step's *current* job would only find the
        # replacement — and re-validating it would then look like a stale request.
        # The persisted receipt is the record of what this operation already did.
        operation_id = idempotency_key or f"explainer-collect:{step_binding_id}"
        scope = f"explainer:collection-continue:{step_binding_id}"
        existing = self._read_receipt(scope, operation_id)
        if existing is not None:
            if str(existing.get("status")) == "REPLACED" and existing.get("replacement_job_id"):
                return {**existing, "idempotent_replay": True}
            # A receipt that never reached REPLACED means the previous attempt died
            # between persisting and swapping; continue the same operation.
            expected_old_job_id = str(existing.get("previous_job_id") or expected_old_job_id or "")

        with self.database.connect() as connection:
            task = connection.execute(
                """SELECT id, job_id, status, revision, ordinal, item_key, item_json, machine_context_json
                     FROM automation_workflow_run_tasks
                    WHERE run_id = ? AND job_id = ?
                    ORDER BY ordinal DESC LIMIT 1""",
                (workflow_run_id, str(step.get("job_id") or "")),
            ).fetchone()
        if task is None:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_TASK_NOT_FOUND",
                "没有找到该步骤对应的工作流任务。",
                {"run_id": run_id, "step_binding_id": step_binding_id},
            )
        task_revision = int(task["revision"] or 0)
        if expected_task_revision is not None and int(expected_task_revision) != task_revision:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_STALE_TASK",
                "该任务已被其他操作更新，请刷新后重试。",
                {"expected_task_revision": int(expected_task_revision), "actual_task_revision": task_revision},
            )
        old_job_id = str(task["job_id"] or "")
        if expected_old_job_id is not None and str(expected_old_job_id) != old_job_id:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_STALE_TASK",
                "该任务的当前作业已变化，请刷新后重试。",
                {"expected_old_job_id": str(expected_old_job_id), "actual_old_job_id": old_job_id},
            )
        if not old_job_id:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_TASK_NOT_BLOCKED", "该任务当前没有阻塞中的作业，无需替换。"
            )

        selected = [str(item) for item in selected_candidate_ids]
        failed = [str(item) for item in failed_candidate_ids]
        missing = self._owners_without_candidate(task_code, selected)
        if missing:
            # A required owner with no usable candidate must stop here: a partially
            # successful batch cannot be reported as "ready to continue".
            raise ExplainerVisualGenerationError(
                "EXPLAINER_REQUIRED_OWNER_MISSING",
                "仍有关键对象没有任何可用候选，不能继续；请先补齐这些项。",
                {"missing_owner_ids": missing[:20], "missing_count": len(missing)},
            )

        receipt = {
            "run_id": run_id,
            "step_binding_id": step_binding_id,
            "task_code": task_code,
            "status": "PENDING_REPLACEMENT",
            "previous_job_id": old_job_id,
            "replacement_job_id": None,
            "selected_candidate_ids": selected,
            "failed_candidate_ids": failed,
            "reason": None,
            "actor": actor,
            "machine_policy_applied": actor == "machine-policy",
            "idempotent_replay": False,
        }
        collection_hash = _hash_text(
            json.dumps(
                {
                    "step_binding_id": step_binding_id,
                    "selected": sorted(selected),
                    "failed": sorted(failed),
                    "operation_id": operation_id,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        self._write_receipt(scope, operation_id, collection_hash, receipt)

        replacement_job_id = self._replace_collect_job(
            workflow_run_id=workflow_run_id,
            task_id=str(task["id"]),
            old_job_id=old_job_id,
            task_revision=task_revision,
            step_binding_id=step_binding_id,
            step=step,
            collection_hash=collection_hash,
            operation_id=operation_id,
            selected=selected,
        )
        receipt = {**receipt, "status": "REPLACED", "replacement_job_id": replacement_job_id}
        self._write_receipt(scope, operation_id, collection_hash, receipt)
        return receipt

    def _read_receipt(self, scope: str, key: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT response_json FROM command_idempotencies WHERE scope = ? AND idempotency_key = ?",
                (scope, key),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row["response_json"]))
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def _write_receipt(self, scope: str, key: str, payload_hash: str, receipt: Mapping[str, Any]) -> None:
        """Persist a recoverable receipt before and after the swap (design §D5.1 step 1)."""

        body = json.dumps(receipt, ensure_ascii=False)
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO command_idempotencies (scope, idempotency_key, payload_hash, response_json) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(scope, idempotency_key) "
                "DO UPDATE SET response_json = excluded.response_json",
                (scope, key, payload_hash, body),
            )

    def _owners_without_candidate(self, task_code: str, selected: Sequence[str]) -> list[str]:
        """Which required owners still have no valid candidate or adopted choice."""

        owners: list[str] = []
        if task_code in {"IMAGE_COLLECT", "VISUAL_GENERATION"}:
            for beat in self.repo.list_where("explainer_visual_beats", {}):
                beat_id = str(beat["id"])
                if any(
                    str(row.get("id")) in selected and str(row.get("status")) == "READY"
                    for row in self.repo.media_candidates(beat_id=beat_id, purpose="KEYFRAME")
                ):
                    continue
                if self.repo.active_beat_selection(beat_id, purpose="KEYFRAME"):
                    continue
                owners.append(beat_id)
        elif task_code in {"VIDEO_COLLECT", "VISUAL_CLIP_COLLECT"}:
            for beat in self.repo.list_where("explainer_visual_beats", {}):
                beat_id = str(beat["id"])
                if self.repo.active_beat_selection(beat_id, purpose="VISUAL"):
                    continue
                if any(
                    str(row.get("id")) in selected and str(row.get("status")) == "READY"
                    for row in self.repo.media_candidates(beat_id=beat_id, purpose="VISUAL")
                ):
                    continue
                owners.append(beat_id)
        elif task_code == "IDENTITY_COLLECT":
            for entity in self.repo.list_where("explainer_entities", {}):
                entity_id = str(entity["id"])
                if any(
                    str(row.get("id")) in selected and str(row.get("status")) == "READY"
                    for row in self.repo.media_candidates(entity_id=entity_id, purpose="REFERENCE")
                ):
                    continue
                owners.append(entity_id)
        return owners

    def _replace_collect_job(
        self,
        *,
        workflow_run_id: str,
        task_id: str,
        old_job_id: str,
        task_revision: int,
        step_binding_id: str,
        step: Mapping[str, Any],
        collection_hash: str,
        operation_id: str,
        selected: Sequence[str],
    ) -> str | None:
        """Cancel the stale collect and swap the task pointer in one transaction.

        ``JobService.create_job_in_transaction`` is reused so the replacement job
        carries the same real dependency semantics as any other job; the task pointer
        is then swapped with a compare-and-set, which is what makes a concurrent
        double-click harmless.
        """

        jobs = build_explainer_job_service(self.database)
        try:
            jobs.cancel(old_job_id, actor="local-user")
        except Exception as error:  # a collect blocked on a dependency has no active attempt
            raise ExplainerVisualGenerationError(
                "EXPLAINER_COLLECT_CANCEL_FAILED",
                "无法终结旧的收集任务，请稍后重试。",
                {"old_job_id": old_job_id, "error": type(error).__name__},
            ) from error

        with self.database.connect() as connection:
            old_job = connection.execute("SELECT * FROM jobs WHERE id = ?", (old_job_id,)).fetchone()
        if old_job is None:
            raise ExplainerVisualGenerationError("EXPLAINER_JOB_NOT_FOUND", "旧的收集作业已不存在。")

        # ``jobs.input_snapshot_json`` is a JSON text column, so it comes back as a
        # string from the raw connection and must be decoded before it can be extended.
        snapshot = _as_mapping(old_job["input_snapshot_json"])
        snapshot["collection_hash"] = collection_hash
        snapshot["replacement_of_job_id"] = old_job_id
        snapshot["selected_candidate_ids"] = list(selected)

        new_job_id: str | None = None
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT job_id, revision FROM automation_workflow_run_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if current is None:
                raise ExplainerVisualGenerationError("EXPLAINER_TASK_NOT_FOUND", "工作流任务已不存在。")
            if str(current["job_id"] or "") != old_job_id or int(current["revision"] or 0) != task_revision:
                raise ExplainerVisualGenerationError(
                    "EXPLAINER_STALE_TASK",
                    "该任务已被其他操作替换，本次继续请求已失效。",
                    {"task_id": task_id},
                )
            job = jobs.create_job_in_transaction(
                connection,
                old_job["project_id"],
                str(old_job["type"]),
                str(old_job["subject_type"]),
                str(old_job["subject_id"]),
                str(old_job["channel"]),
                snapshot,
                f"{operation_id}:replacement",
                subject_kind=str(old_job["subject_kind"] or old_job["subject_type"]),
                scope_kind=str(old_job["scope_kind"] or "PROJECT"),
                scope_project_id=old_job["scope_project_id"],
                scope_episode_id=old_job["scope_episode_id"],
                scope_shot_id=old_job["scope_shot_id"],
                stage_code=str(old_job["stage_code"] or "LEGACY_UNCLASSIFIED"),
            )
            new_job_id = str(job["id"])
            updated = connection.execute(
                "UPDATE automation_workflow_run_tasks SET job_id = ?, revision = revision + 1, "
                "machine_context_json = ? "
                "WHERE id = ? AND job_id = ? AND revision = ?",
                (
                    new_job_id,
                    json.dumps(
                        {
                            "replacement_history": [
                                {"old_job_id": old_job_id, "new_job_id": new_job_id, "operation_id": operation_id}
                            ],
                            "collection_hash": collection_hash,
                        },
                        ensure_ascii=False,
                    ),
                    task_id,
                    old_job_id,
                    task_revision,
                ),
            ).rowcount
            if updated != 1:
                raise ExplainerVisualGenerationError(
                    "EXPLAINER_STALE_TASK",
                    "替换收集任务时发生并发冲突，请刷新后重试。",
                    {"task_id": task_id},
                )
            connection.execute(
                "UPDATE explainer_step_bindings SET job_id = ?, status = 'PENDING', job_attempt_id = NULL, "
                "started_at = NULL, finished_at = NULL, attempt_count = 0, "
                "blocker_code = NULL, error_detail_redacted = NULL, output_ref_json = ? "
                "WHERE id = ?",
                (
                    new_job_id,
                    json.dumps(
                        {"collection_hash": collection_hash, "replacement_of": old_job_id},
                        ensure_ascii=False,
                    ),
                    step_binding_id,
                ),
            )
        return new_job_id

    # ------------------------------------------------------------------ helpers
    def _submission_service(self) -> Any:
        if self._submission_factory is not None:
            return self._submission_factory()
        return build_v2_submission_service(self.database, self.settings)

    def _preview_request(
        self,
        *,
        capability_code: str,
        project_id: str,
        semantic_inputs: Mapping[str, Any],
        run_overrides: Mapping[str, Any],
        execution_profile_version_id: str | None,
        expected_resolution_hash: str | None = None,
    ) -> Any:
        from local_drama.model_platform.application.capability_resolution import CapabilityScopeContext
        from local_drama.model_platform.application.execution_planning import ExecutionPreviewRequest

        return ExecutionPreviewRequest(
            capability_code=capability_code,
            scope=CapabilityScopeContext(project_id=project_id),
            semantic_inputs=dict(semantic_inputs),
            run_overrides=dict(run_overrides),
            expected_resolution_hash=expected_resolution_hash,
            execution_profile_version_id=execution_profile_version_id,
        )

    def _preview(
        self,
        *,
        capability_code: str,
        project_id: str,
        semantic_inputs: Mapping[str, Any],
        run_overrides: Mapping[str, Any],
        execution_profile_version_id: str | None,
    ) -> dict[str, Any]:
        planner = build_explainer_execution_planner(self.database)
        preview = planner.preview(
            self._preview_request(
                capability_code=capability_code,
                project_id=project_id,
                semantic_inputs=semantic_inputs,
                run_overrides=run_overrides,
                execution_profile_version_id=execution_profile_version_id,
            )
        )
        blockers = [
            {
                "code": str(blocker),
                "message": _blocker_message(str(blocker)),
                "retryable": str(blocker).startswith("RUNTIME_INSTALLATION"),
            }
            for blocker in preview.blockers
        ]
        if preview.execution_profile_version_id is None:
            blockers.append(
                {
                    "code": "EXPLAINER_PROFILE_UNAVAILABLE",
                    "message": f"本机没有可用于 {capability_code} 的已发布 Profile。",
                    "retryable": False,
                }
            )
        return {
            "execution_profile_version_id": preview.execution_profile_version_id,
            "profile_title": str((preview.resolved_parameters or {}).get("__profile_title__") or "") or None,
            "resolved_parameters": dict(preview.resolved_parameters),
            "resolution_hash": preview.resolution_hash,
            "blockers": blockers,
        }

    def _candidate_count(self, mode: str, requested: Any) -> int:
        video = self.repo.require_video_for_project
        del video
        default = 1
        try:
            value = int(requested) if requested is not None else default
        except (TypeError, ValueError) as error:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_CANDIDATE_COUNT_INVALID", "候选数量必须是整数。", {"candidate_count": requested}
            ) from error
        if mode == "IMAGE_TO_VIDEO":
            if value not in {1, 2}:
                raise ExplainerVisualGenerationError(
                    "EXPLAINER_CANDIDATE_COUNT_INVALID",
                    "视频候选每批只能是 1 段或 2 段。",
                    {"candidate_count": value, "allowed": [1, 2]},
                )
            return value
        if not 1 <= value <= 4:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_CANDIDATE_COUNT_INVALID",
                "图片候选每批只能是 1 到 4 张。",
                {"candidate_count": value, "allowed": [1, 2, 3, 4]},
            )
        return value

    def _candidate_seeds(
        self,
        owner: ExplainerVisualOwner,
        purpose: str,
        mode: str,
        count: int,
        command: Mapping[str, Any],
    ) -> list[int]:
        supplied = command.get("candidate_seeds")
        if isinstance(supplied, Sequence) and not isinstance(supplied, (str, bytes)):
            seeds = [int(value) for value in supplied][:count]
            if len(seeds) == count and len(set(seeds)) == count:
                return seeds
            raise ExplainerVisualGenerationError(
                "EXPLAINER_SEEDS_INVALID",
                "服务端提供的种子必须与候选数量一致且互不相同。",
                {"candidate_seeds": list(supplied), "candidate_count": count},
            )
        # New creative draw: every candidate in the batch gets its own seed, and the
        # batch is derived from the operation so a replay keeps the same seeds.
        operation = str(command.get("operation_id") or uuid.uuid4().hex)
        seeds: list[int] = []
        for ordinal in range(count):
            material = f"{owner.video_id}:{owner.id}:{purpose}:{mode}:{operation}:{ordinal}"
            seeds.append(int(hashlib.sha256(material.encode("utf-8")).hexdigest()[:8], 16))
        if len(set(seeds)) != count:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_SEEDS_INVALID", "无法为该批次分配互不相同的种子，请重新发起一次抽卡。"
            )
        return seeds

    def _resolve_references(
        self, owner: ExplainerVisualOwner, command: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        """Resolve the adopted references into verified immutable media versions.

        For an image-to-video command the *adopted keyframe* of the beat occupies the
        ``FIRST_FRAME`` slot.  The keyframe is a real ``explainer_beat_selections``
        row, so it is resolved through the same verification the reference branch
        uses — as a business reference, never as a path.  Without this the plan could
        only ever be reported as missing ``FIRST_FRAME`` even though the operator had
        already adopted a first frame, which made every real I2V plan unexecutable.
        """

        requested = command.get("reference_selections") or []
        resolved: list[dict[str, Any]] = []
        keyframe_selection_id = str(command.get("input_keyframe_selection_id") or "").strip()
        if keyframe_selection_id:
            if owner.kind != "BEAT":
                raise ExplainerVisualGenerationError(
                    "EXPLAINER_KEYFRAME_OWNER_MISMATCH",
                    "首帧只能属于画面段，不能作为定妆参考的输入。",
                    {"owner_kind": owner.kind},
                )
            resolved.append(self._keyframe_reference(owner, keyframe_selection_id))
        for item in requested:
            if not isinstance(item, Mapping):
                raise ExplainerVisualGenerationError(
                    "EXPLAINER_REFERENCE_INVALID", "参考引用必须是对象。", {"reference": item}
                )
            entity_id = str(item.get("entity_id") or "").strip()
            reference_id = str(item.get("reference_id") or "").strip()
            if not entity_id or not reference_id:
                raise ExplainerVisualGenerationError(
                    "EXPLAINER_REFERENCE_INVALID",
                    "参考引用必须同时提供实体与已采用的参考 ID。",
                    {"reference": dict(item)},
                )
            media = self._reference_media_version(owner, entity_id, reference_id)
            resolved.append(
                {
                    "entity_id": entity_id,
                    "reference_id": reference_id,
                    "media_version_id": media["media_version_id"],
                    "sha256": media["sha256"],
                    "reference_kind": media["reference_kind"],
                }
            )
        return resolved

    def _keyframe_reference(
        self, owner: ExplainerVisualOwner, selection_id: str
    ) -> dict[str, Any]:
        """Resolve an adopted ``KEYFRAME`` selection into its verified media version.

        The selection row is the operator's own adoption fact; the media version it
        points at is re-verified here (registered, integrity ``VERIFIED``, and
        readable), so the motion model is only ever handed a real, hashed picture.
        """

        selection = self.repo.find("explainer_beat_selections", selection_id)
        if selection is None:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_KEYFRAME_NOT_ADOPTED",
                "找不到该首帧选择记录。",
                {"input_keyframe_selection_id": selection_id},
            )
        if str(selection.get("beat_id") or "") != owner.id:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_KEYFRAME_OWNER_MISMATCH",
                "该首帧不属于当前画面段。",
                {"input_keyframe_selection_id": selection_id, "beat_id": owner.id},
            )
        if str(selection.get("status") or "").upper() != "ACTIVE":
            raise ExplainerVisualGenerationError(
                "EXPLAINER_KEYFRAME_NOT_ADOPTED",
                "该首帧不是当前采用的首帧。",
                {"input_keyframe_selection_id": selection_id, "status": selection.get("status")},
            )
        if str(selection.get("purpose") or "VISUAL").upper() != "KEYFRAME":
            raise ExplainerVisualGenerationError(
                "EXPLAINER_KEYFRAME_NOT_ADOPTED",
                "所选记录不是首帧（KEYFRAME）采用记录。",
                {"input_keyframe_selection_id": selection_id, "purpose": selection.get("purpose")},
            )
        media_version_id = str(selection.get("media_version_id") or "")
        media = self.repo.find("media_versions", media_version_id) if media_version_id else None
        if media is None:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_KEYFRAME_MEDIA_MISSING",
                "首帧对应的媒体版本不存在，无法作为图生视频输入。",
                {"input_keyframe_selection_id": selection_id, "media_version_id": media_version_id},
            )
        if str(media.get("integrity_status") or "").upper() != "VERIFIED":
            raise ExplainerVisualGenerationError(
                "EXPLAINER_KEYFRAME_INTEGRITY_NOT_VERIFIED",
                "首帧媒体未通过完整性校验，拒绝作为图生视频输入。",
                {"media_version_id": media_version_id, "integrity_status": media.get("integrity_status")},
            )
        return {
            "media_version_id": media_version_id,
            "sha256": str(media.get("sha256") or ""),
            "reference_kind": "KEYFRAME",
            "selection_id": selection_id,
        }

    def _reference_media_version(
        self, owner: ExplainerVisualOwner, entity_id: str, reference_id: str
    ) -> dict[str, Any]:
        entity = self.repo.find("explainer_entities", entity_id)
        if entity is None or str(entity.get("video_id")) != owner.video_id:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_REFERENCE_ENTITY_MISMATCH",
                "参考图所属的实体不在当前解说作品中。",
                {"entity_id": entity_id},
            )
        reference = self.repo.find("story_asset_references", reference_id)
        if reference is None or str(reference.get("status")) != "ACTIVE":
            raise ExplainerVisualGenerationError(
                "EXPLAINER_REFERENCE_NOT_ADOPTED",
                "该参考图不是当前采用的参考版本。",
                {"reference_id": reference_id},
            )
        if str(reference.get("story_asset_id") or "") != str(entity.get("story_asset_id") or ""):
            raise ExplainerVisualGenerationError(
                "EXPLAINER_REFERENCE_ENTITY_MISMATCH",
                "该参考图不属于所选人物/场景。",
                {"entity_id": entity_id, "reference_id": reference_id},
            )
        media_version_id = str(reference.get("media_version_id") or "")
        media = self.repo.find("media_versions", media_version_id) if media_version_id else None
        if media is None:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_REFERENCE_MEDIA_MISSING",
                "参考图对应的媒体版本不存在，无法作为生成输入。",
                {"reference_id": reference_id, "media_version_id": media_version_id},
            )
        return {
            "media_version_id": media_version_id,
            "sha256": str(media.get("sha256") or ""),
            "reference_kind": str(reference.get("reference_kind") or "HERO"),
        }

    def _compile_prompt(
        self,
        owner: ExplainerVisualOwner,
        purpose: str,
        command: Mapping[str, Any],
        references: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Compile the frozen prompt bundle in the fixed order the design requires.

        Order is: subject/beat content → chosen identity/state/scene reference
        invariants → channel style → workflow hard constraints.  A user override
        replaces the content paragraph, never the hard constraints, and every field
        is length-limited *before* concatenation so a hard constraint can never be
        truncated away.
        """

        video = self.repo.find("explainer_videos", owner.video_id) or {}
        style = self._resolved_style(video)
        geometry = self._geometry(video, command)
        beat = owner.record if owner.kind == "BEAT" else {}
        entity = owner.record if owner.kind == "ENTITY" else {}

        override = str(command.get("prompt_override") or "").strip()
        if owner.kind == "BEAT":
            content = override or str(beat.get("prompt_intent") or "").strip() or str(beat.get("visual_intent") or "").strip()
        else:
            content = override or _entity_reference_prompt(entity)
        if not content:
            content = f"解说画面：{owner.code}"

        identity_parts: list[str] = []
        for reference in references:
            identity_parts.append(str(reference.get("reference_kind") or "HERO"))
        unresolved: list[str] = []
        style_notes: list[dict[str, Any]] = []
        style_text = str(style.get("style_prompt") or "").strip()
        negative = str(style.get("negative_prompt") or "").strip()
        override_negative = str(command.get("negative_override") or "").strip()
        if override_negative:
            negative = override_negative
        if not style_text:
            # Informational only: the built-in default is a supported, versioned choice
            # (§B3.2), so it must not become an unresolved *conflict* that blocks the plan.
            style_notes.append(
                {
                    "kind": "STYLE_DEFAULT_APPLIED",
                    "message": "本片没有可用的风格版本，已使用应用内有版本号的默认风格描述。",
                }
            )
            style_text = _DEFAULT_STYLE_PROMPT

        hard_constraints = [
            "no text, no letters, no captions, no watermark",
        ]
        if purpose == "REFERENCE" and owner.kind == "ENTITY":
            hard_constraints.append("single subject, no extra characters")
            if str(entity.get("entity_type") or "") == "LOCATION":
                hard_constraints.append("no people in frame")
        if purpose == "KEYFRAME":
            hard_constraints.append("first frame state only")
        if purpose == "VISUAL" and str(command.get("mode") or "").upper() == "IMAGE_TO_VIDEO":
            hard_constraints.append("start from the adopted first frame")

        segments = [
            _limited("content", content, 900),
            _limited("identity", "；".join(identity_parts), 240),
            _limited("style", style_text, 500),
            _limited("constraints", "；".join(hard_constraints), 300),
        ]
        prompt = "；".join(part for part in segments if part)
        # A style can forbid exactly what the content must show ("无渐变" style vs
        # "从浅蓝渐变到深黑" content).  Such a pair is not a user-declared conflict,
        # so it never blocks the plan, but it silently produces a picture that
        # fails style review.  Measured with the real model: the flat-infographic
        # style rendered a continuous gradient and the local vision check answered
        # ``style_match=FAIL`` for a request the compiler had reported as resolved.
        conflict = _style_content_conflict(style_text, content)
        if conflict is not None:
            style_notes.append(
                {
                    "kind": "STYLE_CONTENT_CONFLICT_RISK",
                    "message": (
                        f"所选风格要求「{conflict[0]}」，而画面描述要求「{conflict[1]}」；"
                        "生成时以风格的渲染方式为准，如需渐变请改用写实/科学可视化风格。"
                    ),
                    "style_requirement": conflict[0],
                    "content_requirement": conflict[1],
                }
            )
        motion = str(command.get("motion_prompt") or "").strip()
        if purpose == "VISUAL" and motion:
            prompt = f"{prompt}；运动：{_limited('motion', motion, 300)}"
        camera = str(command.get("camera_movement") or "").strip()
        if camera:
            prompt = f"{prompt}；镜头：{_limited('camera', camera, 120)}"
        if len(prompt) > 2000:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_PROMPT_OVER_BUDGET",
                "画面提示词超出预算，请缩短画面描述或风格描述。",
                {"length": len(prompt), "budget": 2000},
            )
        return {
            "schema_version": "localdrama.explainer.prompt-bundle.v1",
            "prompt": prompt,
            "negative_prompt": negative,
            "content": content,
            "identity_reference_kinds": identity_parts,
            "style_hash": style.get("style_hash"),
            "style_version_id": style.get("version_id"),
            "geometry": geometry,
            "hard_constraints": hard_constraints,
            "unresolved_constraints": unresolved,
            "style_notes": style_notes,
            "max_image_references": self._max_image_references(command),
            "allowed_durations_ms": self._allowed_durations(command) if purpose == "VISUAL" else None,
            "render_type_planned": MODE_RENDER_TYPE.get(str(command.get("mode") or "").upper()),
        }

    def _resolved_style(self, video: Mapping[str, Any]) -> dict[str, Any]:
        return resolve_explainer_style(self.repo, video)

    def _geometry(self, video: Mapping[str, Any], command: Mapping[str, Any]) -> dict[str, int]:
        width = int(command.get("width") or video.get("width") or 1920)
        height = int(command.get("height") or video.get("height") or 1080)
        return {"width": max(64, width), "height": max(64, height)}

    @staticmethod
    def _frozen_semantic_inputs(plan: ExplainerVisualPlan, seed: int) -> dict[str, Any]:
        """The plan's frozen semantic inputs for one candidate seed.

        Falls back to a re-derivation only for a plan built before the frozen map
        existed; a submit must never invent a different input shape than the plan
        the operator confirmed.
        """

        frozen = plan.semantic_inputs_by_seed.get(str(seed))
        if frozen:
            return {str(key): value for key, value in frozen.items()}
        return {
            "PROMPT": plan.prompt,
            "NEGATIVE_PROMPT": plan.negative_prompt,
            "SEED": int(seed),
        }

    def _semantic_inputs(
        self,
        mode: str,
        prompt_bundle: Mapping[str, Any],
        references: Sequence[Mapping[str, Any]],
        seed: int,
        duration_ms: int | None,
    ) -> dict[str, Any]:
        """Build the exact semantic inputs the worker's compiled workflow consumes.

        Reference images enter as business references and nothing else; the worker
        verifies and materialises them.  No path, URL or artifact id is invented.

        ``SEED`` is included because every published image workflow declares it as
        a *required* semantic slot; without it the worker refuses the execution
        (measured ``MP_COMFY_EXECUTION_INPUT_INVALID``, ``missing=['SEED']``) and,
        worse, several candidates of one batch would all render the workflow's
        authored seed instead of their own frozen one.
        """

        geometry = prompt_bundle.get("geometry") if isinstance(prompt_bundle.get("geometry"), Mapping) else {}
        inputs: dict[str, Any] = {
            "PROMPT": str(prompt_bundle.get("prompt") or ""),
            "NEGATIVE_PROMPT": str(prompt_bundle.get("negative_prompt") or ""),
            "SEED": int(seed),
        }
        if mode != "IMAGE_EDIT":
            # The published edit workflows render their own reference geometry and
            # do not declare WIDTH/HEIGHT, so sending them would be an unknown slot.
            inputs["WIDTH"] = int(geometry.get("width") or 1920)
            inputs["HEIGHT"] = int(geometry.get("height") or 1080)
        for index, reference in enumerate(references):
            inputs[self._reference_role(mode, index)] = {
                "media_version_id": str(reference.get("media_version_id")),
                "sha256": str(reference.get("sha256")),
            }
        if mode == "IMAGE_TO_VIDEO" and duration_ms:
            inputs["DURATION_MS"] = int(duration_ms)
        return inputs

    @staticmethod
    def _reference_role(mode: str, index: int) -> str:
        """Name the semantic slot a reference occupies for this generation mode.

        The published workflows declare exact slot names, and a reference sent to
        an undeclared slot is refused as an unknown input.
        """

        if mode == "IMAGE_TO_VIDEO":
            return "FIRST_FRAME" if index == 0 else f"FIRST_FRAME_{index}"
        if mode == "IMAGE_EDIT":
            return f"REFERENCE_IMAGE_{index + 1}"
        return "REFERENCE_IMAGE" if index == 0 else f"REFERENCE_IMAGE_{index}"

    def _declared_semantic_slots(
        self, execution_profile_version_id: str | None
    ) -> tuple[frozenset[str], frozenset[str]] | None:
        """The semantic slots (and the required subset) of the resolved workflow.

        Returns ``None`` when the Profile is unknown or carries no V2 Comfy
        workflow binding, in which case the caller keeps the full input set and
        the execution path still fails closed with its own error code.
        """

        if not execution_profile_version_id:
            return None
        with self.database.connect() as connection:
            profile = connection.execute(
                "SELECT payload_json FROM mp_execution_profile_versions WHERE id = ?",
                (execution_profile_version_id,),
            ).fetchone()
            if profile is None:
                return None
            payload = profile["payload_json"]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except (TypeError, ValueError):
                    return None
            binding = payload.get("execution_binding") if isinstance(payload, Mapping) else None
            workflow_version_id = binding.get("workflow_version_id") if isinstance(binding, Mapping) else None
            if not workflow_version_id:
                return None
            workflow = connection.execute(
                "SELECT contract_json FROM workflow_versions WHERE id = ?", (str(workflow_version_id),)
            ).fetchone()
        if workflow is None:
            return None
        contract = workflow["contract_json"]
        if isinstance(contract, str):
            try:
                contract = json.loads(contract)
            except (TypeError, ValueError):
                return None
        slots = contract.get("input_slots") if isinstance(contract, Mapping) else None
        if not isinstance(slots, Mapping):
            return None
        declared = frozenset(str(name) for name in slots)
        required = frozenset(
            str(name)
            for name, spec in slots.items()
            if not isinstance(spec, Mapping) or spec.get("required", True)
        )
        return declared, required

    def _planned_duration(self, owner: ExplainerVisualOwner, purpose: str) -> int | None:
        if purpose != "VISUAL":
            return None
        beat = owner.record if owner.kind == "BEAT" else {}
        preferred = beat.get("preferred_duration_ms")
        try:
            return int(preferred) if preferred else None
        except (TypeError, ValueError):
            return None

    def _max_image_references(self, command: Mapping[str, Any]) -> int | None:
        value = command.get("max_image_references")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _allowed_durations(self, command: Mapping[str, Any]) -> tuple[int, ...] | None:
        values = command.get("allowed_durations_ms")
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)) and values:
            return tuple(int(value) for value in values)
        return None

    def _budget(self) -> dict[str, Any] | None:
        limits = getattr(self.settings, "explainer_budget", None)
        return dict(limits) if isinstance(limits, Mapping) else None

    def _next_variant_no(self, plan: ExplainerVisualPlan) -> int:
        rows = self.repo.media_candidates(
            beat_id=plan.owner.id if plan.owner.kind == "BEAT" else None,
            entity_id=plan.owner.id if plan.owner.kind == "ENTITY" else None,
            purpose=plan.purpose,
        )
        return 1 + max((int(row.get("variant_no") or 0) for row in rows), default=0)

    def _assert_expected_revision(
        self, owner: ExplainerVisualOwner, purpose: str, command: Mapping[str, Any]
    ) -> None:
        if owner.kind == "BEAT":
            expected = command.get("expected_beat_revision")
        else:
            expected = command.get("expected_entity_revision")
        if expected is None:
            return
        try:
            expected_value = int(expected)
        except (TypeError, ValueError) as error:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_REVISION_INVALID", "预期修订号必须是整数。", {"expected": expected}
            ) from error
        if expected_value != owner.revision:
            raise ExplainerVisualGenerationError(
                "EXPLAINER_STALE_REVISION",
                "该对象已更新，请刷新当前选择后再生成。",
                {"expected_revision": expected_value, "actual_revision": owner.revision, "owner_kind": owner.kind},
            )


_DEFAULT_STYLE_PROMPT = (
    "写实解说画面风格，自然光照，构图清晰，主体居中偏左，环境细节克制；"
    "不出现文字、字幕、水印或额外人物。"
)


def _style_content_conflict(style_text: str, content: str) -> tuple[str, str] | None:
    """Return the first (style ban, content requirement) pair that contradicts.

    Only deterministic, literal pairs are reported: a style that says "无渐变"
    against content that asks for 渐变/光影/体积光, and the flat-vector family
    against a photographic look.  Anything subtler stays the model's judgement.
    """

    for ban, required in _STYLE_BAN_VS_CONTENT:
        if ban in style_text and any(word in content for word in required):
            hit = next(word for word in required if word in content)
            return ban, hit
    return None


#: ``(style wording, content wordings it forbids)``.  Kept deliberately small and
#: literal so the check cannot invent a conflict the user never wrote.
_STYLE_BAN_VS_CONTENT: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("无渐变", ("渐变", "体积光", "光束")),
    ("无阴影", ("光影", "阴影", "体积光")),
    ("扁平矢量", ("写实", "照片", "摄影")),
    ("纯色块", ("渐变",)),
)


def _bounded_scalar_input(value: Any) -> bool:
    """Whether a value is a non-empty bounded scalar the V2 Comfy contract accepts.

    The worker refuses mappings, sequences, ``None`` and blank or over-long strings,
    so the planner applies the same rule before it freezes semantic inputs.
    """

    if value is None or isinstance(value, (Mapping, list, tuple, set)):
        return False
    if isinstance(value, str):
        return bool(value.strip()) and len(value) <= 8192
    return isinstance(value, (int, float, bool))


def _entity_reference_prompt(entity: Mapping[str, Any]) -> str:
    """Deterministic reference prompt for a character / scene / prop (spec C4.4)."""

    entity_type = str(entity.get("entity_type") or "").upper()
    name = str(entity.get("name") or entity.get("code") or "对象")
    description = str(entity.get("description") or "").strip()
    if entity_type == "LOCATION":
        base = f"{name} 的场景参考图：突出固定空间布局、主要入口与材质，画面中没有人物"
    elif entity_type == "PROP":
        base = f"{name} 的道具参考图：单一主体，完整展示外形、材质与尺度参照"
    elif entity_type in {"REAL_PERSON", "FICTIONAL_CHARACTER"}:
        base = f"{name} 的人物参考图：只有一个人物，正面站立，服务身份识别，不做剧情动作"
    else:
        base = f"{name} 的参考图：清晰展示主要外观特征"
    return f"{base}。{description}".strip("。") if description else base


def _style_prompt_from_profile(profile: Mapping[str, Any] | None) -> str:
    if not profile:
        return ""
    parts: list[str] = []
    render_style = str(profile.get("render_style") or "").strip()
    if render_style:
        parts.append(render_style)
    for key in ("palette_json", "lighting_json", "camera_grammar_json"):
        value = profile.get(key)
        if isinstance(value, Mapping):
            for item in value.values():
                text = str(item).strip()
                if text:
                    parts.append(text)
        elif isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "；".join(parts)


def _negative_from_profile(profile: Mapping[str, Any] | None) -> str:
    if not profile:
        return ""
    value = profile.get("negative_constraints_json")
    if isinstance(value, Mapping):
        return "；".join(str(item).strip() for item in value.values() if str(item).strip())
    if isinstance(value, str):
        return value.strip()
    return ""


def _limited(field: str, value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    raise ExplainerVisualGenerationError(
        "EXPLAINER_PROMPT_FIELD_OVER_BUDGET",
        f"画面提示词的“{field}”字段超出长度预算，请缩短该字段后重试。",
        {"field": field, "length": len(text), "budget": limit},
    )


def _blocker_message(code: str) -> str:
    return {
        "RUNTIME_INSTALLATION_VERSION_NOT_ACTIVE": "该 Profile 绑定的运行时当前不可用，请先在模型中心检查运行时状态。",
        "MP_EXECUTION_HANDLER_UNAVAILABLE": "该能力还没有对应的执行处理器。",
        "MP_EXECUTION_NOT_READY": "当前能力没有可提交的 Profile。",
    }.get(code, f"生成条件未满足：{code}")


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def resolve_explainer_style(repo: Any, video: Mapping[str, Any]) -> dict[str, Any]:
    """The film's effective style, resolved from the frozen snapshot only.

    Shared by the prompt compiler and the step-2 read model so the style shown in
    the UI is the exact style a generation command would freeze — never a second,
    independently derived label.  ``title``/``name`` come from the bound profile
    version when there is one, and stay ``None`` when the request falls back to the
    project default, so a caller cannot present a default as a chosen template.
    """

    payload = video.get("input_payload_json")
    payload = payload if isinstance(payload, Mapping) else {}
    preferences = payload.get("visual_preferences") if isinstance(payload.get("visual_preferences"), Mapping) else {}
    style_override = str(preferences.get("style_prompt_override") or "").strip()
    negative_override = str(preferences.get("negative_prompt_override") or "").strip()
    profile_version_id = str(video.get("current_channel_profile_version_id") or "")
    profile = repo.find("channel_profile_versions", profile_version_id) if profile_version_id else None
    style_prompt = style_override or _style_prompt_from_profile(profile)
    negative = negative_override or _negative_from_profile(profile)
    return {
        "version_id": profile_version_id or None,
        "title": (profile or {}).get("title"),
        "name": (profile or {}).get("name"),
        "style_prompt": style_prompt,
        "negative_prompt": negative,
        "style_hash": _hash_text(
            json.dumps(
                {"version_id": profile_version_id, "style": style_prompt, "negative": negative},
                ensure_ascii=False,
                sort_keys=True,
            )
        ),
        "source": "VISUAL_PREFERENCE_OVERRIDE" if style_override else ("CHANNEL_PROFILE_VERSION" if profile else "DEFAULT"),
    }


def _as_mapping(value: Any) -> dict[str, Any]:
    """Decode a possibly text-encoded JSON object column into a mutable mapping."""

    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (str, bytes)):
        try:
            decoded = json.loads(value or "{}")
        except (TypeError, ValueError):
            return {}
        return dict(decoded) if isinstance(decoded, Mapping) else {}
    return {}


def _now_stamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def build_explainer_visual_generation_service(
    repo: ExplainerRepository,
    *,
    database: Any,
    settings: Any,
    picture_runtime: Any = None,
) -> ExplainerVisualGenerationService:
    """Wire the generation adapter.

    Cross-service construction lives in this factory (the repository's
    architecture-debt policy allows a named ``build_*`` scope to name the concrete
    services it binds), so the service class itself depends only on the objects it is
    handed.
    """

    return ExplainerVisualGenerationService(
        repo, database=database, settings=settings, picture_runtime=picture_runtime
    )


def build_explainer_execution_planner(database: Any) -> Any:
    """The Model Platform V2 planner used for the read-only generation preview."""

    from local_drama.model_platform.application.execution_planning import ExecutionPlanningService

    return ExecutionPlanningService(database)


def build_v2_submission_service(database: Any, settings: Any) -> Any:
    """The only service allowed to enqueue V2 model work.

    ``settings`` stays in the signature because the composition root the routes use
    passes it; the handler registry itself is built from the process settings on the
    worker side (``production_worker_execution_handlers``), and the submission side only
    needs the declarations.  The previous import named a symbol that does not exist
    (``build_production_execution_handlers``), so every explainer image/video submit
    raised ImportError and answered HTTP 500 — the read-only plan kept working, which is
    why the fake-client tests never saw it.
    """

    del settings
    from local_drama.model_platform.application.execution_submission import ExecutionSubmissionService
    from local_drama.model_platform.application.production_execution_registry import (
        production_execution_handlers,
    )

    return ExecutionSubmissionService(database, production_execution_handlers())


def build_explainer_capability_assignment_service(database: Any) -> Any:
    """The V2 capability assignment chain used to resolve a capability for a project."""

    from local_drama.model_platform.application.capability_resolution import CapabilityAssignmentService

    return CapabilityAssignmentService(database)


def build_explainer_job_service(database: Any) -> Any:
    """The shared job service, used for cancel/retry and transactional job creation."""

    from local_drama.application.jobs import JobService

    return JobService(database)
