# Feature Sheet — Asset Bible, QC Policies, Director Recipes, Production Settings

> Every statement below is taken directly from the LocalDramaStudio source at
> `F:\AI_Projects\h3\local_drama_studio`. Each claim cites its file path; quoted strings are verbatim.
> All API paths are the raw `/api/v1...` strings used by the frontend clients. Endpoint/method pairs
> marked “generated” come from `apps/web/src/generated/api.ts` (the single source the UI calls).

---

## Reference map (frontend client → backend route)

| Frontend client fn (file) | HTTP call | Backend handler (`apps/api/local_drama/api/routes/…`) |
|---|---|---|
| `getAssetBible` (asset-bible-v2/api.ts:39) | `GET /api/v1/projects/{project_id}/asset-bible` | `asset_bible.py:75` |
| `createStoryAsset` (generated api.ts:1239) | `POST /api/v1/projects/{project_id}/story-assets` | `story_assets.py` (route not in this scope) |
| `archiveStoryAsset` (generated api.ts:1256) | `POST /api/v1/story-assets/{asset_id}:archive` | `story_assets.py` |
| `createStoryAssetState` (asset-bible-v2/api.ts:47) | `POST /api/v1/story-assets/{asset_id}/states` | `asset_bible.py:139` |
| `createStoryAssetReference` (asset-bible-v2/api.ts:51) | `POST /api/v1/story-assets/{asset_id}/references` | `asset_bible.py:172` |
| `getAssetReferenceMediaVersion` (asset-bible-v2/api.ts:43) | `GET /api/v1/media-versions/{mediaVersionId}` | `media.py` |
| `preflightAssetMultiView` (multiviewClient.ts:82) | `POST /api/v1/story-assets/{asset_id}/generate-multiview:preflight` | `asset_bible.py:91` |
| `submitAssetMultiView` (multiviewClient.ts:90) | `POST /api/v1/story-assets/{asset_id}/generate-multiview` | `asset_bible.py:99` |
| `getAssetMultiViewHistory` (multiviewClient.ts:98) | `GET /api/v1/story-assets/{asset_id}/detail` | `asset_bible.py:83` |
| `preflightAssetExpression` (expressionClient.ts:29) | `POST /api/v1/story-assets/{asset_id}/generate-expression:preflight` | `asset_bible.py:107` |
| `submitAssetExpression` (expressionClient.ts:32) | `POST /api/v1/story-assets/{asset_id}/generate-expression` | `asset_bible.py:115` |
| `preflightAssetDetail` (detailClient.ts:23) | `POST /api/v1/story-assets/{asset_id}/generate-detail:preflight` | `asset_bible.py:123` |
| `submitAssetDetail` (detailClient.ts:24) | `POST /api/v1/story-assets/{asset_id}/generate-detail` | `asset_bible.py:131` |
| `listCharacterIdentityPacks` (identityPackClient.ts:122) | `GET /api/v1/story-assets/{story_asset_id}/identity-packs` | `character_identity_packs.py:47` |
| `createCharacterIdentityPack` (identityPackClient.ts:126) | `POST /api/v1/story-assets/{story_asset_id}/identity-packs` | `character_identity_packs.py:55` |
| `getCharacterIdentityPack` (identityPackClient.ts:137) | `GET /api/v1/character-identity-packs/{pack_id}` | `character_identity_packs.py:71` |
| `createCharacterIdentityPackVersion` (identityPackClient.ts:141) | `POST /api/v1/character-identity-packs/{pack_id}/versions?from_version_id=…` | `character_identity_packs.py:79` |
| `getCharacterIdentityPackVersion` (identityPackClient.ts:151) | `GET /api/v1/character-identity-pack-versions/{version_id}` | `character_identity_packs.py:87` |
| `setCharacterIdentityPackSlot` (identityPackClient.ts:155) | `PUT /api/v1/character-identity-pack-versions/{version_id}/slots` | `character_identity_packs.py:95` |
| `removeCharacterIdentityPackSlot` (identityPackClient.ts:166) | `DELETE /api/v1/character-identity-pack-versions/{version_id}/slots/{slot_kind}` | `character_identity_packs.py:111` |
| `approveCharacterIdentityPackVersion` (identityPackClient.ts:175) | `POST /api/v1/character-identity-pack-versions/{version_id}:approve` | `character_identity_packs.py:119` |
| `compareCharacterIdentityPackVersions` (identityPackClient.ts:186) | `GET /api/v1/character-identity-pack-versions/{base_version_id}:compare?target_version_id=…` | `character_identity_packs.py:127` |
| `getCharacterIdentityPackVersionImpact` (identityPackClient.ts:194) | `GET /api/v1/character-identity-pack-versions/{version_id}/impact` | `character_identity_packs.py:139` |
| `retireCharacterIdentityPackVersion` (identityPackClient.ts:200) | `POST /api/v1/character-identity-pack-versions/{version_id}:retire` | `character_identity_packs.py:147` |
| `listQcPolicies` (qc-policy-v2/api.ts:14) | `GET /api/v1/projects/{project_id}/qc-policies` | `qc_policies.py:34` (no stage query) |
| `resolveQcPolicy` (qc-policy-v2/api.ts:17) | `GET /api/v1/projects/{project_id}/qc-policies?stage=…&episode_id=…&shot_id=…` | `qc_policies.py:34` (with stage → `resolve`) |
| `putQcPolicy` (qc-policy-v2/api.ts:23) | `PUT /api/v1/projects/{project_id}/qc-policies` | `qc_policies.py:50` |
| `listQcSeasons` (qc-policy-v2/api.ts:26) | `GET /api/v1/projects/{project_id}/seasons` | `projects.py` |
| `listQcEpisodes` (qc-policy-v2/api.ts:27) | `GET /api/v1/projects/seasons/{season_id}/episodes` | `projects.py` |
| `listQcShots` (qc-policy-v2/api.ts:28) | `GET /api/v1/projects/episodes/{episode_id}/shots` | `projects.py` |
| `listDirectorRecipes` (recipes-v2/api.ts:14) | `GET /api/v1/projects/{project_id}/director-recipes` | `director_recipes.py:29` |
| `getDirectorRecipeBinding` (recipes-v2/api.ts:15) | `GET /api/v1/projects/{project_id}/director-recipe-binding` | `director_recipes.py:61` |
| `createDirectorRecipe` (recipes-v2/api.ts:16) | `POST /api/v1/projects/{project_id}/director-recipes` | `director_recipes.py:45` |
| `createDirectorRecipeVersion` (recipes-v2/api.ts:17) | `POST /api/v1/projects/{project_id}/director-recipes/{recipe_id}/versions` | `director_recipes.py:53` |
| `bindDirectorRecipe` (recipes-v2/api.ts:18) | `PUT /api/v1/projects/{project_id}/director-recipe-binding` | `director_recipes.py:69` |
| `listRecipeQcPolicies` (recipes-v2/api.ts:19) | `GET /api/v1/projects/{project_id}/qc-policies` | `qc_policies.py:34` |
| `getProductionFreshness` (freshness/api.ts:49) | `GET /api/v1/projects/{project_id}/production-freshness?limit=…` | `production_freshness.py:25` |
| `getShotContinuityContext` (generated api.ts:735) | `GET /api/v1/shots/{shot_id}/continuity-context` | `production.py:40` |
| `createShotRevision` (generated api.ts:739) | `POST /api/v1/projects/shots/{shot_id}/revisions` | `shot_editing.py` |
| `markShotProductionReady` (generated api.ts:743) | `POST /api/v1/projects/shots/{shot_id}:mark-production-ready` | `shot_editing.py` |
| `resolveProfileCameraPlan` (generated api.ts:297) | `POST /api/v1/profile-versions/{profile_version_id}:resolve-camera-plan` | `profiles.py` |
| `bindStoryAssetToShot` (generated api.ts:1260) | `POST /api/v1/shots/{shot_id}/story-asset-bindings` | `story_assets.py` |
| `listShotStoryAssets` (generated api.ts:1264) | `GET /api/v1/shots/{shot_id}/story-asset-bindings` | `story_assets.py` |
| `unbindStoryAssetFromShot` (generated api.ts:1268) | `DELETE /api/v1/story-asset-bindings/{binding_id}` | `story_assets.py` |
| `createPrompt` (generated api.ts:557) | `POST /api/v1/prompts` | `prompts.py` |
| `listPrompts` (generated api.ts:561) | `GET /api/v1/prompts?project_id=…&owner_type=…&owner_id=…` | `prompts.py` |
| `authorizeWorkspaceAsset` (generated api.ts:1127) | `POST /api/v1/projects/{project_id}/workspace-assets/authorize` | `workspace (projects.py)` |
| `reviewInbox` (generated api.ts:358) | `GET /api/v1/reviews/inbox?project_id=…` | `reviews.py` |
| `listWorkspaceAssetAuthorizations` (generated api.ts:1135) | `GET /api/v1/projects/{project_id}/workspace-assets/authorizations` | `workspace (projects.py)` |
| `getProjectConfiguration` (generated api.ts:985) | `GET /api/v1/projects/{project_id}/configuration` | `configuration.py` |
| `getProjectHealth` (generated api.ts:243) | `GET /api/v1/projects/{project_id}/health` | `health.py` |
| `getCapacitySnapshot` (generated api.ts:1033) | `GET /api/v1/capacity/snapshot?project_id=…` | `capacity.py` |
| `getG9Readiness` (generated api.ts:944) | `GET /api/v1/projects/{project_id}/gates/g9` | `gates.py` |
| `listSeasons` (generated api.ts:667) | `GET /api/v1/projects/{project_id}/seasons` | `projects.py` |
| `listEpisodes` (generated api.ts:671) | `GET /api/v1/projects/seasons/{season_id}/episodes` | `projects.py` |

