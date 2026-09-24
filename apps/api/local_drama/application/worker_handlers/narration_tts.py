"""NARRATION_TTS job handler: synthesise one narration segment's ``spoken_text``.

The handler is the only place that turns a frozen narration segment into a real
``narration_takes`` row, and it does so through injected ports so it can run
against a fake runtime in tests and against the local worker in production:

* ``NarrationTtsRuntimePort`` performs the offline synthesis.
* ``NarrationMediaPort`` registers the produced file as a real media version and
  probes it with the existing FFprobe-backed media service.

Rules this handler enforces:

* It synthesises ``spoken_text`` only.  ``display_text`` is the subtitle writing
  and is never sent to a speech model.
* It validates that the selected voice/model snapshot is authorized *for
  narration*: the voice's declared locale must support the segment's locale, the
  snapshot purpose must include ``NARRATION``, and its licence state must be
  explicitly authorized (or explicitly test-only).  The legacy dialogue gate is
  not reused and ``LOCAL_TEST_ONLY`` is not assumed.
* A take is only inserted after a real probe returned a positive duration and a
  positive sample rate.  There is no path that writes a take with a zero or
  guessed duration, and no path that reports success without a media version.

What this handler deliberately does NOT do:

* It never touches the legacy dialogue tables (``dialogue_lines`` /
  ``dialogue_text_revisions``) or the legacy ``TTS_GENERATION`` snapshot gate.
* It never writes to ``narration_alignment_revisions``: alignment is a separate
  job family with its own authority.
* It never edits the script.  A changed segment produces a new revision through
  the narration service, never an in-place update here.
* It never selects a take on the human's behalf beyond ``selected=False``.
"""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any, Callable, Mapping, Protocol

from local_drama.application.explainers.narration import ExplainerNarrationService
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.explainers.contracts import normalize_locale, utc_now_iso
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository

AtomicWriter = Callable[[Path, Callable[[Path], object]], None]

#: Purposes a narration voice snapshot may declare.
REQUIRED_VOICE_PURPOSE = "NARRATION"

#: Licence states that authorize a narration take.
AUTHORIZED_LICENSE_STATUS: frozenset[str] = frozenset({"VERIFIED", "AUTHORIZED", "PUBLIC_DOMAIN"})
#: States that authorize a take only for local test listening, never delivery.
TEST_ONLY_LICENSE_STATUS: frozenset[str] = frozenset({"LOCAL_TEST_ONLY", "UNVERIFIED_TEST_ONLY"})


class NarrationTtsRuntimePort(Protocol):
    """Offline speech runtime contract for narration.

    A concrete adapter either declares a parameter or does not: ``speech_rate``
    is a *declared product parameter* and its applied value is recorded in the
    take's ``generation_json``.
    """

    def synthesize(
        self,
        *,
        text: str,
        voice_ref: str,
        model_ref: str,
        output: Path,
        speech_rate: float,
        timeout_seconds: int,
    ) -> Mapping[str, Any]:  # pragma: no cover - protocol boundary
        ...


class NarrationMediaPort(Protocol):
    """Real media registration plus FFprobe-backed verification."""

    def register_narration_audio(
        self,
        *,
        project_id: str,
        video_id: str,
        source_path: Path,
        label: str,
        take_no: int,
    ) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...

    def probe_audio(self, path: Path) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...


class WorkerPersistencePort(Protocol):
    """Minimum persistence abstraction: an open SQLite connection."""

    def connect(self) -> Any:  # pragma: no cover - protocol boundary
        ...


def _commit(connection: Any) -> None:
    commit = getattr(connection, "commit", None)
    if callable(commit):
        commit()


