# LocalDramaStudio 修复实施记录与回归证据

**基准提交：** `8a63c604a1a13556dbe277d312ecf73bebb52883`（与《深度测试报告》《AI 修复任务书》《接口覆盖清单》一致，已核对 `git rev-parse HEAD`）。
**依据文档：** `docs/gpt6/LocalDramaStudio_深度测试报告_2026-09-22.md`、`docs/gpt6/LocalDramaStudio_AI修复任务书_2026-09-22.md`、`docs/gpt6/LocalDramaStudio_接口覆盖清单_2026-09-22.md`。
**性质：** 本文件按任务书《提交格式》记录每个问题的「问题 ID / 旧行为 / 新行为 / 修改文件 / 兼容性 / 回归命令与结果 / 证据层级 / 残留限制」。

> 记录规则：只写实际执行过的命令与真实结果。未能执行或未验证的项目显式标注「未验证」，不以组件测试或合成数据顶替真实环境验收。

---

## 0. 修复前问题确认（不盲信文档行号）

任务书要求「开始前核对实际HEAD；若代码已更新，先逐项判断问题是否仍存在」。本次对每个条目先读代码再改，确认结论如下。

| 条目 | 文档结论 | 实际核对结果 |
|---|---|---|
| BKT-01 | 两份锁文件漏 `pypdf`，导入 `local_drama.main` 失败 | **确认**：`apps/api/pyproject.toml:17` 声明 `pypdf>=5,<7`，`requirements.lock` 与 `requirements-runtime.lock` 全文均无 pypdf；`application/documents.py:18` 顶层 `from pypdf import PdfReader` |
| BKT-03 | 默认 check 依赖仓库上级蓝图目录 | **确认**：`scripts/g0_validate.py` 用 `Path(__file__).resolve().parents[2]` 定位仓库**上一级**的 `LocalDramaStudio_Blueprint_v2` 与 `model_manifest.json`，并硬断言 FR/NFR/TC=86/15/85 |
| BKT-08 | 缺模型清单会跳过内置审核模板初始化 | **确认**：`main.py` 的 `sync_manifest()` 与 `ensure_templates()` 在同一个 `try` 内，前者抛错即整体跳过 |
| HTTP-04 | 不完整库仍返回 HEALTHY | **确认**：`api/routes/health.py` 的 `ready()` 只校验能否读到任意 `alembic_version` 行 |
| NP01 | 首章前正文被丢弃但仍报 FULL | **确认**：`pipeline_orchestrator._episode_specs` 有章节时从首个章节标题开始建单元；`_source_coverage` 用「完成单元数==计划单元数」推 FULL |
| NP02 | 超 60 章/超 24000 字符只处理首批且无可执行续接 | **确认**：`episode_specs = all_episode_specs[:60]`；`:resume` 直接抛 `PIPELINE_RESUME_UNSUPPORTED` |
| NP03 | 仅改标题也丢失导演字段 | **确认**：`breakdown_revisions._validated_replacement` 重建只含少数表单字段的新 dict |
| NP05/NP06/NP07 | DOCX 软换行丢失、EPUB 嵌套重复、EPUB `../` 引用失败 | **确认**：`documents.py` 的 `_read_docx` 只收集 `w:t`；`_epub_document_text` 对父子块各取一次 `itertext()`；`_read_epub` 直接以 `OPS/../Text/...` 查 ZIP key |
| MED-02 | 非循环音轨忽略 `end_us` | **确认**（且另见第 4 节发现的 `adelay` 毫秒/微秒量纲错误） |

接口覆盖清单本身也做了独立复核（见第 5 节）。

---

## 1. Phase 0：首次启动与可重复测试

### BKT-01（P1）必需依赖未进入锁文件

**旧行为：** 严格按 README 只装两份锁文件时，应用在 `import local_drama.main` 阶段即因缺少 `pypdf` 失败；发行脚本的浅层 smoke 只 `import local_drama`，漏检该问题。

**新行为：** `pypdf==6.16.2`（本机实测版本，满足 `>=5,<7`）写入开发与运行时锁；新增锁文件—pyproject 一致性门禁并接入 `check.ps1`；发行 smoke 提升为导入 `local_drama.main` 并在新临时实例上迁移、创建 app、读取 health/contract。

**修改文件：** `apps/api/requirements.lock`、`apps/api/requirements-runtime.lock`、`scripts/check_dependency_locks.py`（新增）、`packaging/common/build_release.py`、`scripts/check.ps1`、`README.md`。

**兼容性：** 仅新增依赖，不改契约；已装环境无需迁移。

**回归命令与结果：** 见第 6 节「实际执行记录」。

### BKT-03（P2）默认 check 硬依赖仓库外旧蓝图

**旧行为：** 干净 checkout 执行 `scripts/check.ps1` 立即 `RuntimeError: expected exactly one in-scope document for 00`。

**新行为：** 默认 check 可移植（不要求上级目录存在同名蓝图）；外部蓝图/模型清单审计改为显式 `--blueprint-root`/`--manifest` 扩展入口；缺少可选真实环境时输出 `NOT_CONFIGURED` 并指向 UAT，不伪造 PASS。

**修改文件：** `scripts/g0_validate.py`、`scripts/check.ps1`。

### BKT-06（P3）维护审计外部输出路径写完又异常

**旧行为：** `--output` 指向仓库外时 JSON 已写出，随后 `_relative()` 的 `path.relative_to(ROOT)` 抛 `ValueError`，汇总不打印且门禁退出码错误。

**新行为：** 仓库外路径按绝对路径显示，`--output` 语义不变。

**修改文件：** `scripts/maintainability_audit.py`。

### BKT-07（P2）README 称默认不接实时 ComfyUI，`api:test` 未过滤

