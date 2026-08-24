# Feature Sheet — Review, Audio, Timeline, Delivery, Episode Run, Jobs, Diagnostics, Operations, Canvas

Everything below is read directly from source. Paths are relative to `F:\AI_Projects\h3\local_drama_studio`. Labels are quoted verbatim; `→` notes the action and the API call as bound in `apps/web/src/generated/api.ts` (methods/paths are the equivalent HTTP call). "GET" is used for client calls that pass `undefined` init (see `requestJson` defaulting `init?.method ?? 'GET'` at `apps/web/src/generated/api.ts:205`).

---

## 1. EpisodeReviewPage

- **Route:** `/projects/:projectId/episodes/:episodeId/review` (`routeRegistry.ts:104`, title "本集审核"). Page title line: eyebrow `本集审核`, `<h2>从候选问题到整集批准</h2>` (`apps/web/src/pages/EpisodeReviewPage.tsx:11`).
- **Purpose:** Batch review of unresolved candidate media for one episode — adopt candidates, approve media, and approve the whole episode as three separable, auditable human actions.
- **Guard:** Missing context renders `缺少项目或分集上下文。` (`EpisodeReviewPage.tsx:8`).

### Layout
Header actions: `返回导演台` → `/direct`, `查看时间线` → `/timeline` (`EpisodeReviewPage.tsx:11`). Body is `EpisodeReviewWorkspace` (`apps/web/src/features/episode-review-v2/EpisodeReviewWorkspace.tsx`).

Workspace top summary cards (`EpisodeReviewWorkspace.tsx:152-157`): label/value/detail — `未解决候选` (`allItems.length`), `阻塞问题` (blockedCount, `tone danger` when >0), `机器证据待补` (machineIssueCount), `失效审核` (staleCount).

Tabs (`taskItems`, `EpisodeReviewWorkspace.tsx:145-149`):
- `Shot 审核` (badge = item count)
- `Render 审核` (badge = `可审核` / `未生成`)
- `交付交接` (badge = `${staleCount} 失效` / `待确认`)

> **Note (differs from prompt):** The prompt guessed tabs Shot/Render/交付. Actual labels are `Shot 审核` / `Render 审核` / `交付交接`.

`Shot 审核` tab (`EpisodeReviewWorkspace.tsx:164-188`): eyebrow `Shot · 候选收件箱`, `<h3>比较、决定与证据一次完成</h3>`. Filters (`168-171`): `问题范围` select (`全部未解决` / `仅阻塞` / `机器证据待补` / `失效待重审`), `镜头 / 版本` input (placeholder `搜索镜头编号或版本 ID`), pill `显示 {visible}/{total}`. Button `采用正式版本` opens Drawer `采用正式版本` (`206`).

`Render 审核` tab (`EpisodeReviewWorkspace.tsx:191-196`): eyebrow `Render · 整集`, `<h3>审核冻结时间线产出的 render</h3>`, pill `独立人工 Gate`. Renders `EpisodeReviewPanel`.

`交付交接` tab (`EpisodeReviewWorkspace.tsx:198-203`): eyebrow `Delivery · 交接`, `<h3>审核结论不会自动发布交付包</h3>`; links `检查冻结时间线` → `/timeline`, `进入交付工作区` → `/delivery`.

Drawer `采用正式版本` (`206-211`) hosts `FormalSelectionPanel`.

### Interactive controls (Episode Review)
| Label | Type | Action | API endpoint + method |
|---|---|---|---|
| 返回导演台 | link | nav `/direct` | — |
| 查看时间线 | link | nav `/timeline` | — |
| 采用正式版本 | button | open drawer | — |
| 问题范围 | select | filter items | — |
| 镜头 / 版本 | text input | filter by shot/version | — |
| 选择此候选/置顶参考图/逐帧退/逐帧进/同步播放 etc. | inside `ReviewInboxPanel` | candidate compare/preview | — |
| `预检 {n} 项` | button | batch preflight | `preflightReviewBatch` → POST `/api/v1/reviews/batch:preflight` (api.ts:413) |
| `确认原子选择` | button | commit FORMAL selections | `selectMediaVersion` per item → POST `/api/v1/media-versions/{id}:select` (api.ts:429-430, via `FormalSelectionPanel`) |
| `预检批量审核` / `重新预检` | button | batch preflight | POST `/api/v1/reviews/batch:preflight` (api.ts:413) |
| `提交审核` | button | decision submit | `submitReview` → POST `/api/v1/subjects/MEDIA_VERSION/{id}/reviews` (api.ts:405-406) |
| `运行音频 QC` | button | run machine check (label unconditional in JSX; `machineChecking ? "检查中…"`) | `runMachineCheck` → POST `/api/v1/subjects/MEDIA_VERSION/{id}/machine-checks` (api.ts:401-402) |
| `再次选择为 {selectionType}` | button | re-promote selection | `selectMediaVersion` → POST `/api/v1/media-versions/{id}:select` |
| `重试` | button | refetch failure | — |
| Render tab `提交整集审核` | button | whole-episode render review | `submitEpisodeRenderReview` → POST `/api/v1/subjects/EPISODE_RENDER_VERSION/{id}/reviews` (api.ts:409-410) |

Reads (`EpisodeReviewWorkspace.tsx`): `reviewInbox` (GET `/api/v1/reviews/inbox`), `listReviewTemplates` (GET `/api/v1/review-templates`), `getEpisodeTimelineStatus` (GET `/api/v1/episodes/{id}/timeline-status`), `getReviewContext` (GET `/api/v1/subjects/MEDIA_VERSION/{id}/review-context`), `listFormalSelectionCandidates` (GET `/api/v1/formal-selection...`).

