"""EXPERIMENT_CELL job handler: one frozen matrix cell becomes a child Variant + GPU Job.

The CPU cell is orchestration, not a fake generation success: it is rebound to
the child generation Job so cancellation, terminal state and progress follow
actual execution.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Protocol

from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation import VariantPlan
from local_drama.domain.policies import VariantInput

AtomicWriter = Callable[[Path, Callable[[Path], None]], None]


class WorkerPersistencePort(Protocol):
    """Minimum persistence abstraction used by experiment cell handlers."""

    def connect(self) -> Any:  # pragma: no cover - protocol boundary
        ...

    def transaction(self) -> Any:  # pragma: no cover - protocol boundary
        ...


class ExperimentGenerationPort(Protocol):
    """Generation capabilities required by EXPERIMENT_CELL handlers."""

    def get_variant(self, variant_id: str) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...

    def preflight_variant(self, intent_id: str, plan: VariantPlan) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...

    def submit_confirmed_variant(
        self,
        intent_id: str,
        plan: VariantPlan,
        plan_hash: str,
        idempotency_key: str,
    ) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...


def run_experiment_cell(
    job: dict[str, Any],
    output_root: Path,
    *,
    work_root: Path,
    database: WorkerPersistencePort,
    generation_ops: ExperimentGenerationPort,
    atomic_writer: AtomicWriter,
) -> tuple[str, str]:
    snapshot = job["input_snapshot"]
    experiment_id = str(snapshot.get("experiment_id") or "")
    cell_key = str(snapshot.get("cell_key") or "")
    parameters = snapshot.get("parameters")
    if not experiment_id or not cell_key or not isinstance(parameters, dict) or not parameters:
        raise DomainRuleError("EXPERIMENT_CELL_INPUT_INVALID", "实验 cell 缺少冻结计划或参数")

    with database.connect() as connection:
        row = connection.execute(
            """SELECT ec.id AS cell_id, ec.job_id AS cell_job_id, ec.status AS cell_status,
            ge.status AS experiment_status, ge.created_at AS experiment_created_at,
            ge.axis_definitions_json, ge.intent_id, gi.project_id
            FROM experiment_cells ec
            JOIN generation_experiments ge ON ge.id=ec.experiment_id
            JOIN generation_intents gi ON gi.id=ge.intent_id
            WHERE ec.experiment_id=? AND ec.cell_key=?""",
            (experiment_id, cell_key),
        ).fetchone()
    if row is None:
        raise DomainRuleError("EXPERIMENT_CELL_NOT_FOUND", "实验 cell 不存在")
    if str(row["experiment_status"]) != "CONFIRMED":
        raise DomainRuleError("EXPERIMENT_NOT_CONFIRMED", "实验已取消或尚未确认，禁止继续派生")
    if str(row["cell_job_id"]) != str(job["id"]):
        # A previous Attempt already dispatched the immutable child.  The
        # parent retry must not create another Variant.
        with database.connect() as connection:
            existing_child = connection.execute(
                "SELECT id, subject_id, state FROM jobs WHERE id=? AND subject_type='GENERATION_VARIANT'",
                (row["cell_job_id"],),
            ).fetchone()
        if existing_child is None:
            raise DomainRuleError("EXPERIMENT_CELL_LINEAGE_INVALID", "实验 cell 的子任务谱系无效")
        result = {
            "experiment_id": experiment_id,
            "cell_id": str(row["cell_id"]),
            "cell_key": cell_key,
            "variant_id": str(existing_child["subject_id"]),
            "generation_job_id": str(existing_child["id"]),
            "generation_job_state": str(existing_child["state"]),
            "idempotent_replay": True,
        }
    else:
        try:
            axes_plan = json.loads(str(row["axis_definitions_json"] or "{}"))
        except (TypeError, json.JSONDecodeError) as error:
            raise DomainRuleError("EXPERIMENT_PLAN_INVALID", "实验冻结计划无法解析") from error
        base_variant_id = str(snapshot.get("base_variant_id") or axes_plan.get("base_variant_id") or "")
        if not base_variant_id:
            # Compatibility for plans authored before base_variant_id was
            # frozen: resolve the newest Variant that already existed when
            # the experiment itself was created.  This is deterministic and
            # excludes later branches.
            with database.connect() as connection:
                base_row = connection.execute(
                    """SELECT id FROM generation_variants
                    WHERE intent_id=? AND created_at<=?
                    ORDER BY created_at DESC, variant_no DESC, id DESC LIMIT 1""",
                    (row["intent_id"], row["experiment_created_at"]),
                ).fetchone()
            base_variant_id = str(base_row["id"]) if base_row is not None else ""
        if not base_variant_id:
            raise DomainRuleError(
                "EXPERIMENT_BASE_VARIANT_REQUIRED",
                "实验矩阵必须基于一个已提交的 GenerationVariant；请先完成生成预检与提交",
            )

        base = generation_ops.get_variant(base_variant_id)
        if str(base["intent_id"]) != str(row["intent_id"]):
            raise DomainRuleError("EXPERIMENT_BASE_VARIANT_MISMATCH", "实验冻结的基础 Variant 不属于当前 Intent")
        parameter_set = json.loads(str(base["parameter_set_json"] or "{}"))
        if not isinstance(parameter_set, dict):
            raise DomainRuleError("EXPERIMENT_BASE_PARAMETERS_INVALID", "基础 Variant 参数快照无效")
        changed_keys: list[str] = []
        for raw_key, value in parameters.items():
            key = str(raw_key).strip()
            if not key:
                raise DomainRuleError("EXPERIMENT_AXIS_INVALID", "实验轴名称不能为空")
            target_key = "SEED" if key.casefold() == "seed" else next(
                (existing for existing in parameter_set if str(existing).casefold() == key.casefold()),
                key,
            )
            if parameter_set.get(target_key) != value:
                changed_keys.append(target_key)
            parameter_set[target_key] = value

        seed_policy = str(base["seed_policy"])
        explicit_seed = int(base["explicit_seed"]) if base.get("explicit_seed") is not None else None
        if "SEED" in parameter_set:
            if seed_policy != "EXPLICIT":
                raise DomainRuleError("EXPERIMENT_SEED_POLICY_UNSUPPORTED", "Provider random 基础 Variant 不能运行显式 seed 实验轴")
            try:
                explicit_seed = int(parameter_set["SEED"])
            except (TypeError, ValueError) as error:
                raise DomainRuleError("EXPERIMENT_SEED_INVALID", "实验 seed 必须是整数") from error
            parameter_set["SEED"] = explicit_seed
        variant_type = (
            "RESAMPLE_NEW_SEED"
            if set(changed_keys) == {"SEED"} and explicit_seed != base.get("explicit_seed")
            else "PARAMETER_BRANCH"
        )
        bindings = tuple(
            VariantInput(
                str(item["role"]),
                str(item["media_version_id"]),
                int(item["ordinal"]),
                float(item["weight"]) if item.get("weight") is not None else None,
            )
            for item in base["bindings"]
        )
        plan = VariantPlan(
            variant_type=variant_type,
            parent_variant_id=base_variant_id,
            branch_reason=f"EXPERIMENT_CELL:{experiment_id}:{cell_key}",
            prompt_revision_id=str(base["prompt_revision_id"]) if base.get("prompt_revision_id") else None,
            profile_version_id=str(base["capability_profile_version_id"]),
            parameter_set=parameter_set,
            seed_policy=seed_policy,
            explicit_seed=explicit_seed,
            bindings=bindings,
            provider_random_nonce=str(base["provider_random_nonce"]) if base.get("provider_random_nonce") else None,
        )
        child_key = f"experiment:{experiment_id}:generation:{cell_key}"
        with database.connect() as connection:
            existing_child = connection.execute(
                "SELECT id, subject_id, state FROM jobs WHERE project_id=? AND idempotency_key=?",
                (row["project_id"], child_key),
            ).fetchone()
        if existing_child is None:
            preflight = generation_ops.preflight_variant(str(row["intent_id"]), plan)
            submitted = generation_ops.submit_confirmed_variant(
                str(row["intent_id"]), plan, str(preflight["plan_hash"]), child_key
            )
            child = submitted["job"]
            variant_id = str(submitted["variant"]["id"])
            replay = False
        else:
            child = dict(existing_child)
            variant_id = str(existing_child["subject_id"])
            replay = True
        with database.transaction() as connection:
            updated = connection.execute(
                """UPDATE experiment_cells SET variant_id=?, job_id=?, status=?
                WHERE id=? AND job_id=? AND status NOT IN ('CANCELLED','CANCEL_REQUESTED')""",
                (variant_id, child["id"], str(child["state"]), row["cell_id"], job["id"]),
            )
            if updated.rowcount != 1:
                raise DomainRuleError("EXPERIMENT_CELL_STALE", "实验 cell 已被取消或由其他 Worker 派生")
            connection.execute(
                """INSERT INTO outbox_events (type, project_id, subject_type, subject_id, payload_json)
                VALUES ('EXPERIMENT_CELL_DISPATCHED', ?, 'EXPERIMENT_CELL', ?, ?)""",
                (
                    row["project_id"],
                    row["cell_id"],
                    json.dumps(
                        {"experiment_id": experiment_id, "variant_id": variant_id, "job_id": child["id"]},
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
        result = {
            "experiment_id": experiment_id,
            "cell_id": str(row["cell_id"]),
            "cell_key": cell_key,
            "base_variant_id": base_variant_id,
            "variant_id": variant_id,
            "generation_job_id": str(child["id"]),
            "generation_job_state": str(child["state"]),
            "parameters": parameters,
            "idempotent_replay": replay,
        }

    output = output_root / "experiment-cell.json"
    atomic_writer(
        output,
        lambda target: target.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        ),
    )
    return "EXPERIMENT_CELL_REPORT", output.relative_to(work_root).as_posix()
