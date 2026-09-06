"""Episode-scoped confirmation for an already materialized shot plan."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from local_drama.application.production_spec_resolution import effective_video_profile
from local_drama.domain.director_intent import normalize_director_intent_v3, validate_director_intent_v3_payload
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation_contracts import CameraPlan, resolve_camera_plan
from local_drama.domain.policies import validate_shot_ready
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _decode(value: object, fallback: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError):
        return fallback


class EpisodeShotReadyService:
    """Validate and confirm all active draft/director shots in one episode.

    This command exists for an already-applied plan whose director revisions
    are complete but whose status/camera contract still needs one explicit
    creator confirmation.  It appends immutable revisions and never rewrites
    the existing revision or media history.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _episode(connection: sqlite3.Connection, episode_id: str) -> sqlite3.Row:
        row = connection.execute(
            """SELECT e.id,e.revision,s.project_id
            FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE e.id=?""",
            (episode_id,),
        ).fetchone()
        if row is None:
            raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
        return row

    @staticmethod
    def _camera_contract(profile: dict[str, Any]) -> tuple[bool, bool]:
        parameter_schema = profile.get("parameter_schema")
        capabilities = parameter_schema.get("capabilities", {}) if isinstance(parameter_schema, dict) else {}
        camera = capabilities.get("camera", {}) if isinstance(capabilities, dict) else {}
        support = str(
            camera.get("support", "PROMPT_FALLBACK" if "camera" not in capabilities else "UNSUPPORTED")
        ) if isinstance(camera, dict) else "UNSUPPORTED"
        if support not in {"NATIVE", "PROMPT_FALLBACK", "UNSUPPORTED"}:
            raise DomainRuleError("PROFILE_CAMERA_CONTRACT_INVALID", "Profile camera capability support 无效")
        fallback = support == "PROMPT_FALLBACK" and (
            camera.get("prompt_fallback", True)
            if "prompt_fallback" not in camera
            else camera.get("prompt_fallback") is True
        )
        if support == "PROMPT_FALLBACK" and not fallback:
            raise DomainRuleError("PROFILE_CAMERA_FALLBACK_INVALID", "Camera prompt fallback 必须由 Profile 显式声明")
        return support == "NATIVE", fallback

    @classmethod
    def _resolve_fields(cls, fields: object, profile: dict[str, Any], *, auto_heal: bool = False) -> dict[str, Any]:
        from local_drama.application.shot_production_normalizer import ShotProductionSpecNormalizer

        if not isinstance(fields, dict):
            fields = {}
        if not auto_heal:
            # Strictly validate without auto-healing dummy/default fields
            if not fields:
                raise DomainRuleError("SHOT_NOT_PRODUCTION_READY", "镜头分镜草稿未填写导演意图")
            normalized = normalize_director_intent_v3(fields)
            validate_director_intent_v3_payload(normalized)
            camera_payload = normalized.get("camera_plan")
            if not camera_payload or not isinstance(camera_payload, dict):
                raise DomainRuleError("CAMERA_PLAN_REQUIRED", "运镜方案必须是对象")
            current_camera = CameraPlan.from_payload(camera_payload)
            profile_id = str(profile.get("id") or "") or None
        else:
            # Self-heal raw/incomplete fields into fully compliant director-intent.v3
            profile_id = str(profile.get("id") or "") or None
            normalized = ShotProductionSpecNormalizer.normalize_fields(fields, profile_version_id=profile_id)
            current_camera = CameraPlan.from_payload(normalized.get("camera_plan"))

        native_supported, prompt_fallback_supported = cls._camera_contract(profile)
        resolved = resolve_camera_plan(
            native_supported=native_supported,
            prompt_fallback_supported=prompt_fallback_supported,
            shot_type=current_camera.shot_type,
            movement=current_camera.movement,
            prompt_text=current_camera.prompt_text or f"平稳运镜 {current_camera.movement}，聚焦主体",
            direction=current_camera.direction,
            intensity=current_camera.intensity,
            curve=current_camera.curve,
            profile_version_id=profile_id,
        )
        if resolved.mode == "UNSUPPORTED":
            # If native camera control is unsupported by this profile, fallback to prompt-based camera control
            resolved = CameraPlan(
                mode="PROMPT_FALLBACK",
                shot_type=current_camera.shot_type,
                movement=current_camera.movement,
                prompt_text=current_camera.prompt_text or f"平稳运镜 {current_camera.movement}，聚焦主体与场景光影",
                direction=current_camera.direction,
                intensity=current_camera.intensity,
                curve=current_camera.curve,
                profile_version_id=profile_id,
            )
            resolved.validate()
        normalized["camera_plan"] = resolved.to_dict()
        validate_shot_ready(normalized)
        return normalized

    def confirm(
        self,
        episode_id: str,
        *,
        expected_episode_revision: int,
        idempotency_key: str,
        actor: str = "local-user",
        auto_heal: bool = False,
    ) -> dict[str, Any]:
        key = idempotency_key.strip()
        if not key or len(key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "命令必须提供 1—200 字符的 Idempotency-Key")
        scope = f"episode-shots-ready:{episode_id}"
        payload_hash = _hash({"expected_episode_revision": expected_episode_revision, "auto_heal": auto_heal})
        with self.database.transaction() as connection:
            prior = connection.execute(
                "SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?",
                (scope, key),
            ).fetchone()
            if prior is not None:
                if str(prior["payload_hash"]) != payload_hash:
                    raise DomainRuleError("EPISODE_SHOTS_READY_IDEMPOTENCY_MISMATCH", "相同幂等键不能复用不同的本集确认请求")
                replay = cast(dict[str, Any], _decode(prior["response_json"], {}))
                replay["idempotent_replay"] = True
                return replay

            episode = self._episode(connection, episode_id)
            if int(episode["revision"]) != expected_episode_revision:
                raise DomainRuleError(
                    "EPISODE_SHOTS_READY_REVISION_CONFLICT",
                    "本集已变化，请刷新后重新确认当前分镜方案",
                    {"expected_revision": expected_episode_revision, "actual_revision": int(episode["revision"])},
                )
            profile = effective_video_profile(connection, str(episode["project_id"]))
            if profile is None:
                raise DomainRuleError(
                    "VIDEO_PROFILE_REQUIRED",
                    "当前项目没有可执行的已发布 VIDEO Profile，请先完成项目生成设置",
                    {"project_id": str(episode["project_id"])},
                )
            if str(profile.get("status")) != "PUBLISHED":
                raise DomainRuleError("PROFILE_NOT_PUBLISHED", "只有已发布 Profile 才能确认本集镜头")

            shots = connection.execute(
                """SELECT s.id,s.code,s.revision,s.current_revision_id,s.status,
                sr.fields_json,sr.revision_no,sr.is_frozen
                FROM shots s LEFT JOIN shot_revisions sr ON sr.id=s.current_revision_id
                WHERE s.episode_id=? AND s.status IN ('DRAFT','DIRECTED') AND s.archived_at IS NULL
                ORDER BY CAST(s.order_key AS REAL),s.code,s.id""",
                (episode_id,),
            ).fetchall()
            if not shots:
                raise DomainRuleError("EPISODE_SHOTS_READY_NOTHING_TO_DO", "当前本集没有待确认的分镜草稿")

            validated: list[tuple[sqlite3.Row, dict[str, Any]]] = []
            failures: list[dict[str, Any]] = []
            for shot in shots:
                try:
                    fields = _decode(shot["fields_json"], None)
                    normalized = self._resolve_fields(fields, profile, auto_heal=auto_heal)
                except DomainRuleError as error:
                    failures.append({
                        "shot_id": str(shot["id"]),
                        "shot_code": str(shot["code"]),
                        "code": error.code,
                        "message": error.message,
                    })
                except (TypeError, ValueError, json.JSONDecodeError) as error:
                    failures.append({
                        "shot_id": str(shot["id"]),
                        "shot_code": str(shot["code"]),
                        "code": "SHOT_REVISION_INVALID",
                        "message": f"镜头导演意图无法解析：{type(error).__name__}",
                    })
                else:
                    validated.append((shot, normalized))
            if failures:
                raise DomainRuleError(
                    "EPISODE_SHOTS_READY_VALIDATION_FAILED",
                    "本集存在不能就绪的镜头，未执行任何写入",
                    {"failed_shots": failures, "profile_version_id": str(profile["id"])},
                )

            now = _now()
            ready_ids: list[str] = []
            for shot, fields in validated:
                revision_id = str(uuid.uuid4())
                revision_no = int(connection.execute(
                    "SELECT COALESCE(MAX(revision_no),0)+1 FROM shot_revisions WHERE shot_id=?",
                    (str(shot["id"]),),
                ).fetchone()[0])
                connection.execute(
                    """INSERT INTO shot_revisions
                    (id,shot_id,revision_no,fields_json,is_frozen,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?, ?,0,?,?,?,1,'v2')""",
                    (revision_id, str(shot["id"]), revision_no, _json(fields), now, now, actor),
                )
                changed = connection.execute(
                    """UPDATE shots SET current_revision_id=?,status='READY',revision=revision+1,updated_at=?
                    WHERE id=? AND episode_id=? AND status=? AND revision=? AND archived_at IS NULL""",
                    (revision_id, now, str(shot["id"]), episode_id, str(shot["status"]), int(shot["revision"])),
                )
                if changed.rowcount != 1:
                    raise DomainRuleError(
                        "EPISODE_SHOTS_READY_REVISION_CONFLICT",
                        "本集镜头在确认期间发生变化，请刷新后重试",
                        {"shot_id": str(shot["id"])},
                    )
                ready_ids.append(str(shot["id"]))

            episode_changed = connection.execute(
                "UPDATE episodes SET revision=revision+1,updated_at=? WHERE id=? AND revision=?",
                (now, episode_id, expected_episode_revision),
            )
            if episode_changed.rowcount != 1:
                raise DomainRuleError("EPISODE_SHOTS_READY_REVISION_CONFLICT", "本集已变化，请刷新后重试")
            result = {
                "status": "READY",
                "episode_id": episode_id,
                "project_id": str(episode["project_id"]),
                "profile_version_id": str(profile["id"]),
                "episode_revision": expected_episode_revision + 1,
                "ready_shot_ids": ready_ids,
                "ready_shot_count": len(ready_ids),
                "failed_shots": [],
                "idempotent_replay": False,
            }
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,before_revision,after_revision,summary,metadata_redacted_json)
                VALUES (?,'director','EPISODE_SHOTS_MARKED_READY','episode',?,?,?,?,?)""",
                (
                    actor,
                    episode_id,
                    expected_episode_revision,
                    expected_episode_revision + 1,
                    "人工确认后批量标记本集当前分镜方案为可生产",
                    _json({"episode_id": episode_id, "ready_shot_count": len(ready_ids), "profile_version_id": profile["id"]}),
                ),
            )
            connection.execute(
                """INSERT INTO outbox_events (type,project_id,subject_type,subject_id,payload_json)
                VALUES ('EPISODE_SHOTS_READY',?,'EPISODE',?,?)""",
                (str(episode["project_id"]), episode_id, _json(result)),
            )
            connection.execute(
                """INSERT INTO command_idempotencies (scope,idempotency_key,payload_hash,response_json)
                VALUES (?,?,?,?)""",
                (scope, key, payload_hash, _json(result)),
            )
            return result
