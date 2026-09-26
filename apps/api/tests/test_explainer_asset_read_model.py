"""Step 2's read model and the entity ↔ shared-asset link (design §B3.1, §B3.3).

What these tests protect
------------------------
The step-2 page shows two numbers per object that must never be conflated —
``appearance_beat_count`` ("出场 N 镜") and ``reference_binding_status`` — plus the
category that decides whether the object is a mandatory fixed-appearance task at all.
A regression here is invisible in a unit test of the page (the page would simply fall
back to "状态未知" for every card and classify every object as 其他), so the read model
is asserted directly:

* the categories come from the entity type, and organisation/concept entities are not
  counted as missing mandatory references;
* the appearance count counts *beats that reference the entity*, never identity bindings;
* the reference status distinguishes 未设参考图 / 已采用参考图 / 待更新 and carries the
  real adopted media version and lock flag;
* the shared asset an adoption binds to is created once and then reused, so a project
  never ends up with two assets for one object.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from local_drama.api.routes.explainers import _assets_view
from local_drama.application.explainers.entity_assets import ensure_entity_story_asset
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "asset-read-project"
VIDEO_ID = "asset-read-video"
NOW = "2026-01-01T00:00:00Z"

CHARACTER_ID = "ar-character"
SCENE_ID = "ar-scene"
CONCEPT_ID = "ar-concept"
CHARACTER_STATE_ID = "ar-character-state"
CHARACTER_ASSET_ID = "ar-character-asset"
CHARACTER_MEDIA_ID = "ar-character-media"
MEDIA_ASSET_ID = "ar-media-asset"


def _seed(database: Database) -> None:
    with database.transaction() as connection:
        connection.execute("DELETE FROM projects WHERE id=?", (PROJECT_ID,))
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, target_duration_ms,
            product_kind, created_at, updated_at, created_by)
            VALUES (?, 'ar_proj', '读取', 'DRAFT', 'v2', ?, 300000, 'EXPLAINER',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, PROJECT_ID),
        )
        connection.execute(
            """INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale,
            input_kind, duration_mode, target_seconds, tolerance_percent, automation_mode, inference_mode,
            research_mode, status, input_payload_json, created_at, updated_at, created_by)
            VALUES (?, ?, '读取', '为什么', 'FACTUAL_EXPLAINER', 'zh-CN', 'TOPIC', 'FIXED', 300, 5,
            'AUTO_WITH_EXCEPTIONS', 'LOCAL_ONLY', 'OFFLINE_IMPORT', 'DRAFT', ?,
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (
                VIDEO_ID,
                PROJECT_ID,
                json.dumps(
                    {
                        "visual_preferences": {
                            "visual_strategy": "KEY_MOMENTS_I2V",
                            "image_candidate_count": 2,
                            "style_prompt_override": "冷色调纪录片质感",
                            "negative_prompt_override": "水印",
                        }
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        connection.execute(
            """INSERT INTO media_assets (id, project_id, owner_type, owner_id, purpose, media_kind,
            version_counter, metadata_json, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 'EXPLAINER_VIDEO', ?, 'EXPLAINER_ENTITY_REFERENCE', 'IMAGE', 1, '{}', ?, ?, 'test', 1, 'v2')""",
            (MEDIA_ASSET_ID, PROJECT_ID, VIDEO_ID, NOW, NOW),
        )
        connection.execute(
            """INSERT INTO media_versions (id, media_asset_id, version_no, take_no, stage, rel_path, mime_type,
            byte_size, sha256, integrity_status, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, 1, 'ASSET_HERO', 'media/hero.png', 'image/png', 1024, ?, 'VERIFIED', ?, ?, 'test', 1, 'v2')""",
            (CHARACTER_MEDIA_ID, MEDIA_ASSET_ID, "a" * 64, NOW, NOW),
        )
        connection.execute(
            """INSERT INTO story_assets (id, project_id, kind, code, name, description, extra_json, status,
            created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 'CHARACTER', 'AR_CHAR', '守灯员', '', '{}', 'ACTIVE', ?, ?, 'test', 1, 'v2')""",
            (CHARACTER_ASSET_ID, PROJECT_ID, NOW, NOW),
        )
        repo = ExplainerRepository(connection)
        repo.insert(
            "explainer_entities",
            {
                "id": CHARACTER_ID,
                "video_id": VIDEO_ID,
                "project_id": PROJECT_ID,
                "code": "ENT001",
                "entity_type": "FICTIONAL_CHARACTER",
                "name": "守灯员",
                "fictional": True,
                "story_asset_id": CHARACTER_ASSET_ID,
            },
        )
        repo.insert(
            "explainer_entities",
            {
                "id": SCENE_ID,
                "video_id": VIDEO_ID,
                "project_id": PROJECT_ID,
                "code": "ENT002",
                "entity_type": "LOCATION",
                "name": "灯塔",
            },
        )
        repo.insert(
            "explainer_entities",
            {
                "id": CONCEPT_ID,
                "video_id": VIDEO_ID,
                "project_id": PROJECT_ID,
                "code": "ENT003",
                "entity_type": "CONCEPT",
                "name": "信号规律",
            },
        )
        repo.insert(
            "entity_state_revisions",
            {
                "id": CHARACTER_STATE_ID,
                "entity_id": CHARACTER_ID,
                "revision_no": 1,
                "label": "基础状态",
                "condition": "深色旧外套，携带工作手电",
                "content_hash": "c" * 64,
            },
        )
        repo.update(
            "explainer_entities",
            CHARACTER_ID,
            {"canonical_state_revision_id": CHARACTER_STATE_ID},
        )
        # The character appears in two beats; the concept appears in one; the scene in none.
        for beat_id, code, refs in [
            ("ar-beat-1", "B001", [CHARACTER_ID]),
            ("ar-beat-2", "B002", [CHARACTER_ID, CONCEPT_ID]),
        ]:
            repo.insert(
                "explainer_visual_beats",
                {
                    "id": beat_id,
                    "video_id": VIDEO_ID,
                    "code": code,
                    "ordinal": int(code[1:]),
                    "render_type": "I2V",
                    "visual_intent": "画面",
                    "prompt_intent": "画面",
                    "entity_refs_json": refs,
                },
            )
        # An identity binding is a different fact and must not be read as an appearance.
        repo.insert(
            "entity_identity_bindings",
            {
                "id": "ar-binding",
                "video_id": VIDEO_ID,
                "entity_id": SCENE_ID,
                "beat_id": "ar-beat-1",
                "status": "ACTIVE",
                "binding_hash": "b" * 64,
            },
        )


def _entity(view: dict[str, Any], entity_id: str) -> dict[str, Any]:
    return next(item for item in view["entities"] if item["entity_id"] == entity_id)


@pytest.fixture()
def view(database: Database) -> dict[str, Any]:
    _seed(database)
    with database.connect() as connection:
        return _assets_view(ExplainerRepository(connection), PROJECT_ID)


def test_categories_come_from_the_entity_type_and_exclude_semantic_only_objects(view) -> None:
    assert _entity(view, CHARACTER_ID)["asset_kind"] == "CHARACTER"
    assert _entity(view, SCENE_ID)["asset_kind"] == "SCENE"
    assert _entity(view, CONCEPT_ID)["asset_kind"] == "OTHER", (
        "a concept is not a mandatory fixed-appearance task (design §B3.1)"
    )
    assert view["entity_counts"] == {"CHARACTER": 1, "SCENE": 1, "OTHER": 1}


def test_the_appearance_count_counts_beats_not_identity_bindings(view) -> None:
    character = _entity(view, CHARACTER_ID)
    scene = _entity(view, SCENE_ID)
    assert character["appearance_beat_count"] == 2, "出场镜数必须数真正引用该实体的画面段"
    assert scene["appearance_beat_count"] == 0, "身份绑定数不能冒充出场数"
    assert scene["beat_reference_count"] == 1, "身份绑定数仍作为独立事实保留"


def test_an_object_without_a_reference_reports_where_the_reference_will_bind(view) -> None:
    character = _entity(view, CHARACTER_ID)
    assert character["reference_binding_status"] == "NO_REFERENCE"
    assert character["reference"] is None
    assert character["missing_reason"] == "还没有采用参考图"
    scene = _entity(view, SCENE_ID)
    assert scene["missing_reason"] == "还没有共享资产；采用第一张参考图时会自动建立或复用同名资产"
    # Only the two fixed-appearance objects without a picture count as missing.
    assert view["missing_reference_count"] == 2
    assert _entity(view, CONCEPT_ID)["requires_reference"] is False
    assert _entity(view, CHARACTER_ID)["requires_reference"] is True
    # The retired ``visual_strategy`` selector stored on a legacy film is dropped by
    # ``normalise_visual_preferences`` instead of being read back as a live choice.
    assert "visual_strategy" not in view["visual_preferences"]
    assert view["visual_preferences"]["image_candidate_count"] == 2
    assert view["resolved_style"]["style_prompt"] == "冷色调纪录片质感"
    assert view["resolved_style"]["negative_prompt"] == "水印"
    assert view["resolved_style"]["source"] == "VISUAL_PREFERENCE_OVERRIDE"
    assert view["resolved_style"]["style_hash"]
    assert view["project_id"] == PROJECT_ID


def test_a_patch_that_still_sends_visual_strategy_is_ignored_not_refused() -> None:
    """The PATCH request no longer carries ``visual_strategy`` anywhere.

    ``ExplainerVisualPreferencesRequest`` keeps ``visual_preferences`` as a free
    object, so a client that still sends the retired key is accepted and the key is
    dropped by ``merge_visual_preferences`` — it is silently ignored, never applied
    and never an error.
    """

    from local_drama.api.schemas.explainers import ExplainerVisualPreferencesRequest
    from local_drama.application.explainers.contracts_v2 import merge_visual_preferences

    request = ExplainerVisualPreferencesRequest(
        expected_revision=1,
        visual_preferences={"visual_strategy": "PARALLAX", "image_candidate_count": 4},
    )
    merged = merge_visual_preferences({}, request.visual_preferences)
    assert "visual_strategy" not in merged
    assert merged["image_candidate_count"] == 4
    assert merged["video_candidate_count"] == 1


def test_state_revisions_are_keyed_rows_and_the_visual_description_is_real(view) -> None:
    character = _entity(view, CHARACTER_ID)
    assert [row["id"] for row in character["state_revisions"]] == [CHARACTER_STATE_ID]
    assert character["canonical_state_revision_id"] == CHARACTER_STATE_ID
    assert character["visual_description"] == "深色旧外套，携带工作手电"
    assert character["candidate_counts"] == {"REFERENCE": 0, "KEYFRAME": 0, "VISUAL": 0}


def test_an_active_reference_is_reported_with_its_media_and_lock(database: Database, view) -> None:
    with database.transaction() as connection:
        ExplainerRepository(connection).insert(
            "story_asset_references",
            {
                "id": "ar-reference",
                "project_id": PROJECT_ID,
                "story_asset_id": CHARACTER_ASSET_ID,
                "asset_state_id": None,
                "media_version_id": CHARACTER_MEDIA_ID,
                "reference_kind": "HERO",
                "label": "守灯员 主参考",
                "priority": 100,
                "is_locked": 1,
                "metadata_json": {"explainer_entity_state_revision_id": CHARACTER_STATE_ID},
                "status": "ACTIVE",
            },
        )
    with database.connect() as connection:
        refreshed = _assets_view(ExplainerRepository(connection), PROJECT_ID)
    character = _entity(refreshed, CHARACTER_ID)
    assert character["reference_binding_status"] == "ADOPTED_REFERENCE"
    assert character["reference"]["media_version_id"] == CHARACTER_MEDIA_ID
    assert character["reference"]["is_locked"] is True
    assert character["missing_reason"] is None
    assert character["candidate_counts"] == {"REFERENCE": 0, "KEYFRAME": 0, "VISUAL": 0}
    assert refreshed["missing_reference_count"] == 1


def test_a_reference_adopted_for_an_older_state_reads_as_needs_update(
    database: Database, view
) -> None:
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        repo.insert(
            "story_asset_references",
            {
                "id": "ar-reference-old",
                "project_id": PROJECT_ID,
                "story_asset_id": CHARACTER_ASSET_ID,
                "asset_state_id": None,
                "media_version_id": CHARACTER_MEDIA_ID,
                "reference_kind": "HERO",
                "label": "守灯员 主参考",
                "priority": 100,
                "metadata_json": {"explainer_entity_state_revision_id": CHARACTER_STATE_ID},
                "status": "ACTIVE",
            },
        )
        repo.insert(
            "entity_state_revisions",
            {
                "id": "ar-state-2",
                "entity_id": CHARACTER_ID,
                "revision_no": 2,
                "label": "雨夜状态",
                "condition": "湿透的外套",
                "content_hash": "d" * 64,
            },
        )
        repo.update("explainer_entities", CHARACTER_ID, {"canonical_state_revision_id": "ar-state-2"})
    with database.connect() as connection:
        refreshed = _assets_view(ExplainerRepository(connection), PROJECT_ID)
    character = _entity(refreshed, CHARACTER_ID)
    assert character["reference_binding_status"] == "NEEDS_UPDATE"
    assert character["missing_reason"] == "对象状态已更新，当前参考图待确认"
    assert character["reference"]["media_version_id"] == CHARACTER_MEDIA_ID, "当前采用的图不能因为待更新而消失"


def test_adopting_a_reference_establishes_the_shared_asset_and_records_the_state(
    database: Database,
) -> None:
    """§B3.3 adoption must not need a pre-existing asset, and must stay FK-safe.

    ``story_asset_references.asset_state_id`` points at the shared
    ``story_asset_states`` table, so an explainer state revision can never be written
    there; it is recorded in metadata instead and the read model compares that.
    """

    from local_drama.api.routes.explainers import _adopt_entity_reference
    from local_drama.api.schemas.explainers import ExplainerReferenceAdoptionRequest

    _seed(database)
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        assert repo.get("explainer_entities", SCENE_ID)["story_asset_id"] is None
        result = _adopt_entity_reference(
            repo,
            PROJECT_ID,
            SCENE_ID,
            ExplainerReferenceAdoptionRequest(
                media_version_id=CHARACTER_MEDIA_ID,
                entity_state_revision_id=None,
                lock=True,
                actor="operator",
            ),
        )
        assert result["locked"] is True
        reference = repo.find("story_asset_references", str(result["reference_id"]))
        assert reference is not None
        assert reference["asset_state_id"] is None, "explainer 状态不能写进 story_asset_states 外键"
        assert bool(reference["is_locked"]) is True
        entity = repo.get("explainer_entities", SCENE_ID)
        assert entity["story_asset_id"], "采用应当建立或复用共享资产"
        asset = repo.get("story_assets", str(entity["story_asset_id"]))
        assert asset["kind"] == "SCENE"

    with database.connect() as connection:
        refreshed = _assets_view(ExplainerRepository(connection), PROJECT_ID)
    scene = _entity(refreshed, SCENE_ID)
    assert scene["reference_binding_status"] == "ADOPTED_REFERENCE"
    assert scene["reference"]["media_version_id"] == CHARACTER_MEDIA_ID
    assert scene["reference"]["is_locked"] is True
    assert refreshed["missing_reference_count"] == 1, "只有仍未设参考图的人物/场景还算缺失"


def test_adopting_with_a_foreign_state_revision_is_refused(database: Database) -> None:
    from local_drama.api.routes.explainers import _adopt_entity_reference
    from local_drama.api.schemas.explainers import ExplainerReferenceAdoptionRequest
    from local_drama.domain.explainers.contracts import ExplainerContractError

    _seed(database)
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        with pytest.raises(ExplainerContractError) as error:
            _adopt_entity_reference(
                repo,
                PROJECT_ID,
                SCENE_ID,
                ExplainerReferenceAdoptionRequest(
                    media_version_id=CHARACTER_MEDIA_ID,
                    # Belongs to the character, not to the scene.
                    entity_state_revision_id=CHARACTER_STATE_ID,
                    actor="operator",
                ),
            )
    assert error.value.code == "INVALID_REQUEST"


def test_the_shared_asset_is_created_once_and_then_reused(database: Database) -> None:
    _seed(database)
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        scene = repo.get("explainer_entities", SCENE_ID)
        first = ensure_entity_story_asset(repo, project_id=PROJECT_ID, entity=scene, actor="tester")
        assert first["created"] is True
        assert first["kind"] == "SCENE"
        linked = repo.get("explainer_entities", SCENE_ID)
        assert linked["story_asset_id"] == first["story_asset_id"]
        asset = repo.get("story_assets", first["story_asset_id"])
        assert asset["name"] == "灯塔"
        assert asset["canonical_media_version_id"] is None, "建立资产不等于已经出图"

        # A second call is idempotent: the same asset, no duplicate row.
        second = ensure_entity_story_asset(repo, project_id=PROJECT_ID, entity=linked, actor="tester")
        assert second["created"] is False
        assert second["reused"] is True
        assert second["story_asset_id"] == first["story_asset_id"]

        # A *different* entity with the same name reuses the project's existing asset
        # instead of creating a second one for the same object.
        repo.insert(
            "explainer_entities",
            {
                "id": "ar-scene-copy",
                "video_id": VIDEO_ID,
                "project_id": PROJECT_ID,
                "code": "ENT009",
                "entity_type": "LOCATION",
                "name": "灯塔",
            },
        )
        copy = repo.get("explainer_entities", "ar-scene-copy")
        third = ensure_entity_story_asset(repo, project_id=PROJECT_ID, entity=copy, actor="tester")
        assert third["story_asset_id"] == first["story_asset_id"]
        assert third["created"] is False

        rows = repo.query_all(
            "SELECT id FROM story_assets WHERE project_id = ? AND name = '灯塔'", (PROJECT_ID,)
        )
        assert len(rows) == 1, "一个项目不应为同名对象产生两行共享资产"

        # An entity that already points at an ACTIVE asset keeps it untouched.
        character = repo.get("explainer_entities", CHARACTER_ID)
        kept = ensure_entity_story_asset(repo, project_id=PROJECT_ID, entity=character, actor="tester")
        assert kept["story_asset_id"] == CHARACTER_ASSET_ID
        assert kept["linked"] is False
