# LocalDramaStudio 一键生产实现交接（2026-09-21）

## 1. 交接结论

本轮实现已停止，现场保持可复现状态。不要把当前工作误判为全部完成：主要功能、持久生产会话、整部容量窗口、机器临时选择、集中审核、局部返工、原稿流水线自动续接等已经实现并经过大量测试，但“真实原稿到整部待审”的最后一条验收链仍停在第二个关键帧波次缺口；真实 24 小时 soak 也尚未达到 86400 秒。

当前最重要的接手任务只有两个：

1. 修复自动生产关键帧分波时重复生成同一个镜头的问题，并检查视频分波是否存在同类的“Job 已成功、业务产物尚未提升”竞争窗口。
2. 让当前隔离 UAT 会话继续到 `WAITING_REVIEW`，生成最终来源链证据，然后完成全量回归、发布审计和 24 小时 soak 收口。

当前没有伪造任何人工批准、机器选择或媒体成功证据。历史失败 Job 被保留用于审计。

## 2. 工作区与保护要求

- 仓库：`F:\AI_Projects\h3\local_drama_studio`
- 主方案文档：`docs/gpt/LocalDramaStudio_一键生产与持续运行开发方案_2026-09-21.md`
- 工作区有大量用户原有改动和本轮改动，`git status` 很大。
- 不要执行 `git reset --hard`、`git clean`、整目录覆盖或回滚未知文件。
- `.codex-tmp/` 包含真实 UAT 数据和崩溃恢复证据，不能删除。
- `work/evidence/` 中的 24 小时记录器正在写文件，不能把未满 86400 秒的结果标为通过。

## 3. 已经实现的主要能力

### 3.1 持久生产会话与工厂界面

已经存在并接入：

- migrations `0095` 至 `0100`
- `production_sessions`、条目、预算、Job 关联、机器临时选择、会话身份输入、会话资产输入
- 单集、整部生产模式
- `DRAFT / BALANCED / QUALITY` 模式策略
- `ON_EXCEPTION` 等 checkpoint 策略
- GPU 队列容量窗口、每 tick 镜头数、任务数、尝试数、输出字节数、时长、磁盘预算
- `WAITING_USER`、`WAITING_REVIEW`、失败重试、局部返工、重新合成
- 集中审核投影与待审预览
- 前端生产工厂页面和生产会话客户端

主要新文件包括：

- `apps/api/alembic/versions/0096_production_sessions.py`
- `apps/api/alembic/versions/0098_production_session_identity_inputs.py`
- `apps/api/alembic/versions/0099_production_session_asset_inputs.py`
- `apps/api/alembic/versions/0100_production_session_waiting_user.py`
- `apps/api/local_drama/api/routes/production_sessions_v2.py`
- `apps/api/local_drama/api/schemas/production_sessions_v2.py`
- `apps/api/local_drama/application/production_sessions.py`
- `apps/api/local_drama/application/production_session_runner.py`
- `apps/api/local_drama/application/production_session_budgets.py`
- `apps/api/local_drama/application/production_session_review.py`
- `apps/api/local_drama/application/production_choices.py`
- `apps/api/local_drama/application/production_identity_inputs.py`
- `apps/api/local_drama/application/production_asset_inputs.py`
- `apps/web/src/features/production-sessions/`
- `apps/web/src/pages/ProductionFactoryPage.tsx`

### 3.2 原稿流水线自动续接整部生产

已经实现：原稿流水线成功并应用授权章节后，使用输入快照中的 `production_authorization` 创建并启动唯一的 `WHOLE_DRAMA` 生产会话。

涉及文件：

- `apps/api/local_drama/api/schemas/pipeline.py`
- `apps/api/local_drama/api/routes/pipeline.py`
- `apps/api/local_drama/application/pipeline_orchestrator.py`
- `apps/api/local_drama/application/worker_handlers/story_pipeline_apply.py`
- `apps/web/src/features/pipeline/pipelineClient.ts`
- `apps/web/src/features/pipeline/OneClickPipelineWorkbench.tsx`
- `apps/api/tests/test_pipeline_orchestrator.py`
- `apps/web/src/features/pipeline/OneClickPipelineWorkbench.test.tsx`

