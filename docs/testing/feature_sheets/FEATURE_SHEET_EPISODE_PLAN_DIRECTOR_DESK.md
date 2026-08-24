# Feature Sheet — Episode Plan & Director Desk

Scope: LocalDramaStudio React front-end at `apps/web/src/...` and its FastAPI routes at `apps/api/local_drama/api/routes/...`.
Every claim below is grounded in the actual source; citations give `file.tsx:line` or `file.py:line`. Nothing is inferred from screenshots or documentation.

Conventions:
- Endpoint prefixes: the web clients that call `fetch("/api/v1" + path)` (the `request()` helpers in `episode-plan-v2/*Api.ts`) prepend `/api/v1`; the generated client (`apps/web/src/generated/api.ts`) writes the full `/api/v1`. The hand-written `director-v2` clients also write full `/api/v1`.
- "Plan page" = `EpisodePlanPage.tsx`; "Director Desk" = `DirectorDeskPage.tsx`.

---

## 1. Episode Plan Page

### 1.1 Route + page title
- **Route**: `/projects/:projectId/episodes/:episodeId/plan` — `routeRegistry.ts:56-57` (`routes.episodePlan`), pathPattern at `routeRegistry.ts:99`, title `分集策划`.
- **Page component/title**: `EpisodePlanPage.tsx`. Renders eyebrow `分集策划` + heading `从原文证据到可生产镜头` (`EpisodePlanPage.tsx:74-75`). It is wrapped in an `ErrorBoundary` with fallback title `分集策划工作区异常` (`:70`).

### 1.2 Purpose
Turn the current episode's quoted source text into a reviewed, versioned shot plan: browse source evidence, arrange scenes/shot-groups, and snapshot the prompt payload — while AI draft ownership/import remains in the Story workspace.

### 1.3 Layout: panels / tabs / sections (exact labels)
Top actions (`:78-98`): button `选定 Beat 重排`; links `故事工作区` (→ `routes.storyWorkspace`), `进入导演台` (→ `routes.directorDesk`).
Muted note (`:100-102`): `项目长文导入与 AI 拆解在故事工作区统一管理；此处展示当前集引用的原文范围与分镜头编排。`
Data-flow ordered list (`:104-108`): `1 引用原文证据`, `2 编排场景与镜头`, `3 冻结生产快照`.

Tabs (`PLAN_TABS`, `:19-24`):
- `镜头分镜板` (id `storyboard`)
- `场景与分组` (id `scenes`)
- `原文证据` (id `source`)
- `提示词快照` (id `prompts`)

Tab bodies:
1. **storyboard** → `StoryboardBatchWorkbench` (`:115-119`).
2. **scenes** → `ShotGroupPlanner` + `EpisodeSceneRanges`, stacked (`:122-131`).
3. **source** → `StoryboardBatchWorkbench`-independent card: section title `故事原稿与段落引用` + link `前往故事工作区审核或重新拆解 →`, muted `本集镜头所绑定的来源段落与上下文（Unicode 字符偏移范围与原文比对）：`, then `EpisodeSourcePassage` (`:134-151`).
4. **prompts** → `镜头提示词快照` panel (`:156-205`) + `PromptTemplatePanel`.

Replan drawer (`:208-217`): `Drawer` title `选定 Beat 重排与对比`, width 600, contains `SelectedBeatReplanPanel`.

**Note on `分镜批量台` (storyboard tab) internals** (`StoryboardBatchWorkbench.tsx`):
- Header eyebrow `FR-ING-003 · 分镜批量台`, heading `身份稳定的镜头清单`, status pill `{n} 镜 · {m} 秒` (`:117-118`).
- View toggle group `分镜显示方式` with buttons `清单` / `故事板` / `时间线` (`:123-125`).
- Command actions: `已选 {n} 镜`, buttons `批量操作`, `重排、拆分与复制` (`:127-130`).
- Page summary `第 {n} / {m} 页` and `{a}–{b} / {c} 镜` (`:133-136`).
- Bounded viewport (`:138`), pagination `上一页` / `下一页`, `每页最多 25 镜` (`:163-167`).
- Row inner labels: checkbox, ordinal, `{code}`, `{shot_type} · {x} 秒 · r{revision}`, `Scene：…`, `Beat：…`, `画面/动作：…`, `对白：…`, assets `{n} 项角色/场景资产` or `角色/场景资产：未绑定`, status pill, `Production Ready`/`未 Ready`, button `编辑详情` (`:146-155`).
- Shot-detail drawer title `{code} · 镜头详情` (`:169`), fields `时长 ms`, `镜头类型`, buttons `上移一位`, `下移一位`, and asset list (`:180-182`).
- BATCH drawer title `批量操作 · 已选 {n} 镜` (`:188`).
- STRUCTURE drawer title `重排、拆分与复制`, `dirtyGuard` on pending reorder/splits/copies/edits (`:201`). Sections `复制镜头` and `拆分镜头` (`:203-204`).

