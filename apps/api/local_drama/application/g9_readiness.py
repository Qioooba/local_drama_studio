"""Read-only exit readiness for Production Cockpit and Visual Lab."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.episode_production_repository import SqliteEpisodeProductionReadRepository
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


class G9ReadinessService:
    """Inspect the replacement workspaces without mutating production data."""

    def __init__(self, database: Database, evidence_root: Path | None = None) -> None:
        self.database = database
        self.evidence_root = evidence_root or Path(__file__).resolve().parents[4] / "docs" / "evidence" / "g9"

    def _browser_evidence(self, name: str, project_id: str, episode_id: str, required_surface: str) -> dict[str, Any] | None:
        path = self.evidence_root / name
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if any((payload.get("status") != "PASS", payload.get("project_id") != project_id, payload.get("episode_id") != episode_id, payload.get("production_evidence") is not True, payload.get("fixture_mode") is not False, payload.get("runtime_contacted") is not False, payload.get("network_contacted") is not False)):
            return None
        viewports = payload.get("viewports")
        if not isinstance(viewports, list) or {item.get("viewport") for item in viewports if isinstance(item, dict)} != {"1440x900", "1280x800", "1024x768"}:
            return None
        for item in viewports:
            if not isinstance(item, dict) or item.get("status") != "PASS" or item.get("horizontal_page_overflow_px") != 0:
                return None
            if any(item.get(key) not in ([], None) for key in ("console_errors", "page_errors", "failed_responses")):
                return None
            if item.get("surface") != required_surface or item.get("keyboard_accessible") is not True:
                return None
        try:
            evidence_path = path.relative_to(Path(__file__).resolve().parents[4]).as_posix()
        except ValueError:
            evidence_path = path.as_posix()
        return {"path": evidence_path, "observed_at": payload.get("observed_at")}

    def inspect(self, project_id: str, episode_id: str | None = None) -> dict[str, Any]:
        with self.database.connect() as connection:
            project = connection.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
            if project is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            episode = connection.execute(
                """SELECT e.id,e.code,e.title FROM episodes e JOIN seasons s ON s.id=e.season_id
                WHERE s.project_id=? AND (? IS NULL OR e.id=?) ORDER BY e.display_order LIMIT 1""",
                (project_id, episode_id, episode_id),
            ).fetchone()
            if episode is None:
                raise DomainRuleError("EPISODE_NOT_FOUND", "项目没有匹配的集", {"project_id": project_id, "episode_id": episode_id})
            lab_count = int(connection.execute("SELECT COUNT(*) FROM visual_lab_documents WHERE project_id=? AND status='ACTIVE'", (project_id,)).fetchone()[0])
            node_count = int(connection.execute("SELECT COUNT(*) FROM visual_lab_nodes n JOIN visual_lab_documents d ON d.id=n.document_id WHERE d.project_id=?", (project_id,)).fetchone()[0])
            snapshot_count = int(connection.execute("SELECT COUNT(*) FROM visual_lab_snapshots s JOIN visual_lab_documents d ON d.id=s.document_id WHERE d.project_id=?", (project_id,)).fetchone()[0])
            invalid_edge_count = int(connection.execute(
                """SELECT COUNT(*) FROM visual_lab_edges e
                LEFT JOIN visual_lab_nodes s ON s.id=e.source_node_id
                LEFT JOIN visual_lab_nodes t ON t.id=e.target_node_id
                WHERE e.document_id IN (SELECT id FROM visual_lab_documents WHERE project_id=?)
                AND (s.id IS NULL OR t.id IS NULL OR s.document_id<>e.document_id OR t.document_id<>e.document_id)""",
                (project_id,),
            ).fetchone()[0])

        grid = SqliteEpisodeProductionReadRepository(self.database).shot_facts(str(episode["id"]), cursor=0, limit=75, states=set())
        total_shots = int(grid["total"])
        visible_rows = len(grid["items"])
        production_evidence = self._browser_evidence("g9-production-cockpit-uat.json", project_id, str(episode["id"]), "PRODUCTION_COCKPIT")
        lab_evidence = self._browser_evidence("g9-visual-lab-uat.json", project_id, str(episode["id"]), "VISUAL_LAB")
        checks = [
            {"code": "PRODUCTION_GRID_HAS_SHOTS", "passed": total_shots > 0, "count": total_shots, "detail": "生产驾驶舱从正式镜头状态矩阵读取事实"},
            {"code": "PRODUCTION_GRID_BOUNDED", "passed": visible_rows <= 75, "count": visible_rows, "detail": "规范化镜头阶段投影服务端分页且单页不超过 75 行"},
            {"code": "VISUAL_LAB_VERSIONED_SNAPSHOT", "passed": lab_count > 0 and node_count > 0 and snapshot_count > 0, "count": snapshot_count, "detail": "Visual Lab 已创建版本化节点和显式快照"},
            {"code": "VISUAL_LAB_TOPOLOGY_INTEGRITY", "passed": invalid_edge_count == 0, "count": invalid_edge_count, "detail": "所有连接均在同一文档内引用有效节点"},
            {"code": "PRODUCTION_COCKPIT_BROWSER_UAT", "passed": production_evidence is not None, "count": 3 if production_evidence else 0, "detail": "三档视口的表格、筛选、键盘和详情面板验收"},
            {"code": "VISUAL_LAB_BROWSER_UAT", "passed": lab_evidence is not None, "count": 3 if lab_evidence else 0, "detail": "三档视口的节点、连线、检视器和键盘验收"},
        ]
        first_blocker = next((str(check["code"]) for check in checks if not check["passed"]), None)
        return {
            "gate": "G9", "status": "PASS" if first_blocker is None else "IN_PROGRESS", "project_id": project_id,
            "episode": {"id": str(episode["id"]), "code": str(episode["code"]), "title": str(episode["title"])},
            "checks": checks, "next_required_action": first_blocker,
            "evidence": {"production_total_shots": total_shots, "production_visible_rows": visible_rows, "visual_lab_document_count": lab_count, "visual_lab_node_count": node_count, "visual_lab_snapshot_count": snapshot_count, "production_cockpit_uat": production_evidence, "visual_lab_uat": lab_evidence, "automated_fixture": "apps/api/tests/test_visual_lab_refactor.py is the replacement-domain fixture; browser evidence remains separate"},
            "observed_at": _now(), "runtime_contacted": False, "network_contacted": False, "mutated": False,
        }
