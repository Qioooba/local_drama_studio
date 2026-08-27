from __future__ import annotations

import base64
import hashlib
import json
import shutil
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient

from local_drama.application.media import MediaService
from local_drama.application.project_packages import ProjectPackageService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.shot_studio_command_repository import shot_studio_command_service
from local_drama.main import create_app


def _project(workspace, database) -> dict[str, object]:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="package_source",
        title="项目包源",
        episode_count=2,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=90_000,
        allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(projects.list_seasons(str(project["id"]))[0]["id"])[0]
    shot = projects.create_shot(str(episode["id"]), "SHOT_001", 4_000, "CLOSE_UP")
    shot_studio_command_service(database).save_draft_revision(str(shot["id"]), {"subject_action": "turn", "dialogue": "本地"}, freeze=True)
    root = workspace.projects_root / "package_source"
    sample = root / "01_story" / "source_documents" / "中文 剧本.md"
    sample.write_text("# 本地项目包\n", encoding="utf-8")
    return project


def _registered_image(workspace, database, project_id: str) -> tuple[str, str]:
    content = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
    relative = "04_media/images/assets/imported.png"
    path = workspace.projects_root / "package_source" / relative
    path.write_bytes(content)
    asset_id, version_id = str(uuid.uuid4()), str(uuid.uuid4())
    with database.transaction() as connection:
        shot_id = connection.execute(
            "SELECT sh.id FROM shots sh JOIN episodes e ON e.id=sh.episode_id JOIN seasons s ON s.id=e.season_id WHERE s.project_id=?",
            (project_id,),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO media_assets (id,project_id,owner_type,owner_id,purpose,media_kind,version_counter,metadata_json)
            VALUES (?,?,'SHOT',?,'KEYFRAME','IMAGE',1,'{}')""",
            (asset_id, project_id, shot_id),
        )
        connection.execute(
            """INSERT INTO media_versions (id,media_asset_id,version_no,take_no,stage,rel_path,mime_type,byte_size,sha256,
            integrity_status,source_name,import_source,probe_json) VALUES (?,?,1,1,'KEYFRAME',?,'image/png',?,?,'VERIFIED','imported.png','LOCAL_FILE','{}')""",
            (version_id, asset_id, relative, len(content), hashlib.sha256(content).hexdigest()),
        )
    return asset_id, version_id


def test_project_package_export_is_verified_deterministic_and_database_read_only(workspace, database) -> None:
    project = _project(workspace, database)
    service = ProjectPackageService(database, workspace.projects_root)
    with database.connect() as connection:
        audit_before = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    first = service.export(str(project["id"]))
    second = service.export(str(project["id"]))
    assert first["status"] == "EXPORTED" and first["reused"] is False
    assert second["reused"] is True and second["sha256"] == first["sha256"]
    assert first["database_mutated"] is False and first["network_contacted"] is False
    inspected = service.dry_run(str(project["id"]), str(first["rel_path"]))
    assert inspected["status"] == "READY_REBIND_EXISTING"
    assert inspected["conflict_options"] == ["REBIND_EXISTING", "IMPORT_AS_COPY_REWRITE_IDENTITY"]
    assert inspected["would_import"] is False and inspected["mutated"] is False
    package = workspace.projects_root / "package_source" / str(first["rel_path"])
    with zipfile.ZipFile(package) as archive:
        assert "payload/01_story/source_documents/中文 剧本.md" in archive.namelist()
        assert "package-manifest.json" in archive.namelist()
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == audit_before


def test_project_package_never_bundles_external_user_model_reference(workspace, database) -> None:
    """User-selected model weights remain machine references, never package payload."""

    project = _project(workspace, database)
    external_model = workspace.data_root.parent / "user-model.safetensors"
    external_model.write_bytes(b"user-owned model bytes")
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO model_artifacts
            (id,runtime_id,code,kind,machine_path_ref,sha256,size_bytes,license_note,compatibility_json,
             status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,?,?,?,'CANDIDATE',?,?,?,?, 'v2')""",
            (
                str(uuid.uuid4()),
                None,
                "external-user-model",
                "T2V",
                str(external_model.resolve()),
                hashlib.sha256(external_model.read_bytes()).hexdigest(),
                external_model.stat().st_size,
                "USER_SUPPLIED_LOCAL_MODEL",
                "{}",
                "now",
                "now",
                "test",
                1,
            ),
        )
    exported = ProjectPackageService(database, workspace.projects_root).export(str(project["id"]))
    package = workspace.projects_root / "package_source" / str(exported["rel_path"])
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        state = archive.read("project-state.json")
    assert not any("user-model.safetensors" in name for name in names)
    assert str(external_model.resolve()).encode("utf-8") not in state
    assert external_model.is_file()


