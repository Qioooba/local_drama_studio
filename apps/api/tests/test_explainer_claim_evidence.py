"""FE-A11: the API must expose the frozen evidence behind one fact.

The fact ledger rendered ``事实 Cxxx`` buttons, but the page never read the
``?claim=`` parameter and no endpoint returned the evidence: ``claim_span_records``
already joined a claim to its source and to the exact saved span, and nothing exposed
it.  A fact in conflict therefore could not be inspected or corrected, so the
"查看证据 → 修正/排除 → 再冻结" loop was impossible from the page.

These tests exercise the real route against a real SQLite schema and distinguish
"this fact has no evidence" from "the evidence could not be read".
"""

from __future__ import annotations

import pytest

from local_drama.api.routes.explainers import _claim_evidence
from local_drama.domain.explainers.contracts import ExplainerContractError
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database


def _seed(database: Database, key: str = "fea11", *, with_span: bool = True) -> dict[str, str]:
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO projects (id, code, title, status, template_version, root_rel, product_kind)"
            " VALUES (?,?,?,?,?,?,?)",
            (f"proj-{key}", f"code_{key}", f"项目{key}", "ACTIVE", "v2", f"projects/{key}", "EXPLAINER"),
        )
        connection.execute(
            "INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale,"
            " input_kind, duration_mode, target_seconds, tolerance_percent, automation_mode,"
            " inference_mode, research_mode, status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"video-{key}", f"proj-{key}", f"作品{key}", "主题", "FACTUAL_EXPLAINER", "zh-CN",
                "TOPIC", "TARGET", 300, 5.0, "AUTO_WITH_EXCEPTIONS", "LOCAL_ONLY", "OFFLINE_IMPORT", "DRAFT",
            ),
        )
        connection.execute(
            "INSERT INTO explainer_research_packets (id, video_id, revision_no, status, mode, topic,"
            " allowed_domains_json, external_request_count, max_external_requests, content_hash, blockers_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"pkt-{key}", f"video-{key}", 1, "READY", "OFFLINE_IMPORT", "主题", "[]", 0, 0, "c" * 64, "[]"),
        )
        connection.execute(
            "INSERT INTO explainer_sources (id, packet_id, video_id, project_id, source_kind, title,"
            " fetched_at, body_sha256, credibility_kind, retrieved_via, url)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"src-{key}", f"pkt-{key}", f"video-{key}", f"proj-{key}", "WEB_PAGE",
                "灯塔值守记录（原始档案）", "2026-01-01T00:00:00+00:00", "a" * 64,
                "PRIMARY", "OFFLINE_IMPORT", "https://example.invalid/light",
            ),
        )
        connection.execute(
            "INSERT INTO explainer_claims (id, video_id, packet_id, code, statement, statement_kind,"
            " status, importance, confidence_reason, verified_as_history, disambiguation_json, verification_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"claim-{key}", f"video-{key}", f"pkt-{key}", "C001",
                "值班记录在事故当晚被撕掉一页。", "FACT", "UNVERIFIED", "CORE",
                "只有一家转载。", 0, "{}", "{}",
            ),
        )
        if with_span:
            connection.execute(
                "INSERT INTO explainer_source_spans (id, source_id, packet_id, ordinal, start_offset,"
                " end_offset, quote_text, span_hash, page_no, paragraph_no)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    f"span-{key}", f"src-{key}", f"pkt-{key}", 1, 120, 168,
                    "记录显示当夜值守页缺失一页，编号不连续。", "b" * 64, 3, 2,
                ),
            )
            connection.execute(
                "INSERT INTO claim_evidence (id, claim_id, source_id, source_span_id, stance,"
                " independence_key, note) VALUES (?,?,?,?,?,?,?)",
                (f"ev-{key}", f"claim-{key}", f"src-{key}", f"span-{key}", "SUPPORTS", f"src-{key}", "原始档案"),
            )
    return {"project_id": f"proj-{key}", "video_id": f"video-{key}", "claim_id": f"claim-{key}"}


def test_evidence_returns_the_saved_span_with_its_source_and_offsets(database: Database) -> None:
    seeded = _seed(database)
    with database.connect() as connection:
        result = _claim_evidence(ExplainerRepository(connection), seeded["project_id"], seeded["claim_id"])

    assert result["claim_code"] == "C001"
    assert result["evidence_count"] == 1
    assert result["independent_source_count"] == 1
    assert result["empty_state"] is None
    span = result["evidence"][0]
    # The readable window, its exact offsets and its source travel together, so the
    # page can open the sentence instead of the whole document.
    assert span["quote_text"] == "记录显示当夜值守页缺失一页，编号不连续。"
    assert (span["start_offset"], span["end_offset"]) == (120, 168)
    assert span["source_title"] == "灯塔值守记录（原始档案）"
    assert span["source_url"] == "https://example.invalid/light"
    assert span["credibility_kind"] == "PRIMARY"
    assert span["span_hash"] == "b" * 64


def test_a_fact_without_evidence_is_reported_as_unsupported(database: Database) -> None:
    seeded = _seed(database, key="fea11empty", with_span=False)
    with database.connect() as connection:
        result = _claim_evidence(ExplainerRepository(connection), seeded["project_id"], seeded["claim_id"])

    # "No span recorded" is a distinct, explicit state: the assertion has no evidence
    # and must not look like a load failure.
    assert result["evidence"] == []
    assert result["evidence_count"] == 0
    assert result["empty_state"] == "NO_EVIDENCE_SPAN_RECORDED"


def test_evidence_is_scoped_to_the_owning_video(database: Database) -> None:
    first = _seed(database, key="fea11a")
    second = _seed(database, key="fea11b")
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError):
            _claim_evidence(ExplainerRepository(connection), first["project_id"], second["claim_id"])
