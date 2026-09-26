"""Full-text chunked extraction contracts (spec §C3.1, §C3.2, §C9 items 3–4, 7).

What this file proves:

* ``build_evidence_chunks`` is pure: stable source/offset order, chunks composed
  of whole ``source_span`` rows, no ID or span body split, program-assigned
  ``chunk_id``/``chunk_hash``/``ordinal``/``owned_span_ids``/``context_span_ids``;
* coverage excludes ``context_only`` fragments and a resume reuses finished
  chunks by input hash, so a restart loses neither the tail nor a duplicate piece
  of evidence;
* a document longer than the old 24 000-character prefix whose **last** paragraph
  holds a unique key person and a turning point really does produce that entity
  and claim — the tail is no longer dropped;
* the same alias found in several chunks merges to exactly one entity, two
  same-named people stay separate, and an unknown relation becomes a
  pending-review item;
* a model that cites a non-existent span, misses coverage, or references another
  project's media is rejected.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from local_drama.application.explainers.contracts_v2 import (
    CONTENT_EXTRACT_SCHEMA_VERSION,
    alias_declarations_from_text,
    apply_disambiguation_decisions,
    dedupe_evidence,
    dedupe_sources_by_body_sha256,
    entity_merge_candidates,
    plan_disambiguation,
)
from local_drama.application.explainers.evidence_chunks import (
    ANALYSIS_MANIFEST_FILENAME,
    build_evidence_chunks,
    coverage_status,
    empty_manifest,
    pending_chunks,
    read_analysis_manifest,
    record_chunk_result,
    write_analysis_manifest,
)
from local_drama.application.explainers.research import FACT_EXTRACTION_SCHEMA, validate_model_payload
from local_drama.application.explainers.sources import split_paragraph_spans
from local_drama.application.explainers.text_planner import (
    LocalTextPlanner,
    build_stage_handlers,
)
from local_drama.domain.explainers.contracts import ExplainerContractError, ProductKind
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "chunk-project"
VIDEO_ID = "chunk-video"


# --------------------------------------------------------------------------- #
# pure chunking
# --------------------------------------------------------------------------- #
def _span(span_id: str, source_id: str, ordinal: int, start: int, text: str) -> dict[str, Any]:
    return {
        "id": span_id,
        "source_id": source_id,
        "ordinal": ordinal,
        "start_offset": start,
        "end_offset": start + len(text),
        "quote_text": text,
        "span_hash": f"{ordinal:064d}",
    }


def test_chunks_are_whole_spans_in_stable_order() -> None:
    spans = [
        _span("s2", "src-b", 1, 10, "乙" * 40),
        _span("s1", "src-a", 0, 0, "甲" * 40),
        _span("s3", "src-a", 1, 100, "丙" * 40),
    ]
    chunks = build_evidence_chunks(spans, chunk_character_budget=100, context_character_budget=0)
    assert [chunk["owned_span_ids"] for chunk in chunks] == [["s1", "s3"], ["s2"]]
    assert [chunk["ordinal"] for chunk in chunks] == [1, 2]
    assert [chunk["chunk_id"] for chunk in chunks] == ["chk-0001", "chk-0002"]
    assert all(chunk["chunk_hash"] for chunk in chunks)
    # A span body and its id are never split.
    assert {entry["source_span_id"] for chunk in chunks for entry in chunk["spans"]} == {"s1", "s2", "s3"}
    for chunk in chunks:
        for entry in chunk["spans"]:
            assert entry["quote_text"]
            assert entry["end_offset"] - entry["start_offset"] == len(entry["quote_text"])


def test_a_single_oversized_span_is_its_own_chunk_and_never_split() -> None:
    spans = [_span("big", "src-a", 0, 0, "长" * 50_000), _span("small", "src-a", 1, 60_000, "短")]
    chunks = build_evidence_chunks(spans, chunk_character_budget=1_000)
    assert chunks[0]["owned_span_ids"] == ["big"]
    assert chunks[0]["spans"][0]["quote_text"] == "长" * 50_000
    assert chunks[1]["owned_span_ids"] == ["small"]


def test_adjacent_chunks_carry_context_only_material() -> None:
    spans = [_span(f"s{index}", "src-a", index, index * 100, "文" * 60) for index in range(4)]
    chunks = build_evidence_chunks(spans, chunk_character_budget=60, context_character_budget=60)
    assert len(chunks) == 4
    assert chunks[0]["context_span_ids"] == []
    for previous, chunk in zip(chunks, chunks[1:], strict=False):
        assert chunk["context_span_ids"] == previous["owned_span_ids"]
        context_entries = [item for item in chunk["spans"] if item["context_only"]]
        assert [item["source_span_id"] for item in context_entries] == chunk["context_span_ids"]
    owned = [item for chunk in chunks for item in chunk["owned_span_ids"]]
    assert len(owned) == len(set(owned)) == 4


def test_coverage_ignores_context_and_reports_missing_spans() -> None:
    chunks = [
        {"chunk_id": "chk-0001", "owned_span_ids": ["s1", "s2"], "context_span_ids": []},
        {"chunk_id": "chk-0002", "owned_span_ids": ["s3"], "context_span_ids": ["s2"]},
    ]
    manifest = empty_manifest(
        required_span_ids=["s1", "s2", "s3"],
        prompt_version="v",
        contract=CONTENT_EXTRACT_SCHEMA_VERSION,
    )
    manifest = record_chunk_result(
        manifest, chunk=chunks[0], input_hash="h1", response_hash="r1"
    )
    status = coverage_status(chunks, manifest, required_span_ids=["s1", "s2", "s3"])
    assert status["complete"] is False
    assert status["missing_span_ids"] == ["s3"]
    assert status["display"] == "已分析 2/3 段"
    manifest = record_chunk_result(manifest, chunk=chunks[1], input_hash="h2", response_hash="r2")
    assert coverage_status(chunks, manifest, required_span_ids=["s1", "s2", "s3"])["complete"] is True


def test_resume_skips_completed_chunks_and_redoes_changed_input() -> None:
    spans = [_span(f"s{index}", "src-a", index, index * 10, "x" * 10) for index in range(4)]
    chunks = build_evidence_chunks(spans, chunk_character_budget=25, context_character_budget=0)
    manifest = empty_manifest(
        required_span_ids=[item["id"] for item in spans],
        prompt_version="prompt-v1",
        contract=CONTENT_EXTRACT_SCHEMA_VERSION,
    )
    todo = pending_chunks(
        chunks, manifest, prompt_version="prompt-v1", contract=CONTENT_EXTRACT_SCHEMA_VERSION
    )
    assert [item["chunk_id"] for item in todo] == [chunk["chunk_id"] for chunk in chunks]
    hashes = {item["chunk_id"]: item["input_hash"] for item in todo}
    for chunk in chunks:
        manifest = record_chunk_result(
            manifest, chunk=chunk, input_hash=hashes[chunk["chunk_id"]], response_hash="r"
        )
    assert pending_chunks(
        chunks, manifest, prompt_version="prompt-v1", contract=CONTENT_EXTRACT_SCHEMA_VERSION
    ) == []
    # A new prompt version invalidates every chunk's input hash.
    assert len(
        pending_chunks(chunks, manifest, prompt_version="prompt-v2", contract=CONTENT_EXTRACT_SCHEMA_VERSION)
    ) == len(chunks)


def test_manifest_is_written_atomically_and_a_corrupt_file_reads_as_empty(tmp_path: Path) -> None:
    target = tmp_path / ANALYSIS_MANIFEST_FILENAME
    write_analysis_manifest(target, {"chunks": {"chk-0001": {"status": "COMPLETED"}}})
    assert read_analysis_manifest(target) == {"chunks": {"chk-0001": {"status": "COMPLETED"}}}
    target.write_text("{not json", encoding="utf-8")
    assert read_analysis_manifest(target) is None
    assert read_analysis_manifest(tmp_path / "missing.json") is None


# --------------------------------------------------------------------------- #
# §C3.2 merge / alias / disambiguation rules
# --------------------------------------------------------------------------- #
def test_source_dedup_uses_body_sha256_and_keeps_upstream_marker() -> None:
    result = dedupe_sources_by_body_sha256(
        [
            {"id": "a", "body_sha256": "hash", "upstream_source_id": None},
            {"id": "b", "body_sha256": "hash", "upstream_source_id": "a"},
            {"id": "c", "body_sha256": "other", "upstream_source_id": None},
        ]
    )
    assert [item["id"] for item in result["unique"]] == ["a", "c"]
    assert result["duplicates"] == [
        {"source_id": "b", "duplicate_of_source_id": "a", "body_sha256": "hash", "upstream_source_id": "a"}
    ]
    assert result["independent_source_count"] == 2


def test_evidence_dedup_key_is_claim_span_stance() -> None:
    items = [
        {"claim_id": "c1", "source_span_id": "s1", "stance": "SUPPORTS"},
        {"claim_id": "c1", "source_span_id": "s1", "stance": "SUPPORTS"},
        {"claim_id": "c1", "source_span_id": "s1", "stance": "REFUTES"},
    ]
    assert len(dedupe_evidence(items)) == 2


def test_same_alias_in_several_chunks_merges_to_one_entity() -> None:
    entities = [
        {"code": "C01-E001", "name": "林舟", "entity_type": "FICTIONAL_CHARACTER", "aliases": ["守灯员"], "state": None, "ambiguity": None},
        {"code": "C02-E001", "name": "林舟", "entity_type": "FICTIONAL_CHARACTER", "aliases": ["林守灯"], "state": None, "ambiguity": None},
    ]
    candidates = entity_merge_candidates(entities)
    assert len(candidates["merge_candidates"]) == 1
    assert candidates["merge_candidates"][0]["entity_indexes"] == [0, 1]
    merged = apply_disambiguation_decisions(
        entities,
        decisions=[
            {
                "match_key": candidates["merge_candidates"][0]["match_key"],
                "entity_indexes": [0, 1],
                "verdict": "SAME",
            }
        ],
    )
    assert len(merged["entities"]) == 1
    assert set(merged["entities"][0]["aliases"]) == {"林舟", "林守灯", "守灯员"}


def test_two_same_named_people_stay_separate_and_go_to_review() -> None:
    entities = [
        {
            "code": "C01-E001",
            "name": "林舟",
            "entity_type": "REAL_PERSON",
            "aliases": [],
            "state": {"label": "二十岁"},
            "ambiguity": None,
        },
        {
            "code": "C02-E001",
            "name": "林舟",
            "entity_type": "REAL_PERSON",
            "aliases": [],
            "state": {"label": "五十岁"},
            "ambiguity": None,
        },
    ]
    candidates = entity_merge_candidates(entities)
    assert candidates["merge_candidates"] == []
    assert candidates["review_items"]
    assert candidates["review_items"][0]["auto_merged"] is False
    # Same-name without evidence is never a merge signal.
    declarations = alias_declarations_from_text("林舟，又名 林守灯。")
    assert declarations and declarations[0]["basis"] == "EXPLICIT_ALIAS_IN_SOURCE"


def test_disambiguation_metadata_never_erases_a_human_decision() -> None:
    from local_drama.application.explainers.contracts_v2 import merge_decision_metadata

    existing = {"human_decision": "KEEP_SEPARATE", "merge_candidates": [1], "evidence": ["s1"]}
    # An empty machine write must leave the operator's decision untouched.
    assert merge_decision_metadata(existing, {}) == existing
    merged = merge_decision_metadata(existing, {"merge_candidates": [1, 2]})
    assert merged["human_decision"] == "KEEP_SEPARATE"
    assert merged["human_decision_preserved"] is True
    assert merged["merge_candidates"] == [1, 2]
    # With no human decision the machine value is merged in normally.
    machine_only = merge_decision_metadata({"merge_candidates": [1]}, {"merge_candidates": [2]})
    assert machine_only == {"merge_candidates": [2], "human_decision_preserved": False}


def test_repository_disambiguation_write_never_erases_an_existing_decision(database: Database) -> None:
    _seed_long_document(database, tail="最后一页没有关键人物。")
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        entity = repo.insert(
            "explainer_entities",
            {
                "video_id": VIDEO_ID,
                "project_id": PROJECT_ID,
                "code": "E001",
                "entity_type": "REAL_PERSON",
                "name": "林舟",
                "aliases_json": [],
                "fictional": False,
                "descriptive_only": False,
                "disambiguation_json": {"human_decision": "KEEP_SEPARATE", "evidence": ["s1"]},
                "status": "ACTIVE",
            },
        )
        entity_id = str(entity["id"])
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        unchanged = repo.set_disambiguation("explainer_entities", entity_id, {})
        assert unchanged["disambiguation_json"]["human_decision"] == "KEEP_SEPARATE"
        updated = repo.set_disambiguation("explainer_entities", entity_id, {"merge_candidates": [2]})
        assert updated["disambiguation_json"]["human_decision"] == "KEEP_SEPARATE"
        assert updated["disambiguation_json"]["human_decision_preserved"] is True
        assert updated["disambiguation_json"]["merge_candidates"] == [2]


def test_unknown_disambiguation_keeps_both_entities_and_creates_review() -> None:
    candidates = [
        {"match_key": "林舟", "entity_indexes": [0, 1], "entity_type": "REAL_PERSON"},
    ]
    plan = plan_disambiguation(
        candidates=candidates,
        evidence_span_ids=["s1"],
        verdicts=[{"match_key": "林舟", "verdict": "UNKNOWN", "evidence_span_ids": []}],
    )
    assert plan["decisions"][0]["merged"] is False
    assert plan["review_items"][0]["requires_review"] is True
    assert plan["both_entities_kept_on_unknown"] is True
    with pytest.raises(ExplainerContractError):
        plan_disambiguation(
            candidates=candidates,
            evidence_span_ids=["s1"],
            verdicts=[{"match_key": "林舟", "verdict": "PROBABLY", "evidence_span_ids": []}],
        )
    with pytest.raises(ExplainerContractError):
        plan_disambiguation(
            candidates=candidates,
            evidence_span_ids=["s1"],
            verdicts=[{"match_key": "林舟", "verdict": "SAME", "evidence_span_ids": ["s-other"]}],
        )


# --------------------------------------------------------------------------- #
# DB-backed: >24 000 characters, tail preserved, resume, alias merge
# --------------------------------------------------------------------------- #
_TAIL_KEY_PERSON = "林望舒"
_TAIL_RE = re.compile(r"关键人物：([\u4e00-\u9fff]{2,4})")


class RecordingChunkClient:
    """Deterministic stand-in that answers each content-extract chunk.

    The answer cites the *last* span of the chunk it was given and names whoever
    the chunk text declares as 关键人物, so a chunk that never reached the model
    cannot appear in the ledger.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def chat_json(
        self,
        system: str,
        user: str,
        images: Any = None,
        *,
        json_schema: Any = None,
        inference_options: Any = None,
    ) -> dict[str, Any]:
        del images
        self.calls.append({"system": system, "user": user, "schema": json_schema, "options": inference_options})
        span_ids = re.findall(r'"source_span_id": "([^"]+)"', user)
        assert span_ids, "the prompt must contain the chunk's spans"
        names = _TAIL_RE.findall(user)
        sid = span_ids[-1]
        if names:
            entities = [
                {
                    "name": names[-1],
                    "entity_type": "REAL_PERSON",
                    "same_as_entity_id": None,
                    "aliases": ["守灯人"],
                    "source_span_ids": [sid],
                    "known_appearance": [],
                    "unknown_attributes": ["准确外貌"],
                    "state": None,
                    "ambiguity": None,
                }
            ]
            claims = [
                {
                    "statement": f"{names[-1]}在最后一班记录里留下了转折点。",
                    "statement_kind": "FACT",
                    "importance": "KEY",
                    "entity_indexes": [0],
                    "evidence": [{"source_span_id": sid, "stance": "SUPPORTS"}],
                }
            ]
        else:
            entities = []
            claims = []
        return {
            "schema_version": CONTENT_EXTRACT_SCHEMA_VERSION,
            "entities": entities,
            "claims": claims,
            "events": [],
            "ambiguities": [],
        }


