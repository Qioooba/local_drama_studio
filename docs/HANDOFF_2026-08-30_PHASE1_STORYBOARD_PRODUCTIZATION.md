# HANDOFF 2026-08-30：Phase 1 本地模型与故事板产品化

## 结论

本轮完成的是原设计内已有的导演台批量生产与轻量时间线能力，并系统验收了此前接入的 LatentSync、VoxCPM2 声线克隆、ForcedAligner 词级字幕和 RAG 改编分析。实现遵守“计划后提交、不可变版本、机器检查不等于人工批准”的现有产品契约。

没有实施 Phase 3 的一键修脸、2D/3D 机位、智能 BGM，也没有新增缺乏真实需求的 QuickCreate 导入。本轮也没有把 RAG 文段静默注入镜头生成 Prompt：剧本拆解已有权威原文范围与覆盖率约束，额外检索注入会增加重复或虚构风险；RAG 继续服务于有显式来源证据的改编分析。

## 已完成能力

### 本地模型链路

- LatentSync：镜头级任务固定视频、音频 MediaVersion 与 SHA-256，worker 离线执行，成功 artifact 显式登记为 `LIPSYNC` 视频；创建、列表、收尾路由已有严格响应模型。
- VoxCPM2：资产圣经支持上传、试听、授权确认、克隆并绑定角色声线；导演声音 Inspector 可使用已绑定声线。
- ForcedAligner：对齐词时间进入字幕切分，无法使用时保留时长式回退；修正了对齐循环的严格类型契约。
- RAG：改编计划继续保留来源与检索证据，不进入无来源的自动批准或镜头 Prompt 改写。

### 故事板批量生产

- 新增共享 `compose_shot_prompt` 编译器，单镜与分集 worker 使用同一导演意图事实；`prompt_modifiers` 只作为最后一层显式修饰，不改写原创作字段。
- 导演台选中镜头可批量写入修饰词，每镜创建新 revision，保留旧版本；重复修饰词按大小写稳定去重。
- 批量视频生成采用显式 `plan → submit`：计划阶段只读且不触碰运行时，提交阶段创建既有 Automation Workflow，每个所选镜头成为独立可恢复任务。
- 目标镜头和预期 revision 被固定；计划过期、缺关键帧、缺能力 Profile 或导演字段不完整都会阻塞提交。
- `force_new_take` 只新增 Take，不覆盖候选；选择、采用、质检和人工批准仍是独立动作。
- 应用服务依赖预检与 workflow 端口，不直接依赖具体 SQLite，也没有引入新的架构债。

### 导演台同步预览

- 从后期编辑页抽出共享 `TimelineLanes`，导演台按需读取同一个 Edit v2 工作区事实。
- 展示采用视频、对白、BGM/SFX、字幕和播放头；点击视频块进入对应镜头。
- 导演台预览是只读的。时长、入点、转场、排序、草稿、冻结与导出继续只属于后期编辑页。

## API 与数据契约

- `DirectorIntentV3.prompt_modifiers: string[]`，最多 20 项、单项最多 80 字；旧 revision 缺字段时按空列表兼容。
- `POST /api/v2/episodes/{episode_id}/storyboard-generation-batches:plan`
- `POST /api/v2/episodes/{episode_id}/storyboard-generation-batches:submit`
- 两个批量端点及三个 lipsync 端点均有严格 Pydantic 响应模型；OpenAPI 快照已重新生成。
- 架构债清单已重新审计；`StoryboardGenerationBatchService` 使用 `StoryboardGenerationPreflightPort` 与 `StoryboardGenerationWorkflowPort`。

## 验证证据

- 后端第一轮安全全量：1083 通过、1 跳过；发现 2 项架构门禁失败，已以端口与响应模型重构修复，相关架构/批量/lipsync 定向测试 9 项通过。
- 后端全仓 mypy：394 个源码文件无问题。
- 后端 Ruff：全部通过。
- 前端全量 Vitest：125 个文件、480 项测试通过。
- 前端生产构建：Vite 构建和 bundle budget 通过，56 chunks，最大 chunk 455.0 KiB。
- 维护性审计：无 hard failures（总体状态 `PARTIAL` 为仓库既有证据覆盖状态）。
- 后端第二轮安全全量：1087 项通过、1 项预期跳过、4 项 ComfyUI 实机用例按安全模式排除；零失败，耗时 31 分 28 秒。

## 工作树边界

工作树同时包含另一项托管 llama.cpp 运行时工作，包括迁移 0087、GPU 生命周期 adapter、模型平台文件、配置、生成客户端和其交接/ADR/证据。本轮没有回退或覆盖这些修改。合并或提交时应按文件和 diff 语义拆分，不能直接把整个脏工作树视为单一改动。
