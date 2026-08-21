# ADR-0108：DirectorIntent V3 存于不可变 shot revision

- 状态：Accepted
- 日期：2026-08-20
- 对应：xinjihua 02 §80 ADR-008；03 P6

## Context

镜头意图经历过多个字段版本。新 Inspector 需要 typed 景别、构图、运动、表演、光线和 blocking，同时旧 revision 必须可读、冻结 revision 不可修改。

## Decision

DirectorIntent V3 是 shot revision fields 的 typed domain view。normalizer 将 v1/v2 安全映射至统一 view model；保存永远创建 revision n+1，以 expected revision 检测冲突。freeze 约束保持在 revision authority，Director Desk 不存 mutable intent copy。

## Rejected alternatives

- 在 shots 增加一组 mutable intent columns：拒绝，因为会绕过 revision、freeze 和历史比较。
- 只在前端兼容旧字段：拒绝，因为 API/worker 仍可能产生不同解释。
- 原地升级所有旧 revision JSON：拒绝，因为会篡改历史证据且迁移风险高。

## Consequences

旧项目按需 normalize，无需 destructive migration；生成和 readiness 读取同一 typed view。未知 optional 字段可保留，缺失 required 字段返回 domain error，保存冲突返回明确 409。

## 事实来源

`domain/director_intent.py`、Director intent schema/route/client、`features/director-v2/DirectorIntentEditor.tsx`、`test_director_intent_v3.py`、`test_director_fields.py`。
