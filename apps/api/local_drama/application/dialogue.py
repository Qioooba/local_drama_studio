"""Immutable dialogue text, voice authorization and TTS candidate governance."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from local_drama.application.ports.dialogue import DialogueJobPort, DialogueMediaPort, DialogueUnitOfWork
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.filesystem.path_policy import controlled_path
from local_drama.platform import create_platform_services
from local_drama.platform.contracts import TtsRuntime, TtsRuntimeError


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class DialogueService:
    def __init__(
        self,
        database: DialogueUnitOfWork,
        settings: Settings,
        tts_runtime: TtsRuntime | None = None,
        *,
        jobs: DialogueJobPort | None = None,
        media: DialogueMediaPort | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.media = media
        self.tts_runtime = tts_runtime or create_platform_services(settings).tts_runtime
        self.jobs = jobs

    def _media(self) -> DialogueMediaPort:
        if self.media is None:
            raise DomainRuleError("DIALOGUE_MEDIA_PORT_REQUIRED", "对白媒体端口未配置")
        return self.media

    @staticmethod
    def _command_replay(
        connection: Any,
        scope: str,
        idempotency_key: str,
        payload: dict[str, Any],
        *,
        mismatch_code: str,
    ) -> dict[str, Any] | None:
        key = idempotency_key.strip()
        if not key or len(key) > 200:
            raise DomainRuleError("DIALOGUE_IDEMPOTENCY_KEY_INVALID", "对白命令 idempotency_key 必须为 1—200 字符")
        payload_hash = hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
        row = connection.execute(
            "SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?",
            (scope, key),
        ).fetchone()
        if row is None:
            return None
        if str(row["payload_hash"]) != payload_hash:
            raise DomainRuleError(mismatch_code, "相同 idempotency_key 的对白请求内容不一致")
        result = cast(dict[str, Any], json.loads(str(row["response_json"])))
        result["idempotent_replay"] = True
        return result

    @staticmethod
    def _store_command(
        connection: Any,
        scope: str,
        idempotency_key: str,
        payload: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        connection.execute(
            "INSERT INTO command_idempotencies (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)",
            (
                scope,
                idempotency_key.strip(),
                hashlib.sha256(_json(payload).encode("utf-8")).hexdigest(),
                _json(response),
            ),
        )

    def save_shot_dialogue_draft(
        self,
        shot_id: str,
        *,
        line_id: str | None,
        code: str,
        speaker: str,
        text: str,
        pronunciation: dict[str, Any],
        expected_shot_revision: int,
        expected_text_revision_no: int | None,
        idempotency_key: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        code, speaker, text = code.strip(), speaker.strip(), text.strip()
        if not code or not speaker or not text:
            raise DomainRuleError("DIALOGUE_FIELDS_REQUIRED", "对白 code、说话人和文本均为必填")
        payload = {
            "line_id": line_id,
            "code": code,
            "speaker": speaker,
            "text": text,
            "pronunciation": pronunciation,
            "expected_shot_revision": expected_shot_revision,
            "expected_text_revision_no": expected_text_revision_no,
        }
        scope = f"shot-dialogue-draft:{shot_id}"
        now = _now()
        try:
            with self.database.transaction() as connection:
                replay = self._command_replay(
                    connection,
                    scope,
                    idempotency_key,
                    payload,
                    mismatch_code="DIALOGUE_IDEMPOTENCY_MISMATCH",
                )
                if replay is not None:
                    return replay
                shot = connection.execute(
                    """SELECT s.id,s.episode_id,s.revision,se.project_id FROM shots s
                    JOIN episodes e ON e.id=s.episode_id JOIN seasons se ON se.id=e.season_id
                    WHERE s.id=? AND s.archived_at IS NULL""",
                    (shot_id,),
                ).fetchone()
                if shot is None:
                    raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在")
                if int(shot["revision"]) != expected_shot_revision:
                    raise DomainRuleError(
                        "SHOT_REVISION_CONFLICT",
                        "镜头已变化，请刷新后再保存对白",
                        {"expected_revision": expected_shot_revision, "actual_revision": int(shot["revision"])},
                    )
                if line_id is None:
                    next_line_id = str(uuid.uuid4())
                    revision_id = str(uuid.uuid4())
                    connection.execute(
                        """INSERT INTO dialogue_lines
                        (id,episode_id,shot_id,code,speaker,created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,?,?,?,?,?,?,1,'v2')""",
                        (next_line_id, shot["episode_id"], shot_id, code, speaker, now, now, actor),
                    )
                    next_revision_no = 1
                    line_revision = 1
                    action = "DIALOGUE_LINE_CREATED"
                else:
                    line = connection.execute(
                        "SELECT * FROM dialogue_lines WHERE id=? AND shot_id=?",
                        (line_id, shot_id),
                    ).fetchone()
                    latest = connection.execute(
                        "SELECT revision_no FROM dialogue_text_revisions WHERE dialogue_line_id=? ORDER BY revision_no DESC LIMIT 1",
                        (line_id,),
                    ).fetchone()
                    if line is None or latest is None:
                        raise DomainRuleError("DIALOGUE_LINE_NOT_FOUND", "对白不存在或不属于当前镜头")
                    if expected_text_revision_no is None or int(latest["revision_no"]) != expected_text_revision_no:
                        raise DomainRuleError(
                            "DIALOGUE_TEXT_REVISION_CONFLICT",
                            "对白文本 revision 已变化，请刷新后重试",
                            {"expected_revision": expected_text_revision_no, "actual_revision": int(latest["revision_no"])},
                        )
                    next_line_id = line_id
                    revision_id = str(uuid.uuid4())
                    next_revision_no = expected_text_revision_no + 1
                    line_revision = int(line["revision"]) + 1
                    connection.execute(
                        "UPDATE dialogue_lines SET code=?,speaker=?,updated_at=?,revision=revision+1 WHERE id=?",
                        (code, speaker, now, line_id),
                    )
                    action = "DIALOGUE_VERSION_CHANGED"
                text_hash = hashlib.sha256(_json({"text": text, "pronunciation": pronunciation}).encode("utf-8")).hexdigest()
                connection.execute(
                    """INSERT INTO dialogue_text_revisions
                    (id,dialogue_line_id,revision_no,text,pronunciation_json,text_hash,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?,?,?,?,?,?,?,1,'v2')""",
                    (revision_id, next_line_id, next_revision_no, text, _json(pronunciation), text_hash, now, now, actor),
                )
                result = {
                    "shot_id": shot_id,
                    "line_id": next_line_id,
                    "code": code,
                    "speaker": speaker,
                    "line_revision": line_revision,
                    "text_revision": {
                        "id": revision_id,
                        "revision_no": next_revision_no,
                        "text": text,
                        "pronunciation": pronunciation,
                        "text_hash": text_hash,
                        "created_at": now,
                    },
                    "idempotent_replay": False,
                }
                connection.execute(
                    """INSERT INTO audit_events
                    (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                    VALUES (?,'producer',?,'DIALOGUE_LINE',?,'在 Shot Studio 保存对白版本',?)""",
                    (actor, action, next_line_id, _json({"shot_id": shot_id, "text_revision_id": revision_id, "text_hash": text_hash})),
                )
                connection.execute(
                    """INSERT INTO outbox_events (type,project_id,subject_type,subject_id,payload_json)
                    VALUES ('DialogueVersionChanged',?,'DIALOGUE_LINE',?,?)""",
                    (shot["project_id"], next_line_id, _json({"shot_id": shot_id, "episode_id": shot["episode_id"], "text_revision_id": revision_id, "revision_no": next_revision_no})),
                )
                self._store_command(connection, scope, idempotency_key, payload, result)
                return result
        except Exception as error:
            if "UNIQUE constraint failed: dialogue_lines.episode_id, dialogue_lines.code" in str(error):
                raise DomainRuleError("DIALOGUE_CODE_EXISTS", "当前集对白 code 已存在") from error
            raise

    def create_line(
        self,
        episode_id: str,
        *,
        code: str,
        speaker: str,
        text: str,
        pronunciation: dict[str, Any],
        shot_id: str | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        code, speaker, text = code.strip(), speaker.strip(), text.strip()
        if not code or not speaker or not text:
            raise DomainRuleError("DIALOGUE_FIELDS_REQUIRED", "对白 code、说话人和文本均为必填")
        with self.database.connect() as connection:
            episode = connection.execute(
                "SELECT e.id,s.project_id FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE e.id=?", (episode_id,)
            ).fetchone()
            shot = connection.execute("SELECT episode_id FROM shots WHERE id=?", (shot_id,)).fetchone() if shot_id else None
        if episode is None:
            raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在")
        if shot_id and (shot is None or str(shot["episode_id"]) != episode_id):
            raise DomainRuleError("DIALOGUE_SHOT_MISMATCH", "对白镜头必须属于当前集")
        line_id, revision_id, now = str(uuid.uuid4()), str(uuid.uuid4()), _now()
        text_hash = hashlib.sha256(_json({"text": text, "pronunciation": pronunciation}).encode("utf-8")).hexdigest()
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """INSERT INTO dialogue_lines
                    (id,episode_id,shot_id,code,speaker,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?,?,?,?,?,?,1,'v2')""",
                    (line_id, episode_id, shot_id, code, speaker, now, now, actor),
                )
                connection.execute(
                    """INSERT INTO dialogue_text_revisions
                    (id,dialogue_line_id,revision_no,text,pronunciation_json,text_hash,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,1,?,?,?,?,?,?,1,'v2')""",
                    (revision_id, line_id, text, _json(pronunciation), text_hash, now, now, actor),
                )
                connection.execute(
                    """INSERT INTO audit_events
                    (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                    VALUES (?,'audio_editor','DIALOGUE_LINE_CREATED','dialogue_line',?,'创建对白与不可变文本 revision',?)""",
                    (actor, line_id, _json({"text_revision_id": revision_id, "text_hash": text_hash})),
                )
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                raise DomainRuleError("DIALOGUE_CODE_EXISTS", "当前集对白 code 已存在") from error
            raise
        return self.get_line(line_id)

    def revise_text(
        self,
        line_id: str,
        *,
        expected_revision_no: int,
        text: str,
        pronunciation: dict[str, Any],
        actor: str = "local-user",
    ) -> dict[str, Any]:
        text = text.strip()
        if not text:
            raise DomainRuleError("DIALOGUE_TEXT_REQUIRED", "对白文本不能为空")
        now, revision_id = _now(), str(uuid.uuid4())
        with self.database.transaction() as connection:
            line = connection.execute("SELECT id FROM dialogue_lines WHERE id=?", (line_id,)).fetchone()
            latest = connection.execute(
                "SELECT revision_no FROM dialogue_text_revisions WHERE dialogue_line_id=? ORDER BY revision_no DESC LIMIT 1", (line_id,)
            ).fetchone()
            if line is None or latest is None:
                raise DomainRuleError("DIALOGUE_LINE_NOT_FOUND", "对白不存在")
            if int(latest["revision_no"]) != expected_revision_no:
                raise DomainRuleError("DIALOGUE_TEXT_REVISION_CONFLICT", "对白文本 revision 已变化，请刷新后重试")
            next_no = expected_revision_no + 1
            text_hash = hashlib.sha256(_json({"text": text, "pronunciation": pronunciation}).encode("utf-8")).hexdigest()
            connection.execute(
                """INSERT INTO dialogue_text_revisions
                (id,dialogue_line_id,revision_no,text,pronunciation_json,text_hash,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?,?,?,1,'v2')""",
                (revision_id, line_id, next_no, text, _json(pronunciation), text_hash, now, now, actor),
            )
            connection.execute("UPDATE dialogue_lines SET updated_at=?,revision=revision+1 WHERE id=?", (now, line_id))
        return self.get_line(line_id)

    def submit_line_tts_generation(
        self,
        line_id: str,
        *,
        expected_text_revision_no: int,
        voice_profile_version_id: str,
        emotion: str,
        speech_rate: float,
        idempotency_key: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            revision = connection.execute(
                """SELECT dtr.id,dtr.revision_no,dl.shot_id FROM dialogue_text_revisions dtr
                JOIN dialogue_lines dl ON dl.id=dtr.dialogue_line_id
                WHERE dl.id=? ORDER BY dtr.revision_no DESC LIMIT 1""",
                (line_id,),
            ).fetchone()
        if revision is None:
            raise DomainRuleError("DIALOGUE_LINE_NOT_FOUND", "对白不存在")
        if revision["shot_id"] is None:
            raise DomainRuleError("DIALOGUE_SHOT_REQUIRED", "Shot Studio TTS 只接受已绑定镜头的对白")
        if int(revision["revision_no"]) != expected_text_revision_no:
            raise DomainRuleError(
                "DIALOGUE_TEXT_REVISION_CONFLICT",
                "对白文本 revision 已变化，请刷新后重试",
                {"expected_revision": expected_text_revision_no, "actual_revision": int(revision["revision_no"])},
            )
        job = self.submit_tts_job(
            str(revision["id"]),
            voice_profile_version_id=voice_profile_version_id,
            emotion=emotion,
            speech_rate=speech_rate,
            idempotency_key=idempotency_key,
            actor=actor,
        )
        return {
            "line_id": line_id,
            "text_revision_id": str(revision["id"]),
            "text_revision_no": int(revision["revision_no"]),
            "job": {
                "id": str(job["id"]),
                "state": str(job["state"]),
                "subject_kind": str(job["subject_kind"]),
                "scope_project_id": str(job["scope_project_id"]),
                "scope_episode_id": str(job["scope_episode_id"]),
                "scope_shot_id": str(job["scope_shot_id"]),
                "stage_code": str(job["stage_code"]),
                "idempotent_replay": bool(job["idempotent_replay"]),
            },
        }

    def adopt_working_audio(
        self,
        media_version_id: str,
        *,
        expected_text_revision_no: int,
        idempotency_key: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        payload = {
            "media_version_id": media_version_id,
            "expected_text_revision_no": expected_text_revision_no,
        }
        scope = f"audio-working-adoption:{media_version_id}"
        now = _now()
        with self.database.transaction() as connection:
            replay = self._command_replay(
                connection,
                scope,
                idempotency_key,
                payload,
                mismatch_code="AUDIO_ADOPTION_IDEMPOTENCY_MISMATCH",
            )
            if replay is not None:
                return replay
            candidate = connection.execute(
                """SELECT tc.id AS tts_candidate_id,tc.dialogue_text_revision_id,tc.status,
                dtr.dialogue_line_id,dtr.revision_no,dl.shot_id,dl.episode_id,se.project_id,
                ma.media_kind,mv.integrity_status
                FROM tts_candidates tc
                JOIN dialogue_text_revisions dtr ON dtr.id=tc.dialogue_text_revision_id
                JOIN dialogue_lines dl ON dl.id=dtr.dialogue_line_id
                JOIN episodes e ON e.id=dl.episode_id JOIN seasons se ON se.id=e.season_id
                JOIN media_versions mv ON mv.id=tc.media_version_id
                JOIN media_assets ma ON ma.id=mv.media_asset_id
                WHERE tc.media_version_id=? ORDER BY tc.created_at DESC,tc.id DESC LIMIT 1""",
                (media_version_id,),
            ).fetchone()
            if candidate is None:
                raise DomainRuleError("TTS_CANDIDATE_NOT_FOUND", "该音频版本不是已登记的 TTS 候选")
            latest = connection.execute(
                "SELECT id,revision_no FROM dialogue_text_revisions WHERE dialogue_line_id=? ORDER BY revision_no DESC LIMIT 1",
                (candidate["dialogue_line_id"],),
            ).fetchone()
            if latest is None or int(latest["revision_no"]) != expected_text_revision_no:
                raise DomainRuleError(
                    "DIALOGUE_TEXT_REVISION_CONFLICT",
                    "对白文本 revision 已变化，请刷新后再采用声音",
                    {"expected_revision": expected_text_revision_no, "actual_revision": int(latest["revision_no"]) if latest else None},
                )
            if str(latest["id"]) != str(candidate["dialogue_text_revision_id"]):
                raise DomainRuleError("TTS_CANDIDATE_TEXT_STALE", "候选绑定的对白文本已不是最新 revision")
            if str(candidate["status"]) != "READY" or str(candidate["media_kind"]) != "AUDIO" or str(candidate["integrity_status"]) != "VERIFIED":
                raise DomainRuleError("TTS_CANDIDATE_MEDIA_INVALID", "工作采用只接受 READY 且完整性已验证的 AUDIO TTS 候选")
            selection_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO dialogue_candidate_selections
                (id,dialogue_line_id,tts_candidate_id,source_text_revision_id,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?,1,'v2')""",
                (selection_id, candidate["dialogue_line_id"], candidate["tts_candidate_id"], candidate["dialogue_text_revision_id"], now, now, actor),
            )
            result = {
                "id": selection_id,
                "dialogue_line_id": str(candidate["dialogue_line_id"]),
                "tts_candidate_id": str(candidate["tts_candidate_id"]),
                "media_version_id": media_version_id,
                "source_text_revision_id": str(candidate["dialogue_text_revision_id"]),
                "status": "ADOPTED",
                "idempotent_replay": False,
            }
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES (?,'producer','TTS_WORKING_AUDIO_ADOPTED','DIALOGUE_LINE',?,'在 Shot Studio 采用 TTS 工作候选',?)""",
                (actor, candidate["dialogue_line_id"], _json({"selection_id": selection_id, "tts_candidate_id": candidate["tts_candidate_id"], "media_version_id": media_version_id})),
            )
            connection.execute(
                """INSERT INTO outbox_events (type,project_id,subject_type,subject_id,payload_json)
                VALUES ('AudioWorkingCandidateChanged',?,'DIALOGUE_LINE',?,?)""",
                (candidate["project_id"], candidate["dialogue_line_id"], _json({"shot_id": candidate["shot_id"], "episode_id": candidate["episode_id"], "selection_id": selection_id})),
            )
            self._store_command(connection, scope, idempotency_key, payload, result)
            return result

    def create_voice_profile(
        self,
        project_id: str,
        *,
        code: str,
        title: str,
        voice_ref: str,
        license_status: str,
        license_evidence_path_rel: str,
        provider_profile_version_id: str | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        code, title, voice_ref = code.strip(), title.strip(), voice_ref.strip()
        if not code or not title or not voice_ref:
            raise DomainRuleError("VOICE_PROFILE_FIELDS_REQUIRED", "音色 code、标题和本地引用均为必填")
        if license_status not in {"USER_OWNED", "VERIFIED_LOCAL"}:
            raise DomainRuleError("VOICE_LICENSE_REQUIRED", "音色必须具有 USER_OWNED 或 VERIFIED_LOCAL 授权")
        with self.database.connect() as connection:
            project = connection.execute("SELECT root_rel FROM projects WHERE id=?", (project_id,)).fetchone()
            profile = (
                connection.execute("SELECT capability,status FROM execution_profile_versions WHERE id=?", (provider_profile_version_id,)).fetchone()
                if provider_profile_version_id
                else None
            )
        if project is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
        if provider_profile_version_id and (
            profile is None or profile["status"] != "PUBLISHED" or "TTS" not in str(profile["capability"]).upper()
        ):
            raise DomainRuleError("TTS_PROFILE_REQUIRED", "正式 TTS 音色必须绑定已发布 TTS Profile")
        root = self.settings.resolve_project_root(str(project["root_rel"]))
        evidence = controlled_path(
            root,
            license_evidence_path_rel,
            must_exist=True,
            require_file=True,
            code="VOICE_LICENSE_EVIDENCE_INVALID",
        )
        digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
        profile_id, now = str(uuid.uuid4()), _now()
        with self.database.transaction() as connection:
            next_no = connection.execute(
                "SELECT COALESCE(MAX(version_no),0)+1 FROM voice_profile_versions WHERE project_id=? AND code=?",
                (project_id, code),
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO voice_profile_versions
                (id,project_id,code,version_no,title,voice_ref,license_status,license_evidence_json,
                 provider_profile_version_id,status,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?,?,?,'ACTIVE',?,?,?,1,'v2')""",
                (
                    profile_id, project_id, code, next_no, title, voice_ref, license_status,
                    _json({"path_rel": evidence.relative_to(root).as_posix(), "sha256": digest}),
                    provider_profile_version_id, now, now, actor,
                ),
            )
        return self.get_voice_profile(profile_id)

    def discover_local_sapi_voices(self) -> dict[str, Any]:
        """Read local voice metadata through the selected platform adapter."""
        return dict(self.tts_runtime.discover_voices())

    def register_candidate(
        self,
        text_revision_id: str,
        *,
        voice_profile_version_id: str,
        media_version_id: str,
        emotion: str,
        speech_rate: float,
        seed: int | None,
        model_ref: str,
        candidate_kind: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        if not 0.5 <= speech_rate <= 2.0:
            raise DomainRuleError("TTS_SPEECH_RATE_INVALID", "TTS 语速必须在 0.5 到 2.0 之间")
        if candidate_kind not in {"PREVIEW", "FORMAL"}:
            raise DomainRuleError("TTS_CANDIDATE_KIND_INVALID", "候选类型必须是 PREVIEW 或 FORMAL")
        with self.database.connect() as connection:
            text_revision = connection.execute(
                """SELECT dtr.*,s.project_id FROM dialogue_text_revisions dtr
                JOIN dialogue_lines dl ON dl.id=dtr.dialogue_line_id JOIN episodes e ON e.id=dl.episode_id
                JOIN seasons s ON s.id=e.season_id WHERE dtr.id=?""", (text_revision_id,)
            ).fetchone()
            voice = connection.execute("SELECT * FROM voice_profile_versions WHERE id=?", (voice_profile_version_id,)).fetchone()
        if text_revision is None:
            raise DomainRuleError("DIALOGUE_TEXT_REVISION_NOT_FOUND", "对白文本 revision 不存在")
        if voice is None or voice["status"] != "ACTIVE":
            raise DomainRuleError("VOICE_PROFILE_NOT_ACTIVE", "音色版本不存在或未启用")
        if str(voice["project_id"]) != str(text_revision["project_id"]):
            raise DomainRuleError("VOICE_PROFILE_PROJECT_MISMATCH", "音色版本必须属于对白项目")
        if candidate_kind == "FORMAL" and not voice["provider_profile_version_id"]:
            raise DomainRuleError("TTS_FORMAL_PROFILE_REQUIRED", "正式 TTS 候选必须绑定已发布 TTS Profile")
        if not voice["provider_profile_version_id"] and model_ref.strip() != "IMPORTED_LOCAL_AUDIO":
            raise DomainRuleError("TTS_IMPORTED_MODEL_REF_REQUIRED", "未绑定 TTS Profile 的导入候选必须标记 IMPORTED_LOCAL_AUDIO")
        media = self._media().verify_content_integrity(media_version_id)
        if media["project_id"] != text_revision["project_id"] or media["media_kind"] != "AUDIO" or media["integrity_status"] != "VERIFIED":
            raise DomainRuleError("TTS_CANDIDATE_MEDIA_INVALID", "TTS 候选必须是同项目已验证 AUDIO MediaVersion")
        duration_ms = media.get("duration_ms")
        if candidate_kind == "PREVIEW" and (duration_ms is None or not 3_000 <= int(duration_ms) <= 10_000):
            raise DomainRuleError(
                "TTS_PREVIEW_DURATION_INVALID",
                "TTS 试听候选必须是已探测的 3—10 秒音频",
                {"duration_ms": duration_ms, "minimum_ms": 3_000, "maximum_ms": 10_000},
            )
        candidate_id, now = str(uuid.uuid4()), _now()
        provenance = {
            "schema_version": "localdrama.tts-candidate.v1",
            "text_revision_id": text_revision_id,
            "text_hash": text_revision["text_hash"],
            "voice_profile_version_id": voice_profile_version_id,
            "voice_license_status": voice["license_status"],
            "voice_license_evidence": json.loads(str(voice["license_evidence_json"])),
            "provider_profile_version_id": voice["provider_profile_version_id"],
            "media_sha256": media["sha256"],
            "media_duration_ms": int(duration_ms) if duration_ms is not None else None,
            "emotion": emotion.strip(),
            "speech_rate": speech_rate,
            "seed": seed,
            "model_ref": model_ref.strip(),
            "candidate_kind": candidate_kind,
        }
        if not provenance["emotion"] or not provenance["model_ref"]:
            raise DomainRuleError("TTS_PROVENANCE_REQUIRED", "TTS 候选必须记录情绪和 model ref")
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO tts_candidates
                (id,dialogue_text_revision_id,voice_profile_version_id,media_version_id,emotion,speech_rate,seed,
                 model_ref,candidate_kind,provenance_json,status,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,'READY',?,?,?,1,'v2')""",
                (candidate_id, text_revision_id, voice_profile_version_id, media_version_id, provenance["emotion"], speech_rate, seed, provenance["model_ref"], candidate_kind, _json(provenance), now, now, actor),
            )
        return self.get_candidate(candidate_id)

    def submit_tts_job(
        self,
        text_revision_id: str,
        *,
        voice_profile_version_id: str,
        emotion: str,
        speech_rate: float,
        idempotency_key: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        emotion = emotion.strip()
        if not emotion or not 0.5 <= speech_rate <= 2.0:
            raise DomainRuleError("TTS_JOB_PARAMETERS_INVALID", "TTS Job 必须提供情绪，语速必须在 0.5—2.0")
        with self.database.connect() as connection:
            text_revision = connection.execute(
                """SELECT dtr.*,dl.id AS dialogue_line_id,dl.shot_id,dl.episode_id,s.project_id FROM dialogue_text_revisions dtr
                JOIN dialogue_lines dl ON dl.id=dtr.dialogue_line_id JOIN episodes e ON e.id=dl.episode_id
                JOIN seasons s ON s.id=e.season_id WHERE dtr.id=?""",
                (text_revision_id,),
            ).fetchone()
            latest = connection.execute(
                "SELECT id FROM dialogue_text_revisions WHERE dialogue_line_id=(SELECT dialogue_line_id FROM dialogue_text_revisions WHERE id=?) ORDER BY revision_no DESC LIMIT 1",
                (text_revision_id,),
            ).fetchone()
            voice = connection.execute("SELECT * FROM voice_profile_versions WHERE id=?", (voice_profile_version_id,)).fetchone()
            profile = (
                connection.execute("SELECT * FROM execution_profile_versions WHERE id=?", (voice["provider_profile_version_id"],)).fetchone()
                if voice is not None and voice["provider_profile_version_id"]
                else None
            )
        if text_revision is None or latest is None:
            raise DomainRuleError("DIALOGUE_TEXT_REVISION_NOT_FOUND", "对白文本 revision 不存在")
        if str(latest["id"]) != text_revision_id:
            raise DomainRuleError("TTS_JOB_TEXT_STALE", "正式 TTS Job 只能使用最新对白文本 revision")
        if voice is None or voice["status"] != "ACTIVE" or str(voice["project_id"]) != str(text_revision["project_id"]):
            raise DomainRuleError("VOICE_PROFILE_NOT_ACTIVE", "TTS Job 音色必须是同项目 ACTIVE 版本")
        if (
            profile is None
            or profile["status"] != "PUBLISHED"
            or "TTS" not in str(profile["capability"]).upper()
            or not str(voice["voice_ref"]).startswith("sapi:")
        ):
            raise DomainRuleError("TTS_PUBLISHED_LOCAL_PROFILE_REQUIRED", "正式 TTS Job 必须绑定 Published 本地 SAPI TTS Profile")
        snapshot = {
            "schema_version": "localdrama.tts-job.v1",
            "text_revision_id": text_revision_id,
            "text_hash": str(text_revision["text_hash"]),
            "text": str(text_revision["text"]),
            "voice_profile_version_id": voice_profile_version_id,
            "voice_ref": str(voice["voice_ref"]),
            "voice_license_status": str(voice["license_status"]),
            "voice_license_evidence": json.loads(str(voice["license_evidence_json"])),
            "emotion": emotion,
            "speech_rate": speech_rate,
            "provider_profile_version_id": str(profile["id"]),
            "provider_kind": "WINDOWS_SAPI_LOCAL",
            "network_allowed": False,
        }
        if self.jobs is None:
            raise DomainRuleError("DIALOGUE_JOB_PORT_REQUIRED", "对白生成任务端口未配置")
        return self.jobs.create_job(
            str(text_revision["project_id"]),
            "TTS_GENERATION",
            "DIALOGUE_TEXT_REVISION",
            text_revision_id,
            "CPU",
            snapshot,
            idempotency_key,
            execution_profile_version_id=str(profile["id"]),
            max_attempts=1,
            actor=actor,
            subject_kind="DIALOGUE_TEXT_REVISION",
            scope_project_id=str(text_revision["project_id"]),
            scope_episode_id=str(text_revision["episode_id"]),
            scope_shot_id=str(text_revision["shot_id"]) if text_revision["shot_id"] is not None else None,
            stage_code="AUDIO_SUBTITLE",
        )

    def finalize_tts_job(self, job_id: str, actor: str = "local-user") -> dict[str, Any]:
        with self.database.connect() as connection:
            job = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            artifact = connection.execute(
                """SELECT a.id FROM artifacts a JOIN job_attempts ja ON ja.id=a.job_attempt_id
                WHERE ja.job_id=? AND ja.state='SUCCEEDED' AND a.status='VERIFIED' AND a.kind='TTS_AUDIO'
                ORDER BY a.created_at DESC LIMIT 1""",
                (job_id,),
            ).fetchone()
        if job is None or job["type"] != "TTS_GENERATION" or job["state"] != "SUCCEEDED" or artifact is None:
            raise DomainRuleError("TTS_JOB_NOT_FINALIZABLE", "只有成功且具有 VERIFIED TTS_AUDIO artifact 的 Job 可以结束登记")
        snapshot = json.loads(str(job["input_snapshot_json"]))
        promoted = self._media().promote_job_artifact(str(artifact["id"]), purpose="DIALOGUE_TTS", media_kind="AUDIO", stage="FORMAL", actor=actor)
        media_version_id = str(promoted.get("media_version_id") or promoted["id"])
        media = self._media().get_version(media_version_id)
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM tts_candidates WHERE dialogue_text_revision_id=? AND media_version_id=? ORDER BY created_at LIMIT 1",
                (snapshot["text_revision_id"], media_version_id),
            ).fetchone()
        candidate = (
            self.get_candidate(str(existing["id"]))
            if existing is not None
            else self.register_candidate(
                str(snapshot["text_revision_id"]),
                voice_profile_version_id=str(snapshot["voice_profile_version_id"]),
                media_version_id=media_version_id,
                emotion=str(snapshot["emotion"]),
                speech_rate=float(snapshot["speech_rate"]),
                seed=None,
                model_ref="WINDOWS_SAPI_LOCAL",
                candidate_kind="FORMAL",
                actor=actor,
            )
        )
        return {"job_id": job_id, "artifact_id": str(artifact["id"]), "media": media, "candidate": candidate, "idempotent_replay": existing is not None}

    def select_candidate(self, candidate_id: str, actor: str = "local-user") -> dict[str, Any]:
        candidate = self.get_candidate(candidate_id)
        with self.database.connect() as connection:
            latest = connection.execute(
                "SELECT id FROM dialogue_text_revisions WHERE dialogue_line_id=? ORDER BY revision_no DESC LIMIT 1",
                (candidate["dialogue_line_id"],),
            ).fetchone()
            machine = connection.execute(
                "SELECT status FROM machine_check_runs WHERE subject_type='MEDIA_VERSION' AND subject_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                (candidate["media_version_id"],),
            ).fetchone()
            review = connection.execute(
                """SELECT decision FROM review_decisions
                WHERE subject_type='MEDIA_VERSION' AND subject_id=? AND is_stale=0
                ORDER BY created_at DESC,id DESC LIMIT 1""",
                (candidate["media_version_id"],),
            ).fetchone()
        if latest is None or latest["id"] != candidate["dialogue_text_revision_id"]:
            raise DomainRuleError("TTS_CANDIDATE_TEXT_STALE", "候选绑定的对白文本已不是最新 revision")
        if candidate["candidate_kind"] == "FORMAL" and (
            machine is None or machine["status"] != "PASS" or review is None or review["decision"] != "APPROVED"
        ):
            raise DomainRuleError("TTS_FORMAL_APPROVAL_REQUIRED", "正式对白候选必须同时通过最新音频机器 QC 和人工审核")
        selection_id, now = str(uuid.uuid4()), _now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO dialogue_candidate_selections
                (id,dialogue_line_id,tts_candidate_id,source_text_revision_id,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?,1,'v2')""",
                (selection_id, candidate["dialogue_line_id"], candidate_id, candidate["dialogue_text_revision_id"], now, now, actor),
            )
        return {"id": selection_id, "tts_candidate_id": candidate_id, "status": "SELECTED", "source_text_revision_id": candidate["dialogue_text_revision_id"]}

    def get_voice_profile(self, profile_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM voice_profile_versions WHERE id=?", (profile_id,)).fetchone()
        if row is None:
            raise DomainRuleError("VOICE_PROFILE_NOT_FOUND", "音色版本不存在")
        result = dict(row)
        result["license_evidence"] = json.loads(result.pop("license_evidence_json"))
        return result

    def get_candidate(self, candidate_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT tc.*,dtr.dialogue_line_id FROM tts_candidates tc
                JOIN dialogue_text_revisions dtr ON dtr.id=tc.dialogue_text_revision_id WHERE tc.id=?""", (candidate_id,)
            ).fetchone()
        if row is None:
            raise DomainRuleError("TTS_CANDIDATE_NOT_FOUND", "TTS 候选不存在")
        result = dict(row)
        result["provenance"] = json.loads(result.pop("provenance_json"))
        return result

    def get_line(self, line_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            line = connection.execute("SELECT * FROM dialogue_lines WHERE id=?", (line_id,)).fetchone()
            revisions = connection.execute("SELECT * FROM dialogue_text_revisions WHERE dialogue_line_id=? ORDER BY revision_no", (line_id,)).fetchall()
            candidates = connection.execute(
                """SELECT tc.* FROM tts_candidates tc JOIN dialogue_text_revisions dtr ON dtr.id=tc.dialogue_text_revision_id
                WHERE dtr.dialogue_line_id=? ORDER BY tc.created_at""", (line_id,)
            ).fetchall()
            selected = connection.execute(
                "SELECT * FROM dialogue_candidate_selections WHERE dialogue_line_id=? ORDER BY created_at DESC,id DESC LIMIT 1", (line_id,)
            ).fetchone()
        if line is None:
            raise DomainRuleError("DIALOGUE_LINE_NOT_FOUND", "对白不存在")
        return {
            **dict(line),
            "text_revisions": [{**dict(row), "pronunciation": json.loads(row["pronunciation_json"])} for row in revisions],
            "candidates": [{**dict(row), "provenance": json.loads(row["provenance_json"])} for row in candidates],
            "selection": dict(selected) if selected else None,
        }

    def publish_local_sapi_profile(
        self,
        voice_ref: str,
        smoke_text: str = "本机语音合成验收通过",
        actor: str = "local-user",
    ) -> dict[str, Any]:
        """Publish the built-in Windows SAPI TTS profile only after a real local WAV probe."""
        voice_ref = voice_ref.strip()
        smoke_text = smoke_text.strip()
        if not voice_ref.startswith("sapi:") or not voice_ref.removeprefix("sapi:").strip():
            raise DomainRuleError("SAPI_VOICE_REF_INVALID", "必须显式选择本机扫描到的 SAPI 音色")
        discovered = self.discover_local_sapi_voices()
        voice = next((item for item in discovered.get("items", []) if item.get("voice_ref") == voice_ref), None)
        if voice is None:
            raise DomainRuleError("SAPI_VOICE_NOT_DISCOVERED", "所选 SAPI 音色不在本机实时扫描结果中")
        code = "local-tts-windows-sapi"
        now = _now()
        provisional_profile_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "local-drama:profile:local-tts-windows-sapi"))
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO execution_profiles
                (id,code,title,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?, ?,?,?,1,'v2') ON CONFLICT(code) DO NOTHING""",
                (provisional_profile_id, code, "Windows SAPI 本机 TTS", now, now, actor),
            )
            profile_row = connection.execute("SELECT id FROM execution_profiles WHERE code=?", (code,)).fetchone()
        if profile_row is None:
            raise DomainRuleError("SAPI_PROFILE_CREATE_FAILED", "无法创建本机 SAPI TTS Profile")
        profile_id = str(profile_row["id"])
        version_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"local-drama:profile-version:{profile_id}:1"))
        probe_dir = (self.settings.work_root / "profile-probes" / version_id).resolve()
        probe_dir.mkdir(parents=True, exist_ok=True)
        wav_path = probe_dir / "sapi-smoke.wav"
        try:
            self.tts_runtime.synthesize(
                voice=str(voice["name"]),
                text=smoke_text,
                output=wav_path,
                timeout=30,
            )
        except TtsRuntimeError as error:
            raise DomainRuleError("SAPI_SMOKE_TEST_FAILED", "本机 SAPI 冒烟测试无法执行", {"reason": type(error).__name__}) from error
        if not wav_path.is_file() or wav_path.stat().st_size <= 44:
            raise DomainRuleError("SAPI_SMOKE_TEST_FAILED", "本机 SAPI 未生成可读取的 WAV 冒烟证据")
        probe = self._media()._probe(wav_path, "AUDIO")
        audio_streams = [item for item in probe.get("streams", []) if isinstance(item, dict) and item.get("codec_type") == "audio"]
        if probe.get("probe_status") != "PASS" or not audio_streams:
            raise DomainRuleError("SAPI_SMOKE_TEST_FAILED", "本机 SAPI WAV 未通过 ffprobe 音频校验")
        digest = hashlib.sha256(wav_path.read_bytes()).hexdigest()
        evidence = {
            "runtime": "WINDOWS_SAPI_LOCAL",
            "voice_ref": voice_ref,
            "voice": voice,
            "smoke_sha256": digest,
            "smoke_byte_size": wav_path.stat().st_size,
            "smoke_path_rel": wav_path.relative_to(self.settings.work_root).as_posix(),
            "ffprobe": probe,
            "network_contacted": False,
            "validated_at": now,
        }
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO execution_profile_versions
                (id,execution_profile_id,version_no,capability,model_bundle_json,input_contract_json,
                 parameter_schema_json,status,manifest_sha256,capability_json,worker_policy,
                 created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,1,'TTS',?,?,?,'PUBLISHED',?,?, 'ONE_LOCAL_TTS_TASK',?,?,?,1,'v2')
                ON CONFLICT(execution_profile_id,version_no) DO UPDATE SET
                  capability='TTS',model_bundle_json=excluded.model_bundle_json,
                  input_contract_json=excluded.input_contract_json,parameter_schema_json=excluded.parameter_schema_json,
                  status='PUBLISHED',manifest_sha256=excluded.manifest_sha256,capability_json=excluded.capability_json,
                  worker_policy=excluded.worker_policy,updated_at=excluded.updated_at,revision=execution_profile_versions.revision+1""",
                (
                    version_id,
                    profile_id,
                    _json({"runtime": "WINDOWS_SAPI_LOCAL", "voice_ref": voice_ref}),
                    _json({"text": "string", "emotion": "string", "speech_rate": "0.5..2.0"}),
                    _json({"voice_ref": {"const": voice_ref}, "network": {"const": False}}),
                    digest,
                    _json(evidence),
                    now,
                    now,
                    actor,
                ),
            )
            actual = connection.execute(
                "SELECT id FROM execution_profile_versions WHERE execution_profile_id=? AND version_no=1",
                (profile_id,),
            ).fetchone()
            if actual is None:
                raise DomainRuleError("SAPI_PROFILE_CREATE_FAILED", "无法保存本机 SAPI TTS Profile 版本")
            version_id = str(actual["id"])
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES (?,'audio_editor','LOCAL_SAPI_TTS_PROFILE_PUBLISHED','execution_profile_version',?,
                '真实本机 SAPI WAV 冒烟通过后发布 TTS Profile',?)""",
                (actor, version_id, _json({"voice_ref": voice_ref, "smoke_sha256": digest, "network_contacted": False})),
            )
        return {"id": version_id, "code": code, "version_no": 1, "capability": "TTS", "status": "PUBLISHED", "evidence": evidence}

    def list_lines(self, episode_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            episode = connection.execute("SELECT id FROM episodes WHERE id=?", (episode_id,)).fetchone()
            rows = connection.execute("SELECT id FROM dialogue_lines WHERE episode_id=? ORDER BY code,id", (episode_id,)).fetchall()
        if episode is None:
            raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在")
        return [self.get_line(str(row["id"])) for row in rows]

    def list_voice_profiles(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            project = connection.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
            rows = connection.execute(
                "SELECT id FROM voice_profile_versions WHERE project_id=? ORDER BY code,version_no DESC", (project_id,)
            ).fetchall()
        if project is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
        return [self.get_voice_profile(str(row["id"])) for row in rows]

    def bind_character_voice(
        self,
        project_id: str,
        character_asset_id: str,
        voice_profile_version_id: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        """Bind a project CHARACTER story asset to an ACTIVE voice profile version."""
        with self.database.connect() as connection:
            project = connection.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
            asset = connection.execute("SELECT * FROM story_assets WHERE id=?", (character_asset_id,)).fetchone()
            voice = connection.execute("SELECT * FROM voice_profile_versions WHERE id=?", (voice_profile_version_id,)).fetchone()
            existing = connection.execute(
                "SELECT id FROM character_voice_bindings WHERE character_asset_id=?", (character_asset_id,)
            ).fetchone()
        if project is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
        if asset is None:
            raise DomainRuleError("STORY_ASSET_NOT_FOUND", "故事资产不存在")
        if str(asset["kind"]) != "CHARACTER":
            raise DomainRuleError("STORY_ASSET_KIND_INVALID", "角色音色绑定只接受 CHARACTER 故事资产")
        if str(asset["status"]) != "ACTIVE":
            raise DomainRuleError("STORY_ASSET_NOT_ACTIVE", "角色资产必须处于 ACTIVE 状态")
        if str(asset["project_id"]) != project_id:
            raise DomainRuleError("STORY_ASSET_PROJECT_MISMATCH", "角色资产必须属于当前项目")
        if voice is None or str(voice["status"]) != "ACTIVE":
            raise DomainRuleError("VOICE_PROFILE_NOT_ACTIVE", "音色版本不存在或未启用")
        if str(voice["project_id"]) != project_id:
            raise DomainRuleError("VOICE_PROFILE_PROJECT_MISMATCH", "音色版本必须属于当前项目")
        if existing is not None:
            raise DomainRuleError("CHARACTER_VOICE_ALREADY_BOUND", "该角色已绑定音色，请先解绑再换绑")
        binding_id, now = str(uuid.uuid4()), _now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO character_voice_bindings
                (id,project_id,character_asset_id,voice_profile_version_id,created_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,1,'v2')""",
                (binding_id, project_id, character_asset_id, voice_profile_version_id, now, actor),
            )
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES (?,'audio_editor','CHARACTER_VOICE_BOUND','character_voice_binding',?,'角色绑定音色',?)""",
                (
                    actor,
                    binding_id,
                    _json(
                        {
                            "project_id": project_id,
                            "character_asset_id": character_asset_id,
                            "voice_profile_version_id": voice_profile_version_id,
                        }
                    ),
                ),
            )
        return self._get_character_voice_binding(binding_id)

    def unbind_character_voice(self, binding_id: str, actor: str = "local-user") -> dict[str, Any]:
        with self.database.connect() as connection:
            binding = connection.execute("SELECT * FROM character_voice_bindings WHERE id=?", (binding_id,)).fetchone()
        if binding is None:
            raise DomainRuleError("CHARACTER_VOICE_BINDING_NOT_FOUND", "角色音色绑定不存在")
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM character_voice_bindings WHERE id=?", (binding_id,))
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES (?,'audio_editor','CHARACTER_VOICE_UNBOUND','character_voice_binding',?,'角色解除音色绑定',?)""",
                (
                    actor,
                    binding_id,
                    _json(
                        {
                            "character_asset_id": str(binding["character_asset_id"]),
                            "voice_profile_version_id": str(binding["voice_profile_version_id"]),
                        }
                    ),
                ),
            )
        return {"id": binding_id, "status": "UNBOUND"}

    def list_character_voice_bindings(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            project = connection.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
            rows = connection.execute(
                """SELECT cvb.id AS binding_id, cvb.project_id, cvb.character_asset_id, cvb.voice_profile_version_id,
                   cvb.created_at, cvb.created_by,
                   sa.code AS character_code, sa.name AS character_name, sa.kind AS character_kind, sa.status AS character_status,
                   vpv.code AS voice_code, vpv.title AS voice_title, vpv.voice_ref, vpv.status AS voice_status
                FROM character_voice_bindings cvb
                JOIN story_assets sa ON sa.id=cvb.character_asset_id
                JOIN voice_profile_versions vpv ON vpv.id=cvb.voice_profile_version_id
                WHERE cvb.project_id=? ORDER BY sa.code, cvb.created_at""",
                (project_id,),
            ).fetchall()
        if project is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
        return [self._character_voice_binding_row(row) for row in rows]

    def _get_character_voice_binding(self, binding_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT cvb.id AS binding_id, cvb.project_id, cvb.character_asset_id, cvb.voice_profile_version_id,
                   cvb.created_at, cvb.created_by,
                   sa.code AS character_code, sa.name AS character_name, sa.kind AS character_kind, sa.status AS character_status,
                   vpv.code AS voice_code, vpv.title AS voice_title, vpv.voice_ref, vpv.status AS voice_status
                FROM character_voice_bindings cvb
                JOIN story_assets sa ON sa.id=cvb.character_asset_id
                JOIN voice_profile_versions vpv ON vpv.id=cvb.voice_profile_version_id
                WHERE cvb.id=?""",
                (binding_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("CHARACTER_VOICE_BINDING_NOT_FOUND", "角色音色绑定不存在")
        return self._character_voice_binding_row(row)

    @staticmethod
    def _character_voice_binding_row(row: Any) -> dict[str, Any]:
        return {
            "id": str(row["binding_id"]),
            "project_id": str(row["project_id"]),
            "character_asset_id": str(row["character_asset_id"]),
            "voice_profile_version_id": str(row["voice_profile_version_id"]),
            "created_at": str(row["created_at"]),
            "created_by": str(row["created_by"]),
            "character": {
                "id": str(row["character_asset_id"]),
                "code": str(row["character_code"]),
                "name": str(row["character_name"]),
                "kind": str(row["character_kind"]),
                "status": str(row["character_status"]),
            },
            "voice": {
                "id": str(row["voice_profile_version_id"]),
                "code": str(row["voice_code"]),
                "title": str(row["voice_title"]),
                "voice_ref": str(row["voice_ref"]),
                "status": str(row["voice_status"]),
            },
        }

    def submit_episode_tts_batch(
        self,
        episode_id: str,
        *,
        idempotency_key_prefix: str,
        emotion: str = "NEUTRAL",
        speech_rate: float = 1.0,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        """Resolve every dialogue line to its bound voice and submit one TTS job per line.

        Resolution order: exactly one CHARACTER shot binding (when the line has a shot)
        first, then an exact normalized speaker match against CHARACTER asset name/code.
        Lines without a resolvable job-eligible voice are skipped, never blocking the rest.
        """
        emotion = emotion.strip()
        prefix = idempotency_key_prefix.strip()
        if not emotion:
            raise DomainRuleError("TTS_JOB_PARAMETERS_INVALID", "TTS 批量 Job 必须提供情绪")
        if not 0.5 <= speech_rate <= 2.0:
            raise DomainRuleError("TTS_SPEECH_RATE_INVALID", "TTS 语速必须在 0.5 到 2.0 之间")
        if not prefix or len(prefix) > 100:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "TTS 批量幂等前缀必须是 1—100 字符")
        with self.database.connect() as connection:
            episode = connection.execute(
                "SELECT e.id,s.project_id FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE e.id=?", (episode_id,)
            ).fetchone()
        if episode is None:
            raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在")
        project_id = str(episode["project_id"])
        lines = self.list_lines(episode_id)
        with self.database.connect() as connection:
            characters = connection.execute(
                "SELECT id,code,name FROM story_assets WHERE project_id=? AND kind='CHARACTER' AND status='ACTIVE' ORDER BY code",
                (project_id,),
            ).fetchall()
            binding_rows = connection.execute(
                """SELECT cvb.character_asset_id, cvb.voice_profile_version_id, vpv.voice_ref, vpv.status
                FROM character_voice_bindings cvb
                JOIN voice_profile_versions vpv ON vpv.id=cvb.voice_profile_version_id
                WHERE cvb.project_id=?""",
                (project_id,),
            ).fetchall()
            voice_rows: list[Any] = []
            profiles_by_id: dict[str, Any] = {}
            voice_ids = [str(row["voice_profile_version_id"]) for row in binding_rows]
            if voice_ids:
                placeholders = ",".join("?" for _ in voice_ids)
                voice_rows = connection.execute(
                    f"SELECT id,provider_profile_version_id FROM voice_profile_versions WHERE id IN ({placeholders})",
                    voice_ids,
                ).fetchall()
                provider_ids = [str(row["provider_profile_version_id"]) for row in voice_rows if row["provider_profile_version_id"]]
                if provider_ids:
                    profile_placeholders = ",".join("?" for _ in provider_ids)
                    for profile in connection.execute(
                        f"SELECT id,status,capability FROM execution_profile_versions WHERE id IN ({profile_placeholders})",
                        provider_ids,
                    ).fetchall():
                        profiles_by_id[str(profile["id"])] = profile
            shot_character_by_shot: dict[str, list[dict[str, Any]]] = {}
            shot_ids = [str(line["shot_id"]) for line in lines if line.get("shot_id")]
            if shot_ids:
                shot_placeholders = ",".join("?" for _ in shot_ids)
                for row in connection.execute(
                    f"""SELECT sab.shot_id, sa.id AS asset_id FROM shot_asset_bindings sab
                    JOIN story_assets sa ON sa.id=sab.asset_id
                    WHERE sab.shot_id IN ({shot_placeholders}) AND sa.kind='CHARACTER' AND sa.status='ACTIVE'""",
                    shot_ids,
                ).fetchall():
                    shot_character_by_shot.setdefault(str(row["shot_id"]), []).append(dict(row))
        provider_by_voice_id = {str(row["id"]): row["provider_profile_version_id"] for row in voice_rows}
        voice_by_character = {str(row["character_asset_id"]): row for row in binding_rows}

        def _normalize(value: str) -> str:
            return value.strip().replace("\u3000", "").replace(" ", "")

        submitted: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for line in lines:
            line_id = str(line["id"])
            code = str(line["code"])
            speaker = str(line["speaker"])
            revisions = line["text_revisions"]
            if not revisions:
                skipped.append({"line_id": line_id, "code": code, "speaker": speaker, "reason": "NO_TEXT_REVISION"})
                continue
            text_revision = revisions[-1]
            character_asset_id: str | None = None
            shot_id = line.get("shot_id")
            if shot_id:
                shot_characters = shot_character_by_shot.get(str(shot_id), [])
                if len(shot_characters) == 1:
                    character_asset_id = str(shot_characters[0]["asset_id"])
            if character_asset_id is None:
                normalized_speaker = _normalize(speaker)
                for character in characters:
                    if normalized_speaker and normalized_speaker in {
                        _normalize(str(character["name"])),
                        _normalize(str(character["code"])),
                    }:
                        character_asset_id = str(character["id"])
                        break
            if character_asset_id is None:
                skipped.append({"line_id": line_id, "code": code, "speaker": speaker, "reason": "VOICE_UNRESOLVED"})
                continue
            voice = voice_by_character.get(character_asset_id)
            if voice is None or str(voice["status"]) != "ACTIVE":
                skipped.append({"line_id": line_id, "code": code, "speaker": speaker, "reason": "VOICE_UNRESOLVED"})
                continue
            voice_profile_version_id = str(voice["voice_profile_version_id"])
            provider_profile_version_id = provider_by_voice_id.get(voice_profile_version_id)
            profile = profiles_by_id.get(str(provider_profile_version_id)) if provider_profile_version_id else None
            eligible = (
                provider_profile_version_id is not None
                and profile is not None
                and str(profile["status"]) == "PUBLISHED"
                and "TTS" in str(profile["capability"]).upper()
                and str(voice["voice_ref"]).startswith("sapi:")
            )
            if not eligible:
                skipped.append({"line_id": line_id, "code": code, "speaker": speaker, "reason": "VOICE_NOT_JOB_ELIGIBLE"})
                continue
            try:
                job = self.submit_tts_job(
                    str(text_revision["id"]),
                    voice_profile_version_id=voice_profile_version_id,
                    emotion=emotion,
                    speech_rate=speech_rate,
                    idempotency_key=f"{prefix}:{line_id}",
                    actor=actor,
                )
            except Exception as error:  # noqa: BLE001 - batch isolates per-line failures
                failed.append({"line_id": line_id, "code": code, "reason": str(getattr(error, "code", "UNKNOWN_ERROR"))})
                continue
            submitted.append(
                {
                    "line_id": line_id,
                    "code": code,
                    "speaker": speaker,
                    "character_asset_id": character_asset_id,
                    "voice_profile_version_id": voice_profile_version_id,
                    "job_id": str(job["id"]),
                    "text_revision_id": str(text_revision["id"]),
                }
            )
        result: dict[str, Any] = {
            "episode_id": episode_id,
            "submitted": submitted,
            "skipped": skipped,
            "failed": failed,
            "counts": {"submitted": len(submitted), "skipped": len(skipped), "failed": len(failed)},
        }
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES (?,'audio_editor','EPISODE_TTS_BATCH_SUBMITTED','episode',?,'整集 TTS 批量提交',?)""",
                (
                    actor,
                    episode_id,
                    _json(
                        {
                            "counts": result["counts"],
                            "job_ids": [item["job_id"] for item in submitted],
                            "idempotency_key_prefix": prefix,
                            "emotion": emotion,
                            "speech_rate": speech_rate,
                        }
                    ),
                ),
            )
        return result
