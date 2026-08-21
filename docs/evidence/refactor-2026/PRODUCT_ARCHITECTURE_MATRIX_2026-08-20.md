# Product / Architecture 逐节代码证据矩阵（2026-08-20）

审计范围：`docs/xinjihua/01_竞品研究与产品_UI_UX_总设计.md` §§27–60、63–64、P0–P2，以及 `docs/xinjihua/02_架构与前后端重构规格.md` §§0、5–86。本次只读取 Markdown、源码和测试文本，未读取任何图片或原始媒体。

状态定义：

- **代码闭环**：存在真实实现文件与自动化测试；不代表真实 GPU、媒体观感或人工 UAT 已通过。
- **部分闭环**：已有主体实现，但原文明确要求仍有可本地开发的代码缺口。
- **外部 UAT**：必须依赖真实项目、Windows/浏览器、GPU/Comfy、人工视觉/音画或恢复演练。
- **按 gate 延后**：代码保留兼容路径，只有 release/UAT gate 满足后才允许退役。

## 仍可本地开发的代码缺口

1. Asset Bible 的图片反推 DNA 尚无可用视觉理解能力与完整 command/UI；表情九宫格、近景细节与参考版本 A/B 比较已闭环。
2. 不能证明“每一个”异步组件均覆盖 refreshing、paused、partial、offline、authorization 等十二状态。
3. axe、完整键盘路径与 AA 对比度缺少全量自动化；运行时性能仍必须转真实浏览器 UAT。
4. legacy `App.tsx` 已在所有独占 authority 入口迁入 V2 后退出源码树；后端旧 application 模块仍分别受事实与发布门禁约束。

## 文档 01：产品 / UI / UX §§27–60、63–64