> Note: `listRecipeQcPolicies` reuses the `GET /projects/{project_id}/qc-policies` route with **no** stage
> query, so it returns the full list as `items`; the recipes UI then filters by `policy_version_id`.

---

# Section 1 — Asset Bible page

## 1.1 Route + page title
- Route: `/projects/:projectId/assets` (`apps/web/src/app/router.tsx:57`, the `FeatureFlagRoute flag="ASSET_BIBLE_V2"`).
- Heading: eyebrow `资产圣经`, `<h3>角色 / 场景 / 道具 / 服装</h3>` (`AssetBiblePage.tsx:120`).
- Right tag: `<StatusBadge>{bible?.asset_count ?? "…"} 项资产</StatusBadge>` (`AssetBiblePage.tsx:121`).

## 1.2 Purpose
One-line: view and manage the project’s Asset Bible — create character/scene/prop/costume assets, attach
immutable image references, generate multi-view / expression / detail images, and review immutably-versioned
references before use (`AssetBiblePage.tsx`).

## 1.3 Layout (visible panels / tabs / labels)
Confirmed present: Asset Bible has four kind tabs `角色 / 场景 / 道具 / 服装`
(`AssetBiblePage.tsx:20-25`, `KIND_TABS`).

1. **panel-heading** — eyebrow `资产圣经`, `<h3>角色 / 场景 / 道具 / 服装</h3>`, `{asset_count} 项资产`.
2. **Left aside `.bible-list`** (`aria-label="资产列表"`):
   - `.bible-tabs` (`role="tablist"`, `aria-label="资产类别"`): four `<button role="tab">` =
     `角色`, `场景`, `道具`, `服装`.
   - `.bible-asset-list`: one row per asset — thumbnail (empty label `无图`), `<span>{name}</span>`,
     `<code>{code}</code>`. Empty state: `还没有{KIND_LABELS[tab]}资产` / `使用右侧的新建入口建立第一项资产；已有媒体可在创建后绑定为参考。` (`AssetBiblePage.tsx:141`).
3. **Middle `.bible-detail`** (`aria-label="资产详情"`):
   - Detail head: eyebrow = kind label (`角色/场景/道具/服装`), `<h4>{name}</h4>`,
     `<p class="muted">{description || "无描述"}</p>`.
   - Action row: `<StatusBadge>` = `已归档` (if `status === "ARCHIVED"`) else `启用中`; `<button>归档</button>`.
   - `.bible-main-visual`: reference grid of `base_references` + all `states[].references`; empty = `还没有参考图`
     (for CHARACTER: `先添加主参考（HERO），再生成三视图。`; else `从项目媒体中选择一张低分辨率参考缩略图。`). Ref caption = `REF_KIND_LABELS[...]` + optional ` · 已锁定`.
   - **ReferenceVersionCompare** (see §1.3.1).
   - `.bible-aside-info` panels (shown based on `selected.asset.kind`):
     - `SCENE` → `SceneBiblePanel` (eyebrow `Scene Bible`, h4 `日夜状态与空间参考`).
     - `CHARACTER` → `GenerateMultiViewPanel` (eyebrow `角色一致性`, h4 `生成 FRONT / LEFT / RIGHT`).
     - `CHARACTER` → `CharacterIdentityPackPanel` (eyebrow `角色一致性基准`, h4 `身份包 · {assetName}`).
     - `CHARACTER` → `GenerateExpressionPanel` (eyebrow `角色表情`, h4 `生成表情九宫格`).
     - `CHARACTER` → `GenerateDetailPanel` (eyebrow `角色细节`, h4 `生成近景细节`).
     - `panel` — eyebrow `造型状态`, h4 `States`. Empty: `还没有造型状态` / `状态用于区分服装、伤势、情绪、日夜或光线，不会覆盖基础资产。`
       State rows show `<strong>{label}</strong> <code>{code}</code> {state_kind} <StatusBadge>{references.length} 参考</StatusBadge>`.
       `<details><summary>新增剧情状态</summary>` with fields `状态代码` (placeholder `INJURED`),
       `显示名称` (placeholder `受伤`), `状态类型` `<select>` (`自定义`/`服装`/`伤势`/`情绪`/`时段`/`天气`/`光线`), button `创建状态`.
     - `panel` — eyebrow `参考图`, h4 `选择或上传项目图片`. Hint: `选择结果固定到不可变媒体版本，后续更新不会改变历史生成输入。列表只读取低分辨率缩略图；HERO 默认锁定。`
       `MediaPicker` label `资产参考图选择器`; then `参考类型` select (from `REF_KIND_LABELS`:
       `主参考/正面/左侧/右侧/背面/三视图/全身/特写/表情表/大全景/反打/全景拼接/光线参考/其他`),
       `绑定状态` select (`基础参考` + each state label), button `添加参考`.
     - `panel` — eyebrow `准备度`, h4 `Readiness`. `级别：{level}`; `缺参考：{missing kinds}` else `参考图齐全`.
     - `panel` (conditional on `voice`) — eyebrow `声音`, h4 `Voice`, shows `voice.voice_title`.
     - **AssetUsagePanel** (eyebrow `使用情况`, h4 `Used episodes / shots`, see §1.3.2).
4. **Right aside `.bible-create`** (`aria-label="新建资产"`): `<details><summary>新建{KIND_LABELS[tab]}资产</summary>` with
   `代码` (placeholder `CHAR_${tab.slice(0,3).toUpperCase()}`), `名称`, `描述`, button `创建资产`;
   helper `<small>` = `请填写资产代码与名称后创建` / `请填写资产名称后创建`.
