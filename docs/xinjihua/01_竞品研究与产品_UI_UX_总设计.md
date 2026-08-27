# LocalDramaStudio 2026 漫剧竞品研究与产品 / UI / UX 总设计

> 文档定位：竞品研究 + 产品重构 + UI/UX 详细设计稿（文字规格）  
> 适用对象：产品负责人、UI/UX、前端工程师 Agent、后端工程师 Agent、测试 Agent  
> 研究基线：2026-08-21（竞品网页与当前 V2 实机复核）  
> 当前项目源码：`F:\AI_Projects\h3\local_drama_studio` 当前工作树；`local_drama_studio_src_2026-08-18.zip` 仅作为历史设计来源  
> 结论优先级：**真实生产体验 > 保留旧页面结构 > 少改代码**。允许重构、允许大改，但不允许为了“重做”而丢弃已经存在且正确的版本、审核、任务、连续性和本地生产能力。

---

## 0A. 2026-08-21 当前实现控制块（冲突时以本节为准）

本文主体最初用于指导 V2 迁移，其中相当一部分已经实现。后续开发 Agent 必须先读本控制块，再读后面的目标设计；当旧章节中的“当前页面”“待迁移阶段”或旧截图判断与本节冲突时，**以 2026-08-21 当前工作树、实机截图和本文末尾的“当前 V2 规范增补”为准**。

禁止把历史计划重新执行一遍：

- V2 Router、`AppShell`、`LegacyRouteBoundary` 已接管入口，旧 `App.tsx` 已删除；不得恢复旧入口。
- 数据库已到 `0048_asset_proposals`；不得重建或改号 `0042`—`0048`。
- Story 长文导入、Asset Bible、Episode Plan 基础域、Director Desk、候选/采用/撤销、Frame Bridge、Generation、Review、Audio、Timeline、Delivery、Episode Run、模型偏好、QC、Recipe 和持久 Job 均已有真实实现；只能按“保留 / 补齐 / 拆分 / 退役”矩阵演进。
- 当前不是缺少页面，而是部分页面仍把能力纵向堆叠；目标是重新分配任务所有权和交互形态，不是复制第二套事实与第二套任务系统。

当前实机发布阻断：

1. Episode Plan 因两个组件共用 `['script-breakdown-drafts', projectId]` 却缓存不同响应形状而稳定崩溃。
2. Director Desk 在 1024 / 1280 / 1440 / 1920 宽度存在主网格 intrinsic width 溢出，常见宽度下 Inspector 不可达。
3. 从 Director 的非第一镜进入手工生成时，链接把镜头放在 query，Generation 只读 path param，实际会错选第一镜。
4. 新旧 capability 名称和解析路径分裂，当前不能把“自定义选模型”宣传成端到端可靠能力。

本轮证据位置：

- 当前 V2：`docs/research/assets/current-v2-2026-08-21/`，22 个路由原始全页截图、80 个 720px 切片、5 张缩略联系表和 `manifest.json`。
- 竞品：`docs/research/assets/competitors/`，原始公开图、逐产品缩略联系表、来源记录和总索引；图片数量只代表已归档证据，不代表市场排名。
- 视觉审计：`docs/research/notes/current_v2_visual_audit.txt`。
- 当前源码审计：`docs/research/notes/repo_audit.txt`。

---

## 0. 先给最终结论：本项目下一阶段应该变成什么

LocalDramaStudio 不应继续演化为“很多独立功能面板的集合”，而应该重构成一个**以导演台为中心的 AI 漫剧生产操作系统**。

目标不是复制某一个竞品，而是吸收不同产品最成熟的部分：

1. 学字节“漫剧创作工具”：**低门槛线性主流程、第一屏单一任务、分集级合成、镜头编辑中资产就近可用**。
2. 学小云雀：**三步式新手体验、自动完成大部分前置工作、线性分镜 + 右侧预览 + 底部时间线**。
3. 学 WorkRally / DramaBuddy / Seko / LibTV：**Agent + Skill + 资产长期复用 + 无限画布作为专家模式，而不是把所有用户强迫进画布**。
4. 学纳逗 Pro / 天工短剧工作台：**减少“抽卡”，把镜头语言、站位、多视图、机位、光线变成可视化控制**。
5. 学万镜一刻 / 火山剧创：**Agent 自动模式 + 人工模式双轨，不让“一键生成”和“专业精修”互相排斥**。
6. 保留 LocalDramaStudio 已有优势：**本地优先、模型可替换、不可变版本、显式审核、持久 Job、首尾帧连续性、SQLite/WAL 真值、媒体血缘、自动化工作流**。

因此，目标产品形态应固定为：

> **“极速 Agent 模式负责把 80% 的剧做出来；导演台负责把关键 20% 做对；高级画布负责非线性试验；整个系统共享同一套角色/场景资产、模型能力、生成版本、审核和时间线。”**

这也是本次重构的北极星。

---

# 第一篇：竞品全景与证据等级

## 1. 研究方法

本次不把“官网一句宣传文案”当成产品事实，采用四级证据：

| 等级 | 定义 | 本文如何使用 |
|---|---|---|
| A | 官方产品页、官方文档、官方 GitHub、官方新闻 | 可作为功能事实 |
| B | 主流媒体现场报道、较完整实测文章，并能看到真实界面截图 | 可用于流程与 UI 判断 |
| C | 媒体转述、行业媒体、二手实测 | 只用于补充，不作为唯一依据 |
| D | 搜索结果、社区零散说法、未能核验完整页面 | 只列雷达，不下确定结论 |

同时，对“公开截图没有展示某项功能”与“产品没有某项功能”严格区分。本文凡写“未看到”，都只表示**公开证据中暂未看到**，不能推导为产品绝对没有。

## 2. 竞品全景：建议持续跟踪的产品池

### 2.1 第一梯队：必须长期跟踪、已经影响本项目设计

| 产品 | 主要路线 | 最值得学习 | 本项目不能照搬的部分 |
|---|---|---|---|
| 字节“漫剧创作工具” | 长剧本 → 项目规划 → 剧本策划 → 资产 → 分镜 → 合成 | 极清晰线性流程、首尾帧开关、镜头就近资产、合成本集 | 豆包/字节生态强绑定；线性流程对专家可能偏僵硬 |
| 小云雀短剧 Agent | 一句话/长剧本 → 角色场景 → 分集视频 | 新手最易懂、自动程度高、线性视频台 | 仍有抽卡、越轴、站位和连续性不稳定 |
| 火山剧创 Dramart | Agent / 人工双模式 | 自动和人工两种心智清楚 | 云端/平台能力不是本地项目可直接复制 |
| 腾讯 WorkRally | 工业级 Agent + Skill + 无限画布 | 资产多状态、多视图、Skill、Agent 自主调用专业能力 | 空白无限画布对普通用户学习成本高 |
| 腾讯 WorkSolo | 个人化 AI 短剧 / 互动影视 / 自由画布 | 轻量个人入口、自由创作 | 功能仍处内测快速变化期 |
| 商汤 Seko 3.0 | Agent + 无限画布 + Skill | Agent 调度模型与 Skill、资产沉淀 | 过度依赖 Agent 可能弱化显式控制 |
| LibTV | Creator + Agent 双入口、无限画布 | 人类和 Agent 共用同一创作引擎、Skill 生态 | 节点式流程复杂度高，不宜成为唯一入口 |
| 纳逗 Pro | 专业影视智能体 + 3D 导演台 | 3D 预演、多机位、编剧助手、团队工作空间 | 3D 全量重做成本高，本项目应分阶段实现 |
| 天工短剧工作台 | Agent 智能分镜 + 无限画布 + 3D 导演台 | 多视图资产、站位、720°、多角度、打光、成本数据中心 | 复杂工具过多时容易让新手迷失 |
| 阿里“万镜一刻” | 故事板 / 无限画布 / Agent 三模式 | 同一平台三种工作模式、团队模板复用 | 云端生态与企业部署不是本项目当前边界 |
| 万兴剧厂 | 长文本改编 + 资产 + 分镜 + 画布 + 后期 | 全链路、企业团队、模型集成 | 依赖外部模型；曾有大镜头粒度过粗的实测问题 |
| 阅文 DramaBuddy | IP 改编 + Skill + 无限画布 + 全景导演台 | 长篇主体多状态、任意节点开创作、虚拟预演 | 强 IP 生态能力不可复制 |
| 掌阅泡漫 PopoMint | 工业流水线 | 九宫格、三视图、远中近景、一站式量产 | 公开 UI 细节相对少 |
| 巨日禄 | 专业团队量产 | 团队批量生产、多模型调度、行业化流水线 | 对新手友好度不是其唯一目标 |
| Alibaba LumenX | 开源 Pipeline-first + Playground | 架构可参考、三视图、多模型、批量抽卡、TTS、FFmpeg | 当前项目已有更强本地审计/版本，不要反向简化 |

### 2.2 第二梯队：应该吸收特定能力

- 快手“造梦专家 2.0”：公开报道确认“剧本—分镜—视频”一站式生成，重点关注分发生态与平台生产闭环。
- 360“纳米漫剧流水线”：更偏专业团队和工业量产，关注自动审核、规模调度。
- 美图 RoboNeo：2026-07 已公开 3D 导演台、项目资产库、全链路工作流、剧本/分镜、后期增强，关注“视觉编辑工具 + Agent”的融合。
- ELSER.AI / 心影次元：角色库、专业 Agent 分工、网文/IP 衍生方向。
- 千幕 Qianmu：短剧/漫剧工作台、镜头级节点和角色资产能力。
- 绘梦画布 / 绘梦工坊：节点画布 + 角色三视图 + 资产可视化。
- 剧火 AI：关注团队生产与批量生成，但公开可核验 UI 资料暂不足以做深度结论。
- 可梦 AI、有戏 AI、幻舟 AI、MicroDrama AI、萤火织光、AniShort 等：放入季度雷达，不直接作为本轮架构主参照。

### 2.3 邻接工具层：不是完整竞品，但必须兼容

这些更多是“生成模型/单点工具”，不应拿来指导整套项目 IA，但会影响模型路由层：

- Seedance / 即梦 / 小云雀底层视频能力
- Kling / 可灵
- Vidu
- Wan 系列
- PixVerse
- Runway
- Hailuo / 海螺
- ComfyUI 生态
- TTS / lip-sync / 音乐模型

本项目的差异化不是“永远绑定某一个最强模型”，而是：**模型会换，导演意图、资产、版本、首尾帧、审核、时间线不能跟着换。**

---

# 第二篇：字节“漫剧创作工具”深拆

## 3. 已核验公开事实

截至 2026-08-18 的公开报道显示：

- 产品名直接为“漫剧创作工具”，处于内测。
- 搭载多个豆包大模型。
- 支持个人创作与团队协作，一个团队空间支持 30 名成员。
- 首页工作台首先要求“上传剧本”。
- 支持本地 PDF / DOC，最大 20 万字。
- 项目规划阶段设置剧目名称、画面比例、画风。
- 剧本策划由系统自动理解和拆分，报道给出的常规等待时间约 10 分钟。
- 资产阶段自动建立人物、场景框架，支持批量/单个 AI 生成、上传、编辑、新增、删除、引用资产库、批量导出。
- 系统按集拆分分镜生产脚本。
- 分镜可进入编辑子流程并选择叙事模式。
- 右上角存在“合成本集”，可导出本地。
- 可绑定抖音短剧创作者中心账号，进行跨端数据互通。

### 3.1 截图 1：产品首页 —— 极强的“唯一下一步”

真实截图的结构非常克制：

