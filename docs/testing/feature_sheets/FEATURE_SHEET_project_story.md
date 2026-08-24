# Feature Sheet — Project Creation & Story/Script

Source basis: concrete front-end TSX/TS + backend route files in `apps/web` and `apps/api`. All labels below are transcribed verbatim from JSX; HTTP method + path confirmed from `apps/web/src/generated/api.ts` (for most) and the backend `@router` decorators. `apps/web/src/features/episode-plan-v2/AssetProposalReviewPanel.tsx` and `assetProposalsApi.ts` were read because StoryWorkspacePage Stage 4 renders them.

---

## 0. Route map

| Page / component | Route | Router source |
|---|---|---|
| ProjectsPage | `/projects` (index child of `/projects` AppShell) | `apps/web/src/app/router.tsx:44-49` |
| ProjectHomePage | `/projects/:projectId` (index) | `apps/web/src/app/router.tsx:51-56` |
| StoryWorkspacePage | `/projects/:projectId/story` | `apps/web/src/app/router.tsx:56` |
| ProjectCreateWizard | not routed — rendered inside ProjectsPage hero | `ProjectsPage.tsx:59-65` |

---

## 1. ProjectsPage (`apps/web/src/pages/ProjectsPage.tsx`)

### 1.1 Route + title
- Route `/projects`. Header eyebrow **"项目工作区"**, `<h2>` **"从一个项目继续创作"**, muted line **"选择现有项目继续下一步，或创建一个新的本地项目。"** (lines 57-58).

### 1.2 Purpose
Creator-first project entry: list/search existing local projects, surface one clear next action per project, and launch the project-creation wizard.

### 1.3 Layout (visible panels)
- **Hero** (`.projects-hero`) left text + embedded `ProjectCreateWizard` (line 59).
- **Filters bar** `.project-filters`, `role="search"`, `aria-label="筛选项目"` (line 68).
- **Empty/loading/error** states (lines 73-81).
- **最近项目** section (`panel-heading`: `<h3>最近项目</h3>` + muted **"按最近更新排序"**) and card grid (lines 83-86).
- **其他项目** section (`<h3>其他项目</h3>`) and card grid (lines 87-90).

### 1.4 Interactive controls

| Visible label | Type | What it does | API (method + path) |
|---|---|---|---|
| **新建项目** | button (secondary) | Opens `ProjectCreateWizard` dialog | — (opens dialog) |
| **搜索项目** | text input (`placeholder="标题或 code"`) | Filters visible projects by title/code (case-insensitive) | client-side on `listProjects` data |
| **项目状态** | select | Filter by status | client-side on `listProjects` data |
| （select options）**全部状态 / 生产中 / 待配置 / 已暂停 / 已归档** | option | Value = `""`/`ACTIVE`/`DRAFT`/`PAUSED`/`ARCHIVED` | — |
| Project card status pill | `status-pill` | Text from `statusLabels`: **生产中/待配置/已暂停/已归档** (line 10-12) | derived |
| **修订 {revision}** | `<small>` | Shows `project.revision` | derived |
| **继续生产 / 完成项目设定 / 查看暂停原因 / 查看归档项目** (`nextStep`) | `<Link>` `.primary-action .project-next-action` | Navigates to `/projects/{id}`. `aria-label="{title}：{nextStep}"`. Text chosen by status (lines 14-19, 51) | — (router link) |

Data: `listProjects({limit:100})` (GET `/api/v1/projects?limit=100`). `listProfiles()` GET `/api/v1/profiles` (passed to wizard).

### 1.5 Happy-path click sequence (list page)
1. On `/projects`, click **新建项目**.
2. (Filtering, optional) type into **搜索项目**; change **项目状态**.
3. After wizard creates a project, the page `invalidateQueries(projects.lists())` and `navigate(/projects/{id})` (lines 61-64).

### 1.6 State semantics / edge cases
- Loading → `<p role="status">正在读取本地项目…</p>` (line 73). Error → `role="alert"` **"项目读取失败：{message}"** (line 74).
- Empty VISIBLE list: eyebrow **"尚未开始"** + `<h3>` = **"没有符合筛选条件的项目"** when items exist (else **"创建你的第一个项目"**) (line 78); muted note differs accordingly (line 79).
- `partitionRecentProjects(visible, 4)` splits newest 4 by `updated_at` into **最近项目**, the rest into **其他项目** (`projectRecency.ts:22-26`). Recent/re-ordered deterministically; legacy rows with no timestamp sort to the end (`projectRecency.ts:13`).
- Search trims lowercases and matches `${title} ${code}` (line 35); status filter is exact equality (line 36).

---

## 2. ProjectCreateWizard (`apps/web/src/features/projects/ProjectCreateWizard.tsx`)

### 2.1 Route + title
Not routed. Opens as a modal `role="dialog"` `aria-modal="true"` `aria-labelledby="project-create-title"`. Panel heading eyebrow **"步骤 {step}/6"** (`aria-live="polite"`), `<h3>` **"新建版本化项目"**, and a **关闭** button (line 72). Helper/screen-reader text (line 73): **"项目向导分六步完成。按 Escape 关闭向导，关闭后焦点返回“新建项目”按钮。"**

### 2.2 Purpose
Six-step guided creation of a new versioned local project with read-only preflights, explicit configuration route (production plan / profile bindings / delivery target) or deferred-blocked DRAFT.

### 2.3 Layout – the 6 steps (visible sections)
- **Step 1** `.wizard-grid` — project identity fields.
- **Step 2** `.wizard-grid` — publication spec fields.
- **Step 3** `.wizard-confirm` — storage-only preflight.
- **Step 4** `.wizard-confirm` — configuration route radios + profile bindings.
- **Step 5** `.wizard-grid` — production plan / delivery target fields.
- **Step 6** `.wizard-preview` — final plan summary + create button.

### 2.4 Interactive controls table

**Step 1** (`disabled` on next unless `title && code && seasons>=1 && episodes>=1`):

| Visible label | Type | Does | API |
|---|---|---|---|
| **项目标题** | text input (autofocus) | sets `values.title` | — |
| **项目 code** | text input, `placeholder="小写字母、数字、下划线"` | sets `values.code` (trimmed) | — |
| **季数** | number, min 1 | sets `values.seasons` | — |
| **每季集数** | number, min 1 | sets `values.episodes` | — |
| **下一步：发布规格** | button | `setStep(2)` | — |