`ShotGroupPlanner` internals (`ShotGroupPlanner.tsx`):
- Header eyebrow `0044 · 连续镜头组`, heading `场景归属与镜头组编排`, pill `{n} 组 · {m} 镜` (`:63`).
- Create form labels: `编号`, `标题`, `类型`, `场景` (options `暂不确定` + `{code} · {title}`), button `创建分组` (`:65-71`).
- Group card header shows kind pill `{group.kind}`, strong `{group.code} · {group.title}`, buttons `↑`, `↓`, `归档` (`:75`).
- Assignment table (`:81-85`): columns `镜头`, `类型 / 时长`, `场景`, `连续镜头组`; per-shot `select` labeled `{code} 场景` and `{code} 分组`.

`SelectedBeatReplanPanel` internals (`SelectedBeatReplanPanel.tsx`):
- Header eyebrow `人工审核 · Selected Beat AI Replan`, heading `只重排选定 Beat`, pill `计划 → 差异 → 落地` (`:60`).
- Form labels: `选定 Beat`, `AI 拆解草稿`, `建议场次`, button `生成差异预览` (`:63-70`).
- Diff table columns `动作`, `当前`, `建议`, `安全规则` (`:78`); summary line `保留 {n} · 新增 {n} · 修改 {n} · 归档 {n} · 冻结保护 {n}` (`:75`); apply button `确认应用以上差异` (`:86`).

`AssetProposalReviewPanel` (`AssetProposalReviewPanel.tsx`): header `AI Breakdown · 资产身份审核` / `角色资产建议`, pill `{n} 待处理`. Buttons `接受合并建议`, `保留为独立角色`, `拒绝建议`, plus `独立资产 code` input. **This panel is NOT mounted on the Episode Plan page** — it is mounted in `StoryWorkspacePage.tsx:160` (imported at `:6`). Task list also named a non-existent `episode-plan-v2/EpisodePlanIntegration.tsx`; only a test fixture `EpisodePlanIntegration.test.tsx` survives (see "Questions to resolve").

### 1.4 Interactive controls → action / API

