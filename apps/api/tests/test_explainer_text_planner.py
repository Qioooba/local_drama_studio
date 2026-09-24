"""First-party local text planner and explainer stage handlers.

These tests drive the four production text stages through a **deterministic fake
LLM client**, so they prove the contracts around the model rather than the model
itself:

* an identifier the model invented is refused instead of persisted;
* evidence must reference a real source span of the packet;
* a ``FACT`` segment without a claim reference is refused;
* a render type outside the frozen capability snapshot is refused instead of
  silently downgraded;
* ``must_be_motion`` can never be satisfied by a still-image render type;
* the handlers write real rows (claims, entities, script revision, beats) and
  produce the report shape the worker consumes.

No model runs here, so nothing in this file is evidence of generated-media or
model-output quality.

Requirement mapping: REQ-05 (facts/evidence), REQ-06 (entities), REQ-07
(script + real narration feedback), REQ-09 (beats/render types).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from local_drama.application.explainers.narration import ExplainerNarrationService
from local_drama.application.explainers.text_planner import (
    BEAT_SCHEMA,
    RESEARCH_KEYWORD_SCHEMA,
    SEGMENT_SCHEMA,
    LocalTextPlanner,
    build_stage_handlers,
)
from local_drama.domain.explainers.contracts import ExplainerContractError, ExplainerErrorCode, ProductKind
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "planner-project"
FICTION_TEXT = (
    "虚构世界设定：1936 年冬夜、岚湾灯塔；地名、人物和事件均为原创虚构。\n\n"
    "沈砚为即将退休的守灯员；林澄为负责交接检查的检修员；周禾为负责记录的学徒；当晚只有这三人。\n\n"
    "21:17:00，林澄进行预定的内部观察窗检查，遮住朝向值班室的内部观察位置，造成值班室观察窗无光。"
)


class FakeClient:
    """Deterministic stand-in for :class:`LocalLLMClient`."""

    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def chat_json(self, system: str, user: str, images: Any = None, *, json_schema: Any = None, inference_options: Any = None) -> dict[str, Any]:
        self.calls.append({"system": system, "user": user, "schema": json_schema, "options": inference_options})
        key = _schema_key(json_schema)
        if key not in self.responses:
            raise AssertionError(f"fake has no response for schema {key}")
        return self.responses[key]


def _schema_key(schema: Any) -> str:
    if schema is RESEARCH_KEYWORD_SCHEMA:
        return "research"
    if schema is SEGMENT_SCHEMA:
        return "script"
    if schema is BEAT_SCHEMA:
        return "beats"
    return "facts"


def _seed(database: Database, *, with_source: bool = True) -> dict[str, Any]:
    """Create one explainer project, video, packet and (optionally) its spans.

    Idempotent: several tests share the module-scoped project id, so the seed
    removes only its own project row first.  Every explainer table cascades from
    ``projects`` (or from ``explainer_videos``), so this never has to guess at a
    delete order and never touches another project.
    """

    with database.transaction() as connection:
        connection.execute("DELETE FROM projects WHERE id=?", (PROJECT_ID,))
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, target_duration_ms,
            product_kind, created_at, updated_at, created_by)
            VALUES (?, 'planner_proj', '灯塔最后一页值班记录', 'DRAFT', 'v2', ?, 300000, ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, PROJECT_ID, ProductKind.EXPLAINER.value),
        )
        connection.execute(
            """INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale, input_kind,
            duration_mode, target_seconds, tolerance_percent, automation_mode, inference_mode, research_mode,
            status, created_at, updated_at, created_by)
            VALUES ('video-1', ?, '灯塔最后一页值班记录', '观察窗为什么短暂无光', 'ORIGINAL_FICTION', 'zh-CN',
            'DOCUMENT_IMPORT', 'FIXED', 300, 0, 'AUTO_WITH_EXCEPTIONS', 'LOCAL_ONLY', 'OFFLINE_IMPORT',
            'DRAFT', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID,),
        )
        repo = ExplainerRepository(connection)
        packet = repo.insert(
            "explainer_research_packets",
            {
                "video_id": "video-1",
                "revision_no": 1,
                "status": "READY",
                "mode": "OFFLINE_IMPORT",
                "topic": "观察窗为什么短暂无光",
                "max_external_requests": 0,
                "content_hash": "a" * 64,
            },
        )
        spans: list[dict[str, Any]] = []
        if with_source:
            source = repo.insert(
                "explainer_sources",
                {
                    "packet_id": packet["id"],
                    "video_id": "video-1",
                    "project_id": PROJECT_ID,
                    "source_kind": "AUTHORED_FICTION_PACK",
                    "title": "原创虚构事实包",
                    "event_date_precision": "SECOND",
                    "fetched_at": "2026-01-01T00:00:00Z",
                    "body_sha256": "b" * 64,
                    "credibility_kind": "AUTHORED_FICTION",
                    "retrieved_via": "USER_SUPPLIED",
                },
            )
            for index, paragraph in enumerate(FICTION_TEXT.split("\n\n")):
                spans.append(
                    repo.insert(
                        "explainer_source_spans",
                        {
                            "source_id": source["id"],
                            "packet_id": packet["id"],
                            "ordinal": index,
                            "start_offset": FICTION_TEXT.index(paragraph),
                            "end_offset": FICTION_TEXT.index(paragraph) + len(paragraph),
                            "quote_text": paragraph,
                            "span_hash": f"{index:064d}",
                            "paragraph_no": index + 1,
                        },
                    )
                )
        return {"packet_id": str(packet["id"]), "span_ids": [str(item["id"]) for item in spans]}


