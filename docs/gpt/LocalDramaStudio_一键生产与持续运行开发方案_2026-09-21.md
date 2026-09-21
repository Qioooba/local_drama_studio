# LocalDramaStudio 一键生产与持续运行开发方案

日期：2026-09-21  
交付对象：项目负责人及后续实施的 Sol 模型  
性质：基于实际源码的产品分析、增量开发设计、重构边界与验收任务书；不是已实现功能说明。

## 0. 先确定要做成什么

建议把项目发展为**可以持续生产待审成片的本地工作台**：用户设置一次本次制作范围、候选数量和运行预算，机器自动补齐必要材料，逐镜生成并选择可用候选，完成声音、字幕、整集预览；遇到可恢复失败自动处理，遇到局部问题记录后继续其他可生产内容，最终由用户集中审片、换候选、局部返工和批准交付。

这里的“一键”是提交一份明确、可恢复的生产计划。它不等于在浏览器里依次调用十几个接口，也不等于把所有镜头一次性塞进 GPU 队列。

最值得优先开发的是：

1. **一键补齐本集关键帧**：已有批量生成能力，主要补入口、缺口计算、首尾帧角色识别、恢复与自动暂用。
2. **一键生成本集预览片**：连接已有生成、TTS、字幕、时间线和渲染，中途不再因普通创作审核逐镜停住。
3. **一键生成所选集／整部预览片**：持久化项目级生产计划，有限投放，失败隔离，跨集共享资产。
4. **一键继续未完成／补抽问题镜头／重新合成**：准确计算影响范围，保护已经认可的结果。
5. **集中审核与有上限的抽卡**：机器先暂用，用户最后集中选择和批准。

保留 React、FastAPI、SQLite WAL、现有 Worker、ComfyUI、本地 LLM、TTS、FFmpeg 和原生 Runtime Host。第一轮不引入 Redis、Celery、Temporal、微服务、多机集群、通用节点编辑器、多 Agent 编剧系统或云计费。

### 0.1 本次分析的基线与可信范围

- 仓库：`F:/AI_Projects/h3/local_drama_studio`。
- 分析开始时 HEAD：`04829fe`，提交说明为 `fix(reliability): draft guard coordination, asset batch idempotency, task retry feedback`。
- 源码迁移 head 经 `alembic heads` 核实为 `0094_project_target_duration`，与 `docs/release/migration-contract.json` 一致。这不是对用户运行数据库版本的检查。
- 工作区有既存未提交改动，分析期间后期、制作规格等文件还出现其他修改。因此本文以**观察到的工作树**为依据，不能假定它等于上述提交的纯净快照。Sol 实施前必须重新检查 diff 和等价实现。
- 阅读覆盖：故事流水线相关入口、资产与关键帧批次、整集及整剧编排、人工审核、身份包、候选选择、时间线、任务恢复、GPU 协调、路由和相关测试。没有宣称逐行审计整个仓库。
- 页面入口判断来自实际 React 源码；本次没有进行完整浏览器或真实 GPU 的端到端验收。
- 本文中的“新增”“建议接口”“示意代码”均为待实施设计；现有代码路径在第 1 节列出。

### 0.2 阅读顺序

负责人先读第 1—4、12、13 节；Sol 从第 0 节读到第 15 节后按任务包逐项实施。不要只复制示意代码。本文所有代码中的新方法都要与现有端口、事务和迁移规范对齐。

## 1. 当前项目真正已有的能力与缺口

### 1.1 功能盘点

下表路径均相对于仓库根目录。

| 环节 | 已核实的代码事实 | 真实缺口与建议 |
|---|---|---|
| 小说全剧分析 | `application/pipeline_orchestrator.py`、`story_pipeline_ai.py`、`worker_handlers/story_pipeline_draft.py`；前端 `features/pipeline/OneClickPipelineWorkbench.tsx` | 已有，不重写提取器；补与后续生产计划的衔接以及自动继续策略 |
| 故事草稿应用 | `worker_handlers/story_pipeline_apply.py` 与现有 draft/apply 合同 | “文本结构已应用”不等于“成片已批准”；无人值守只能在用户本次明确选择的自动应用范围内推进 |
| 本集方案准备 | `application/episode_preparation.py:prepare()`；已有 `automatic_apply=True` 的拆解 Job，已有就绪草稿也会调用 `BreakdownApplyService` | 不是所有前期操作都必须人工再点一次；要复用此入口，并补项目级持久化调度 |
| 分镜就绪确认 | `application/episode_shot_ready.py`；整剧准备调用 `confirm(..., auto_heal=True)` | 区分结构修正和创作改写，不能将未知剧情或人物关系靠默认值补成“合格” |
| 核心资产主图批量生成 | `application/asset_image_generation.py`；`features/asset-bible-v2/AssetImageBatchWorkbench.tsx` | 已有 plan/submit、批次、Job、完成登记和不覆盖现有主参考的机制；不是零起点 |
| 身份包和连续性 | `application/character_identity_packs.py`、`shot_identity_references.py` | 有严格的当前已批准身份包校验；自动主图不等于身份包已完成，夜间全流程会在这里受阻 |
| 单镜和批量关键帧 | `application/shot_keyframe_generation.py`；`ShotGenerationInspector.tsx`、`StoryboardBatchActions.tsx` | 已支持首帧、首尾帧、每项 1—4 候选；入口折叠且需选镜。应提升可发现性，增加“整集补缺”和生产暂用 |
| 批量视频候选 | `application/storyboard_generation_batches.py`；`storyboardGenerationBatchApi.ts` | 已有批量新增 Take，不应另做独立视频队列；需区分补缺、技术重试、创作重抽 |
| 整集生产 | `application/episode_production_runs.py`、`episode_worker_actions.py`、`worker_handlers/automation_task.py` | 已有完整后半链路，但关键帧审批、身份包、前期事实、候选采用等环节会停；存在运行结束判定等可靠性边界需优先验证 |
| 整集模式 | `PRODUCTION_MODE_POLICIES`：DRAFT／BALANCED／QUALITY；典型候选 1／2／4，受 Profile 策略调整且视频上限 4 | 草稿和平衡已有自动选择视频，精品默认不自动选择；质量档和审核时机耦合，应拆开 |
| 较新的跨集调度 | `application/whole_drama_orchestrator.py`；`/api/v2/projects/{id}/whole-drama:run` | 已逐集调用整集生产；项目级持久计划、容量窗口、持续补投、独立历史状态还不足 |
| 故事页小样入口 | `OneClickPipelineWorkbench.tsx` 调用 `runWholeDrama`，显式选择最多 2 集 | 应保留“小样”价值，增加项目首页的正式整部生产入口，不把小样按钮直接改成无限范围 |
| 旧整剧模板 | `AutomationWorkflowService.create_from_template()` 的 WHOLE_DRAMA | 旧模板只有 `KEYFRAME_CHECK → TTS_BATCH → RENDER → DELIVERY`，不是新的完整生产链；保留兼容，不能包装成完整小说到成片 |
| 声音和字幕 | `dialogue.py`、`worker_handlers/automation_task.py` 中 TTS_BATCH、TTS_FINALIZE、SUBTITLE；`timeline.py:plan_tts_subtitle_draft()` | 已有整集 TTS 与自动字幕；补候选策略、失败恢复、声音授权缺口、音画时长预检 |
| 自动时间线 | `timeline.py:assemble_episode_timeline()` | 已可自动组装首版并冻结；继续支持人工剪辑，新增生产范围内明确引用候选的预览组装入口 |
| 自动渲染与交付 | `timeline.py`、`episode_compose.py`、`delivery_build.py` | 已有；增加“预览完成待审”的终点，正式交付继续走人工审核 |
| 机器 QC、审核、批量审核 | `application/reviews.py`、`review_decisions.py`、`EpisodeReviewWorkspace.tsx` | 技术 QC 和人工批准已经分离；集中审核页应复用，不能复制另一套审批数据 |
| 持久队列与恢复 | `jobs.py`、`worker_sessions.py`、`episode_production_runs.py:watchdog()/recover()` | 已有租约、退避、崩溃恢复、存储 reconciliation；需补新的父计划和所有子 Job 的关联恢复 |
| 24 小时运行基础 | `entrypoints/worker.py --watch`、`cmd/runtime-host`、GPU runtime coordinator | 常驻运行底座已有；缺产品化运行预算、调度窗口、机器状态总览、阶段最终态和长期验收 |

上述后端 `application/` 均在 `apps/api/local_drama/application/`，前端 `features/` 均在 `apps/web/src/features/`。

### 1.2 最重要的源码断点

**A. 关键帧生成与视频入口之间仍有人工硬停点。**

`EpisodeWorkerActionService.keyframe_generation()` 会生成候选，但明确不批准；随后 `EpisodeFrontHalfActionService.keyframe_check()` 调用 `approved_keyframes_for_shots()`。后者要求 `media_assets.approved_version_id`、非 stale 的 `ReviewDecision(APPROVED)` 和 VERIFIED 媒体同时成立。单纯选择 `AUTO_CONTINUE` 不能消除这一要求。

正确改法：保留旧 approved 查询的语义，给新无人值守生产增加“本次生产可以使用的候选”查询。不要把 `approved_keyframes_for_shots()` 改成返回任意 VERIFIED 图片。

**B. 身份包也是独立前置门槛。**

`CharacterIdentityPackService.generation_snapshot_for_intent()` 要求角色身份包当前、完整、已批准、媒体有效。资产主图自动生成只是其中一部分。只放开关键帧审核仍不能实现“原稿上传后一直运行到成片”。第 7 节明确给出分阶段兼容方案。

**C. 现有整剧服务主要是同步分发，不能等同于可恢复的整剧生产会话。**

`WholeDramaOrchestratorService.run()` 使用进程内锁和 `command_idempotencies`；`_run_once()` 循环准备并启动子集，完成后才保存总响应。子集有持久 Job，但父级缺少逐项持久意图、窗口和补投游标。崩溃在分发中途、多个 API 进程竞态、失败集稍后再准备、范围增减，都需要清楚的恢复规则。

`inspect()` 按每集最近 workflow 推导状态，可能混入其他运行或局部操作。新的生产页必须按明确 session 及关联子 run 查询，不能继续把“最新任务”当本次整剧的权威。

**D. 旧 workflow 的 task/run 状态有提前完成风险，不能直接作为新父计划完成依据。**

`AutomationWorkflowService.step_run()` 在投放最后一项时就可能设置 run 为 SUCCEEDED，且有限批次耗尽判断在最终机器结果条件处理之前。现有 task 的 status 也不是单纯的 Job 执行状态。Sol 应先用“一项／两项 workflow，最后一个 Job 未完成或最后报告失败”的测试确认当前行为，再按第 5 节给新执行语义加版本。

本次另在隔离临时数据库复现：一项有限 workflow 的 run 已为 SUCCEEDED，唯一 Job 仍为 QUEUED，Attempt 数为 0。它证明提前完成语义确实存在；没有运行真实模型或故障演练。不能仅修改页面显示来修复底层完成语义。

**E. 关键帧批次要补强幂等和部分提交恢复。**

`ShotKeyframeGenerationBatchService.submit()` 先按 episode/idempotency key 返回旧批次，没有像资产批次一样验证规范化请求 payload hash；记录 PLANNED items 后逐个提交 Job，再更新关联。应测试同 key 不同参数、并发双击、写 Job 后关联前崩溃，以及 PLANNED 永久悬挂。先参考已经修补过的 `asset_image_generation.py` 和 `test_asset_image_idempotency.py`，不要另创协议。

**F. 多候选不等于自动挑最优。**

现有 `_shot_video()` 已有过滤 stale、优先既有机器检查等逻辑，相关测试包含“旧 PASS 候选胜过新失败候选”。但 `qc()` 并不等于逐个候选完整审美排序。自动重抽后仍可能报告 NEEDS_HITL，而不是等待重抽完成并自动复检。要在现有筛选基础上增加有限循环，不要删掉已有修补。

**G. 单个工作槽无法表达首尾帧成对选择。**

现有 `shot_working_media_slots.KEYFRAME` 只表示一个当前关键帧，而批次有 FIRST_FRAME、END_FRAME。批准查询也不按 frame_role 区分。自动流水线必须按角色精确引用，不能因为“找到了最后批准的关键帧”而把尾帧当首帧；第 6、8 节给出方案。

**H. 当前静态上限不一致。**

关键帧单批最多 100 镜，局部操作 target_shot_ids 也最多 100，而时间线读取有 500 镜上限。整集自动流程不能把全量镜头直接传给 100 镜 API。生产计划需要按批分页投放；第一版不提高 500 镜的单集剪辑上限，超过时明确建议重新分集。

### 1.3 外部模式参考

