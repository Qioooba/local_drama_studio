"""Zero-shot voice cloning: bind an uploaded reference audio to a character.

The reference stays a project media version; the voice reference stores the
stable ``voxcpm2:media:<id>|<transcript>`` contract so the TTS worker resolves
the file through integrity-checked content access instead of a raw path.
"""

from __future__ import annotations

from typing import Any

from local_drama.application.dialogue import DialogueService
from local_drama.application.media import MediaService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database

MIN_REFERENCE_MS = 2_000
MAX_REFERENCE_MS = 15_000


class VoiceCloneService:
    def __init__(self, database: Database, settings: Any) -> None:
        self.database = database
        self.settings = settings

    def _dialogue(self) -> DialogueService:
        return DialogueService(self.database, self.settings)

    def _character(self, project_id: str, asset_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id, code, name, kind, status, project_id FROM story_assets WHERE id=?",
                (asset_id,),
            ).fetchone()
        if row is None or str(row["project_id"]) != project_id:
            raise DomainRuleError("STORY_ASSET_NOT_FOUND", "角色资产不存在", {"asset_id": asset_id})
        if str(row["kind"]) != "CHARACTER" or str(row["status"]) != "ACTIVE":
            raise DomainRuleError("STORY_ASSET_KIND_INVALID", "声线克隆只接受 ACTIVE 状态的 CHARACTER 资产")
        return dict(row)

    def _reference_audio(self, project_id: str, media_version_id: str) -> dict[str, Any]:
        media = MediaService(self.database, self.settings)
        version = media.get_version(media_version_id)
        if (
            str(version["project_id"]) != project_id
            or str(version["media_kind"]).upper() != "AUDIO"
            or str(version["integrity_status"]).upper() != "VERIFIED"
        ):
            raise DomainRuleError("VOICE_CLONE_REFERENCE_INVALID", "克隆参考必须是同项目已验证的音频 MediaVersion")
        duration_ms = int(version.get("duration_ms") or 0)
        if not MIN_REFERENCE_MS <= duration_ms <= MAX_REFERENCE_MS:
            raise DomainRuleError(
                "VOICE_CLONE_REFERENCE_DURATION_INVALID",
                f"克隆参考音频时长需在 {MIN_REFERENCE_MS // 1000}—{MAX_REFERENCE_MS // 1000} 秒之间",
                {"duration_ms": duration_ms},
            )
        return dict(version)

    def _latest_published_tts_profile(self, project_id: str) -> str | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT epv.id FROM project_profile_bindings ppb
                JOIN execution_profile_versions epv ON epv.id=ppb.execution_profile_version_id
                WHERE ppb.project_id=? AND ppb.status='ACTIVE' AND upper(epv.capability) LIKE '%TTS%'
                AND epv.status='PUBLISHED'
                ORDER BY epv.version_no DESC LIMIT 1""",
                (project_id,),
            ).fetchone()
        return str(row["id"]) if row else None

    def clone_character_voice(
        self,
        project_id: str,
        asset_id: str,
        *,
        media_version_id: str,
        title: str,
        transcript: str,
        consent: bool,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        character = self._character(project_id, asset_id)
        if not consent:
            raise DomainRuleError("VOICE_CLONE_CONSENT_REQUIRED", "必须确认拥有该声音的克隆授权")
        reference = self._reference_audio(project_id, media_version_id)
        transcript = transcript.strip()
        voice_ref = f"voxcpm2:media:{media_version_id}" + (f"|{transcript}" if transcript else "")
        dialogue = self._dialogue()
        provider_profile_version_id = self._latest_published_tts_profile(project_id)
        voice = dialogue.create_voice_profile(
            project_id,
            code=f"VOICE-{str(character['code'])[:24]}",
            title=title.strip() or f"{character['name']}克隆声线",
            voice_ref=voice_ref,
            license_status="USER_OWNED",
            license_evidence_path_rel=str(reference["rel_path"]),
            provider_profile_version_id=provider_profile_version_id,
            actor=actor,
        )
        with self.database.connect() as connection:
            existing_binding = connection.execute(
                "SELECT id FROM character_voice_bindings WHERE character_asset_id=?",
                (str(character["id"]),),
            ).fetchone()
        if existing_binding is not None:
            dialogue.unbind_character_voice(str(existing_binding["id"]), actor=actor)
        binding = dialogue.bind_character_voice(project_id, str(character["id"]), str(voice["id"]), actor=actor)
        return {"voice": voice, "binding": binding, "reference_media_version_id": media_version_id}
