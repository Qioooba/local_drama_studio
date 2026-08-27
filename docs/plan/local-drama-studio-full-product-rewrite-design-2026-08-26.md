# LocalDramaStudio 全站产品与架构重构开发设计

> 状态：实施基线 v1.0（2026-08-26，产品/UX/架构交叉审阅已收口）  
> 适用对象：产品、UX、前端、后端、数据迁移、测试、发布负责人  
> 代码基线：`HEAD ccaab08` 加 2026-08-26 当前未提交工作树  
> 核心原则：保留可靠事实层，重写用户心智和工作台；一个任务只有一个 owner、一个 canonical route、一个事实来源  
> 文档关系：本文件取代既有总设计中与“当前页面、菜单、路由、实施顺序”冲突的部分；既有领域细节和 ADR 继续作为背景资料。

---

## 0. 决策摘要

LocalDramaStudio 的主要问题不是功能不足，而是**能力、页面、菜单、状态投影和入口同时过多**。当前系统把后端领域边界和工程运维边界直接暴露成了用户页面：用户需要自己理解策划、导演、生成、运行、驾驶舱、审核、声音、时间线、交付、模型、任务、诊断之间的关系。

本次重构作出以下确定决策：

1. 产品定位改为：**可本地运行、可恢复、可审计，以异常处理为中心的 AI 系列短剧制片台**。
2. 新建项目只提供“快速成片、专业制片”两个主入口；Visual Lab 在项目建立后作为高级工具进入。三者共享同一套项目、资产、镜头、版本、任务和审核事实，不形成三套产品。
3. 默认主导航从完整上下文下的 22 个目的地收敛到 8 个创作阶段：项目首页、故事、资产、分集策划、镜头工作台、分集生产、后期、交付。
4. Director Desk 与 Generation 合并重写为 `Shot Studio`；Episode Run 与 Production Cockpit 合并重写为 `Episode Production`；Review、Audio、Timeline 放入统一 `Post` 外壳，但后端领域仍保持分离。
5. Project Operations 删除；QC、导演模板进入项目设置；Models、Jobs、Diagnostics、Comfy 退出主创作导航；Visual Lab 只作为高级实验入口。
6. 删除持久化 WorkspaceTabs。命令面板不再复制菜单，只保留动作、实体搜索和最近访问。
7. Story 中的泛型 CreativeLibrary 退役。角色、场景、道具、服装统一归 Asset Bible；声音归 Voice Profile；系列圣经、视觉风格和字幕样式改为 typed、versioned 事实。
8. 保留 SQLite/WAL、不可变 revision/version、durable Job、lease/heartbeat/outbox、人工审核权威、Timeline freeze、Delivery lineage；不拆微服务，不重建第二套队列、媒体、审核或 Run 真值。
9. 后端重写为模块化单体的明确 bounded contexts，新增 typed `/api/v2` 聚合查询和命令；废止前端拼装五套生产状态投影的做法。
10. 实施采用“纵向替换”：新工作台验收后，同一里程碑内切换路由并删除旧组件、旧查询和旧写入口。禁止长期双写、长期双页面、`V3` 包裹旧面板、CSS 覆盖旧布局等补丁式演进。

### 0.1 最终用户流程

```text
给出故事或剧本
  → 检查 AI 制作计划
  → 确认角色、场景与视觉规则
  → 自动生产整集
  → 只处理失败、过期和低质量镜头
  → 审阅自动初剪
  → 验证并交付
```

不再要求普通用户按以下系统模块自行拼接流程：

```text
故事 → 资产 → QC → Recipe → Settings → Plan → Director → Generation
→ Run → Cockpit → Review → Audio → Timeline → Delivery → Jobs
```

---

## 1. 审计范围、证据与可信边界

### 1.1 最新代码与编译基线

本轮不是只看旧截图或旧文档，而是使用当前工作树重新编译、启动隔离实例并复核：

- Web 构建命令：`pnpm --dir apps/web build`
- 构建结果：通过；Vite 6.4.3；480 个模块；49 个输出 chunk。
- 最大入口 chunk：约 462.89 KB raw / 139.23 KB gzip，当前 bundle budget 通过。
- 前端生产 TypeScript/TSX：195 个文件、约 29,100 行。
- 前端测试：128 个文件、约 11,147 行。
- 后端 route 文件：50 个；HTTP route 装饰器约 470 个。
- 后端 application：92 个 Python 文件、约 39,460 行。

以上规模数据按 2026-08-26 当前工作树用 `rg --files`、逐文件行数统计和 route decorator 正则得到，只作为复杂度量级，不作为长期固定 KPI；代码继续变化时必须重跑统计，不能复制旧数字。

当前工作树包含大量用户尚未提交的代码，本次只新增本文档；没有重置、覆盖或整理现有业务改动。

### 1.2 页面、路由和菜单覆盖

当前代码可区分为：

- 25 种 canonical 业务 URL 模式；
- 1 个根兼容入口 `/`；
- 23 个独立渲染页面；Director 和 Generation 的 shot URL 复用相同页面；
- 7 类兼容/重定向入口；
- 完整项目和分集上下文下 22 个侧栏目的地；
- 命令面板静态导航也是相同的 22 个目的地；
- WorkspaceTabs 覆盖 19/25 个 canonical 模式，却仍保留已重定向的 legacy canvas。

审计方法：

1. 逐行核对 `router.tsx`、`routeRegistry.ts`、`AppShell.tsx`、Command Palette 和 WorkspaceTabs。
2. 逐页追踪 23 个页面可达的 feature 组件树、页签、抽屉、对话框和静态控件。
3. 使用当前生产构建和全新隔离 G9 fixture，打开全部页面；Visual Lab 额外在隔离数据库内创建两节点画布后复核。
4. 对 10 个高复杂页面补测 1024×768 与 375×812。
5. 使用源代码和实机同时判断 owner 重复，避免把空状态误判为完整业务能力。

截图证据：

- `output/playwright/ui-latest-build-audit-20260826/`：146 张，覆盖 canonical/兼容入口、页面 viewport/full page 与可识别页签状态；
- `output/playwright/ui-latest-responsive-audit-20260826/`：40 张，覆盖 10 个高复杂页面的 1024/375 viewport 与 full page。

### 1.3 本轮没有声称的事情

- 隔离 G9 fixture 有 62 个真实镜头，但大部分媒体、审核、声音和交付内容为空，因此它适合验证页面结构、空态和大列表，不足以证明所有富数据交互都正确。
- 本轮没有点击会创建任务、删除数据、批准媒体或改变正式项目的按钮。
- 在线竞品中部分完整工作台需要登录、付费或白名单；本文将官方公开事实与产品推断分开，不把“官网未展示”写成“产品一定没有”。

---

## 2. 国内主流产品对比与产品判断

### 2.1 行业共同收敛方向

国内领先产品大体收敛到四层：

1. 一句话、小说或剧本的 Agent 快速入口；
2. 剧本 → 主体/资产 → 分镜 → 制作的结构化专业工作台；
3. 用于探索、比较和高级生成的自由画布；
4. 团队、成本、模型、用量、发布等退到项目外层或设置层。

它们并没有减少真实生产步骤，而是把步骤隐藏在 Agent、阶段式工作台和上下文操作中。LocalDramaStudio 当前相反：大量可靠的后台事实被直接映射成一级页面和长期可见菜单。

### 2.2 核心竞品矩阵

核验日期为 2026-08-26。下表“已核验产品结构”只写官方公开页面可支持的事实，“最值得学习/不应照搬”是本文的产品推断。产品类别不同，不能把单镜生成器或通用 NLE 单独当作全站 IA 证据。

