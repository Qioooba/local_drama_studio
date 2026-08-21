# ADR-0105：生成选择由能力解析与版本化偏好继承决定

- 状态：Accepted
- 日期：2026-08-20
- 对应：xinjihua 02 §80 ADR-005；03 P7

## Context

模型字符串写在镜头或前端 silent fallback 无法证明模型当前可用，也无法重现历史生成。项目需要默认偏好，分集和镜头需要显式覆盖，first/last frame 等模式还要求精确 capability。

## Decision

以 immutable execution profile version 表示执行真值；0043 preference set/version 记录 PROJECT → EPISODE → SHOT 继承。解析器先解析最具体有效偏好，再验证 runtime/profile capability；不兼容或缺失返回可解释 blocker，不静默换模型。variant 输入绑定记录解析后的 profile version 与 exact media versions。

## Rejected alternatives

- 前端保存模型名：拒绝，因为不可验证、不可重放。
- 缺模型时自动选任意可用模型：拒绝，因为会改变成本、风格和 first/last 语义。
- 修改旧 preference row：拒绝，因为历史 variant 无法解释当时选择。

## Consequences

项目默认和局部覆盖可预测，历史生成可追溯；禁用覆盖会回退上层。新增生成模式必须先声明 capability，UI 必须显示 blocker 或明确的受控 fallback policy。

## 事实来源

`0043_generation_preferences.py`、`generation_preference_repository.py`、`features/preferences-v2/`、`test_migration_0043.py`、`test_profile_compatibility.py`、`test_ref2va_capability.py`。