### Happy path (a) review + approve an episode
1. Open `/review`. Shot tab selects first visible item, loads `getReviewContext`.
2. (Optional, per item) run machine check (`运行音频 QC` / runMachineCheck).
3. Fill required check items (`通过`/`不通过`), choose `审核决定` (`批准`/`需要修改`/`拒绝`), enter comment; for `拒绝` a reason is mandatory (`ReviewInboxPanel.tsx:241`, `confirm refused` requires note).
4. `提交审核` → submitReview.
5. `采用正式版本` drawer → tick FORMAL candidates → `预检 {n} 项` → `确认原子选择` (selectMediaVersion) to mark FORMAL selection.
6. Render tab: open render preview, fill checks, `提交整集审核` (submitEpisodeRenderReview).
7. `交付交接` tab → `进入交付工作区` to finish delivery.

### State/status semantics
- `is_stale` → badge `失效待重审` / `STALE`; machine `machine_status !== "PASS"` → `机器证据待补`; `is_blocked` → `阻塞问题` (integrity/QC/stale). `latestRender` from `timelineStatus.renders.latest` controls Render badge; when no render → `未生成`.
- "选择“批准”不会自动提交" (`ReviewInboxPanel.tsx:253`); selection and approval are separate (`muted` line). Changing conclusion requires withdrawing the current review first (`currentApproval` note).
- `currentApproval` disables the checklist and submit (`已批准`).
- Batch group must be same project + same template; not mixed media kinds (`ReviewInboxPanel.tsx:244`).

---

## 2. AudioPage

- **Route:** `/projects/:projectId/episodes/:episodeId/audio` (title `声音工作区`, `routeRegistry.ts:105`). Heading eyebrow `本集声音 · Audio Workspace`, `<h2>台词、角色声音与音轨编排</h2>` (`apps/web/src/pages/AudioPage.tsx:60`).
- **Purpose:** Single workspace for dialogue revision → speaker/voice, TTS takes, selected audio, BGM & SFX.
- **Guard:** `缺少项目或分集上下文。` (`AudioPage.tsx:46`).

### Layout
Tabs (`AUDIO_TABS`, `AudioPage.tsx:13-17`): `台词与 TTS` / `音效与配乐` / `缺口与证据`. Header links `音频审核` → `/review`, `进入时间线` → `/timeline` (`AudioPage.tsx:60`). `?view=` selects tab; default `dialogue` (`AudioPage.tsx:24`).
Loading: `正在读取当前声音任务…`; error: `当前声音任务读取失败：…` (`AudioPage.tsx:62-63`).

`台词与 TTS` hosts `DialogueTTSPanel`; `音效与配乐` hosts `AudioTrackPanel`; `缺口与证据` hosts `AudioEpisodeOverview` (`AudioPage.tsx:66-72`).

### Interactive controls
`DialogueTTSPanel` (`apps/web/src/features/status/DialogueTTSPanel.tsx`):
- Pill: `TTS 配置就绪` / `TTS 配置缺失` (depends on published providers).
- `选择此候选` (per candidate) → `selectTTSCandidate` → POST `/api/v1/tts-candidates/{id}:select` (api.ts:799-800).
- `绑定角色音色` → `bindCharacterVoice` → POST `/api/v1/projects/{id}/character-voice-bindings` (api.ts:815-816).
- `解绑` → `unbindCharacterVoice` → DELETE `/api/v1/character-voice-bindings/{id}` (api.ts:823-824).
- `整集批量 TTS` → `submitEpisodeTTSBatch` → POST `/api/v1/episodes/{id}/dialogue-tts:batch` (api.ts:827-828).

`DialogueGovernanceActions` (`apps/web/src/features/status/DialogueGovernanceActions.tsx`, toggled by `新增对白、音色或候选` / `收起对白治理操作`):
- `操作类型` select: `创建对白文本 v1` / `创建文本/发音新 revision` / `创建授权音色版本` / `登记真实音频候选` / `提交正式本地 TTS Job` / `结束登记成功的 TTS Job` / `选择候选` (`:86`).
- Sub-actions use: `createDialogueLine` → POST `/api/v1/episodes/{id}/dialogue-lines` (api.ts:783-784); `createDialogueTextRevision` → POST `/api/v1/dialogue-lines/{id}/text-revisions` (api.ts:787-788); `createVoiceProfileVersion` → POST `/api/v1/projects/{id}/voice-profile-versions` (api.ts:791-792); `registerTTSCandidate` → POST `/api/v1/dialogue-text-revisions/{id}/tts-candidates` (api.ts:795-796); `submitTTSJob` → POST `/api/v1/dialogue-text-revisions/{id}/tts-jobs` (api.ts:803-804); `finalizeTTSJob` → POST `/api/v1/tts-jobs/{id}:finalize` (api.ts:807-808); `selectTTSCandidate` (POST `:select`).
- `扫描本机 SAPI 音色` → `discoverLocalSapiVoices` → GET `/api/v1/tts/voices:discover` (api.ts:831-832).
- `校验并创建不可变记录` (submit) collects the above; validation errors surface as `inline-error`.

`AudioTrackPanel` (`apps/web/src/features/status/AudioTrackPanel.tsx`):
- Rows show `轨道` / `范围 / Gain` / `播放策略` / `授权`; audio player `preload="none"`.
- `AudioImportBindingForm` (`apps/web/src/features/status/AudioImportBindingForm.tsx`), toggled by `导入并绑定本地音频` / `收起本地音频绑定`:
  - Fields: `本地音频绝对路径`, `项目内授权证据相对路径` (placeholder `00_admin/licenses/music.json`), `轨道` select (`对白`/`BGM/音乐`/`音效`/`环境（旧兼容）`/`音乐（旧兼容）`), `授权类型` select (`用户拥有`/`本地授权已核验`/`公有领域`), `开始（秒）`, `结束（秒）`, `Gain（dB）`, `淡入（ms）`, `淡出（ms）`, checkbox `循环源音频以覆盖绑定范围`.
  - `校验、导入并绑定` → first `importMedia` → POST `/api/v1/media:import` (api.ts:839-840), then `bindEpisodeAudio` → POST `/api/v1/episodes/{id}/audio-bindings` (api.ts:835-836). Success `绑定已创建：{id} · 授权证据已验证` (`AudioImportBindingForm.tsx:28`).

