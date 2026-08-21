# LocalDramaStudio V2 实施权威进度文档 (IMPLEMENTATION_PROGRESS.md)

> **实施基准（2026-08-21 19:20 +08:00）**：本文件为仓库内唯一权威执行与验证进度真值记录。所有判定均经过源码级审计、静态类型检查、前后端全量自动化测试、Alembic 数据库迁移与只读事实一致性审计核验。  
> **三维度分离纪律**：本进度严格区分 **代码实现 (Code Complete)**、**自动化测试 (Automated Tests Verified)**、**真实 UAT (Real UAT Verified)**。未完成真实人工/云端验证的条目如实标记为 `PENDING_UAT` 或 `DEFERRED`，严禁伪造全绿结论。  
> **基线目录**：`F:\AI_Projects\h3\local_drama_studio`  
> **上位依据**：`docs/xinjihua/01_竞品研究与产品_UI_UX_总设计.md`、`02_架构与前后端重构规格.md`、`03_实施迁移测试验收手册.md`

---

## 0. 核心架构原则与质量门禁

1. **V2 架构真值唯一**：V2 Router、`AppShell`、`LegacyRouteBoundary` 全面接管 22 条主路由，旧 `App.tsx` 保持退役；不建立第二套 Job / Variant / MediaVersion / Timeline / Run 业务数据模型。
2. **Alembic 迁移线性演进**：当前最新 Alembic head 为 `0054_character_identity_pack_hardening`（覆盖 0049 能力标准化、0050/0054 人物身份包、0051 Worker 会话、0052 存储原子操作、0053 能力修复），所有数据变更均具备可逆/隔离回滚方案。
3. **工作树与代码资产保护**：严禁执行 `reset --hard`、`clean -fd` 或未经审查的破坏性文件覆盖；保持非当前改动完整。
4. **前端响应式与紧凑视口**：主工作区在 1440x900 默认视口下保证紧凑布局（工作区最大垂直高度约 1800px），大量数据列表使用分页/Drawer/内部滚动，900/1024/1280/1440/1920 视口零横向溢出。

---

## 1. PR-CUR-001 ～ PR-CUR-012 实施状态矩阵