| 产品 | 类别 | 已核验产品结构（官方事实） | 最值得学习（本文推断） | 不应照搬（本文推断） |
|---|---|---|---|---|
| [小云雀](https://xyq.jianying.com/) | Agent 快速创作 | 首页以灵感输入为核心，区分短剧创作与通用创作；公开强调长剧本解析和短剧 Agent | 单一开始动作、Agent 内部编排、自然语言局部修改 | 静默采用/批准、云积分和单一生态绑定 |
| [火山剧创 Dramart](https://www.volcengine.com/product/dramart) | 端到端短剧平台 | Agent 模式与人工模式；剧本生短剧、资产库、项目、团队、用量 | 自动与人工在入口解释清楚；QC 是结果状态而非独立必经页面 | 把平台积分、团队云能力直接复制到本地单机产品 |
| [爱奇艺纳逗 Pro](https://nadoupro.iqiyi.com/docs/workspace-guide) | 结构化工作台 + 画布 | 专业工作台按剧本、主体、分镜、制作推进；另有[自由画布](https://nadoupro.iqiyi.com/docs/canvas-guide)和[剧本空间](https://nadoupro.iqiyi.com/docs/script-space-guide) | 能力很多但专业主流程只有少数阶段；分镜参数和生成在上下文内完成 | 其工作台与画布目前不能直接互切，只能复用资产；本项目不能复制这种数据断裂 |
| [万兴剧厂 / ReelMate](https://www.wondershare.cn/new/details/id/1195.html) | 端到端短剧平台 | 长篇改编、批量资产、整集分镜、多模型、团队权限和成本；可与[万兴喵影形成后期闭环](https://www.wondershare.cn/new/details/id/1192.html) | 整集生产、成本/进度、整剧视频/资产/字幕进入后期 | 复制完整企业协同或依赖另一个自有编辑器 |
| [Seko 3.0](https://www.sensetime.com/cn/news/seko-3-0-ai-1) | Agent + 画布 | Agent、无限画布、Skill 和长期资产上下文打通 | 系列资产成为 Agent 记忆；自然语言修改现有结果 | 社区、Skill 市场、商业发行同时扩张造成边界膨胀 |
| [剪映专业版](https://www.capcut.cn/?lang=zh) | 通用 NLE / AI 后期 | AI 成片与专业时间线、字幕、声音、调色等任务型编辑 | 后期围绕预览和时间线，不围绕数据库实体 | 不复制完整通用 NLE；只实现 AI 短剧所需的初剪和修订 |
| [即梦 AI](https://jimeng.jianying.com/) / [Seedance](https://seed.bytedance.com/zh/seedance2_0) | 单镜/素材生成器 | 图片、视频、画布、多模态参考、首尾帧和局部编辑 | 将文字、参考图/视频/音频、首尾帧集中到同一生成面板 | 不能单独作为整剧 IA 合并依据 |
| [可灵 3.0](https://app.klingai.com/cn/quickstart/klingai-video-3-model-user-guide) | 多镜头生成器 | 自动多镜头、自定义多镜头；逐镜时长、景别、视角、内容和运镜 | Director、生成方式和候选不需要拆成三页 | 不把单次多镜头生成误当成整集生产系统 |
| [百度 Hogee](https://cloud.baidu.com/product/Hogee/aidrama.html) | 端到端/私有化方案 | 原创/复刻入口、剧本到成片、画布、多模型、角色一致性与私有化 | 私有化正在成为企业基线，本地产品还需强化审计和恢复 | 不扩张“爆款复刻”和营销投放能力 |

补充观察：

- [磁力开创](https://kc.kuaishou.com/)按“剪同款、一键成片、文生视频、图生视频”组织，说明目标/场景入口比模型入口更易理解。
- [Skywork Skill Hub](https://skywork.ai/skillhub/zh/)适合参考可版本化制作方法，但不构成统一短剧工作台；本项目当前不建设公开 Skill 市场。
- 阿里万镜一刻公开信息可用于观察“故事板 + 无限画布”，但官方完整文档不足，不作为硬验收基线。

### 2.3 按能力维度做出的取舍

| 维度 | 国内产品信号 | 当前产品 | 目标取舍与原因 |
|---|---|---|---|
| 输入与起步 | Agent/长剧本入口已成为常见首屏 | 项目列表长期展开 5/6 步流程 | 两个开始入口 + 可编辑制作计划；先解释成本与 gate，再提交正式事实 |
| 剧本与系列记忆 | 结构化剧本、主体库、长期上下文 | Story 与 CreativeLibrary 混放七类事实 | Story 保留原文/拆解/Series Bible；正式资产只归 Assets |
| 资产一致性 | 角色/场景资产库是整剧产品的共同底座 | Asset Bible 能力强但与 Story 重复 | 保留 typed、versioned 资产和状态，删除第二写入口 |
| 分镜与单镜生成 | 生成器把参考、参数、候选放在镜头上下文 | Director 与 Generation 重复选择和候选 | 合并成 Shot Studio，但只保留设计、生成、候选三种上下文任务 |
| 批量生产 | 端到端产品强调整集进度、成本和失败恢复 | Run/Cockpit/Grid/Freshness 四套解释 | 单一 Production projection，默认只展示待处理异常 |
| 声音 | 端到端产品覆盖音色/TTS；NLE 覆盖混音 | 前期对白与后期音轨职责交叉 | Casting 在 Assets；对白/TTS/口型依赖在生产前；BGM/SFX/混音在 Post |
| 剪辑 | 专业产品与 NLE 形成初剪到精修闭环 | Timeline 同时承担编排、字幕、合成跳转 | 只做 AI 短剧 DRAFT 初剪、字幕、冻结和实际可用的交换格式 |
| 交付 | 企业产品强调平台规格、成本和团队 | Review、Timeline、Delivery 均有交接/批准 | Review 批准冻结代理；Delivery 只做确定性母版、验证和打包 |
| 画布 | 画布用于探索，不应成为第二生产真值 | Lab 采纳不能可靠进入目标镜头 | 保留高级 Lab，只有 typed adoption 才进入正式候选 |
| 模型与成本 | 多模型是基础能力，不再是主流程 | Models/Comfy/Jobs 暴露为一级创作菜单 | 下沉 System；创作页只显示能力、质量、时间、显存和外发风险 |
| 协作与发行 | 部分云平台把团队、社区、投流作为增长层 | 本地产品没有相应身份/权限基础 | 本轮明确不建设，避免复制与定位无关的臃肿功能 |
| 本地、恢复、审计 | 私有化逐渐成为企业卖点 | 已有本地队列、版本、审核与血缘基础 | 作为核心差异化继续强化，但只在异常和证据需要时披露 |

### 2.4 LocalDramaStudio 的真正差异化

应继续强化但下沉到后台事实和异常处理中的能力：

- 本地优先、素材隐私与可离线运行；
- 不可覆盖的内容版本和媒体血缘；
- 可取消、可恢复、可追踪的长任务；
- 候选采用、机器 QC、人工批准、时间线冻结、交付之间的清晰差异；
- 模型、授权、许可证和交付证据；
- 模型可替换，不依赖单一云厂商。

这些是生产级优势，但不应变成 20 多个常驻菜单。

---

## 3. 当前系统的根本问题

### 3.1 导航是五套系统叠加

同一个目的地可能同时出现在：

- 左侧 22 项导航；
- 命令面板 22 项静态导航；
- 顶部项目/季度/分集/镜头四级选择器；
- 面包屑；
- 最多持久化 30 项、显示 8 项的 WorkspaceTabs；
- 项目首页清单、目标卡和分集行快捷按钮。

导航数量多不是唯一问题，更严重的是它们各自维护路由规则和命名，已经造成失效深链和状态不一致。

### 3.2 同一用户任务有多个 owner

| 任务 | 当前 owner 冲突 | 后果 |
|---|---|---|
| 角色/场景/道具/服装 | Story CreativeLibrary + Asset Bible | 用户不知道哪个是正式资产；后端有两套模型 |
| 镜头意图与生成 | Director + Generation | 镜头选择、输入、候选、关键帧重复 |
| 候选采用与批准 | Director + Generation 第 5 步 + Review | “采用、批准、交付”语义混淆 |
| 整集生产状态 | Run + Cockpit + Grid + Freshness + Timeline Status | 页面和 API 同时解释同一集的状态 |
| 模型/工作流实验 | Models + Comfy Lab + Visual Lab | 普通创作者被迫理解三个专家入口 |
| 导演模板/QC | 独立页面 + Production Settings 摘要 | 项目设置被拆散又重复汇总 |
| 后期交接 | Review delivery tab + Timeline export/compose + Delivery | 下一步入口多，责任边界不清 |

### 3.3 页面复杂度已经超过“继续加卡片”的上限

静态可达 UI 树的可复现下限：

| 页面 | 可达 UI LOC | 原生控件下限 | 判断 |
|---|---:|---:|---|
| Models | 3,874 | 139 | 创作者配置、系统合同、工作流和 runtime 混在一起 |
| Director | 2,961 | 168 | 正确能力被六个 Inspector 页签和多层动作包围 |
| Generation | 2,289 | 92 | 五步向导重复 Director 和 Review |
| Production Settings | 2,220 | 103 | 设置、运行、健康、自动化、授权成为大杂烩 |
| Review | 2,121 | 62 | 审核能力强，但又承担交付跳转和重复批准 |

这不是单纯的大文件问题。即使拆成更多 React 文件，只要用户仍要理解相同数量的阶段和 owner，产品复杂度不会下降。

### 3.4 后端重复投影已经反向制造前端复杂度

当前同一集生产状态至少有 production、production summary/detail、production grid、cockpit、timeline status、production freshness 等多套读模型。部分 Production Grid 通过 `job.type/purpose` 字符串猜测阶段。AppShell 又常驻加载项目、目录、setup、storyboard、cockpit、timeline status、worker health，并进行 10/30 秒轮询。

结果：

- 页面需要组合多个时间点不同的状态；
- “运行成功”与“候选已采用/已批准/时间线已刷新”容易各说各话；
- 新页面倾向再建一个 facade，而不是删除旧解释；
- 首页读取健康状态时可能同步遍历目录和 SHA256 媒体，导航成本与项目规模相关。

### 3.5 泛型数据模型正在成为补丁容器

CreativeLibrary 的 `creative_entries` 同时容纳 SERIES_BIBLE、CHARACTER、SCENE、PROP、COSTUME、STYLE、VOICE；其中四类与 story assets 重复，VOICE 与 voice profiles 重复，STYLE 又被字幕样式借用。这种“先不迁移，塞进通用表”的做法会破坏领域所有权和不可变历史。

### 3.6 已确认的路由合同错误

至少七类生产者/消费者不一致：

1. 首页“绑定模型能力”生成 `production-settings?view=profiles`，设置页不接受 `profiles`，静默回到 overview。
2. 首页资产入口携带 `?episode=`，Assets 不读取它。
3. Production Cockpit 审核入口携带 `?shot=`，Review 不读取它。
4. 全局搜索生成 Story `?scene=`，Story 不读取它。
5. 全局搜索生成 Story `?document=`，Story 不读取它。
6. 全局搜索生成 Review `?review=`，Review 不读取它。
7. 搜索生成 Jobs `?project_id=`，前端读取 `?project=`。

资产搜索还可能只带 `asset=id`，但 Assets 先使用默认 CHARACTER 过滤，搜索到场景/道具/服装时可能无法选中目标。

### 3.7 响应式基础可用，但交互密度仍不合格

10 个高复杂页面在 1024 和 375 宽度下没有 document-level 横向滚动，Director 也能退化为抽屉式布局，这是应保留的进步。但自动检查每种视口仍记录 34 个小于 44×44 的可见交互目标；Episode Plan 占大多数。Timeline 和 Episode Run 还使用被当前 CSP 阻止的 `data:` SVG 状态图。

因此响应式目标不是“推翻现有全部 CSS”，而是减少页面任务数、统一工作台 primitive，并明确小屏只承担浏览、异常处理和批准，不强行复刻完整桌面编辑器。

---

## 4. 目标产品模型

### 4.1 两种开始方式，一个高级实验工具

#### 快速成片

适合一句话、小说或剧本起步。Agent 首先生成可编辑制作计划，计划至少展示：

- 将创建的季、集、场、镜；
- 角色、场景、道具和视觉风格；
- 画幅、声音、字幕、音乐与交付目标；
- 本地能力解析结果；
- 预计时间、显存、磁盘和外发风险；
- 当前缺失项和需要人工确认的 gate。

用户确认计划后，调用统一 automation/jobs 执行。正式采用、批准和冻结仍是人工决定。

#### 专业制片

直接进入 Story、Assets 或 Episode Plan；用户可以逐阶段控制，也可以在任一阶段启动批量自动生产。

#### 实验室（项目建立后的高级工具）

Visual Lab 不与“快速成片/专业制片”并列为新建入口。用户先建立最小正式项目，再从高级工具创建 Lab，用于非线性参考、变体、比较和序列草稿。首版任何实验结果只有通过正式 `AdoptLabCandidate` 命令，才进入已有镜头的指定媒体槽候选；不能直接创建隐式镜头，也不能改变正式选择或批准。资产候选采纳不在本次范围内，未来必须另立 typed contract 才能开放。

### 4.2 信息披露而不是永久“用户模式”

- 默认：速度、质量、一致性、画幅等创作语言。
- 高级：模型建议、seed、参考权重、Recipe、生成参数。
- 开发者：Comfy JSON、runtime、原始日志、诊断。

披露层级跟随页面和权限，不建立第二套数据或第二套工作流。

### 4.3 明确的任务所有权

| 用户任务 | 唯一前端 owner | 核心事实 owner |
|---|---|---|
| 原文、剧本、拆解、系列设定 | Story | Project & Story |
| 角色、场景、道具、服装、状态、Voice Profile/casting | Assets | Asset Bible / Voice |
| 场、Beat、镜头顺序和时长草案 | Plan | Planning |
| 镜头意图、参考、生成、工作采用 | Shot Studio | Directing + Generation + Selection |
| 对白版本、TTS 工作候选、口型同步依赖 | Shot Studio | Dialogue + Generation；Episode Production 只编排 |
| 自动编排、失败恢复、异常队列 | Episode Production | Orchestration + Jobs projection |
| 人工内容批准和批注 | Post / Review | Review |
| BGM、SFX、混音和音频缺口 | Post / Audio | Audio |
| 初剪、字幕、轨道放置、冻结、NLE 交换 | Post / Edit | Postproduction |
| 渲染验证、平台验收、包和清单 | Delivery | Delivery |
| 模型、连接、runtime、Job、诊断 | System Center | Runtime Platform |

---

## 5. 目标信息架构与路由

### 5.1 默认可见导航

项目级固定三项：

1. 首页
2. 故事
3. 资产

选中分集后增加五个阶段：

4. 策划
5. 镜头
6. 生产
7. 后期
8. 交付

项目设置从项目菜单进入；Visual Lab 从“高级工具”进入；系统中心由右上角本机状态/任务入口进入。完整上下文最多显示 8 个创作目的地，而不是 22 个。

### 5.2 canonical route tree

```text
/projects
/projects/:projectId
/projects/:projectId/story
/projects/:projectId/assets

/projects/:projectId/episodes/:episodeId/plan
/projects/:projectId/episodes/:episodeId/studio
/projects/:projectId/episodes/:episodeId/studio/:shotId
/projects/:projectId/episodes/:episodeId/production

/projects/:projectId/episodes/:episodeId/post        -> client replace to /post/review
/projects/:projectId/episodes/:episodeId/post/review
/projects/:projectId/episodes/:episodeId/post/audio
/projects/:projectId/episodes/:episodeId/post/edit
/projects/:projectId/episodes/:episodeId/delivery

/projects/:projectId/labs
/projects/:projectId/labs/:labId
/projects/:projectId/settings                        -> client replace to /settings/production
/projects/:projectId/settings/:section

/system/capabilities
/system/jobs
/system/diagnostics
/system/workflows
```

确定性默认入口：`.../post` 客户端 `replace` 到 `post/review`；`.../settings` 客户端 `replace` 到 `settings/production`。不能由生成式 AI 或易变化的 next action 决定 canonical 默认路由。`post/review|audio|edit` 使用明确子路由，不用互不一致的 `view=` query。可分享状态只进入 URL；临时面板开关留在本地 UI state。

### 5.3 Shell 规格

新 Shell 只承担：

- 品牌与返回项目列表；
- 项目/分集上下文切换；Episode picker 按季度/Season 分组，支持搜索和最近分集；
- 当前阶段导航；
- 面包屑；
- 本机/任务状态入口；
- 命令面板。

明确删除或改变：

- 删除全局 shot selector；镜头只由 Shot Studio 左侧 rail 管理。
- 删除 WorkspaceTabs；跨页面恢复依赖 URL、浏览器历史、实体级草稿、上一位置和 Command Palette 的最近项目/分集/镜头。升级时显式迁移仍有效的 draft，并清理旧 tab localStorage key。
- 命令面板不再硬编码 22 条菜单副本，只提供动作、实体搜索、最近访问和少量全局跳转。
- Shell 初始加载只调用一次轻量 `AppContextQuery`，不加载完整 storyboard、cockpit、timeline status 或同步项目健康检查。
- 任务运行通过 SSE/outbox cursor 更新；正常连接时不进行全局 10/30 秒轮询。
- 项目/分集切换尽量保持当前阶段；存在 dirty draft 时必须显示“保存并切换/放弃并切换/取消”，不能静默丢编辑。

### 5.4 旧路由到新路由的切换表

| 当前入口 | 新 canonical route | 切换方式 |
|---|---|---|
| `/projects` | `/projects` | 原位重写 |
| `/projects/:pid` | `/projects/:pid` | 原位重写 |
| `/story` | `/story` | 原位重写，旧 hash 转 typed focus |
| `/assets` | `/assets` | 原位重写，`kind/asset` 转 typed segment |
| `/qc-policies` | `/settings/quality` | 兼容两稳定版本；删除旧页 |
| `/director-recipes` | `/settings/directing` | 兼容两稳定版本；删除旧页 |
| `/production-settings?view=*` | `/settings/:section` | 明确映射合法 section；未知值报错而非静默 overview |
| `/projects/:pid/settings` | `/projects/:pid/settings/production` | 客户端 `replace`；不丢项目上下文 |
| `/operations` | `/settings/production` | 兼容两稳定版本；删除页面 |
| `/episodes/:eid/plan` | 同路径 | 原位重写 |
| `/episodes/:eid/direct[/:shot]` | `/episodes/:eid/studio[/:shot]` | 兼容两稳定版本；保留 shot 精度 |
| `/episodes/:eid/generation[/:shot]` | `/episodes/:eid/studio[/:shot]?focus=generate` | 兼容两稳定版本；删除旧页 |
| `/episodes/:eid/run` | `/episodes/:eid/production` | 兼容两稳定版本 |
| `/episodes/:eid/production` | 同路径 | 从零替换为单一 Production workspace |
| `/episodes/:eid/review` | `/episodes/:eid/post/review` | 兼容两稳定版本；保留 media/focus |
| `/episodes/:eid/audio` | `/episodes/:eid/post/audio` | 兼容两稳定版本 |
| `/episodes/:eid/timeline` | `/episodes/:eid/post/edit` | 兼容两稳定版本；保留 revision/focus |
| `/episodes/:eid/delivery` | 同路径 | 原位重写 |
| `/projects/:pid/labs[/:lab]` | 同路径 | 保留，高级入口 |
| `/models` | `/system/capabilities` | 兼容两稳定版本；`project` 转项目设置上下文 |
| `/jobs` | `/system/jobs` | 兼容两稳定版本；统一 `project` 参数 |
| `/diagnostics` | `/system/diagnostics` | 兼容两稳定版本 |
| `/lab` | `/system/workflows` | 兼容两稳定版本；删除 MediaLab 一级页 |
| `/projects/:pid/models` | `/projects/:pid/settings/capabilities` | 客户端 `replace`；保留项目上下文 |
| `/projects/:pid/jobs` | `/system/jobs?project=:pid` | 客户端 `replace`；统一 typed project filter |
| `/projects/:pid/diagnostics` | `/system/diagnostics?project=:pid` | 客户端 `replace`；保留项目筛选 |
| `/projects/:pid/lab` | `/system/workflows?project=:pid` | 客户端 `replace`；旧 MediaLab 不是 Visual Lab |
| `/canvas` 及根 `?view=` | 对应 typed canonical route | 只做兼容解析；有歧义时显示选择，不静默丢 shot |

切换前必须有 route contract test 覆盖项目、分集、镜头、media、revision 和 focus；redirect 不得丢失实体精度。

服务端可处理的旧 URL 使用 HTTP 308；React Router 内部兼容使用 `<Navigate replace>`/`history.replaceState`，不是 HTTP 308。兼容窗口固定为两个稳定版本，不依赖跨设备遥测或“全局使用量归零”；可选诊断遥测必须另行获得用户明确同意。

---

## 6. 当前 23 个页面的完整处置

| 当前页面 | 当前职责 | 决策 | 目标归属 |
|---|---|---|---|
| Projects | 项目库、新建项目、一句话生成 | 保留项目库，重写开始流程 | `/projects`；快速创作使用独立向导/全屏流程 |
| Models | 项目能力、模型发布、LLM、连接、Profile、Workflow、Runtime | 拆分重写 | 项目能力绑定进 Settings；系统模型/连接/runtime 进 System Center |
| Jobs | 任务列表、详情、恢复、容量 | 保留高级页，降级导航 | 日常状态进任务托盘和 Production；完整页 `/system/jobs` |
| Diagnostics | 环境、审计、全局搜索 | 拆分 | 环境/审计进 `/system/diagnostics`；全局搜索进 Command Palette |
| MediaLab `/lab` | Comfy 启停、捕获 JSON、测试、提升候选 | 删除独立一级页 | `/system/workflows` 的开发者区 |
| Project Home | 7 项清单、4 张目标卡、分集快捷入口 | 从零重写 | 唯一下一步、异常摘要、分集列表、最近活动 |
| Story | 导入、拆解、角色建档、故事圣经 | 保留核心，重写边界 | 原文/拆解/typed Series Bible；资产仅提案或只读引用 |
| Assets | 四类资产、状态、Identity Pack、参考与生成 | 保留并重写为唯一 owner | `/assets` master-detail 工作台 |
| QC Policies | 继承、阈值、自动重抽、版本 | 删除一级页 | `/settings/quality` |
| Director Recipes | 模板、版本、绑定、复制 | 删除一级页 | `/settings/directing`，演进为制作方案组成部分 |
| Production Settings | 概览、freshness、交付、自动化、授权/项目包 | 保留设置外壳但拆成子路由 | `/settings/:section`；运行控制移出设置 |
| Visual Lab List | 实验画布列表与创建 | 保留，高级化 | `/labs`，不出现在默认阶段导航 |
| Visual Lab Workspace | 节点、Inspector、运行、快照、采纳 | 保留并重写生产采纳 | 实体选择器取代 raw ID/JSON；事务性 adopt |
| Project Operations | 四个 owner 的迁移索引 | 删除 | 旧 URL 一次性重定向到项目设置 |
| Episode Plan | 分镜板、场景/分组、原文、提示词快照 | 保留策划核心，收敛 | 原文用上下文 Drawer；提示词进 Studio；“时间线”改名时长条 |
| Director Desk | 镜头意图、连续性、候选、生成、批准 | 与 Generation 合并重写 | Shot Studio；只负责工作采用，正式批准归 Review |
| Generation | 五步生成与候选审核 | 页面删除，能力合并 | Shot Studio 右侧生成 Inspector + bottom Takes |
| Production Cockpit | 镜头×阶段矩阵与筛选 | 页面删除，能力合并 | Episode Production 的 rows/cockpit 视图 |
| Episode Review | 候选、整集、交付交接 | 保留审核 owner，删除空壳交接 | Post / Review；正式人工批准唯一入口 |
| Audio | 对白/TTS、音轨、证据及专家治理操作 | 保留创作部分，专家动作迁出 | Post / Audio；runtime execution profile 发布、手工 Job 完成进入 System |
| Timeline | 编排、字幕、导出与合成 | 保留并收敛 | Post / Edit；只负责初剪、字幕、冻结、NLE 交换 |
| Delivery | 检查、合成、审核、打包 | 保留，重写批准语义 | 消费 Review 内容批准；文件验证、平台验收、打包 |
| Episode Run | Run、Cockpit、Freshness 三层页签 | 与 Cockpit 合并重写 | 单一 Episode Production |

### 6.1 当前 22 个菜单的逐项处置

| 当前菜单 | 决策 |
|---|---|
| 全部项目 | 保留为品牌/项目切换入口，不与首页重复展示 |
| 项目总览 | 保留，改名“首页” |
| 故事 | 保留 |
| 角色与场景 | 保留，统一命名为“资产” |
| 自动质检规则 | 从主导航删除，移入项目设置 |
| 导演模板 | 从主导航删除，移入项目设置/制作方案 |
| 生产设置 | 从主导航删除，放项目菜单 |
| Visual Lab | 从默认导航删除，放高级工具 |
| 项目维护 | 删除 |
| 分集策划 | 保留为“策划” |
| 导演台 | 与镜头生成合并为“镜头” |
| 镜头生成 | 与导演台合并为“镜头” |
| 整集生产 | 保留并重写为“生产” |
| 审核 | 归入“后期 / 审核” |
| 声音 | 归入“后期 / 声音” |
| 时间线 | 归入“后期 / 编辑” |
| 交付 | 保留 |
| 生产驾驶舱 | 删除独立入口，归入“生产” |
| Comfy 工作流设计器 | 移入 System / Workflows，开发者可见 |
| 模型与能力 | 移入 System / Capabilities；项目绑定留在 Settings |
| 任务与机器 | 默认改为任务状态托盘；完整页仅高级入口 |
| 诊断与审计 | 移入 System；异常时由状态点直达 |

### 6.2 每个独立页面的内部结构复核

下表用于证明本轮不是只看页面标题或侧栏，而是把页面内的页签、步骤和主要浮层一起纳入处置判断。

| 页面 | 已复核的内部结构 |
|---|---|
| Projects | 项目搜索/状态、项目卡、三步建项、一句话生成 5/6 步流程、创建 Dialog、模型详情 Drawer |
| Models | 项目生成能力、添加与检查模型、故事拆解模型、远端服务与密钥；执行配置契约、工作流版本、Comfy Runtime；证据确认 Dialog、模型 Drawer |
| Jobs | 项目范围、任务列表、取消/重试/复制、容量快照、任务详情 Drawer |
| Diagnostics | 本机环境、审计历史、全局检索三个页签 |
| MediaLab | Comfy 自动配置、启停/重启、Workflow JSON 捕获、测试、候选提升 |
| Project Home | 七项首次制作检查、四张目标卡、分集行快捷入口、季度/分集追加 |
| Story | 导入原稿、审核拆解、角色建档、故事圣经四阶段；CreativeLibrary 的七类 kind |
| Assets | 角色/场景/道具/服装四类；Identity Pack、状态、参考版本、多视图、表情、近景、媒体选择；三个 Dialog、两个 Drawer |
| QC Policies | 图像/视频/声音/连续性/交付范围，项目→分集→镜头继承、阈值、自动重抽、版本化编辑 |
| Director Recipes | 版本库、项目绑定、新版本/复制、画幅、镜头、资产、生成和 QC 规则 |
| Production Settings | 生效概览、时效与失效、交付目标与品牌、自动化与外发、授权与项目包五个页签及三类确认/撤回流程 |
| Visual Lab List | 画布列表、创建表单、空态与项目返回 |
| Visual Lab Workspace | 十种 node kind、Palette、React Flow、node Inspector、运行/快照/采纳、删除与修订 |
| Project Operations | 上方四张 owner 卡与下方四个重复 owner 链接 |
| Episode Plan | 镜头分镜板、场景与分组、原文证据、提示词快照四页签；分镜板内清单/故事板/时间线三视图；原文、Beat、镜头和批量 Drawer |
| Director Desk | 画面、角色场景、生成、连贯性、声音、高级六个 Inspector；Shot Rail、Media Stage、Takes、原文、比较、重抽、批准和关键帧 |
| Generation | 方式与镜头、输入与控制、检查并启动、创作分支、人工审核五步；四种生成意图、确认 Dialog、模型 Drawer |
| Production Cockpit | 镜头×六阶段矩阵、七类状态筛选、阶段 Inspector |
| Episode Review | 镜头候选、整集成片、交付交接三页签；筛选、批量审核、A/B、逐帧批注、正式采用 Drawer |
| Audio | 台词与 TTS、音效与配乐、缺口与证据三页签；对白/音色/TTS/音轨治理、两个 Dialog、一个 Drawer |
| Timeline | 编排与冻结、字幕版本、导出与合成三页签；版本证据和合成检查两个 Drawer |
| Delivery | 交付检查、合成候选、审核成片、打包交付四阶段；重冻结 Dialog、文件验证、人工/平台批准、后处理、联系表 |
| Episode Run | 生产运行、关卡总览、失效与影响三个外层页签；运行内动态八阶段、三档质量、五种人工确认策略、取消 Dialog、证据 Drawer |

### 6.3 跨页面功能级处置矩阵

页面清单不能代替功能决策。下表覆盖当前最容易在重写中被“顺手搬家”而继续膨胀的跨页面能力；只有目标 owner 已可用且迁移/不变量测试通过，旧入口才允许删除。

| 当前功能 | 决策 | 目标 owner / 唯一事实 | 删除旧实现的条件 |
|---|---|---|---|
| Outbox Delivery Panel / webhook 投递 | 保留运维能力，退出创作页 | System / Diagnostics；event log + delivery attempts | 新消费者 offset、重试和审计可用 |
| Project Automation 与 Episode Run 自动化 | 合并定义与执行语义 | Settings / Automation 定义；Orchestration 执行 | 不再存在第二套 run/task 状态机 |
| Project Health / 文件 hash 扫描 | 改为显式后台诊断 | Overview 只读摘要；Diagnostics Job 为事实 | 导航路径同步扫描次数为 0 |
| Brand Kit、画幅、水印 | typed、versioned 保留 | Settings / Delivery；Brand/Delivery preset | 历史 preset 与交付 lineage 已迁移 |
| Grant、许可证、外发同意 | 保留并收敛 | Settings / Rights；Rights/Grant revisions | 普通创作页不再编辑授权底层字段 |
| 项目包、备份、恢复、可移植性 | 从 Access 拆出 | Settings / Data & Portability 唯一执行；Recovery Set | DB 与项目树可一致恢复并完成演练 |
| 媒体衍生、孤儿、缓存维护 | 保留专家动作 | System / Diagnostics；Media lineage/index | Asset/Review 页面不再暴露维护按钮 |
| Runtime execution Profile、Local LLM、Provider connection | 拆分项目绑定和全局发布 | Settings / Capabilities + System / Capabilities | 与 Assets 的 Voice Profile 命名、schema 和 owner 严格分开 |
| Job cancel/retry/clone | 日常动作收敛，禁止复制原始技术请求 | 任务托盘/Production；System / Jobs 查看 attempt | “重做”转为 typed command + 新 idempotency key |
| QC policy、自动重抽 | 保留策略，删除一级页面 | Settings / Quality；typed policy version | 所有消费者使用同一有效版本 |
| Director Recipe | 保留为制作方案组成部分 | Settings / Directing；typed recipe version | Shot Studio/Production 能解析同一方案 |
| CreativeLibrary 七类条目 | 拆 typed 事实，停止泛型生产写入 | Story/Assets/Voice/Style 各自 owner | 离线迁移、冲突 quarantine 和历史保全通过 |
| 全局搜索 | 保留能力，删除 Diagnostics 内重复 UI | Command Palette；typed search target | 所有结果由单一 route builder 精确定位 |
| 审计历史 | 保留专家视图 | System / Diagnostics；append-only audit | 创作页仅显示当前实体必要证据 |
| 字幕版本、样式、校对 | 保留，拆事实与放置 | Post / Edit；subtitle revision/style revision | 不再借用 CreativeEntry STYLE |
| Timeline export / compose | 拆分 | Edit 负责 OTIO/EDL/实际支持交换；Delivery 负责确定性 master | 空壳 compose 跳转和重复渲染入口删除 |
| Delivery 本地工具检查 | 保留能力，按需调用 | System capability probe；Delivery preflight 消费 | Delivery 不展示 endpoint/path 等实现细节 |
| Voice Profile 与对白 TTS | 拆清 casting、生产依赖和后期 | Assets 管 Voice/casting；Shot Studio 是对白/TTS 唯一人工入口；Production 只编排；Post / Audio 做混音 | 不再在 Audio 发布 runtime Profile、选择 TTS 或手工完成 Job |
| Visual Lab snapshot/revision/adopt | 保留实验历史，重写采纳 | Visual Lab + Shot media-slot candidate/adoption lineage | 采纳后目标 Shot 立即可见且不自动批准 |
| 候选采用、机器 QC、人工批准 | 严格拆语义 | Selection / Quality Evaluation / Review Decision | UI、API、迁移都不再用一个 `approved` 模糊表示 |

---

## 7. 目标页面详细设计

### 7.1 Projects：项目库与开始创作

目标：用户在 10 秒内理解“继续哪个项目”或“如何开始”。

页面只保留：

- 搜索、最近打开、状态筛选和项目卡；
- 一个主动作“新建”；
- 新建后的两个主入口：快速成片、专业制片；项目建立后才显示 Visual Lab 高级入口；
- 最近失败/需要恢复的运行提示，但不展开 Job 技术细节。

快速成片使用独立全屏流程或 route modal，不把 5/6 步 Wizard 永久铺在项目列表下方。长文本、上传文件和 AI 解析不能只靠 localStorage：它们进入独立 `CreationDraft` 暂存聚合，位于应用级 staging 区而不是正式项目目录，默认 7 天 TTL，可显式保留/删除，内容加密/权限与正式项目一致。草稿使用 revision、幂等解析命令和断点恢复；确认制作计划后用单个 `CommitCreationDraft` UoW 创建项目、季度/分集、文档和 automation plan，成功后标记草稿 consumed。并发确认返回同一 project id；失败不留下半项目，后台清理只能删除过期且未运行的草稿。

不得出现：Profile code、Job ID、SQLite、API health、Comfy endpoint、原始模型契约。

### 7.2 Project Overview：首页

首屏固定为：

1. 当前唯一推荐动作，并显示“为什么推荐”和基于哪些确定性事实；
2. 最多三个可切换分集的阻塞/待处理事项；
3. 分集列表及每集一个“继续”动作；
4. 最近活动。

删除当前“7 项首次制作清单 + 4 张目标卡 + 分集行 4 个快捷入口”的三重重复。准备度细节进入 Drawer；没有阻塞时不显示完整检查项。

首页只读取最近一次健康/诊断摘要，不同步遍历项目目录或计算媒体 SHA256。深度检查由显式后台 Diagnostics Job 完成。

系列结构管理也归此页：创建、重命名、排序、归档/恢复季度与分集；删除只允许在无正式下游事实时执行，否则只能归档。推荐动作由 versioned deterministic rule 生成，生成式 AI 可以解释但不能决定路由；并行制片仍可从待处理事项或分集列表进入其他合理工作。

### 7.3 Story：从原文到可生产故事事实

主阶段固定为：

1. 导入与范围确认；
2. AI 拆解审核；
3. 系列圣经。

交互要求：

- 左侧显示阶段和文档版本；中间是正文/拆解 diff；右侧是当前问题与动作。
- AI 不能直接覆盖原文或已应用拆解；修改形成新 revision。
- 角色/场景/道具/服装只显示“提案”和 Asset Bible 状态，不在 Story 内建立第二套正式资产。
- Series Bible 只承载世界观、主题、人物关系、故事规则、视觉风格引用和跨集连续性规则。
- 任意错误原地显示恢复动作；不要求用户跳 Jobs。

### 7.4 Assets：系列资产唯一工作台

采用 master-detail 布局：

```text
资产类型/筛选  |  资产列表  |  媒体与状态舞台  |  Inspector
```

核心能力：

- 角色、场景、道具、服装四类 typed 资产；
- 角色 Identity Pack、三视图、造型/服装/年龄/伤势/情绪状态；
- 场景时间、天气、破坏状态和多角度参考；
- 道具持有与位置；
- 媒体参考版本、授权、使用位置和影响分析；
- 角色音色为引用关系，编辑入口跳到 typed Voice Profile，而不是复制声音事实。

资产专属生成可以存在，但必须从当前资产上下文发起；不再与通用 Generation 页面并列。普通用户不填写媒体版本 UUID、raw JSON 或文件系统路径。

### 7.5 Episode Plan：分集策划

只负责场、Beat、镜头结构、顺序、时长、画面/动作草案和来源证据。

页面结构：

- 顶部：集目标、总时长、结构状态和唯一主要动作；
- 左侧：场/Beat 分组；
- 中间：镜头清单或故事板；
- 右侧：选中实体 Inspector；
- 原文证据：上下文 Drawer；
- 下方：可撤销的时长条，不称“时间线”。

删除独立“提示词快照”页签。Prompt 是 Shot Studio 生成上下文的一部分；Plan 只显示可读的意图摘要和生产准备状态。

### 7.6 Shot Studio：镜头工作台

这是本次前端重写的核心。它一次性替代 Director Desk、Generation Workbench 和重复的单镜候选审核。

桌面布局：

```text
┌──────────────────────────────────────────────────────────────┐
│ 项目 / 分集 / S023    上一镜  下一镜     保存状态   主要动作 │
├──────────┬───────────────────────────┬───────────────────────┤
│ Shot Rail│        Media Stage        │ Inspector             │
│ 搜索/筛选│  当前采用 / 候选 / 对比    │ 画面 · 资产 · 连贯性  │
│ 虚拟列表 │  原文与邻镜按需 overlay    │ 设计 · 生成 · 候选证据│
├──────────┴───────────────────────────┴───────────────────────┤
│ Takes：候选、血缘、QC、采用/撤销；正式批准入口指向 Review   │
└──────────────────────────────────────────────────────────────┘
```

职责边界：

- 默认只暴露三个上下文任务：“设计”（构图、机位、动作、资产、连续性、Frame Bridge 和对白/声音意图）、“生成”、“候选与证据”；不把旧六个 Inspector 页签原样搬入新页面。
- 在同一 Inspector 内选择生成目标、参考和“速度/质量/一致性”预设；高级区才展示模型/seed/权重。
- Shot Studio 是对白版本编辑、TTS 生成/试听/工作采用及口型同步依赖的唯一人工入口；Episode Production 可以按同一计划自动 dispatch/retry，但不能再实现第二套 TTS 编辑或选择 UI。
- Preflight 直接显示缺失、成本、显存、预计时间和修复动作。
- 生成失败、重试运行错误、重抽创意分支必须分开。
- Takes 显示 lineage、机器证据、工作采用状态；可以采用和撤销，但正式人工批准只在 Post / Review。
- 相邻镜头导航和批量操作共享相同 Shot Studio aggregate，不再重新加载整集全部细节。

响应式：

- `>=1440`：三栏 + bottom Takes。
- `1024–1439`：右侧 Inspector overlay，可固定；Shot Rail 可收起。
- `768–1023`：Shot Rail 与 Inspector 互斥 Drawer；Takes bottom sheet。
- `<768`：支持浏览、备注、采用/撤销、批准跳转和异常处理；不承诺完整导演参数编辑。

### 7.7 Episode Production：异常驱动的整集生产

一次性替代 Run、Cockpit、Production Grid 和 Freshness 页面。

```text
┌──────────────────────────────────────────────────────────┐
│ 生产方案  预计时长/显存/磁盘  开始/暂停/恢复/取消        │
├──────────────────────┬───────────────────────────────────┤
│ 阶段进度/DAG          │ 异常队列                          │
│ 剧本 资产 策划        │ 缺资产 / 失败 / QC / 未采用       │
│ 关键帧 视频 声音 初剪 │ 过期 / 冲突 / 待人工确认          │
├──────────────────────┴───────────────────────────────────┤
│ 镜头行：阶段、状态、原因、候选、采用、批准、下一动作     │
└──────────────────────────────────────────────────────────┘
```

只有两个主视图：“待处理”（默认）与“全部镜头”。默认页显示阻塞/失败/QC/过期/待确认卡片，DAG 压缩为顶部阶段进度摘要；全镜头行和完整 DAG 技术图按需进入第二视图/Drawer。移动端只显示异常卡片与精确修复动作，不呈现阶段矩阵。每个状态必须来自统一 `EpisodeProductionProjection`，不是页面自己扫描 Job 字符串。

页面允许：

- 选择草稿/平衡/精品方案；
- 查看计划和人工 gate；
- 开始、暂停、恢复、取消；
- 只重试失败项；
- 跳到精确 Shot Studio、Review 或 Post 修复；
- 查看汇总 ETA、磁盘、显存、并发和失败原因。

Job attempt、lease、原始 provider response 只在“技术详情”中链接到 System / Jobs。

### 7.8 Post：统一后期外壳

Post 只共享当前分集、transport/playback 和 timecode，不共享多态 selection；Review 的媒体/决定、Audio 的对白/音轨、Edit 的 timeline item 各自维护 query、selection 和懒加载边界。前端统一不等于把后端重新揉成巨型模块。

#### Review

- 人工内容批准的唯一 owner；
- 镜头候选、A/B、逐帧批注、批量决定；
- 区分 selected/adopted、machine passed、human approved；
- 正式决定使用明确 target kind：`VIDEO_SHOT`、`AUDIO_LINE`、`EPISODE_CUT`，不得共用模糊 approved；
- Edit 冻结 timeline revision 后生成低成本 review proxy；整集审核在此批准该 frozen revision，而不是批准 Delivery 临时输出；
- 删除“交付交接”空壳页签，完成后只显示下一步 CTA。

#### Audio

- Assets 是 Voice Profile/casting 的唯一编辑 owner；Shot Studio 在视频/口型同步前准备并锁定对白版本与 TTS 工作候选；Production 只编排/展示其 readiness；Post / Audio 负责 BGM、SFX、混音、音频缺口和 TTS 引用试听；
- 用户不能在此发布 runtime execution profile、手工结束 Job 或粘贴技术 ID；这些移至 System；
- Audio 只能采用/撤销工作候选；任何正式 `AUDIO_LINE` 或整集混音批准都在 Review 完成。

#### Edit

- 基于已采用媒体自动生成 DRAFT 初剪；
- 只负责将已准备的对白/BGM/SFX 放置到轨道，并做裁切、增益、淡入淡出和节奏调整；声音生成和源素材治理不在 Edit；
- 镜头替换、时长、字幕和简单节奏调整；
- 冻结形成不可变 Timeline revision；
- 冻结后生成绑定该 revision 的 review proxy；修改冻结内容必须产生新 revision 和新 proxy；
- 提供 OTIO/EDL/剪映/DaVinci 等实际支持的交换格式；
- 不建设通用调色、特效和完整 NLE；不重复 Delivery 的渲染/打包。

### 7.9 Delivery：验证、验收和打包

固定步骤：

1. 输入与时间线预检；
2. 从已批准 frozen revision 确定性生成 master；
3. 文件与平台验收；
4. 打包与清单。

规则：

- 内容批准来自 Review，Delivery 只消费，不重复批准内容。
- Delivery 不提供内容候选选择；编码失败可重试相同 render spec，任何内容变化必须回到 Edit 产生新 frozen revision 和 Review decision。
- 机器完整性检查不等于人工或平台批准，禁止“一键验证并批准”合并语义。
- 后处理在冻结/合成前完成；打包后发现需要后处理必须产生新的 render lineage。
- 输出预设围绕目标平台、画幅、编码、字幕和水印，不围绕 provider。
- 每个文件都有 hash、来源 revision、许可证/授权和验证结果。

### 7.10 Project Settings：分区而非巨型页签

子路由固定为：

```text
/settings/production     项目规格、默认画幅、默认制作方案
/settings/capabilities   项目能力绑定与继承
/settings/directing      导演模板/Recipe
/settings/quality        QC 阈值、自动重抽和版本
/settings/delivery       目标平台、品牌、字幕、水印
/settings/automation     自动化策略和人工 gate
/settings/rights         授权、许可证与外发同意
/settings/data           项目包导入导出、备份、恢复与可移植性（唯一执行入口）
```

每个子页只承担一个任务，不能再把运行按钮、项目健康、G 系列门禁和技术 runtime 塞回 Settings。

### 7.11 System Center：专家和运维能力

| 页面 | 主对象与布局 | 唯一主动作 | 普通/高级披露与空错态 |
|---|---|---|---|
| Capabilities | 能力目录列表 + 发布版本/连接详情 | 添加或修复能力 | 默认显示“图像/视频/声音可用性”；高级才显示 provider、endpoint、模型契约；无能力时给引导，密钥错误原地修复 |
| Jobs | 筛选/虚拟列表 + attempt/artifact Drawer | 对失败任务执行 typed 重试 | 默认按项目与失败聚合；高级显示 lease、request/provider event；无 Worker、离线、容量满是不同状态 |
| Diagnostics | 本机环境/存储/数据库/审计摘要 + 检查/恢复演练报告 | 运行非破坏性深度检查 | 默认红黄绿摘要；高级显示日志、hash 和恢复集可用性；可做只读恢复演练并跳 Settings/Data，但不能重复执行项目包导入导出或正式恢复 |
| Workflows | Workflow definition/version 列表 + runtime/契约 Inspector | 发布兼容版本 | 默认只见能力匹配；开发者才见 Comfy JSON/runtime；未安装、版本不兼容和运行故障分别处理 |

普通模式只显示“本机正常/有 3 个任务/需要配置视频能力”等创作语言。开发者页才显示 endpoint、JSON、lease、request ID 和 provider event。

### 7.12 Visual Lab：高级沙盒，不是第二套生产系统

保留 React Flow 画布、typed node、edge、revision 和 snapshot。重写以下交互：

- raw `reference_id` 改为项目实体选择器；
- raw JSON Inspector 改为 typed form，高级模式可查看 JSON；
- “正式生产”改成明确的“采纳到镜头候选”；
- preflight 显示目标镜头、源媒体、授权和 lineage；
- commit 必须让候选立即出现在目标 Shot Studio，且不自动 selected/approved。

Lab 只能在已有项目内创建。首版目标实体选择器必须先选择已有镜头及其媒体槽；“自由探索”结果可继续留在 Lab，但没有目标时不能执行正式采纳，也不能暗中生成季度、分集、镜头或资产版本。

### 7.13 全目标页面响应式矩阵

断点统一验证 320/375/414/768/1024/1440；断点是布局约束，不是设备名称。所有触屏/coarse pointer 的主要交互目标至少 44×44，Drawer/Sheet 关闭后焦点回到触发器。

| 页面 | `>=1440` | `1024–1439` | `768–1023` | `<768` / 320–414 |
|---|---|---|---|---|
| Projects / Overview | 卡片/表格可切换，摘要并排 | 紧凑双栏 | 单列摘要 + 紧凑列表 | 只显示继续/异常主动作；创建向导全屏 |
| Story | 阶段、正文/diff、问题三栏 | 正文为主，问题 Drawer | 阶段 rail 可收起，正文/diff 单视图 | 允许审核和备注，不做并排长文 |
| Assets | “类型+筛选+列表”单 rail，舞台 + Inspector | Inspector overlay | rail/Inspector 互斥 Drawer | 资产卡 + 媒体舞台；复杂 Identity Pack 转全屏 sheet |
| Plan | 场/Beat rail、清单/故事板、Inspector；时长条下置 | Inspector overlay，rail 可收起 | rail/Inspector 互斥 | 浏览、排序、时长与备注；拖拽有上移/下移替代 |
| Shot Studio | 三栏 + Takes | Inspector overlay，Shot Rail 可收起 | rail/Inspector 互斥 Drawer，Takes sheet | 浏览、备注、工作采用和异常处理；不承诺全参数编辑 |
| Production | 待处理/全部镜头；技术 DAG Drawer | 分页镜头行 + 异常区 | 异常卡 + 按需镜头详情 | 只显示异常卡、批量安全动作和进度摘要 |
| Post / Review | 播放器 + 决定列表/Inspector | Inspector overlay | 列表与 Inspector 互斥 | 单候选审阅、批注、批准；A/B 为分步对比 |
| Post / Audio | 波形/试听 + 音源/音轨列表 | Inspector overlay | Inspector bottom sheet | 试听、音源与缺口处理；复杂混音只读/跳桌面 |
| Post / Edit | 播放器 + 简化多轨 + Inspector | 简化轨道 + Inspector overlay | 单主轨 + bottom sheet | 浏览、字幕校对、简单裁切；不支持精细多轨拖拽 |
| Delivery | 四步内容区 + 证据 Drawer | 单列 stepper + Drawer | 单列 stepper | 预检、状态、验收与下载；不展示宽表 |
| Settings / System | 左侧 section + 列表详情 | 紧凑列表详情 | section 下拉 + Drawer | 单列列表/表单；破坏性动作独立确认页 |
| Visual Lab | 无限画布 + Palette + Inspector | 画布为主，Inspector Drawer | Palette/Inspector 互斥 Drawer | 只支持浏览、运行状态和简单节点编辑；复杂连线提示桌面继续 |

任何排序/拖拽都有键盘和按钮替代；小屏“只读”边界必须在进入控件前说明，不能等保存时才拒绝。

---

## 8. 目标技术架构

### 8.1 保留的部署与可靠性基线

- React/TypeScript Web；
- FastAPI/Python Worker；
- SQLite WAL 和项目文件系统；
- Durable DB queue、job attempt、lease、heartbeat、outbox；
- 模块化单体；
- 本地优先和显式外发；
- 人工审核最终权威；
- Episode Run/Quick Create 只是统一 orchestration/jobs 的 facade。

不因 UI 重写而删除已有 revision、variant、decision、job、attempt、artifact、timeline、delivery、audit 和授权事实。

### 8.2 目标目录和模块边界

```text
local_drama/
  kernel/
    ids.py
    clock.py
    errors.py
    unit_of_work.py
    domain_events.py

  modules/
    project_story/
    asset_bible/
    planning_directing/
    generation/
    quality_evaluation/
    review_decision/
    dialogue_voice/
    audio_post/
    postproduction/
    delivery/
    orchestration/
    runtime_platform/
    visual_lab/

  readmodels/
    app_context/
    project_overview/
    shot_studio/
    episode_production/
    post_workspace/

  worker/
    runner.py
    handlers/
      media.py
      comfy.py
      llm.py
      tts.py
      compose.py
      delivery.py
      automation.py

  api/
    public_v2/
    internal/

  bootstrap/
    container.py
```

每个业务模块内部使用：

```text
domain/
application/commands/
application/queries/
ports/
adapters/sqlite/
api/schemas.py
```

硬规则：

- Domain 不依赖 FastAPI、SQLite 或 filesystem。
- Application 只依赖 ports 和 Unit of Work。
- Public route 不直接 import/构造具体 Database。
- Application service 不直接 new 另一个 application service。
- 跨模块写操作通过公开 port 或 domain event/outbox。
- Read Model 不得任意跨模块读取内部表。每个模块通过公开 query port 或发布 projection 提供数据；workspace assembler 只能拼装、分页和标注 freshness，不得包含业务判断或写操作。每个聚合返回 `as_of_event_id`、`source_revisions` 和 `freshness`，避免把新 read model 写成下一代巨石。
- 仍是一个 SQLite、一个 API、一个 Worker 部署单元，不拆网络微服务。

### 8.3 六类唯一真值

#### 内容

不可变 revision/version 为权威。对象上的 `current_*` 只能是明确命名、可重建的 head projection。

#### 媒体

`media_assets/media_versions` 只管理二进制及血缘，不承担故事语义。

#### 执行

只有 `jobs/job_attempts/artifacts/provider_execution_events` 是执行真值。Job lifecycle 只允许 `QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED`，但控制意图与观测状态必须分开：`desired_state=ACTIVE|CANCELLED` 和 `cancel_requested_at` 表达“已请求取消但 provider 尚未确认”，UI 可派生 `CANCELLING`；`CLAIMED/RUNNING/ORPHANED/UNKNOWN_SIDE_EFFECT` 属于 attempt observed state，`NEEDS_ATTENTION` 是由 attempt/provider 证据派生的 `attention_reason`，不是另一套 Job 终态。任何 Quick、Run、Cockpit 都不得再建一套 running/succeeded/failed。

#### 编排

只有 automation workflow run/task/event 表达阶段与依赖。Task 状态只允许 `WAITING/READY/DISPATCHED/SETTLED/SKIPPED`，其终态由关联 Job 事件推进；Creator Run 和 Production Run 是 automation run 的 1:1 typed facade，只增加计划、人工 checkpoint 和展示元数据，不保存独立 running/succeeded/failed。

#### 选择与审核

切换后的 append-only selection/review event stream 为权威，事件必须包含 `SET/CLEAR/APPROVE/REVOKE`、单调 aggregate sequence、target kind 和 expected revision；selected/approved head 是可重建 projection。旧库并不具备完整 CLEAR/REVOKE 历史，因此迁移时以现有 `media_assets` head 为切换点初始权威，旧 selection/review 记录只用于校验，绝不能按 `created_at` 猜 head；差异进入 quarantine。机器 QC 永不自动成为人工批准。

#### Timeline 与 Delivery

Timeline revision/item 是可编辑事实；Render 和 Delivery 是特定冻结 revision 的不可变后代。Freshness 由 lineage/fingerprint 推导。

### 8.4 Asset 真值拆分

- `story_asset_sets/story_asset_versions`：角色、场景、道具、服装的不可变语义修订；head projection 只指向当前 version；
- `story_asset_states`：项目/集/场/镜头状态；
- `story_asset_references -> media_versions`：视觉证据；
- Identity Pack：角色身份和多视图；
- Voice Profile：声音事实；
- Series Bible、Visual Style Guide、Subtitle Style Template：各自 typed revisions。

每个迁入 version 保留 source creative entry/revision id、content hash、parent/restored lineage。旧唯一键是 `(project, kind, code)`，目标不能只使用 `(project, code)`；采用稳定内部 ID，加 kind-scoped display code，并用 `asset_aliases` 记录同 code 跨 kind 冲突。删除泛型 CreativeEntry 作为新生产入口。历史数据迁移后只读归档，不能继续被字幕、角色和声音共同借用；VOICE/STYLE 只有在目标 schema、授权和用途可确定时转换，否则 quarantine，禁止塞回 `extra_json`。

#### 8.4.1 Job identity、scope 与 stage

- `subject_kind/subject_id` 是 Job 创建后不可变的业务目标，例如 project、asset、shot、timeline revision 或 delivery；只能有一组 canonical subject。
- `scope_project_id/scope_episode_id/scope_shot_id` 是有外键/层级约束的查询索引，可由 subject/上下文验证，但不成为第二目标真值。
- `stage_code` 是受控枚举/注册表值，在任何 Shot Studio v2 Job 写入前完成 schema 与历史 backfill；禁止继续从 `type/purpose` 运行时猜阶段。
- 旧 `project_id/subject_type/subject_id` 字段停止新写后，先成为兼容 projection，再在相应 slice 的 ledger 中删除；项目级、资产、时间线和交付 Job 不被强行伪装成 shot Job。

#### 8.4.2 Visual Lab 正式采纳真值

`lab_candidate_adoptions` 首版至少包含 `source_lab_revision_id`、`source_media_version_id`、`target_shot_id`、`target_media_slot`、`created_candidate_id`、`lineage_id`、`adopted_by`、`created_at`。命令在一个 UoW 内验证来源授权/血缘、创建属于目标 Shot media slot 的 candidate 记录并写 adoption/outbox；不得改变源 Lab 媒体 owner，也不得只在源媒体上写 selection 文案。采纳只创建正式候选，不自动 SET selection，不自动 APPROVE。

### 8.5 API v2 聚合查询

#### App Context

```http
GET /api/v2/app-context?project_id=&episode_id=
```

仅返回名称、面包屑、最近上下文、权限/能力标志和轻量 worker/task 摘要；禁止返回 `visible_navigation`、完整 storyboard、全量镜头或文件健康扫描。可见导航只由前端 typed route descriptor 结合权限/能力派生，不能再形成第二份菜单事实。

#### Project Overview

```http
GET /api/v2/projects/{projectId}/overview
```

返回唯一 next action、阻塞摘要、episode 摘要和最近活动。

#### Shot Studio

```http
GET /api/v2/episodes/{episodeId}/shots/{shotId}/studio?nav_radius=12
```

返回当前 shot revision、来源、资产与状态、连续性、候选/选择/审核投影、当前 jobs、能力解析、QC、allowed actions 和有限邻镜头。

命令：

```http
PUT  /api/v2/shots/{shotId}/draft
POST /api/v2/shots/{shotId}:mark-ready
POST /api/v2/shots/{shotId}/generations:preflight
POST /api/v2/shots/{shotId}/generations
POST /api/v2/media-versions/{versionId}:adopt
PUT  /api/v2/shots/{shotId}/dialogue-draft
POST /api/v2/dialogue-lines/{lineId}/tts-generations
POST /api/v2/audio-versions/{versionId}:adopt-working
```

#### Episode Production

```http
GET  /api/v2/episodes/{episodeId}/production/overview
GET  /api/v2/episodes/{episodeId}/production/shots?cursor=&limit=&state=
GET  /api/v2/episodes/{episodeId}/production/changes?after=
POST /api/v2/episodes/{episodeId}/production-runs
POST /api/v2/production-runs/{runId}:pause
POST /api/v2/production-runs/{runId}:resume
POST /api/v2/production-runs/{runId}:cancel
POST /api/v2/production-runs/{runId}:recover
```

生产行必须能表达一个镜头的多阶段、多媒体槽和具体失效边，不能压成一个 `stage_code`：

```text
shot_id, overall_state, next_action,
stages[{stage_code,state,reason_code,active_job_id,allowed_actions}],
material_slots[{kind,candidate_count,selected_version_id,
                machine_qc_state,human_decision_id}],
freshness_edges[{source_revision,target_revision,state,reason}],
blockers[{code,owner_route,repair_action}]
```

`overview`、`shots`、`changes` 分开分页和缓存，不返回无限大的超级 payload。

#### Post

```http
GET /api/v2/episodes/{episodeId}/post/overview
```

Review、Audio、Timeline、Delivery 继续拥有各自 typed query/command API；共享的 overview 只用于导航、播放头和阻塞摘要。

正式批准命令只属于 Review：

```http
GET  /api/v2/episodes/{episodeId}/review-targets?target_kind=&cursor=
POST /api/v2/review-decisions
POST /api/v2/review-decisions/{decisionId}:revoke
```

`review-decisions` 请求必须携带 `target_kind`、`target_id`、`expected_revision` 和明确 decision；Shot Studio 只显示只读审核投影与 handoff URL，不提供 review write command。

#### 8.5.1 页面—查询—命令—事件—深链合同

下表是最小合同，不表示把全部领域写进一个 endpoint。所有 `focus`、filter 和实体参数由 schema/route descriptor 白名单验证；未知参数返回可见错误或被显式规范化，禁止静默忽略。

| 页面/owner | 初始 query（示例） | 核心 command | 刷新事件 | 允许的可分享定位 |
|---|---|---|---|---|
| Projects / Creation | `GET /projects`、`GET /creation-drafts/:id` | create/update/analyze/commit/discard draft | `CreationDraftChanged`、`ProjectCreated` | `draftId` path；`status/sort` query |
| Project Overview | `GET /projects/:pid/overview` | create/rename/reorder/archive season/episode | `ProjectStructureChanged`、`NextActionChanged` | `season`、`episode`、`focus=blockers\|activity` |
| Story | `GET /projects/:pid/story?revision=` | import、analyze、apply/revert revision、update bible | `StoryRevisionCreated`、`BibleVersionChanged` | `document`、`revision`、`scene`、`focus=diff\|issue` |
| Assets | `GET /projects/:pid/assets?kind=&cursor=` | create version/state/reference、bind voice | `AssetVersionChanged`、`AssetStateChanged` | `kind`、`asset`、`version`、`focus=media\|state\|rights` |
| Episode Plan | `GET /episodes/:eid/plan` | create/update/reorder scene/beat/shot、mark ready | `PlanRevisionChanged` | `scene`、`beat`、`shot`、`view=list\|board` |
| Shot Studio | studio aggregate + bounded nav | shot/dialogue draft、generate、adopt/undo picture/TTS working candidate | `ShotChanged`、`DialogueVersionChanged`、`CandidateChanged`、`JobChanged` | shot 必须走 path；`focus=design\|generate\|takes`、`media`、`line` |
| Episode Production | overview + paged shots + changes | start/pause/resume/cancel/recover/retry-safe | `AutomationTaskChanged`、`JobChanged`、`ProductionProjectionChanged` | `view=attention\|all`、`state`、`shot`、`stage` |
| Post / Review | `GET /episodes/:eid/reviews?target_kind=&cursor=` | comment、decide、revoke | `ReviewDecisionChanged` | `targetKind`、`targetId`、`media`、`frame`、`decision` |
| Post / Audio | audio workspace + read-only dialogue/TTS references | add/remove BGM/SFX source、edit mix draft | `AudioSourceChanged`、`MixDraftChanged` | `line`、`track`、`range`、`focus=dialogue-reference\|music\|sfx\|mix` |
| Post / Edit | timeline revision + items/subtitles | edit item、subtitle revision、freeze、export | `TimelineRevisionChanged`、`ReviewProxyReady` | `revision`、`item`、`range`、`focus=timeline\|subtitle\|export` |
| Delivery | delivery overview + render/package evidence | preflight、render master、validate、accept platform、package | `RenderChanged`、`DeliveryChanged` | `delivery`、`render`、`file`、`focus=verify\|package` |
| Settings | `GET /projects/:pid/settings/:section` | update/publish typed section；Data 执行 export/import/backup/restore | `ProjectPolicyChanged`、`CapabilityBindingChanged`、`RecoverySetChanged` | section 走 path；`version`、`focus` 按 section schema |
| Visual Lab | list/workspace revision query | mutate graph、run、snapshot、adopt candidate | `LabRevisionChanged`、`LabRunChanged`、`CandidateAdopted` | lab 走 path；`node`、`revision`、`focus=run\|adopt` |
| System / Capabilities | catalog/connection/runtime compatibility | add/test/publish/retire capability | `CapabilityChanged` | `capability`、`version`、`focus=connection\|contract` |
| System / Jobs | paged jobs + attempt/artifact | cancel、typed retry | `JobChanged`、`CapacityChanged` | `project`、`episode`、`shot`、`job`、`state` |
| System / Diagnostics | summary/report/audit queries | start/cancel check、run non-mutating restore rehearsal | `DiagnosticRunChanged` | `project`、`report`、`focus=storage\|database\|audit\|recovery` |
| System / Workflows | workflow/runtime/version queries | test、publish、retire workflow | `WorkflowVersionChanged`、`RuntimeChanged` | `workflow`、`version`、`focus=runtime\|contract` |

每个 command 的 `allowed_actions` 由领域策略返回；前端不能根据按钮是否可见猜权限或状态。全局搜索只返回 typed target，再由同一 descriptor 生成上表允许的 URL。

### 8.6 命令和并发合同

所有写命令必须：

- 有请求 schema、响应 schema 和稳定错误码；
- 携带 `expected_revision` 或等价并发条件；
- 携带 `idempotency_key`；
- 返回新 revision/head 和受影响 projection；
- 写 audit/outbox；
- 失败时给原地恢复动作，不用模糊 500 文案。

### 8.7 事件刷新

```http
GET /api/v2/events?after=<cursor>
```

SSE 不能直接复用现有 outbox 的全局 `delivered_at`。新增不可由 transport ack 改写的 durable `event_log` 与 `consumer_offsets`：webhook delivery attempt、projection checkpoint、每个 SSE client cursor 相互独立，任何消费者都不能“吞掉”别人的事件。事件定义 retention；cursor 早于保留窗口时返回 `CURSOR_TOO_OLD` 和需要重取的 query scopes，客户端先全量刷新再取得新 cursor。

SSE 只发送使 query key 精确失效所需的事件摘要。`Episode Production /changes` 是同一 event log 的 episode-scoped、可分页视图，不是第二套事件真值。断线后用 cursor 补拉；事件不能携带整个领域对象，也不能与轮询同时造成重复风暴。

### 8.8 契约和客户端

当前“generated client”实质仍由脚本内约 1,600 行手写模板生成，且大量 endpoint 缺少明确 `response_model`。目标：

- public v2 endpoint 的请求/响应 schema 覆盖率 100%；
- OpenAPI 是唯一网络合同；
- TS client 使用标准生成器从 OpenAPI 真正生成；
- 关键 envelope 在运行时校验，契约不符统一为 `CONTRACT_MISMATCH`；
- 禁止页面自行 `fetch`、自行断言 `as T` 或维护第二份 DTO。

### 8.9 单一路由描述符

建立一个 typed route descriptor，派生：

- Router；
- breadcrumb；
- stage navigation；
- Command Palette navigation/search validator；
- legacy redirect；
- route ownership test。

后端全局搜索不再返回任意字符串 URL，而返回 typed target：

```json
{
  "kind": "SHOT",
  "project_id": "...",
  "episode_id": "...",
  "shot_id": "...",
  "focus": "review"
}
```

由前端唯一 builder 生成 canonical URL，从根上消除 `project/project_id` 和未消费 query。

---

## 9. 数据重构与离线迁移

当前 schema 已到 `0060_visual_lab_runtime_foundation`。真正实施时必须读取实际 head，使用下一个可用编号，不能抢号或修改历史迁移。

### 9.1 新增或补强的 typed 事实

- 应用级 `CreationDraft`/revision/upload/analysis，含 TTL、consumed 状态和 commit idempotency；
- Series Bible set/version/section；
- Visual Style Guide version；
- Subtitle Style Template version；
- Story Asset set/version/alias/source lineage，完整承接 CreativeEntry revision；
- Creator/Production Run plan/checkpoint 与 automation run 的 1:1 外键，不新增执行 status；
- Job canonical `subject_kind/id`、显式 `stage_code` 与受约束的 `scope_project/episode/shot` 索引；
- append-only selection/review SET/CLEAR/APPROVE/REVOKE event，以及切换点 initial head；
- Lab Candidate Adoption、target candidate 与 lineage；
- 必要的 selected/approved materialized heads；
- durable event log、独立 consumer offsets、projection rebuild cursor 和 migration quarantine。

### 9.2 必须退役的重复事实

- CreativeEntry 中的 CHARACTER/SCENE/PROP/COSTUME/VOICE/STYLE 生产写入口；
- One Sentence Run 自建的执行状态机和候选状态；
- 通过 Job purpose/type 运行时猜生产阶段的逻辑；
- Visual Lab 只写 selection 文案但不建立目标镜头 lineage 的“采纳”；
- 页面或 facade 自己缓存的 Run/Cockpit 成功状态；
- 物理删除不可变 revision 的字幕样式路径。

CreativeEntry 按 kind/消费者逐项切换，不按整表同时关停。旧泛型 UI 和通用写 API 可以先删除；仍服务尚未重写消费者的 STYLE/VOICE 只能经过有明确 owner、截止 Slice 和单写目标的兼容 adapter，禁止双写。每个 kind 的消费者 inventory 为 0 后，才能把对应 legacy 写路径改只读并归档。

### 9.3 离线原子迁移流程

本项目是本地应用，采用短暂停机维护比长期双写更可靠：

现有 maintenance lock 只约束维护命令，现有 recovery set 也只备份数据库/记录文件 manifest，不能满足本次迁移。迁移必须由 launcher 驱动的 release upgrader 执行：

1. 预检磁盘空间、权限、版本和外部 provider 状态；先在恢复集副本上 rehearsal，输出迁移 ledger 和 quarantine 预览。
2. launcher 进入维护模式：API 拒绝新写命令，停止新 Job dispatch；活跃 lease 必须到安全 checkpoint、可证实取消或由用户确认等待，之后停止 API/Worker。单独写 `maintenance.lock` 不算完成栅栏。
3. 使用 SQLite backup API 创建一致 DB 快照并 fsync；实际复制或使用具备快照语义的文件系统能力保存项目树、配置和 staging upload。只写 hash manifest、或对仍可能原地改写的文件建立普通硬链接，都不算备份。
4. 对 DB/项目树恢复副本验证可打开、表计数、关键 ID、revision/version 数量与媒体 hash manifest；验证失败不得开始迁移。
5. 在 staging 副本建 typed 表、约束、索引、event log、consumer offset 和 quarantine。
6. 按 revision、kind 和 consumer inventory 一一迁移 CreativeEntry：保留 source id/hash/parent/restored；CHARACTER/SCENE/PROP/COSTUME 进入 asset set/version，跨 kind code 用 alias/collision 报告；SERIES_BIBLE 与用途明确的 Visual Style 在 Slice 3 切换。Subtitle STYLE 与 VOICE 只有在目标 schema、授权和所有消费者已经改读唯一 typed owner 时才切换，否则保留期限明确的单写 adapter 或进入 quarantine，绝不提前关停整张表。
7. 选择/批准迁移以旧库当前 head 写入 cutover initial event；旧历史只校验，不从不完整历史重建 CLEAR/REVOKE。差异 quarantine，禁止凭 created_at 猜测。
8. One Sentence 历史关联到唯一 automation/job/media；保留原事件但停止写旧执行状态。回填 Job canonical subject/stage/scope，未知项 quarantine，切换后不再从字符串猜阶段。
9. 只有 source、target slot 和 lineage 都有效时回填 Lab adoption，并创建目标 candidate；不能将源 Lab selection 当作目标候选。
10. 重建受本 slice 影响的 read models/event offsets，执行 `integrity_check`、`foreign_key_check`、计数/hash/invariant 对比和项目包往返测试。
11. 原子替换该 slice 的 DB/文件与 Web/API/Worker 版本，再开放写入。第一次 v2 写入是该 slice 的不可逆提交点；提交前可整体恢复 DB + 项目树，提交后不能只降级程序或只恢复 DB。

采用纵向切片发布，不做一次全站双写或一次性全量 v2 大切换。每个 slice 必须提交 `schema → backfill → verify → read switch → write switch → legacy delete/archive → rollback boundary` ledger；同一事实从切换时刻起只有一个写 owner。升级用户保留历史 Alembic 链；稳定两个版本后为新安装生成 squash baseline。旧表先只读归档，再从新安装基线移除。

---

## 10. 非补丁式实施规则

### 10.1 明确禁止

- 不在旧 Director 外再包一个新 Tab；
- 不在 Episode Run 内再嵌一个旧 Cockpit 后保留独立 Cockpit；
- 不建立 `V3` 页面后继续双写 V2/V3 状态；
- 不新增 `status_json/extra_json` 逃避 typed migration；
- 不用 CSS `overflow:hidden` 掩盖布局问题；
- 不用更多首页卡片解决导航迷失；
- 不为每个模型、效果或 Job type 新增页面；
- 不拆微服务来回避模块边界；
- 不为了“重写”丢失已有不可变历史、审核决定、任务恢复和媒体血缘。

### 10.2 每个纵向切片的完成方式

```text
定义唯一 owner 和事实
  → 新建 typed API/query/command
  → 从零实现新工作台
  → 迁移真实快照并跑等价/不变量测试
  → 原子切换 canonical route
  → 同一里程碑删除旧组件、旧写 API、旧投影
  → 旧 URL 只保留有期限的兼容 redirect
```

兼容 redirect 保留两个稳定版本后按发布说明删除；离线本地产品不依赖全局遥测判断“使用量归零”。任何可选遥测必须明确 opt-in。不得用 feature flag 无限期保留两套产品。

### 10.3 纵向切片顺序

#### Slice 0：架构地基

- 建立一条可运行的 walking skeleton：composition root、UoW、port、domain event、typed error、OpenAPI 生成客户端从一个薄 v2 endpoint 贯通；
- 建立精确 legacy debt manifest，记录每个 concrete Database 依赖、跨 service 构造、无 response schema 路由及 owner/slice/删除版本；总数禁止增长；
- 新增 release upgrader、durable event log/consumer offsets；
- 在首个 Shot Studio Job 之前落地 canonical Job subject/stage/scope schema 与 backfill；
- 架构测试先阻止新增违规，并要求当前 slice 所属模块清零；不是要求先横向重写全部 400+ 端点。

#### Slice 1：Settings / System 基础 owner

- typed route descriptor、public/internal API 边界和基础 AppContext；
- 建立 Project Settings 子路由与 System 四页的可用最小版本；
- QC、Recipe、项目能力绑定、Models、Jobs、Diagnostics、MediaLab 的新 owner 先可达；
- 旧入口暂时保持，待对应新页等价验收后逐项切 route 并删除旧写入口；此阶段不先砍导航造成能力失联。

#### Slice 2：Projects / Quick Create / New Shell / Overview

- CreationDraft、上传/解析/TTL/commit 与替换 One Sentence 新建流程；
- 两种项目开始入口，Lab 只在项目创建后可达；
- 8 项阶段导航、按季 Episode picker、dirty switch guard；
- 删除 WorkspaceTabs 和静态 22 命令副本，迁移 draft/最近实体并清理旧 key；
- AppContext + SSE、首页确定性 next action 与季/集管理；
- Project Health 改后台任务；Operations 新 owner 就绪后删除旧导航和页面。

#### Slice 3：Story / Asset Truth

- typed Series Bible、Story Asset revisions 与用途明确的 Visual Style；
- 按 kind 迁移 CreativeLibrary；建立 STYLE/VOICE consumer inventory，不能提前关闭字幕/声音消费者；
- Asset Bible 新命令/查询；
- 删除重复角色/场景/道具/服装生产入口；遗留消费者若需过渡，只能单向调用 typed owner，禁止双写。

#### Slice 4：Shot Studio

- Studio aggregate；
- 镜头草稿、资产、连续性、Frame Bridge；
- generation preflight/job；
- Takes、adopt/undo、review handoff；
- 完成后删除旧 Director route、Generation route 和重复候选组件。

#### Slice 5：Episode Production

- 消费 Slice 0 已完成的 Job 显式 stage/subject/scope；
- 通用 run facade；
- 单一 Production Projection；
- 新 Production workspace；
- 删除旧 Run/Cockpit/Grid/Freshness 页面和投影。

#### Slice 6：Post + Delivery

- Review、Audio、Edit 三子路由共享外壳；
- 生产前对白/TTS 与后期 BGM/SFX/混音边界；自动 DRAFT 初剪；
- frozen revision → review proxy → typed Review decision → deterministic Delivery master；
- 后端拆分巨型 timeline service；
- typed Subtitle Style 成为唯一 owner；旧 Post 字幕 API 临时 adapter 在切换验收后当期删除。VOICE 也按 consumer inventory 在其最后消费者切换的 slice 完成迁移；
- Delivery 只消费内容批准；
- 删除 review delivery 空壳、timeline compose 跳转和重复批准。

#### Slice 7：Runtime + Expert

- Worker runner 与 handlers 真正拆分；
- claim/heartbeat/complete 等内部 API 隔离；
- Visual Lab typed inspector 和 adoption；
- 完成 runtime 深层能力迁移，并删除 Slice 1 之后仍残留的 Models/Comfy/Jobs/Diagnostics legacy 实现；不再重做第二套 System 页面；
- G6/G7/G8/G9 等工程门禁移出正式产品和 public OpenAPI。

#### Slice 8：遗留清除与新安装基线

- 删除 redirect 到期后的旧路由；
- 删除无消费者的 legacy application services；
- 归档并移除旧 projection/泛型表；
- 生成迁移手册、回滚演练和新安装 baseline。

每次切换都遵守依赖门：`新 owner 最小可用 → 真实快照等价/不变量验证 → canonical route 切换 → 当期旧写入口删除`。Slice 8 只能清理已经只读归档或兼容 redirect，不能把有业务消费者的旧 service/projection 债务拖到最后才决定。

---

## 11. 测试、验收和可量化目标

### 11.1 产品与导航

- 一个用户任务只有一个 canonical route 和一个 owner。
- 完整创作上下文默认可见导航不超过 8 项。
- Command Palette 不维护第二份静态导航表。
- 首页同一目标不在清单、卡片和分集行重复出现。
- Quick、Professional、Lab 可互相进入同一正式项目事实，不复制数据。
- 普通模式不出现 G6/G8/G9、provider internals、lease、UUID 或 raw JSON。

### 11.2 页面交互

- 每个状态只有一个视觉主 CTA。
- loading、empty、error、blocked、stale、conflict、offline 都有 typed 状态和原地恢复。
- 所有持久化编辑表单有 dirty、保存中、成功、冲突和离开保护。
- coarse pointer/触屏主要交互目标至少 44×44；WCAG 2.2 AA；完整键盘和焦点回归。
- 不使用被 CSP 阻止的 data URI 图像；图标走受控 SVG/component 管线。
- `>=1024` 支持完整专业编辑；小屏至少无裁切、可浏览、可处理异常和批准。

### 11.3 架构

- 最终 Slice 8：Public route 直接依赖具体 Database 为 0；Application 内直接构造另一 application service 为 0；Domain/application 依赖 infrastructure 为 0；Worker 业务逻辑回流巨型 runner 为 0。
- 过渡期有机器可读 legacy debt manifest；违规总数不得增长，每个 slice 对其触达模块清零并删除相应豁免。
- 新 application/handler 单文件建议上限 500–700 行；超出需 ADR。
- public v2 请求/响应 schema 覆盖率：100%。
- TS API client 手工 DTO/手工 endpoint 模板：0。
- Shell 初始业务查询：1 个 AppContext；页面初始聚合查询不超过 3 个。

### 11.4 数据

- `foreign_key_check` 为空；
- 数据库行数和媒体 hash manifest 无意外差异；
- revision、variant、decision、job、attempt、artifact 不被覆盖；
- 旧库 current selected/approved head 被保留为 cutover 初始权威；切换后的 SET/CLEAR/APPROVE/REVOKE stream 可严格重建 head；
- 无法确定的迁移数据进入可见 quarantine；
- Lab adoption 后目标 Shot Studio 可见候选和完整 lineage；
- shot revision 与 review/timeline freshness 在同一 UoW 或可靠 outbox 中收敛。

### 11.5 可靠性

- 每种 Job handler 在 claim、执行、产物登记、完成前后崩溃都可恢复。
- 重放相同 idempotency key 返回相同 run/job。
- Run 不能在底层 Job 未成功时宣布成功。
- 浏览器关闭不影响已被 Worker 接管的任务；没有 Worker 时 UI 诚实阻塞。
- 构建产物通过新目录构建 + 原子目录切换发布，避免 live server 在 `dist` 清空与新文件写入之间返回 500/404 或旧 chunk。
- webhook、projection 和 SSE 使用独立 consumer offset；一个消费者确认不能导致其他消费者丢事件，过期 cursor 可按合同恢复。

### 11.6 性能

本地正常数据量建议门槛。基准机至少记录 CPU、内存、GPU/显存、磁盘类型、OS/电源模式、数据库大小与版本；使用 empty、62-shot rich-state、500-shot stress 三套 fixture。冷热缓存分别测量，每个指标至少 30 次且报告 p50/p95/max；计时边界从 HTTP request 发出到 schema 校验完成，不含模型生成时间：

- App Context p95 < 150 ms；
- Shot Studio p95 < 250 ms；
- 75 行 Production rows p95 < 300 ms；
- 单个聚合 payload < 500 KB；
- 导航链路同步 SHA256/全目录扫描次数为 0；
- 100+ 镜头 rail 必须虚拟化；
- SSE 正常时固定周期全局轮询为 0。

### 11.7 必须自动化的端到端链路

1. Quick：一句话/剧本 → 制作计划 → 项目/镜头 → 候选 → 工作采用 → 人工批准。
2. Professional：故事 → 资产 → 策划 → Shot Studio → Production → Post → Delivery。
3. Expert：Visual Lab → 预检 → 采纳 → Shot Studio 可见候选与 lineage。
4. Recovery：Worker 在每个关键阶段中断、重启、重放。
5. Migration：真实旧数据库快照 → 离线迁移 → 验证 → v2 全链路 → 完整恢复演练。
6. Route：每个 legacy URL 到唯一 canonical URL；无未消费 query；搜索结果精确定位实体。
7. Visual：所有目标 route 在 1440、1024、768、414、375、320 宽度下的截图和交互矩阵；Shell、Shot Studio、Production、Post 另在 1600/1366 做宽屏回归。
8. Rich state：候选/A-B/批注、失败与重试、offline、并发冲突、stale、quarantine、恢复集和 cursor-too-old，不只验证空页面或 route 能打开。

---

## 12. 明确不新增的范围

本轮不建设：

- 社区、作品广场、公开 Skill 市场；
- 投流、带货、营销模板和内容分发平台；
- 完整 Premiere/DaVinci/剪映级通用编辑器；
- 没有身份、权限和审计基础的云团队协作；
- 为每个新模型、滤镜、单点 AI 能力建立新页面；
- 为了追求“云原生”拆微服务；
- 自动替代人工批准、时间线冻结或交付验收。

可以新增且已经进入目标设计的能力只有：

- 可编辑的快速成片计划；
- 唯一 Next Action / blocker projection；
- typed Series Bible 与连续性记忆；
- Shot Studio；
- 单一 Episode Production projection 和异常队列；
- 自动 DRAFT 初剪；
- 本地制作方案包；
- 任务状态托盘与 SSE；
- typed Visual Lab adoption。

---

## 13. 删除清单与完成定义

### 13.1 页面/入口删除

- `ProjectOperationsPage` 与 `/operations`；
- 独立 `ProductionCockpitPage`；
- 旧 `GenerationPage/GenerationWorkbench` 页面形态；
- Director 与 Generation 的重复 route；
- 独立 QC 和 Director Recipe 一级 route；
- 独立 `/lab` MediaLab；
- legacy `/canvas`；
- WorkspaceTabs；
- Command Palette 的 22 条静态导航副本；
- Review 的 delivery 空壳 tab；
- Timeline 的 compose 跳转 Drawer；
- Story CreativeLibrary 的重复资产/声音写入口。

### 13.2 后端删除或替换

- Run/Cockpit/Grid/Freshness 相互竞争的读模型；
- Job purpose/type 的长期阶段猜测；
- One Sentence bespoke execution state machine；
- Visual Lab 不闭环的 selection-only promotion；
- 巨型 worker 中的业务 handler；
- application service 内部构造其他 service；
- public worker lease 控制接口；
- public 产品中的 G 系列门禁 API；
- 手写“generated client”模板。

### 13.3 总体完成定义

只有同时满足以下条件，才能称为“重构完成”：

1. 新用户从输入故事到获得可审阅初剪，不需要进入 System Center。
2. 专业用户能在 Shot Studio 完成意图、参考、生成、比较和工作采用，不跨 Director/Generation。
3. 整集状态只有一个 Production workspace 和一个 projection。
4. Review 是内容批准唯一 owner；Delivery 不重复内容批准。
5. Story 与 Assets 不再维护同类正式资产。
6. 旧页面、旧写 API、旧投影和旧状态表已实际删除或只读归档，不只是隐藏菜单。
7. Quick、Professional、Lab 共享同一事实和任务系统。
8. 真实旧项目迁移后 revision、审核、Job、媒体 hash、Timeline 和 Delivery lineage 无损。

---

## 14. 关键代码证据索引

- 路由：`apps/web/src/app/router.tsx`
- 路由注册与 URL builder：`apps/web/src/app/routeRegistry.ts`
- 22 项侧栏、顶部选择器与 Shell queries：`apps/web/src/layouts/AppShell.tsx`
- WorkspaceTabs：`apps/web/src/features/workspace-tabs/`
- 项目首页重复入口：`apps/web/src/pages/ProjectHomePage.tsx`
- Story/Creative Library：`apps/web/src/features/projects/CreativeLibrary.tsx`
- Asset Bible：`apps/web/src/pages/AssetBiblePage.tsx`
- Director：`apps/web/src/pages/DirectorDeskPage.tsx`
- Generation：`apps/web/src/features/generation/GenerationWorkbench.tsx`
- Review：`apps/web/src/features/reviews/ReviewInboxPanel.tsx`
- Run/Cockpit：`apps/web/src/pages/EpisodeRunPage.tsx`、`apps/web/src/features/production-cockpit/`
- Project Operations：`apps/web/src/pages/ProjectOperationsPage.tsx`
- Visual Lab：`apps/web/src/features/visual-lab/`
- API routes：`apps/api/local_drama/api/routes/`
- 巨型 application services：`apps/api/local_drama/application/`
- Creative Entry / subtitle style：`apps/api/local_drama/application/subtitle_styles.py`
- One Sentence Run：`apps/api/local_drama/application/one_sentence_video_runs.py`
- Visual Lab promotion：`apps/api/local_drama/application/visual_labs.py`
- Worker：`apps/api/local_drama/application/worker.py`
- 当前迁移 head：`apps/api/alembic/versions/0060_visual_lab_runtime_foundation.py`

---

## 15. 最终判断

LocalDramaStudio 应舍弃的不是专业能力，而是**重复暴露专业能力的页面、菜单和状态解释**。应新增的也不是更多孤立模块，而是快速入口、唯一工作台、异常队列和清晰的领域 owner。

重构的正确边界是：

> 保留可追溯的生产事实，重写面向创作者的产品；保留一个模块化单体，删除重复服务；保留人工权威，把系统复杂性压到需要它的时刻和位置。

这份设计实施完成后，产品应从“功能模块集合”变成真正的“AI 系列短剧制片台”。
