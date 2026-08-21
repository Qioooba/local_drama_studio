# ADR-0106：Episode Production Run 是既有自动化与 jobs 的创作者 facade

- 状态：Accepted
- 日期：2026-08-20
- 对应：xinjihua 02 §80 ADR-006；03 P9

## Context

系统已有 durable jobs、workflow runs/tasks、lease、heartbeat、retry 和 outbox。创作者需要“整集生产、暂停、恢复、局部修复”的语义，但另建 episode queue 会造成状态重复和恢复分叉。

## Decision

`EpisodeProductionRunService` 只把既有 automation workflow/job facts 聚合为分集阶段、进度和 blocker，并通过原调度器 dispatch。pause 阻止 pending dispatch；resume 不重跑已完成且 fingerprint 未 stale 的任务；局部失败仍指向真实 shot/job attempt。

## Rejected alternatives

- 新建 episode_jobs 队列：拒绝，因为会与 jobs/workflow tasks 争夺权威。
- 前端轮询后自行串行调用生成接口：拒绝，因为浏览器关闭后不可恢复。
- pause 直接终止所有 running process：拒绝；必须遵守已有取消/lease/recovery policy。

## Consequences

整集 UI 使用生产语义而不复制执行状态，崩溃恢复继续复用 durable queue。阶段投影必须可从事实重建，自动重试必须服从 QC policy 上限。

## 事实来源

`application/episode_production_runs.py`、`application/automation_workflows.py`、`application/worker.py`、`features/episode-run-v2/`、`test_episode_production_modes.py`、`test_episode_run_recovery.py`。