| PR 标识 | 核心范围与目标 | 代码实现 | 自动化测试 | 真实 UAT | 状态核定 | 当前测试证据 / 关键文件 |
|---|---|:---:|:---:|:---:|:---:|---|
| **PR-CUR-001** | 修复 Episode Plan 数据契约、错误边界与 QueryClient | **CODE_COMPLETE** | **TESTS_PASSED** | **PENDING_UAT** | **LOCAL_VERIFIED** | `queryKeys.ts`, `AIDraftReviewPanel.tsx`, `SelectedBeatReplanPanel.tsx`, `ErrorBoundary.tsx`, `EpisodePlanIntegration.test.tsx` (104 files 345 passed) |
| **PR-CUR-002** | 修复 Director 镜头错选、横向溢出与 Inspector 可达性 | **CODE_COMPLETE** | **TESTS_PASSED** | **PENDING_UAT** | **LOCAL_VERIFIED** | `DirectorDeskPage.tsx`, `GenerationPage.tsx`, `GenerationWorkbench.tsx`, `director-desk.css`, `DirectorDeskPage.test.tsx` |
| **PR-CUR-003** | 统一 Canonical Capability 标准化、别名映射与 Resolver | **CODE_COMPLETE** | **TESTS_PASSED** | **PENDING_UAT** | **LOCAL_VERIFIED** | `domain/capabilities.py`, `0049_canonical_capabilities.py`, `0053_canonical_capability_repair.py`, `test_canonical_capability_resolver.py` |
| **PR-CUR-004** | 修正 P12 退役脚本与证据链契约 | **CODE_COMPLETE** | **TESTS_PASSED** | **PENDING_UAT** | **LOCAL_VERIFIED** | `scripts/p12_decommission_readiness.py`, `docs/release/migration-contract.json`, 0 hard blockers |
| **PR-CUR-005** | 强类型 Route Registry、UI Primitives 与 Dialog/Drawer 焦点规范 | **CODE_COMPLETE** | **TESTS_PASSED** | **PENDING_UAT** | **LOCAL_VERIFIED** | `routeRegistry.ts`, `primitives.tsx`, `primitives.css`, `primitives.test.tsx`, `routeRegistry.test.ts` |
| **PR-CUR-006** | 打通 Story → AI Breakdown 请求契约与草稿审查 | **CODE_COMPLETE** | **TESTS_PASSED** | **PENDING_UAT** | **LOCAL_VERIFIED** | `breakdownClient.ts`, `ScriptImportPanel.tsx`, `EpisodePlanPage.tsx`, `ScriptImportPanel.test.tsx`, `test_ai_breakdown_drafts.py` |
| **PR-CUR-007** | Character Identity Pack 版本化、三视图绑定与不可变快照加固 | **CODE_COMPLETE** | **TESTS_PASSED** | **PENDING_UAT** | **LOCAL_VERIFIED** | `0050_character_identity_packs.py`, `0054_character_identity_pack_hardening.py`, `character_identity_packs.py`, `CharacterIdentityPackPanel.tsx`, `test_character_identity_packs.py` |
| **PR-CUR-008** | Production Settings 与系统页面 (Models/Diagnostics/Ops) 拆分 | **CODE_COMPLETE** | **TESTS_PASSED** | **PENDING_UAT** | **LOCAL_VERIFIED** | `ProductionSettingsPage.tsx`, `ModelsPage.tsx`, `DiagnosticsPage.tsx`, `ProjectOperationsPage.tsx`, `router.tsx` |
| **PR-CUR-009** | 创作工作区单阶段任务化 (009A～009G) | **CODE_COMPLETE** | **TESTS_PASSED** | **PENDING_UAT** | **LOCAL_VERIFIED** | Story/Plan/Review/Audio/Timeline/Delivery/Run 7大工作区任务化，`StoryboardBatchWorkbench.tsx`, `GenerationWorkbench.tsx` |
| **PR-CUR-010** | 补齐 Production DAG 前半链路编排与 Action 调度 | **CODE_COMPLETE** | **TESTS_PASSED** | **PENDING_UAT** | **LOCAL_VERIFIED** | `episode_production_runs.py`, `episode_front_half_actions.py`, `test_episode_worker_actions.py` |
| **PR-CUR-011** | Local Supervisor、Worker Sessions 与 Storage Operations 原子落盘 | **CODE_COMPLETE** | **TESTS_PASSED** | **PENDING_UAT** | **LOCAL_VERIFIED** | `0051_worker_sessions.py`, `0052_storage_operations.py`, `worker.py`, `storage_operations.py`, `test_prcur011_worker_storage.py` |
| **PR-CUR-012** | Connected Edition 云端扩展与权限隔离 | **DEFERRED** | **N/A** | **DEFERRED** | **DEFERRED** | 默认 LOCAL_ONLY 隔离；无用户显式云端授权前保持延后与离线安全边界 |

---

## 2. 逐项技术详情与验收条款

### PR-CUR-001：修复 Episode Plan 数据契约与错误边界
- **核心需求**：彻底修复 Episode Plan 相同 query key 在不同组件中因不同响应结构导致的渲染崩溃；引入全局与局部 `ErrorBoundary`；统一 query key 工厂。
- **已修改文件**：
  - `apps/web/src/query/queryKeys.ts`
  - `apps/web/src/features/projects/AIDraftReviewPanel.tsx`
  - `apps/web/src/features/projects/SelectedBeatReplanPanel.tsx`
  - `apps/web/src/components/ui/ErrorBoundary.tsx`
  - `apps/web/src/pages/EpisodePlanPage.tsx`
- **自动化验证**：
  - `pnpm vitest run src/features/episode-plan-v2/` & `src/pages/EpisodePlanPage.test.tsx` -> PASS
