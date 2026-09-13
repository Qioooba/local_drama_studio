# Phase 1 / K07 生成重入候选身份验收（2026-09-12）

## 结论

K07 已完成。同一次 `force_new_take` 或 stale 工作媒体刷新使用稳定的 task/shot 候选槽；
重入不再用不断变化的 `len(jobs)` 改写幂等键。生成提交在创建 Variant 前查询持久 Job
身份，同 key + 同 plan 返回原 Variant/Job，同 key + 不同 plan 明确冲突，新 key 才创建
下一次创意候选。

## 实施范围

- `episode_worker_actions.py`：强制新拍/过期刷新候选槽固定为当前任务的 slot 0；run、task、
  shot 仍共同构成命令身份。
- `generation.py`：Job 输入冻结 `submission_plan_hash`；原子事务在插入 Variant 前处理持久
  replay 和 payload mismatch，避免产生无 Job 的孤立 Variant。
- 沿用现有 Job/attempt；技术 retry 不改变候选 plan 或 seed。

## 验收证据

执行：

```text
python -m pytest -q \
  apps/api/tests/test_generation_variants.py \
  apps/api/tests/test_episode_production_modes.py \
  apps/api/tests/test_episode_worker_actions.py
```

结果：相关套件全部通过（68 个用例）。新增断言覆盖：

- 新 Service 实例模拟进程退出后，同 key 重入仍复用原 Variant/Job；
- 同 key 不同冻结 plan 返回 `IDEMPOTENCY_PAYLOAD_MISMATCH`；
- 新 key 合法创建第二个明确候选；
- force 路径连续进入时 slot 不随历史 Job 数量变化；
- stale 工作媒体路径保持显式刷新语义。

Ruff 与 `git diff --check` 通过。

## 未扩大范围

没有引入进程内锁或第二套队列，没有声称外部 Provider exactly-once，也没有把技术 retry
算成新的创意候选。