5. Bottom: `<Link>返回项目总览</Link>` → `/projects/{projectId}`; error state `资产圣经暂时无法打开`.

### 1.3.1 ReferenceVersionCompare (feature `ReferenceVersionCompare.tsx`)
- Header: eyebrow `只读版本事实`, h4 `参考版本比较`, status-pill `不修改采用或锁定`.
- Copy: `从当前资产已绑定的不可变参考版本中选择两项。图片只读取缩略图；视频默认不预加载，播放时才读取内容。`
- Two selects `版本 A` / `版本 B` (options grouped by media version; duplicate semantic bindings collapse into one label
  `{label} · {stateLabel} · {mediaVersionId.slice(0,8)}…`). Only enabled when `versions.length >= 2`, else
  `至少需要两个不同的参考媒体版本才能比较；同一版本的多个语义绑定只合并展示。`
- Two panes show per-version facts: `不可变版本` `v{version_no}` (+`Take {n}`), `阶段 / 类型`, `格式`,
  `大小 / 时长`, `完整性`, `SHA-256`, `登记时间` (`ReferenceVersionCompare.tsx:49-57`).
- Read-only: `StatusBadge tone="neutral"` asserts `不修改采用或锁定`. Versions are semantic-deduped (filter `status !== "ARCHIVED"`).

### 1.3.2 AssetUsagePanel (feature `AssetUsagePanel.tsx`)
- Header: eyebrow `使用情况`, h4 `Used episodes / shots`, `{item.usage.shot_count} 镜头`.
- Empty: `此资产尚未绑定到镜头` / `在分集规划或导演台绑定后，会在这里提供直接镜头入口。`
- Rows: `{episode_code} · {shot_code}` (fallback `未编号分集`/`未编号镜头`) + small `scene_code · scene_title · role_in_shot` (else `已绑定资产`),
  with a `<Link>打开镜头</Link>` → `/projects/{projectId}/episodes/{episodeId}/direct/{shotId}` when both ids exist, else `缺少导航事实`.

## 1.4 Interactive controls table

| Exact visible label | Type | Action | API endpoint + method |
|---|---|---|---|
| `角色` `场景` `道具` `服装` | tab button (`role=tab`) | `setTab(kind); setSelectedId(null)` (client only) | — (no fetch) |
| asset row (thumb + name + code) | button | `setSelectedId(id)` (client only) | — |
| `归档` | button | `archive.mutate({id,revision})` | `POST /api/v1/story-assets/{asset_id}:archive` (generated api.ts:1256) |
| `创建资产` | button | `create.mutate()` | `POST /api/v1/projects/{project_id}/story-assets` (generated api.ts:1239) |
| `状态代码` / `显示名称` / `状态类型` | input / input / select | set local state | — (until `创建状态`) |
| `创建状态` | button | `addState.mutate()` | `POST /api/v1/story-assets/{asset_id}/states` (asset-bible-v2/api.ts:47) |
| `参考类型` / `绑定状态` | select / select | set local state | — (until `添加参考`) |
| `添加参考` | button | `addReference.mutate()` (`is_locked: referenceKind==="HERO"`) | `POST /api/v1/story-assets/{asset_id}/references` (asset-bible-v2/api.ts:51) |
| `新建{角色/场景/道具/服装}资产` | `<details><summary>` | expands create form (client) | — |
| `返回项目总览` | link | navigate | route only |
| **Multi-view** `造型状态` | select | set `assetStateId` | — |
| `一致性` | select (`高/中/低`) | sets `consistency_strength` | — |
| `背景` | select (`干净背景/透明背景/保留原背景`) | sets `background` | — |
| `指定能力 Profile（可选）` → select | select (AUTO + published `IMAGE_MULTI_VIEW`) | sets `profileVersionId` | — |
| `运行只读预检` | button | `runPreflight()` | `POST /api/v1/story-assets/{asset_id}/generate-multiview:preflight` |
| `确认生成三视图` | button | `submit()` (needs `preflight.ready`) | `POST /api/v1/story-assets/{asset_id}/generate-multiview` |
| `绑定为 FRONT` / `绑定为 LEFT` / `绑定为 RIGHT` | button | `bind()` → `createStoryAssetReference` (label `三视图生成 · {kind}`) | `POST /api/v1/story-assets/{asset_id}/references` |
| **Expression** `造型状态` / `语义 Profile` | select / select | set state / profile | — |
| `运行只读预检` | button | `runPreflight()` | `POST /api/v1/story-assets/{asset_id}/generate-expression:preflight` |
| `确认生成九个独立槽` | button | `submit()` | `POST /api/v1/story-assets/{asset_id}/generate-expression` |
| `绑定为表情参考` | button | `bindExpressionReference` (label `表情九宫格 · {kind}`) | `POST /api/v1/story-assets/{asset_id}/references` |
| **Detail** `造型状态` / `语义 Profile` | select / select | set state / profile | — |
| `预检近景细节` | button | `runPreflight()` | `POST /api/v1/story-assets/{asset_id}/generate-detail:preflight` |
| `确认生成三个独立槽` | button | `submit()` | `POST /api/v1/story-assets/{asset_id}/generate-detail` |
| `绑定为 CLOSEUP` / `绑定为 DETAIL` | button | `bindDetailReference` (label `近景细节 · {kind}`, ref kind depends on slot) | `POST /api/v1/story-assets/{asset_id}/references` |
| **Scene panel** `创建 DAY` / `创建 NIGHT` | button | `createPeriod.mutate(period)` (state_kind `TIME_OF_DAY`) | `POST /api/v1/story-assets/{asset_id}/states` (asset-bible-v2/api.ts:47) |
| `选择媒体` / `添加新版本` (scene reference slot) | button | `onChooseReference(kind)` → sets `referenceKind`, scrolls to `#asset-reference-picker` | — (client, then manual `添加参考`) |
| **Identity pack** `新建身份包` | button | toggles create form | — (opens form) |
| `创建草稿` (pack) | submit | `handleCreatePack` | `POST /api/v1/story-assets/{story_asset_id}/identity-packs` |
| `从此版本建新草稿` | button | `handleCreateDraft` | `POST /api/v1/character-identity-packs/{pack_id}/versions?from_version_id=…` |
| `版本比较` | button | `compare()` | `GET /api/v1/character-identity-pack-versions/{base}:compare?target_version_id=…` |
| `影响分析` | button | `inspectImpact()` | `GET /api/v1/character-identity-pack-versions/{version_id}/impact` |
| `废弃版本` | button | opens retire dialog | `POST /api/v1/character-identity-pack-versions/{version_id}:retire` |
| `选择图片` / `替换图片` (slot) | button | opens Drawer | — (opens picker) |
| `授权并绑定此版本` | button | `confirmSlot()` | `POST /api/v1/projects/{project_id}/workspace-assets/authorize` (generated api.ts:1127) + `PUT /api/v1/character-identity-pack-versions/{version_id}/slots` |
| `移除` (slot) | button | `removeSlot(slotKind)` | `DELETE /api/v1/character-identity-pack-versions/{version_id}/slots/{slot_kind}` |
| `人工审核并批准` | button | opens approval Dialog | `POST /api/v1/character-identity-pack-versions/{version_id}:approve` |
| `生成缺失三视图` | button | calls `onRequestMissingSlots` → scrolls to `multiview-title` (client) | — |
| **Usage** `打开镜头` | link | navigate | route `…/episodes/{episodeId}/direct/{shotId}` |
| **Version compare** `版本 A` / `版本 B` | select | set left/right id | — |
| `重试版本 A` / `重试版本 B` | button | `refetch()` | `GET /api/v1/media-versions/{mediaVersionId}` |