稳定幂等键：

- 创建：`pipeline:{run_id}:production-session`
- 启动：`pipeline:{run_id}:production-session:start`

没有为此增加新的状态表；沿用 `command_idempotencies` 和生产会话事实。

### 3.3 原稿真实 UAT 准备脚本

新增：`scripts/prepare_production_session_from_source_uat.py`

该脚本会：

- 在线备份现有 SQLite 到新的隔离实例
- 只在克隆库中停止旧的未完成工作
- 创建新项目并写入显式 Profile 偏好
- 写入原稿并提交真实 LLM 流水线
- 授权应用 `STORY_PLAN / STORY_BIBLE / ASSET_PROPOSALS`
- 授权续接到 `WAITING_REVIEW`
- 不启动 worker、不写人工批准、不制造媒体
- 如果主实例存在 `logs/llama/llama_server.pid`，复制该 PID 记录到隔离实例，便于隔离 API 采用已有 llama.cpp 服务；服务管理器仍会自行校验 PID 是否有效

默认已验证 Profile：

| Capability | Profile version |
|---|---|
| `LLM_STORY_PARSE` | `98dae1f1-b625-57a3-aad2-1e448a1b38b9` |
| `IMAGE_CHARACTER` | `bcae6c29-ac73-4d19-9ddf-693f0f60df24` |
| `IMAGE_SCENE` | `a7b62b46-a800-4171-8a09-deb1210cabc3` |
| `IMAGE_CONCEPT` | `d892a257-9459-47bb-9df3-10a3e7428350` |
| `VIDEO_I2V` | `bfade67b-7ef2-4e3e-83a5-2ba77025e952` |

### 3.4 已修复：生产会话身份输入重试规则

文件：`apps/api/local_drama/application/production_sessions.py`

`_retry_prerequisites` 现在只在以下情况要求重新解决机器临时资产身份：

- 条目仍处于 `ASSETS / ASSET_COMPLETION / WAITING_REVIEW`
- 或错误本身属于资产身份输入错误

非身份类的 `PREPARATION / KEYFRAMES / VIDEO` 失败可以复用仍有效的会话临时资产，不会被错误阻塞。最终审核约束保持不变。

回归：`apps/api/tests/test_production_asset_inputs.py`

### 3.5 已修复：多波次任务键重复

文件：`apps/api/local_drama/application/episode_production_runs.py`

当 `wave_count > 1` 时，关键帧和视频动作键追加：

- `:WAVE_0001`
- `:WAVE_0002`
- …

单波次保持旧键，兼容既有幂等语义。此前会触发 `AUTOMATION_BATCH_ITEM_DUPLICATE`。

回归：`apps/api/tests/test_episode_production_runs.py`

### 3.6 已修复：Comfy 异步完成没有收口会话三视图

根因：同步 worker 完成路径会调用 `ProductionIdentityGenerationCompletionService`，但 `ComfyGenerationService._finalize_business_outputs` 只收口资产图和关键帧。真实 Comfy 异步任务成功后，`FRONT / LEFT / RIGHT` Job 和文件都成功，却没有生成 `story_asset_references`、草稿身份包与 `production_session_identity_inputs`。

已修改：`apps/api/local_drama/application/comfy_jobs.py`

- 正常 Comfy 异步完成会调用 `ProductionIdentityGenerationCompletionService.finalize_job`
- 启动恢复扫描会查找成功但没有活动会话身份输入的 `IDENTITY_VIEW_%` Job
- 收口器幂等，支持进程在 Job 成功和业务提升之间崩溃后恢复

已新增回归：`apps/api/tests/test_production_identity_inputs.py::test_comfy_restart_reconciliation_finalizes_session_identity_view`

