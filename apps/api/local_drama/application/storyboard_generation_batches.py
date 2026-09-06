from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from local_drama.application.ports.storyboard_generation_batches import (
    StoryboardGenerationPreflightPort,
    StoryboardGenerationWorkflowPort,
)
from local_drama.domain.errors import DomainRuleError

REPORT_BUDGET_BYTES = 64 * 1024 * 1024


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class StoryboardGenerationBatchService:
    """Plan and durably dispatch selected-shot regeneration.

    The service does not create a second queue. Each selected shot becomes a
    bounded item in the existing Automation Workflow aggregate, and execution
    delegates to EpisodeWorkerActionService/GenerationVariant/Job.
    """

    def __init__(
        self,
        preflight: StoryboardGenerationPreflightPort,
        workflows: StoryboardGenerationWorkflowPort,
    ) -> None:
        self.preflight = preflight
        self.workflows = workflows

    @staticmethod
    def _targets(raw_targets: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        targets: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_targets):
            shot_id = str(raw.get("shot_id") or "").strip()
            expected_revision = int(raw.get("expected_revision") or 0)
            if shot_id in seen:
                issues.append({"code": "SHOT_BATCH_DUPLICATE", "shot_id": shot_id, "item_index": index, "message": "批量镜头不能重复"})
                continue
            seen.add(shot_id)
            targets.append({"shot_id": shot_id, "expected_revision": expected_revision})
        return targets, issues

    def plan(self, episode_id: str, raw_targets: list[dict[str, Any]]) -> dict[str, Any]:
        targets, issues = self._targets(raw_targets)
        if not targets:
            raise DomainRuleError("SHOT_BATCH_EMPTY", "批量生成至少需要一个镜头")
        if len(targets) > 100:
            raise DomainRuleError("SHOT_BATCH_TOO_LARGE", "单次批量生成最多包含 100 个镜头")
        preflight = self.preflight.video_generation_preflight(
            episode_id,
            target_shot_ids=tuple(item["shot_id"] for item in targets),
        )
        expected = {item["shot_id"]: int(item["expected_revision"]) for item in targets}
        for item in preflight["items"]:
            shot_id = str(item["shot_id"])
            if expected[shot_id] != int(item["shot_revision"]):
                issues.append(
                    {
                        "code": "SHOT_REVISION_CONFLICT",
                        "shot_id": shot_id,
                        "message": "镜头已变化，请重新选择后再生成",
                        "expected_revision": expected[shot_id],
                        "current_revision": int(item["shot_revision"]),
                    }
                )
            for blocker in item["blockers"]:
                issues.append(
                    {
                        "code": str(blocker["code"]),
                        "shot_id": shot_id,
                        "message": "镜头尚未满足批量生成条件",
                        **{key: value for key, value in blocker.items() if key != "code"},
                    }
                )
        plan_source = {
            "episode_id": episode_id,
            "targets": targets,
            "input_fingerprint": preflight["input_fingerprint"],
        }
        plan_hash = hashlib.sha256(_canonical(plan_source).encode("utf-8")).hexdigest()
        return {
            **plan_source,
            "project_id": preflight["project_id"],
            "plan_hash": plan_hash,
            "valid": not issues,
            "issues": issues,
            "items": preflight["items"],
            "summary": {
                "selected": len(targets),
                "ready": sum(item["status"] == "READY" for item in preflight["items"]),
                "blocked": sum(item["status"] != "READY" for item in preflight["items"]),
            },
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }

    def submit(
        self,
        episode_id: str,
        raw_targets: list[dict[str, Any]],
        *,
        expected_plan_hash: str,
        idempotency_key: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        plan = self.plan(episode_id, raw_targets)
        if not hmac.compare_digest(str(plan["plan_hash"]), expected_plan_hash):
            raise DomainRuleError("SHOT_BATCH_PLAN_STALE", "批量生成计划已变化，请重新检查")
        if not plan["valid"]:
            raise DomainRuleError("SHOT_BATCH_BLOCKED", "所选镜头存在生成阻塞", {"issues": plan["issues"]})
        code = f"STORYBOARD_BATCH_{episode_id.replace('-', '')[:16]}_{expected_plan_hash[:12]}"
        items = [
            {
                "key": f"shot:{item['shot_id']}",
                "payload": {
                    "action": "VIDEO_GENERATION",
                    "episode_id": episode_id,
                    "target_shot_ids": [item["shot_id"]],
                    "force_new_take": True,
                    "mode_policy": {"target_take_count": 1, "auto_select_videos": False},
                    "input_fingerprint": plan["input_fingerprint"],
                    "batch_plan_hash": expected_plan_hash,
                },
            }
            for item in plan["items"]
        ]
        workflow = self.workflows.create_workflow(
            str(plan["project_id"]),
            code=code,
            title=f"故事板批量新增 {len(items)} 个视频 Take",
            mode="BATCH_AUTOMATED",
            nodes=[
                {
                    "id": "storyboard-batch-generation",
                    "type": "EPISODE_PRODUCTION_TASK",
                    "metadata": {
                        "episode_id": episode_id,
                        "workflow_scope": "STORYBOARD_SELECTED_SHOTS",
                        "target_shot_ids": [item["shot_id"] for item in plan["items"]],
                        "batch_plan_hash": expected_plan_hash,
                    },
                }
            ],
            batch_items=items,
            conditions=[
                {
                    "field": "machine_check.status",
                    "operator": "IN",
                    "value": ["FAIL", "FAILED", "BLOCKED", "NEEDS_HITL"],
                    "action": "PAUSE_HITL",
                }
            ],
            max_iterations=len(items) + 1,
            max_tasks=len(items) + 1,
            max_disk_bytes=REPORT_BUDGET_BYTES,
            human_gate="ON_CONDITION",
            repeat_batch=False,
            source_fingerprint=expected_plan_hash,
            actor=actor,
        )
        run = self.workflows.start_run(
            str(workflow["id"]),
            plan_hash=str(workflow["plan_hash"]),
            idempotency_key=idempotency_key,
            actor=actor,
        )
        return {
            "plan_hash": expected_plan_hash,
            "workflow_id": str(workflow["id"]),
            "run_id": str(run["id"]),
            "status": str(run["status"]),
            "task_count": int(run["task_count"]),
            "selected_shot_ids": [item["shot_id"] for item in plan["items"]],
            "idempotent_replay": bool(run.get("idempotent_replay", False)),
        }
