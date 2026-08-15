# LocalDramaStudio Codex 交接（2026-08-15）

## 工作区与长期目标

- 工作区：F:\AI_Projects\h3\local_drama_studio
- 蓝图：F:\AI_Projects\h3\LocalDramaStudio_Blueprint_v2
- 当前分支：main
- 严格按蓝图文档 09 的 G0→G10 推进，不实施 G11。
- 永久约束：LOCAL_ONLY、零公网请求、不能用 Mock/fixture 冒充生产真实能力。
- 最终完成标准不变：86/86 FR、14/14 NFR、85 个主干测试、完整本地 UAT、无 P0/P1、发布证据、安装/升级/回滚、SBOM、go/no-go 全部真实完成。
- 用户授权 Codex 执行视觉审核、人工选择和测试；视觉证据仍只能使用不超过 720px 的低码率缩略图。

新任务必须先完整阅读：

- docs/HANDOFF_2026-08-15_CODEX.md
- docs/HANDOFF_2026-08-13.md
- design-system/localdramastudio/MASTER.md
- design-system/localdramastudio/figma-state.json
- docs/ui-ux-audit-2026-08-12.md
- docs/requirements-traceability.md

## 当前真实门禁

- G7：PASS。用户自带模型只保存本机路径/hash/格式/量化，不捆绑、上传或分发权重；许可证缺失仅提示用户责任风险。
- G8：PASS。四类真实本地音轨、字幕、timeline、批准 render、delivery 与篡改检测已满足；用户素材授权记录不再作为平台发布硬阻塞。
- G9：PASS。生产 110 可见节点性能与三视图可访问性证据通过。
- G10：发布审计 PASS；Windows x64 LOCAL_ONLY 本地源码发行版 GO，安装运维、SBOM 与 go/no-go 均为 FINAL。

## 最近已提交批次

- aec48c6 feat: add explicit audio import binding
  - 本地 AUDIO 导入、项目内授权证据 SHA 校验、显式轨道绑定。
- 5ce2ace feat: expose model license evidence import
  - Profiles/Diagnostics 真实项目内模型许可证 JSON 导入入口。
- 027a265 feat: expose governed dialogue actions
  - 创建对白 v1、授权音色、PREVIEW/FORMAL 候选和候选选择。
- 7fae0e5 feat: add dialogue text revision workflow
  - 文本/发音新 revision，expected_revision_no 防并发覆盖。
- 73b38ba fix: enforce tts preview duration
  - PREVIEW 强制为已探测的 3—10 秒 AUDIO。
  - 真实 FFmpeg 2 秒 WAV 被拒绝；4 秒 WAV 通过并冻结 media_duration_ms=4000。

提交 73b38ba 后的完整门禁：

- API：174 passed / 4 Comfy live deselected
- Web：56/56
- production build：PASS
- Ruff：PASS
- mypy：100 source files PASS
- Dialogue/TTS 三档 Playwright：1440×900、1280×800、1024×768，3/3 PASS
- 三档证据：零写入、零公网、零自动原音频、零浏览器错误、零短控件、零横向溢出

## 当前未提交的在途改造（2026-08-15 最新）

目标：为 FR-AUD-001 建立真实 Windows SAPI 本地 TTS Job，不把直接导入音频冒充生成。

当前修改：

- apps/api/local_drama/application/dialogue.py
  - 新增 submit_tts_job。
  - 只允许最新 DialogueTextRevision、同项目 ACTIVE VoiceProfileVersion、Published TTS Profile 和 sapi: 音色引用。
  - 冻结文本/hash、音色授权、情绪、语速、Profile、provider kind 和 network_allowed=false。
  - 创建持久 TTS_GENERATION / CPU Job，支持 Idempotency-Key。
  - 新增 finalize_tts_job：只接受 SUCCEEDED Job 的 VERIFIED TTS_AUDIO artifact，幂等晋升 FORMAL AUDIO 并登记 FORMAL TTSCandidate。
- apps/api/local_drama/application/worker.py
  - 新增真实 TTS_GENERATION worker。
  - 运行前交叉验证 Job 快照、数据库文本/hash、项目、音色、Published Profile 和 LOCAL_ONLY 标记。
  - 使用固定 PowerShell/System.Speech 程序；动态值通过专用环境变量传入，避免命令注入。
  - 输出真实 PCM WAV，再用本机 FFprobe 验证。
- apps/api/local_drama/api/schemas/dialogue.py
  - 新增 TTSJobRequest。
- apps/api/local_drama/api/routes/dialogue.py
  - 新增 POST /dialogue-text-revisions/{id}/tts-jobs。
  - 新增 POST /tts-jobs/{id}:finalize。