**旧行为：** `package.json` 的 `api:test` 无 `-m` 过滤，默认收集并执行 4 个实时 marker 用例。

**新行为：** 默认 `api:test` 采用安全 marker 过滤并禁用 Comfy 访问；真实硬件集单独暴露为显式 live 命令；README 写明两者前提。

**修改文件：** `package.json`、`scripts/test_api_safe.ps1`、`README.md`。

### BKT-08（P1）可选模型清单异常跳过必需审核模板初始化

**旧行为：** `main.py` lifespan 内 `sync_manifest()` 与 `ensure_templates()` 共用一个 `try`；清单缺失/损坏时捕获异常后**跳过**审核模板初始化。新库模板数为 0，导入媒体无法进入待审、v2 读模型把它当作「没有待审目标」，审核与交付整条链被阻断且在页面上伪装成「没有待审内容」。

**新行为：** 初始化拆分为三个独立记录、有顺序的步骤——`database_schema`（必需）→ `review_templates`（必需，且**先于**任何清单工作执行）→ `model_manifest`（可选，独立 `try/except`）。清单失败只降级自身并记 `api.startup_step_degraded step=model_manifest required=false`，不再影响模板；必需步骤失败进入显式 readiness 状态而不是假装空审核列表。`ReviewService.ensure_templates` 改为单条 `INSERT ... ON CONFLICT(code, version_no) DO NOTHING`（约束 `uq_review_templates_version`），并发启动不再竞态，且不做任何 UPDATE，保持已发布模板 append-only。

**修改文件：** `apps/api/local_drama/main.py`、`apps/api/local_drama/application/reviews.py`、`apps/api/local_drama/infrastructure/database/readiness.py`（新增）。

**兼容性：** 已发布审核模板版本不被覆盖；`app.state.manifest_sync` / `llm_sync` 语义不变；`HealthCheck` 新增两个可选字段，旧响应形状不变。

**回归测试：** `apps/api/tests/test_startup_required_initialization.py`（8 项）、`apps/api/tests/test_health_readiness_schema.py`（9 项）——均通过。清单缺失/损坏/正常三种情况经真实 lifespan 与真实 `GET /api/v1/review-templates` 都列出内置 6 个模板；重复启动不增生、不覆盖历史版本。

**残留限制：** 测试中的「全新数据库」指已正确迁移、仅删除 6 条内置模板行的库；真正未迁移的空库按设计无法播种模板，现已显式 `NOT_READY`（由 HTTP-04 覆盖）。未覆盖「真实媒体导入 → 待审数 → 批准/撤回 → 交付」的完整端到端场景。

### HTTP-04（P2）不完整数据库就绪探针仍称健康

**旧行为：** 仅有 `alembic_version` 表与一条旧记录的库，`GET /health/ready` 返回 200 `HEALTHY`、`database=ok`，随后 `GET /projects` 返回 500。

**新行为：** 期望 head 由仓库真实 Alembic 迁移链派生（不硬编码）；同时校验核心表/列能力。`ready` 对不可服务业务返回 **503 NOT_READY**，并带结构化 `reasons` 与 `initialization` 字段；`live` 仍只表示进程存活。判定在 lifespan 计算一次并缓存复用，不在每个健康请求上做 `PRAGMA integrity_check`。

**修改文件：** `apps/api/local_drama/api/routes/health.py`、`apps/api/local_drama/infrastructure/database/readiness.py`（新增）、`apps/api/local_drama/main.py`。

**兼容性：** 完整库行为不变（仍 `HEALTHY`）；`/health/live` 未改动。

**验收：** 无库 / 仅版本表 / 旧 head / 多 head / 完整当前库五组中只有最后一组 `HEALTHY` 且 `GET /projects` 可读（已由新增测试覆盖）。

### 测试自足性（BKT-02 相关）

普通测试不再隐式读取开发机模型清单；传输 UAT 的 `LOCAL_DRAMA_COMFY_ACCESS` 在异常路径也原样恢复（含「原本不存在」的情形）。测试按 canonical capability 精确选 Profile，不再取 `list_profiles()[0]`。

**修改文件：** `scripts/local_adapter_transport_windows_uat.py`、测试 fixture 与 helper（含 `apps/api/tests/test_generation_variants.py`）。

### LAN 机器配置前提修正（报告末节裁定）

**旧行为：** 两个环境读取测试只设置 LAN 模式/host/根目录，没有显式可信 LAN 接受声明，因此会去读开发机上恰好存在的 `config.json`；其中一项还硬编码 Windows 反斜杠拼工具目录。

**新行为：** 新增独立测试，显式临时 `LOCAL_DRAMA_INSTANCE_ROOT` + `LOCAL_DRAMA_CONFIG` 指向最小 schema v3 JSON（含 `network.trusted_lan_unauthenticated=true`），工具目录用当前平台 `os.pathsep` 拼接；另加一组「未显式接受必须仍被拒绝」的对照，确认 `LOCAL_DRAMA_TRUSTED_LAN_UNAUTHENTICATED` 环境键不会被误当作可用开关。

**修改文件：** `apps/api/tests/test_server_deployment.py`。既有两条历史失败测试未删除、未放宽断言。

---

## 2. Phase 1：用户输入与文稿导入

（见第 3、4 节及后续分节；解析器版本化与文档格式矩阵由 Phase 1 工作流完成。）

---

## 3. Phase 2：全书覆盖、续接、编辑与归属

### NP01（P1）首章前正文被丢弃，覆盖率却报完整

**旧行为：** 原稿为「序幕 + 第一章 + 正文」时，分集循环从第一个章节标题开始，序幕正文不进入任何单元；而 `_source_coverage` 仅比较「完成单元数 == 计划单元数」，所以 `covered_paragraph_count=2`、`authorized_paragraph_count=3` 仍报 `FULL`、`resume=null`、质量 `READY`、应用预览 `can_apply=true`。序章、引子、首章前无标题正文会从整剧理解中消失，用户却看到完整成功信号。