火宝短剧官方仓库当前说明了资产参考图注入、批量视频生成、失败重试和 FFmpeg 拼接。这些适合借鉴为“用户按生产阶段操作、批量查看结果”的产品流程。[火宝短剧官方仓库](https://github.com/chatfire-AI/huobao-drama)

Toonflow 官方仓库当前强调策划到出片、事件结构化、生产画布及可回溯编辑。适合借鉴的是保留原文上下文和局部修订能力；本项目已有导演工作台和 Visual Lab，无需为一键生产再增加无限画布。[Toonflow 官方仓库](https://github.com/HBAI-Ltd/Toonflow-app)

这些是官方 README 的功能说明，不是本次对其可靠性或连续运行能力的实测；不引用“秒出整剧”“效率倍增”之类宣传作为容量依据。本项目保留 LOCAL_ONLY 和本地模型体系，不移植其云 Provider 或计费逻辑。

## 2. 一键功能应该加在哪里

### 2.1 功能清单与优先级

P0 表示无人值守前必须具备；P1 表示完成主要产品闭环；P2 表示有数据和需要后再做。

| 功能 | 用户看见的名称 | 推荐位置 | 优先级 | 具体范围 |
|---|---|---|---|---|
| 本集关键帧补缺 | 补齐本集关键帧 | 分集策划／镜头页工具栏、本集制作准备卡 | P0 | 当前计划全部镜头，跳过可复用有效结果；不是每点一次全量新增 |
| 所选镜头关键帧 | 生成所选关键帧 | 分镜表选中后的批量工具栏 | P0 | 提升现有折叠入口；首帧／首尾帧、数量在弹窗设置 |
| 本集预览片 | 生成本集预览片 | `EpisodeProductionWorkspace` 顶部主操作 | P0 | 补材料→视频→声音字幕→冻结预览时间线→渲染→待审 |
| 自动暂用候选 | 自动挑选并继续制作 | 启动面板的审核策略 | P0 | 机器选择只属于本次生产，随时人工换掉 |
| 恢复缺失产物 | 继续未完成 | 本集／整部生产页运行卡 | P0 | 沿用原 session，原输入技术重试与新输入补生成分开 |
| 整剧准备 | 补齐制作准备 | 项目首页生产区／资产页缺口卡 | P1 | 已应用故事后的资产参考、分集方案、绑定和技术检查 |
| 所选集或整部 | 生成所选集／生成整部预览片 | 项目首页分集列表上方 | P1 | 一份持久 session，不要求页面保持打开 |
| 夜间持续生产 | 连续运行 | 同一启动面板和生产页 | P1 | 是运行策略，不另做一个含义不同的“工厂启动”按钮 |
| 集中审片 | 审核本次结果 | 项目生产页，跳到已有分集后期审核 | P1 | 按集预览、风险镜头、候选对比、人工批准 |
| 局部补抽 | 补抽所选问题镜头 | 审核页与镜头页 | P1 | 生成新 Variant，保留全部既有版本 |
| 自动补声音 | 补齐本集配音 | 后期声音页 | P1 | 当前对白 revision 缺失或 stale 的行；选项沿用生产计划 |
| 字幕更新 | 按当前配音更新字幕 | 声音／编辑页 | P1 | 复用权威文本与对齐；只创建新的字幕 revision |
| 重新合成 | 按当前选择重新合成 | 本集制作／后期编辑 | P0/P1 | 第一版保留现有 RECOMPOSE_ONLY，并精确区分重渲染和重建时间线 |
| 批准后整部导出 | 导出已批准分集 | 项目生产页完成区 | P1 | 按冻结清单导出，缺批准或文件损坏的集明确列出 |
| 批量口型同步 | 同步所选对白镜头口型 | 后期声音页高级操作 | P2 | 仅适合有正脸、单主说话人和已对齐音频的镜头 |
| 风格模板与包装 | 应用片头片尾／字幕样式 | 项目制作设置与后期编辑 | P2 | 本地已有素材与参数模板，先不自动创作 BGM |
| 存储整理 | 检查并整理临时文件 | 系统诊断／存储 | P2 | 先显示可回收清单；不自动删除未选候选或用户素材 |

### 2.2 页面组织

保留现有“项目 → 故事 → 资产 → 本集制作 → 镜头 → 后期 → 交付”的工作方式。

- **项目首页**增加一块生产汇总，放在“建议下一步”之后、分集列表之前；这里承载整部和所选集入口。
- **项目生产页**新增一个必要路由 `/projects/:projectId/production`，统一展示本次及历史 session。它不是新的创作编辑器。
- **本集制作页**仍是单集运行与修复中心；一个主按钮“生成本集预览片”，旁边“更多操作”包含补关键帧、补配音、补抽、重新合成。
- **镜头页**在选镜工具栏直接展示“生成关键帧”“生成视频候选”；统一修饰词留在高级区域。
- **故事页**保留最多两集的小样入口，结果就绪后显示“加入整部生产”。小样产物可按指纹复用。
- **系统任务页**继续显示底层 Job。创作者默认看分集和镜头进度，不被几十种 Job type 淹没。

前端实现主要改动点：

```text
apps/web/src/pages/ProjectHomePage.tsx
apps/web/src/features/projects/EpisodeProgressLibrary.tsx
apps/web/src/features/episode-production-v2/EpisodeProductionWorkspace.tsx
apps/web/src/features/director-v2/StoryboardBatchActions.tsx
apps/web/src/features/pipeline/OneClickPipelineWorkbench.tsx
apps/web/src/features/episode-review-v2/EpisodeReviewWorkspace.tsx
apps/web/src/app/routeRegistry.ts
apps/web/src/app/router.tsx
apps/web/src/layouts/AppShell.tsx

新增 apps/web/src/features/production-session/
  ProductionLaunchDialog.tsx
  ProjectProductionWorkspace.tsx
  ProductionSessionSummary.tsx
  ProductionSessionEpisodes.tsx
  ProductionReviewSummary.tsx
  productionSessionKeys.ts
  useProductionSessionQueries.ts
```

第一版审核汇总放在项目生产页内，通过 episode_id、shot_id、media_version_id 深链已有审核页；无需再新增全功能“审核中心”路由。

### 2.3 启动面板

默认折叠高级项，先让用户只做四项选择：

```text
生成整部预览片
范围        所有未完成分集 / 所选 8 集
制作档位    草稿 / 平衡 / 精品
审核时机    完成后集中审核 / 每阶段确认
完成目标    关键帧 / 有声预览片

预计新生成  96 张关键帧、48 个视频、126 条配音
可以复用    24 张关键帧、12 个视频
待处理      1 集人物绑定缺失；其他 7 集可开始
运行预算    24 小时，最多 600 个新任务，至少保留 20 GiB

[高级：候选数、帧策略、预算、失败处理、音频、停止时间]
[开始生成 7 集，1 集待处理]
```

以上数量是界面示意，实际必须来自服务端 plan。没有历史运行数据时显示“耗时暂无法估计”，不能编造准确完成时间。存在部分阻塞时按钮必须写明实际可启动范围，不能静默只做一部分。

用户已经预检并配置本次自动运行后，不要每阶段弹重复确认；只有人工策略指定的停点或不可自动处理的问题才需要介入。

### 2.4 运行与审核的展示

```text
照骨灯 · 本次生产 12 集
运行中：第 3 集视频 9/18 镜       资源：GPU 正在生成
预览完成 2 集 | 进行中 1 集 | 排队 7 集 | 待处理 2 集

第 1 集  预览完成待审   [播放] [审核]
第 2 集  预览完成待审   [播放] [审核]
第 3 集  视频生成 9/18  [查看镜头]
第 4 集  待处理：角色引用缺失 [前往资产]

[暂停投放] [继续运行] [结束本次生产]
已生成的候选和预览会保留。
```

- 不用一个 SUCCEEDED 同时代表“提交成功”“文件完成”“人工通过”。
- “机器通过”“机器暂用”“人工采用”“人工批准”使用不同文案，沿用现有设计系统的状态色。
- 暂停默认让当前执行单元收尾，不再投放下一项；取消走现有 Job 取消语义。
- 预览未齐时显示“部分材料已完成”，不能把缺镜头的拼接标成完整预览片。
- 关键问题列出原因、影响范围和返回修复入口。技术 UUID、hash、Profile 详情放在展开信息内。
- 遵循现有 `design-system/localdramastudio/MASTER.md`，保留本地字体、密度和键盘操作；不做全站视觉重构。

## 3. 一键动作的统一语义

### 3.1 三个维度分开

| 维度 | 建议值 | 含义 |
|---|---|---|
| 质量档位 | DRAFT／BALANCED／QUALITY | 候选数量与现有 Profile 参数，不决定是否必须人工逐镜点选 |
| 审核策略 | GUIDED／FINAL_ONLY | 每阶段确认，或机器先制作预览、最后集中审核 |
| 目标产物 | KEYFRAMES／EPISODE_PREVIEW | 只生成关键帧，或完成本集有声／静音预览片 |

正式交付沿用现有 delivery 的审核和打包流程；第一版不把“自动批准交付”列为目标。

旧 `checkpoint_policy`、`production_mode` API 含义不变。新策略只通过新 session 合同启用：旧 QUALITY 仍可维持人工选择，新 session 的 QUALITY + FINAL_ONLY 可以多抽后机器暂用。不得让升级后旧用户的精品任务突然自动采用候选。

### 3.2 默认策略

建议新品入口默认 `BALANCED + FINAL_ONLY + EPISODE_PREVIEW`，且自动补齐、暂用范围在启动面板明示。首次开放时只有完成相应任务包验收后才能展示；未通过验收的能力显示“尚未支持”，不要用 disabled 按钮伪装已完成。

候选默认建议：关键帧 2、视频 1；精品关键帧 3、视频 2。现有 Profile 或用户显式策略优先，最终值由后端返回。1—4 的现有单次上限保留。视频成本通常更大，不应简单把“平衡=所有阶段都生成 2 份”。

每镜先保证一个技术可用版本，再做额外抽卡。第一版不要求每个镜头都用首尾帧，不强制每个说话镜头都做口型同步。

### 3.3 三条落地用户流程

**流程 A：当前用户最容易获得价值的路径。** 已有全剧规划、角色资产和分集拆解 → 一键补关键帧 → 自动暂用 → 视频 → 配音字幕 → 预览片 → 集中审核。第一阶段优先支持当前已批准身份包。

**流程 B：整部持续生成。** 用户选一组分集或整部 → 服务端冻结分集清单和预算 → 依次补齐准备、以容量窗口投放 → 问题集挂起，其他集继续 → 第二天集中审片。页面关闭不影响。

**流程 C：原稿到待审整部。** 用户选原稿和自动应用结构范围 → 复用故事 draft/apply、分集准备、资产主图及身份包生成 → 冻结本次临时角色参考 → 按流程 B 推进。此流程依赖第 7 节身份包和前期衔接任务完成；不能在只完成 A 时声称全流程无人值守。

### 3.4 所有一键必须遵守的规则

1. “补齐”只做缺失、失败或按本次计划判定过期的内容；“补抽”才新增创作候选。
2. 人工当前选择、人工批准和锁定结果优先，机器不能悄悄覆盖。
3. 同一请求重发获得同一结果；参数改变必须新计划、新幂等键。
4. 技术重试复用原 Job／冻结输入，增加 Attempt；创作重抽创建新 Variant 和新 Job。
5. 运行只使用明确版本和依赖，禁止在执行最后一步才取“最新文件”。
6. 自动修正只做允许清单内的字段正规化；不自动改变剧情、对白内容、人物关系、已确认的画幅。
7. “连续运行”必须有时长、任务、候选和磁盘边界；有限项目完成后可以空闲，不用无限抽卡维持 GPU 利用率。

## 4. 最小架构和职责划分

### 4.1 复用现有执行体系

```text
项目首页／本集制作／镜头页
        │ 同一启动面板与 plan/submit
        ▼
ProductionSessionService        新：本次范围、策略、预算、父级状态
        │
        ├─ Project bootstrap     复用故事、资产、分集准备服务
        └─ Episode run          复用 EpisodeProductionRunService
                 │
                 ▼
       AutomationWorkflowService 新执行语义版本的有限阶段链
                 │
                 ▼
         JobService + job_dependencies
                 │
                 ▼
       既有 CPU／GPU Worker 和 runtime coordinator
                 │
                 ▼
       MediaVersion／TTS／TimelineRevision／Render
                 │
                 ▼
       本次机器暂用记录 → 集中审核 → 原有人工批准与交付
```

父 session 只负责项目范围、调度与汇总，不重复实现 Job 的 QUEUED/RUNNING/Attempt、GPU 租约和执行器。已有旧 run 继续由旧逻辑运行；新增 session 的 episode run 明确携带版本。

### 4.2 模块安排

| 模块 | 职责 | 不应做什么 |
|---|---|---|
| 新 `application/production_sessions.py` | plan、submit、pause、resume、cancel、replan、状态汇总 | 不运行 ComfyUI／LLM，不长事务等待外部执行 |
| 新 `application/production_session_scheduler.py` | 小步 reconciliation、预算检查、认领可投放项、关联子 run | 不实现第二套 GPU 队列 |
| 新 `application/production_choices.py` | 候选资格、机器暂用、精确版本、替换影响 | 不写人工 APPROVED、不建立第二套媒体库 |
| 新 `application/production_outcomes.py` | 阶段报告合同、目标产物完成验证 | 不猜测已完成，不能只按任务数统计 |
| 新 `infrastructure/database/production_session_repository.py` | session、item、job link、choice 的事务和读模型 | 不解析提示词和模型参数 |
| 现有 `episode_production_runs.py` | 为新 session 构建有版本的单集阶段清单，旧 facade 保留 | 不继续无限增大；新策略整理到小模块 |
| 现有 `episode_worker_actions.py` | 复用生成／QC 服务，小步推进有限候选批次 | 不在 CPU Job 中 sleep 等 GPU |
| 现有 `whole_drama_orchestrator.py` | 保留旧接口；新 UI 改调 session | 不同时再维护第二份父状态 |
| 现有 `worker_sessions.py` | 在后台调度周期调用 session reconciliation | 不依赖浏览器轮询触发生产 |

新增端口限定为实际测试替换需要的 repository、clock、existing generation 服务端口，沿用项目当前 ports 习惯。不要为每个三行辅助函数建立接口和工厂。

### 4.3 重构顺序

先补事实合同与测试，再接已有能力，最后挪动入口。不要一开始把 `generation.py`、`timeline.py` 和整个模型平台拆成新架构。

只抽取被新旧入口共同调用、或需要隔离新旧语义的部分，例如：候选资格判断、按角色查关键帧、阶段完成条件、生产计划规范化与父级调度。现有 `ReviewService`、媒体登记和 TTS 不重写。

## 5. 阶段状态和真正的完成条件

### 5.1 新单集流程

为新 session 构建 `execution_semantics_version=2` 的有限阶段链。版本号是新设计，不是当前仓库已存在的字段。

```text
PREPARE_EPISODE
  → ENSURE_ASSET_INPUTS
  → KEYFRAME_GENERATION
  → KEYFRAME_RESOLVE
  → VIDEO_GENERATION
  → VIDEO_RESOLVE
  → TTS_BATCH
  → TTS_FINALIZE
  → SUBTITLE
  → TIMELINE_ASSEMBLY
  → RENDER
  → PREVIEW_VERIFY
  → PREVIEW_READY
```

GUIDED 策略在配置停点进入 WAITING_USER。FINAL_ONLY 仅把纯创作选择交给机器暂用，不跳过文件完整性、能力、来源、声音授权和硬连续性检查。

`KEYFRAME_RESOLVE` 和 `VIDEO_RESOLVE` 可以由现有关键帧检查／QC handler 加模式实现，不必新增模型；输出仍保留机器检查和人工批准的区别。静音模式跳过 TTS 与对应字幕；如果用户要求有声预览，缺声音不能静默降级成“已完成”。

**关键帧目标**执行到 KEYFRAME_RESOLVE 后验证每个必需 frame_role 即可结束，不要求视频模型、TTS 和 FFmpeg 全部就绪。

### 5.2 阶段报告合同

当前 task report 的 PASS 有时表示“已投放子任务”，这不足以判断阶段完成。新语义采用以下结构，旧报告仍按 v1 解读：

```python
@dataclass(frozen=True)
class ProductionStageResult:
    # 新合同示意，需接入现有 StrictModel / JsonObject 序列化规范
    state: Literal["COMPLETE", "WAITING", "BLOCKED", "FAILED", "SKIPPED"]
    code: str
    dependency_job_ids: tuple[str, ...] = ()
    output_refs: tuple[dict[str, object], ...] = ()
    next_check_at: str | None = None
    blockers: tuple[dict[str, object], ...] = ()
```

- COMPLETE：本阶段目标已经存在且验证通过。
- WAITING：子 Job 或资源还未完成；不会推进到下一阶段，不会写人工待批准。
- BLOCKED：某个必要条件需用户处理或改变输入；保留原因、scope 和修复路由。
- FAILED：自动恢复额度用尽或不可恢复执行失败。
- SKIPPED：本次计划明确关闭，或已有符合本次指纹的产物，必须记录 `skip_reason`；不可把任意缺失视为跳过。

机器质量信息独立存放为 PASS／FAIL／UNKNOWN，不拿审美分数生成 APPROVED。

### 5.3 复用现有 task 表而非新建通用 DAG

新版本的 workflow task 一行对应一个阶段。使用现有 `automation_workflow_run_tasks.status` 表示上述阶段执行状态；增加最少的 `execution_state_json`，保存有限抽卡轮次、待完成 Job、下次检查时间、结果引用与最后消费的报告 Job ID。已存在的 `job_id` 指向当前阶段报告 Job；被替换的报告 Job 通过已有事件及显式 job link 保留。

阶段 handler 提交需要的生成 Job 后返回 WAITING，其 CPU Job 正常完成并释放 CPU。后台 reconciliation 观察到依赖有变化后，只为这个阶段投放一次新的轻量报告 Job，再调用同一个幂等 handler。无需更改已经完成的 Job，也不反复创建“等待”Attempt。

注意不能让复检 Job 对所有候选 Job 使用“必须全部 SUCCEEDED”的硬依赖：某个候选失败后它仍需要执行以判断是否已有足够成功候选。处理方式是：scheduler 从数据库确认依赖均已进入终态，或者已满足早停条件，再投放复检 Job。Job 硬依赖只用于确实要求成功的输入步骤。

V2 cursor 只在阶段 COMPLETE 或合法 SKIPPED 后前进；WAITING 保持原 ordinal。原版 `max_tasks=len(actions)+1` 不能限制所有媒体子任务；将阶段投放上限和真实 Job 预算分开。补抽次数用单独预算，不能借无限增加 report Job 绕过上限。

旧定义没有 `execution_semantics_version` 时按 v1 处理；新定义带 2。旧模板不就地改写。代码可由 `step_run()` 根据冻结 metadata 分发到一个私有 v2 小步推进函数，不需要建立另一个工作流引擎。

### 5.4 单集与整部状态

新 session 的状态建议为：

| 状态 | 含义 |
|---|---|
| QUEUED | 意图已保存，尚未开始 |
| RUNNING | 有可推进或在执行的工作 |
| PAUSED | 用户暂停或预算已到期；必须附明确 reason，可能仍有当前 Job 正在收尾 |
| WAITING_USER | 没有其他可生产项，剩余工作需要人工处理 |
| WAITING_RESOURCE | 磁盘／runtime 等暂时不足，按计划等待 |
| READY_FOR_REVIEW | 本次目标产物齐全，等待人工审片；不表示批准 |
| PARTIAL_REVIEW | 可生产项已完成，部分范围失败或被阻塞，汇总可供审核 |
| CANCELLED | 本次取消已收敛，仍保留产物 |
| FAILED | 父级不可恢复错误，附原因 |

取消中不直接标 CANCELLED：`control_state=CANCEL_REQUESTED` 表示控制意图，待所拥有的执行中 Job 停止或完成再收敛。暂停也必须说明“当前任务仍在完成”。控制意图、任务执行态、人工审查态分开，避免无限扩充同一个 enum。

READY_FOR_REVIEW 验证：

1. 本次目标集清单完整，所要求的镜头／声音／字幕均齐全。
2. 对应 `TimelineRevision` 和 `episode_render_versions` 是本次计划产物，而非同集另一轮的最新结果。
3. 媒体登记成功，文件真实存在，大小、hash、解码、必要时长／几何规格验证通过。
4. 没有未完成的必需 Job 和未解决的硬阻塞；额外可选抽卡结束或明确停止。
5. 预览记录仍处于待人工审核，不能改写人审状态。

KEYFRAMES 目标只验证所需首尾帧及其角色，不要求时间线／渲染。

## 6. 最少的数据扩展和事务协议

### 6.1 四张小表足够

建议新增四张表。它们分别解决父计划、逐集恢复、机器暂用、所有任务归属，不能复制媒体、Job 或审核表。编号在实施时从实际 head 续接，本文不预占 `0095`。

**production_sessions**：一份用户明确提交的生产计划。

| 字段 | 类型与用途 |
|---|---|
| id、project_id | 主键及项目外键 |
| status、control_state、revision | 父级状态、控制意图、乐观锁 |
| request_hash、plan_hash、plan_revision | 规范化请求、预检计划、当前计划版本 |
| request_snapshot_json、plan_snapshot_json | 范围、策略、预算、外部输入版本、阶段所需能力；不存密钥 |
| started_at、deadline_at、completed_at、next_check_at | 运行与检查时刻，统一 UTC |
| dispatcher_owner、dispatcher_token、dispatcher_expires_at | 防多进程重复推进的短租约 |
| created_by、created_at、updated_at、schema_version | 沿用仓库元数据规范 |

`request_snapshot_json` 固定不变；变更预算或范围用带 expected_revision 的显式命令生成新计划版本，审计中保留旧快照。第一版“增加新集”可直接新 session，不必做运行中复杂扩容。

**production_session_items**：一行是一个项目准备项或一集，不是每个镜头。

| 字段 | 类型与用途 |
|---|---|
| id、session_id、target_kind、target_id | target_kind 为 PROJECT_SETUP／EPISODE；target_id 为对应项目或分集 |
| ordinal、state、revision | 显式展示顺序及状态 |
| input_fingerprint、input_snapshot_json | 本项外部来源及本次采用的版本 |
| child_run_id | 关联现有 automation_workflow_runs；准备阶段为空 |
| preparation_json、output_refs_json | 准备 Job、已生成结果引用、已完成里程碑 |
| blockers_json、next_check_at | 阻塞代码、修复入口、下次检查 |

唯一约束 `(session_id, target_kind, target_id)`；按 `(session_id, state, ordinal)` 建索引。已确定的所有分集先写入再执行，保证“第 3 集启动到一半断电”仍能找回第 4—N 集的意图。从原稿创建新集时按第 7.6 节的单次范围物化合同处理，不能用空集清单宣告完成。

**production_choices**：本次生产使用哪份媒体／身份包的历史记录。

| 字段 | 类型与用途 |
|---|---|
| id、session_id、item_id | 归属本次生产与项目准备／分集项 |
| subject_kind、subject_id、role | SHOT／DIALOGUE／STORY_ASSET；FIRST_FRAME、END_FRAME、VIDEO、TTS、HERO、IDENTITY_PACK |
| media_version_id、identity_pack_version_id | 二选一的真实外键，CHECK 保证恰好一项非空 |
| input_fingerprint、choice_revision | 可复用资格与选择版本 |
| origin | EXISTING_HUMAN／AUTO_TEMPORARY／SESSION_HUMAN |
| reason_code、evidence_json | 技术 QC、候选排序规则、帧配对 token、模型与输入证据 |
| is_current、supersedes_id | 不删除历史；替换时在同一事务切换当前标记 |

部分唯一索引：`(session_id, subject_kind, subject_id, role) WHERE is_current=1`。不能用两个 KEYFRAME 值代替首尾角色。跨集资产使用 PROJECT_SETUP 项的 choice，被各集的输入快照引用。

**production_session_job_links**：包括编排报告、关键帧、视频、TTS、渲染等所有任务。

| 字段 | 类型与用途 |
|---|---|
| session_id、job_id | 联合主键，关联现有 jobs |
| item_id、stage_code | 属于哪一项、哪一阶段 |
| ownership | OWNED／REUSED；取消不能影响外部借用 Job |
| dedupe_key、input_fingerprint | 确保同一逻辑投放不会重复 |
| reserved_bytes、accounted_bytes | 保守输出预留和已登记的增量字节 |
| created_at、settled_at | 预算结算与恢复 |

为 OWNED 的逻辑 dedupe_key 建唯一约束。项目共享资产在单个 session 内只创建一份 OWNED 任务；多个项引用它。第一版同项目只允许一个有自动写入权限的活动 session，新的重叠请求返回已有 session 或作用域冲突；人工单镜编辑仍可进行并触发输入变化检查。

允许 READY_FOR_REVIEW 历史 session 与下一轮新 session 共存。新 session 开始时，旧 session 的选择只作为历史可复用输入，不能继续后台改写。

### 6.2 不要靠进程内 Lock 保证一致性

现有 Python Lock 可以保留用于减少本进程冲突，但正确性依靠 SQLite 事务、唯一索引与 revision/token 条件。

提交顺序：

1. 只读计算 plan，不产生 Job、不唤醒 GPU；收集外部版本、动作清单、能力缺口和估算。
2. 短事务读取同 scope/idempotency key；已有相同 request_hash 就返回原 session，不再拿当前实时 plan_hash 否定已接受请求；不同 request_hash 返回 409。
3. 对新请求重新核对外部 revision／plan_hash，原子写 session、所有 items、命令幂等响应和 outbox。
4. 提交事务，API 返回 202；后台 scheduler 负责准备和投放。

原子写父意图后，即使 API 还没把响应发给浏览器就崩溃，重试也只能返回原 session。

派生业务 key 使用规范化字段的 SHA-256，例如 `session-job:<digest>`，并保留字段原文到 snapshot 便于排查；不要把最长 200 字符的用户 key 再拼接集 ID、镜 ID、阶段和轮次，导致层层超出既有接口上限。技术重试仍沿用原逻辑 key，创作新轮次改变 digest 输入。

### 6.3 调度认领与 Job 创建

伪代码说明职责，`*_in_transaction` 为需要从现有服务抽出的真实事务入口：

```python
def reconcile_one(session_id: str, now: datetime) -> None:
    ticket = repo.try_acquire_dispatcher(session_id, owner=worker_session_id, now=now)
    if ticket is None:
        return
    # 查产物、复用、依赖与资源；不持有数据库写锁执行推理。
    decision = planner.next_action(repo.read_context(session_id), now)
    with db.transaction() as conn:
        repo.assert_dispatcher_and_revision(conn, ticket)
        repo.assert_still_running(conn, session_id)
        repo.assert_input_versions(conn, decision.external_versions)
        budget.assert_can_reserve(conn, session_id, decision.cost)
        # 创建逻辑项、真实 Job 或子 workflow，并写 job link 必须一致。
        dispatch.create_in_transaction(conn, decision)
        repo.record_progress_and_outbox(conn, ticket, decision)
```

数据库写事务不能包含 ComfyUI 提交、FFprobe 全文件扫描、模型 readiness 网络探测或等待。若某个已有服务暂时不能接受 connection，则先持久化 dispatch intent，以稳定业务 key 调用它，再通过 reconciliation 补关联；必须证明“调用成功但未写 link”能按业务唯一 key 找回，而不是盲目重新生成。核心长时间生产路径最终要有原子 Job+link 入口。

短租约超时后旧 dispatcher 不能继续写结果：每次提交以 dispatcher_token 和 revision 为条件。媒体回调晚到可登记为历史产物，不能改变已经取消／重新规划的 session。

新增 scheduler/repository 的读连接必须明确关闭。当前 `Database.connect()` 返回原生 `sqlite3.Connection`，`with connection` 负责事务语义，并不等价于退出时关闭连接；`Database.transaction()` 则已有 finally close。新长跑代码使用 `contextlib.closing()` 或一个明确 finally close 的读取上下文，不能依靠垃圾回收释放句柄。先局部治理新路径，并用句柄／连接增长测试决定是否需要扩大修补范围，不为此全仓机械重写。

### 6.4 输入指纹分层

不要把“本集当前所有数据”哈希作为每个阶段共同的失效条件。自己生成一个视频、写一个选择，也会改变全集状态，造成后续步骤错误地认为输入被外部修改。

定义以下指纹：

| 指纹 | 包含 |
|---|---|
| request_hash | 用户规范化请求；不含进度、当前可用磁盘、随机时间 |
| plan_hash | 外部来源版本、目标范围、策略、预期操作；动态健康值只用于检查，不作为内容变更 |
| keyframe_input | 镜头创作 revision、prompt bundle、frame_role、角色参考快照、图像 Profile／workflow／参数 |
| video_input | 镜头动作／时长／运镜、精确首尾帧 ID+hash、必要 frame bridge、视频 Profile／seed／参数 |
| tts_input | dialogue text revision、voice version、语速情绪和 TTS 配置 |
| subtitle_input | 权威文本、采用 TTS ID+hash、对齐策略版本 |
| compose_input | 有序视频 choices、音轨、字幕、时间线编辑内容、制作规格 |

每一步的依赖产物还不存在时，计划保存将要满足的输出条件；依赖完成后冻结这一步的具体输入，并记录所属 plan_revision。不能要求开始时就知道所有未来 media_version_id，也不能在执行时无限采用任意“最新版本”。

人工修改某镜头只影响依赖它的阶段。新 policy、Profile、模型文件或资产身份发生变化时创建新计划／新候选，不修改旧 Job 的执行快照。对已有 `recover()` 刷新未执行任务的兼容行为保留 v1；新 session 不以改写已冻结输入方式偷偷续跑。

## 7. 机器暂用和最终人工审核如何兼容

### 7.1 保留四种不同事实

1. `MediaVersion.integrity_status=VERIFIED`：文件已登记且通过完整性流程。
2. `machine_check_runs`：技术／机器检查证据。
3. `production_choices(origin=AUTO_TEMPORARY)`：本次机器决定暂时使用的版本。
4. `ReviewDecision(APPROVED)`：人工批准，仍只能通过现有审核服务写入。

不要把第 3 项写进第 4 项。也不要把 `created_by='local-user'` 用作机器动作的伪装：新自动动作统一使用 `production-session:<id>` 或结构化 service actor，并记录用户启动计划的独立授权事实。

### 7.2 候选解析顺序

新增的 `ProductionChoiceService.resolve_input()` 建议使用这个顺序：

```text
本次人工明确选择且仍有效
    ↓ 无
项目当前人工采用／批准且与本次输入相容
    ↓ 无
本次已冻结的机器暂用且未过期
    ↓ 无
对本次合格候选作确定性选择
    ↓ 无
在预算内生成／补抽，或记录缺口
```

所有路径都验证项目、镜头／对白／资产归属，媒体阶段、frame_role、输入指纹、文件完整性和必要的 QC。人工选择若对新剧情已过期，不能继续盲目使用，也不能自动覆盖；显示输入变化及影响，保留原事实。

新自动流程不调用现有 `_auto_select_video()` 去写全局选择；将其复用逻辑提取成读取资格和排序部分，结果写入生产 choices。旧 DRAFT／BALANCED 入口保持原行为，避免破坏兼容性。

### 7.3 人工采用与“保护已有选择”

人工决定采用本次候选时，复用 `ShotStudioCommandService.adopt_working_version()` 及其 repository 的写入权威。此方法目前没有调用者提供的期望槽位 revision，新增批量／异步采用入口需补：

```python
adopt_working_version(
    media_version_id,
    expected_shot_revision=...,
    expected_slot_revision=...,   # 空槽也需要可验证的“仍为空”条件
    source_choice_id=...,
    actor="local-user",
)
```

旧调用可保留可选参数默认行为，新自动／批量路径必须提供。校验和更新放同一事务。人工在 GPU 运行期间换了另一张图，晚到的机器回调只增加候选，不改变当前工作槽。

`selections` 和 `shot_working_media_slots` 目前都被某些读路径使用，`timeline_selections()` 优先工作槽再回退旧 selection。不要直接 SQL 写其中一张表形成不一致；人工采用全部通过现有命令服务，兼容旧 selection 的读取留到独立迁移再治理。

### 7.4 预览时间线必须引用本次 choices

给 `TimelineService.assemble_episode_timeline()` 增加可选、经过服务器验证的 `production_session_item_id`。旧入口仍读取全局工作槽；新入口读取本次 choices，冻结精确视频、TTS、字幕和混音。

预览时间线 `editor_json` 增加：

```json
{
  "source": "PRODUCTION_SESSION_PREVIEW",
  "production_session_id": "session-id",
  "production_session_item_id": "episode-item-id",
  "production_plan_revision": 1,
  "choice_ids": ["choice-1", "choice-2"],
  "compose_input_fingerprint": "sha256",
  "review_state": "PENDING"
}
```

这仍使用已有 TimelineRevision、Render 表和 FFmpeg。不能因为同集已经有一个非 STALE 的人工时间线，就将它直接 SKIPPED 视作本次预览完成；复用必须比较 source/session 或内容指纹。机器预览也不能成为人工编辑页默认“最新版本”而覆盖用户工作上下文：查询必须按用途区分“当前人工时间线”和“本次自动预览”。

受影响读路径至少检查：`TimelineService` 的 latest 查询、`infrastructure/database/edit_repository.py`、后期审核数据源、整集状态、render 查询及 delivery 目标。带 session 的渲染输入必须是明确 timeline_revision_id；后续 PREVIEW_VERIFY 消费该渲染的 id，禁止另取 latest。

### 7.5 角色身份包的两步实施

**第一步交付：已确认角色的夜间生产。** 保持身份包人工批准规则；在新生产计划开始前汇总缺口。可先完成关键帧→整集预览的真正闭环，这正适合当前已经做过资产和拆解的项目。

**第二步交付：新角色也允许先做待审预览。** 在不放宽旧函数默认规则的前提下新增服务器内部的“本次临时身份快照”：

1. 复用资产主图、多视图和身份包 draft 生成服务，产生真实媒体及 DRAFT pack。
2. 校验现有 `_validated_content()` 所要求的完整槽位、项目/角色/状态绑定、媒体完整性与使用授权。首版按现有三视图合同执行，不用一张正脸冒充完整 pack。
3. 将确切 pack_version_id、content_hash、槽位媒体及其 hash 冻结进项目准备项的 `production_choices`；不更改 pack.status 为 APPROVED，也不把它设为全项目 current_version_id。
4. 新 session 的生成调用持有服务器从持久 session 解析的 `ProductionInputContext`，可从中读取临时身份快照。不能接受前端任意传入 `allow_unapproved=true`。
5. 增加一个旁路入口，如 `generation_snapshot_for_production_context()`，复用现有内容校验，但将“已人工批准”替换为“本次策略允许临时输入、快照有效”。原 `generation_snapshot_for_intent()` 默认逻辑不动。
6. 将此 context 接到 `shot_identity_references.py`、关键帧 plan/submit、`generation.py` 的各处身份快照二次校验以及视频生成。只改最外层检查会在提交时再次失败，必须打通所有校验点。
7. 临时包的槽位后续被编辑，hash 不同则停止其依赖新投放，旧候选保留；不能用可编辑 DRAFT 行替换冻结内容。
8. 最终人工审核仍调用现有 pack 审核命令。先做批准影响预览：如果批准仅增加审批记录且实际内容 hash 不变，已生成媒体不应因此被当作换了一个角色重做；按现有 stale 传播规则精确处理，不能全局忽略 stale。

第一版临时 pack 不写入全局 `shot_asset_bindings.identity_pack_version_id`；它由 session context 补充相同角色的临时引用。人工正式采用后才按原有绑定命令物化为正常项目事实。

场景、服装、道具同理：有人工 canonical 参考先复用，自动候选仅在本次临时使用，不改变其他运行的全局参考。只有明确“补空主参考”的既有安全规则可以保留自动填空。

### 7.6 从小说自动衔接前期

前期不另写 LLM 提取器。PROJECT_SETUP 项编排现有 pipeline run、draft apply、资产主图批次。分集 item 调用 `EpisodePreparationService.prepare()` 和 `EpisodeShotReadyService.confirm()`。

自动应用结构的合同必须包含：来源文档版本、段落范围、目标时长、允许新增的实体范围、预期 project／episode revision 和 run id。只自动应用本次启动生成且匹配范围的新 draft；已有用户编辑中的 draft 不可“顺手批准”。有现成镜头的集只补缺或明确重规划，不自动替换人工分镜。

来源不明确、人物别名冲突、跨集范围重叠、源文被修改时生成待处理项；机器继续其他独立集。无可生产项才整体 WAITING_USER。

尚未有分集的原稿入口使用新 `FROM_SOURCE` scope，冻结 source_document_version_id、来源范围、目标集数／单集时长及允许自动应用范围。此时只写 PROJECT_SETUP 项和 `scope_materialized=false`。故事 draft 经既有规则应用后，按本次 materialization 的明确 episode_id 清单，在一个事务内新增全部 EPISODE items 并冻结范围、置 `scope_materialized=true`。重复回调重放同一清单；用户同期另加的分集不自动纳入。原稿范围尚未物化时不得因为 items 中“没有未完成集”就宣告整部完成。

## 8. 关键帧、视频与抽卡的代码方案

### 8.1 关键帧批量生成的增量实现

扩展 `ShotKeyframeGenerationBatchService`，保留现有 plan/submit 入口：

- 新增服务端 `plan_missing()`：参数是 episode/选镜范围、本次 context、frame_strategy、候选策略；返回 reused、needs_generation、blocked，而不是让前端按已加载的 50 镜判断全剧缺口。
- 明确 `candidate_count` 的含义：旧批次是本次新增张数，新“补齐”是期望可用数量，两者不共用一个模糊 UI 文案。
- 有效候选来自当前输入指纹和 frame_role；补齐数为 `max(0, target_usable_count - current_usable_count - active_matching_count)`。
- 历史不合格、其他角色的尾帧、旧模型/旧分镜候选不能减少补齐数量。
- 使用现有 MAX_SHOTS=100 切分；后台实际可以按更小窗口提交。用户选“整集”时后端遍历完整范围，不等于前端当前页。
- 参考资产批次实现 request_hash 校验、事务重放、PLANNED 恢复和 Job 关联回补。扫描当前队列不能替代持久业务唯一键。
- 扩展批次查询，使 orphan／NEEDS_ATTENTION／PLANNED 等状态有明确投影。用户应知道“待调度”“生成中”“待恢复”，不能都显示排队。

现有首帧 completed count 查询会统计 T2I KEYFRAME，而尾帧按 batch item role 统计；候选编号不要继续用不同宽度的历史计数决定首尾配对。新批次增加 `draw_group_key` 或在 item snapshot 中冻结配对标识和 round_no，双方共享一个组号。

### 8.2 帧策略和帧桥

第一版在新 session 中增加 `AUTO` 帧策略，由当前实际执行 Profile 决定：

| 视频路线 | 默认需要的帧 | 行为 |
|---|---|---|
| 可执行 I2V | FIRST_FRAME | 最经济的默认方式 |
| 可执行 FL2VA／首尾帧路线 | FIRST_FRAME + END_FRAME | 两个独立角色、同组且输入一致 |
| 明确配置 T2V | 不强制关键帧 | 新适配路径验收通过才开放，不能把默认 I2V 流程无输入地调用成 T2V |
| 角色多视图／表情板 | 不是镜头关键帧 | 保持 `SHOT_KEYFRAME_SINGLE_FRAME` 合同检查 |

读取路线应复用生成偏好、Profile、workflow contract 和实际绑定，不根据模型显示名称或文件名猜。

首尾帧可以先独立生成同组候选，但这不保证视觉一致；有实际 image-edit reference 输入能力时，可以先选首帧再以它约束尾帧。否则记录“成对一致性尚待人工审核”，不能假装约束已经执行。

镜头自身的 END_FRAME 和前一镜视频提取的 LAST_FRAME 是不同事实。现有 `_end_frame_chain()` 已有相关保护，应保留：前镜尾图约束的是下一镜首帧，不能填入当前镜的 END_FRAME。仅已有 HARD 连续性约束或用户指定的衔接关系引入依赖；不要把整集所有镜头串成严格首尾链而损失可并行性。

HARD 依赖先检查循环；前镜失败只阻塞依赖它的后镜，其他不依赖镜头可继续。提取使用真实视频版本和 FFprobe 时间依据，不能对静态图片伪造视频时间戳。

### 8.3 候选资格与排序

复用 `application/queries/media_eligibility.py` 中 `best_current_video`、`eligible_candidate_counts` 的过滤逻辑，扩展为按本次指纹取候选，不复制旧 SQL 到第三处。

先做硬过滤：所属项目／镜头正确；可读；hash、尺寸、类型正确；没有过期输入；不是被人工拒绝且当前输入未变化的候选；当前策略要求的技术检查通过。

再做确定性排序：

1. 本次人工选择和已锁定结果。
2. 满足硬条件、已被人工认可的可复用候选。
3. 满足目标时长／画幅和明确技术 QC 的候选。
4. 可选的本地质量评估证据。
5. 相同条件时按固定 candidate_index、created_at、id 排序，恢复后不会随机换图。

第一版**只实现 1—3 和稳定 tie-break**，本地 VLM／CLIP／清晰度打分列为后续增强。若没有内容评估，就把推荐原因写成“文件和规格检查通过，按候选顺序暂用”，不要显示无依据的 98 分。

不把图片越锐利、动作越大自动视为越好。黑场可能是剧情需要，静止镜头可能是有意设计；基础指标可提示疑似问题，硬失败阈值需按项目内容和真实样本验证。

### 8.4 有限抽卡状态机

```text
复用当前合格候选
  ├─ 足够 → 冻结 choice → 后续阶段
  └─ 不足 → 检查剩余候选/Job/磁盘/时间预算
             ├─ 有预算 → 提交一轮 → WAITING → 回收产物 → QC → 再判断
             └─ 无预算 → 有可用候选则暂用并标风险，否则待人工处理
```

候选策略至少有：`initial_candidates`、`max_new_candidates_per_subject`、`max_auto_rerolls`、`stop_when_usable`。默认关键帧最多新增 4 张／role，视频最多新增 2 个／镜作为新品建议，不能把它们理解为仓库既有固定规则。技术重试不占“创作候选”计数，但占执行尝试和运行时间；失败 Variant 已创建就占本次候选预算，不能无限失败而不计数。

第一版 `stop_when_usable` 只决定是否继续下一轮；已经提交的本轮任务正常收尾，等本轮候选进入终态后确定一次 choice，避免完成先后导致随机换图。精品模式若承诺先生成 2 个视频，initial 就应为 2；不能 initial=1 后以“找到可用项”为由悄悄减少已展示的候选承诺。

新候选 seed 从 session、subject、role、draw_round、candidate_index 稳定派生；技术重试不变，显式重抽必变。seed 有效范围以实际 Profile 合同为准。保留现有 EXACT_REPLAY 能力和它的限制：相同输入可以重放，跨模型/运行环境不能承诺逐像素一致。

### 8.5 用户“抽卡”的三种动作

| 动作 | 后端行为 | 影响 |
|---|---|---|
| 再抽一张／一段 | 新 round、新 Variant、新 Job | 旧 choice 继续有效，直到新候选被选中 |
| 重试这个失败任务 | 原 Job 生成新 Attempt，冻结参数不变 | 不新增创作分支 |
| 按修改后的内容重新生成 | 新输入指纹、新计划项和候选 | 明确标记依赖的后续产物过期 |

审核页选中 5 个问题镜头后“补抽”应显示新增上限，完成后放回同一审核上下文。不要重新执行本集所有资产、所有关键帧和全部 TTS。

## 9. 24 小时运行的调度与资源方案

### 9.1 常驻运行接到已有后台

`WorkerSupervisor.run_until_idle()` 已有会话心跳、约 30 秒周期的恢复处理、Comfy 结果 reconciliation；`entrypoints/worker.py` 已有 `--watch`。把 session scheduler 接到已有后台维护点，不使用浏览器定时器、React effect 或 Codex 对话自动化来维持生产。

长 GPU Job 可能使单个 supervisor 主循环暂时不调度，因此首版应明确有 CPU/control worker 持续推进父计划，GPU worker 专注推理，两者共享既有 SQLite 和 resource leases。若现有 Runtime Host 配置尚未分开，按其已有多 worker 配置方式启用 CPU worker；不写新进程守护器。

control 的推进不能被长时间 FFmpeg／LLM handler 一并卡住。如果 CPU worker 同时承担这些长任务，将小步 reconciliation 放到该 supervisor 管理的维护线程，每次独立打开并关闭数据库连接；停止时可中断并 join，异常反馈给 supervisor。它只是已有进程的控制循环，不增加一个独立调度服务，也不把 sqlite connection 跨线程共享。

后台周期使用可注入时钟，建议每 2—5 秒检查到期 session，单次有扫描数量上限；事件到达可更早触发。休眠／唤醒后以数据库状态及租约恢复，不根据前端倒计时推断失败。

### 9.2 容量窗口

建议初始策略，需真实机器小样后调整：

| 参数 | 起始值 | 说明 |
|---|---:|---|
| active_episode_limit | 2 | 一集生产，一集可准备；阻塞集不永久占位 |
| max_queued_gpu_jobs | 8 | 指所有 session 已投放未完成的 GPU 工作，总量受容量判断限制 |
| dispatch_shots_per_tick | 4 | 一次少量投放，防长事务与排队洪峰 |
| GPU 同时执行 | 服从已有租约；单卡通常 1 个重任务 | 不把 active episodes=2 误认为同时加载两个大模型 |
| 默认 deadline | 24 小时 | 到期停止新投放，当前 Job 按计划收尾 |
| 每镜关键帧候选 | 默认 2，首版每 role 最多 4 | 兼容原 1—4 单次上限 |
| 每镜视频候选 | 默认 1，精品 2 | 用户及 Profile 可调整，上限受策略与现有合同限制 |

active_episode_limit 和 queue window 需要实际贯穿到 `keyframe_generation()`、`video_generation()` 与各批次 submit；只在父层限制两集，但每集仍一次投放几百个 Job，不算实现容量窗口。

第一版用稳定分集顺序与有限窗口即可。第二阶段根据实测增加运行时亲和批次：先批量做一小段 LLM 准备，再做同类图像、视频，减少单卡在 llama.cpp、ComfyUI 和 TTS 模型之间频繁切换。最多增加一个“优先已驻留 runtime，等待过久则提升优先级”的规则，不设计复杂最优调度器。

### 9.3 预算必须覆盖真正的工作量

预算包括：总新 Job 数、总 Attempt 数、每对象创作候选上限、deadline、输出字节预算、磁盘保留量。不能仅统计 AUTOMATION_WORKFLOW_TASK 报告数量。

每次投放通过 `production_session_job_links` 预留；完成／取消后幂等结算，按 job_id/artifact 引用去重。把 report 文件大小当作媒体总占用，或者将 `produced_extra=0` 视为视频没有占用，都不成立。

磁盘检查按物理卷执行：`work_root`、`projects_root`、cache 和临时 FFmpeg 输出可能在不同卷；同卷按预留汇总，避免并发各自认为空间充足。至少满足：

```text
实际可用空间 - 尚未兑现的预留 - 下一任务保守峰值占用 >= 用户保留量
```

预留同时考虑 staging、中间文件和最终输出，单个渲染过程中可能同时保留多份文件。预留不是物理配额，运行中仍要监测真实空间和处理 ENOSPC。默认保留量可建议 20 GiB，但不覆盖用户原有更严格配置；未设定时后端给出具体值并写入 plan。

已有 `application/queries/generation_estimates.py` 使用历史 p50/p90；继续复用。未知输出大小采用明确标注的保守默认估算，不伪造精度。预算明显不足时先跑两集小样，让用户据结果扩展。

### 9.4 暂停、取消和恢复

- **暂停投放**：父 session 立即保存 PAUSE 意图；各阶段不再创建新的 owned Job；当前 Job 可以完成，回调只登记产物，不推进新阶段。
- **恢复**：原 session、原范围、原 choices；重新检查资源与外部版本。只是磁盘恢复不重规划；输入变了需要差异计划。
- **取消**：遍历显式 OWNED links 调用已有取消服务，处理正在提交／尚未关联的 dispatch intent；REUSED Job 不取消。
- **恢复故障**：先 Job 租约与 provider 对账，再媒体登记，再阶段结果消费，再父级汇总。顺序反过来可能误判缺产物并重复生成。
- **程序关闭**：浏览器关闭不影响；整个 Runtime Host 关闭当然停止执行，重启后从持久化状态恢复。
- **24 小时到期**：不直接杀当前模型进程；停止投放、收尾并显示“预算到期，已完成 X 集”。用户明确扩展预算可继续原 session。

GPU OOM、模型不可用和进程崩溃已有运行时恢复机制，调用其公开能力。不要 `taskkill` 所有 Python／ComfyUI 进程，也不要重置不属于本应用管理的模型服务。Windows 不休眠可作为 Runtime Host 的显式运行选项，必须检查既有实现后再补平台适配，并在停止时释放；不静默更改系统全局电源计划。

### 9.5 一直运行不等于无限失败重试

本地漫剧生产的目标是完成待审内容。任务做完、只有不可恢复问题、预算耗尽或机器需要维护时，应诚实空闲／等待。未来如果用户持续加入新集，提供“加入下一批”即可；第一版不自动下载新小说、不自动发布成片、不无限重抽填满硬盘。

## 10. 声音、字幕、返工和集中审核

### 10.1 声音不是最后随便补一个 TTS 按钮

现有 `_automation_tts_finalize()` 的自动选择跟随 `auto_select_videos`；应让新 session 明确传 `audio_selection_policy`，与视频抽卡数量分离。

对白按当前 `dialogue_text_revisions` 和选定声线版本冻结。没有声音授权时列出受影响角色，不能由自动化替用户勾选声音授权。已配置本地系统声线且用户允许时可用于预览，不自动把克隆音色换成默认音色。

建议先将同一集 TTS 分支提前到视频生成前或与关键帧准备并行，以便检查真实朗读时长。首版可以保持顺序，但 plan 必须提示“音画时长需在 TTS 完成后复检”；若对白超过镜头可容纳时长，不可在最终渲染时简单截掉声音。

自动处理范围限定为：用户预先允许的轻微语速调整、已有剪辑规则可容纳的停顿／镜头时长调整。超出范围生成 `DIALOGUE_VIDEO_DURATION_MISMATCH` 并指向声音／分镜页。不得为适应视频而悄悄改台词、少读一句或捏造口型同步。

TTS candidate 自动暂用写本次 choices；同步给字幕／时间线时通过受控输入参数，不以伪造全局 dialogue selection 的方式绕过审核。必要时抽取既有 `plan_tts_subtitle_draft()` 的输入解析，让它支持“全局已采用 TTS”及“本次临时 TTS”两种读取源，共用文本权威和对齐逻辑。

### 10.2 字幕、BGM 和口型同步

- 字幕复用权威原文校验与现有 ASR／ForcedAligner，不用 LLM 再创作字幕文字。
- 字幕状态和 timing_authority 单独记录；无逐字对齐能力时按现有可靠行级时轴，不冒充逐字精确。
- 第一期 BGM／SFX 只复用用户已配置的本地音轨，缺少不阻塞普通预览；用户明确要求有 BGM 的方案不能静默缺省。
- SILENT 模式既然无 TTS，不应自动生成“声画同步字幕”；如需要无声对白字幕，必须单独列为用户选择且使用真实对白和明确时长策略。
- LatentSync 为 P2 条件节点：采用视频 + 采用 TTS + 人脸条件符合 → 唇形输出新版本 → 技术验证 → 本次视频 choice；不得对所有镜头强制运行。
- 声音授权、素材可用性和语义决策属于已有项目事实，不新增一套平行授权表。

### 10.3 只重做受影响的部分

| 用户修改 | 必须重新计算 | 默认保留 |
|---|---|---|
| 换首帧／尾帧 | 依赖该帧的视频、其口型派生、时间线和渲染 | 不依赖图像的 TTS、其他独立镜头 |
| 改镜头动作／画面提示词 | 对应关键帧、视频、连续性依赖与渲染 | 其他集、无变化的对白 TTS |
| 改对白文字 | TTS、字幕、使用该声音的口型版本、混音／渲染；复检时长 | 未依赖对白画面的基础视频可保留 |
| 换说话音色／语速 | TTS、字幕时轴、口型、混音／渲染 | 基础关键帧和视频 |
| 换角色造型／身份包 | 引用该角色版本的关键帧和视频；按显式依赖传播 | 没出现该角色的镜头 |
| 换 BGM／字幕样式 | 音频／字幕和时间线、渲染 | 已完成图像与视频候选 |
| 调整镜头顺序／转场 | 连续性检查、时间线、渲染；必要依赖镜头 | 与调整无关的素材 |
| 修改目标画幅／分辨率 | 按 Profile 与 ProductionSpec 重做所需生成／派生和渲染 | 原尺寸历史产物；不能静默拉伸当新规格合格 |

区分“上游创作变了”和“仅人工增加了对同一内容的批准记录”。后者不应无理由触发全剧重生成。依赖传播沿用已有 variants、timeline 和审核 stale 机制；为生产 choices 增加同样的 lineage 关联。

### 10.4 重新合成的两种意思

现有 `RECOMPOSE_ONLY` 对冻结 timeline 再渲染，不等于“把刚换的新镜头自动更新进时间线”。新品 UI 要区分：

- **重新渲染此版本**：使用同一 TimelineRevision，适合输出失败、编码参数允许范围内重做。
- **按当前选择重新合成**：生成 compose plan，比对视频／声音／字幕 choices，创建新 TimelineRevision，再渲染。

旧 API 保持含义，新 session 的修复操作走“影响预览 → 新时间线 → 渲染”，不直接改原冻结 timeline 的 items。

### 10.5 集中审核的最小闭环

用户打开“审核本次结果”后，先看到本集预览视频、镜头联系表、机器发现的问题和使用的角色参考。点击风险镜头进入现有候选比较；选择另一候选后立即显示需要重新合成的范围。

整集批量批准不是向所有历史候选写 APPROVED。审核提交应基于固定清单：

```text
session_id / item_id / plan_revision
render_version_id / timeline_revision_id / compose_fingerprint
当前使用的 choice_id、media_version_id、对应 revision
需要先正式认可的身份包版本与影响
```

复用 `ReviewService.batch_preflight()/batch_commit()` 和现有整集 render 审核。若现有批量审核不能承载上述合同，新增一个薄的“生产审核计划”适配器，其内部仍调用原审核服务；不能直接批量 SQL 设置 APPROVED。

审核期间后台新产出一份候选，不改变用户正在看的清单。用户明确换版或输入变更时，旧审核 token 失效，需要重新预览影响。允许“通过当前所选内容，保留其他候选不审”。

若相同媒体和剪辑内容仅从机器暂用转为人工采用，不应再次耗时生成视频。是否必须产生新的 timeline/render，由现有批准与交付合同决定：相同内容可复用验证结果，但必须记录可追溯的正式采用关系。

### 10.6 交付和整部打包

在完成并批准的分集上调用现有 delivery API。项目级“导出已批准分集”生成一份固定 manifest，包含集序、render/package ID、hash、是否缺集及生成时间。

默认按集导出；不默认拼接几个小时的大文件。需要整部合订视频时作为单独导出操作，先估算磁盘和时长。所有 manifest 来自真实文件校验，不把“本次生成已完成”当成“交付人审已通过”。

## 11. API 与前端具体合同

### 11.1 既有接口继续保留

以下已存在，优先复用，不改名、不改变旧默认语义：

```text
POST /api/v2/episodes/{episode_id}/shot-keyframe-batches:plan
POST /api/v2/episodes/{episode_id}/shot-keyframe-batches:submit
POST /api/v2/episodes/{episode_id}/storyboard-generation-batches:plan
POST /api/v2/episodes/{episode_id}/storyboard-generation-batches:submit
POST /api/v2/episodes/{episode_id}/production:prepare
POST /api/v2/episodes/{episode_id}/production:operation-impact
POST /api/v2/episodes/{episode_id}/production-runs
POST /api/v2/production-runs/{run_id}:pause
POST /api/v2/production-runs/{run_id}:resume
POST /api/v2/production-runs/{run_id}:recover
POST /api/v2/production-runs/{run_id}:cancel
GET  /api/v2/projects/{project_id}/whole-drama:status
POST /api/v2/projects/{project_id}/whole-drama:run
```

旧显式小样范围最多两集，保留兼容性。新 session 接口可以支持多集范围，不能为了整部按钮直接放开旧小样校验，也不能让缺失 episode_ids 的请求意外从两集扩大到整部。

### 11.2 新增 session API

文件建议：`api/routes/production_sessions_v2.py`、`api/schemas/production_sessions_v2.py`。在现有 router 装配处注册。

| 方法与路径 | 用途 |
|---|---|
| POST `/api/v2/projects/{project_id}/production-sessions:plan` | 只读检查范围、差异、能力和估算 |
| POST `/api/v2/projects/{project_id}/production-sessions` | 原子提交计划，202 返回 session_id |
| GET `/api/v2/projects/{project_id}/production-sessions` | 分页列出本项目历史，不从 latest episode run 拼凑 |
| GET `/api/v2/production-sessions/{session_id}` | 汇总、预算、运行控制态与下一步 |
| GET `/api/v2/production-sessions/{session_id}/items` | 分页分集状态、产物和问题 |
| GET `/api/v2/production-sessions/{session_id}/changes?after=...` | 复用 outbox cursor 的增量刷新 |
| POST `/api/v2/production-sessions/{session_id}:pause` | 暂停新投放 |
| POST `/api/v2/production-sessions/{session_id}:resume` | 校验后恢复原计划 |
| POST `/api/v2/production-sessions/{session_id}:cancel` | 取消 owned 工作，保留产物 |
| POST `/api/v2/production-sessions/{session_id}:reconcile` | 用户主动恢复检查，后台也调用同一 service |
| POST `/api/v2/production-sessions/{session_id}:repair-plan` | 对当前所选问题给出重试／补抽／重合成差异 |
| POST `/api/v2/production-sessions/{session_id}:repair` | 提交明确修复范围和预算，不重启整部 |

首个单集版本只实现 plan/create/get/items/control，repair 在集中审核任务中补齐。不需要第一天把全部接口搭成空壳。

### 11.3 新启动请求示例

plan 接收 `request` 内容；create 接收相同 request、expected_plan_hash、idempotency_key。示例值仅说明合同：

```json
{
  "request": {
    "scope": {
      "kind": "SELECTED_EPISODES",
      "episode_ids": ["episode-01", "episode-02"]
    },
    "target": "EPISODE_PREVIEW",
    "operation": "FILL_MISSING",
    "production_mode": "BALANCED",
    "review_policy": "FINAL_ONLY",
    "preparation_policy": "USE_APPROVED_INPUTS",
    "frame_strategy": "AUTO",
    "audio_strategy": "EXTERNAL_TTS",
    "candidate_policy": {
      "keyframe_initial": 2,
      "keyframe_max_per_role": 4,
      "video_initial": 1,
      "video_max_per_shot": 2,
      "max_auto_rerolls": 1,
      "stop_when_usable": true
    },
    "failure_policy": "CONTINUE_INDEPENDENT_ITEMS",
    "budget": {
      "max_duration_seconds": 86400,
      "max_new_jobs": 600,
      "max_attempts_total": 1200,
      "max_output_bytes": 107374182400,
      "min_free_disk_bytes": 21474836480,
      "max_queued_gpu_jobs": 8,
      "active_episode_limit": 2
    }
  },
  "expected_plan_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "idempotency_key": "client-command-uuid"
}
```

准备策略首版仅开放 USE_APPROVED_INPUTS；完成前期自动化任务后再开放 AUTO_PREPARE_PREVIEW 及 FROM_SOURCE scope。ALL_EPISODES 明确表示提交当时的全部集，服务端立即展开并冻结；运行后新增集不自动加入。FROM_SOURCE 是尚未分集的原稿入口，按第 7.6 节单次物化。选择顺序按季度／分集 display_order，不能按 E1/E10/E2 字符串排序。示例 hash 是格式占位值，实际提交必须使用 plan 返回的值。

校验要求：不同项目 ID、重复集、空范围、超限数量、负预算、无效候选策略、GUIDED 与 FINAL_ONLY 的冲突字段，返回明确 4xx。scope 上限应显式定义，例如首版最多 500 集，单集最多 500 镜；它们是请求／规划安全上限，不是一次投放数量。

对 SINGLE_EPISODE 和 SELECTED_SHOTS 建同一 scope discriminated union；SELECTED_SHOTS 必须限定一集、验证归属且兼容现有最多 100 镜单次交互。整集补缺由后端分块，不要求前端提交上百个 ID。

### 11.4 plan 返回值

```json
{
  "plan_hash": "sha256",
  "request_hash": "sha256",
  "status": "PARTIALLY_READY",
  "scope_summary": {"episodes": 8, "shots": 120},
  "actions": {
    "reuse": 64,
    "new_keyframes": 96,
    "new_videos": 48,
    "new_tts": 126,
    "renders": 7
  },
  "blocked_episode_count": 1,
  "blockers": [{
    "code": "ASSET_BINDING_REQUIRED",
    "message": "第 8 集需要确认角色绑定",
    "scope": {"episode_id": "episode-08"},
    "owner_route": "SHOT_STUDIO",
    "repair_action": "BIND_CHARACTER"
  }],
  "blocker_total": 1,
  "estimates": {"p50_seconds": null, "p90_seconds": null, "source": "INSUFFICIENT_HISTORY"},
  "effective_policy": {},
  "mutated": false,
  "runtime_contacted": false
}
```

真实 `blockers` 必须包含 code、message、scope、owner_route、repair_action；上例是虚构数据，actual code 和 route 适配现有合同。大范围只返回有界问题摘要、总数和可分页的详细读取方案；避免每次轮询返回所有 prompt bundle、图片 base64 和全量候选。

plan 还必须区分“将由前置阶段自动补齐”和“当前不可自动处理”。缺关键帧／待生成视频属于 planned work，不算阻塞；模型不支持、来源不明或人工保护冲突才是 blocker。否则“一键补齐”会因为本来要补的材料缺失而永远不能启动。

plan 用持久 capability/smoke 证据；需要刷新 runtime 健康时由独立健康探针更新。create 和 claim 时读取新健康情况，可返回资源等待，不因磁盘数字变化把同一创作计划误判为 stale。

### 11.5 标准错误与用户动作

| 错误／状态类别 | 自动行为 | 用户入口 |
|---|---|---|
| PLAN_STALE／REVISION_CONFLICT | 不执行旧 plan，显示差异 | 重新检查本次计划 |
| IDEMPOTENCY_PAYLOAD_MISMATCH | 拒绝，不能返回另一个请求的旧结果 | 客户端以新参数重新预检 |
| RESOURCE_BUSY／runtime 加载中 | WAITING_RESOURCE，有限退避 | 一般无需用户操作 |
| DISK_SPACE_LOW | 停新投放，保留当前产物 | 系统存储与预算 |
| WORKER_LEASE_EXPIRED | 先对账，确定无副作用再重试 | 后台自动恢复／诊断 |
| provider 提交结果未知 | 不盲目重抽；复用现有 uncertain-success 恢复 | 任务详情／对账 |
| 模型缺失／contract 不匹配 | 阻塞受影响阶段；不自动下载或替换模型 | 能力配置 |
| 技术 QC 失败 | 按候选与 Attempt 预算补抽或重试 | 镜头候选 |
| 身份／动作质量未知 | 可暂用且标待审；策略要求时挂起 | 集中审核 |
| 声音授权缺失 | 阻塞需要该声线的内容 | 资产声线设置 |
| 角色／来源冲突 | 受影响项 WAITING_USER，其他项继续 | 故事／资产／分集策划 |
| 预算耗尽 | 不再投放，保留结果和剩余清单 | 扩展预算／结束本次 |

错误代码优先复用现有值，上表新增英文名称是建议分类，不要求同名重复制造一个异常。

### 11.6 前端提交与刷新细节

- 提交前先处理目标范围内未保存草稿，复用 `features/drafts/draftRegistry` 的 token/version 和协调规则。不能在保存尚未完成时先请求 plan。
- plan 成功后用户改了选镜、prompt、帧策略、数量、声音、模式或 scope，立即使旧 plan 无效；提交时再次核对当前表单 fingerprint。
- 网络超时但未确定 create 是否成功时，保留原幂等键重试；成功后再清理。不能每点一次自动生成新 key。
- 生产 hooks 复用 TanStack Query，按 project/session/item 完整分层的 query key；outbox 事件失联时有界轮询回退，恢复后按服务端事实刷新。
- 切项目、切集时重置所属 session 查询，清理事件订阅；不能让上一项目的延迟响应覆盖当前页。
- 一个列表“全选”要说明“当前页／所有符合条件的镜头”；整集操作必须由服务器解析全量，不依赖当前加载的数据。
- mutation 后展示服务器的有效范围、blocked count、session_id 及下一步，不使用本地假进度。
- 媒体只加载缩略图，视频按用户播放；大量候选列表分页或虚拟化，不同时加载全部视频。
- 新 API 类型通过 `scripts/generate_client.py` 生成。已有少量手写 batch client 可暂留，但新 session 不再维护第二份 DTO。

## 12. 给 Sol 的分阶段开发任务

### 12.1 实施里程碑

| 里程碑 | 完成后的用户价值 | 包含任务 | 此时不能宣称 |
|---|---|---|---|
| M1 单集闭环 | 现有已确认资产与方案，一键生成关键帧直到待审预览片 | WP00—WP04 | 不能宣称从空项目到整部无人值守 |
| M2 整部持续生产 | 项目级生产计划、容量控制、局部返工、集中审核、常驻恢复 | WP05、WP07、WP08 | 未做 WP06 时不能自动绕过新身份包准备 |
| M3 原稿到待审整部 | 自动衔接故事、分集、资产和临时身份包 | WP06，并重新执行 WP07/08 相关验收 | 不承诺零人工决策的正式发布 |

WP00 先做。之后每个任务包都给出代码、测试和可验证结果，不必强制按一份大 PR 合并。能交付 M1 就先体验，避免等一个庞大重构结束才发现操作方式不合适。

### WP00 核对基线与补失败用例

**目标**：确认本文分析没有被正在进行的其他修改取代。

操作：记录 HEAD、工作树 diff、迁移 head；读取既有 `docs/gpt/LocalDramaStudio_收尾修补与交付验收实施方案.md`；核实近期 draft guard、音轨、制作规格和审核页改动。保护用户及其他任务未提交内容。

现有测试重点：

```text
apps/api/tests/test_episode_production_runs.py
apps/api/tests/test_episode_production_modes.py
apps/api/tests/test_episode_run_recovery.py
apps/api/tests/test_whole_drama_orchestrator.py
apps/api/tests/test_shot_keyframe_generation.py
apps/api/tests/test_episode_worker_actions.py
apps/api/tests/test_episode_one_click_pipeline.py
apps/api/tests/test_asset_image_idempotency.py
```

新增失败用例只针对行为：单阶段未执行不能完成；最后报告 FAIL 不能完成；同 key 不同关键帧参数冲突；Job 创建和 item 关联间崩溃可恢复。旧 v1 若保留历史语义，用测试锁定其兼容行为，并明确新 v2 的正确合同，不粗暴改原断言让它“变绿”。

**验收**：一份简短差异清单，列“仍存在／已有等价实现／必须调整的设计”。不使用本文当作不再读代码的理由。

### WP01 持久生产计划基础

**新增**：`production_sessions.py`、`production_session_repository.py`、session schemas/routes、迁移和必要端口。

**复用／修改**：`command_idempotencies`、outbox、API router 装配、release migration contract。先实现 SINGLE_EPISODE，不一次开放全部范围。

实现：第 6 节四表及索引；只读 plan；原子提交；状态读模型；带 expected_revision 的暂停／取消意图。临时选择和 job link 表可以初期为空，但 schema 必须与后续接口同一合同。

**测试**：新增 `test_production_sessions.py`、`test_production_session_repository.py`。同请求重放、异参同 key、跨进程争抢、事务回滚、范围归属、取消后的晚写入。

**验收**：提交后重启 API 仍能查到同一 session；重复提交不产生第二份 item。这个阶段 UI 不显示“已支持一键出片”。

### WP02 修复有版本的阶段推进与关键帧恢复

**修改**：`automation_workflows.py`、`worker_handlers/automation_task.py`、`episode_production_runs.py`、`shot_keyframe_generation.py`、`worker_sessions.py`。

**新增**：`production_outcomes.py`、session scheduler 的单项推进、v2 task 状态最少字段迁移。

实现：COMPLETE／WAITING 等阶段报告；最后一个阶段产物验收；一次消费完成事件；依赖终态后复检；关键帧 batch 的 request_hash、PLANNED 恢复、stable intent/job key、按角色统计；技术重试预算不被绕过。

不要继续用 full `EpisodeProductionRunService.preflight()` 一次要求所有未来产物已存在。新 `start_for_session()` 分两层：启动时验证来源和所需能力配置，阶段投放时验证该阶段的具体输入。例如 KEYFRAME_GENERATION 本来就是为了补缺关键帧，不能在 start 时先要求人工批准关键帧。

**测试**：新增 `test_production_stage_progression.py`；扩展关键帧、恢复与 automation 测试。覆盖所有 Wait／失败／结束边界。

**验收**：两个阶段带异步子 Job，任一时刻 run 状态与真实阶段一致；report 重投不多走一步；最后子 Job 失败不会显示预览完成。

### WP03 机器暂用与单集预览链

**修改**：`episode_worker_actions.py`、`keyframe_references.py` 的调用方、`queries/media_eligibility.py`、`shot_identity_references.py` 的新 context 入口、`timeline.py`、TTS handler。

**新增**：`production_choices.py`、候选资格纯函数及按 session 查询。

实现：先只使用已批准身份包；关键帧按 FIRST/END 角色机器暂用；视频按本次输入生成；有限候选 QC；TTS 临时选择；字幕及预览时间线明确输入；渲染结果登记后 PREVIEW_VERIFY。

timeline.py 不整体重构，仅抽取共用的输入解析／组装；旧人工时间线读写与新自动预览按来源区分。保持后期已有音轨和制作规格修补。

**测试**：新增 `test_production_choices.py`、`test_production_preview_pipeline.py`。空关键帧／空视频起点也能真实调度测试执行器，不得预塞所有媒体后宣称完成这一任务。

**验收**：同一集完整从“没有关键帧和视频”推进到具有真实文件的待审预览。过程中没有自动写入任何人审 APPROVED；已有人工工作槽、时间线不被替换。

### WP04 前端单集入口闭环

**修改**：`EpisodeProductionWorkspace.tsx`、`StoryboardBatchActions.tsx`；新增共用 `ProductionLaunchDialog` 和 session hooks。

实现：主按钮、补缺入口、范围清晰、一次配置、后台进度、关闭页面后恢复、问题 deep link。保留原“继续未完成／原输入重试／新 Take／重新渲染”含义。

**测试**：`ProductionLaunchDialog.test.tsx`、现有两个工作区测试、Playwright 单集 preview 流程。

**验收**：用户无需知道 workflow/job ID，也能开始、暂停、继续、审片；100 镜以上的整集补缺没有只处理当前页。

### WP05 整部与容量窗口

**修改**：`production_session_scheduler.py`、`production_sessions.py`、`worker_sessions.py`；给现有 generation batch 提供有界投放参数。

**前端**：`ProjectHomePage.tsx`、`EpisodeProgressLibrary.tsx`、新 `ProjectProductionWorkspace.tsx`、路由与 AppShell。故事页小样只增“进入本次生产”链接。

实现：ALL_EPISODES／SELECTED_EPISODES；父项先存后执行；active episode 和 GPU queue window；共享资产复用；按 session 精确汇总；局部问题让位；真实 Job／Attempt／磁盘预算；旧 whole-drama 接口维持旧响应。

不要直接复用旧 inspect 的 latest 查询计算新 session 状态；若想让旧接口转调新实现，先做明确版本／适配测试，第一版可不迁移旧入口。

**测试**：新增 `test_production_session_scheduler.py`、`test_production_session_budget.py`、项目页与路由测试；模拟 50 集每集 100 镜，实际队列始终在窗口内。

**验收**：第 2 集受阻时第 3 集继续；程序在投放第 3 集后退出，重启不会重复 1—3 集；取消本次整部不会取消手动创建的外部任务。

### WP06 原稿和资产自动准备

**修改**：调用 `pipeline_orchestrator.py`、`EpisodePreparationService`、资产批次和身份包服务；`generation.py` 所有相关身份快照冻结／再次校验点、`shot_identity_references.py`。

**新增**：可置于 `production_sessions.py` 同目录的小型 `production_preparation.py`，只负责组合已有服务。

实现：PROJECT_SETUP 项；用户选择的自动应用结构范围；分集准备恢复；共享角色草稿和临时 pack 快照；全链路传递受控 ProductionInputContext；首版按三视图规则；新角色使用只限当前生产。

**测试**：扩展 pipeline、episode preparation、character identity packs、shot identity references；新增临时包不能通过旧人工批准查询、另一个 session 无权引用、槽位变化使快照失效等用例。

**验收**：隔离项目从原稿开始，无逐镜人审操作也能输出待审预览；临时包和所有素材的正式人审状态仍未被伪造。原有手工绑定包的完整规则继续通过。

### WP07 集中审核与局部返工

**修改**：`EpisodeReviewWorkspace.tsx`、existing review APIs/services、`ShotStudioCommandService` 与 repository 的 CAS 采用入口、timeline 和交付薄适配。

实现：本次结果固定清单；机器暂用说明；候选替换；按原因筛选；repair plan；单镜补抽；按最新选择重建时间线；批量人审与旧 token 冲突；已批准分集导出。

**测试**：新增 `test_production_repair_plan.py`、`test_production_review_scope.py`；前端审核流程与对应 E2E。

**验收**：换一个镜头仅重做必要部分；未审旧候选没有被批量批准；重合成后旧 render 审核不会自动转移到新 render；同内容批准不会无故重生成全部视频。

### WP08 长时间运行、发行和恢复验收

**修改范围**：根据实测补 `worker_sessions.py`／Runtime Host 配置、存储预算与状态读模型；需要新增 Windows 防休眠支持时再改平台层。不预先重写 `cmd/runtime-host`。

完成：第 13 节分层验收；验证日志有界、磁盘预留、断点恢复；迁移新 head、OpenAPI 客户端、打包／恢复合同和使用文档。

**验收**：有真实 24 小时证据，至少一次实际进程重启恢复，全部结果能按 manifest 对应文件；若机器性能使只完成少量镜头，记录实数，不以数量不足伪造负载；禁止用快进虚拟时钟取代真实 soak。

### 12.2 工作量判断和停止扩张点

M1 已涉及执行语义、媒体选择和后期输入，是实质功能开发，不是给页面加几个按钮。建议先按上述任务独立交付，不按未经实测的“几小时全完成”承诺排期。

可以推迟的范围：视觉 AI 打分、多机、云接口、无限节点编辑、自动生成 BGM、所有镜头口型、自动跨项目持续取新任务、审美指标排行榜。每引入一项新能力都应有真实使用理由，不把它们作为 M1 前置条件。

若 WP03 发现 session choices 对 timeline 改造过大，先支持“自动填充空工作槽”也只能作为明确受限实验：必须原子保护人工值、记录自动 provenance、解决首尾帧角色，并保持独立预览／审核语义。不能默默删掉这些合同换取表面跑通；正式方案仍推荐按本次 choices 构建预览，避免污染人工剪辑。

## 13. 测试与验收方案

### 13.1 本次已经做过的验证

在当前工作树使用仓库 `.venv/Scripts/python.exe` 运行，五组测试全部通过，共 **83 个测试**：

| 文件 | 收集到的测试数 |
|---|---:|
| `apps/api/tests/test_whole_drama_orchestrator.py` | 16 |
| `apps/api/tests/test_episode_production_modes.py` | 33 |
| `apps/api/tests/test_episode_run_recovery.py` | 6 |
| `apps/api/tests/test_shot_keyframe_generation.py` | 16 |
| `apps/api/tests/test_episode_worker_actions.py` | 12 |

命令：

```powershell
.\.venv\Scripts\python.exe -m pytest apps/api/tests/test_whole_drama_orchestrator.py apps/api/tests/test_episode_production_modes.py apps/api/tests/test_episode_run_recovery.py apps/api/tests/test_shot_keyframe_generation.py apps/api/tests/test_episode_worker_actions.py -m 'not comfyui' -q
```

运行存在 Starlette/httpx 与 Alembic path_separator 的弃用告警，没有本次测试失败。测试数据使用 `conftest.py` 的隔离临时目录和迁移数据库。这些通过结果是**现有功能的回归证据**，不证明本文新增功能已经实现。

另使用隔离数据库确认了有限 workflow 的提前完成问题，读取到：

```text
automation_workflow_runs: status=SUCCEEDED, task_count=1
jobs: state=QUEUED
job_attempts: count=0
```

说明：最初探测脚本在 start 后额外调用 step，因 run 已终态报错；随后只读检查该隔离库确认了上述状态。Windows 临时目录退出时还有数据库句柄未释放的清理错误，因此这里记录的是已验证的状态事实，不把初始脚本包装成一次无异常的测试通过。

本次未跑全仓 check、全部前端、真实生成、24 小时 soak。仓库现有 `test_episode_one_click_pipeline.py` 明确预置已批准的前期事实与视频候选，适合验证后半链路；它不能代替“从无关键帧／无视频开始自动生成”的新验收。

### 13.2 单元和数据库集成测试矩阵

不要只 mock 一个 `run()` 返回 SUCCEEDED。状态机测试用可控时钟和假执行器，数据库集成使用真实 SQLite 与迁移。每项都断言外部可观察行为、真实持久关系或产物，避免为内部函数改名编写大量重复测试。

| ID | 场景 | 必须断言 |
|---|---|---|
| T01 | plan 一次／多次 | 无 Job、无选择写入、不联系模型，稳定输入得到稳定 hash |
| T02 | 双击提交 | 同一个 session、同一份 items、同一个业务 Job |
| T03 | 同 key 不同参数 | 409，旧记录不变，不返回错误请求的结果 |
| T04 | 响应发送前 API 退出 | 重试得到已接受的原 session |
| T05 | 两个 API 进程同时提交 | 数据库唯一性保护成立，不能只靠线程锁 |
| T06 | 两个 scheduler 抢同项 | 只有一个有效 dispatcher 和一次投放 |
| T07 | 旧 dispatcher 租约过期后回写 | token 不匹配被拒绝 |
| T08 | 第一／最后一个 Job 未执行 | 不能 READY_FOR_REVIEW 或成功完成阶段 |
| T09 | 最后一个阶段报告 FAIL | 不能因 batch exhaustion 忽略失败 |
| T10 | 完成事件重复投递／重启补投 | 一次消费、一次预算结算、不跳两阶段 |
| T11 | 子 Job 仍运行 | 阶段 WAITING，CPU 已释放，不写 HITL |
| T12 | 两候选一成功一失败 | 能按策略选成功项；失败依赖不导致复检永久排队 |
| T13 | 子 Job 全失败 | 有预算则有限补抽；无预算则问题项清楚可见 |
| T14 | Job 成功但产物未登记 | 阶段保持等待／恢复登记，不能误判材料齐全 |
| T15 | 关键帧 PLANNED 后进程退出 | 重启补投未关联项，不重新生成已投放项 |
| T16 | 创建生成 Job 后关联前崩溃 | 按稳定业务 key 找回、补 link，不增加候选 |
| T17 | 旧参考、旧镜头 revision | 不算本次可用候选；历史仍可查 |
| T18 | 首尾帧混合历史 | END_FRAME 不满足 FIRST_FRAME；组号匹配明确 |
| T19 | 多视图模型被设成关键帧路线 | 保留 SINGLE_FRAME 合同阻塞，不产拼图冒充镜头 |
| T20 | 101 镜／分页 50 镜 | 处理完整 scope，单批 ≤100，不只处理首屏 |
| T21 | 超过单集时间线上限 | plan 明确阻塞，不悄悄截为 500 镜 |
| T22 | 跨项目／跨镜候选 | 拒绝引用，不因 media_id 存在就可用 |
| T23 | 人工换版本与自动回调竞态 | 人工槽位不被覆盖，自动结果留候选 |
| T24 | 人工拒绝的候选 | 同输入不被再次自动暂用，除非人工明确改决策 |
| T25 | 机器挑选／QC | 人工 APPROVED 记录数量没有自动增加 |
| T26 | 临时角色包 | 本次可预览，旧 approved 函数仍拒绝，其他 session 无法直接引用 |
| T27 | pack 槽位内容变更 | hash 失配阻止新投放，旧内容证据保留 |
| T28 | 相同内容人工批准 | 不误判角色内容变化导致全剧重做 |
| T29 | 新 seed 与技术重试 | 新抽 seed 变化；原 Job Attempt 重试参数完全不变 |
| T30 | candidate 最大值／失败候选 | 所有新增 Variant 计入预算，不靠失败免费无限抽 |
| T31 | TTS 文本／音色变更 | 旧 TTS 不被复用；相应字幕和口型依赖失效 |
| T32 | 无声线授权 | 不自动勾选、不静默换声线；独立镜头继续 |
| T33 | 对白比镜头长 | 不截台词当成功；有限调整或明确阻塞 |
| T34 | 有声目标缺 TTS | 不能标有声预览完成 |
| T35 | 有人工编辑时间线 | 自动预览不覆盖它、不意外成为其默认版本 |
| T36 | render 执行中产生新 timeline | 仍引用冻结目标；下一步不另取 latest |
| T37 | 文件大小相同但内容被替换 | 完成验证检查 hash，拒绝坏产物 |
| T38 | 前后镜 HARD 依赖 | 按依赖推进；循环在计划时拒绝 |
| T39 | 单集受阻／失败 | 不占满窗口，不阻止其他独立集 |
| T40 | 全部剩余项阻塞 | session WAITING_USER，不无限空转或伪成功 |
| T41 | 队列窗口与共享 GPU | owned 新投放量受配置约束；遵守既有全局资源租约 |
| T42 | 正在执行时到 deadline | 不新增任务；当前任务按策略收尾；预算可解释 |
| T43 | 两根目录同卷／不同卷 | 正确累计预留，不能重复认为空间可用 |
| T44 | 完成后再次 reconciliation | 字节和 Attempt 不重复计数 |
| T45 | 暂停与完成竞态 | 产物保留但不生成下一阶段；resume 不重复消费 |
| T46 | 取消与 provider 晚回调 | 不恢复运行；晚产物可登记但不改变选择 |
| T47 | 复用外部任务 | 取消 session 不取消 REUSED Job |
| T48 | provider 已接受但本机未保存 ACK | 对账恢复，不盲目创建新候选 |
| T49 | 用户新增／删除／重排集 | 原 session 范围固定；删除／归属变化给可见冲突 |
| T50 | 只换 BGM／字幕样式 | 不调用图像或视频生成器，只重做相应后期 |
| T51 | 人工审片时 choices 变化 | 旧审核 token 拒绝，不批准未看过的新版 |
| T52 | 批量批准当前结果 | 只批准固定清单，不批准全部历史候选 |
| T53 | 老项目迁移与旧 workflow | 原数据和语义不被强制转换，新功能默认不自动启动 |
| T54 | 项目包导入／复制 | 不带入有效 worker token、不自动继续生产、不引用原项目 choices |
| T55 | v1/v2 模型平台兼容 | 使用已有真实执行路线，原 Profile override 和快照来源不丢失 |
| T56 | 磁盘不足于渲染中发生 | 文件进入已有存储恢复／quarantine，不能登记空成片成功 |

### 13.3 前端测试矩阵

至少覆盖：

1. 主按钮在正确页面出现，当前项目和分集范围可见。
2. 未选镜时不能提交“所选镜头”；整集动作可以作用于未加载分页。
3. Dirty draft 保存未完成时不生成旧 plan；保存失败给出具体失败项。
4. plan 后改设置使旧预检失效，旧异步响应不重新激活按钮。
5. 网络超时重试保留原 key；按钮 pending 防双击但正确性仍由后端保证。
6. “已调度”“等待资源”“预览完成待审”“人工已通过”文案准确。
7. 切项目和页面重载后恢复同一个 session，不重复提交。
8. PAUSED 时当前 Job 收尾有正确提示；RESUME 不重复生成媒体。
9. 问题项链接到正确 project/episode/shot，并保留审核列表筛选与返回位置。
10. 大量候选分页、缩略图加载和播放互斥，键盘可操作，错误有 role=alert。
11. 旧编辑页、审核页、模型设置页与现有 draft guard 回归不退化。

组件测试以“用户操作→请求合同→可见结果”为断言，不只 snapshot 一大段 HTML。

### 13.4 五类端到端样本

| 样本 | 起点 | 终点 | 执行环境 |
|---|---|---|---|
| E1 最小关键帧 | 1 集 3 镜，已有已批准角色，0 张关键帧 | 补齐首帧并机器暂用，0 条人审批准新增 | 快速可控执行器 + 实际小图片登记 |
| E2 单集预览 | 1 集 6 镜，0 图／0 视频，3 条对白 | 可解码预览片、精确 timeline、字幕、待审状态 | Mock 推理可用于 CI；本地真实 FFmpeg |
| E3 新角色前期 | 已提交原稿，无身份包／无分镜 | 自动 draft/apply、临时身份包、待审预览 | WP06 后测试；单独记录模型是否真实 |
| E4 整部恢复 | 3 集，第二集有可控失败，第三集可独立完成 | 1/3 集完成，2 集待处理，重启后不重做完成集 | 隔离数据库、可注入错误的执行器 |
| E5 人工返工 | 一集已出预览 | 换一镜、补抽、重新合成、人工批准、导出可验证文件 | 浏览器 + API + Worker |

额外做一次“两个人物同镜、身份参考数量和工作流槽位匹配”的真实关键帧验收，防止自动化把多角色镜头退化为单人肖像。

### 13.5 故障注入

测试必须落在明确窗口，而不是随机重启后看看有没有报错：

| 注入点 | 期望恢复 |
|---|---|
| 父 session 已写，首个子 run 未写 | scheduler 根据持久 item 启动一次 |
| 子 Job 已写，batch item 尚未关联 | 找回同一个 Job 并补关联 |
| provider 已接收，provider_job_id 尚未登记 | 已有 uncertain submit/recovery 路径处理 |
| 文件已写入 work，media promotion 未完成 | StorageOperation/Comfy reconciliation 继续登记 |
| Job SUCCEEDED，完成 outbox 尚未消费 | v2 阶段重放报告一次 |
| Stage COMPLETE，父 session 尚未刷新 | 父汇总 reconciliation 收敛 |
| 选中候选与人工采用同一瞬间 | revision/token 防覆盖 |
| 磁盘阈值、OOM、runtime 重启 | 有上限退避、没有无限尝试与重复成片 |

先自动化故障注入测试，再做隔离项目的真实进程重启。不要对用户正在生成的生产进程做断电演练。

### 13.6 24 小时验收标准

分三层执行，证据分开：

**层 1：虚拟时钟与压力模型。** 快速覆盖 24 小时预算、数千规划对象、过期租约、重复完成事件和排队上限。不调用真实模型，不称作真实长跑。

**层 2：真实小样。** 选择实际可用的图像／视频／声音模型，完成 1—2 集。记录 GPU 型号、显存、模型／Profile／workflow 版本、分辨率、时长、候选数、有效视频数、实际耗时和最大磁盘增量。关闭浏览器后确认 Worker 仍推进。

**层 3：真实 24 小时 soak。** 在隔离测试项目和明确预算内保持服务运行 24 小时，有可执行素材时持续生产；有限任务提前完成后合法空闲也要记录。至少演练一次受控重启恢复，并在日志中标出时间。

必须达成：

- 未出现重复逻辑投放、自动伪造人审、覆盖人工采用、跨项目引用。
- 实际 GPU 队列和同时执行数未超过策略及现有租约合同。
- 任意展示为完成的结果都有可验证文件；缺镜头不冒充整集。
- 可恢复问题能继续，局部失败不会阻止独立集；未恢复问题均有原因与下一步。
- deadline／磁盘／任务／候选上限准确停止新投放；取消最终收敛。
- 没有持续增长且无法回收的句柄、内存、未结算 lease、无界日志；对照启动和每小时快照记录趋势，出现增长必须解释。
- 记录 `planned / dispatched / attempted / materialized / selected / preview_verified / human_approved` 七类计数，不能只报总 Job 成功率。

产出一份运行 manifest、按小时状态汇总、故障恢复日志、真实媒体 sample 和未解决问题清单。没有完成 24 小时，就明确写“尚未完成长跑验收”。不以有 `--watch` 参数为验收通过。

### 13.7 开发时的验证命令

以下命令供 Sol 在实现后执行；除第 13.1 节已说明的命令外，本次没有执行它们。

```powershell
# 当前修改相关 API 测试；先选本任务触及的文件，不必每次跑全仓。
.\.venv\Scripts\python.exe -m pytest apps/api/tests/test_production_sessions.py apps/api/tests/test_production_choices.py -m 'not comfyui'

# 所有不接触 ComfyUI 的 API 回归，脚本会临时禁用 Comfy 访问并恢复环境。
pnpm run api:test:safe

# API 契约变更后生成，不手改 generated/api.ts。
.\.venv\Scripts\python.exe scripts/generate_client.py

# Web 构建与测试。
pnpm --dir apps/web build
pnpm --dir apps/web test

# 新 E2E 文件创建后，使用已有配置；先确认 API/Vite 指向隔离测试项目。
pnpm exec playwright test --config tests/e2e/playwright.config.ts production_session.spec.ts

# 发布前完整门禁。默认不包含真实 ComfyUI 测试。
pnpm run check
```

`tests/e2e/playwright.config.ts` 当前使用本机 Edge 路径及 localhost:5173，执行前按现有约定核对，不把真实用户项目当测试数据。真实 ComfyUI／TTS／GPU 验收另行按测试说明执行，不混进纯单测。

新迁移用 pytest 隔离 fixture 验证空库升级和旧库副本升级，实际升级生产数据前使用 Runtime Host 的恢复集流程。不要在调试命令里随手对用户数据库执行迁移／回滚。

## 14. 兼容、迁移、上线与容量评估

### 14.1 数据与接口兼容

1. 新表和新字段增量迁移；旧记录默认无 session、无临时选择、v1 执行语义。不把所有历史运行自动编造成生产 session。
2. 旧关键帧、人工采用、身份包、TimelineRevision 和交付文件继续可读。历史关键帧没有 frame_role 时不能随意猜为尾帧；明确的既有人工首帧事实可按兼容适配处理，其他情况标角色待确认。
3. 旧 API 和旧客户端默认行为不变。新 scope/review 策略只在新增 session endpoints 生效，不能通过默认参数静默开启自动化。
4. 新生成 task 的 metadata 保存 execution_semantics_version=2；没有版本的历史 workflow 永远不是按名字猜新版本。
5. 旧 WHOLE_DRAMA 模板可在高级工作流页保留，并改清楚用途说明。新项目主入口只使用新生产会话，避免两个看似相同的整剧按钮承诺不同结果。
6. 新 schema 纳入 `docs/release/migration-contract.json` 和相关迁移测试；不照本文猜迁移编号、不产生未经处理的多 head。
7. release 回滚继续使用“匹配代码 + 经验证的迁移前恢复集”，不以 Alembic downgrade 删除新生产历史。界面回退开关不等于数据库可以倒退。

### 14.2 模型和平台兼容

当前仓库同时有业务使用的旧 Profile/workflow 表和 Model Platform V2 的快照、执行链接、rollout 机制。新 session 是编排层，不应成为推动整个模型平台大迁移的借口。

- 通过现有 generation/preferences/rollout 服务取得实际可执行路线；视频、关键帧、TTS 使用同样的用户 override 和能力合同。
- 保存业务接口返回的 profile_version、workflow/runtime/snapshot 及 native model locator 证据，不根据相同名称认定两份配置等价。
- v2 已接管的 capability 保持 v2 原生执行；仍走旧路线的业务继续使用已验证旧路线。两者均遵守现有快照和 worker 契约。
- Workflow 只支持 1 人参考时，多角色镜头不能无声只传第一个角色；必须能力匹配或有明确用户批准的替代计划。
- H3／Comfy、llama.cpp、VoxCPM2、SAPI、LatentSync 的硬件差异由现有 capability 与 runtime 处理，不在 UI 写死模型参数。
- 保持本地执行与 LOCAL_ONLY；不用外部云服务解决本机能力缺口。网络查询仅是本次写方案的参考，不是产品自动调用公网的授权。

### 14.3 项目包、复制、备份和存储

修改 `application/project_packages.py` 时采用明确策略：

- 项目包可以包含完成 session 的摘要、choice provenance 和相关媒体引用，以便复现。
- 活跃 dispatcher token、worker session、资源 lease、next-check 控制信息不可作为可执行状态移植。
- 导入副本时 remap 所有项目／集／镜头／媒体／身份包引用；运行状态默认停止或历史只读，不能导入后自动烧 GPU。
- 如果第一版暂不支持导出 session 元数据，应在 manifest 明确声明排除范围；不能静默丢失恢复证据后声称完整可恢复。常规整机备份仍需覆盖数据库与媒体。
- 媒体清理必须把 production_choices、active jobs、timeline、render、delivery 和人工采用加入引用保护；不能因为一个候选没进入全局 working slot 就当垃圾删除。
- 清理 staging 和日志使用现有 storage operation/quarantine、保留期与路径校验；不设计“每天删掉所有落选卡”的默认策略。

### 14.4 分批启用

采用一个服务端入口开关或现有功能开关体系启用 `production_sessions_v2`；默认不自动迁移和启动任何旧任务。无需再设计十几个互相组合的开关。

启用顺序：内部隔离样本 → 当前项目一集 → 两集小样 → 一组分集 → 整部 → 新原稿自动准备。发布前有活动生产时沿用 Runtime Host drain/stop 流程；升级后先 reconciliation，再接受新投放。

关闭新品入口时保留已有 session 的查询、暂停和取消；不能因回退 UI 导致后台失控或用户无法停止。若需要停新投放，由明确服务端控制执行，不删除队列。

### 14.5 如何估算一晚上能做多少

不要用外部平台宣传速度估算本机产能。先用现有历史 p50/p90 按**相同 Profile、分辨率、时长、候选、运行时**建立样本；冷加载和模型切换另计。

粗略工作量：

```text
关键帧新任务 = 各镜必需角色的缺口候选数之和
视频新任务   = 各镜本次视频候选缺口之和
TTS 新任务   = 当前文本／声线输入需要补齐的对白行数
总执行时间   ≈ 各实际串行资源上的生成耗时 + 模型切换 + QC/对齐/合成 + 重试
```

不同资源可以部分重叠，因此这是解释性估算，不能简单把独立阶段的 p90 相加后声称为整部 p90。应返回范围及样本数；没有数据就未知。

算术示例，**不是本机测试数据**：12 集 × 每集 18 镜，共 216 镜；只做首帧，每镜先 2 张图、1 个视频；每镜平均 3 条 TTS，则约 432 图 + 216 视频 + 648 TTS。假设图 30 秒、视频 180 秒、TTS 6 秒，纯生成串行合计约 15.5 小时，尚未计资产、加载、对齐、合成及失败。

这个例子也说明示例 API 的 600 个 Job 预算不足以完成全范围；plan 应显示需要提高预算或减少分集，而不是运行到一半才告诉用户。实际预算必须根据项目真实镜头和可复用产物计算。

优先减少无效生成：先关键帧筛选、先复用已认可资产、先确保一个可用视频、最后再为重点镜头补抽。提高候选数量不必然提高整部可用率。

## 15. Sol 执行合同与交付清单

### 15.1 可直接交给 Sol 的指令

> 请按本仓库 `docs/gpt/LocalDramaStudio_一键生产与持续运行开发方案_2026-09-21.md` 实施 LocalDramaStudio 的一键生产能力。先做 WP00，读取当前 HEAD、未提交修改和已有等价实现，保护其他工作的改动，不重写已实现的批量关键帧、Job 队列、Worker、身份包、TTS 和时间线系统。按 WP01—WP04 先完成“已有已确认资产与分集方案，从无关键帧和无视频开始，一键生成本集待审预览片”的垂直闭环，再推进整部窗口、集中审核和 24 小时运行；原稿自动准备按 WP06 单独验收。每个阶段给出实际代码、迁移／契约变更、相关测试、真实运行证据与未解决问题。生成、机器暂用、人工采用、人工批准不得混同；未知能力不得用假产物或预置全部视频冒充自动生成成功。所有异步操作可重放，取消保护外部任务，重试不重复生产，人工选择不被自动覆盖。保持 LOCAL_ONLY 和现有模型平台路由。只在实现范围确实需要时抽取模块，不另建队列／工作流产品，不引入大型依赖。不在未完成真实 24 小时验收时宣称已支持可靠连续生产。

### 15.2 每个任务包的完成报告

每个 WP 用同一格式简短交付：

```text
任务包与已完成用户行为
修改文件与关键接口／数据迁移
本次复用的既有服务
实际运行的测试命令和结果
真实生成／模拟执行的范围
失败恢复与人工数据保护的证据
剩余限制和下一任务依赖
```

不要只报告“加了按钮”“后端已接通”或“所有状态都是绿色”。也不要把生成客户端、测试 fixture 或占位实现当生产功能完成。

### 15.3 最终验收清单

- [x] “补齐本集关键帧”入口明显，支持首帧／首尾帧、分页全量和缺口复用。
- [x] 单集可从无图／无视频起点自动产出可校验预览片。
- [x] 品质档位和审核时机互相独立，新旧 API 默认兼容。
- [x] 机器暂用有 provenance，未伪造人审批准，未覆盖人工选择和时间线。
- [x] session、阶段、Job、Attempt、产物之间有完整可恢复关系。
- [x] 最后一个 Job 和产物完成之前，不显示整集或整部完成。
- [x] 部分失败不会阻塞独立集；全阻塞时停在准确可解释状态。
- [x] 所有实际媒体子任务受任务、候选、时间、磁盘和 GPU 窗口限制。
- [x] 断点恢复和 provider 对账不产生重复候选；暂停／取消竞态已验证。
- [x] 首尾帧／frame bridge 角色正确，临时身份包不污染项目全局批准事实。
- [x] 声音和字幕遵守真实文本、时长、授权与输入版本。
- [x] 集中审核固定清单、局部重抽和精确重新合成可用。
- [x] 导出只引用真实验证过的内容，正式审核继续由原服务执行。
- [x] 迁移、客户端生成、旧项目与项目包兼容有测试。
- [x] 真实零媒体单集小样有独立文件、探测和 SHA-256 证据；不使用模拟数据冒充。
- [x] 真实双集整部小样按容量窗口串行推进，两集均形成独立待审预览且没有自动写入人工批准。
- [ ] 真实 24 小时 soak 已达到 86,400 秒有效墙钟覆盖并输出 `PASS_REAL_24H`。

只有 M1 完成时，产品文案应写“支持已有制作准备的单集自动预览”；M2 完成后写“支持整部持续生成待审预览”；M3 完成后才写“支持原稿到待审整部”。这个分阶段承诺既能尽早使用，也避免大而全的一键按钮掩盖未打通的环节。

## 16. 2026-09-21 实施记录

本节记录本方案首次落地后的真实代码状态，供后续 Sol 继续实现时核对；它不是用计划替代验收。

### 16.1 已落地

- 新增 `0096_production_sessions`，持久化 session、episode item、session choice 与 Job lineage；迁移接在 `0095_video_upscale_delivery` 后，后续 `0097_video_upscale_previews` 已顺序接入，发布迁移合同为单一 head。
- 新增 V2 计划、创建、列表、详情、item、启动、刷新、暂停、恢复、取消、待审投影、逐集确认、候选换选和 item 局部重试接口。计划 hash、命令幂等、revision 乐观锁和分页上限均已实现。
- WorkerSupervisor 启动时及常驻循环会恢复活动 session；整部生产使用 `max_parallel_episodes` 容量窗口，不再受旧两集 pilot 限制。
- 修复有限自动化在最后一个 Job 仍为 `QUEUED` 时提前报告成功的问题；阶段只有在实际 Job `SUCCEEDED` 后才能结束。
- 关键帧和视频均支持 session 内 `MACHINE/TEMPORARY` 选择；机器选择不会写 `review_decisions`，不会写人工批准，也不会调用旧 QC 的全局自动采用去写 `FORMAL_SELECTION/PROXY_WINNER`。视频选择必须具有精确媒体版本的 PASS 机器质检。
- TTS 现也遵守相同的 session 输入语义：`TTS_BATCH` 创建的子 Job 快照携带 `production_session_id`，因此计入会话任务、Attempt 与输出预算；`TTS_FINALIZE` 只收尾当前自动化任务直接依赖的 TTS Job，强制关闭旧的全局自动采用，音频机器质检通过后写入 `DIALOGUE_LINE/TTS_AUDIO` 的 `MACHINE/TEMPORARY` choice。普通 `dialogue_candidate_selections`、人工审核与全局采用事实不被写入；已有有效全局人工 TTS 仍可作为兼容输入复用。
- 会话重试会先复用文本 revision、音色版本和已验证媒体都仍匹配的临时 TTS choice，不重复提交声音 Job。缺少音色绑定或授权时，普通交互式生产仍在预检阶段严格阻断；生产 session 把缺口延迟到 `AUDIO_SUBTITLE`，先完成可独立执行的画面工作，且明确记录 `silent_voice_substitution_allowed=false`，不会静默换音色。
- 字幕草稿与时间线对白轨会按精确 `production_session_id` 覆盖读取本次 TTS choice，选择和媒体指纹进入 session 时间线快照。字幕 revision 记录 session provenance，只使同一 session 的旧时间线失效；会话渲染和集中审核均按该 session 的时间线及其 render 定位，即使后来出现普通人工时间线，也不会串用另一条预览链。正式交付继续按既有“最新整集批准版本”合同读取，不能由 session 临时事实绕过。
- 新建生产 session 的无人值守工作流固定停在 `TIMELINE_ASSEMBLY → RENDER` 的已验证待审预览，不再把必须依赖人工整集批准的 `DELIVERY` 放进自动图中。旧版本已经冻结且仍带 `DELIVERY` 的持久工作流会无副作用地返回 `DELIVERY_DEFERRED_TO_HUMAN_REVIEW/SKIPPED`，从而兼容恢复并到达集中审核；普通非 session 工作流和交付 API 的批准门禁保持不变。
- 后台正式交付结果优先按不可变 Job／operation id 精确关联 `delivery_packages`，旧 Job 才回退到冻结输入组合查询；回退会把 API 的显式 `NONE` 控制值规范成数据库 `NULL`。这修复了交付文件已真实构建、Job 已 `SUCCEEDED`，但后台操作详情因水印 `NONE/NULL` 表达差异仍返回空结果的问题。
- 待审投影会比较 session 视频 choice 与最新时间线真实引用；不一致、时间线未冻结或预览成片未验证时禁止确认。
- 逐集确认只绑定已有、未 stale、精确对应候选的人工 `APPROVED` 决策，并要求当前 session 预览同时是本集最新合成版本且已有匹配当前 revision 的有效整集批准；确认接口自己不制造审核决定。工厂页会分别提示缺少候选批准或预览成片批准。全部分集确认后 session 才进入 `COMPLETED`，此时原交付页可按既有批准合同构建正式交付。
- 工厂页为每个尚未批准的 session 候选和当前预览成片提供精确审核链接，使用 `targetKind/targetId` 直接打开既有正式审核的对应对象，避免在本集全部历史媒体中手工寻找。已确认分集显示只读“本集已确认”，不再保留一个看似还能提交的确认按钮，并直接提供“进入本集交付”入口；用户可以从集中审核自然进入既有正式交付流程。
- “换一个”按稳定顺序选择下一个已验证候选。视频换选只更新 session 内 `MACHINE/TEMPORARY` choice，不新增全局 `FORMAL_SELECTION`，不改 `media_assets.selected_version_id`、revision 或 version counter；它会保留旧产物、只把 `input_snapshot.production_session_id` 属于本会话的预览时间线标为 `STALE`，并以新 attempt 顺序执行 `TIMELINE_ASSEMBLY → RENDER`。组装器直接读取精确 session 视频 choice 并在时间线快照记录 session id 与 choice fingerprint，已有人工正式时间线和选片事实不被覆盖。关键帧换选会明确阻塞在视频重生成，避免复用不匹配的旧视频。
- 局部重试支持失败阶段、仅重组预览和整集重跑；每次 retry 使用新 attempt 和新幂等键，旧错误与旧运行仍保留。
- 集中审核现在由服务端返回只读 `repair_plan`：缺视频选择使用 `FULL_EPISODE` 补缺并复用已有 VERIFIED 输入，时间线／预览问题使用 `RECOMPOSE_ONLY`，生成依赖失败使用 `RETRY_FAILED_STAGE`。资产确认或预算扩容是显式前置条件，未完成时页面不显示可执行返工按钮；前端不再固定猜测一种 retry 策略。审核项的 `allowed_actions` 同样由服务端按 `repair_plan` 派生：前置条件未完成时只暴露对应处理动作，可立即返工时才暴露 `REQUEST_LOCAL_RETRY`，进入人工审核时只暴露确认动作。
- 磁盘低于 session 的保留阈值时 item 进入 `RESOURCE_WAIT`，常驻 reconcile 在空间恢复后自动继续，不消耗人工重试。
- 暂停、恢复、取消现在在同一数据库事务内传播到关联 Workflow 与 Job。排队 Job 暂停后不可领取；恢复重新排队；运行中取消进入 `CANCEL_REQUESTED`，未运行任务直接 `CANCELLED`。
- 同一项目只允许一个具有自动写入权限的活动 session；重复启动会返回现存 session 的 ID 和状态。准备阶段复用的外部 Job 标记为 `EPISODE_PREPARATION_REUSED`，取消 session 时不会误取消它；session 自建 Job 仍按 OWNED 语义取消。
- 项目首页新增“一键漫剧工厂”，支持单集／整部预检与启动、质量档、分集并发、checkpoint、TTS、跨重启会话列表、进度、控制、集中预览、正式审核跳转、换候选、局部重试和最终确认。
- 本集制作页新增“无人值守生成本集”入口，使用带 episode 参数的 canonical route 进入同一工厂页；工厂会自动选择 `SINGLE_EPISODE` 并冻结目标分集，仍先执行只读预检，不在跳转时提交生成。
- 工厂页新增“一键补齐本集关键帧”：自动读取本集全部镜头，按质量档补到 1／2／4 个候选，支持只补首帧或补首尾帧，并复用既有批次的缺口计算、plan hash、幂等提交和恢复语义。
- 生产 session 的前端 DTO 已从手写业务客户端移入 `scripts/generate_client.py` 的生成契约；业务 client 只导入并转出生成类型。后端增加字段后，TypeScript 会直接要求页面和测试夹具同步，避免两份 DTO 静默漂移。
- 整集单条审核、超分批量审核及本机模型库目录写入接口现有显式 response model；OpenAPI 与 TypeScript 客户端由当前注册路由重新生成。SPA 的 API 兜底会区分“已注册路径但方法不符”和“路径不存在”：前者保留标准 405，退役或未知 `/api/...` 返回统一 404，不再被前端页面 fallback 吞掉。
- 新生产会话应用服务已改为依赖现有 `DatabaseUnitOfWork` 端口；会话编排层仍需复用既有业务服务的 26 个直接构造点，已在架构债务清单中逐项登记 `owner=production-sessions-orchestration/remove_by_slice=12`，没有为通过门禁另造空端口或复制业务逻辑。
- Web 继续使用页面级 lazy load；构建新增 manifest 当前发布检查与 vendor 分包，保留旧 hash chunk 的同时避免把历史文件误算进本次预算。
- 新增 `0098_production_session_identity_inputs`。生产会话可冻结完整、已验证、已项目授权的 `DRAFT/READY_FOR_REVIEW` 三视图包作为当前 session 的 `MACHINE_TEMPORARY` 输入；该事实不修改 pack 的 `APPROVED` 状态、不更新全局 current approved version，也不创建审核决定。
- 关键帧 `plan → preflight → submit → GenerationVariant/Job snapshot` 已显式携带服务器解析的 `production_session_id`。提交前会重新计算精确槽位与媒体 hash；草稿槽位变化会得到 `PRODUCTION_SESSION_IDENTITY_INPUT_STALE`，另一 session 无法读取原 session 的临时输入。
- 同一镜头可以逐角色混用正式已批准身份包和会话临时身份包，快照会记录每个包及整体的 `selection_authority/human_approved`，不会因一个正式角色存在而漏掉另一个临时角色。
- Episode Production 启动会先幂等复用完整草稿，再以 session 身份快照执行启动 preflight，并把快照加入生产输入 fingerprint；恢复时重新核对同一快照。旧的交互式关键帧、通用 Generation API 和非 session 生产仍只接受正式身份权威。
- 当角色已有 HERO、但缺少完整三视图时，session 的 `ASSET_COMPLETION` 会复用 `AssetMultiViewService` 的 `IMAGE_MULTI_VIEW` preflight 和 submit，按 FRONT／LEFT／RIGHT 投放真实 Generation Job，并把每个 Job 作为 `IDENTITY_VIEW_*` OWNED lineage 关联到对应 session item。自动化的下一任务显式等待这些依赖，不用轮询假完成。
- 当角色连 HERO 都没有时，session 工作流会在 `ASSET_COMPLETION` 前插入 `ASSET_HERO_COMPLETION`：复用现有 `AssetImageGenerationBatchService` 的纯文生图 preflight/submit，按 `IDENTITY_HERO` 关联 Job；HERO 任务成功并完成媒体登记后，下一阶段才提交三视图。两波异步任务分别进入既有 Job 依赖图，不在 CPU worker 中阻塞等待 GPU，也不会用一张未生成的占位图越过三视图合同。
- HERO 完成登记支持 reconciliation：即使 Job 已写成 `SUCCEEDED` 后进程在绑定资产参考前退出，后续三视图阶段也会从 VERIFIED artifact 幂等恢复 HERO。启动 preflight 同时验证纯文生图 Profile 和多视图 Profile；只延迟可由前一阶段解决的 `HERO_REQUIRED`，其他能力缺口仍 fail-closed。
- Worker 只在三视图 Job 实际成功并产生 VERIFIED 图片 artifact 后，晋升媒体、创建默认缩略图、完成项目工作区授权并登记机器生成的资产参考。三个必需视角齐备后才生成／更新 `DRAFT` 身份包并注册 session 输入；任何完成登记失败都会把 session item 停在 `ASSETS/BLOCKED`，不会把生成 Job 成功冒充身份准备成功。
- 三视图完成处理可对已经 `SUCCEEDED` 但在进程中断前尚未登记的 Job 执行 reconciliation；重复完成、已有同视角参考和重复 dispatch 都复用既有事实或底层幂等回执。
- 新增 `0099_production_session_asset_inputs`。自动应用拆解后，对名称有效且唯一匹配的待决角色建议，session 可精确复用现有资产；没有匹配时创建标记为 `provisional=true/human_approved=false` 的临时资产，并按原拆解的逐镜人物证据补写镜头绑定。`story_asset_proposals.status` 始终保持 `PENDING`、`resolved_asset_id` 仍为空，不会把机器决定写成 `ACCEPTED_NEW/ACCEPTED_MERGE`。
- 拆解中的结构化 `scene.location` 与逐镜 `shots[].props` 现在也会形成待审 SCENE／PROP 建议。session 仅按这些明确字段逐镜绑定临时资产，不从标题、动作或自然语言猜场景／道具；正式名称或人工配置的唯一别名可安全复用，别名歧义继续阻断。
- `ASSET_HERO_COMPLETION` 已扩展为按 CHARACTER／SCENE／PROP／COSTUME 类型分批复用 `AssetImageGenerationBatchService`。所有实际用于镜头且缺 HERO 的关键资产先生成并登记 VERIFIED 主图；只有 CHARACTER 继续进入三视图和身份包阶段。每批保持稳定幂等键、100 项上限和原 Job lineage，进程中断后仍由既有完成登记 reconciliation 恢复。
- HERO 可用性现在同时要求 ACTIVE 资产引用和 `media_versions.integrity_status=VERIFIED`。损坏或未验证的旧引用不会让主图补齐错误跳过，完成登记也不会把新产物错误标成被损坏引用取代。
- 自动应用的完整分镜可保持全局 `DRAFT`，仅在当前 session 内按 `MACHINE_TEMPORARY` 进入生产；应用审计明确记录 `application_authority=MACHINE_TEMPORARY` 和 `human_approved=false`。名称无效、同名多资产、类型或作用域不一致继续 fail-closed。
- 集中审核会列出 session 临时资产输入。人工必须在既有资产建议面板中确认同一资产，临时输入才变为 `CONFIRMED`；选择不同资产、拒绝或仍为 PENDING 都阻止最终确认本集，避免生成用的角色与最后批准的角色不一致。
- Job 调度器已能把失败依赖传播为 `JOB_DEPENDENCY_FAILED`；session runner 现在进一步把这种后台状态收敛为可见的分集 `BLOCKED`，不再永久显示 RUNNING。局部重试会先显式重排 session 自有的失败 HERO／三视图 Job，再创建新一轮工作流依赖；外部受理未知仍要求先对账。
- 新增 `scripts/production_session_soak.py` 与运行手册。它按真实墙钟观察一个实际 session，原子保存有界状态样本，最终重新校验关联 artifact 文件与 SHA-256；记录器进程中断后使用同一输出可续跑并累计 invocation/restart 证据。少于 86,400 秒只能输出 `PASS_SHORT_REHEARSAL/real_24h=false`，不会冒充真实 24 小时验收。
- `PASS_REAL_24H` 还要求观察到会话离开 `READY`、至少一个关联 Job，并最终保留至少一个重新计算 SHA-256 后仍有效的 VERIFIED artifact；空闲 session 放置一天会明确输出 `FAILED`。观察期累计量独立于有界 sample 数保存，旧样本被裁剪后仍不会丢失真实活动事实。
- 长跑证据区分墙钟跨度与有效采样覆盖。相邻采样正常累计，超过两个采样周期的断档只计有限容差；记录器或整机停机后可以从同一证据续跑，但必须补足实际观察时长才可能得到 `PASS_REAL_24H`，不能用“启动一次、一天后再启动一次”伪造连续长跑。
- 增加真实子进程 `os._exit(17)` 故障注入：子进程在持久化 session/item 的运行快照后退出，父进程重新构造 runner 并连续 reconciliation；同一 automation run 保持一份，session Job link 幂等收敛为一份，没有重新投放 episode run。该测试证明提交后进程退出窗口，不替代真实模型进程/整机断电演练。
- 新增 `0100_production_session_waiting_user`。当 session 已没有可自动推进的 PENDING／RUNNING／资源等待项，且剩余项全部是 BLOCKED／FAILED 时，状态收敛为 `WAITING_USER`，WorkerSupervisor 不再空转扫描；页面显示“需要人工处理”。修复后对具体 item 执行局部重试会显式回到 `RUNNING`，不会把等待人工处理误写成终态或批准事实。
- 生产会话配置现已冻结最长运行时间、最多新建 Job、最多 Attempt、最多已验证媒体输出字节数、GPU 活动队列上限和单轮镜头调度数。旧 session 不迁移 JSON 也能通过服务端兼容默认值读取；会话详情实时从 Job、Attempt、VERIFIED media 和全局 GPU 队列计算使用量、剩余量与阻塞原因，不依赖内存计数。运行时长从首次 start 开始计算，READY 状态等待用户不会消耗预算，存在 `finished_at` 后计时冻结。
- 硬预算耗尽只阻止新的分集／生成波次，已经运行的子任务可以完成；没有其他可推进项时 session 进入 `WAITING_USER/BUDGET_WAIT`。新增 `:extend-budget` 幂等命令只允许单调提高上限，使用 revision 锁防并发覆盖，并以新的 session run attempt 重新武装因预算停止的分集，不把资源扩容伪造成审核批准。
- GPU 队列达到上限时，尚未投放的分集进入 `RESOURCE_WAIT`，容量释放后由常驻 reconcile 自动继续。工厂页展示真实任务、尝试和 GPU 占用，并提供默认 24 小时预算的高级配置；“提高已耗尽预算并继续”只提高服务端实际报告已耗尽的预算维度，不会连带放宽任务数、输出空间或 GPU 窗口。
- 创作者选择 `AFTER_ASSETS`、`AFTER_SHOT_PLAN` 或 `BEFORE_VIDEO` 后，底层 run 的 `PAUSED_HITL` 会被父 session 汇总成 `WAITING_USER`，工厂页直接显示会话级“继续”，无需用户查找 workflow/run ID。继续操作会在同一控制事务内恢复该 session 关联的门禁 Job；普通资产歧义、预算阻塞等没有可恢复门禁的 `WAITING_USER` 仍只允许取消，直接调用 RESUME API 也会被拒绝。
- session 内关键帧与视频生成不再由一个 Worker action 一次性投放整集。工作流按镜头数、质量候选数、分集并发和 GPU 窗口展开为有界串行波次；每一波只提交 `dispatch_job_limit` 内的真实 Generation Job，下一波显式依赖上一波 Job 完成。关键帧批次支持只截取本波 ready items，视频波次支持候选级截断并把剩余镜头标为 `DEFERRED_CAPACITY`，后续波次继续补缺口。
- 使用完全隔离的数据、项目、工作和缓存根目录做过真实 CLI 短跑：从空数据库升级到 `0099`，创建真实持久 session，再由 `production_session_soak.py` 写入证据并用同一命令续跑；随后同一数据库已原位升级到 `0100` 并成功读取原 session。当前证据有 3 次 invocation、`restart_count=2`、约 547 秒墙钟跨度和 2 秒有效覆盖；结果为 `PASS_SHORT_REHEARSAL`、`real_24h=false`、`production_activity_valid=false`、SQLite integrity=`ok`、artifact 校验通过，位于 `work/evidence/production-session-short-rehearsal-20260921-054556/evidence.json`。这只证明迁移、命令入口、断点续记、采样覆盖和证据落盘可运行，不替代真实生产或 24 小时验收。
- 使用 `backups/pre_migration_20260905T103407Z.sqlite3` 做过不接触运行实例的隔离升级／恢复演练：真实执行 `0093 → 0094 → 0095 → 0096 → 0097 → 0098 → 0099 → 0100`，升级副本最终为唯一 `0100` head、SQLite integrity=`ok`，恢复副本与源备份 SHA-256 完全一致；证据位于 `docs/evidence/g10/upgrade-rollback-rehearsal-0100_production_session_waiting_user-2026-09-21.json`。`release_audit.py` 已移除 `0031 → 0039` 和 `0041` 的旧硬编码，改读 `migration-contract.json`、自动选择符合当前 head 的演练证据，并在旧数据库上输出 `IN_PROGRESS/UPGRADE_DATABASE` 而不是因新列缺失崩溃。
- 另对当前实时 `data/local_drama.sqlite3` 执行官方 `rehearse-upgrade`：在 API 与嵌入式 Worker 保持运行时，通过 SQLite 在线备份取得约 108 MiB、22 项目的隔离副本，只在副本上执行 `0094 → 0100`。升级后完整性为 `ok`，项目、季、集、镜头、Job、媒体资产、媒体版本、整集渲染和交付包九类核心表计数与源快照逐项相等，六张生产会话表全部存在；源库仍保持 `0094` 且实时 API 继续 `HEALTHY`。证据位于 `docs/evidence/g10/live-database-upgrade-rehearsal-0100-2026-09-21.json`。这证明当前真实数据可迁移，不代表实时实例已经迁移。
- 使用上述升级副本的独立复制件，在 `127.0.0.1:43211` 以禁用 Worker 的方式启动当前源码；API 完成 startup/lifespan 后为 `HEALTHY`，`POST /api/v2/projects/{project_id}/production-sessions:plan` 对一个无图片、无视频、无渲染、无历史 Job 的单镜头项目返回 `production_session_plan_v2`、1 集／1 镜、零 warning，并完成优雅关闭。证据位于 `docs/evidence/g10/production-session-api-preflight-2026-09-21.json`。该预检只证明升级后启动和计划路由可用，没有冒充真实生成完成。
- 在旧进程没有活动 GPU／CPU 生产任务后，先用官方备份得到 `backups/manual_20260921T040055Z.sqlite3`（完整性 `ok`、head=`0094_project_target_duration`、SHA-256=`842b42735644d98fe1d05b4dbdac125d0115faa183104badc635bb805c0f905b`），再执行 `python -m local_drama.entrypoints.maintenance upgrade`。升级命令自动创建 `backups/pre_migration_20260921T040146Z.sqlite3` 并先在副本演练，随后实时数据库原位达到唯一 head `0100_production_session_waiting_user`，完整性仍为 `ok`；API／嵌入式 Worker 重启后健康检查、生产会话计划路由和三条 Worker channel 均正常。
- 真实零媒体 UAT 使用项目 `video_5fa4ef6eb2eb420b` 的单镜头分集：起点为 0 图片、0 视频、0整集渲染、0 历史 Job。会话 `42ee2489-caf8-4ba4-a815-627c93def336` 实际生成资产 HERO 和多轮关键帧，随后以同一 session 的 `MACHINE/TEMPORARY` 关键帧选择提交真实 H3 I2V；视频 Job `1f6157a6-6f15-4e3b-afde-050955402cc8` 成功输出 480×832、24 fps、107 帧、4.458 秒 MP4。机器 QC 通过后写入临时视频 choice，时间线冻结并合成 480×854、24 fps、4.458 秒、含静音 AAC 轨的预览片。最终 session 为 `WAITING_REVIEW`，审核投影为 1 集 ready、0 blocked、2 个临时 choice、0 confirmed，时间线与 choice 为 `MATCH`，`human_approval_written=false`。
- 真实 UAT 证据位于 `docs/evidence/g10/production-session-real-zero-media-uat-2026-09-21.json`。证据独立记录 62 个会话期间 Job、59 个 Attempt、59 个 Artifact，并对所有 Artifact、被选关键帧、被选视频和预览片重新读取真实文件并复算 SHA-256；预览片文件大小 542,358 bytes，全部完整性断言通过，所选输出没有任何 `review_decisions`。该证据只声明 `PASS_REAL_ZERO_MEDIA_TO_REVIEW_PREVIEW`，没有声明 24 小时通过。
- 真实运行暴露并修复三项兼容缺口：计划现在统计 `asset_bound_shot_count/missing_asset_binding_count` 并对未绑定镜头给出 `EPISODE_ASSET_BINDINGS_MISSING`；正式 I2V 的 `VariantPlan` 会冻结 `production_session_id`，仅允许同一会话已验证的临时关键帧，并在 Job 媒体绑定快照写入 `source_production_choice_id`、保持 `source_approval_id=null`；失败阶段重试会直接复用同一 session 的关键帧 choice，不再重复投放关键帧。普通 Generation API 和另一 session 仍必须具备人工批准关键帧，未降低原安全门禁。
- 增加隔离整部真实验收工具 `scripts/prepare_production_session_whole_drama_uat.py` 和只读取证工具 `scripts/capture_production_session_uat_evidence.py`。准备工具使用在线 SQLite 备份和单项目目录复制建立独立 instance，通过正式 Project、Shot Studio、生成偏好、故事资产和生产会话服务追加第二集；新镜头复制并重新校验导演意图，继承已验证的镜头级 I2V Profile，不伪造媒体或批准。取证工具交叉检查 session/review、Job/Attempt、artifact、选择媒体、Comfy history 和逐文件 SHA-256。
- 隔离双集会话 `78b5c303-1b70-4fee-b5ed-74c851def71a` 已在 RTX 3090 Ti 上真实完成。`max_parallel_episodes=1` 时首集先进入 `WAITING_REVIEW`，第二集才自动接棒；两集最终均为 `READY_FOR_HUMAN_REVIEW`，0 blocked、0 reviewed、4 个 `MACHINE/TEMPORARY` choice、0 confirmed、0 timeline mismatch。两集分别生成新的 Qwen 关键帧；首集明确复用已有 VERIFIED 视频，第二集以 prompt `426a0195-a950-4baf-9efd-260cdd4b51de` 新生成 MiniMax H3 视频，并分别合成独立预览片。证据记录本会话 35 个 Job、35 个 Attempt、35 个 Artifact，全部文件和 4 个选择媒体 SHA-256 匹配，3 个 Comfy prompt history 均为 success，`human_approval_written=false`；文件位于 `docs/evidence/g10/production-session-real-whole-drama-uat-2026-09-21.json`，结果为 `PASS_REAL_WHOLE_DRAMA_TO_REVIEW`。
- 双集验收首次启动时还真实暴露了 provider 恢复顺序缺陷：启动 reconciliation 先把不确定上游置为 `NEEDS_ATTENTION` 并传播到下游，随后 Comfy history 又证明上游成功，但下游仍被搁置。`JobService.requeue_recovered_dependencies` 现在只重新排队 `last_error_code=JOB_DEPENDENCY_FAILED` 且全部依赖已持久化为 `SUCCEEDED` 的 Job；WorkerSupervisor 在 provider 对账和业务输出恢复之后、工作流 watchdog 之前执行该步骤，常驻循环采用相同顺序。其他人工处理状态不会被自动解除，新增回归测试覆盖失败传播、上游恢复和下游重新领取。
- 真实 24 小时记录器已经针对上述 session 启动，证据持续写入 `work/evidence/production-session-real-soak-42ee2489-caf8-4ba4-a815-627c93def336.json`。记录器进程在三次受控 API 重启后仍存活，并已观察到真实 Job 与产物；当前结果仍为 `IN_PROGRESS`、`real_24h=false`，必须达到 86,400 秒有效墙钟覆盖并最终复核文件后才能勾选长跑验收。
- 当前只读发布审计保存在 `docs/evidence/g10/release-readiness-2026-09-21.json`。刷新后的审计确认实时数据库完整性和迁移 head `0100`、最近五份备份、升级／恢复演练、metadata scale、安全、恢复与 stale-job 证据；总体仍为 `IN_PROGRESS`，因为有序 G7/G9、旧总需求映射、只读基线和 SBOM FINAL 等仓库级发布门禁尚未闭合。迁移 head 已不再是阻塞项。

### 16.2 当前诚实边界

- M1 单集待审预览已经通过真实本机零媒体单镜头小样；M2 整部容量窗口也已通过上述双集真实 ComfyUI 样片，产品可以准确承诺“支持已有制作准备的整部持续生成待审预览”。这份双集证据包含已验证视频复用与新视频生成两条路径，不等同于原稿自动准备验收。
- 已确认原稿范围但没有分镜的分集，会复用 `EpisodePreparationService` 的可恢复拆解与自动应用。无歧义的人物、结构化场景位置和逐镜道具建议可作为 session 临时资产继续；所有关键资产走“无 HERO → 自动主图”，人物再走“自动三视图 → 草稿包”。名称／别名歧义、缺少结构化场景／道具字段或多人镜头没有精确出镜证据时仍会 fail-closed，最终确认仍要求人工处理原 proposal。尚未完成真实本机模型的原稿到整部端到端验收，因此仍不能宣称完成“原稿到待审整部”。
- 尚未完成真实 24 小时 wall-clock soak、真实模型进程强制 kill／整机断电演练；本轮已完成真实双集 ComfyUI 样片，并在 provider 已完成、worker 退出的窗口验证了持久对账和缺陷修复。产品仍不能写“已通过 24 小时可靠性认证”。
- 当前 3210 端口已运行迁移后和最终修复后的 API（PID 35204），数据库 head 为 `0100`；`/api/v2/.../production-sessions` 计划、待审和控制路由均由真实 TCP 客户端验证，嵌入式 Worker 正常。三次 API 重启都只在 Comfy 队列和项目活动任务为空时执行，session 从持久化状态恢复，最终仍为 `WAITING_REVIEW`。
- 2026-09-21 本机 runtime 已实际使用 ComfyUI 0.33.1 与 RTX 3090 Ti 完成 Qwen 关键帧和 MiniMax H3 I2V，不再只是只读 smoke。运行结束后 Comfy 队列为 0；真实视频、派生媒体、机器 QC、session 时间线和 FFmpeg 预览均有数据库与文件证据。
- 批准后整部导出继续复用项目交付页及既有交付批准规则；本次没有建立第二套打包系统。

### 16.3 本次新增核心测试

- 计划确定性、超过两集范围、创建幂等、陈旧计划、会话列表、HTTP 合同。
- 容量窗口、Worker 恢复、准备 Job lineage、连续三次新建 runner 模拟进程重启且不重复投放、抽卡后 `RECOMPOSE_ONLY`、磁盘等待后自动恢复。
- 机器临时关键帧／视频 choice 不写人工批准、待审时间线一致性、人工确认与幂等重放。
- 暂停／恢复／取消向 Workflow 与 Job 的传播；最后 Job 未成功时 workflow 不提前成功。
- 前端关键帧一键补缺、整部预检→创建→启动、canonical route、项目首页与本集制作页入口；TypeScript、Vite 构建和 500 KiB chunk 预算。
- 项目级活动 session 冲突、OWNED/REUSED 取消隔离、迁移图与发布 migration contract、OpenAPI 及生成客户端契约。
- 会话草稿身份不能通过旧的正式批准查询；会话内可解析且明确 `human_approved=false`；槽位变化使冻结快照失效；第二个 session 不可越权读取；session 资产完成检查复用草稿后仍不产生批准审计。
- 缺三视图时只提交三个既有多视图能力 Job，并写入 session Job lineage；真实 FRONT／LEFT／RIGHT 参考齐备后组装 DRAFT 包和 session input，重放不重复创建人工权威。
- 完全缺 HERO 时先提交既有资产主图批次，并验证工作流顺序固定为 `ASSET_IDENTITY → ASSET_HERO_COMPLETION → ASSET_COMPLETION → EPISODE_PLAN`；主图 Job 作为下一阶段真实依赖，且 session 取消可覆盖该 OWNED Job。
- 自动应用的 DRAFT 分镜不改写为人工 READY；无歧义角色建议生成／复用 session 临时资产，原 proposal 保持 PENDING；集中审核确认相同资产后才解除最终确认阻塞。
- 结构化场景位置和逐镜道具会生成 SCENE／PROP 建议并精确绑定；唯一正式名称／别名被复用，临时资产仍保持 PENDING；CHARACTER／SCENE／PROP 分类型主图批次均进入同一可恢复 Job 依赖链。
- 双 runner 同时持有旧资产 plan 时，事务内会重查并复用先写入的 session input；临时资产编码冲突只在来源确实不一致时 fail-closed。并发测试验证每个 proposal 最终只有一个 provisional asset 和一个活动 input，不再把唯一键异常暴露给持续运行循环。
- HERO／三视图依赖失败会从队列传播到 session item 的可见 BLOCKED；局部重试只重排 session 自有失败 Job，测试覆盖不再永久 RUNNING 和重试不新建重复资产任务。
- 长跑证据器短跑测试覆盖原子落盘、真实 session/Job 状态采样、数据库 integrity、artifact 清单、同一证据文件续跑与 `restart_count`；短跑明确保持 `real_24h=false`。
- 长跑证据器另有快进时钟的反例测试：空闲 READY session 即使达到 86,400 秒，也因没有实际 Job/产物而 `FAILED`；该测试只验证拒绝伪证据的判定，不被记录成真实 24 小时运行。
- 记录器断档反例测试验证 24 小时墙钟跳变在 10 秒采样配置下只累计 20 秒有效覆盖，并保持 `IN_PROGRESS`；断档不会被计入真实 soak。
- session 故障注入使用真实子进程退出码 17，验证新 runner 重建、run/job lineage 恢复和重复 reconcile 不产生第二次投放。
- 全部剩余项阻塞时 session 进入 `WAITING_USER`，不再出现在活动 reconcile 扫描中；状态列表可筛选，局部重试后恢复 `RUNNING/PENDING`。迁移测试同时覆盖既有 0099 数据库原位升级到 0100。
- 整部会话中“部分分集已到待审、其余分集全部阻塞”也会收敛为 `WAITING_USER`；待审等待不再被误算成机器可推进工作，父会话优先展示真实阻塞阶段，避免无人值守期间空轮询。
- 自动化任务因机器失败或需修复条件进入 `PAUSED_HITL` 时，父 session 会把该分集收敛为可返工的 `BLOCKED` 并立即释放分集并发槽位，其他 PENDING 分集继续；只有 `CONFIGURED_CREATOR_CHECKPOINT`／节点人工门禁保留 `WAITING` 并通过父会话“继续”。这避免并发为 1 时一个声音或素材问题锁死整部。
- 上述混合状态不会锁死已到待审的独立分集：候选重抽按目标 item 的 `WAITING/WAITING_REVIEW` 事实授权，父 session 为 `WAITING_REVIEW` 或因其他分集阻塞而成为 `WAITING_USER` 时均可执行；尚未到待审的 item 仍由服务端拒绝。
- session 计数进一步区分 `review_waiting`、`gate_waiting` 和 `machine_waiting`。工厂页分别显示待调度、待审核、阶段等待与需处理，不再把准备任务、GPU 资源等待或创作者 checkpoint 都写成“待审核”；旧 session 没有新计数键时仍按父状态兼容显示。
- 新增预算回归覆盖：运行时长耗尽后不再投放并进入 `WAITING_USER`；预算单调扩展、revision 和幂等重放；GPU 队列占满时自动等待及释放后恢复；通过不可变 snapshot 统计 session 自有 Job/Attempt。生成波次测试验证 4 镜×2 候选在 GPU 窗口 4 下展开为两波关键帧和两波视频，每波上限为 4；视频 action 的候选级截断验证单轮不会越过 3 个 Job。HTTP 合同测试还覆盖了 `:extend-budget` 的实时预算投影和相同幂等键重放。
- 新增返工计划回归覆盖：缺视频选择不会被错误归类成只重合成；预览失败保留现有媒体；资产审核未完成时禁止直接 retry；生成失败只从失败阶段继续。审核投影集成测试同时断言实际返回的最小返工策略与可执行动作；资产和预算两个前置条件并存时不会错误暴露局部重试。`:retry` 写接口也会在同一数据库事务内重新检查未确认的 session 临时资产和实时预算硬上限，直接调用 API 不能绕过前端隐藏的前置条件。
- 机器视频重抽回归夹具预置一条人工 `FORMAL_SELECTION`，并逐字段比较重抽前后的全局 selection 历史、`selected_version_id`、素材 revision 与 version counter；只有 session choice 允许变化。
- session 时间线集成测试使用两段真实 FFmpeg 视频：人工全局采用 A，机器 session 临时选择 B；自动组装生成新的 session 时间线并引用 B，快照带 session provenance，重复组装幂等跳过，全局采用 A 及 selection 数量保持不变。重合成工作流固定为 `TIMELINE_ASSEMBLY → RENDER`，两步都携带 production session id。
- session TTS 回归使用真实 FFmpeg 音频并执行现有音频 QC：机器选择只写 `production_choices`，全局对白采用记录保持为空；普通时间线看不到它，带 session id 的字幕草稿和时间线输入精确引用该候选。TTS 收尾处理器另验证只接收当前依赖 Job、`auto_select=false` 并产出临时 choice；后续出现更晚的人工时间线时，会话审核和渲染仍使用原 session 时间线。
- 声音缺口延迟测试确认普通生产仍返回 `TTS_CONFIGURATION_MISSING/BLOCKED`，session 仅把同一缺口延迟到 `AUDIO_SUBTITLE` 且禁止静默替代；两集并发 1 的异常门禁测试确认第一集声音失败后变为 BLOCKED、第二集立即投放，而显式创作者 checkpoint 仍可从父会话恢复。
- 修复 ForcedAligner 常见的省略标点兼容问题：对齐 token 只提供时间证据，校验时仅容忍空白和 Unicode 标点差异，再把时间映射回权威剧本文本；输出继续保留原文标点和原文断句，实际文字不一致仍拒绝对齐并回退。新增句末标点缺失与文字不一致反例，完整时间线测试及生产会话、重组、自动化和旧 G6 工作流共 79 项关联回归通过。
- 真实 UAT 后新增 session 关键帧授权和重试复用回归：普通 I2V 继续拒绝未批准输入；同一 session 的 VERIFIED 临时 choice 可进入预检，错误 session 被拒绝；Job 快照保留 choice provenance 且不写批准；失败阶段重试跳过已有 session 关键帧。四项聚焦回归、完整 `test_episode_worker_actions.py` 以及 production sessions／choices／runner／worker actions 共 40 项关联回归通过；最新改动 Ruff、compileall 和 `git diff --check` 通过。
- 本机 runtime 只读体检输出位于 `work/evidence/runtime-smoke-production-session.json`；`production_database_contacted=false`、`production_mutated=false`，未把健康检查冒充媒体生成。
- 最终定向后端回归覆盖资产、拆解、session、runner、soak、单集生产、来源绑定、Job、自动化、迁移、发布合同与 OpenAPI，全部通过；本轮补充的角色声音、TTS provider、旧整部自动化、会话 TTS、时间线隔离、异常容量释放、预览／交付边界与集中审核关联域共 90 项再次通过。正式交付另通过“未批准拒绝、仅最新批准可交付、真实 FFmpeg 时间线→渲染→后台交付→结果读取”3 项定向回归。第一次完整安全 API 套件得到 `1567 passed / 4 failed / 1 skipped / 4 deselected`，暴露并修复了磁盘耗尽后的注意状态断言、关键帧 prompt 不应包含对白文字、退役 API 应返回 404、架构债务门禁四处问题；随后针对 SPA fallback 补齐了已注册路径 405 兼容。第二次完整安全 API 套件在显式禁用 Comfy/GPU 的隔离环境中得到 `1571 passed / 1 skipped / 4 deselected`，耗时 54 分 02 秒，零失败。OpenAPI 快照和生成客户端已从当前注册路由重新生成并通过一致性测试。生产工厂、项目交付、路由、生产设置、单集制作与审核共 53 项定向前端测试通过。前端全量最初暴露两处陈旧门禁：候选组件已使用创作者可读中文标签，旧测试仍断言 `PROXY/FORMAL`；项目交付对比视频仍设置 `preload=metadata`。测试断言已与产品文案同步，两个对比视频改为 `preload=none`，13 项聚焦回归通过，随后前端全量 `647/647` 通过。TypeScript 检查、Vite 生产构建及 bundle 预算再次通过（54 个 chunk，最大 chunk 约 295.2 KiB）；全部改动 Python 文件的 Ruff、compileall 和 `git diff --check` 通过。当前 mypy 会沿导入图报告 12 个既有模块中的 54 项类型错误（如 `comfy_diagnostics.py`、`dialogue_timing.py`、`local_llm.py`），因此未把 mypy 记录为本轮通过项，也没有为通过门禁而扩大修改范围。