- apps/api/tests/test_local_sapi_tts.py
  - 隔离工作区建立 Published SAPI Profile、音色授权、对白和 Job。
  - 真实调用 Microsoft Huihui Desktop 生成 WAV。
  - 验证缺少 Idempotency-Key、Job 幂等、未完成时 finalize 拒绝、真实 worker/artifact、晋升、FORMAL candidate 和 finalize 幂等。
  - 验证非 sapi: voice ref 拒绝、stale 文本拒绝、Job 快照 hash 被篡改后 worker 以 TTS_JOB_SNAPSHOT_INVALID 失败。
- scripts/generate_client.py、apps/web/src/generated/api.ts、docs/openapi/openapi.json
  - 已重新生成 TTSJobRequest、submitTTSJob 和 finalizeTTSJob。
- apps/web/src/features/status/DialogueGovernanceActions.tsx
  - 已新增 TTS_JOB 和 FINALIZE_TTS_JOB 两种显式操作模式。
  - TTS Job 只允许选择带 provider profile 的音色，显式提交情绪/语速，并生成稳定 Idempotency-Key；成功后只报告“已排队”，不伪装 worker 已完成。
  - finalize 必须显式输入真实 TTS Job ID。
- apps/web/src/features/status/DialogueGovernanceActions.test.tsx
  - 已增加 provider-bound TTS Job 提交和显式 finalize 交互测试。
- apps/web/src/app/App.tsx
  - 对白治理变更后同步失效 jobs 查询。

最新聚焦结果：

- pytest apps/api/tests/test_local_sapi_tts.py -q → 1 passed（包含真实 Windows System.Speech/PCM WAV）。
- Web Vitest → 58/58 passed。
- Web production build → PASS。
- 完整 `scripts/check.ps1` 已通过：API 175 passed / 4 Comfy live deselected、Web 58/58、production build、Ruff、mypy 100 source files 全绿。
- TTS_JOB/FINALIZE_TTS_JOB 三档只读 Playwright 已通过：1440×900、1280×800、1024×768，3/3 PASS；零写入、公网、原音频、浏览器错误、短控件或横向溢出。

本机只读探测到的可用音色：

- Microsoft Huihui Desktop（zh-CN）
- Microsoft Huihui（zh-CN）
- Microsoft Kangkang（zh-CN）
- Microsoft Yaoyao（zh-CN）
- Microsoft Zira Desktop（en-US）

重要：测试中的许可证 JSON 仅是隔离测试 fixture，不能复制到正式项目，不能作为生产授权证据，也不能据此关闭 FR-AUD-001。

## 当前工作树

SAPI 批次已提交为 `ec48a61 feat: run real local sapi tts jobs`。源代码、测试、生成客户端、OpenAPI、证据和追踪文档均已提交；仅剩未跟踪的 `test-results/` Playwright 临时输出，不要提交。

## 下一步精确顺序

1. FR-AUD-001 仍不能标 VERIFIED，直到正式项目存在真实授权音色、Published TTS Profile、真实 Job/MediaVersion/candidate、试听、QC、审核与选择闭环。
2. G7 模型许可证首阻塞仍优先；真实证据未出现时可以继续其他实现，但禁止越级宣布 G7/G8/G9 PASS。

## 当前本地进程

交接时监听状态：

- API：127.0.0.1:3210，PID 31772
- Vite：127.0.0.1:5173，PID 31920
- ComfyUI：127.0.0.1:8188，PID 3988

本轮没有向 ComfyUI 提交、轮询、恢复或抢占任务。用户此前允许使用 ComfyUI，但仍必须遵守一任务一 GPU Worker、LOCAL_ONLY、真实 artifact/QC/recovery 证据和有序门禁。

## 新任务启动提示词

继续 LocalDramaStudio 长期目标。工作区：
F:\AI_Projects\h3\local_drama_studio

先完整阅读：
docs\HANDOFF_2026-08-15_CODEX.md
docs\HANDOFF_2026-08-13.md
design-system\localdramastudio\MASTER.md
design-system\localdramastudio\figma-state.json
docs\ui-ux-audit-2026-08-12.md
docs\requirements-traceability.md

真实 Windows SAPI TTS Job 后端、worker、路由、客户端、UI、测试和证据已提交为 `ec48a61`；完整门禁 API 175 passed / 4 deselected、Web 58/58、build/Ruff/mypy 全绿，三档只读 Playwright 3/3 PASS。严格保持 LOCAL_ONLY、零公网、不实施 G11、不用 Mock 冒充真实能力。G7 仍 11/12，唯一首阻塞是真实 H3 模型许可证证据；G8/G9 只算 progress，禁止越级宣告。不要提交 test-results/。
