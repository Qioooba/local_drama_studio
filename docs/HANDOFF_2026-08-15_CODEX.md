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

- G7：11/12 IN_PROGRESS。
- 唯一首阻塞：MODEL_LICENSE_HASH_QUANTIZATION_REPORT。
- E:\AI\ComfyUI-Models\diffusers\MiniMax-H3 未发现 LICENSE/NOTICE/EULA/README。
- 正式项目 projects/g2_smoke2/00_admin/licenses 当前没有模型许可证证据。
- 禁止根据插件许可证、下载 URL、模型名称或推测生成许可证记录。
- G8：IN_PROGRESS；真实音频授权证据计数为 0，历史四轨均为 LEGACY_INCOMPLETE。
- G9：仅有 progress/evidence，受 G7→G8 顺序阻塞。
- G10：发布工件仍为 DRAFT/NO-GO。

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

## 当前未提交的在途改造

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
  - 验证 Job 幂等、artifact、晋升、FORMAL candidate 和 finalize 幂等。

最新聚焦结果：pytest apps/api/tests/test_local_sapi_tts.py -q → 1 passed。

本机只读探测到的可用音色：

- Microsoft Huihui Desktop（zh-CN）
- Microsoft Huihui（zh-CN）
- Microsoft Kangkang（zh-CN）
- Microsoft Yaoyao（zh-CN）
- Microsoft Zira Desktop（en-US）

重要：测试中的许可证 JSON 仅是隔离测试 fixture，不能复制到正式项目，不能作为生产授权证据，也不能据此关闭 FR-AUD-001。

## 当前工作树

未提交文件：

- M apps/api/local_drama/api/routes/dialogue.py
- M apps/api/local_drama/api/schemas/dialogue.py
- M apps/api/local_drama/application/dialogue.py
- M apps/api/local_drama/application/worker.py
- ?? apps/api/tests/test_local_sapi_tts.py
- ?? test-results/

test-results/ 是 Playwright 临时输出，不要提交。

## 下一步精确顺序

1. 审核当前 SAPI diff并补安全负例：
   - stale 文本拒绝；
   - 无 Published TTS Profile 拒绝；
   - 非 sapi: voice ref 拒绝；
   - Job 快照或数据库 hash 篡改时 worker 失败；
   - finalize 在未成功或无 artifact 时拒绝。
2. 用 FastAPI TestClient 覆盖两个新路由、Idempotency-Key 和错误码。
3. 更新 scripts/generate_client.py 并重新生成 submitTTSJob 和 finalizeTTSJob。
4. 在 DialogueGovernanceActions 增加“提交正式 TTS Job”模式：
   - 显式选择最新文本 revision和绑定 Published Profile 的音色；
   - 显式填写情绪和语速；
   - 生成 Idempotency-Key；
   - 不自动启动 worker，不伪装同步成功。
5. 为 worker/finalize 提供清晰的本地操作或安全 UI；正式候选选择仍必须经过音频机器 QC PASS +人工 APPROVED。
6. 运行完整门禁：
   - & .\scripts\check.ps1
   - Set-Location apps\web
   - pnpm exec playwright test dialogue_tts_governance.spec.ts --reporter=line
7. 更新以下证据：
   - docs/evidence/g10/dialogue-tts-governance-uat-2026-08-15.json
   - docs/requirements-traceability.md
   - docs/HANDOFF_2026-08-13.md
8. 只有完整门禁通过后才提交该批次。
9. FR-AUD-001 仍不能标 VERIFIED，直到正式项目存在真实授权音色、Published TTS Profile、真实 Job/MediaVersion/candidate、试听、QC、审核与选择闭环。
10. G7 模型许可证首阻塞仍优先；真实证据未出现时可以继续其他实现，但禁止越级宣布 G7/G8/G9 PASS。

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

当前有未提交的真实 Windows SAPI TTS Job 改造，聚焦测试 1 passed。先按新交接“下一步精确顺序”补安全/API/UI 测试，跑完整 check.ps1 和三档 Playwright，通过后提交。严格保持 LOCAL_ONLY、零公网、不实施 G11、不用 Mock 冒充真实能力。G7 仍 11/12，唯一首阻塞是真实 H3 模型许可证证据；G8/G9 只算 progress，禁止越级宣告。不要提交 test-results/。
