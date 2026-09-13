# Phase 0 基线记录：最终改造与测试实施方案

- 日期：2026-09-12（Asia/Shanghai）
- 方案：`docs/local_drama_studio_最终改造与测试实施方案_2026-09-09.md` v1.0
- 本阶段范围：只读核验、隔离测试、最小复现；不修改业务源码、生产数据、模型或工作流
- 当前提交：`e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3`
- 报告基线：`f15a354ec81fa56233154ac753c4499356b9c2a2`
- 工作区：仅上述方案文档为原有未跟踪文件；本记录是 Phase 0 新增证据文件

## 1. 当前运行与数据安全边界

当前不是空白开发环境，后续实现前必须保护正在运行的事实：

- `config/config.json`：`LAN_SERVICE`、监听 `0.0.0.0:3210`，Worker channels 为 `CPU,GPU_H3,GPU_LOCAL_AI`。
- 端口 3210 存在由本仓库 `.venv` Python 进程持有的监听器，进程始于 2026-09-07。
- 默认数据库 `data/local_drama.sqlite3` 存在，核验时大小 90,030,080 字节，2026-09-12 19:30 仍有更新。
- 数据库 schema head 为 `0094_project_target_duration`，与 `docs/release/migration-contract.json` 一致。
- 只读盘点：20 个项目、245 集；Job 终态包含 1,333 成功、55 失败、53 取消、11 待处理。
- 当前存在 1 个 `STORY_PIPELINE_DRAFT/CANCEL_REQUESTED` Job；Automation run 有 1 个 `RUNNING`、1 个 `PAUSED_HITL`。

实施 K01 前的安全要求：不复用生产库跑测试；不停止当前监听进程；不处理或删除上述在途/暂停记录；测试继续使用 pytest fixture 数据库和隔离 transport。

## 2. 当前真实入口与执行路线

### 故事规划

前端 `/projects/:projectId/story` 使用 `OneClickPipelineWorkbench`，调用 `/api/v1/projects/{project_id}/pipeline:*`。后端由 `build_pipeline_orchestrator()` 装配 `PipelineOrchestratorService`，其 AI 实现来自 `build_story_ai()`：

```text
Pipeline API
  -> PipelineOrchestratorService
  -> FullStoryAIGenerationService
  -> LocalLLMService
```

该装配确认 K01 位于真实调用链，不是死代码。

### 单集与整剧生产

- 单集生产页面最终进入 `EpisodeProductionWorkspace`，API 为 `/api/v2/episodes/{episode_id}/production-runs*`。
- 整剧入口为 `/api/v2/projects/{project_id}/whole-drama:status|prepare|run`，实现为 `WholeDramaOrchestratorService`。
- 视频生产仍由 `GENERATION_VARIANT` + `GPU_H3` 主链承载；数据库历史有 378 个成功的 `GENERATION_VARIANT` Job，最近使用时间晚于 V2 model-platform execution。
- Model Platform V2 已有 3 个 profile、5 个 version/publication，但数据库中 `mp_business_selection_rollouts=0`、`mp_legacy_profile_version_crosswalks=0`。因此当前不能把 V2 平台存在误写成业务生产已切换。
- 项目仍有 18 条 legacy `project_profile_bindings`，已发布 legacy Profile 包括 `LLM_STORY_PARSE`、`VIDEO_I2V`、图像和 TTS。

结论：本轮主链以当前 legacy Profile/GenerationVariant/Comfy Job 事实为准；V2 snapshot Worker 仅在实际业务 rollout 建立后成为生产切换依据。

## 3. 测试基线与本次实际结果

环境：Python 3.12.10、pytest 8.4.2、Node 22.23.2、pnpm 9.15.9。仓库包含 240 个 API 测试文件、137 个 Web 测试文件和 37 个 E2E 文件。

本次运行 7 个相关 API 测试文件，共收集 66 个用例：

- `test_story_pipeline_ai.py`
- `test_g8_status.py`
- `test_h3_runtime_overrides.py`
- `test_shot_production_normalizer.py`
- `test_shot_prompt.py`
- `test_episode_production_modes.py`
- `test_whole_drama_orchestrator.py`