定向测试已通过：

```text
ruff: PASS
test_comfy_jobs.py + test_production_identity_inputs.py + test_asset_bible.py 相关筛选: PASS
```

真实实例重启后，旧的三视图成功产物已经被补登记：

- identity input：`9b3fe2ab-421b-474b-8876-ff4df1c1a97a`
- story asset：`6849b1a2-59f1-45da-8ed9-82cd110fee1e`
- draft pack version：`7f33f5b6-8b26-4807-abc7-13a79391ccf4`
- 没有人工身份包批准

### 3.7 来源链证据采集器

已扩展：`scripts/capture_production_session_uat_evidence.py`

新增 `--pipeline-run-id`，检查：

- 流水线存在、同项目、`SUCCEEDED`、`APPLIED`
- 提取模式确实为 LLM，模型和 Provider 有记录且无 LLM 错误
- 原始输入授权终点是 `WAITING_REVIEW`
- 创建、启动两个幂等记录都唯一指向当前会话
- 当前重试工作流的终态 Job 没有失败
- 历史失败保留，但不把已被正式重试取代的历史 Job 当成当前终态失败
- 所有 Artifact、被选媒体、预览文件 SHA256
- Comfy prompt 历史
- 没有生产选择人工决定、没有身份包人工批准
- 存在会话临时身份输入

当前中间证据：

`F:\AI_Projects\h3\local_drama_studio\.codex-tmp\source-production-uat-20260921-140825\in-progress-evidence.json`

它当前应为 `IN_PROGRESS_OR_FAILED`，因为会话还未到 `WAITING_REVIEW`。不要提交它作为最终通过证据。

## 4. 已通过的验收与测试

在本轮最后几个修复之前，曾完成：

- 后端全量：1571 passed，1 skipped，4 deselected
- 前端：647 / 647
- 前端 build、lint：PASS
- OpenAPI 生成与合同检查：PASS
- 真实零媒体单集 UAT：PASS
- 真实双集整部 UAT：PASS

已存在正式证据：

- `docs/evidence/g10/production-session-real-zero-media-uat-2026-09-21.json`
- `docs/evidence/g10/production-session-real-whole-drama-uat-2026-09-21.json`
- `docs/evidence/g10/live-database-upgrade-rehearsal-0100-2026-09-21.json`
- `docs/evidence/g10/upgrade-rollback-rehearsal-0100_production_session_waiting_user-2026-09-21.json`

最近对 `comfy_jobs.py` 和证据脚本修改后，只运行了定向回归和 Ruff；必须重新运行全量后端、前端、build、lint、OpenAPI 和 release audit，不能沿用旧全量结果声称当前 HEAD 已通过。

## 5. 真实原稿到整部 UAT 现场

### 5.1 标识

| 对象 | ID / 路径 |
|---|---|
| 隔离实例 | `.codex-tmp/source-production-uat-20260921-140825` |
| 数据库 | `.codex-tmp/source-production-uat-20260921-140825/data/local_drama.sqlite3` |
| project | `d99875b1-524a-44e7-9ce3-16521bb70ed5` |
| pipeline | `pipe-29d7331e8b25` |
| production session | `d949166f-8d5d-48a9-80da-c2b76aca1158` |
| session item | `44f0b302-52b1-45eb-984f-c7feeb9fb746` |
| episode | `b354b873-f35c-4c9a-8c45-f125824b2a0f` |
| current episode workflow | `1976ffd1-24c6-4d50-aa50-2f284e22da23` |
| first shot | `e5e4a548-5775-4cbb-a3dd-d6e51f7265d4` |
| second shot | `8ecce6cc-8807-495c-80ee-49fe7b8ac9ec` |

### 5.2 已验证事实

