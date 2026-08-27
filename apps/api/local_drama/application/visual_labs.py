from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.api.schemas.variants import VariantPlanRequest
from local_drama.application.generation import GenerationService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


DEFAULT_PORTS: dict[str, dict[str, dict[str, str]]] = {
    "TEXT_REF": {"inputs": {}, "outputs": {"text": "TEXT"}},
    "STORY_ASSET_REF": {"inputs": {}, "outputs": {"asset": "STORY_ASSET", "guide": "IMAGE"}},
    "MEDIA_REF": {"inputs": {}, "outputs": {"media": "ANY_MEDIA"}},
    "SHOT_REF": {"inputs": {}, "outputs": {"context": "SHOT_CONTEXT"}},
    "GENERATION_INTENT": {"inputs": {"prompt": "TEXT", "first_frame": "IMAGE", "last_frame": "IMAGE", "reference": "ANY_MEDIA", "shot": "SHOT_CONTEXT"}, "outputs": {"candidate": "ANY_MEDIA"}},
    "TRANSFORM_INTENT": {"inputs": {"source": "ANY_MEDIA", "mask": "MASK", "prompt": "TEXT"}, "outputs": {"result": "ANY_MEDIA"}},
    "COMPARE_SET": {"inputs": {"candidate": "ANY_MEDIA"}, "outputs": {"winner": "ANY_MEDIA"}},
    "SEQUENCE_PREVIEW": {"inputs": {"item": "ANY_MEDIA", "audio": "AUDIO"}, "outputs": {"sequence": "SEQUENCE"}},
    "OUTPUT_DRAFT": {"inputs": {"media": "ANY_MEDIA", "sequence": "SEQUENCE"}, "outputs": {"output": "ANY_MEDIA"}},
    "NOTE": {"inputs": {}, "outputs": {}},
    "FRAME": {"inputs": {}, "outputs": {}},
}