def _planner(responses: dict[str, dict[str, Any]]) -> tuple[LocalTextPlanner, FakeClient]:
    client = FakeClient(responses)
    return LocalTextPlanner(client_factory=lambda: client), client


def _repo_factory(database: Database):
    class _Ctx:
        def __enter__(self) -> ExplainerRepository:
            self._context = database.transaction()
            return ExplainerRepository(self._context.__enter__())

        def __exit__(self, *args: Any) -> Any:
            return self._context.__exit__(*args)

    return _Ctx


def _fact_response(span_id: str) -> dict[str, Any]:
    return {
        "claims": [
            {
                "code": "C001",
                "statement": "当晚只有沈砚、林澄与周禾三人在塔内。",
                "statement_kind": "FICTION",
                "importance": "CORE",
                "confidence_reason": "来自虚构事实包",
                "evidence": [{"source_span_id": span_id, "stance": "SUPPORTS", "note": "第 2 段"}],
            },
            {
                "code": "C002",
                "statement": "21:17:00 的内部观察窗检查造成值班室观察窗无光。",
                "statement_kind": "FICTION",
                "importance": "KEY",
                "evidence": [{"source_span_id": span_id, "stance": "SUPPORTS"}],
            },
        ],
        "events": [
            {
                "code": "E001",
                "title": "内部观察窗检查",
                "story_time_start": "21:17:00",
                "place_label": "值班室",
                "participant_entity_codes": ["ZHOU"],
                "claim_codes": ["C002"],
                "causal_note": "遮住内部观察位置导致观察窗无光",
                "sequence_no": 1,
            }
        ],
        "entities": [
            {
                "code": "ZHOU",
                "name": "周禾",
                "entity_type": "FICTIONAL_CHARACTER",
                "latin_name": "Zhou He",
                "aliases": ["学徒周禾"],
                "descriptive_only": False,
                "state": {"label": "当晚", "age": 19, "wardrobe": "浅灰蓝棉外套"},
            }
        ],
    }


def _script_response() -> dict[str, Any]:
    return {
        "outline": ["观察窗为什么无光：先提出悬念"],
        "segments": [
            {
                "canonical_segment_id": "seg_001",
                "display_text": "一九三六年，一个虚构的冬夜。",
                "spoken_text": "一九三六年，一个虚构的冬夜。",
                "statement_type": "QUESTION",
            },
            {
                "canonical_segment_id": "seg_002",
                "display_text": "当晚只有三个人，谁都没有离开。",
                "statement_type": "FACT",
                "claim_code": "C001",
            },
            {
                "canonical_segment_id": "seg_003",
                "display_text": "1962 年的那次检查造成观察窗无光。",
                "spoken_text": "一九六二年的那次检查造成观察窗无光。",
                "statement_type": "FACT",
                "claim_code": "C002",
                # The map must reproduce the spoken text exactly; the planner
                # refuses a reading it cannot prove equivalent.
                "pronunciation_map": [{"display": "1962", "spoken": "一九六二"}],
            },
        ],
    }