`AudioEpisodeOverview` (`apps/web/src/features/audio-v2/AudioEpisodeOverview.tsx`) — read-only "缺口与证据" summary cards: `台词`, `Speaker / Voice`, `TTS Takes`, `Selected Audio`, `授权缺口`, `音量 / QC`, `BGM / SFX`; plus `生产缺口` panel listing `missing text`, `no TTS take`, `unselected`, `authorization evidence`.

### Happy path (b) add TTS dialogue + audio track
1. `台词与 TTS` tab: `新增对白、音色或候选` → 操作类型 `创建对白文本 v1` (createDialogueLine → v1).
2. Create a voice: 操作类型 `创建授权音色版本` (createVoiceProfileVersion); optionally `扫描本机 SAPI 音色` then choose.
3. Register a candidate: 操作类型 `登记真实音频候选` (registerTTSCandidate, PREVIEW or FORMAL).
4. Choose it: `选择此候选` per card (selectTTSCandidate). Select `整集批量 TTS` (submitEpisodeTTSBatch) or per-line `提交正式本地 TTS Job`.
5. `音效与配乐` tab → `导入并绑定本地音频` → fill fields → `校验、导入并绑定` (importMedia + bindEpisodeAudio) to add a BGM/SFX/DIALOGUE binding.
6. `缺口与证据` tab → review the summary; then `进入时间线`.

### State/status semantics
- `track_type` values `DIALOGUE`/`BGM`/`SFX`/`MUSIC`/`ENVIRONMENT`; `authorization_status === "VERIFIED_EVIDENCE"` → `授权证据已验证`, else `遗留授权证据不完整` (`AudioTrackPanel.tsx:16`).
- `license_status` enum `USER_OWNED`/`VERIFIED_LOCAL`/`PUBLIC_DOMAIN`.
- Voice `status === "ACTIVE"` counts as active; `provider_profile_version_id` present → "已发布 TTS Profile" (job-eligible). Without it, local audio is only import/preview candidate.
- `candidate_kind` `PREVIEW` vs `FORMAL`; FORMAL selection requires latest machine QC PASS + human APPROVED (UI cannot bypass — `DialogueTTSPanel.tsx:28`, `DialogueGovernanceActions.tsx:94`).
- `selection` present → `已选择`; `text_revisions.at(-1)` is latest revision.
- Audio player never preloads (`preload="none"`) — "播放器默认不预加载" (`AudioTrackPanel.tsx:9`).

---

## 3. TimelinePage

- **Route:** `/projects/:projectId/episodes/:episodeId/timeline` (title `时间线`, `routeRegistry.ts:106`). Heading eyebrow `本集时间线`, `<h2>版本化成片编排</h2>` (`apps/web/src/pages/TimelinePage.tsx:43`).
- **Purpose:** Compose adopted shots, audio and subtitles into a new immutable timeline revision; mark stale when upstream changes.

### Layout
Header: `版本证据` (Drawer), `合成与交付` → `/delivery` (`TimelinePage.tsx:43`). Summary cards: `时间线版本` (revision_count + latest status/`尚未创建`), `音频绑定` (binding_count + `{n} 个已验证本地来源`), `整集渲染` (renders.count + `{n} 个完整性已验证`) (`TimelinePage.tsx:47-49`).

Tabs (`TIMELINE_TABS`, `TimelinePage.tsx:16-20`): `编排与冻结` / `字幕 revision` / `导出与合成`.

### Interactive controls
`TimelineComposer` (`apps/web/src/features/timeline-v2/TimelineComposer.tsx`):
- `载入最新上游为新草稿` (stale banner, `:132`) → reload current adopted upstream.
- Per-shot `更换项目视频` / `选择项目视频` (`:139`) opens `MediaPicker` (`:135`).
- Per-shot `时长（秒）` input and `入场转场` select (`硬切`/`叠化`/`淡入`) (`:140`); `打开镜头` link.
- Checkboxes `包含已授权音轨` / `包含最新字幕 revision` (`:134`).
- Footage summary `V1 画面` (`:134`).
- Freeze confirmation checkbox `我已核对画面、时长、音频和字幕，允许冻结这次快照` (`:146`).
- `保存新草稿 revision` → `createTimelineRevision(status=DRAFT)`; `冻结新 revision` → `createTimelineRevision(status=FROZEN)` → POST `/api/v1/episodes/{id}/timeline-revisions` (api.ts:751-752).

`TimelineExportPanel` (`apps/web/src/features/timeline-v2/TimelineExportPanel.tsx`, `导出与合成` tab):
- `导出标准 / OTIO / EDL` → `exportTimelineRevision(...,'standard')` → POST `/api/v1/timeline-revisions/{id}:export?format=standard` (api.ts:759-764).
- `导出剪映草稿` → `exportJianyingTimeline` → same `:export?format=jianying` (api.ts:767-768).
- Both disabled unless a FROZEN revision exists (`!timelineRevisionId`).

`TimelineStatusPanel` (Drawer `时间线版本证据`, `ReadinessPanels.tsx:111-127`): 时间线修订/字幕·音频/整集渲染/交付包 cards.
`SubtitleRevisionPanel` (subtitles tab, `TimelinePage.tsx:56`).