- 流水线真实调用 Qwen：`Qwen3.8-27B-UD-Q4_K_M`
- Provider：`LLAMA_CPP_MANAGED`
- `pipeline_runs.state = SUCCEEDED`
- `apply_state = APPLIED`
- 生成并应用了 1 集、1 角色、1 场景、1 道具
- 后续真实分集拆解生成了 2 个镜头
- 创建并启动了唯一 `WHOLE_DRAMA` 会话
- HERO、FRONT、LEFT、RIGHT 都是真实 Comfy 产物
- GPU 容量窗口始终只放行一个任务
- 没有人工批准

### 5.3 强制杀 API 的恢复验收

在 HERO Job 运行时强制杀死隔离 API，随后重启：

- Job：`c97e17dc-194c-4be8-b02e-19a061383ae8`
- Comfy prompt：`2006b03e-783d-4c2e-9b59-54a6086524c5`
- Attempt：`b4657cb1...`
- 恢复后仍是同一个 Job、同一个 Attempt、同一个 prompt
- 没有重新提交 Provider
- 输出 PNG 已验证

证据：

`.codex-tmp/source-production-uat-20260921-140825/crash-recovery-before-kill.json`

结果字段：`PASS_REAL_API_FORCE_KILL_PROVIDER_RECONCILIATION`

### 5.4 当前精确状态

截至停止实现时：

- session：`WAITING_USER`
- session stage：`VIDEO`
- session revision：`17`
- item：`BLOCKED`
- item revision：`25`
- item error：`PRODUCTION_SESSION_STAGE_REVIEW_REQUIRED`
- current workflow：`PAUSED_HITL`
- open task ordinal：`10`
- action：`VIDEO_GENERATION:WAVE_0001`
- blocker：`SESSION_KEYFRAME_CANDIDATE_REQUIRED`
- 缺少候选的镜头：`8ecce6cc-8807-495c-80ee-49fe7b8ac9ec`

第一镜头已有机器临时选择：

- choice：`bdd614be-1665-4f1b-8dc4-9659ebad0559`
- candidate：`c2790139-92e0-48b2-9ea1-a46577c7f0f9`
- `choice_type = MACHINE`
- `selection_state = TEMPORARY`
- `human_review_decision_id = NULL`

## 6. 尚未修复的关键帧波次缺陷

### 6.1 现象

两镜头、`dispatch_shots_per_tick = 1`，计划生成两个关键帧波次：

- wave 1 为第一镜头生成候选
- wave 2 本应为第二镜头生成候选

实际两个真实 Job 都为第一镜头生成了候选，第二镜头没有任何 `shot_keyframe_generation_batch_items`。到视频动作时，机器只能选中第一镜头，随后正确地暂停并报告第二镜头缺输入。

两个重复候选 Job：

- `e478a8e7-69c5-4bf5-8802-fcb9caf2bbb9`
- `bd0f54fe-49b2-448e-ba9f-6a22db9ef3c4`

### 6.2 根因

`EpisodeWorkerActionService.keyframe_generation` 只把以下镜头视为已覆盖：

- 已人工批准关键帧
- 当前会话已经完成机器临时选择的关键帧

而机器临时选择只在后续 `KEYFRAME_CHECK` 执行。wave 1 的真实候选已经成功，但 wave 2 执行时还没有 choice，因此再次把第一镜头送进 `ShotKeyframeGenerationBatchService.plan`。

`ShotKeyframeGenerationBatchService._next_candidate_indices` 在候选数已达到目标后仍返回一套新索引，这是交互式“重抽/重画”的既有语义，不能直接全局改成返回空，否则会破坏手动重画功能。

此外存在一个很短的竞争窗口：Comfy 先把 Job 标为 `SUCCEEDED`，再做媒体提升。下游波次可能在 Job 成功与业务收口之间开始，所以只数 `media_versions` 还不够。

### 6.3 推荐的最小修复

不要修改通用交互批次的重画语义。只在自动化入口 `EpisodeWorkerActionService.keyframe_generation` 增加“当前可复用候选数”判断：