def _beats_response() -> dict[str, Any]:
    return {
        "beats": [
            {
                "code": "B001",
                "render_type": "STILL_MOTION",
                "segment_ids": ["seg_001"],
                "visual_intent": "雨夜中的灯塔剪影",
                "visual_factuality": "FICTIONAL",
                "entity_codes": [],
                "claim_codes": [],
                "must_be_motion": False,
                "prompt_intent": "灰蓝铜色插画风格的灯塔剪影与雨夜",
            },
            {
                "code": "B002",
                "render_type": "STILL_MOTION",
                "segment_ids": ["seg_002", "seg_003"],
                "visual_intent": "值班室三人围着日志",
                "visual_factuality": "FICTIONAL",
                "entity_codes": ["ZHOU"],
                "claim_codes": ["C001", "C002"],
                "must_be_motion": False,
                "prompt_intent": "室内暖光下的三人围桌构图",
            },
        ]
    }


# --------------------------------------------------------------------------- #
# research
# --------------------------------------------------------------------------- #
def test_research_stage_never_sends_offline_queries(database: Database) -> None:
    seeded = _seed(database)
    planner, client = _planner(
        {"research": {"queries": ["岚湾灯塔 1936"], "reference_keywords": ["沈砚", "观察窗"], "notes": "还缺灯光记录"}}
    )
    with database.connect() as connection:
        result = planner.plan_research(
            repo=ExplainerRepository(connection),
            project_id=PROJECT_ID,
            video_id="video-1",
            packet_id=seeded["packet_id"],
        )
    assert result["queries"] == []
    assert result["reference_keywords"] == ["沈砚", "观察窗"]
    assert result["outbound_requests"] == 0
    assert result["network_contacted"] is False
    assert client.calls[0]["schema"] is RESEARCH_KEYWORD_SCHEMA


def test_research_requires_a_real_packet(database: Database) -> None:
    seeded = _seed(database)
    planner, _ = _planner({"research": {"queries": [], "reference_keywords": []}})
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            planner.plan_research(
                repo=ExplainerRepository(connection),
                project_id=PROJECT_ID,
                video_id="video-1",
                packet_id="does-not-exist",
            )
    assert error.value.code == "NOT_FOUND"
    assert seeded["packet_id"]


def test_research_blocks_when_the_packet_has_no_spans(database: Database) -> None:
    seeded = _seed(database, with_source=False)
    planner, _ = _planner({"research": {"queries": [], "reference_keywords": []}})
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            planner.plan_research(
                repo=ExplainerRepository(connection),
                project_id=PROJECT_ID,
                video_id="video-1",
                packet_id=seeded["packet_id"],
            )
    assert error.value.code == ExplainerErrorCode.SOURCE_EVIDENCE_MISSING.value


# --------------------------------------------------------------------------- #
# facts
# --------------------------------------------------------------------------- #
def test_fact_stage_persists_claims_with_real_span_evidence(database: Database) -> None:
    seeded = _seed(database)
    planner, _ = _planner({"facts": _fact_response(seeded["span_ids"][1])})
    handlers = build_stage_handlers(planner_factory=lambda: planner, repo_factory=_repo_factory(database))
    report = handlers["FACT_EXTRACT"](
        {"id": "job-1"},
        {
            "task_code": "FACT_EXTRACT",
            "semantic_inputs": {
                "project_id": PROJECT_ID,
                "video_id": "video-1",
                "packet_id": seeded["packet_id"],
            },
        },
    )
    assert report["machine_check"]["status"] == "PASS"
    assert report["produced"]["claim_count"] == 2
    assert report["produced"]["verified_as_history"] is False
    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        claim = repo.claim_by_code("video-1", "C001")
        assert claim is not None
        assert claim["verified_as_history"] in (0, False)
        evidence = repo.claim_span_records(str(claim["id"]))
        assert len(evidence) == 1
        assert evidence[0]["span_id"] == seeded["span_ids"][1]
        assert repo.independent_evidence_count(str(claim["id"])) == 1
        entity = repo.list_where("explainer_entities", {"video_id": "video-1"})
        assert [item["code"] for item in entity] == ["ZHOU"]
        events = repo.list_where("explainer_events", {"video_id": "video-1"})
        assert events and events[0]["story_time_precision"] == "SECOND"