def test_project_package_dry_run_rejects_zip_slip_duplicates_and_unregistered_paths(workspace, database) -> None:
    project = _project(workspace, database)
    service = ProjectPackageService(database, workspace.projects_root)
    output = workspace.projects_root / "package_source" / "exports" / "project-packages"
    output.mkdir(parents=True, exist_ok=True)
    malicious = output / "malicious.ldspkg"
    with zipfile.ZipFile(malicious, "w") as archive:
        archive.writestr("../escape", b"bad")
        archive.writestr("package-manifest.json", b"{}")
        archive.writestr("project-state.json", b"{}")
    with pytest.raises(DomainRuleError) as unsafe:
        service.dry_run(str(project["id"]), "exports/project-packages/malicious.ldspkg")
    assert unsafe.value.code == "PROJECT_PACKAGE_PATH_INVALID"
    outside = workspace.projects_root / "outside.ldspkg"
    outside.write_bytes(b"not a package")
    with pytest.raises(DomainRuleError) as path_error:
        service.dry_run(str(project["id"]), "../outside.ldspkg")
    assert path_error.value.code == "PROJECT_PACKAGE_PATH_NOT_ALLOWED"


def test_project_package_api_exports_and_dry_runs_only_registered_package(workspace, database) -> None:
    project = _project(workspace, database)
    with TestClient(create_app(workspace)) as client:
        exported = client.post(f"/api/v1/projects/{project['id']}/packages:export")
        assert exported.status_code == 200
        package = exported.json()["package"]
        inspected = client.post(f"/api/v1/projects/{project['id']}/packages:dry-run", json={"rel_path": package["rel_path"]})
    assert inspected.status_code == 200
    assert inspected.json()["dry_run"]["status"] == "READY_REBIND_EXISTING"


def test_project_package_browser_download_and_upload(workspace, database) -> None:
    project = _project(workspace, database)
    with TestClient(create_app(workspace)) as client:
        exported = client.post(f"/api/v1/projects/{project['id']}/packages:export").json()["package"]
        downloaded = client.get(f"/api/v1/projects/{project['id']}/packages:download", params={"rel_path": exported["rel_path"]})
        uploaded = client.post(
            "/api/v1/project-packages:upload",
            content=downloaded.content,
            headers={"X-File-Name": "browser-copy.ldspkg", "Content-Type": "application/octet-stream"},
        )
    assert downloaded.status_code == 200 and downloaded.content[:2] == b"PK"
    assert uploaded.status_code == 201
    assert (workspace.data_root / "imports" / "project-packages" / "inbox" / uploaded.json()["package"]["name"]).is_file()


def test_external_package_staging_uses_fixed_inbox_and_content_addressed_token(workspace, database) -> None:
    project = _project(workspace, database)
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    exported = service.export(str(project["id"]))
    source = workspace.projects_root / "package_source" / str(exported["rel_path"])
    inbox = workspace.data_root / "imports" / "project-packages" / "inbox"
    inbox.mkdir(parents=True)
    shutil.copy2(source, inbox / "外部 项目.ldspkg")
    staged = service.stage_from_inbox("外部 项目.ldspkg")
    repeated = service.stage_from_inbox("外部 项目.ldspkg")
    assert staged["status"] == "STAGED" and staged["source_retained"] is True
    assert staged["stage_token"] == exported["sha256"] and repeated["reused"] is True
    assert service.dry_run_staged(str(staged["stage_token"]))["status"] == "READY_REBIND_EXISTING"
    assert (inbox / "外部 项目.ldspkg").is_file()
    assert not list((workspace.data_root / "imports" / "project-packages" / "staged").glob(".partial-*"))
    with pytest.raises(DomainRuleError) as traversal:
        service.stage_from_inbox("../外部 项目.ldspkg")
    assert traversal.value.code == "PROJECT_PACKAGE_INBOX_NAME_INVALID"


def test_external_package_staging_api_never_accepts_arbitrary_paths(workspace, database) -> None:
    project = _project(workspace, database)
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    exported = service.export(str(project["id"]))
    source = workspace.projects_root / "package_source" / str(exported["rel_path"])
    inbox = workspace.data_root / "imports" / "project-packages" / "inbox"
    inbox.mkdir(parents=True)
    shutil.copy2(source, inbox / "api.ldspkg")
    with TestClient(create_app(workspace)) as client:
        staged = client.post("/api/v1/project-packages:stage", json={"inbox_name": "api.ldspkg"})
        assert staged.status_code == 200
        token = staged.json()["staging"]["stage_token"]
        dry_run = client.post(f"/api/v1/project-packages/{token}:dry-run")
        rejected = client.post("/api/v1/project-packages:stage", json={"inbox_name": str(source)})
    assert dry_run.status_code == 200 and dry_run.json()["dry_run"]["would_import"] is False
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "PROJECT_PACKAGE_INBOX_NAME_INVALID"