**Step 2** (`disabled` on next unless `specsReady` — ratio, width≥64, height≥64, fpsNum>0, fpsDen>0, duration>0, language, subtitleMode, and (NONE or subtitleLanguage)):

| Visible label | Type | Does | API |
|---|---|---|---|
| **画幅比例** | text, `placeholder="例如 9:16（不自动选择）"` | `values.ratio` | — |
| **制作宽度 / 制作高度** | number, min 64 | `values.width/height` | — |
| **fps 分子 / fps 分母** | number, min 1 | `values.fpsNum/fpsDen` | — |
| **目标集时长（秒）** | number, min 1 | `values.duration` (×1000 → `target_duration_ms`) | — |
| **主语言** | text, `placeholder="例如 zh-CN"` | `values.language` | — |
| **字幕策略** | select: **未选择 / NONE / SIDECAR / BURN_IN / BOTH** | `values.subtitleMode` | — |
| **字幕语言** | text (shown only when mode ≠ NONE) | `values.subtitleLanguage` | — |
| **上一步** | secondary button | `setStep(1)` | — |
| **下一步：存储预检** | button | `setStep(3)` | — |

**Step 3**:

| Visible label | Type | Does | API |
|---|---|---|---|
| text **"预检只检查 code、目标目录和磁盘，不创建正式目录或数据库记录。"** | paragraph | — | — |
| **上一步** | secondary | `setStep(2)` | — |
| **运行只读存储预检** | button | `storagePlan.mutate()` → on success advance to step 4 if status ≠ BLOCKED | `planProjectCreation` **POST `/api/v1/projects:plan`** with `allow_unconfigured_capabilities:true`, `production_plan:undefined`, `profile_bindings:[]`, `delivery_target:undefined`. Label **"预检中…"** while pending. |
| Result line | `<p>` | `{plan.status} · 总集数 {total_episode_count}` (line 76) | derived |

**Step 4**:

| Visible label | Type | Does | API |
|---|---|---|---|
| text **"选择配置路线；系统不会暗选 H3、Profile、ProductionPlan 或交付目标。"** | paragraph | — | — |
| **现在显式配置** | radio (checked=`configureNow`) | sets `configureNow=true` | — |
| **稍后配置并接受阻塞** | radio (checked=`!configureNow && accepted`) | `configureNow=false; accepted=true` | — |
| `{capability}` (per published profile) | label + select | sets `profileBindings[capability]`; option **"暂不配置"** at `""`, others `{item.title}` | — |
| **"本机没有 Published Profile；请选择稍后配置，项目将保持阻塞。"** | muted (when no published) | — | — |
| **下一步：创建预览** | button, `disabled` unless `configureNow || accepted` | `setStep(5)` | — |

**Step 5** (`disabled` on final unless `configureNow && configuredReady`; `configuredReady` = planCode,planTitle,targetCode,targetTitle,targetPath all non-empty AND `Object.values(profileBindings).some(Boolean)`):

| Visible label | Type | Does | API |
|---|---|---|---|
| **方案 code / 方案标题** | text | `values.planCode/planTitle` | — |
| **交付目标 code / 交付目标标题** | text | `values.targetCode/targetTitle` | — |
| **项目内交付路径** | text, `placeholder="06_delivery/master"` | `values.targetPath` | — |
| (defer mode) text **"将创建 DRAFT，并保留 PROFILE / PRODUCTION_PLAN / DELIVERY_TARGET 三类真实 blocker。"** | paragraph | — | — |
| **上一步** | secondary | `setStep(4)` | — |
| **运行最终创建预检** | button | `finalPlan.mutate()` | `planProjectCreation` **POST `/api/v1/projects:plan`** (full `basePayload`). Label **"最终预检中…"**. |

**Step 6**:

| Visible label | Type | Does | API |
|---|---|---|---|
| plan status | `<strong>` | `{plan.status}` | — |
| `目标目录：{target_root_rel} · {season_count} 季 × {episode_count_per_season} 集` | paragraph | — | — |
| check list | `<ul>` | `PASS/FAIL · {check.code}` each | — |
| `创建后阻塞：{configuration_blockers.join("、") || "无"}` | paragraph | — | — |
| **返回修改** | secondary | `setStep(5)` | — |
| **确认创建 DRAFT** | button, `disabled` if plan.status === `BLOCKED` or pending | `create.mutate()` → `onCreated(project)` → navigates `/projects/{id}` | `createProject` **POST `/api/v1/projects`** (full `basePayload`). Label **"原子创建中…"**. Error shown `role="alert"`. |

`basePayload` (lines 16-27): `code,title,season_count,episode_count,target_duration_ms,aspect_ratio,width,height,fps{numerator,denominator},primary_language,subtitle_mode,subtitle_language(if!=NONE),allow_unconfigured_capabilities:!configureNow`, plus (only if `configureNow`) `production_plan{code,title,plan{LOCAL_ONLY,...},...}`, `profile_bindings=Object.entries(profileBindings).filter(version)`, `delivery_target{code,title,spec{path_rel,...}}`.

### 2.5 Happy-path click sequence (create + set production plan)
1. On `/projects`, click **新建项目**.
2. Step 1 — type **项目标题** (e.g. `我的新剧`); type **项目 code** (e.g. `my_drama`); set **季数** to `1`; set **每季集数** to `10`. Click **下一步：发布规格**.
3. Step 2 — **画幅比例** `9:16`; **制作宽度** `1080`; **制作高度** `1920`; **fps 分子** `24`; **fps 分母** `1`; **目标集时长（秒）** `600`; **主语言** `zh-CN`; **字幕策略** `NONE` (or `SIDECAR` + **字幕语言**). Click **下一步：存储预检**.
4. Step 3 — click **运行只读存储预检**. If the plan is not `BLOCKED`, it advances to step 4.
5. Step 4 — to set the production plan now, select **现在显式配置**; pick a profile binding for each published capability if any are shown. Click **下一步：创建预览**.
   *(Alternatively choose **稍后配置并接受阻塞** to create a DRAFT carrying real blockers and defer the plan.)*