1. 用 `eligible_candidate_counts(connection, shot_ids)` 统计当前、未 stale、已验证的 KEYFRAME 媒体。
2. 同时统计 `generation_intents.owner_type='SHOT'`、`purpose='T2I'`、`generation_variants.is_stale=0`、Job 为 `SUCCEEDED` 且存在 `artifacts.status='VERIFIED'` 的 Variant，覆盖 Job 成功但媒体尚未提升的竞争窗口。
3. 每个镜头取两种计数的最大值。
4. 如果计数达到 `candidate_count`，自动化波次把该镜头视为已覆盖，报告 `GENERATED_CANDIDATE_REUSED`，不要再次调用批次 `plan/submit`。
5. 后续 `KEYFRAME_CHECK` 仍负责创建机器临时选择，不写人工批准。

刚才尝试写该补丁时 `apply_patch` 因上下文不匹配失败，**没有任何这部分代码被应用**。接手者必须从当前文件重新实现。

建议函数位置：

- `apps/api/local_drama/application/episode_worker_actions.py`
- 在 `ACTIVE_JOB_STATES / FAILED_JOB_STATES` 下方增加一个小的查询 helper
- 在 `keyframe_generation` 构造 `covered` 前调用

建议测试：

1. 单元测试：没有 approved/choice，但存在 1 个可复用候选时，不调用 `keyframe_batches.plan`。
2. SQL 集成测试：Job 已 `SUCCEEDED`、Artifact 已 `VERIFIED`、媒体尚未提升时，候选数仍为 1。
3. 两镜头、`dispatch_job_limit=1` 集成测试：第一波结束后第二波只提交第二镜头。
4. 候选 stale 后必须重新生成。
5. 手动批次重画测试继续通过，证明未破坏 `_next_candidate_indices` 的交互语义。

### 6.4 视频波次需要同步检查

视频调度目前依赖已提升的 eligible media 计数。关键帧缺陷修复后，继续真实 UAT 时检查视频 wave 1/2 是否也会在“Job SUCCEEDED → 媒体提升”窗口重复第一个镜头。

如果能复现，采用同样的自动化入口层修复：自动生产用 durable Job + verified Artifact 覆盖短暂提升窗口；不要改变用户显式 `NEW_TAKE` 的语义。

## 7. 接手后的执行顺序

### 7.1 先修复并测试关键帧波次

```powershell
.\.venv\Scripts\python.exe -m ruff check `
  apps/api/local_drama/application/episode_worker_actions.py `
  apps/api/tests/test_episode_worker_actions.py `
  apps/api/tests/test_shot_keyframe_generation.py

.\.venv\Scripts\python.exe -m pytest -q `
  apps/api/tests/test_episode_worker_actions.py `
  apps/api/tests/test_shot_keyframe_generation.py `
  apps/api/tests/test_episode_production_runs.py
```

### 7.2 重启隔离 API

当前隔离 API：

- port：`3222`
- PID：`78928`
- Codex exec session（本次会话内）：`67748`

启动命令：

```powershell
$env:LOCAL_DRAMA_INSTANCE_ROOT=(Resolve-Path '.codex-tmp\source-production-uat-20260921-140825').Path
$env:LOCAL_DRAMA_COMFY_INPUT_ROOT=(Resolve-Path 'work\comfy-production\input').Path
$env:LOCAL_DRAMA_COMFY_OUTPUT_ROOT=(Resolve-Path 'work\comfy-production\output').Path
.\.venv\Scripts\python.exe -m local_drama.entrypoints.api --host 127.0.0.1 --port 3222
```

### 7.3 正式重试当前条目

不要直接改数据库。调用：

`POST /api/v2/production-sessions/{session_id}/items/{item_id}:retry`

当前预期 revision：

- `expected_session_revision = 17`
- `expected_item_revision = 25`
- `strategy = RETRY_FAILED_STAGE`

写 API 需要：