| Exact visible label | Type | Action | API endpoint + method |
|---|---|---|---|
| `选定 Beat 重排` | Button (secondary) | Opens replan drawer (`setReplanDrawerOpen(true)`) | None (drawer) |
| `故事工作区` | Link | `routes.storyWorkspace(projectId)` | None |
| `进入导演台` | Link | `routes.directorDesk(projectId, episodeId)` | None |
| View tabs `镜头分镜板`/`场景与分组`/`原文证据`/`提示词快照` | Tabs | `selectTab` sets `?view=` | None (client routing) |
| Storyboard `清单`/`故事板`/`时间线` | Buttons (toggle) | `setView` switches render | None |
| Storyboard row `编辑详情` | Button | Opens shot-detail drawer | None |
| `上移一位` / `下移一位` (drawer) | Button | `move()` sets `pendingReorder` + invalidates plan | None (local; commit below) |
| `批量操作` | Button | Opens BATCH drawer | None |
| `应用到所选镜头草稿` | Button | `applyBatchShotType` writes `edits` | None (local) |
| `批量设置资产状态` | Button | `setEpisodePlanShotAssetState` per shot | POST `/api/v1/shots/{shot_id}/asset-state-bindings` (`episodePlanTableApi.ts:25-29`) |
| `批量 Production Ready` | Button | `markEpisodePlanShotReady` per shot | POST `/api/v1/projects/shots/{shot_id}:mark-production-ready` (`episodePlanTableApi.ts:31-33` — see Risk B) |
| `重排、拆分与复制` | Button | Opens STRUCTURE drawer | None |
| `加入复制计划` | Button | `addCopy()` adds to `copies` | None (local) |
| `加入拆分计划` | Button | `addSplit()` adds `splits` | None (local) |
| `预览重排 / 拆分` | Button | `semanticPlanning` → `planShotEdit` | POST `/api/v1/episodes/{episode_id}/shot-edit:plan` (`shotEditingApi.ts:36-40`) |
| `确认语义编辑` | Button | `semanticCommitting` → `commitShotEdit` | POST `/api/v1/episodes/{episode_id}/shot-edit:commit` (`shotEditingApi.ts:42-46`) |
| `校验字段 / 复制计划` | Button | `planning` → `planStoryboardBatch` | POST `/api/v1/projects/episodes/{episode_id}/storyboard:plan` (`generated/api.ts:691-692`) |
| `确认字段 / 复制` | Button | `committing` → `commitStoryboardBatch` | POST `/api/v1/projects/episodes/{episode_id}/storyboard:commit` (`generated/api.ts:695-696`) |
| `创建分组` (ShotGroupPlanner) | Button (submit) | `createShotGroup` | POST `/api/v1/episodes/{episode_id}/shot-groups` (`shotGroupsApi.ts:32-36`) |
| Group `↑` / `↓` | Button | `moveGroup` → `reorderShotGroups` | POST `/api/v1/episodes/{episode_id}/shot-groups:reorder` (`shotGroupsApi.ts:56-63`) |
| Group `归档` | Button | `archiveShotGroup` | POST `/api/v1/shot-groups/{group_id}:archive` (`shotGroupsApi.ts:50-54`) |
| `{code} 场景` select (assignment row) | Select | `assignShotScene` | POST `/api/v1/shots/{shot_id}:assign-scene` (`shotGroupsApi.ts:38-42`) |
| `{code} 分组` select (assignment row) | Select | `setMembership` → `replaceShotGroupMembers` | PUT `/api/v1/shot-groups/{group_id}/members` (`shotGroupsApi.ts:44-48`) |
| `生成差异预览` | Button | `preview` → `planBeatReplan` | POST `/api/v1/episodes/{episode_id}/shot-groups/{group_id}/replan:plan` (`beatReplanApi.ts:28-32`) |
| `确认应用以上差异` | Button | `apply` → `applyBeatReplan` | POST `/api/v1/episodes/{episode_id}/shot-groups/{group_id}/replan:apply` (`beatReplanApi.ts:34-43`) |
| `接受合并建议` | Button | `decideAssetProposal(action=MERGE_EXISTING)` | POST `/api/v1/asset-proposals/{proposal_id}:decide` (`assetProposalsApi.ts:19-29`) |
| `保留为独立角色` | Button | `decideAssetProposal(action=CREATE_NEW)` | POST `/api/v1/asset-proposals/{proposal_id}:decide` |
| `拒绝建议` | Button | `decideAssetProposal(action=REJECT)` | POST `/api/v1/asset-proposals/{proposal_id}:decide` |
| Storyboard workspace load | Query | `getStoryboardWorkspace` | GET `/api/v1/projects/episodes/{episode_id}/storyboard` (`generated/api.ts:687-689`) |
| Shot-group workspace load | Query | `getShotGroupWorkspace` | GET `/api/v1/episodes/{episode_id}/shot-groups` (`shotGroupsApi.ts:28-30`) |
| Asset bible load | Query | `getEpisodePlanAssets` | GET `/api/v1/projects/{project_id}/asset-bible` (`episodePlanTableApi.ts:21-23`) |

### 1.5 Happy-path click sequence

**(a) Plan an episode from source text into shots.**
1. Import the source text + run the AI breakdown in the **Story workspace** (`故事工作区`); the plan page explicitly states AI 拆解 lives there (`EpisodePlanPage.tsx:101`). A `ScriptBreakdownDraft` with `application_status: APPLIED` is produced; `EpisodeSourcePassage.tsx:13-16` derives `source_document_version_id` from the draft with `application_status === "APPLIED"`.
2. Return to the plan page; tab `镜头分镜板` shows the identity-stable shot list (`StoryboardBatchWorkbench`).
3. On the plan page, tab `场景与分组` → `ShotGroupPlanner`: `创建分组` to make a group (kind 节拍/对白/动作/蒙太奇/自定义), assign shots to a group and a scene via the row selects, then use `生成差异预览` + `确认应用以上差异` in `SelectedBeatReplanPanel` to re-derive a specific Beat against an `AI 拆解草稿` (this is what actually *creates/changes shots from AI text*).
4. To add/copy/split shots, STRUCTURE drawer: set `复制来源`+`新编号` then `加入复制计划`; or pick `拆分镜头` + `第一段时长 ms` + `第一段编号`/`第二段编号` then `加入拆分计划`; then `预览重排 / 拆分` → `确认语义编辑`.
5. If source passage viewing is needed, tab `原文证据` → `EpisodeSourcePassage` (read-only, `SourcePassagePanel` with `← 前一片段`/`后一片段 →`).