## 1.5 Happy-path click sequences

### (a) Create a character + generate multi-view / expression images
1. Open `角色` tab (default when page loads — `useState("CHARACTER")`, `AssetBiblePage.tsx:53`).
2. In the right `新建角色资产` details, fill `代码` (e.g. `CHAR_XXX`), `名称`, `描述`; click `创建资产`
   → `POST /projects/{id}/story-assets`. The new asset becomes `selectedId` (page refreshes).
3. Select a state (optional): in `造型状态 / States`, click `新增剧情状态`, fill `状态代码`/`显示名称`,
   pick `状态类型`, click `创建状态` → `POST /story-assets/{id}/states`.
4. Add a HERO reference: in `参考图 / 选择或上传项目图片`, use `资产参考图选择器` to pick an image,
   choose `参考类型=主参考`, click `添加参考` → `POST /story-assets/{id}/references` with `is_locked=true`.
5. In `角色一致性 / 生成 FRONT / LEFT / RIGHT`, click `运行只读预检` → preflight
   (`…/generate-multiview:preflight`). If ready and showing `预检通过 · 将创建 3 个任务`, click
   `确认生成三视图` → `POST …/generate-multiview`. The panel polls `GET /story-assets/{id}/detail` every 2 s
   while a batch is active (`GenerateMultiViewPanel.tsx:103-117`).
6. When each slot completes, click `绑定为 FRONT` / `LEFT` / `RIGHT` → binds each output as a reference.
7. Repeat in `角色表情 / 生成表情九宫格` (click `运行只读预检`, `确认生成九个独立槽`, then `绑定为表情参考`)
   for the 9-slot expression grid, and in `角色细节 / 生成近景细节` for close-up/detail slots.

### (b) Create a scene
1. Click the `场景` tab; in `新建场景资产` fill code/name/description, click `创建资产` → `POST /projects/{id}/story-assets`.
2. `SceneBiblePanel` appears (`日夜状态与空间参考`). Click `创建 DAY` / `创建 NIGHT` to add a
   `TIME_OF_DAY` state (`POST /story-assets/{id}/states`).
3. For each scene reference slot (`大全景 / Wide`, `反打 / Reverse`, `全景拼接 / Panorama`, `光线参考 / Light`),
   click `选择媒体` (or `添加新版本`) to jump to `#asset-reference-picker`, pick media and `添加参考`.

### (c) Review / approve an asset version
- `ReferenceVersionCompare` is read-only (never writes). It only compares two immutably-versioned media
  versions (`GET /media-versions/{id}`).
- Human APPROVAL happens on a **Character Identity Pack version**: create a pack (`新建身份包` → `创建草稿`),
  fill the FRONT/LEFT/RIGHT (+BACK/FACE) slots, then `人工审核并批准` → `POST …/{version_id}:approve`
  (requires `approval_ready`, i.e. three distinct VERIFIED images + a comment). Approved = `已批准`, further
  edits require a new version because approved versions are immutable (`IMMUTABLE_STATUSES` = APPROVED/SUPERSEDED/RETIRED).

### (d) QC policy + director recipe — see Sections 2, 3.

### (e) Production settings — see Section 4.

## 1.6 State / status semantics & edge cases
- Tab filtering: `visible = items.filter(item.asset.kind === tab)`; `selected` is `selectedId` from `visible`,
  else `visible[0]`, else `null` (`AssetBiblePage.tsx:72-73`). Switching tab resets selection.
- Asset status: `ARCHIVED` → pill `已归档`, `归档` disabled (`title="资产已经归档"`); else `启用中`.
  Archived assets cannot be generated or edited in panels.
- Archive optimistic save is conditional on `expected_revision` (`archiveStoryAsset(item.id,{expected_revision})`).
- Create validation: `createValid = code && name`; disabled reason computed by
  `createDisabledReason` (pending / missing code / missing name). Same pattern for state (`状态正在创建，请稍候` / `请填写状态代码` / `请填写显示名称`) and reference (`参考正在绑定，请稍候` / `请先从项目媒体中选择图片`).
- **HERO gating**: `hasHero` requires a HERO reference in the selected references (or base HERO when a state is selected);
  pill shows `HERO 已就绪` / `缺少 HERO`. `assetStatus !== "ACTIVE"` disables `运行只读预检`/`确认生成…` and shows `归档角色不能生成。` (`GenerateMultiViewPanel.tsx:188-190`).
- **Preflight gate**: submit is disabled unless `preflight?.ready`. Preflight result shows `预检通过 · 将创建 N 个任务`
  or `预检阻塞 · N 项` with per-blocker `{message}` / `suggested_action` / `code`.
- **Polling**: while any batch is active (`isMultiViewBatchActive`), the panel refetches history every 2000 ms
  (`GenerateMultiViewPanel.tsx:115`); expression/detail poll too (`GenerateExpressionPanel.tsx:43`,
  `GenerateDetailPanel.tsx:37`).
- **Bind idempotence**: `alreadyBound` suppresses re-binding the same output to the same state
  (`disabled={alreadyBound || bindingKey !== null}`), button becomes `已绑定此结果`.
- Terminal job states treated as failure: `FAILED`, `CANCELLED`, `NEEDS_ATTENTION`, `ORPHANED`
  (`GenerateMultiViewPanel.tsx:22`); batch terminal states: `SUCCEEDED`, `FAILED`, `PARTIAL_FAILED`, `CANCELLED`.
- **Readiness** levels: `READY | BASIC | EMPTY | STALE`; missing kinds display `缺参考：…`.
- **Identity pack approval**: `approvalReady` is either the server `approval_ready` or
  `missingSlots.length === 0 && requiredUnique` (3 distinct required views). Blockers shown as
  `媒体证据未通过门禁` when slots exist but `approval_blockers` are non-empty. `editable = !IMMUTABLE_STATUSES.has(status)`.
- **Authorization**: binding a pack slot always calls `authorizeWorkspaceAsset` first (explicit operator action,
  never couplings on picker-select alone) then `setCharacterIdentityPackSlot` — see comment `CharacterIdentityPackPanel.tsx:201-203`.

---

# Section 2 — QC Policies page

## 2.1 Route + page title
- Route: `/projects/:projectId/qc-policies` (`router.tsx:58`).
- Heading: eyebrow `生产治理`, `<h2>QC 策略</h2>`, status-pill `版本化门禁`
  (`QcPoliciesPage.tsx:6`).
- Sub-copy: `定义检查类别、阈值和有限自动重抽。所有策略版本不可变；机器检查通过仍不会创建人工批准。`

## 2.2 Purpose
Define immutable, versioned QC policies per scope (project/episode/shot) and per stage (image/video/audio/continuity/delivery),
with per-category pass thresholds and a bounded automatic-reroll budget (`QcPolicyManager.tsx`).

## 2.3 Layout (visible panels / labels)
`QcPolicyManager` renders three `<section>`s:
1. `.qc-context` — eyebrow `0045 · QC POLICY`, h3 `生产范围与继承解析`, status-pill `Project → Episode → Shot`.
   - `.qc-context-grid`: `季度` select (options `{code} · {title}`), `分集` select (option `仅项目级` default,
     options `{code} · {title}`), `镜头` select (option `仅分集级` default, options `{code}`).
   - `.qc-stage-tabs` (`role="tablist"`, `aria-label="QC 阶段"`): image `图像`, video `视频`, audio `声音`,
     continuity `连续性`, delivery `交付` (each button shows label + `{id}`).
