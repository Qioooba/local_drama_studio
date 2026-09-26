"""Worker-side completion of an explainer visual candidate (design §D2.1/§D6).

What these tests protect
------------------------
``ExplainerVisualGenerationService`` reserves a candidate and submits a real model
job; nothing in the explainer domain owns the moment the output artifact exists.
``ExplainerVisualGenerationCompletionService`` is that owner, and these tests pin the
properties a UI surface depends on:

* a finished job registers the media **and queues the default derivatives** for a
  picture *and* for a real I2V clip.  Without the clip case, step 4/5 asks the
  read-only thumbnail endpoint for a poster frame that was never queued, so a
  candidate that is already ``READY`` can only render a placeholder;
* a replayed callback changes nothing and does not queue a second derivative batch;
* a job that belongs to another feature is ignored instead of raising on the worker's
  completion path.

Requirement mapping: §D2.1 (idempotent finalize), §D6 (worker completion), §B9
(a candidate card must be renderable without a mutating GET).
"""

from __future__ import annotations

import json

import pytest

from local_drama.application.explainers.visual_generation_completion import (
    ExplainerVisualGenerationCompletionService,
)
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "vgc-project"
VIDEO_ID = "vgc-video"
BEAT_ID = "vgc-beat"
CANDIDATE_ID = "vgc-candidate"
JOB_ID = "vgc-job"
MEDIA_VERSION_ID = "vgc-media-version"
SHA = "d" * 64


class _FakeMedia:
    """Stand-in for ``MediaService`` so no real promotion or FFmpeg work happens."""

    def __init__(self) -> None:
        self.promotions: list[dict[str, object]] = []
        self.derivative_requests: list[str] = []

    def promote_job_artifact(self, artifact_id: str, **kwargs: object) -> dict[str, object]:
        self.promotions.append({"artifact_id": artifact_id, **kwargs})
        return {
            "media_version_id": MEDIA_VERSION_ID,
            "media_asset_id": "vgc-media-asset",
            "sha256": SHA,
        }

    def submit_default_derivatives(self, media_version_id: str) -> list[dict[str, object]]:
        self.derivative_requests.append(media_version_id)
        return [{"id": f"derivative-{len(self.derivative_requests)}"}]


class _Settings:
    comfy_input_root = None
    comfy_output_root = None
    ffmpeg_path = None
    ffprobe_path = None
    explainer_budget = None
    work_root = None