| 条目 | 状态 | 实现证据 | 测试证据 / 真实缺口 |
|---|---|---|---|
| §27 一级导航 | 代码闭环 | `apps/web/src/layouts/AppShell.tsx`, `apps/web/src/app/router.tsx` | `router.test.tsx`；系统区折叠、创作/工具/系统语义路由已存在。 |
| §28 四个目标入口 | 代码闭环 | `pages/ProjectHomePage.tsx`, `pages/ProjectsPage.tsx`, `pages/projectRecency.ts` | 多季目标分集与项目最近/其他分区均有测试；权威 `updated_at` 稳定降序，每卡保持唯一下一步。 |
| §29 极速 Agent 模式 | 代码闭环 | `episode-run-v2/EpisodeRunPanel.tsx`, `episode-cockpit/EpisodeCockpit.tsx`, `application/episode_production_runs.py`, `application/automation_workflows.py` | production mode、八个创作阶段、pause/resume，以及五档 `checkpoint_policy` 均有测试；策略冻结进 fingerprint/workflow/batch，真实长运行仍属 UAT。 |
| §30 专业导演模式 | 代码闭环 | `pages/DirectorDeskPage.tsx`, `features/director-v2/`, `director-desk.css` | `ShotNavigator.test.tsx`, `DirectorMediaStage.test.tsx`, router tests；真实视口/媒体操作仍属 UAT。 |
| §30.1 Context Bar | 代码闭环 | `DirectorDeskPage.tsx` | 页面/路由测试；真实默认模型与集级动作随数据变化需 UAT。 |
| §30.2 Shot Navigator | 代码闭环 | `ShotNavigator.tsx`, storyboard reorder command | `ShotNavigator.test.tsx`, `test_shot_editing.py`, `test_storyboard_batch.py`。 |
| §30.3 Media Stage | 代码闭环 | `DirectorMediaStage.tsx`, `CandidateCompareDialog.tsx`, `DirectorIntentEditor.tsx`, `StagingBoard.tsx`, `director-3d/LazyDirector3DSpike.tsx` | `DirectorMediaStage.test.tsx` 覆盖 Fit/100%、detail ×2、九宫格、安全框、按住闪切；2D/3D 已由 Inspector 懒加载组合，真实操作仍属 UAT。 |
| §30.4 查看原文 | 代码闭环 | `source-passage/DirectorSourcePassage.tsx`, source passage API | source passage / scene range tests；20 万字真实文本性能属 UAT。 |
| §30.5 Inspector | 代码闭环 | `DirectorIntentEditor.tsx`, `DirectorSoundInspector.tsx`, ShotAssetSection、Generation/Frame Bridge panels | 角色/场景走正式 shot asset bind/unbind；声音映射项目 voice 与分集 audio binding 真实事实，无镜头级 authority 时诚实只读并深链；定向 10 tests。 |
| §30.6 Takes / Filmstrip | 代码闭环 | Director takes tray、Shot Navigator、Candidate Compare | candidate/compare tests；真实 20+ 镜横向扫片体验属 UAT。 |
| §31 Retry / Resample 分离 | 代码闭环 | jobs retry API 与 generation variant/resample commands 分离 | `test_generation_variants.py`, `test_variant_submission.py`, worker tests。 |
| §32 重抽原因 | 代码闭环 | generation variant branch reason / reason menu | generation variant 与 director UI tests；统计报表属于 P2 尚未完整。 |
| §33 Candidate 生命周期 | 代码闭环 | candidate、selection、machine check、human review、delivery facts 分表/服务 | `test_g4_reviews.py`, `test_formal_selection_commit.py`, `test_review_versions.py`。 |
| §34 Frame Bridge | 代码闭环 | `FrameBridgeControls.tsx`, `application/frame_bridges.py` | `FrameBridgeControls.test.tsx`, `test_frame_bridge_source_frame.py`。 |
| §35 首帧来源 | 代码闭环 | Frame Bridge source menu、media/image/video time source commands | Frame Bridge tests；真实抽帧质量属 UAT。 |
| §36 尾帧来源 | 代码闭环 | exact LAST_FRAME binding / source-frame extraction | `test_frame_bridge_source_frame.py`。 |
| §37 锁定 / stale | 代码闭环 | frame anchors、boundary revision、stale propagation | continuity / frame bridge tests；文档九步真实 stale 场景为外部 UAT。 |
| §38 Asset Bible 布局 | 代码闭环 | `pages/AssetBiblePage.tsx`, `asset-bible-v2/` | `AssetBiblePage.test.tsx`, `AssetUsagePanel.test.tsx`。 |
| §39 角色快捷动作 | 部分闭环 | multi-view、`GenerateExpressionPanel.tsx` 九槽、`GenerateDetailPanel.tsx` 三槽 IMAGE_EDIT、state、voice、reference picker 与 `ReferenceVersionCompare.tsx` | 三视图/表情/近景/MediaPicker/参考 A-B 比较 tests；图片反推 DNA 仍因无视觉理解 capability 未闭环。 |
| §40 Reference 类型 | 代码闭环 | migration 0042、asset bible domain/API | `test_migration_0042.py`, `test_asset_bible.py` 覆盖 reference metadata/HERO。 |
| §41 Character State | 代码闭环 | state/reference/episode/shot state binding | `test_asset_bible.py`, Asset Bible UI tests。 |
| §42 Scene Asset | 部分闭环 | `SceneBiblePanel.tsx` 的 DAY/NIGHT 与 WIDE/REVERSE/PANORAMA/LIGHT_REFERENCE | `SceneBiblePanel.test.tsx`；720° panorama 是未来可选，真实场景参考 UAT 未完成。 |
| §43 能力优先模型 UI | 代码闭环 | preferences UI、semantic Published Profile selectors、Generation Estimate 与 `RecommendationFacts` | 同 Profile/同维度最近终态 attempts 样本≥3才显示成功率；不足明确 UNKNOWN，不伪造 GPU 型号。 |
| §44 Override 层级 | 代码闭环 | migration 0043、preferences resolver/UI | `test_migration_0043.py`, `test_profile_compatibility.py`。 |
| §45 自动推荐 | 代码闭环 | AUTO resolver 与 `AUTO_NEWEST_PUBLISHED_EXACT_CAPABILITY` 推荐事实 | 返回 semantic profile/capability exact/PUBLISHED/native/resources 与可信成功率 cohort；后端6项、前端2项定向测试。 |
| §46 Episode Cockpit | 代码闭环 | `episode-cockpit/EpisodeCockpit.tsx`, cockpit read route | `EpisodeCockpit.test.tsx`, `test_episode_cockpit.py`。 |
| §47 一键生成策略 | 代码闭环 | DRAFT/BALANCED/QUALITY production modes | `test_episode_production_modes.py`, `EpisodeRunPanel.test.tsx`。 |
| §48 QC 分类 | 部分闭环 | file/video/audio/continuity/director QC services与 UI | formal video/audio/QC tests；可选脸部相似模型和部分叙事视觉判断未闭环。 |
| §49 bounded reroll | 代码闭环 | QC policy `max_auto_rerolls`、budget/gate、append-only variants | `test_qc_auto_reroll_policy.py`, episode worker tests。 |
| §50 Audio UX | 代码闭环 | `audio-v2/`, dialogue governance、Audio Episode Overview | dialogue/TTS/audio QC tests；真实 LUFS/口型/混音观感属 UAT。 |
| §51 Timeline | 代码闭环 | `timeline-v2/TimelineTracks.tsx`, immutable timeline revisions/compose | `TimelineTracks.test.tsx`, timeline/compose/delivery tests。 |
| §52 Advanced Canvas 定位 | 代码闭环 | `canvas/ProductionCanvasPanel.tsx`, legacy compatible canvas entry | `test_g9_canvas.py`；标准生产仍走线性页面。 |
| §53 Canvas nodes | 部分闭环 | production canvas read model / existing nodes | canvas tests；全部建议节点与正式依赖/视觉连线区分仍非完整证明。 |
| §54 视觉方向 | 代码闭环 | `styles.css`, feature CSS 与现有 hybrid workstation tokens | 浏览器 UAT；静态存在不等于视觉验收。 |
| §55 语义 token | 代码闭环 | `styles.css` stale/blocked/draft/reference/media/timeline tokens | CSS/组件测试；AA 对比度仍需自动/人工验证。 |
| §56 页面层级 | 代码闭环 | Project/Story/Asset 标题与紧凑 Director context bar | 精确四档 Edge UAT spec；无真实数据时只证明 shell。 |
| §57 系统状态下沉 | 代码闭环 | `LocalRuntimeIndicator.tsx`, system routes | `LocalRuntimeIndicator.test.tsx`；真实 runtime degraded/reconnect 为 UAT。 |
| §58 键盘 | 代码闭环 | Director shortcuts、Media Stage Space、Command Palette | `DirectorIntentEditor.shortcuts.test.tsx`, `DirectorMediaStage.test.tsx`, `CommandPalette.test.tsx`；全键盘巡检属 UAT。 |
| §59 拖拽 | 代码闭环 | Shot reorder、StoryAsset→shot、`DirectorTakeAdoption` candidate→selected、`frameCandidateDrag.ts` + FrameBridgeControls 首/尾帧 drop targets | 所有 drop 先确认并复用正式 authority command，按钮/键盘等价；跨项目/归档/重复/只读前端阻止且后端再裁决。 |
| §60 Undo / 全状态 / 错误 | 部分闭环 | superseding selection、inverse reorder、shared Empty/Error/Skeleton、creator blockers | primitives/selection/reorder tests；不能证明每个异步组件十二状态全部覆盖。 |
| §63 U1–U5 | 外部 UAT | 对应入口和各 vertical slice 均存在 | 20 万字、30 秒定位、真实四候选、五镜局部失败、整集合成必须用发布候选实测。 |
| §64 可量化 UX | 外部 UAT | deep links、inline actions、audit/revision 为前置代码事实 | 点击数、90% blocker、零丢失、20+ 镜任务需遥测/人工计时；不可用单测冒充。 |

