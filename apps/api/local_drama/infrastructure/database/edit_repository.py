"""Canonical Post / Edit projection and immutable timeline commands."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.domain.dialogue_timing import assert_dialogue_timing, dialogue_timing_issues
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class SqliteEpisodeEditRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _episode(connection: sqlite3.Connection, episode_id: str) -> dict[str, Any]:
        row = connection.execute(
            """SELECT e.id,e.code,e.title,se.project_id FROM episodes e
            JOIN seasons se ON se.id=e.season_id WHERE e.id=?""",
            (episode_id,),
        ).fetchone()
        if row is None:
            raise DomainRuleError("EPISODE_NOT_FOUND", "分集不存在", {"episode_id": episode_id})
        return dict(row)

    @staticmethod
    def _video_upstream(connection: sqlite3.Connection, episode_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            """WITH continuity AS (
              SELECT shot_id,
              CASE WHEN MAX(is_stale)=1 THEN 'STALE'
                   WHEN MAX(is_conflict)=1 THEN 'CONFLICT'
                   WHEN MAX(is_attention)=1 THEN 'ATTENTION' ELSE 'OK' END continuity_status
              FROM (
                SELECT to_shot_id shot_id,is_stale,
                CASE WHEN compatibility_status IN ('BLOCKED','CONFLICT','INCOMPATIBLE') THEN 1 ELSE 0 END is_conflict,
                CASE WHEN compatibility_status IN ('WARNING','ATTENTION') THEN 1 ELSE 0 END is_attention
                FROM shot_transition_constraints
                UNION ALL
                SELECT from_shot_id,is_stale,
                CASE WHEN compatibility_status IN ('BLOCKED','CONFLICT','INCOMPATIBLE') THEN 1 ELSE 0 END,
                CASE WHEN compatibility_status IN ('WARNING','ATTENTION') THEN 1 ELSE 0 END
                FROM shot_transition_constraints
              ) GROUP BY shot_id
            ), working_slot AS (
              SELECT ws.shot_id, ws.media_version_id
              FROM shot_working_media_slots ws
              JOIN media_versions mv ON mv.id=ws.media_version_id
              JOIN media_assets ma ON ma.id=mv.media_asset_id
              WHERE ws.slot_type='VIDEO' AND ma.media_kind='VIDEO'
            ), selected AS (
              SELECT s.id AS shot_id,
              COALESCE(ws.media_version_id, se.media_version_id) AS media_version_id,
              ROW_NUMBER() OVER (PARTITION BY s.id ORDER BY
                CASE WHEN ws.media_version_id IS NOT NULL THEN 3
                     WHEN se.selection_type = 'FORMAL_SELECTION' THEN 2 ELSE 1 END DESC,
                se.created_at DESC,se.id DESC) AS rank_no
              FROM shots s
              LEFT JOIN working_slot ws ON ws.shot_id=s.id
              LEFT JOIN selections se ON se.selection_type IN ('FORMAL_SELECTION','PROXY_WINNER')
              LEFT JOIN media_versions selected_mv ON selected_mv.id=se.media_version_id
              LEFT JOIN media_assets ma ON ma.id=selected_mv.media_asset_id
              LEFT JOIN generation_variants gv ON ma.owner_type='GENERATION_VARIANT' AND ma.owner_id=gv.id
              LEFT JOIN generation_intents gi ON gi.id=gv.intent_id
              WHERE s.episode_id=? AND (
                ws.media_version_id IS NOT NULL
                OR (
                  ma.media_kind='VIDEO' AND selected_mv.mime_type LIKE 'video/%'
                  AND ((ma.owner_type='SHOT' AND ma.owner_id=s.id)
                    OR (gi.owner_type='SHOT' AND gi.owner_id=s.id))
                )
              )
            )
            SELECT s.id AS shot_id,s.code AS shot_code,s.target_duration_ms,
              selected.media_version_id,mv.source_name,mv.duration_ms AS source_duration_ms,mv.sha256 AS media_sha256,
              COALESCE(continuity.continuity_status,'MISSING') AS continuity_status
            FROM shots s
            LEFT JOIN selected ON selected.shot_id=s.id AND selected.rank_no=1
            LEFT JOIN media_versions mv ON mv.id=selected.media_version_id
            LEFT JOIN continuity ON continuity.shot_id=s.id
            WHERE s.episode_id=? AND s.archived_at IS NULL
            ORDER BY CAST(s.order_key AS REAL),s.code,s.id LIMIT 501""",
            (episode_id, episode_id),
        ).fetchall()
        if len(rows) > 500:
            raise DomainRuleError("TIMELINE_SHOT_LIMIT_EXCEEDED", "单集时间线最多支持 500 个镜头，请先拆分分集")
        return [dict(row) for row in rows]

    @staticmethod
    def _dialogue_upstream(connection: sqlite3.Connection, episode_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            """SELECT dl.id,dl.code,dl.shot_id,dcs.tts_candidate_id,tc.media_version_id,
            mv.source_name,mv.duration_ms,mv.sha256,dcs.revision AS selection_revision,
            CASE WHEN dcs.source_text_revision_id<>dtr.id THEN 1 ELSE 0 END AS selection_stale
            FROM dialogue_lines dl
            JOIN dialogue_text_revisions dtr ON dtr.id=(SELECT latest.id FROM dialogue_text_revisions latest
              WHERE latest.dialogue_line_id=dl.id ORDER BY latest.revision_no DESC,latest.id DESC LIMIT 1)
            LEFT JOIN dialogue_candidate_selections dcs ON dcs.id=(SELECT latest_selection.id FROM dialogue_candidate_selections latest_selection
              WHERE latest_selection.dialogue_line_id=dl.id ORDER BY latest_selection.created_at DESC,latest_selection.id DESC LIMIT 1)
            LEFT JOIN tts_candidates tc ON tc.id=dcs.tts_candidate_id
            LEFT JOIN media_versions mv ON mv.id=tc.media_version_id
            WHERE dl.episode_id=? ORDER BY dl.code,dl.id""",
            (episode_id,),
        ).fetchall()
        return [{**dict(row), "selection_stale": bool(row["selection_stale"])} for row in rows]

    @staticmethod
    def _audio_upstream(connection: sqlite3.Connection, episode_id: str) -> tuple[int, list[dict[str, Any]]]:
        mix = connection.execute("SELECT revision FROM audio_mix_drafts WHERE episode_id=?", (episode_id,)).fetchone()
        rows = connection.execute(
            """SELECT ab.id,ab.media_version_id,ab.track_type,mv.source_name,ab.start_us,ab.end_us,
            ab.gain_db,ab.loop_enabled,ab.fade_in_us,ab.fade_out_us,ab.revision,mv.sha256
            FROM audio_bindings ab JOIN media_versions mv ON mv.id=ab.media_version_id
            WHERE ab.episode_id=? ORDER BY ab.start_us,ab.id""",
            (episode_id,),
        ).fetchall()
        return (int(mix["revision"]) if mix else 0, [dict(row) for row in rows])

    @staticmethod
    def _subtitle_upstream(connection: sqlite3.Connection, episode_id: str) -> dict[str, Any] | None:
        row = connection.execute(
            """SELECT sr.id AS revision_id,sr.revision_no,sr.format,sr.content_hash,
            (SELECT COUNT(*) FROM subtitle_cues sc WHERE sc.subtitle_revision_id=sr.id) AS cue_count
            FROM subtitle_revisions sr WHERE sr.episode_id=? ORDER BY sr.revision_no DESC,sr.id DESC LIMIT 1""",
            (episode_id,),
        ).fetchone()
        return dict(row) if row else None

    @classmethod
    def _upstream(cls, connection: sqlite3.Connection, episode_id: str) -> dict[str, Any]:
        video = cls._video_upstream(connection, episode_id)
        dialogue = cls._dialogue_upstream(connection, episode_id)
        mix_revision, audio = cls._audio_upstream(connection, episode_id)
        subtitle = cls._subtitle_upstream(connection, episode_id)
        fingerprint = _digest(
            {
                "video": [[item["shot_id"], item["media_version_id"], item["media_sha256"], item["target_duration_ms"]] for item in video],
                "dialogue": [[item["id"], item["tts_candidate_id"], item["media_version_id"], item["sha256"], item["selection_revision"]] for item in dialogue],
                "audio_mix_revision": mix_revision,
                "audio": [[item["id"], item["media_version_id"], item["revision"]] for item in audio],
                "subtitle": [subtitle["revision_id"], subtitle["content_hash"]] if subtitle else None,
            }
        )
        return {"video": video, "dialogue": dialogue, "audio": audio, "audio_mix_revision": mix_revision, "subtitle": subtitle, "fingerprint": fingerprint}

    @staticmethod
    def _revision_summary(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        snapshot = json.loads(str(item["input_snapshot_json"] or "{}"))
        content = json.loads(str(item["content_json"] or "[]"))
        video = [clip for clip in content if str(clip.get("track_type", "")).upper() == "VIDEO"]
        audio = [clip for clip in content if str(clip.get("track_type", "")).upper() != "VIDEO"]
        return {
            "id": str(item["id"]),
            "revision_no": int(item["revision_no"]),
            "status": str(item["status"]),
            "revision_hash": str(item["revision_hash"]),
            "duration_us": max((int(clip.get("end_us") or 0) for clip in video), default=0),
            "video_count": len(video),
            "audio_count": len(audio),
            "subtitle_revision_id": snapshot.get("subtitle_revision_id"),
            "upstream_fingerprint": snapshot.get("upstream_fingerprint") or snapshot.get("upstream_selection_fingerprint"),
            "created_at": str(item["created_at"]),
            "created_by": str(item["created_by"]),
        }

    @staticmethod
    def _timeline_video_clips(latest: sqlite3.Row, upstream_video: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_shot = {str(item["shot_id"]): item for item in upstream_video}
        content = json.loads(str(latest["content_json"] or "[]"))
        clips: list[dict[str, Any]] = []
        for item in content:
            if str(item.get("track_type", "")).upper() != "VIDEO":
                continue
            parameters = item.get("parameters") or {}
            shot_id = str(parameters.get("shot_id") or "")
            source = by_shot.get(shot_id, {})
            clips.append(
                {
                    "shot_id": shot_id,
                    "shot_code": str(parameters.get("shot_code") or source.get("shot_code") or shot_id[:8]),
                    "media_version_id": item.get("media_version_id"),
                    "source_name": source.get("source_name"),
                    "source_duration_ms": source.get("source_duration_ms"),
                    "start_us": int(item["start_us"]),
                    "end_us": int(item["end_us"]),
                    "source_start_us": int(parameters.get("source_start_us") or 0),
                    "transition_in": str(parameters.get("transition_in") or "CUT"),
                    "continuity_status": str(source.get("continuity_status") or "LEGACY"),
                }
            )
        return clips

    @staticmethod
    def _upstream_video_clips(video: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cursor = 0
        clips: list[dict[str, Any]] = []
        for item in video:
            duration = max(100_000, int(item["target_duration_ms"]) * 1000)
            clips.append(
                {
                    "shot_id": str(item["shot_id"]),
                    "shot_code": str(item["shot_code"]),
                    "media_version_id": item["media_version_id"],
                    "source_name": item["source_name"],
                    "source_duration_ms": item["source_duration_ms"],
                    "start_us": cursor,
                    "end_us": cursor + duration,
                    "source_start_us": 0,
                    "transition_in": "CUT",
                    "continuity_status": str(item["continuity_status"]),
                }
            )
            cursor += duration
        return clips

    @staticmethod
    def _audio_facts(upstream: dict[str, Any], video_clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
        shot_starts = {str(item["shot_id"]): int(item["start_us"]) for item in video_clips}
        shot_offsets: dict[str, int] = {}
        facts: list[dict[str, Any]] = []
        for item in upstream["dialogue"]:
            media_id = item.get("media_version_id")
            shot_id = str(item.get("shot_id") or "")
            if not media_id or item["selection_stale"] or shot_id not in shot_starts:
                continue
            duration = max(100_000, int(item.get("duration_ms") or 0) * 1000)
            offset = shot_offsets.get(shot_id, 0)
            start = shot_starts[shot_id] + offset
            shot_offsets[shot_id] = offset + duration
            facts.append({"id": str(item["id"]), "lane": "DIALOGUE", "media_version_id": str(media_id), "source_name": str(item.get("source_name") or item["code"]), "start_us": start, "end_us": start + duration, "gain_db": 0.0, "source_revision": int(item.get("selection_revision") or 0), "owner_route": "SHOT_STUDIO"})
        for item in upstream["audio"]:
            kind = str(item["track_type"]).upper()
            lane = kind if kind in {"BGM", "SFX", "ENVIRONMENT"} else "LEGACY"
            facts.append({"id": str(item["id"]), "lane": lane, "media_version_id": str(item["media_version_id"]), "source_name": str(item["source_name"]), "start_us": int(item["start_us"]), "end_us": int(item["end_us"]), "gain_db": float(item["gain_db"]), "source_revision": int(item["revision"]), "owner_route": "POST_AUDIO"})
        return facts

    def workspace(self, episode_id: str, *, history_limit: int = 20) -> dict[str, Any]:
        limit = max(1, min(int(history_limit), 50))
        with self.database.connect() as connection:
            episode = self._episode(connection, episode_id)
            upstream = self._upstream(connection, episode_id)
            rows = connection.execute(
                "SELECT * FROM timeline_revisions WHERE episode_id=? ORDER BY revision_no DESC,id DESC LIMIT ?",
                (episode_id, limit + 1),
            ).fetchall()
        latest = rows[0] if rows else None
        latest_summary = self._revision_summary(latest) if latest else None
        latest_fingerprint = latest_summary["upstream_fingerprint"] if latest_summary else None
        freshness = "EMPTY" if latest is None else ("CURRENT" if latest_fingerprint == upstream["fingerprint"] and str(latest["status"]) != "STALE" else "STALE")
        video_clips = self._timeline_video_clips(latest, upstream["video"]) if latest is not None and freshness == "CURRENT" else self._upstream_video_clips(upstream["video"])
        audio_clips = self._audio_facts(upstream, video_clips)
        shot_for_line = {str(line["id"]): str(line.get("shot_id") or "") for line in upstream["dialogue"]}
        timing_items = [
            {**clip, "track_type": "VIDEO", "parameters": {"shot_id": str(clip["shot_id"])}}
            for clip in video_clips
        ] + [
            {**clip, "track_type": "DIALOGUE", "parameters": {"dialogue_line_id": clip["id"], "shot_id": shot_for_line.get(str(clip["id"]), "")}}
            for clip in audio_clips if clip["lane"] == "DIALOGUE"
        ]
        issues: list[dict[str, Any]] = [
            {"code": issue["code"], "severity": "BLOCKER", "message": issue["message"], "subject_id": issue["shot_id"], "owner_route": "SHOT_STUDIO"}
            for issue in dialogue_timing_issues(timing_items)
        ]
        for item in upstream["video"]:
            if not item["media_version_id"]:
                issues.append({"code": "VIDEO_SELECTION_MISSING", "severity": "BLOCKER", "message": f"{item['shot_code']} 尚未采用视频", "subject_id": item["shot_id"], "owner_route": "SHOT_STUDIO"})
            if item["continuity_status"] == "CONFLICT":
                issues.append({"code": "CONTINUITY_CONFLICT", "severity": "BLOCKER", "message": f"{item['shot_code']} 存在连续性冲突", "subject_id": item["shot_id"], "owner_route": "SHOT_STUDIO"})
            elif item["continuity_status"] in {"STALE", "ATTENTION"}:
                issues.append({"code": "CONTINUITY_REVIEW", "severity": "WARNING", "message": f"{item['shot_code']} 需要复核连续性", "subject_id": item["shot_id"], "owner_route": "REVIEW"})
        if freshness == "STALE":
            issues.insert(0, {"code": "TIMELINE_UPSTREAM_STALE", "severity": "WARNING", "message": "镜头采用、声音或字幕已变化；当前画布已显式载入最新上游，保存后会创建新版本", "subject_id": str(latest["id"]) if latest else None, "owner_route": "POST_EDIT"})
        allowed = []
        if video_clips and not any(item["severity"] == "BLOCKER" for item in issues):
            allowed.append("CREATE_DRAFT")
        if latest_summary and latest_summary["status"] == "DRAFT" and freshness == "CURRENT":
            allowed.append("FREEZE_LATEST_DRAFT")
        return {
            "episode_id": episode_id,
            "project_id": str(episode["project_id"]),
            "episode_code": str(episode["code"]),
            "episode_title": episode["title"],
            "freshness": freshness,
            "upstream_fingerprint": upstream["fingerprint"],
            "latest_revision": latest_summary,
            "history": [self._revision_summary(row) for row in rows[:limit]],
            "history_has_more": len(rows) > limit,
            "video_clips": video_clips,
            "audio_clips": audio_clips,
            "subtitle": upstream["subtitle"],
            "issues": issues,
            "duration_us": max((int(item["end_us"]) for item in video_clips), default=0),
            "allowed_actions": allowed,
        }

    @staticmethod
    def _idempotent_replay(connection: sqlite3.Connection, scope: str, key: str, payload_hash: str) -> dict[str, Any] | None:
        row = connection.execute("SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?", (scope, key)).fetchone()
        if row is None:
            return None
        if str(row["payload_hash"]) != payload_hash:
            raise DomainRuleError("TIMELINE_IDEMPOTENCY_PAYLOAD_MISMATCH", "相同 idempotency_key 的编辑请求内容不一致")
        return {**json.loads(str(row["response_json"])), "idempotent_replay": True}

    @staticmethod
    def _store_idempotency(connection: sqlite3.Connection, scope: str, key: str, payload_hash: str, result: dict[str, Any]) -> None:
        connection.execute("INSERT INTO command_idempotencies (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)", (scope, key, payload_hash, _json(result)))

    @staticmethod
    def _latest_id(connection: sqlite3.Connection, episode_id: str) -> str | None:
        row = connection.execute("SELECT id FROM timeline_revisions WHERE episode_id=? ORDER BY revision_no DESC,id DESC LIMIT 1", (episode_id,)).fetchone()
        return str(row["id"]) if row else None

    @classmethod
    def _validate_latest(cls, connection: sqlite3.Connection, episode_id: str, expected: str | None) -> None:
        actual = cls._latest_id(connection, episode_id)
        if actual != expected:
            raise DomainRuleError("TIMELINE_REVISION_CONFLICT", "时间线已有新版本，请刷新后重试", {"expected_revision_id": expected, "actual_revision_id": actual})

    @staticmethod
    def _insert_revision(connection: sqlite3.Connection, episode_id: str, items: list[dict[str, Any]], snapshot: dict[str, Any], status: str, actor: str, now: str) -> dict[str, Any]:
        revision_no = int(connection.execute("SELECT COALESCE(MAX(revision_no),0)+1 FROM timeline_revisions WHERE episode_id=?", (episode_id,)).fetchone()[0])
        revision_id = str(uuid.uuid4())
        revision_hash = _digest({"items": items, "input_snapshot": snapshot})
        connection.execute(
            """INSERT INTO timeline_revisions
            (id,episode_id,revision_no,content_json,input_snapshot_json,revision_hash,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,?,?,?,?,1,'v2')""",
            (revision_id, episode_id, revision_no, _json(items), _json(snapshot), revision_hash, status, now, now, actor),
        )
        for item in items:
            connection.execute(
                """INSERT INTO timeline_items
                (id,timeline_revision_id,track_type,media_version_id,start_us,end_us,parameters_json)
                VALUES (?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), revision_id, item["track_type"], item["media_version_id"], item["start_us"], item["end_us"], _json(item["parameters"])),
            )
        return {"id": revision_id, "episode_id": episode_id, "revision_no": revision_no, "status": status, "revision_hash": revision_hash}

    @staticmethod
    def _audit_and_event(connection: sqlite3.Connection, actor: str, project_id: str, revision_id: str, episode_id: str, action: str, result: dict[str, Any]) -> None:
        connection.execute(
            """INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
            VALUES (?,'editor',?,'timeline_revision',?,?,?)""",
            (actor, action, revision_id, action, _json(result)),
        )
        connection.execute(
            "INSERT INTO outbox_events (type,project_id,subject_type,subject_id,payload_json) VALUES ('TimelineRevisionChanged',?,'EPISODE',?,?)",
            (project_id, episode_id, _json(result)),
        )

    def create_draft(self, episode_id: str, command: dict[str, Any], *, actor: str) -> dict[str, Any]:
        key, scope = str(command["idempotency_key"]).strip(), f"timeline-draft:create:{episode_id}"
        payload_hash = _digest({name: value for name, value in command.items() if name != "idempotency_key"})
        now = _now()
        with self.database.transaction() as connection:
            replay = self._idempotent_replay(connection, scope, key, payload_hash)
            if replay is not None:
                return replay
            episode = self._episode(connection, episode_id)
            self._validate_latest(connection, episode_id, command.get("expected_latest_revision_id"))
            upstream = self._upstream(connection, episode_id)
            if str(command["expected_upstream_fingerprint"]) != upstream["fingerprint"]:
                raise DomainRuleError("TIMELINE_UPSTREAM_CONFLICT", "镜头采用、声音或字幕已变化，请刷新后重试")
            expected_video = {str(item["shot_id"]): item for item in upstream["video"]}
            clips = command["clips"]
            if len(clips) != len(expected_video) or {str(item["shot_id"]) for item in clips} != set(expected_video):
                raise DomainRuleError("TIMELINE_CLIP_SET_INVALID", "时间线必须包含本集全部有效镜头，不能静默遗漏")
            items: list[dict[str, Any]] = []
            cursor = 0
            shot_starts: dict[str, int] = {}
            for index, clip in enumerate(clips):
                source = expected_video[str(clip["shot_id"])]
                if not source["media_version_id"] or str(clip["media_version_id"]) != str(source["media_version_id"]):
                    raise DomainRuleError("TIMELINE_VIDEO_SELECTION_CONFLICT", f"{source['shot_code']} 的采用视频已变化")
                if index == 0 and clip["transition_in"] != "CUT":
                    raise DomainRuleError("TIMELINE_FIRST_TRANSITION_INVALID", "第一个镜头必须使用硬切起始")
                source_duration_us = int(source.get("source_duration_ms") or 0) * 1000
                if source_duration_us and int(clip["source_start_us"]) >= source_duration_us:
                    raise DomainRuleError("TIMELINE_TRIM_SOURCE_INVALID", f"{source['shot_code']} 的入点超出源视频时长")
                duration = int(clip["duration_us"])
                shot_starts[str(clip["shot_id"])] = cursor
                items.append({"track_type": "VIDEO", "media_version_id": str(clip["media_version_id"]), "start_us": cursor, "end_us": cursor + duration, "parameters": {"shot_id": str(clip["shot_id"]), "shot_code": str(source["shot_code"]), "source_start_us": int(clip["source_start_us"]), "transition_in": str(clip["transition_in"])}})
                cursor += duration
            if command["include_dialogue"]:
                offsets: dict[str, int] = {}
                for line in upstream["dialogue"]:
                    shot_id = str(line.get("shot_id") or "")
                    if not line.get("media_version_id") or line["selection_stale"] or shot_id not in shot_starts:
                        continue
                    duration = max(100_000, int(line.get("duration_ms") or 0) * 1000)
                    start = shot_starts[shot_id] + offsets.get(shot_id, 0)
                    offsets[shot_id] = offsets.get(shot_id, 0) + duration
                    items.append({"track_type": "DIALOGUE", "media_version_id": str(line["media_version_id"]), "start_us": start, "end_us": start + duration, "parameters": {"dialogue_line_id": str(line["id"]), "shot_id": shot_id, "tts_candidate_id": line["tts_candidate_id"], "gain_db": 0.0}})
            if command["include_music_and_sfx"]:
                for track in upstream["audio"]:
                    items.append({"track_type": str(track["track_type"]), "media_version_id": str(track["media_version_id"]), "start_us": int(track["start_us"]), "end_us": int(track["end_us"]), "parameters": {"audio_binding_id": str(track["id"]), "gain_db": float(track["gain_db"]), "loop_enabled": bool(track["loop_enabled"]), "fade_in_us": int(track["fade_in_us"]), "fade_out_us": int(track["fade_out_us"])}})
            items.sort(key=lambda item: (int(item["start_us"]), str(item["track_type"]), str(item["media_version_id"])))
            assert_dialogue_timing(items)
            subtitle = upstream["subtitle"] if command["include_subtitles"] else None
            snapshot = {"schema_version": "localdrama.timeline-editor.v3", "upstream_fingerprint": upstream["fingerprint"], "audio_mix_revision": upstream["audio_mix_revision"], "subtitle_revision_id": subtitle["revision_id"] if subtitle else None, "subtitle_content_hash": subtitle["content_hash"] if subtitle else None, "include_dialogue": bool(command["include_dialogue"]), "include_music_and_sfx": bool(command["include_music_and_sfx"]), "include_subtitles": bool(command["include_subtitles"]), "requires_human_confirmation": True}
            created = self._insert_revision(connection, episode_id, items, snapshot, "DRAFT", actor, now)
            result = {**created, "outcome": "DRAFT_CREATED", "idempotent_replay": False}
            self._audit_and_event(connection, actor, str(episode["project_id"]), created["id"], episode_id, "TIMELINE_DRAFT_CREATED_V2", result)
            self._store_idempotency(connection, scope, key, payload_hash, result)
            return result

    def freeze(self, timeline_revision_id: str, command: dict[str, Any], *, actor: str) -> dict[str, Any]:
        key, scope = str(command["idempotency_key"]).strip(), f"timeline-freeze:{timeline_revision_id}"
        payload_hash = _digest({name: value for name, value in command.items() if name != "idempotency_key"})
        now = _now()
        with self.database.transaction() as connection:
            replay = self._idempotent_replay(connection, scope, key, payload_hash)
            if replay is not None:
                return replay
            source = connection.execute("SELECT * FROM timeline_revisions WHERE id=?", (timeline_revision_id,)).fetchone()
            if source is None:
                raise DomainRuleError("TIMELINE_REVISION_NOT_FOUND", "时间线版本不存在")
            if str(source["status"]) != "DRAFT":
                raise DomainRuleError("TIMELINE_DRAFT_REQUIRED", "只有最新草稿可以冻结")
            episode_id = str(source["episode_id"])
            if command["expected_latest_revision_id"] != timeline_revision_id:
                raise DomainRuleError("TIMELINE_REVISION_CONFLICT", "冻结目标与预期最新版本不一致")
            self._validate_latest(connection, episode_id, timeline_revision_id)
            upstream = self._upstream(connection, episode_id)
            snapshot = json.loads(str(source["input_snapshot_json"] or "{}"))
            expected_fingerprint = str(command["expected_upstream_fingerprint"])
            if expected_fingerprint != upstream["fingerprint"] or snapshot.get("upstream_fingerprint") != upstream["fingerprint"]:
                raise DomainRuleError("TIMELINE_UPSTREAM_CONFLICT", "冻结前上游事实已变化，请重新载入并创建草稿")
            episode = self._episode(connection, episode_id)
            items = json.loads(str(source["content_json"] or "[]"))
            assert_dialogue_timing(items)
            frozen_snapshot = {**snapshot, "schema_version": "localdrama.timeline-editor.v3", "frozen_from_timeline_revision_id": timeline_revision_id, "frozen_at": now}
            created = self._insert_revision(connection, episode_id, items, frozen_snapshot, "FROZEN", actor, now)
            result = {**created, "outcome": "FROZEN", "idempotent_replay": False}
            self._audit_and_event(connection, actor, str(episode["project_id"]), created["id"], episode_id, "TIMELINE_FROZEN_V2", result)
            self._store_idempotency(connection, scope, key, payload_hash, result)
            return result