1. 先 GET `/api/v1/session/bootstrap`
2. 读取 `X-Local-Instance-Token`
3. 写请求携带：
   - `X-Local-Instance-Token`
   - `X-API-Contract-Version: localdrama.api.2026-08-29.3`
   - 唯一 `Idempotency-Key`

重试后预期：

- 前置拆解、资产、身份输入复用
- 已有第一镜头候选复用
- 只新增第二镜头关键帧
- 两个镜头都生成机器临时选择
- 视频按容量窗口逐镜生成
- 合成预览
- 会话最终进入 `WAITING_REVIEW`

### 7.4 生成最终来源链证据

```powershell
.\.venv\Scripts\python.exe scripts/capture_production_session_uat_evidence.py `
  --instance-root .codex-tmp/source-production-uat-20260921-140825 `
  --session-id d949166f-8d5d-48a9-80da-c2b76aca1158 `
  --pipeline-run-id pipe-29d7331e8b25 `
  --output docs/evidence/g10/production-session-real-from-source-uat-2026-09-21.json
```

只有输出满足以下条件才能作为通过证据：

- `result = PASS_REAL_RAW_SOURCE_TO_WHOLE_DRAMA_REVIEW`
- `failures = []`
- session 为 `WAITING_REVIEW`
- 所有预览可 ffprobe
- Artifact、selected media、preview SHA256 全部匹配
- Comfy history 全部 success
- `human_approval_written = false`
- `human_identity_pack_approval_written = false`

## 8. 24 小时 soak

记录器仍在后台运行：

- PID：`88496`
- session：`42ee2489-caf8-4ba4-a815-627c93def336`
- 文件：`work/evidence/production-session-real-soak-42ee2489-caf8-4ba4-a815-627c93def336.json`
- 开始：`2026-09-21T04:09:15.811178Z`
- 本交接时观测约：`9940.94` 秒
- 当前：`IN_PROGRESS`
- `real_24h = false`
- recorder restart count：`0`
- 被监控会话已经稳定在 `WAITING_REVIEW`

只有同时满足以下条件才能写 `PASS_REAL_24H`：

- `observed_duration_seconds >= 86400`
- 记录器自然完成
- 最终文件明确写 `PASS_REAL_24H`
- 数据库完整性与 Artifact 校验通过

不要因为会话已经进入 `WAITING_REVIEW` 就提前结束或伪造 24 小时结论。

## 9. 当前相关进程

| PID | 用途 | 处理建议 |
|---:|---|---|
| `78928` | 隔离来源 UAT API，port 3222 | 接手修改代码后重启 |
| `88496` | 真实 24 小时 soak recorder | 保持运行 |
| `49636` | llama gateway，port 28088 | 保持运行 |
| `58516` | 旧的独立 API，port 3211 | 与本 UAT 无关，不要杀 |

ComfyUI 在 `http://127.0.0.1:8188`。此前存在两个 Comfy Python 进程，未由本轮启动，不要随意结束。

## 10. 全量验收清单

真实来源 UAT通过后运行：

```powershell
.\.venv\Scripts\python.exe -m ruff check apps/api/local_drama apps/api/tests scripts
.\.venv\Scripts\python.exe -m pytest -q apps/api/tests
```

前端使用仓库既有命令执行：

- 全量单测
- lint
- build
- bundle budget
- 生成客户端和 OpenAPI 漂移检查

再执行：

- migration contract
- release audit
- 0100 从真实旧库升级与回滚演练检查
- 关键帧、视频、预览文件人工抽查
- 机器临时选择与人工审核事实隔离检查
- 局部返工只失效受影响下游、不重跑整集检查

更新这些文档：

- `docs/gpt/LocalDramaStudio_一键生产与持续运行开发方案_2026-09-21.md`
- `docs/operations/production-session-soak.md`
- `scripts/README.md`
- `scripts/release_audit.py` 的新证据门禁

## 11. 当前停止点

本交接完成后不再继续实现、不再重试会话、不终止后台 soak。当前数据库停在可复现的真实 blocker 上，适合接手模型直接定位和验证修复。