6. Step 5 — **方案 code** `plan_local`; **方案标题** `本地制作方案`; **交付目标 code** `delivery_local`; **交付目标标题** `本地交付`; **项目内交付路径** `06_delivery/master`. Click **运行最终创建预检**.
7. Step 6 — review plan status / target dir / PASS-FAIL checks / blockers. Click **确认创建 DRAFT**. On success you land on `/projects/{newId}`.

The production plan (`production_plan`) + `delivery_target` + `profile_bindings` are **embedded in the `createProject` payload**, not a separate `bindProductionPlan` call. (A standalone production-settings page exists at `/projects/:id/production-settings` — router line 60 — but it is out of scope here.)

### 2.6 State semantics / edge cases
- Modal is focus-trapped (Tab cycles first↔last), `Escape` closes, focus returns to **新建项目** (lines 34-69).
- **`values` are NOT reset on close/open** — re-opening the dialog shows the previously typed values (state only persists; `open` toggles, line 70). No reset path exists.
- `configuredReady` requires at least one profile binding; with zero published profiles the "现在显式配置" route cannot complete, so the user must defer (or the plan stays blocked).
- `planProjectCreation` with `allow_unconfigured_capabilities:true` and no plan is the **storage-only** preflight (step 3); `BLOCKED` status prevents advancing past step 3 and prevents create at step 6.
- `createProject` backend ignores `Idempotency-Key` (project codes unique) — `projects.py:62`. Re-creating the same `code` will instead error on the unique constraint.
- Mutations are disabled while pending (labels switch to 预检中…/最终预检中…/原子创建中…).

---

## 3. ProjectHomePage (`apps/web/src/pages/ProjectHomePage.tsx`)

### 3.1 Route + title
- Route `/projects/:projectId`. Eyebrow **"项目总览"**, `<h2>` = `current.title ?? "项目加载中…"`, status pill = `current.code` (line 62).

### 3.2 Purpose
Bounded, multi-season "project cockpit": from the project, start by goal (story/assets/episodes), or pick an episode to enter its production flow. Reads up to 8 seasons / 100 episodes each.

### 3.3 Layout (visible panels)
- **Header** (`panel-heading`): eyebrow + project title + code pill (line 62). Health blockers as `role="alert"` joined by `；` (line 63).
- **按目标开始** section — eyebrow **"按目标开始"**, `<h3>` **"你现在想从哪里继续？"**, status pill neutral **"目标分集：{code · title}**" (or **"尚无分集"**); optional `review-success` message (lines 64-72). Four goal cards:
  - pill **原文** → `<h3>` **"导入小说 / 剧本"**, muted **"下一步：为 {target} 核对原文权威并预览 AI 拆解。"**, link **"进入导入与策划"** (or `<small>` **"先创建季度与分集后可用"**) (line 68).
  - pill **分集** → `<h3>` **"继续未完成分集"**, muted **"下一步：从 {target} 的分集规划继续到导演、审核与交付。"**, link **"继续 {code}"** (primary) (or `<small>` **"当前项目还没有分集"**) (line 69).
  - pill **资产** → `<h3>` **"从已有分镜 / 资产开始"**, muted **"下一步：补齐 {target} 所需角色状态、场景参考和镜头使用关系。"**, link **"打开资产圣经"** (line 70).
  - pill **高级** → `<h3>` **"自由创作"**, muted **"下一步：围绕 {target} 检查依赖、分支实验与非线性工作流。"**, link **"打开高级画布"** (line 71).
- **分集** section (`panel`) — eyebrow **"分集"**, `<h3>` **"选择一集进入生产流程"**, pill neutral **"{n} 集 · 最多读取 8 季 / 每季 100 集"**; per-season groups and episode rows (lines 74-86).

### 3.4 Interactive controls

| Visible label | Type | Does | API |
|---|---|---|---|
| **进入导入与策划** | `<Link>` (to `/projects/{id}/episodes/{eid}/plan`) | Enter episode plan | — (router) |
| **继续 {code}** | `<Link className="primary-action">` (to `.../plan`) | Continue that episode plan | — |
| **打开资产圣经** | `<Link>` (to `/projects/{id}/assets?episode={eid}`) | Read asset bible | — |
| **打开高级画布** | `<Link>` (to `/projects/{id}/canvas?episode={eid}`) | Advanced canvas | — |
| **分集规划 / 导演台 / 整集生产** | `<Link className="secondary">` x3 per episode (to `.../plan`, `.../direct`, `.../run`) | Episode production entry points | — |

Data: `getProjectHealth` GET `/api/v1/projects/{id}/health`; `listSeasons` GET `/api/v1/projects/{id}/seasons`; `listEpisodes` GET `/api/v1/projects/seasons/{season_id}/episodes`; `project` via `listProjects({})` + `.find(id)` (GET `/api/v1/projects`).

### 3.5 Happy-path click sequence (enter production)
1. On `/projects/{id}`, read the four goal cards. To enter story/script import, the story workspace is at `/projects/{id}/story` (not rendered directly here), but **导入小说 / 剧本** links to the episode plan page.
2. Pick an episode under **选择一集进入生产流程** via **分集规划 / 导演台 / 整集生产**.

### 3.6 State semantics / edge cases
- `project` query takes `listProjects({})` and selects by id — if not present, `current` is `undefined` and title shows **"项目加载中…"** (no explicit "not found" branch) (lines 20-23, 62).
- Season/episode ordering: numeric `number ?? display_order ?? MAX_SAFE_INTEGER`, tie-break by `code` (lines 13, 34-37); capped at `SEASON_LIMIT=8` and `EPISODE_LIMIT_PER_SEASON=100` (lines 7-8).
- `targetEpisode` = first episode whose `production_status` is **not** `DELIVERED|APPROVED`, else the first episode (lines 14, 53). `targetLabel` = **"尚无分集"** when none.
- `allComplete` true when every loaded episode is delivered/approved → shows the `review-success` note and re-targets the earliest episode (line 54, 66).
- Pending state **"正在读取各季度分集…"**, error **"分集目录读取不完整：{msg}"**, no-group **"当前项目还没有季度；请先建立季度与分集。"** (lines 76-78).

