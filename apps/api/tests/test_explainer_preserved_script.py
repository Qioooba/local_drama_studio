"""Preserved-script mode: the program keeps every character (spec §C4.1, §C9 item 5).

What this file proves:

* the deterministic split keeps digits, quotes, ellipses, newlines and leading /
  trailing whitespace, and reconstructing ``display_text + separator`` (plus the
  leading separator) reproduces the manuscript byte-for-byte with a matching
  SHA-256;
* a "nearly equal" reconstruction is refused outright;
* ``spoken_text`` defaults to the display text and is derived only from an
  explicit ``pronunciation_map``; the display body is never stripped or rewritten;
* the ``NARRATION_WRITE`` handler takes the preserved branch for
  ``script_policy=PRESERVE_ORIGINAL``: it makes **no** model call, performs no
  length expansion (``max_script_revisions = 0``), and the manuscript hash is
  unchanged after the step;
* registering the same manuscript twice reuses the finished revision instead of
  creating a second one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from local_drama.application.explainers import contracts_v2 as contracts
from local_drama.application.explainers.commands import (
    ExplainerCreateCommand,
    build_explainer_creation_service,
)
from local_drama.application.explainers.narration import ExplainerNarrationService
from local_drama.application.explainers.sources import (
    canonical_script_source_text,
    canonical_body_offset_map,
    normalise_document_text,
    script_source_hash,
)
from local_drama.application.explainers.text_planner import LocalTextPlanner, build_stage_handlers
from local_drama.domain.explainers.contracts import ExplainerContractError, text_hash
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

MANUSCRIPT = (
    "\ufeff  第 1 段：1962 年 3 月 14 日，灯塔在雨夜里重新亮起。\r\n"
    "第二段：“引用”与省略号…… 数字 21:17 与 3.14 都必须原样保留。\r\n"
    "\r\n"
    "\u3000\u3000第三段：末尾有两个空格与制表符\t\n   "
)


class RecordingClient:
    """Records calls and always answers with a forbidden rewrite.

    If preserved mode ever reached the model for a rewrite, the returned body
    would differ from the manuscript and the assertions below would fail.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def chat_json(self, system, user, images=None, *, json_schema=None, inference_options=None):
        self.calls.append({"system": system, "user": user})
        return {
            "schema_version": contracts.SCRIPT_DRAFT_SCHEMA_VERSION,
            "outline": [],
            "segments": [
                {
                    "chapter_index": 0,
                    "display_text": "模型改写后的正文",
                    "statement_type": "FACT",
                    "claim_ids": [],
                    "entity_ids": [],
                    "pronunciation_suggestions": [],
                    "pause_after_ms": 0,
                }
            ],
            "insufficient_content": False,
            "missing_content_note": "",
        }


def _repo_factory(database: Database):
    class _Ctx:
        def __enter__(self) -> ExplainerRepository:
            self._context = database.transaction()
            return ExplainerRepository(self._context.__enter__())

        def __exit__(self, *args: Any) -> Any:
            return self._context.__exit__(*args)

    return _Ctx


@pytest.fixture()
def preserved_project(database: Database, workspace: Any) -> dict[str, Any]:
    service = build_explainer_creation_service(database, workspace)
    command = ExplainerCreateCommand(
        title="雨夜灯塔",
        input_kind="PASTED_SCRIPT",
        script_policy="PRESERVE_ORIGINAL",
        pasted_text=MANUSCRIPT,
        outputs=({"edition_key": "main", "voice_locale": "zh-CN"},),
    )
    created = service.create_workspace(command, idempotency_key="preserve-1")
    return {
        "project_id": str(created["project"]["id"]),
        "video_id": str(created["video"]["id"]),
        "script_revision_id": str(created["video"]["current_script_revision_id"]),
        "input_payload": created["video"]["input_payload_json"],
    }