### Happy path (c) assemble a timeline + render / revision compare
1. `编排与冻结` tab → `TimelineComposer` loads shot adoptions + audio bindings + subtitles.
2. Per shot: if a shot lacks media (`缺少视频`), `选择项目视频` via picker; adjust `时长（秒）` and `入场转场`.
3. Leave `包含已授权音轨`/`包含最新字幕 revision` on (subtitles disabled if none).
4. If upstream changed, `载入最新上游为新草稿` (prompts stale — `上游采用结果已变化`).
5. `保存新草稿 revision` (DRAFT) to iterate, then tick `我已核对…` and `冻结新 revision` (FROZEN).
6. `导出与合成` tab → `导出标准 / OTIO / EDL` and/or `导出剪映草稿`; `版本证据` drawer shows evidence. Revision compare is via the stale banner and `TimelineStatusPanel` (persisted fingerprint vs current fingerprint).

### State/status semantics
- Revision `status` `DRAFT` vs `FROZEN`; only FROZEN enables exports (`latestTimeline?.status === "FROZEN"`). Policy note: `OTIO、EDL 与剪映导出只对冻结 revision 开放` (`TimelinePage.tsx:62`).
- `stale` when persisted fingerprint ≠ current fingerprint (`persistedStale`, `TimelineComposer.tsx:96`); `upstreamChanged` when draft is out of date (`:82`). `legacyUnknown` when latest revision lacks v2 fingerprint (`:97`).
- Cannot freeze/save with missing video; freeze requires confirmation checkbox (`TimelineComposer.tsx:101-102`).

---

## 4. DeliveryPage

- **Route:** `/projects/:projectId/episodes/:episodeId/delivery` (title `成片交付`, `routeRegistry.ts:107`). Heading eyebrow `合成与交付`, `<h2>从冻结时间线创建可验证成片</h2>` (`apps/web/src/pages/DeliveryPage.tsx:50`).
- **Purpose:** Create a verifiable finished episode from a frozen timeline; render, delivery candidate, manifest verify and manual approval are separate evidence steps.

### Layout
Header link `返回时间线` → `/timeline` (`DeliveryPage.tsx:50`). Steps (`DELIVERY_TABS`, `DeliveryPage.tsx:14-19`): `1 准备预检` / `2 合成候选` / `3 审核证据` / `4 打包交付`.

> **Note (differs from prompt):** prompt guessed 候选/构建/验证/导出/撤回. Actual tabs are the numbered four above.

- `1 准备预检`: eyebrow `步骤 1 · Preflight`, `<h3>确认冻结输入、目标与退出证据</h3>`, pill `只读`; renders `G8ReadinessPanel`. Button `继续到合成候选` → `selectStep("compose")` (`DeliveryPage.tsx:59`).
- `2 合成候选`: `DeliveryWorkflowPanel focus="COMPOSE"`; button `下一步：审核证据 →` (`:64`).
- `3 审核证据`: `DeliveryWorkflowPanel focus="REVIEW"`; button `下一步：打包交付 →` (`:68`).
- `4 打包交付`: `DeliveryWorkflowPanel focus="PACKAGE"` + `增强与联系表` section (`本地交付工具`, `不覆盖输入`) → `EpisodeContactSheetAction` + `PostProcessPanel` (`DeliveryPage.tsx:70-79`).

### Interactive controls (`DeliveryWorkflowPanel`, `apps/web/src/features/production/DeliveryWorkflowPanel.tsx`)
| Label | Type | Action | API method |
|---|---|---|---|
| `登记整集渲染` | button | `renderEpisode` | POST `/api/v1/timeline-revisions/{id}:render` (api.ts:847-848) |
| `创建交付候选` | button | `buildDeliveryPackage` | POST `/api/v1/delivery-packages` (api.ts:871-872) |
| `验证 manifest / SHA` | button | `verifyDeliveryPackage` | GET `/api/v1/delivery-packages/{id}:verify` (api.ts:887-888; client passes `undefined`) |
| `记录人工批准` | button | opens note dialog → `reviewDeliveryPackage(reviewer_type=HUMAN)` | POST `/api/v1/delivery-packages/{id}:review` (api.ts:899-900) |
| `记录平台批准` | button | opens note dialog → `reviewDeliveryPackage(reviewer_type=PLATFORM)` | POST `...:review` |
| `撤回交付包` | button | opens note → `withdrawDeliveryPackage` | POST `/api/v1/delivery-packages/{id}:withdraw` (api.ts:895-896) |
| `复验 manifest / SHA` | button | `verifyDeliveryPackage` | GET `...:verify` |
| `刷新交付历史` | button | `listEpisodeDeliveryPackages` | GET `/api/v1/episodes/{id}/delivery-packages` (api.ts:875-876) |

Note dialog buttons: `记录审核` / `确认撤回`, `取消` (`DeliveryWorkflowPanel.tsx:76`).

`G8ReadinessPanel` (`ReadinessPanels.tsx:133-141`): reads `getG8Readiness` → GET `/api/v1/projects/{id}/gates/g8?episode_id=`. Pass/fail list, `next_required_action`.

`EpisodeContactSheetAction` (`apps/web/src/features/production/EpisodeContactSheetAction.tsx`): `导出联系表` → `exportEpisodeContactSheet` → POST `/api/v1/episodes/{id}/contact-sheet:export` (api.ts:437-438). Result `已导出 {n} 项：<path>`.

`PostProcessPanel` (package tab, `apps/web/src/features/generation/PostProcessPanel.tsx`): recipe version select, `创建 DRAFT v1`/`派生 DRAFT 新版本`, `发布所选版本`, `只读预检增强计划`, `确认运行并注册新版本`, plus `增强前后旁路比较` (`PostProcessPanel.tsx:110-124`).