**(b) Open the Director Desk for a shot.**
1. On plan page, `进入导演台` (or `routes.directorDesk`) → `/projects/:projectId/episodes/:episodeId/direct` (no shotId).
2. The chooser screen (`DirectorDeskPage.tsx:250-255`) `先选择要精修的镜头` lists `{code}` links; click one → `/direct/:shotId`.
3. Inside, use the `镜头导航` list (ShotNavigator) or the `J`/`K` stepper (`:236-237`) to switch shots.

**(c) Fill DirectorIntent (camera/subject/action/scene).**
1. In director desk, open `检查器` (I) → inspector tab `画面` (`InspectorTab="picture"`, default) → `DirectorIntentEditor` (`:310`).
2. `景别` (ChooseGrid) sets `shot_type`; `构图` sets `composition.preset`. Fields `主体动作`, `画面创作意图`, `表演与情绪` (`情绪`, `强度`, `身体动作`, `面部动作`, `视线`), `运镜` (`运动方式`, `方向`, `强度`, `Curve`, `Mode`, `Prompt fallback`), and `时长与环境` (`目标时长`, `环境`, `连续性`). The `2D 站位预演` (StagingBoard) and `可选 3D 导演预演` also write into this draft.
3. Press `保存 revision {n+1}` (or `Ctrl S`) → creates a new revision. API: POST `/api/v1/projects/shots/{shot_id}/revisions` (`directorIntentClient.ts:30-39`). Note `scene` here is not a standalone field; scene is assigned in the plan page (`assign-shot-scene`) and only surfaced read-only on the desk.

**(d) Set first/last-frame continuity via FrameBridge / StagingBoard.**
1. Inspector tab `连贯性` (F) → `FrameBridgeControls` (`:314`).
2. First frame: click `继承上一镜尾帧` (inherit last-frame of previous shot) or `用当前候选帧` (set current verified IMAGE candidate as first frame), or drag a candidate onto `拖到这里设为首帧`.
3. For video candidates, the code first creates a `FrameAnchor` then sets it: POST `/api/v1/media-versions/{mid}:create-frame-anchor` + POST `/api/v1/frame-bridges/{transition_id}/current-frame` (`FrameBridgeControls.tsx:82-85`, `frameBridgeClient.ts:88-99`). Inherit: POST `/api/v1/frame-bridges/{transition_id}/inherit`. Lock/unlock: POST `/api/v1/frame-bridges/{transition_id}/lock` / `/unlock`.
4. Last frame: `从当前视频提取尾帧` (or `重新提取当前视频尾帧`, or drag onto `拖到这里提取尾帧`) → creates `LAST_FRAME` anchor then POST `/api/v1/frame-bridges/{transition_id}/source-frame` (`FrameBridgeControls.tsx:87-91`).
5. Optionally set staging via the intent editor's `2D 站位预演` (StagingBoard), which patches `blocking_summary` + `camera_plan` into the *draft*; then `保存 revision`.

**(e) Reroll / compare a candidate.**
- **Reroll**: `重抽当前镜头` (primary, disabled without `parentVariantId` or `can_generate`) → dialog `为什么要重抽 {code}？`; pick a reason (`人物不一致`, `动作不对`, `构图不对`, `镜头语言不对`, `连续性问题`, `其他`) → `创建并排队` → POST `/api/v1/generation/variants/{parent_variant_id}/reroll` (`DirectorDeskClient.ts:55-62` → `variants.py:26-27`). Job id is shown in feedback: `新候选已排队（任务 {job.id}）。`.
- **Compare**: `并排比较` (disabled when `candidates.length < 2`) → `CandidateCompareDialog` (`2-up / 4-up Compare`). Tick `Take {n}` checkboxes (up to 4), `全部播放`/`全部暂停` (Space), `回到开头`.

---

## 2. Director Desk Page

### 2.1 Route + page title
- **Route**: `/projects/:projectId/episodes/:episodeId/direct` (any-shot) and `/projects/:projectId/episodes/:episodeId/direct/:shotId` — `routeRegistry.ts:58-61` (`routes.directorDesk`), pathPattern `routeRegistry.ts:100-101`, title `导演工作台`.
- When no `shotId` is in the URL the page renders a shot **chooser** instead of the desk (`DirectorDeskPage.tsx:250-255`).

### 2.2 Purpose
Per-shot director workstation: review/edit the DirectorIntent (V3), inspect/select/adopt generated candidates, manage first/last-frame continuity (Frame Bridge), and reroll/compare candidates — all against a bounded read-model with explicit permission flags.