- 整体深黑背景，左侧只有很窄的图标栏。
- 中央是产品名“漫剧创作工具”。
- 视觉中心不是 dashboard，不是状态卡，而是一个巨大上传区域。
- 主按钮只有“上传剧本”。
- 上传区直接告诉格式和 20 万字限制。
- 下方只有三张价值说明卡，没有塞入模型、队列、SQLite、服务器状态等工程信息。
- 右上角团队入口单独存在。

**可学习点：**

新用户进入创作工具时，不需要先理解系统架构。第一屏应该回答一个问题：**“我现在要怎么开始？”**

LocalDramaStudio 当前第一层界面仍然显示 `API HEALTHY / LOCAL_ONLY / SQLite WAL / Profile` 等工程状态。这些对开发者有价值，对创作者却占用了最宝贵的第一视区。

目标调整：

- 首页创作区只保留“导入小说/剧本”“从空白分集开始”“从已有项目继续”三类任务。
- API / SQLite / GPU 等状态只缩成右上角一个“本机正常”状态点；异常时才展开。

### 3.2 截图 2：项目规划 + 剧本策划 —— 两阶段并排而不是几十个卡片

公开截图显示：

- 黑色点阵工作区。
- 左侧大卡片标记 `01 项目规划`，包含剧本文件、剧名、画面比例、画风。
- 比例以可点击可视卡形式出现：9:16、3:4、4:3、16:9、1:1。
- 画风是图像缩略图选项，不是纯文本 Select。
- 右侧 `02 剧本策划` 展示系统抽取/生成的结构化内容。
- 两个阶段在空间上并列，告诉用户“输入是什么、AI 将产出什么”。
- 底部只有一个强主动作“生产资产”。

**可学习点：**

本项目应该减少“配置页”和“生产页”的来回跳转，采用**阶段式 Master Workflow**：

`项目设定 → 故事理解 → 资产圣经 → 分集规划 → 导演台 → 合成`。

每个阶段都应该满足：

- 左边是“当前事实/可编辑输入”；
- 右边是“AI 结构化结果/待确认”；
- 底部仅一个主动作推进；
- 任意时候可以退回上一步，但退回修改会明确提示哪些下游资产变成 stale。

### 3.3 截图 3：资产详情 —— 媒体优先的 Master-Detail

公开角色页采用：

- 左：角色列表 + 头像 + 新增角色。
- 中：巨大角色主视觉。
- 主图下方：参考图集合，可新增。
- 右：角色描述、出场集、服饰/身份等结构化信息。

这比 LocalDramaStudio 当前 `StoryAssetLibraryPanel` 的“96×54 小缩略图 + code + description + 粘贴 canonical_media_version_id”更符合创作心智。

**必须学习：**

角色资产不是一条数据库记录，也不是只有一个 canonical image；它是一个**角色圣经（Character Bible）**，至少由：

- 主形象
- 正/侧/背三视图
- 半身/全身
- 表情九宫格
- 特写细节
- 服装/年龄/受伤等状态
- 声音
- 角色描述/DNA
- 负向约束
- 出现集数

组成。

### 3.4 截图 4：分镜编辑器 —— 本次重构最重要的参考

这一张界面尤其重要。可直接观察到：

- 左侧是大号竖屏画面预览。
- 中间是当前分镜的结构化镜头文本，而不是一整段 Prompt。
- 文本里可见时长、环境、机位/运动、声音等字段。
- 角色/场景用实体 chip 的方式插入语义，而不是让用户手工写 ID。
- 右侧紧贴资产库，人物、场景可直接选。
- 顶部可“查看原文”“查看历史”。
- 底部是整集 filmstrip，用户始终知道当前镜头在整集的位置。
- 可切换叙事模式。
- **界面明确存在“首尾帧功能”开关。**
- 整体是“媒体预览 + 镜头语义 + 资产 + 时间线”同屏。

这说明行业正在从“生成器”转向“导演工作站”。

LocalDramaStudio 应把目前分散的：

- `GenerationWorkbench`
- `DirectorShotEditor`
- `StoryAssetLibraryPanel`
- `ContinuityPanel`
- `PromptTemplatePanel`
- `ImageCandidateGrid`
- `ReviewInboxPanel`
- 首尾帧 / frame anchor
- Timeline

重组到**同一个 Director Desk 上下文**，而不是继续分别做页面。

### 3.5 截图 5：集级动作 —— “生成剩余 / 导出本集 / 合成本集”始终可见

右侧上方公开截图可见：

- 生成剩余分镜
- 导出本集
- 合成本集
- 下方仍是资产库

这个设计非常符合漫剧真实生产：用户不会逐个镜头“完成以后忘了全局”。

本项目也必须提供常驻集级动作：

- `生成缺失镜头`
- `仅重新生成失败镜头`
- `运行整集 QC`
- `合成本集`
- `提交整集审核`
- `导出`

但必须与现有严格审批体系兼容：生成完成 ≠ 选择 ≠ 批准 ≠ 可交付。

## 4. 字节方案的优势

1. **心智负担低。** 不需要先理解模型、任务、Provider。
2. **线性主流程非常适合长剧本转漫剧。**
3. **资产与分镜紧密绑定。**
4. **分集是核心生产单位。**
5. **首尾帧已经上升为用户可见能力，而不是底层工程概念。**
6. **局部人工干预存在，不是彻底黑盒。**
7. **合成本集在镜头工作台附近，而不是藏在另一个“交付系统”。**
8. **团队/抖音生态有天然闭环。**

## 5. 字节方案的可推断弱点 / LocalDramaStudio 的机会

以下为“公开界面推断”，不是声明字节产品一定缺失：

1. **线性流程对专家用户可能偏强约束。** 本项目可通过“专业导演模式 + 高级画布”解决。
2. **公开资料以豆包/字节模型为核心。** 本项目应保持模型中立，允许本地 ComfyUI、不同 LLM、图像/视频/TTS Profile 自由配置。
3. **公开的首尾帧更像一个用户级开关，尚未在截图中看到完整的边界状态/锁定/继承/冲突管理。** LocalDramaStudio 已经有 `frame_anchors + shot_transition_constraints + stale`，应该把这项底层优势做成更强的可视化“镜头桥”。
4. **公开截图未展示 3D 站位/多角度导演工具。** 纳逗 Pro / 天工在这一点更激进。
5. **内测意味着流程仍可能快速变化。** 不应逐像素仿照，而应该吸收心智模型。

---

# 第三篇：其他主流产品深拆

## 6. 小云雀短剧 Agent

### 6.1 已核验流程

2026-03 官方/主流媒体资料确认：

- 最多上传 10 万字剧本；
- 自动故事理解和全局角色管理；
- 从剧本到视频成片；
- 公开实测中也可从一句故事梗概启动。

### 6.2 UI 结构

公开实测截图中，小云雀比字节漫剧工具更“轻产品化”：

- 浅色极简全局设置窗：剧名、风格、比例。
- 顶部三步 Stepper：`剧本大纲 → 角色和场景 → 分集视频`。
- 分集使用卡片列表，能看到角色/场景数量和生成入口。
- 视频编辑台：左资产、中脚本/提示词、右视频预览、底部时间线。
- 模型选择与当前生成动作同屏。
- 失败镜头在原位显示失败并可重试，而不是要求去“任务中心”找。

### 6.3 公开实测暴露的痛点

钛媒体 2026-04 实测显示：

- 角色设定与生成角色图可能不一致；
- 人物站位可能错误；
- 同 Prompt 重抽可能修复一个问题，却带来另一个问题；
- 一集成片中可能出现镜头不衔接、越轴、细节错误。

**对本项目的启示：**

“重抽”不能只是重新点一次 Generate。重抽应该记录：

- 为什么重抽；
- 是保持 Prompt 仅换 seed，还是修改角色/构图/动作/运镜；
- 上一 take 的错误标签；
- 是否继承首尾帧；
- 新旧 take 对比；
- 最终哪一条被选中。

这正好可以复用现有 `GenerationIntent / GenerationVariant / branch_reason / parent_variant_id`。

---

## 7. 纳逗 Pro

### 7.1 早期 2026-04 与当前 2026-06 要分开判断

4 月份公开实测曾批评：任务黑箱、运行后难中止/修改、积分不透明、视频顺序不好管理。这些是**当时版本实测**，不能直接等同于 6 月以后的产品状态。

6 月 23/24 日的新一轮升级公开确认：

- 编剧助手：小说/剧本 → 大纲、人设、分场，多形态改编与评估；
- 3D 导演台：人体素模、多机位预览构图；
- 图片二次打光；
- 720° 全景图；
- 团队工作空间最高 100 人同时在线编辑画布；
- 历史资产、工程文件、积分管理；
- 开发及规划中的智能体接近 70 个。

### 7.2 最值得学习

**“不要靠更复杂 Prompt 解决空间问题；把空间问题变成空间工具。”**

双人对话、群像、武打、视线关系，用文字描述永远有随机性。纳逗的 3D 导演台代表一个非常重要的方向。

但 LocalDramaStudio 不应 P0 就引入完整 3D。建议三阶段：

- P0：镜头角度 / 景别 / 朝向 / 轴线的可视化预设。
- P1：2D Staging Board，人物点位 + 相机方向 + 轴线 + 景深，用 SVG / Canvas 即可。
- P2：真正 3D Previz（Three.js），用于复杂场面。

这样能获得 80% 可控收益，而不在第一轮重构陷入 3D 工程黑洞。

---

## 8. 天工短剧工作台

### 8.1 2026-07 的核心方向

公开资料明确将产品升级为：

- Agent 智能分镜 + 无限画布双轨；
- 多视细节资产；
- 生成前规划人物站位和机位；
- 参考上一镜构图生成下一镜站位；
- 可导入导演提示词模板；
- 720° 全景；
- 多角度编辑器；
- 光源编辑器；
- 3D 导演台；
- 团队账号/子工作室权限；
- 数据中心统计项目、剧集、成员、时间维度的算力消耗。

### 8.2 UI/交互最值得学习

1. 无限画布不是唯一模式，而是与 Agent 分镜双轨。
2. 多角度编辑不是让用户重新写 Prompt，而是角度/距离/俯仰等可视控件。
3. 3D 导演台先排人物，再生成图/视频。
4. 成本数据被纳入制片管理，而不是等月底看账单。

### 8.3 本项目对应方案

- **已被 `04_画布与_ComfyUI_整体重构设计开发方案.md` 取代：** 不保留现有生产 DAG 作为 Lab 基础；退役旧 `ProductionCanvasPanel`，新建结构化 Production Cockpit 与独立 Visual Lab。
- 在 Director Desk 中增加“镜头构图”轻量视觉控件。
- 本地模式成本中心改成：GPU 时间、预计耗时、VRAM、磁盘、失败率、平均重抽次数；未来若接云端再加入金额/credits。

---

## 9. WorkRally / WorkSolo

### 9.1 WorkRally 的真正价值不在“无限画布”四个字

公开 GitHub 说明 WorkRally 已将：

- 图像生成
- 视频生成
- 项目/剧集/场次/分镜 CRUD
- 资产库
- 媒资
- 无限画布
- 文件上传下载

封成可供 Agent 调用的工具集合。

2026-07 实测 UI 进一步说明：

- 新建项目有“从小说开始 / 从分镜开始”两个入口；
- AI 自动补基础信息、世界观、人物、场景；
- 智能助手旁有 Skill 商店；
- Skill 涵盖剧本、资产、脚本设计、导演运镜；
- Agent 会自动组合资产提取、多视图、三维资产等 Skill；
- 角色不是一张图，而是原画、多视图、九种表情、不同剧情状态；
- 脚本、资产、分镜全部可以平铺到画布；
- 卡片可拖拽排序、右键重生成。