2. `.qc-workspace`:
   - `.qc-resolution` — eyebrow `当前生效策略`, h3 `{stage}`, status-pill `{OWNER_LABEL[source]}` or `未配置`.
     Ordered inheritance list `1/2/3` for `项目默认`/`分集覆盖`/`镜头覆盖` showing
     `v{version_no} · revision {revision}` or `继承上级` or `未选择`. Resolution facts: `生效版本` `v{n} · {id.slice(0,8)}…`,
     `自动上限` `{n} 次`, `自动类别` `{join}"、"` or `无，失败进入人工门禁`; empty → `该上下文没有可继承策略，生产前应先配置项目默认。`
   - `.qc-editor` form — eyebrow `版本化编辑`, h3 `{OWNER_LABEL[ownerType]} · {stage}`, status-pill `revision {n}` / `新策略`.
     - `写入层级` fieldset: buttons `项目默认` / `分集覆盖` / `镜头覆盖` (with `aria-pressed`).
     - `检查类别与通过阈值` fieldset: 8 rows — `画面`(VISUAL), `面部`(FACE), `身份`(IDENTITY), `构图`(COMPOSITION),
       `运动`(MOTION), `连续性`(CONTINUITY), `声音`(AUDIO), `技术规格`(TECHNICAL) — each a checkbox + number input
       (min 0 / max 1 / step 0.05) + `<output>{round(pct*100)}%`.
     - `最大自动重抽次数` slider (0–10) `<output>{maxRerolls}`; hint `0 表示任何失败都进入人工门禁；系统绝不会无限重抽。`
     - `允许自动重抽的失败类别` fieldset (disabled when `maxRerolls===0`) — same category checkboxes.
     - `变更原因` textarea.
     - Footer buttons: `刷新版本` (secondary), `创建 v{current.version_no + 1}` / `创建策略 v1` (primary submit).
3. `.qc-dispositions` — eyebrow `机器决定的含义`, h3 `Disposition 不是人工批准`, status-pill `机器证据 ≠ 人工批准`.
   Four cards: `PASS` (机器检查通过 / `只证明本次检查通过，候选仍需人工选择/批准。`),
   `ATTENTION` (需要显式确认 / `机器提示风险；UI 必须让人确认，不静默批准。`),
   `AUTO_REROLL_ALLOWED` (允许有限重抽 / `仅对允许类别且未达到上限创建子 Variant。`),
   `WAITING_GATE` (等待人工门禁 / `达到上限或类别不可自动修复，停止自动流程。`).

## 2.4 Interactive controls table

| Exact visible label | Type | Action | API endpoint + method |
|---|---|---|---|
| `季度` | select | set `seasonId` (clears episode+shot) | `GET /projects/{project_id}/seasons` |
| `分集` | select | set `episodeId` (clears shot) | `GET /projects/seasons/{season_id}/episodes` |
| `镜头` | select | set `shotId` | `GET /projects/episodes/{episode_id}/shots` |
| stage tabs `图像/视频/声音/连续性/交付` | tab button | `setStage(id)` | — (triggers refetch of resolution) |
| `项目默认`/`分集覆盖`/`镜头覆盖` (写入层级) | button | `setOwnerType(type)` | — (client) |
| category checkboxes + threshold number inputs | checkbox + number | `toggleThreshold` / `setThresholds` | — |
| `最大自动重抽次数` | range slider | `setMaxRerolls`, clears auto cats when 0 | — |
| `允许自动重抽的失败类别` checkboxes | checkbox | `toggleAuto` | — |
| `变更原因` | textarea | `setReason` | — |
| `刷新版本` | button | `policies.refetch()` | `GET /projects/{project_id}/qc-policies` |
| `创建 v{current+1}` / `创建策略 v1` | submit | `save.mutate()` | `PUT /projects/{project_id}/qc-policies` |

## 2.5 Happy-path sequence (create/edit a QC policy)
1. Choose scope: pick `季度` → `分集` (option `仅项目级`) → `镜头` (option `仅分集级`). `ownerType` auto-defaults
   to SHOT if `initialShotId`, EPISODE if `initialEpisodeId`, else PROJECT.
2. Pick stage tab (e.g. `视频`).
3. Set `写入层级` to `项目默认` (owner inherits project).
4. Tick `检查类别` checkboxes incl. `身份`, `连续性`; adjust thresholds (default 0.85 / 0.80 as initial local
   state — `useState({ IDENTITY: .85, CONTINUITY: .8 })`, `QcPolicyManager.tsx:21`).
5. Slide `最大自动重抽次数` (0–10) and, if >0, tick auto-reroll categories.
6. Fill `变更原因`.
7. Click `创建策略 v1` → `PUT /projects/{project_id}/qc-policies`. Success toast
   `已创建不可变版本 v{n}，policy revision {n}`; on 409 `版本冲突：…刷新后再保存`; on other error `保存失败：…`.
8. Repeat for another owner/stage to create overrides; the `当前生效策略` pane shows what resolves for the current context.

## 2.6 State / status semantics & edge cases
- Inheritance: resolution walks `PROJECT → EPISODE → SHOT` in that order (hierarchy list `1/2/3`); the effective
  source is highlighted. `resolveQcPolicy` sends `stage` + optional `episode_id`/`shot_id`.
- Every policy version is immutable (`所有策略版本不可变`); save creates a NEW version (`创建 v{current.version_no + 1}`),
  and the editor is seeded with `expected_revision = current.revision`.
- Guard rails: `ownerId` must be fully selected (`请先选择完整的项目、分集或镜头范围`); if `maxRerolls === 0` but
  auto categories are chosen → `自动重抽上限为 0 时不能选择自动类别`.
- Only categories with an explicit threshold (`thresholds[id] !== undefined`) become the `checks` array; the document
  always sets `attention_selection: "REQUIRE_CONFIRMATION"`.
- `checks`/`thresholds` defaults: enabling a category inserts `.8` (`toggleThreshold`). Number input clamps `[0,1]`.
- Read-only resolution pane never writes; machine disposition (`PASS`/`ATTENTION`/`AUTO_REROLL_ALLOWED`/`WAITING_GATE`)
  is explicitly NOT a human approval (`Disposition 不是人工批准`).

---

# Section 3 — Director Recipes page

## 3.1 Route + page title
- Route: `/projects/:projectId/director-recipes` (`router.tsx:59`).
- Heading: eyebrow `专业生产模板`, `<h2>导演配方</h2>`, status-pill `0046 · Declarative`.
- Sub-copy: `把画幅、镜头规划、资产要求、生成能力与 QC 固定为可复现的不可变版本。项目只在你显式绑定时升级。`

## 3.2 Purpose
Define declarative, immutable Director Recipe versions and explicitly bind one to the project (`DirectorRecipeManager.tsx`).

## 3.3 Layout (visible panels / labels)
`DirectorRecipeManager` renders:
1. `.recipe-provenance` — eyebrow `当前项目绑定`, h3 `Recipe provenance`,
   status-pill `已显式绑定` / `尚未绑定`.
   - Binding card: `{title}`, `{code} · v{version_no} · binding revision {revision}`, hash snippet, `version {recipe_version_id} · 冻结：{是|否}`.
   - Empty: `生成前请显式绑定一个不可变版本；系统不会静默跟随“最新版”。`
   - `.recipe-bind-controls`: `目标版本` select (options `{code} · v{n} · {hash.slice(0,10)}`), `升级原因` input,
     button `绑定到项目` / `显式升级项目`.