### 2.3 Layout (exact labels)
- **Context bar** (`:262-271`): breadcrumb `项目 / 本集 / {selected.code}`; buttons `镜头列表 N` (toggle nav), `检查器 I` (toggle inspector), `查看原文 O` (opens source drawer), `并排比较` (disabled if <2 candidates), link `集审核`, primary button `重抽当前镜头`.
- **Main grid** `director-main-grid` (`:274`): left `ShotNavigator` (`镜头导航`), center stage, right inspector.
- **Stage toolbar** (`:287-294`): kicker `当前镜头`, heading `{code} · {title}`, stepper `{currentIndex+1} / {total}` with `J`/`K` prev/next.
- **DirectorMediaStage** (`:295`) shows `{code} 当前选中媒体`, badges `{shot_type}`, `{x}s`, `修订已锁定`/`可编辑`; empty state `等待首个画面`.
- **Frame Bridge strip** (`:296-298`): `上一镜尾帧` / `本镜首帧` / `本镜尾帧` nodes; button `管理来源与锁定`.
- **Inspector** (`:302-322`): title `Inspector` + `STATUS_LABELS[status]`; tabs `画面` / `角色场景` / `生成` / `连贯性` / `声音` / `高级` (`INSPECTOR_TABS`, `:24-31`). Tab bodies:
  - `画面` → `DirectorIntentEditor`
  - `角色场景` → `本镜资产与角色` + `打开资产圣经` + `ShotAssetSection`
  - `生成` → `本镜生效生成配置` + `管理生产设置`; button `创建新候选分支` or link `预检并生成首个候选`
  - `连贯性` → `FrameBridgeControls` + `ContinuityPanel`
  - `声音` → `DirectorSoundInspector`
  - `高级` → `{修订号, 镜头 ID, 冻结, 活动任务}` read-only
- **Timeline section** (`:326-342`): `Takes & Timeline` toggle, `{n} 个候选 · 本集 {m} 镜 · {k} 已批准`, `DirectorTakeAdoption`, `+ 重抽候选` / `+ 生成首个候选`, empty `还没有候选`.
- **Feedback** line (`:345`): `role="status"` — shows `feedback` or `{n} 个生成任务正在执行`.

### 2.4 Interactive controls → action / API

| Exact visible label | Type | Action | API endpoint + method |
|---|---|---|---|
| `镜头列表 N` | Button (ghost) | Toggle nav drawer | None |
| `检查器 I` | Button (ghost) | Toggle inspector drawer | None |
| `查看原文 O` | Button (ghost) | Open source drawer (`DirectorSourcePassage`) | None (fetch via `getEpisodeProduction`/`listScriptBreakdownDrafts`) |
| `并排比较` | Button (ghost) | Open `CandidateCompareDialog` | None |
| `集审核` | Link | `/projects/:p/episodes/:e/review` | None |
| `重抽当前镜头` | Button (primary) | Open resample dialog | None (dialog confirms → reroll) |
| `管理来源与锁定` | Button | `setInspectorTab("continuity")` | None |
| Inspector tab `画面` | Button (tab) | `setInspectorTab("picture")` | None |
| Inspector tab `角色场景` | Button (tab) | `setInspectorTab("assets")` | None |
| Inspector tab `生成` | Button (tab) | `setInspectorTab("generate")` | None |
| Inspector tab `连贯性` | Button (tab) | `setInspectorTab("continuity")` | None |
| `创建新候选分支` / `预检并生成首个候选` (生成 tab) | Button / Link | Open resample dialog / `routes.generation(...)` | None |
| Take card `采用` (DirectorTakeAdoption) | Button | `requestAdoption` → confirm → `selectMutation` | POST `/api/v1/media-versions/{media_version_id}:select` (`DirectorDeskClient.ts:27-33`, backend `reviews.py:187`) |
| `用于交付` (FORMAL+VIDEO take) | Button | Opens approval dialog | `approveFormalCandidate` → two calls (see below) |
| Approval `确认批准用于交付` | Button | `approveMutation` | POST `/api/v1/reviews/formal-selection:preflight` then POST `/api/v1/reviews/formal-selection:commit` (`DirectorDeskClient.ts:35-53`, `reviews.py:171,179`) |
| `拖到这里采用` (adoption slot) | Drag-drop | Drop a take → `requestAdoption` confirm | Same POST select as above |
| Resample reason buttons (`人物不一致` etc) | Button | `setRerollReason` | None |
| Resample `创建并排队` | Button | `rerollMutation` | POST `/api/v1/generation/variants/{parent_variant_id}/reroll` (`DirectorDeskClient.ts:55-62`) |
| Resample `取消` | Button | Close dialog | None |
| FrameBridge `继承上一镜尾帧` / `重新继承上一镜尾帧` | Button | `run("inherit")` | POST `/api/v1/frame-bridges/{transition_id}/inherit` (`frameBridgeClient.ts:76-86`) |
| FrameBridge `用当前候选帧` | Button | `run("candidate")` | POST `/api/v1/media-versions/{mid}:create-frame-anchor` (if video) + POST `/api/v1/frame-bridges/{transition_id}/current-frame` |
| FrameBridge `从当前视频提取尾帧` / `重新提取当前视频尾帧` | Button | `run("extract-end")` | POST `/api/v1/media-versions/{mid}:create-frame-anchor` (LAST_FRAME) + POST `/api/v1/frame-bridges/{transition_id}/source-frame` |
| FrameBridge `锁定边界` / `解除锁定` | Button | `run("lock"/"unlock")` | POST `/api/v1/frame-bridges/{transition_id}/lock` / `/unlock` |
| FrameBridge `拖到这里设为首帧` / `拖到这里提取尾帧` | Drag-drop | `requestDrop` → confirm → same candidate/source-frame commands | — |
| DirectorIntentEditor `保存 revision {n+1}` | Button | `saveDirectorIntentRevision` | POST `/api/v1/projects/shots/{shot_id}/revisions` (`directorIntentClient.ts:30-39`) |
| DirectorIntentEditor `放弃修改` | Button | Reset to baseline | None |
| DirectorIntentEditor `保存后冻结` | Checkbox | `setFreeze` | Passed as `freeze` in the revision POST |
| Desk load | Query | `loadDirectorDesk` | GET `/api/v1/projects/{project_id}/episodes/{episode_id}/director-desk?nav_radius=25[&shot_id=…]` (`DirectorDeskClient.ts:19-25`) |