def test_fact_stage_refuses_an_invented_span_reference(database: Database) -> None:
    seeded = _seed(database)
    response = _fact_response("span-invented-by-the-model")
    planner, _ = _planner({"facts": response})
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            planner.plan_fact_extraction(
                repo=ExplainerRepository(connection),
                project_id=PROJECT_ID,
                video_id="video-1",
                packet_id=seeded["packet_id"],
            )
    assert "不存在的来源片段" in error.value.message


def test_fact_stage_refuses_an_undeclared_model_field(database: Database) -> None:
    seeded = _seed(database)
    response = _fact_response(seeded["span_ids"][0])
    response["claims"][0]["status"] = "SUPPORTED"  # not schema-declared
    planner, _ = _planner({"facts": response})
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            planner.plan_fact_extraction(
                repo=ExplainerRepository(connection),
                project_id=PROJECT_ID,
                video_id="video-1",
                packet_id=seeded["packet_id"],
            )
    assert error.value.code == "SCHEMA_INVALID"
    assert "status" in str(error.value.details)


# --------------------------------------------------------------------------- #
# script
# --------------------------------------------------------------------------- #
def _claim_ledger(database: Database, seeded: dict[str, Any]) -> None:
    planner, _ = _planner({"facts": _fact_response(seeded["span_ids"][1])})
    handlers = build_stage_handlers(planner_factory=lambda: planner, repo_factory=_repo_factory(database))
    handlers["FACT_EXTRACT"](
        {"id": "job-1"},
        {
            "task_code": "FACT_EXTRACT",
            "semantic_inputs": {"project_id": PROJECT_ID, "video_id": "video-1", "packet_id": seeded["packet_id"]},
        },
    )


def test_script_stage_writes_and_freezes_a_revision(database: Database) -> None:
    seeded = _seed(database)
    _claim_ledger(database, seeded)
    planner, client = _planner({"script": _script_response()})
    handlers = build_stage_handlers(planner_factory=lambda: planner, repo_factory=_repo_factory(database))
    report = handlers["NARRATION_WRITE"](
        {"id": "job-2"},
        {"task_code": "NARRATION_WRITE", "semantic_inputs": {"project_id": PROJECT_ID, "video_id": "video-1"}},
    )
    assert report["machine_check"]["status"] == "PASS"
    assert report["produced"]["segment_count"] == 3
    assert report["produced"]["frozen"] is True
    revision_id = report["produced"]["script_revision_id"]
    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        revision = repo.get("explainer_script_revisions", revision_id)
        assert revision["status"] == "FROZEN"
        segments = repo.segments(revision_id)
        assert [item["canonical_segment_id"] for item in segments] == ["seg_001", "seg_002", "seg_003"]
        # A segment stores resolved claim ids, so verify each one points at the
        # claim code the model cited instead of at a raw code string.
        resolved = segments[1]["claim_ids_json"]
        assert len(resolved) == 1
        assert repo.get("explainer_claims", str(resolved[0]))["code"] == "C001"
        assert segments[2]["spoken_text"] != segments[2]["display_text"]
        video = repo.get("explainer_videos", "video-1")
        assert str(video["current_script_revision_id"]) == revision_id
    assert client.calls[0]["schema"] is SEGMENT_SCHEMA