**新行为：**
1. 授权范围开头到首章之间的非空正文成为独立的 `序幕与开篇` 单元（无文字时不产生空单元），必进模型输入。
2. `_source_coverage` 改为**区间集合差**证明：以已完成单元的段落区间并集对授权区间求补集，任何未覆盖段落都进入 `coverage_gaps`（`AUTHORIZED_RANGE_NOT_COVERED`）；`FULL` 必须同时满足集合差为空、无截断窗口、无未完成单元。
3. 新增 `coverage_complete`、`coverage_gaps`、`completed_window_count`、`total_window_count` 字段，schema 升级为 `pipeline.source-coverage.v2`（v1 仍可反序列化）。
4. 质量规则沿用既有 `SOURCE_COVERAGE_COMPLETE`（WARNING），但因为 `FULL` 现在是真证明，它不再可能对缺口报通过。

**修改文件：** `apps/api/local_drama/application/pipeline_orchestrator.py`、`apps/web/src/features/pipeline/pipelineClient.ts`（类型兼容 v1/v2）。

**回归测试（`apps/api/tests/test_pipeline_orchestrator.py`，均通过）：**
- `test_episode_specs_include_the_prologue_before_the_first_chapter`：序幕文本确实进入单元，且 `source_start_paragraph==1`。
- `test_source_coverage_never_reports_full_while_a_paragraph_is_uncovered`：丢掉序幕单元后必须 `PARTIAL` 且 `coverage_gaps` 精确指出第 1 段；授权范围尾部多出未覆盖段落时同样显式成缺口。

### NP02（P1）长篇只完成首批，续接位置没有执行通道

**旧行为：** 61 章原稿只处理前 60 章；单章超 24,000 字符在提示词层被静默截断，尾部情节永不进入模型；`:resume` 返回 422 `PIPELINE_RESUME_UNSUPPORTED`，`:retry` 对 SUCCEEDED 返回 409 `PIPELINE_STATE_INVALID`；前端只显示续接信息，没有可执行继续入口。

**新行为：**
1. **有界输入窗口**：单元内长正文按字符切成 `<= 24000` 的有界窗口，窗口文本**逐字符拼回等于原文**（段落分隔符用显式哨兵承载，窗口边界落在段落之间也不会丢字符）。窗口携带 `unit_number`/`window_index`/`window_count`/`source_character_count`/`source_input_sha256`，因此同一章可以被多窗口覆盖而不再截断。
2. **持久游标**：运行快照写 `analysis_cursor`（`completed_window_count`/`next_window_index`/`total_window_count`/`source_sha256`），并通过 run 响应暴露。
3. **真实 `continue-analysis` 命令**：`POST /api/v1/projects/{project_id}/pipeline/{run_id}:continue-analysis`，输入 `expected_revision` + `expected_source_sha256` + 可选 `expected_next_window_index`；服务端按**自己的游标**切片，只处理未完成窗口，最多再 60 个窗口，重复命令幂等，游标过期返回 409 `PIPELINE_CURSOR_STALE` 并回报服务端游标，已全部完成返回 409 `PIPELINE_COVERAGE_COMPLETE`，运行中重复提交返回 409 `PIPELINE_ALREADY_RUNNING`。
4. **不覆盖已制作分集**：续接批次只追加自己规划的单元，第一批已规划的单元原样保留（`merged_episodes = saved[:prior] + new`），不会从 EP01 重写名称或记忆。
5. 前端客户端新增 `continuePipelineAnalysis()` 与 `analysis_cursor` 类型（`has_more_windows` 是唯一可信的「还有剩余原稿」信号）。

**修改文件：** `apps/api/local_drama/application/pipeline_orchestrator.py`、`apps/api/local_drama/api/routes/pipeline.py`、`apps/api/local_drama/api/schemas/pipeline.py`、`apps/api/local_drama/application/errors.py`（新增两个 409 错误码映射）、`apps/web/src/features/pipeline/pipelineClient.ts`。

**回归测试（均通过）：**
- `test_long_unit_is_split_into_bounded_windows_covering_the_tail`：长章/长段都切成多窗口，每窗口 `<=24000` 且 `source_character_count` 与文本一致，同属一个 `unit_number`，`window_index` 连续，**尾部唯一事件出现在最后一个窗口**，窗口拼回等于原文；覆盖率 `FULL`、`coverage_gaps==[]`、无 `EPISODE_INPUT_CHARACTER_LIMIT`。
- `test_bounded_windows_preserve_body_text_when_boundary_falls_between_paragraphs`：窗口边界落在段落之间时段落分隔符不丢（这是修复过程中自查发现的真实缺陷：初版实现会在该情形丢 2 个字符）。
- `test_continue_analysis_finishes_the_remaining_windows_of_a_long_novel`：61 章端到端。第一批 60 单元 `SUCCEEDED` + `PARTIAL` + `has_more_windows=true`，第 61 章确实不在第一批；旧 `:resume` 仍 422；游标过期 409 且回报服务端游标；正确游标 200 后运行中二次提交 409；第二批完成后 `FULL`、`coverage_complete=true`、覆盖区间恰为 1–122、第一批标题前缀原样保留、`has_more_windows=false`；再续接 409 `PIPELINE_COVERAGE_COMPLETE`。

**兼容性：** 旧草案 v1 覆盖率快照仍可读；`_source_coverage` 新增参数全部有默认值，旧调用点语义不变（`tests/test_pipeline_orchestrator.py` 既有 20 项断言全部保留并通过，仅把两条「断言截断发生」的诊断用例按任务书要求改写为「正确行为必须成立」）。

