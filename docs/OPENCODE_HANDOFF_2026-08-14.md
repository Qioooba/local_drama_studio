# LocalDramaStudio → OpenCode 夜间开发交接（2026-08-14）

## 1. 工作区与权威文档

- 主工作区：`F:\AI_Projects\h3\local_drama_studio`
- 蓝图根目录：`F:\AI_Projects\h3\LocalDramaStudio_Blueprint_v2`
- 阶段门禁权威：`F:\AI_Projects\h3\LocalDramaStudio_Blueprint_v2\09_实施WBS_阶段门禁与评审.md`
- 当前总交接：`F:\AI_Projects\h3\local_drama_studio\docs\HANDOFF_2026-08-13.md`
- 追踪矩阵：`F:\AI_Projects\h3\local_drama_studio\docs\requirements-traceability.md`
- 设计系统：`F:\AI_Projects\h3\local_drama_studio\design-system\localdramastudio\MASTER.md`
- Figma 状态：`F:\AI_Projects\h3\local_drama_studio\design-system\localdramastudio\figma-state.json`
- G6 退出报告：`F:\AI_Projects\h3\local_drama_studio\docs\phase_reports\G6_exit_report.md`
- 本文件是 OpenCode 当前夜间接管入口；开始前仍须完整阅读上列文档。

项目目录本身当前没有 `.git`，不要声称已有 commit/diff。已有文件均视为用户工作，禁止破坏性 reset/覆盖。

## 2. 不可变目标与约束

- 严格按蓝图 09 的 G0→G10 顺序。G0—G6 已有退出证据；当前只推进 G7。G8/G9 只有 progress，禁止越级宣告；G10 未完成；不实施 G11。
- 只有 86/86 FR、14/14 NFR、85 个主干测试、完整本地 UAT、无 P0/P1、发布证据、安装/升级/回滚、SBOM、go/no-go 全部完成，才能标记长期目标完成。
- `LOCAL_ONLY`，运行期零公网请求；无云 Provider、API Key、计费或多租户。
- 禁止 Mock、静态假页面或表单校验冒充真实模型/媒体能力。
- ComfyUI 当前未运行（8188 无监听）。只有需要真实运行证据时才按 `scripts/comfy.ps1` 的受控 ephemeral/H3-only 方式使用；一任务一 Worker，结束后停止并验证显存/进程回收。不要启动 Manager 或引入公网请求。
- 所有人工图片查看仅使用最大边不超过 720px、低码率 JPEG/WebP；审核 UI 默认 `size=small`，不加载原图。
- Figma 文件 `ZtpUS9N1Of3vRYIFQDSJqh`；已有 Foundations/Components/Generation Workbench 页面。Starter MCP 今日仍返回调用上限，未写入、未重复 foundations。额度可用后必须先 inspect，禁止覆盖已有变量/基础页。

## 3. 当前运行状态

- API：`http://127.0.0.1:3210`，PID 27092；启动/停止用 `scripts/start.ps1 -Port 3210`、`scripts/stop.ps1`。
- Web：`http://127.0.0.1:5173`，PID 8688。
- SQLite 权威库：`data/local_drama.sqlite3`（WAL）。
- ComfyUI：未运行，8188 无监听。
- 当前生产项目：`e5eaa01d-d39a-4a63-acbf-026da30b46e7`。

## 4. 阶段真值

- G0—G5：PASS，有 `docs/phase_reports/G0_exit_report.md` 至 `G5_exit_report.md`。
- G6：真实 PASS，详见 `G6_exit_report.md`。4 个同源真实 I2V proxy、人工 winner、formal 生成、机器 QC、人工 review 全闭环。
- G7：`IN_PROGRESS`，绝非 PASS。
- G8/G9：仅 progress；不可因已有代码越级退出。
- G10：未完成。

G6 关键正式证据：

- 批准 KEYFRAME `0d389e44-0fc3-47e2-b492-f7e3501ccf0c`，approval `c092a9f5-20fc-4165-bd4b-19ef8487d7cc`。
- proxy winner MediaVersion `2b5e49e4-09e0-4dc0-b208-29b33670589f`。
- formal Profile `2147a504-e7ae-4756-8e35-77de81c9cbdc`。
- formal MediaVersion `bf6f2151-a104-48d4-9c85-b89a7bad68c9`，SHA `68cbc0b06585adfe03611a56a3cf271690f11c36db05ba0c7b64e93d125a3ed8`。
- machine check `4a04f522-9b2b-4d73-bae9-0df5ee059d32` PASS；人工 review `c1b3cf70-75db-4787-9be7-b9ab6585fa91` APPROVED。
- 已知真实限制：冻结 prompt 写有 candle/period costume，但源图是现代室内粉衣人物。批准仅确认技术质量/源图连续性，不宣称错误语义目标实现。

