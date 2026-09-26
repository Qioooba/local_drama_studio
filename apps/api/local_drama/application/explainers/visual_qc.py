"""Local multimodal visual QC provider over the offline LLM capability.

This is the adapter behind design §18.1's semantic check layer and §10.2's visual
review smoke contract.  It really reads images: frames are encoded and passed to
the configured multimodal model's image input, and the answer is validated
against a closed schema.

Boundaries this module keeps:

* **Honest capability.**  ``capability()`` reports ``available=False`` when no
  multimodal profile is configured, so the semantic layer stays ``UNCHECKED``
  rather than becoming a silent pass.  A text-only chat model is not accepted as
  a visual reviewer.
* **UNKNOWN is a result.**  A frame the model cannot judge comes back with
  ``unknown_reason`` set and no fabricated issue; the caller records it as
  unknown instead of as a pass.
* **No self-grading of facts.**  The provider reports what it observed in the
  picture.  Numbers, sources and timeline correctness stay with the deterministic
  fact detector; this provider never decides whether a claim is true.
* **One bounded call per batch.**  Frames are sent in bounded batches so a 30
  minute film never lands in one context, and every frame id that was actually
  inspected is reported back.
"""

from __future__ import annotations

import base64
import json
import mimetypes
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.domain.explainers.contracts import ExplainerContractError

#: Bounded batch so one request never carries the whole film.
MAX_FRAMES_PER_BATCH = 6
#: Bounded image payload per frame (a review frame, not a master).
MAX_FRAME_BYTES = 4 * 1024 * 1024

_PROVIDER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["frames"],
    "properties": {
        "frames": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["frame_id", "observations"],
                "properties": {
                    "frame_id": {"type": "integer"},
                    "character_count": {"type": "integer"},
                    "observations": {"type": "array", "items": {"type": "string"}},
                    "issues": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["issue_kind", "observed"],
                            "properties": {
                                "issue_kind": {"type": "string"},
                                "observed": {"type": "string"},
                                "expected": {"type": "string"},
                                "confidence": {"type": "number"},
                            },
                        },
                    },
                    "unknown_reason": {"type": "string"},
                },
            },
        }
    },
}

_VISUAL_SYSTEM = (
    "你是本机离线解说工厂的视觉审片检查器。只输出 JSON。"
    "你只能报告在图像里实际看到的内容；看不清或无法判断时必须写 unknown_reason，不得猜测。"
    "不要判断史实真伪，也不要评价影片好不好看，只报告与检查问题相关的可见事实。"
    "可读文字如果模糊不清，按无法判断处理。"
)


def _encode_frame(path: Path) -> str | None:
    """Base64 data URL for one frame, or ``None`` when it cannot be read."""

    try:
        if not path.is_file():
            return None
        payload = path.read_bytes()
    except OSError:
        return None
    if not payload or len(payload) > MAX_FRAME_BYTES:
        return None
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    if not mime.startswith("image/"):
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"