执行结果：退出码 0。测试使用显式系统临时目录、禁用 pytest cache，没有连接真实模型。该结果只证明已有用例绿色，不能覆盖下列新反例。

已完成的最小复现：

1. K01：`LocalLLMService.client` 签名不存在 `profile_version_id`，真实装配的调用方会传入该关键字。
2. K02：输入表演强度 `0/0.2/0.9` 均被 `ShotProductionSpecNormalizer` 重写为 `0.5`；“推门”会得到虚构的 `SLOW_PUSH`。
3. K03：内存 SQLite 中旧 revision 有 BGM、当前 revision 删除、归档镜头有 SFX，查询仍返回 `BGM + SFX`。
4. K04：同时存在 Turbo 与角色 LoRA 时，将 Turbo 强度设为 `0.77` 会把角色 LoRA 的 `0.33` 也改为 `0.77`。
5. K05：输入 `{speaker: "A", text: "do-not-leave"}` 的最终提示词只有正文，speaker 丢失。

## 4. K01—K30 当前处置

“已复现”表示有本次运行或直接静态可达证据；“待专项复现”表示已有风险定位，但尚未补充该工作包要求的完整反例。没有项目被标记为真实 GPU 通过。

| K | 当前结论 | 本次依据 / 下一步 |
|---|---|---|
| K01 | 待修（已复现） | 真实 `build_story_ai` 注入 `LocalLLMService`；端口调用与实现签名不一致。作为第一个小 PR。 |
| K02 | 待修（已复现） | 合法 performance intensity 被固定为 0.5；动作词参与运镜推断并产生默认创作语义。拆规则与确认命令两个 PR。 |
| K03 | 待修（已复现） | `_field_cue_counts` 扫描本集全部 shot revision，且不排除归档镜头。 |
| K04 | 待修（已复现） | 已能识别 Turbo 节点，但 TURBO 分支仍修改所有 `LoraLoaderModelOnly` 的强度。 |
| K05 | 待修（已复现） | `text or speaker` 只保留一项；现有测试未覆盖同时存在。 |
| K06 | 待专项复现 | 生产模式已有 1/2/4 测试，Worker 仍在入口静默 clamp 到 4；需要补 6/8/16 与 UI/预检一致性。 |
| K07 | 待修（静态可达） | force/stale 分支先于活动任务复用，并用变化中的 `len(jobs)` 生成 take identity；现有测试未做同任务二次进入。 |
| K08 | 待修（静态可达） | 整剧聚合仅输出 COMPLETED/RUNNING/READY，失败、暂停、取消和未开始会被压入 READY；run 只返回调度计数。 |
| K09 | 待专项复现 | EpisodePreparation/Replan 仍按项目最新 committed import session 取源，未证明与本集不可变来源绑定。 |
| K10 | 待修（静态可达） | `_episode_specs` 截到 60；单集 prompt 截到 24,000 字符；当前仅 warning，未记录未覆盖范围。 |
| K11 | 现有能力部分覆盖 | 已有 CHUNK_MAP/ARC_REDUCE 等分层分析；本集 4,000 字符 breakdown 仍需验证有界衔接，禁止新建平行分析平台。 |
| K12 | 待专项复现 | 分层分析的知识检索在运行时追加且不冻结到 Job snapshot；需验证重试输入与调用摘要。 |
| K13 | 待专项复现 | Pipeline 有 quality report 和显式 apply，但 60 集截断只产生 warning；应用影响与旧草案失效需验证。 |
| K14 | 待修（静态可达） | 模型检查使用任意 `usable_models`；preflight 实际探测 Comfy 后仍返回 `runtime_contacted=false/network_contacted=false`。 |
| K15 | 现有能力部分覆盖 | `GenerationService` 已统一调用 prompt bundle/frozen contract；自动视频入口仍直接构造参数，需做单镜/自动入口哨兵穿透。 |
| K16 | 待修（静态可达） | `_end_frame_chain` 明确把前镜尾帧绑定为当前镜 `END_FRAME`；缺前驱时无条件 SKIPPED，未表达硬依赖。 |
| K17 | 待专项复现 | 已有 QC 与 auto-select，但需验证旧合格候选优先、采用 BLOCKED 是否正确传播。 |
| K18 | 待修（静态可达） | `_view` 仍以历史 PASS/selection/render 数量计算阶段完成，尚未统一当前媒体资格。 |
| K19 | 待修（静态可达） | Director batch IDs 被当前半径窗口过滤；完整批次会随导航窗口缩小。 |
| K20 | 待修（静态可达） | 联系表只查 `media_assets.owner_type='SHOT' + selected_version_id + selections`，不读取 V2 working slot。 |
| K21 | 现有能力部分覆盖 | 已有 dialogue timing/native audio/compose guard；实际唯一声音来源与逐说话者音色检查仍待专项复现。 |
| K22 | 待修（静态可达） | 单集入口要求 idempotency key；整剧 schema/前端/路由未传 key，服务回退到随机 UUID。 |
| K23 | 分路处理 | legacy Comfy 路线当前适用，已有 uncertain-success/recovery 机制，需补故障测试；V2 业务切换因 rollout/crosswalk 为 0 暂不进入生产改造。 |
| K24 | 待修（静态可达） | preflight 指纹包含项目全部 references/models，可能让未消费对象使本集失效。 |
| K25 | 待修（静态可达） | `_build_plan` 仍按数组 index 配对 current/proposals，A/B/C -> A/X/B/C 会错配。 |
| K26 | 待修（静态可达） | `_copy_asset_bindings` 只复制 asset_id/role，遗漏已有 `identity_pack_version_id`。 |
| K27 | 待修（静态可达） | Pipeline 成功后由前端 `useEffect` 自动调用 apply，关页后无法承担可靠续接。 |
| K28 | 待专项复现 | prepare/run/review/keyframe 服务均存在；缺的是从零镜到预览的持久衔接与真实暂停原因。 |
| K29 | 现有能力部分覆盖 | 单集已有稳定 start key 和 force_new_take/target_shot_ids；四种操作及影响预览尚未形成统一合同。 |
| K30 | 后续验收项 | 现有分层测试设施充分，但只有 Phase 1—5 范围结项后才执行 L3 小样和两集有限推进。 |

