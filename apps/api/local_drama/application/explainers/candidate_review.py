"""Strict ``candidate-review.v1`` boundary for the semantic picture check.

Design reference: 解说工厂-体验功能与代码改造实施规范 §C5.5 and `prompts/05-candidate-review.*`.

Why this module exists
----------------------
``visual_qc.LocalLlmVisualQcProvider`` already reads real sampled frames and asks the
local multimodal model a structured question, and ``candidate_qc`` already builds the
frozen expectation from the beat and candidate rows.  What was missing is the *strict*
contract at the model boundary: the provider accepted a loose ``{"frames": [...]}``
shape, so a reply could carry a frame id nobody sent, a criterion that was never asked
for, or an ``OBSERVED`` motion claim based on a single still — and the adoption gate
would then read a verdict that the contract would have rejected.

This module is that boundary.  It performs, in order:

1. **Shape**: the reply must validate as ``CandidateReviewV1`` (additionalProperties
   false, fixed enums, bounded lengths, fixed ``schema_version``).
2. **Identity**: the report's ``candidate_id`` must be the candidate that was checked.
3. **Frames**: every returned ``frame_id`` must be a frame that was actually sent, with
   no duplicates — ``assert_frame_ids_in_manifest``.
4. **Coverage of criteria**: every criterion that was asked about must appear for every
   frame; a criterion the model skipped becomes ``UNKNOWN`` rather than disappearing.
5. **Motion honesty**: with a single checked image the observation may not be
   ``OBSERVED`` — ``assert_motion_observation``.
6. **Issues**: only allowed issue kinds are mapped to findings; an unrecognised label is
   preserved as an explicit unidentified issue instead of being dropped (§3.6).

The output keeps both halves: the validated contract object (for audit) and the
``findings`` list the existing QC/adoption code already understands, so this is a real
integration rather than a parallel verdict.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from local_drama.application.explainers.contracts_v2 import (
    CANDIDATE_REVIEW_SCHEMA_VERSION,
    CandidateReviewV1,
    assert_frame_ids_in_manifest,
    assert_motion_observation,
    validate_contract,
)
from local_drama.domain.explainers.contracts import ExplainerContractError

__all__ = [
    "CANDIDATE_REVIEW_CRITERIA",
    "KNOWN_ISSUE_KINDS",
    "REVIEW_VERDICTS",
    "build_review_request",
    "findings_from_review",
    "verdicts_from_review",
    "validate_review_response",
]


#: The criteria the request asks about, in the fixed order the design lists them.
CANDIDATE_REVIEW_CRITERIA: tuple[str, ...] = (
    "CHARACTER_COUNT",
    "REFERENCE_CONSISTENCY",
    "SCENE_CONTINUITY",
    "KEY_PROP",
    "MAIN_ACTION",
    "DISTORTION",
    "UNEXPECTED_TEXT",
)

REVIEW_VERDICTS: tuple[str, ...] = ("PASS", "FAIL", "UNKNOWN")

#: Issue labels this product can act on.  Anything else is preserved as an
#: explicitly unidentified issue instead of being silently discarded.
KNOWN_ISSUE_KINDS: frozenset[str] = frozenset(
    {
        "PROP_MISSING",
        "LIGHT_SOURCE_OFF",
        "ERA_INCONSISTENT",
        "ACTION_MISSING",
        "CONTENT_MISMATCH",
        "WRONG_SCENE",
        "MUST_BE_MOTION_VIOLATED",
        "WRONG_CHARACTER",
        "EXTRA_PERSON",
        "CHARACTER_COUNT_MISMATCH",
        "IDENTITY_MISMATCH",
        "TEXT_ILLEGIBLE",
        "TEXT_UNREADABLE",
        "TEXT_TRUNCATED",
        "TEXT_OVERFLOWS_SAFE_AREA",
        "VISUAL_FRAME_UNAVAILABLE",
    }
)

#: A verdict that is not one of the three fixed values is a contract violation.
_UNIDENTIFIED_ISSUE_KIND = "VISUAL_UNIDENTIFIED_ISSUE"


def build_review_request(
    *,
    candidate_id: str,
    candidate_snapshot: Mapping[str, Any],
    frame_manifest: Sequence[Mapping[str, Any]],
    review_basis: Mapping[str, Any],
    criteria: Sequence[str] = CANDIDATE_REVIEW_CRITERIA,
) -> dict[str, Any]:
    """The frozen request the check is asked with (design §C6.5 User block).

    ``frame_manifest`` is the *actual* image order: role/order/id per frame, so the
    report can be checked against what was really sent.  Sending a media id in text
    instead of the image itself is not a visual check, and this manifest is what makes
    that difference provable.
    """

    return {
        "schema_version": CANDIDATE_REVIEW_SCHEMA_VERSION,
        "candidate_id": str(candidate_id),
        "candidate_snapshot": dict(candidate_snapshot),
        "image_manifest": [dict(item) for item in frame_manifest],
        "review_basis": dict(review_basis),
        "criteria": [str(item) for item in criteria],
        "checked_frame_ids": sorted(int(item["frame_id"]) for item in frame_manifest),
    }


def validate_review_response(
    raw: Any,
    *,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one provider reply against the frozen request.

    Raises :class:`ExplainerContractError` when the reply cannot be trusted.  The
    caller must treat that as UNCHECKED — never as a pass and never as a failure of the
    picture (design §C7: an unanswered question stays open).
    """

    report = validate_contract("candidate-review.v1", raw)
    if not isinstance(report, CandidateReviewV1):  # pragma: no cover - defensive
        raise ExplainerContractError("SCHEMA_INVALID", "候选检查报告类型不正确")
    expected_candidate = str(request.get("candidate_id") or "")
    if str(report.candidate_id) != expected_candidate:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            "候选检查报告不属于本次检查的候选",
            {"candidate_id": str(report.candidate_id), "expected": expected_candidate},
        )
    manifest = [int(item["frame_id"]) for item in (request.get("image_manifest") or [])]
    checked = assert_frame_ids_in_manifest(report.frame_results, frame_manifest=manifest)
    criteria = [str(item) for item in (request.get("criteria") or CANDIDATE_REVIEW_CRITERIA)]
    missing: list[dict[str, Any]] = []
    for frame in report.frame_results:
        reported = {str(check.criterion) for check in frame.checks}
        for criterion in criteria:
            if criterion not in reported:
                missing.append({"frame_id": int(frame.frame_id), "criterion": criterion})

    # A single still can never prove the motion happened (§C5.5).
    motion = assert_motion_observation(
        report.motion_observation,
        checked_image_count=max(1, len(manifest)),
        code="SCHEMA_INVALID",
    )
    return {
        "candidate_id": expected_candidate,
        "contract": report,
        "frame_results": [item.model_dump() for item in report.frame_results],
        "checked_frame_ids": checked,
        "motion_observation": motion,
        "criteria_checked": criteria,
        "criteria_never_reported": missing,
        "schema_version": report.schema_version,
    }


