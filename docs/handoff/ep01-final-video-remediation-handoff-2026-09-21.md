# EP01 最终视频修复与验收交接（2026-09-21）

## 交接状态

任务按用户要求在此停止，**尚未宣布完成**。

当前已经完成代码修复、九个镜头替换、对白/字幕/BGM 重建、正式时间线冻结与一版最终母版渲染；母版已通过逐帧解码、黑帧/冻结检测、响度检测、每 0.5 秒画面抽检以及最终混音对白强制对齐。

当前尚未完成三件事：

1. 没有给新母版提交最终 `APPROVED` 审核决定。
2. 没有用新母版构建并批准新的 delivery package。
3. 没有把最终通过交付的文件复制到 Z 盘（Z 盘旧的 `LocalDramaStudio_EP01_audio_fixed_review.mp4` 只是早期声音修正版，不是本轮最终母版）。

另外，最后一次用户界面验收发现一个需要继续定位的状态显示问题：时间线页面能正确显示 `v6 · FROZEN`、两条对白、纯器乐 BGM、模型原声关闭和两条字幕，但页头同时显示“需要更新/已载入最新上游”，镜头 Inspector 还显示“缺少媒体”。该问题没有影响已生成母版的内容和完整性，但从用户操作角度不能视为无问题，下一位接手者应先查清它是 freshness 指纹误报、预览 URL 问题还是 UI 文案/状态组合问题。

## 项目定位

- 工作区：`F:\AI_Projects\h3\local_drama_studio`
- 项目 ID：`6b6b2481-0a2c-4ee8-8f85-7c21578c681d`
- 分集 ID：`cefd0f7d-38bb-47f7-a95b-da1be4cd6aa4`
- 项目目录：`projects/luna_iab_quan_liu_cheng_yan_shou_20260914`
- 分集：`EPISODE_001 / 停在七点的告别`
- API：`http://127.0.0.1:3217`（无嵌入 worker）和 `http://127.0.0.1:3210`（嵌入 worker）
- Web：`http://127.0.0.1:5174`
- 当前 API 合同：`localdrama.api.2026-08-29.3`

## 当前正式母版

- Render ID：`dcee2cda-dc9a-4b0f-9be0-6da902b160fe`
- Timeline Revision：`e7706584-44ea-47f1-bd1a-fa1ee2005167`（v6，FROZEN）
- 文件：`projects/luna_iab_quan_liu_cheng_yan_shou_20260914/05_timelines/renders/episode-EPISODE_001-4ce83d6fef604a2a82237f0d48215824.mp4`
- SHA-256：`68a81bc235767ee14c3f4cabfcb321749a93974282ae082460668938cf2b56d6`
- 大小：13,529,321 bytes
- 媒体规格：120.000 秒，H.264，854×480，24 fps，2880 帧；AAC 48 kHz stereo，192 kb/s
- 渲染状态：`VERIFIED`
- 音频策略：`source_audio_policy=MUTE`，`render_mode=CURATED_AUDIO`
- 字幕策略：`BOTH`，成片快照 `subtitle_burned_in=true`

不要误用旧 Render：`f2d32de1-1b1b-47ff-aa01-d83447be89cd` 和 `66ee5f67-1b15-447f-a3be-96a00857ef53`。

## 内容组成

### 采用视频

按镜头顺序为：

1. `30774a9b-3a23-450a-9337-4fd5ac3c5415`
2. `79013207-c360-4074-b38a-a83b46681e34`
3. `e3d1e181-9d4e-41f4-9f52-cedec6a57fc8`
4. `3a337482-a95e-45aa-bbc6-54c509f61ea5`
5. `505ac2e7-e645-4243-9148-59298c376354`
6. `ca15d6a2-6142-45c6-a78d-50e2271e4551`
7. `32a09e9c-1d68-4e19-8c25-518065764aba`
8. `1e20f329-dede-4704-b22d-fb8a0e0fde3e`
9. `3d8f740e-4232-4cdf-9e9b-dbd514712e51`

九个镜头均已逐候选检查并采用。最终画面为横屏全幅，无旧版竖屏黑边；人物、怀表、照片、河边与老街背景在当前母版中没有发现额外人物、重复道具、突然换景或模型自带跳切。

### 对白与字幕

只允许两段脚本对白：

- 27.000—29.059 秒：“去河边等我。”；TTS media `1fa8e877-38d3-4664-9fac-4e27a7073d47`
- 63.000—68.243 秒：“有些时间不是为了追回，而是为了好好告别。”；TTS media `028b4b04-643e-4fe2-a32c-5d01ed55014b`

字幕 Revision：`f08c6d9d-67eb-488b-8704-6cc004b1a356`，格式 ASS，两条字幕，烧录和外挂均启用。