---

## 4. StoryWorkspacePage (`apps/web/src/pages/StoryWorkspacePage.tsx`)

### 4.1 Route + title
- Route `/projects/:projectId/story`. Eyebrow **"项目级故事工作区"**, `<h2>` **"从长文原稿到可审核的生产事实"** (lines 53-54). Header actions: button **"收起详情"/"展开详情"** (toggles inspector, aria-label) and `<Link>` **"返回项目总览"** (to `/projects/{id}`) (lines 56-68).
- Intro muted (line 70-72): **"源文档先生成解析预览；AI 拆解只保存草稿。只有明确选择目标分集并点击应用，才会创建场次、镜头与对白。"**

### 4.2 Purpose
Project-level source-of-truth workflow in four stages: bibliography/creative library (bible) → long-source import → AI-draft review & explicit apply → character identity resolution. Source only becomes production facts after an explicit human apply to a chosen episode.

### 4.3 Layout — stage navigation + 4 stages
**Stage nav** `nav[aria-label="故事工作流阶段"]` (lines 75-89): four numbered buttons; active has `aria-current="step"`:
- **1 故事圣经** — "结构化创作资料库与设定" (anchor `#story-bible`)
- **2 导入长文** — "剧本长文解析与提交" (anchor `#story-import`)
- **3 审核拆解** — "AI 分镜预览与目标分集应用" (anchor `#story-review`)
- **4 裁决身份** — "AI 提取角色身份建议与建档" (anchor `#story-assets`)

**Three-pane layout** (lines 92-202): left `EntityRail` (`.navRail`) title **"工作流步骤"**; center main stage renders the active stage section; right `Inspector` title **"故事工作区证据"** (when open). `navOpen` default `innerWidth>960`, `inspectorOpen` default `>1280`.

**Stage 1 (`#story-bible`)** — `<span>1</span>`, `<h3>` **"故事圣经与创作资料"**, muted **"结构化资料的每次修改都会派生 revision，可比较、可回退。"** → renders `<CreativeLibrary>` (lines 103-116).

**Stage 2 (`#story-import`)** — `<span>2</span>`, `<h3>` **"导入小说、剧本或长文"**, muted **"先读取并展示段落与字符统计，再显式 commit；不修改原文件。"** → renders `<ScriptImportPanel>` (lines 118-131).

**Stage 3 (`#story-review`)** — `<span>3</span>`, `<h3>` **"预览 AI 拆解并人工应用"**, muted **"草稿不会自动落地。确认内容后选择真实目标分集，再执行应用。"** → renders `<AIDraftReviewPanel>` (lines 133-146).

