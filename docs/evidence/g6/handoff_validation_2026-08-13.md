# 2026-08-13 接管验收与 G6 安全进展证据

状态：`G6 IN_PROGRESS / BLOCKED`。本文件不是退出报告，不授权进入 G7。

## 测试与构建

- API 总收集数：68；距 85 个主干测试目标仍差 17，目标未完成。
- 固定非 ComfyUI 安全套件：`pnpm api:test:safe`，真实结果 `64 passed, 4 deselected`（43.33 秒）。4 项 live tests 包括 3 项 workflow/runtime ComfyUI 读取和 1 项 diagnostics loopback probe。
- 安全套件设置 `LOCAL_DRAMA_COMFY_ACCESS=disabled`。`ComfyClient._request` 与 websocket 在任何网络动作前抛 `COMFY_ACCESS_DISABLED`；diagnostics `_probe_loopback` 在调用 `urlopen` 前返回 `{reason: access_disabled}`。两条 fail-fast 路径均用 monkeypatch 证明没有网络调用。默认 `scripts/check.ps1` 走此安全套件；live tests 需要显式 `-IncludeComfyUI`。
- 历史偏差保留：此前一次命令把 diagnostics node id 错写为 `apps/api/tests/...`，pytest 未报 deselect 不匹配并执行了该测试，造成一次只读 loopback ComfyUI 可达性探测；没有提交、queue、重启、恢复或抢占。该次结果不计入“无 Comfy 触碰”证据。
- Web：2 passed。
- 生产构建：PASS；Vite 239 modules，CSS 33.46 kB（gzip 7.04 kB），JS 438.04 kB（gzip 138.17 kB）。
- Ruff：本轮变更范围 PASS。全 `apps/api` 扫描另发现 8 个既有 Alembic import-order 问题（`env.py`、`0001`—`0007`）；本轮未改写历史迁移，因此不宣称全目录 PASS。
- mypy：PASS，72 source files。

## Playwright 三档验收

被测地址仅为 `http://127.0.0.1:5173`；浏览器记录中没有公网 origin。三档均无水平溢出、无 console error、无 console warning、无可见 P0/P1 回归。

| Viewport | 布局观测 | 结果 |
| --- | --- | --- |
| 1440×900 | sidebar 220×832；主编辑区约 759 px + 318 px 双栏 | PASS |
| 1280×800 | sidebar 220×732；主编辑区约 648 px + 280 px 双栏 | PASS |
| 1024×768 | 导航转为 1024×57 横向布局；编辑区 930 px 单栏；能力区在主区之后正常堆叠 | PASS |

视觉证据仅保留低码率 WebP 缩略图，最长边不超过 720 px；原始截图在压缩后已删除：

- `output/playwright/handoff-validation/validation-1440-thumb.webp`：720×450，15,398 bytes，SHA-256 `0267ECED3BB2B132FF09894DD6AD4695177E175B51549A2188584C9FE01F1D72`
- `output/playwright/handoff-validation/validation-1280-thumb.webp`：720×450，15,572 bytes，SHA-256 `FC7492D2B1510024C97BFFE832002F7309DF49ED876D74E146E17A4517464FD1`
- `output/playwright/handoff-validation/validation-1024-thumb.webp`：720×540，16,878 bytes，SHA-256 `9D083234DD0CA6899E67452BA44BFEB3CB3F81B57D08867E87446B02C375D159`

## G6 非 ComfyUI 门禁修复