## 5. 已完成的 G7 基础

- 新增只读 `GET /api/v1/projects/{project_id}/gates/g7`，明确 `runtime_contacted=false`、`network_contacted=false`、`mutated=false`。
- loopback URL 真实性校验：公网 URL 伪装 `LOOPBACK_HTTP` 会在访问前硬拒绝。
- 生产项目已绑定：
  - ProductionPlanVersion `5a0a2a07-8703-4642-9df5-3bd33ad41970`
  - I2V formal `2147a504...`
  - T2V `988b3fc3...`
  - SCRIPT_BREAKDOWN_LLM `08789ef4...`
  - LOCAL_FILESYSTEM DeliveryTargetVersion `f001acd0-096f-44f1-b466-e0f11212a090`
- G7 readiness 当前仍硬阻塞：`PROFILE_EDITOR_TEST_PUBLISH`、`PROFILE_CAPABILITY_COMPATIBILITY`、`ZERO_PUBLIC_NETWORK_E2E`、`WORKSPACE_ASSET_AUTHORIZATION`、`MODEL_LICENSE_HASH_QUANTIZATION_REPORT`。

## 6. 刚完成但尚需完整回归/生产 UAT 的 Profile 编辑器

新增 migration `0015_g7_profile_contract_editor`：

- `execution_profile_versions.output_contract_json`
- `execution_profile_versions.resource_policy_json`
- `profile_validation_attestations`

新增后端：

- `GET /profile-versions/{id}`
- `POST /profile-versions/{id}:derive-contract`
- `POST /profile-versions/{id}:validate-contract`
- `POST /profile-versions/{id}:publish-contract`
- Published 版本不会原位覆盖；编辑派生 DRAFT。
- contract validation 绑定 SHA-256；只做本地结构校验，不接触 runtime/network。
- 若执行指纹改变，`publish-contract` 返回 `PROFILE_REAL_EVIDENCE_REQUIRED`，禁止用契约校验冒充模型实跑。
- `publish_from_evidence` 刚扩展为支持 DRAFT：要求匹配 contract hash 的 PASS validation，并把 validation attestation ID 冻结进 capability contract；output/resource contract 复制到新 Published 版本。

新增前端“模型与能力”专业配置页：

- 版本列表、不可变 DRAFT 派生、输入契约/参数 Schema/输出契约/资源策略分区。
- 明确可见 label、helper text、字段旁反馈、验证前禁用发布。
- 1440×900、1280×800、1024×768 浏览器只读验收均：零水平溢出、零 console/page error、零失败响应。
- 低清证据：`apps/web/output/playwright/profile-editor-*.webp`（最大 720px，14—18KB）。
- 结构化结果：`apps/web/output/playwright/profile-editor-validation.json`。

注意：最后一次修改了 `profiles.py` 和 `g7_readiness.py` 后，定向测试 9/9 与 Ruff PASS；但尚未再跑 mypy、全 `api:test:safe`、Web/build，也尚未迁移生产 DB 和执行真实 DRAFT→validate→publish-from-evidence UAT。必须先做这些，不能直接宣告 `PROFILE_EDITOR_TEST_PUBLISH` PASS。

## 7. 立即接手顺序

1. 检查 `profiles.py` 最后补丁的 SQL placeholder/字段顺序；跑：
   - `.\.venv\Scripts\python.exe -m mypy apps/api/local_drama`
   - `pnpm api:test:safe`
   - `pnpm web:test`
   - `pnpm web:build`
   - `.\.venv\Scripts\python.exe -m ruff check apps/api`
2. 通过 `scripts/stop.ps1` / `scripts/start.ps1 -Port 3210` 迁移生产 DB 到 0015；确认 `PRAGMA integrity_check=ok`，先做在线备份。
3. 用 formal Profile `2147a504...` 派生一份契约完整的 DRAFT，补：
   - output `{media_kind: VIDEO, container: mp4, codec: h264}`
   - resource `{gpu_heavy_concurrency: 1, worker_policy: ONE_H3_WORKER_ONE_GPU_TASK}`
   - 保持现有 input/parameter/workflow/model/runtime 不变。
