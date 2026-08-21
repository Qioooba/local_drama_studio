"""Apply a DRAFT_READY script-breakdown draft into real production entities.

This is the explicit human-confirmation step of the AI script-breakdown
pipeline: ``apply_draft`` materializes the LLM suggestions (scenes, shots and
dialogue lines) as genuine production rows for a target episode.  The whole
operation runs inside a **single** ``database.transaction()``: every insert
(scenes, episode_scene_ranges, shots, shot_revisions, dialogue_lines +
dialogue_text_revisions) shares one sqlite transaction so a failure anywhere
rolls back everything.  Nested service calls (``ProjectService.create_scene``,
``bind_episode_scene_range``, ``DialogueService.create_line``) are deliberately
NOT used because each opens its own ``BEGIN IMMEDIATE`` transaction; the SQL
below replicates their exact column sets and audit-event conventions instead.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database

_DIALOGUE_LINE_SPLIT = re.compile(r"\r\n|\r|\n")
_SPEAKER_TEXT = re.compile(r"^\s*([^：:]{1,120}?)\s*[：:]\s*(.+?)\s*$")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_dialogue_entries(value: Any) -> list[dict[str, str]]:
    """Normalize a shot's ``dialogue`` payload into ``[{speaker?, text}]`` rows.

    Supported shapes (the breakdown schema allows any JSON value here):

    * ``"角色名：台词"`` / ``"角色名:台词"`` — one line per entry
    * ``"台词"`` — plain text without a speaker (caller applies the fallback)
    * ``{"speaker": "...", "text": "..."}`` — structured object
    * a list mixing any of the above
    """

    entries: list[dict[str, str]] = []

    def append_raw(raw: Any) -> None:
        if raw is None:
            return
        if isinstance(raw, str):
            for line in _DIALOGUE_LINE_SPLIT.split(raw):
                stripped = line.strip()
                if not stripped:
                    continue
                match = _SPEAKER_TEXT.match(stripped)
                if match:
                    entries.append({"speaker": match.group(1).strip(), "text": match.group(2).strip()})
                else:
                    entries.append({"text": stripped})
            return
        if isinstance(raw, dict):
            speaker = raw.get("speaker")
            text = raw.get("text")
            if isinstance(text, str) and text.strip():
                entries.append(
                    {"speaker": speaker.strip() if isinstance(speaker, str) and speaker.strip() else "", "text": text.strip()}
                )
            return
        if isinstance(raw, list):
            for item in raw:
                append_raw(item)
            return
        text = str(raw).strip()
        if text:
            entries.append({"text": text})

    append_raw(value)
    return entries


class BreakdownApplyService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def apply_draft(self, draft_id: str, episode_id: str, actor: str = "local-user") -> dict[str, Any]:
        """Materialize one DRAFT_READY breakdown draft into the target episode.

        Raises ``DomainRuleError`` with the documented codes on any invalid
        precondition or write conflict; the whole transaction rolls back.
        """
        with self.database.transaction() as connection:
            draft = connection.execute("SELECT * FROM script_breakdown_drafts WHERE id=?", (draft_id,)).fetchone()
            if draft is None:
                raise DomainRuleError("BREAKDOWN_DRAFT_NOT_READY", "剧本拆解草稿不存在")
            if draft["status"] == "APPLIED":
                raise DomainRuleError("BREAKDOWN_DRAFT_ALREADY_APPLIED", "剧本拆解草稿已应用，不能重复应用")
            if draft["status"] != "DRAFT_READY":
                raise DomainRuleError(
                    "BREAKDOWN_DRAFT_NOT_READY",
                    "只有 DRAFT_READY 的剧本拆解草稿可以应用",
                    {"status": draft["status"]},
                )
            episode = connection.execute(
                "SELECT e.id,e.code,s.project_id FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE e.id=?",
                (episode_id,),
            ).fetchone()
            if episode is None:
                raise DomainRuleError("EPISODE_NOT_FOUND", "目标集不存在", {"episode_id": episode_id})
            if str(episode["project_id"]) != str(draft["project_id"]):
                raise DomainRuleError(
                    "EPISODE_PROJECT_MISMATCH",
                    "目标集必须与剧本拆解草稿属于同一项目",
                    {"draft_project_id": draft["project_id"], "episode_project_id": episode["project_id"]},
                )
            draft_payload = json.loads(str(draft["draft_json"]))
            confidence = json.loads(str(draft["confidence_json"] or "{}"))
            scenes = draft_payload.get("scenes") if isinstance(draft_payload, dict) else None
            if not isinstance(scenes, list) or not scenes:
                raise DomainRuleError("BREAKDOWN_DRAFT_NOT_READY", "剧本拆解草稿不包含任何场次建议")

            source_ranges = self._source_ranges_by_scene(confidence)
            project_id = str(draft["project_id"])
            episode_code = str(episode["code"])
            now = _now()

            created_scenes = 0
            created_shots = 0
            created_lines = 0
            dialogue_seq = int(
                connection.execute("SELECT COUNT(*) FROM dialogue_lines WHERE episode_id=?", (episode_id,)).fetchone()[0]
            )
            extracted_characters: dict[str, int] = {}

            for scene in scenes:
                scene_no = int(scene.get("scene_no", 0))
                title = str(scene.get("title") or f"第{scene_no}场").strip()
                summary = str(scene.get("summary") or "").strip()
                character_names = [str(name).strip() for name in (scene.get("characters") or []) if str(name).strip()]
                for name in character_names:
                    extracted_characters[name] = extracted_characters.get(name, 0) + 1
                scene_code = f"SC{scene_no:02d}"
                if connection.execute("SELECT 1 FROM scenes WHERE project_id=? AND code=?", (project_id, scene_code)).fetchone():
                    raise DomainRuleError("SCENE_CODE_CONFLICT", "同一项目的母本场次 code 必须唯一", {"code": scene_code})
                scene_id = str(uuid.uuid4())
                connection.execute(
                    """INSERT INTO scenes (id,project_id,code,title,location,time_of_day,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?,?,?,?,?,?,?,1,'v2')""",
                    (scene_id, project_id, scene_code, title, None, None, now, now, actor),
                )
                connection.execute(
                    """INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                    VALUES (?,'writer','MASTER_SCENE_CREATED','scene',?,'创建项目级母本场次',?)""",
                    (actor, scene_id, _json({"project_id": project_id, "code": scene_code})),
                )
                created_scenes += 1

                start, end = source_ranges.get(scene_no, (0, 1))
                if connection.execute(
                    "SELECT 1 FROM episode_scene_ranges WHERE episode_id=? AND ordinal=?", (episode_id, scene_no)
                ).fetchone():
                    raise DomainRuleError("SCENE_RANGE_ORDINAL_CONFLICT", "当前分集的场次顺序已被占用", {"ordinal": scene_no})
                range_id = str(uuid.uuid4())
                connection.execute(
                    """INSERT INTO episode_scene_ranges
                    (id,episode_id,scene_id,ordinal,source_start,source_end,source_label,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?,?,?,?,?,?,?,?,1,'v2')""",
                    (range_id, episode_id, scene_id, scene_no, start, end, f"第{scene_no}场", now, now, actor),
                )
                connection.execute(
                    """INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                    VALUES (?,'writer','EPISODE_SCENE_RANGE_BOUND','episode_scene_range',?,'关联分集与项目级母本场次',?)""",
                    (actor, range_id, _json({"episode_id": episode_id, "scene_id": scene_id, "ordinal": scene_no, "source_start": start, "source_end": end})),
                )

                for shot in scene.get("shots") or []:
                    shot_no = int(shot.get("shot_no", 0))
                    duration_ms = int(float(shot.get("duration_seconds", 0)) * 1000)
                    if duration_ms <= 0:
                        raise DomainRuleError("INVALID_TARGET_DURATION", "target_duration_ms 必须大于 0")
                    shot_code = f"{episode_code}-{scene_no:02d}-{shot_no:02d}"
                    if connection.execute("SELECT 1 FROM shots WHERE episode_id=? AND code=?", (episode_id, shot_code)).fetchone():
                        raise DomainRuleError("SHOT_CODE_CONFLICT", "当前集镜头编号冲突", {"code": shot_code})
                    maximum = connection.execute(
                        "SELECT COALESCE(MAX(CAST(order_key AS REAL)),0) FROM shots WHERE episode_id=?", (episode_id,)
                    ).fetchone()[0]
                    shot_id = str(uuid.uuid4())
                    revision_id = str(uuid.uuid4())
                    fields = {
                        "visual": str(shot.get("visual") or "").strip(),
                        "action": str(shot.get("action") or "").strip(),
                        "dialogue": shot.get("dialogue", ""),
                        "summary": summary,
                    }
                    connection.execute(
                        """INSERT INTO shots (id, episode_id, code, order_key, target_duration_ms, shot_type, status,
                        current_revision_id, created_at, updated_at, created_by, revision, schema_version)
                        VALUES (?, ?, ?, ?, ?, 'STANDARD', 'DRAFT', ?, ?, ?, ?, 1, 'v2')""",
                        (shot_id, episode_id, shot_code, str(float(maximum) + 1), duration_ms, revision_id, now, now, actor),
                    )
                    connection.execute(
                        """INSERT INTO shot_revisions (id, shot_id, revision_no, fields_json, is_frozen, created_at, updated_at, created_by)
                        VALUES (?, ?, 1, ?, 0, ?, ?, ?)""",
                        (revision_id, shot_id, _json(fields), now, now, actor),
                    )
                    created_shots += 1

                    fallback_speaker = character_names[0] if character_names else "旁白"
                    for entry in _parse_dialogue_entries(shot.get("dialogue")):
                        speaker = (entry.get("speaker") or fallback_speaker).strip()
                        text = entry.get("text", "").strip()
                        if not text:
                            continue
                        dialogue_seq += 1
                        line_id = str(uuid.uuid4())
                        text_revision_id = str(uuid.uuid4())
                        pronunciation: dict[str, Any] = {}
                        text_hash = hashlib.sha256(
                            _json({"text": text, "pronunciation": pronunciation}).encode("utf-8")
                        ).hexdigest()
                        connection.execute(
                            """INSERT INTO dialogue_lines
                            (id,episode_id,shot_id,code,speaker,created_at,updated_at,created_by,revision,schema_version)
                            VALUES (?,?,?,?,?,?,?,?,1,'v2')""",
                            (line_id, episode_id, shot_id, f"AI-DL-{dialogue_seq:04d}", speaker, now, now, actor),
                        )
                        connection.execute(
                            """INSERT INTO dialogue_text_revisions
                            (id,dialogue_line_id,revision_no,text,pronunciation_json,text_hash,created_at,updated_at,created_by,revision,schema_version)
                            VALUES (?,?,1,?,?,?,?,?,?,1,'v2')""",
                            (text_revision_id, line_id, text, _json(pronunciation), text_hash, now, now, actor),
                        )
                        connection.execute(
                            """INSERT INTO audit_events
                            (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                            VALUES (?,'audio_editor','DIALOGUE_LINE_CREATED','dialogue_line',?,'创建对白与不可变文本 revision',?)""",
                            (actor, line_id, _json({"text_revision_id": text_revision_id, "text_hash": text_hash})),
                        )
                        created_lines += 1

            flipped = connection.execute(
                "UPDATE script_breakdown_drafts SET status='APPLIED', updated_at=?, revision=revision+1 WHERE id=? AND status='DRAFT_READY'",
                (now, draft_id),
            )
            if flipped.rowcount != 1:
                raise DomainRuleError("BREAKDOWN_DRAFT_APPLY_CONFLICT", "剧本拆解草稿应用冲突，请重新读取草稿状态")

            extracted = [
                {"name": name, "scene_count": count}
                for name, count in sorted(extracted_characters.items(), key=lambda item: (-item[1], item[0]))
            ]
            for character in extracted:
                normalized_name = unicodedata.normalize("NFKC", character["name"]).strip().casefold()
                suggested = connection.execute(
                    """SELECT id FROM story_assets WHERE project_id=? AND kind='CHARACTER' AND status='ACTIVE'
                    AND lower(name)=lower(?) ORDER BY created_at,id LIMIT 1""",
                    (project_id, character["name"]),
                ).fetchone()
                proposal_id = str(uuid.uuid4())
                connection.execute(
                    """INSERT INTO story_asset_proposals
                    (id,project_id,breakdown_draft_id,proposal_key,kind,name,evidence_json,suggested_asset_id,
                     resolved_asset_id,status,decision_note,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?,?,?,?,?, ?,NULL,'PENDING','',?,?,?,1,'v2')""",
                    (proposal_id, project_id, draft_id, f"CHARACTER:{normalized_name}", "CHARACTER", character["name"],
                     _json({"scene_count": character["scene_count"], "source": "script_breakdown_draft"}),
                     suggested["id"] if suggested else None, now, now, actor),
                )
                connection.execute(
                    """INSERT INTO audit_events
                    (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                    VALUES (?,'writer','STORY_ASSET_PROPOSAL_CREATED','story_asset_proposal',?,'AI 提取角色形成待审核资产建议',?)""",
                    (actor, proposal_id, _json({"draft_id": draft_id, "suggested_asset_id": suggested["id"] if suggested else None})),
                )
            created = {"scenes": created_scenes, "shots": created_shots, "lines": created_lines}
            connection.execute(
                """INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES (?,'writer','SCRIPT_BREAKDOWN_APPLIED','script_breakdown_draft',?,'人工确认后将剧本拆解草稿应用为生产实体',?)""",
                (actor, draft_id, _json({"episode_id": episode_id, "created": created, "extracted_characters": extracted})),
            )

        return {
            "draft_id": draft_id,
            "episode_id": episode_id,
            "created": created,
            "extracted_characters": extracted,
            "applied": True,
        }

    @staticmethod
    def _source_ranges_by_scene(confidence: dict[str, Any]) -> dict[int, tuple[int, int]]:
        """Recover verified source offsets for each scene from the evidence.

        ``confidence_json`` carries ``source_passages`` (``scene_no``,
        ``quote``, ``source_start``, ``source_end``) written by the breakdown
        validator.  Drafts without them fall back to ``(0, 1)`` because
        ``episode_scene_ranges`` requires ``source_end > source_start``.
        """
        ranges: dict[int, tuple[int, int]] = {}
        passages = confidence.get("source_passages") if isinstance(confidence, dict) else None
        if isinstance(passages, list):
            for passage in passages:
                if not isinstance(passage, dict):
                    continue
                scene_no = passage.get("scene_no")
                start = passage.get("source_start")
                end = passage.get("source_end")
                if isinstance(scene_no, int) and isinstance(start, int) and isinstance(end, int) and end > start >= 0:
                    ranges[scene_no] = (start, end)
        return ranges