**Stage 4 (`#story-assets`)** — `<span>4</span>**, `<h3>` **"处理提取出的角色身份建议"**, muted **"合并、独立建档和拒绝都需要人工动作，不做破坏性自动合并。"** → renders `<AssetProposalReviewPanel>` (lines 148-161).

**Inspector content** (lines 164-193): **"当前阶段"** = active stage title; **"工作流保障"** list = 不可变源文档存储 / AI 拆解草稿隔离 / 人工指定目标分集应用 / 角色建议显式合并/建档; **"快速跳转"** links **"打开资产圣经 →"** (`/projects/{id}/assets`) and **"管理生产设置 →"** (`/projects/{id}/production-settings`).

### 4.4 Interactive controls
- **收起详情 / 展开详情** button — toggles `inspectorOpen` (line 57-64). `aria-label="收起信息栏"/"展开信息栏"`.
- **返回项目总览** `<Link>` — to `/projects/{id}` (line 65).
- Stage nav buttons (the four stages above) — set `location.hash` (line 34-41) and drive the active stage. `navOpen` closes on small screens after select.
- Stage-specific controls live in the embedded panels (sections 5-9 below).

### 4.5 Happy-path click sequence (write/import/generate story + apply AI draft)
This page has **no free-text script editor or "generate story" button** — the "story/script" is produced by importing an existing document and letting the local LLM break it down. Sequence:
1. Navigate to `/projects/{id}/story`.
2. Stage 1 (story bible, optional): in CreativeLibrary, click **新建创作资料**, select a **类型** (e.g. `CHARACTER`), type **代码** (e.g. `CHAR_MOTHER`), **标题**, valid **初始内容 JSON** (object), **建立说明**. Click **建立并保存 revision 1**.
3. Stage 2 (**导入长文**): paste an absolute path into **电脑中的文档绝对路径** (or click **浏览…**) → click **建立源版本并解析预览** (see `{paragraph_count} 段 · {character_count} 字符` preview) → click **确认 commit（不覆盖母本）** → show **"已确认导入"**. Click **提交 AI 拆解任务** (requires local LLM status PASS). The job appears in **AI 拆解任务进度**; wait for it to reach `SUCCEEDED` (auto-refreshes every 3 s).
4. Stage 3 (**审核拆解**): in AIDraftReviewPanel, for the draft, choose a real target episode in **应用到成片目标集**, then click **应用到成片**. Confirmation shows **"应用完成：创建 {n} 场 · {n} 镜 · {n} 条对白；角色 …"**.
5. Stage 4 (**裁决身份**): in AssetProposalReviewPanel, decide each character proposal — **接受合并建议**, or type an independent code and **保留为独立角色**, or **拒绝建议**.

### 4.6 State semantics / edge cases
- `activeStage` is derived from `location.hash` (`stageFromHash`, line 19); unknown hash → `"bible"` (line 20).
- Missing `projectId` → `role="alert"` **"缺少项目上下文。"** (line 32).
- Inspector/nav default-open depends on viewport width; toggle buttons label reflects state (`收起详情` vs `展开详情`).
- No save-on-exit; incomplete state is held within each embedded panel. All AI outputs stay as drafts until stage-3 apply.

---

## 5. CreativeLibrary (Stage 1; `apps/web/src/features/projects/CreativeLibrary.tsx`)

Header: eyebrow **"FR-WRT-001 · 不可变历史"**, `<h3>` **"故事圣经与创作资料"**, pill **"{n} 项"** (line 31).

Layout: left list of entry buttons `{code}` / `{kind} · revision {revision_no}`; right detail pane; collapsible `<details>` creation form.

| Visible label | Type | Does | API |
|---|---|---|---|
| Entry buttons | button | select entry (`setSelectedId`) | — |
| **结构化内容 JSON** | textarea | edit content (JSON) | — |
| **变更说明** | input | edit change note | — |
| **保存新 revision** | button, `disabled` unless `editValid` (valid JSON + note) | `save.mutate()` | `createCreativeEntryRevision` **POST `/api/v1/creative-entries/{id}/revisions`** |
| **比较旧版 / 比较新版** | select (from revisions) | set `leftId/rightId` | — |
| **从所选旧版派生回退 revision** | button, `disabled` unless `restoreValid` (`leftId`, note, and `leftId != current_revision_id`) | `restore.mutate()` | `restoreCreativeEntryRevision` **POST `/api/v1/creative-entries/{id}:restore`** |
| **新建创作资料** | `<details><summary>` | toggle create form | — |
| **类型** | select (**请选择** + `SERIES_BIBLE/CHARACTER/SCENE/PROP/COSTUME/STYLE/VOICE`) | `kind` | — |
| **代码** | input `placeholder="CHAR_MOTHER"` | `code` | — |
| **标题** | input | `title` | — |
| **初始内容 JSON** | textarea (wide) | `initialContent` | — |
| **建立说明** | input (wide) | `initialNote` | — |
| **建立并保存 revision 1** | button, `disabled` unless `createValid` (kind+code+title+valid JSON+note) | `create.mutate()` | `createCreativeEntry` **POST `/api/v1/creative-entries`** |
| comparison diff | `<p>` — `{field}` + `{before} → {after}`; **"两版结构化内容一致。"** if none | read-only | `compareCreativeEntryRevisions` **GET `/api/v1/creative-entries/{id}/compare?left_revision_id=&right_revision_id=`** |

Edge cases: data queries keyed `["creative-entries", projectId]` and `["creative-entry", selected]`. `comparison` only runs when `leftId != rightId`. The `error` shown is the first of `create/save/restore/entries/detail/comparison` errors (a failed comparison, e.g. picking the same revision, surfaces here). `detail.data` effect auto-fills `editContent`, `leftId=revisions[1]`, `rightId=revisions[0]` (line 20). `parseContent` must be a JSON **object** (not array), else invalid.

---

## 6. ScriptImportPanel (Stage 2; `apps/web/src/features/projects/ScriptImportPanel.tsx`)

Header: `section-title` **"剧本文档导入"** + `<small>FR-ING-001 · TXT / Markdown / DOCX</small>` (lines 137-140).

| Visible label | Type | Does | API |
|---|---|---|---|
| **电脑中的文档绝对路径** | input (`placeholder="D:\Scripts\episode-01.docx"`) | `path`; `changePath` resets prepared/committed/errors | — |
| **浏览… / 选择中…** | button secondary | `browse()` — pick OS file | `pickLocalDocumentFile` **POST `/api/v1/system/dialogs:document-file`** |
| **建立源版本并解析预览 / 解析中…** | button, `disabled` unless `path.trim() && pending===null` | `preview()` | `importScriptDocument` **POST `/api/v1/projects/{id}/imports`** (`{source_path}`) |
| **确认 commit（不覆盖母本） / 提交中… / 已确认导入** | button primary, `disabled` unless `prepared && !committed && pending===null` | `commit()` | `commitImportSession` **POST `/api/v1/import-sessions/{session_id}:commit`** (`{expected_preview_hash}`) |
| preview block | `<ol>` paragraphs + **"{n} 段 · {n} 字符 · preview {hash16}"** | read-only | — |
| status text | `COMMITTED` → **"COMMITTED：源文档版本已保留，重复提交幂等。"** / **"PREVIEW_READY：尚未 commit，不会自动创建生产镜头。"** | read-only | — |

After `COMMITTED`, the **breakdown-trigger area** (lines 206-239):
- title **"提交 AI 剧本拆解任务（FR-WRT-007）"** + small **"SQLite durable Job · 本地 worker · 人工应用"**.
- muted **"提交后立即返回 Job；Ollama 由独立 worker 调用。拆解只生成结构化草稿与逐字原文引用，绝不自动批准、应用或覆盖生产事实。"**
- **提交 AI 拆解任务 / 正在验证 Profile 并排队…** (primary) — `disabled` unless `pending===null && isLLMPass`. `isLLMPass = llmStatus.status === 'PASS'` (line 118). `triggerBreakdown` = `syncLocalLLMProfile()` **POST `/api/v1/local-llm/profile:sync`** → `publishLocalLLMProfile(profileVersionId)` **POST `/api/v1/local-llm/profile:publish`** → `requestScriptBreakdown(sessionId, profileVersionId, crypto.randomUUID())` **POST `/api/v1/import-sessions/{session_id}:request-breakdown`** (sends `Idempotency-Key`; body `{profile_version_id}`).
- If LLM not ready: `role="status"` **"本地 LLM 未就绪（状态: {status}）。请确认 Ollama 已启动。"** (line 226-230).
- On success: `role="status"` **"AI 拆解任务已持久化排队（Job {id12}…）。可以刷新或关闭页面；草稿完成后仍需进入第 3 阶段人工审核并应用。"** + link **"前往第 3 阶段：审核 AI 拆解草稿 →"** (anchor `#story-review`) (lines 232-237).

**AI 拆解任务进度 monitor** (lines 242-309): title **"AI 拆解任务进度"** + <small>"刷新恢复 · 取消 · 失败后显式重试"</small>; muted **"页面关闭不会删除任务；执行依赖独立本地 worker。若长期停在 QUEUED，请到任务与机器页检查 WorkerSession。"** Lists `listJobs(projectId)` **GET `/api/v1/jobs?project_id=`** (`refetchInterval:3000`), filtered `type === "SCRIPT_BREAKDOWN_LOCAL_LLM"`, latest 5. Each card: state pill, phase + percent `<progress>`, optional `last_error_code`/detail, frame-feedbacks (`SUCCEEDED` → **"草稿已生成，但尚未应用。请进入审核阶段选择目标分集并人工确认。"**; `CANCEL_REQUESTED` → **"正在等待本地 Ollama 调用返回；取消会在草稿持久化前再次检查。"**). Actions:
- **查看 Job 详情** `<Link>` to `/projects/{id}/jobs?job={jobId}`.
- **取消任务 / 取消中…** — `cancelJob` **POST `/api/v1/jobs/{job_id}:cancel`** (states QUEUED/CLAIMED/RUNNING).
- **失败重试 / 重新排队中…** — `retryJob` **POST `/api/v1/jobs/{job_id}:retry`** (states FAILED/NEEDS_ATTENTION/ORPHANED). Blank note **"Attempt 失败重试保持同一 Job"**.
- Empty **"当前项目还没有 AI 拆解 Job。"**; loading **"正在读取已持久化任务…"**; error + **重新读取任务** button.

