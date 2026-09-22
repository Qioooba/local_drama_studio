"""SQLite persistence for the Explainer Factory domain (W01).

Persistence and query only: no HTTP, no downloads and never a long GPU wait
inside a transaction (《源码接入与开发任务清单》§3, §8).

Two rules this module enforces on every write:

1. **Every new entity carries a real foreign key.**  Polymorphic media
   references are validated in the service layer *and* constrained here so a
   JSON payload can never smuggle a cross-project media reference in.
2. **``explainer_runs`` / ``explainer_step_bindings`` are projections.**  They
   store business scope and frozen snapshots; the authoritative execution state
   stays in the existing ``automation_workflow_runs`` / ``jobs`` / ``job_attempts``
   tables, and this repository never creates a second claim queue.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Iterable, Mapping, Sequence

from local_drama.domain.explainers.contracts import (
    ExplainerContractError,
    ProductKind,
    canonical_json,
    content_hash,
    utc_now_iso,
)

JSON_COLUMNS: dict[str, frozenset[str]] = {
    "explainer_videos": frozenset({"input_payload_json", "research_allowed_domains_json"}),
    "channel_profile_versions": frozenset(
        {
            "palette_json",
            "camera_grammar_json",
            "lighting_json",
            "typography_json",
            "subtitle_safe_area_json",
            "transition_set_json",
            "voice_json",
            "bgm_policy_json",
            "negative_constraints_json",
            "license_policy_json",
        }
    ),
    "explainer_research_packets": frozenset({"allowed_domains_json", "blockers_json"}),
    "explainer_sources": frozenset({"rights_json"}),
    "explainer_claims": frozenset({"disambiguation_json", "verification_json"}),
    "explainer_events": frozenset({"participant_entity_ids_json", "claim_ids_json"}),
    "explainer_entities": frozenset({"aliases_json", "disambiguation_json"}),
    "entity_state_revisions": frozenset({"carried_prop_entity_ids_json", "reference_media_version_ids_json"}),
    "explainer_script_revisions": frozenset({"outline_json", "terminology_json", "provenance_json"}),
    "explainer_chapters": frozenset({"claim_ids_json", "source_ids_json"}),
    "narration_segments": frozenset({"claim_ids_json", "pronunciation_map_json"}),
    "explainer_visual_beats": frozenset({"allowed_fallbacks_json", "entity_refs_json", "claim_refs_json", "shot_grammar_json"}),
    "explainer_editions": frozenset({"subtitle_locales_json", "frozen_narration_take_ids_json"}),
    "narration_takes": frozenset({"generation_json"}),
    "narration_alignment_revisions": frozenset(
        {"word_timings_json", "display_map_json", "unaligned_tokens_json", "asr_review_json"}
    ),
    "explainer_subtitle_revisions": frozenset(
        {"narration_alignment_revision_ids_json", "cues_json", "layout_report_json"}
    ),
    "composition_revisions": frozenset({"manifest_json", "validation_json"}),
    "composition_items": frozenset(
        {"layer_json", "transform_json", "subtitle_json", "audio_json", "transition_json"}
    ),
    "composition_renders": frozenset({"probe_json", "blockers_json"}),
    "composition_render_chunks": frozenset({"codec_signature_json"}),
    "composition_deliveries": frozenset({"output_profile_json", "probe_json", "files_json", "licenses_json"}),
    "explainer_runs": frozenset(
        {
            "plan_json",
            "frozen_inputs_json",
            "policy_snapshot_json",
            "capability_snapshot_json",
            "budget_json",
            "progress_json",
            "blockers_json",
            "budget_used_json",
        }
    ),
    "explainer_step_bindings": frozenset({"planned_inputs_json", "resolved_inputs_json", "output_ref_json"}),
    "explainer_media_candidates": frozenset({"execution_snapshot_json", "lineage_json", "qc_summary_json"}),
    "run_identity_inputs": frozenset({"slot_hashes_json", "verified_slot_hashes_json"}),
    "entity_identity_bindings": frozenset(
        {"reference_media_version_ids_json", "reference_slot_roles_json", "consumed_slot_roles_json"}
    ),
    "explainer_qc_reports": frozenset({"coverage_json", "unverified_checks_json", "detectors_json", "summary_json"}),
    "explainer_qc_issues": frozenset({"scope_json", "evidence_json", "closed_evidence_json"}),
    "explainer_decisions": frozenset({"thresholds_json", "evidence_json", "reviewed_intervals_json"}),
    "explainer_schedules": frozenset(
        {
            "rule_json",
            "source_allowlist_json",
            "daily_budget_json",
            "closed_window_json",
            "failure_notification_json",
            "durations_json",
            "outputs_json",
        }
    ),
    "publication_packages": frozenset(
        {"files_json", "license_scope_json", "license_blockers_json", "ai_disclosure_json", "metadata_json", "requested_territories_json"}
    ),
    "publication_receipts": frozenset({"response_json"}),
}

#: Tables whose ``id`` is a UUID and that carry the standard audit columns.
_TABLES: tuple[str, ...] = (
    "channel_profiles",
    "channel_profile_versions",
    "explainer_videos",
    "explainer_research_packets",
    "explainer_sources",
    "explainer_source_spans",
    "explainer_claims",
    "claim_evidence",
    "explainer_events",
    "explainer_entities",
    "entity_state_revisions",
    "explainer_script_revisions",
    "explainer_chapters",
    "narration_segments",
    "explainer_visual_beats",
    "beat_narration_links",
    "explainer_editions",
    "narration_takes",
    "narration_alignment_revisions",
    "explainer_subtitle_revisions",
    "composition_revisions",
    "composition_items",
    "composition_renders",
    "composition_render_chunks",
    "composition_deliveries",
    "explainer_runs",
    "explainer_step_bindings",
    "artifact_dependencies",
    "explainer_media_candidates",
    "explainer_beat_selections",
    "run_identity_inputs",
    "entity_identity_bindings",
    "explainer_qc_reports",
    "explainer_qc_issues",
    "explainer_decisions",
    "explainer_schedules",
    "schedule_occurrences",
    "publication_packages",
    "publication_receipts",
)

#: Tables that carry ``updated_at``/``revision`` and therefore support touch().
_MUTABLE_TABLES: frozenset[str] = frozenset(
    {
        "channel_profiles",
        "channel_profile_versions",
        "explainer_videos",
        "explainer_research_packets",
        "explainer_sources",
        "explainer_claims",
        "explainer_events",
        "explainer_entities",
        "entity_state_revisions",
        "explainer_script_revisions",
        "explainer_chapters",
        "narration_segments",
        "explainer_visual_beats",
        "explainer_editions",
        "narration_takes",
        "narration_alignment_revisions",
        "explainer_subtitle_revisions",
        "composition_revisions",
        "composition_items",
        "composition_renders",
        "composition_render_chunks",
        "composition_deliveries",
        "explainer_runs",
        "explainer_step_bindings",
        "artifact_dependencies",
        "explainer_media_candidates",
        "explainer_beat_selections",
        "run_identity_inputs",
        "entity_identity_bindings",
        "explainer_qc_reports",
        "explainer_qc_issues",
        "explainer_decisions",
        "explainer_schedules",
        "schedule_occurrences",
        "publication_packages",
        "publication_receipts",
    }
)

#: Audit columns injected automatically on insert when absent from the payload.
_AUDIT_INSERT: dict[str, Any] = {
    "created_at": None,  # resolved at call time
    "updated_at": None,
    "created_by": "system",
    "revision": 1,
    "schema_version": "v2",
}


def _new_id(prefix: str = "") -> str:
    value = str(uuid.uuid4())
    return f"{prefix}{value}" if prefix else value


def encode_row(table: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """Encode a Python payload into SQLite-ready values for ``table``.

    ``None`` for a JSON column is stored as ``null`` rather than ``"null"`` so a
    "not generated yet" field stays distinguishable (design example: ``null`` is
    "尚未生成", never zero).
    """

    encoded: dict[str, Any] = {}
    json_columns = JSON_COLUMNS.get(table, frozenset())
    for key, value in row.items():
        if key in json_columns:
            encoded[key] = None if value is None else canonical_json(value)
        elif isinstance(value, bool):
            encoded[key] = 1 if value else 0
        elif isinstance(value, (dict, list, tuple)):
            encoded[key] = canonical_json(value)
        else:
            encoded[key] = value
    return encoded


def decode_row(table: str, row: sqlite3.Row | Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for column in JSON_COLUMNS.get(table, frozenset()):
        if column in data and isinstance(data[column], str):
            try:
                data[column] = json.loads(data[column])
            except ValueError:
                data[column] = None
    for column in ("active", "selected", "adopted", "stale", "fictional", "locked_by_human", "must_be_motion", "preserve_human_locks"):
        if column in data and data[column] is not None:
            data[column] = bool(data[column])
    return data


def decode_rows(table: str, rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [decoded for row in rows if (decoded := decode_row(table, row)) is not None]


class ExplainerRepository:
    """Typed CRUD over the explainer tables on an open SQLite connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    # ------------------------------------------------------------------ raw sql
    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self.connection.execute(sql, tuple(parameters))

    def query_one(self, sql: str, parameters: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, tuple(parameters)).fetchone()

    def query_all(self, sql: str, parameters: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, tuple(parameters)).fetchall())

    # ------------------------------------------------------------------ generic
    def insert(self, table: str, payload: Mapping[str, Any], *, actor: str = "system") -> dict[str, Any]:
        if table not in _TABLES:
            raise ExplainerContractError("SCHEMA_INVALID", f"未知的解说表：{table}", {"table": table})
        data = dict(payload)
        data.setdefault("id", _new_id())
        now = utc_now_iso()
        if table in _MUTABLE_TABLES:
            data.setdefault("created_at", now)
            data.setdefault("updated_at", now)
            data.setdefault("created_by", actor)
            data.setdefault("revision", 1)
            data.setdefault("schema_version", "v2")
        encoded = encode_row(table, data)
        columns = ", ".join(encoded)
        placeholders = ", ".join("?" for _ in encoded)
        self.connection.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(encoded.values())
        )
        return self.get(table, str(data["id"]))

    def get(self, table: str, row_id: str) -> dict[str, Any]:
        row = self.query_one(f"SELECT * FROM {table} WHERE id = ?", (row_id,))
        decoded = decode_row(table, row)
        if decoded is None:
            raise ExplainerContractError("NOT_FOUND", f"{table} 中不存在该对象", {"table": table, "id": row_id})
        return decoded

    def find(self, table: str, row_id: str | None) -> dict[str, Any] | None:
        if not row_id:
            return None
        return decode_row(table, self.query_one(f"SELECT * FROM {table} WHERE id = ?", (row_id,)))

    def list_where(
        self,
        table: str,
        where: Mapping[str, Any] | None = None,
        *,
        order_by: str = "created_at",
        descending: bool = True,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        for key, value in (where or {}).items():
            if value is None:
                clauses.append(f"{key} IS NULL")
            else:
                clauses.append(f"{key} = ?")
                parameters.append(1 if value is True else 0 if value is False else value)
        sql = f"SELECT * FROM {table}"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += f" ORDER BY {order_by} {'DESC' if descending else 'ASC'}"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            parameters.extend([int(limit), int(offset)])
        return decode_rows(table, self.query_all(sql, tuple(parameters)))

    def count(self, table: str, where: Mapping[str, Any] | None = None) -> int:
        clauses: list[str] = []
        parameters: list[Any] = []
        for key, value in (where or {}).items():
            if value is None:
                clauses.append(f"{key} IS NULL")
            else:
                clauses.append(f"{key} = ?")
                parameters.append(1 if value is True else 0 if value is False else value)
        sql = f"SELECT COUNT(*) FROM {table}"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        row = self.query_one(sql, tuple(parameters))
        return int(row[0]) if row else 0

    def update(
        self,
        table: str,
        row_id: str,
        payload: Mapping[str, Any],
        *,
        expected_revision: int | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        if table not in _TABLES:
            raise ExplainerContractError("SCHEMA_INVALID", f"未知的解说表：{table}", {"table": table})
        current = self.get(table, row_id)
        if expected_revision is not None and int(current.get("revision") or 1) != int(expected_revision):
            raise ExplainerContractError(
                "STALE_REVISION",
                "对象已被其他操作更新，请基于最新 revision 重新提交",
                {
                    "table": table,
                    "id": row_id,
                    "expected_revision": expected_revision,
                    "actual_revision": int(current.get("revision") or 1),
                },
            )
        data = encode_row(table, dict(payload))
        assignments = [f"{key} = ?" for key in data]
        parameters: list[Any] = list(data.values())
        if table in _MUTABLE_TABLES:
            assignments.append("updated_at = ?")
            parameters.append(utc_now_iso())
            assignments.append("revision = revision + 1")
            if actor:
                assignments.append("created_by = ?")
                parameters.append(actor)
        parameters.append(row_id)
        self.connection.execute(f"UPDATE {table} SET {', '.join(assignments)} WHERE id = ?", tuple(parameters))
        return self.get(table, row_id)

    def bump(self, table: str, row_id: str, **counters: int) -> None:
        """Increment integer counters without touching revision semantics."""

        if not counters:
            return
        assignments = [f"{key} = {key} + ?" for key in counters]
        parameters: list[Any] = [int(value) for value in counters.values()]
        parameters.append(row_id)
        self.connection.execute(f"UPDATE {table} SET {', '.join(assignments)} WHERE id = ?", tuple(parameters))

    # ------------------------------------------------------------------ projects
    def project_kind(self, project_id: str) -> str:
        row = self.query_one("SELECT product_kind FROM projects WHERE id = ?", (project_id,))
        if row is None:
            raise ExplainerContractError("NOT_FOUND", "项目不存在", {"project_id": project_id})
        return str(row["product_kind"] or ProductKind.DRAMA.value)

    def require_explainer_project(self, project_id: str) -> None:
        kind = self.project_kind(project_id)
        if kind != ProductKind.EXPLAINER.value:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "该入口只服务于解说作品；短剧项目请使用原有工作台",
                {"project_id": project_id, "product_kind": kind},
            )

    def project_row(self, project_id: str) -> dict[str, Any]:
        row = self.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if row is None:
            raise ExplainerContractError("NOT_FOUND", "项目不存在", {"project_id": project_id})
        return dict(row)

    # ------------------------------------------------------------------ videos
    def video_for_project(self, project_id: str) -> dict[str, Any] | None:
        return decode_row(
            "explainer_videos",
            self.query_one("SELECT * FROM explainer_videos WHERE project_id = ?", (project_id,)),
        )

    def require_video_for_project(self, project_id: str) -> dict[str, Any]:
        video = self.video_for_project(project_id)
        if video is None:
            raise ExplainerContractError(
                "NOT_FOUND", "该项目还没有解说作品", {"project_id": project_id}
            )
        return video

    # ------------------------------------------------------------------ media ownership
    def require_same_project_media(self, *, project_id: str, media_version_id: str) -> dict[str, Any]:
        """Validate a media reference belongs to ``project_id``.

        JSON payloads carry media ids; this is the service-side check the design
        requires in addition to the real foreign key.
        """

        row = self.query_one(
            """
            SELECT mv.id AS media_version_id, mv.media_asset_id, mv.sha256, mv.integrity_status,
                   mv.duration_ms, mv.mime_type, ma.project_id, ma.media_kind, ma.purpose
            FROM media_versions mv
            JOIN media_assets ma ON ma.id = mv.media_asset_id
            WHERE mv.id = ?
            """,
            (media_version_id,),
        )
        if row is None:
            raise ExplainerContractError(
                "NOT_FOUND", "媒体版本不存在", {"media_version_id": media_version_id}
            )
        if str(row["project_id"]) != project_id:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "媒体版本属于其他项目，不能在解说作品中使用",
                {"media_version_id": media_version_id, "media_project_id": row["project_id"]},
            )
        return dict(row)

    def require_same_project_media_many(self, *, project_id: str, media_version_ids: Iterable[str]) -> list[dict[str, Any]]:
        return [
            self.require_same_project_media(project_id=project_id, media_version_id=media_version_id)
            for media_version_id in dict.fromkeys(media_version_ids)
        ]

    # ------------------------------------------------------------------ claims / evidence
    def claim_by_code(self, video_id: str, code: str) -> dict[str, Any] | None:
        return decode_row(
            "explainer_claims",
            self.query_one("SELECT * FROM explainer_claims WHERE video_id = ? AND code = ?", (video_id, code)),
        )

    def claim_evidence(self, claim_id: str) -> list[dict[str, Any]]:
        return decode_rows(
            "claim_evidence",
            self.query_all("SELECT * FROM claim_evidence WHERE claim_id = ? ORDER BY created_at", (claim_id,)),
        )

    def claim_span_records(self, claim_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.query_all(
                """
                SELECT ce.id AS evidence_id, ce.stance, ce.independence_key, ce.note,
                       s.id AS source_id, s.title AS source_title, s.url AS source_url,
                       s.published_at, s.fetched_at, s.credibility_kind, s.body_sha256,
                       sp.id AS span_id, sp.start_offset, sp.end_offset, sp.quote_text, sp.span_hash
                FROM claim_evidence ce
                JOIN explainer_sources s ON s.id = ce.source_id
                JOIN explainer_source_spans sp ON sp.id = ce.source_span_id
                WHERE ce.claim_id = ?
                ORDER BY ce.created_at
                """,
                (claim_id,),
            )
        ]

    def independent_evidence_count(self, claim_id: str) -> int:
        row = self.query_one(
            "SELECT COUNT(DISTINCT independence_key) FROM claim_evidence WHERE claim_id = ?",
            (claim_id,),
        )
        return int(row[0]) if row else 0

    def open_core_conflicts(self, video_id: str) -> list[dict[str, Any]]:
        return decode_rows(
            "explainer_claims",
            self.query_all(
                """
                SELECT * FROM explainer_claims
                WHERE video_id = ? AND status = 'DISPUTED' AND importance IN ('CORE','KEY')
                ORDER BY code
                """,
                (video_id,),
            ),
        )

    def unsupported_core_claims(self, video_id: str) -> list[dict[str, Any]]:
        return decode_rows(
            "explainer_claims",
            self.query_all(
                """
                SELECT c.* FROM explainer_claims c
                WHERE c.video_id = ? AND c.importance = 'CORE'
                  AND c.status IN ('UNVERIFIED','DISPUTED','EXCLUDED')
                ORDER BY c.code
                """,
                (video_id,),
            ),
        )

    # ------------------------------------------------------------------ narration
    def segments(self, script_revision_id: str) -> list[dict[str, Any]]:
        return decode_rows(
            "narration_segments",
            self.query_all(
                "SELECT * FROM narration_segments WHERE script_revision_id = ? ORDER BY ordinal",
                (script_revision_id,),
            ),
        )

    def segment_by_canonical(self, video_id: str, canonical_segment_id: str) -> dict[str, Any] | None:
        return decode_row(
            "narration_segments",
            self.query_one(
                "SELECT * FROM narration_segments WHERE video_id = ? AND canonical_segment_id = ? ORDER BY ordinal DESC LIMIT 1",
                (video_id, canonical_segment_id),
            ),
        )

    def selected_takes(self, video_id: str, locale: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM narration_takes WHERE video_id = ? AND selected = 1"
        parameters: list[Any] = [video_id]
        if locale:
            sql += " AND locale = ?"
            parameters.append(locale)
        sql += " ORDER BY canonical_segment_id, take_no"
        return decode_rows("narration_takes", self.query_all(sql, tuple(parameters)))

    def latest_alignment_for_take(self, take_id: str) -> dict[str, Any] | None:
        return decode_row(
            "narration_alignment_revisions",
            self.query_one(
                "SELECT * FROM narration_alignment_revisions WHERE take_id = ? ORDER BY revision_no DESC LIMIT 1",
                (take_id,),
            ),
        )

    def text_locked_segments(self, script_revision_id: str) -> list[dict[str, Any]]:
        return decode_rows(
            "narration_segments",
            self.query_all(
                "SELECT * FROM narration_segments WHERE script_revision_id = ? AND content_locked_by_human = 1",
                (script_revision_id,),
            ),
        )

    # ------------------------------------------------------------------ beats / editions
    def beats(self, video_id: str) -> list[dict[str, Any]]:
        return decode_rows(
            "explainer_visual_beats",
            self.query_all("SELECT * FROM explainer_visual_beats WHERE video_id = ? ORDER BY ordinal", (video_id,)),
        )

    def beat_links(self, video_id: str) -> list[dict[str, Any]]:
        return decode_rows(
            "beat_narration_links",
            self.query_all(
                """
                SELECT l.*, s.canonical_segment_id, s.locale, s.display_text
                FROM beat_narration_links l
                JOIN narration_segments s ON s.id = l.narration_segment_id
                WHERE l.video_id = ?
                ORDER BY l.beat_id, l.ordinal
                """,
                (video_id,),
            ),
        )

    def editions(self, video_id: str) -> list[dict[str, Any]]:
        return decode_rows(
            "explainer_editions",
            self.query_all("SELECT * FROM explainer_editions WHERE video_id = ? ORDER BY edition_key, revision_no DESC", (video_id,)),
        )

    def edition_by_key(self, video_id: str, edition_key: str, revision_no: int | None = None) -> dict[str, Any] | None:
        if revision_no is None:
            return decode_row(
                "explainer_editions",
                self.query_one(
                    "SELECT * FROM explainer_editions WHERE video_id = ? AND edition_key = ? ORDER BY revision_no DESC LIMIT 1",
                    (video_id, edition_key),
                ),
            )
        return decode_row(
            "explainer_editions",
            self.query_one(
                "SELECT * FROM explainer_editions WHERE video_id = ? AND edition_key = ? AND revision_no = ?",
                (video_id, edition_key, revision_no),
            ),
        )

    def active_beat_selection(self, beat_id: str, edition_id: str | None = None) -> dict[str, Any] | None:
        if edition_id is None:
            return decode_row(
                "explainer_beat_selections",
                self.query_one(
                    "SELECT * FROM explainer_beat_selections WHERE beat_id = ? AND status = 'ACTIVE' ORDER BY created_at DESC LIMIT 1",
                    (beat_id,),
                ),
            )
        return decode_row(
            "explainer_beat_selections",
            self.query_one(
                """
                SELECT * FROM explainer_beat_selections
                WHERE beat_id = ? AND status = 'ACTIVE' AND (edition_id IS NULL OR edition_id = ?)
                ORDER BY created_at DESC LIMIT 1
                """,
                (beat_id, edition_id),
            ),
        )

    def has_human_lock(self, beat_id: str) -> bool:
        selection = self.active_beat_selection(beat_id)
        if selection and selection.get("locked_by_human"):
            return True
        beat = self.find("explainer_visual_beats", beat_id)
        return bool(beat and beat.get("locked_by_human"))

    def beats_referencing_identity(self, video_id: str, identity_pack_version_id: str) -> list[dict[str, Any]]:
        return decode_rows(
            "entity_identity_bindings",
            self.query_all(
                """
                SELECT b.* FROM entity_identity_bindings b
                WHERE b.video_id = ? AND b.identity_pack_version_id = ? AND b.status = 'ACTIVE'
                """,
                (video_id, identity_pack_version_id),
            ),
        )

    # ------------------------------------------------------------------ composition
    def compositions(self, edition_id: str) -> list[dict[str, Any]]:
        return decode_rows(
            "composition_revisions",
            self.query_all(
                "SELECT * FROM composition_revisions WHERE edition_id = ? ORDER BY revision_no DESC", (edition_id,)
            ),
        )

    def latest_composition(self, edition_id: str, *, status: str | None = None) -> dict[str, Any] | None:
        sql = "SELECT * FROM composition_revisions WHERE edition_id = ?"
        parameters: list[Any] = [edition_id]
        if status:
            sql += " AND status = ?"
            parameters.append(status)
        sql += " ORDER BY revision_no DESC LIMIT 1"
        return decode_row("composition_revisions", self.query_one(sql, tuple(parameters)))

    def composition_items(self, composition_revision_id: str) -> list[dict[str, Any]]:
        return decode_rows(
            "composition_items",
            self.query_all(
                "SELECT * FROM composition_items WHERE composition_revision_id = ? ORDER BY track, ordinal",
                (composition_revision_id,),
            ),
        )

    def composition_chunks(self, render_id: str) -> list[dict[str, Any]]:
        return decode_rows(
            "composition_render_chunks",
            self.query_all("SELECT * FROM composition_render_chunks WHERE render_id = ? ORDER BY chunk_no", (render_id,)),
        )

    def renders(self, edition_id: str) -> list[dict[str, Any]]:
        return decode_rows(
            "composition_renders",
            self.query_all("SELECT * FROM composition_renders WHERE edition_id = ? ORDER BY revision_no DESC", (edition_id,)),
        )

    def current_root_render(self, edition_id: str) -> dict[str, Any] | None:
        return decode_row(
            "composition_renders",
            self.query_one(
                """
                SELECT * FROM composition_renders
                WHERE edition_id = ? AND integrity_status = 'VERIFIED'
                ORDER BY revision_no DESC LIMIT 1
                """,
                (edition_id,),
            ),
        )

    def import_episode_render_to_composition(
        self,
        *,
        edition_id: str,
        video_id: str,
        project_id: str,
        composition_revision_id: str,
        render_id: str,
        media_asset_id: str,
        media_version_id: str,
        sha256: str,
        frame_count: int,
        duration_ms: int | None,
        rel_path: str | None,
        byte_size: int | None,
        probe: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Register an existing drama render as an explainer composition render.

        Used by the shared-renderer adapter so both surfaces point at one
        immutable manifest hash without copying media.
        """

        manifest = self.get("composition_revisions", composition_revision_id)
        return self.insert(
            "composition_renders",
            {
                "edition_id": edition_id,
                "composition_revision_id": composition_revision_id,
                "video_id": video_id,
                "project_id": project_id,
                "manifest_hash": manifest["manifest_hash"],
                "status": "SUCCEEDED",
                "media_asset_id": media_asset_id,
                "media_version_id": media_version_id,
                "sha256": sha256,
                "frame_count": frame_count,
                "duration_ms": duration_ms,
                "rel_path": rel_path,
                "byte_size": byte_size,
                "probe_json": dict(probe),
                "integrity_status": "VERIFIED",
            },
        )

    # ------------------------------------------------------------------ runs
    def run_by_idempotency(self, project_id: str, idempotency_key: str) -> dict[str, Any] | None:
        return decode_row(
            "explainer_runs",
            self.query_one(
                "SELECT * FROM explainer_runs WHERE project_id = ? AND idempotency_key = ?",
                (project_id, idempotency_key),
            ),
        )

    def steps(self, run_id: str) -> list[dict[str, Any]]:
        return decode_rows(
            "explainer_step_bindings",
            self.query_all(
                """
                SELECT b.*, j.state AS job_state, j.stage_code AS job_stage_code,
                       j.last_error_code AS job_error_code, j.progress_json AS job_progress_json
                FROM explainer_step_bindings b
                LEFT JOIN jobs j ON j.id = b.job_id
                WHERE b.run_id = ?
                ORDER BY b.created_at, b.task_key
                """,
                (run_id,),
            ),
        )

    def steps_for_video(self, video_id: str) -> list[dict[str, Any]]:
        return decode_rows(
            "explainer_step_bindings",
            self.query_all(
                """
                SELECT b.*, j.state AS job_state, j.stage_code AS job_stage_code
                FROM explainer_step_bindings b
                LEFT JOIN jobs j ON j.id = b.job_id
                WHERE b.video_id = ?
                ORDER BY b.created_at, b.task_key
                """,
                (video_id,),
            ),
        )

    def step_by_task_key(self, run_id: str, task_key: str) -> dict[str, Any] | None:
        return decode_row(
            "explainer_step_bindings",
            self.query_one(
                "SELECT * FROM explainer_step_bindings WHERE run_id = ? AND task_key = ?", (run_id, task_key)
            ),
        )

    # ------------------------------------------------------------------ staleness graph
    def add_dependency(
        self,
        *,
        video_id: str,
        project_id: str,
        upstream_kind: str,
        upstream_id: str,
        upstream_hash: str,
        downstream_kind: str,
        downstream_id: str,
        edition_id: str | None = None,
    ) -> dict[str, Any]:
        existing = self.query_one(
            """
            SELECT id FROM artifact_dependencies
            WHERE upstream_kind = ? AND upstream_id = ? AND downstream_kind = ? AND downstream_id = ?
            """,
            (upstream_kind, upstream_id, downstream_kind, downstream_id),
        )
        if existing is not None:
            self.connection.execute(
                """
                UPDATE artifact_dependencies
                SET upstream_hash = ?, stale = 0, stale_reason = NULL, stale_at = NULL,
                    invalidated_by = NULL, updated_at = ?
                WHERE id = ?
                """,
                (upstream_hash, utc_now_iso(), existing["id"]),
            )
            return self.get("artifact_dependencies", str(existing["id"]))
        return self.insert(
            "artifact_dependencies",
            {
                "video_id": video_id,
                "project_id": project_id,
                "edition_id": edition_id,
                "upstream_kind": upstream_kind,
                "upstream_id": upstream_id,
                "upstream_hash": upstream_hash,
                "downstream_kind": downstream_kind,
                "downstream_id": downstream_id,
                "stale": False,
            },
        )

    def mark_dependents_stale(
        self,
        *,
        upstream_kind: str,
        upstream_id: str,
        downstream_kinds: Iterable[str],
        reason: str,
        invalidated_by: str,
    ) -> list[dict[str, Any]]:
        kinds = list(dict.fromkeys(downstream_kinds))
        if not kinds:
            return []
        placeholders = ", ".join("?" for _ in kinds)
        rows = self.query_all(
            f"""
            SELECT * FROM artifact_dependencies
            WHERE upstream_kind = ? AND upstream_id = ? AND downstream_kind IN ({placeholders}) AND stale = 0
            """,
            (upstream_kind, upstream_id, *kinds),
        )
        now = utc_now_iso()
        for row in rows:
            self.connection.execute(
                """
                UPDATE artifact_dependencies
                SET stale = 1, stale_reason = ?, stale_at = ?, invalidated_by = ?, updated_at = ?
                WHERE id = ?
                """,
                (reason, now, invalidated_by, now, row["id"]),
            )
        return [
            {**dict(row), "stale": True, "stale_reason": reason, "invalidated_by": invalidated_by}
            for row in rows
        ]

    def dependents_of(self, *, upstream_kind: str, upstream_id: str) -> list[dict[str, Any]]:
        return decode_rows(
            "artifact_dependencies",
            self.query_all(
                "SELECT * FROM artifact_dependencies WHERE upstream_kind = ? AND upstream_id = ?",
                (upstream_kind, upstream_id),
            ),
        )

    def stale_dependents(self, video_id: str) -> list[dict[str, Any]]:
        return decode_rows(
            "artifact_dependencies",
            self.query_all(
                "SELECT * FROM artifact_dependencies WHERE video_id = ? AND stale = 1", (video_id,)
            ),
        )

    # ------------------------------------------------------------------ qc
    def latest_qc_report(
        self, *, subject_kind: str, subject_revision_id: str, subject_hash: str | None = None
    ) -> dict[str, Any] | None:
        sql = "SELECT * FROM explainer_qc_reports WHERE subject_kind = ? AND subject_revision_id = ?"
        parameters: list[Any] = [subject_kind, subject_revision_id]
        if subject_hash:
            sql += " AND subject_hash = ?"
            parameters.append(subject_hash)
        sql += " ORDER BY created_at DESC LIMIT 1"
        return decode_row("explainer_qc_reports", self.query_one(sql, tuple(parameters)))

    def issues(self, report_id: str, *, statuses: Sequence[str] | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM explainer_qc_issues WHERE report_id = ?"
        parameters: list[Any] = [report_id]
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            sql += f" AND status IN ({placeholders})"
            parameters.extend(statuses)
        sql += " ORDER BY severity, created_at"
        return decode_rows("explainer_qc_issues", self.query_all(sql, tuple(parameters)))

    def open_issues_for_edition(self, edition_id: str) -> list[dict[str, Any]]:
        return decode_rows(
            "explainer_qc_issues",
            self.query_all(
                """
                SELECT * FROM explainer_qc_issues
                WHERE edition_id = ? AND status IN ('OPEN','FIXING')
                ORDER BY CASE severity WHEN 'BLOCKER' THEN 0 WHEN 'MAJOR' THEN 1 WHEN 'MINOR' THEN 2 ELSE 3 END,
                         created_at
                """,
                (edition_id,),
            ),
        )

    def active_decisions(
        self, *, subject_kind: str, subject_revision_id: str, subject_hash: str | None = None
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT * FROM explainer_decisions WHERE subject_kind = ? AND subject_revision_id = ? "
            "AND status = 'ACTIVE' AND stale = 0"
        )
        parameters: list[Any] = [subject_kind, subject_revision_id]
        if subject_hash:
            sql += " AND subject_hash = ?"
            parameters.append(subject_hash)
        sql += " ORDER BY decided_at DESC"
        return decode_rows("explainer_decisions", self.query_all(sql, tuple(parameters)))

    def mark_decisions_stale(
        self, *, subject_kind: str, subject_revision_id: str, reason: str
    ) -> list[dict[str, Any]]:
        rows = self.query_all(
            """
            SELECT * FROM explainer_decisions
            WHERE subject_kind = ? AND subject_revision_id = ? AND status = 'ACTIVE' AND stale = 0
            """,
            (subject_kind, subject_revision_id),
        )
        for row in rows:
            self.connection.execute(
                "UPDATE explainer_decisions SET stale = 1, stale_reason = ?, updated_at = ? WHERE id = ?",
                (reason, utc_now_iso(), row["id"]),
            )
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ schedules
    def due_occurrences(self, *, now_utc: str, limit: int = 50) -> list[dict[str, Any]]:
        return decode_rows(
            "schedule_occurrences",
            self.query_all(
                """
                SELECT o.*, s.timezone, s.code AS schedule_code, s.status AS schedule_status,
                       s.max_concurrent_runs, s.insufficient_topic_policy
                FROM schedule_occurrences o
                JOIN explainer_schedules s ON s.id = o.schedule_id
                WHERE o.status IN ('PENDING','MISSED')
                  AND o.scheduled_for <= ?
                  AND s.status = 'ACTIVE'
                ORDER BY o.scheduled_for
                LIMIT ?
                """,
                (now_utc, int(limit)),
            ),
        )

    def occurrence_by_trigger(self, schedule_id: str, scheduled_for: str) -> dict[str, Any] | None:
        return decode_row(
            "schedule_occurrences",
            self.query_one(
                "SELECT * FROM schedule_occurrences WHERE schedule_id = ? AND scheduled_for = ?",
                (schedule_id, scheduled_for),
            ),
        )

    # ------------------------------------------------------------------ publication
    def package_files_hash(self, files: Sequence[Mapping[str, Any]]) -> str:
        ordered = sorted((dict(item) for item in files), key=lambda item: str(item.get("rel_path", "")))
        return content_hash(ordered)

    def receipt_by_hash(self, package_id: str, package_hash: str) -> list[dict[str, Any]]:
        return decode_rows(
            "publication_receipts",
            self.query_all(
                "SELECT * FROM publication_receipts WHERE package_id = ? AND package_hash = ? ORDER BY attempt_no",
                (package_id, package_hash),
            ),
        )