def findings_from_review(
    review: Mapping[str, Any],
    *,
    provider_identity: Mapping[str, Any] | None = None,
    frame_refs: Mapping[int, str] | None = None,
) -> list[dict[str, Any]]:
    """Map a validated report onto the finding records the QC gate already reads.

    Only *actionable* findings are produced: a criterion that came back ``FAIL``, or an
    issue the model actually reported.  A criterion the model could not judge is **not**
    a defect and must not be listed as one — the previous version turned every unreported
    criterion into a "problem", which put 85 non-issues in front of the user for one
    6-minute film.  ``UNKNOWN`` is carried by :func:`verdicts_from_review` instead, which
    is what the adoption gate reads and what keeps the candidate out of automatic
    adoption without inventing a problem.
    """

    identity = dict(provider_identity or {})
    refs = dict(frame_refs or {})
    findings: list[dict[str, Any]] = []
    for frame in review.get("frame_results") or []:
        frame_id = int(frame["frame_id"])
        frame_ref = refs.get(frame_id, str(frame_id))
        for check in frame.get("checks") or []:
            criterion = str(check["criterion"])
            result = str(check.get("result") or "")
            if result not in REVIEW_VERDICTS:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "候选检查结果不在 PASS/FAIL/UNKNOWN 内",
                    {"criterion": criterion, "result": result},
                )
            if result != "FAIL":
                continue
            findings.append(
                {
                    "frame_ref": frame_ref,
                    "issue_kind": criterion,
                    "observed": str(check.get("evidence") or ""),
                    "expected": criterion,
                    "confidence": 1.0,
                    "unknown_reason": None,
                    "provider_id": identity.get("provider_id"),
                    "model_revision": identity.get("model_revision"),
                    "criterion": criterion,
                    "criterion_result": result,
                }
            )
        for issue in frame.get("issues") or []:
            kind = str(issue.get("issue_kind") or "").strip().upper()
            unrecognised = kind not in KNOWN_ISSUE_KINDS
            findings.append(
                {
                    "frame_ref": frame_ref,
                    "issue_kind": _UNIDENTIFIED_ISSUE_KIND if unrecognised else kind,
                    "observed": str(issue.get("observed") or ""),
                    "expected": str(issue.get("expected") or ""),
                    "confidence": issue.get("confidence"),
                    "unknown_reason": str(issue.get("reason") or "") or None,
                    "provider_id": identity.get("provider_id"),
                    "model_revision": identity.get("model_revision"),
                    "reported_issue_kind": kind,
                    "issue_kind_recognised": not unrecognised,
                }
            )
        unknown_reason = str(frame.get("unknown_reason") or "").strip()
        has_unknown_check = any(
            str(check.get("result")) == "UNKNOWN" for check in frame.get("checks") or []
        )
        if unknown_reason and not frame.get("issues") and not has_unknown_check:
            # A frame the model could not judge at all still has to be visible: it blocks
            # automatic adoption, and the reason is recorded without inventing a defect.
            findings.append(
                {
                    "frame_ref": frame_ref,
                    "issue_kind": "VISUAL_UNKNOWN",
                    "observed": "；".join(str(item) for item in (frame.get("observations") or [])),
                    "expected": "",
                    "confidence": None,
                    "unknown_reason": unknown_reason,
                    "provider_id": identity.get("provider_id"),
                    "model_revision": identity.get("model_revision"),
                }
            )
    return findings


