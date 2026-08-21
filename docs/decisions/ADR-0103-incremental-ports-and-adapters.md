# ADR-0103：Ports & Adapters 采用增量迁移

- 状态：Accepted
- 日期：2026-08-20
- 对应：xinjihua 02 §80 ADR-003；03 P0-03、P2-02

## Context

仓库存在可工作的旧 application 服务，也存在新 command/query 切片。一次性把全部旧服务改成理想分层会扩大回归范围，并与持续功能开发冲突。

## Decision

新垂直切片从 domain/application 依赖 port protocol，由 infrastructure/database 或 runtime adapter 实现；`application/commands`、`application/queries` 和 `application/ports` 禁止导入 concrete sqlite/comfy/manifest。旧模块先被测试记录，再在实际修改时迁移，不要求一次清债。

## Rejected alternatives

- 一次性 DDD 重写：拒绝，因为交付延迟且历史事实回归风险高。
- route 直接操作 SQLite：拒绝，因为并发、审计和 invariant 会散落。
- 为每个 SQL 建极细 repository：拒绝，因为接口数量会超过业务语义；按聚合/用例划分更可维护。

## Consequences

新代码边界可 hard fail，旧债务可渐进消除；过渡期会同时存在 legacy service 和 port-based slice，需要事实守卫证明没有第二套表或 authority。

## 事实来源

`application/ports/`、`infrastructure/database/asset_bible_repository.py`、`test_refactor_architecture_boundaries.py`、`test_refactor_fact_guardrails.py`、`scripts/refactor_invariants.py`。