### 2.5 Happy-path click sequence (desk)
For (c),(d),(e) see §1.5 — the controls are the same components. From a shot in the desk:
- To choose a shot at the drawer: `镜头列表` → click a `director-shot-card` (or `J`/`K`).
- The page never silently opens the first shot: with no `shotId` it shows the chooser (`DirectorDeskPage.tsx:250`).

### 2.6 State / status semantics, empty/disabled/error states, risks

**Shot status labels** (`:33-41`, `ShotNavigator.tsx:7-15`):
`DRAFT=草稿`, `READY=可生成`, `PRODUCTION_READY=可生成`, `RUNNING=生成中`, `FAILED=失败`, `SELECTED=已选中`, `APPROVED=已批准`; unknown statuses fall back to the raw string.

**Candidate state machine** (types.ts `DirectorDeskCandidate`): `selected`, `approved`, `is_stale`, `stage` (`FORMAL`/`PROXY`/`KEYFRAME`), `integrity_status` (`VERIFIED`). Adoption uses `selectionTypeForCandidate` → `.stage`:
- `FORMAL` → `FORMAL_SELECTION`; `PROXY` → `PROXY_WINNER`; `KEYFRAME`+IMAGE → `KEYFRAME`; else `null`.
- A `null` selection type disables the take (`DirectorTakeAdoption.tsx:16`).
Frame source validity (`frameCandidateDrag.ts:13-18`): only `IMAGE`/`VIDEO`, `VERIFIED`, not stale.

**Disabled rules**:
- `重抽当前镜头` disabled unless `parentVariantId && can_generate` (`:270`).
- `并排比较` disabled unless `candidates.length >= 2` (`:268`).
- `用于交付` requires `can_approve` and not `approveMutation.isPending` (`:338`).
- FrameBridge buttons disabled on `!canEdit`, busy, or a candidate/source issue (`:176-190`).
- Inspector editor `canEdit={desk.data?.permissions.can_edit}` (`:310`).

**Empty states**:
- No shotId & empty episode → `本集还没有镜头，请先在分集规划中创建镜头。` (`:254`).
- Desk loaded but no shot → `本集还没有镜头` (with `前往分集规划`) (`:258`).
- No candidates → `还没有候选` / `生成后可在这里同屏比较；选择与批准始终是两个动作。` (`:340`).
- Media empty → `等待首个画面` / `当前镜头还没有已选中或已批准的媒体。` + `生成候选` (`DirectorMediaStage.tsx:49`).
- `找不到可编辑 revision → {code} 尚无可编辑 revision` + `先创建镜头初始修订...` (`DirectorIntentEditor.tsx:296`).
- Sound: `本镜尚未绑定角色...`; `本集还没有持久化 BGM、SFX...` (`DirectorSoundInspector.tsx:44,51`).