def test_staged_project_package_import_as_copy_rewrites_identity_and_retains_retry_source(workspace, database) -> None:
    source_project = _project(workspace, database)
    _registered_image(workspace, database, str(source_project["id"]))
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    exported = service.export(str(source_project["id"]))
    source = workspace.projects_root / "package_source" / str(exported["rel_path"])
    inbox = workspace.data_root / "imports" / "project-packages" / "inbox"
    inbox.mkdir(parents=True)
    shutil.copy2(source, inbox / "copy.ldspkg")
    token = str(service.stage_from_inbox("copy.ldspkg")["stage_token"])

    imported = service.import_as_copy(token, code="package_copy", title="项目包副本")
    repeated = service.import_as_copy(token, code="package_copy", title="项目包副本")

    assert imported["status"] == "IMPORTED"
    assert repeated["reused"] is True and repeated["project_id"] == imported["project_id"]
    assert imported["identity_mode"] == "IMPORT_AS_COPY_REWRITE_IDENTITY"
    assert imported["project_id"] != source_project["id"]
    assert imported["counts"]["episodes"] == 2
    assert imported["counts"]["shots"] == 1
    assert imported["counts"]["media_assets"] == 1
    assert imported["counts"]["media_versions"] == 1
    assert imported["counts"]["thumbnails_pending"] == 0
    assert imported["counts"]["thumbnails_created"] == 1
    assert (workspace.data_root / "imports" / "project-packages" / "staged" / f"{token}.ldspkg").is_file()
    copied_root = workspace.projects_root / "package_copy"
    assert (copied_root / "01_story" / "source_documents" / "中文 剧本.md").read_text(encoding="utf-8") == "# 本地项目包\n"
    project_json = json.loads((copied_root / "project.json").read_text(encoding="utf-8"))
    assert project_json["project_id"] == imported["project_id"]
    assert project_json["project_code"] == "package_copy"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects WHERE id=?", (imported["project_id"],)).fetchone()[0] == 1
        assert (
            connection.execute("SELECT COUNT(*) FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE s.project_id=?", (imported["project_id"],)).fetchone()[
                0
            ]
            == 2
        )
        imported_media = connection.execute(
            """SELECT ma.owner_id,mv.id AS media_version_id,mv.rel_path,mv.sha256 FROM media_assets ma JOIN media_versions mv ON mv.media_asset_id=ma.id
            JOIN shots sh ON sh.id=ma.owner_id JOIN episodes e ON e.id=sh.episode_id JOIN seasons s ON s.id=e.season_id
            WHERE ma.project_id=? AND s.project_id=?""",
            (imported["project_id"], imported["project_id"]),
        ).fetchone()
        assert imported_media is not None
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM media_cache_entries mce JOIN media_versions mv ON mv.id=mce.media_version_id WHERE mv.id IN (SELECT mv2.id FROM media_versions mv2 JOIN media_assets ma ON ma.id=mv2.media_asset_id WHERE ma.project_id=?)",
                (imported["project_id"],),
            ).fetchone()[0]
            == 1
        )
        audit = connection.execute("SELECT action FROM audit_events WHERE subject_id=? ORDER BY rowid DESC LIMIT 1", (imported["project_id"],)).fetchone()
        assert connection.execute("SELECT COUNT(*) FROM project_package_imports WHERE status='COMPLETED'").fetchone()[0] == 1
    assert audit["action"] == "PROJECT_PACKAGE_IMPORTED"
    assert hashlib.sha256((copied_root / imported_media["rel_path"]).read_bytes()).hexdigest() == imported_media["sha256"]
    thumbnail, mime = MediaService(database, workspace).thumbnail(str(imported_media["media_version_id"]), "small")
    assert thumbnail.is_file() and thumbnail.stat().st_size > 0 and mime == "image/webp"
    assert "small" in thumbnail.parts


