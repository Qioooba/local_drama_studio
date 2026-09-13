# Phase 1 / K04：Turbo LoRA 参数作用域实施记录

- 日期：2026-09-12
- 状态：`FIX_MINIMAL`
- 范围：`ComfyGenerationService._apply_effective_configuration` 的 Turbo LoRA 强度覆盖。
- 安全边界：仅复制后的内存 workflow 与隔离数据库测试；未连接 ComfyUI，未运行 GPU。

## 红灯证据

同一 workflow 同时放入角色 LoRA（0.33）与已识别 Turbo LoRA（1.0），应用 Turbo 强度 0.77 后，两者都被改成 0.77。第二采样器虽绑定角色 LoRA，也存在被错误扩散的风险。

## 最小修复

- `strength_model` 只写入 `_is_turbo_lora()` 已识别的节点 ID。
- Scheduler/Guider 只在仍直接引用基础模型 `['1', 0]` 时改接 Turbo；已有角色 LoRA 链不改接。
- 不修改非目标 LoRA 名称/强度、第二采样器连接、宽高和帧数。
- Profile workflow 原始字节不变；仍只修改每任务编译副本。

## 验收

- `test_h3_runtime_overrides.py`
- `test_comfy_workflow_bindings.py`

结果：11 passed；Ruff 与 `git diff --check` 通过；仅既有 Alembic 弃用警告。