从最终 MP4 重新提取对白窗口并使用 Qwen3 ForcedAligner 对齐的结果：

- 第一段逐字完整，成片实际语音约 27.14—28.34 秒，落在字幕区间内。
- 第二段 18 个汉字逐字完整，成片实际语音约 63.14—67.46 秒，落在字幕区间内。

### 背景音乐

- BGM media：`62f180f7-348b-4e97-b0da-60b770c70c3c`
- Binding：`20f70043-a8cd-4d8d-8f1d-2d122a5f2165`
- 文件源：`.codex-tmp/final-video-audit/ep01-safe-instrumental.flac`
- SHA-256：`8e23b9878ad2d1186ddacc25dfc467d7d2bc4d7e886abc3d3f7b9e3563905abd`
- 音量：-18 dB；淡入 2 秒，淡出 3 秒；120 秒；不循环
- 生成脚本：`.codex-tmp/final-video-audit/generate_safe_bgm.py`
- 授权/来源证据：`projects/luna_iab_quan_liu_cheng_yan_shou_20260914/00_admin/licenses/ep01-safe-instrumental-2026-09-21.json`

该音乐为确定性加法合成，仅使用正弦泛音、程序化旋律和延迟，不含录音样本或人声。旧 ACE-Step BGM 已从时间线移除，以消除“音乐里可能还有另一种声音”的主观风险。

## 最终母版验收证据

审查目录：`.codex-tmp/final-video-audit/final`

- `probe.json`：音视频流和时长证据。
- `black-freeze-full.log`：完整 2880 帧解码；无 blackdetect/freezedetect 事件。
- `audio-loudness-silence.log`：综合响度约 -17.9 LUFS，LRA 7.6 LU，true peak -1.8 dBFS；无削波；119.392 秒后约 0.608 秒静音是 BGM 正常淡出。
- `contact-every-2s.png`：全片 2 秒间隔接触表。
- `contact-000-030-every-0.5s.png`
- `contact-030-060-every-0.5s.png`
- `contact-060-090-every-0.5s.png`
- `contact-090-120-every-0.5s.png`
- `subtitle-keyframes.png`：27.5、28.5、63.5、66.0 秒字幕画面；中文字体由 libass 回退到 Microsoft YaHei UI，字形、描边、位置和安全区人工检查通过。
- `dialogue-window-1.wav`、`dialogue-window-2.wav`：从最终 MP4 提取的对白对齐窗口。

审查脚本：`.codex-tmp/final-video-audit/audit-final-video.ps1`。

## 已处理的根因和代码改动

本轮主要问题与修复：

1. **叠人声**：H3/model native audio 改为显式 opt-in，默认关闭；时间线渲染默认静音所有源视频音轨，只混入冻结的对白/BGM/SFX。
2. **有声无字幕**：字幕烧录由目标 `BURN_IN/BOTH` 驱动；交付需要字幕但成片快照无烧录证据时 fail closed。
3. **跨方向画幅**：ProductionSpec 增加跨方向约束；当前镜头按横屏链路重新生成，最终采用 COVER/交付几何 854×480。
4. **生成提示词漂移**：后端和 `ShotGenerationInspector.tsx` 的默认负向提示词统一加入 extra people、bystanders、crowd、duplicate、scene change、hard cut、jump cut、transition、aerial、unrelated 等约束。
5. **视觉采用状态误判**：`post_repository.py` 的 review target `is_adopted` 改为检测当前视觉 working slot，不再只按音频采用判断。
6. **Windows 中文命令行损坏**：`local_ai_subprocess.py` 与 `scripts/local_ai_runtime.py` 对 embedding、VoxCPM、forced alignment 的文本/语言/提示词改用 Base64 ASCII-safe transport；stdout JSON 使用 ASCII-safe 编码，文件仍为 UTF-8。
7. **BGM 语义不确定**：移除旧 AI 音乐，改用结构上不可能含人声的确定性纯器乐生成器。

已知直接相关文件至少包括：

- `apps/api/local_drama/application/timeline.py`
- `apps/api/local_drama/domain/production_spec.py`
- `apps/api/local_drama/domain/shot_prompt.py`
- `apps/api/local_drama/infrastructure/database/post_repository.py`
- `apps/api/local_drama/infrastructure/local_ai_subprocess.py`
- `apps/web/src/features/director-v2/ShotGenerationInspector.tsx`
- `apps/web/src/features/edit-v2/EpisodeEditWorkspace.tsx`
- `scripts/local_ai_runtime.py`
- 对应测试文件

工作树目前很脏，包含大量既有/并行改动和未跟踪文件。**不要 `git reset --hard`、不要批量还原，也不要把所有 dirty files 都归因于本轮任务。** 接手时先用精确 diff 和测试范围拆分。

## 已通过测试