**Error states**:
- Desk query error → `导演台暂时无法打开` + `返回分集规划` (`:257`).
- Chooser error → `无法读取镜头列表` + `重试` (`:252`).
- Continuity query error → `连续性对照读取失败：…` (`:316`).
- Save conflict (409): `保存冲突` + `刷新最新版本` (`DirectorIntentEditor.tsx:316`); Frame Bridge 409 → `边界版本冲突` + `刷新最新 Frame Bridge` (`FrameBridgeControls.tsx:195-198`); Reroll/approve errors → feedback `重抽提交失败：…` / `批准未提交：…`.
- Video play error → `无法播放当前视频` + `重新加载` (`DirectorMediaStage.tsx:96`).

**Unauthorized / read-only**: `can_edit` (intent editor + FrameBridge edits), `can_generate` (reroll + generate), `can_approve` (approve), plus `read_only` which disables the ShotNavigator (`canEdit = can_edit && !read_only`, `:282`).

**Known risk areas** (from code, not tests):
- **Risk A — `markEpisodePlanShotReady` wrong path.** `episodePlanTableApi.ts:31-33` POSTs `/api/v1/shots/{shot_id}:mark-production-ready`; the backend route is `projects.py:377` under `prefix="/projects"`, i.e. `/api/v1/projects/shots/{shot_id}:mark-production-ready`. The generated client (`generated/api.ts:744`) uses the correct `.../projects/shots/...` path. The episode-plan-v2 client is missing the `/projects` segment and will likely 404 the `批量 Production Ready` action.
- **Risk B — `createFrameAnchor` role/position for end frame.** `FrameBridgeControls.tsx:90` calls `createFrameAnchor(..., { position_mode: "LAST_FRAME", role_hint: "LAST_FRAME" })`. The generated OpenAPI union for `role_hint` is `'FIRST_FRAME' | 'CURRENT_FRAME' | 'LAST_FRAME'` (`generated/api.ts:441`) so `LAST_FRAME` is valid; but the generated `role_hint` union does **not** include a `CURRENT_FRAME`-equivalent for a first-frame-of-next from the previous tail, and `FIRST_FRAME` is reused both as a real first frame and as an extract target — worth confirming the backend discriminates these.
- **Risk C — reroll default reason.** `rerollReason` initialises to `"USER_REROLL"` (`DirectorDeskPage.tsx:98`), which is in the `RerollReasonCode` union (`types.ts:110-121`) but is **not** among the six dialog reason buttons (`REROLL_REASONS` `:43-50`), so no reason chip starts selected; a user who reopens the dialog without choosing a reason would submit `USER_REROLL`.
- **Risk D — `approveMutation` feedback string on 409.** The preflight throws with `blockers` joined, surfaced as `批准未提交：…`; uncaught front-end state after a failed `refreshDesk` is not handled (only `await refreshDesk()` on success).
- **Risk E — inactive `AssetProposalReviewPanel` on plan page.** The panel is present in `episode-plan-v2` but only mounted on the Story workspace (`StoryWorkspacePage.tsx:160`). A reader of the Episode Plan page will not find the asset-identity review UI there.
- **Risk F — `StageBoard`/`StagingBoard` write to draft only.** Both the `2D 站位预演` and `3D` previs update the *local* `draft` via `onChange`; none of it persists until the user clicks `保存 revision {n+1}`. There is no auto-save of the staging patch.
- **Risk G — `approveFormalCandidate` idempotency.** It posts an `idempotency_key` only via `rerollDirectorCandidate` (`DirectorDeskClient.ts:56`); the preflight/commit path (`:35-52`) carries `plan_hash` but no `idempotency_key`, so a double commit could double-approve.
- **Risk H — nav drawer/backdrop and modal Escape interplay.** `modalOpen` is `sourceOpen||resampleOpen||compareOpen||approvalOpen||adoptionOpen` (`:101`); Escape closes only modals first, then drawers (`:169-191`). The `[`/`]` candidate focus shortcuts are guarded by `!modalOpen` but run even when the inspector/nav drawer is open — minor UX ambiguity.

---

## 3. Backend route map (for button→API traceability)