- **回滚方式**：回滚对应前端 queryKey 与组件变更，无需数据库回滚。

### PR-CUR-002：修复 Director 镜头错选与横向溢出
- **核心需求**：修复从分镜列表/URL 非第一镜（如 Shot 3）进入 Director Desk 时误重置为第一镜的问题；修复 900/1024/1280/1440/1920 视口下的 CSS 横向滚动溢出；确保右侧 Inspector 在所有分辨率下可达。
- **已修改文件**：
  - `apps/web/src/pages/DirectorDeskPage.tsx`
  - `apps/web/src/pages/GenerationPage.tsx`
  - `apps/web/src/features/generation/GenerationWorkbench.tsx`
  - `apps/web/src/features/director-v2/director-desk.css`
  - `apps/web/src/features/generation/generation-workbench.css`
- **自动化验证**：
  - `pnpm vitest run src/pages/DirectorDeskPage.test.tsx src/pages/GenerationPage.test.tsx src/features/generation/GenerationWorkbench.test.tsx` -> PASS

### PR-CUR-003：统一 Canonical Capability 标准化与共享 Resolver
- **核心需求**：建立不可变的权威能力枚举（LLM/IMAGE/VIDEO/AUDIO/POST/QC），支持全量历史别名显式归一化；在 Alembic 中提供 0049 和 0053 自动修复；前后端共用解析器。
- **已修改文件**：
  - `apps/api/local_drama/domain/capabilities.py`
  - `apps/api/alembic/versions/0049_canonical_capabilities.py`
  - `apps/api/alembic/versions/0053_canonical_capability_repair.py`
  - `apps/api/local_drama/application/queries/generation_preferences.py`
  - `apps/web/src/features/preferences-v2/canonicalCapabilities.ts`
- **自动化验证**：
  - `python -m pytest tests/test_canonical_capability_resolver.py tests/test_canonical_capability_writes.py tests/test_migration_0049.py tests/test_migration_0053.py` -> PASS

### PR-CUR-004：修正 P12 退役脚本与证据链契约
- **核心需求**：修正 `scripts/p12_decommission_readiness.py` 中的路由扫描与证据判定；确保迁移契约 `migration-contract.json` 覆盖全部 Alembic head；生成真实的证据文件。
- **已修改文件**：
  - `scripts/p12_decommission_readiness.py`
  - `docs/release/migration-contract.json`
  - `apps/api/tests/test_release_migration_contract.py`
- **自动化验证**：
  - `python scripts/p12_decommission_readiness.py` -> PASS (0 hard blockers)
  - `python -m pytest tests/test_release_migration_contract.py` -> PASS

### PR-CUR-005：强类型 Route Registry 与 UI Primitives
- **核心需求**：建立 22 条 V2 主路由的强类型 Registry；完善 UI Primitives（Button, Tabs, Dialog, Drawer, Badge, Banner）；实现 Portal 挂载、焦点陷阱、Escape 键层级响应与滚动锁定。
- **已修改文件**：
  - `apps/web/src/app/routeRegistry.ts`
  - `apps/web/src/components/ui/primitives.tsx`
  - `apps/web/src/components/ui/primitives.css`
  - `apps/web/src/components/ui/Drawer.tsx`
  - `apps/web/src/components/ui/Dialog.tsx`
- **自动化验证**：
  - `pnpm vitest run src/app/routeRegistry.test.ts src/components/ui/` -> PASS

### PR-CUR-006：打通 Story → AI Breakdown 请求契约
- **核心需求**：实现剧本分段到分镜草稿的真实 AI Breakdown 调用契约；提供非破坏性草稿审查与应用流程；保留分镜谱系与版本一致性。
- **已修改文件**：
  - `apps/web/src/features/projects/breakdownClient.ts`
  - `apps/web/src/features/projects/ScriptImportPanel.tsx`
  - `apps/web/src/features/projects/AIDraftReviewPanel.tsx`
  - `apps/api/local_drama/application/breakdown_apply.py`
