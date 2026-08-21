# ADR-0110：Router 采用 strangler 迁移并保持旧 URL 合同

- 状态：Accepted
- 日期：2026-08-20
- 对应：xinjihua 02 §80 ADR-010、§69；03 P1-01、P12

## Context

旧 Web 使用 `/?view=...&project=...&episode=...&shot=...` 手工状态；新工作区需要可刷新、可分享、可 back/forward 的语义路径。直接删除旧 URL 会破坏书签、旧 UAT 和尚未迁移的专家 Canvas/精确 review 上下文。

## Decision

新增稳定 `/projects/...`、`/models`、`/jobs`、`/diagnostics` 路由树，旧 `/` 继续挂载。兼容 boundary 仅将存在等价目标且上下文完整的旧 query URL replace 到新路径；未知、不完整、Canvas 和携带精确 review selection 的 URL 保持旧 Shell。`legacy=1` 是显式回退。旧 API 同样在替代完成前保留，不因 UI 迁移提前 deprecated/delete。

## Rejected alternatives

- 一次切换所有 URL：拒绝，因为会丢失旧上下文且无法快速回退。
- 永远保留两套导航无转换：拒绝，因为新路径采用率和测试合同不明确。
- 无条件把所有旧 query 映到最近页面：拒绝，因为会 silent drop review/shot 等精确状态。

## Consequences

新页面具有稳定 deep link，旧入口可渐进收敛；P12 清理必须等待新路径 UAT、no callers 和 package/API 依赖检查。解析器需要独立 mapping、编码、invalid/incomplete 与 escape-hatch 测试。

## 事实来源

`apps/web/src/app/router.tsx`、`legacyRoute.tsx`、`router.test.tsx`、`legacyRoute.test.tsx`、最终文本证据矩阵。
