from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database

VALID_KINDS = {"BEAT", "DIALOGUE", "ACTION", "MONTAGE", "CUSTOM"}


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(value: str | None) -> dict[str, Any]:
    try:
        decoded = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


class ShotGroupService:
    """Episode-local semantic scene and shot grouping commands/read model."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def workspace(self, episode_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            episode = self._episode(connection, episode_id)
            scene_rows = connection.execute(
                """SELECT id, code, title, revision
                FROM scenes WHERE project_id=?
                ORDER BY code, title, id""",
                (episode["project_id"],),
            ).fetchall()
            shot_rows = connection.execute(
                "SELECT * FROM shots WHERE episode_id=? ORDER BY order_key, code, id", (episode_id,),
            ).fetchall()
            group_rows = connection.execute(
                "SELECT * FROM shot_groups WHERE episode_id=? ORDER BY order_key, code, id", (episode_id,),
            ).fetchall()
            member_rows = connection.execute(
                """SELECT m.group_id, m.shot_id, m.order_key
                FROM shot_group_members m JOIN shot_groups g ON g.id=m.group_id
                WHERE g.episode_id=? ORDER BY m.group_id, m.order_key, m.shot_id""",
                (episode_id,),
            ).fetchall()

        memberships: dict[str, list[dict[str, str]]] = {}
        active_group_by_shot: dict[str, str] = {}
        active_ids = {str(row["id"]) for row in group_rows if row["status"] == "ACTIVE"}
        for row in member_rows:
            item = {"shot_id": str(row["shot_id"]), "order_key": str(row["order_key"])}
            memberships.setdefault(str(row["group_id"]), []).append(item)
            if str(row["group_id"]) in active_ids:
                active_group_by_shot[str(row["shot_id"])] = str(row["group_id"])

        return {
            "episode": {key: episode[key] for key in ("id", "code", "title", "project_id")},
            "scenes": [dict(row) for row in scene_rows],
            "shots": [self._shot_dict(row, active_group_by_shot.get(str(row["id"]))) for row in shot_rows],
            "groups": [self._group_dict(row, memberships.get(str(row["id"]), [])) for row in group_rows],
        }

    def assign_scene(
        self, *, shot_id: str, scene_id: str | None, expected_revision: int, actor: str = "local-user",
    ) -> dict[str, Any]:
        with self.database.transaction() as connection:
            shot = connection.execute(
                """SELECT sh.*, se.project_id FROM shots sh JOIN episodes e ON e.id=sh.episode_id
                JOIN seasons se ON se.id=e.season_id WHERE sh.id=?""", (shot_id,),
            ).fetchone()
            if not shot:
                raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在", {"shot_id": shot_id})
            if int(shot["revision"]) != expected_revision:
                raise DomainRuleError("SHOT_GROUP_REVISION_CONFLICT", "镜头已被其他操作更新，请刷新后重试")
            if scene_id is not None:
                self._scene_in_project(connection, scene_id, str(shot["project_id"]))
            now = _now()
            connection.execute(
                "UPDATE shots SET scene_id=?, revision=revision+1, updated_at=? WHERE id=?",
                (scene_id, now, shot_id),
            )
            self._audit(connection, actor, "SHOT_SCENE_ASSIGNED", "shot", shot_id, {"scene_id": scene_id})
            updated = connection.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone()
        return self._shot_dict(updated, None)

    def create_group(
        self, *, episode_id: str, kind: str, code: str, title: str, scene_id: str | None,
        metadata: dict[str, Any], order_key: str | None, actor: str = "local-user",
    ) -> dict[str, Any]:
        self._validate_group(kind=kind, code=code, title=title)
        with self.database.transaction() as connection:
            episode = self._episode(connection, episode_id)
            if scene_id is not None:
                self._scene_in_project(connection, scene_id, str(episode["project_id"]))
            if order_key is None:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM shot_groups WHERE episode_id=?", (episode_id,),
                ).fetchone()
                order_key = f"{int(row['count']) + 1:04d}"
            group_id, now = str(uuid.uuid4()), _now()
            try:
                connection.execute(
                    """INSERT INTO shot_groups
                    (id,episode_id,scene_id,kind,code,title,order_key,metadata_json,status,
                    created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?,?,?,?,?,?, 'ACTIVE',?,?,?,1,'v1')""",
                    (group_id, episode_id, scene_id, kind, code, title, order_key, _json(metadata), now, now, actor),
                )
            except sqlite3.IntegrityError as error:
                raise DomainRuleError("SHOT_GROUP_CODE_EXISTS", "当前分集已存在相同分组 code", {"code": code}) from error
            self._audit(connection, actor, "SHOT_GROUP_CREATED", "shot_group", group_id, {"episode_id": episode_id})
            row = connection.execute("SELECT * FROM shot_groups WHERE id=?", (group_id,)).fetchone()
        return self._group_dict(row, [])

    def update_group(
        self, *, group_id: str, expected_revision: int, kind: str, code: str, title: str,
        scene_id: str | None, metadata: dict[str, Any], order_key: str, actor: str = "local-user",
    ) -> dict[str, Any]:
        self._validate_group(kind=kind, code=code, title=title)
        with self.database.transaction() as connection:
            group = self._group(connection, group_id)
            self._expect_revision(group, expected_revision)
            episode = self._episode(connection, str(group["episode_id"]))
            if scene_id is not None:
                self._scene_in_project(connection, scene_id, str(episode["project_id"]))
            try:
                connection.execute(
                    """UPDATE shot_groups SET scene_id=?,kind=?,code=?,title=?,order_key=?,metadata_json=?,
                    updated_at=?,revision=revision+1 WHERE id=?""",
                    (scene_id, kind, code, title, order_key, _json(metadata), _now(), group_id),
                )
            except sqlite3.IntegrityError as error:
                raise DomainRuleError("SHOT_GROUP_CODE_EXISTS", "当前分集已存在相同分组 code", {"code": code}) from error
            self._audit(connection, actor, "SHOT_GROUP_UPDATED", "shot_group", group_id, {})
            row = connection.execute("SELECT * FROM shot_groups WHERE id=?", (group_id,)).fetchone()
            members = self._members(connection, group_id)
        return self._group_dict(row, members)

    def archive_group(
        self, *, group_id: str, expected_revision: int, actor: str = "local-user",
    ) -> dict[str, Any]:
        with self.database.transaction() as connection:
            group = self._group(connection, group_id)
            self._expect_revision(group, expected_revision)
            connection.execute(
                "UPDATE shot_groups SET status='ARCHIVED',updated_at=?,revision=revision+1 WHERE id=?",
                (_now(), group_id),
            )
            self._audit(connection, actor, "SHOT_GROUP_ARCHIVED", "shot_group", group_id, {})
            row = connection.execute("SELECT * FROM shot_groups WHERE id=?", (group_id,)).fetchone()
            members = self._members(connection, group_id)
        return self._group_dict(row, members)

    def replace_members(
        self, *, group_id: str, shot_ids: list[str], expected_revision: int, actor: str = "local-user",
    ) -> dict[str, Any]:
        if len(set(shot_ids)) != len(shot_ids):
            raise DomainRuleError("SHOT_GROUP_DUPLICATE_MEMBER", "同一镜头不能在分组中重复出现")
        with self.database.transaction() as connection:
            group = self._group(connection, group_id)
            self._expect_revision(group, expected_revision)
            if group["status"] != "ACTIVE":
                raise DomainRuleError("SHOT_GROUP_ARCHIVED", "已归档分组不可再修改成员")
            if shot_ids:
                placeholders = ",".join("?" for _ in shot_ids)
                rows = connection.execute(
                    f"SELECT id,episode_id FROM shots WHERE id IN ({placeholders})", tuple(shot_ids),
                ).fetchall()
                found = {str(row["id"]): str(row["episode_id"]) for row in rows}
                invalid = [shot_id for shot_id in shot_ids if found.get(shot_id) != group["episode_id"]]
                if invalid:
                    raise DomainRuleError("SHOT_GROUP_MEMBER_SCOPE_INVALID", "分组成员必须属于同一分集", {"shot_ids": invalid})
                # A shot has one active navigation group. Archived memberships remain as history.
                connection.execute(
                    f"""DELETE FROM shot_group_members WHERE shot_id IN ({placeholders}) AND group_id IN
                    (SELECT id FROM shot_groups WHERE status='ACTIVE')""", tuple(shot_ids),
                )
            connection.execute("DELETE FROM shot_group_members WHERE group_id=?", (group_id,))
            now = _now()
            connection.executemany(
                "INSERT INTO shot_group_members (group_id,shot_id,order_key,created_at,created_by) VALUES (?,?,?,?,?)",
                [(group_id, shot_id, f"{index:04d}", now, actor) for index, shot_id in enumerate(shot_ids, 1)],
            )
            connection.execute(
                "UPDATE shot_groups SET updated_at=?,revision=revision+1 WHERE id=?", (now, group_id),
            )
            self._audit(connection, actor, "SHOT_GROUP_MEMBERS_REPLACED", "shot_group", group_id, {"shot_ids": shot_ids})
            row = connection.execute("SELECT * FROM shot_groups WHERE id=?", (group_id,)).fetchone()
            members = self._members(connection, group_id)
        return self._group_dict(row, members)

    def reorder_groups(
        self, *, episode_id: str, items: list[dict[str, Any]], actor: str = "local-user",
    ) -> list[dict[str, Any]]:
        if len({str(item["group_id"]) for item in items}) != len(items):
            raise DomainRuleError("SHOT_GROUP_REORDER_INVALID", "分组排序列表包含重复项")
        with self.database.transaction() as connection:
            self._episode(connection, episode_id)
            for item in items:
                group = self._group(connection, str(item["group_id"]))
                if group["episode_id"] != episode_id:
                    raise DomainRuleError("SHOT_GROUP_SCOPE_INVALID", "只能排序当前分集的分组")
                self._expect_revision(group, int(item["expected_revision"]))
            now = _now()
            for item in items:
                connection.execute(
                    "UPDATE shot_groups SET order_key=?,updated_at=?,revision=revision+1 WHERE id=?",
                    (str(item["order_key"]), now, str(item["group_id"])),
                )
            self._audit(connection, actor, "SHOT_GROUPS_REORDERED", "episode", episode_id, {"count": len(items)})
            rows = connection.execute(
                "SELECT * FROM shot_groups WHERE episode_id=? ORDER BY order_key,code,id", (episode_id,),
            ).fetchall()
            return [self._group_dict(row, self._members(connection, str(row["id"]))) for row in rows]

    @staticmethod
    def _episode(connection: sqlite3.Connection, episode_id: str) -> sqlite3.Row:
        row = connection.execute(
            """SELECT e.id,e.code,e.title,se.project_id FROM episodes e
            JOIN seasons se ON se.id=e.season_id WHERE e.id=?""", (episode_id,),
        ).fetchone()
        if not row:
            raise DomainRuleError("EPISODE_NOT_FOUND", "分集不存在", {"episode_id": episode_id})
        return row

    @staticmethod
    def _group(connection: sqlite3.Connection, group_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM shot_groups WHERE id=?", (group_id,)).fetchone()
        if not row:
            raise DomainRuleError("SHOT_GROUP_NOT_FOUND", "镜头分组不存在", {"group_id": group_id})
        return row

    @staticmethod
    def _scene_in_project(connection: sqlite3.Connection, scene_id: str, project_id: str) -> None:
        row = connection.execute("SELECT project_id FROM scenes WHERE id=?", (scene_id,)).fetchone()
        if not row:
            raise DomainRuleError("SCENE_NOT_FOUND", "场景不存在", {"scene_id": scene_id})
        if row["project_id"] != project_id:
            raise DomainRuleError("SHOT_SCENE_SCOPE_INVALID", "场景与镜头必须属于同一项目")

    @staticmethod
    def _expect_revision(group: sqlite3.Row, expected: int) -> None:
        if int(group["revision"]) != expected:
            raise DomainRuleError("SHOT_GROUP_REVISION_CONFLICT", "镜头分组已被其他操作更新，请刷新后重试")

    @staticmethod
    def _validate_group(*, kind: str, code: str, title: str) -> None:
        if kind not in VALID_KINDS:
            raise DomainRuleError("SHOT_GROUP_KIND_INVALID", "不支持的镜头分组类型")
        if not code.strip() or len(code) > 80:
            raise DomainRuleError("SHOT_GROUP_CODE_INVALID", "分组 code 必须为 1—80 个字符")
        if not title.strip() or len(title) > 200:
            raise DomainRuleError("SHOT_GROUP_TITLE_INVALID", "分组标题必须为 1—200 个字符")

    @staticmethod
    def _members(connection: sqlite3.Connection, group_id: str) -> list[dict[str, str]]:
        return [dict(row) for row in connection.execute(
            "SELECT shot_id,order_key FROM shot_group_members WHERE group_id=? ORDER BY order_key,shot_id", (group_id,),
        ).fetchall()]

    @staticmethod
    def _group_dict(row: sqlite3.Row, members: list[dict[str, str]]) -> dict[str, Any]:
        value = dict(row)
        value["metadata"] = _decode(value.pop("metadata_json", "{}"))
        value["members"] = members
        return value

    @staticmethod
    def _shot_dict(row: sqlite3.Row, group_id: str | None) -> dict[str, Any]:
        value = dict(row)
        value["group_id"] = group_id
        return value

    @staticmethod
    def _audit(
        connection: sqlite3.Connection, actor: str, action: str, subject_type: str,
        subject_id: str, metadata: dict[str, Any],
    ) -> None:
        connection.execute(
            """INSERT INTO audit_events
            (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
            VALUES (?,'director',?,?,?,?,?)""",
            (actor, action, subject_type, subject_id, action, _json(metadata)),
        )