#: Which gate field each review criterion speaks to.  A criterion the review could not
#: judge leaves its field out of the verdict, which the adoption gate reads as UNKNOWN.
CRITERION_GATE_FIELDS: Mapping[str, str] = {
    "CHARACTER_COUNT": "identity_ok",
    "REFERENCE_CONSISTENCY": "identity_ok",
    "KEY_PROP": "content_relevant",
    "MAIN_ACTION": "content_relevant",
    "SCENE_CONTINUITY": "content_relevant",
    "DISTORTION": "content_relevant",
    "UNEXPECTED_TEXT": "text_readable",
}


def verdicts_from_review(review: Mapping[str, Any]) -> dict[str, Any]:
    """The tri-state verdicts the picture-QC gate reads from a validated report.

    Every asked criterion contributes exactly one state, so a criterion that was never
    answered shows up as ``UNKNOWN`` — visible and blocking for automatic adoption —
    rather than as an invented problem or as a silent pass.
    """

    criteria_state: dict[str, str] = {}
    for frame in review.get("frame_results") or []:
        for check in frame.get("checks") or []:
            criterion = str(check["criterion"])
            result = str(check.get("result") or "UNKNOWN")
            previous = criteria_state.get(criterion)
            # FAIL is sticky: one failing frame must not be averaged away by another.
            if previous == "FAIL" or result == "FAIL":
                criteria_state[criterion] = "FAIL"
            elif previous == "UNKNOWN" or result == "UNKNOWN":
                criteria_state[criterion] = "UNKNOWN"
            else:
                criteria_state[criterion] = "PASS"
    for criterion in review.get("criteria_never_reported") or []:
        criteria_state.setdefault(str(criterion["criterion"]), "UNKNOWN")

    verdicts: dict[str, Any] = {}
    states: dict[str, str] = {}
    # Several criteria can speak to the same gate field, so the field state follows the
    # same precedence as the criteria themselves: a FAIL or an UNKNOWN from any criterion
    # is never downgraded to PASS by another criterion that happened to pass.
    rank = {"FAIL": 0, "UNKNOWN": 1, "PASS": 2}
    for criterion, result in criteria_state.items():
        field = CRITERION_GATE_FIELDS.get(criterion)
        if field is None:
            continue
        if field not in states or rank[result] < rank[states[field]]:
            states[field] = result
        if result == "FAIL":
            verdicts[field] = False
        elif result == "PASS":
            verdicts.setdefault(field, True)
    for field, result in states.items():
        if result == "UNKNOWN":
            # No verdict entry for UNKNOWN: the gate treats a missing measurement as
            # UNKNOWN, which is exactly the honest outcome.  A FAIL keeps its False.
            verdicts.pop(field, None)
    return {
        "checked": True,
        "reason": "FRAMES_READ",
        "verdicts": verdicts,
        "check_states": states,
        "criteria_states": criteria_state,
        "issue_kinds": sorted(
            {str(item.get("issue_kind") or "") for item in findings_from_review(review) if item.get("issue_kind")}
        ),
        "unknown_is_not_a_pass": True,
        "motion_observation": str(review.get("motion_observation") or ""),
    }
