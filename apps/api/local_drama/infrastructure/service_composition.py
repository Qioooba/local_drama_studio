"""Infrastructure composition for creative-production application services.

All concrete adapters are wired here so orchestration classes depend only on
application ports and remain independently testable.
"""

from __future__ import annotations

import sqlite3

from local_drama.application.asset_image_generation import (
    AssetImageGenerationBatchService,
    AssetImageGenerationCompletionService,
)
from local_drama.application.commands.asset_bible import AssetBibleCommandService
from local_drama.application.documents import DocumentImportService
from local_drama.application.generation import GenerationService
from local_drama.application.jobs import JobService
from local_drama.application.local_llm import LocalLLMService
from local_drama.application.media import MediaService
from local_drama.application.pipeline_orchestrator import PipelineOrchestratorService
from local_drama.application.queries.generation_preferences import GenerationPreferenceQueryService
from local_drama.application.scene_prop_asset_generation import ScenePropAssetGenerationService
from local_drama.application.shot_keyframe_generation import (
    ShotKeyframeGenerationBatchService,
    ShotKeyframeGenerationCompletionService,
)
from local_drama.application.story_pipeline_ai import FullStoryAIGenerationService
from local_drama.config import Settings
from local_drama.infrastructure.database.asset_bible_repository import SqliteAssetBibleRepository
from local_drama.infrastructure.database.generation_preference_repository import SqliteGenerationPreferenceRepository
from local_drama.infrastructure.database.sqlite import Database


def _preference_resolver(connection: sqlite3.Connection) -> GenerationPreferenceQueryService:
    return GenerationPreferenceQueryService(SqliteGenerationPreferenceRepository(connection))


def _asset_commands(connection: sqlite3.Connection) -> AssetBibleCommandService:
    return AssetBibleCommandService(SqliteAssetBibleRepository(connection))


def build_asset_image_batch(database: Database, settings: Settings) -> AssetImageGenerationBatchService:
    return AssetImageGenerationBatchService(
        database,
        settings,
        generation=GenerationService(database, settings),
        preference_resolver_factory=_preference_resolver,
    )


def build_asset_image_completion(database: Database, settings: Settings) -> AssetImageGenerationCompletionService:
    return AssetImageGenerationCompletionService(
        database,
        settings,
        media=MediaService(database, settings),
        asset_commands=_asset_commands,
    )


def build_shot_keyframe_batch(database: Database, settings: Settings) -> ShotKeyframeGenerationBatchService:
    return ShotKeyframeGenerationBatchService(
        database,
        settings,
        generation=GenerationService(database, settings),
        preference_resolver_factory=_preference_resolver,
    )


def build_shot_keyframe_completion(database: Database, settings: Settings) -> ShotKeyframeGenerationCompletionService:
    return ShotKeyframeGenerationCompletionService(database, settings, media=MediaService(database, settings))


def build_story_ai(database: Database, settings: Settings) -> FullStoryAIGenerationService:
    return FullStoryAIGenerationService(database, settings, llm=LocalLLMService(database, settings))


def build_pipeline_orchestrator(database: Database, settings: Settings) -> PipelineOrchestratorService:
    return PipelineOrchestratorService(
        database,
        settings,
        jobs=JobService(database, settings),
        documents=DocumentImportService(database, settings),
        ai_generation=build_story_ai(database, settings),
    )


def build_scene_prop_generation(database: Database, settings: Settings) -> ScenePropAssetGenerationService:
    return ScenePropAssetGenerationService(database, settings, llm=LocalLLMService(database, settings))