### Happy path (e) build + verify + export a delivery
1. `1 准备预检` → review `G8ReadinessPanel` (`只读`), `继续到合成候选`.
2. `2 合成候选` → `登记整集渲染` (requires a FROZEN timeline) → `创建交付候选` (requires render + explicit delivery target), then `下一步：审核证据 →`.
3. `3 审核证据` → `验证 manifest / SHA`, `记录人工批准` / `记录平台批准` (note required), `撤回交付包` if needed, then `下一步：打包交付 →`.
4. `4 打包交付` → `复验 manifest / SHA`; local tools: `导出联系表`, `PostProcessPanel` enhancements; delivery history table shows status/target/SHA/path/download count/withdraw reason.

### State/status semantics
- `render.integrity_status`; `delivery.status`; `human_review_status`/`platform_review_status`; `manifest_sha256`; `withdrawn_reason`; DOWNLOAD event count (`DeliveryWorkflowPanel.tsx:81`).
- Guards: build needs `renderId && targetVersionId`, review/verify/withdraw need `deliveryId`; error `请先创建交付候选`/`请先创建并选择冻结 TimelineRevision`/`必须同时拥有整集 render 和项目显式交付目标`.
- `机器 PASS ≠ 人工/平台批准` (`DeliveryWorkflowPanel.tsx:80`). Preflight (`只读`) never creates render/package.

---

## 5. EpisodeRunPage

- **Route:** `/projects/:projectId/episodes/:episodeId/run` (title `分集生产运行`, `routeRegistry.ts:108`, feature flag `EPISODE_AGENT_RUN_V2`). Heading eyebrow `本集生产`, `<h2>运行、关卡与失效处置</h2>`, pill `本机持久化事实` (`apps/web/src/pages/EpisodeRunPage.tsx:45`).
- **Purpose:** Creator-facing seven-stage (headline says eight-stage) facade for a durable episode automation run with pause/resume/cancel and HITL checkpoints.

### Layout
Tabs (`RUN_TABS`, `EpisodeRunPage.tsx:12-16`): `生产运行` / `关卡总览` / `失效与影响`.

> **Note (differs from prompt):** prompt guessed 生产/关卡/失败. Actual labels are `生产运行` / `关卡总览` / `失效与影响`.

- `生产运行` → `EpisodeRunPanel` (in `#episode-production-controls`).
- `关卡总览` → `EpisodeCockpit` (read-only).
- `失效与影响` → `FreshnessPanel scopeType="EPISODE"`.

### Interactive controls (`EpisodeRunPanel`, `apps/web/src/features/episode-run-v2/EpisodeRunPanel.tsx`)
Preflight (no run yet): mode radio group `生产质量` (`草稿`/`平衡`/`精品`), `人工确认节点` select (`遇到例外时停下`/`资产确认后停下`/`分镜确认后停下`/`生成视频前停下`/`全程自动推进`), checkbox `自动生成人声`, `重新运行预检` (icon), `查看全部 {n} 项检查`.
- `开始整集生产` → `startEpisodeRun` → POST `/api/v1/episodes/{id}/production-runs` (api.ts:97-107).
- `暂停` → `pauseEpisodeRun` → POST `/api/v1/episode-production-runs/{id}/pause` (api.ts:124).
- `继续生产` → `resumeEpisodeRun` → POST `/api/v1/episode-production-runs/{id}/resume` (api.ts:125).
- `取消` → `cancelEpisodeRun` → POST `/api/v1/episode-production-runs/{id}/cancel` (api.ts:126); `window.confirm`.
- `新建生产运行` (terminal) clears `?run`.
- Stage tabs (per stage, `stageTabs` `:152`) and per-stage `StageCard`; `阻塞与任务事件` (Drawer `阻塞与任务事件`) shows gate + stage job events; `打开失败镜头` link when stage BLOCKED.
- Reads: `preflightEpisodeRun` → GET `/api/v1/episodes/{id}/production-runs/preflight?tts_enabled=&production_mode=&checkpoint_policy=` (api.ts:89-95); `getEpisodeRun` → GET `/api/v1/episode-production-runs/{id}?include_jobs=false` (api.ts:109-114).

### Happy path (d) run episode production + pause/resume
1. `生产运行` → confirm preflight `已具备整集生产条件` (or handle blockers via `去处理`/`去检查`).
2. Choose `生产质量` (mode), `人工确认节点` (checkpoint), `自动生成人声` toggle.
3. `开始整集生产` → run starts, `?run={id}` set; stage tabs/pills update.
4. `暂停` at any RUNNING moment (status → `PAUSED_HITL`), then `继续生产` to resume; terminal ends → `新建生产运行`.
5. `关卡总览`/`失效与影响` tabs for read-only stage facts and stale impact.

### State/status semantics
- Run statuses via `RUN_STATUS` (`EpisodeRunPanel.tsx:20-29`): `PENDING`=等待开始, `RUNNING`=自动生产中, `PAUSED_HITL`=已暂停，等待你确认, `SUCCEEDED`/`COMPLETED`=整集生产完成, `FAILED`=生产遇到问题, `CANCELLED`=已取消, `CANCEL_REQUESTED`=正在安全停止.
- Stage statuses: `PENDING`/`RUNNING`/`PAUSED`/`BLOCKED`/`COMPLETED` (`STAGE_STATUS`).
- `canPause` = RUNNING; `isPaused` = PAUSED_HITL; `isTerminal` = SUCCEEDED/COMPLETED/FAILED/CANCELLED.
- Modes `DRAFT`/`BALANCED`/`QUALITY` → takes 1/2/4 per shot; checkpoint policies.
- Preflight `status` `PASS`/`BLOCKED`; `blocked` list with `去处理`/`去检查` links (`EpisodeRunPanel.tsx:180`).
- "已完成的阶段不会丢失"; pause cancels keep completed creative results (`:200`).