**残留限制：** 未在真实模型上跑 120/121 章与 24,001/48,000 字符单章（本机无权重/GPU）；上述为真实编排、真实 SQLite、真实 HTTP 与显式 TEST_ONLY 生成替身的组合，属结构逻辑证据。

### NP03（P1，存活 API）编辑标题删除未修改的导演信息

**旧行为：** `_validated_replacement` 重建只含 `title/summary/characters/shots(visual,action,dialogue,duration)` 的新 dict，因此仅改标题也会丢掉场次 `location/time/atmosphere/lighting/props` 与每个镜头的 `shot_type/camera/composition/lighting/sound/emotion/continuity` 等；正式输出退化为 `OTHER`/`STATIC`/`CENTER`/`sound_plan=null`，青灯右手的连续性变成通用句。接口 schema 也没表达这些字段，UI 无法在保存时补回。

**新行为：**
1. 以 PATCH 语义深拷贝原场次，**只覆盖调用方明确提交的字段**；未提交字段保持原值，未知的受信历史字段原样透传。
2. 请求 schema 增加可选导演字段（`shot_type/camera/composition/lighting/sound/emotion/emotion_intensity/continuity/creative_intent/camera_direction/camera_intensity/camera_curve/facial_action/eye_line/blocking_summary/transition_plan` 与场次级 `location/time/atmosphere/lighting/props/purpose/continuity`），路由改用 `model_dump(..., exclude_unset=True)`，因此「未提交」与「显式清空」可区分。
3. `summary`/`characters` 由必填改为可选（未提交即保持原值），与 PATCH 语义一致。
4. 不允许覆盖的来源/系统字段仍由服务端权威掌握（镜头编号与顺序仍锁定，编辑白名单以外的键不会写入）。

**修改文件：** `apps/api/local_drama/application/breakdown_revisions.py`、`apps/api/local_drama/api/schemas/g3.py`、`apps/api/local_drama/api/routes/imports.py`。

**回归测试（`apps/api/tests/test_breakdown_scene_revision_preservation.py`，3 项通过）：**
- 只改标题：场次 9 个未提交字段与两个镜头全部字段逐一 deep-equal。
- 分别只改镜头画面与时长：仅对应字段变化，运镜/构图/连续性/音效保持；显式提交运镜与构图时仍被采纳。
- 真实 HTTP 修订 → 真实 HTTP 应用 → 查询正式 `shot_revisions.fields_json`：`camera_plan.movement == PUSH_IN`、`composition.preset == RIGHT_THIRD`、`sound_plan.description == 水声与虫鸣`、`lighting`/`continuity` 与原稿一致，不再退化为默认值。

**兼容性：** 既有旧式 PUT（客户端提交全部字段）行为不变；`tests/test_breakdown_apply.py`、`test_breakdown_contracts.py`、`test_ai_breakdown_drafts.py` 共 58 项回归通过。修订历史仍不可变；stale revision 与已应用场次仍被拒绝。

---

## 4. 修复过程中额外发现并修复的真实缺陷

这些不在报告的 58 条登记内，是修复过程中由新回归测试暴露出来的，一并记录。

### 4.1 TTS 情绪默认值大小写不一致导致整集批量 TTS 全部失败

**发现方式：** BKT-08/HTTP-04 的修复者在回归中报告 `test_automation_worker_tts_batch_submits_and_reports_counts` 失败（`submitted 0 / failed 2`），并证明该失败不是其文件引入。追查后定位为同期 MED-04 能力门禁与产品默认值不一致。

**根因：** 产品规范化情绪 token 是**大写** `NEUTRAL`（`api/schemas/dialogue.py` 的 `EpisodeTTSBatchRequest.emotion` 默认值与 `DialogueService.submit_episode_tts_batch` 的默认值都是 `"NEUTRAL"`），而 provider 能力声明的默认值写成小写 `"neutral"`；`requires_rejection()` 用 `value != capability.default` 比较，于是「就是默认值」也被判为不受支持的请求。批量接口每条台词都失败，整集 TTS 一条也提交不出去。

**新行为：** 默认值比较对字符串做 casefold 归一（数字等非字符串仍按原等值语义，避免 `1.0` 被强制成 `"1.0"`）；两个 provider 声明的默认值改回产品规范的大写 `NEUTRAL`；拒绝门禁的**能力仍完整保留**——对真正声明为 `UNSUPPORTED` 的参数与非声明参数，仍拒绝非默认值。

**修改文件：** `apps/api/local_drama/application/worker_handlers/tts_job.py`。

**回归测试：** `apps/api/tests/test_tts_parameter_capabilities.py`（12 项通过）覆盖：情绪的声明模式为 `METADATA_ONLY` 且不出现在 `applied_parameters()`、`applied_parameters()` 恰为 `["speech_rate"]`、`NEUTRAL/neutral/ANGRY/警觉/激动` 一律不触发拒绝、把声明临时改为 `UNSUPPORTED` 时非默认值仍被拒绝而默认值通过、未声明参数一律拒绝、`speech_rate` 数字默认值不被字符串化、**声明的情绪默认值与 `EpisodeTTSBatchRequest` 的产品默认值必须一致**（防止再次漂移）。`test_automation_whole_drama.py::test_automation_worker_tts_batch_submits_and_reports_counts` 恢复为 `submitted 2 / failed 0 / job_count 2`。

**关于情绪语义的最终裁定（重要，避免误读为「静默忽略」）：** MED-04 的修复者最初把情绪声明为 `UNSUPPORTED` 并拒绝非默认值，随后发现这会打挂既有的 `test_character_voice_batch.py::test_episode_tts_batch_validation_and_idempotency`（该用例对 SAPI provider 提交 `emotion="警觉"` 并期望成功），于是把情绪改判为 `METADATA_ONLY` 并自证：两种不同情绪的运行时请求与输出音频完全相同。