def _authorize_voice_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Reject a voice/model snapshot that is not authorized for narration.

    The failure is a structured blocker rather than a generic error so the API
    can surface the concrete missing fact to the operator.
    """

    locale = normalize_locale(str(snapshot.get("locale") or ""))
    supported = [normalize_locale(str(item)) for item in (snapshot.get("supported_locales") or [])]
    if not supported:
        raise DomainRuleError(
            "NARRATION_VOICE_LANGUAGE_UNSUPPORTED",
            "所选音色未声明支持的语言，无法确认可以朗读该旁白",
            {"locale": locale, "supported_locales": []},
        )
    if locale not in supported:
        raise DomainRuleError(
            "NARRATION_VOICE_LANGUAGE_UNSUPPORTED",
            "所选音色/模型不支持该旁白语言",
            {"locale": locale, "supported_locales": supported},
        )
    purposes = [str(item) for item in (snapshot.get("purposes") or [])]
    if REQUIRED_VOICE_PURPOSE not in purposes:
        raise DomainRuleError(
            "NARRATION_VOICE_PURPOSE_NOT_AUTHORIZED",
            "所选音色未声明可用于旁白解说用途",
            {"purposes": purposes, "required": REQUIRED_VOICE_PURPOSE},
        )
    license_status = str(snapshot.get("license_status") or "").upper()
    if license_status in AUTHORIZED_LICENSE_STATUS:
        return {"license_status": license_status, "delivery_authorized": True, "test_only": False}
    if license_status in TEST_ONLY_LICENSE_STATUS:
        if snapshot.get("test_only_acknowledged") is not True:
            raise DomainRuleError(
                "NARRATION_VOICE_LICENSE_NOT_AUTHORIZED",
                "该音色仅有本地试听许可；用于旁白成片前必须显式确认仅本地试听",
                {"license_status": license_status},
            )
        return {"license_status": license_status, "delivery_authorized": False, "test_only": True}
    raise DomainRuleError(
        "NARRATION_VOICE_LICENSE_NOT_AUTHORIZED",
        "旁白音色的许可范围未验证，不能生成正式旁白",
        {"license_status": license_status, "evidence": snapshot.get("license_evidence")},
    )


def _resolve_segment(
    connection: Any,
    *,
    job: Mapping[str, Any],
    semantic_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    repository = ExplainerRepository(connection)
    segment_id = str(semantic_inputs.get("narration_segment_id") or "")
    if segment_id:
        segment = repository.find("narration_segments", segment_id)
        if segment is None:
            raise DomainRuleError(
                "NARRATION_SEGMENT_NOT_FOUND", "旁白段落不存在", {"narration_segment_id": segment_id}
            )
        return segment
    canonical_id = str(semantic_inputs.get("canonical_segment_id") or "")
    locale = str(semantic_inputs.get("locale") or "")
    if not canonical_id or not locale:
        raise DomainRuleError(
            "NARRATION_JOB_SNAPSHOT_INVALID",
            "旁白合成 Job 必须提供 narration_segment_id，或同时提供 canonical_segment_id 与 locale",
            {"semantic_inputs": dict(semantic_inputs)},
        )
    candidate = repository.query_one(
        """
        SELECT s.* FROM narration_segments s
        JOIN explainer_videos v ON v.id = s.video_id
        WHERE v.project_id = ? AND s.canonical_segment_id = ? AND s.locale = ?
        ORDER BY s.ordinal DESC LIMIT 1
        """,
        (str(job["project_id"]), canonical_id, normalize_locale(locale)),
    )
    if candidate is None:
        raise DomainRuleError(
            "NARRATION_SEGMENT_NOT_FOUND",
            "按 canonical_segment_id 与语言找不到旁白段落",
            {"canonical_segment_id": canonical_id, "locale": locale, "project_id": str(job["project_id"])},
        )
    return repository.get("narration_segments", str(candidate["id"]))


def run_narration_tts_job(
    job: dict[str, Any],
    output_root: Path,
    *,
    work_root: Path,
    database: WorkerPersistencePort,
    narration_runtime: NarrationTtsRuntimePort,
    media_ops: NarrationMediaPort,
    atomic_writer: AtomicWriter,
    take_suffix: str = "wav",
    pre_synthesized: Path | None = None,
    pre_synthesized_receipt: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """Synthesise one narration segment and register the take.

    ``pre_synthesized`` carries a WAV a batch run already produced with the same
    authorised voice and text.  Every verification below still runs: the file is
    probed, hashed, registered as an immutable media version and re-checked against
    the segment hash, so a batched take is the same durable fact a per-segment
    synthesis would have produced.

    ``pre_synthesized_receipt`` is that batch run's own per-item receipt.  It is
    recorded instead of a hardcoded assumption: the previous code declared
    ``speed_applied_natively: True`` for every batched take regardless of what the
    runtime reported, so a requested speech rate that the model did not apply
    natively looked already-handled and was never post-processed.
    """
    snapshot = job["input_snapshot"]
    semantic_inputs = snapshot.get("semantic_inputs") or {}
    if not isinstance(semantic_inputs, Mapping):
        raise DomainRuleError("NARRATION_JOB_SNAPSHOT_INVALID", "旁白合成 Job 的 semantic_inputs 必须是对象")
    voice_snapshot = snapshot.get("voice_snapshot") or {}
    if not isinstance(voice_snapshot, Mapping):
        raise DomainRuleError("NARRATION_JOB_SNAPSHOT_INVALID", "旁白合成 Job 缺少 voice_snapshot")
    if str(job.get("subject_type") or "") not in {"NARRATION_SEGMENT", "EXPLAINER_NARRATION_SEGMENT"}:
        raise DomainRuleError(
            "NARRATION_JOB_SNAPSHOT_INVALID",
            "旁白合成 Job 的 subject_type 必须是 NARRATION_SEGMENT",
            {"subject_type": job.get("subject_type")},
        )
    model_ref = str(voice_snapshot.get("model_ref") or snapshot.get("model_ref") or "")
    if not model_ref:
        raise DomainRuleError("NARRATION_JOB_SNAPSHOT_INVALID", "旁白合成 Job 必须记录 model_ref")
    requested_rate = float(snapshot.get("speech_rate") or 1.0)
    if not 0.5 <= requested_rate <= 2.0:
        raise DomainRuleError(
            "NARRATION_SPEECH_RATE_INVALID", "旁白语速必须在 0.5–2.0 之间", {"speech_rate": requested_rate}
        )
    timeout_seconds = int(snapshot.get("timeout_seconds") or 120)

    with database.connect() as connection:
        segment = _resolve_segment(connection, job=job, semantic_inputs=semantic_inputs)
        repository = ExplainerRepository(connection)
        video = repository.get("explainer_videos", str(segment["video_id"]))
        if str(video.get("project_id")) != str(job["project_id"]):
            raise DomainRuleError(
                "NARRATION_PROJECT_MISMATCH",
                "旁白段落属于其他项目",
                {"segment_id": str(segment["id"]), "project_id": str(job["project_id"])},
            )
        if str(job.get("subject_id") or "") not in {"", str(segment["id"]), str(segment["canonical_segment_id"])}:
            raise DomainRuleError(
                "NARRATION_JOB_SNAPSHOT_INVALID",
                "旁白合成 Job 的 subject_id 与段落不一致",
                {"subject_id": job.get("subject_id"), "segment_id": str(segment["id"])},
            )
        script_revision = repository.get("explainer_script_revisions", str(segment["script_revision_id"]))
        segment_locale = normalize_locale(str(segment["locale"]))
        voice_snapshot_checked = _authorize_voice_snapshot({**dict(voice_snapshot), "locale": segment_locale})
        expected_hash = str(snapshot.get("segment_hash") or "")
        if expected_hash and expected_hash != str(segment["segment_hash"]):
            raise DomainRuleError(
                "NARRATION_SEGMENT_STALE",
                "旁白段落已变更，入队时的段落哈希与当前讲稿不一致",
                {
                    "segment_id": str(segment["id"]),
                    "expected_segment_hash": expected_hash,
                    "actual_segment_hash": str(segment["segment_hash"]),
                },
            )
        if "allow_draft_script" not in snapshot and str(script_revision.get("status")) != "FROZEN":
            raise DomainRuleError(
                "NARRATION_SCRIPT_NOT_FROZEN",
                "旁白合成必须基于已冻结的讲稿修订",
                {"script_revision_id": str(script_revision["id"]), "status": str(script_revision.get("status"))},
            )
        spoken_text = str(segment["spoken_text"])
        if not spoken_text.strip():
            raise DomainRuleError(
                "NARRATION_SEGMENT_EMPTY", "旁白段落没有可朗读的 spoken_text", {"segment_id": str(segment["id"])}
            )
        take_row = repository.query_one(
            "SELECT COALESCE(MAX(take_no), 0) AS current FROM narration_takes WHERE segment_id = ?",
            (str(segment["id"]),),
        )
        take_no = int(take_row["current"] if take_row is not None else 0) + 1
        voice_profile_version_id = voice_snapshot.get("voice_profile_version_id")

    raw_output = output_root / f"narration.{take_no}.{take_suffix}"
    runtime_result: Mapping[str, Any] = {}

    def synthesize(target: Path) -> None:
        nonlocal runtime_result
        runtime_result = narration_runtime.synthesize(
            text=spoken_text,
            voice_ref=str(voice_snapshot.get("voice_ref") or ""),
            model_ref=model_ref,
            output=target,
            speech_rate=requested_rate,
            timeout_seconds=timeout_seconds,
        ) or {}

    if pre_synthesized is not None:
        source = Path(pre_synthesized)
        if not source.is_file() or source.stat().st_size <= 0:
            raise DomainRuleError(
                "NARRATION_BATCH_OUTPUT_MISSING",
                "批量旁白合成没有产出该段落的音频文件",
                {"segment_id": str(segment["id"]), "path": source.name},
            )
        receipt = dict(pre_synthesized_receipt or {})
        runtime_result = {
            "runtime": "voxcpm2-subprocess-batch",
            "batch": True,
            "source": source.name,
            "network_used": False,
            "speed_applied_natively": bool(receipt.get("speed_applied_natively", False)),
            "requested_speed": receipt.get("requested_speed"),
            "tts_parameters": dict(receipt.get("parameters") or {}),
        }
        atomic_writer(raw_output, lambda target: shutil.copyfile(source, target))
    else:
        atomic_writer(raw_output, synthesize)
    if not raw_output.exists() or raw_output.stat().st_size <= 0:
        raise DomainRuleError(
            "NARRATION_TTS_OUTPUT_MISSING", "旁白合成没有产出可用的音频文件", {"path": raw_output.name}
        )
    probe = media_ops.probe_audio(raw_output)
    probe_status = str(probe.get("probe_status") or "")
    format_block = probe.get("format") if isinstance(probe.get("format"), Mapping) else {}
    streams = probe.get("streams") if isinstance(probe.get("streams"), list) else []
    audio_stream = next(
        (item for item in streams if isinstance(item, Mapping) and str(item.get("codec_type")) == "audio"),
        None,
    )
    duration_ms = _probe_duration_ms(probe)
    sample_rate_hz = _probe_sample_rate_hz(audio_stream)
    sample_count = _probe_sample_count(audio_stream)
    if probe_status != "PASS" or duration_ms is None or duration_ms <= 0 or not sample_rate_hz:
        raise DomainRuleError(
            "NARRATION_TTS_OUTPUT_INVALID",
            "旁白音频未通过本机 FFprobe 验证（时长或采样率缺失）",
            {
                "probe_status": probe_status,
                "duration_ms": duration_ms,
                "sample_rate_hz": sample_rate_hz,
                "format": dict(format_block),
            },
        )
    registered = media_ops.register_narration_audio(
        project_id=str(job["project_id"]),
        video_id=str(video["id"]),
        source_path=raw_output,
        label=f"narration-{segment['canonical_segment_id']}-take{take_no}",
        take_no=take_no,
    )
    for field in ("media_asset_id", "media_version_id", "sha256"):
        if not registered.get(field):
            raise DomainRuleError(
                "NARRATION_MEDIA_REGISTRATION_INCOMPLETE",
                "旁白音频未被登记为真实媒体版本",
                {"missing_field": field},
            )
    generation_json = {
        "schema_version": "localdrama.explainer.narration-take.v1",
        "synthesized_text_kind": "SPOKEN_TEXT",
        "spoken_text": spoken_text,
        "display_text_used_for_subtitles_only": True,
        "voice_snapshot": {
            "voice_profile_version_id": voice_profile_version_id,
            "voice_ref": str(voice_snapshot.get("voice_ref") or ""),
            "model_ref": model_ref,
            "license_status": voice_snapshot_checked["license_status"],
            "delivery_authorized": voice_snapshot_checked["delivery_authorized"],
            "test_only": voice_snapshot_checked["test_only"],
        },
        "requested_speech_rate": requested_rate,
        "runtime_result": {str(key): value for key, value in dict(runtime_result).items()},
        "probe": {"probe_status": probe_status, "duration_ms": duration_ms, "sample_rate_hz": sample_rate_hz},
        "generated_at": utc_now_iso(),
        "network_contacted": False,
    }
    # The automatic adoption policy: a take is adoptable once it has been *measured*.
    # A failed probe raised above, so reaching here means the file decoded, its
    # duration and sample count came from a real probe, and the text/voice snapshot
    # are the ones this job was frozen with.
    instrumentally_verified = (
        probe_status == "PASS" and duration_ms is not None and duration_ms > 0 and bool(sample_count)
    )
    with database.connect() as connection:
        repository = ExplainerRepository(connection)
        take = repository.insert(
            "narration_takes",
            {
                "video_id": str(video["id"]),
                "segment_id": str(segment["id"]),
                "canonical_segment_id": str(segment["canonical_segment_id"]),
                "locale": segment_locale,
                "take_no": take_no,
                "media_asset_id": str(registered["media_asset_id"]),
                "media_version_id": str(registered["media_version_id"]),
                "media_sha256": str(registered["sha256"]),
                "segment_hash": str(segment["segment_hash"]),
                "voice_profile_version_id": voice_profile_version_id,
                "model_ref": model_ref,
                "emotion": segment.get("emotion"),
                "speech_rate": requested_rate,
                "measured_duration_ms": duration_ms,
                "measured_sample_count": sample_count,
                "sample_rate_hz": sample_rate_hz,
                "status": "GENERATED",
                # Adoption is decided below, once the measurement is known to be real.
                "selected": False,
                "source_job_attempt_id": str(job.get("attempt_id") or "") or None,
                "generation_json": generation_json,
            },
            actor=str(job.get("actor") or "narration-tts-worker"),
        )
        video_id = str(video["id"])
        segment_id = str(segment["id"])
        canonical_segment_id = str(segment["canonical_segment_id"])
        adoption: dict[str, Any] | None = None
        if instrumentally_verified:
            # The automatic policy adopts a take it has really measured: the file
            # decoded, the duration and sample count came from a probe, and the text
            # and voice snapshot are the ones the job was frozen with.  Without this
            # the take was inert (``selected`` stayed False), the newer take lost to
            # an older selected one, and alignment then refused the segment with
            # ``NARRATION_TAKE_NOT_SELECTED`` — a re-read that could not take effect
            # (audit A03, design §5.2).
            from local_drama.application.explainers.narration import build_narration_service

            adoption = build_narration_service(repository).adopt_take(                segment_id=segment_id,
                take_id=str(take["id"]),
                actor=str(job.get("actor") or "narration-tts-worker"),
                record={
                    "adoption_reason": "MEASURED_AND_DECODED",
                    "measured_duration_ms": duration_ms,
                    "measured_sample_count": sample_count,
                    "probe_status": probe_status,
                },
            )
        _commit(connection)
    report = {
        "schema_version": "localdrama.explainer.narration-tts-report.v1",
        "job_id": str(job.get("id") or ""),
        "video_id": video_id,
        "segment_id": segment_id,
        "canonical_segment_id": canonical_segment_id,
        "take_id": str(take["id"]),
        "take_no": take_no,
        "media_version_id": str(registered["media_version_id"]),
        "media_sha256": str(registered["sha256"]),
        "measured_duration_ms": duration_ms,
        "sample_rate_hz": sample_rate_hz,
        "measured_sample_count": sample_count,
        # Whether this take is now the segment's adopted take.  A re-read that
        # produced an inert take used to look identical to one that took effect.
        "adopted": adoption is not None,
        "adoption_authority": "MACHINE_STAGE" if adoption is not None else None,
        "adoption_reason": "MEASURED_AND_DECODED" if adoption is not None else None,
        "synthesized_text_kind": "SPOKEN_TEXT",
        "delivery_authorized": voice_snapshot_checked["delivery_authorized"],
        "test_only": voice_snapshot_checked["test_only"],
        "network_contacted": False,
    }
    relative_output = _write_report(
        output_root=output_root, work_root=work_root, name="narration-tts-report.json", report=report, atomic_writer=atomic_writer
    )
    return "NARRATION_TAKE_REPORT", relative_output


def _probe_duration_ms(probe: Mapping[str, Any]) -> int | None:
    format_block = probe.get("format") if isinstance(probe.get("format"), Mapping) else {}
    raw = probe.get("duration_ms")
    if raw is None and isinstance(format_block, Mapping):
        raw = format_block.get("duration_ms")
        if raw is None and format_block.get("duration") is not None:
            try:
                return int(round(float(format_block["duration"]) * 1000))
            except (TypeError, ValueError):
                return None
    if raw is None:
        return None
    try:
        return int(round(float(raw)))
    except (TypeError, ValueError):
        return None


def _probe_sample_rate_hz(audio_stream: Mapping[str, Any] | None) -> int | None:
    if audio_stream is None:
        return None
    for key in ("sample_rate", "sample_rate_hz"):
        value = audio_stream.get(key)
        if value is None:
            continue
        try:
            return int(str(value))
        except (TypeError, ValueError):
            return None
    return None


def _probe_sample_count(audio_stream: Mapping[str, Any] | None) -> int | None:
    if audio_stream is None:
        return None
    duration = audio_stream.get("duration")
    sample_rate = _probe_sample_rate_hz(audio_stream)
    if duration is None or not sample_rate:
        return None
    try:
        return int(round(float(duration) * sample_rate))
    except (TypeError, ValueError):
        return None


def _write_report(
    *,
    output_root: Path,
    work_root: Path,
    name: str,
    report: Mapping[str, Any],
    atomic_writer: AtomicWriter,
) -> str:
    import json

    output = output_root / name
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    atomic_writer(output, lambda target: target.write_text(payload, encoding="utf-8"))
    repository_path = Path(work_root)
    try:
        return output.relative_to(repository_path).as_posix()
    except ValueError:
        return output.as_posix()


def narration_segment_frozen_payload(repo: ExplainerRepository, segment_id: str) -> dict[str, Any]:
    """Convenience helper: the frozen snapshot that contains ``segment_id``.

    Exposed so a caller (API or another handler) can re-derive exactly the same
    authoritative view this handler synthesised from.  It reuses
    :class:`ExplainerNarrationService.frozen_payload` statically rather than
    constructing a second service instance, so this worker module does not hold a
    cross-service construction dependency.
    """

    segment = repo.get("narration_segments", segment_id)
    revision_id = str(segment["script_revision_id"])
    return ExplainerNarrationService.frozen_payload_for_repo(repo, script_revision_id=revision_id)
