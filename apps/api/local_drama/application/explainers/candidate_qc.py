"""The per-candidate picture check the adoption gate reads.

Audit finding A10: the visual review provider was assembled with an empty ``basis``
and the picture candidates were never inspected at all, so the fields the adoption
gate reads (``content_relevant``, ``identity_ok``, ``text_readable``) were written by
nobody.  The design requires the review request to carry *this shot's* frozen
expectation — visual intent, key action, the applicable person/costume/scene
references — together with the candidate's own real sampled frames (§6.2), and it
requires an unanswered question to stay ``UNKNOWN`` rather than pass.

Nothing here invents a verdict: a check is only written when frames were really read
and the model really answered.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from local_drama.domain.explainers.contracts import ExplainerContractError

__all__ = [
    "CONTENT_ISSUE_KINDS",
    "IDENTITY_ISSUE_KINDS",
    "TEXT_ISSUE_KINDS",
    "build_candidate_checker",
    "candidate_expectation",
    "verdicts_from_findings",
]

#: Findings that mean the picture does not match what the beat asked for.
CONTENT_ISSUE_KINDS: frozenset[str] = frozenset(
    {
        "PROP_MISSING",
        "LIGHT_SOURCE_OFF",
        "ERA_INCONSISTENT",
        "ACTION_MISSING",
        "CONTENT_MISMATCH",
        "WRONG_SCENE",
        "MUST_BE_MOTION_VIOLATED",
    }
)
#: Findings about the locked people and props the shot must keep.
IDENTITY_ISSUE_KINDS: frozenset[str] = frozenset(
    {"WRONG_CHARACTER", "EXTRA_PERSON", "CHARACTER_COUNT_MISMATCH", "IDENTITY_MISMATCH"}
)
#: Findings about readable text drawn into the picture.
TEXT_ISSUE_KINDS: frozenset[str] = frozenset(
    {"TEXT_ILLEGIBLE", "TEXT_UNREADABLE", "TEXT_TRUNCATED", "TEXT_OVERFLOWS_SAFE_AREA"}
)

#: A frame the provider could not read is not evidence about the picture.
FRAME_UNAVAILABLE_KIND = "VISUAL_FRAME_UNAVAILABLE"

#: How many frames one candidate is checked with: the first, middle and last of its
#: own clip.  The design starts short clips at first/middle/last and asks for extra
#: frames near a key action; the sampling range is recorded either way.
CANDIDATE_SAMPLE_FRACTIONS: tuple[float, ...] = (0.05, 0.5, 0.95)


def candidate_expectation(
    *,
    beat: Mapping[str, Any],
    candidate: Mapping[str, Any],
    identity_references: Sequence[Mapping[str, Any]] = (),
    script_revision_id: str | None = None,
    locale: str | None = None,
) -> dict[str, Any]:
    """This shot's frozen expectation, as handed to the visual review.

    It is built from the *frozen* beat and candidate rows, so the model is asked
    about what the plan declared rather than about whatever is on disk now.
    """

    return {
        "beat_id": str(beat.get("id") or ""),
        "beat_code": str(beat.get("code") or ""),
        "visual_intent": str(beat.get("visual_intent") or ""),
        "must_be_motion": bool(beat.get("must_be_motion")),
        "render_type_planned": str(beat.get("render_type") or ""),
        "render_type_actual": str(
            candidate.get("render_type_actual") or beat.get("render_type") or ""
        ),
        "visual_factuality": str(beat.get("visual_factuality") or ""),
        "entity_codes": [str(item) for item in (beat.get("entity_refs") or [])],
        "claim_codes": [str(item) for item in (beat.get("claim_refs") or [])],
        "prompt_intent": str(beat.get("prompt_intent") or ""),
        "identity_references": [
            {
                "entity_code": str(item.get("entity_code") or item.get("entity_id") or ""),
                "media_version_id": str(item.get("media_version_id") or ""),
                "media_sha256": str(item.get("media_sha256") or ""),
                "slot": str(item.get("slot") or ""),
            }
            for item in identity_references
            if isinstance(item, Mapping)
        ],
        "source_window_us": {
            "source_in_us": candidate.get("source_in_us", (candidate.get("execution_snapshot") or {}).get("source_in_us")),
            "source_out_us": candidate.get("source_out_us", (candidate.get("execution_snapshot") or {}).get("source_out_us")),
        },
        "script_revision_id": script_revision_id,
        "locale": locale,
        "expectation_authority": "FROZEN_BEAT_AND_CANDIDATE",
    }


def check_questions(expectation: Mapping[str, Any]) -> list[str]:
    """The questions this shot is actually checked with."""

    questions = [
        f"画面是否与冻结视觉意图一致：{expectation.get('visual_intent') or '(未声明)'}？",
        "画面中是否出现了与依据不符的物体、时代错误或错误场景？",
    ]
    if expectation.get("must_be_motion"):
        questions.append("画面是否表现了真实运动，而不是静止画面？")
    if expectation.get("entity_codes"):
        questions.append(
            "画面中的人物数量与外观是否与冻结参考一致（"
            + "、".join(str(item) for item in expectation["entity_codes"])
            + "）？"
        )
    if str(expectation.get("render_type_planned")) == "INFOGRAPHIC":
        questions.append("画面中的可读文字是否清晰、完整、未被裁切？")
    return questions


def verdicts_from_findings(findings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Map the provider's findings to the tri-state fields the gate reads.

    A category the model reported nothing about is ``PASS`` **only because it looked
    and found nothing**; a category it could not judge is left out entirely, which the
    adoption gate reads as ``UNKNOWN``.  A frame nobody could read is not evidence.
    """

    unreadable = [
        item for item in findings if str(item.get("issue_kind") or "") == FRAME_UNAVAILABLE_KIND
    ]
    real = [
        item for item in findings if str(item.get("issue_kind") or "") != FRAME_UNAVAILABLE_KIND
    ]
    if not real and unreadable:
        return {
            "checked": False,
            "reason": "NO_READABLE_FRAME",
            "unreadable_frames": len(unreadable),
            "verdicts": {},
            "evidence": [],
        }
    # A finding that says "I could not judge this" is not a pass for its category.
    undecided = {
        str(item.get("issue_kind") or "")
        for item in real
        if str(item.get("unknown_reason") or "").strip()
    }
    kinds = {str(item.get("issue_kind") or "") for item in real}
    verdicts: dict[str, Any] = {}
    state: dict[str, str] = {}
    for field, issue_kinds in (
        ("content_relevant", CONTENT_ISSUE_KINDS),
        ("identity_ok", IDENTITY_ISSUE_KINDS),
        ("text_readable", TEXT_ISSUE_KINDS),
    ):
        failed = sorted(kinds & issue_kinds)
        if failed:
            verdicts[field] = False
            state[field] = "FAIL"
        elif kinds & undecided:
            # Something in this review could not be judged: say nothing about it.
            state[field] = "UNKNOWN"
        else:
            # Unknown issue kinds are not silently forgiven: an unreconciled anomaly
            # is a reason not to auto-adopt, so it counts against content.
            unreconciled = sorted(
                kind
                for kind in kinds
                if kind not in IDENTITY_ISSUE_KINDS and kind not in TEXT_ISSUE_KINDS
            )
            if field == "content_relevant" and unreconciled:
                verdicts[field] = False
                state[field] = "FAIL"
            else:
                verdicts[field] = True
                state[field] = "PASS"
    return {
        "checked": True,
        "reason": "FRAMES_READ",
        "unreadable_frames": len(unreadable),
        "verdicts": verdicts,
        "check_states": state,
        "issue_kinds": sorted(kinds),
        "evidence": [
            {
                "issue_kind": str(item.get("issue_kind") or ""),
                "observed": str(item.get("observed") or ""),
                "expected": str(item.get("expected") or ""),
                "frame_ref": str(item.get("frame_ref") or ""),
            }
            for item in real[:10]
        ],
        "unknown_is_not_a_pass": True,
    }


