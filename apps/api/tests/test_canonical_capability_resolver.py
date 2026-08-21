"""Tests for PR-CUR-003: Canonical capability authority, legacy aliases, and unified resolver."""

from __future__ import annotations

import sqlite3

import pytest

from local_drama.application.commands.generation_preferences import GenerationPreferenceCommandService
from local_drama.application.queries.generation_preferences import (
    GenerationPreferenceQueryService,
    compute_resolution_fingerprint,
)
from local_drama.domain.capabilities import (
    CANONICAL_CAPABILITIES,
    normalize_capability,
)
from local_drama.infrastructure.database.generation_preference_repository import (
    SqliteGenerationPreferenceRepository,
)


@pytest.fixture
def memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE projects (id TEXT PRIMARY KEY, code TEXT, title TEXT);
        CREATE TABLE seasons (id TEXT PRIMARY KEY, project_id TEXT);
        CREATE TABLE episodes (id TEXT PRIMARY KEY, season_id TEXT);
        CREATE TABLE shots (id TEXT PRIMARY KEY, episode_id TEXT);

        CREATE TABLE execution_profiles (id TEXT PRIMARY KEY, code TEXT, title TEXT, capability TEXT);
        CREATE TABLE execution_profile_versions (
            id TEXT PRIMARY KEY,
            execution_profile_id TEXT,
            version_no INTEGER,
            capability TEXT,
            status TEXT,
            capability_json TEXT,
            updated_at TEXT
        );

        CREATE TABLE generation_preference_sets (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            owner_type TEXT,
            owner_id TEXT,
            capability TEXT,
            current_version_id TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT,
            created_by TEXT,
            revision INTEGER,
            schema_version TEXT
        );

        CREATE TABLE generation_preference_versions (
            id TEXT PRIMARY KEY,
            preference_set_id TEXT,
            version_no INTEGER,
            execution_profile_version_id TEXT,
            resolution_mode TEXT,
            settings_json TEXT,
            reason TEXT,
            is_frozen INTEGER,
            created_at TEXT,
            created_by TEXT,
            schema_version TEXT
        );

        CREATE TABLE audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor TEXT,
            role_context TEXT,
            action TEXT,
            subject_type TEXT,
            subject_id TEXT,
            before_revision INTEGER,
            after_revision INTEGER,
            summary TEXT,
            metadata_redacted_json TEXT
        );
        """
    )
    return conn


def test_canonical_capabilities_inventory_and_normalization() -> None:
    # 1. Check canonical set
    assert "LLM_STORY_PARSE" in CANONICAL_CAPABILITIES
    assert "VIDEO_I2V" in CANONICAL_CAPABILITIES
    assert "VIDEO_T2V" in CANONICAL_CAPABILITIES
    assert "TTS" in CANONICAL_CAPABILITIES
    assert "VOICE_CLONE" in CANONICAL_CAPABILITIES
    assert "LIPSYNC" in CANONICAL_CAPABILITIES

    # 2. Check normalization of legacy aliases
    assert normalize_capability("SCRIPT_BREAKDOWN_LLM") == "LLM_STORY_PARSE"
    assert normalize_capability("story_parse") == "LLM_STORY_PARSE"
    assert normalize_capability("i2v") == "VIDEO_I2V"
    assert normalize_capability("IMAGE_TO_VIDEO") == "VIDEO_I2V"
    assert normalize_capability("T2V") == "VIDEO_T2V"
    assert normalize_capability("audio_tts") == "TTS"
    assert normalize_capability("audio_clone") == "VOICE_CLONE"
    assert normalize_capability("lip_sync") == "LIPSYNC"
    assert normalize_capability("motion_brush") == "VIDEO_MOTION_CONTROL"
    assert normalize_capability("r2v") == "VIDEO_REFERENCE"
    assert normalize_capability("v2v") == "VIDEO_REFERENCE"

    # 3. Fail closed on unknown or ambiguous
    with pytest.raises(ValueError):
        normalize_capability("IMAGE_GENERATION")
    with pytest.raises(ValueError):
        normalize_capability("AMBIGUOUS_UNKNOWN_XYZ")
    with pytest.raises(ValueError):
        normalize_capability("UNKNOWN_MODEL_ABC")


def test_shot_explicit_compatible_resolution(memory_db: sqlite3.Connection) -> None:
    repo = SqliteGenerationPreferenceRepository(memory_db)
    cmd_service = GenerationPreferenceCommandService(repo)
    query_service = GenerationPreferenceQueryService(repo)

    # Seed Project, Season, Episode, Shot
    memory_db.execute("INSERT INTO projects VALUES ('proj-1', 'P1', '测试项目')")
    memory_db.execute("INSERT INTO seasons VALUES ('season-1', 'proj-1')")
    memory_db.execute("INSERT INTO episodes VALUES ('ep-1', 'season-1')")
    memory_db.execute("INSERT INTO shots VALUES ('shot-1', 'ep-1')")

    # Seed Execution Profile and Version
    memory_db.execute("INSERT INTO execution_profiles VALUES ('prof-i2v', 'I2V_PRO', 'I2V Profile', 'VIDEO_I2V')")
    memory_db.execute(
        "INSERT INTO execution_profile_versions VALUES ('pv-1', 'prof-i2v', 1, 'VIDEO_I2V', 'PUBLISHED', '{}', '2026-08-21T00:00:00Z')"
    )

    # Put EXPLICIT preference on SHOT
    cmd_service.put(
        project_id="proj-1",
        owner_type="SHOT",
        owner_id="shot-1",
        capability="i2v",
        resolution_mode="EXPLICIT",
        execution_profile_version_id="pv-1",
    )

    # Resolve for shot
    result = query_service.resolve(project_id="proj-1", capability="VIDEO_I2V", shot_id="shot-1")
    assert result["capability"] == "VIDEO_I2V"
    assert result["profile_version_id"] == "pv-1"
    assert result["source"] == "SHOT"
    assert result["native_support"] is True
    assert result["blocked_reason"] is None
    assert result["profile"]["code"] == "I2V_PRO"
    assert result["resolution_fingerprint"] is not None


def test_shot_explicit_incompatible_fails_closed(memory_db: sqlite3.Connection) -> None:
    repo = SqliteGenerationPreferenceRepository(memory_db)
    query_service = GenerationPreferenceQueryService(repo)

    memory_db.execute("INSERT INTO projects VALUES ('proj-1', 'P1', '测试项目')")
    memory_db.execute("INSERT INTO seasons VALUES ('season-1', 'proj-1')")
    memory_db.execute("INSERT INTO episodes VALUES ('ep-1', 'season-1')")
    memory_db.execute("INSERT INTO shots VALUES ('shot-1', 'ep-1')")

    # Seed draft (unpublished) profile
    memory_db.execute("INSERT INTO execution_profiles VALUES ('prof-i2v', 'I2V_DRAFT', 'I2V Draft', 'VIDEO_I2V')")
    memory_db.execute(
        "INSERT INTO execution_profile_versions VALUES ('pv-draft', 'prof-i2v', 1, 'VIDEO_I2V', 'DRAFT', '{}', '2026-08-21T00:00:00Z')"
    )

    # Also seed valid auto profile at PROJECT level to verify it does NOT silently fall back
    memory_db.execute("INSERT INTO execution_profiles VALUES ('prof-auto', 'I2V_AUTO', 'I2V Auto', 'VIDEO_I2V')")
    memory_db.execute(
        "INSERT INTO execution_profile_versions VALUES ('pv-auto', 'prof-auto', 1, 'VIDEO_I2V', 'PUBLISHED', '{}', '2026-08-21T00:00:00Z')"
    )

    # Manually insert explicit draft preference in DB to simulate unavailable profile
    memory_db.execute(
        "INSERT INTO generation_preference_sets VALUES ('set-1', 'proj-1', 'SHOT', 'shot-1', 'VIDEO_I2V', 'ver-1', 'ACTIVE', 'now', 'now', 'tester', 1, 'v1')"
    )
    memory_db.execute(
        "INSERT INTO generation_preference_versions VALUES ('ver-1', 'set-1', 1, 'pv-draft', 'EXPLICIT', '{}', '', 1, 'now', 'tester', 'v1')"
    )

    result = query_service.resolve(project_id="proj-1", capability="VIDEO_I2V", shot_id="shot-1")
    # Must fail closed with PROFILE_UNAVAILABLE and not fallback to pv-auto!
    assert result["blocked_reason"] == "PROFILE_UNAVAILABLE"
    assert result["profile_version_id"] is None
    assert result["source"] == "SHOT"
    assert result["native_support"] is False


def test_hierarchy_inheritance_shot_episode_project_auto(memory_db: sqlite3.Connection) -> None:
    repo = SqliteGenerationPreferenceRepository(memory_db)
    cmd_service = GenerationPreferenceCommandService(repo)
    query_service = GenerationPreferenceQueryService(repo)

    memory_db.execute("INSERT INTO projects VALUES ('proj-1', 'P1', '测试项目')")
    memory_db.execute("INSERT INTO seasons VALUES ('season-1', 'proj-1')")
    memory_db.execute("INSERT INTO episodes VALUES ('ep-1', 'season-1')")
    memory_db.execute("INSERT INTO shots VALUES ('shot-1', 'ep-1')")

    memory_db.execute("INSERT INTO execution_profiles VALUES ('prof-proj', 'PROJ_TTS', 'Proj TTS', 'TTS')")
    memory_db.execute(
        "INSERT INTO execution_profile_versions VALUES ('pv-proj', 'prof-proj', 1, 'TTS', 'PUBLISHED', '{}', '2026-08-21T00:00:00Z')"
    )

    # Set Project level preference
    cmd_service.put(
        project_id="proj-1",
        owner_type="PROJECT",
        owner_id="proj-1",
        capability="TTS",
        resolution_mode="EXPLICIT",
        execution_profile_version_id="pv-proj",
    )

    # Resolve from shot-1: inherits from PROJECT
    result = query_service.resolve(project_id="proj-1", capability="TTS", shot_id="shot-1")
    assert result["source"] == "PROJECT"
    assert result["profile_version_id"] == "pv-proj"

    # Now override at EPISODE level
    memory_db.execute("INSERT INTO execution_profiles VALUES ('prof-ep', 'EP_TTS', 'Ep TTS', 'TTS')")
    memory_db.execute(
        "INSERT INTO execution_profile_versions VALUES ('pv-ep', 'prof-ep', 1, 'TTS', 'PUBLISHED', '{}', '2026-08-21T00:00:00Z')"
    )
    cmd_service.put(
        project_id="proj-1",
        owner_type="EPISODE",
        owner_id="ep-1",
        capability="TTS",
        resolution_mode="EXPLICIT",
        execution_profile_version_id="pv-ep",
    )

    # Resolve from shot-1: now resolves from EPISODE
    result_ep = query_service.resolve(project_id="proj-1", capability="TTS", shot_id="shot-1")
    assert result_ep["source"] == "EPISODE"
    assert result_ep["profile_version_id"] == "pv-ep"


def test_requirements_mismatch_blocks_without_silent_downgrade(memory_db: sqlite3.Connection) -> None:
    repo = SqliteGenerationPreferenceRepository(memory_db)
    query_service = GenerationPreferenceQueryService(repo)

    memory_db.execute("INSERT INTO projects VALUES ('proj-1', 'P1', '测试项目')")
    memory_db.execute("INSERT INTO execution_profiles VALUES ('prof-mc', 'MC_PROF', 'Motion Profile', 'VIDEO_I2V')")
    memory_db.execute(
        "INSERT INTO execution_profile_versions VALUES ('pv-mc', 'prof-mc', 1, 'VIDEO_I2V', 'PUBLISHED', '{}', '2026-08-21T00:00:00Z')"
    )

    # Request requirements: requires_motion_control=True (which requires VIDEO_MOTION_CONTROL capability)
    result = query_service.resolve(
        project_id="proj-1",
        capability="VIDEO_I2V",
        requirements={"requires_motion_control": True},
    )
    # Since profile is VIDEO_I2V without motion control, it must be blocked
    assert result["blocked_reason"] == "NO_COMPATIBLE_PROFILE"
    assert result["profile_version_id"] is None


def test_first_last_frame_requirement_rejects_plain_i2v(memory_db: sqlite3.Connection) -> None:
    repo = SqliteGenerationPreferenceRepository(memory_db)
    query_service = GenerationPreferenceQueryService(repo)

    memory_db.execute("INSERT INTO projects VALUES ('proj-1', 'P1', '测试项目')")
    memory_db.execute("INSERT INTO execution_profiles VALUES ('prof-i2v', 'I2V_ONLY', 'Plain I2V', 'VIDEO_I2V')")
    memory_db.execute(
        "INSERT INTO execution_profile_versions VALUES ('pv-i2v', 'prof-i2v', 1, 'VIDEO_I2V', 'PUBLISHED', '{}', '2026-08-21T00:00:00Z')"
    )

    result = query_service.resolve(
        project_id="proj-1",
        capability="VIDEO_I2V",
        requirements={"requires_first_last_frame": True},
    )

    assert result["blocked_reason"] == "NO_COMPATIBLE_PROFILE"
    assert result["profile_version_id"] is None


def test_resolution_fingerprint_determinism() -> None:
    fp1 = compute_resolution_fingerprint("VIDEO_I2V", "pv-100", 2, "SHOT", {"seed": 42})
    fp2 = compute_resolution_fingerprint("VIDEO_I2V", "pv-100", 2, "SHOT", {"seed": 42})
    fp3 = compute_resolution_fingerprint("VIDEO_I2V", "pv-100", 2, "EPISODE", {"seed": 42})
    fp4 = compute_resolution_fingerprint("VIDEO_I2V", "pv-100", 3, "SHOT", {"seed": 42})

    assert fp1 == fp2
    assert fp1 != fp3
    assert fp1 != fp4
