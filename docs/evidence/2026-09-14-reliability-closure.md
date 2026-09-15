# 可靠性收尾证据（2026-09-14）

基线：`791b3c48aa66f15b23cf024ffd1d29c943a7055d`（`git rev-parse HEAD` 一致，`git diff --stat` 仅本轮改动，无其他未提交代码被覆盖，未执行 reset/clean，未推送远程）。

本文是工程证据与诚实状态表，不是“全部通过”声明。参考协调器检查通过不计入仓库集成测试数量。

## 1. 任务状态表

| 任务 | 状态 | 证据 |
|---|---|---|
| R01 草稿保存与导航守卫 | CLOSED（定向验证通过） | `draftCoordinator.test.ts` 12/12；`AppShell.test.tsx` 8/8；`SystemPages.test.tsx` 15/15；`DirectorIntentEditor.drafts.test.tsx` 10/10；`tsc --noEmit` PASS |
| R02 资产批次提交与恢复 | CLOSED（定向验证通过） | 前端 `ProjectAssetImageWorkbench.test.tsx` 2/2、`recovery.test.tsx` 5/5、`assetImageCommands.test.ts` 11/11；后端 `test_asset_image_idempotency.py` 5/5、`test_asset_bible.py` 29/29；`tsc` PASS |
| R03 当前集任务重试反馈 | CLOSED（定向验证通过） | `EpisodeTaskDrawer.test.tsx` 1/1、`EpisodeTaskDrawer.retry.test.tsx` 5/5；`tsc` PASS |
| R04 未定位 API 失败收尾 | NOT_VERIFIED（原两次失败）+ 本轮改动域定向 PASS | `.pytest_cache/v/cache/lastfailed` 不存在；原全量 60% 中止无 JUnit/traceback，无法核对 nodeid/SHA/环境；未盲跑全量生产链；本轮 R02 后端域 34/34 PASS，前端改动域 54/54 PASS（见 §3），`git diff --check` PASS，`ruff` PASS |
| R05 真实整集页面验收 | BLOCKED（需唯一测试者经可见页面完成） | 本轮未操作浏览器、未调用业务 API、未写库批准、未生成新媒体；沿用 `docs/evidence/luna-ui-full-episode-2026-09-14.md` 现有 9 镜/资产进展，不按旧记录重生成，不以短片代表整集 |

## 2. 改动清单

新增建议路径（本方案建议新增，非仓库既有）：

- `apps/web/src/features/drafts/draftRegistry.ts`：同步可观察注册集合。clean 保留挂载、注销分离、token 句柄、旧句柄晚到忽略、同 token 版本不倒退、`getSnapshot` 不可变快照。
- `apps/web/src/features/drafts/settleDirtyDrafts.ts`：有限协调一遍遍历。保存中新增不偷存、失败不回滚已成功但报告已完成/待处理、回执版本校验、缺失回调/旧 void 不放行（旧 boolean `true` 仅作迁移桥接代清，且仅当身份未变）。
- `apps/web/src/features/asset-bible-v2/assetImageCommands.ts`：冻结命令、sessionStorage 待确认记录、错误分类（`REJECTED`/`UNKNOWN`/`CONFLICT`）。

修改现有路径：

- `apps/web/src/features/drafts/draftGuard.ts`（`notifyDraftDirty`）：先同步写 registry，再发兼容事件。
- `apps/web/src/layouts/AppShell.tsx`（`finishBlockedNavigation`、`blocker`、`Dialog`）：`useSyncExternalStore` 派生 dirty、同步单飞锁、捕获被阻止导航意图、调用 `settleDirtyDrafts`、全集合重查、同一同步段 `proceed`、不再 `setDraftDirty(false)` 伪造放行。
- `apps/web/src/features/director-v2/DirectorIntentEditor.tsx`（`save`、`discardCurrentDraft`、`applyDraft`、`saveAndReady`）：`expectedVersion` 校验、冻结当次载荷/版本/revision、写成功仅当仍对应提交版本才清 dirty、刷新分离为 `refreshWarning`、409 保留编辑、`Ctrl+S` 传版本、移除站内链接 confirm（`beforeunload` 仍由 AppShell 统一承担）。
- `apps/web/src/features/director-v2/ShotGenerationInspector.tsx`（`saveMentionDraft`、`discardMentionDraft`、`applyMentionEdit`）：同上版本化合同；服务端基线刷新不静默覆盖仍脏引用（无本地缓冲时用内存保留，有缓冲的意图编辑器用显式恢复候选保持可追溯）。
- `apps/web/src/features/asset-bible-v2/assetImageBatchClient.ts`（`findAssetImageBatchByCommandKey`）：`?idempotency_key=` 精确查询，不受最近 5 条限制。
- `apps/web/src/features/asset-bible-v2/ProjectAssetImageWorkbench.tsx`（`generate`、`recoverUnknown`、`runWithRefresh`）：冻结命令先存后发、回执按 `projectId+key` 存取、UNKNOWN 全局锁新意图、精确核对（不用 `plan_hash` 认领）、`CONFLICT` 不换键、`onChanged` 失败分离为刷新提示、`busy` 必释放。
- `apps/web/src/layouts/EpisodeTaskDrawer.tsx`（新增 `EpisodeJobRow`、`retryMutation`）：行顶层 `useMutation`、`mutationKey ["job-retry", project, episode, job]`、`retry: 0`、同步 ref 防双击、`mutateAsync` 捕获、`fetchQuery`+`invalidate` 分离刷新并检查结果、受理/未知/拒绝三态行内展示、切集 key 隔离、unmount 守卫。
- `apps/api/local_drama/application/asset_image_generation.py`（`asset_batch_request_identity`、`submit`、`list_batches`）：复用 `command_idempotencies` 的 `scope=asset-image-batch:submit:{project_id}` + 同一业务幂等键 + 载荷指纹；受理前先查身份（plan 新鲜度之后不再卡恢复）；短写事务内重查、MISSING_ONLY 在途重叠保护、预留 batch/items/回执；唯一竞争仅对确认键回读比对；仅创建方分派 Intent/Variant/Job；模型/GPU 在事务外；历史无指纹键返回兼容性冲突（`existing_batch_id`），不伪造；`list_batches(idempotency_key=)` 精确 0/1 条。
- `apps/api/local_drama/api/routes/asset_bible.py`（`list_asset_image_generation_batches`）：兼容新增 `idempotency_key` 查询参数。
- `apps/api/local_drama/application/errors.py`：`ASSET_IMAGE_BATCH_ALREADY_IN_PROGRESS` 明确映射 409（复用统一 `DomainRuleError` 处理器）。
- `docs/openapi/openapi.json`：经 `scripts/generate_client.py`（`.venv\Scripts\python.exe`）重生成，`idempotency_key` 已进入 `listAssetImageGenerationBatches` 参数；`apps/web/src/generated/api.ts` 模板无变化（资产批次走 `assetImageBatchClient` typed helper，无需手改生成结果）。

