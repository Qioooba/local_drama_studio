# Phase 1 / K02：安全镜头归一化实施记录

- 日期：2026-09-12
- 状态：`FIX_MINIMAL`
- 范围：表演强度、运镜推断、创作字段门禁、Camera Profile 能力和确认审计。
- 安全边界：仅隔离 SQLite/纯逻辑测试；未启动真实 LLM/GPU，未修改生产数据库或在线服务。

## 红灯证据

- 合法 `performance.intensity` 0、0.2、0.9 全部被改写为 0.5。
- 非法强度被静默替换为 0.5。
- “推门、拉椅子、摇头”分别被误判为推、拉、摇镜。
- 空白镜头被泛化创作文本补齐并错误判为 Production Ready。
- Camera capability 为 `UNSUPPORTED` 时，确认服务仍强制改成 `PROMPT_FALLBACK`。
- 自动技术补齐的审计被写成 `director / 人工确认`。

## 最小修复

1. 合法表演强度原值保留且归一化幂等；非法显式值返回 `DIRECTOR_INTENT_INTENSITY_INVALID`。
2. 运镜只从显式 camera 字段解析，不再扫描主体动作；缺失时采用中性 `STATIC/UNSPECIFIED` 技术值。
3. 不再编造主体动作、环境、连续性、面部动作、视线和站位等创作事实。
4. `normalize_fields()` 只归一化结构；`ensure_ready()` 和确认命令负责 Production Ready 门禁。
5. Profile 不支持 camera 时保留 `UNSUPPORTED` 并由门禁拒绝，不伪造 prompt fallback。
6. 自动技术补齐使用 `automation / EPISODE_SHOTS_AUTO_HEALED_READY / AUTOMATED_TECHNICAL_COMPLETION` 审计；人工确认维持 director 语义。
7. 冻结版本保持不可变；技术补齐产生新版本时，台词、连续性、服装和表演强度保持原值。

## 验收

- `test_shot_production_normalizer.py`
- `test_episode_shot_ready.py`
- `test_camera_plan_contracts.py`
- `test_director_fields.py`
- `test_whole_drama_orchestrator.py`

最终结果：35 passed；相关修改文件 Ruff 通过；只有既有 Starlette/Alembic 弃用警告。

整剧自动准备面对空白创作字段时现在返回 `BLOCKED / EPISODE_SHOTS_READY_VALIDATION_FAILED`，不会制造 READY 状态；已有完整创作内容仍可进行有审计的技术补齐。
