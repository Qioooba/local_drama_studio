"""The explainer per-object generation command (design §D4/D7).

What these tests protect
------------------------
The design replaces "one long synchronous stage writes candidate rows" with a
plan/submit command that has a read-only plan, frozen seeds, an idempotent receipt
and an explicit owner.  Each test below pins one rule that would otherwise be easy
to regress:

* ``plan`` is genuinely read-only (no candidate, no job);
* a plan whose frozen inputs changed is refused (``EXPLAINER_STALE_PLAN``) instead of
  silently running a different prompt;
* ``purpose`` and ``mode`` cannot be mixed across layers, so a still frame can never
  masquerade as a final clip and a reference can never be a video;
* candidate counts follow the documented limits (images 1–4, video 1–2);
* an object revision mismatch is a 409-shaped refusal, not an overwrite;
* ``submit`` reports a truthful per-item receipt, and a replay of the same
  operation extends the missing ordinals instead of creating a second batch;
* the entity reference path needs an adopted reference and produces a real
  ``story_asset_references`` row that the generation input bridge can read.

Requirement mapping: §D4.1 (plan/submit), §D4.2 (keyframe → video), §B3.3/§B5.3
(adopt vs adopt-and-lock), §C8.1 (retry vs new draw) and §D3.1 (visual preferences).
"""

from __future__ import annotations

import json

import pytest

from local_drama.application.explainers.visual_generation import (
    ExplainerVisualGenerationError,
    ExplainerVisualGenerationService,
)
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "vg-project"
VIDEO_ID = "vg-video"
BEAT_ID = "vg-beat"
ENTITY_ID = "vg-entity"
STORY_ASSET_ID = "vg-asset"
REFERENCE_ID = "vg-reference"
MEDIA_ASSET_ID = "vg-media-asset"
MEDIA_VERSION_ID = "vg-media-version"
KEYFRAME_CANDIDATE_ID = "vg-keyframe-candidate"
KEYFRAME_SELECTION_ID = "vg-keyframe-selection"
NOW = "2026-01-01T00:00:00Z"
SHA = "b" * 64


class _FakePreview:
    execution_profile_version_id = "profile-image-edit"
    resolution_hash = "f" * 64
    blockers: tuple[str, ...] = ()
    resolved_parameters = {"steps": 20}


class _FakePlanning:
    """Stand-in for ``ExecutionPlanningService`` so no real model is contacted."""

    def __init__(self, database: object) -> None:
        self.database = database

    def preview(self, request: object) -> _FakePreview:
        return _FakePreview()


class _FakeSubmission:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.calls: list[dict[str, object]] = []

    def submit(self, request: object, key: str, *, after_linked_in_transaction: object = None) -> object:
        self.calls.append({"request": request, "key": key})
        job = {"id": f"job-{len(self.calls)}", "status": "QUEUED"}
        snapshot = type("Snapshot", (), {"id": f"snap-{len(self.calls)}", "content_hash": "c" * 64})()
        if after_linked_in_transaction is not None:
            with self.database.connect() as connection:
                after_linked_in_transaction(connection, job, snapshot)
        return type("Submission", (), {"job": job})()


@pytest.fixture()
def fake_submission(database: Database) -> _FakeSubmission:
    """The recording stand-in for ``ExecutionSubmissionService``."""

    return _FakeSubmission(database)


@pytest.fixture()
def service_factory(database: Database, monkeypatch: pytest.MonkeyPatch, fake_submission: _FakeSubmission):
    """Build a service on a live connection per call.

    ``ExplainerRepository`` wraps one connection, so a single long-lived instance
    would be closed by the time a test used it.  The factory mirrors how the route
    builds the service inside the request's connection scope.
    """

    _seed(database)
    submission = fake_submission
    monkeypatch.setattr(
        "local_drama.model_platform.application.execution_planning.ExecutionPlanningService",
        _FakePlanning,
    )
    open_connections: list[object] = []

    def factory() -> ExplainerVisualGenerationService:
        connection = database.connect()
        open_connections.append(connection)
        repo = ExplainerRepository(connection)
        return ExplainerVisualGenerationService(
            repo,
            database=database,
            settings=_Settings(),
            submission_service_factory=lambda: submission,
        )

    yield factory
    for connection in open_connections:
        try:
            connection.close()  # type: ignore[attr-defined]
        except Exception:
            pass


