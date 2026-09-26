"""Orchestration tests for the explainer QC stage handlers.

The QC stages decide *which* layers report and how their coverage is described.  The
two answers this file pins down are the ones that would be easy to get wrong and
hard to notice:

* a layer whose real measurement port is missing is reported ``LAYER_NOT_RUN`` and
  makes the whole stage non-PASS, never a silent success;
* technical/decode coverage and semantic/sampled coverage are never merged into one
  claim, and no handler ever writes a human approval or a publication authorization.
"""

from __future__ import annotations

from typing import Any

import pytest

from local_drama.application.explainers.quality import ExplainerQualityService
from local_drama.application.worker_handlers.explainer_qc import (
    QC_LAYERS,
    build_qc_handlers,
    build_qc_readers,
    qc_handler_availability,
    qc_layer_status,
    run_qc_layers,
)
from local_drama.domain.explainers.contracts import ExplainerContractError, ProductKind, content_hash
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "qc-project"


def _seed(database: Database) -> dict[str, str]:
    """One explainer project, video and edition, plus one claim with evidence."""

    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        connection.execute("DELETE FROM projects WHERE id=?", (PROJECT_ID,))
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, target_duration_ms,
            product_kind, created_at, updated_at, created_by)
            VALUES (?, 'qc_proj', '解说质检', 'DRAFT', 'v2', ?, 300000, ?, '2026-01-01T00:00:00Z',
                    '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, PROJECT_ID, ProductKind.EXPLAINER.value),
        )
        profile = repo.insert(
            "channel_profiles",
            {"project_id": PROJECT_ID, "code": "history", "title": "历史插画", "status": "ACTIVE", "scope": "PROJECT"},
        )
        version = repo.insert(
            "channel_profile_versions",
            {
                "channel_profile_id": profile["id"],
                "version_no": 1,
                "title": "历史插画 v1",
                "status": "FROZEN",
                "content_hash": "c" * 64,
            },
        )
        video = repo.insert(
            "explainer_videos",
            {
                "project_id": PROJECT_ID,
                "title": "灯塔与值班记录",
                "topic": "灯塔",
                "content_kind": "FACTUAL_EXPLAINER",
                "source_locale": "zh-CN",
                "input_kind": "TOPIC",
                "duration_mode": "TARGET",
                "target_seconds": 300,
                "tolerance_percent": 5.0,
                "automation_mode": "AUTO_WITH_EXCEPTIONS",
                "inference_mode": "LOCAL_ONLY",
                "research_mode": "OFFLINE_IMPORT",
                "current_channel_profile_version_id": version["id"],
                "status": "DRAFT",
            },
        )
        edition = repo.insert(
            "explainer_editions",
            {
                "video_id": video["id"],
                "edition_key": "zh-CN-16x9",
                "revision_no": 1,
                "voice_locale": "zh-CN",
                "subtitle_mode": "BURNED",
                "subtitle_locales_json": ["zh-CN"],
                "aspect_ratio": "16:9",
                "fps_num": 25,
                "fps_den": 1,
                "status": "DRAFT",
            },
        )
        packet = repo.insert(
            "explainer_research_packets",
            {
                "video_id": video["id"],
                "revision_no": 1,
                "status": "READY",
                "mode": "OFFLINE_IMPORT",
                "topic": "灯塔",
                "max_external_requests": 0,
                "content_hash": "d" * 64,
            },
        )
        source = repo.insert(
            "explainer_sources",
            {
                "packet_id": packet["id"],
                "video_id": video["id"],
                "project_id": PROJECT_ID,
                "source_kind": "DOCUMENT_IMPORT",
                "title": "来源",
                "event_date_precision": "DAY",
                "fetched_at": "2026-01-01T00:00:00Z",
                "body_sha256": "e" * 64,
                "credibility_kind": "SECONDARY",
                "retrieved_via": "USER_SUPPLIED",
            },
        )
        span = repo.insert(
            "explainer_source_spans",
            {
                "packet_id": packet["id"],
                "source_id": source["id"],
                "start_offset": 0,
                "end_offset": 12,
                "quote_text": "灯塔建于 1898 年。",
                "span_hash": "f" * 64,
            },
        )
        claim = repo.insert(
            "explainer_claims",
            {
                "video_id": video["id"],
                "packet_id": packet["id"],
                "code": "C001",
                "statement": "灯塔建于 1898 年。",
                "statement_kind": "FACT",
                "importance": "CORE",
                "status": "SUPPORTED",
                "verification_json": {"method": "SOURCE_RESOLUTION", "resolved": True},
            },
        )
        repo.insert(
            "claim_evidence",
            {
                "claim_id": claim["id"],
                "source_id": source["id"],
                "source_span_id": span["id"],
                "stance": "SUPPORTS",
                "independence_key": "source-1",
            },
        )
        revision = repo.insert(
            "explainer_script_revisions",
            {
                "video_id": video["id"],
                "revision_no": 1,
                "locale": "zh-CN",
                "title": "灯塔",
                "status": "FROZEN",
                "content_hash": "a" * 64,
                "frozen_at": "2026-01-01T00:00:00Z",
                "frozen_by": "test",
            },
        )
        segment = repo.insert(
            "narration_segments",
            {
                "video_id": video["id"],
                "script_revision_id": revision["id"],
                "canonical_segment_id": "seg-1",
                "locale": "zh-CN",
                "ordinal": 1,
                "display_text": "灯塔建于 1898 年。",
                "spoken_text": "灯塔建于 1898 年。",
                "statement_type": "FACT",
                "segment_hash": "b" * 64,
            },
        )
        repo.update("narration_segments", segment["id"], {"claim_ids_json": [claim["id"]]})
        repo.update("explainer_videos", video["id"], {"current_script_revision_id": revision["id"]})
        subtitle = repo.insert(
            "explainer_subtitle_revisions",
            {
                "edition_id": edition["id"],
                "video_id": video["id"],
                "revision_no": 1,
                "locale": "zh-CN",
                "format": "JSON",
                "text_authority": "NARRATION_SCRIPT",
                "script_revision_id": revision["id"],
                "content_hash": "9" * 64,
                "content_text": "灯塔建于 1898 年。",
                "cues_json": [
                    {
                        "index": 1,
                        "start_ms": 0,
                        "end_ms": 2000,
                        "text": "灯塔建于 1898 年。",
                        "box": {"x": 200, "y": 900, "width": 1520, "height": 120},
                    }
                ],
                # The layout contract is in whole pixels, so the fixture uses the
                # same units the detector reads instead of normalized fractions.
                "layout_report_json": {
                    "safe_area": {"left_px": 96, "top_px": 54, "right_px": 1824, "bottom_px": 1026}
                },
            },
        )
        repo.update("explainer_editions", edition["id"], {"frozen_subtitle_revision_id": subtitle["id"]})
    return {
        "project_id": PROJECT_ID,
        "video_id": str(video["id"]),
        "edition_id": str(edition["id"]),
        "revision_id": str(revision["id"]),
    }


