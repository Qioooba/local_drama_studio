# 导演台改造方案（2026-08-18 调研版）

> 背景：字节 8 月 18 日内测「漫剧创作工具」，用户要求重新上网调研后，对照 LocalDramaStudio 现状给出完整方案：缺什么、改什么、优化什么、界面改成什么布局。
> 本方案所有"现状"结论均以仓库真实代码/迁移文件为准（`apps/api/alembic/versions/0001~0041`、`apps/web/src/app/App.tsx`、各 feature 面板），所有"竞品"结论均附来源。
> 证据分级：✅ = 公开报道证实；🧠 = 内测截图转述，待官方。

---

## 0. 一句话摘要

**我们与字节的差距不在"能力有没有"，而在"导演工作台整合度"**：抽卡、首尾帧、过渡约束、声音绑定、时间线、交付的数据层全部已经存在（迁移 0001–0041 为证），缺的是①分镜组/多参考图等少数数据结构、②几个可操作的 UI 桥接（过渡约束创建、尾帧继承、时间线可视化）、③一个把散落面板整合成「导演台」的空间布局。建议新增一个 **导演台（director）主视图**，采用「左镜头树 + 中央预览 + 右 Inspector + 底部时间线/Take 条」四区布局，并完成 4 张新表迁移与 7 个功能桥接。

---

## 1. 调研更新：竞品全景（本次上网新增核实）

### 1.1 字节系三层产品矩阵 ✅