未新增迁移：复用 `0091` 的 `uq_asset_image_batch_idempotency` 数据库约束 + `command_idempotencies` 主键（scope,key），未重写旧迁移，未清理历史重复行。

## 3. 定向测试结果

代码 SHA：`791b3c48aa66f15b23cf024ffd1d29c943a7055d`（基线未变，改动未提交）。

后端（`.venv\Scripts\python.exe -m pytest --tb=no -p no:warnings`）：

- `apps/api/tests/test_asset_image_idempotency.py` 5 passed（identity 稳定/同键同指纹零新增回放/同键異参零副作用冲突/在途重叠单批次/精确键查询+路由）。
- `apps/api/tests/test_asset_bible.py` 29 passed（回归）。
- `ruff check` 上述 4 文件 PASS（修复 `B904` 后）。
- `git diff --check` PASS。

前端（`apps/web`，`npm test -- <paths>`，`vitest run`）：

- 改动域 8 文件 54 passed：`draftCoordinator` 12、`AppShell` 8、`DirectorIntentEditor.drafts` 10、`Workbench` 2、`Workbench.recovery` 5、`assetImageCommands` 11、`EpisodeTaskDrawer` 1、`EpisodeTaskDrawer.retry` 5。
- `npx tsc --noEmit` PASS（退出码 0）。
- `npm run build`：`tsc` PASS、`vite build` PASS（493→496 modules），`check-bundle-budget.mjs` FAIL——基线与本轮均超 500 KiB（基线 `index-D4bAOJ5B.js` 511.5 KiB，本轮 `index-eYnOVcqo.js` 508.8 KiB，本轮略小）。未改宽预算，属预存发布判断项，不归因于本轮回退。

R04 原失败定位：

- `git log` 证实 `round2-final-uat-and-benchmark-closure-2026-09-14.md` §回归状态所记“API 全量约 60% 2 失败后中止、无 JUnit/traceback”；本地 `.pytest_cache` 不存在，`docs/evidence/round2*.xml` 为聚焦/ corrective 产物，非那次中止全量的 JUnit。
- 按方案未执行 `--lf` 全量（无可信 cache，`--last-failed-no-failures=none` 会退化），未开 GPU/真实链路复跑。故 R04 保持 NOT_VERIFIED。

## 4. API 合同影响

- 有：只读列表兼容新增可选 `idempotency_key`（不带时行为不变）；`submit` 错误新增 `ASSET_IMAGE_BATCH_ALREADY_IN_PROGRESS`（409）与历史键 `IDEMPOTENCY_PAYLOAD_MISMATCH+existing_batch_id`（409，复用既有码）。
- OpenAPI 已重生成并提交为工作区改动；`generated/api.ts` 无需手改；无迁移；`response_json` 仅存 `{"batch_id"}` 定位，回放经 `get_batch` 读权威进度。

## 5. 页面验收结果（R05）

- 未执行：真实整集 480p/约 120 秒交付仍需 Luna 经可见页面完成冻结→渲染→验证→批准→交付/下载全路径，含刷新后交付仍在、第二集未污染检查。
- 约束遵守：未用 curl/脚本/控制台调业务 API、未写库、未拼交付、未重启/强杀在途 GPU（共享 `:3210/:8188` 未动）。

## 6. 剩余风险

- R04 原 2 失败未定位：仍为发布阻断项，需找到当时控制台/JUnit/CI 日志或由测试者在隔离环境重跑聚焦用例。
- 模型质量：镜头 5/6 类审美拒绝属创作选择，不以重抽冒充修复。
- 部署边界：可信局域网无登录模式不作公网多租户承诺；`dist` 因 `emptyOutDir:false` 堆积旧 chunk，预算脚本会连旧超限 chunk 一并报错，发布前需按已有排空/恢复机制清理或归档，而非改宽预算。
- 同 Shot 双编辑器同 revision 竞争：第二写以 409 停止并保留（意图侧经本地缓冲显式恢复，引用侧内存保留），需用户在页面内按冲突提示人工合并，禁止无条件合并覆盖。