def test_script_stage_refuses_a_fact_segment_without_a_claim(database: Database) -> None:
    seeded = _seed(database)
    _claim_ledger(database, seeded)
    response = _script_response()
    del response["segments"][1]["claim_code"]
    planner, _ = _planner({"script": response})
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            planner.plan_script(repo=ExplainerRepository(connection), project_id=PROJECT_ID, video_id="video-1")
    assert error.value.code == ExplainerErrorCode.SOURCE_EVIDENCE_MISSING.value


def test_script_stage_refuses_an_unknown_claim_code(database: Database) -> None:
    seeded = _seed(database)
    _claim_ledger(database, seeded)
    response = _script_response()
    response["segments"][1]["claim_code"] = "C999"
    planner, _ = _planner({"script": response})
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            planner.plan_script(repo=ExplainerRepository(connection), project_id=PROJECT_ID, video_id="video-1")
    assert "不存在的事实编号" in error.value.message


def test_script_stage_blocks_on_an_unresolved_conflict(database: Database) -> None:
    seeded = _seed(database)
    _claim_ledger(database, seeded)
    with database.transaction() as connection:
        ExplainerRepository(connection).update(
            "explainer_claims",
            str(ExplainerRepository(connection).claim_by_code("video-1", "C002")["id"]),
            {"status": "DISPUTED"},
        )
    planner, _ = _planner({"script": _script_response()})
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            planner.plan_script(repo=ExplainerRepository(connection), project_id=PROJECT_ID, video_id="video-1")
    assert error.value.code == ExplainerErrorCode.CLAIM_CONFLICT.value


def test_script_stage_falls_back_when_a_reading_cannot_be_proven(database: Database) -> None:
    """A plausible but unprovable model reading must not reach the TTS handler."""

    seeded = _seed(database)
    _claim_ledger(database, seeded)
    response = _script_response()
    response["segments"][2]["pronunciation_map"] = [{"display": "unrelated", "spoken": "无关"}]
    planner, _ = _planner({"script": response})
    with database.connect() as connection:
        plan = planner.plan_script(repo=ExplainerRepository(connection), project_id=PROJECT_ID, video_id="video-1")
    normalised = plan["segments"][2]
    assert normalised["spoken_text"] == normalised["display_text"]
    assert normalised["pronunciation_map"] == []
    assert plan["spoken_text_dispositions"][2]["disposition"] == "MODEL_READING_UNPROVABLE_USED_DISPLAY_TEXT"


def test_script_stage_needs_a_claim_ledger_first(database: Database) -> None:
    _seed(database)
    planner, _ = _planner({"script": _script_response()})
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            planner.plan_script(repo=ExplainerRepository(connection), project_id=PROJECT_ID, video_id="video-1")
    assert error.value.code == ExplainerErrorCode.SOURCE_EVIDENCE_MISSING.value


# --------------------------------------------------------------------------- #
# storyboard
# --------------------------------------------------------------------------- #
def _frozen_script(database: Database) -> None:
    seeded = _seed(database)
    _claim_ledger(database, seeded)
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        service = ExplainerNarrationService(repo)
        created = service.create_script_revision(
            project_id=PROJECT_ID,
            video_id="video-1",
            locale="zh-CN",
            title="灯塔",
            outline=["观察窗为什么无光"],
            segments=[
                {"canonical_segment_id": "seg_001", "display_text": "开场提问。", "spoken_text": "开场提问。", "statement_type": "QUESTION"},
                {"canonical_segment_id": "seg_002", "display_text": "当晚只有三个人。", "spoken_text": "当晚只有三个人。", "statement_type": "FACT", "claim_ids": ["C001"]},
                {"canonical_segment_id": "seg_003", "display_text": "检查造成观察窗无光。", "spoken_text": "检查造成观察窗无光。", "statement_type": "FACT", "claim_ids": ["C002"]},
            ],
        )
        revision = created["script_revision"]
        service.freeze_script(script_revision_id=str(revision["id"]), actor="test")
        repo.update("explainer_videos", "video-1", {"current_script_revision_id": str(revision["id"])})