本次独立复核了「产品是否真的在使用情绪」这一问题，结论支持 `METADATA_ONLY`：

1. **UI 从未把 TTS 情绪当作可调项。** 唯一真实提交路径 `features/director-v2/DirectorSoundInspector.tsx:188` 把 `emotion` **硬编码为 `"neutral"`**，界面上只暴露语速控件。因此「界面提供 0.75–2× 下拉却无效果」这一 MED-04 原始症状，指的是**语速**；语速已真正生效（VoxCPM2 走 `atempo` 后处理、SAPI 走原生 rate）。
2. 存在一处**非 TTS** 的 `EmotionPicker`（`DirectorPerformanceControls.tsx`），它编辑的是 DirectorIntent 的表演情绪（镜头语义），与本条 TTS 参数是不同概念，不受影响。
3. 因此把情绪判为 `UNSUPPORTED` 并拒绝，会在一个界面根本不提供的参数上打断合法的批量提交，且不会改善任何用户可见行为。

**残留：** 若产品日后希望「情绪一旦被提交就必须真正影响音频」，正确的实现是让 provider 原生支持情绪（或明确的后处理链），届时把该参数从 `METADATA_ONLY` 提升为 `NATIVE`/`POST_PROCESSING` 即可；当前声明已经如实说明情绪不影响合成语音，不存在「接受却假装已生效」的表述。

### 4.2 有界窗口在段落边界处丢字符（NP02 实现自查）

见 NP02 回归测试第 2 项。首版实现用「前件缩进」方式承载段落分隔符，当窗口恰好填满在段落之间时会丢 2 个字符；改为显式哨兵件后，窗口拼回逐字符等于原文，并由测试锁定。

### 4.3 续接批次的未处理区间计算错误（NP02 实现自查）

首版 `_source_coverage` 假设批次从窗口 0 开始，因此在续接批次里会把已完成的 60 个窗口again报成 `BATCH_EPISODE_LIMIT` 未处理，并使 `FULL` 永远无法达成。改为显式传入 `selected_window_offset`，前序窗口计入已完成、不再报未处理。由 `test_continue_analysis_finishes_the_remaining_windows_of_a_long_novel` 锁定。

### 4.4 QUALITY 磁盘门禁测试夹具选错能力（报告已裁定，本次落实）

**旧行为：** 共享 helper 用 `list_profiles()[0]` 把 manifest 排序当作能力选择，可能选中 `VIDEO_FIRST_LAST_FRAME`，却写入 `I2V_VIDEO` 绑定；磁盘子项因此以 `estimate_source=UNKNOWN` 通过，测试实际上绕过了要验证的磁盘预算。

**新行为：** helper 改为按 canonical capability 精确选择（`_published_profile(..., capability="VIDEO_I2V")`），没有匹配能力时明确报错并列出可用能力；测试额外断言被引用版本自身声明 `VIDEO_I2V`、`PROFILE_CAPABILITY_MISSING` 子项为 `PASS`、`estimate_source == FROZEN_PROFILE_RESOURCE_POLICY`，确保 40,000 > 20,000 确实由真实估算得出而不是「未知即放行」。

**修改文件：** `apps/api/tests/test_generation_variants.py`、`apps/api/tests/test_capacity_disk_gates.py`。回归：`test_capacity_disk_gates.py` 4 项通过、`test_generation_variants.py` 44 项通过。

### 4.5 R-01：可信 LAN 模式的 DNS rebinding 防线（经产品决策后实施）

**背景：** 报告 R-01 记录，显式配置 `LAN_SERVICE` + `trusted_lan_unauthenticated=true` 时，写请求的「同源」判定只比较 `Origin` 与客户端自己发来的 `Host`。攻击者控制的域名只要解析到本机，就能自行凑出这一对，从而越过同源判定（DNS rebinding）。报告同时说明这属于**带配置前提**的边界观察，未完成真实浏览器利用验证，且「优先级应结合实际是否启用 LAN 决定」。

**决策：** 已就该产品取舍向用户确认，选择「加白名单：只有显式配置的 host/Host 才获信任，任意域名不再自动同源」。

**新行为：**
1. 新增 `Settings.trusted_hosts`（可由机器配置 `network.trusted_hosts` 提供）。为 `None` 时从 `allowed_origins` 派生信任主机，并额外信任**回环主机**（`localhost` 与回环 IP，Vite 端口漂移仍可写，且回环地址无法由 DNS rebinding 产生）与**IP 字面量主机**（保留文档化的 LAN 直连 IP 工作流）；显式设置时该列表即权威，连内置规则一并关闭，空列表表示只信任已登记的 origin。
2. 写请求的同源判定改为「`Origin` 与 `Host` 必须一致」**且**「`Host` 必须可信」。未登记的**域名** Host 一律拒绝，这正是 rebinding 必须使用的形式。
3. 两个既有行为被显式保留并有测试锁定：Vite 前端端口漂移时的回环 origin 仍可写；LAN 下用服务器自身 IP 访问的同源写仍可写。

**修改文件：** `apps/api/local_drama/middleware.py`、`apps/api/local_drama/config.py`、`apps/api/local_drama/bootstrap/config_loader.py`、`apps/api/local_drama/main.py`。

**回归测试（`apps/api/tests/test_server_deployment.py`，共 38 项通过）：**
- `test_lan_dns_rebinding_host_is_rejected_without_explicit_registration`：拿到 GET 下发的实例 token 后，用未登记域名 Host + 自洽 Origin 发写请求 → 403 `ORIGIN_NOT_ALLOWED`（修复前会通过到路由层）。
- `test_lan_ip_literal_host_keeps_working_and_registered_host_is_trusted`：IP 字面量 Host 与显式登记 origin 均通过 origin/token 边界。
- `test_explicit_trusted_hosts_override_replaces_origin_derived_hosts`：显式 `trusted_hosts=()` 且清空 `allowed_origins` 时，`localhost` 域名写请求被拒，证明「显式列表权威」而非静默回退。
- 既有 35 项（含跨主机/端口不匹配/仿冒域名拒绝、端口漂移可写、CORS 预检）全部保持通过。