class VisualLabService:
    def __init__(self, database: Database, settings: Any) -> None:
        self.database = database
        self.settings = settings

    def list(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT d.*,(SELECT COUNT(*) FROM visual_lab_nodes n WHERE n.document_id=d.id) node_count,
                (SELECT COUNT(*) FROM visual_lab_edges e WHERE e.document_id=d.id) edge_count
                FROM visual_lab_documents d WHERE d.project_id=? AND d.status='ACTIVE' ORDER BY d.updated_at DESC""",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create(self, project_id: str, code: str, title: str, episode_id: str | None, actor: str = "local-user") -> dict[str, Any]:
        now, document_id = _now(), str(uuid.uuid4())
        with self.database.transaction() as connection:
            project = connection.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
            if project is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
            if episode_id:
                episode = connection.execute("SELECT se.project_id FROM episodes e JOIN seasons se ON se.id=e.season_id WHERE e.id=?", (episode_id,)).fetchone()
                if episode is None or str(episode["project_id"]) != project_id:
                    raise DomainRuleError("VISUAL_LAB_EPISODE_SCOPE_INVALID", "分集不属于当前项目")
            try:
                connection.execute(
                    """INSERT INTO visual_lab_documents
                    (id,project_id,episode_id,code,title,status,topology_revision,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?,?,?,'ACTIVE',1,?,?,?,1,'v1')""",
                    (document_id, project_id, episode_id, code, title, now, now, actor),
                )
            except Exception as error:
                if "UNIQUE" in str(error).upper():
                    raise DomainRuleError("VISUAL_LAB_CODE_CONFLICT", "项目内画布代码已存在") from error
                raise
        return self.get(document_id)["document"]

    def get(self, document_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            document = connection.execute("SELECT * FROM visual_lab_documents WHERE id=?", (document_id,)).fetchone()
            if document is None:
                raise DomainRuleError("VISUAL_LAB_NOT_FOUND", "Visual Lab 不存在")
            nodes = connection.execute(
                """SELECT n.*,r.revision_no AS content_revision_no,r.content_json,r.content_hash
                FROM visual_lab_nodes n JOIN visual_lab_node_revisions r ON r.id=n.current_content_revision_id
                WHERE n.document_id=? ORDER BY n.z_index,n.created_at""",
                (document_id,),
            ).fetchall()
            edges = connection.execute("SELECT * FROM visual_lab_edges WHERE document_id=? ORDER BY created_at", (document_id,)).fetchall()
        node_payload = []
        for row in nodes:
            item = dict(row)
            item["content"] = json.loads(item.pop("content_json"))
            node_payload.append(item)
        edge_payload = []
        for row in edges:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            edge_payload.append(item)
        document_payload = dict(document)
        document_payload["viewport"] = json.loads(document_payload.pop("viewport_json") or '{"x":0,"y":0,"zoom":1}')
        return {"document": document_payload, "nodes": node_payload, "edges": edge_payload}

    @staticmethod
    def _validate_content(kind: str, content: dict[str, Any]) -> dict[str, Any]:
        if kind not in DEFAULT_PORTS:
            raise DomainRuleError("VISUAL_LAB_NODE_KIND_UNSUPPORTED", "不支持的 Visual Lab 节点")
        normalized = {**content}
        normalized.setdefault("title", kind.replace("_", " ").title())
        normalized.setdefault("ports", DEFAULT_PORTS[kind])
        if kind in {"STORY_ASSET_REF", "MEDIA_REF", "SHOT_REF"} and not normalized.get("reference_id"):
            raise DomainRuleError("VISUAL_LAB_REFERENCE_REQUIRED", "引用节点必须绑定精确对象")
        if kind == "GENERATION_INTENT" and not normalized.get("intent_id"):
            raise DomainRuleError("VISUAL_LAB_INTENT_REQUIRED", "生成节点必须绑定 GenerationIntent")
        return normalized

    def add_node(self, document_id: str, payload: dict[str, Any], actor: str = "local-user") -> dict[str, Any]:
        now, node_id, revision_id = _now(), str(uuid.uuid4()), str(uuid.uuid4())
        content = self._validate_content(str(payload["node_kind"]), dict(payload["content"]))
        with self.database.transaction() as connection:
            document = connection.execute("SELECT * FROM visual_lab_documents WHERE id=?", (document_id,)).fetchone()
            if document is None:
                raise DomainRuleError("VISUAL_LAB_NOT_FOUND", "Visual Lab 不存在")
            if int(document["topology_revision"]) != int(payload["expected_topology_revision"]):
                raise DomainRuleError("REVISION_CONFLICT", "Visual Lab 结构已变化", {"current_revision": document["topology_revision"]})
            connection.execute(
                """INSERT INTO visual_lab_nodes
                (id,document_id,node_kind,current_content_revision_id,position_x,position_y,width,height,z_index,collapsed,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,NULL,?,?,?,?,?,0,?,?,?,1,'v1')""",
                (node_id, document_id, payload["node_kind"], payload["position_x"], payload["position_y"], payload["width"], payload["height"], payload.get("z_index", 0), now, now, actor),
            )
            connection.execute(
                """INSERT INTO visual_lab_node_revisions
                (id,node_id,revision_no,parent_revision_id,content_json,content_hash,change_note,created_at,created_by,schema_version)
                VALUES (?,?,1,NULL,?,?,?, ?,?,'v1')""",
                (revision_id, node_id, _canonical(content), _hash(content), "创建节点", now, actor),
            )
            connection.execute("UPDATE visual_lab_nodes SET current_content_revision_id=? WHERE id=?", (revision_id, node_id))
            connection.execute("UPDATE visual_lab_documents SET topology_revision=topology_revision+1,updated_at=?,revision=revision+1 WHERE id=?", (now, document_id))
        return next(item for item in self.get(document_id)["nodes"] if item["id"] == node_id)

    def revise_node(self, node_id: str, content: dict[str, Any], change_note: str, expected_revision: int, actor: str = "local-user") -> dict[str, Any]:
        now, revision_id = _now(), str(uuid.uuid4())
        with self.database.transaction() as connection:
            node = connection.execute("SELECT * FROM visual_lab_nodes WHERE id=?", (node_id,)).fetchone()
            if node is None:
                raise DomainRuleError("VISUAL_LAB_NODE_NOT_FOUND", "节点不存在")
            if int(node["revision"]) != expected_revision:
                raise DomainRuleError("REVISION_CONFLICT", "节点已被修改", {"current_revision": node["revision"]})
            normalized = self._validate_content(str(node["node_kind"]), content)
            prior = connection.execute("SELECT revision_no FROM visual_lab_node_revisions WHERE id=?", (node["current_content_revision_id"],)).fetchone()
            connection.execute(
                """INSERT INTO visual_lab_node_revisions
                (id,node_id,revision_no,parent_revision_id,content_json,content_hash,change_note,created_at,created_by,schema_version)
                VALUES (?,?,?,?,?,?,?,?,?,'v1')""",
                (revision_id, node_id, int(prior["revision_no"]) + 1, node["current_content_revision_id"], _canonical(normalized), _hash(normalized), change_note, now, actor),
            )
            connection.execute("UPDATE visual_lab_nodes SET current_content_revision_id=?,updated_at=?,revision=revision+1 WHERE id=?", (revision_id, now, node_id))
        return next(item for item in self.get(str(node["document_id"]))["nodes"] if item["id"] == node_id)

    def move_nodes(self, document_id: str, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = _now()
        with self.database.transaction() as connection:
            for item in nodes:
                result = connection.execute(
                    """UPDATE visual_lab_nodes SET position_x=?,position_y=?,width=COALESCE(?,width),height=COALESCE(?,height),
                    z_index=COALESCE(?,z_index),collapsed=COALESCE(?,collapsed),updated_at=?,revision=revision+1
                    WHERE id=? AND document_id=? AND revision=?""",
                    (item["position_x"], item["position_y"], item.get("width"), item.get("height"), item.get("z_index"), int(item["collapsed"]) if item.get("collapsed") is not None else None, now, item["id"], document_id, item["expected_revision"]),
                )
                if result.rowcount != 1:
                    raise DomainRuleError("REVISION_CONFLICT", "节点位置已被修改", {"node_id": item["id"]})
            connection.execute("UPDATE visual_lab_documents SET updated_at=?,revision=revision+1 WHERE id=?", (now, document_id))
        return self.get(document_id)["nodes"]

    def save_viewport(self, document_id: str, viewport: dict[str, float]) -> dict[str, float]:
        now = _now()
        normalized = {"x": float(viewport["x"]), "y": float(viewport["y"]), "zoom": float(viewport["zoom"])}
        with self.database.transaction() as connection:
            result = connection.execute(
                "UPDATE visual_lab_documents SET viewport_json=?,updated_at=?,revision=revision+1 WHERE id=?",
                (_canonical(normalized), now, document_id),
            )
            if result.rowcount != 1:
                raise DomainRuleError("VISUAL_LAB_NOT_FOUND", "Visual Lab 不存在")
        return normalized

    def duplicate_nodes(self, document_id: str, node_ids: list[str], offset_x: float, offset_y: float, expected_topology_revision: int, actor: str = "local-user") -> dict[str, Any]:
        unique_ids = list(dict.fromkeys(node_ids))
        now = _now()
        mapping: dict[str, str] = {}
        with self.database.transaction() as connection:
            document = connection.execute("SELECT topology_revision FROM visual_lab_documents WHERE id=?", (document_id,)).fetchone()
            if document is None:
                raise DomainRuleError("VISUAL_LAB_NOT_FOUND", "Visual Lab 不存在")
            if int(document["topology_revision"]) != expected_topology_revision:
                raise DomainRuleError("REVISION_CONFLICT", "Visual Lab 结构已变化", {"current_revision": document["topology_revision"]})
            placeholders = ",".join("?" for _ in unique_ids)
            rows = connection.execute(
                f"""SELECT n.*,r.content_json FROM visual_lab_nodes n
                JOIN visual_lab_node_revisions r ON r.id=n.current_content_revision_id
                WHERE n.document_id=? AND n.id IN ({placeholders}) ORDER BY n.created_at""",
                (document_id, *unique_ids),
            ).fetchall()
            if len(rows) != len(unique_ids):
                raise DomainRuleError("VISUAL_LAB_NODE_SCOPE_INVALID", "部分待复制节点不属于当前 Visual Lab")
            for row in rows:
                source_id = str(row["id"])
                node_id, revision_id = str(uuid.uuid4()), str(uuid.uuid4())
                mapping[source_id] = node_id
                content = json.loads(str(row["content_json"]))
                content["title"] = f"{content.get('title') or row['node_kind']} 副本"
                connection.execute(
                    """INSERT INTO visual_lab_nodes
                    (id,document_id,node_kind,current_content_revision_id,position_x,position_y,width,height,z_index,collapsed,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?,NULL,?,?,?,?,?,0,?,?,?,1,'v1')""",
                    (node_id, document_id, row["node_kind"], float(row["position_x"]) + offset_x, float(row["position_y"]) + offset_y, row["width"], row["height"], int(row["z_index"]) + 1, now, now, actor),
                )
                connection.execute(
                    """INSERT INTO visual_lab_node_revisions
                    (id,node_id,revision_no,parent_revision_id,content_json,content_hash,change_note,created_at,created_by,schema_version)
                    VALUES (?,?,1,NULL,?,?,?, ?,?,'v1')""",
                    (revision_id, node_id, _canonical(content), _hash(content), "复制节点", now, actor),
                )
                connection.execute("UPDATE visual_lab_nodes SET current_content_revision_id=? WHERE id=?", (revision_id, node_id))
            edge_rows = connection.execute(
                f"""SELECT * FROM visual_lab_edges WHERE document_id=?
                AND source_node_id IN ({placeholders}) AND target_node_id IN ({placeholders})""",
                (document_id, *unique_ids, *unique_ids),
            ).fetchall()
            for edge in edge_rows:
                connection.execute(
                    """INSERT INTO visual_lab_edges
                    (id,document_id,source_node_id,source_port,target_node_id,target_port,edge_kind,metadata_json,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,1,'v1')""",
                    (str(uuid.uuid4()), document_id, mapping[str(edge["source_node_id"])], edge["source_port"], mapping[str(edge["target_node_id"])], edge["target_port"], edge["edge_kind"], edge["metadata_json"], now, now, actor),
                )
            connection.execute("UPDATE visual_lab_documents SET topology_revision=topology_revision+1,updated_at=?,revision=revision+1 WHERE id=?", (now, document_id))
        graph = self.get(document_id)
        return {"nodes": [node for node in graph["nodes"] if node["id"] in mapping.values()], "id_mapping": mapping, "topology_revision": expected_topology_revision + 1}

    def delete_nodes(self, document_id: str, node_ids: list[str], expected_topology_revision: int) -> dict[str, Any]:
        unique_ids = list(dict.fromkeys(node_ids))
        now = _now()
        with self.database.transaction() as connection:
            document = connection.execute("SELECT topology_revision FROM visual_lab_documents WHERE id=?", (document_id,)).fetchone()
            if document is None:
                raise DomainRuleError("VISUAL_LAB_NOT_FOUND", "Visual Lab 不存在")
            if int(document["topology_revision"]) != expected_topology_revision:
                raise DomainRuleError("REVISION_CONFLICT", "Visual Lab 结构已变化", {"current_revision": document["topology_revision"]})
            placeholders = ",".join("?" for _ in unique_ids)
            rows = connection.execute(f"SELECT id FROM visual_lab_nodes WHERE document_id=? AND id IN ({placeholders})", (document_id, *unique_ids)).fetchall()
            if len(rows) != len(unique_ids):
                raise DomainRuleError("VISUAL_LAB_NODE_SCOPE_INVALID", "部分待删除节点不属于当前 Visual Lab")
            promoted = int(connection.execute(f"SELECT COUNT(*) FROM visual_lab_promotions WHERE node_id IN ({placeholders})", tuple(unique_ids)).fetchone()[0])
            if promoted:
                raise DomainRuleError("VISUAL_LAB_PROMOTED_NODE_PROTECTED", "已采纳到正式生产的节点不能批量删除", {"promoted_count": promoted})
            connection.execute(f"UPDATE visual_lab_nodes SET current_content_revision_id=NULL WHERE id IN ({placeholders})", tuple(unique_ids))
            # Revision rows form a self-referencing history chain. Break only the
            # chains owned by the nodes being deleted before the node cascade runs;
            # otherwise SQLite's immediate RESTRICT check can reject valid deletes.
            connection.execute(f"UPDATE visual_lab_node_revisions SET parent_revision_id=NULL WHERE node_id IN ({placeholders})", tuple(unique_ids))
            connection.execute(f"DELETE FROM visual_lab_nodes WHERE id IN ({placeholders})", tuple(unique_ids))
            connection.execute("UPDATE visual_lab_documents SET topology_revision=topology_revision+1,updated_at=?,revision=revision+1 WHERE id=?", (now, document_id))
        return {"node_ids": unique_ids, "deleted": True, "topology_revision": expected_topology_revision + 1}

    def delete_node(self, node_id: str, expected_topology_revision: int) -> dict[str, Any]:
        now = _now()
        with self.database.transaction() as connection:
            node = connection.execute("SELECT document_id FROM visual_lab_nodes WHERE id=?", (node_id,)).fetchone()
            if node is None:
                raise DomainRuleError("VISUAL_LAB_NODE_NOT_FOUND", "节点不存在")
            document = connection.execute("SELECT topology_revision FROM visual_lab_documents WHERE id=?", (node["document_id"],)).fetchone()
            if int(document["topology_revision"]) != expected_topology_revision:
                raise DomainRuleError("REVISION_CONFLICT", "Visual Lab 结构已变化", {"current_revision": document["topology_revision"]})
            promoted = connection.execute("SELECT 1 FROM visual_lab_promotions WHERE node_id=? LIMIT 1", (node_id,)).fetchone()
            if promoted is not None:
                raise DomainRuleError("VISUAL_LAB_PROMOTED_NODE_PROTECTED", "已采纳到正式生产的节点不能删除")
            connection.execute("UPDATE visual_lab_nodes SET current_content_revision_id=NULL WHERE id=?", (node_id,))
            connection.execute("UPDATE visual_lab_node_revisions SET parent_revision_id=NULL WHERE node_id=?", (node_id,))
            connection.execute("DELETE FROM visual_lab_nodes WHERE id=?", (node_id,))
            connection.execute("UPDATE visual_lab_documents SET topology_revision=topology_revision+1,updated_at=?,revision=revision+1 WHERE id=?", (now, node["document_id"]))
        return {"node_id": node_id, "deleted": True, "topology_revision": expected_topology_revision + 1}

    @staticmethod
    def _port_type(content: dict[str, Any], side: str, name: str) -> str | None:
        ports = content.get("ports") if isinstance(content.get("ports"), dict) else {}
        mapping = ports.get(side) if isinstance(ports.get(side), dict) else {}
        return str(mapping[name]) if name in mapping else None

    def connect(self, document_id: str, payload: dict[str, Any], actor: str = "local-user") -> dict[str, Any]:
        if payload["source_node_id"] == payload["target_node_id"]:
            raise DomainRuleError("VISUAL_LAB_SELF_EDGE", "节点不能连接自身")
        now, edge_id = _now(), str(uuid.uuid4())
        with self.database.transaction() as connection:
            document = connection.execute("SELECT * FROM visual_lab_documents WHERE id=?", (document_id,)).fetchone()
            if document is None:
                raise DomainRuleError("VISUAL_LAB_NOT_FOUND", "Visual Lab 不存在")
            if int(document["topology_revision"]) != int(payload["expected_topology_revision"]):
                raise DomainRuleError("REVISION_CONFLICT", "Visual Lab 结构已变化", {"current_revision": document["topology_revision"]})
            rows = connection.execute(
                """SELECT n.id,r.content_json FROM visual_lab_nodes n JOIN visual_lab_node_revisions r ON r.id=n.current_content_revision_id
                WHERE n.document_id=? AND n.id IN (?,?)""",
                (document_id, payload["source_node_id"], payload["target_node_id"]),
            ).fetchall()
            by_id = {str(row["id"]): json.loads(row["content_json"]) for row in rows}
            if len(by_id) != 2:
                raise DomainRuleError("VISUAL_LAB_EDGE_SCOPE_INVALID", "连接节点不属于当前 Visual Lab")
            source_type = self._port_type(by_id[payload["source_node_id"]], "outputs", payload["source_port"])
            target_type = self._port_type(by_id[payload["target_node_id"]], "inputs", payload["target_port"])
            compatible = source_type == target_type or source_type == "ANY_MEDIA" and target_type in {"ANY_MEDIA", "IMAGE", "VIDEO", "AUDIO"} or target_type == "ANY_MEDIA" and source_type in {"ANY_MEDIA", "IMAGE", "VIDEO", "AUDIO"}
            if not source_type or not target_type or not compatible:
                raise DomainRuleError("VISUAL_LAB_PORT_INCOMPATIBLE", "节点端口类型不兼容", {"source_type": source_type, "target_type": target_type})
            edges = connection.execute("SELECT source_node_id,target_node_id FROM visual_lab_edges WHERE document_id=?", (document_id,)).fetchall()
            adjacency: dict[str, set[str]] = {}
            for edge in edges:
                adjacency.setdefault(str(edge["source_node_id"]), set()).add(str(edge["target_node_id"]))
            adjacency.setdefault(payload["source_node_id"], set()).add(payload["target_node_id"])
            if self._reachable(adjacency, payload["target_node_id"], payload["source_node_id"]):
                raise DomainRuleError("VISUAL_LAB_CYCLE", "连接会形成循环，无法创建")
            connection.execute(
                """INSERT INTO visual_lab_edges
                (id,document_id,source_node_id,source_port,target_node_id,target_port,edge_kind,metadata_json,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,1,'v1')""",
                (edge_id, document_id, payload["source_node_id"], payload["source_port"], payload["target_node_id"], payload["target_port"], payload["edge_kind"], _canonical(payload.get("metadata", {})), now, now, actor),
            )
            connection.execute("UPDATE visual_lab_documents SET topology_revision=topology_revision+1,updated_at=?,revision=revision+1 WHERE id=?", (now, document_id))
        return next(item for item in self.get(document_id)["edges"] if item["id"] == edge_id)

    @staticmethod
    def _reachable(adjacency: dict[str, set[str]], start: str, target: str) -> bool:
        stack, seen = [start], set()
        while stack:
            current = stack.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            stack.extend(adjacency.get(current, ()))
        return False

    def delete_edge(self, edge_id: str) -> None:
        now = _now()
        with self.database.transaction() as connection:
            edge = connection.execute("SELECT document_id FROM visual_lab_edges WHERE id=?", (edge_id,)).fetchone()
            if edge is None:
                raise DomainRuleError("VISUAL_LAB_EDGE_NOT_FOUND", "连接不存在")
            connection.execute("DELETE FROM visual_lab_edges WHERE id=?", (edge_id,))
            connection.execute("UPDATE visual_lab_documents SET topology_revision=topology_revision+1,updated_at=?,revision=revision+1 WHERE id=?", (now, edge["document_id"]))

    def snapshot(self, document_id: str, actor: str = "local-user") -> dict[str, Any]:
        graph = self.get(document_id)
        content = {"topology_revision": graph["document"]["topology_revision"], "viewport": graph["document"]["viewport"], "nodes": graph["nodes"], "edges": graph["edges"]}
        now, snapshot_id = _now(), str(uuid.uuid4())
        with self.database.transaction() as connection:
            next_no = int(connection.execute("SELECT COALESCE(MAX(snapshot_no),0)+1 FROM visual_lab_snapshots WHERE document_id=?", (document_id,)).fetchone()[0])
            connection.execute(
                "INSERT INTO visual_lab_snapshots (id,document_id,snapshot_no,content_json,content_hash,created_at,created_by,schema_version) VALUES (?,?,?,?,?,?,?,'v1')",
                (snapshot_id, document_id, next_no, _canonical(content), _hash(content), now, actor),
            )
        return {"id": snapshot_id, "document_id": document_id, "snapshot_no": next_no, "content_hash": _hash(content), "created_at": now}

    def list_snapshots(self, document_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            document = connection.execute("SELECT id FROM visual_lab_documents WHERE id=?", (document_id,)).fetchone()
            if document is None:
                raise DomainRuleError("VISUAL_LAB_NOT_FOUND", "Visual Lab 不存在")
            rows = connection.execute(
                """SELECT id,document_id,snapshot_no,content_hash,created_at,created_by
                FROM visual_lab_snapshots WHERE document_id=? ORDER BY snapshot_no DESC LIMIT ?""",
                (document_id, max(1, min(limit, 100))),
            ).fetchall()
        return [dict(row) for row in rows]

    def restore_preflight(self, snapshot_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            snapshot = connection.execute("SELECT * FROM visual_lab_snapshots WHERE id=?", (snapshot_id,)).fetchone()
            if snapshot is None:
                raise DomainRuleError("VISUAL_LAB_SNAPSHOT_NOT_FOUND", "画布快照不存在")
            content = json.loads(str(snapshot["content_json"]))
            document = connection.execute("SELECT topology_revision FROM visual_lab_documents WHERE id=?", (snapshot["document_id"],)).fetchone()
            snapshot_ids = {str(item["id"]) for item in content.get("nodes", []) if isinstance(item, dict) and item.get("id")}
            current_rows = connection.execute("SELECT id FROM visual_lab_nodes WHERE document_id=?", (snapshot["document_id"],)).fetchall()
            extra_ids = [str(row["id"]) for row in current_rows if str(row["id"]) not in snapshot_ids]
            protected = 0
            if extra_ids:
                placeholders = ",".join("?" for _ in extra_ids)
                protected = int(connection.execute(f"SELECT COUNT(*) FROM visual_lab_promotions WHERE node_id IN ({placeholders})", tuple(extra_ids)).fetchone()[0])
        snapshot_payload = {
            "snapshot_id": snapshot_id,
            "snapshot_hash": str(snapshot["content_hash"]),
            "document_id": str(snapshot["document_id"]),
            "current_topology_revision": int(document["topology_revision"]),
        }
        blockers = [] if protected == 0 else [{"code": "VISUAL_LAB_PROMOTED_NODE_PROTECTED", "message": "恢复会删除已采纳到正式生产的节点", "count": protected}]
        return {"status": "READY" if not blockers else "BLOCKED", "plan_hash": _hash(snapshot_payload), "blockers": blockers, "snapshot": snapshot_payload, "mutated": False}

    def restore_snapshot(self, snapshot_id: str, plan_hash: str, actor: str = "local-user") -> dict[str, Any]:
        plan = self.restore_preflight(snapshot_id)
        if plan["status"] != "READY":
            raise DomainRuleError("VISUAL_LAB_RESTORE_BLOCKED", "画布快照不能恢复", {"blockers": plan["blockers"]})
        if plan_hash != plan["plan_hash"]:
            raise DomainRuleError("PLAN_HASH_MISMATCH", "画布已变化，请重新确认恢复")
        now = _now()
        with self.database.transaction() as connection:
            snapshot = connection.execute("SELECT * FROM visual_lab_snapshots WHERE id=?", (snapshot_id,)).fetchone()
            content = json.loads(str(snapshot["content_json"]))
            document_id = str(snapshot["document_id"])
            document = connection.execute("SELECT topology_revision FROM visual_lab_documents WHERE id=?", (document_id,)).fetchone()
            if int(document["topology_revision"]) != int(plan["snapshot"]["current_topology_revision"]):
                raise DomainRuleError("REVISION_CONFLICT", "画布已变化，请重新确认恢复", {"current_revision": document["topology_revision"]})
            snapshot_nodes = [item for item in content.get("nodes", []) if isinstance(item, dict) and item.get("id")]
            snapshot_ids = {str(item["id"]) for item in snapshot_nodes}
            current_rows = connection.execute("SELECT id FROM visual_lab_nodes WHERE document_id=?", (document_id,)).fetchall()
            extra_ids = [str(row["id"]) for row in current_rows if str(row["id"]) not in snapshot_ids]
            connection.execute("DELETE FROM visual_lab_edges WHERE document_id=?", (document_id,))
            if extra_ids:
                placeholders = ",".join("?" for _ in extra_ids)
                connection.execute(f"UPDATE visual_lab_nodes SET current_content_revision_id=NULL WHERE id IN ({placeholders})", tuple(extra_ids))
                connection.execute(f"UPDATE visual_lab_node_revisions SET parent_revision_id=NULL WHERE node_id IN ({placeholders})", tuple(extra_ids))
                connection.execute(f"DELETE FROM visual_lab_nodes WHERE id IN ({placeholders})", tuple(extra_ids))
            existing_ids = {str(row["id"]) for row in connection.execute("SELECT id FROM visual_lab_nodes WHERE document_id=?", (document_id,)).fetchall()}
            for item in snapshot_nodes:
                node_id = str(item["id"])
                node_kind = str(item["node_kind"])
                normalized = self._validate_content(node_kind, dict(item.get("content") or {}))
                revision_id = str(uuid.uuid4())
                if node_id in existing_ids:
                    current = connection.execute("SELECT current_content_revision_id FROM visual_lab_nodes WHERE id=?", (node_id,)).fetchone()
                    next_revision = int(connection.execute("SELECT COALESCE(MAX(revision_no),0)+1 FROM visual_lab_node_revisions WHERE node_id=?", (node_id,)).fetchone()[0])
                    parent_revision_id = current["current_content_revision_id"]
                    connection.execute(
                        """INSERT INTO visual_lab_node_revisions
                        (id,node_id,revision_no,parent_revision_id,content_json,content_hash,change_note,created_at,created_by,schema_version)
                        VALUES (?,?,?,?,?,?,?,?,?,'v1')""",
                        (revision_id, node_id, next_revision, parent_revision_id, _canonical(normalized), _hash(normalized), f"恢复快照 #{snapshot['snapshot_no']}", now, actor),
                    )
                    connection.execute(
                        """UPDATE visual_lab_nodes SET node_kind=?,current_content_revision_id=?,position_x=?,position_y=?,width=?,height=?,z_index=?,collapsed=?,updated_at=?,revision=revision+1 WHERE id=?""",
                        (node_kind, revision_id, item["position_x"], item["position_y"], item["width"], item["height"], item.get("z_index", 0), item.get("collapsed", 0), now, node_id),
                    )
                else:
                    connection.execute(
                        """INSERT INTO visual_lab_nodes
                        (id,document_id,node_kind,current_content_revision_id,position_x,position_y,width,height,z_index,collapsed,created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,?,NULL,?,?,?,?,?,?,?,?,?,1,'v1')""",
                        (node_id, document_id, node_kind, item["position_x"], item["position_y"], item["width"], item["height"], item.get("z_index", 0), item.get("collapsed", 0), now, now, actor),
                    )
                    connection.execute(
                        """INSERT INTO visual_lab_node_revisions
                        (id,node_id,revision_no,parent_revision_id,content_json,content_hash,change_note,created_at,created_by,schema_version)
                        VALUES (?,?,1,NULL,?,?,?,?,?,'v1')""",
                        (revision_id, node_id, _canonical(normalized), _hash(normalized), f"恢复快照 #{snapshot['snapshot_no']}", now, actor),
                    )
                    connection.execute("UPDATE visual_lab_nodes SET current_content_revision_id=? WHERE id=?", (revision_id, node_id))
            for edge in content.get("edges", []):
                if not isinstance(edge, dict) or str(edge.get("source_node_id")) not in snapshot_ids or str(edge.get("target_node_id")) not in snapshot_ids:
                    continue
                connection.execute(
                    """INSERT INTO visual_lab_edges
                    (id,document_id,source_node_id,source_port,target_node_id,target_port,edge_kind,metadata_json,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,1,'v1')""",
                    (str(uuid.uuid4()), document_id, edge["source_node_id"], edge["source_port"], edge["target_node_id"], edge["target_port"], edge["edge_kind"], _canonical(edge.get("metadata") or {}), now, now, actor),
                )
            viewport = content.get("viewport") if isinstance(content.get("viewport"), dict) else {"x": 0, "y": 0, "zoom": 1}
            connection.execute(
                """UPDATE visual_lab_documents SET topology_revision=topology_revision+1,viewport_json=?,updated_at=?,revision=revision+1 WHERE id=?""",
                (_canonical(viewport), now, document_id),
            )
        return {"restored": True, "snapshot_id": snapshot_id, "graph": self.get(document_id)}

    def run_preflight(self, node_id: str) -> dict[str, Any]:
        node = self._node(node_id)
        if node["node_kind"] not in {"GENERATION_INTENT", "TRANSFORM_INTENT"}:
            raise DomainRuleError("VISUAL_LAB_NODE_NOT_EXECUTABLE", "该节点不是可执行生成节点")
        content = node["content"]
        try:
            request = VariantPlanRequest.model_validate({"intent_id": content["intent_id"], **content["variant_plan"]})
        except Exception as error:
            return {"status": "BLOCKED", "plan_hash": _hash({"node_id": node_id, "revision": node["content_hash"]}), "blockers": [{"code": "VARIANT_PLAN_INCOMPLETE", "message": str(error)}], "would_create": {"variant": False, "job": False}, "mutated": False}
        plan = GenerationService(self.database, self.settings).preflight_variant(request.intent_id, request.to_domain())
        return {**plan, "visual_lab_node_id": node_id, "node_content_hash": node["content_hash"]}

    def run(self, node_id: str, plan_hash: str, idempotency_key: str) -> dict[str, Any]:
        node = self._node(node_id)
        content = node["content"]
        request = VariantPlanRequest.model_validate({"intent_id": content["intent_id"], **content["variant_plan"]})
        result = GenerationService(self.database, self.settings).submit_confirmed_variant(request.intent_id, request.to_domain(), plan_hash, idempotency_key)
        return {**result, "visual_lab_node_id": node_id}

    def _node(self, node_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT n.*,r.content_json,r.content_hash FROM visual_lab_nodes n
                JOIN visual_lab_node_revisions r ON r.id=n.current_content_revision_id WHERE n.id=?""",
                (node_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("VISUAL_LAB_NODE_NOT_FOUND", "节点不存在")
        item = dict(row)
        item["content"] = json.loads(item.pop("content_json"))
        return item

    def promotion_preflight(self, node_id: str, media_version_id: str, target_type: str, target_id: str) -> dict[str, Any]:
        node = self._node(node_id)
        with self.database.connect() as connection:
            media = connection.execute("SELECT mv.id,mv.integrity_status,ma.project_id,ma.id AS media_asset_id FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id WHERE mv.id=?", (media_version_id,)).fetchone()
            target = connection.execute("SELECT sh.id,se.project_id FROM shots sh JOIN episodes e ON e.id=sh.episode_id JOIN seasons se ON se.id=e.season_id WHERE sh.id=?", (target_id,)).fetchone()
            document = connection.execute("SELECT project_id FROM visual_lab_documents WHERE id=?", (node["document_id"],)).fetchone()
        blockers = []
        if media is None:
            blockers.append({"code": "MEDIA_VERSION_NOT_FOUND", "message": "候选媒体版本不存在"})
        elif str(media["integrity_status"]).upper() not in {"VERIFIED", "OK", "PASS"}:
            blockers.append({"code": "MEDIA_INTEGRITY_REQUIRED", "message": "候选媒体尚未通过完整性检查"})
        if target_type != "SHOT_CANDIDATE" or target is None:
            blockers.append({"code": "PROMOTION_TARGET_INVALID", "message": "目前只支持采纳为镜头候选"})
        if media is not None and target is not None and (str(media["project_id"]) != str(target["project_id"]) or str(media["project_id"]) != str(document["project_id"])):
            blockers.append({"code": "PROJECT_SCOPE_MISMATCH", "message": "候选和目标镜头不属于同一项目"})
        payload = {"node_id": node_id, "node_content_hash": node["content_hash"], "source_media_version_id": media_version_id, "target_type": target_type, "target_id": target_id}
        return {"status": "BLOCKED" if blockers else "READY", "plan_hash": _hash(payload), "blockers": blockers, "snapshot": payload, "mutated": False}

    def promote(self, node_id: str, media_version_id: str, target_type: str, target_id: str, plan_hash: str, actor: str = "local-user") -> dict[str, Any]:
        plan = self.promotion_preflight(node_id, media_version_id, target_type, target_id)
        if plan["status"] != "READY":
            raise DomainRuleError("VISUAL_LAB_PROMOTION_BLOCKED", "候选尚不能采纳", {"blockers": plan["blockers"]})
        if plan_hash != plan["plan_hash"]:
            raise DomainRuleError("PLAN_HASH_MISMATCH", "采纳计划已变化，请重新预检")
        now, promotion_id = _now(), str(uuid.uuid4())
        node = self._node(node_id)
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO visual_lab_promotions
                (id,document_id,node_id,source_media_version_id,target_type,target_id,plan_hash,result_subject_type,result_subject_id,created_at,created_by,schema_version)
                VALUES (?,?,?,?,?,?,?,'SHOT',?,?,?,'v1')""",
                (promotion_id, node["document_id"], node_id, media_version_id, target_type, target_id, plan_hash, target_id, now, actor),
            )
        return {"id": promotion_id, "source_media_version_id": media_version_id, "target_type": target_type, "target_id": target_id, "target_shot_id": target_id, "approved": False, "mutated": True}