- `GenerationExperiment :confirm` 不再与 `:expand` 共用无门禁路径。
- DRAFT 计划必须提交当前 64 位 `plan_hash` 才能确认；错误或过期哈希拒绝创建 Job。
- 超过 24 cells 的矩阵必须提交 `confirm_large_matrix=true`；缺少二次确认时保持 DRAFT，Job 数为 0。
- `:expand` 只允许 `CONFIRMED` 计划，继续支持分批懒展开。
- estimate 显式返回计划哈希、确认状态、大矩阵阈值和 `would_create_jobs=false`。
- 新增 service 与 API 契约测试 2 项；OpenAPI 快照与生成 TypeScript client 已同步。
- Variant 谱系继续按不可变 recipe 收紧：父 Variant 必须属于同一 Intent；Exact replay 必须保持 Prompt、Profile、参数、seed 与语义输入完全一致；输入必须引用同项目已注册 MediaVersion；未知 variant type 与重复 role/ordinal 被拒绝。
- 新增 Variant plan/create/list/get/lineage API：preflight 不持久化 Variant、不创建 Job；创建必须携带当前 plan hash；祖先集合和 Profile 允许的语义槽均由服务端推导，客户端不能伪造。
- Variant preflight 只接受 Published Profile，校验 input slot 数量、MediaVersion 项目归属与 integrity，并把 Profile revision、父 recipe hash、媒体 hash 纳入 plan hash。
- manifest 同步不再原位改写 ExecutionProfileVersion：同一 manifest 快照复用既有版本且保持 Published 状态；manifest 变化创建新候选版本。模型 artifact ID 同样按 manifest hash 版本化，旧 Published Profile 的 model bundle 引用保持冻结。
- Published Profile 必须冻结 Published WorkflowVersion；Variant preflight 会确认所用 semantic role 在 workflow node bindings 中存在，且 binding 指向冻结 workflow JSON 中真实、带 inputs 容器的节点。缺 Workflow、未发布 Workflow、缺槽或悬空 node binding 均在持久化前拒绝。
- Experiment 提供 `cancel-remaining`：事务内保留 SUCCEEDED/FAILED/CANCELLED 历史，取消 QUEUED、对活动任务写入 CANCEL_REQUESTED，并把未展开 cells 记为停止范围；实验转为 CANCELLED 后不能继续 expand，取消事件写入 outbox。
- Experiment expand 的 Job、cell、command idempotency、audit 与 outbox 现在共用同一 SQLite 事务。故障注入证明在 Job INSERT 后、cell INSERT 前失败时五类写入全部回滚；实验保持 CONFIRMED，可在故障修复后安全重试展开。
- 新增不可变 `prompts` / `prompt_revisions` schema 与 API；Prompt branch 必须引用父 revision 的直接冻结子 revision，父内容不修改。Variant 的 PromptRevision 必须存在且属于同项目。
- 执行 `recipe_hash` 只包含 Prompt/Profile/参数/seed/语义输入，不混入 variant type、parent 或 branch reason；因此合法 Exact Replay 与父 Variant 执行 recipe hash 相同。
- 服务端派生计划支持 Exact Replay、单 seed Resample 与 1—24 个唯一 seed 的批量计划；批量计划不持久化 Variant、不创建 Job，每个 seed 有独立 plan hash，diff 只包含 `explicit_seed`。
- `PROMPT_BRANCH` 与 `SOURCE_IMAGE_BRANCH` 均由服务端 derive-plan API 生成，不接受客户端自由拼装混合 scope。Prompt branch 只能采用父 PromptRevision 的直接冻结子 revision；Source image branch 必须且只能替换 `FIRST_FRAME ordinal 0`。两者都保持其余执行字段和父 Variant 历史不变。
- First/End preflight 要求真实图片 probe 尺寸；双方比例不兼容时阻塞，不可 probe 时阻塞，不做静默 crop/pad。
- Transition create 拒绝跨项目 shot；validate 校验 anchor 项目、shot owner、source/extracted integrity 与 extracted hash。项目级桥接 anchor 明确返回 WARNING，不冒充 shot-owned 连续性证据。
- `0009_g6_continuity_stale` 实现 TC-VAR-014 离线契约：批准替换 Shot 当前首帧前，`approval-impact` 返回受影响 REQUIRED `END_AT_NEXT_FIRST` 与仍绑定旧首帧的下游 Variant，并冻结为 plan hash。缺确认或过期 hash 时整个审核事务回滚；确认后 transition/Variant stale、review、approved pointer、audit/outbox 同事务提交并保留历史。首次批准与普通 `KEYFRAME` 实验选择不传播。
- `0010_g6_frame_anchor_stale` 扩展上游视频 winner 传播：真实本地 MP4 经 FFmpeg 形成 `LAST_FRAME` Anchor，Transition 引用 anchor，下游 Variant 引用 extracted image；替换批准视频前预览三层影响，确认后 FrameAnchor/REQUIRED Transition/Variant 同事务 stale。测试证明完整 lineage 与原因 `approved_video_winner_changed` 均保留，不触碰源媒体或历史输出。
- TC-VAR-015 不再把导入时 `integrity_status='VERIFIED'` 当作实时证据。`MediaService.verify_content_integrity` 在 Variant preflight 和 FrameAnchor extraction 前对受控本地文件重新计算 SHA-256 与 byte size；实际文件被追加 1 字节后，两条 API 均返回 `SOURCE_INTEGRITY_FAILED`、版本标记 `CORRUPT`，并证明 Variant、Job、FrameAnchor、FRAME_ANCHOR MediaAsset 数量均为 0。
- TC-VAR-015 持续校验进一步覆盖已存在 Anchor：Transition validate 在同一事务中分别重算 source video 与 extracted image 的实际 hash/size。参数化测试在 Anchor 创建后分别篡改两端，均返回 `BLOCKED` + `FRAME_ANCHOR_INTEGRITY_FAILED`，包含准确 `media_role`/`media_version_id`，并持久化 `CORRUPT` 与 Transition `BLOCKED` 状态。
- stale 传播不再只是显示状态：Generation preflight/derive 与 Transition create 分别拒绝 `VARIANT_PARENT_STALE`、`FRAME_ANCHOR_STALE_INPUT`、`FRAME_ANCHOR_STALE`。真实 winner→Anchor→Transition→Variant 测试在传播后尝试三种绕过，均被服务端拒绝，Variant/Transition 数量保持不变。
- preflight→create 竞态已固定为 API 回归：先取得合法 plan hash，再直接篡改受控输入文件，随后携旧 hash 创建。create 重新运行完整 preflight，返回 `SOURCE_INTEGRITY_FAILED`，并证明 MediaVersion=`CORRUPT`、Variant=0、Job=0、`GENERATION_VARIANT_CREATED` audit=0。
- TC-VAR-013 已补 Profile A/B 的离线派生前置契约：`PROFILE_BRANCH` 请求必须显式给出不同的 Published ProfileVersion；生成的 plan/diff 只改变 `profile_version_id`，并逐项证明 PromptRevision、parameter set、seed policy、explicit seed、bindings 不漂移。Candidate 返回 `PROFILE_NOT_PUBLISHED`，同时覆盖 seed 的混合请求返回 `VARIANT_DERIVATION_SCOPE_INVALID`；成功与失败预检前后 Variant/Job 数量一致。尚无两个真实 Profile 的 Job、artifact 与隔离 review，TC-VAR-013 不得标记完整通过。
- TC-VAR-004 新增 `RESUBMIT_PROVIDER_RANDOM` 离线契约和 `0011_g6_provider_random_nonce`：Profile 必须允许无 seed，计划冻结 `PROVIDER_RANDOM` + null explicit seed + 唯一 UUID nonce，两个同父请求 recipe hash 不同，并明确返回 `NON_REPRODUCIBLE`/“不保证重复结果”。迁移、plan→create nonce 保持、Required-seed Profile 拒绝、零 Job 都有测试；真实 new Job/take 尚未执行，因此 TC-VAR-004 仍为 PARTIAL。
- derive-plan 不再是唯一安全边界：直接构造 `VariantPlan` 调用 preflight/create 时，Resample 同时改参数、Profile branch 同时改 seed、Provider random 同时改参数均分别返回 scope error；实际 create 入口也被覆盖，三条拒绝后 Variant/Job 数量不变。这证明 commit-time invariant，但不替代真实 Job/take。
- TC-VAR-009 采用真实 1 fps/3 秒/3 帧 `testsrc2` 视频：FIRST、1.5 秒播放头、LAST 分别解析为 frame index 0/1/2 与 PTS 0/1s/2s，三份注册图片 SHA-256 互异；每个 Anchor 冻结源 MediaVersion SHA、requested/resolved time、resolved frame index 和 extraction method。请求 3 秒（duration 上界）及同时提供 frame index+mode 均 422，Anchor/MediaAsset 计数不变，临时提取目录无残留。该测试用例后端/API 可标 VERIFIED，UI 菜单不在此证据范围。
- TC-VAR-007 API 契约：只支持 FIRST_FRAME 的 Published Profile 收到 END_FRAME 时返回 `PROFILE_CAPABILITY_UNSUPPORTED`；details 包含 `profile_version_id`、`unsupported_roles=['END_FRAME']`、supported roles 与 required capability，统一错误 envelope 透传明确 `suggested_action`，且 Variant/Job 均为 0。
- TC-VAR-003 用真实 SQLite Job/Attempt 状态机验证 retry 与 resample 分离：GenerationVariant 绑定的 `CPU_VARIANT_CONTRACT_TEST` Job 在 max_attempts=1 后进入 FAILED，API `:retry` 只重排同一 Job，下一次 CPU claim 创建 attempt 2 并成功；Variant/Job/媒体 take 数均不增加。该证据不涉及 GPU，也不冒充真实 H3 artifact。
- TC-VAR-001—015 覆盖审计保持保守：当前只把离线不变量和 CPU 队列证据视为相应契约进展；TC-VAR-001/002/011/013 涉及实际 Variant/Job/artifact 或 Profile A/B 审核隔离，在 ComfyUI 释放和真实链闭环前不得宣称完整通过；TC-VAR-010 属 G8 装配，不因代码存在越级宣告。

## 额度保护停点

操作者报告额度剩余约 24%，要求为另一窗口 AI 视频生成至少保留 3%。Codex 无法读取实时百分比，因此在完成上述闭环与回归后主动停止继续扩展，避免越过保留线。目标保持活动；续跑时继续 G6。Prompt/Source derive-plan、两条 continuity stale 传播与 TC-VAR-015 实时 hash 门禁均已完成。Variant+Job 提交事务必须等操作者明确释放 ComfyUI 后再做，因为写入 `QUEUED/GPU_H3` 可能被现有 worker 自动领取；不得以实现为由抢占当前 ComfyUI，也不得越级宣告 G8。

门禁结论：本轮安全开发与响应式验收通过，但 G6 仍缺成功真实 H3 artifact、四条真实 proxy takes、正式 selection/review 与 crash-isolation 证据；不得宣告 PASS。