def test_storyboard_stage_creates_a_many_to_many_plan(database: Database) -> None:
    _frozen_script(database)
    planner, _ = _planner({"beats": _beats_response()})
    handlers = build_stage_handlers(planner_factory=lambda: planner, repo_factory=_repo_factory(database))
    report = handlers["EXPLAINER_STORYBOARD"](
        {"id": "job-3"},
        {
            "task_code": "EXPLAINER_STORYBOARD",
            "semantic_inputs": {
                "project_id": PROJECT_ID,
                "video_id": "video-1",
                "usable_render_types": ["STILL_MOTION", "INFOGRAPHIC"],
            },
        },
    )
    assert report["machine_check"]["status"] == "PASS"
    assert report["produced"]["beat_count"] == 2
    assert report["produced"]["timing_authority"] == "PLANNED_HINT_NOT_MEASURED"
    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        beats = repo.beats("video-1")
        assert [item["code"] for item in beats] == ["B001", "B002"]
        links = repo.beat_links("video-1")
        # seg_002 and seg_003 are carried by one beat, and seg_003 exists once:
        # the mapping is many-to-many, not one-to-one.
        assert len(links) == 3
        assert len({str(item["narration_segment_id"]) for item in links}) == 3


def test_storyboard_refuses_a_render_type_outside_the_capability_snapshot(database: Database) -> None:
    _frozen_script(database)
    response = _beats_response()
    response["beats"][1]["render_type"] = "I2V"
    planner, _ = _planner({"beats": response})
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            planner.plan_storyboard(
                repo=ExplainerRepository(connection),
                project_id=PROJECT_ID,
                video_id="video-1",
                usable_render_types=["STILL_MOTION", "INFOGRAPHIC"],
            )
    assert error.value.code == ExplainerErrorCode.CAPABILITY_UNAVAILABLE.value


def test_storyboard_records_a_coerced_motion_requirement_instead_of_claiming_motion(
    database: Database,
) -> None:
    """A still picture type can never be reported as satisfying ``must_be_motion``.

    The planner used to abort the whole run when the model asked for motion but
    declared a still type.  This build's picture path is the deterministic
    still/graphic renderer, so the plan is kept with the declared type, the motion
    requirement is dropped and the degradation is recorded on the beat — the one
    thing that must never happen is a beat that still claims ``must_be_motion``
    while its render type cannot move.
    """

    _frozen_script(database)
    response = _beats_response()
    response["beats"][0]["must_be_motion"] = True
    planner, _ = _planner({"beats": response})
    with database.connect() as connection:
        plan = planner.plan_storyboard(
            repo=ExplainerRepository(connection),
            project_id=PROJECT_ID,
            video_id="video-1",
            usable_render_types=["STILL_MOTION", "INFOGRAPHIC"],
        )
    coerced = list(plan.get("motion_coerced_beats") or [])
    assert coerced, "must_be_motion + still render type must be recorded as coerced"
    coerced_codes = {str(item["code"]) for item in coerced}
    for beat in plan["beats"]:
        if str(beat["code"]) in coerced_codes:
            assert beat["must_be_motion"] is False
            assert beat["motion_requirement_coerced"] is True
        else:
            assert beat["must_be_motion"] is False or beat["render_type"] in ("I2V", "PARALLAX")


def test_storyboard_refuses_an_unknown_segment_or_entity(database: Database) -> None:
    _frozen_script(database)
    response = _beats_response()
    response["beats"][0]["entity_codes"] = ["GHOST"]
    planner, _ = _planner({"beats": response})
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            planner.plan_storyboard(
                repo=ExplainerRepository(connection),
                project_id=PROJECT_ID,
                video_id="video-1",
                usable_render_types=["STILL_MOTION"],
            )
    assert "不存在的实体" in error.value.message

    response = _beats_response()
    response["beats"][0]["segment_ids"] = ["seg_999"]
    planner, _ = _planner({"beats": response})
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            planner.plan_storyboard(
                repo=ExplainerRepository(connection),
                project_id=PROJECT_ID,
                video_id="video-1",
                usable_render_types=["STILL_MOTION"],
            )
    assert "不存在的叙述段落" in error.value.message