**残留限制：** 本机无真实浏览器，未执行报告所述的 DNS rebinding 端到端利用验证；本次交付的是防线实现与可回归断言，不是「已证明无法被利用」。真实 LAN 部署（含自建域名访问）需要操作员按新配置登记 `trusted_hosts` 或 `allowed_origins`。

### 4.6 新代码层违反仓库自身架构门禁（本次修复过程中暴露）

**发现方式：** 广域回归时 `tests/test_refactor_architecture_boundaries.py::test_new_application_dirs_do_not_import_legacy_application_monoliths` 失败。

**根因：** `application/queries/generation_estimates.py` 为把 GPU 归类交给「规范调度权威」，直接 `from local_drama.application.job_resources import ...`。该测试硬性要求 `application/commands|queries|orchestration` 只能依赖 ports，不得依赖遗留应用单体模块。

**新行为：** 把 GPU 归类下沉为端口提供的事实：`SqliteGenerationEstimateRepository` 与 `SqliteGenerationPreferenceRepository` 在返回历史尝试时附带 `gpu_class`（先取调度器已持久化的 lease `resource_key`，再回退 channel）；查询层只读取该标注，必要时对仅含 `resource_key`/`channel` 的原始行做等价回退。**没有**把 import 藏进函数体（该门禁遍历整棵 AST，藏 import 属于规避而非修复）。

**修改文件：** `apps/api/local_drama/application/queries/generation_estimates.py`、`apps/api/local_drama/infrastructure/database/generation_estimate_repository.py`、`apps/api/local_drama/infrastructure/database/generation_preference_repository.py`。

**回归：** `test_refactor_architecture_boundaries.py` 3 项通过；`test_generation_estimates.py`、`test_generation_preference_recommendations.py`、`test_capacity_disk_gates.py` 共 13 项通过（含在纯净 HEAD worktree 中先确认这些用例原本为绿，确保本次不是把失败改写成通过）。

### 4.7 迁移合同未随新迁移递增（Phase 0 门禁暴露）

**旧行为：** Phase 1 新增迁移 `0101_versioned_source_parsing` 后，`docs/release/migration-contract.json` 仍声明 head 为 `0100_production_session_waiting_user`；Phase 0 新增的可移植 G0 门禁因此正确报红（head 不匹配 + 新迁移不可达）。

**新行为：** 合同 head 递增为 `0101_versioned_source_parsing`，并登记该迁移的事实（`parser_version`/`structure_version`、新解析世代拥有独立 extracted 文本且不重写既有 offset、导入会话提交正文范围用于范围感知幂等、退役零段落 `PREVIEW_READY` 会话）。

**修改文件：** `docs/release/migration-contract.json`。未放松门禁本身去凑绿。

### 4.8 声明的 Windows 时区依赖从未进入锁文件（锁门禁自行暴露）

**发现方式：** 本轮新增的 `scripts/check_dependency_locks.py` 门禁在收尾时直接报出：`pyproject.toml` 声明了 `tzdata>=2024.1; platform_system == 'Windows'`，但开发锁与运行时锁都没有它。

**影响：** 与 BKT-01 同类但更隐蔽——带 platform marker 的依赖最容易被漏掉。若某处按 DST 时区做时间计算，纯净 Windows 安装会因缺少 `tzdata` 而无法解析 `zoneinfo.ZoneInfo("Asia/Shanghai")`。

**新行为：** 把实测版本 `tzdata==2026.4`（`pip show` 确认无传递依赖）写入两份锁文件并保持既有字母序。这正是 BKT-01 修复所要求的「依赖变更门禁检查所有 pyproject 必需依赖都存在于 runtime lock」在真实场景下第一次生效。

**修改文件：** `apps/api/requirements-runtime.lock`、`apps/api/requirements.lock`。

**回归：** `python scripts/check_dependency_locks.py` → `dependency_locks=PASS declared=8 locks=2`（修复前 exit 1 并逐条列出缺失依赖）。

### 4.9 收尾时的 lint 与既有断言时效性清理

广域检查（`ruff check local_drama tests`，工作目录 `apps/api`，即项目自身配置生效的调用方式）报出 6 条诊断，全部来自本轮新增文件，逐条按根因修复而非忽略：

| 位置 | 诊断 | 处理 |
|---|---|---|
| `application/worker.py:65` | F401 未使用的 `ExplainerRepository` 导入 | 删除该导入（实际使用的是同模块的 `ExplainerRepositoryTransaction`） |
| `tests/test_explainer_factory.py:939` | B007 循环变量未使用 | 改为遍历 `report.values()`，语义不变 |
| `tests/test_explainer_factory.py:992` | B017 盲断言 `Exception` | 收紧为 `pytest.raises(ValidationError)`：`POLICY_ACCEPTED` 被刻意排除在 `Literal` 之外，契约本身就会拒绝，收紧后断言与真实契约一致 |
| `tests/test_qwen_image21_workflow_export.py:19,21` | E402 + I001（`sys.path` 插入后再导入） | 改为用 `importlib.util.spec_from_file_location` 按路径加载脚本，既消除诊断，也不再依赖其他测试对 `sys.path` 的修改顺序 |

另有一处**既有测试的过时常量**：`tests/test_document_import_commit.py:238` 硬编码 `source_structure_version == 2`，而 NP09 的结构语法版本已按任务书要求提升为 3。改为从 `local_drama.domain.source_text.SOURCE_STRUCTURE_VERSION` 读取，而不是把 2 改成 3——这样以后再提升结构版本不会又留下一个过期字面量。