Edge cases: errors surface per action (选择器失败/解析失败/提交失败/发起 AI 拆解失败/取消或重试失败; messages include a fallback to paste an absolute path). `committed` blocks re-commit (button becomes **"已确认导入"**); server commit is idempotent (`idempotent`,`source_preserved:true`). The breakdown request sends a fresh `crypto.randomUUID()` Idempotency-Key on every click, so the UI does **not** deduplicate repeated clicks (each click enqueues a new Job with the same session). Page close does not delete jobs (SQLite durable Job + local worker).

---

## 7. AIDraftReviewPanel (Stage 3; `apps/web/src/features/projects/AIDraftReviewPanel.tsx`)

Header: eyebrow **"FR-WRT-007 · 本地 LLM 草稿"**, `<h3>` **"AI 辅助提取草稿"**, pill **"{n} 份"** (line 20). Guidance **"模型输出只保存为 DRAFT_READY；不会自动创建或覆盖母本场次、镜头或创作资料。采纳必须由后续显式人工流程完成。"** (line 21).

| Visible label | Type | Does | API |
|---|---|---|---|
| **重新读取草稿 / 正在重试…** | button secondary (on error) | `drafts.refetch()` | — |
| draft card `{source_document_title}` + `{status} · APPLIED/NOT_APPLIED` | article | — | `listScriptBreakdownDrafts` **GET `/api/v1/projects/{id}/script-breakdown-drafts`** |
| **{n} 个建议场次 · {n} 个建议镜头** | paragraph | derived counts | — |
| evidence line | paragraph | `COMPLETE` → **"证据完整 · 置信度 {pct}% · {n} 个待确认问题 · {n} 条原文引用"**; else **"历史草稿 · 未记录完整 Profile/置信度/问题/原文引用，不补造证据"** | — |
| info line | `<small>` | `{code} · Profile {version|'legacy 未记录'} · requires_human_action=true · automatic_apply=false` | — |
| **应用到成片目标集** | `<label>` + select of `{episode.code} · {episode.title}` (`aria-label="选择目标集"`) | set selected episode | — |
| **应用到成片 / 应用中…** | button, `disabled` unless `selected && !busy` | `apply.mutate({draftId,episodeId})` | `applyScriptBreakdownDraft` **POST `/api/v1/breakdown-drafts/{draft_id}:apply`** (`{episode_id}`) |
| **"项目暂无分集，请先创建集。"** | muted | when project has no episodes | — |
| success note | `role="status"` | **"应用完成：创建 {n} 场 · {n} 镜 · {n} 条对白；角色 {name}×{n}…"** | derived |
| ALREADY-applied note | `.frame-feedback.success` | **"已应用：本草稿的场次/镜头/对白已落地为生产实体，不能重复应用。"** | — |

Data: seasons `GET /api/v1/projects/{id}/seasons`; episodes `GET /api/v1/projects/seasons/{firstSeasonId}/episodes`. `defaultEpisodeId` = first episode of the first season; per-draft selected episode map `selectedEpisode[item.id] ?? defaultEpisodeId`.

Edge cases: `applied = application_status === 'APPLIED'` → the apply area is replaced by the cannot-reapply message; no re-apply path (non-idempotent, guarded in UI). Empty list → **"当前没有真实本地 LLM 拆解草稿；不会显示模拟建议。"** (line 40). Apply error shows per-draft `role="alert"`. `busy` tracks that this specific draft is applying (`apply.variables?.draftId === item.id`).

---

## 8. AssetProposalReviewPanel (Stage 4; `apps/web/src/features/episode-plan-v2/AssetProposalReviewPanel.tsx`)

Header: eyebrow **"AI Breakdown · 资产身份审核"**, `<h4>` **"角色资产建议"**, pill **"{n} 待处理"** (line 19). Guidance **"AI 只创建建议，不自动合并身份。合并只把建议指向既有资产；“保留为独立角色”会显式创建新资产。"** (line 20).

| Visible label | Type | Does | API |
|---|---|---|---|
| proposal `{name}` + `{scene_count} 个场次` | article | — | `listAssetProposals` **GET `/api/v1/projects/{id}/asset-proposals`** |
| **"可能与既有资产 {code} · {name} 相同，请人工裁决。"** / **"没有发现精确同名的既有角色资产。"** | paragraph | read-only | — |
| **接受合并建议** | button secondary (only if `suggested_asset_id`) | `decideAssetProposal(..., 'MERGE_EXISTING')` | **POST `/api/v1/asset-proposals/{id}:decide`** (`{action:'MERGE_EXISTING', expected_revision, target_asset_id}`) |
| **独立资产 code** | input `placeholder="CHAR_NAME"` | local code map | — |
| **保留为独立角色** | button, `disabled` unless code typed | `decideAssetProposal(..., 'CREATE_NEW')` | **POST `/api/v1/asset-proposals/{id}:decide`** (`{action:'CREATE_NEW', new_asset_code}`) |
| **拒绝建议** | button secondary | `decideAssetProposal(..., 'REJECT')` | **POST `/api/v1/asset-proposals/{id}:decide`** (`{action:'REJECT'}`) |

Edge cases: `pending = status === 'PENDING'`; loading **"正在读取资产建议…"**; empty **"没有待处理的资产身份建议。"**; error inline `role="alert"`. Mutation `onSuccess` invalidates the proposal query. `expected_revision` is the proposal's `revision` for optimistic-concurrency (an optimistic reject/merge with a stale revision will error rather than silently double-resolve).