# --------------------------------------------------------------------------- #
# deterministic slicing
# --------------------------------------------------------------------------- #
def test_split_preserves_every_character_and_the_hash() -> None:
    expected = canonical_script_source_text(MANUSCRIPT)
    built = contracts.build_preserved_segments(expected)
    assert built["script_source_hash"] == script_source_hash(MANUSCRIPT) == text_hash(expected)
    rebuilt = built["leading_separator"] + "".join(
        item["display_text"] + item["separator"] for item in built["segments"]
    )
    assert rebuilt == expected
    assert all(
        item["display_text"] == expected[item["source_start"] : item["source_end"]]
        for item in built["segments"]
    )
    joined = "".join(item["display_text"] for item in built["segments"])
    for token in ("1962", "21:17", "3.14", "“引用”", "……", "\t"):
        assert token in joined
    assert built["leading_separator"] == "  "
    assert built["segments"][-1]["separator"].endswith("   ")
    assert "\t" in built["segments"][-1]["display_text"]
    assert any("\n" in item["separator"] for item in built["segments"])
    contracts.validate_preserved_concatenation(
        built["segments"],
        script_source_text=expected,
        leading_separator=built["leading_separator"],
    )


def test_approximately_equal_reconstruction_is_refused() -> None:
    expected = canonical_script_source_text(MANUSCRIPT)
    built = contracts.build_preserved_segments(expected)
    tampered = [dict(item) for item in built["segments"]]
    tampered[0]["display_text"] = tampered[0]["display_text"][:-1]  # drop one character
    with pytest.raises(ExplainerContractError) as error:
        contracts.validate_preserved_concatenation(
            tampered, script_source_text=expected, leading_separator=built["leading_separator"]
        )
    assert error.value.details["expected_hash"] != error.value.details["rebuilt_hash"]
    # A punctuation-only change is also refused, not "normalised away".
    tampered = [dict(item) for item in built["segments"]]
    tampered[0]["display_text"] = tampered[0]["display_text"].replace("。", ".")
    with pytest.raises(ExplainerContractError):
        contracts.validate_preserved_concatenation(
            tampered, script_source_text=expected, leading_separator=built["leading_separator"]
        )


def test_spoken_text_is_derived_only_from_an_explicit_map() -> None:
    text = "1962 年的检查造成观察窗无光。"
    built = contracts.build_preserved_segments(
        text, pronunciation_map=[{"display": "1962", "spoken": "一九六二"}]
    )
    segment = built["segments"][0]
    assert segment["display_text"] == text
    assert segment["spoken_text"] == "一九六二 年的检查造成观察窗无光。"
    assert segment["pronunciation_map"] == [{"display": "1962", "spoken": "一九六二"}]
    # Without a map the spoken form is the body itself, byte-for-byte.
    plain = contracts.build_preserved_segments(text)["segments"][0]
    assert plain["spoken_text"] == plain["display_text"] == text


def test_exact_mode_of_span_equivalence_rejects_a_rewritten_body() -> None:
    service = ExplainerNarrationService.__new__(ExplainerNarrationService)
    service._assert_span_equivalence(  # noqa: SLF001 - the exact mode is the unit under test
        display_text="灯塔亮起。",
        spoken_text="灯塔亮起。",
        pronunciation_map=[],
        exact_body="灯塔亮起。",
    )
    with pytest.raises(ExplainerContractError) as error:
        service._assert_span_equivalence(  # noqa: SLF001
            display_text="灯塔亮起",  # the trailing full stop was dropped
            spoken_text="灯塔亮起",
            pronunciation_map=[],
            exact_body="灯塔亮起。",
        )
    assert error.value.details["preserved_mode"] is True


def test_offset_map_between_exact_source_and_evidence_body_is_explicit() -> None:
    canonical = canonical_script_source_text(MANUSCRIPT)
    evidence = normalise_document_text(MANUSCRIPT)
    mapping = canonical_body_offset_map(MANUSCRIPT, evidence)
    assert mapping["explicit_offset_mapping"] is True
    assert mapping["bom_normalised"] is True
    assert mapping["body_is_substring"] is True
    body_without_bom = evidence[1:] if evidence.startswith("\ufeff") else evidence
    shift = mapping["offset_shift"]
    assert canonical[shift : shift + len(body_without_bom)] == body_without_bom
    # The BOM carried the manuscript's own leading spaces at offset 0 once dropped.
    assert mapping["leading_whitespace_characters"] == 0
    plain = canonical_body_offset_map("  正文  ", "正文")
    assert plain["offset_shift"] == 2
    assert plain["bom_normalised"] is False
    assert plain["body_is_substring"] is True