- **自动化验证**：
  - `pnpm vitest run src/features/projects/ScriptImportPanel.test.tsx` -> PASS
  - `python -m pytest tests/test_ai_breakdown_drafts.py tests/test_breakdown_apply.py` -> PASS

### PR-CUR-007：Character Identity Pack 版本化与加固
- **核心需求**：实现角色身份包（正面、侧面、背面三视图与派生表情/九宫格），支持明确的人工审核通过流程与版本不可变性；生成任务冻结身份包快照，当主包被新批准版本覆盖时精确报告 STALE 状态。
- **已修改文件**：
  - `apps/api/alembic/versions/0050_character_identity_packs.py`
  - `apps/api/alembic/versions/0054_character_identity_pack_hardening.py`
  - `apps/api/local_drama/application/character_identity_packs.py`
  - `apps/web/src/features/asset-bible-v2/CharacterIdentityPackPanel.tsx`
- **自动化验证**：
  - `python -m pytest tests/test_character_identity_packs.py tests/test_generation_variants.py` -> PASS (41 tests passed)
  - `pnpm vitest run src/features/asset-bible-v2/CharacterIdentityPackPanel.test.tsx` -> PASS

### PR-CUR-008：Production Settings 与系统页面拆分
- **核心需求**：将原杂合面板拆分为高内聚的独立路由与页面：`ProductionSettingsPage` (项目/分集生成参数)、`ModelsPage` (本地模型与权重)、`DiagnosticsPage` (系统诊断与环境日志)、`ProjectOperationsPage` (项目运维与包迁移)。
- **已修改文件**：
  - `apps/web/src/pages/ProductionSettingsPage.tsx`
  - `apps/web/src/pages/ModelsPage.tsx`
  - `apps/web/src/pages/DiagnosticsPage.tsx`
  - `apps/web/src/pages/ProjectOperationsPage.tsx`
  - `apps/web/src/app/router.tsx`
- **自动化验证**：
  - `pnpm vitest run src/pages/ProductionSettingsPage.test.tsx src/pages/ModelsPage.test.tsx src/pages/ProjectOperationsPage.test.tsx` -> PASS

### PR-CUR-009：创作工作区单阶段任务化 (009A～009G)
- **核心需求**：对 Story (009A)、Plan (009B)、Review (009C)、Audio (009D)、Timeline (009E)、Delivery (009F)、Run (009G) 进行任务化单阶段重构；URL 保持 `?step=` 或 hash 状态；列表采用 25 项分页并在内部有界区域滚动，详情与批量修改进入右侧抽屉；默认 1440x900 视口下无无限页面拉长。
- **已修改文件**：
  - `apps/web/src/pages/StoryWorkspacePage.tsx`
  - `apps/web/src/pages/EpisodePlanPage.tsx`
  - `apps/web/src/pages/EpisodeReviewPage.tsx`
  - `apps/web/src/pages/AudioPage.tsx`
  - `apps/web/src/pages/TimelinePage.tsx`
  - `apps/web/src/pages/DeliveryPage.tsx`
  - `apps/web/src/pages/EpisodeRunPage.tsx`
  - `apps/web/src/features/projects/StoryboardBatchWorkbench.tsx`
  - `apps/web/src/features/generation/GenerationWorkbench.tsx`
- **自动化验证**：
  - 全量 Web Vitest 104 files / 345 tests 全部通过。
  - `pnpm build` -> PASS (bundle budget PASS: 40 chunks)。

### PR-CUR-010：补齐 Production DAG 前半链路编排
- **核心需求**：在 `EpisodeProductionRunService` 与 `EpisodeFrontHalfActionService` 中打通故事解析、剧本拆解、资产身份提取、资产补全、分集规划、镜头关键帧 6 大前半链路 action；支持 `front_half_only` 模式与 HITL 断点。
- **已修改文件**：
  - `apps/api/local_drama/application/episode_production_runs.py`
  - `apps/api/local_drama/application/episode_front_half_actions.py`
  - `apps/api/local_drama/api/routes/episode_production_runs.py`