### 9.2 WorkRally 的 Character Bible 是本项目资产系统必须达到的基线

LocalDramaStudio 当前 `story_assets` 只有：

- `kind`
- `code`
- `name`
- `description`
- 一个 `canonical_media_version_id`
- `extra_json`

这远远不够。

目标角色资产至少：

- 主参考
- FRONT / LEFT / RIGHT / BACK
- FULL_BODY / CLOSEUP
- EXPRESSION_GRID
- ACTION / POSE
- OUTFIT
- 角色状态：初始、受伤、战斗后、年龄变化等
- Voice Profile
- prompt DNA / negative constraints
- 出现集与状态区间

### 9.3 WorkSolo 的启示

WorkSolo 把 AI 短剧、互动影游、自由画布作为不同入口，说明“**用户目标先于工具形态**”。

LocalDramaStudio 首页也不应让用户选择技术模块，而应选择：

- 我有小说/剧本，自动生成
- 我只想做这一集
- 我已经有分镜，直接生产
- 我要自由试验素材

---

## 10. Seko 3.0

2026-07 官方公开的关键逻辑：

`Agent（生产自动化） + Canvas（资产体系） + Skill（专业经验）` 三者打通。

这对 LocalDramaStudio 的意义非常直接：

- 现有 `automation_workflows` 对应 Agent 编排基础；
- 现有 `ProductionCanvasPanel` 只提供了早期技术验证证据，不再作为画布基础；新 Visual Lab 使用独立文档、typed nodes/edges 与语义生成契约，详见 `04_画布与_ComfyUI_整体重构设计开发方案.md`；
- 现有 Profile / Workflow / Prompt Template 可演化为“能力 + Recipe/Skill”；
- 缺的是产品层统一，而不是从零搭底层。

建议 P2 新增“导演 Recipe/Skill”：

- 正反打对话
- 情绪递进近景
- 一镜到底武打
- 快节奏爽剧
- 古风静态漫剧
- 解说漫
- 竖屏真人

Skill 不是一段 Prompt，而是**结构化规则 + 模型能力要求 + 镜头计划 + 参数 preset + QC policy**。

---

## 11. LibTV

LibTV 的定位值得学习：**Creator 和 Agent 双入口，但共用同一个生产内核。**

官方资料明确：

- 无限画布；
- 图像/视频/音频全链路；
- 100+ AI 导演 Skill；
- Agent 可调用平台的生成和编辑能力。

对本项目的最大启示：

不要做两套系统：

- “人工页面”一套数据库；
- “自动化 Agent”再写一套脚本。

正确方式是：人类按钮与 Agent 调用**同一 Application Command / API**。区别只在谁触发和是否经过 HITL。

---

## 12. 火山剧创 Dramart

官方产品页明确提供：

- Agent 模式：导入剧本，自动提取角色/场景，拆全剧分镜；
- 人工模式：不用上传剧本，直接创建项目，自由组装资产与创作。

这正是 LocalDramaStudio 应该采用的“双轨模式”。

目标定义：

### 极速 Agent 模式

适合：批量生产、小说改编、小团队。

系统自动推进：

`导入 → 故事理解 → 资产提取 → 资产生成 → 分镜 → 首帧/参考图 → 视频 → QC → 声音 → 时间线 → 待审`

用户只看异常和关键审核点。

### 专业导演模式

适合：精品剧、关键集、问题镜头。

用户可以逐镜控制：

`资产 → 构图 → 运镜 → 表演 → 首尾帧 → 模型 → Prompt → Candidates → 选择 → 审核`

两者共享同一数据，不需要“从 Agent 项目复制到人工项目”。

---

## 13. 阿里万镜一刻

2026-05 公开信息给出三种创作形态：

1. 故事板模式：面向剧情/短漫剧，剧本 → 分镜 → 运镜 → 镜头组连续画面。
2. 无限画布：自由创作，可把工作流存为团队模板。
3. Agent：自然语言，多 Agent 协作成片。

这个三模式结构比“所有功能放在左导航”更符合用户意图。

LocalDramaStudio 建议映射：

- `导演台` = 故事板 / 专业生产模式
- `高级画布` = 自由模式
- `自动制片` = Agent 模式

而“模型与能力 / Jobs / Diagnostics”属于系统层，不属于三种创作模式。

---

## 14. 万兴剧厂

2026 的公开资料显示已经覆盖：

- 长篇文本/小说改编；
- 角色、场景、道具；
- 智能分镜；
- 无限画布 + Agent；
- 后期剪辑；
- 多模型接入；
- 团队协作。

4 月实测曾暴露一个非常重要的问题：某次接近 1 分钟的内容仅拆成 3 个大分镜，造成单镜头人物动线复杂，视频更容易崩。

**本项目必须防止“AI 一次拆完就不可质疑”。**

分镜规划需要：

- 每场预估时长；
- 每镜推荐时长；
- 动作复杂度；
- 人物数量；
- 是否应拆镜；
- 轴线风险；
- 连续性风险；
- 用户可“拆成 2 镜 / 合并 / 重规划这一段”，而不是只能重跑整集。

---

## 15. DramaBuddy

2026-07 的公开资料已经给出：

- Skill 技能库；
- 无限画布；
- 可从剧本、分镜、角色任意环节开启；
- 全景导演台；
- 故事板；
- 动捕视频参考；
- 全剧主体提取；
- 多状态智能匹配。

这印证了一个方向：**“角色状态”必须是资产域的一等公民。**

例如：

`角色：沈池`

- S0 初登场 / 白色长袍
- S1 战后 / 袖口破损 / 轻伤
- S2 黑化 / 黑色战甲
- S3 十年后 / 年龄变化

镜头绑定的不应该永远只是 `CHAR_SHENCHI`，而应该可以绑定 `CHAR_SHENCHI @ STATE_S2`。

---

## 16. 掌阅泡漫 PopoMint

公开资料中特别值得吸收的是：

- 九宫格；
- 三视图；
- 远 / 中 / 近景；
- 剧本—角色—场景—分镜—视频流水线。

这些能力应直接进入“资产圣经”的快捷动作区，而不是单独做一个工具页面。

---

## 17. 巨日禄

巨日禄更偏专业短剧/漫剧团队生产。公开官网强调：

- 多人协同；
- 角色/场景/物品一致；
- AI 短剧工业生产。

公开实测 4 月版本对新手并不友好，曾更依赖已有剧本和资产；这也说明本项目需要同时服务两类人：

- “零资产启动”用户；
- “我已经有角色/场景标准资产，请不要替我重做”的专业团队。

导入向导必须提供：

- 自动提取并生成资产；
- 只提取，不生成；
- 使用已有资产库匹配；
- 手动绑定。

---

## 18. LumenX：开源架构参照

Alibaba LumenX GitHub 的公开 README 将产品拆成：

- Studio：Pipeline-first，剧本 → 分镜 → 资产 → 视频 → 合成 → 导出；
- Playground：不需要剧本上下文的单独图片/视频生成台。

并明确支持：

- 角色三视图；
- 场景参考；
- I2V / R2V；
- 批量抽卡；
- 多种视频模型；
- TTS；
- 时间线 + FFmpeg 合成。

这证明我们把 LocalDramaStudio 的“高级实验生成”从主线拆成 Playground/Lab 是合理的：**生产流和探索流不是一个 UI。**

---

# 第四篇：竞品共同规律 —— 真正需要抄的是这些

## 19. 2026 行业已经形成的九个共识

### 19.1 “一键生成”是入口，不是完整产品

最终仍然需要：

- 资产修正
- 单镜重抽
- 镜头拆分
- 人物状态
- 连贯性修复
- 音频调整
- 局部重合成

所以系统必须支持**异常驱动生产（exception-driven production）**。

### 19.2 角色一致性正在从“Prompt 一致”升级为“资产体系一致”

三视图、九宫格、状态、服装、参考集合已经越来越常见。

### 19.3 生成随机性不能只靠多抽几次

纳逗/天工的 3D/站位、多角度等功能代表行业向“预演”发展。

### 19.4 “上一镜最后一帧 → 下一镜第一帧”已经成为显式创作概念

LocalDramaStudio 底层已经有比大多数 UI 更完整的数据结构，应该将其产品化。

### 19.5 长篇项目必须围绕“集”生产，而不是单个媒体任务

集级状态、缺失镜头、剩余生成、合成本集、全剧资产是核心。

### 19.6 无限画布适合专家，不适合所有人

画布优秀，但完全空白的画布会增加新手认知负担。最佳实践正在变成：

`线性/Agent 主流程 + 无限画布专家模式`。

### 19.7 Agent 需要 Skill / Recipe，不应该每次从 Prompt 重新发明工作流

专业经验要可保存、可版本化、可复用。

### 19.8 模型选择必须贴近任务

用户想做的是“视频首尾帧”“角色三视图”“TTS”，不是“选择 provider_version_id”。

### 19.9 失败必须原地可处理

用户不应该为了修当前镜头跳到“任务中心 → Job → artifact → review → 回镜头”。

系统运维视图是后台，创作视图必须在原地显示：

- 生成中
- 失败
- 重试运行错误
- 重抽创意结果
- 查看详细日志

---

# 第五篇：当前 LocalDramaStudio UI/UX 代码审计

## 20. 当前产品已经有什么 —— 不要低估现有基础

源码已经具备：

- React 19 + TypeScript + TanStack Query；
- FastAPI；
- SQLite/WAL；
- 本地模型 / Profile / Workflow；
- ComfyUI；
- 项目 / 分集 / 场 / 镜头；
- Script Import；
- AI Breakdown Draft；
- 分镜批处理；
- DirectorShotEditor；
- 生成候选、Generation Variant；
- 审核与 Formal Selection；
- 角色/场景/道具/服装资产；
- Character Voice Binding；
- TTS / Audio；
- Subtitle / Timeline；
- 首尾帧 Frame Anchor；
- Shot Transition Constraint；
- stale propagation；
- 持久 Job / worker / lease / progress；
- 自动化 Workflow；
- Delivery；
- Audit；
- 画布；
- 大量 API / Web 测试。

所以真正的任务不是“再开发一个竞品功能列表”，而是**重组心智、补齐资产和导演交互、收敛架构边界**。

## 21. 当前导航存在的问题

`App.tsx` 当前顶级 `View`：

`overview / projects / canvas / reviews / jobs / profiles / generation / diagnostics`

左侧导航按：

### 制片中心

- 概览
- 分集生产
- AI 生成工作台
- 审核收件箱
- 业务画布

### 资源与系统

- 模型与能力
- 任务与机器
- 诊断中心

问题在于：**这是开发模块分类，不是用户生产顺序。**

创作者实际问题是：

1. 剧本导进来了吗？
2. AI 理解得对不对？
3. 角色长什么样？
4. 这一集分成哪些场/镜？
5. 这一镜怎么拍？
6. 哪个 take 最好？
7. 前后接得上吗？
8. 声音完整吗？
9. 能合成吗？

现有导航需要用户自己把“分集生产 → AI生成 → 审核 → 回生成 → 时间线 → 交付”拼起来。

## 22. 当前“分集生产”是典型的功能仓库页

`App.tsx` 在 `projects` view 中连续堆叠：

- ProjectList
- EpisodeSceneRanges
- StoryboardBatchWorkbench
- CreativeLibrary
- StoryAssetLibraryPanel
- ScriptImportPanel
- AIDraftReviewPanel
- DialogueTTSPanel
- AudioTrackPanel
- SubtitleRevisionPanel
- TimelineRevisionPanel
- Contact Sheet
- Timeline Status
- Episode Review
- Timeline Export
- Delivery Workflow
- G8 readiness
- ProjectConfigurationSnapshot
- BrandKit