2. `.recipe-workspace`:
   - `.recipe-library` (aside) — eyebrow `版本库`, h3 `不可变历史`, button `新建`.
     Each recipe: `{title}`, `{code} · {versions.length} 个版本`; selectable pill list of versions
     `v{version_no}` + hash + `{reason || "未填写变更原因"}`. Empty: `还没有 Director Recipe。`
   - `.recipe-editor` form — eyebrow `声明式编辑器`, h3 `{mode==="VERSION" ? "从 v{n} 创建新版本" : mode==="COPY" ? "复制为新 Recipe" : "创建 Recipe"}`.
     - If `selectedVersion`: `.recipe-mode-tabs` buttons `同 Recipe 新版本` / `复制为新 Recipe`.
     - If not VERSION: `Recipe code` input (lowercased, `pattern="[a-z][a-z0-9_-]{1,79}"`, placeholder `vertical-drama`) and `标题` input.
     - `画幅与镜头规划` fieldset: `画幅` select (`9:16`/`16:9`/`1:1`/`4:3`), `平均时长（毫秒）` number, `对白覆盖` select
       (`中近景偏置 (MEDIUM_CLOSEUP_BIASED)`/`均衡 (BALANCED)`/`大景别偏置 (WIDE_CONTEXT)`/`特写亲密 (CLOSEUP_INTIMATE)`).
     - `资产策略` fieldset: `角色必需参考类型` text (comma-separated, upper-cased, 1–20 items).
     - `生成能力` fieldset: `图像能力` select (`IMAGE_CHARACTER`/`IMAGE_SCENE`/`IMAGE_CONCEPT`/`IMAGE_EDIT`),
       `视频能力` select (`VIDEO_FIRST_LAST_FRAME`/`VIDEO_FIRST_FRAME`/`VIDEO_I2V`/`VIDEO_REFERENCE`).
     - `QC Policy 固定引用` fieldset: `不可变 QC Policy version` select (options `{stage} · {owner_type} · v{n} · {id.slice(0,8)}`).
     - `发布原因` textarea.
     - `.recipe-security` note: `安全边界：Recipe 只声明策略` / `前后端均拒绝 shell、python、command、executor、code、script 等执行字段。Recipe 不能读取任意文件、启动进程或绕过 Profile/QC 门禁。`
     - Footer submit: `创建 Recipe v1` (NEW/COPY) or `创建 v{selectedVersion.version_no + 1}` (VERSION).

## 3.4 Interactive controls table

| Exact visible label | Type | Action | API endpoint + method |
|---|---|---|---|
| `目标版本` | select | set `versionId` (and `recipeId`) | — |
| `升级原因` | input | set `bindingReason` | — |
| `绑定到项目` / `显式升级项目` | button | `bind.mutate()` | `PUT /projects/{project_id}/director-recipe-binding` |
| `新建` | button | reset editor to `DEFAULT_RECIPE`, mode NEW | — |
| recipe row (title/code/versions) | button | `setRecipeId` | — |
| version pill `v{n}` | button | `setVersionId` | — |
| `同 Recipe 新版本` / `复制为新 Recipe` | tab button | `setMode("VERSION" / "COPY")` | — |
| `Recipe code` / `标题` | input / input | set code/title | — |
| `画幅` select | select | `patch("aspect_ratio", …)` | — |
| `平均时长（毫秒）` | number | `patch("shot_planning", …)` | — |
| `对白覆盖` select | select | `patch("shot_planning", …)` | — |
| `角色必需参考类型` | text | `patch("asset_policy", …)` | — |
| `图像能力` / `视频能力` select | select | `patch("generation", …)` | — |
| `不可变 QC Policy version` select | select | `patch("qc_policy_ref", …)` | — |
| `发布原因` | textarea | `setReason` | — |
| `创建 Recipe v1` / `创建 v{n+1}` | submit | `publish.mutate()` | `POST /projects/{project_id}/director-recipes` (NEW/COPY) or `POST /projects/{project_id}/director-recipes/{recipe_id}/versions` (VERSION) |

## 3.5 Happy-path sequence (create/edit a director recipe)
1. Open the page; if recipes exist, the first is pre-selected and its latest version loads into the editor
   (`useEffect` seed mode VERSION, `{code}-copy`/`标题（副本）`).
2. To create a new recipe: click `新建` (resets editor to `DEFAULT_RECIPE`, mode NEW), enter `Recipe code`
   (lowercased) and `标题`.
3. Set `画幅`, `平均时长（毫秒）`, `对白覆盖`; then `角色必需参考类型` (e.g. `HERO, FRONT, LEFT, RIGHT`).
4. Pick `图像能力` and `视频能力`.
5. Pick `不可变 QC Policy version` (defaults to first QcPolicy version option if none chosen).
6. Fill `发布原因`, click `创建 Recipe v1` → `POST /projects/{project_id}/director-recipes`.
7. To edit an existing recipe as a new version: select it, keep `同 Recipe 新版本`, adjust fields, click `创建 v{n+1}`
   → `POST /projects/{project_id}/director-recipes/{recipe_id}/versions`.
8. Bind to project: in `当前项目绑定`, pick `目标版本`, enter `升级原因`, click `绑定到项目`/`显式升级项目`
   → `PUT /projects/{project_id}/director-recipe-binding`.

## 3.6 State / status semantics & edge cases
- **Declarative-only contract**: `DirectorRecipeDocument` has exactly `aspect_ratio`, `shot_planning`,
  `asset_policy`, `generation`, `qc_policy_ref` (recipes-v2/types.ts:1-7). No execution fields are allowed.
- **Forbidden-field guard**: `findForbiddenRecipePath` rejects any key in
  `{shell, python, command, commands, executor, exec, code, script, subprocess, powershell, bash, cmd, runtime_code, entrypoint}`
  anywhere in the draft → error `检测到禁止的执行字段：{path}` (`DirectorRecipeManager.tsx:8-14`).
- Publish guard: must have a `qc_policy_ref.policy_version_id` (`请选择本项目 QC Policy version`); in VERSION mode a
  `recipeId` is required; in NEW/COPY mode `code` and `title` are required.
- Recipes are immutable by version; hash shown via `shortHash`. `is_frozen` shown in binding card.
- Binding is optimistically guarded: `disabled={!versionId || versionId === currentVersionId || bind.isPending}`;
  tooltip `项目已经绑定此版本` when already bound. On 409 → `绑定版本冲突：…刷新后重试`.
- Success messages: publish → `不可变版本 v{n} 已创建` / `Recipe {code} 与 v1 已创建`; bind →
  `项目已显式升级到 {code} v{n}，binding revision {n}`.
- No silent "latest" follow: `系统不会静默跟随“最新版”` / `项目不会静默跟随任一 Recipe 的最新版。`

---

# Section 4 — Production Settings page

## 4.1 Route + page title
- Route: `/projects/:projectId/production-settings` (`router.tsx:60`; `/projects/:projectId/settings` redirects here, `router.tsx:61`).
- Heading: eyebrow `Project Settings`, `<h2>生产设置与项目配置</h2>`, status-pill `项目级事实真值`.
- Sub-copy: `集中管理项目生效默认、覆盖来源、交付规格、自动化与资产授权；具体配置由各自版本化模块管理，不生成第二套业务事实。`

## 4.2 Purpose
Central hub that surfaces effective project defaults, override sources, delivery/integration specs, automation and asset
authorization, delegating each concern to its own versioned module (`ProductionSettingsPage.tsx`).