def _seed_long_document(database: Database, *, tail: str) -> dict[str, Any]:
    body_parts = []
    filler = "灯塔的值班记录逐页抄写着当年的潮汐、风向与灯号，天气与检修安排都按原文保留。"
    while sum(len(part) for part in body_parts) < 30_000:
        body_parts.append(filler)
    body_parts.append(tail)
    body = "\n\n".join(body_parts)
    with database.transaction() as connection:
        connection.execute("DELETE FROM projects WHERE id=?", (PROJECT_ID,))
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, target_duration_ms,
            product_kind, created_at, updated_at, created_by)
            VALUES (?, 'chunk_proj', '灯塔长文', 'DRAFT', 'v2', ?, 300000, ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, PROJECT_ID, ProductKind.EXPLAINER.value),
        )
        connection.execute(
            """INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale, input_kind,
            duration_mode, target_seconds, tolerance_percent, automation_mode, inference_mode, research_mode,
            status, created_at, updated_at, created_by)
            VALUES (?, ?, '灯塔长文', '灯塔记录', 'FACTUAL_EXPLAINER', 'zh-CN',
            'DOCUMENT_IMPORT', 'FIXED', 300, 0, 'AUTO_WITH_EXCEPTIONS', 'LOCAL_ONLY', 'OFFLINE_IMPORT',
            'DRAFT', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (VIDEO_ID, PROJECT_ID),
        )
        repo = ExplainerRepository(connection)
        packet = repo.insert(
            "explainer_research_packets",
            {
                "video_id": VIDEO_ID,
                "revision_no": 1,
                "status": "READY",
                "mode": "OFFLINE_IMPORT",
                "topic": "灯塔记录",
                "max_external_requests": 0,
                "content_hash": "a" * 64,
            },
        )
        source = repo.insert(
            "explainer_sources",
            {
                "packet_id": packet["id"],
                "video_id": VIDEO_ID,
                "project_id": PROJECT_ID,
                "source_kind": "DOCUMENT_IMPORT",
                "title": "长文来源",
                "event_date_precision": "SECOND",
                "fetched_at": "2026-01-01T00:00:00Z",
                "body_sha256": "b" * 64,
                "credibility_kind": "SECONDARY",
                "retrieved_via": "USER_SUPPLIED",
            },
        )
        for span in split_paragraph_spans(body, max_span_chars=1_200):
            repo.insert(
                "explainer_source_spans",
                {
                    "source_id": source["id"],
                    "packet_id": packet["id"],
                    "ordinal": span["ordinal"],
                    "start_offset": span["start_offset"],
                    "end_offset": span["end_offset"],
                    "quote_text": span["quote_text"],
                    "span_hash": span["span_hash"],
                    "paragraph_no": span["paragraph_no"],
                },
            )
    return {"packet_id": str(packet["id"]), "content": body}


def _repo_factory(database: Database):
    class _Ctx:
        def __enter__(self) -> ExplainerRepository:
            self._context = database.transaction()
            return ExplainerRepository(self._context.__enter__())

        def __exit__(self, *args: Any) -> Any:
            return self._context.__exit__(*args)

    return _Ctx


def test_document_longer_than_the_old_prefix_limit_keeps_its_tail(
    database: Database, tmp_path: Path
) -> None:
    seeded = _seed_long_document(
        database, tail=f"最后一页：关键人物：{_TAIL_KEY_PERSON}，他记下了全片唯一的转折点。"
    )
    assert len(seeded["content"]) > 24_000
    client = RecordingChunkClient()
    planner = LocalTextPlanner(client_factory=lambda: client)
    handlers = build_stage_handlers(planner_factory=lambda: planner, repo_factory=_repo_factory(database))
    report = handlers["FACT_EXTRACT"](
        {"id": "job-chunk"},
        {
            "task_code": "FACT_EXTRACT",
            "output_root": tmp_path,
            "semantic_inputs": {
                "project_id": PROJECT_ID,
                "video_id": VIDEO_ID,
                "packet_id": seeded["packet_id"],
            },
        },
    )
    produced = report["produced"]
    assert produced["coverage"]["complete"] is True
    assert produced["chunk_count"] > 2
    assert produced["claim_count"] >= 1
    assert (tmp_path / ANALYSIS_MANIFEST_FILENAME).is_file()
    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        names = [str(item["name"]) for item in repo.list_where("explainer_entities", {"video_id": VIDEO_ID})]
        assert _TAIL_KEY_PERSON in names
        claims = repo.list_where("explainer_claims", {"video_id": VIDEO_ID})
        tail_claim = next((item for item in claims if _TAIL_KEY_PERSON in str(item["statement"])), None)
        assert tail_claim is not None
        assert len(repo.claim_span_records(str(tail_claim["id"]))) == 1
    assert len(client.calls) == produced["chunk_count"]


def test_resume_loses_no_tail_and_duplicates_no_evidence(database: Database, tmp_path: Path) -> None:
    seeded = _seed_long_document(
        database, tail=f"最后一页：关键人物：{_TAIL_KEY_PERSON}，他记下了全片唯一的转折点。"
    )
    client = RecordingChunkClient()
    planner = LocalTextPlanner(client_factory=lambda: client)
    handlers = build_stage_handlers(planner_factory=lambda: planner, repo_factory=_repo_factory(database))
    context = {
        "task_code": "FACT_EXTRACT",
        "output_root": tmp_path,
        "semantic_inputs": {
            "project_id": PROJECT_ID,
            "video_id": VIDEO_ID,
            "packet_id": seeded["packet_id"],
        },
    }
    first = handlers["FACT_EXTRACT"]({"id": "job-1"}, context)
    calls_after_first = len(client.calls)
    second = handlers["FACT_EXTRACT"]({"id": "job-2"}, context)

    assert second["produced"]["chunks_processed_this_run"] == []
    assert len(client.calls) == calls_after_first, "a resumed run must not re-call the model"
    assert second["produced"]["coverage"]["complete"] is True
    assert second["produced"]["claim_count"] == first["produced"]["claim_count"]
    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        names = [str(item["name"]) for item in repo.list_where("explainer_entities", {"video_id": VIDEO_ID})]
        assert _TAIL_KEY_PERSON in names
        claims = repo.list_where("explainer_claims", {"video_id": VIDEO_ID})
        assert len(claims) == first["produced"]["claim_count"]
        for claim in claims:
            records = repo.claim_span_records(str(claim["id"]))
            assert len(records) == len({str(item["span_id"]) for item in records})
    # A partial manifest cannot be mistaken for success.
    manifest = read_analysis_manifest(tmp_path / ANALYSIS_MANIFEST_FILENAME)
    assert manifest is not None and manifest["status"] == "COMPLETED"


def test_a_failed_chunk_saves_the_rest_and_is_resumed_later(
    database: Database, tmp_path: Path
) -> None:
    """A crash mid-document must not look like success, and must not lose work."""

    seeded = _seed_long_document(
        database, tail=f"最后一页：关键人物：{_TAIL_KEY_PERSON}，他记下了全片唯一的转折点。"
    )

    class FailsOnThirdChunk(RecordingChunkClient):
        def chat_json(self, system, user, images=None, *, json_schema=None, inference_options=None):
            if len(self.calls) >= 2:
                raise ExplainerContractError("CAPABILITY_UNAVAILABLE", "本机文本模型不可用")
            return super().chat_json(
                system, user, images, json_schema=json_schema, inference_options=inference_options
            )

    failing = FailsOnThirdChunk()
    planner = LocalTextPlanner(client_factory=lambda: failing)
    handlers = build_stage_handlers(planner_factory=lambda: planner, repo_factory=_repo_factory(database))
    context = {
        "task_code": "FACT_EXTRACT",
        "output_root": tmp_path,
        "semantic_inputs": {
            "project_id": PROJECT_ID,
            "video_id": VIDEO_ID,
            "packet_id": seeded["packet_id"],
        },
    }
    with pytest.raises(ExplainerContractError):
        handlers["FACT_EXTRACT"]({"id": "job-partial"}, context)
    manifest = read_analysis_manifest(tmp_path / ANALYSIS_MANIFEST_FILENAME)
    assert manifest is not None
    assert manifest["status"] == "PARTIAL"
    completed = [
        key for key, value in manifest["chunks"].items() if value.get("status") == "COMPLETED"
    ]
    assert len(completed) == 2
    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        assert repo.list_where("explainer_claims", {"video_id": VIDEO_ID}) == []

    healthy = RecordingChunkClient()
    resumed = build_stage_handlers(
        planner_factory=lambda: LocalTextPlanner(client_factory=lambda: healthy),
        repo_factory=_repo_factory(database),
    )
    report = resumed["FACT_EXTRACT"]({"id": "job-resume"}, context)
    assert report["produced"]["coverage"]["complete"] is True
    # Only the chunks that never completed were re-sent.
    assert len(healthy.calls) == report["produced"]["chunk_count"] - 2
    assert report["produced"]["chunks_processed_this_run"]
    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        names = [str(item["name"]) for item in repo.list_where("explainer_entities", {"video_id": VIDEO_ID})]
        assert _TAIL_KEY_PERSON in names
    final = read_analysis_manifest(tmp_path / ANALYSIS_MANIFEST_FILENAME)
    assert final is not None and final["status"] == "COMPLETED"


def test_invented_span_reference_is_rejected(database: Database, tmp_path: Path) -> None:
    seeded = _seed_long_document(database, tail="最后一页没有关键人物。")

    class InventingClient:
        def chat_json(self, system, user, images=None, *, json_schema=None, inference_options=None):
            del system, user, images, json_schema, inference_options
            return {
                "schema_version": CONTENT_EXTRACT_SCHEMA_VERSION,
                "entities": [],
                "claims": [
                    {
                        "statement": "引用了一个不存在的片段。",
                        "statement_kind": "FACT",
                        "importance": "KEY",
                        "entity_indexes": [],
                        "evidence": [{"source_span_id": "span-invented-by-the-model", "stance": "SUPPORTS"}],
                    }
                ],
                "events": [],
                "ambiguities": [],
            }

    planner = LocalTextPlanner(client_factory=lambda: InventingClient())
    handlers = build_stage_handlers(planner_factory=lambda: planner, repo_factory=_repo_factory(database))
    with pytest.raises(ExplainerContractError) as error:
        handlers["FACT_EXTRACT"](
            {"id": "job-invent"},
            {
                "task_code": "FACT_EXTRACT",
                "output_root": tmp_path,
                "semantic_inputs": {
                    "project_id": PROJECT_ID,
                    "video_id": VIDEO_ID,
                    "packet_id": seeded["packet_id"],
                },
            },
        )
    assert "未提供的 ID" in error.value.message or "不存在的来源片段" in error.value.message


def test_cross_project_media_reference_is_refused(database: Database) -> None:
    """A media version outside this project is refused, never silently resolved."""

    _seed_long_document(database, tail="最后一页没有关键人物。")
    # Another project owns this media version id; from THIS project it resolves to
    # nothing, which must be a structured refusal rather than a silent pass.
    with pytest.raises(ExplainerContractError) as error:
        with database.connect() as connection:
            ExplainerRepository(connection).require_same_project_media(
                project_id=PROJECT_ID, media_version_id="media-version-of-another-project"
            )
    assert error.value.code in {"NOT_FOUND", "INVALID_REQUEST"}
    assert error.value.details["media_version_id"] == "media-version-of-another-project"


def test_mapping_output_still_validates_as_the_legacy_ledger(database: Database, tmp_path: Path) -> None:
    seeded = _seed_long_document(
        database, tail=f"最后一页：关键人物：{_TAIL_KEY_PERSON}，他记下了全片唯一的转折点。"
    )
    client = RecordingChunkClient()
    planner = LocalTextPlanner(client_factory=lambda: client)
    with database.connect() as connection:
        plan = planner.plan_fact_extraction(
            repo=ExplainerRepository(connection),
            project_id=PROJECT_ID,
            video_id=VIDEO_ID,
            packet_id=seeded["packet_id"],
            artifact_dir=tmp_path,
        )
    validate_model_payload(plan["extracted"], FACT_EXTRACTION_SCHEMA, scope="fact_extraction")
    assert json.dumps(plan["extracted"], ensure_ascii=False)
    assert plan["entity_merge_candidates"] == []
    assert plan["context_only_excluded_from_coverage"] is True