## 文档 01：执行优先级 P0–P2

| 优先级 | 状态 | 代码证据 | 未完成部分 |
|---|---|---|---|
| P0 | 代码主体闭环 | Router/AppShell、Director five-zone、Shot Navigator、Media Stage、candidate/reroll、Frame Bridge、MediaPicker、Asset Bible、Episode Cockpit | 真实 Windows/Comfy/媒体 UAT仍是发布 blocker。 |
| P1 | 部分闭环 | state/scene、multi-view、preference、QC policy、已组合 StagingBoard、audio/timeline、Story/shot editing | 表情九宫格、完整 split/merge 场景 UAT及部分 QC 模型仍缺。 |
| P2 | 部分闭环 | Director Recipe、production modes、Advanced Canvas、3D spike | 重抽原因/模型质量统计、Canvas 与 Recipe 完整互通、3D 正式产品化未闭环。 |

## 文档 02：架构 §§0、5–86

| 条目 | 状态 | 实现证据 | 测试证据 / 真实缺口 |
|---|---|---|---|
| §0 硬规则 | 部分闭环 | append-only revision、filesystem authority、local-only、generated client、no arbitrary recipe execution | fact guardrail/release invariant tests；legacy 与全量外部 UAT未退场。 |
| §5 路由 / URL | 代码闭环 | `app/router.tsx`, `legacyRoute.tsx` | router/legacy route tests。 |
| §6 前端目录 | 代码闭环 | `pages/`, `features/`, `components/ui/`, `layouts/`, `generated/` | architecture/review audit；旧 App 仍隔离保留。 |
| §7 App.tsx 最终职责 | 已完成 | V2 Router/AppShell 是唯一入口；旧 `App.tsx` 已删除，历史 query 由兼容边界无损映射到 V2 | `p12_decommission_readiness.py`；router/legacy/P12 tests。 |
| §8 Director 组件架构 | 代码闭环 | `features/director-v2/`, `DirectorDeskPage.tsx` | Director component/router tests。 |
| §9 Director 聚合 Read Model | 代码闭环 | `application/director_desk.py`, route/client | `test_director_desk.py` 含空镜/100-shot bound。 |
| §10 Command / Query 分离 | 代码闭环 | `application/commands/`, `application/queries/` | architecture boundary tests。 |
| §11 generated API | 代码闭环 | `apps/web/src/generated/api.ts`, generator script | OpenAPI/API contract audit；本矩阵未手改 generated。 |
| §12 后端目标目录 | 部分闭环 | domain/application/infrastructure/api 分层已存在 | 新能力受边界测试；旧 application modules 尚未全部退役。 |
| §13 Ports & Adapters | 部分闭环 | `application/ports/`, database/runtime adapters | architecture tests；渐进迁移，非所有旧服务均 port 化。 |
| §14 DirectorIntent V3 | 代码闭环 | `domain/director_intent.py`, schemas/routes/editor | `test_director_intent_v3.py`, `test_director_fields.py`。 |
| §15 Shot/Scene/Group | 代码闭环 | migration 0044、shot groups/scene ranges | shot group/scene range/shot editing tests。 |
| §16 Source Context | 代码闭环 | source passage APIs/components | document import/breakdown/source range tests；长文性能外部 UAT。 |
| §17 Story Asset 模型 | 代码闭环 | migration 0042、asset bible repository/domain | migration/asset bible/package tests。 |
| §18 Character Voice | 部分闭环 | voice profile/binding、audio overview、Asset Bible voice read fact | dialogue/voice tests；Character Bible 内完整 voice 编辑体验仍可加强。 |
| §19 三视图流程 | 部分闭环 | asset multi-view service/API/UI | component/API tests；真实 Comfy 三视图一致性为外部 UAT。 |
| §20 Generation Preference | 代码闭环 | migration 0043、resolver/repository/UI | migration/profile capability tests。 |
| §21 创作者/工程 UI 分层 | 部分闭环 | semantic Profile selectors、advanced details、system model pages | raw UUID 普通路径已持续清理；全部页面仍需静态/浏览器复审。 |
| §22 Resample API | 代码闭环 | GenerationVariant plan/submit/branch reason | generation variant tests。 |
| §23 权威关系 | 代码闭环 | candidate/selection/QC/review/delivery facts | review/formal selection/delivery tests。 |
| §24 Frame Bridge | 代码闭环 | existing frame anchors/transition facts + bridge read model | Frame Bridge/continuity tests。 |
| §25 Shot commands | 代码闭环 | reorder/split/duplicate/batch commands | `test_shot_editing.py`, storyboard batch tests。 |
| §26 Episode Run | 代码闭环 | facade/preflight/stages/pause/resume/recovery | production mode/worker/recovery tests；process-kill UAT仍外部。 |
| §27 Recipe | 代码闭环 | migration 0046、recipes-v2、safe declarative validation | `test_director_recipes.py`; arbitrary command fields 前后端拒绝。 |
| §28 QC Policy | 代码闭环 | migration 0045、policy resolver/UI/auto-reroll | QC policy tests。 |
| §29 Migration 总表 | 代码闭环 | migrations 0042–0048，唯一 head 0048 | migration suite与 rehearsal evidence。 |
| §30 0042 Backfill | 代码闭环 | 0042 canonical HERO idempotent backfill/downgrade | `test_migration_0042.py`。 |
| §31 Story Asset API | 代码闭环 | asset bible routes/commands | `test_asset_bible.py`。 |
| §32 Asset Bible Read Model | 代码闭环 | project aggregate read route/repository | asset bible tests与规模夹具；目标机耗时仍应记录。 |
| §33 Asset stale | 部分闭环 | query-time/fingerprint stale、proposal merge propagation | asset/frame/generation tests；统一 Stale 服务尚未收敛。 |
| §34 SSE | 代码闭环 | `features/events/eventClient.ts`, backend event/outbox routes | `eventClient.test.ts`；真实断线重连 UAT。 |
| §35 Job / Worker | 代码闭环 | idempotency、lease、cancel、attempt/recovery | worker/job/recovery tests。 |
| §36 Model Adapter | 代码闭环 | local adapter contracts、Comfy isolation | adapter/profile tests；真实模型运行外部。 |
| §37 Capability Manifest | 代码闭环 | runtime manifest/profile capability resolver | profile/capability tests。 |
| §38 Error Contract | 部分闭环 | structured API errors、creator blocker panels | contract tests；仍不能证明所有旧 UI 均已转创作者语言。 |
| §39 Query Key | 代码闭环 | `apps/web/src/query/queryKeys.ts` 集中定义 project/season/episode、asset、settings、jobs/capacity、diagnostics/audit、profiles/workflows、freshness/source passage | `queryKeys.test.ts` 与 Audit 定向测试；Jobs heartbeat 与 Capacity 失效边界已区分。 |
| §40 Optimistic Update | 代码闭环 | authority mutation 均等待服务端；纯 UI tab/nav/reorder preview 可本地即时更新 | `authorityMutationGuard.test.ts` 10项静态守卫禁止 authority `onMutate` cache/entity/伪成功写，相关22项行为测试。 |
| §41 Director 保存模型 | 代码闭环 | explicit save、expected revision、dirty warning 与 revision/schema scoped local draft buffer | 500ms debounce、1MB上限；恢复不自动套用，stale草稿必须显式迁移；保存清理、409/Quota/坏JSON fail-safe 共7项测试。 |
| §42 并发冲突 | 代码闭环 | expected revision/409 conflict、immutable revisions | director/shot/timeline conflict tests。 |
| §43 Episode Plan | 代码闭环 | `EpisodePlanPage.tsx`, storyboard workbench/group planner | storyboard batch/shot group tests。 |
| §44 项目生产配置 | 代码闭环 | `ProductionSettingsOverview.tsx` + preference/QC/recipe/delivery/capacity deep links | router/feature tests；多季交付目标已去除 first-season 假设。 |
| §45 Style / Brand Kit | 代码闭环 | `generation_style_context.py`, `generation.py`, BrandKit/STYLE/STYLE_REFERENCE 事实 | `test_generation_style_context.py`；只冻结声明式 token 与不可变 provenance，危险执行/路径字段剥离，变化使旧 preflight/replay 失效。 |
| §46 Timeline / Director 边界 | 代码闭环 | lightweight Director filmstrip + dedicated Timeline page | timeline/director tests。 |
| §47 Audio | 代码闭环 | dialogue/voice/TTS/audio bindings/QC/timeline tracks | audio/TTS/QC tests；人工音画 UAT。 |
| §48 媒体存储 / 缩略图 | 代码闭环 | filesystem authority、thumbnail endpoints/cache、video Range | thumbnail/media policy/episode render tests；真实大库 UAT。 |
| §49 SQLite WAL | 代码闭环 | database bootstrap/connection policies | database/concurrency tests；真实压力仍外部。 |
| §50 索引 | 代码闭环 | Alembic migrations中的 project/episode/shot/status/order indexes | migration/schema tests。 |
| §51 Director 性能预算 | 代码闭环 + 外部 UAT | bounded aggregate、thumbnail-only、lazy 3D、progressive list、V2 路由与 legacy workspace 懒加载；`check-bundle-budget.mjs` 强制单 JS chunk ≤500 KiB | Build 已通过 52 chunks、最大 397.3 KiB；首屏/切镜/100+镜耗时目标仍必须在发布硬件测量。 |
| §52 Read Model SQL | 代码闭环 | director/asset/cockpit aggregate queries | read model tests；目标 DB query plan 仍可归档。 |
| §53 安全边界 | 代码闭环 | local-only、path validation、recipe arbitrary execution rejection、no network manifests | security/release invariant/recipe tests。 |
| §54 模型 / 凭据 | 代码闭环 | local profile/config/credential redaction | profile/audit tests；真实凭据配置 UAT。 |
| §55 Audit Event | 代码闭环 | audit history route、domain mutations append audit | `test_audit_history.py` 与各 command tests。 |
| §56 Prompt / Recipe 边界 | 代码闭环 | immutable prompt facts + declarative recipe versions | prompt/recipe tests。 |
| §57 Prompt 构造 | 代码闭环 | prompt builder、DirectorIntent/asset/frame/preference snapshots | generation/prompt tests；模型主观效果外部。 |
| §58 输入指纹 | 代码闭环 | plan/input fingerprint、snapshot/hash、replay guards | episode/generation/compose tests。 |
| §59 Stale 服务 | 代码闭环 | `ProductionFreshnessService` + SQLite read adapter + project/episode/shot UI | 统一返回 SHOT/ASSET_REFERENCE/ASSET_STATE/FRAME_BRIDGE/PROFILE/PROMPT/POST_PROCESS/SELECTION 变化；UI 只显示报告，4项定向测试。 |
| §60 Shot 状态机 | 代码闭环 | shot status/readiness commands and gates | shot editing/readiness tests。 |
| §61 Episode Run 状态机 | 代码闭环 | production run stages/pause/resume/recovery | mode/worker/recovery tests。 |
| §62 Asset Readiness | 代码闭环 | EMPTY/BASIC/READY/STALE aggregate | asset bible/multiview tests。 |
| §63 Design Token | 代码闭环 | `styles.css`, `components/ui/primitives.css`, feature CSS | build/component tests；AA 外部/自动扫描待补。 |
| §64 UI components | 代码闭环 | eleven shared primitives + adopted V2 pages | `primitives.test.tsx`；迁移触达外旧组件仍存在。 |
| §65 Command Palette | 代码闭环 | `commands/CommandPalette.tsx`, registry/navigation/search | `CommandPalette.test.tsx`; AppShell project context tests。 |
| §66 Accessibility | 部分闭环 | semantic labels, dialog focus/Esc, keyboard panes, reduced motion | component tests；axe、全键盘、AA和读屏 UAT未完成。 |
| §67 前端旧组件迁移 | 按 gate 延后 | V2 pages/routes已承接主链，legacy route仍显式保留 | P12 readiness gate未满足，不能删除旧页面。 |
| §68 后端旧模块迁移 | 部分闭环 | new commands/queries/ports已承接新增主链 | 旧 application services仍有合法 caller，待渐进收敛。 |
| §69 API version / 兼容 | 代码闭环 | `/api/v1`, OpenAPI generated client、legacy normalizers | API contract/release audit tests。 |
| §70 Feature Flag | 部分闭环 | V2 router/legacy opt-in与 capability gates | 没有覆盖所有 vertical slice 的统一 flag registry。 |
| §71 迁移回滚 | 代码闭环 + 外部演练 | Alembic downgrade、`upgrade-rollback-rehearsal-0041-to-0048-2026-08-20.json` | 临时副本演练有证据；生产备份恢复必须外部执行。 |
| §72 Project Package | 代码闭环 | `application/project_packages.py`, 0042–0048 facts | `test_project_packages.py` round-trip/legacy/id rewrite。 |
| §73 Search | 代码闭环 | GET `/search`, CommandPalette/GlobalSearchPanel navigation | `test_search_read_model.py`, `CommandPalette.test.tsx`。 |
| §74 Diagnostics 分离 | 代码闭环 | system pages/routes与创作页 local indicator | `SystemPages.test.tsx`, router tests。 |
| §75 Task-language gates | 部分闭环 | Episode Cockpit/preflight/creator blocker mappings | 多数 V2 已转译；无法证明所有旧 error code 均已转换。 |
| §76 Estimate | 代码闭环 | generation estimate service + `GenerationEstimateFact.tsx` | `test_generation_estimates.py`, component tests；少样本显示 NO_LOCAL_ESTIMATE。 |
| §77 Capacity / Disk | 代码闭环 | capacity snapshot/disk gate/UI | `test_capacity_snapshot.py`, `test_capacity_disk_gates.py`。 |
| §78 FFmpeg Adapter | 代码闭环 | compose/post-process/ffprobe adapters、no arbitrary command config | compose/post-process/delivery tests；真实 codec UAT。 |
| §79 Testing seam | 代码闭环 | ports、fake adapters、temporary DB、isolated browser fixture | architecture/unit/integration suites。 |
| §80 ADR | 代码闭环 | `docs/decisions/ADR-0101`–`ADR-0110`, README map | 文本审计；决策存在不等于实现自动通过。 |
| §81 Single source of truth | 代码闭环 | media/version/revision/selection/review/recipe/preference facts各自唯一 authority | fact guardrail/package/review tests。 |
| §82 P0 API | 代码闭环 | Director aggregate、asset bible、commands、preference、frame bridge、episode run APIs | OpenAPI missing=0 与对应 API tests。 |
| §83 P0 frontend | 代码闭环 | Project/Asset/Plan/Director/Run/Review/Audio/Timeline pages | router与 feature tests；真实 UAT另列。 |
| §84 不提前工程化 | 代码闭环 | modular monolith、SQLite、现有 CSS、无微服务/Redux/Tailwind 重写 | architecture invariants / ADR。 |
| §85 Review Checklist | 部分闭环 | release/refactor invariant scripts覆盖大量数据/生成/UI规则 | checklist含人工 review 项，不能由脚本全部勾选。 |
| §86 Architecture DoD | 部分闭环 + 外部 UAT | 主 vertical slices、migrations、package、recovery、tests均有代码事实 | 真实 Comfy/GPU、旧项目、20+镜、音画、浏览器/无障碍、备份恢复仍未通过。 |

## 自动化与外部证据边界

可复用的历史冻结证据见 `FINAL_TEXT_EVIDENCE_MATRIX_2026-08-20.md`、baseline 与 migration rehearsal JSON。它们证明当时的源码/临时副本测试结果，不自动证明后续 checkpoint 代码或生产数据仍通过。发布候选必须重新归档：Web build/Vitest、backend offline suite、OpenAPI/release invariants、四档 Edge 只读 UAT、临时副本 upgrade/rollback。

以下保持未通过，等待同一发布候选的外部证据：真实 Comfy/GPU 与模型、20 万字导入、真实四候选/五镜/20+镜生产、Frame Bridge 九步 stale、进程 kill/app restart、真实音画/字幕/compose/delivery 文件、旧项目原地升级、1280×720/1440×900/1920×1080/2560×1440 视觉与键盘/读屏/AA、生产备份恢复。

## 审计结论

核心产品与架构 vertical slices 已有广泛代码和自动化证据，但仍存在上述可本地开发缺口，并且产品故事、性能、视觉、媒体质量、恢复与真实运行环境不能由静态代码审计替代。本矩阵没有用“未发现问题”作为完成证据，也没有把技术 spike、历史测试结果或外部依赖写成最终验收通过。