| 层 | 产品 | 上线时间 | 定位 | 关键能力 |
|---|---|---|---|---|
| 素材 | 即梦 | 长期 | 单素材生成 | 文/图/视频生成 |
| Agent | 小云雀短剧 Agent | 2026-03 | 一键成片 | 10 万字剧本 → 故事蓝图→角色设计→分镜→成片，开放角色/分镜人工编辑（[科技日报](https://www.stdaily.com/web/gdxw/2026-03/20/content_488925.html)、[IT 之家](https://m.ithome.com/html/930665.htm)） |
| 生产系统 | 漫剧创作工具 | 2026-08-18 内测 | 长篇连载工业化 | 20 万字剧本、六节点（上传剧本→项目规划→剧本策划→资产设计→分镜生产→视频合成）、每节点人工干预、豆包大模型（[AI 前线独家](https://api.aitntnews.com/newDetail.html?newId=28337)、[21财经](https://m.21jingji.com/article/20260818/herald/870d7c1ac9f6ad7ae2149d95c9ca5509.html)、[钛媒体](https://www.tmtpost.com/8107419.html)） |
| 企业版 | 火山剧创 Dramart | 2026-05 | 企业级一站式 | 剧本解析→资产设定→分镜→生成→拼接，周期缩短 80%（[火山文档](https://docs.volcengine.com/docs/4/1864794?lang=en)、[AIHub](https://www.aihub.cn/tools/huoshan-juchuang/)） |

### 1.2 其他大厂与创业公司 ✅

- **腾讯 WorkRally**（2026-07 前后）：首款漫剧制作工业级 AI 平台，专家级 Agent 流水线，宣传产能提升 5 倍、成本减半（[腾讯云开发者](https://cloud.tencent.com.cn/developer/article/2693070?policyId=1003)、[澎湃](https://m.thepaper.cn/newsDetail_forward_33006697)）。
- **LibTV（LiblibAI）**：无限画布 + 节点式工作流，把剧本/分镜/图像/视频/音频各环节做成节点，端到端可控（[量子位](https://www.qbitai.com/2026/03/390320.html)、[极客公园深度体验](http://www.geekpark.net/news/367815)）。
- **商汤 Seko 2.0/3.0**：无限画布、**三视图确保角色一致性**、创编一体多剧集生成（[优设网实测](https://www.uisdc.com/hangye/seko)、[商汤官网](https://www.sensetime.com/cn/news/seko-3-0-ai-1)）。
- **万兴剧厂**：满血版 Seedance 2.0 全链路 + 后剪辑闭环（[万兴官网](http://ori-www.wondershare.cn/new/details/id/1186.html)）。
- **开源同栈项目**：`comfyui-auto-drama`（ComfyUI + MiniMax H3 自动化短剧流水线，与本项目技术栈一致，[GitHub](https://github.com/qiukaihui/comfyui-auto-drama)）、`MiniMax-H3-Codex-Drama`（角色/场景设计→分镜→ComfyUI 生成→后期→QC，[GitHub](https://github.com/chiphoton/MiniMax-H3-Codex-Drama)）。

### 1.3 行业界面范式总结：三类布局

| 范式 | 代表 | 优点 | 缺点 | 适合谁 |
|---|---|---|---|---|
| A. 一键 Agent | 小云雀、火山剧创 | 零门槛、快 | 黑盒、编辑点有限、无法导演级控制 | 流量型创作者 |
| B. 节点画布 | LibTV、Seko、ComfyUI | 自由、可组合 | 学习成本高、组织成本高、离"剧"远 | 技术流/工作流玩家 |
| C. 导演台 NLE | 字节漫剧工具、WorkRally | 集/镜头层级清晰、预览+检查器+时间线直觉 | 需要业务建模深 | **专业连载生产** |

**结论**：字节这次把 C 范式做成了主流认知（分镜组 01/02 + 左画面中字段右资产 + 底部 Shot 时间线 🧠）。我们的护城河 = **C + 本地离线 + 多模型 Profile + 不可变抽卡**。建议：主视图走向 C，现有 B（业务画布 canvas 视图）保留为辅助；不做 A。

---

## 2. 现状盘点（以代码/迁移为准，不是猜的）

### 2.1 数据层已有（迁移文件为证）

| 能力 | 实现位置 | 备注 |
|---|---|---|
| 项目/季/集/镜头/场景 | `0001_g2_core.py`（projects/seasons/episodes/shots/scenes） | scenes 表存在但 **shots 无 scene_id**（孤立） |
| 镜头字段版本化 | `shot_revisions`（fields_json + is_frozen） | 导演 9 字段走 JSON 契约，加字段=契约升级非迁移 |
| 首尾帧 | `frame_anchors`（source/extracted media、time/frame index、role_hint、approval）+ `createFrameAnchor` API（position_mode FIRST/LAST）| **数据层与 API 完整** |
| 镜头过渡约束 | `shot_transition_constraints`（from/to shot、from/to anchor、enforcement、compatibility）+ create/validate API | **数据层完整，前端无创建 UI**（ContinuityPanel 只读计数） |
| 抽卡/不可变分支 | `generation_intents` + `generation_variants`（variant_type、parent、seed_policy、input_fingerprint、recipe_hash）+ `variant_input_bindings` + Seed 批量实验 | 完整，前端「只新增，不覆盖」已文案化 |
| 时间线 | `timeline_revisions` + `timeline_items`（track_type/start_us/end_us） | 数据完整，**前端是 JSON 文本框**（`TimelineRevisionPanel.tsx`） |
| 交付 | `episode_render_versions`/`delivery_packages`/`delivery_files` + Outbox | 完整 |
| 资产卡 | `0040_story_assets.py`（story_assets + shot_asset_bindings + role_in_shot）| 资产卡**单张 canonical 图** |
| 角色声音绑定 | `0041_character_voice_bindings.py`（character → voice_profile_version）| 已有（GPT 建议的"角色挂声音"已实现） |
| 模型/Profile | `execution_profiles`/`execution_profile_versions`（capability/model_bundle/契约）+ 兼容性/许可证证据 | 完整 |
| 剧集场景范围 | `0025_episode_scene_ranges.py` + `EpisodeSceneRanges.tsx` | 已有（集↔剧本场景段落） |

### 2.2 前端已有（面板/视图）

8 个视图（`App.tsx` L85）：概览 / 分集生产 / AI 生成工作台 / 审核收件箱 / 业务画布 / 模型与能力 / 任务与机器 / 诊断中心。

关键面板：`GenerationWorkbench`（T2I/I2V/T2V 三模式 + 分支 chips + Seed 矩阵 + 首帧锚点）、`DirectorShotEditor`（9 字段 + CameraPlan + 资产绑定）、`StoryAssetLibraryPanel`（4 类资产卡）、`ContinuityPanel`（只读上下文）、`TimelineRevisionPanel`（JSON）、`DialogueTTSPanel`/`AudioTrackPanel`/`SubtitleRevisionPanel`（音频三件套）、`FormalSelectionPanel`/`ReviewInboxPanel`（选 Take/审核）。

### 2.3 现状问题（就是"优化什么"的原始依据）

- `generation` 视图一页纵向堆 6 个大面板（App.tsx L450）：工作台、授权、后处理、导演编辑器、提示模板、连续性——无空间分区，导演找字段要长滚动。
- `projects` 视图混装了「生产数据（镜头列表、剧本、资产）+ 音频 + 时间线 + 交付 + 门禁」全链路（App.tsx L409-440），镜头列表是纯文本行（shot-row：code/status/blockers/next_action），无缩略图、无 take 数、无模型徽章。
- 时间线 = JSON textarea；过渡约束 = 只读数字。
- 抽卡在 generation 视图，选 Take 在 reviews 视图，入时间线在 projects 视图——一个镜头的完整导演动作跨 3 个视图。

---

## 3. 差距清单（缺什么）

### 3.1 数据层缺口（GAP-D）

| ID | 缺口 | 说明 | 方案 |
|---|---|---|---|
| D1 | **分镜组（Shot Group）**无表 | shots 直接挂 episode；字节有「分镜组 01/02」层级 | 新表 `shot_groups` + `shots.shot_group_id`（迁移 0042） |
| D2 | 资产卡**多参考图**无表 | story_assets 只有单 canonical_media_version_id | 新表 `story_asset_references`（多角度/表情/服装 ref，迁移 0043） |
| D3 | shots 无 scene 关联 | scenes 表孤立；场景资产卡（kind=SCENE）与 scenes 表两套并存 | `shots.scene_id` 可空外键（迁移 0044）；UI 上把「场景资产卡」作为首选载体，scenes 表保留剧本场景段落语义 |
| D4 | **镜头级模型绑定**无持久列 | 生成时经 intent 绑定 profile，但镜头没有"我习惯用哪个 Profile"的持久偏好 | 新表 `shot_generation_preferences`（迁移 0044；默认继承项目/能力级） |
| D5 | 自动首尾帧继承无落点 | frame_anchors 与 transition_constraints 都在，但"上一镜尾帧→本镜首帧"没有一条自动桥接记录 | 复用 `shot_transition_constraints`（constraint_type='AUTO_BRIDGE'，from_anchor=上镜 LAST_FRAME 锚点）＋自动化任务，无需新表 |

### 3.2 功能层缺口（GAP-F）

| ID | 缺口 | 现状 | 方案 |
|---|---|---|---|
| F1 | 过渡约束**创建 UI 缺失** | API 有（createShotTransitionConstraint），前端只读计数 | 导演台 Inspector 加「与上一镜约束」区：选锚点/类型/强度，即时 validate 显示 COMPATIBLE/WARNING/BLOCKED |
| F2 | 时间线无可视化 | JSON 文本框 | 底部时间线渲染 timeline_items 轨道（VIDEO/DIALOGUE/BGM/SFX/SUBTITLE），只读展示 + 拖动排序/入出点编辑（先只读+行级删除，写入仍走不可变 revision） |
| F3 | 分镜字段缺 情绪/转场/声音引用 | 9 字段：景别/构图/主体动作/镜头运动/时长/对白/环境/连续性/创作意图 | 契约升级：+emotion、+transition、+sound_ref（引用对白 TTS/音效资产）→ 12 字段 |
| F4 | 无「上一镜尾帧→本镜首帧」一键继承 | 有锚点 API 但无桥接按钮 | Inspector 首尾帧区加「继承上一镜尾帧」按钮（创建 LAST_FRAME 锚点 + AUTO_BRIDGE 约束 + 填入本镜 FIRST_FRAME 槽） |
| F5 | 无镜头级 Profile 选择器 | Profile 是项目/能力级 | Inspector 加「本镜头模型」下拉（默认=项目默认，显式选择写 shot_generation_preferences） |
| F6 | 无「查看原文」对照 | 字节 UI 有 🧠 | 分镜组/镜头 Inspector 折叠区：显示该镜头对应的剧本段落（来自 episode source_range_json / scenes 范围 + 镜头字段回填） |
| F7 | 无分镜组批量生成 | 只有单镜头 | 分镜组右键/按钮「本组批量规划」：循环走现有 generation 预检/分支规划，产出 N 个独立 Job 列表 |

### 3.3 界面层缺口（GAP-U）

| ID | 缺口 | 方案 |
|---|---|---|
| U1 | 生成工作台一页堆 6 面板 | 拆散分流进导演台五区（见 §4.3 迁移表） |
| U2 | 抽卡/选 Take/入时间线跨 3 视图 | 导演台中央预览内置 Take 条（V1..Vn ★），底部时间线直接"将选中 Take 加入轨道" |
| U3 | 无底部时间线 + Take 条 | 导演台底部新增（F2 的可视化） |
| U4 | 无中央预览区 | 导演台中央（候选切换、首尾帧槽、帧放大） |
| U5 | 镜头列表纯文本 | 升级为缩略图卡片行（poster 缩略图 + 状态 pill + take 数 + 模型徽章 + 阻塞角标） |

---

## 4. 改造方案（改什么）

### 4.1 数据层：4 张新表（迁移 0042–0044，全部 append-only）

```
0042_shot_groups
  shot_groups(id, episode_id→episodes, code, title, order_key,
              summary, status[ACTIVE/ARCHIVED], audit 列, uq(episode_id,code))
  + shots.shot_group_id → shot_groups.id (可空，旧数据=未分组)
  + ix_shots_episode_group (episode_id, shot_group_id, order_key)

0043_story_asset_references
  story_asset_references(id, asset_id→story_assets, role[FRONT/LEFT45/RIGHT45/SIDE/
    FULL_BODY/EXPRESSION/WARDROBE/COSTUME_REF/PROP_DETAIL/SCENE_REF],
    media_version_id→media_versions, note, ordinal, audit 列, uq(asset_id,role,media_version_id))
  # 保留 canonical_media_version_id 为"默认图"，references 为多视图圣经

0044_shot_extension
  + shots.scene_id → scenes.id (可空)
  + shots.emotion / shots.transition / shots.sound_ref (Text 可空)
    # 注意：导演字段历史都在 shot_revisions.fields_json；这三个新字段
    # 同时进 fields_json 契约（读优先 fields_json，落库双写，兼容旧 revision）
  shot_generation_preferences(id, shot_id→shots, capability_profile_version_id,
    purpose[GENERATION/I2V/T2V/T2I], note, audit 列, uq(shot_id,purpose))
    # 默认继承项目 Profile；显式选择才写行
```

> 设计原则：**不动现有表语义**（除 shots 加列），所有新表 append-only，与项目既有审计/不可变风格一致；镜头字段仍走 fields_json 契约（加 3 字段=契约版本升级，旧数据默认为空字符串）。

### 4.2 功能层：7 个桥接（新 API/组件）

| 桥接 | 依赖 | 前端落点 |
|---|---|---|
| B1 过渡约束创建+校验 | 已有 API | 导演台 Inspector「连续性」区 |
| B2 尾帧继承一键桥 | 已有 frame_anchors + transition API + 新增 AUTO_BRIDGE 类型校验 | Inspector 首尾帧区 |
| B3 时间线可视化 | timeline_items 读模型（已有 read model） | 导演台底部 |
| B4 12 字段契约升级 | 后端 fields 校验（`application/production.py` 契约处） | DirectorShotEditor |
| B5 镜头级 Profile 选择 | 新表 + 新路由 `GET/PUT /api/v1/shots/{id}/generation-preference` | Inspector |
| B6 查看原文 | 已有 source_range_json/scene ranges 数据 | Inspector 折叠区 |
| B7 分镜组批量规划 | 复用 generation 预检逻辑循环 | 左栏分镜组操作 |

### 4.3 界面层：新增「导演台」视图（核心设计）

#### 4.3.1 导航重组

```
生产面（做剧）                    管理面（保运行）
 导演台 ★新主视图                  概览
 审核收件箱（保留）                模型与能力
 成片交付 ★从分集生产拆出独立       任务与机器
 业务画布（保留为辅助）             诊断中心
```

- 分集生产视图的职责收缩为：剧本导入、拆集、场景范围、资产库、配置快照（"项目筹备"），重命名「项目筹备」。
- 交付链路（TimelineStatus/DeliveryWorkflow/Outbox）独立成「成片交付」视图。
- 生成工作台面板拆散并入导演台。

#### 4.3.2 导演台五区布局（总览线框）

```
┌──────────────────────────────────────────────────────────────────────────┐
│ 顶栏：项目 / 季 / 集 ▼  [分镜组: 全部|G01|G02|G03]   [查看原文][历史]    │
├──────────────┬────────────────────────────────────────┬──────────────────┤
│ 左栏 镜头树   │ 中央 预览区                              │ 右栏 Inspector    │
│ (约 260px)   │  ┌────────────────────────────────┐   │ (约 380px)        │
│              │  │  当前镜头: SH-023 · 中景·推进    │   │ ▸ 分镜字段        │
│ ▸ EP01       │  │  候选 TAKE 3/5  ◀ ▶           │   │   景别/构图/动作   │
│  ▸ G01 庭院夜 │  │  ┌──────────────────────────┐ │   │   运镜(Profile裁决)│
│   ●SH-021 4s │  │  │                          │ │   │   时长/对白       │
│    SH-022 6s │  │  │     预览 / 视频播放        │ │   │   +情绪/转场/声音 │
│    SH-023 6s◄│  │  │                          │ │   │   环境/连续性     │
│  ▸ G02 内室  │  │  └──────────────────────────┘ │   │ ▸ 资产绑定        │
│   SH-024 8s  │  │  [首帧槽][尾帧槽] ←继承上一镜尾帧 │   │   C001 林晚(主角) │
│   SH-025 5s  │  │  关键帧条: ●●●○● (提取帧缩略)    │   │   S014 顾家庭院   │
│ ▸ EP02       │  │  提示: 与上一镜约束 COMPATIBLE ✓ │   │ ▸ 首尾帧锚点      │
│              │  │  [标记 Production Ready]        │   │ ▸ 本镜头模型      │
│              │  └────────────────────────────────┘   │   [MiniMax H3 ▼]  │
│              │                                        │   (继承项目默认)   │
│              │                                        │ ▸ 查看原文(折叠)   │
│              │                                        │ ▸ 生成历史        │
│              │                                        │   V1 V2 ★V3 V4    │
├──────────────┴────────────────────────────────────────┴──────────────────┤
│ 底部 集时间线 + Take 条 (约 220px)                                         │
│  VIDEO  ▮SH-021▮▮SH-022▮▮SH-023▮(take3)▮SH-024▮…                         │
│  DIALOG ▮对白1▮▮对白2▮▮对白3▮                                            │
│  BGM    ▮bgm_a▮                                                            │
│  SFX    ▮音效▮                                                             │
│  字幕   ▮字幕条▮                                                            │
│  Take 条: [V1] [V2] [★V3 动作最好] [V4 构图最好] [V5 脸漂移❌]  [+ 生成]    │
└──────────────────────────────────────────────────────────────────────────┘
```

#### 4.3.3 分区细节规范

**① 左栏 · 镜头树**（改造 `shot-row` → 卡片行）
- 每行：poster 缩略图（有候选用候选海报、无则灰底 code）＋ code ＋ 时长 ＋ 状态 pill（state-*）＋ take 数徽章 ＋ 模型徽章（H3/本地）＋ 阻塞角标（blockers 数）。
- 点击行 → 中央预览/Inspector 联动（复用现有 selectShot + location state）。
- 分镜组折叠、右键菜单：「本组批量规划」（F7）。
- 空态：「当前集还没有镜头」。

**② 中央 · 预览区**
- 无候选时显示「尚无代理视频候选」＋ 一键跳生成表单（内嵌工作台的模式卡 T2I/I2V/T2V，不再占整页）。
- 有候选时：当前 take 播放/海报 ＋ 「◀ ▶」take 切换 ＋ take 序号（3/5）。
- 下方首尾帧槽（复用 GenerationWorkbench media-slot 组件）：首帧槽/尾帧槽/「← 继承上一镜尾帧」（F4）。
- 关键帧条：已注册 FrameAnchor 缩略图横排。
- 连续性与阻塞提示条（复用 review-guidance/review-success）。

**③ 右栏 · Inspector**（改造 DirectorShotEditor）
- 分区分组（fieldset 折叠）：分镜字段（12 项）/ 资产绑定（沿用 shot-asset-bindings）/ 首尾帧锚点 / 本镜头模型（F5，默认显示「继承项目默认 · H3」）/ 声音（对白 TTS 引用）/ 查看原文（F6）/ 生成历史（V1..Vn 缩略条）。
- 底部动作：保存 revision、按 Profile 裁决运镜、标记 Production Ready（沿用现有 missing 校验）。
- Prompt 降级文本、创作意图等低频字段放折叠区，保持首屏不超两屏。

**④ 底部 · 集时间线 + Take 条**
- 轨道渲染：读 timeline_items 按 track_type 分轨、按 start_us 横向定位；标尺 0-集时长；选中镜头高亮对应 VIDEO 段。
- 只读 + 行内删除 + 拖拽排序（写入仍创建不可变 TimelineRevision）；「加入当前 Take」按钮把选中 take 的 media_version_id 插入 VIDEO 轨（默认接在上一段后）。
- Take 条：选中镜头的全部媒体候选（take_no 升序），★=正式选择（selections 表已有），❌=被拒，一键「生成新 take（+1/+4）」跳工作台表单并预填当前镜头。

**⑤ 顶栏**
- 面包屑 + 集切换下拉（沿用 topbar selects 但移到导演台内部，顶栏全局仍保留）。
- 分镜组 tabs：全部 / G01 / G02…（无分组时只显示「全部」）。
- 「查看原文」「历史」按钮（F6 / 审计历史）。

#### 4.3.4 面板迁移表（谁去哪）

| 现位置 | 面板 | 迁移到 |
|---|---|---|
| generation | GenerationWorkbench（模式卡+分支+Seed 矩阵） | 导演台中央（生成表单改为中央抽屉/折叠区，不再整页） |
| generation | DirectorShotEditor | 导演台右栏 Inspector（12 字段版） |
| generation | ContinuityPanel | 导演台右栏「连续性」区（升级为可创建约束 B1） |
| generation | PromptTemplatePanel | Inspector「提示模板」折叠区 |
| generation | WorkspaceAssetAuthorizationPanel | 保留在 generation 原视图（改名「生成授权」小面板，或并入导演台设置） |
| generation | PostProcessPanel | 审核收件箱（后处理是审核后的动作） |
| projects | TimelineRevisionPanel | 导演台底部（可视化版 B3） |
| projects | AudioTrackPanel/DialogueTTSPanel/SubtitleRevisionPanel | 导演台底部对应轨道；设置入口留「项目筹备」 |
| projects | EpisodeReviewPanel/TimelineStatus/G8 | 「成片交付」新视图 |
| projects | StoryAssetLibraryPanel/CreativeLibrary/ScriptImport/AIDraftReview/EpisodeSceneRanges/StoryboardBatch | 「项目筹备」视图 |
| projects | ProjectList/模板复制/包导入 | 概览 + 项目筹备 |

#### 4.3.5 响应式与可访问性（遵守既有审计标准）

- 五区在 <1280px 时：右栏 Inspector 折叠为底部抽屉；<900px 时左栏树收进抽屉，中央+底部保留。
- 全部新增控件沿用全局输入皮肤（min-height 40px）、中文文案、`aria-*`、无 window.prompt/alert、无裸 console、空态文案，保证既有审计脚本（verify-ui/strict-browser-audit）继续全绿。

---

## 5. 优化清单（存量优化）

| ID | 优化点 | 价值 | 成本 |
|---|---|---|---|
| O1 | 时间线 JSON→可视化轨道 | 导演台底座 | 中（D 阶段） |
| O2 | 镜头列表缩略图化 | 一眼看到生产状态 | 低（复用 poster 缩略图 API） |
| O3 | 分集生产视图拆分（筹备/交付独立） | 职责清晰、减少单页堆叠 | 低（纯前端重组） |
| O4 | ContinuityPanel 只读→可操作 | 解锁过渡约束已有 API | 低 |
| O5 | 镜头行直显 next_action 引导 | 复用 read model 既有字段 | 低 |
| O6 | 「首尾帧·待能力发布」chip 与 H3_REF2VA 能力真实联动 | 文案不误导（能力发布后自动解锁） | 低 |
| O7 | 导演台内直接选 Take 入时间线 | 抽卡→成片闭环一屏完成 | 中 |
| O8 | 资产卡多参考图切换画廊 | 角色圣经落地（D2） | 中 |
| O9 | 全局空态/骨架屏统一 | 观感 | 低（收尾阶段） |

---

## 6. 实施路线图（本地单人开发估算）

| 阶段 | 内容 | 交付物 | 验收 | 估时 |
|---|---|---|---|---|
| **A 数据层** | 迁移 0042–0044 + read model 扩展 + shot fields 12 字段契约升级 + 单测 | 新表、新路由、API 契约 | pytest 全绿、OpenAPI 更新 | 0.5–1 天 |
| **B 导演台骨架** | 新视图 + 五区布局 + 镜头树升级（缩略图/徽章）+ 导航重组 | 导演台可导航、面板迁移表落地 | Playwright 8 视图审计改写后全绿、无溢出 | 1–2 天 |
| **C Inspector 升级** | 12 字段 + 镜头级模型选择 + 首尾帧继承 + 过渡约束创建 + 查看原文 | 导演可单镜头完整操作 | 单测 + 手工冒烟（G2 smoke 项目） | 1 天 |
| **D 时间线+Take 条** | 轨道可视化 + 加入 Take + 排序/删除 | 底部时间线可用 | 时间线操作审计 | 1–2 天 |
| **E 分镜组+自动化** | 分镜组批量规划 + AUTO_BRIDGE 自动化任务 | 批量生产能力 | 端到端跑通一集 | 1–2 天 |
| **F 收尾** | O3/O9 优化 + 中文文案 + 全套审计回归 | 方案全量交付 | tsc/vitest/pytest/Playwright 全绿 | 1 天 |

**合计约 5–9 个工作日**；阶段 A/B 可先行（用户价值最大、风险最低），C/D 递进，E/F 视优先级裁剪。

### 风险与对策

- **风险 1**：导演台大改打破既有 8 视图审计。→ 阶段 B 采用「新增视图、原视图保留为回退」策略，审计脚本按新导航改，回退点明确。
- **风险 2**：fields_json 契约升级影响旧 revision 读取。→ 双读兼容（缺失字段=空），写时双写，单测覆盖旧数据。
- **风险 3**：H3 ref2v 能力未发布导致首尾帧按钮形同虚设。→ O6 真实联动，能力未发布时按钮禁用+解释，发布即解锁。
- **风险 4**：时间线可视化与不可变 revision 冲突。→ 拖拽排序先出「草稿预览 → 一键创建新 TimelineRevision」两段式，不破坏不可变性。

---

## 7. 与字节方案的差异化声明（一句话）

> 字节做「豆包生态的一键工业化生产系统」，我们做「本地离线、多模型 Profile、不可变抽卡的**导演工作台**」——同一个 C 范式，但我们把每一镜的模型选择、Seed、Prompt、首尾帧、Take 历史全部摊在桌面上，这是内测版字节工具尚未展示、也因绑定自家模型体系而不愿展示的部分。

## 8. 参考来源

- 字节漫剧创作工具：[AI 前线独家](https://api.aitntnews.com/newDetail.html?newId=28337) · [21财经](https://m.21jingji.com/article/20260818/herald/870d7c1ac9f6ad7ae2149d95c9ca5509.html) · [钛媒体](https://www.tmtpost.com/8107419.html) · [起飞客](https://qifukexue.com/?p=24137)
- 小云雀：[科技日报](https://www.stdaily.com/web/gdxw/2026-03/20/content_488925.html) · [IT 之家](https://m.ithome.com/html/930665.htm) · [爱范儿实测](https://www.ifanr.com/1658787)
- 火山剧创 Dramart：[火山官方文档](https://docs.volcengine.com/docs/4/1864794?lang=en) · [AIHub](https://www.aihub.cn/tools/huoshan-juchuang/)
- 腾讯 WorkRally：[腾讯云开发者](https://cloud.tencent.com.cn/developer/article/2693070?policyId=1003) · [澎湃](https://m.thepaper.cn/newsDetail_forward_33006697)
- LibTV：[量子位](https://www.qbitai.com/2026/03/390320.html) · [极客公园](http://www.geekpark.net/news/367815)
- 商汤 Seko：[优设网三视图实测](https://www.uisdc.com/hangye/seko) · [商汤官网](https://www.sensetime.com/cn/news/seko-3-0-ai-1)
- 万兴剧厂：[万兴官网](http://ori-www.wondershare.cn/new/details/id/1186.html)
- 同栈开源：[comfyui-auto-drama](https://github.com/qiukaihui/comfyui-auto-drama) · [MiniMax-H3-Codex-Drama](https://github.com/chiphoton/MiniMax-H3-Codex-Drama)

---

## 9. 2026-08-30 实施校准

本轮只补齐原方案已经定义、且能复用现有领域能力的部分，没有把导演台扩张成第二套 NLE：

- **F2 / B3 / O1 / 阶段 D（部分完成）**：导演台新增按需展开的「本集同步预览」，读取既有 Edit v2 聚合事实，并与后期编辑页复用同一个多轨渲染组件。导演台只允许播放头定位和镜头跳转；时长、转场、排序、保存与冻结仍由后期编辑页拥有。
- **F7 / B7 / 阶段 E（批量规划完成）**：选中镜头先走无副作用预检，再显式提交到既有 Automation Workflow；每镜仍创建独立 Variant / Job，失败可恢复，批量操作不覆盖旧 Take，也不自动采用或批准。
- **统一视觉修饰**：作为 `DirectorIntentV3.prompt_modifiers` 的显式持久化事实进入共享提示词编译器；单镜与批量生产使用同一编译规则，避免页面拼接出不同 Prompt。
- **仍未实施**：导演台内的时间线写入、排序或删除，以及 AUTO_BRIDGE。本轮没有为满足视觉布局而复制后期命令，也没有把机器检查提升为人工批准。

外部建议中的一键修脸、2D/3D 机位、智能 BGM 和无明确工作流的 QuickCreate 导入不属于本方案当前阶段，继续留待真实需求与模型能力证据出现后再评估。