---

## 6. JobsPage

- **Route:** `/jobs` and `/projects/:projectId/jobs` (title `任务队列`, `routeRegistry.ts:82/93`). Heading eyebrow `系统区`, `<h2>任务与机器</h2>`, pill `SQLite durable queue · LOCAL_ONLY` (`apps/web/src/pages/JobsPage.tsx:49-50`).
- **Purpose:** Observe real Job/Attempt/lease/progress/verified artifacts; durable operational facts, not a creative-history system.

### Layout
- `项目范围` select (`全部项目` / project list) (`JobsPage.tsx:54-71`).
- `JobsPanel` + `CapacitySnapshotPanel` (`JobsPage.tsx:74,77`).

### Interactive controls (`JobsPanel`, `apps/web/src/features/jobs/JobsPanel.tsx`)
| Label | Type | Action | API method |
|---|---|---|---|
| `扫描过期租约` | button | `reconcileJobs` | POST `/api/v1/jobs:reconcile` (api.ts:495-496) |
| `详情 · 产物` | button | open drawer | `getJob` → GET `/api/v1/jobs/{id}` (api.ts:470-471) |
| `取消` | button | `cancelJob` (only QUEUED/RUNNING/CLAIMED/WAITING) | POST `/api/v1/jobs/{id}:cancel` (api.ts:483-484) |
| `故障重试（同一 Job）` | button | `retryJob` (only FAILED/NEEDS_ATTENTION/ORPHANED) | POST `/api/v1/jobs/{id}:retry` (api.ts:487-488) |
| `复制任务` | button | `cloneJob` (non-GENERATION_VARIANT) | POST `/api/v1/jobs/{id}:clone` (api.ts:491-492) |
| `继续显示任务（{a}/{b}）` | button | reveal more rows | — |
| (GENERATION_VARIANT) `创作重抽 → Variant Composer` | static text | no clone (variant composer owns it) | — |

`JobDetailsPanel` (`apps/web/src/features/jobs/JobDetailsPanel.tsx`): `登记为媒体版本` (per verified artifact) → `promoteJobArtifactToMedia` → POST `/api/v1/artifacts/{id}:promote-media` (api.ts:499-500). Fields `登记媒体类型` (`VIDEO`/`IMAGE`/`AUDIO`), `登记阶段` (`PROXY`/`KEYFRAME`/`FORMAL`/`TIMELINE`), `媒体用途`.

`CapacitySnapshotPanel` (`JobsPanel.tsx:37-53`): read-only capacity cards `排队`/`活跃 Attempt`/`GPU_H3`/`近 24h 完成`/`耗时 / 失败`/`重试 / 审核通过`/`本机磁盘 / GPU`; `Webhook:{status}`.

Reads: `listJobsPage` → GET `/api/v1/jobs?project_id=&cursor=&limit=` (api.ts:462-467, auto-refetch 5000ms); `getCapacitySnapshot` → GET `/api/v1/capacity/snapshot?project_id=` (api.ts:1033-1035, 5000ms).

### Happy path (f) monitor / cancel / retry jobs
1. Pick `项目范围` (or route-scoped). Jobs list polls every 5s.
2. Click `详情 · 产物` to inspect attempts and artifacts.
3. To cancel a queued/running job → `取消`. To recover a FAILED/NEEDS_ATTENTION/ORPHANED job → `故障重试（同一 Job）`. To duplicate a non-generation job's inputs → `复制任务`.
4. To promote a verified artifact → in details `登记为媒体版本`. 
5. `扫描过期租约` to reconcile stale worker leases; `CapacitySnapshotPanel` observes queue/GPU/disk.

### State/status semantics
- Job `state` values include QUEUED/RUNNING/CLAIMED/WAITING/FAILED/NEEDS_ATTENTION/ORPHANED (controls gate on these). `type === "GENERATION_VARIANT"` suppresses clone.
- "故障重试只回到同一 Job 并新增 Attempt；创作重抽必须在 Variant Composer 生成新的 Variant + Job" (`JobsPanel.tsx`, muted).
- Server-side: `listJobsPage` infinite via cursor, `maxPages:10`; SSE/project-event invalidation for JOB_QUEUED/CLAIMED/HEARTBEAT/FINISHED/RECONCILED/REQUEUED/CANCEL_REQUESTED/ARTIFACT_REGISTERED (`JobsPage.tsx:32-38`).
- Artifact promote disabled unless `artifact.status === "VERIFIED"` (`JobDetailsPanel.tsx:28`).

---

## 7. DiagnosticsPage

- **Route:** `/diagnostics` and `/projects/:projectId/diagnostics` (title `诊断与审计`, `routeRegistry.ts:83/94`). Heading eyebrow `系统区`, `<h2>诊断、审计与运维检索</h2>`, pill `本机事实 · 创作区外` (`apps/web/src/pages/DiagnosticsPage.tsx:47-50`).
- **Purpose:** Engineering diagnostics kept separate from creator workspaces: local env checks, read-only audit history, cross-entity search.

### Layout
Tabs (`DIAGNOSTIC_TABS`, `DiagnosticsPage.tsx:11-15`): `本机环境检查` / `审计历史` / `全局检索`.

- `本机环境检查`: `运行诊断` button; renders `DiagnosticPanel` (`ReadinessPanels.tsx:162-166`).
- `审计历史`: `审计项目范围` select + `AuditHistoryPanel`.
- `全局检索`: `GlobalSearchPanel`.

> **Note:** `status-v2/LocalRuntimeIndicator` is **not** imported by `DiagnosticsPage` (`DiagnosticsPage.tsx` only imports `AuditHistoryPanel`, `DiagnosticPanel`, `GlobalSearchPanel`). It does not appear on this page.