## 5. Phase 1 第一个小 PR：K01

### 目标

故事规划使用用户明确选择且已发布的 `LLM_STORY_PARSE` Profile；不得吞掉选择或静默回落到全局默认。

### 写前必读

- `apps/api/local_drama/infrastructure/service_composition.py`
- `apps/api/local_drama/application/story_pipeline_ai.py`
- `apps/api/local_drama/application/local_llm.py`
- `apps/api/local_drama/application/ports/creative_generation.py`
- 当前 execution profile / provider connection 解析服务与相关测试

### 最小允许改动

优先给 `LocalLLMService` 增加明确的 Profile 解析入口，或在 composition 层注入满足端口的薄适配器；端口、调用方、实现和测试必须使用同一合同。复用现有 ProviderConnection 和密钥解析，不创建第二个模型平台。

### 首个失败测试

使用真实 `build_story_ai()` 与 fixture 数据库，仅替换最终 HTTP transport：

1. 显式发布 Profile：断言请求 model/base_url/provider 来自该 Profile。
2. 默认发布 Profile：断言确定性选中并记录解析结果。
3. 未发布、撤销或能力不符：明确拒绝。
4. ProviderConnection：使用其连接与 secret 解析；本地连接不继承云端 key。
5. 防回归：不能仅通过接受 `**kwargs` 或改为无参调用让 TypeError 消失。

### 合并门槛

- 新失败测试先证明当前 K01；最小修改后通过。
- 重跑 `test_story_pipeline_ai.py` 及 Profile/ProviderConnection 相关测试。
- 不运行真实 LLM/GPU，不修改生产数据库，不改默认模型或网络策略。

## 6. Phase 0 结论

Phase 0 已完成仓库、运行入口、数据安全、schema、执行路线和首批缺陷的基线核验。Phase 1 可以进入 K01，但当前生产 API 与 workflow 仍活动；实施和测试必须继续使用隔离数据库，任何迁移或服务重启都不在 K01 授权范围内。