这不是一个工作台，是“所有插件都放在同一个长页面”。

继续往这里加功能会导致：

- 首屏永远看不到最重要任务；
- 组件之间缺少共享焦点；
- 滚动距离越来越长；
- 无法形成当前镜头上下文；
- Agent 开发者倾向继续“加一个 Panel”；
- 页面测试难聚焦用户路径。

**必须拆。**

## 23. 当前 Generation 页面的问题

当前 generation view 又依次堆叠：

- GenerationWorkbench
- WorkspaceAssetAuthorizationPanel
- PostProcessPanel
- DirectorShotEditor
- PromptTemplatePanel
- ContinuityPanel

核心导演编辑器反而在生成工作台之后。

而行业成熟界面已经证明：导演信息、预览、资产、首尾帧、候选应该围绕**当前镜头**同屏。

## 24. 当前 DirectorShotEditor：数据正确，交互太工程化

它已经有九类导演字段、CameraPlan、Profile resolve、保存 revision、freeze、Production Ready；这是很好的后端语义基础。

但 UI 主要是：

- select
- input
- textarea
- 英文枚举
- “按 Profile 裁决运镜能力”
- `NATIVE / PROMPT_FALLBACK`

这更像内部 QA 工具，不像导演台。

应该把技术语义翻译成创作语言：

- `PUSH_IN` → 推近
- `TRUCK` → 横移
- `PROMPT_FALLBACK` → “当前模型不支持原生运镜参数，将使用提示词近似执行”
- Profile → “视频模型 / 生成方案”

技术详情放进“高级”折叠区。

## 25. 当前故事资产库：最大功能差距之一

`StoryAssetLibraryPanel` 当前需要用户输入：

- code
- name
- description
- canonical 媒体版本 ID

并显示 96×54 缩略图。

“粘贴媒体版本 ID”是典型开发者交互，必须移除出普通用户路径。

创作者应该：

- 直接拖图/选图；
- 从候选里“设为主参考”；
- 一键生成三视图；
- 加入表情/服装/状态；
- 绑定声音；
- 看在哪些镜头使用；
- 更新资产后看到下游受影响镜头。

## 26. 当前视觉系统：方向基本正确，不要推倒

现有 Design System 的 Hybrid Professional Workstation 是合理的：

- 深色 persistent chrome；
- 暖白工作表面；
- 媒体/时间线深色；
- vermilion 创作主动作；
- semantic status color。

当前 Generation Workbench 截图也已经从早期“全深色技术 dashboard”改善为“暗色导航 + 暖色内容”。

真正要改的是：

- 减少 hero；
- 减少系统状态卡；
- 增大媒体工作区；
- 把页面从“面板堆叠”改成“工作台布局”；
- 把技术信息逐层下沉。

---

# 第六篇：目标产品信息架构

## 27. 一级导航重做

### 27.1 创作区

1. **项目总览**
2. **故事与剧本**
3. **资产圣经**
4. **分集规划**
5. **导演台**
6. **审核**
7. **声音与字幕**
8. **时间线与成片**
9. **交付**

### 27.2 工具区

10. **高级画布**
11. **素材实验室**

### 27.3 系统区（折叠，默认不占创作焦点）

12. **模型与能力**
13. **任务与机器**
14. **诊断与审计**

## 28. 首页四个“按目标开始”入口

不要直接显示十几个模块。

### 卡 1：导入小说 / 剧本

说明：自动识别剧情、人物、场景并生成第一版项目。

### 卡 2：从这一集开始

说明：已经有故事设定，直接创建分集与分镜。

### 卡 3：从已有分镜/资产开始

说明：保留已有角色、场景、Storyboard，不让 AI 重做。

### 卡 4：自由创作

说明：进入高级画布 / Playground。

下方再显示“最近项目”。

系统健康只在顶栏：

`● 本机正常 · 1 个任务运行中`

异常才变成：

`! GPU 不可用 · 查看诊断`

---

# 第七篇：双工作模式

## 29. 极速 Agent 模式

### 29.1 适合用户

- 网文改编
- 大量分集
- 小团队
- 先出第一版再修

### 29.2 用户看到的不是 50 个后台任务，而是 8 个生产阶段

1. 故事解析
2. 资产提取
3. 资产补全
4. 分集 / 分镜规划
5. 镜头画面
6. 视频
7. 声音 / 字幕
8. 合成 / QC

每个阶段显示：

- 已完成数量
- 失败数量
- 需要人工决定数量
- 预计剩余
- 展开后才看 Job 详情

### 29.3 Agent 运行规则

用户可以配置检查点：

- 每阶段自动继续
- 资产生成后停下确认
- 分镜生成后停下确认
- 视频生成前确认
- 只在异常时停

这是 HITL，而不是“一键到底后才发现 80 个镜头错了”。

---

## 30. 专业导演模式

核心原则：**一个镜头的所有相关工作尽量不离开同一个屏幕。**

最终导演台布局固定为五区。

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ 项目 / 第12集 / 场03 / 镜头 S032    [自动制片状态] [模型▼] [生成候选] [···] │
├───────────────┬───────────────────────────────────────┬──────────────────────┤
│ 左：镜头导航  │              中：媒体舞台             │ 右：导演 Inspector  │
│               │                                       │                      │
│ 场03          │         当前首帧/视频/候选对比        │ 画面与镜头           │
│  S030 ✓       │                                       │ 角色与场景           │
│  S031 !       │     [A] [B] [C] [D] / Before-After   │ 生成设置              │
│> S032 ⟳       │                                       │ 连贯性                │
│  S033 ○       │       查看原文 / 安全框 / 网格       │ 声音                  │
│               │                                       │ 高级                  │
├───────────────┴───────────────────────────────────────┴──────────────────────┤
│ 下：Takes + Episode Filmstrip / Audio / Frame Bridge                        │
│ S030  S031   [S032:A B C D]  S033  S034 …                                  │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 30.1 顶部 Context Bar

必须始终可见：

- 项目
- 分集
- 场
- 镜头
- 当前镜头状态
- 当前默认视频模型
- 生成候选
- 集级动作菜单

不要显示四个大型 API/SQLite 状态卡。

### 30.2 左侧 Shot Navigator：260–300px

每个镜头卡显示：

- 镜头编号
- 2:3 / 16:9 小缩略图
- 2 行摘要
- 时长
- 状态：未规划 / 已导演 / 生成中 / 有候选 / 已选 / 已批 / 失败 / stale
- blocker 数量

支持：

- 按场折叠
- 筛选“只看失败 / 只看未生成 / 只看 stale / 只看待审”
- 多选
- 批量改模型
- 批量重生成
- 批量标记导演参数
- 拖动排序（必须写入正式 order command，不是只改 UI）

### 30.3 中央 Media Stage

默认占最大空间。

Tab：

- 当前结果
- 候选 Takes
- 对比
- 首尾帧
- 站位预演（P1）

提供：

- Fit / 100%
- 安全框
- 九宫格
- 前后帧 flicker
- 2-up / 4-up 比较
- 快速放大脸部
- 选择为当前
- 提交审核

### 30.4 “查看原文”不能另开大页面

点击后从中央或右侧抽屉展开：

- 小说原文范围
- 剧本场次
- 当前分镜来源句
- 上下文前后 1–2 段

并高亮 AI 当前镜头覆盖的源文本范围。

### 30.5 右侧 Inspector：360–420px

Tab 不超过 6 个：

#### A. 画面与镜头

- 景别：可视化按钮，非英文下拉
- 构图：九宫格点位、中心/三分、过肩、双人等 preset
- 相机角度：平视/俯/仰/侧/过肩
- 运镜：静止/推/拉/摇/移/环绕
- 方向
- 强度
- 时长
- 表演/动作
- 情绪
- 转场意图

#### B. 角色与场景

- 当前绑定角色 chip
- 角色状态
- 服装状态
- 场景
- 道具
- 添加/替换直接弹资产 picker，绝不让用户粘 ID

#### C. 生成

- 图像模型
- 视频模型
- 模式：T2V / I2V / First-Last / Reference
- 候选数：1 / 2 / 4
- 分辨率 / 时长
- Seed：Auto / Lock
- Prompt
- Negative
- 预估耗时 / 资源

#### D. 连贯性

- 上一镜最后帧
- 本镜第一帧
- 本镜最后帧
- 下一镜第一帧
- 继承状态
- 当前角色/服装/道具 continuity diff
- 轴线提示

#### E. 声音

- 台词
- 说话角色
- voice
- 语速/情绪
- SFX
- BGM cue

#### F. 高级

- Profile version
- capability contract
- native/fallback
- raw prompt / raw settings
- revision details

普通创作者无需看技术枚举。

### 30.6 底部 Takes & Timeline：180–240px，可折叠

两种层级同时存在：

- 近层：当前镜头的 A/B/C/D 候选；
- 远层：整集镜头 filmstrip。

这样用户可以：

- 横向扫整集；
- 对当前镜头反复重抽；
- 不离开导演台。

---

# 第八篇：重抽 / 候选 / 审核的正确交互

## 31. “重试”和“重抽”必须完全分离

### 重试 Retry

含义：任务执行失败，输入和创作意图不变。

例如：

- Comfy 崩溃
- 磁盘暂不可写
- runtime timeout

归属于 Job 运维语义。

### 重抽 Resample

含义：创作结果不满意，需要新 Variant。

必须新增 GenerationVariant，永不覆盖。

## 32. 重抽按钮点击后的快速原因

默认不弹大表单，而出现小菜单：

- 构图不对
- 人物不一致
- 人物站位错误
- 动作失败
- 运镜失败
- 首尾不连贯
- 风格不对
- 口型/对白问题
- 其他

然后二级选择：

- 保持设置，仅换随机性
- 自动修正 Prompt 后生成
- 我先手动调整

原因写入 `branch_reason`，将来可以统计“哪个模型最容易在哪类镜头失败”。

## 33. Candidate 生命周期

推荐明确五个状态：

1. Generated：生成完成
2. Shortlisted：进入候选
3. Selected：当前镜头采用
4. Approved：人工批准
5. Delivery-used：已进入某次可追溯交付

任何 UI 都不允许用一个“绿色勾”混淆这些状态。

---

# 第九篇：首尾帧不是开关，而是 Frame Bridge

## 34. 新 UI 概念：镜头桥 Frame Bridge

在两个镜头之间显示：

```text
S032 ──[ 尾帧 #A · 已锁定 ]──▶ [ 继承 ]──▶[ 首帧 #A ]── S033
                         continuity: OK
```

状态：

- AUTO_INHERITED：自动继承上一镜尾帧
- EXPLICIT：用户显式指定
- GENERATED：为过渡单独生成
- STALE：上游结果变化
- CONFLICT：人物/场景/帧锚不兼容
- MISSING：缺失

## 35. 首帧来源菜单

- 继承上一镜尾帧
- 从当前候选取帧
- 从媒体库选择图片
- 从视频选时间点
- AI 生成首帧

## 36. 尾帧来源

- 当前视频实际尾帧
- 指定时间点
- 生成目标尾帧
- 选择参考图

## 37. 锁定语义

“锁定首帧/尾帧”之后：

- 重抽本镜时默认继续使用锁定边界；
- 只有用户显式解除才能换；
- 上游候选被替换时标记 stale，不静默换帧。

这正好对应当前数据库 `frame_anchors`、`shot_transition_constraints`、`is_stale`、`boundary_revision`，不需要另起一套事实表。

---

# 第十篇：资产圣经完整 UI

## 38. 资产页整体布局