def test_project_package_roundtrip_preserves_asset_bible_and_preference_history(workspace, database) -> None:
    source_project = _project(workspace, database)
    project_id = str(source_project["id"])
    _, media_version_id = _registered_image(workspace, database, project_id)
    now = "2026-08-20T00:00:00Z"
    asset_id, state_id = str(uuid.uuid4()), str(uuid.uuid4())
    preference_set_id, preference_v1, preference_v2 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    with database.transaction() as connection:
        episode_id = str(
            connection.execute(
                "SELECT e.id FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE s.project_id=? ORDER BY e.display_order LIMIT 1",
                (project_id,),
            ).fetchone()[0]
        )
        shot_id = str(
            connection.execute(
                "SELECT sh.id FROM shots sh JOIN episodes e ON e.id=sh.episode_id JOIN seasons s ON s.id=e.season_id WHERE s.project_id=? LIMIT 1",
                (project_id,),
            ).fetchone()[0]
        )
        connection.execute(
            """INSERT INTO story_assets
            (id,project_id,kind,code,name,description,canonical_media_version_id,extra_json,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,'CHARACTER','CHAR_HERO','Hero','scar',?,'{"palette":"red"}','ACTIVE',?,?, 'author',3,'v2')""",
            (asset_id, project_id, media_version_id, now, now),
        )
        connection.execute(
            """INSERT INTO story_asset_states
            (id,project_id,story_asset_id,code,label,state_kind,description,state_json,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,'INJURED','Injured','INJURY','act 2','{"severity":2}','ACTIVE',?,?,'author',2,'v1')""",
            (state_id, project_id, asset_id, now, now),
        )
        connection.execute(
            """INSERT INTO story_asset_references
            (id,project_id,story_asset_id,asset_state_id,media_version_id,reference_kind,label,priority,is_locked,
             metadata_json,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,'FRONT','front',10,1,'{"source":"approved"}','ACTIVE',?,?,'author',4,'v1')""",
            (str(uuid.uuid4()), project_id, asset_id, state_id, media_version_id, now, now),
        )
        connection.execute(
            """INSERT INTO episode_asset_state_bindings
            (id,episode_id,story_asset_id,asset_state_id,created_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,'author',2,'v1')""",
            (str(uuid.uuid4()), episode_id, asset_id, state_id, now),
        )
        connection.execute(
            """INSERT INTO shot_asset_bindings
            (id,shot_id,asset_id,asset_state_id,role_in_shot,created_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,'main',?,'author',2,'v2')""",
            (str(uuid.uuid4()), shot_id, asset_id, state_id, now),
        )
        connection.execute(
            """INSERT INTO generation_preference_sets
            (id,project_id,owner_type,owner_id,capability,current_version_id,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,'PROJECT',?,'T2I',NULL,'ACTIVE',?,?,'director',2,'v1')""",
            (preference_set_id, project_id, project_id, now, now),
        )
        for version_id, version_no, settings in ((preference_v1, 1, {"steps": 20}), (preference_v2, 2, {"steps": 28})):
            connection.execute(
                """INSERT INTO generation_preference_versions
                (id,preference_set_id,version_no,execution_profile_version_id,resolution_mode,settings_json,reason,is_frozen,created_at,created_by,schema_version)
                VALUES (?,?,?,NULL,'AUTO',?,'director choice',1,?,'director','v1')""",
                (version_id, preference_set_id, version_no, json.dumps(settings), now),
            )
        connection.execute("UPDATE generation_preference_sets SET current_version_id=? WHERE id=?", (preference_v2, preference_set_id))

    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    exported = service.export(project_id)
    source = workspace.projects_root / "package_source" / str(exported["rel_path"])
    inbox = workspace.data_root / "imports" / "project-packages" / "inbox"
    inbox.mkdir(parents=True)
    shutil.copy2(source, inbox / "v2-facts.ldspkg")
    token = str(service.stage_from_inbox("v2-facts.ldspkg")["stage_token"])
    imported = service.import_as_copy(token, code="package_v2_facts", title="V2 facts")

    assert imported["counts"]["story_assets"] == 1
    assert imported["counts"]["story_asset_states"] == 1
    assert imported["counts"]["story_asset_references"] == 1
    assert imported["counts"]["episode_asset_state_bindings"] == 1
    assert imported["counts"]["shot_asset_bindings"] == 1
    assert imported["counts"]["generation_preference_sets"] == 1
    assert imported["counts"]["generation_preference_versions"] == 2
    with database.connect() as connection:
        imported_asset = connection.execute(
            "SELECT id,canonical_media_version_id,extra_json,revision FROM story_assets WHERE project_id=?",
            (imported["project_id"],),
        ).fetchone()
        assert imported_asset is not None and json.loads(imported_asset["extra_json"]) == {"palette": "red"}
        assert imported_asset["canonical_media_version_id"] != media_version_id and imported_asset["revision"] == 3
        reference = connection.execute(
            "SELECT r.reference_kind,r.metadata_json FROM story_asset_references r WHERE r.project_id=?",
            (imported["project_id"],),
        ).fetchone()
        assert reference["reference_kind"] == "FRONT" and json.loads(reference["metadata_json"])["source"] == "approved"
        versions = connection.execute(
            """SELECT v.version_no,v.settings_json,s.current_version_id,v.id FROM generation_preference_versions v
            JOIN generation_preference_sets s ON s.id=v.preference_set_id WHERE s.project_id=? ORDER BY v.version_no""",
            (imported["project_id"],),
        ).fetchall()
        assert [json.loads(row["settings_json"])["steps"] for row in versions] == [20, 28]
        assert versions[1]["id"] == versions[1]["current_version_id"]