- `apps/api/tests/test_post_v2.py`：通过。
- `apps/web/src/features/director-v2/ShotGenerationInspector.test.tsx`：11 项通过。
- `apps/api/tests/test_local_ai_subprocess.py`：3 项通过。
- 音频、编辑、G8 delivery、timeline、production spec、shot prompt 相关后端组合：42 项通过。
- 更早一轮的 timeline/delivery 前端与后端定向测试、Ruff、前端构建均通过；最终交接前没有重新跑完整仓库测试。

## 接手后的推荐顺序

### 1. 先处理 UI 状态矛盾

打开：

`http://127.0.0.1:5174/projects/6b6b2481-0a2c-4ee8-8f85-7c21578c681d/episodes/cefd0f7d-38bb-47f7-a95b-da1be4cd6aa4/post/edit`

复现要点：

- 页面显示 `v6 · FROZEN`，但标题同时显示“需要更新”。
- stale banner 显示“已载入最新上游”。
- 选中首镜时 Inspector 显示“缺少媒体”，但文件和最终渲染实际存在。

先读取 `GET /api/v2/episodes/{episode_id}/edit-workspace`（以 `apps/web/src/generated/api.ts` 中实际路径为准），比较 `freshness`、`upstream_fingerprint`、`latest_revision.upstream_fingerprint` 与 frozen revision 的 `input_snapshot.timeline_input_snapshot`。重点检查 `DELIVERY_STALE_REFRESH` 写入的 refresh-plan fingerprint 是否和正常编辑 workspace 使用同一规范化算法，以及视觉 working slot 的新 `is_adopted` 修复是否导致指纹组成变化。

不要为了去掉 UI 提示直接再冻结一版；先定位为何最新上游已经装载仍被判 stale。

### 2. 重新跑定向验证

至少覆盖：

- edit workspace freshness / stale refresh 测试
- post review target 采用状态测试
- timeline render / curated audio / subtitle delivery 测试
- frontend `EpisodeEditWorkspace` 与 `ShotGenerationInspector` 测试
- frontend typecheck + build

### 3. 对当前母版做最后人工播放

机器与接触表检查已完成，但交付批准前仍应在页面播放器或本地播放器从头到尾听看一遍，特别确认：

- 除 27 秒和 63 秒附近外没有人声。
- 两段对白听感清晰，BGM 不遮对白。
- 两条字幕起止自然、无遮挡。
- 九个硬切的节奏主观可接受。

### 4. 提交 Render 审核

目标：`EPISODE_RENDER_VERSION / dcee2cda-dc9a-4b0f-9be0-6da902b160fe`

Review target 当前：subject revision 1，template `53ced272-35d9-5c80-a440-765d23348148`，检查项为：

- `decode`
- `timeline_inputs`
- `audio_mix`
- `subtitles`
- `delivery_ready`

确认 UI 状态问题已解释/修复且最终播放通过后，再向 `/api/v2/review-decisions` 提交五项 PASS 和 `APPROVED`。

### 5. 构建新交付包

- 目标版本：`7c70a4ee-f9fb-4690-a12e-ac94971e5b02`
- 字幕模式：`BOTH`
- 使用 `POST /api/v1/delivery-packages`，body 含当前 render ID 和 target version ID。
- 构建后调用 `:verify`，检查 manifest、MP4 和 SRT。
- 再提交 HUMAN 与 PLATFORM review（两者均需真实复核通过后才批准）。

不要复用旧 delivery package `37189e85-9063-45f3-a39f-11e7c1d28e40`，它引用旧 render `f2d32de1-1b1b-47ff-aa01-d83447be89cd`。

### 6. 复制到 Z 盘并做落盘校验

建议最终文件名：

- `Z:\LocalDramaStudio_EP01_FINAL_QC_PASS_2026-09-21.mp4`
- `Z:\LocalDramaStudio_EP01_FINAL_QC_PASS_2026-09-21.srt`
- 可选：同名 `manifest.json` 或一份 SHA-256 文本

复制后重新计算 Z 盘文件 SHA-256，必须与正式 delivery package manifest 一致；再从 Z 盘文件运行 ffprobe 和全片 decode。只有这一步完成后，才可以告诉用户“最终视频已放到 Z 盘”。

## 旧审查文档说明

`docs/evidence/final-video-av-audit-2026-09-21.md` 目前仍主要记录旧交付的失败原因和早期声音修正版。它没有被更新为本轮新母版的最终 PASS 报告。接手者在完成上述剩余步骤后，应将新 Render ID、delivery package ID、Z 盘路径、最终 SHA-256 和最终人工听看结论补入该证据文档。

## 停止点

最后执行的是只读 UI 检查。没有在发现 UI freshness/“缺少媒体”问题后继续修改代码，也没有批准 Render、构建新交付包或写入 Z 盘。任务已按用户要求停止在可安全接手的位置。