def test_storyboard_refuses_a_plan_that_leaves_segments_uncovered(database: Database) -> None:
    """A partial plan is refused, not returned as PASS with a footnote.

    The audit's A07 finding was exactly this: the shortfall was reported in
    ``uncovered_segment_ids`` while the plan still came back ``PASS``, so a long
    script could become a film that silently dropped its tail.
    """

    _frozen_script(database)
    response = _beats_response()
    response["beats"] = response["beats"][:1]
    planner, _ = _planner({"beats": response})
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            planner.plan_storyboard(
                repo=ExplainerRepository(connection),
                project_id=PROJECT_ID,
                video_id="video-1",
                usable_render_types=["STILL_MOTION"],
            )
    assert "没有覆盖全部叙述段落" in error.value.message
    assert error.value.details["uncovered_segment_ids"] == ["seg_002", "seg_003"]


def test_storyboard_refuses_a_plan_that_runs_backwards(database: Database) -> None:
    """Beats may overlap, but the picture clock is allocated in beat order."""

    _frozen_script(database)
    response = _beats_response()
    # B001 now carries the *last* sentence and B002 the first two, so the plan
    # would place the closing narration before the opening narration.
    response["beats"][0]["segment_ids"] = ["seg_003"]
    response["beats"][1]["segment_ids"] = ["seg_001", "seg_002"]
    planner, _ = _planner({"beats": response})
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            planner.plan_storyboard(
                repo=ExplainerRepository(connection),
                project_id=PROJECT_ID,
                video_id="video-1",
                usable_render_types=["STILL_MOTION"],
            )
    assert "画面段顺序与旁白顺序不一致" in error.value.message


class _BatchCoveringClient:
    """Stand-in model that covers exactly the segments one batch prompt lists.

    Batching can only be proven with a model that answers *the batch it was
    given*; replaying one fixed plan would hide a dropped batch.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def chat_json(
        self, system: str, user: str, images: Any = None, *, json_schema: Any = None, inference_options: Any = None
    ) -> dict[str, Any]:
        del system, images, inference_options
        assert _schema_key(json_schema) == "beats"
        self.calls.append(user)
        catalogue = json.loads(user.split("叙述段落清单：\n", 1)[1].split("\n\n实体名录：", 1)[0])
        return {
            "beats": [
                {
                    "code": f"B{index:03d}",
                    "render_type": "STILL_MOTION",
                    "segment_ids": [str(item["canonical_segment_id"])],
                    "visual_intent": f"画面 {index}",
                    "visual_factuality": "FICTIONAL",
                    "entity_codes": [],
                    "claim_codes": [],
                    "must_be_motion": False,
                    "prompt_intent": f"意图 {index}",
                }
                for index, item in enumerate(catalogue, start=1)
            ]
        }


def _frozen_chaptered_script(database: Database, *, segment_count: int, chapter_count: int = 8) -> list[str]:
    """Freeze a long, chaptered script — the audit's 320-segment shape (V08)."""

    seeded = _seed(database)
    _claim_ledger(database, seeded)
    per_chapter = segment_count // chapter_count
    ids = [f"seg_{index:04d}" for index in range(1, segment_count + 1)]
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        service = ExplainerNarrationService(repo)
        created = service.create_script_revision(
            project_id=PROJECT_ID,
            video_id="video-1",
            locale="zh-CN",
            title="长稿",
            outline=["长稿"],
            segments=[
                {
                    "canonical_segment_id": segment_id,
                    "display_text": f"第 {index} 段解说内容。",
                    "spoken_text": f"第 {index} 段解说内容。",
                    "statement_type": "FACT",
                    "claim_ids": ["C001"],
                    "chapter_code": f"CH{(index - 1) // per_chapter + 1:02d}",
                }
                for index, segment_id in enumerate(ids, start=1)
            ],
        )
        revision = created["script_revision"]
        service.freeze_script(script_revision_id=str(revision["id"]), actor="test")
        repo.update("explainer_videos", "video-1", {"current_script_revision_id": str(revision["id"])})
    return ids