# --------------------------------------------------------------------------- #
# creation registers the manuscript before any preflight can demand it
# --------------------------------------------------------------------------- #
def test_creation_registers_a_frozen_preserved_revision(
    database: Database, preserved_project: dict[str, Any]
) -> None:
    payload = preserved_project["input_payload"]
    assert payload["script_policy"] == "PRESERVE_ORIGINAL"
    exact = payload["preserved_original"]
    assert exact["script_source_text"] == canonical_script_source_text(MANUSCRIPT)
    assert exact["normalisation"] == "newlines_lf_only"
    assert exact["whitespace_preserved"] is True
    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        revision = repo.get("explainer_script_revisions", preserved_project["script_revision_id"])
        assert revision["status"] == "FROZEN"
        provenance = revision["provenance_json"]
        assert provenance["preserved_original"] is True
        assert provenance["script_source_hash"] == exact["script_source_hash"]
        assert provenance["max_script_revisions"] == 0
        assert provenance["length_expansion_requested"] is False
        segments = repo.segments(str(revision["id"]))
        span_map = {item["canonical_segment_id"]: item for item in provenance["segment_spans"]}
        rebuilt = provenance["leading_separator"] + "".join(
            item["display_text"] + span_map[item["canonical_segment_id"]]["separator"] for item in segments
        )
        assert rebuilt == exact["script_source_text"]
        assert str(repo.get("explainer_videos", preserved_project["video_id"])["current_script_revision_id"]) == str(
            revision["id"]
        )


# --------------------------------------------------------------------------- #
# the NARRATION_WRITE handler takes the preserved branch
# --------------------------------------------------------------------------- #
def test_narration_write_never_calls_a_model_or_expands_length(
    database: Database, preserved_project: dict[str, Any]
) -> None:
    client = RecordingClient()
    planner = LocalTextPlanner(client_factory=lambda: client)
    handlers = build_stage_handlers(planner_factory=lambda: planner, repo_factory=_repo_factory(database))
    report = handlers["NARRATION_WRITE"](
        {"id": "job-preserved"},
        {
            "task_code": "NARRATION_WRITE",
            "semantic_inputs": {
                "project_id": preserved_project["project_id"],
                "video_id": preserved_project["video_id"],
            },
        },
    )
    assert client.calls == [], "preserved mode must not send the manuscript to the model"
    assert report["status"] == "PASS"
    produced = report["produced"]
    assert produced["preserved"] is True
    assert produced["script_policy"] == "PRESERVE_ORIGINAL"
    assert produced["max_script_revisions"] == 0
    assert produced["length_expansion_requested"] is False
    assert produced["script_source_hash"] == preserved_project["input_payload"]["preserved_original"]["script_source_hash"]

    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        revision = repo.get("explainer_script_revisions", produced["script_revision_id"])
        assert revision["status"] == "FROZEN"
        segments = repo.segments(str(revision["id"]))
        span_map = {item["canonical_segment_id"]: item for item in revision["provenance_json"]["segment_spans"]}
        rebuilt = revision["provenance_json"]["leading_separator"] + "".join(
            item["display_text"] + span_map[item["canonical_segment_id"]]["separator"] for item in segments
        )
        assert rebuilt == preserved_project["input_payload"]["preserved_original"]["script_source_text"]
        assert text_hash(rebuilt) == preserved_project["input_payload"]["preserved_original"]["script_source_hash"]
        # Every segment's spoken form is its own body: no reading is ever invented
        # for a preserved manuscript without an explicit map.
        for segment in segments:
            assert segment["spoken_text"] == segment["display_text"]
            assert segment["pronunciation_map_json"] == []