class LocalLlmVisualQcProvider:
    """``VisualQcProvider`` over the configured offline multimodal model.

    ``basis`` is the frozen expectation handed to the model (the reference frames
    and the declared identities/scenes of the edition); the provider never invents
    an expectation of its own.
    """

    def __init__(
        self,
        *,
        client_factory: Callable[[], Any],
        frame_path_resolver: Callable[[Mapping[str, Any]], Path | None],
        basis: Mapping[str, Any] | None = None,
        provider_id: str = "local-multimodal",
        model_revision: str = "",
        reads_video: bool = False,
        batch_size: int = MAX_FRAMES_PER_BATCH,
        strict_review: Mapping[str, Any] | None = None,
        strict_required: bool = False,
        strict_review_factory: Callable[[Mapping[str, Any], Sequence[Mapping[str, Any]]], Mapping[str, Any]]
        | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._frame_path_resolver = frame_path_resolver
        self.basis = dict(basis or {})
        self.provider_id = provider_id
        self.model_revision = model_revision
        self.reads_video = reads_video
        self.batch_size = max(1, min(int(batch_size), MAX_FRAMES_PER_BATCH))
        # When a frozen request is supplied, the reply is validated against the strict
        # ``candidate-review.v1`` contract before any finding is derived (design §C5.5).
        # ``strict_required`` decides what a contract violation means: for a候选 check it
        # means UNCHECKED (never a pass), which is why the caller asks for it.
        self.strict_review = dict(strict_review) if strict_review else None
        self.strict_required = bool(strict_required)
        # The per-candidate factory is the practical form: one provider instance is
        # reused for every candidate of a batch, and each check builds its own frozen
        # request from the candidate id plus the frames actually sent.
        self.strict_review_factory = strict_review_factory

    # ------------------------------------------------------------------ protocol
    def capability(self) -> dict[str, Any]:
        """Report whether a real multimodal profile is usable right now."""

        try:
            client = self._client_factory()
        except DomainRuleError as error:
            return {
                "available": False,
                "provider_id": self.provider_id,
                "model_revision": self.model_revision or None,
                "reads_images": False,
                "reads_video": False,
                "reason": error.code,
            }
        except ExplainerContractError as error:
            return {
                "available": False,
                "provider_id": self.provider_id,
                "model_revision": self.model_revision or None,
                "reads_images": False,
                "reads_video": False,
                "reason": error.code,
            }
        multimodal = bool(getattr(client, "supports_images", True))
        return {
            "available": bool(getattr(client, "model", None)) and multimodal,
            "provider_id": self.provider_id,
            "model_revision": self.model_revision or str(getattr(client, "model", "") or ""),
            "reads_images": multimodal,
            "reads_video": self.reads_video,
            "reason": None if multimodal else "MODEL_DOES_NOT_ACCEPT_IMAGES",
        }

    def check_frames(
        self, *, frames: Sequence[dict[str, Any]], questions: Sequence[str]
    ) -> list[dict[str, Any]]:
        """Inspect the given frames and return the anomalies actually observed."""

        capability = self.capability()
        if not capability["available"]:
            raise ExplainerContractError(
                "CAPABILITY_UNAVAILABLE",
                "没有可用的本地多模态审片模型，语义检查保持未检查",
                {"reason": capability.get("reason")},
            )
        client = self._client_factory()
        findings: list[dict[str, Any]] = []
        answered_batches = 0
        last_error: ExplainerContractError | None = None
        for batch in _batched(list(frames), self.batch_size):
            try:
                findings.extend(self._check_batch(client, batch=batch, questions=questions, capability=capability))
                answered_batches += 1
                continue
            except ExplainerContractError as error:
                # A batch the model server could not answer marks *its* frames
                # unchecked and the pass continues.  Raising here discarded the
                # batches that had already been read: the 1962 film's 13-batch
                # semantic pass reported NOT_RUN and zero coverage because one
                # request met a restarting model server, while twelve batches of
                # real observations were available.  When *no* batch was answered the
                # layer really did not run, and that is still raised.
                last_error = error
                for frame in batch:
                    findings.append(
                        {
                            "frame_id": frame.get("frame_id"),
                            "frame_ref": str(frame.get("frame_ref") or frame.get("frame_id")),
                            "issue_kind": "SEMANTIC_QC_UNCHECKED",
                            "observed": f"该帧未被模型读到：{str(error)[:200]}",
                            "expected": "每一帧都由真实读图模型实际检查",
                            "confidence": None,
                            "unknown_reason": str(getattr(error, "code", "") or "VISUAL_QC_PROVIDER_ERROR"),
                        }
                    )
        if answered_batches == 0 and last_error is not None:
            raise last_error
        return findings

    # ------------------------------------------------------------------ internals
    def _check_batch(
        self,
        client: Any,
        *,
        batch: Sequence[Mapping[str, Any]],
        questions: Sequence[str],
        capability: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        images: list[str] = []
        described: list[dict[str, Any]] = []
        unreadable: list[dict[str, Any]] = []
        for frame in batch:
            path = self._frame_path_resolver(frame)
            data_url = _encode_frame(path) if path is not None else None
            if data_url is None:
                unreadable.append(
                    {
                        "frame_ref": str(frame.get("frame_ref") or frame.get("frame_id")),
                        "issue_kind": "VISUAL_FRAME_UNAVAILABLE",
                        "observed": "抽帧文件缺失或超出体积上限，无法读取该帧",
                        "expected": "抽帧文件存在且可解码",
                        "confidence": 0.0,
                        "unknown_reason": "FRAME_NOT_READABLE",
                    }
                )
                continue
            images.append(data_url)
            described.append({"frame_id": int(frame["frame_id"]), "frame_ref": str(frame.get("frame_ref") or "")})
        if not images:
            return unreadable
        user = (
            "检查下面这些抽帧，逐帧回答 JSON。\n"
            f"期望依据（冻结快照，不得改写）：{json.dumps(self.basis, ensure_ascii=False)[:6000]}\n"
            f"检查问题：{json.dumps([str(item) for item in questions], ensure_ascii=False)}\n"
            f"帧清单：{json.dumps(described, ensure_ascii=False)}\n\n"
            "每个帧对象返回：frame_id、character_count（你实际看到的人物数量，看不清就省略）、"
            "observations（你实际看到的关键事实，每条一句）、"
            "issues（仅在确实发现问题时给出；issue_kind 用英文大写下划线，例如 "
            "CHARACTER_COUNT_MISMATCH / WRONG_CHARACTER / PROP_MISSING / TEXT_ILLEGIBLE / "
            "LIGHT_SOURCE_OFF / EXTRA_PERSON / ERA_INCONSISTENT；"
            "observed 写你看到什么，expected 写依据要求什么，confidence 取 0–1）、"
            "unknown_reason（无法判断时写明原因）。\n"
            "不要为每一帧都编造问题；没有问题就返回空数组。"
        )
        try:
            result = client.chat_json(
                _VISUAL_SYSTEM,
                user,
                images,
                json_schema=_PROVIDER_SCHEMA,
                inference_options={"temperature": 0.1, "top_p": 0.9, "max_tokens": 2_500, "num_ctx": 32_768},
            )
        except Exception as error:  # noqa: BLE001 - a failing call is UNCHECKED, never a pass
            # The provider reports the underlying failure's code, message and details:
            # "the multimodal call failed" alone cannot tell an operator whether the
            # model server is down, the payload was refused, or the reply did not
            # match the schema — and each of those needs a different next step.
            raise ExplainerContractError(
                "CAPABILITY_UNAVAILABLE",
                "本地多模态审片调用失败，语义检查保持未检查",
                {
                    "reason": type(error).__name__,
                    "code": str(getattr(error, "code", "") or ""),
                    "message": str(error)[:300],
                    "detail": str(getattr(error, "details", "") or "")[:300],
                },
            ) from error
        if self.strict_review is not None or self.strict_review_factory is not None:
            request = self._strict_request_for(list(batch))
            if self.strict_required and request is None:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "严格候选检查缺少冻结请求，语义检查保持未检查",
                )
            return unreadable + self._strict_findings(result, described=described, request=request)
        return unreadable + self._normalise(result, batch=batch, capability=capability)

    def _strict_request_for(self, frames: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
        """The frozen ``candidate-review.v1`` request for these frames, if one applies."""

        if self.strict_review_factory is not None:
            candidate_id = str((frames[0] if frames else {}).get("candidate_id") or "")
            if not candidate_id:
                return None
            manifest = [
                {
                    "frame_id": int(frame["frame_id"]),
                    "frame_ref": str(frame.get("frame_ref") or ""),
                    "role": str(frame.get("role") or "CANDIDATE_FRAME"),
                }
                for frame in frames
            ]
            built = self.strict_review_factory({"candidate_id": candidate_id}, manifest)
            return dict(built) if built else None
        return dict(self.strict_review or {}) or None

    def _strict_findings(
        self,
        result: Any,
        *,
        described: Sequence[Mapping[str, Any]],
        request: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Validate the reply against ``candidate-review.v1`` and map it to findings.

        A reply that cannot be validated is refused here.  In strict mode that refusal is
        raised, so the caller records UNCHECKED — an unverifiable answer must never
        become a PASS, and an unrecognised shape must never silently lose a FAIL.
        """

        from local_drama.application.explainers.candidate_review import (
            findings_from_review,
            validate_review_response,
            verdicts_from_review,
        )

        frozen = dict(request or self.strict_review or {})
        review = validate_review_response(result, request=frozen)
        findings = findings_from_review(
            review,
            provider_identity={"provider_id": self.provider_id, "model_revision": self.model_revision},
            frame_refs={int(item["frame_id"]): str(item.get("frame_ref") or "") for item in described},
        )
        # A criterion the review could not judge is carried as a gate state, not as a
        # finding: only real FAILs and reported issues reach the user as problems.
        validated = verdicts_from_review(review)
        self.last_verdicts = {
            "checked": bool(validated.get("checked")),
            "reason": str(validated.get("reason") or ""),
            "verdicts": dict(validated.get("verdicts") or {}),
            "check_states": dict(validated.get("check_states") or {}),
            "issue_kinds": list(validated.get("issue_kinds") or []),
            "unknown_is_not_a_pass": True,
        }
        return findings

    def _normalise(
        self, result: Any, *, batch: Sequence[Mapping[str, Any]], capability: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        if not isinstance(result, Mapping):
            raise ExplainerContractError("SCHEMA_INVALID", "多模态审片返回的顶层不是对象")
        known = {int(frame["frame_id"]): frame for frame in batch}
        findings: list[dict[str, Any]] = []
        for entry in result.get("frames") or []:
            if not isinstance(entry, Mapping):
                continue
            try:
                frame_id = int(entry.get("frame_id"))
            except (TypeError, ValueError):
                continue
            frame = known.get(frame_id)
            if frame is None:
                # A frame id the caller never sent is not evidence about the film.
                continue
            frame_ref = str(frame.get("frame_ref") or frame_id)
            unknown_reason = str(entry.get("unknown_reason") or "").strip()
            for issue in entry.get("issues") or []:
                if not isinstance(issue, Mapping):
                    continue
                confidence = issue.get("confidence")
                try:
                    confidence_value = float(confidence) if confidence is not None else None
                except (TypeError, ValueError):
                    confidence_value = None
                if confidence_value is not None and not 0.0 <= confidence_value <= 1.0:
                    confidence_value = None
                findings.append(
                    {
                        "frame_ref": frame_ref,
                        "issue_kind": str(issue.get("issue_kind") or "VISUAL_UNSPECIFIED"),
                        "observed": str(issue.get("observed") or ""),
                        "expected": str(issue.get("expected") or ""),
                        "confidence": confidence_value,
                        "unknown_reason": None,
                        "provider_id": capability.get("provider_id"),
                        "model_revision": capability.get("model_revision"),
                    }
                )
            if unknown_reason and not entry.get("issues"):
                findings.append(
                    {
                        "frame_ref": frame_ref,
                        "issue_kind": "VISUAL_UNKNOWN",
                        "observed": str("；".join(str(item) for item in (entry.get("observations") or []))),
                        "expected": "",
                        "confidence": None,
                        "unknown_reason": unknown_reason,
                        "provider_id": capability.get("provider_id"),
                        "model_revision": capability.get("model_revision"),
                    }
                )
        return findings


def _batched(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def build_visual_qc_provider(
    database: Any,
    settings: Any,
    *,
    frame_root: Path,
    basis: Mapping[str, Any] | None = None,
    strict_review: Mapping[str, Any] | None = None,
    strict_review_factory: Any | None = None,
) -> LocalLlmVisualQcProvider:
    """Build the semantic provider over the configured multimodal profile.

    ``strict_review`` optionally supplies the frozen ``candidate-review.v1`` request for
    the candidate being checked; when it is present the reply is contract-validated
    before any verdict is derived (design §C5.5).
    """

    def client_factory() -> Any:
        from local_drama.application.local_llm import LocalLLMService

        service = LocalLLMService(database, settings)
        with database.connect() as connection:
            row = connection.execute(
                """SELECT epv.id, epv.capability FROM execution_profile_versions epv
                JOIN execution_profiles ep ON ep.id=epv.execution_profile_id
                WHERE epv.capability IN ('QC_VISUAL','LLM_STORY_PARSE') AND epv.status='PUBLISHED'
                ORDER BY CASE epv.capability WHEN 'QC_VISUAL' THEN 0 ELSE 1 END,
                         epv.updated_at DESC, epv.version_no DESC, epv.id ASC LIMIT 1"""
            ).fetchone()
        if row is None:
            raise DomainRuleError(
                "VISUAL_QC_PROFILE_MISSING",
                "没有已发布的视觉审片或本地多模态方案，语义检查保持未检查",
            )
        return service.client(profile_version_id=str(row["id"]))

    def resolve(frame: Mapping[str, Any]) -> Path | None:
        rel_path = frame.get("rel_path")
        if not rel_path:
            return None
        candidate = (frame_root / str(rel_path)).resolve()
        try:
            candidate.relative_to(frame_root.resolve())
        except ValueError:
            return None
        return candidate

    return LocalLlmVisualQcProvider(
        client_factory=client_factory,
        frame_path_resolver=resolve,
        basis=basis,
        strict_review=strict_review,
        strict_review_factory=strict_review_factory,
    )