```text
┌──────────────┬───────────────────────────────┬──────────────────────┐
│ 资产列表      │ 主视觉 / 多视图 / 状态画廊    │ 结构化信息 / 使用情况 │
│ 角色          │                               │ DNA / Voice / 状态    │
│ 场景          │                               │ 出现集 / 引用镜头      │
│ 道具          │                               │ 一致性风险             │
│ 服装          │                               │                       │
└──────────────┴───────────────────────────────┴──────────────────────┘
```

## 39. 角色页快捷动作

- 生成三视图
- 生成表情九宫格
- 生成近景细节
- 创建服装状态
- 创建剧情状态
- 绑定声音
- 从图片反推 DNA
- 替换主参考
- 比较参考版本

## 40. Asset Reference 类型

至少：

`HERO / FRONT / LEFT / RIGHT / BACK / THREE_VIEW / FULL_BODY / HALF_BODY / CLOSEUP / EXPRESSION_GRID / POSE / ACTION / OUTFIT / DETAIL / OTHER`

每张参考图可设置：

- locked
- priority
- yaw / pitch（可选）
- 适用状态
- 来源 MediaVersion
- 审核状态

## 41. Character State

不要复制整张角色资产。使用状态层：

- 状态名称
- 年龄/时间段
- 服装
- 发型
- 伤势
- 携带道具
- prompt delta
- 参考图集合
- 生效分集区间

镜头绑定：`character_asset_id + state_id`。

## 42. 场景资产

场景也需要：

- HERO
- 远 / 中 / 近
- 方向 0° / 90° / 180° / 270°
- 日 / 夜 / 黄昏
- 灯光状态
- 720° panorama（未来可选）
- 固定道具
- 可用机位

---

# 第十一篇：模型选择 UX

## 43. 用户先选“能力”，再选模型

禁止在普通 UI 先展示 provider / runtime / manifest。

例如视频模型下拉应写：

```text
视频生成
● 自动推荐：H3 Turbo — 快，适合批量镜头
  H3 Base — 慢，质量优先
  Seedance … — 如未来接入
  Kling …
[高级：查看 Profile 与参数]
```

每项附：

- 能力标签：首尾帧 / 参考图 / 原生运镜 / 声音
- 最大时长
- 分辨率
- 预计耗时
- 本地资源
- 最近成功率（有样本后）

## 44. Override 层级

模型选择必须具有可解释继承：

`项目默认 → 分集覆盖 → 镜头覆盖 → 本次动作临时覆盖`

UI 显示：

`继承自项目：H3 Turbo  [为本镜改用…]`

绝不能静默 fallback。

## 45. 自动推荐

Auto 只做推荐，不替用户偷偷换模型。

推荐解释示例：

> 推荐 H3 Base：本镜使用首尾帧约束且包含双人运动；当前 Turbo Profile 不支持该 CameraPlan 原生参数。

---

# 第十二篇：整集 / 全剧自动生产

## 46. 集级 Cockpit

每一集显示：

- 镜头总数
- 已导演
- 有候选
- 已选
- 已批准
- failed
- stale
- missing frame bridge
- 音频缺失
- QC 问题

主动作根据阶段智能变化：

- `生成剩余镜头`
- `修复 6 个失败镜头`
- `处理 3 个连贯性问题`
- `合成本集`
- `送审`

而不是永远写“运行 G8 readiness”。

## 47. 整集一键生成可配置策略

### 草稿模式

- 候选 1
- 快速模型
- 基础 QC
- 不自动重抽或最多 1 次

### 平衡模式

- 关键镜头候选 2
- 自动 QC
- 失败最多重抽 1 次
- 每场首镜人工检查点

### 精品模式

- 关键镜头 4 候选
- 强 continuity
- 更严格 QC
- 复杂镜头先预演
- 多个人工 checkpoint

用户还可以保存为 Recipe。

---

# 第十三篇：自动 QC

## 48. QC 分类

### 文件级

- 文件可解码
- 黑帧/空帧
- 分辨率
- 宽高比
- 时长
- FPS
- 音频轨存在

### 角色一致性

- 角色脸部相似度（有可用模型时）
- 发型/服装大偏差
- 人数
- 主体缺失

### 连贯性

- 上镜尾帧 vs 下镜首帧
- 场景突变
- 角色位置大跳
- 道具状态
- 轴线风险

### 叙事/导演约束

- 景别是否大致匹配
- 动作是否覆盖
- 运镜是否明显违背

### 音频

- LUFS
- true peak
- clipping
- 空音轨
- TTS 覆盖
- 音画时长错位

机器检查永远是证据，不自动变成 Human Approved。

## 49. 自动重抽策略

自动重抽必须：

- 有最大次数；
- 有最大资源/时间预算；
- 每次创建新 variant；
- 写明触发原因；
- 如果连续两次同类失败，转人工，不无限循环。

---

# 第十四篇：声音、字幕和时间线 UX

## 50. 声音从“状态面板”迁移到生产流程

Character Bible 中绑定默认 voice。

当前镜头 Inspector 中：

- 角色
- 台词
- voice（继承/覆盖）
- emotion
- rate
- preview
- regenerate

整集 Audio 页面处理：

- 批量 TTS
- BGM
- SFX
- waveform
- ducking
- 缺失台词

## 51. 时间线

当前系统已有不可变 Timeline Revision，这是优势。

新 UI 做成：

- V1 视频镜头
- A1 Dialogue
- A2 SFX
- A3 BGM
- Subtitle

每次“一键合成本集”生成一个新的 timeline revision / render evidence，不覆盖旧版本。

导演台底部只是轻量 filmstrip；真正混音/剪辑进入完整时间线页。

---

# 第十五篇：高级画布定位

## 52. 画布不要删除，但重新定位

新名字：**高级画布 / Visual Lab**。

适用：

- 角色多视图生成链
- 场景不同角度探索
- 特殊镜头实验
- 参考图组合
- Prompt / 模型对照实验
- Skill/Recipe 设计

不适用：

- 新手首次导入剧本
- 每一集日常逐镜生产
- 全部审核工作

## 53. 画布节点建议

- Text
- StoryAsset
- Media
- Shot
- Prompt
- Generation
- Transform/Edit
- Video
- Audio
- Timeline/Sequence
- Compare
- Output

业务依赖与画布视觉连线需要区分；不能让拖线等同于随意更改正式生产关系。

---

# 第十六篇：视觉与组件规范调整

## 54. 保留现有 Hybrid Professional Workstation

继续保留现有基础 token：

- Chrome `#1F2625`
- Canvas `#F4F1EB`
- Surface `#FFFDF8`
- Creative `#E9633B`
- Selected teal
- Approved green
- Danger red

## 55. 新增语义 token

建议新增：

- `--media-stage: #111715`
- `--timeline: #0E1412`
- `--stale: #8A6A2E`
- `--blocked: #9B4B3E`
- `--draft: #667085`
- `--reference: #476B8E`

具体实现仍要跑 AA 对比度检查，不以此表作为最终颜色通过证据。

## 56. 页面不再统一 Hero

- 项目总览可以有 24–32px title。
- Story / Asset 可以 24px。
- Director Desk **不要大 hero**，标题压到 48–56px context bar 内。
- 媒体舞台获得最大高度。

## 57. 系统状态条下沉

创作页不再常驻：

- API HEALTHY 卡
- LOCAL_ONLY 卡
- SQLite WAL 卡
- Profile 数卡

改成：

`● 本机正常` 点击展开 popover：API / DB / GPU / queue / disk。

出问题时才在上下文区域显示 error banner。

---

# 第十七篇：关键交互细则

## 58. 键盘

在不聚焦输入框时：

- J / K：上一镜 / 下一镜
- Space：播放/暂停
- G：打开生成面板
- R：重抽菜单
- C：候选对比
- F：首尾帧面板
- A：资产 picker
- `[` / `]`：上一/下一 candidate
- Enter：打开当前镜头详情
- Esc：关闭 drawer / popover

禁止使用会与文字编辑冲突的全局 shortcut。

## 59. 拖拽

支持：

- 资产拖到镜头 → 建立语义绑定
- 媒体拖到 First/Last Frame 槽 → 建立 frame binding
- candidate 拖到 selected 槽 → 等同显式 selection action（需确认或 Undo）
- Shot 在镜头树内排序 → 调正式 reorder command

## 60. Undo

不能伪造数据库级“撤销”。

可以 Undo 的 UI 操作：

- 本地未保存表单
- 排序后立即发一个逆向 command
- 选择 winner 后通过 superseding selection 撤销

历史版本不删除。

---

# 第十八篇：全状态设计

每一个异步业务组件必须实现：

1. First loading
2. Refreshing with old data retained
3. Empty
4. Running
5. Paused
6. Blocked
7. Failed
8. Partial success
9. Success
10. Stale
11. Offline/local runtime unavailable
12. Permission/authorization missing（未来）

### 60.1 错误信息格式

不要只显示 `Error: 500`。

格式：

> 视频生成失败：本机 ComfyUI 连接中断。  
> 已保留镜头与参数，不会丢失。  
> [重试任务] [打开任务详情] [诊断 ComfyUI]

### 60.2 Blocker 要用创作者语言

坏：

`G6 readiness failed: FRAME_ANCHOR_REQUIRED`

好：

`下一镜要求继承尾帧，但当前镜头还没有已选视频。先选择一个视频候选。`

高级详情再显示内部 code。

---

# 第十九篇：Figma / 视觉交付策略

## 61. 当前 Figma 状态

源码中的 `design-system/localdramastudio/figma-state.json` 显示：

- 已有 Foundations 页面；
- Components / Generation Workbench 尚未完成；
- 当前 blocker 是 Starter MCP tool call limit。

鉴于用户明确说 Figma 额度有限，本轮**不应该继续消耗额度去画大量静态稿**。

### 61.1 正确策略

先让开发 Agent 按本文：

- tokens
- layout
- component anatomy
- route structure
- states

做一个代码可运行 Director Desk prototype。

随后只在 Figma 做 5 张高价值关键帧：

1. Project Start
2. Story Planning
3. Character Bible
4. Director Desk
5. Episode Cockpit / Timeline

不需要把 50 个后台配置页全画一遍。

## 62. Figma 最终组件集合

必须建：

- AppShell
- GlobalNav
- ContextBar
- StatusDot/StatusBadge
- EpisodeTree
- ShotRow
- MediaStage
- CandidateTile
- AssetChip
- AssetPicker
- CharacterReferenceCard
- FrameBridge
- CameraControl
- ModelPicker
- JobInlineStatus
- InspectorTabs
- TimelineClip
- Drawer
- CommandPalette
- ErrorBanner
- EmptyState

---

# 第二十篇：产品验收标准

## 63. 五个必须能顺畅完成的用户故事

### U1：20 万字以内剧本导入后，我不用理解系统技术结构就能得到第一版项目

成功标准：

- 入口明确；
- 解析在后台运行；
- 可离开页面；
- 可看到阶段进度；
- 完成后进入 Story Review；
- 不会自动覆盖用户已有事实。

### U2：我发现第 12 集第 18 镜人物站错了，我能 30 秒内定位、重抽、比较并选中新版本

全过程不应跳过 2 个以上顶级页面。

### U3：我可以固定角色三视图和第 18 镜首帧，重抽视频时不丢这些约束

UI 必须显示锁定状态和来源。

### U4：我可以对整集点“生成剩余镜头”，但失败镜头不会阻止我继续修别的镜头

### U5：我可以一键合成本集，但系统明确告诉我哪些镜头仅“已选”而尚未“批准”

交付流程不能混淆状态。

## 64. 可量化 UX 目标