def test_storyboard_plans_every_segment_past_the_old_catalogue_cap(database: Database) -> None:
    """V08: a 320-segment script is planned in full, not truncated at 160.

    The old ``_catalogue`` kept the first ``MAX_CATALOGUE_ITEMS`` rows and the
    storyboard returned PASS with the remaining 160 reported as uncovered.
    """

    ids = _frozen_chaptered_script(database, segment_count=320)
    client = _BatchCoveringClient()
    planner = LocalTextPlanner(client_factory=lambda: client)
    with database.connect() as connection:
        plan = planner.plan_storyboard(
            repo=ExplainerRepository(connection),
            project_id=PROJECT_ID,
            video_id="video-1",
            usable_render_types=["STILL_MOTION"],
        )
    assert plan["status"] == "PASS"
    assert plan["uncovered_segment_ids"] == []
    assert plan["segment_count"] == 320
    assert plan["covered_segment_count"] == 320
    # Every segment went to the model, and a 320-segment script needs more than
    # one call: the batches are a partition, not a truncation.
    assert plan["batch_count"] > 1
    seen: list[str] = []
    for beat in plan["beats"]:
        seen.extend(beat["segment_canonical_ids"])
    assert sorted(seen) == sorted(ids)
    # Per-batch ``B001`` numbering must be namespaced before the merge.
    codes = [str(beat["code"]) for beat in plan["beats"]]
    assert len(codes) == len(set(codes))
    assert len(client.calls) == plan["batch_count"]
    for report in plan["batches"]:
        assert report["segment_count"] > 0
    # The batches follow narration order and cover the script exactly once.
    ordered = [report["first_segment_id"] for report in plan["batches"]]
    assert ordered == sorted(ordered, key=lambda item: ids.index(item))
    assert sum(report["segment_count"] for report in plan["batches"]) == 320


def test_storyboard_batches_never_leave_a_batch_unplanned(database: Database) -> None:
    """A batch that returns nothing must fail loudly, not shrink the plan."""

    _frozen_chaptered_script(database, segment_count=130)
    client = _BatchCoveringClient()
    planner = LocalTextPlanner(client_factory=lambda: client)
    seen = {"count": 0}
    covering = _BatchCoveringClient.chat_json

    def empty_on_second(system: str, user: str, images: Any = None, **kwargs: Any) -> dict[str, Any]:
        seen["count"] += 1
        if seen["count"] == 2:
            return {"beats": []}
        return covering(client, system, user, images, **kwargs)

    client.chat_json = empty_on_second  # type: ignore[method-assign]
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            planner.plan_storyboard(
                repo=ExplainerRepository(connection),
                project_id=PROJECT_ID,
                video_id="video-1",
                usable_render_types=["STILL_MOTION"],
            )
    assert "没有返回任何画面段" in error.value.message


def test_storyboard_needs_a_frozen_script(database: Database) -> None:
    _seed(database)
    planner, _ = _planner({"beats": _beats_response()})
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            planner.plan_storyboard(
                repo=ExplainerRepository(connection),
                project_id=PROJECT_ID,
                video_id="video-1",
                usable_render_types=["STILL_MOTION"],
            )
    assert "还没有冻结的讲稿修订" in error.value.message


def test_planner_reports_a_missing_model_as_capability_unavailable(database: Database) -> None:
    from local_drama.domain.errors import DomainRuleError

    _seed(database)

    def broken() -> Any:
        raise DomainRuleError("LLM_UNAVAILABLE", "本机没有可用的离线文本模型")

    planner = LocalTextPlanner(client_factory=broken)
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            planner.plan_script(repo=ExplainerRepository(connection), project_id=PROJECT_ID, video_id="video-1")
    assert error.value.code == ExplainerErrorCode.CAPABILITY_UNAVAILABLE.value


def test_stage_handlers_require_the_declared_inputs(database: Database) -> None:
    planner, _ = _planner({"research": {"queries": [], "reference_keywords": []}})
    handlers = build_stage_handlers(planner_factory=lambda: planner, repo_factory=_repo_factory(database))
    with pytest.raises(ExplainerContractError) as error:
        handlers["RESEARCH_ACQUIRE"]({"id": "job-x"}, {"task_code": "RESEARCH_ACQUIRE", "semantic_inputs": {}})
    assert "缺少必需输入" in error.value.message
    assert Path.cwd().exists()