### Interactive controls
| Label | Type | Action | API method |
|---|---|---|---|
| `运行诊断` | button | `runDiagnostics` | POST `/api/v1/diagnostics/runs` (api.ts:321-322); busy `检查中…` |
| `重试` (audit) | — | refetch | — |
| `审计项目范围` | select | audit filter | — |
| (AuditHistoryPanel) `动作`/actor/type/id/time filters, `取证明` likely | — | `listAuditEvents` / `getAuditProof` | GET `/api/v1/audit-events?...` (api.ts:329-337); proof `/api/v1/audit-events/proof?...` (api.ts:340-347) |
| (GlobalSearchPanel) search input | text | `searchAll` | GET `/api/v1/search?q=&project_id=&limit=` (api.ts:247-250) |

Reads: `getDiagnostics` → GET `/api/v1/diagnostics` (api.ts:317-318); `listProjects`.

### Happy path (g) run diagnostics
1. `本机环境检查` → `运行诊断` (POST) → `DiagnosticPanel` shows overall `status` + per-check codes/status (`DiagnosticPanel.tsx:162-166`). Empty state until first run (`还没有诊断记录；点击“运行诊断”执行本机只读检查。`).
2. `审计历史` → optional `审计项目范围` → table of events (time/action/subject/actor/summary/details with `查看脱敏字段`).
3. `全局检索` → type ≥2 chars → results navigate to their routes (`GlobalSearchPanel.tsx:14-23`).

### State/status semantics
- `DiagnosticRun.status` + `checks[].status`; `DiagnosticPanel` colors `status-{lowercase}` (`ReadinessPanels.tsx:164`).
- Audit is read-only, append-only, sanitized (`DiagnosticsPage.tsx:52-54`).
- Search requires `> =2` chars; empty/error states shown (`GlobalSearchPanel.tsx:16-22`).

---

## 8. ProjectOperationsPage

- **Route:** `/projects/:projectId/operations` (title `项目运营与工具`, `routeRegistry.ts:97`). Heading eyebrow `Project Operations`, `<h2>项目运维入口</h2>` (`apps/web/src/pages/ProjectOperationsPage.tsx:70-71`).
- **Purpose:** Migration index only — routes each operational fact to its single-owner workspace.

### Layout
Nav grid (`OWNER_WORKSPACES`, `ProjectOperationsPage.tsx:8-37, 79-90`) of 4 cards (`code`/`title`/`description`), each `<h3>` + `进入工作区 →`:
- `SET · 生产设置` → `/projects/:projectId/production-settings`
- `MOD · 模型与能力` → `/projects/:projectId/models`
- `JOB · 任务队列` → `/projects/:projectId/jobs`
- `DIA · 诊断与审计` → `/projects/:projectId/diagnostics`

Below, `事实归属与旧入口迁移` section lists ownership (`owns`) with `打开唯一 owner` links (`ProjectOperationsPage.tsx:92-108`).

### Interactive controls
All are `<Link>` navigation cards — **no API-write buttons** on this page (it is a hub). The feature panels it owns (configuration snapshot, model compatibility, jobs, diagnostics) are the actual surfaces described above. `ProjectConfigurationSnapshot`, `AdapterContractsPanel`, `ModelCompatibilityPanel`, `AutomationWorkflowPanel`/`AutomationPanel`, `OutboxDeliveryPanel`, `ProjectHealthPanel`, `WorkspaceAssetAuthorizationPanel` live on the settings/model pages, not here.

### Happy path (h) package a project
Packaging is driven from the owned workspaces, not `operations` itself:
1. `SET 生产设置` → `ProjectConfigurationSnapshot` → `创建新目标版本` (`createDeliveryTarget`, POST `/api/v1/projects/{id}/delivery-targets`) or `从平台预设创建交付目标` (`createDeliveryTargetFromPreset`, POST `.../delivery-targets:from-preset`) → `选择为当前交付目标` (`selectDeliveryTargetVersion`, POST `...:select-delivery-target`).
2. `MOD 模型与能力` → `ModelCompatibilityPanel` for model evidence (`LocalModelScanForm`, `LocalModelReferenceForm`, `ModelLicenseEvidenceForm`).
3. Real packaging (stage/commit) is under `project_packages.py` — stage/commit routes `POST :stage`, `POST /{stage_token}:dry-run`, `POST /{stage_token}:commit` (`apps/api/local_drama/api/routes/project_packages.py:32,40,48`) exposed via the settings workspace's `ProjectPackageAction`. UI button labels for package staging are in `apps/web/src/features/projects/ProjectPackageAction.tsx` (not in the required file set; the endpoint mapping is from the backend route list).
4. `JOB 任务队列` / `DIA 诊断与审计` as described above.

### State/status semantics
- Page requires `projectId` (`缺少项目上下文。`), loads project detail; cards are navigation only.
- Delivery target must be explicitly selected; remote transport never enabled (`ProjectConfigurationSnapshot` `:70`, "远端已禁用").

---

## 9. CanvasPage

- **Route:** `/projects/:projectId/canvas` (title `高级画布`, `routeRegistry.ts:96`). Heading eyebrow `高级生产画布`, `<h2>高级生产画布</h2>`, `<p>业务依赖来自权威 read model；拖动只保存布局，不改变生成事实。</p>` (`apps/web/src/pages/CanvasPage.tsx:36`).
- **Purpose:** Advanced business production DAG for one episode; layout drags are saved but never change generation facts.

### Layout
Header selector `当前分集` (`select`, options `{code} · {title}`); body `ProductionCanvasPanel` (`apps/web/src/features/canvas/ProductionCanvasPanel.tsx`).