@pytest.fixture()
def service(service_factory) -> ExplainerVisualGenerationService:
    return service_factory()


class _Settings:
    comfy_input_root = None
    comfy_output_root = None
    ffmpeg_path = None
    ffprobe_path = None
    explainer_budget = None
    work_root = None


def _seed(database: Database) -> None:
    with database.transaction() as connection:
        connection.execute("DELETE FROM projects WHERE id=?", (PROJECT_ID,))
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, target_duration_ms,
            product_kind, created_at, updated_at, created_by)
            VALUES (?, 'vg_proj', '生成', 'DRAFT', 'v2', ?, 300000, 'EXPLAINER',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, PROJECT_ID),
        )
        connection.execute(
            """INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale, input_kind,
            duration_mode, target_seconds, tolerance_percent, automation_mode, inference_mode, research_mode,
            status, input_payload_json, created_at, updated_at, created_by)
            VALUES (?, ?, '生成', '为什么', 'FACTUAL_EXPLAINER', 'zh-CN', 'TOPIC',
            'FIXED', 300, 5, 'AUTO_WITH_EXCEPTIONS', 'LOCAL_ONLY', 'OFFLINE_IMPORT', 'DRAFT',
            ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (
                VIDEO_ID,
                PROJECT_ID,
                json.dumps(
                    {
                        "script_policy": "PRESERVE_ORIGINAL",
                        "visual_preferences": {
                            "schema_version": "localdrama.explainer.visual-preferences.v1",
                            "image_candidate_count": 1,
                            "video_candidate_count": 1,
                            "style_prompt_override": "水彩手绘风格",
                            "negative_prompt_override": "多余人物",
                        },
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        connection.execute(
            """INSERT INTO media_assets (id, project_id, owner_type, owner_id, purpose, media_kind,
            version_counter, metadata_json, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 'EXPLAINER_VIDEO', ?, 'EXPLAINER_ENTITY_REFERENCE', 'IMAGE', 1, '{}',
            ?, ?, 'test', 1, 'v2')""",
            (MEDIA_ASSET_ID, PROJECT_ID, VIDEO_ID, NOW, NOW),
        )
        connection.execute(
            """INSERT INTO media_versions (id, media_asset_id, version_no, take_no, stage, rel_path, mime_type,
            byte_size, sha256, integrity_status, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, 1, 'ASSET_HERO', 'media/ref.png', 'image/png', 1024, ?, 'VERIFIED',
            ?, ?, 'test', 1, 'v2')""",
            (MEDIA_VERSION_ID, MEDIA_ASSET_ID, SHA, NOW, NOW),
        )
        connection.execute(
            """INSERT INTO story_assets (id, project_id, kind, code, name, description, status,
            created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 'CHARACTER', 'ZHOU', '周工', '', 'ACTIVE', ?, ?, 'test', 1, 'v2')""",
            (STORY_ASSET_ID, PROJECT_ID, NOW, NOW),
        )
        repo = ExplainerRepository(connection)
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
            "explainer_entities",
            {
                "id": ENTITY_ID,
                "video_id": VIDEO_ID,
                "project_id": PROJECT_ID,
                "code": "E001",
                "entity_type": "FICTIONAL_CHARACTER",
                "name": "周工",
                "story_asset_id": STORY_ASSET_ID,
            },
        )
        repo.insert(
            "story_asset_references",
            {
                "id": REFERENCE_ID,
                "project_id": PROJECT_ID,
                "story_asset_id": STORY_ASSET_ID,
                "media_version_id": MEDIA_VERSION_ID,
                "reference_kind": "HERO",
                "label": "主参考",
                "priority": 100,
                "is_locked": 0,
                "status": "ACTIVE",
            },
        )


BEAT_REF = {"kind": "BEAT", "id": BEAT_ID}
ENTITY_REF = {"kind": "ENTITY", "id": ENTITY_ID}


def _adopt_keyframe(database: Database, *, selection_id: str = KEYFRAME_SELECTION_ID) -> str:
    """Write the rows the adoption path writes for a beat's first frame.

    A real I2V plan consumes an *adopted* ``KEYFRAME`` selection, so a test that wants
    an executable plan must provide one.  This is deliberately a real candidate plus a
    real ``explainer_beat_selections`` row: the plan resolves the selection, not a
    caller-supplied picture reference.
    """

    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        repo.insert(
            "explainer_media_candidates",
            {
                "id": KEYFRAME_CANDIDATE_ID,
                "video_id": VIDEO_ID,
                "beat_id": BEAT_ID,
                "variant_no": 1,
                "candidate_kind": "CREATIVE",
                "purpose": "KEYFRAME",
                "media_asset_id": MEDIA_ASSET_ID,
                "media_version_id": MEDIA_VERSION_ID,
                "media_sha256": SHA,
                "status": "READY",
                "render_type_planned": "I2V",
                # A first frame is a picture, so it declares no actual render type.
                "render_type_actual": None,
                "qc_summary_json": {},
            },
        )
        repo.insert(
            "explainer_beat_selections",
            {
                "id": selection_id,
                "video_id": VIDEO_ID,
                "beat_id": BEAT_ID,
                "candidate_id": KEYFRAME_CANDIDATE_ID,
                "media_asset_id": MEDIA_ASSET_ID,
                "media_version_id": MEDIA_VERSION_ID,
                "media_sha256": SHA,
                "adoption_authority": "MACHINE_POLICY",
                "locked_by_human": False,
                "purpose": "KEYFRAME",
                "status": "ACTIVE",
            },
        )
    return selection_id


def _command(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "operation_id": "op-0000000001",
        "purpose": "KEYFRAME",
        "mode": "TEXT_TO_IMAGE",
        "candidate_count": 1,
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------------- #
# owner resolution
# --------------------------------------------------------------------------- #
def test_an_unknown_owner_is_refused(service: ExplainerVisualGenerationService) -> None:
    with pytest.raises(ExplainerVisualGenerationError) as error:
        service.resolve_owner(PROJECT_ID, {"kind": "SHOT", "id": "x"})
    assert error.value.code == "EXPLAINER_OWNER_INVALID"


def test_a_beat_from_another_video_is_refused(service: ExplainerVisualGenerationService) -> None:
    with pytest.raises(DomainRuleError) as error:
        service.resolve_owner(PROJECT_ID, {"kind": "BEAT", "id": "missing-beat"})
    assert error.value.code == "EXPLAINER_BEAT_NOT_FOUND"


# --------------------------------------------------------------------------- #
# plan
# --------------------------------------------------------------------------- #
def test_plan_is_read_only_and_freezes_seeds(service: ExplainerVisualGenerationService, database: Database) -> None:
    plan = service.plan(PROJECT_ID, BEAT_REF, _command(candidate_count=2))
    assert plan.status == "EXECUTABLE"
    assert plan.purpose == "KEYFRAME"
    assert plan.candidate_count == 2
    assert len(set(plan.candidate_seeds)) == 2
    assert plan.expected_resolution_hash == "f" * 64
    # A text-to-image command produces a picture, so it declares no render type at all.
    assert plan.render_type_planned is None
    # The style override from visual_preferences is the frozen style.
    assert "水彩手绘风格" in plan.prompt
    with database.connect() as connection:
        candidates = connection.execute(
            "SELECT COUNT(*) FROM explainer_media_candidates WHERE beat_id=?", (BEAT_ID,)
        ).fetchone()[0]
        jobs = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert candidates == 0
    assert jobs == 0


def test_plan_hash_is_stable_for_the_same_frozen_input(service: ExplainerVisualGenerationService) -> None:
    first = service.plan(PROJECT_ID, BEAT_REF, _command())
    second = service.plan(PROJECT_ID, BEAT_REF, _command())
    assert first.plan_hash == second.plan_hash
    other = service.plan(PROJECT_ID, BEAT_REF, _command(prompt_override="完全不同的画面"))
    assert other.plan_hash != first.plan_hash


def test_a_stale_plan_hash_is_refused(service: ExplainerVisualGenerationService) -> None:
    with pytest.raises(ExplainerVisualGenerationError) as error:
        service.submit(
            PROJECT_ID,
            BEAT_REF,
            _command(expected_plan_hash="0" * 64),
            idempotency_key="key-1",
        )
    assert error.value.code == "EXPLAINER_STALE_PLAN"


def test_a_stale_object_revision_is_refused(service: ExplainerVisualGenerationService) -> None:
    with pytest.raises(ExplainerVisualGenerationError) as error:
        service.plan(PROJECT_ID, BEAT_REF, _command(expected_beat_revision=99))
    assert error.value.code == "EXPLAINER_STALE_REVISION"
    assert error.value.detail["actual_revision"] == 1


# --------------------------------------------------------------------------- #
# purpose / mode
# --------------------------------------------------------------------------- #
def test_an_entity_cannot_produce_a_visual_candidate(service: ExplainerVisualGenerationService) -> None:
    with pytest.raises(ExplainerVisualGenerationError) as error:
        service.plan(PROJECT_ID, ENTITY_REF, _command(purpose="VISUAL"))
    assert error.value.code == "EXPLAINER_PURPOSE_OWNER_MISMATCH"


def test_a_beat_cannot_produce_a_reference_candidate(service: ExplainerVisualGenerationService) -> None:
    with pytest.raises(ExplainerVisualGenerationError) as error:
        service.plan(PROJECT_ID, BEAT_REF, _command(purpose="REFERENCE"))
    assert error.value.code == "EXPLAINER_PURPOSE_OWNER_MISMATCH"


def test_a_clip_cannot_be_requested_as_a_still(service: ExplainerVisualGenerationService) -> None:
    with pytest.raises(ExplainerVisualGenerationError) as error:
        service.plan(PROJECT_ID, BEAT_REF, _command(purpose="VISUAL", mode="TEXT_TO_IMAGE"))
    assert error.value.code == "EXPLAINER_MODE_INVALID"


def test_video_candidate_count_is_limited_to_two(service: ExplainerVisualGenerationService) -> None:
    with pytest.raises(ExplainerVisualGenerationError) as error:
        service.plan(
            PROJECT_ID,
            BEAT_REF,
            _command(purpose="VISUAL", mode="IMAGE_TO_VIDEO", candidate_count=4),
        )
    assert error.value.code == "EXPLAINER_CANDIDATE_COUNT_INVALID"


def test_image_candidate_count_is_limited_to_four(service: ExplainerVisualGenerationService) -> None:
    with pytest.raises(ExplainerVisualGenerationError) as error:
        service.plan(PROJECT_ID, BEAT_REF, _command(candidate_count=5))
    assert error.value.code == "EXPLAINER_CANDIDATE_COUNT_INVALID"


def test_image_edit_requires_an_adopted_reference(service: ExplainerVisualGenerationService) -> None:
    plan = service.plan(PROJECT_ID, BEAT_REF, _command(mode="IMAGE_EDIT"))
    assert plan.status == "BLOCKED"
    assert any(item["code"] == "EXPLAINER_REFERENCE_REQUIRED" for item in plan.blockers)


def test_an_adopted_reference_becomes_a_verified_image_input(
    service: ExplainerVisualGenerationService,
) -> None:
    plan = service.plan(
        PROJECT_ID,
        BEAT_REF,
        _command(
            mode="IMAGE_EDIT",
            reference_selections=[
                {"entity_id": ENTITY_ID, "reference_id": REFERENCE_ID, "entity_state_revision_id": None}
            ],
        ),
    )
    assert plan.status == "EXECUTABLE"
    assert plan.reference_capacity["resolved_image_references"] == 1
    assert plan.resolved_references[0]["media_version_id"] == MEDIA_VERSION_ID
    assert plan.resolved_references[0]["sha256"] == SHA


def test_an_unadopted_reference_is_refused(service: ExplainerVisualGenerationService) -> None:
    with pytest.raises(ExplainerVisualGenerationError) as error:
        service.plan(
            PROJECT_ID,
            BEAT_REF,
            _command(
                mode="IMAGE_EDIT",
                reference_selections=[{"entity_id": ENTITY_ID, "reference_id": "not-adopted"}],
            ),
        )
    assert error.value.code == "EXPLAINER_REFERENCE_NOT_ADOPTED"


def test_a_i2v_plan_without_a_keyframe_is_blocked(service: ExplainerVisualGenerationService) -> None:
    plan = service.plan(PROJECT_ID, BEAT_REF, _command(purpose="VISUAL", mode="IMAGE_TO_VIDEO"))
    assert plan.status == "BLOCKED"
    assert plan.render_type_planned == "I2V"
    assert any(item["code"] == "EXPLAINER_KEYFRAME_REQUIRED" for item in plan.blockers)


def test_a_retired_still_motion_mode_is_refused(service: ExplainerVisualGenerationService) -> None:
    """The deterministic still-image push/pull route is gone.

    A ``VISUAL`` command may only ask for a real image-to-video execution; the retired
    ``STILL_MOTION`` mode is refused by the existing mode check rather than silently
    producing a picture that claims to be a clip.
    """

    with pytest.raises(ExplainerVisualGenerationError) as error:
        service.plan(PROJECT_ID, BEAT_REF, _command(purpose="VISUAL", mode="STILL_MOTION"))
    assert error.value.code == "EXPLAINER_MODE_INVALID"
    assert error.value.detail["allowed"] == ["IMAGE_TO_VIDEO"]


def test_a_keyframe_selection_from_another_beat_is_refused(
    service: ExplainerVisualGenerationService, database: Database
) -> None:
    selection_id = _adopt_keyframe(database)
    with database.transaction() as connection:
        ExplainerRepository(connection).insert(
            "explainer_visual_beats",
            {
                "id": "vg-other-beat",
                "video_id": VIDEO_ID,
                "code": "B999",
                "ordinal": 9,
                "render_type": "I2V",
                "visual_intent": "另一个画面段",
            },
        )
        connection.execute(
            "UPDATE explainer_beat_selections SET beat_id=? WHERE id=?",
            ("vg-other-beat", selection_id),
        )
    with pytest.raises(ExplainerVisualGenerationError) as error:
        service.plan(
            PROJECT_ID,
            BEAT_REF,
            _command(purpose="VISUAL", mode="IMAGE_TO_VIDEO", input_keyframe_selection_id=selection_id),
        )
    assert error.value.code == "EXPLAINER_KEYFRAME_OWNER_MISMATCH"


def test_a_superseded_keyframe_selection_is_refused(
    service: ExplainerVisualGenerationService, database: Database
) -> None:
    selection_id = _adopt_keyframe(database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE explainer_beat_selections SET status='SUPERSEDED' WHERE id=?", (selection_id,)
        )
    with pytest.raises(ExplainerVisualGenerationError) as error:
        service.plan(
            PROJECT_ID,
            BEAT_REF,
            _command(purpose="VISUAL", mode="IMAGE_TO_VIDEO", input_keyframe_selection_id=selection_id),
        )
    assert error.value.code == "EXPLAINER_KEYFRAME_NOT_ADOPTED"


def test_a_keyframe_whose_media_is_not_verified_is_refused(
    service: ExplainerVisualGenerationService, database: Database
) -> None:
    selection_id = _adopt_keyframe(database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE media_versions SET integrity_status='UNKNOWN' WHERE id=?", (MEDIA_VERSION_ID,)
        )
    with pytest.raises(ExplainerVisualGenerationError) as error:
        service.plan(
            PROJECT_ID,
            BEAT_REF,
            _command(purpose="VISUAL", mode="IMAGE_TO_VIDEO", input_keyframe_selection_id=selection_id),
        )
    assert error.value.code == "EXPLAINER_KEYFRAME_INTEGRITY_NOT_VERIFIED"


# --------------------------------------------------------------------------- #
# submit
# --------------------------------------------------------------------------- #
def test_submit_reserves_one_candidate_and_one_job_per_candidate(
    service: ExplainerVisualGenerationService, database: Database
) -> None:
    receipt = service.submit(PROJECT_ID, BEAT_REF, _command(candidate_count=2), idempotency_key="key-a")
    assert receipt["status"] == "ACCEPTED"
    assert receipt["accepted_count"] == 2
    assert {item["submission_status"] for item in receipt["items"]} == {"ACCEPTED"}
    assert all(item["job_id"] for item in receipt["items"])
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT status, job_id, variant_no, lineage_json FROM explainer_media_candidates WHERE beat_id=? ORDER BY variant_no",
            (BEAT_ID,),
        ).fetchall()
    assert [str(row["status"]) for row in rows] == ["PENDING", "PENDING"]
    assert [int(row["variant_no"]) for row in rows] == [1, 2]
    assert all(row["job_id"] for row in rows)
    assert all(json.loads(row["lineage_json"])["operation_id"] == "op-0000000001" for row in rows)


def test_submit_requires_an_idempotency_key(service: ExplainerVisualGenerationService) -> None:
    with pytest.raises(ExplainerVisualGenerationError) as error:
        service.submit(PROJECT_ID, BEAT_REF, _command(), idempotency_key="")
    assert error.value.code == "EXPLAINER_IDEMPOTENCY_KEY_REQUIRED"


def test_a_replay_extends_missing_ordinals_instead_of_creating_a_batch(
    service: ExplainerVisualGenerationService, database: Database
) -> None:
    service.submit(PROJECT_ID, BEAT_REF, _command(candidate_count=2), idempotency_key="key-b")
    replay = service.submit(PROJECT_ID, BEAT_REF, _command(candidate_count=2), idempotency_key="key-b")
    assert replay["idempotent_replay"] is True
    assert {item["submission_status"] for item in replay["items"]} == {"REPLAYED"}
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM explainer_media_candidates WHERE beat_id=?", (BEAT_ID,)
        ).fetchone()[0]
    assert count == 2


def test_a_blocked_plan_is_never_submitted(service: ExplainerVisualGenerationService) -> None:
    receipt = service.submit(
        PROJECT_ID,
        BEAT_REF,
        _command(purpose="VISUAL", mode="IMAGE_TO_VIDEO"),
        idempotency_key="key-c",
    )
    assert receipt["status"] == "REJECTED"
    assert receipt["accepted_count"] == 0
    assert receipt["items"] == []


# --------------------------------------------------------------------------- #
# workflow semantic slots
# --------------------------------------------------------------------------- #
def test_semantic_inputs_carry_the_candidate_seed(
    service: ExplainerVisualGenerationService,
) -> None:
    """``SEED`` is a required slot of every published image workflow.

    Without it the worker refuses the execution (``MP_COMFY_EXECUTION_INPUT_INVALID``,
    ``missing=['SEED']``) and several candidates of one batch would all render the
    workflow's authored seed instead of their own frozen one.
    """

    inputs = service._semantic_inputs("TEXT_TO_IMAGE", {"prompt": "深海", "negative_prompt": ""}, [], 4242, None)
    assert inputs["SEED"] == 4242
    assert inputs["PROMPT"] == "深海"
    assert inputs["WIDTH"] == 1920 and inputs["HEIGHT"] == 1080


def test_edit_semantic_inputs_use_the_declared_reference_slots(
    service: ExplainerVisualGenerationService,
) -> None:
    """The published edit workflows declare REFERENCE_IMAGE_1/2 and no WIDTH/HEIGHT."""

    reference = {"media_version_id": MEDIA_VERSION_ID, "sha256": SHA}
    inputs = service._semantic_inputs("IMAGE_EDIT", {"prompt": "重绘", "negative_prompt": ""}, [reference], 7, None)
    assert inputs["REFERENCE_IMAGE_1"]["media_version_id"] == MEDIA_VERSION_ID
    assert "WIDTH" not in inputs and "HEIGHT" not in inputs
    second = service._semantic_inputs("IMAGE_EDIT", {"prompt": "重绘", "negative_prompt": ""}, [reference, reference], 7, None)
    assert second["REFERENCE_IMAGE_2"]["sha256"] == SHA


def test_i2v_semantic_inputs_use_the_first_frame_slot(
    service: ExplainerVisualGenerationService, database: Database
) -> None:
    """A real adopted KEYFRAME selection is resolved and placed in ``FIRST_FRAME``.

    The command names an ``explainer_beat_selections`` row, not a raw picture; the plan
    loads and verifies it, and it must be reference number one so it occupies the
    ``FIRST_FRAME`` slot the published I2V workflow declares.
    """

    selection_id = _adopt_keyframe(database)
    plan = service.plan(
        PROJECT_ID,
        BEAT_REF,
        _command(purpose="VISUAL", mode="IMAGE_TO_VIDEO", input_keyframe_selection_id=selection_id),
    )
    assert plan.status == "EXECUTABLE"
    assert plan.render_type_planned == "I2V"
    assert plan.media_kind == "VIDEO"
    assert plan.resolved_references[0] == {
        "media_version_id": MEDIA_VERSION_ID,
        "sha256": SHA,
        "reference_kind": "KEYFRAME",
        "selection_id": selection_id,
    }
    for inputs in plan.semantic_inputs_by_seed.values():
        assert inputs["FIRST_FRAME"] == {"media_version_id": MEDIA_VERSION_ID, "sha256": SHA}
    # The slot naming itself still maps the first reference to FIRST_FRAME and carries
    # the planned duration for the motion model.
    direct = service._semantic_inputs(
        "IMAGE_TO_VIDEO",
        {"prompt": "运动", "negative_prompt": ""},
        [{"media_version_id": MEDIA_VERSION_ID, "sha256": SHA}],
        9,
        6000,
    )
    assert direct["FIRST_FRAME"]["media_version_id"] == MEDIA_VERSION_ID
    assert direct["DURATION_MS"] == 6000


def test_plan_freezes_one_semantic_input_set_per_candidate(
    service: ExplainerVisualGenerationService,
) -> None:
    plan = service.plan(PROJECT_ID, BEAT_REF, _command(candidate_count=2))
    assert len(plan.semantic_inputs_by_seed) == 2
    seeds = {str(seed) for seed in plan.candidate_seeds}
    assert set(plan.semantic_inputs_by_seed) == seeds
    # Each frozen set carries exactly its own seed, so the candidates really differ.
    for seed in seeds:
        assert plan.semantic_inputs_by_seed[seed]["SEED"] == int(seed)
        assert str(plan.resolution_hash_by_seed[seed])
    assert set(plan.resolution_hash_by_seed) == seeds
    assert plan.expected_resolution_hash in set(plan.resolution_hash_by_seed.values())


def test_submit_replays_the_frozen_semantic_inputs(
    service: ExplainerVisualGenerationService, fake_submission: _FakeSubmission
) -> None:
    plan = service.plan(PROJECT_ID, BEAT_REF, _command(candidate_count=2))
    service.submit(PROJECT_ID, BEAT_REF, _command(candidate_count=2), idempotency_key="key-frozen")
    assert len(fake_submission.calls) == 2
    submitted_seeds = []
    for call in fake_submission.calls:
        request = call["request"]
        submitted_seeds.append(request.semantic_inputs["SEED"])
        assert request.semantic_inputs["SEED"] in plan.candidate_seeds
        # The request the worker receives is the frozen one, slot for slot.
        assert request.semantic_inputs == plan.semantic_inputs_by_seed[str(request.semantic_inputs["SEED"])]


def test_a_style_ban_that_contradicts_the_content_is_reported_not_hidden(
    service: ExplainerVisualGenerationService, database: Database
) -> None:
    """A style/content contradiction is recorded so it cannot pass silently.

    Measured with the real model: the flat-infographic style ("无渐变阴影") rendered
    a continuous gradient for content that asks for one, and the local vision check
    answered ``style_match=FAIL`` while the compiler had reported no conflict.
    """

    with database.transaction() as connection:
        connection.execute(
            "UPDATE explainer_videos SET input_payload_json=? WHERE id=?",
            (
                json.dumps(
                    {
                        "script_policy": "PRESERVE_ORIGINAL",
                        "visual_preferences": {
                            "schema_version": "localdrama.explainer.visual-preferences.v1",
                            "image_candidate_count": 1,
                            "video_candidate_count": 1,
                            "style_prompt_override": "扁平矢量插画，几何色块，无渐变阴影，留白充足",
                            "negative_prompt_override": None,
                        },
                    }
                ),
                VIDEO_ID,
            ),
        )
        connection.execute(
            "UPDATE explainer_visual_beats SET prompt_intent=? WHERE id=?",
            ("海水从浅蓝渐变到深黑，阳光形成体积光", BEAT_ID),
        )

    plan = service.plan(PROJECT_ID, BEAT_REF, _command())
    notes = [item for item in plan.prompt_bundle.get("style_notes") or [] if item.get("kind") == "STYLE_CONTENT_CONFLICT_RISK"]
    assert notes, "a style ban against the content requirement must be reported"
    assert notes[0]["style_requirement"] == "无渐变"
    assert notes[0]["content_requirement"] in {"渐变", "体积光"}
    # It is a warning, not a plan blocker: the style is still a supported choice.
    assert plan.status == "EXECUTABLE"


def test_declared_semantic_slots_come_from_the_bound_workflow(    service: ExplainerVisualGenerationService, database: Database
) -> None:
    """Slots are read from the real published workflow, not guessed."""

    from tests.test_gpu_resource_policy_authority import (
        _ADAPTER_BINDING_ID,
        _PARAMETER_CONTRACT_ID,
        _RESOURCE_POLICY_ID,
        _RUNTIME_VERSION_ID,
        _seed_profile_contracts,
    )

    capability_id = _seed_profile_contracts(database, resource_policy={"gpu_runtime": "COMFY", "exclusive_gpu": True})
    with database.transaction() as connection:
        connection.execute(
            """INSERT OR REPLACE INTO workflows (id, code, title, created_at, updated_at, created_by)
            VALUES ('wf-owner-vg', 'vg_workflow', '生成工作流', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')"""
        )
        connection.execute(
            """INSERT OR REPLACE INTO workflow_versions
            (id, workflow_id, version_no, content_hash, status, contract_json, created_at, updated_at)
            VALUES ('wf-vg', 'wf-owner-vg', 1, 'hash-vg', 'PUBLISHED', ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')""",
            (
                json.dumps(
                    {
                        "input_slots": {
                            "PROMPT": {"required": True},
                            "SEED": {"required": True},
                            "STEPS": {"required": False},
                        }
                    }
                ),
            ),
        )
        connection.execute(
            """INSERT OR REPLACE INTO mp_execution_profiles (id, code, title, created_at, updated_at)
            VALUES ('profile-vg-slots', 'vg_slots', '生成 Profile', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"""
        )
        connection.execute(
            """INSERT OR REPLACE INTO mp_execution_profile_versions
            (id, profile_id, version_no, capability_definition_id, runtime_installation_version_id,
             parameter_contract_version_id, adapter_binding_contract_version_id, resource_policy_version_id,
             payload_json, payload_hash, created_at, updated_at)
            VALUES ('profile-vg-slots', 'profile-vg-slots', 1, ?, ?, ?, ?, ?,
                    ?, 'payload-hash-vg', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')""",
            (
                capability_id,
                _RUNTIME_VERSION_ID,
                _PARAMETER_CONTRACT_ID,
                _ADAPTER_BINDING_ID,
                _RESOURCE_POLICY_ID,
                json.dumps({"execution_binding": {"workflow_version_id": "wf-vg"}}),
            ),
        )

    declared = service._declared_semantic_slots("profile-vg-slots")
    assert declared is not None
    slots, required = declared
    assert slots == {"PROMPT", "SEED", "STEPS"}
    assert required == {"PROMPT", "SEED"}
    assert service._declared_semantic_slots("does-not-exist") is None