## 4.3 Layout — tabs (note: NOT the prompt's assumed 生成/时间/交付/自动/授权)
`SETTINGS_TABS` (`ProductionSettingsPage.tsx:20-26`) — **the actual labels are**:
- `overview` → **生效概览** (default; `?view=` omitted for overview)
- `freshness` → **时效与失效**
- `delivery` → **交付目标与品牌**
- `automation` → **自动化与外发**
- `assets` → **授权与项目包**

Tab selection is URL-driven: `?view=`. `setActiveTab` writes `Next.set("view", id)` (or removes for overview)
with `replace: true` (`ProductionSettingsPage.tsx:35-39`).

### Tab 1 – 生效概览 (`ProductionSettingsOverview`), `production-settings-v2/ProductionSettingsOverview.tsx`
- Readiness banner (`生产就绪性`): `{blockers.length} 项需要处理` / `项目默认配置完整` + `BLOCKED`/`READY` pill.
  Blockers list each a `处理` link (URLs: `/diagnostics`, `/projects/{id}/director-recipes`, `/projects/{id}/qc-policies`,
  or `/projects/{id}/episodes/{firstEpisodeId}/delivery`). Copy: `配置事实均来自当前 API；进入具体生产步骤时仍会执行实时预检。`
- Cards:
  - `项目默认` (01): `默认画幅` (from `production_plan`), `生产计划` `{title} · v{version_no}`, `默认风格资产`
    (`由资产圣经 Style 分类与引用版本生效`). Link `管理风格资产` → `/projects/{id}/assets`.
  - `生成能力` (02): 3 key capabilities `默认角色图像`(IMAGE_CHARACTER), `默认首尾帧视频`(VIDEO_FIRST_LAST_FRAME),
    `默认 TTS`(TTS) each with source label + pill (`显式`/profile title/`AUTO`). Link `配置偏好` → `/models?project=…`.
    Footer `{projectPreferences} 项项目默认 · {overrideCount} 项分集/镜头覆盖。最终来源在具体上下文实时解析，不静默 fallback。`
  - `QC Policy` (03): project-scope policies `{stage}` label + `项目默认 · v{version_no}` + `最多 {n} 次`; empty `没有项目级 QC 默认`.
    Link `管理策略` → `/projects/{id}/qc-policies`. Footer `{policyOverrideCount} 项分集/镜头覆盖。机器 QC 证据永远不等于人工批准。`
  - `Director Recipe` (04): binding `{title}`, `{code} · v{version_no}`, hash, `来源：项目显式绑定 · revision {n}`; empty `尚未绑定。项目不会静默跟随任一 Recipe 的最新版。`
    Link `管理配方` → `/projects/{id}/director-recipes`.
  - `输出规格与本机容量` (05, wide): `输出规格` `{title} · v{version_no}` / `未选择`, `传输边界` `允许远程`/`仅本机；不允许远程传输`,
    `磁盘可用`, `GPU 并发` `{active}/{limit}`, `队列 / Worker` `{queued} 排队 · {active} 活动`. Link `打开交付`
    → `/projects/{id}/episodes/{firstEpisodeId}/delivery` (or `先创建分集` → `/projects/{id}`). Copy: `容量是当前只读观测，不是基准承诺；正式交付仍要求 manifest 校验与人工批准。`
  - For a `delivered/approved` episode the first non-delivered episode is used as `deliveryEpisode`.

### Tab 2 – 时效与失效 (`FreshnessPanel`), `freshness/FreshnessPanel.tsx` (`scopeType="PROJECT"`)
- Header: eyebrow `Production Freshness · 只读`, h3 `生产时效报告`, `报告上限` select (`25/50/100/200`), `刷新` button.
  Copy: `报告只聚合当前 SQLite 事实，不改写历史 Variant、审批、Frame Bridge 或 Timeline。机器证据和 freshness 均不等于人工批准。`
- Summary: `需要更新 {stale}`, `当前有效 {current}`, `已返回 {returned} / limit {query_limit}`.
- Items grouped by `fact_type` (`生成候选` VARIANT, `Frame Bridge` FRAME_BRIDGE, `时间线` TIMELINE) with pill
  `CURRENT`/`STALE`; each shows reasons (`资产参考已变化`, `资产状态已变化`, `Frame Bridge 已变化`, `当前选择已变化`,
  `镜头 revision 已变化`, `生成模型已变化`, `生成提示已变化`, `后期增强配方已变化`), source/current revisions,
  and `remediation_links` rendered as `Link` buttons.
- Audit footer: `只读查询 · {query_count} 条 SQL · 未执行写入 · 未访问公网`.

### Tab 3 – 交付目标与品牌 (`delivery`) — `ProductionSettingsPage.tsx:92-105`
- `ProjectConfigurationSnapshot` (from `configuration.data.configuration`; `onChanged=refetch`).
- `BrandKitPanel`.
- `ProjectHealthPanel`.
- `G9ReadinessPanel` (from `getG9Readiness` data), `{readiness}`.

### Tab 4 – 自动化与外发 (`automation`) — `ProductionSettingsPage.tsx:107-113`
- `AutomationPanel`, `AutomationWorkflowPanel`, `OutboxDeliveryPanel`.

### Tab 5 – 授权与项目包 (`assets`) — `ProductionSettingsPage.tsx:115-138`
- `WorkspaceAssetAuthorizationPanel` (reads `reviewInbox` + `listWorkspaceAssetAuthorizations`; `onChanged` refetches both).
- `ProjectAssetGrantPanel`.
- `ProjectPackageAction` (on import navigates to `/projects/{importedId}`).
- `ProjectTemplateCopyAction` (on copy invalidates projects + navigates to `/projects/{copied.id}`).

## 4.4 Interactive controls table (all tabs)

| Exact visible label | Type | Action | API endpoint + method |
|---|---|---|---|
| tab buttons `生效概览` `时效与失效` `交付目标与品牌` `自动化与外发` `授权与项目包` | tab | `setActiveTab(id)` (updates `?view=`) | route only |
| `处理` (blocker) | link | navigate | route only |
| `管理风格资产` | link | navigate `/projects/{id}/assets` | route only |
| `配置偏好` | link | navigate `/models?project=…` | route only |
| `管理策略` | link | navigate `/projects/{id}/qc-policies` | route only |
| `管理配方` | link | navigate `/projects/{id}/director-recipes` | route only |
| `打开交付` / `先创建分集` | link | navigate | route only |
| `报告上限` (freshness) | select | `setLimit` | `GET /projects/{project_id}/production-freshness?limit=…` (freshness/api.ts:49) |
| `刷新` (freshness) | button | `report.refetch()` | same GET plus `?limit` |
| `ProjectConfigurationSnapshot` (delivery) | component | `onChanged=refetch` | `GET /projects/{project_id}/configuration` |
| `WorkspaceAssetAuthorizationPanel` (assets) | component | `onChanged` refetches both | `GET /reviews/inbox?project_id=…` + `GET /projects/{project_id}/workspace-assets/authorizations` |

## 4.5 Happy-path sequence (configure production settings)
1. Open `生产设置与项目配置`; the `生效概览` tab loads by default and issues a read-only summary fetch
   (`configuration`, `health`, `capacity`, `preferences`, `qc`, `recipe`, `seasons`, `episodes`).
2. Read the `生产就绪性` banner; click any `处理` link to resolve blockers (e.g. bind a Director Recipe, add a project-level QC stage, select a delivery target).
3. Switch to `交付目标与品牌` to inspect the configuration snapshot, brand kit, project health, and G9 readiness.
4. Switch to `自动化与外发` to review automation workflows and outbox delivery.
5. Switch to `授权与项目包` to manage workspace asset authorizations, project asset grants, import a package,
   or copy a project template.