### Interactive controls (`ProductionCanvasPanel`)
| Label | Type | Action | API method |
|---|---|---|---|
| `保存布局` | button | `saveProductionCanvasLayout` | PUT `/api/v1/canvas/EPISODE/{id}/layout` (api.ts:1231-1232) |
| `运行节点预检` | button | `preflightProductionCanvasRun` | POST `/api/v1/canvas/EPISODE/{id}/runs:preflight` (api.ts:1235-1236) |
| `搜索节点` | text | filter nodes | — |
| `全部` / `聚焦上游` / `聚焦下游` | buttons | focus filter | — |
| node list / ReactFlow node click | — | select node; `onSelectShot` navigates to `/direct/{shotId}` | — |
| `重试业务画布` | button | refetch graph | GET `/api/v1/canvas/EPISODE/{id}?cursor=&limit=` (api.ts:1227-1228) |

Muted: `节点显示状态、take、variant 和阻塞；edges 来自后端业务依赖，布局接口无法修改它们。` Status line: `选中 / 显示 {visible}/{total} 节点 / 布局 revision / 业务依赖可编辑：否`; node detail (thumbnail, take count, logs, 变体谱系, 实验进度, 相邻边界约束, 执行日志).

### State/status semantics
- Node `className` `state-{lowercase}`; MiniMap color: blocked `#df9d4b`, running `#7457b5`, else `#2d7467`.
- Canvas is scoped `EPISODE` and `?episode=` param; first episode auto-selected; at most `invariants.max_visible_nodes`.
- Focus upstream/downstream requires a selected node; disabled otherwise.
- Preflight `plan.status`, `node_ids`, `blockers`, `estimate`, `submitted:false`.
- Empty states: `本集暂无镜头，请切换分集。`; `请先选择一个包含集的项目。` (`CanvasPage.tsx:39`, `ProductionCanvasPanel.tsx:102`).
- Drag only saves layout (PUT layout), never business dependency edges (`nodesConnectable={false}`, `deleteKeyCode={null}`).

---

## Cross-cutting state/status notes
- **Immutability:** timeline revisions, dialogue revisions, voice profile versions, TTS candidates, review records, delivery packages, Job attempts are never overwritten; `stale`/`legacy` flags mark divergence rather than mutate.
- **Separation of duties everywhere:** `selection`, `approval`, `machine check`, `render`, `manifest verify`, `human/platform` are distinct evidence steps; "机器通过不等于人工批准" and "UI不能绕过" recur.
- **Local-only:** the whole surface is `LOCAL_ONLY` (`SQLite durable queue · LOCAL_ONLY`), no public network (remote transport disabled, outbox loopback only, diagnostics never contact runtime) — these are stated as invariants in each workspace's muted text.
- **Preload policy:** all audio/video players use `preload="none"`; `try-UI` never loads a preview until the user plays (Audio `播放器默认不预加载`, Timeline `使用本地音频/视频`, Delivery render preview `poster` + `preload="none"`).

---

## Questions to resolve
1. **Delivery verify method mismatch:** `DeliveryWorkflowPanel` calls `verifyDeliveryPackage` (generated client passes `undefined` init → **GET** `:verify`, `api.ts:887-888`), but the backend also exposes a **POST** `:verify` (`verifyDeliveryPackagePost`, `timeline.py:384`). Which is intended for the `验证 manifest / SHA` / `复验 manifest / SHA` buttons? The client binding used in the UI is GET, which is unusual for a mutating "verify".
2. **Backend route prefixes:** several operations referenced above are bound to paths like `/api/v1/canvas/...`, `/api/v1/delivery-packages`, `/api/v1/reviews/batch:...`, `/api/v1/media:import`, `/api/v1/events:deliver`, `/api/v1/automation-clients`, `/api/v1/episode-production-runs/...`. The provided backend route list (`reviews.py`, `dialogue.py`, `timeline.py`, `episode_production_runs.py`, `jobs.py`, `diagnostics.py`, `capacity.py`, `automation_workflows.py`, `audit.py`, `project_packages.py`) does not include `canvas.py`, `delivery` route, `events`, or `media.py`; confirm where those live (they exist under `routes/` as `canvas.py`, `media.py`, `production.py`, etc.) so the button→endpoint map can be fully reconciled to file/line.
3. **Canvas route title:** the `routeRegistry` title is `高级画布` but the page's `eyebrow`/`h2` are both `高级生产画布` and the muted text references business DAG. Confirm the canonical display title.
4. **Episode run stage count:** `EpisodeRunPanel` hero says `系统按八个创作阶段自动推进` while `EpisodeRunPage.tsx:18` doc-comment says "seven-stage facade"; `EpisodeRunStage.code` lists 8 codes (STORY_ANALYSIS…COMPOSE_QC). Confirm the intended stage count to document.
5. **`ProjectConfigurationSnapshot`/`createDeliveryTarget`/`selectDeliveryTargetVersion` endpoints** are in `apps/web/src/features/status/ReadinessPanels.tsx` via generated `api.ts` but were not in the "backend routes" list given; confirm their route file (likely `configuration.py` or `production.py`).
6. **Machine-check button label:** `ReviewInboxPanel.tsx:253` shows the single machine-check button label is `运行音频 QC` (unconditional in the JSX, disabled while `machineChecking` → `检查中…`). No separate label exists for IMAGE/VIDEO in the required files; confirm whether audio-only wording is intended for video/image subjects.
7. **SubtitleRevisionPanel / PostProcessPanel detail** (buttons and endpoints) were read only at the surface in the required scope files; if a deeper feature sheet is needed, read `apps/web/src/features/production/SubtitleRevisionPanel.tsx` and `apps/web/src/features/generation/PostProcessPanel.tsx` fully (their exact API bindings are in `generated/api.ts`).