- 从进入项目到打开当前问题镜头：≤ 3 次主导航动作。
- 从当前镜头重抽到看到新 candidate：≤ 2 次创作点击（不计必需确认）。
- 给镜头绑定已有角色资产：≤ 3 次点击或一次拖拽。
- 设为首帧：≤ 2 次动作。
- 用户不进入 Diagnostics 的情况下，可以解决至少 90% 的创作类 blocker。
- 任务失败时，当前工作内容零丢失。
- 所有 destructive / selection / approval 行为可追溯。

---

# 第二十一篇：本项目应该“明确不做”的错误方向

1. 不要继续把十几个 Panel 往 `App.tsx` 追加。
2. 不要把所有功能都塞进无限画布。
3. 不要为 UI 简单而删掉不可变 revision / audit。
4. 不要为了“Agent 一键”建立第二套业务事实。
5. 不要把 Provider/Model ID 暴露给普通创作者。
6. 不要把首尾帧只做成一个 boolean。
7. 不要让重抽覆盖旧图/旧视频。
8. 不要让机器 QC 自动等同于人工批准。
9. 不要 P0 直接重写成完整 3D 引擎。
10. 不要用大 Hero / 系统健康卡占导演台首屏。
11. 不要要求用户粘贴 `media_version_id`。
12. 不要把所有错误都推给 Jobs/Diagnostics 页面。
13. 不要每接一个新模型就新增一套专用页面。
14. 不要同时维护“老工作台”和“新工作台”两套长期分叉业务逻辑。

---

# 第二十二篇：执行优先级

## P0：必须先做

- 新 IA / 路由框架
- Director Desk 五区布局
- Shot Navigator
- 当前镜头媒体舞台
- Inspector 重组
- Candidate / reroll 同屏
- Frame Bridge UI
- Asset Picker（不再粘 ID）
- Character Bible 基础多参考
- 集级生成剩余/失败管理
- 创作页移除系统状态卡

## P1：强烈建议

- Character State / Scene State
- 三视图 / 九宫格生成动作
- 模型 Override 层级
- 自动 QC + bounded reroll
- 2D Staging Board
- 完整音频/时间线 UX
- Story Review / Split-Merge Shot

## P2：形成差异化

- Recipe / Skill
- Agent 自动制片策略
- Advanced Canvas 与 Skill 互通
- 统计重抽原因与模型质量
- 720° / 多角度工具
- 3D Previz（需求验证后）

---

# 附录 A：公开资料来源

> 说明：以下链接用于开发阶段复核竞品事实与公开 UI。对产品截图的判断来自对应公开文章内真实截图；文档本身不嵌入大图，避免上下文和仓库膨胀。

### S01 字节“漫剧创作工具”

- AITNT 转载《读佳》，2026-08-18：`https://m.aitntnews.com/newDetail.html?newId=28337`
- 关键内容：20 万字、PDF/DOC、团队 30 人、项目规划、剧本策划、资产设计、逐集分镜、叙事模式、合成本集、公开 UI 截图。

### S02 五款短剧 Agent 实测

- 钛媒体，2026-04-17：`https://www.tmtpost.com/7956912.html`
- 关键内容：小云雀 / 纳逗 Pro / 巨日禄 / 万兴剧厂 / 天工的真实使用流程与失败案例。

### S03 小云雀

- 科技日报，2026-03-20：`https://www.stdaily.com/web/gdxw/2026-03/20/content_488925.html`

### S04 火山剧创 Dramart

- 火山引擎官方：`https://www.volcengine.com/product/dramart`

### S05 纳逗 Pro 2026-06 升级

- 央广网，2026-06-24：`https://tech.cnr.cn/techgd/20260624/t20260624_527674992.shtml`

### S06 天工短剧工作台 2026-07

- 36氪 / 昆仑万维，2026-07-16：`https://www.36kr.com/p/3897777804002950`

### S07 Seko 3.0

- 商汤官方，2026-07-29：`https://www.sensetime.com/cn/news/seko-3-0-ai-1`
- 产品：`https://seko.sensetime.com/`

### S08 WorkRally

- Tencent GitHub：`https://github.com/Tencent/workrally`
- 2026-07 实测：`https://m.sohu.com/a/1048685325_121948416`

### S09 WorkSolo

- 36氪欧洲站 / 读佳，2026-07-30：`https://eu.36kr.com/zh/p/3917516141876864`

### S10 LibTV

- 官方：`https://www.liblib.tv/wappro?sourceid=040004`
- Skill GitHub：`https://github.com/libtv-labs/libtv-skills`

### S11 DramaBuddy

- 官方：`https://aicomic.yuewen.com/`
- 科技日报，2026-07-15：`https://www.stdaily.com/web/gdxw/2026-07/15/content_547831.html`

### S12 PopoMint

- 新浪财经，2026-02-04：`https://finance.sina.com.cn/stock/relnews/cn/2026-02-04/doc-inhkseni3587988.shtml`
- 官方现网（本轮直接截图）：`https://www.popreels.cn/`

### S13 万兴剧厂

- 万兴科技：`https://www.wondershare.cn/new/details/id/1183.html`
- 科技日报：`https://www.stdaily.com/web/gdxw/2026-01/31/content_468665.html`

### S14 万镜一刻

- 新浪财经 / 上海证券报，2026-05-21：`https://finance.sina.com.cn/roll/2026-05-21/doc-inhyshrf6271492.shtml`
- 公开介绍：`https://finance.sina.com.cn/wm/2026-05-21/doc-inhysaip8236597.shtml`

### S15 巨日禄

- 官网：`https://video.jurilu.com/`
- 新华网，2025-11：`https://www.xinhuanet.com/digital/20251107/e3450f82993941f882c833b63c26e856/c.html`

### S16 LumenX

- Alibaba GitHub：`https://github.com/alibaba/lumenx`

### S17 快手“造梦专家 2.0”行业确认

- 人民日报海外版，2026-03-23：`https://paper.people.com.cn/rmrbhwb/pc/content/202603/23/content_30146543.html`

### S18 RoboNeo

- 36氪快讯，2026-07-24：`https://36kr.com/newsflashes/3909538588742787`

### S19 MiniMax Design / H3

- MiniMax Design 官方：`https://hub.minimaxi.com/`
- H3 官方能力页：`https://design.minimaxi.com/h3`
- 官方手册：`https://my.feishu.cn/wiki/VEoVwpfCKiTHvHkAGQ7cQJxCncf`
- 2026-08-20 独立实测镜像：`https://www.sina.cn/news/detail/5333942988442749.html`

### S20 本轮逐图证据索引

- 全产品来源记录：`docs/research/assets/competitors/sources.json`
- 证据矩阵：`docs/research/notes/competitor_evidence_matrix.csv`
- 字节 / MiniMax 专项逐图边界：`docs/research/notes/bytedance_minimax.txt`

---

# 附录 B：开发 Agent 阅读顺序

开发 Agent **不要只读某一个章节**。

必须依次：

1. 本文 0–5：先理解为什么重构；
2. 20–26：理解当前项目真实问题；
3. 27–60：按新信息架构和导演台实现；
4. 再读《02_架构与前后端重构规格》确定代码结构和数据模型；
5. 最后读《03_实施迁移测试验收手册》按阶段开发，禁止自行改顺序。

---

# 2026-08-21 规范增补：实机证据、MiniMax 导演台与当前 V2 最终改造稿

> 本增补是当前实施的规范性入口。前文的竞品原理仍有效；前文将旧页面称为“当前实现”的段落只作历史背景。开发时必须同时遵守本文顶部 `0A`、本增补、`02` 的当前架构控制块和 `03` 的当前 PR 门禁。

## A. 这次研究究竟看了什么

### A.1 取证方式

本轮没有把搜索结果卡片或宣传文案直接当成 UI：

1. 先保存原图与原页面；能取得真实操作截图时优先真实截图。
2. 把官方真实 UI、媒体实机截图、官方营销合成图、生成样片分层；营销图不能证明隐藏菜单或交互完成度。
3. 对长页面按 720px 高切片，只看缩略联系表做全局结构判断，发现问题后再回到原图。
4. 每项判断写明“可确认 / 报道声称 / 未证实”；公开图没出现某能力，只能写“未见证据”。
5. 竞品总目录的 `image_count` 是证据库存，不是受欢迎程度，也不能代替证据等级。

证据索引：

- `docs/research/assets/competitors/evidence-index.json`
- `docs/research/assets/competitors/competitor-overview-contact-sheet.jpg`
- 各产品目录下 `evidence-manifest.json`、`contact-sheet-evidence-*.jpg`、`sources.json`（若有）
- 字节逐图结论：`docs/research/assets/competitors/bytedance/sources.json`
- MiniMax 逐图结论：`docs/research/assets/competitors/minimax/sources.json`

### A.2 强制产品覆盖与证据边界

| 产品 | 可用公开界面证据 | 可确认的界面骨架 | 本项目学习点 | 必须保留的证据边界 |
|---|---|---|---|---|
| 字节“漫剧创作工具” | 5 张同源媒体实机图，B1 | 窄左栏；上传首屏；项目规划；资产三栏；单集分镜 + 底部镜头条 | 垂直剧集对象、唯一下一步、按集补齐/合成/导出 | 尚无官方公开页；豆包具体模型、自选模型、三视图、完整音轨均未证实 |
| MiniMax Design / 导演台 | 官方页、官方手册、官方账号与独立实测，A1/A2/B1 分层 | 左项目树；中无限画布/3D/NLE；右 Agent；3D/NLE 才出现底部时间线 | Agent 审批、节点溯源、Skill、本地资产、3D 预演、NLE、节点级模型 | 3D 导演台是空间/运镜预演，不是多集漫剧管理；H3 能力不等于每个节点均有同样 UI |
| 小云雀 | 官网/媒体流程和多张 UI/样片混合证据 | 极简白色步骤页；角色/素材列表；中脚本；右竖屏预览；底镜头条 | 新手三步入口、失败原位提示、线性镜头生产 | 样片帧不证明控件；公开版本快速变化 |
| 纳逗 Pro | 媒体实机与发布材料，多张深色工作台图 | 左阶段/资产；中画布或预览；右参数；底镜头/多轨；弹出模型与生成参数 | 多机位/运动/站位控制、候选阵列、模型就近选择、3D 预演 | 不把单次演示质量当稳定产能；版本间 UI 有变化 |
| 天工短剧工作台 | 2 张可读实机图 + 官方/协会说明 | 资产抽取卡片；深色生成弹层；模型、分辨率、时长就地选择 | 资产抽取确认、生成前的能力/规格选择 | 公开完整导航、审核、合成和恢复流程不足 |
| WorkRally | 官方 GitHub/公开演示图 | 脚本与计划面板、角色多视图/表情、Skill 开关、分镜输出 | Character Bible、Skill/Recipe、开放可部署架构 | 部分图片为宣发与样片，不能推断完整运行界面 |
| WorkSolo / OnSolo | 官方/可信报道实屏 | 目标入口卡；左节点导航；中自由画布/播放器；右 Agent/Inspector；底剪辑轨 | “先选创作目标再进工具”、个人创作入口、画布与编辑器模式切换 | 产品更名/内测迭代快；不要把 WorkRally 图片混为 OnSolo UI |
| Seko（公开材料跨版本） | 官方发布与实屏 | 暗色素材/角色板、镜头并排比较、参数浮层、候选网格、竖屏时间线/画布 | 脚本—素材—分镜—镜头局部重做、多集共享主体 | 不同版本 IA 有变化；公布样片不能证明大规模失败率与成本 |
| LibTV | 官方产品视频/页面实屏，画布步骤较完整 | 暗色无限画布；左浮动工具；节点菜单；文本/图/视频/音频节点；分支与候选网格 | 节点就地参数、分支可追溯、资产库与 Skill | 大画布并非默认漫剧导航；需补剧集/镜头表格视图 |
| 火山剧创 Dramart | 官方产品材料，以营销化 UI 片段为主 | 剧本解析、角色一致性板、分镜解析三类面板 | 极速 Agent 与专业导演双模式、策略 Agent | 截图是宣发合成，不能据此还原全部菜单或可操作细节 |
| 万镜一刻 | 发布报道 + 一张可辨认画布实屏，其他多为宣传图 | 深色节点画布、参考资产节点、生成输出节点 | 模型/素材节点可视血缘、局部分支 | 公开证据不足以证明全套剧集功能 |
| 万兴剧厂 / ReelMate | 现网页面与媒体弹窗，3 张可辨真实 UI + 1 宣传/播放图 | 左全局 IA；生成页左参数/右结果；批量分镜 dialog；工具箱按视频/图像/音频分类 | 项目与单点工具解耦、批量分镜、模型/音效就近选择 | 未登录深层项目的版本、时间线和团队权限仍需实测 |
| DramaBuddy | 阅文官方入口、腾讯云官方指南中的流程/首页 UI；新闻图噪声已排除 | 项目/内容卡片首页；官方指南中的创建—配置—生成流程 | IP/内容库入口、模板化立项 | 新版无限画布/全景导演台缺少可核验公开实屏；搜索到的财经广告不是产品证据 |
| 泡漫 PopoMint | 3 张官方现网直接截图，A | 暗色；左创作/项目/接单/变现/空间；中创建区；右充值/销售；创作类型 dialog | 按业务目的组织入口、DOCX/TXT 导入、工作流/转绘/自由画布、创作—发行/接单闭环 | 已核验的是登录前/浅层现网；深层分集、单镜重抽、首尾帧、音频和时间线仍未核验 |
| 巨日禄 | 官网/公开视频中的工作台实屏 | 深色左导航；项目/剧本任务；资产匹配；候选网格；生成预览 | 剧本拆解与资产匹配紧邻、批量候选 | 视频字幕覆盖界面，字段细节需二次实测 |
| LumenX | Alibaba 官方 GitHub 与仓库截图，A1 | 左六步流程；中主编辑/候选；右参数/状态；脚本—角色—分镜—视频—配音—合成 | 开源端到端参考、三视图、批量 Take、TTS/FFmpeg、可复现部署 | 是工程参考而非市场成熟度证明；先验证许可证、模型依赖和运行成本 |