def _payload(seed: dict[str, str], *, layers: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "project_id": seed["project_id"],
        "video_id": seed["video_id"],
        "edition_id": seed["edition_id"],
        "subject_kind": "EDITION",
        "subject_revision_id": seed["edition_id"],
    }
    if layers is not None:
        payload["layers"] = layers
    payload.update(extra)
    return payload


def _run(database: Database, payload: dict[str, Any], **ports: Any) -> dict[str, Any]:
    return run_qc_layers(
        payload,
        quality_factory=lambda repo: ExplainerQualityService(repo),
        repo_factory=lambda: _context(database),
        **ports,
    )


class _context:
    """A context manager yielding a repository over one short transaction."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.connection: Any = None

    def __enter__(self) -> ExplainerRepository:
        self.connection = self.database.connect()
        self.connection.execute("BEGIN IMMEDIATE")
        return ExplainerRepository(self.connection)

    def __exit__(self, *exc: object) -> None:
        self.connection.commit()
        self.connection.close()


# --------------------------------------------------------------- layer selection
def test_qc_layers_are_named_and_ordered() -> None:
    assert QC_LAYERS == ("TECHNICAL", "SUBTITLE", "FACT", "SEMANTIC", "DEPTH")
    assert set(qc_layer_status()) == set(QC_LAYERS)
    # The build states exactly which layers cannot run without an external port.
    assert qc_layer_status()["SUBTITLE"] == "WIRED"
    assert qc_layer_status()["FACT"] == "WIRED"
    assert "SUPPLIED" in qc_layer_status()["TECHNICAL"]


def test_an_unknown_layer_is_rejected_instead_of_ignored(database: Database) -> None:
    seed = _seed(database)

    with pytest.raises(ExplainerContractError) as error:
        _run(database, _payload(seed, layers=["TECHNICAL", "VIBES"]))

    assert error.value.code == "SCHEMA_INVALID"
    assert error.value.details["unknown_layers"] == ["VIBES"]


def test_a_missing_measurement_port_is_reported_as_not_run(database: Database) -> None:
    """No reader injected means the layer did not run; it is never a pass."""

    seed = _seed(database)

    report = _run(database, _payload(seed, layers=["TECHNICAL"]))

    assert report["status"] == "NOT_RUN"
    assert report["machine_check"] == {"status": "NOT_RUN", "ok": False}
    assert report["layers_skipped"] == [{"layer": "TECHNICAL", "reason": "TECHNICAL_READER_NOT_INJECTED"}]
    assert report["unverified_checks"] == ["LAYER_NOT_RUN:TECHNICAL"]
    assert report["reports"] == []


def test_a_partial_run_is_never_reported_as_pass(database: Database) -> None:
    """One layer ran, one had no measurement port: the stage is PARTIAL, not PASS."""

    seed = _seed(database)

    report = _run(database, _payload(seed, layers=["SUBTITLE", "TECHNICAL"]), **build_qc_readers())

    assert report["status"] == "PARTIAL"
    assert report["machine_check"]["ok"] is False
    assert report["layers_completed"] and report["layers_skipped"]
    assert report["unverified_checks"] == ["LAYER_NOT_RUN:TECHNICAL"]


# --------------------------------------------------------------- stored readers
def test_bound_readers_run_the_subtitle_and_fact_layers(database: Database) -> None:
    """The always-bound readers read the frozen revision artefacts, not a guess."""

    seed = _seed(database)

    report = _run(database, _payload(seed, layers=["SUBTITLE", "FACT"]), **build_qc_readers())

    assert report["status"] == "PASS"
    assert report["layers_skipped"] == []
    assert report["report_count"] == 2
    # The subtitle layer records its cue-level findings; the fact layer resolves
    # every claim against its stored evidence rows and finds nothing to report.
    assert report["layer_statuses"] == ["PASS", "PASS"]
    # A QC report is never a human approval and never authorizes publication.
    assert report["human_approval_written"] is False
    assert report["publication_authorized"] is False
    assert report["human_reviewed_claimed"] is False


def test_a_cue_level_finding_makes_the_subtitle_layer_pass_with_issues(database: Database) -> None:
    """An over-fast cue is recorded as an issue, so the layer is not a clean pass."""

    seed = _seed(database)
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        revision = repo.list_where(
            "explainer_subtitle_revisions", {"edition_id": seed["edition_id"]}, order_by="revision_no"
        )[0]
        repo.update(
            "explainer_subtitle_revisions",
            str(revision["id"]),
            {
                "cues_json": [
                    {
                        "index": 1,
                        "start_ms": 0,
                        "end_ms": 200,
                        # ~18 characters in 200ms is far beyond any reading rate.
                        "text": "灯塔在一八九八年由当地渔民集资建成，至今仍在服役。",
                        "box": {"x": 200, "y": 900, "width": 1520, "height": 120},
                    }
                ]
            },
        )

    report = _run(database, _payload(seed, layers=["SUBTITLE"]), **build_qc_readers())

    assert report["layer_statuses"] == ["PASS_WITH_ISSUES"]
    assert report["status"] == "PASS"
    findings = report["reports"][0]["issues"]
    assert any(item["issue_kind"].startswith("SUBTITLE_READING_RATE") for item in findings), findings


def test_readers_never_claim_decode_or_semantic_coverage(database: Database) -> None:
    seed = _seed(database)

    report = _run(database, _payload(seed, layers=["SUBTITLE", "FACT"]), **build_qc_readers())

    for item in report["reports"]:
        coverage = item.get("coverage") or {}
        assert coverage.get("decode_coverage_established_by_this_report") is False
        assert coverage.get("semantic_coverage_established_by_this_report") is False
    assert report["decoded_is_not_semantic"] is True


# ------------------------------------------------------------------- provider
def _valid_plan(seed: dict[str, str], *, total_frames: int = 10) -> dict[str, Any]:
    """A sampling plan whose hash is the one ``run_semantic_check`` recomputes."""

    plan = {
        "video_id": seed["video_id"],
        "edition_id": seed["edition_id"],
        "density": "STANDARD",
        "sampled_frame_ids": [1, 5, 9],
        "total_frames": total_frames,
    }
    plan["plan_hash"] = content_hash(plan)
    return plan


def test_semantic_layer_without_a_provider_stays_unchecked(database: Database) -> None:
    """A valid plan with no image-reading provider is UNCHECKED, not PASS."""

    seed = _seed(database)
    plan = _valid_plan(seed)

    def sampling(repo: ExplainerRepository, context: dict[str, Any]) -> dict[str, Any]:
        del repo, context
        return {
            "plan": plan,
            "reference_frames": [{"frame_id": 1, "rel_path": "a.png"}, {"frame_id": 5, "rel_path": "b.png"}],
        }

    report = _run(database, _payload(seed, layers=["SEMANTIC"]), sampling_reader=sampling)

    assert report["status"] == "NOT_RUN"
    assert report["visual_provider"]["available"] is False
    assert report["visual_provider"]["reason"] == "VISUAL_QC_PROVIDER_NOT_CONFIGURED"
    assert report["report_count"] == 1
    layer_report = report["reports"][0]
    assert layer_report["status"] == "NOT_RUN"
    assert layer_report["coverage"]["semantic_layer_checked"] is False
    assert layer_report["coverage"]["decode_coverage_established_by_this_report"] is False
    assert "VISUAL_QC_PROVIDER_UNAVAILABLE" in layer_report["unverified_checks"]


def test_a_tampered_sampling_plan_is_rejected(database: Database) -> None:
    """The plan hash is recomputed by the service, so an edited plan cannot pass."""

    seed = _seed(database)
    plan = _valid_plan(seed)
    plan["sampled_frame_ids"] = [1, 2, 3, 4]

    def sampling(repo: ExplainerRepository, context: dict[str, Any]) -> dict[str, Any]:
        del repo, context
        return {"plan": plan, "reference_frames": []}

    with pytest.raises(ExplainerContractError) as error:
        _run(database, _payload(seed, layers=["SEMANTIC"]), sampling_reader=sampling)

    assert error.value.code == "STALE_REVISION"


def test_depth_layer_without_a_provider_is_skipped(database: Database) -> None:
    """A beat-scale depth pass with no image-reading provider is skipped, not run."""

    seed = _seed(database)

    def sampling(repo: ExplainerRepository, context: dict[str, Any]) -> dict[str, Any]:
        del repo, context
        return {"plan": _valid_plan(seed), "depth_frame_range": [0, 10]}

    report = _run(
        database,
        _payload(seed, layers=["DEPTH"], subject_kind="VISUAL_BEAT", subject_hash="c" * 64),
        sampling_reader=sampling,
    )

    assert report["status"] == "NOT_RUN"
    assert {"layer": "DEPTH", "reason": "VISUAL_QC_PROVIDER_NOT_CONFIGURED"} in report["layers_skipped"]
    assert report["visual_provider"]["available"] is False


def test_a_film_scale_depth_layer_is_skipped_instead_of_failing_the_job(
    database: Database,
) -> None:
    """The delivered 1962 film's COMPOSITION_QC died on the depth layer.

    ``run_depth_check`` is defined over one named shot and rejects the ``(0, 0)``
    default a film-scale subject supplies, so the whole job failed with
    ``SCHEMA_INVALID frame_range`` and threw away the technical and subtitle reports
    it had already measured.
    """

    seed = _seed(database)

    def sampling(repo: ExplainerRepository, context: dict[str, Any]) -> dict[str, Any]:
        del repo, context
        return {"plan": _valid_plan(seed)}

    report = _run(
        database,
        _payload(
            seed,
            layers=["DEPTH"],
            subject_kind="COMPOSITION_RENDER",
            subject_hash="b" * 64,
        ),
        sampling_reader=sampling,
    )

    assert report["status"] == "NOT_RUN"
    skipped_layers = {str(item["layer"]): str(item["reason"]) for item in report["layers_skipped"]}
    assert skipped_layers == {"DEPTH": "DEPTH_LAYER_SCOPE_IS_ONE_SHOT"}
    assert report["unverified_checks"] == ["LAYER_NOT_RUN:DEPTH"]


def test_one_unrunnable_layer_keeps_the_reports_the_other_layers_measured(
    database: Database,
) -> None:
    """A layer with nothing to measure is skipped; it is never fatal for the rest."""

    seed = _seed(database)

    def subtitle(repo: ExplainerRepository, context: dict[str, Any]) -> dict[str, Any]:
        del repo, context
        return {
            "cues": [],
            "safe_area": {"left_px": 96, "top_px": 54, "right_px": 1824, "bottom_px": 1026},
            "fps_num": 25,
            "fps_den": 1,
            "total_frames": 100,
        }

    def fact(repo: ExplainerRepository, context: dict[str, Any]) -> dict[str, Any]:
        del repo, context
        raise ExplainerContractError("NOT_RUN", "没有可核对的主张", {"video_id": seed["video_id"]})

    report = _run(
        database,
        _payload(seed, layers=["SUBTITLE", "FACT"]),
        subtitle_reader=subtitle,
        fact_reader=fact,
    )

    assert [item["layer"] for item in report["layers_skipped"]] == ["FACT"]
    assert report["layers_skipped"][0]["reason"] == "NOT_RUN"
    assert report["report_count"] == 1
    assert report["status"] == "PARTIAL"


def test_a_real_layer_failure_is_not_swallowed_as_not_run(database: Database) -> None:
    """Only "this layer has nothing to measure" is tolerated."""

    seed = _seed(database)

    def fact(repo: ExplainerRepository, context: dict[str, Any]) -> dict[str, Any]:
        del repo, context
        raise ExplainerContractError("MEDIA_HASH_MISMATCH", "媒体哈希不一致")

    with pytest.raises(ExplainerContractError) as error:
        _run(database, _payload(seed, layers=["FACT"]), fact_reader=fact)

    assert error.value.code == "MEDIA_HASH_MISMATCH"


# --------------------------------------------------------------------- handler
def test_handler_availability_names_the_provider_dependency() -> None:
    availability = qc_handler_availability()

    assert set(availability) == {"EXPLAINER_VISUAL_QC", "COMPOSITION_QC"}
    for status in availability.values():
        assert status["handler"] == "WIRED"
        assert status["subtitle_reader"] == "WIRED"
        assert "SUPPLIED" in status["visual_provider"]


def test_each_qc_stage_reports_its_own_stage_code(database: Database) -> None:
    """A COMPOSITION_QC job must not write a report claiming to be visual QC."""

    seed = _seed(database)
    handlers = build_qc_handlers(
        quality_factory=lambda repo: ExplainerQualityService(repo),
        repo_factory=lambda: _context(database),
        **build_qc_readers(),
    )

    assert set(handlers) == {"EXPLAINER_VISUAL_QC", "COMPOSITION_QC"}
    visual = handlers["EXPLAINER_VISUAL_QC"]({}, {"semantic_inputs": _payload(seed, layers=["FACT"])})
    composition = handlers["COMPOSITION_QC"]({}, {"semantic_inputs": _payload(seed, layers=["FACT"])})

    assert visual["stage_code"] == "EXPLAINER_VISUAL_QC"
    assert composition["stage_code"] == "COMPOSITION_QC"
    assert visual["status"] == composition["status"] == "PASS"


def test_run_requires_project_and_video(database: Database) -> None:
    seed = _seed(database)

    with pytest.raises(ExplainerContractError):
        _run(database, {"project_id": seed["project_id"], "layers": ["FACT"]})