def test_project_package_roundtrip_rewrites_groups_qc_and_director_recipe_refs(workspace, database) -> None:
    source_project = _project(workspace, database)
    project_id = str(source_project["id"])
    now = "2026-08-20T00:00:00Z"
    scene_id, group_id = str(uuid.uuid4()), str(uuid.uuid4())
    policy_set_id, policy_version_id = str(uuid.uuid4()), str(uuid.uuid4())
    recipe_id, recipe_version_id = str(uuid.uuid4()), str(uuid.uuid4())
    asset_id, proposal_id = str(uuid.uuid4()), str(uuid.uuid4())
    with database.transaction() as connection:
        episode_id = str(
            connection.execute(
                "SELECT e.id FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE s.project_id=? ORDER BY e.display_order LIMIT 1",
                (project_id,),
            ).fetchone()[0]
        )
        shot_id = str(
            connection.execute(
                "SELECT sh.id FROM shots sh JOIN episodes e ON e.id=sh.episode_id JOIN seasons s ON s.id=e.season_id WHERE s.project_id=? LIMIT 1",
                (project_id,),
            ).fetchone()[0]
        )
        connection.execute(
            "INSERT INTO scenes (id,project_id,code,title,location,time_of_day,created_at,updated_at,created_by) VALUES (?,?,?,?,?,?,?,?,?)",
            (scene_id, project_id, "SCENE_A", "Scene A", "studio", "night", now, now, "test"),
        )
        connection.execute("UPDATE shots SET scene_id=? WHERE id=?", (scene_id, shot_id))
        connection.execute(
            """INSERT INTO shot_groups
            (id,episode_id,scene_id,kind,code,title,order_key,metadata_json,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,'BEAT','GROUP_A','Beat A','1','{"mood":"tense"}','ACTIVE',?,?, 'test',2,'v1')""",
            (group_id, episode_id, scene_id, now, now),
        )
        connection.execute(
            "INSERT INTO shot_group_members (group_id,shot_id,order_key,created_at,created_by) VALUES (?,?, '1',?,'test')",
            (group_id, shot_id, now),
        )
        connection.execute(
            """INSERT INTO story_assets
            (id,project_id,kind,code,name,description,canonical_media_version_id,extra_json,status,
             created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,'CHARACTER','CHAR_A','角色甲','',NULL,'{}','ACTIVE',?,?,'test',1,'v2')""",
            (asset_id, project_id, now, now),
        )
        connection.execute(
            """INSERT INTO story_asset_proposals
            (id,project_id,breakdown_draft_id,proposal_key,kind,name,evidence_json,suggested_asset_id,resolved_asset_id,
             status,decision_note,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,NULL,'CHARACTER:角色甲','CHARACTER','角色甲','{"scene_count":2}',?,?,'ACCEPTED_MERGE','same',?,?,'test',2,'v2')""",
            (proposal_id, project_id, asset_id, asset_id, now, now),
        )
        connection.execute(
            """INSERT INTO generation_qc_policy_sets
            (id,project_id,owner_type,owner_id,stage,current_version_id,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,'PROJECT',?,'VIDEO',NULL,'ACTIVE',?,?,'test',2,'v1')""",
            (policy_set_id, project_id, project_id, now, now),
        )
        policy = {"thresholds": {"continuity": 0.8}}
        connection.execute(
            """INSERT INTO generation_qc_policy_versions
            (id,policy_set_id,version_no,policy_json,max_auto_rerolls,auto_reroll_categories_json,is_frozen,reason,created_at,created_by,schema_version)
            VALUES (?,?,1,?,2,'["continuity"]',1,'baseline',?,'test','v1')""",
            (policy_version_id, policy_set_id, json.dumps(policy), now),
        )
        connection.execute("UPDATE generation_qc_policy_sets SET current_version_id=? WHERE id=?", (policy_version_id, policy_set_id))
        recipe = {
            "aspect_ratio": "9:16",
            "shot_planning": {"avg_duration_ms": 3000, "dialogue_coverage": "balanced"},
            "asset_policy": {"character_required_refs": ["FRONT"]},
            "generation": {"image": {"capability": "T2I"}, "video": {"capability": "I2V"}},
            "qc_policy_ref": {"policy_version_id": policy_version_id},
        }
        canonical = json.dumps(recipe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        connection.execute(
            """INSERT INTO director_recipes
            (id,project_id,code,title,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,'vertical_drama','Vertical Drama','ACTIVE',?,?,'test',1,'v1')""",
            (recipe_id, project_id, now, now),
        )
        connection.execute(
            """INSERT INTO director_recipe_versions
            (id,recipe_id,version_no,recipe_json,recipe_hash,reason,is_frozen,created_at,created_by,schema_version)
            VALUES (?,?,1,?,?,'baseline',1,?,'test','v1')""",
            (recipe_version_id, recipe_id, canonical, hashlib.sha256(canonical.encode()).hexdigest(), now),
        )
        connection.execute(
            """INSERT INTO project_director_recipe_bindings
            (project_id,recipe_version_id,reason,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,'selected',?,?,'test',1,'v1')""",
            (project_id, recipe_version_id, now, now),
        )

    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    exported = service.export(project_id)
    source = workspace.projects_root / "package_source" / str(exported["rel_path"])
    inbox = workspace.data_root / "imports" / "project-packages" / "inbox"
    inbox.mkdir(parents=True)
    shutil.copy2(source, inbox / "v2-production-policy.ldspkg")
    token = str(service.stage_from_inbox("v2-production-policy.ldspkg")["stage_token"])
    imported = service.import_as_copy(token, code="package_policy_copy", title="Policy copy")

    assert imported["counts"]["shot_groups"] == 1
    assert imported["counts"]["shot_group_members"] == 1
    assert imported["counts"]["generation_qc_policy_sets"] == 1
    assert imported["counts"]["generation_qc_policy_versions"] == 1
    assert imported["counts"]["director_recipes"] == 1
    assert imported["counts"]["director_recipe_versions"] == 1
    assert imported["counts"]["project_director_recipe_bindings"] == 1
    assert imported["counts"]["story_asset_proposals"] == 1
    with database.connect() as connection:
        imported_shot = connection.execute(
            """SELECT sh.scene_id,g.scene_id AS group_scene,m.group_id FROM shots sh
            JOIN shot_group_members m ON m.shot_id=sh.id JOIN shot_groups g ON g.id=m.group_id
            JOIN episodes e ON e.id=sh.episode_id JOIN seasons s ON s.id=e.season_id WHERE s.project_id=?""",
            (imported["project_id"],),
        ).fetchone()
        assert imported_shot is not None and imported_shot["scene_id"] == imported_shot["group_scene"]
        imported_policy = connection.execute(
            """SELECT s.current_version_id,v.id FROM generation_qc_policy_sets s
            JOIN generation_qc_policy_versions v ON v.policy_set_id=s.id WHERE s.project_id=?""",
            (imported["project_id"],),
        ).fetchone()
        imported_recipe = connection.execute(
            """SELECT b.recipe_version_id,v.id,v.recipe_json,v.recipe_hash FROM project_director_recipe_bindings b
            JOIN director_recipe_versions v ON v.id=b.recipe_version_id WHERE b.project_id=?""",
            (imported["project_id"],),
        ).fetchone()
        assert imported_policy["current_version_id"] == imported_policy["id"]
        assert imported_recipe["recipe_version_id"] == imported_recipe["id"]
        rewritten = json.loads(imported_recipe["recipe_json"])
        assert rewritten["qc_policy_ref"]["policy_version_id"] == imported_policy["id"]
        assert (
            imported_recipe["recipe_hash"]
            == hashlib.sha256(json.dumps(rewritten, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        )
        assert imported_policy["id"] != policy_version_id and imported_recipe["id"] != recipe_version_id
        imported_proposal = connection.execute(
            "SELECT * FROM story_asset_proposals WHERE project_id=?",
            (imported["project_id"],),
        ).fetchone()
        assert imported_proposal["status"] == "ACCEPTED_MERGE"
        assert imported_proposal["resolved_asset_id"] == imported_proposal["suggested_asset_id"]
        assert imported_proposal["resolved_asset_id"] != asset_id
        assert connection.execute("SELECT COUNT(*) FROM variant_qc_links WHERE policy_version_id=?", (imported_policy["id"],)).fetchone()[0] == 0


def test_project_package_import_old_v2_defaults_new_domains_to_empty(workspace, database) -> None:
    project = _project(workspace, database)
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    exported = service.export(str(project["id"]))
    source = workspace.projects_root / "package_source" / str(exported["rel_path"])
    inbox = workspace.data_root / "imports" / "project-packages" / "inbox"
    inbox.mkdir(parents=True)
    legacy = inbox / "legacy-v2.ldspkg"
    with zipfile.ZipFile(source) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    state = json.loads(contents["project-state.json"])
    for key in (
        "story_assets",
        "story_asset_proposals",
        "story_asset_states",
        "story_asset_references",
        "episode_asset_state_bindings",
        "shot_asset_bindings",
        "generation_preference_sets",
        "generation_preference_versions",
        "shot_groups",
        "shot_group_members",
        "generation_qc_policy_sets",
        "generation_qc_policy_versions",
        "director_recipes",
        "director_recipe_versions",
        "project_director_recipe_binding",
    ):
        state.pop(key, None)
    for shot in state["shots"]:
        shot.pop("scene_id", None)
    state_bytes = (json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    manifest = json.loads(contents["package-manifest.json"])
    entry = next(item for item in manifest["entries"] if item["path"] == "project-state.json")
    entry.update(byte_size=len(state_bytes), sha256=hashlib.sha256(state_bytes).hexdigest())
    manifest["state_sha256"] = entry["sha256"]
    manifest["expanded_bytes"] = sum(item["byte_size"] for item in manifest["entries"])
    with zipfile.ZipFile(legacy, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in contents.items():
            if name not in {"project-state.json", "package-manifest.json"}:
                archive.writestr(name, content)
        archive.writestr("project-state.json", state_bytes)
        archive.writestr(
            "package-manifest.json",
            (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        )

    token = str(service.stage_from_inbox(legacy.name)["stage_token"])
    imported = service.import_as_copy(token, code="package_legacy_v2", title="Legacy v2")
    assert imported["counts"]["story_assets"] == 0
    assert imported["counts"]["story_asset_proposals"] == 0
    assert imported["counts"]["story_asset_states"] == 0
    assert imported["counts"]["generation_preference_sets"] == 0
    assert imported["counts"]["shot_groups"] == 0
    assert imported["counts"]["generation_qc_policy_sets"] == 0
    assert imported["counts"]["director_recipes"] == 0
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM story_assets WHERE project_id=?", (imported["project_id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM generation_preference_sets WHERE project_id=?", (imported["project_id"],)).fetchone()[0] == 0


def test_project_thumbnail_rebuild_endpoint_retries_registered_image_cache(workspace, database) -> None:
    project = _project(workspace, database)
    _registered_image(workspace, database, str(project["id"]))
    _, legacy_id = _registered_image(workspace, database, str(project["id"]))
    with database.transaction() as connection:
        connection.execute("UPDATE media_versions SET mime_type='application/json' WHERE id=?", (legacy_id,))
    with TestClient(create_app(workspace)) as client:
        response = client.post(f"/api/v1/projects/{project['id']}/media-thumbnails:rebuild")
    assert response.status_code == 200, response.text
    rebuild = response.json()["rebuild"]
    assert rebuild["requested"] == 1
    assert rebuild["created"] == 1
    assert rebuild["failed"] == 0
    assert rebuild["skipped"] == 1
    assert rebuild["exclusions"] == [{"media_version_id": legacy_id, "code": "MEDIA_KIND_MIME_MISMATCH"}]
    assert rebuild["pending"] == 0
    assert rebuild["runtime_contacted"] is False and rebuild["network_contacted"] is False


def test_staged_project_package_import_rolls_back_database_and_filesystem_on_failure(workspace, database) -> None:
    project = _project(workspace, database)
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    exported = service.export(str(project["id"]))
    source = workspace.projects_root / "package_source" / str(exported["rel_path"])
    inbox = workspace.data_root / "imports" / "project-packages" / "inbox"
    inbox.mkdir(parents=True)
    shutil.copy2(source, inbox / "rollback.ldspkg")
    token = str(service.stage_from_inbox("rollback.ldspkg")["stage_token"])

    with pytest.raises(RuntimeError, match="simulated"):
        service.import_as_copy(token, code="package_rollback", title="回滚", simulate_failure=True)

    assert not (workspace.projects_root / "package_rollback").exists()
    assert not list(workspace.projects_root.glob(".package_rollback.import-*"))
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects WHERE code='package_rollback'").fetchone()[0] == 0
        receipt = connection.execute("SELECT target_project_id,status FROM project_package_imports WHERE target_code='package_rollback'").fetchone()
    assert receipt["status"] == "FAILED"
    assert (workspace.data_root / "imports" / "project-packages" / "staged" / f"{token}.ldspkg").is_file()

    retried = service.import_as_copy(token, code="package_rollback", title="回滚后重试")
    assert retried["project_id"] == receipt["target_project_id"] and retried["reused"] is False


def test_rebind_existing_only_restores_a_missing_matching_project_root(workspace, database) -> None:
    project = _project(workspace, database)
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    exported = service.export(str(project["id"]))
    source = workspace.projects_root / "package_source" / str(exported["rel_path"])
    inbox = workspace.data_root / "imports" / "project-packages" / "inbox"
    inbox.mkdir(parents=True)
    shutil.copy2(source, inbox / "rebind.ldspkg")
    token = str(service.stage_from_inbox("rebind.ldspkg")["stage_token"])

    with pytest.raises(DomainRuleError) as overwrite:
        service.rebind_existing(token)
    assert overwrite.value.code == "PROJECT_PACKAGE_REBIND_ROOT_EXISTS"

    shutil.rmtree(workspace.projects_root / "package_source")
    rebound = service.rebind_existing(token)
    assert rebound["status"] == "REBOUND" and rebound["database_structure_changed"] is False
    root = workspace.projects_root / "package_source"
    assert (root / "01_story" / "source_documents" / "中文 剧本.md").is_file()
    assert json.loads((root / "project.json").read_text(encoding="utf-8"))["project_id"] == project["id"]


def test_project_package_commit_api_requires_explicit_identity_decision(workspace, database) -> None:
    project = _project(workspace, database)
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    exported = service.export(str(project["id"]))
    source = workspace.projects_root / "package_source" / str(exported["rel_path"])
    inbox = workspace.data_root / "imports" / "project-packages" / "inbox"
    inbox.mkdir(parents=True)
    shutil.copy2(source, inbox / "commit.ldspkg")
    token = str(service.stage_from_inbox("commit.ldspkg")["stage_token"])
    with TestClient(create_app(workspace)) as client:
        missing_identity = client.post(
            f"/api/v1/project-packages/{token}:commit",
            json={"identity_mode": "IMPORT_AS_COPY_REWRITE_IDENTITY"},
        )
        committed = client.post(
            f"/api/v1/project-packages/{token}:commit",
            json={"identity_mode": "IMPORT_AS_COPY_REWRITE_IDENTITY", "code": "api_package_copy", "title": "API 项目包副本"},
        )
    assert missing_identity.status_code == 422
    assert missing_identity.json()["error"]["code"] == "PROJECT_PACKAGE_COPY_IDENTITY_REQUIRED"
    assert committed.status_code == 201
    assert committed.json()["commit"]["project_code"] == "api_package_copy"


def test_stale_import_journal_recovers_only_its_owned_orphan_root(workspace, database) -> None:
    project = _project(workspace, database)
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    exported = service.export(str(project["id"]))
    source = workspace.projects_root / "package_source" / str(exported["rel_path"])
    inbox = workspace.data_root / "imports" / "project-packages" / "inbox"
    inbox.mkdir(parents=True)
    shutil.copy2(source, inbox / "crash.ldspkg")
    token = str(service.stage_from_inbox("crash.ldspkg")["stage_token"])
    mode, code = "IMPORT_AS_COPY_REWRITE_IDENTITY", "crash_recovery"
    operation_key = hashlib.sha256(f"{token}\0{mode}\0{code}".encode()).hexdigest()
    target_project_id = str(uuid.uuid4())
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO project_package_imports (operation_key,stage_token,identity_mode,source_project_id,
            target_project_id,target_code,status,result_json,created_at,updated_at,created_by)
            VALUES (?,?,?,?,?,?,'PREPARING','{}','2000-01-01T00:00:00Z','2000-01-01T00:00:00Z','test')""",
            (operation_key, token, mode, project["id"], target_project_id, code),
        )
    orphan = workspace.projects_root / code
    orphan.mkdir()
    (orphan / "project.json").write_text(
        json.dumps(
            {
                "project_id": target_project_id,
                "project_code": code,
                "imported_from_package_sha256": token,
            }
        ),
        encoding="utf-8",
    )
    (orphan / "orphan.tmp").write_text("partial", encoding="utf-8")

    recovered = service.import_as_copy(token, code=code, title="崩溃恢复")

    assert recovered["project_id"] == target_project_id
    assert not (orphan / "orphan.tmp").exists()
    assert (orphan / "01_story" / "source_documents" / "中文 剧本.md").is_file()
