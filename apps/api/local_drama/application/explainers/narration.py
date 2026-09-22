"""Explainer narration script authority: revisions, segmentation, freezing, patching.

This module owns the *authoritative* narration script for one explainer video:

* ``create_script_revision`` writes one ``explainer_script_revisions`` row plus
  its ``narration_segments`` (and, when the caller declares chapters, the
  ``explainer_chapters`` rows).  A segment keeps two texts that must stay
  equivalent: ``display_text`` is the canonical writing used by subtitles
  (``1962``), ``spoken_text`` is what the speech model actually reads
  (``一九六二年``), and ``pronunciation_map`` is the declared evidence that the
  two are the same statement.
* ``freeze_script`` turns a revision into the immutable authority.  Every
  downstream artifact (takes, alignments, subtitle revisions) hashes against the
  frozen payload, so a frozen revision is never edited in place.
* ``patch_segment`` produces a *new* revision that copies the old segments and
  replaces exactly one, then records staleness for the downstream artifacts that
  really exist in the database.  Re-reading one segment therefore never
  invalidates its neighbours' segments, while the joins those neighbours'
  alignment depends on are re-checked through the dependency edges.

What this module deliberately does NOT do:

* It never writes to the legacy drama tables (``dialogue_lines`` /
  ``dialogue_text_revisions`` / ``subtitle_revisions``).  Explainer narration has
  its own tables so legacy dialogue semantics keep their own truth.
* It never synthesises speech, never probes media and never invents a measured
  duration.  Text length is only ever reported as
  ``TEXT_ESTIMATE_NOT_MEASURED_TTS``.
* It never rewrites a human-locked segment silently: ``patch_segment`` refuses
  unless the caller passes ``allow_locked=True``.
* It never fabricates a claim record.  A ``claim_ids`` entry that does not
  resolve to an ``explainer_claims`` row of *this video* raises
  ``SOURCE_EVIDENCE_MISSING``.
* It never re-times or edits an alignment: it only marks existing rows stale.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from local_drama.domain.explainers.contracts import (
    ExplainerContractError,
    StatementType,
    content_hash,
    normalize_locale,
    script_length_estimate,
    utc_now_iso,
)
from local_drama.domain.explainers.policies import staleness_plan
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository

#: Keys whose value participates in a segment's content hash.  Keep this list
#: aligned with the design spec: changing any of them changes what the speech
#: model would say or how long the pause after it is.
SEGMENT_HASH_FIELDS: tuple[str, ...] = (
    "display_text",
    "spoken_text",
    "claim_ids",
    "pronunciation_map",
    "pause_after_ms",
)

#: Change kinds this service can invalidate downstream artifacts for.
SEGMENT_TEXT_CHANGE = "SEGMENT_TEXT"
PRONUNCIATION_CHANGE = "PRONUNCIATION_LEXICON"

_DEFAULT_CHAPTER_TITLE = "未命名章节"

#: Downstream kinds a script-text change can invalidate, mapped from the
#: policies' staleness vocabulary onto the ``artifact_dependencies`` vocabulary.
_POLICY_TO_DEPENDENCY_KIND: dict[str, str] = {
    "NARRATION_TAKE": "NARRATION_TAKE",
    "ALIGNMENT": "ALIGNMENT",
    "SUBTITLE_REVISION": "SUBTITLE_REVISION",
    "VISUAL_BEAT": "VISUAL_BEAT",
    "COMPOSITION_REVISION": "COMPOSITION_REVISION",
}


def _commit(repo: ExplainerRepository) -> None:
    """Retained no-op kept for call-site symmetry.

    ``Database.connect()`` opens the connection with ``isolation_level=None`` and
    ``Database.transaction()`` drives an explicit ``BEGIN``/``COMMIT`` pair, so an
    application-level ``connection.commit()`` here would end the caller's
    transaction early (verified: it makes the outer ``COMMIT`` fail with "no
    transaction is active").  The caller owns commit/rollback; this function must
    not touch it.
    """

    del repo


def _as_mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ExplainerContractError("SCHEMA_INVALID", f"{field} 必须是对象", {"field": field})
    return {str(key): item for key, item in value.items()}


def normalize_pronunciation_map(raw: Any, *, field: str = "pronunciation_map") -> list[dict[str, str]]:
    """Validate and canonicalise ``[{"display": ..., "spoken": ...}]``."""

    if raw is None:
        return []
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise ExplainerContractError("SCHEMA_INVALID", f"{field} 必须是对象数组", {"field": field})
    normalised: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        item = _as_mapping(entry, field=f"{field}[{index}]")
        display = str(item.get("display") or "").strip()
        spoken = str(item.get("spoken") or "").strip()
        if not display or not spoken:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "发音映射的 display 与 spoken 都必须非空",
                {"field": f"{field}[{index}]", "display": display, "spoken": spoken},
            )
        if display in seen:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "发音映射的 display 不能重复", {"field": field, "display": display}
            )
        seen.add(display)
        normalised.append({"display": display, "spoken": spoken})
    return normalised


def apply_pronunciation_map(display_text: str, pronunciation_map: Sequence[Mapping[str, str]]) -> str:
    """The spoken form *implied* by a pronunciation map applied to display text."""

    result = display_text
    # Longest display string first so "1962年" wins over a bare "19".
    ordered = sorted(pronunciation_map, key=lambda item: len(str(item.get("display") or "")), reverse=True)
    for item in ordered:
        display = str(item.get("display") or "")
        spoken = str(item.get("spoken") or "")
        if display and display in result:
            result = result.replace(display, spoken)
    return result


def derive_segment_hash(fields: Mapping[str, Any]) -> str:
    """Deterministic content hash for one narration segment."""

    return content_hash(
        {
            "display_text": str(fields.get("display_text") or ""),
            "spoken_text": str(fields.get("spoken_text") or ""),
            "claim_ids": [str(item) for item in (fields.get("claim_ids") or [])],
            "pronunciation_map": [
                {"display": str(item.get("display") or ""), "spoken": str(item.get("spoken") or "")}
                for item in (fields.get("pronunciation_map") or [])
            ],
            "pause_after_ms": int(fields.get("pause_after_ms") or 0),
        }
    )


class ExplainerNarrationService:
    """Script revision authority for one explainer video."""

    def __init__(self, repo: ExplainerRepository) -> None:
        self.repo = repo

    # ------------------------------------------------------------------ create
    def create_script_revision(
        self,
        *,
        project_id: str,
        video_id: str,
        locale: str,
        title: str,
        outline: Any = None,
        terminology: Mapping[str, Any] | None = None,
        source_script_revision_id: str | None = None,
        segments: Sequence[Mapping[str, Any]],
        status: str = "DRAFT",
        actor: str = "local-user",
        parent_plan_id: str | None = None,
        chapters: Sequence[Mapping[str, Any]] | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.repo.require_explainer_project(project_id)
        video = self.repo.find("explainer_videos", video_id)
        if video is None or str(video.get("project_id")) != project_id:
            raise ExplainerContractError(
                "NOT_FOUND", "解说作品不存在或不属于该项目", {"project_id": project_id, "video_id": video_id}
            )
        target_locale = normalize_locale(locale)
        if status not in {"DRAFT", "IN_REVIEW", "FROZEN", "SUPERSEDED"}:
            raise ExplainerContractError("SCHEMA_INVALID", "讲稿修订状态不合法", {"status": status})
        if status == "FROZEN":
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "新建讲稿修订不能直接声明 FROZEN，请先创建再调用 freeze_script 冻结",
                {},
            )
        if source_script_revision_id:
            source = self.repo.find("explainer_script_revisions", source_script_revision_id)
            if source is None or str(source.get("video_id")) != video_id:
                raise ExplainerContractError(
                    "NOT_FOUND",
                    "来源讲稿修订不存在或不属于本作品",
                    {"source_script_revision_id": source_script_revision_id},
                )
        normalised_segments = self._validate_segments(video_id=video_id, segments=segments)
        chapter_payload = self._build_chapters(segments=normalised_segments, chapters=chapters)
        revision_no = self._next_revision_no(video_id=video_id, locale=target_locale)
        outline_payload = outline if outline is not None else []
        if not isinstance(outline_payload, (list, tuple)):
            raise ExplainerContractError("SCHEMA_INVALID", "outline 必须是数组", {"type": type(outline_payload).__name__})
        terminology_payload = dict(terminology or {})
        content = content_hash(
            {
                "video_id": video_id,
                "locale": target_locale,
                "title": title,
                "outline": list(outline_payload),
                "terminology": terminology_payload,
                "segments": [
                    {
                        "canonical_segment_id": item["canonical_segment_id"],
                        "segment_hash": item["segment_hash"],
                    }
                    for item in normalised_segments
                ],
            }
        )
        revision = self.repo.insert(
            "explainer_script_revisions",
            {
                "video_id": video_id,
                "revision_no": revision_no,
                "locale": target_locale,
                "source_script_revision_id": source_script_revision_id,
                "title": str(title or ""),
                "outline_json": list(outline_payload),
                "terminology_json": terminology_payload,
                "status": status,
                "content_hash": content,
                "parent_plan_id": parent_plan_id,
                "provenance_json": dict(provenance or {}),
            },
            actor=actor,
        )
        chapter_ids: dict[str, str] = {}
        written_chapters: list[dict[str, Any]] = []
        for index, chapter in enumerate(chapter_payload):
            written = self.repo.insert(
                "explainer_chapters",
                {
                    "script_revision_id": revision["id"],
                    "ordinal": index,
                    "code": chapter["code"],
                    "title": chapter["title"],
                    "audience_question": chapter["audience_question"],
                    "summary": chapter["summary"],
                    "claim_ids_json": chapter["claim_ids"],
                    "source_ids_json": chapter["source_ids"],
                },
                actor=actor,
            )
            chapter_ids[chapter["code"]] = str(written["id"])
            written_chapters.append(written)
        written_segments: list[dict[str, Any]] = []
        previous_segment_id: str | None = None
        for ordinal, item in enumerate(normalised_segments):
            chapter_code = item.get("chapter_code")
            chapter_id = chapter_ids.get(chapter_code) if chapter_code else None
            if chapter_code and chapter_id is None:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "分会场编码没有对应的章节定义", {"chapter_code": chapter_code}
                )
            row = self.repo.insert(
                "narration_segments",
                {
                    "video_id": video_id,
                    "script_revision_id": revision["id"],
                    "chapter_id": chapter_id,
                    "canonical_segment_id": item["canonical_segment_id"],
                    "locale": target_locale,
                    "ordinal": ordinal,
                    "display_text": item["display_text"],
                    "spoken_text": item["spoken_text"],
                    "statement_type": item["statement_type"],
                    "claim_ids_json": item["claim_ids"],
                    "pronunciation_map_json": item["pronunciation_map"],
                    "speaker": item.get("speaker"),
                    "emotion": item.get("emotion"),
                    "pause_after_ms": item["pause_after_ms"],
                    "target_duration_ms": item.get("target_duration_ms"),
                    "content_locked_by_human": bool(item.get("content_locked_by_human")),
                    "locked_by": actor if item.get("content_locked_by_human") else None,
                    "locked_at": utc_now_iso() if item.get("content_locked_by_human") else None,
                    "segment_hash": item["segment_hash"],
                    "previous_segment_id": previous_segment_id,
                },
                actor=actor,
            )
            previous_segment_id = str(row["id"])
            written_segments.append(row)
        self.repo.update(
            "explainer_videos",
            video_id,
            {"current_script_revision_id": revision["id"]},
            actor=actor,
        )
        _commit(self.repo)
        return {
            "script_revision": revision,
            "segments": written_segments,
            "chapters": written_chapters,
        }

    # ------------------------------------------------------------------ freeze
    @staticmethod
    def frozen_payload_for_repo(repo: ExplainerRepository, *, script_revision_id: str) -> dict[str, Any]:
        """Immutable snapshot built from an explicit repository.

        Exposed as a static entry point so a worker handler can re-derive exactly
        the same authoritative view without constructing this service (see
        ``scripts/audit_architecture_debt.py``: new cross-service construction is
        not allowed).
        """

        revision = repo.get("explainer_script_revisions", script_revision_id)
        segments = repo.segments(script_revision_id)
        chapter_rows = repo.list_where(
            "explainer_chapters", {"script_revision_id": script_revision_id}, order_by="ordinal", descending=False
        )
        segment_payload = [
            {
                "segment_id": str(item["id"]),
                "canonical_segment_id": str(item["canonical_segment_id"]),
                "ordinal": int(item["ordinal"]),
                "display_text": str(item["display_text"]),
                "spoken_text": str(item["spoken_text"]),
                "statement_type": str(item["statement_type"]),
                "claim_ids": list(item.get("claim_ids_json") or []),
                "pronunciation_map": [
                    {"display": str(pair.get("display") or ""), "spoken": str(pair.get("spoken") or "")}
                    for pair in (item.get("pronunciation_map_json") or [])
                ],
                "speaker": item.get("speaker"),
                "emotion": item.get("emotion"),
                "pause_after_ms": int(item.get("pause_after_ms") or 0),
                "target_duration_ms": item.get("target_duration_ms"),
                "content_locked_by_human": bool(item.get("content_locked_by_human")),
                "segment_hash": str(item["segment_hash"]),
                "chapter_id": item.get("chapter_id"),
            }
            for item in segments
        ]
        chapter_payload = [
            {
                "chapter_id": str(item["id"]),
                "ordinal": int(item["ordinal"]),
                "code": str(item["code"]),
                "title": str(item["title"]),
                "audience_question": str(item.get("audience_question") or ""),
                "summary": str(item.get("summary") or ""),
                "claim_ids": list(item.get("claim_ids_json") or []),
                "source_ids": list(item.get("source_ids_json") or []),
            }
            for item in chapter_rows
        ]
        body = {
            "schema_version": "localdrama.explainer.frozen-script.v1",
            "script_revision_id": str(revision["id"]),
            "video_id": str(revision["video_id"]),
            "locale": str(revision["locale"]),
            "revision_no": int(revision["revision_no"]),
            "source_script_revision_id": revision.get("source_script_revision_id"),
            "title": str(revision.get("title") or ""),
            "status": str(revision.get("status")),
            "frozen_at": revision.get("frozen_at"),
            "frozen_by": revision.get("frozen_by"),
            "terminology": dict(revision.get("terminology_json") or {}),
            "outline": list(revision.get("outline_json") or []),
            "segments": segment_payload,
            "chapters": chapter_payload,
            "segment_count": len(segment_payload),
            "locked_segment_ids": [
                item["canonical_segment_id"] for item in segment_payload if item["content_locked_by_human"]
            ],
        }
        return {**body, "payload_hash": content_hash(body)}

    def frozen_payload(self, *, script_revision_id: str) -> dict[str, Any]:
        """Instance convenience wrapper over :meth:`frozen_payload_for_repo`."""

        return ExplainerNarrationService.frozen_payload_for_repo(self.repo, script_revision_id=script_revision_id)

    def freeze_script(self, *, script_revision_id: str, actor: str) -> dict[str, Any]:
        revision = self.repo.get("explainer_script_revisions", script_revision_id)
        if str(revision.get("status")) == "SUPERSEDED":
            raise ExplainerContractError(
                "STALE_REVISION", "该讲稿修订已被取代，不能冻结", {"script_revision_id": script_revision_id}
            )
        video_id = str(revision["video_id"])
        conflicts = self.repo.open_core_conflicts(video_id)
        if conflicts:
            raise ExplainerContractError(
                "CLAIM_CONFLICT",
                "存在未解决的核心断言冲突，冻结前必须先处理",
                {
                    "video_id": video_id,
                    "conflicts": [
                        {"claim_id": str(item["id"]), "code": str(item["code"]), "status": str(item["status"])}
                        for item in conflicts
                    ],
                },
            )
        segments = self.repo.segments(script_revision_id)
        if not segments:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "空讲稿修订不能被冻结", {"script_revision_id": script_revision_id}
            )
        payload = self.frozen_payload(script_revision_id=script_revision_id)
        frozen_at = utc_now_iso()
        updated = self.repo.update(
            "explainer_script_revisions",
            script_revision_id,
            {
                "status": "FROZEN",
                "frozen_at": frozen_at,
                "frozen_by": actor,
                "content_hash": payload["payload_hash"],
            },
            actor=actor,
        )
        _commit(self.repo)
        return {
            "script_revision": updated,
            "frozen_payload": payload,
            "frozen_at": frozen_at,
            "frozen_by": actor,
        }

    # ------------------------------------------------------------------ patch
    def patch_segment(
        self,
        *,
        segment_id: str,
        expected_revision: int,
        expected_script_revision_id: str,
        display_text: str | None = None,
        spoken_text: str | None = None,
        pronunciation_map: Sequence[Mapping[str, str]] | None = None,
        actor: str = "local-user",
        allow_locked: bool = False,
    ) -> dict[str, Any]:
        segment = self.repo.get("narration_segments", segment_id)
        script_revision_id = str(segment["script_revision_id"])
        if expected_script_revision_id and script_revision_id != expected_script_revision_id:
            raise ExplainerContractError(
                "STALE_REVISION",
                "该段落已不属于指定的讲稿修订，请刷新后重新提交",
                {
                    "segment_id": segment_id,
                    "expected_script_revision_id": expected_script_revision_id,
                    "actual_script_revision_id": script_revision_id,
                },
            )
        if int(segment.get("revision") or 1) != int(expected_revision):
            raise ExplainerContractError(
                "STALE_REVISION",
                "段落已被其他操作更新，请基于最新 revision 重新提交",
                {
                    "segment_id": segment_id,
                    "expected_revision": int(expected_revision),
                    "actual_revision": int(segment.get("revision") or 1),
                },
            )
        revision = self.repo.get("explainer_script_revisions", script_revision_id)
        new_display = str(segment["display_text"] if display_text is None else display_text)
        new_spoken = str(segment["spoken_text"] if spoken_text is None else spoken_text)
        current_map = normalize_pronunciation_map(segment.get("pronunciation_map_json"))
        new_map = current_map if pronunciation_map is None else normalize_pronunciation_map(pronunciation_map)
        if not new_display.strip() or not new_spoken.strip():
            raise ExplainerContractError("SCHEMA_INVALID", "display_text 与 spoken_text 都必须非空")
        if bool(segment.get("content_locked_by_human")) and not allow_locked:
            if new_display != str(segment["display_text"]) or new_spoken != str(segment["spoken_text"]):
                raise ExplainerContractError("SCHEMA_INVALID", "人工锁定的文本不能被自动改写，请先解除锁定或显式允许覆盖")
        self._assert_span_equivalence(display_text=new_display, spoken_text=new_spoken, pronunciation_map=new_map)
        changed_fields = [
            name
            for name, old, new in (
                ("display_text", str(segment["display_text"]), new_display),
                ("spoken_text", str(segment["spoken_text"]), new_spoken),
                ("pronunciation_map", current_map, new_map),
            )
            if old != new
        ]
        video_id = str(revision["video_id"])
        locale = str(revision["locale"])
        new_revision_no = self._next_revision_no(video_id=video_id, locale=locale)
        revision_body = {
            "video_id": video_id,
            "revision_no": new_revision_no,
            "locale": locale,
            "source_script_revision_id": revision.get("source_script_revision_id"),
            "title": str(revision.get("title") or ""),
            "outline_json": list(revision.get("outline_json") or []),
            "terminology_json": dict(revision.get("terminology_json") or {}),
            "status": "DRAFT",
            "parent_plan_id": revision.get("parent_plan_id"),
            "provenance_json": {
                "derived_from_script_revision_id": script_revision_id,
                "patched_segment_id": segment_id,
                "patched_fields": changed_fields,
                "actor": actor,
                "created_at": utc_now_iso(),
            },
        }
        new_hash = derive_segment_hash(
            {
                "display_text": new_display,
                "spoken_text": new_spoken,
                "claim_ids": list(segment.get("claim_ids_json") or []),
                "pronunciation_map": new_map,
                "pause_after_ms": int(segment.get("pause_after_ms") or 0),
            }
        )
        revision_body["content_hash"] = content_hash(
            {
                "video_id": video_id,
                "locale": locale,
                "title": revision_body["title"],
                "outline": revision_body["outline_json"],
                "terminology": revision_body["terminology_json"],
                "segments": [],
                "parent_script_revision_id": script_revision_id,
                "patched_segment_hash": new_hash,
            }
        )
        new_revision = self.repo.insert("explainer_script_revisions", revision_body, actor=actor)
        old_segments = self.repo.segments(script_revision_id)
        chapters = self.repo.list_where(
            "explainer_chapters", {"script_revision_id": script_revision_id}, order_by="ordinal", descending=False
        )
        chapter_id_map: dict[str, str] = {}
        for chapter in chapters:
            copied = self.repo.insert(
                "explainer_chapters",
                {
                    "script_revision_id": new_revision["id"],
                    "ordinal": int(chapter["ordinal"]),
                    "code": str(chapter["code"]),
                    "title": str(chapter["title"]),
                    "audience_question": str(chapter.get("audience_question") or ""),
                    "summary": str(chapter.get("summary") or ""),
                    "claim_ids_json": list(chapter.get("claim_ids_json") or []),
                    "source_ids_json": list(chapter.get("source_ids_json") or []),
                },
                actor=actor,
            )
            chapter_id_map[str(chapter["id"])] = str(copied["id"])
        new_segments: list[dict[str, Any]] = []
        patched_segment: dict[str, Any] | None = None
        previous_segment_id: str | None = None
        for item in old_segments:
            is_patched = str(item["id"]) == segment_id
            body = {
                "video_id": video_id,
                "script_revision_id": new_revision["id"],
                "chapter_id": chapter_id_map.get(str(item.get("chapter_id"))) if item.get("chapter_id") else None,
                "canonical_segment_id": str(item["canonical_segment_id"]),
                "locale": locale,
                "ordinal": int(item["ordinal"]),
                "display_text": new_display if is_patched else str(item["display_text"]),
                "spoken_text": new_spoken if is_patched else str(item["spoken_text"]),
                "statement_type": str(item["statement_type"]),
                "claim_ids_json": list(item.get("claim_ids_json") or []),
                "pronunciation_map_json": new_map if is_patched else normalize_pronunciation_map(item.get("pronunciation_map_json")),
                "speaker": item.get("speaker"),
                "emotion": item.get("emotion"),
                "pause_after_ms": int(item.get("pause_after_ms") or 0),
                "target_duration_ms": item.get("target_duration_ms"),
                "content_locked_by_human": bool(item.get("content_locked_by_human")),
                "locked_by": item.get("locked_by"),
                "locked_at": item.get("locked_at"),
                "segment_hash": new_hash if is_patched else str(item["segment_hash"]),
                "previous_segment_id": previous_segment_id,
            }
            written = self.repo.insert("narration_segments", body, actor=actor)
            previous_segment_id = str(written["id"])
            new_segments.append(written)
            if is_patched:
                patched_segment = written
        if patched_segment is None:
            raise ExplainerContractError("NOT_FOUND", "待修改的段落不属于该讲稿修订", {"segment_id": segment_id})
        new_revision = self.repo.get("explainer_script_revisions", str(new_revision["id"]))
        invalidated = self._record_invalidation(
            video_id=video_id,
            locale=locale,
            canonical_segment_id=str(segment["canonical_segment_id"]),
            new_segment=patched_segment,
            changed_fields=changed_fields,
            actor=actor,
        )
        self.repo.update(
            "explainer_videos", video_id, {"current_script_revision_id": new_revision["id"]}, actor=actor
        )
        _commit(self.repo)
        return {
            "script_revision": new_revision,
            "segment": patched_segment,
            "invalidated": invalidated,
            "segments": new_segments,
            "changed_fields": changed_fields,
            "neighbour_joins_recheck_required": True,
        }

    def lock_segments(
        self, *, script_revision_id: str, canonical_segment_ids: Sequence[str], actor: str
    ) -> dict[str, Any]:
        self.repo.get("explainer_script_revisions", script_revision_id)
        wanted = [str(item) for item in canonical_segment_ids]
        if not wanted:
            raise ExplainerContractError("SCHEMA_INVALID", "至少需要指定一个要锁定的段落")
        segments = {str(item["canonical_segment_id"]): item for item in self.repo.segments(script_revision_id)}
        missing = [item for item in wanted if item not in segments]
        if missing:
            raise ExplainerContractError(
                "NOT_FOUND",
                "指定段落不属于该讲稿修订",
                {"script_revision_id": script_revision_id, "missing": missing},
            )
        locked_at = utc_now_iso()
        locked: list[dict[str, Any]] = []
        for canonical_id in wanted:
            row = self.repo.update(
                "narration_segments",
                str(segments[canonical_id]["id"]),
                {
                    "content_locked_by_human": True,
                    "locked_by": actor,
                    "locked_at": locked_at,
                },
                actor=actor,
            )
            locked.append(row)
        _commit(self.repo)
        return {
            "script_revision_id": script_revision_id,
            "locked_at": locked_at,
            "locked_by": actor,
            "segments": locked,
            "locked_canonical_segment_ids": wanted,
        }

    def unlock_segments(
        self, *, script_revision_id: str, canonical_segment_ids: Sequence[str], actor: str
    ) -> dict[str, Any]:
        self.repo.get("explainer_script_revisions", script_revision_id)
        wanted = [str(item) for item in canonical_segment_ids]
        segments = {str(item["canonical_segment_id"]): item for item in self.repo.segments(script_revision_id)}
        missing = [item for item in wanted if item not in segments]
        if missing:
            raise ExplainerContractError(
                "NOT_FOUND", "指定段落不属于该讲稿修订", {"missing": missing}
            )
        released: list[dict[str, Any]] = []
        for canonical_id in wanted:
            released.append(
                self.repo.update(
                    "narration_segments",
                    str(segments[canonical_id]["id"]),
                    {"content_locked_by_human": False, "locked_by": None, "locked_at": None},
                    actor=actor,
                )
            )
        _commit(self.repo)
        return {"script_revision_id": script_revision_id, "segments": released}

    # ------------------------------------------------------------------ budget
    def text_budget(self, *, locale: str, text: str, chars_per_second: float) -> dict[str, Any]:
        """Arithmetic length budget.

        The returned estimate is explicitly *not* a measured TTS duration: the
        ``timing_status`` says so, and ``measured_duration_ms`` stays ``None`` so
        no caller can mistake the estimate for a probe result.
        """

        normalize_locale(locale)
        estimate = script_length_estimate(text, chars_per_second=chars_per_second)
        return {
            "locale": locale,
            "text": text,
            "character_count": estimate["character_count"],
            "chars_per_second": estimate["chars_per_second"],
            "estimated_seconds": estimate["estimated_seconds"],
            "estimated_duration_ms": round(estimate["estimated_seconds"] * 1000),
            "timing_status": "TEXT_ESTIMATE_NOT_MEASURED_TTS",
            "measured_duration_ms": None,
        }

    # ------------------------------------------------------------------ internals
    def _next_revision_no(self, *, video_id: str, locale: str) -> int:
        row = self.repo.query_one(
            "SELECT COALESCE(MAX(revision_no), 0) AS current FROM explainer_script_revisions WHERE video_id = ? AND locale = ?",
            (video_id, locale),
        )
        return int(row["current"] if row is not None else 0) + 1

    def _assert_span_equivalence(
        self, *, display_text: str, spoken_text: str, pronunciation_map: Sequence[Mapping[str, str]]
    ) -> None:
        for pair in pronunciation_map:
            display = str(pair.get("display") or "")
            spoken = str(pair.get("spoken") or "")
            if display not in display_text:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "发音映射的 display 必须出现在 display_text 中",
                    {"display": display, "display_text": display_text},
                )
            if spoken not in spoken_text:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "发音映射的 spoken 必须出现在 spoken_text 中",
                    {"spoken": spoken, "spoken_text": spoken_text},
                )
        implied = apply_pronunciation_map(display_text, pronunciation_map)
        if implied != spoken_text:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "display_text 与 spoken_text 不等价：按发音映射朗读得到的结果与 spoken_text 不一致",
                {
                    "display_text": display_text,
                    "spoken_text": spoken_text,
                    "implied_spoken_text": implied,
                    "pronunciation_map": [dict(pair) for pair in pronunciation_map],
                },
            )

    def _validate_segments(
        self, *, video_id: str, segments: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        if isinstance(segments, (str, bytes)) or not isinstance(segments, Sequence) or not segments:
            raise ExplainerContractError("SCHEMA_INVALID", "讲稿修订至少需要一个段落")
        known_claims = {
            str(row["id"]) for row in self.repo.query_all("SELECT id FROM explainer_claims WHERE video_id = ?", (video_id,))
        }
        known_claim_codes = {
            str(row["code"]): str(row["id"])
            for row in self.repo.query_all("SELECT id, code FROM explainer_claims WHERE video_id = ?", (video_id,))
        }
        valid_types = {item.value for item in StatementType}
        seen: set[str] = set()
        normalised: list[dict[str, Any]] = []
        for index, raw in enumerate(segments):
            item = _as_mapping(raw, field=f"segments[{index}]")
            canonical_id = str(item.get("canonical_segment_id") or "").strip()
            if not canonical_id:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "段落必须提供 canonical_segment_id", {"index": index}
                )
            if canonical_id in seen:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "canonical_segment_id 必须唯一", {"canonical_segment_id": canonical_id}
                )
            seen.add(canonical_id)
            statement_type = str(item.get("statement_type") or StatementType.FACT.value)
            if statement_type not in valid_types:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "statement_type 不在允许的取值内",
                    {"canonical_segment_id": canonical_id, "statement_type": statement_type},
                )
            claim_ids = [str(value) for value in (item.get("claim_ids") or [])]
            resolved: list[str] = []
            for claim_ref in claim_ids:
                if claim_ref in known_claims:
                    resolved.append(claim_ref)
                    continue
                mapped = known_claim_codes.get(claim_ref)
                if mapped is None:
                    raise ExplainerContractError(
                        "SOURCE_EVIDENCE_MISSING",
                        "段落声明的断言在本作品中不存在，请先在资料页登记该断言",
                        {"canonical_segment_id": canonical_id, "claim_id": claim_ref, "video_id": video_id},
                    )
                resolved.append(mapped)
            display_text = str(item.get("display_text") or "")
            spoken_text = str(item.get("spoken_text") or "")
            if not display_text.strip() or not spoken_text.strip():
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "display_text 与 spoken_text 都必须非空",
                    {"canonical_segment_id": canonical_id},
                )
            pronunciation_map = normalize_pronunciation_map(item.get("pronunciation_map"))
            self._assert_span_equivalence(
                display_text=display_text, spoken_text=spoken_text, pronunciation_map=pronunciation_map
            )
            pause_after_ms = int(item.get("pause_after_ms") or 0)
            if pause_after_ms < 0:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "pause_after_ms 不能为负", {"canonical_segment_id": canonical_id}
                )
            target_duration_ms = item.get("target_duration_ms")
            if target_duration_ms is not None and int(target_duration_ms) <= 0:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "target_duration_ms 必须为正", {"canonical_segment_id": canonical_id}
                )
            chapter_code = item.get("chapter_code")
            normalised.append(
                {
                    "canonical_segment_id": canonical_id,
                    "display_text": display_text,
                    "spoken_text": spoken_text,
                    "statement_type": statement_type,
                    "claim_ids": resolved,
                    "pronunciation_map": pronunciation_map,
                    "speaker": item.get("speaker"),
                    "emotion": item.get("emotion"),
                    "pause_after_ms": pause_after_ms,
                    "target_duration_ms": None if target_duration_ms is None else int(target_duration_ms),
                    "content_locked_by_human": bool(item.get("content_locked_by_human")),
                    "chapter_code": str(chapter_code) if chapter_code else None,
                    "segment_hash": derive_segment_hash(
                        {
                            "display_text": display_text,
                            "spoken_text": spoken_text,
                            "claim_ids": resolved,
                            "pronunciation_map": pronunciation_map,
                            "pause_after_ms": pause_after_ms,
                        }
                    ),
                }
            )
        return normalised

    def _build_chapters(
        self,
        *,
        segments: Sequence[Mapping[str, Any]],
        chapters: Sequence[Mapping[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        declared: dict[str, dict[str, Any]] = {}
        for index, raw in enumerate(chapters or ()):
            item = _as_mapping(raw, field=f"chapters[{index}]")
            code = str(item.get("code") or item.get("chapter_code") or "").strip()
            if not code:
                raise ExplainerContractError("SCHEMA_INVALID", "章节必须提供 code", {"index": index})
            if code in declared:
                raise ExplainerContractError("SCHEMA_INVALID", "章节 code 必须唯一", {"code": code})
            declared[code] = item
        ordered_codes: list[str] = []
        for segment in segments:
            code = segment.get("chapter_code")
            if code and code not in ordered_codes:
                ordered_codes.append(str(code))
        for code in declared:
            if code not in ordered_codes:
                ordered_codes.append(code)
        built: list[dict[str, Any]] = []
        for index, code in enumerate(ordered_codes):
            item = declared.get(code, {})
            claim_ids = [str(value) for value in (item.get("claim_ids") or [])]
            if not claim_ids:
                for segment in segments:
                    if segment.get("chapter_code") == code:
                        claim_ids.extend(str(value) for value in segment["claim_ids"])
            built.append(
                {
                    "code": code,
                    "title": str(item.get("title") or _DEFAULT_CHAPTER_TITLE),
                    "audience_question": str(item.get("audience_question") or ""),
                    "summary": str(item.get("summary") or ""),
                    "claim_ids": list(dict.fromkeys(claim_ids)),
                    "source_ids": [str(value) for value in (item.get("source_ids") or [])],
                    "ordinal_hint": index,
                }
            )
        return built

    def _record_invalidation(
        self,
        *,
        video_id: str,
        locale: str,
        canonical_segment_id: str,
        new_segment: Mapping[str, Any],
        changed_fields: Sequence[str],
        actor: str,
    ) -> list[dict[str, Any]]:
        """Record dependency edges and mark the downstream rows that exist stale.

        The ``staleness_plan`` vocabulary decides *which kinds* are affected; the
        database decides *which rows* actually exist.  A kind with no row is not
        invented and is not reported as invalidated.
        """

        project_row = self.repo.query_one("SELECT project_id FROM explainer_videos WHERE id = ?", (video_id,))
        if project_row is None:
            raise ExplainerContractError("NOT_FOUND", "解说作品不存在", {"video_id": video_id})
        project_id = str(project_row["project_id"])
        new_segment_id = str(new_segment["id"])
        new_segment_hash = str(new_segment["segment_hash"])
        plan = staleness_plan(SEGMENT_TEXT_CHANGE)
        pron_plan = staleness_plan(PRONUNCIATION_CHANGE)
        plans = {"SEGMENT_TEXT": plan}
        if "pronunciation_map" in set(changed_fields):
            plans["PRONUNCIATION_LEXICON"] = pron_plan
        existing_downstream_kinds: set[str] = set()
        for kind in list(plan["invalidates"]) + list(pron_plan["invalidates"]):
            mapped = _POLICY_TO_DEPENDENCY_KIND.get(str(kind))
            if mapped:
                existing_downstream_kinds.add(mapped)
        downstream_rows = self._downstream_artifacts(
            video_id=video_id, locale=locale, canonical_segment_id=canonical_segment_id
        )
        invalidated: list[dict[str, Any]] = []
        reason = "讲稿段落文本或发音映射已变更：" + ",".join(changed_fields or ["none"])
        for entry in downstream_rows:
            if entry["downstream_kind"] not in existing_downstream_kinds:
                continue
            self.repo.add_dependency(
                video_id=video_id,
                project_id=project_id,
                upstream_kind="SCRIPT_SEGMENT",
                upstream_id=new_segment_id,
                upstream_hash=new_segment_hash,
                downstream_kind=entry["downstream_kind"],
                downstream_id=entry["downstream_id"],
                edition_id=entry.get("edition_id"),
            )
            marked = self.repo.mark_dependents_stale(
                upstream_kind="SCRIPT_SEGMENT",
                upstream_id=new_segment_id,
                downstream_kinds=[entry["downstream_kind"]],
                reason=reason,
                invalidated_by=actor,
            )
            invalidated.append(
                {
                    "downstream_kind": entry["downstream_kind"],
                    "downstream_id": entry["downstream_id"],
                    "detail": entry["detail"],
                    "stale_recorded": bool(marked),
                    "reason": reason,
                }
            )
        return invalidated

    def _downstream_artifacts(
        self, *, video_id: str, locale: str, canonical_segment_id: str
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        takes = self.repo.list_where(
            "narration_takes",
            {"video_id": video_id, "canonical_segment_id": canonical_segment_id},
            order_by="take_no",
            descending=False,
        )
        take_ids = [str(item["id"]) for item in takes]
        for take in takes:
            entries.append(
                {
                    "downstream_kind": "NARRATION_TAKE",
                    "downstream_id": str(take["id"]),
                    "detail": {
                        "take_no": int(take["take_no"]),
                        "locale": str(take["locale"]),
                        "status": str(take["status"]),
                        "selected": bool(take.get("selected")),
                    },
                }
            )
        alignments: list[dict[str, Any]] = []
        for take_id in take_ids:
            alignments.extend(
                self.repo.list_where(
                    "narration_alignment_revisions",
                    {"take_id": take_id},
                    order_by="revision_no",
                    descending=False,
                )
            )
        for alignment in alignments:
            entries.append(
                {
                    "downstream_kind": "ALIGNMENT",
                    "downstream_id": str(alignment["id"]),
                    "detail": {
                        "take_id": str(alignment["take_id"]),
                        "revision_no": int(alignment["revision_no"]),
                        "alignment_status": str(alignment["alignment_status"]),
                    },
                }
            )
        alignment_ids = [str(item["id"]) for item in alignments]
        affected_script_revision_ids = self._affected_script_revision_ids(
            video_id=video_id, canonical_segment_id=canonical_segment_id
        )
        subtitle_rows = self.repo.list_where("explainer_subtitle_revisions", {"video_id": video_id})
        for subtitle in subtitle_rows:
            if not self._subtitle_is_downstream(
                subtitle=subtitle,
                alignment_ids=alignment_ids,
                affected_script_revision_ids=affected_script_revision_ids,
            ):
                continue
            entries.append(
                {
                    "downstream_kind": "SUBTITLE_REVISION",
                    "downstream_id": str(subtitle["id"]),
                    "edition_id": subtitle.get("edition_id"),
                    "detail": {
                        "locale": str(subtitle["locale"]),
                        "revision_no": int(subtitle["revision_no"]),
                        "text_authority": str(subtitle["text_authority"]),
                        "linked_by": (
                            "ALIGNMENT"
                            if set(str(item) for item in (subtitle.get("narration_alignment_revision_ids_json") or []))
                            & set(alignment_ids)
                            else "SCRIPT_AUTHORITY"
                        ),
                    },
                }
            )
        return entries

    def _affected_script_revision_ids(
        self, *, video_id: str, canonical_segment_id: str
    ) -> frozenset[str]:
        """Every revision whose text for this segment derives from the change.

        A translation chain inherits staleness: the English revision declares the
        Chinese revision as its ``source_script_revision_id``, so a subtitle that
        used the English text authority is downstream of the Chinese edit too.
        """

        revisions = self.repo.query_all(
            "SELECT id, locale, source_script_revision_id FROM explainer_script_revisions WHERE video_id = ?",
            (video_id,),
        )
        segment_rows = self.repo.query_all(
            "SELECT DISTINCT script_revision_id FROM narration_segments WHERE video_id = ? AND canonical_segment_id = ?",
            (video_id, canonical_segment_id),
        )
        affected = {str(row["script_revision_id"]) for row in segment_rows}
        changed = True
        while changed:
            changed = False
            for revision in revisions:
                parent = revision["source_script_revision_id"]
                if parent and str(parent) in affected and str(revision["id"]) not in affected:
                    affected.add(str(revision["id"]))
                    changed = True
        return frozenset(affected)

    def _subtitle_is_downstream(
        self,
        *,
        subtitle: Mapping[str, Any],
        alignment_ids: Sequence[str],
        affected_script_revision_ids: frozenset[str],
    ) -> bool:
        linked = {str(item) for item in (subtitle.get("narration_alignment_revision_ids_json") or [])}
        if linked & set(alignment_ids):
            return True
        if str(subtitle.get("text_authority")) not in {"NARRATION_SCRIPT", "TRANSLATED_SCRIPT"}:
            return False
        script_revision_id = subtitle.get("script_revision_id")
        return bool(script_revision_id) and str(script_revision_id) in affected_script_revision_ids
