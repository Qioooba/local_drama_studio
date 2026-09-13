# Phase 5 / K28：单集从零镜头到可播放预览

日期：2026-09-13

## 结论

首版继续限定为一个已确认来源范围的分集，并复用既有准备、拆解应用、生产 Run、关键帧批次、ReviewService、时间线与合成服务。没有新增复制子状态的父运行表，也没有把 validator 改成隐式生成器。

## 复核与修正

- 零镜头入口调用 `EpisodePreparationService.prepare`；缺方案时返回并持久显示原 `SHOT_PLANNING` Job ID，关页后从读模型恢复。
- 已有匹配的 `DRAFT_READY` 草案仍调用原 `BreakdownApplyService.apply_draft`；应用完成后前端重新读取事实，再由 `EpisodeProductionRunService.start` 建立新的冻结快照。
- 修正“只要存在一个镜头就视为方案完成”：现在必须存在已应用 breakdown 的场次证据；单个手工镜头且方案不完整会继续排入本集方案 Job。
- 缺故事拆解模型不再阻断只读来源诊断；真正提交准备命令时返回 `EPISODE_BREAKDOWN_MODEL_REQUIRED`，不会虚构方案已补全。
- 身份冲突、缺身份包、缺执行 Profile、关键帧待审继续由既有 preflight/attention 投影集中返回实体、原因与入口。
- 批量关键帧继续使用既有 `ShotKeyframeGenerationBatchService`；完成只登记候选，批准与工作槽选择仍走 `ReviewService`。
- 可播放小样继续由同一 Episode Production Run 的 TTS、字幕、时间线、render 流程生成；正式交付门禁没有放宽。

## 验证

- `pytest test_episode_source_binding.py`：5 项通过，包括来源 A/B 隔离、内容篡改阻断、跨项目拒绝、单手工镜头不冒充完整方案。
- `pytest test_episode_production_v2.py test_episode_production_runs.py test_episode_one_click_pipeline.py`：组合运行除修正前暴露的旧缺模型诊断问题外其余均通过；修正后相关来源套件重新全绿。
- `pytest test_shot_keyframe_generation.py test_episode_one_click_pipeline.py`：16 项通过，包括只读计划、缺运行绑定阻断、批次幂等回放、候选完成登记，以及经隔离 transport/本地 FFmpeg 的单集 rough-cut 闭环。
- `vitest EpisodeProductionWorkspace.test.tsx`：18 项通过，包括零镜头准备、失败 Job 恢复、重新读取状态、待确认聚合、关键帧重生成不批准旧候选。
- 本轮没有连接真实 ComfyUI/GPU，也没有启动全剧媒体生成。

## 暂停点语义

当前流程只报告真实事实：无镜头、方案 Job 运行/失败、身份决策待处理、Profile/模型缺失、关键帧候选待审、生产 Run HITL 或合成结果。每个暂停均保留原 Job/run/shot 实体；已通过内容不会因刷新或重复提交被重做。