4. 本地 validate 应 PASS 且 runtime/network 未接触；然后以既有 formal 成功证据 `020f60ff-b66f-48a9-bb33-2991ba67e359` 或真正与该 workflow 完整匹配的正式媒体执行 `publish-evidence`。务必核对 workflow/Job snapshot 匹配；不匹配就不能复用，必须真实运行新证据。
5. 让 G7 readiness 的 `PROFILE_EDITOR_TEST_PUBLISH` 基于真实 Published + attestation 自动 PASS；不要手改状态。
6. 为前端补 Vitest 交互测试：保存 DRAFT、validation FAIL/PASS、发布禁用、`PROFILE_REAL_EVIDENCE_REQUIRED` 错误说明。
7. 重跑三档 Playwright，实际完成 DRAFT→validate 流程；视觉仍只看 <=720px WebP。
8. 更新 `HANDOFF_2026-08-13.md`、`requirements-traceability.md`，建立 `docs/phase_reports/G7_progress_report.md`。G7 仍不能退出，继续下一个硬阻塞。

## 8. G7 剩余 WBS（按蓝图顺序）

- G7-01/02：Profile editor/test/publish 完整 UAT与真实发布证据（当前正在做）。
- G7-03：整理 loopback OpenAI-compatible、Comfy、CLI、FFmpeg adapter SDK/统一契约。
- G7-04：H3 manifest/script 导入候选且不自动激活（已有基础，补完整 UAT/报告）。
- G7-05：ProductionPlan 绑定矩阵、继承/override/影响分析，而非只有基础绑定。
- G7-06：DeliveryTargetVersion 编辑器和项目显式选择 UI/UAT。
- G7-07：REMOTE transport 禁用逻辑及未来 adapter contract tests。
- G7-08：完整链路公网请求为 0 的真实网络测试；不能只靠静态代码推断。
- G7-09：input contract、seed/determinism、extend/V2V/reference/motion capability schema 与兼容检查。
- G7-10：工作区资产授权、BrandKit、离线模型导入以及 license/hash/量化兼容报告。

只有 G7 退出演示完全满足才建立 `G7_exit_report.md` 并进入 G8。随后严格完成 G8 音频/字幕/时间线/交付，G9 完整业务画布，G10 全量 UAT/安全/性能/发布/安装升级回滚/SBOM/go-no-go。

## 9. 常用验证命令

```powershell
cd F:\AI_Projects\h3\local_drama_studio
pnpm api:test:safe
pnpm web:test
pnpm web:build
.\.venv\Scripts\python.exe -m ruff check apps/api
.\.venv\Scripts\python.exe -m mypy apps/api/local_drama
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/stop.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start.ps1 -Port 3210
```

不要运行公开网络安装/下载命令。Playwright 使用本机现有 Chromium：`C:\Users\Qi\AppData\Local\ms-playwright\chromium-1228\chrome-win64\chrome.exe`。

## 10. 完成口径

持续开发一整夜不等于可以降低证据标准。遇到失败应保留失败历史、修复并回归；不得删除审计、伪造 PASS、用 Mock 替代真实能力，或因为时间耗尽把 G7/G8/G9/G10 标成完成。

## 11. 2026-08-14 继续交接状态

- G10 已完成一次受控隔离升级/恢复演练：从 `0019_g7_model_compatibility_reports` 升级到 `0020_g7_model_license_evidence` 的副本通过完整性检查；独立恢复副本回到 0019，SHA-256 与源备份一致。
- 证据：`docs/evidence/g10/upgrade-rollback-rehearsal-2026-08-14.json`；只读发布审计新增 `UPGRADE_ROLLBACK_REHEARSAL=PASS`。
- 生产库、API、ComfyUI、网络和任务队列均未接触；隔离副本暂留在 `temp/release-rehearsal-20260814_224333` 供审计复核。
- 当前仍真实为：G7 `IN_PROGRESS`（下一硬阻塞 `MODEL_LICENSE_HASH_QUANTIZATION_REPORT`），G8 `IN_PROGRESS`（`THREE_REAL_SHOTS`），G9 `IN_PROGRESS`（`VISIBLE_NODE_PERFORMANCE_UAT`），G10 `IN_PROGRESS`。不得越级或把草稿发布物改为 FINAL。