同时确认 `tests/test_release_migration_contract.py` 的 4 项全部通过（其中 120/129/130/165/173 行的 `0100` 是**故意**用来验证审计工具漂移检测的夹具值，不是过期常量，未改动）。

**回归：** `ruff check local_drama tests` → `All checks passed!`；`pytest tests/test_qwen_image21_workflow_export.py tests/test_explainer_factory.py` → 40 passed；`pytest tests/test_document_import_commit.py` → 8 passed；`pytest tests/test_release_migration_contract.py` → 4 passed。

---

## 5. 接口覆盖清单独立复核

对《接口覆盖清单》做了独立的机器核对（解析文档表格 × 实时 OpenAPI × 源码定位）。

**结构核对结果（全部符合）：**

| 核对项 | 结果 |
|---|---|
| 文档解析出的行数 | **631**（与文档自述一致） |
| 方法分布 | GET 234 / POST 361 / PUT 18 / PATCH 6 / DELETE 9 / HEAD 3 —— 与文档自述**完全一致** |
| 文档有、实时 OpenAPI 无（虚报） | **0** |
| operationId 不一致 | **0** |
| 源码定位可解析且落在路由/处理器附近 | 616 / 631（97.6%） |

**结论：** 覆盖清单的接口集合与 operationId 是可信的，没有虚报项。差异只有 4 条，全部是**本轮修复新增**的接口（不在基线提交中）：
1. `GET /api/v1/delivery-packages/{package_id}/archive:download`（MED-08 交付整包下载）
2. `GET /api/v1/delivery-packages/{package_id}/files/{file_id}/download`（MED-08 单文件下载）
3. `POST /api/v1/import-sessions/{session_id}:select-range`（NP08 异范围提交需显式新建分析选择）
4. `POST /api/v1/projects/{project_id}/pipeline/{run_id}:continue-analysis`（NP02 续接命令）

这些是**修复产物而非缺陷**，将在收尾时补入覆盖清单。

**行号精度说明：** 15 条（2.4%）的源码行号在修复过程中因相邻代码变动而漂移（最大约 28 行，集中在 `workflows.py`、`projects.py`、`jobs.py`、`timeline.py`、`health.py`）。任务书第 7 节已提示「不要盲目按旧行号覆盖新代码」，实际按此执行（逐条先读代码确认再改）。该现象属元数据时效性，不是产品缺陷。

**为什么会有漂移（已核实，非文档错误）：** 以 `health.py` 为例，文档写 `healthLive` 在 `:51`；基线提交 HEAD 中该装饰器实际在 `:50`（差 1 行，属文档自身的定位粒度），而当前工作树已因本轮修复变为 `:55`（该文件新增 78 行、删除 21 行）。也就是说漂移来自**修复本身**与轻度的定位粒度差，不是清单伪造或源码错位。收尾时按实际路由重新锚定行号。

**清单与源码一致性结论：** 631 条的「方法 + 路径 + operationId」三元组全部与运行中的 OpenAPI 一致，`operationId` 零不一致、零虚报；可作为可靠的接口库存继续使用。

**收尾处理：** 新增 `scripts/regenerate_api_coverage_list.py`（AST 扫描路由装饰器 + 实时 OpenAPI 对比），并提供 `--check` 供门禁复验。已据此把清单中 620 条漂移的源码行号**重新锚定到实际装饰器行**，并把本轮新增的 41 个操作补入文末独立小节，其状态明确标注为「本轮新增未巡检」——**没有**给它们伪造成通过。收尾校验结果：

```
documented_rows=672 live_operations=672
documented_but_absent=0
absent_from_document=0
operation_id_mismatch=0
stale_source_lines=0
```

新增的 41 个操作来自本轮修复（`continue-analysis`、交付整包/单文件下载、`select-range`）以及并行落地的其他功能（解释器工厂 `api/v2/explainer-*`，属本报告 58 条之外的功能）。清单头部已如实写明「原始审计 631 条；当前运行时 672 条」。

**未做的事（明确保留）：** 新增操作没有经过主 HTTP 巡检，因此没有状态码结论；清单不把「存在」当作「已验证」。

---

## 6. 实际执行记录（命令与真实结果）

> 本节在全部并行修复收敛后补齐最终一轮的完整结果；以下为各条目所属修复批次内已实际执行并观察到的结果。