### A.3 雷达池：列全不等于全部深抄

下列产品或项目应留在季度雷达，但在缺少真实操作证据前不进入 P0 设计依据：AI导演台、DramaGround、触手AI、牛宁/人人AI、漫剧宝、炼字工坊、CoolCanvas、剧火AI、Doratoon/Laihua、帧赞、360 纳米漫剧流水线、AniShort、YooM、COMWARE、MediaGo Drama、绘映网、次幕AI、ArcReel、镜织、小蓝梯、MagicFrame、叙光、云枢万象、Pixmax、Masous、快手造梦专家。每季度记录：产品状态、可访问性、真实 UI 证据、剧集对象、资产一致性、镜头版本、模型路由、音频/NLE、任务恢复与价格；不要写“网上所有”这种不可验收的无限集合。

海外结构参照不应漏掉：LTX Studio（Elements/Storyboard/Retake/Timeline/Sound/协作的完整影视台）、Katalist（低门槛 Script→Story Canvas→Timeline）、Boords（故事板、Animatic、评审签核与版本）、Wonder Unit Storyboarder（极简板序与专业导出）、Runway Agent + Timeline、Adobe Firefly Boards + Video Editor。它们主要用于验证画布治理、分镜元数据、评审签核和 NLE 心智，不直接证明适配中文长篇漫剧。来源与证据边界见 `docs/research/notes/ecosystem.txt`。

## B. 字节与 MiniMax 的精确结论

### B.1 字节：学垂直骨架，不照抄黑箱

截至 2026-08-21，可核验来源仍是 2026-08-18 的独家报道（`https://m.aitntnews.com/newDetail.html?newId=28337`）。五张图可以证明：

- 首页把“上传剧本”设为唯一 CTA，公开图可读到 PDF/DOCX、5MB；20 万字、30 人团队和约 10 分钟分析来自报道正文。
- 规划阶段先选剧名、画幅和画风，再进入剧本策划；昂贵资产生成之前有明确阶段门。
- 资产页是真正的 Master—Detail—Inspector，而不是一条很长的资产表单。
- 单集分镜页同时放预览、结构化镜头字段、原文/历史/资产和底部镜头条；可见常规/超长叙事模式及首尾帧开关。
- 右上保留“生成剩余分镜 / 合成本集 / 导出本集”，业务产出单位始终是“集”。

不能据此声称它已支持：自由选择供应商、候选 A/B 评审、角色固定正/侧/背、完整音轨、片头片尾、失败续跑、多人权限或抖音发布回滚。本项目应把这些公开空白做成差异化能力。

### B.2 MiniMax Design：学创作 OS，不把画布变成唯一入口

官方当前把 MiniMax Design 定位为本地 AI Agent 创作工作台（`https://hub.minimaxi.com/`）；官方手册与实测显示稳定壳层为：左侧项目/资产/Skill，中间当前模式，右侧 Agent/审批。其五段方法是 Agent Mode → Canvas Flow → Skill Ready → Local Index → Output Sync。

真实 UI 可支持以下判断：

- “添加节点”包含文本、表格、图片、视频、音频、3D 导演台、视频剪辑、ComfyUI 工作流。
- 选中节点后只显示与当前媒体相关的工具；生成卡在执行前显示参考、提示、模型、画幅、分辨率、时长、数量和成本，并可进入等待批准。
- Skill 有分类、搜索、安装、版本和官方标识；工作流是可复用资产，不只是 Prompt 收藏夹。
- 3D 导演台由中部 3D 场景、属性 Inspector、右侧导演 Agent、底部角色/相机多轨构成；输出是静态构图或动态预演参考。
- 视频编辑器遵循左素材库—中播放器—底多轨—右 Agent 的 NLE 心智。

MiniMax H3 官方页（`https://design.minimaxi.com/h3`）能证明最长 15 秒、最高 2K、可选首尾帧和多参考等模型能力；它不能自动证明每个 Design 云节点都有同样控件。项目设计必须将“模型支持”“适配器已接入”“当前 Profile 可用”“此镜头已选择”四层分开显示。

### B.3 两者融合后的北极星

最终不是“字节皮肤 + MiniMax 画布”，而是：

> 字节式项目—剧本—资产—集—镜头—成片骨架，MiniMax 式 Agent 计划/审批、上下文 Inspector、节点血缘、Skill、3D/NLE 专业模式，再叠加 LocalDramaStudio 已有不可变版本、人工审核、Frame Bridge、持久 Job 与本地模型中立性。

默认结构视图回答“这一集做到哪”；高级依赖画布回答“这个结果从哪里来”；两者读取同一事实，不创建画布专属媒体或任务。

## C. 当前 V2 实机审计：22 个路由的真实处置

截图环境为 1280×720，Director 另测 1024×768、1440×900、1920×1080。全页拼接会重复 sticky chrome，只有在普通 viewport 也复现的现象才判定为布局缺陷。

| 路由/页面 | 实际高度或状态 | 保留点 | 当前处置 |
|---|---:|---|---|
| Projects | 720 | 搜索、最近项目、单一新建动作 | 保留并把技术状态改为“下一步/阻塞原因” |
| Project Home | 1086 | 四个目标入口、分集列表 | 每集只留一个“继续”，其他进溢出菜单；加生产进度条 |
| Story Workspace | 2170 | 四步语义、预览后 commit | 改成 viewport 四步 rail + 中央正文/编辑 + 右证据 Inspector |
| Asset Bible | 720，空 fixture | Master—Detail 意图、真实空态 | 左实体/状态，中视觉板，右约束/声音/使用；需另补 populated 视觉测试 |
| QC Policies | 1746 | 继承、机器证据不等于人工批准 | 汇入 Production Settings；版本编辑用 drawer/dialog，历史独立 tab |
| Director Recipes | 1477 | provenance 与不可变版本 | Recipe library + detail；新版本在聚焦编辑器中创建 |
| Production Settings | 1322 | capability/QC/Recipe/输出/容量汇总 | 成为唯一项目治理入口，只在概览显示 effective value |
| Advanced Canvas | 1495 | 专家图与业务依赖 | 全视口画布 + 浮动工具 + Inspector；永不做新手默认入口 |
| Media Lab | 720 | 单一实验任务 | 保留；一键“登记为 MediaVersion”，明确未晋升状态 |
| Project Operations | 7284 | 很多真实低频能力 | 退役聚合 UI，拆成 Project Settings / Runtime / Automation / Delivery Targets / Brand & Compliance / Authorization |
| Models | 3407 | capability、发布版本、resolution | Registry / Effective Resolution 两 tab；发布/编辑用 dialog；普通选择回到任务附近 |
| Jobs | 2038 | 持久队列 | 虚拟表格 + 保存筛选 + 右侧详情抽屉；日志默认折叠 |
| Diagnostics | 1815 | 环境、审计、事件 | Environment / Audit / Event Delivery tabs；请求 ID 深链 |
| Episode Plan | React Router 错误页 | 领域基础已存在 | 先修 query shape 冲突；再做 source / breakdown / shot plan 三步工作区 |
| Director Desk | 文档宽 1670，Inspector 越界 | Shot Nav、Stage、Frame Bridge、Takes、Inspector | 先修 intrinsic width；再实现 focus shell 与断点抽屉 |
| Manual Generation | 4356 | preflight、不可变分支、批量 seed | 常规生成移进 Director Inspector；保留 Advanced Generation 深链与 tabs |
| Episode Review | 4376 | 候选、正式选择、成片审核分离 | 左 inbox、中 compare、右 decision/evidence；三类审核切 tab |
| Audio | 2229 | 对白、授权、轨道绑定 | 对白队列 + 波形/播放器 + Inspector；批量 TTS 在 drawer |
| Timeline | 4255 | revision、冻结输入 | 全视口 NLE-lite；历史/冻结/合成在 drawers/dialogs |
| Delivery | 2110 | 冻结版本、渲染证据、非破坏增强 | Preflight → Compose → Review → Package/Export 四步清单 |
| Episode Run | 4815 | stage/blocker/retry/human gate | 全视口 cockpit；阶段 rail + active detail + blocker/event drawer；控制条 sticky |
| Root Boundary | 正确跳 `/projects` | 无旧 bundle | 保留 query 兼容测试；不得恢复旧 App |

### C.1 量化的长页问题

Project Operations 11 屏、Run/Review/Generation 各约 7 屏、Timeline 6 屏、Models 5 屏。问题不是页面“颜色不漂亮”，而是一个 route 同时承担 overview、编辑、历史、诊断、批量操作与底层配置。验收口径改为：

- 创作工作区默认不使用 document 级马拉松滚动；主媒体、主要决策和唯一 CTA 在一屏上下文内。
- 列表、Inspector、时间线可在有边界的区域内部滚动；滚动容器必须有标题、位置感和键盘可达性。
- 次要详情进 tab/Inspector；低频编辑进 drawer/dialog；有独立生命周期和深链需求的能力进子路由。
- 50+ 行启用虚拟化；历史默认分页；不把折叠 20 个 section 当成完成重构。

## D. 最终信息架构与路由所有权

### D.1 三层导航，不再把所有路由塞进一条侧栏

