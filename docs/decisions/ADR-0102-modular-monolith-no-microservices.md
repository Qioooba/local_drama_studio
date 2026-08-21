# ADR-0102：保持模块化单体，不拆微服务

- 状态：Accepted
- 日期：2026-08-20
- 对应：xinjihua 02 §80 ADR-002、§84

## Context

LocalDramaStudio 是 Windows 本地单机生产系统，SQLite、项目媒体目录、本地 Worker 和 loopback runtime 共同构成部署边界。当前体验瓶颈是事实权威、工作区组织和可恢复任务，不是跨团队独立部署。

## Decision

继续采用 React Web + FastAPI API + Python Worker 的模块化单体。领域、应用、ports、基础设施和 API 保持代码边界；API/Worker 可分进程运行，但共享版本化 schema 和本地部署单元。SQLite WAL、jobs/leases/outbox 和项目目录仍是权威基础设施。

## Rejected alternatives

- 拆 asset/generation/review 微服务：拒绝，因为会引入分布式事务、部署和故障面，无法改善本地创作主流程。
- 引入 Redis/Celery/PostgreSQL：拒绝，因为现有持久队列和 SQLite 恢复语义已满足单机目标。
- 全量 event sourcing：拒绝；不可变 revision/variant/audit 已覆盖需要追溯的事实，无需重写所有读写。

## Consequences

跨模块一致性可用单 SQLite 事务维护，安装和备份简单；必须用架构测试防止模块重新耦合。未来只有在真实容量或独立部署证据出现时，才另写 ADR 拆分。

## 事实来源

`ADR-0001-architecture-baseline.md`、`main.py`、`application/commands/`、`application/queries/`、`test_refactor_architecture_boundaries.py`、`scripts/refactor_invariants.py`。
