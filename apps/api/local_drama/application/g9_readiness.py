"""Read-only G9 canvas exit readiness.

The service reports production graph facts and separates them from formal UAT
that cannot be inferred from a database row.  It never creates a fixture,
layout, execution plan or job while inspecting readiness.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from local_drama.application.canvas import ProductionCanvasService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


class G9ReadinessService:
    """Inspect canvas readiness without mutation, runtime or network contact."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def inspect(self, project_id: str, episode_id: str | None = None) -> dict[str, Any]:
        with self.database.connect() as connection:
            project = connection.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
            if project is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            episode = connection.execute(
                """SELECT e.id, e.code, e.title FROM episodes e
                JOIN seasons s ON s.id=e.season_id WHERE s.project_id=? AND (? IS NULL OR e.id=?)
                ORDER BY e.display_order LIMIT 1""",
                (project_id, episode_id, episode_id),
            ).fetchone()
            if episode is None:
                raise DomainRuleError("EPISODE_NOT_FOUND", "项目没有匹配的集", {"project_id": project_id, "episode_id": episode_id})
            plan_count = int(connection.execute("SELECT COUNT(*) FROM canvas_execution_plans WHERE scope_type='EPISODE' AND scope_id=?", (episode["id"],)).fetchone()[0])
            layout_count = int(connection.execute("SELECT COUNT(*) FROM canvas_layouts WHERE scope_type='EPISODE' AND scope_id=?", (episode["id"],)).fetchone()[0])

        graph = ProductionCanvasService(self.database).graph("EPISODE", str(episode["id"]), cursor=0, limit=300)
        visible_nodes = len(graph["nodes"])
        total_shots = int(graph["page"]["total_shots"])
        invariants = graph["invariants"]
        checks = [
            {
                "code": "LAZY_GRAPH_READ_MODEL",
                "passed": bool(invariants.get("lazy") and int(invariants.get("max_visible_nodes", 0)) == 300),
                "count": visible_nodes,
                "detail": "后端 read model 分页且可见节点上限为 300",
            },
            {
                "code": "LAYOUT_DEPENDENCY_ISOLATION",
                "passed": invariants.get("layout_changes_business_dependencies") is False,
                "count": layout_count,
                "detail": "布局只保存视觉状态，不改变业务 edges",
            },
            {
                "code": "PREFLIGHT_PERSISTENCE",
                "passed": plan_count > 0,
                "count": plan_count,
                "detail": "运行必须先生成持久化 preflight 计划，不能直接提交",
            },
            {
                "code": "VISIBLE_NODE_PERFORMANCE_UAT",
                "passed": 100 <= visible_nodes <= 300 and total_shots >= 20,
                "count": visible_nodes,
                "required_range": [100, 300],
                "detail": "正式退出需要 100—300 可见节点浏览器性能证据；生产当前集不是规模样本",
            },
            {
                "code": "ACCESSIBILITY_ROUTE_UAT",
                "passed": False,
                "count": 0,
                "detail": "完整三视图键盘/可访问性 checklist 尚未形成正式退出证据",
            },
        ]
        first_blocker = next((str(check["code"]) for check in checks if not check["passed"]), None)
        return {
            "gate": "G9",
            "status": "PASS" if all(bool(check["passed"]) for check in checks) else "IN_PROGRESS",
            "project_id": project_id,
            "episode": {"id": str(episode["id"]), "code": str(episode["code"]), "title": str(episode["title"])},
            "checks": checks,
            "next_required_action": first_blocker,
            "evidence": {
                "production_total_shots": total_shots,
                "production_visible_nodes": visible_nodes,
                "persisted_layout_count": layout_count,
                "persisted_preflight_count": plan_count,
                "automated_fixture": "apps/api/tests/test_g9_canvas.py creates 62 shots and asserts 100-node page; fixture is not production evidence",
            },
            "observed_at": _now(),
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }
