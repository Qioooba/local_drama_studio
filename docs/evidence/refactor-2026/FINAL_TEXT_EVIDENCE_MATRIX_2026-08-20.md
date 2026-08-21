# xinjihua 03 最终文本证据矩阵（2026-08-20）

范围：逐项对照 `docs/xinjihua/03_实施迁移测试验收手册.md` 的 P0-01～P12、§121 V2.0 必须项和 §123 Final Release Checklist。本审计只读取 Markdown 文本，不读取原图。状态定义：

- **代码闭环**：存在真实实现和自动化测试证据；不等同于真实硬件 UAT 已完成。
- **部分闭环**：主能力存在，但文档中的一部分仍待自动化或人工验收。
- **外部验证**：必须使用真实项目、Windows/GPU/Comfy、人工视觉或恢复演练，不能由源码静态检查代替。
- **按设计延后**：文档明确要求满足前置 UAT 后才执行。

## P0～P12 证据矩阵

| Work item | 状态 | 实现证据 | 测试/验收证据与剩余项 |
|---|---|---|---|
| P0-01 标准环境 | 部分闭环 | `scripts/check.ps1`, `scripts/doctor.ps1`, `scripts/comfy.ps1` | 本机实测 Python 3.12.10、Node 22.23.2、pnpm 9.15.9、FFmpeg 8.1.2；真实 Comfy 启停/GPU 仍为外部验证。测试 DB 隔离由 pytest 临时目录覆盖。 |
| P0-02 Baseline Evidence | 代码闭环 | `docs/evidence/refactor-2026/baseline/BASELINE_2026-08-19.md` | 已落 baseline 文本；发布时仍应归档当次 check、浏览器、迁移和样本 DB 元数据，不能复用旧时间戳冒充当次结果。 |
| P0-03 Architecture Tests | 代码闭环 | `application/commands`, `application/queries`, `application/ports`; `scripts/refactor_invariants.py` | `test_refactor_architecture_boundaries.py`, `test_refactor_release_invariants.py` 对新目录 concrete infrastructure import hard fail。 |
| P0-04 生成事实护栏 | 代码闭环 | variants、selection、frozen revision、stale 采用 append-only/历史保留实现 | `test_refactor_fact_guardrails.py`, `test_generation_variants.py`, `test_variant_submission.py`, `test_shot_editing.py`, `test_frame_bridge_source_frame.py`。 |
| P1-01 Router | 代码闭环 | `apps/web/src/app/router.tsx`, `apps/web/src/app/legacyRoute.tsx`；旧 `/` 与 `legacy=1` 回退仍保留 | `router.test.tsx`, `legacyRoute.test.tsx`；项目、资产、分集支持 deep link；有等价目标的旧 query URL 渐进重定向，未知/不完整/精确 review 上下文不 silent drop。 |
| P1-02 AppShell | 代码闭环 | `apps/web/src/layouts/AppShell.tsx` | 项目/分集上下文、生产语义侧栏、命令入口和全局任务/运行时入口已组合；新“故事”路由保持增量链接。 |
| P1-03 System Status 收缩 | 代码闭环 | `features/status-v2/LocalRuntimeIndicator.tsx` | `LocalRuntimeIndicator.test.tsx` 覆盖紧凑状态；真实 degraded/runtime reconnect 仍需发布 UAT。 |
| P1-04 UI primitives | 代码闭环 | `components/ui/primitives.tsx` 集中提供 ResizablePane、MediaThumb、StatusBadge、InspectorSection、PropertyRow、EmptyState、ErrorState、Skeleton、Dialog、ContextMenu、Tooltip；样式集中在 `primitives.css` | `primitives.test.tsx` 覆盖缩略图 fail-closed、语义状态、Dialog 键盘关闭/焦点、ResizablePane 键盘边界和 ContextMenu disabled reason；V2 页面继续按触达范围迁移，禁止新增重复 primitive CSS。 |
| P2-01 Migration 0042 | 代码闭环 | `0042_asset_bible_states_references.py` | `test_migration.py`, `test_migration_0042.py` 覆盖空库、旧库/backfill/downgrade 边界；当前 Alembic 唯一 head 为 `0048_asset_proposals`。 |
| P2-02 Repository Ports | 代码闭环 | `infrastructure/database/asset_bible_repository.py`, `application/ports/` | `test_asset_bible.py` 使用临时 SQLite；架构测试约束 application 边界。 |
| P2-03 Commands | 代码闭环 | `application/commands/asset_bible.py`, `routes/asset_bible.py` | `test_asset_bible.py` 覆盖 state/reference/HERO/episode/shot 绑定与跨项目 invariant。 |
| P2-04 Asset Bible Read Model | 代码闭环 | `GET /asset-bible` aggregate | `test_asset_bible.py` 包含 read model/规模夹具证据；应在最终目标机保留查询耗时记录。 |
| P2-05 Project package | 代码闭环 | `application/project_packages.py` 已包含 states、refs、bindings 及后续 0048 proposal 兼容 | `test_project_packages.py` 覆盖新包 round-trip、旧包默认和 ID 重写。 |
| P3-01～P3-02 Asset Bible UI/详情 | 代码闭环 | `pages/AssetBiblePage.tsx`, `features/asset-bible-v2/` | `AssetBiblePage.test.tsx`, `SceneBiblePanel.test.tsx`；普通流程显示语义名而非裸 UUID。 |
| P3-03 Upload/Select Existing | 代码闭环 | `features/media-picker/MediaPicker.tsx` 及 asset bible API | `MediaPicker.test.tsx`；真实大文件上传和候选媒体人工选择需 UAT。 |
| P3-04 三视图 | 部分闭环 | `GenerateMultiViewPanel.tsx`, `application/asset_multiview.py` | 组件/API 测试覆盖无 HERO、capability、partial result、选择后 refs 与历史保留；真实 Comfy 三张结果质量是外部验证。 |
| P3-05 Scene Bible | 代码闭环 | `SceneBiblePanel.tsx` 复用同一 asset framework | `SceneBiblePanel.test.tsx`；真实旧项目 canonical 映射需 UAT。 |
| P4-01 Source Passage | 代码闭环 | `pages/EpisodePlanPage.tsx` 组合原文/拆解/计划工作区 | 路由测试与既有 breakdown/document import tests；长文真实性能仍需项目 UAT。 |
| P4-02 Shot Table | 代码闭环 | `StoryboardBatchWorkbench.tsx`: Scene/Beat、动作、对白、资产、时长、状态/Ready、拖动重排、拆分、复制、多选、批量类型、批量状态、逐镜 Ready | `StoryboardBatchWorkbench.test.tsx`, `test_storyboard_batch.py`, `test_shot_editing.py`。批量类型于本审计补齐，进入既有 plan/hash/expected revision/commit，不绕过预览。 |
| P4-03 Selected Beat Replan | 代码闭环 | `application/beat_replan.py`, `routes/beat_replan.py`, `SelectedBeatReplanPanel.tsx` | `test_beat_replan.py` 覆盖 KEEP/ADD/MODIFY/DELETE/PROTECTED、冻结保护、hash/revision/idempotency/audit/outbox。 |
| P4-04 Shot Groups/Scene id | 代码闭环 | migrations 0044；`application/shot_groups.py`, `ShotGroupPlanner.tsx` | `test_shot_groups.py`；SQLite migration 使用 batch-safe additive 方案。 |
| P5-01 Director aggregate | 代码闭环 | `application/director_desk.py`, `routes/director_desk.py` | `test_director_desk.py` 覆盖空镜、候选、stale、旧项目、真实 scene/group、archived split parent 排除与 100-shot bounded query。 |
| P5-02～P5-03 Five-zone/Media Stage | 代码闭环 | `pages/DirectorDeskPage.tsx`, `ShotNavigator.tsx`, `DirectorMediaStage.tsx`, `director-desk.css` | ShotNavigator/MediaStage tests；1280/1440/1920、键盘 J/K 和真实视频操作仍列发布人工/Playwright UAT。 |
| P6-01 DirectorIntent V3 | 代码闭环 | `domain/director_intent.py`, director intent route/schema/client | `test_director_intent_v3.py` 覆盖 v1/v2 normalizer、v3 round-trip、unknown/invalid。 |
| P6-02～P6-04 Inspector/Revision/Ready | 代码闭环 | `DirectorIntentEditor.tsx`, `application/commands/`, readiness/preflight 服务 | `test_director_fields.py`, `test_director_intent_v3.py`, `test_episode_asset_preflight.py`；真实浏览器离开 dirty warning需最终 UAT。 |
| P7-01～P7-02 Preference/Resolver | 代码闭环 | migration 0043；`generation_preference_repository.py`, preferences UI/API | `test_migration_0043.py`, `test_profile_compatibility.py`, `test_ref2va_capability.py` 覆盖继承、显式 blocker 与 capability。 |
| P7-03～P7-06 Candidate/Generate/Reroll/Compare | 代码闭环 | Director takes tray、GenerationControl、CandidateCompareDialog | generation/variant/compare 组件和 API tests；真实 4 候选生成耗时与视频同步体验需 Comfy UAT。 |
| P8-01～P8-03 Frame Bridge | 代码闭环 | `FrameBridgeControls.tsx`, `frame_bridges.py`, exact FIRST_FRAME/LAST_FRAME bindings | `FrameBridgeControls.test.tsx`, `test_frame_bridge_source_frame.py`, continuity/generation tests。 |
| P8-04 stale UAT | 外部验证 | stale propagation 和历史保留已有实现 | 自动回归不能替代文档 9 步真实选择/抽帧/重继承操作，列为 release blocker UAT。 |
| P9-01～P9-03 Episode Run facade/preflight/UI | 代码闭环 | `episode_production_runs.py`, `EpisodeRunPanel.tsx`, existing automation queue facade；五档人工确认策略冻结进 workflow 与 batch | `test_episode_production_modes.py`, `test_episode_asset_preflight.py`, `test_episode_hitl_checkpoints.py`, `EpisodeRunPanel.test.tsx`。 |
| P9-04～P9-06 Retry/Pause/Recovery | 代码闭环 + 外部演练 | bounded QC policy、worker actions、durable attempts、pause/resume/recovery | `test_qc_auto_reroll_policy.py`, `test_episode_worker_actions.py`, `test_episode_run_recovery.py`；真实 process kill、app restart、Comfy reconnect仍为外部演练。 |
| P9-07 Five-shot Pilot | 外部验证 | `scripts/g6_four_takes_uat.py`, production/run tooling 可执行 | 必须使用指定五镜真实项目、真实模型/Comfy/GPU 跑通，不能依据单测勾选。 |
| P10 Review/Audio/Timeline/Compose | 代码闭环 + 外部场景 | 独立 Review/Audio/Timeline/Delivery routes；formal review authority、timeline revisions、`application/compose.py` | review/audio/timeline/compose/export tests 存在；完整媒体合成观感、音画同步、delivery 文件人工检查为外部验证。 |
| P11-01 2D Staging | 代码闭环（V2.1） | `features/director-v2/StagingBoard.tsx`，结构化 blocking/camera plan | UI/DirectorIntent contract 证据；模型实际消费效果需生成 UAT。 |
| P11-02 3D Spike | 技术 spike，非 V2.0 blocker | `features/director-3d/` lazy load、结构模型和基础舞台 | `model.test.ts`；截图/参考导出与生成模型利用效果尚不能宣称完整成功。 |
| P11-03 Advanced Canvas | 代码闭环（专家入口） | `ProductionCanvasPanel.tsx`, `/ ?view=canvas` compatibility entry | `test_g9_canvas.py`；标准生产仍走线性导演台。 |
| P12 清理旧页面 | 按设计延后，可机器证明 | 新 Router 与旧 `/` 并存；`scripts/p12_decommission_readiness.py` 只读扫描替代路由、legacy callers/query links、API symbols、package/API protected facts 与 UAT gates | 当前报告必须为 `DEFERRED`；只有 caller=0 且 `p12-decommission-gates.json` 三份 UAT evidence 均为 PASS + approved 才报告 READY。READY 也只授权另行评审的删除变更，脚本自身永不删除。 |

P12 caller 收敛记录：Episode Review 与 Timeline 已从旧 `getEpisodeProduction` 改读 0044 shot-group workspace，实际旧 API callsites 从最初扫描的 `6/2/7`（含旧扫描误计的 import/type references）收紧并迁移为 `getEpisodeProduction=1`、`getShotContinuityContext=1`、`getReviewContext=3`。余下调用均属于 legacy shell 或需要 exact media owner/review detail，原因登记在 `p12-decommission-gates.json`；5 个 Canvas/legacy query callsites 均无完全等价 V2 route，因此保留。

## §121 V2.0 必须项

| 必须项 | 代码状态 | 发布前仍需证明 |
|---|---|---|
| Router/Shell | 已实现 | deep link + Windows viewport/keyboard UAT |
| Asset Bible multi-ref/state | 已实现 | 旧项目 canonical、真实上传/三视图 UAT |
| Episode Plan | 已实现 | 长文本、20+ 镜交互与性能 |
| Director Desk five-zone | 已实现 | 1280/1440/1920 与真实视频操作 |
| DirectorIntent V3 | 已实现 | dirty navigation/refresh 精确恢复浏览器 UAT |
| Candidate/Reroll | 已实现 | 真实 Comfy 4 takes、reroll lineage evidence |
| Project/Episode/Shot preference | 已实现 | 目标机模型缺失/恢复场景 |
| Frame Bridge | 已实现 | §P8-04 九步 stale UAT |
| Episode Run facade | 已实现 | 5/20+ 镜、pause/resume、kill/restart |
| Review/Audio/Timeline integration | 已实现 | 人工音画、review authority、导出检查 |
| Migration/compatibility/recovery | 已实现至 head 0048 | 发布候选备份、旧 fixture upgrade、restore rehearsal |

## §122 最终场景判定

从长剧本导入到 Delivery 的每一段均有真实代码入口和自动化事实测试，但**整条链尚不能仅凭源码标记“最终通过”**。以下必须作为同一次发布候选的外部证据归档：

1. 真实长剧本导入、AI 拆解人工 apply、角色/场景 identity 裁决。
2. 真实 HERO/三视图和模型 capability；Comfy 离线/重连。
3. 五镜后再 20+ 镜生产，含首尾帧 stale、局部重抽与超限人工处理。
4. TTS/音画同步、Timeline snapshot、Compose、Review 和最终导出文件人工检查。
5. 旧项目、旧 assets/variants/frame anchors/timeline 原地升级与打开。

## §123 Final Release Checklist 诚实状态

### 可由当前仓库/本机证明

- Environment：Python 3.12.10；Node 22.23.2；pnpm 9.15.9；FFmpeg 8.1.2 可定位。
- Database：Alembic 唯一 head `0048_asset_proposals`；0042/0043/全链迁移、invariant、package compatibility 均有定向测试。
- Backend：safe API、Ruff、架构边界、maintainability/release audit 均有脚本/测试入口；发布候选必须重跑并归档输出，静态存在不等于当次 PASS。
- Frontend：TypeScript build、Vitest、router deep link 和主要 Director 组件测试具备。
- Core production：Asset Bible、revision、candidate/reroll、preference、Frame Bridge、Episode Run、Review/Audio/Timeline/Compose/export 均有实现与自动化证据。

### 必须保持未勾选，等待发布候选外部验证

- Comfy runtime smoke、真实 GPU/模型、Comfy offline/reconnect。
- DB backup complete 与 backup restore rehearsal（不得对生产 DB 代跑）。
- 完整 mypy 与真实浏览器人工无障碍检查；Ruff/API/Vitest/基础 Edge 自动化已有本次实跑证据。
- 4 个真实 candidates、五镜 pilot、20+ 镜 episode、旧项目升级。
- 1280/1440/1920 视觉布局、Windows x64 Playwright core、键盘/可访问性人工 spot-check。
- job crash/app restart、media missing、真实音画/Compose/Delivery 人工验收。

结论：V2.0 的主要代码事实已成形，剩余 release blockers 主要是最终冻结版本的全套自动化重跑与真实 Windows/Comfy/旧项目/人工媒体验收。P12 清理必须继续等待这些 UAT 完成。

## 架构决策证据

`docs/decisions/README.md` 将 xinjihua 02 §80 的十个必需主题逐项映射到唯一编号 `ADR-0101`～`ADR-0110`。每份均记录 Context、Decision、Rejected alternatives、Consequences 和当前实现/测试事实来源；历史重复的 `ADR-0012` 文件保持原名以免破坏既有 evidence 链接，新系列不复用冲突编号。

## 本次冻结前自动化实跑（2026-08-20）

- Web Vitest（最终前端修复后重跑）：`58 files / 180 tests passed`。
- Backend 全量离线测试：`pytest -m "not comfyui" -q` 收集/执行 `468 tests`，`0 failures`；Release audit PASS，refactor invariants PASS，OpenAPI/API contract `missing=0`。
- Web production build：TypeScript + Vite 成功，`397 modules transformed`；仅保留 generated API 动/静态混用与主 chunk 体积警告。
- Windows Edge 隔离只读核心链：`1440×900`、`1280×800`、`1024×768` 共 `3 passed`；无公网请求、无原始媒体请求。测试首次暴露旧快照未迁移，已修复 `serve_isolated_core_browser_uat.py`，现在仅升级可丢弃副本至 Alembic head 后启动。
- 上述浏览器测试请求的图片均为 thumbnail endpoint；未读取原图。

这些结果把“最终冻结代码上的后端离线全量测试、Release/API 审计、Web build/Vitest/基础 Windows viewport 自动化”从待运行推进为已运行；`verify_release` 仍因生产 data DB 保持在 0041 且 loopback API `127.0.0.1:3210` 未启动而处于环境待办。真实 Comfy/GPU、五镜/20+ 镜、人工音画与完整旧项目升级仍保持未验收。