全局 Rail（始终稳定）：

- Projects
- `Ctrl/Cmd+K` Search / Commands
- Jobs & Machine
- Local Runtime
- System overflow：Models、Diagnostics、Settings

项目 Context Header：项目 / 季 / 集 breadcrumb，保存状态，生产状态，effective 模型/QC 摘要，返回项目入口。

项目 Tabs：Overview、Story、Asset Bible、Production Settings；Canvas、Lab、Automation 等归入 Tools。

分集 Stage Rail：Plan → Direct → Review → Audio → Timeline → Deliver。`Episode Run` 是醒目的生产模式/动作，不与每个手工阶段等权；Manual Generation 从选中镜头进入，不永久占导航。

### D.2 URL 与状态必须可恢复

建议规范：

```text
/projects
/projects/:projectId?tab=episodes
/projects/:projectId/story?step=review&document=:documentId
/projects/:projectId/assets/:assetId?state=:stateId&tab=references
/projects/:projectId/settings?tab=models
/projects/:projectId/episodes/:episodeId/plan?step=shots&beat=:groupId
/projects/:projectId/episodes/:episodeId/direct/:shotId?inspector=generate&take=:variantId
/projects/:projectId/episodes/:episodeId/review?queue=unresolved&shot=:shotId
/projects/:projectId/episodes/:episodeId/audio?line=:dialogueId
/projects/:projectId/episodes/:episodeId/timeline?revision=:revisionId
/projects/:projectId/episodes/:episodeId/delivery?stage=preflight
/projects/:projectId/episodes/:episodeId/run?stage=video
```

URL 只保存可分享的选择/视图，不保存未提交表单、token 或大型 JSON。刷新后恢复当前 project/episode/shot/inspector；无效 ID 回退到同域列表并给出原因，绝不静默选择第一镜去执行写操作。

## E. 核心工作区文字线框与响应式规则

### E.1 >= 1440：Director Focus Mode

```text
┌56 Rail┬──── 240–280 Shot Navigator ────┬──── minmax(520,1fr) Media Stage ────┬── 360–400 Inspector ──┐
│ back  │ scene / search / filters       │ context bar + preview                │ 意图/资产/声音/生成 │
│ jobs  │ shot cards + status + bulk     │ Frame Bridge                         │ 只滚此区域          │
│ cmd-k │                                ├──────────────────────────────────────┴─────────────────────┤
│       │                                │ 180–220 Takes / candidates / compare / new take            │
└───────┴────────────────────────────────┴────────────────────────────────────────────────────────────┘
```

Focus Mode 自动收起普通 `AppShell`，但必须保留返回、breadcrumb、Jobs、运行状态与命令面板。CSS 约束：主 grid 与每个 grid item `min-width:0`；弹性列必须 `minmax(0,1fr)`；长 ID/Prompt `overflow-wrap:anywhere`；document `scrollWidth <= clientWidth`。

### E.2 1280

- 56px rail + 220px Shot Navigator + 弹性 Stage。
- Inspector 变为 360px overlay drawer 或右侧 44px tab rail，打开后覆盖 Stage，不扩展 document。
- Takes 保持底部可折叠；筛选 chip 允许换行或进“更多”，禁止局部横向滚动条。

### E.3 1024 / 900

- Shot Navigator 与 Inspector 为互斥 drawers，Stage 永远保留至少 560px 可用宽度。
- Takes 为 bottom sheet；全局项目导航不变成一条可横向滑动的文字菜单。
- 不把桌面三栏简单垂直堆成 2000px 页面；这是模式降级，不是 DOM 重排。

### E.4 Story / Plan / Review / Audio 的通用壳层

```text
┌ context + stage/step rail ─────────────────────────────────────────────┐
│ left queue/tree  │ central source/media/editor │ right evidence/action │
│ searchable       │ one primary task            │ tabs, bounded scroll  │
└─────────────────────────────────────────────────────────────────────────┘
```

- Story：左文档/章节与四步 rail；中原文或结构稿；右引用、问题、差异与 commit。
- Plan：左 Beat/场次；中镜头表/分镜板；右原文、资产引用、局部 replan diff。
- Review：左默认“仅未解决”队列；中 A/B/overlay compare；右选择、批准、问题和机器证据。
- Audio：左对白/轨道；中视频 + 波形；右声线、授权、TTS、效果与响度。

### E.5 Timeline / Run / Delivery

- Timeline 必须像轻量 NLE：播放器、镜头 strip、对白/配音、BGM/SFX、字幕 lanes、playhead、zoom、snap；卡片只表达缺失输入，不替代轨道。
- Run 用 stage rail 显示真实 DAG 状态；中间只展示当前阶段与阻塞；右 drawer 是事件和恢复动作。未实现的前半阶段显示“需要人工完成”，不能播放假进度。
- Delivery 只有一个随阶段变化的 sticky CTA；增强前后对比在 drawer，不把预检、渲染、增强、打包四个完整表单叠在一起。

## F. 弹窗、抽屉、Inspector 与独立路由的裁决表

| 交互 | 使用场景 | 禁止场景 | 必备行为 |
|---|---|---|---|
| Popover | 3–8 个轻量选择：排序、快速重抽原因、模型快捷预设 | 长表单、异步工作流、危险确认 | anchor、Escape、外点关闭、键盘导航 |
| Dialog | 创建、确认、一次性生成设置、批量成本批准 | 需要边看主媒体边持续调参 | focus trap、初始焦点、返回焦点、脏表单确认 |
| Drawer | 复杂 Inspector、Job 详情、replan diff、模型 resolution、历史/审计 | 唯一主任务或需要全屏比较 | URL 可选同步、固定标题/动作、内部滚动、不扩 document |
| Inline Inspector | 高频且必须边看媒体边调：意图、Frame Bridge、当前 Take | 低频系统配置 | 自动保存/显式保存状态、冲突、继承来源 |
| 独立 Route | 深链、长历史、大表、独立恢复上下文 | 只有三个字段的小编辑 | route ErrorBoundary、loading/empty/error、返回路径 |

所有 feature 禁止自制无焦点管理的 `div` 遮罩。统一使用共享 Dialog/Drawer/Tabs/Popover/Tooltip；图标按钮可视尺寸可小，但命中区至少 44×44。

## G. 模型、抽卡、三视图和首尾帧的最终产品语义

### G.1 模型选择器

普通用户看到“任务能力”，不是 provider 内部名：故事拆解、角色设定、角色三视图、场景概念、镜头首帧、首尾帧视频、多参考视频、配音、口型、超分、视觉 QC。选择器每项显示：

- 推荐原因与继承来源：项目 → 本集 → 本镜；
- 本机/云端、可用/缺依赖、Profile 发布版本；
- 支持输入类型、最大参考数、时长、画幅、分辨率、首尾帧/同步音频；
- 预计时间/显存/费用和商用、地域、隐私标签；
- “自动推荐”与“明确固定”分开；任何 fallback 都需用户可见并写入 Job snapshot。

高级参数在 drawer 用 typed controls；JSON 只放 Debug tab。更换模型时先显示兼容性差异与会失效的输入，不允许把首尾帧悄悄降级为普通 I2V。

### G.2 Takes / 重抽

每次生成创建新 Variant，保留旧版本。用户动作严格分成：

- Retry：同输入/同模型/同参数重试技术失败。
- Resample：显式选择原因，创建新 seed/参数分支。
- Adopt：成为当前选中结果，可撤销。
- Approve：人工批准，不能由 QC 自动代替。
- Compare：2–4 个候选同步放大、擦拭/并排、播放位置联动。

Take 卡显示模型、seed、参考版本、耗时/费用、失败码、QC、selected/adopted/approved；批量“重抽失败镜头”不能覆盖已批准结果。

### G.3 Character Identity Pack

角色一致性不是一张封面图。每个不可变 Identity Pack Version 至少包括：Canonical ID/别名、正/左/右/背、面部近景、表情组、姿态组、服装/年龄/伤势等状态、声音/试听、关系、禁忌、负向约束、来源与授权。镜头引用具体 pack/state version；上游更新只标记下游 stale，不静默替换已生成镜头。

界面：左角色与状态，中参考槽/大图/对比，右描述/声音/出现集/影响范围。空槽说明用途和生成/上传/从媒体登记三种入口；生成三视图进 dialog，批量审核留在主工作区。

### G.4 Frame Bridge

Frame Bridge 永远显示 `上一镜尾帧 → 当前起始参考 → 当前尾帧/下一镜输入`，每个节点显示来源、锁定、版本、stale、缺失和模型兼容性。用户可从上一镜 approved end、当前 keyframe、指定 MediaVersion、自动抽帧中选来源。锁定后重抽不得更换；上游变化必须要求显式重绑或保留旧锚点。

## H. 视觉系统与交互质量门禁

继续使用现有 `design-system/localdramastudio/MASTER.md`：深色持久 chrome、暖色低眩光工作面、朱砂主动作、状态色语义化、4px 网格、SVG 线性图标。检索到的通用“粉紫渐变/Comic 字体/零界面”建议与生产工作台不匹配，明确不采用。

硬规则：

- 正文至少 14px；辅助信息至少 12px；9–10px 只用于非关键短标签，不能承载错误或操作说明。
- desktop field/button 高度 40px；高频图标命中区 44px；焦点环不得被 `overflow:hidden` 裁掉。
- 状态同时用文本/图标/形状，不只靠红绿；错误就近说明影响和下一步。
- healthy runtime 提示降噪；只有会阻止当前动作时才在 Context Bar 强提示。
- loading 保持布局尺寸；长任务显示 queued/running/waiting-human/failed/recovering，不用无限 spinner。
- tabs 支持 roving tabindex、方向键、Home/End、`aria-controls`；Dialog/Drawer 支持 Escape、focus trap/return；媒体快捷键在 modal 打开时停用。
- 尊重 `prefers-reduced-motion`；动画 120–220ms，只表达层级/状态变化，不做装饰性漂移。
- 1024、1280、1440、1920 四宽度均无 document 横向滚动；200% zoom、键盘-only 和高对比模式纳入 UAT。

## I. 产品层完成定义

这轮“大改”只有在以下场景都成立时才算完成：

1. 新用户导入长剧本后，能看见解析进度、离开页面、回来继续，并在人工确认前不会写入正式镜头。
2. 用户在项目级固定角色 Identity Pack，在本集选状态，在单镜查看实际引用版本；更新上游时能看到影响面。
3. 任意非第一镜可从 Plan/Director/Review 深链进入，刷新后仍是同一镜；生成 Job 的 `shot_id` 与 URL/选择完全一致。
4. 用户在 Director 同屏完成看原文、改意图、选资产、首尾帧、选模型、预检、生成、比较、采用、批准；1280 宽不横向平移。
5. 一集可批量运行、暂停、恢复、只重试失败节点；每个阶段都是真实动作或明确人审门，没有假“一键”。
6. Review 默认定位未解决问题，机器证据和人工批准严格分开；选择、采用、批准均可追溯。
7. Audio/Timeline/Delivery 不再是长表单仓库，用户在固定上下文完成监听、对齐、粗剪、冻结、渲染和打包。
8. 模型的 UI 选择、Director 预检、Manual Generation、Episode Run 和 worker 使用同一个 canonical resolver；缺能力时 fail closed，不静默回退。
9. 100/500/1000 镜头项目仍能搜索、虚拟滚动、恢复选择；页面没有 React 默认错误页或未处理 console error。
10. 所有事实仍落在现有不可变版本、MediaVersion、Variant、Selection、Job、Review、Timeline/Render/Delivery 真值中；UI 简化不以丢失证据链为代价。