6. Switch to `时效与失效` to view the production freshness report (read-only) and jump to remediation links.
Note: this page itself is **read-only aggregating**; the actual writes live in the linked modules (Asset Bible, QC, Recipes, Delivery).

## 4.6 State / status semantics & edge cases
- **Never a second source of truth**: `具体配置由各自版本化模块管理，不生成第二套业务事实。`
- Readiness blockers derived locally = health blockers + no recipe binding + missing project-level QC stages +
  no selected delivery target (`ProductionSettingsOverview.tsx:54-59`).
- `isDeliveredOrApproved` uses regex `/DELIVERED|APPROVED/` on `episode.production_status`; the first non-delivered
  episode becomes the context for `打开交付`.
- Capacity is present-tense only: `容量是当前只读观测，不是基准承诺`.
- Freshness is strictly read-only: the report includes `local_only: true`, `network_contacted: false`,
  `audit.read_only: true`, `audit.writes_performed: 0`; backend sets `PRAGMA query_only=ON`
  (`production_freshness.py:17`). It aggregates SQLite facts and never rewrites history/approvals.
- `FreshnessPanel` subscribes to live project events (`useProjectEventInvalidation`) for
  `SHOT_REVISION_CREATED`, `SHOT_PRODUCTION_READY`, `episode.shot_plan.changed`, `JOB_FINISHED`, `JOB_REQUEUED`,
  `ARTIFACT_REGISTERED`, `CONTINUITY_STALE_PROPAGATED` (`FreshnessPanel.tsx:10-18`).

---

# Appendix — supporting "production" feature panels (used by the Director Desk / project workspace, not the 4 target pages)

These were requested for context and map to the same backend surface.

## A. `production/ContinuityPanel.tsx`
- Header: eyebrow `FR-WRT-006 · 只读对照`, h3 `跨镜头连续性`, status-pill `small 缩略图`.
- Renders columns `上一镜` / `当前镜` / `下一镜` (`context.shots.previous/current/next`), plus `绑定资产 · {n}`.
- Facets shown: `人物外观`, `服装`, `道具`, `光线`, `空间方向`, `连续性`; missing facets flagged `missing`.
- References show `selection_state`, `purpose`, `v{version_no}`, `integrity_status`; footer `边界约束 {n}`,
  `runtime_contacted=false`, `network_contacted=false`, `mutated=false`.
- Source: `getShotContinuityContext` → `GET /api/v1/shots/{shot_id}/continuity-context` (generated api.ts:735, `production.py:40`).

## B. `production/DirectorShotEditor.tsx`
- Header: eyebrow `FR-WRT-003/005 · 不可变 revision`, h3 `导演分镜字段`; pill `{code} · {readiness.state ?? status}`.
- Field labels: `景别`, `构图`, `主体动作`, `镜头运动` (CameraPlan), `时长`, `对白`, `环境`, `连续性`, `创作意图`.
- Required non-empty: `shot_type`, `composition`, `subject_action`, `camera_plan`, `target_duration_ms`, `continuity`, `creative_intent`.
- CameraPlan edit: `已发布 Profile`, `运动` (STATIC/PUSH_IN/PULL_OUT/PAN/TILT/TRUCK/PEDESTAL/ZOOM/ORBIT/ROLL),
  `方向`, `强度（0—1）`, `运动曲线` (LINEAR/EASE_IN/EASE_OUT/EASE_IN_OUT), `Prompt 降级文本`,
  button `按 Profile 裁决运镜能力` → `GET`/`POST /api/v1/profile-versions/{id}:resolve-camera-plan`.
- `保存新 revision` (checkbox `保存时冻结 revision`) → `POST /api/v1/projects/shots/{shot_id}/revisions`
  `{ fields, freeze }` (generated api.ts:739).
- `标记 Production Ready` → `POST /api/v1/projects/shots/{shot_id}:mark-production-ready` (generated api.ts:743);
  disabled unless `shot.status === "DIRECTED"` and no missing fields.
- `ShotAssetSection` (故事资产): binds story assets via `POST /api/v1/shots/{shot_id}/story-asset-bindings`,
  unbinds via `DELETE /api/v1/story-asset-bindings/{binding_id}`; shows identity-pack staleness and an
  `升级至最新版` button → `bindShotCharacterPack` → `POST /api/v1/shots/{shot_id}/character-identity-packs:bind`
  (identityPackClient.ts:215). Drag-and-drop into `拖到这里绑定到本镜头` (drop only opens confirmation;
  cross-project/archived/repeat binds are rejected client-side by `storyAssetDropIssue`).
- Camera modes: `NATIVE` / `PROMPT_FALLBACK` are production-ready; anything else (`当前未裁决或 Profile 不支持，禁止进入生产`).

## C. `production/PromptTemplatePanel.tsx`
- Header: eyebrow `FR-WRT-004 · 冻结`, h3 `关键词与提示词模板`, pill `{n} 个 Prompt`.
- Fields `标题`, `语言`, `模型 Profile`, `模板`, `展开结果`, `负向词`; button `冻结展开结果`
  → `POST /api/v1/prompts` with `purpose: "GENERATION_TEMPLATE"`, `owner_type: "SHOT"`,
  `content_text = expanded_text`, structured `source_fields/template_text/expanded_text/negative_text/language/model_profile_version_id`.
- Listing shows `{title} · revision {n}`, `FROZEN · 内容 hash {slice}`; helper `保存会创建内容 hash 固定的 PromptRevision，不覆盖历史。`

旧 `production/StoryAssetLibraryPanel.tsx` 已由本表前文描述的 V2 `AssetBiblePage` 完整接管并退役，不再维护第二套资产创建与归档界面。

---

# Questions to resolve
1. **Production Settings tab labels**: the brief guessed `生成/时间/交付/自动/授权`, but the actual `SETTINGS_TABS`
   are `生效概览 / 时效与失效 / 交付目标与品牌 / 自动化与外发 / 授权与项目包`
   (`ProductionSettingsPage.tsx:20-26`). Confirm the feature sheet should describe the real labels (it does here).
2. **`ProductionSettingsOverview` write path**: It is a read-only aggregation. Any "configure" behavior is delegated to
   the linked modules. Confirm the expected "configure production settings" happy path is the module-links flow vs.
   an in-page editor (there is none).
3. **Backend route files**: `story_assets.py`, `prompts.py`, `configuration.py`, `profiles.py`, `gates.py`,
   `reviews.py`, `workspace` authorization endpoints were NOT in the requested read list. Their method/path pairs here are
   taken from the frontend `generated/api.ts`, which is authoritative for the UI, but their handler
   files are not cited. Confirm whether to also read them to cite handlers.
4. **Asset Bible "review/approve an asset version"**: There is no approve button on the Asset Bible page itself —
   `ReferenceVersionCompare` is explicitly read-only (`不修改采用或锁定`). Human approval happens on the
   Character Identity Pack version (`人工审核并批准`). Confirm the intended meaning (approve = identity-pack version).
5. **State kind values**: the Asset Bible UI offers `自定义/服装/伤势/情绪/时段/天气/光线`, while the backend schema
   (`schemas/asset_bible.py:7-9`) also allows `BASE`, `AGE`, `DAMAGE`. The UI is a subset. Confirm whether the sheet
   should surface the full backend enum or only the UI options.
6. **`image`/`video` capability values in recipes**: the UI exposes 4 image + 4 video capability options that match
   `generated` profiles, but the recipe document is free-text at the API boundary (`DirectorRecipeDocument.generation.image.capability`)
   — confirm no server-side allowed-list validation exists (the frontend guard is only the forbidden-execution-field check).
