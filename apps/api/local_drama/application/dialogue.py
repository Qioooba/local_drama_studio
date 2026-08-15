"""Immutable dialogue text, voice authorization and TTS candidate governance."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class DialogueService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.media = MediaService(database, settings)

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
        root = (self.settings.projects_root / str(project["root_rel"])).resolve()
        candidate = root / license_evidence_path_rel
        evidence = candidate.resolve()
        relative_candidate = candidate.relative_to(root) if candidate.is_relative_to(root) else None
        has_symlink_component = relative_candidate is not None and any(
            (root / Path(*relative_candidate.parts[:index])).is_symlink() for index in range(1, len(relative_candidate.parts) + 1)
        )
        if relative_candidate is None or has_symlink_component or not evidence.is_relative_to(root) or not evidence.is_file():
            raise DomainRuleError("VOICE_LICENSE_EVIDENCE_INVALID", "音色授权证据必须是项目内普通文件")
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
        media = self.media.verify_content_integrity(media_version_id)
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
                """SELECT dtr.*,dl.id AS dialogue_line_id,s.project_id FROM dialogue_text_revisions dtr
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
        return JobService(self.database, self.settings).create_job(
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
        promoted = self.media.promote_job_artifact(str(artifact["id"]), purpose="DIALOGUE_TTS", media_kind="AUDIO", stage="FORMAL", actor=actor)
        media_version_id = str(promoted.get("media_version_id") or promoted["id"])
        media = self.media.get_version(media_version_id)
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