def test_registering_the_same_manuscript_twice_reuses_the_revision(
    database: Database, workspace: Any
) -> None:
    service = build_explainer_creation_service(database, workspace)
    created = service.create_workspace(
        ExplainerCreateCommand(
            title="普通改写作品",
            input_kind="PASTED_SCRIPT",
            script_policy="ADAPT_SOURCES",
            pasted_text=MANUSCRIPT,
            outputs=({"edition_key": "main"},),
        ),
        idempotency_key="reuse-preserve",
    )
    project_id = str(created["project"]["id"])
    video_id = str(created["video"]["id"])
    with database.transaction() as connection:
        first = ExplainerNarrationService(ExplainerRepository(connection)).register_preserved_script(
            project_id=project_id,
            video_id=video_id,
            locale="zh-CN",
            title="普通改写作品",
            script_source_text=canonical_script_source_text(MANUSCRIPT),
            actor="test",
        )
    with database.transaction() as connection:
        second = ExplainerNarrationService(ExplainerRepository(connection)).register_preserved_script(
            project_id=project_id,
            video_id=video_id,
            locale="zh-CN",
            title="普通改写作品",
            script_source_text=canonical_script_source_text(MANUSCRIPT),
            actor="test",
        )
    assert first["reused"] is False
    assert second["reused"] is True
    assert str(first["script_revision"]["id"]) == str(second["script_revision"]["id"])
    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        revisions = repo.list_where("explainer_script_revisions", {"video_id": video_id})
        assert len(revisions) == 1


def test_plan_preserved_script_reports_the_mapping_and_refuses_rewrites(
    database: Database, preserved_project: dict[str, Any]
) -> None:
    client = RecordingClient()
    planner = LocalTextPlanner(client_factory=lambda: client)
    with database.connect() as connection:
        plan = planner.plan_preserved_script(
            repo=ExplainerRepository(connection),
            project_id=preserved_project["project_id"],
            video_id=preserved_project["video_id"],
            pronunciation_map=[{"display": "1962", "spoken": "一九六二"}],
        )
    assert client.calls == []
    assert plan["preserved"] is True
    assert plan["length_expansion_requested"] is False
    assert plan["max_script_revisions"] == 0
    assert plan["rewrite_forbidden"] is True
    assert plan["display_text_is_program_slice"] is True
    assert plan["validation"]["concatenation_exact"] is True
    assert plan["script_source_hash"] == preserved_project["input_payload"]["preserved_original"]["script_source_hash"]
    mapped = [item for item in plan["segments"] if item["pronunciation_map"]]
    assert mapped and mapped[0]["spoken_text"] != mapped[0]["display_text"]
    assert all(item["source_end"] - item["source_start"] == len(item["display_text"]) for item in plan["segments"])
    assert json.dumps(plan["span_map"], ensure_ascii=False)


def test_annotations_may_only_label_and_never_cover_the_body(database: Database, workspace: Any) -> None:
    """The annotation path enforces one-per-segment and substring membership."""

    service = build_explainer_creation_service(database, workspace)
    created = service.create_workspace(
        ExplainerCreateCommand(
            title="标注作品",
            input_kind="PASTED_SCRIPT",
            script_policy="ADAPT_SOURCES",
            pasted_text="第一句。第二句。",
            outputs=({"edition_key": "main"},),
        ),
        idempotency_key="annotate-1",
    )
    project_id = str(created["project"]["id"])
    video_id = str(created["video"]["id"])
    good = {
        "schema_version": contracts.PRESERVED_SCRIPT_SCHEMA_VERSION,
        "annotations": [
            {
                "canonical_segment_id": "seg_001",
                "claim_ids": [],
                "entity_ids": [],
                "statement_type": "FACT",
                "pronunciation_suggestions": [],
                "pause_after_ms": 250,
                "unverified_phrases": [{"phrase": "第一句", "reason": "来源不足"}],
            },
            {
                "canonical_segment_id": "seg_002",
                "claim_ids": [],
                "entity_ids": [],
                "statement_type": "FACT",
                "pronunciation_suggestions": [],
                "pause_after_ms": 0,
                "unverified_phrases": [],
            },
        ],
        "chapter_break_before_segment_ids": ["seg_002"],
    }
    with database.transaction() as connection:
        registered = ExplainerNarrationService(ExplainerRepository(connection)).register_preserved_script(
            project_id=project_id,
            video_id=video_id,
            locale="zh-CN",
            title="标注作品",
            script_source_text="第一句。第二句。",
            actor="test",
            annotations=good,
        )
    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        segments = repo.segments(str(registered["script_revision"]["id"]))
        # The body is still exactly the program's slices with their separators.
        provenance = registered["script_revision"]["provenance_json"]
        span_map = {item["canonical_segment_id"]: item for item in provenance["segment_spans"]}
        rebuilt = provenance["leading_separator"] + "".join(
            item["display_text"] + span_map[item["canonical_segment_id"]]["separator"] for item in segments
        )
        assert rebuilt == "第一句。第二句。"
        assert {item["canonical_segment_id"]: item["chapter_id"] for item in segments}["seg_001"] != (
            {item["canonical_segment_id"]: item["chapter_id"] for item in segments}["seg_002"]
        )

    # A dropped segment, an invented segment and an invented phrase are all refused.
    for broken in (
        {**good, "annotations": good["annotations"][:1]},
        {
            **good,
            "annotations": [
                {**good["annotations"][0], "canonical_segment_id": "seg_999"},
                good["annotations"][1],
            ],
        },
        {
            **good,
            "annotations": [
                {**good["annotations"][0], "unverified_phrases": [{"phrase": "并不存在", "reason": "x"}]},
                good["annotations"][1],
            ],
        },
        {
            **good,
            "annotations": [
                {**good["annotations"][0], "display_text": "模型改写的正文"},
                good["annotations"][1],
            ],
        },
    ):
        with database.transaction() as connection:
            with pytest.raises(ExplainerContractError):
                ExplainerNarrationService(ExplainerRepository(connection)).register_preserved_script(
                    project_id=project_id,
                    video_id=video_id,
                    locale="zh-CN",
                    title="标注作品",
                    script_source_text="第一句。第二句。",
                    actor="test",
                    annotations=broken,
                )