def _seed(database: Database, *, mode: str, render_type_planned: str | None) -> None:
    with database.transaction() as connection:
        connection.execute("DELETE FROM projects WHERE id=?", (PROJECT_ID,))
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, target_duration_ms,
            product_kind, created_at, updated_at, created_by)
            VALUES (?, 'vgc_proj', '收尾', 'DRAFT', 'v2', ?, 300000, 'EXPLAINER',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, PROJECT_ID),
        )
        connection.execute(
            """INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale,
            input_kind, duration_mode, target_seconds, tolerance_percent, automation_mode, inference_mode,
            research_mode, status, input_payload_json, created_at, updated_at, created_by)
            VALUES (?, ?, '收尾', '为什么', 'FACTUAL_EXPLAINER', 'zh-CN', 'TOPIC', 'FIXED', 300, 5,
            'AUTO_WITH_EXCEPTIONS', 'LOCAL_ONLY', 'OFFLINE_IMPORT', 'DRAFT', '{}',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (VIDEO_ID, PROJECT_ID),
        )
        repo = ExplainerRepository(connection)
        # The real promotion port creates the media asset/version before the candidate
        # row is updated, and the update is foreign-key checked.
        connection.execute(
            """INSERT INTO media_assets (id, project_id, owner_type, owner_id, purpose, media_kind,
            version_counter, metadata_json, created_at, updated_at, created_by, revision, schema_version)
            VALUES ('vgc-media-asset', ?, 'EXPLAINER_VIDEO', ?, 'EXPLAINER_VISUAL_CANDIDATE', 'IMAGE',
            1, '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test', 1, 'v2')""",
            (PROJECT_ID, VIDEO_ID),
        )
        connection.execute(
            """INSERT INTO media_versions (id, media_asset_id, version_no, take_no, stage, rel_path, mime_type,
            byte_size, sha256, integrity_status, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, 'vgc-media-asset', 1, 1, 'CLIP', 'media/candidate.mp4', 'video/mp4', 2048, ?,
            'VERIFIED', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test', 1, 'v2')""",
            (MEDIA_VERSION_ID, SHA),
        )
        repo.insert(
            "explainer_visual_beats",
            {
                "id": BEAT_ID,
                "video_id": VIDEO_ID,
                "code": "B001",
                "ordinal": 0,
                "render_type": "I2V",
                "visual_intent": "周工在机房检查线路",
                "prompt_intent": "周工站在机房中央检查线路",
            },
        )
        repo.insert(
            "explainer_media_candidates",
            {
                "id": CANDIDATE_ID,
                "video_id": VIDEO_ID,
                "beat_id": BEAT_ID,
                "variant_no": 1,
                "candidate_kind": "CREATIVE",
                "purpose": "VISUAL",
                "status": "GENERATING",
                "job_id": JOB_ID,
                "execution_snapshot_json": {"mode": mode, "generator": "explainer_visual_generation"},
                "render_type_planned": render_type_planned,
                "qc_summary_json": {},
                "adopted": False,
            },
        )


@pytest.fixture()
def media() -> _FakeMedia:
    return _FakeMedia()


def _service(database: Database, media: _FakeMedia) -> ExplainerVisualGenerationCompletionService:
    return ExplainerVisualGenerationCompletionService(database, _Settings(), media=media)


def _artifacts() -> list[dict[str, object]]:
    return [{"id": "vgc-artifact", "job_attempt_id": "vgc-attempt"}]


@pytest.mark.parametrize(
    ("mode", "planned_render_type", "registered_kind"),
    [
        ("IMAGE_TO_VIDEO", "I2V", "VIDEO"),
        # A picture-producing command (a reference / first frame) has no render type of
        # its own and registers an IMAGE; the real I2V command registers a VIDEO.
        ("IMAGE", None, "IMAGE"),
    ],
)
def test_a_finished_job_registers_media_and_queues_its_default_derivatives(
    database: Database, media: _FakeMedia, mode: str, planned_render_type: str | None, registered_kind: str
) -> None:
    """A generated picture and a real I2V clip must both leave the thumbnail endpoint servable."""

    _seed(database, mode=mode, render_type_planned=planned_render_type)
    result = _service(database, media).finalize_job(JOB_ID, _artifacts())

    assert result is not None
    assert result["status"] == "READY"
    assert result["media_version_id"] == MEDIA_VERSION_ID
    assert media.promotions[0]["media_kind"] == registered_kind
    assert media.derivative_requests == [MEDIA_VERSION_ID], (
        f"a READY {registered_kind} candidate was registered without queuing its default "
        "derivative jobs, so its card could only show a placeholder"
    )
    with database.connect() as connection:
        row = connection.execute(
            "SELECT status, media_version_id, qc_summary_json FROM explainer_media_candidates WHERE id=?",
            (CANDIDATE_ID,),
        ).fetchone()
    assert row is not None
    assert str(row["status"]) == "READY"
    assert str(row["media_version_id"]) == MEDIA_VERSION_ID
    qc = json.loads(str(row["qc_summary_json"]) or "{}")
    assert qc.get("content_checked") is False, (
        "content inspection must not be claimed when it did not run"
    )


def test_a_replayed_callback_does_not_queue_a_second_derivative_batch(
    database: Database, media: _FakeMedia
) -> None:
    _seed(database, mode="IMAGE_TO_VIDEO", render_type_planned="I2V")
    service = _service(database, media)
    first = service.finalize_job(JOB_ID, _artifacts())
    second = service.finalize_job(JOB_ID, _artifacts())

    assert first is not None and first["idempotent_replay"] is False
    assert second is not None and second["idempotent_replay"] is True
    assert media.derivative_requests == [MEDIA_VERSION_ID]
    assert len(media.promotions) == 1


def test_a_job_that_is_not_an_explainer_candidate_is_ignored(
    database: Database, media: _FakeMedia
) -> None:
    """The worker calls this for every finished job, so a foreign job must be silent."""

    _seed(database, mode="IMAGE_TO_VIDEO", render_type_planned="I2V")
    assert _service(database, media).finalize_job("some-other-job", _artifacts()) is None
    assert media.derivative_requests == []