---

## 9. EpisodeSceneRanges (`apps/web/src/features/projects/EpisodeSceneRanges.tsx`)

Header: eyebrow **"FR-WRT-002"**, `<h4>` **"母本场次与当前集范围"**, pill **"{n} 个关联"** (line 32). Guidance **"母本场次属于项目；不同分集引用同一 scene UUID，不复制成不同实体。来源范围使用显式起止偏移。"** (line 33).

| Visible label | Type | Does | API |
|---|---|---|---|
| range rows `{ordinal}. {scene_code} · {scene_title}` + `{source_start}—{source_end} · {label}` | article | read-only | `listEpisodeSceneRanges` **GET `/api/v1/projects/episodes/{eid}/scene-ranges`** |
| **管理母本场次与范围** | `<details><summary>` | toggle form | — |
| **场次 code / 场次标题** | input | `sceneCode/sceneTitle` | — |
| **创建母本场次 / 创建中…** | button secondary, `disabled` unless code+title | `createScene.mutate()` | `createProjectScene` **POST `/api/v1/projects/{pid}/scenes`** (`{code,title}`) |
| **已有母本场次** | select (from `listProjectScenes`) | `sceneId` | `listProjectScenes` **GET `/api/v1/projects/{pid}/scenes`** |
| **集内顺序** | number min 1 | `ordinal` | — |
| **来源起点 / 来源终点** | number (min 0 / min 1) | `sourceStart/sourceEnd` | — |
| **范围说明** | input | `sourceLabel` | — |
| **关联到当前集 / 关联中…** | button secondary, `disabled` unless `sceneId && ordinal>=1 && sourceStart>=0 && sourceEnd>sourceStart` | `bind.mutate()` | `bindEpisodeSceneRange` **POST `/api/v1/projects/episodes/{eid}/scene-ranges`** |

Edge cases: after a successful bind, ordinal increments and source window advances (`sourceStart=sourceEnd; sourceEnd=sourceEnd+1`) (line 27). Error **"保存失败：{msg}"** for create/bind. Empty **"当前集还没有母本场次范围。"** (line 34). `useEffect` auto-selects the first project scene (line 16).

---

## 10. ProjectAssetGrantPanel (`apps/web/src/features/projects/ProjectAssetGrantPanel.tsx`)

Header: eyebrow **"FR-AST-001 · 项目资产授权"**, `<h3>` **"跨项目工作区资产复用"**, pill neutral **"不复制源文件"** (line 27). Guidance **"只显示其他项目已显式授权且完整性仍有效的媒体。Grant 只冻结来源项目、授权 revision、SHA-256 和用途；源授权撤回或内容变化会立即显示影响，不会静默变成可用资产。"** (line 28).

| Visible label | Type | Does | API |
|---|---|---|---|
| **候选源资产** | select, `disabled` if no grantable | `selected` | `listProjectAssetGrantCandidates` **GET `/api/v1/projects/{id}/asset-grant-candidates`** (filtered `grantable`; options **"暂无可授权源资产"** / **"选择已授权源资产"** / `{source_project_code} · {asset_kind} · {path_rel}`) |
| **授权用途** | select: **READ_ONLY · 只读复用** / **DERIVED · 仅派生新版本** | `mode` | — |
| **创建项目 Grant / 授权中…** | button primary, `disabled` unless selected | `create.mutate()` | `createProjectAssetGrant` **POST `/api/v1/projects/{id}/asset-grants`** (`{authorization_id,access_mode}`) |
| fingerprint | `role="status"` | **"来源 {title} · v{revision} · {sha16}… · {license_status}"** when selected | — |
| **撤回 Grant** | button secondary (when `status==='ACTIVE'`) | opens inline revoke dialog | — |
| **撤回原因（必填）** | input | `revokeReason` | — |
| **确认撤回** | button primary, `disabled` unless reason non-empty | `revoke.mutate()` | `revokeProjectAssetGrant` **POST `/api/v1/asset-grants/{grant_id}:revoke`** (`{reason}`) |
| **取消** | button secondary | close revoke dialog | — |
| grant row | `impact` → **"影响：{…}"** or **"来源 revision/hash 当前一致 · 可用/不可用"** | read-only | `listProjectAssetGrants` **GET `/api/v1/projects/{id}/asset-grants`** |

Edge cases: empty candidates → **"暂无可授权源资产；请先在来源项目对 VERIFIED 媒体执行“授权到工作区”。"** (line 31). Error **"资产 Grant 操作失败：{msg}"**. `onSuccess` invalidates both grant+candidate queries and clears `selected`. Grant `usable`/`impact` reflects live source integrity.

---

## 11. Source-passage (read-only original text; `apps/web/src/features/source-passage/*`)

### SourcePassagePanel
Reads a window of the immutable source document. `SOURCE_PASSAGE_MAX_CHARACTERS = 8_000` (`sourcePassageApi.ts:1`). API **GET `/api/v1/source-document-versions/{source_document_version_id}/passage?start=&end=`**.
- No version → notice **"尚未找到已应用拆解对应的原文版本。"** (+ blockquote fallback if present).
- Loading **"正在读取最多 8,000 个字符…"**; error + **重试**.
- Toolbar: **"← 前一片段"** (disabled if `source_start<=0` or fetching) / **"后一片段 →"** (disabled if `!has_more` or fetching); span **"字符 {start}–{end} / {total}"**. `<pre>` text. Meta **"偏移单位：Unicode 字符"**, **"单次上限：{max}"**, **"已按服务端上限截断"** when truncated, **"正在切换片段…"** when fetching.

### EpisodeSourcePassage & DirectorSourcePassage
Both find the `APPLIED` breakdown draft's `source_document_version_id` from `listScriptBreakdownDrafts(projectId)`, then render `SourcePassagePanel`. EpisodeSourcePassage additionally lists scene ranges (from `listEpisodeSceneRanges(episodeId)`), sorts by `ordinal`, and offers **"定位场景"** select (`{scene_code} · {scene_title}`), label **"分集原文片段"**. DirectorSourcePassage reads `source_context.source_range.source_start/source_end` (finite, >=0) and labels **"镜头来源片段"**.