def test_plan_preserved_script_without_a_manuscript_is_a_clear_refusal(
    database: Database, workspace: Any
) -> None:
    service = build_explainer_creation_service(database, workspace)
    created = service.create_workspace(
        ExplainerCreateCommand(
            title="没有正文",
            input_kind="TOPIC",
            script_policy="ADAPT_SOURCES",
            outputs=({"edition_key": "main"},),
        ),
        idempotency_key="empty-preserve",
    )
    planner = LocalTextPlanner(client_factory=lambda: RecordingClient())
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            planner.plan_preserved_script(
                repo=ExplainerRepository(connection),
                project_id=str(created["project"]["id"]),
                video_id=str(created["video"]["id"]),
            )
    assert error.value.code == "SOURCE_EVIDENCE_MISSING"


def test_plain_rewrite_mode_does_not_register_a_preserved_revision(
    database: Database, workspace: Any
) -> None:
    service = build_explainer_creation_service(database, workspace)
    created = service.create_workspace(
        ExplainerCreateCommand(
            title="资料改写",
            input_kind="DOCUMENT_IMPORT",
            script_policy="ADAPT_SOURCES",
            outputs=({"edition_key": "main"},),
        ),
        idempotency_key="adapt-1",
    )
    payload = created["video"]["input_payload_json"]
    assert payload["script_policy"] == "ADAPT_SOURCES"
    assert "preserved_original" not in payload
    assert created["video"].get("current_script_revision_id") in (None, "")
    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        assert repo.list_where("explainer_script_revisions", {"video_id": str(created["video"]["id"])}) == []


def test_legacy_row_without_the_option_resolves_to_adapt_sources() -> None:
    assert contracts.resolve_script_policy(None) == "ADAPT_SOURCES"
    assert contracts.script_policy_from_input_payload({}) == "ADAPT_SOURCES"
    assert contracts.script_policy_from_input_payload({"script_policy": "PRESERVE_ORIGINAL"}) == (
        "PRESERVE_ORIGINAL"
    )
    with pytest.raises(ExplainerContractError):
        contracts.resolve_script_policy("KEEP_EVERYTHING")


def test_preserved_source_directory_is_under_the_project_root() -> None:
    # Regression guard for the raw-upload space the import route writes into.
    from local_drama.infrastructure.filesystem.template import TEMPLATE_DIRECTORIES

    assert "01_story/source_documents" in TEMPLATE_DIRECTORIES
    assert Path("01_story/source_documents/abc.txt").parts[0] == "01_story"