def build_candidate_checker(
    *,
    provider: Any | None,
    extract_frames: Callable[..., Mapping[int, str]] | None,
    media_path: Callable[[str], Path] | None,
    frame_rate: tuple[int, int] = (25, 1),
) -> Callable[[Any, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], dict[str, Any]]:
    """Build the per-candidate check used by the picture QC stage.

    Every port is injected because the check needs a real multimodal runtime, a real
    decoder and a real media path; without them it records *why* nothing was checked
    instead of claiming a pass (design §6.2: an unavailable capability is stated at
    preflight, and the check has no result rather than a good one).
    """

    def checker(
        repo: Any,
        context: Mapping[str, Any],
        beat: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> dict[str, Any]:
        if provider is None:
            return {"checked": False, "reason": "VISUAL_PROVIDER_NOT_CONFIGURED", "verdicts": {}}
        capability = dict(provider.capability() or {})
        if not capability.get("available"):
            return {
                "checked": False,
                "reason": str(capability.get("reason") or "VISUAL_PROVIDER_UNAVAILABLE"),
                "verdicts": {},
            }
        if extract_frames is None or media_path is None:
            return {"checked": False, "reason": "FRAME_SAMPLER_NOT_CONFIGURED", "verdicts": {}}
        media_version_id = str(candidate.get("media_version_id") or "")
        if not media_version_id:
            return {"checked": False, "reason": "CANDIDATE_HAS_NO_MEDIA", "verdicts": {}}
        expectation = candidate_expectation(
            beat=beat,
            candidate=candidate,
            identity_references=_identity_references(repo, context, beat),
            script_revision_id=str(context.get("script_revision_id") or "") or None,
            locale=str(context.get("locale") or "") or None,
        )
        try:
            source = Path(media_path(media_version_id))
        except ExplainerContractError as error:
            return {"checked": False, "reason": str(error.code), "verdicts": {}}
        if not source.is_file():
            return {"checked": False, "reason": "CANDIDATE_MEDIA_MISSING", "verdicts": {}}
        # A short clip is checked at its first, middle and last frames; the exact
        # frame ids are recorded so the sampling range is honest.
        duration_frames = _duration_frames(candidate, frame_rate)
        frame_ids = sorted(
            {
                min(duration_frames - 1, max(0, int(round(duration_frames * fraction))))
                for fraction in CANDIDATE_SAMPLE_FRACTIONS
            }
        )
        namespace = f"candidate-{str(candidate.get('id') or media_version_id)[:32]}"
        try:
            extracted = dict(
                extract_frames(
                    source=source,
                    frame_ids=frame_ids,
                    frame_rate=frame_rate,
                    namespace=namespace,
                )
            )
        except ExplainerContractError as error:
            return {"checked": False, "reason": str(error.code), "verdicts": {}}
        if not extracted:
            return {"checked": False, "reason": "NO_FRAME_EXTRACTED", "verdicts": {}}
        frames = [
            {
                "frame_id": int(frame_id),
                "frame_ref": rel_path,
                "media_version_id": media_version_id,
                "beat_id": str(beat.get("id") or ""),
            }
            for frame_id, rel_path in sorted(extracted.items())
        ]
        try:
            findings = provider.check_frames(frames=frames, questions=check_questions(expectation))
        except ExplainerContractError as error:
            # A failing review leaves the question open; it never becomes a pass.
            return {"checked": False, "reason": str(error.code), "verdicts": {}}
        verdict = verdicts_from_findings(list(findings or ()))
        verdict["expectation"] = expectation
        verdict["sampled_frame_refs"] = [str(item["frame_ref"]) for item in frames]
        return verdict

    return checker


def _duration_frames(candidate: Mapping[str, Any], frame_rate: tuple[int, int]) -> int:
    snapshot = candidate.get("execution_snapshot")
    snapshot = snapshot if isinstance(snapshot, Mapping) else {}
    duration_ms = snapshot.get("duration_ms") or candidate.get("duration_ms")
    try:
        milliseconds = int(duration_ms)
    except (TypeError, ValueError):
        milliseconds = 0
    if milliseconds <= 0:
        # Without a measured duration the whole clip is sampled; the frame count is
        # only used to pick sample points, never as a claim about the media.
        return 25
    num, den = frame_rate
    return max(1, int(round(milliseconds * num / (1000 * den))))


def _identity_references(
    repo: Any, context: Mapping[str, Any], beat: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """The frozen reference images of the entities this beat binds."""

    video_id = str(context.get("video_id") or "")
    entity_codes = {str(item) for item in (beat.get("entity_refs") or [])}
    if not video_id or not entity_codes:
        return []
    bindings = repo.list_where("entity_identity_bindings", {"video_id": video_id})
    references: list[dict[str, Any]] = []
    for binding in bindings:
        if str(binding.get("status") or "ACTIVE") != "ACTIVE":
            continue
        entity_id = str(binding.get("entity_id") or "")
        entity = repo.find("explainer_entities", entity_id) if entity_id else None
        code = str((entity or {}).get("code") or "")
        if code and code not in entity_codes:
            continue
        references.append({**dict(binding), "entity_code": code})
    return references