| Web client function | Endpoint (method) | Backend file:line |
|---|---|---|
| `getDirectorDesk` | GET `/api/v1/projects/{pid}/episodes/{eid}/director-desk` | `director_desk.py:20-24` |
| `inheritFrameBridge` | POST `/frame-bridges/{tid}/inherit` | `director_desk.py:44-47` |
| `setFrameBridgeCurrentFrame` | POST `/frame-bridges/{tid}/current-frame` | `director_desk.py:58-61` |
| `setFrameBridgeSourceFrame` | POST `/frame-bridges/{tid}/source-frame` | `director_desk.py:72-75` |
| `setFrameBridgeLocked(lock)` | POST `/frame-bridges/{tid}/lock` | `director_desk.py:96-98` |
| `setFrameBridgeLocked(unlock)` | POST `/frame-bridges/{tid}/unlock` | `director_desk.py:106-108` |
| `switchFrameBridge` body validation | (schemas) | `schemas/director_desk.py:8-30` (must provide exactly one of `media_version_id`/`frame_anchor_id` — `:21-25`) |
| `planShotEdit` / `commitShotEdit` | POST `/episodes/{eid}/shot-edit:plan` / `:commit` | `shot_editing.py:25-43` |
| `getShotEditingContext` | GET `/episodes/{eid}/shot-edit` | `shot_editing.py:17-22` |
| shot-group CRUD | GET/POST `/episodes/{eid}/shot-groups`, PUT `/shot-groups/{gid}`, POST `/shot-groups/{gid}:archive`, PUT `/shot-groups/{gid}/members`, POST `/episodes/{eid}/shot-groups:reorder` | `shot_groups.py:24-88` |
| `assignShotScene` | POST `/shots/{shot_id}:assign-scene` | `shot_groups.py:32-39` |
| `listAssetProposals` | GET `/projects/{pid}/asset-proposals` | `asset_proposals.py:11-13` |
| `decideAssetProposal` | POST `/asset-proposals/{pid}:decide` | `asset_proposals.py:16-22` |
| `planBeatReplan` / `applyBeatReplan` | POST `/episodes/{eid}/shot-groups/{gid}/replan:plan` / `:apply` | `beat_replan.py:17-43` |
| `rerollGenerationVariant` | POST `/generation/variants/{vid}/reroll` | `variants.py:26-45` |
| `selectMediaVersion` | POST `/media-versions/{mid}:select` | `reviews.py:187` |
| `preflight/commitFormalSelection` | POST `/reviews/formal-selection:preflight` / `:commit` | `reviews.py:171,179` |
| `createFrameAnchor` | POST `/media-versions/{mid}:create-frame-anchor` | `timeline.py:189` |
| `getShotContinuityContext` | GET `/shots/{sid}/continuity-context` | `production.py:40-44` |
| `getEpisodeProduction` | GET `/episodes/{eid}/production` | `production.py:16-21` |
| `markShotProductionReady` | POST `/projects/shots/{sid}:mark-production-ready` | `projects.py:377` |
| `setShotAssetState` | POST `/shots/{sid}/asset-state-bindings` | `asset_bible.py:205` |

---

## Questions to resolve

1. **`EpisodePlanIntegration` source is missing.** Only `episode-plan-v2/EpisodePlanIntegration.test.tsx` exists; no matching `.tsx`/component file. Its tests compose `AIDraftReviewPanel` + `SelectedBeatReplanPanel` with a shared `QueryClient`. Is the harness component meant to still exist, or was it removed in favor of the tabbed `EpisodePlanPage`?
2. **`批量 Production Ready` path.** `episodePlanTableApi.ts:31-33` uses `/api/v1/shots/{id}:mark-production-ready` but the backend route lives under `/projects`. Is this a stale client path (404), or is there a second router that serves `/shots/{id}:mark-production-ready` without the `/projects` prefix? (`generated/api.ts:744` proves the correct path includes `/projects`.)
3. **First-frame anchor semantics.** Confirm whether `createFrameAnchor(..., { position_mode: "FIRST_FRAME", role_hint: "FIRST_FRAME" })` for setting the *current* shot's first frame should be distinguished from a literal first-frame extraction of a video's own first frame, and how the backend maps `role_hint` from the generated union (`generated/api.ts:441`).
4. **Reroll reason default.** `rerollReason` starts at `"USER_REROLL"` which is not displayed among the six chips; confirm that submitting with no explicit selection is intended (or that a chip should be pre-selected).
5. **`AssetProposalReviewPanel` placement.** It is only wired into the Story workspace; should the Episode Plan page surface asset-identity review too, or is the Story workspace the intended home?
6. **Approval idempotency.** `approveFormalCandidate` sends `plan_hash` but no `idempotency_key`; is a repeat commit guarded server-side?
7. **`Approved_count` vs `selected`.** The desk shows `approved_count` at the episode level (`types.ts:72`) and per-take `approved`; confirm whether "已采用" (`selected`) and "已批准" (`approved`) can coexist on the same candidate and how the UI derives the `已采用`/`已批准` caption when both are set (`DirectorTakeAdoption.tsx:177`).
8. **`ContinuityPanel`/EpisodeSceneRanges** are rendered but not in scope of the requested files; confirm their state/error contracts if they influence the same shot plan.