---

## 12. Backend route summary (what the buttons trigger)

| Endpoint seen in UI | Method + path | Handler (`operation_id`) | Route file |
|---|---|---|---|
| 新建项目 → create | POST `/api/v1/projects` | `createProject` | `projects.py:56` |
| 存储/最终预检 | POST `/api/v1/projects:plan` | `planProjectCreation` | `projects.py:89` |
| 项目列表 | GET `/api/v1/projects` | `listProjects` | `projects.py:48` |
| 项目健康 | GET `/api/v1/projects/{id}/health` | `projectHealth` | `projects.py:187` |
| 季列表 | GET `/api/v1/projects/{id}/seasons` | `listSeasons` | `projects.py:245` |
| 集列表 | GET `/api/v1/projects/seasons/{sid}/episodes` | `listEpisodes` | `projects.py:263` |
| 项目场次 + 创建 | GET/POST `/api/v1/projects/{id}/scenes` | `listProjectScenes`/`createProjectScene` | `projects.py:250,255` |
| 集场景范围 列表/绑定 | GET/POST `/api/v1/projects/episodes/{eid}/scene-ranges` | `listEpisodeSceneRanges`/`bindEpisodeSceneRange` | `projects.py:276,284` |
| 剧本导入（解析预览） | POST `/api/v1/projects/{pid}/imports` | `importScriptDocument` | `imports.py:18` |
| commit 导入会话 | POST `/api/v1/import-sessions/{sid}:commit` | `commitImportSession` | `imports.py:59` |
| 原文片段 | GET `/api/v1/source-document-versions/{id}/passage` | `getSourceDocumentPassage` | `imports.py:42` |
| 提交 AI 拆解 | POST `/api/v1/import-sessions/{sid}:request-breakdown` | `requestScriptBreakdown` | `imports.py:67` |
| 应用拆解草稿 | POST `/api/v1/breakdown-drafts/{did}:apply` | `applyScriptBreakdownDraft` | `imports.py:84` |
| 创作资料 列表/创建/详情/revision/比较/回退 | GET/POST `/api/v1/creative-entries...` | `list*`/`create*`/`get`/`createRevision`/`compare`/`restore` | `creative_entries.py` |
| LLM 状态/profile:sync/profile:publish | GET `/api/v1/local-llm/status`; POST `.../profile:sync`, `.../profile:publish` | `status`/`syncCandidate`/`publish` | `llm.py:18,26,34` |
| 拆解草稿列表 | GET `/api/v1/projects/{pid}/script-breakdown-drafts` | `listBreakdownDrafts` | `llm.py:59` |
| 生产计划绑定 (standalone, not used by wizard) | POST `/api/v1/projects/{pid}/production-plan` | `bindProductionPlan` | `configuration.py:29` |
| 交付目标 | POST `/api/v1/projects/{pid}/delivery-targets(:from-preset)` | `createDeliveryTarget`/`createDeliveryTargetFromPreset` | `configuration.py:37,49` |
| Job 列表/取消/重试 | GET `/api/v1/jobs`; POST `/api/v1/jobs/{id}:cancel`, `:retry` | `listJobs`/`cancelJob`/`retryJob` | (jobs routes; endpoints confirmed in `generated/api.ts:457-489`) |
| 资产 Grant 候选/列表/创建/撤回 | GET/POST `/api/v1/projects/{id}/asset-grant*`; POST `/api/v1/asset-grants/{id}:revoke` | — | (generated client; backend in asset routes/enabled by `listProjectAssetGrantCandidates` etc.) |
| 资产建议 列表/裁决 | GET `/api/v1/projects/{id}/asset-proposals`; POST `/api/v1/asset-proposals/{id}:decide` | — | `assetProposalsApi.ts:15-28` |

---

## 13. Questions to resolve

1. **No free-text script authoring / story-generate UI exists.** The "write/generate a story/script" path in the happy sequence is really "import an existing document + AI breakdown"; there is no blank-script editor, no "generate story" button, and no button that writes a script from scratch. Confirm whether the intended "write" action is the import path only, or if a story-authoring surface is expected elsewhere (out of the files reviewed).
2. **The wizard bakes the production plan into `createProject`** rather than calling a separate `bindProductionPlan`; the standalone `/projects/:id/production-settings` page exists (router line 60) but was **not** in the review scope. If "set its production plan" must go through that page (not the wizard), its controls are unverified here.
3. **`requestScriptBreakdown` sends a fresh random UUID Idempotency-Key per click**, so repeated clicks on **提交 AI 拆解任务** would enqueue multiple Jobs for the same session (one per click) unless the backend dedups by session. Unclear whether repeated submissions are intended to be prevented in the UI.
4. **Project create has no client-side Idempotency-Key** (backend ignores it) — re-clicking **确认创建 DRAFT** with the same `code` errors on uniqueness rather than returning the same project. Confirm the expected UX.
5. **Job monitor `listJobs`** endpoint/route file (jobs.py) was not among the supplied backend route files; endpoints were only confirmed from the generated client (`/api/v1/jobs`, `:cancel`, `:retry`). The server-side cancel/retry guarantees are unverified.
6. **ProjectHomePage "project" fetch uses `listProjects({})` then `.find(id)`** — it never calls a `getProject` endpoint, so a valid id that's missing from the (paginated, limit-50) list would render as **"项目加载中…"** with no not-found state. Confirm this is intended (or that the query always returns all projects).
7. **`revokeProjectAssetGrant`, asset-proposal and asset-grant backend handlers** were not in the provided route set; their exact server-side semantics (e.g. whether revoke is idempotent, whether grants can be re-created) are inferred only from the client types.
8. **Stage 4 "裁决身份" (`AssetProposalReviewPanel`) render source / route** is `episode-plan-v2`, and the asset-proposals router path prefix `/api/v1/projects/{id}/asset-proposals` comes from the feature-local `assetProposalsApi.ts` — confirm this matches a real backend controller (not enumerated in the provided routes).
9. **The 8,000-char passage window and the `source_document_version_id`-based lookup** in SourcePassagePanel depend on a draft having been applied (`APPLIED`); for a draft that was never applied there is no passage — confirm this matches the intended evidence model.