| 命令 | 真实结果 |
|---|---|
| `apps/api/tests/test_pipeline_orchestrator.py` | **18 passed**（含 3 项新增 NP01/NP02 用例；2 项原「断言截断发生」的诊断用例已按任务书改写为正确行为断言） |
| `apps/api/tests/test_breakdown_scene_revision_preservation.py` | **3 passed**（新增 NP03） |
| `apps/api/tests/test_breakdown_apply.py` + `test_breakdown_contracts.py` + `test_ai_breakdown_drafts.py` | **58 passed**（NP03 无回归） |
| `apps/api/tests/test_tts_parameter_capabilities.py` | **10 passed**（新增 4.1） |
| `test_automation_whole_drama.py::test_automation_worker_tts_batch_submits_and_reports_counts` | **passed**（修复 4.1 前为 `submitted 0 / failed 2`） |
| `apps/api/tests/test_capacity_disk_gates.py` | **4 passed**（4.4） |
| `apps/api/tests/test_generation_variants.py` | **44 passed**（4.4） |
| `apps/api/tests/test_server_deployment.py`（新增 2 项 LAN hermetic 用例 + 3 项 R-01 用例） | **38 passed** |
| `apps/api/tests/test_openapi_release_contract.py`（重生成快照后） | **2 passed** |
| `apps/api/tests/test_release_migration_contract.py` | **4 passed** |
| `apps/api/tests/test_refactor_architecture_boundaries.py` | **3 passed**（修复违规导入后） |
| `apps/api/tests/test_document_import_commit.py` | **8 passed**（过时常量改为读常量后） |
| `apps/api/tests/test_qwen_image21_workflow_export.py` + `test_explainer_factory.py` | **40 passed**（lint 按根因修复后） |
| `.venv\Scripts\python.exe -m ruff check <本文件涉及的全部改动文件>` | All checks passed |
| `cd apps/api; ruff check local_drama tests`（项目自身配置生效的调用方式） | **All checks passed!** |
| `python scripts/g0_validate.py`（可移植 G0 门禁） | **g0_status=PASS**（6/6 检查通过） |
| `python scripts/check_dependency_locks.py` | **dependency_locks=PASS declared=8 locks=2** |
| `python scripts/generate_client.py --check` | **generated_artifacts=PASS checked=2** |
| `python scripts/regenerate_api_coverage_list.py --check` | **零差异**（672/672，见第 5 节） |
| `pnpm --dir apps/web build` | **✓ built**，bundle budget PASS（54 chunks，最大 295.2 KiB） |
| `pnpm --dir apps/web test` | **162 files / 735 tests passed** |
| `mypy local_drama/application/pipeline_orchestrator.py` | 本文件 0 错误（用 HEAD 原始文件对照确认基线自身已有 9 条，本次未新增；其中 1 条本次引入的 `int()` 收窄错误已修复） |

### 6.1 完整安全测试集（`-m "not comfyui and not video_upscale_gpu"`）

在全部修复落地后执行一次完整运行（`pytest tests -m "not comfyui and not video_upscale_gpu" -p no:randomly --junitxml=...`），结果：

```
tests=2047 failures=2 errors=0 skipped=2
```

两条失败均与本轮改动直接相关，且**都已按根因修复**（不是改断言凑绿）：

1. `test_architecture_debt_manifest.py::test_legacy_architecture_debt_does_not_grow`
   —— 我给 NP02 新增的 `continuePipelineAnalysis` 路由没有声明 `response_model`，触发了仓库「新增路由不得新增架构债」的门禁。**修法：** 在 `api/schemas/pipeline.py` 新增 `PipelineRunResponse` 信封并挂到该路由（`run: dict[str, object]`，刻意不收紧字段，避免静默丢弃前端已依赖的 `draft`/`analysis_cursor` 等字段），而不是把新路由登记进债务白名单。
2. `test_migration.py::test_g2_migration_is_real_wal_schema`
   —— 该用例硬编码 `version == "0100_production_session_waiting_user"`，而本轮新增了 `0101`（解析版本化）与 `0102`（并行落地的解释器工厂）。**修法：** 改为从 `docs/release/migration-contract.json` 的 `expected_heads` 读取（该合同本身另有测试与 alembic 图比对），既消除硬编码，又保留「迁移库 head 必须是声明 head」的语义。

修复后复跑这 4 个受影响模块（含 `test_pipeline_orchestrator.py`、`test_openapi_release_contract.py`）：**25 passed**。

**最终完整运行（全部修复落地、快照重生成之后）：**

```
$ cd apps/api
$ ..\..\.venv\Scripts\python.exe -m pytest tests -m "not comfyui and not video_upscale_gpu" -p no:randomly --junitxml=...
tests=2047  passed=2045  failures=0  errors=0  skipped=2
[exit code: 0]
```

2 项 skip 为平台前提（POSIX 权限契约 / Linux 真实 `/proc` 僵尸进程），与本轮改动无关且已在第 7 节列为未验证项。4 个 `comfyui`/`video_upscale_gpu` 标记用例按 BKT-07 的默认安全口径被排除，需显式 live 命令与真实硬件。

> 说明：这一轮的 2047 是当前工作树的用例库存（基线为 1583 常规 + 4 Comfy 标记），增长来自本轮为 40 余条登记问题补充的回归用例与并行落地的解释器工厂功能，不改变「数量不等于覆盖率」的结论。

---

## 7. 未验证 / 残留限制（如实保留）

以下项目**没有**被本次修复勾成通过，需在真实目标环境补验（对应任务书 Phase 7）：

1. **真实浏览器：** 本次无可用浏览器审批，0 张真实截图。FE-05/FE-06 的焦点与嵌套 Esc、FE-08 的真实 `video.currentTime` seek、FE-14 的模态键盘行为、各视口/125%–150% 缩放与中文 IME 均**未在真实浏览器验证**；jsdom 断言不能替代像素与真实播放器结论。
2. **真实模型/GPU：** 本机无权重、无 GPU、无 ComfyUI/VoxCPM2/LatentSync 服务。T2V/I2V/首尾帧/Ref2V、真实画质与角色一致性、中文语音情绪听感、口型、真实超分均未验收。
3. **Windows 发行：** 未运行 Windows 安装器、桌面启动器、SAPI、服务注册、UAC、防火墙与离线回滚；`pypdf` 已进入锁但**未在纯净 Windows 离线环境**实际安装验证。
4. **LAN 安全模式 Host 防线的浏览器侧利用**（报告 R-01）未执行真实 DNS rebinding 验证；本次只落实了「测试前提显式化」与「未接受必须拒绝」的可回归断言。
5. **长章/多章的真实模型续接**：NP02 的续接语义已由真实编排 + 真实 SQLite + 显式 TEST_ONLY 生成替身证明，但 120/121 章与 48,000 字符单章未在真实模型上跑完。
6. **BKT-10 的 Linux 僵尸进程判定**：本机为 Windows，POSIX 分支只能经可注入的进程状态接缝做单元级验证，真实 Linux `/proc` 行为未在本机执行。