- **自动化验证**：
  - `python -m pytest tests/test_episode_worker_actions.py tests/test_episode_production_runs.py tests/test_episode_hitl_checkpoints.py` -> PASS

### PR-CUR-011：Local Supervisor、Worker Sessions 与 Storage Operations
- **核心需求**：引入持久化 `worker_sessions` 与心跳治理；引入 `storage_operations` 两阶段原子落盘与检疫机制；实现失败重试与超时退避。
- **已修改文件**：
  - `apps/api/alembic/versions/0051_worker_sessions.py`
  - `apps/api/alembic/versions/0052_storage_operations.py`
  - `apps/api/local_drama/application/worker.py`
  - `apps/api/local_drama/application/storage_operations.py`
- **自动化验证**：
  - `python -m pytest tests/test_prcur011_worker_storage.py` -> PASS (14 tests passed)

### PR-CUR-012：Connected Edition (云端扩展)
- **状态**：**DEFERRED**（延后待授权）
- **说明**：系统默认以 `LOCAL_ONLY` 模式运行，不发起非本地网络请求。待用户明确授权云端接入与隐私配置后再行激活。

---

## 3. 自动化门禁测试汇总报告

### 3.1 前端测试门禁 (apps/web)
- **TypeScript 静态检查**：`pnpm tsc --noEmit` -> **0 ERRORS**
- **生产打包构建**：`pnpm build` -> **0 ERRORS** (Bundle Budget PASS, 40 Chunks)
- **Vitest 测试套件**：`pnpm vitest run`
  - **测试文件总数**：104 个文件
  - **测试用例总数**：345 条用例
  - **通过率**：**100% (345 / 345 PASSED)**
  - **总耗时**：16.06s

### 3.2 后端测试门禁 (apps/api)
- **Pytest 测试套件**：`python -m pytest -m "not comfyui"`
  - **测试用例总数**：562+ 条用例
  - **通过率**：**100% (全部通过，0 FAILURES)**
- **Alembic 迁移与架构事实一致性**：
  - `scripts/refactor_invariants.py` -> **PASS (21 PASSED, 0 FAILED)**
  - `tests/test_migration.py` & `tests/test_migration_0042.py` -> **PASS**
  - `tests/test_release_migration_contract.py` -> **PASS**
  - `tests/test_verify_release.py` -> **PASS**
  - `scripts/p12_decommission_readiness.py` -> **PASS (0 HARD BLOCKERS)**

---

## 4. 迁移与回滚操作手册

### 4.1 数据库升级与降级
- **全新升级**：`.venv\Scripts\python.exe -m alembic upgrade head`（升级至 `0054_character_identity_pack_hardening`）
- **版本降级**：`.venv\Scripts\python.exe -m alembic downgrade 0048_asset_proposals`（支持单步或批量回滚）
- **完整恢复**：从 `data/backups/` 恢复已验证快照。

### 4.2 离线发布检查命令
```bash
# 1. 验证 Alembic 当前版本与 Schema 一致性
.venv\Scripts\python.exe scripts/refactor_invariants.py

# 2. 验证发布迁移契约与只读检查
.venv\Scripts\python.exe -m pytest tests/test_release_migration_contract.py tests/test_verify_release.py

# 3. 验证 Web 前端构建与测试
cd apps/web && pnpm tsc --noEmit && pnpm vitest run && pnpm build
```

---

## 5. 待推进事项与演进路线

1. **真实浏览器 UAT (Playwright)**：在具备完整本地测试浏览器环境下运行 22 路由端到端只读与交互 UAT 测试，形成最终生产签发证据。
2. **生产环境部署验证**：在目标生产机器上验证与本地 ComfyUI 实例及本地模型的物理对接。
3. **PR-CUR-012 云连接授权**：当用户需要云端多端协作时，接入云端同步协议。
