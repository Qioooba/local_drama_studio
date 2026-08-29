# LocalDramaStudio 长篇小说改编规划重构设计与开发实施方案

> 状态：实施设计 v1.0  
> 日期：2026-08-29  
> 适用对象：产品、UX、前端、后端、AI/Prompt、测试、迁移与发布负责人  
> 代码基线：2026-08-29 当前工作树；工作树含用户未提交改动，本方案不覆盖或整理这些改动  
> 文档关系：本文件是 docs/plan/local-drama-studio-full-product-rewrite-design-2026-08-26.md 在“故事 / 原稿 / 改编规划 / 分集拆解”垂直切片上的实施深化；ADR-0102 与 ADR-0103 继续生效  
> 核心结论：这不是“把第 1 集改成第 1 季”的界面修改，而是将原稿导入、整剧规划、项目结构、单集拆解和审核重建为五个边界清晰、可追溯的阶段

---

## 0. 一页决策

### 0.1 当前设计为什么不成立

现有页面隐含了一个错误等式：

    一份上传正文 = 某一个现有分集的输入

这对一个已经明确属于某集的短剧本成立，但对整本小说、若干卷、连续更新章节或多集剧本都不成立。当前界面又自动回退到第一季度、第一集，所以一个 740 段左右的长篇会被描述成“为第 1 集生成拆解草稿”。这不是文案问题，而是请求协议、数据模型和页面职责共同造成的领域错误。

本次确定改为：

    不可变原稿版本
      → 原稿诊断
      → 可审核的改编规划
      → 可选季分组 + 必备故事弧 + 稳定分集计划
      → 审核后发布/映射项目结构
      → 选集批量生成单集拆解草稿
      → 人工审核并应用到生产实体

### 0.2 最终产品决策

1. 新增独立的 **改编规划 Adaptation Planning** 领域，不在 ScriptImportPanel 内继续堆条件分支。
2. 首屏不再询问“这段正文准备做成哪一集”，而是先诊断原稿，再让用户选择：
   - 规划一部连续剧；
   - 续接已有改编规划；
   - 精拆单集。
3. **故事弧是叙事必备层，季度是可选发行分组。** AI 可以建议季边界，但不能强迫上传三章的用户先创建一季，也不能把整本书默认塞进第一季。
4. 长篇规划不要求项目预先存在季度或分集；批准后才批量创建或映射真实项目结构。
5. 单集精拆保留，但必须由用户显式选择目标集；永不默认第一集。
6. 原稿、规划、分集草稿、真实项目结构分别版本化。AI 只生成派生草稿，绝不覆盖原稿或已审核生产内容。
7. 长文本采用分层分析与递归汇总，不用一个超长提示词“一次拆 100 集”。
8. 一个面向创作者的规划运行可以包含多个持久 Job；Job 依赖 DAG 负责 fan-out/fan-in，AdaptationPlanRun 只保存领域进度与节点引用，不新建第二套队列。现有通用 Automation Workflow 可以调用“开始规划/批量拆解”公开命令，但不拥有规划节点或来源覆盖事实。
9. 激活仓库已有但尚未真正使用的 LLM_EPISODE_PLAN 能力；LLM_STORY_PARSE 负责长文事实抽取，LLM_STORYBOARD 逐步接管单集场镜规划。旧 SCRIPT_BREAKDOWN_LOCAL_LLM 在迁移期复用同一新核心，不长期保留第二套执行代码。
10. 新链路验收后，在同一切换里程碑删除旧写入口；不长期双写、不用 V3 外壳包住旧页面、不用 CSS 遮盖旧流程。

### 0.3 对当前真实样本的正确表现

仓库根目录样本“照骨灯_凡人修仙原创长篇_约200分钟.txt”经现有解析链路约为：

- 29,855 个 Unicode 字符；
- 740 个解析段落；
- 42 个章节；
- 原稿自述约 190–220 分钟；
- 当前项目已有 100 集，单集目标 120 秒。

新页面应将它诊断为长篇连续内容，建议约 95–110 集的规划范围，并展示故事弧和可选季候选；用户批准后，系统才预览“建议 100 个计划分集如何映射现有 100 个项目分集”。第一轮应建议先生成 1–3 集样片或前 10 集拆解草稿，而不是显示“为第 1 集生成”。

---

## 1. 代码现状与根因审计

### 1.1 前端现状

apps/web/src/features/projects/ScriptImportPanel.tsx 当前约 723 行，同时承担：

- 文件上传、解析与预览；
- 章节/段落范围选择；
- 导入确认；
- 模型 Profile 选择；
- 季度与分集选择；
- 单集拆解提交；
- Job 轮询、取消、重试、删除；
- 跳转草稿审核。

其中：

- 第 67–74 行附近把未选择的季度/分集自动退化为列表第一项；
- 第 213–239 行附近的提交逻辑强制依赖 effectiveEpisodeId；
- 第 610 行附近直接询问“这段正文准备做成哪一集？”；
- 第 675 行附近用是否存在目标集决定按钮可用性。

apps/web/src/pages/StoryWorkspacePage.tsx 目前使用 hash 在导入、审核、资产、故事圣经间切换，不能给原稿、规划、计划分集、批次或草稿提供稳定深链。

apps/web/src/features/projects/AIDraftReviewPanel.tsx 又一次默认选择第一季和第一集，并将全部项目草稿平铺成卡片。它不适合数十到数百集的审核队列。

### 1.2 后端现状

当前接口：

    POST /api/v1/import-sessions/{session_id}:request-breakdown

BreakdownRequest 在协议上只能接收一个 episode_id。application/local_llm.py 又通过：

    _BREAKDOWN_MAX_SOURCE_CHARACTERS = 4_000

把一次输入限制为 4,000 字，并创建 SCRIPT_BREAKDOWN_LOCAL_LLM Job。这个上限适合“单集精拆”的保护，不适合整本小说规划。

当前服务已经具备值得保留的事实：

- DocumentImportService 保存不可变来源、原始文件、内容哈希和 Unicode 偏移；
- 大文档正文读取已经是 bounded preview / passage，而不是整本回传；
- 现有测试可以处理 200,000 个中文字符并验证来源哈希与偏移；
- Job 已有持久化、幂等键、依赖、租约、心跳、恢复和 Outbox；
- Automation Workflow 已能表达有限、可审计的编排与人工门；
- 领域能力枚举已经包含 LLM_EPISODE_PLAN、LLM_STORY_PARSE、LLM_STORYBOARD；
- 生产阶段已有 STORY_ANALYSIS 与 SHOT_PLANNING 的语义。

因此正确做法是新增正确的领域切片并接入这些基础设施，而不是推翻可靠底座。

后端深审还确认了七个必须在批量拆解前解决的阻塞项：

1. import session 已提交的 SOURCE_BODY_RANGE 没有被 enqueue_breakdown 稳定读取，默认路径仍可能取全文；
2. /import-sessions/{id}:request-breakdown 与 /import-sessions/{id}:breakdown-local-llm 是两个重复入口，后者忽略段落范围；
3. source_document_versions 当前缺少真正的“同一逻辑文档新增版本”应用 API，新增内容通常创建新 document 且 version_no 仍为 1；
4. episode_scene_ranges 只有裸偏移，没有 source_document_version_id；
5. breakdown_apply 使用项目级 SC01/SC02 编号，第二集再次从 SC01 开始可能触发唯一键冲突；
6. 缺少来源证据时会退化为 (0,1) 假偏移；
7. 本地 LLM 客户端丢弃 token usage、finish reason 和 provider request id，无法准确核算、审计或判断截断。

这些不是“后续优化”，而是 M2/M4/M5 的退出门：新写入禁止假来源，场次编号必须跨集安全，重复接口必须统一，模型调用必须保留执行元数据。

### 1.3 根因

| 表象 | 根因 |
|---|---|
| 整本书仍显示“第 1 集” | 导入会话与单集拆解请求被绑定成同一个用例 |
| 季/集长下拉挤成一行 | 用生产实体选择器代替了改编规划 |
| 4,000 字上限与整书冲突 | 单集模型窗口被误当成长篇规划容量 |
| 一个长进度条无法表示百集 | 页面直接观察 Job，没有创作语义的运行/批次聚合 |
| 重拆风险高 | 规划修订、草稿修订与真实生产结构没有明确隔离 |
| 无法安全扩成整季 | 当前 episode_id 是请求前置条件，而整剧规划本应先于分集实体 |

---

## 2. 线上竞品与研究结论

### 2.1 已核验行业做法

本轮优先采用官方产品页、官方帮助文档、官方公司资料和论文；登录后不可见能力或营销宣传未当成确定事实。

| 产品/资料 | 已核验做法 | 本项目借鉴 | 明确不照搬 |
|---|---|---|---|
| [小云雀](https://xyq.jianying.com/) | 官方公开“10 万字剧本解析”，强调长剧本理解与多集生产 | 全局理解、跨集角色状态、一次分析多集使用 | 黑盒式一键批准、把宣传上限当单提示词能力 |
| [小蓝梯 AI 分镜指南](https://manju.xiaolanti.cn/help-storyboard.html) | 小说/大纲/剧本先拆集，展示全部分集大纲后再校验和分镜；建议先做前 10–30 集 | 先审核分集规划，再进入昂贵生成；先样片后批量 | 全题材硬编码“3 秒钩子”；用营销规则代替可配置策略 |
| [DramaFlow 2.0 官方指南](https://www.ctyun.cn/document/11057595/11097192) | 分集拆解→主体提取→分镜；AI 估算或指定集数；长文可批量追加；项目级主体复用 | 原文只读、稳定内部分集 ID、追加式规划、生成前预估 | 重新拆解覆盖分镜并清除下游资产 |
| [万兴剧厂官方资料](https://www.wondershare.cn/new/details/id/1195.html) | 公开百万字符级小说改编、角色资产、整集分镜、后剪与团队协同 | 文本、资产、镜头、后剪是独立产物与阶段 | 把云端团队/积分模型硬塞进本地单机核心 |
| [DramaBuddy](https://aicomic.yuewen.com/) | 小说、剧本、分镜多入口；长网文理解；主体提取与批量生成 | 原稿理解是独立能力，多状态角色和镜头级审核 | 假设任意用户原稿都拥有平台自有 IP 知识 |
| [迅漫工厂](https://www.beiyinbook.com/) | 小说转剧本→资产锁定→批量分镜→接入即梦 | 生成服务商属于下游 Provider | 用即梦的数据结构反向定义本项目领域 |
| [Dreamina Storyboard](https://dreamina.capcut.com/create/storyboard-ai) | 脚本到故事板、多帧与参考素材 | 作为镜头/画面生产工具 | 把故事板工具误认为长篇分季分集规划器 |
| [Toonany](https://github.com/casperkwok/toonany) | 小说→故事线→分集大纲→资产→风格样张→剧本→分镜→视频；有依赖追踪 | 上游变更传播、风格样片门、本地可回溯产物 | 直接复制文件夹协议或单模型假设 |

线上产品共同收敛为：

    长篇内容理解
      → 分集规划
      → 人工校正
      → 项目级主体/资产
      → 逐集分镜
      → 批量生成与失败恢复

没有可靠公开证据支持“所有长篇都必须先自动分季”。多数产品的公开层级仍是项目/剧→集→镜头。因此本方案把 Story Arc 设为叙事必备，把 Season 设为可选发布分组。

### 2.2 长上下文为什么不能只扩大窗口

[Lost in the Middle（TACL 2024）](https://aclanthology.org/2024.tacl-1.9/) 表明，即使模型宣称支持长上下文，当关键信息位于长输入中段时，利用效果仍可能显著下降。[RAPTOR（ICLR 2024）](https://openreview.net/pdf?id=GN921JHCRw) 说明递归摘要树可以在不同抽象层级组织长文档信息。

本项目据此采用以下工程推断：

- 大窗口是容量能力，不是稳定理解保证；
- 原稿必须先形成可复用的结构化分析节点；
- 分集规划同时读取局部证据、故事弧摘要和全局 Canon，而不是反复发送整本书；
- 每个结论必须保留可点击的原文证据范围，不能只保存自由文本摘要。

---

## 3. 产品原则与非目标

### 3.1 不可违反的原则

1. **原稿不可变**：解析、规划、拆解、审核都是派生事实。
2. **先规划、后生产**：规划阶段不创建镜头、不消耗视觉模型。
3. **先审后落库**：未批准规划不得静默创建或改写季度/分集。
4. **故事弧必备，季可选**：叙事结构与发行结构分离。
5. **稳定 ID 与显示序号分离**：重排“第几集”不改变逻辑分集身份。
6. **证据优先**：每集边界、摘要、人物状态和伏笔必须可回到来源版本与 Unicode/段落偏移。
7. **部分成功可用**：长任务失败不能丢弃已完成分析节点或已成功分集草稿。
8. **运行事实唯一**：Jobs/Job dependencies 是执行与 DAG 事实；AdaptationPlanRun/RunNode 是带业务含义的节点与进度快照，不 claim、不 lease、不重试；通用 Automation 只能调用公开命令。
9. **Provider 可替换**：即梦、Seedance、可灵、本地 LLM 都不能进入核心领域模型。
10. **影响可预览**：上游变化先计算失效范围，再由用户创建新修订或重跑受影响节点。

### 3.2 非目标

- 不拆微服务，不引入 Redis/Celery/PostgreSQL；
- 不做全量 Event Sourcing；
- 不在本切片重写整个项目、资产、镜头或交付域；
- 不把“自动生成最终季数”作为硬承诺；
- 不保证任意长度全文一次无人工干预出成片；
- 不在普通 UI 暴露 Prompt、上下文窗口或供应商私有参数；
- 不用一个无限画布取代长篇规划表；画布更适合资产关系或镜头精修。

---

## 4. 目标用户旅程

### 4.1 入口一：规划一部连续剧

适用：整本书、多章小说、多集剧本、尚未建立项目分集结构。

    上传/选择原稿版本
      → 原稿诊断
      → 选择全文/章节/段落范围
      → 设置表现形式、单集时长、集数策略、季策略、节奏策略
      → 预检模型、隐私、预计任务和成本
      → 生成改编规划草稿
      → 审核故事弧、分集边界、摘要、钩子、时长和覆盖
      → 批准
      → 预览并发布/映射项目结构

### 4.2 入口二：续接已有改编规划

适用：只上传前几章、按卷生产、连载追加。

- 选择已有规划与新来源范围；
- 展示已批准边界、最后一集结束状态、未回收伏笔与角色状态；
- 只为新增范围创建新分析节点和计划分集；
- 已发布逻辑分集 ID 与序号不得重写；
- 如果需要在中间插集，使用稳定 ID + 显示序号重排，并明确下游影响；
- 新内容与历史 Canon 冲突时形成 blocker，不能静默改写既有事实。

### 4.3 入口三：精拆单集

适用：一段明确属于某集且符合当前模型容量的短文本。

- 用户显式选择来源范围和目标集；
- UI 不提供默认目标；
- 4,000 字可继续作为当前默认 Profile 的保守保护值，但必须由运行时上下文预算计算替代全局常量；
- 超限时提供“转为连续剧规划”“按建议拆成 N 集”“缩小范围”，而不只报错；
- 输出仍是待审核分集草稿，不覆盖目标集。

### 4.4 系统诊断，但不替用户决定

系统先进行低成本确定性诊断，必要时再用 LLM 补充：

| 诊断项 | 展示 |
|---|---|
| 内容类型 | 长篇小说 / 小说节选 / 多集剧本 / 单集剧本 / 大纲 / 未知 |
| 规模 | 字符、段落、章节、卷、可用正文比例 |
| 结构 | 章节完整性、标题模式、对话密度、场景切换密度 |
| 建议 | 推荐入口、预计集数范围、是否按卷推进 |
| 风险 | 目录/前言占比、章节缺口、解析置信度、远程发送范围 |

“推荐”只能是说明和默认聚焦，不能自动提交、自动创建季或自动选第一集。

---

## 5. 信息架构与路由

### 5.1 故事工作区

故事从 hash 流程改成可长期返回的嵌套路由工作区：

    /projects/:projectId/story
      → /story/sources

    /story/sources
    /story/sources/:sourceVersionId

    /story/plans
    /story/plans/new
    /story/plans/:planId
    /story/plans/:planId/episodes/:logicalEpisodeId
    /story/plans/:planId/runs/:runId

    /story/drafts
    /story/drafts/:draftId

    /story/assets
    /story/bible

旧 /story#story-import、#story-review、#story-assets、#story-bible 仅做一次兼容重定向，不再承载写入状态。

### 5.2 页面所有权

| 页面 | 唯一职责 | 不负责 |
|---|---|---|
| 原稿库 | 文件版本、解析状态、章节结构、来源查看 | 选择目标集、调用分镜 |
| 改编规划向导 | 规划范围与约束、预检、提交 | 编辑真实季度/分集 |
| 规划审核工作台 | 故事弧、季候选、计划分集与证据审核 | 直接创建镜头 |
| 发布结构预览 | 创建/复用/冲突的季度分集事务预览 | 自动覆盖已有内容 |
| 批量拆解页 | 从已批准计划分集创建批次 | 复制 Job 状态机 |
| 草稿审核收件箱 | 筛选、排序、进入待审项 | 内嵌全部富编辑器 |
| 草稿审核详情 | 场、镜、对白、证据、应用预览 | 静默改投其他分集 |

---

## 6. 核心交互设计

### 6.1 原稿详情首屏

    照骨灯 · 原稿版本 v1                         [创建改编规划]

    29,855 字符 · 740 段 · 42 章 · 索引完成
    识别：长篇连续小说
    建议：按 120 秒/集规划约 95–110 集；先审核整剧规划

    这份原稿准备如何处理？

    ○ 规划一部连续剧（推荐）
      先生成故事弧和分集规划，不直接生成镜头

    ○ 续接已有改编规划
      把后续章节接到已批准规划

    ○ 精拆单集
      仅适合已经明确属于一集的短文本

选项必须使用 fieldset/legend/radio，不能使用只有鼠标可点的 div 卡片。

### 6.2 长篇规划向导

四步全页向导，不使用拥挤 Modal：

1. **来源范围**：全文、连续章节、连续段落、多个有序区间。
2. **规模策略**：
   - AI 估算集数；
   - 指定总集数；
   - 指定本批规划集数；
   - 单集目标时长和允许容差。
3. **改编策略**：
   - 剧情漫 / 解说漫 / 仿真人短剧；
   - 压缩强度；
   - 章节边界偏好；
   - 冲突、信息和钩子密度；
   - 季策略：暂不分季 / AI 建议 / 指定季数 / 沿用现有季。
4. **预检**：
   - 原稿覆盖范围；
   - 预计分块与 Job 数；
   - 使用的 LLM_EPISODE_PLAN Profile；
   - 本地或远程、预计发送字符/Token；
   - 预计文本成本；
   - 主按钮“生成改编规划草稿”。

普通用户只看到模型名称和隐私/成本结论；高级设置才允许切换已发布 Profile，不暴露运行时私有参数。

### 6.3 规划审核工作台

桌面 ≥1280px 使用三栏：

    ┌ 故事弧/季树 240px ┐ ┌ 虚拟化分集表 ─────────────┐ ┌ 检查器 360px ┐
    │ 故事弧 A           │ │ 01 段1–8   118s 夜雨入城 │ │ 原文证据    │
    │  第 1 季候选       │ │ 02 段9–16  122s 身份暴露 │ │ 摘要/节拍   │
    │   EP01 ✓           │ │ 03 段17–24 120s ...      │ │ 承接/钩子   │
    │   EP02 !           │ │                            │ │ 校验问题    │
    └────────────────────┘ └────────────────────────────┘ └─────────────┘

顶部只保留一个主 CTA，随状态变化：

- 生成中：查看运行；
- 待审核：提交审核；
- 校验通过：批准规划；
- 已批准未发布：发布到项目结构；
- 已发布：生成分集拆解。

分集行字段：

| 字段 | 目的 |
|---|---|
| 故事弧 / 季候选 | 全局结构 |
| 显示序号 | 可重排标签，不是 ID |
| 原稿范围 | 点击打开只读证据 |
| 本集标题与主线 | 改编意图 |
| 核心冲突/回报 | 节奏审核 |
| 开场承接 | 与上一集连续 |
| 结尾钩子 | 追更承诺 |
| 字数/时长/镜头估算 | 可生产性 |
| 新增或变更 Canon | 一致性影响 |
| 校验/审核状态 | 下一动作 |

操作：

- 拆为两集、合并相邻集；
- 上移、下移、移动到故事弧/季；
- 调整来源边界；
- 添加/删除季候选边界；
- 锁定选中集，重新规划未锁定范围；
- 比较修订；
- 查看遗漏、重叠和重复覆盖；
- 只为选中集创建单集拆解批次。

拖拽只作为快捷方式，必须同时提供键盘按钮。

### 6.4 批量拆解

从已批准/已发布规划进入：

- 按故事弧、季、状态、是否已有草稿、是否过期筛选；
- 明确区分“选择当前页”和“选择所有匹配结果”；
- 预检未映射分集、Profile 不可用、上下文超限、预算和已有内容；
- 创建一个领域运行/批次，由多个 durable Job 执行；
- 显示排队、运行、待审核、失败、取消数量；
- 成功项可边生成边审核，失败项可独立重试；
- 支持暂停后续、取消未开始项、断点续跑；
- 全局任务中心仍是 Job 事实入口，批次页只提供创作语义聚合。

### 6.5 分集草稿审核

拆成“审核收件箱 + 详情工作台”：

- 左侧场次导航；
- 中央镜头/对白编辑；
- 右侧原文证据、时长、覆盖、阻塞项；
- 底部固定：保存修订、标记审核完成、应用预览、确认应用。

来自规划的 target episode 映射默认只读。改投其他分集必须执行“重新映射”，并预览目标已有内容、目标时长合同和是否需要重新生成。

### 6.6 状态、错误与可访问性

- 错误统一写成“原因 + 影响 + 下一步”；
- 一个页面只用一个 role=alert 汇总，频繁 Job 更新不重复打断读屏；
- 所有状态使用中文文字和图标，不依赖颜色；
- 交互目标最小 44px；
- 路由切换后焦点进入主标题；
- Dialog/Drawer 锁焦、Esc 关闭并恢复触发点；
- 支持 prefers-reduced-motion；
- 桌面正文不低于 14px，移动编辑字段不低于 16px；
- 375、768、1024、1440 四档必须进入 E2E。

### 6.7 大列表与性能

- 原稿正文继续服务端游标/窗口读取；
- 章节、计划分集、草稿收件箱均为 bounded/cursor API；
- 超过 50 个分集使用 @tanstack/react-virtual；
- DOM 中维持约 30–60 个可见行，不创建 1000 个富编辑器；
- 复杂编辑放右侧 Inspector，虚拟行保持固定折叠高度；
- 活跃运行优先用项目事件失效，轮询仅兜底，终态停止；
- 输入反馈目标 <100ms，滚动主线程目标 <16ms/帧；
- 返回列表恢复筛选、选中项与滚动位置。

---

## 7. 领域模型

### 7.1 聚合关系

    SourceDocument
      └─ SourceVersion（不可变）
          ├─ SourceUnit / SourceStructure / Chapter
          ├─ SourceSpan
          └─ AnalysisNode（可重建缓存）

    AdaptationPlan
      └─ AdaptationPlanRevision（不可变快照）
          ├─ StoryArc（必备）
          ├─ SeasonGroup（可选）
          └─ PlannedEpisode
              ├─ stable logical_episode_id
              ├─ display_ordinal
              ├─ SourceSpan[]
              ├─ continuity / hook / duration contract
              └─ evidence / confidence

    AdaptationPlanRun（领域进度）
      └─ AdaptationPlanRunNode
          └─ job_id / output hash / LLM invocation

    PlanMaterialization
      └─ logical_episode_id ↔ real season_id / episode_id

    BreakdownBatch
      └─ BreakdownDraft（按计划分集/真实分集）
          └─ HumanRevision
              └─ Atomic Apply → Scene / Shot / Dialogue facts

### 7.2 核心实体

#### AdaptationPlan

- id、project_id、source_version_id；
- mode：COMPLETE_WORK / SERIAL_INCREMENTAL / PRESEGMENTED_SCRIPT / SINGLE_EPISODE；
- current_revision_id；
- artifact_status；
- created_by、created_at、archived_at。

#### AdaptationPlanRun / RunNode

- Run 冻结 plan、source scope、constraints、Profile/Workflow/Runtime 和合同 hash；
- RunNode 表达 CHUNK_MAP / ARC_REDUCE / SEASON_PLAN / EPISODE_BOUNDARY / VALIDATE；
- Node 保存 core source range、context range、input fingerprint、job_id、output hash 和质量标记；
- Run/Node 的状态是领域进度快照，不执行 claim、lease、heartbeat 或 retry；
- Worker 只通过现有 Job 完成节点；Node 结果先幂等落库，再让 Job 成功；
- 页面聚合 Node/Job 形成进度，不能凭空伪造百分比。

#### AdaptationPlanRevision

- id、plan_id、revision_no、parent_revision_id；
- source_scope_json；
- constraints_json；
- analysis_contract_version；
- prompt_schema_version；
- profile_version_id、runtime_contract_snapshot；
- content_hash；
- validation_summary_json；
- created_reason：AI_GENERATED / HUMAN_EDIT / REGENERATED / REBASED；
- expected_revision / ETag。

#### StoryArc

- stable logical_arc_id；
- title、summary、dramatic_promise；
- source spans；
- ordinal；
- optional season_group_id；
- beginning_state、ending_state、open_threads。

#### SeasonGroup

- stable logical_season_id；
- title、ordinal、release_intent；
- source-derived=false/true；
- 只是发布/组织分组，不替代 StoryArc。

#### PlannedEpisode

- stable logical_episode_id；
- display_ordinal；
- arc_id、optional season_group_id；
- title、logline、opening_carry、core_conflict、payoff、ending_hook；
- primary_source_start/end paragraph + Unicode offsets；
- additional source spans；
- target_duration_ms、duration_tolerance；
- estimated_dialogue_chars、estimated_narration_chars、estimated_shot_count；
- character/location/prop state deltas；
- evidence_json、confidence；
- review_state、lock_state。

### 7.3 状态分离

规划产物状态只表达人工/业务生命周期：

    DRAFT → IN_REVIEW → APPROVED → MATERIALIZED
       └──────────────→ SUPERSEDED / ARCHIVED

“正在生成”不进入产物状态机，由关联 run/job 投影表达。这样刷新、失败或重试不会污染规划本身。

Job 状态继续使用现有执行状态：

    QUEUED → RUNNING → SUCCEEDED / FAILED / CANCELLED

通用 Automation Workflow 不承载内部 chunk/reduce DAG。它可以在更高层调用“开始规划”，等待领域事件后再进入 HITL 或后续公开命令；不能直接写计划表、创建 RunNode 或拥有覆盖校验。

### 7.4 不变量

1. SourceVersion 一经提交不可修改。
2. 已批准 Revision 不原地修改；任何编辑产生新 Revision。
3. logical_episode_id 在重排、改季时不变。
4. display_ordinal 在同一 Revision/分组内唯一且连续。
5. 主要来源范围按原稿顺序单调，遗漏/重叠必须显式标注。
6. 一个 MATERIALIZED Revision 的映射必须可追到真实 episode_id。
7. 生产实体已有镜头/媒体/审核/时间线事实时，结构变化必须产生 blocker。
8. 不允许 AI Job 直接写生产 Scene/Shot；只能写草稿或分析节点。

---

## 8. 长文本理解与规划流水线

### 8.1 流水线

    0. 确定性摄取与结构识别
       ↓
    1. 分块事件/人物状态/场景/伏笔抽取
       ↓
    2. 分层归并与全局故事图
       ↓
    3. 故事弧与可选季候选
       ↓
    4. 分集边界与时长规划
       ↓
    5. 确定性全局校验
       ↓
    6. 人工审核与锁定
       ↓
    7. 逐集拆解（当前集 + 邻接集 + Canon）

### 8.2 分块策略

不能使用全局固定字符数。每次运行按 Profile 和 Runtime Contract 计算：

    usable_input_tokens
      = context_window
      - system_and_prompt_tokens
      - output_reserve
      - global_context_reserve
      - safety_margin

实现一个 TokenBudgetPort：

- 本地模型有 tokenizer 时使用真实 tokenizer；
- 不可用时用保守中文字符/Token 估算；
- chunk 边界优先卷、章、场景，再按段落；
- 保留 1–2 个段落的语义重叠，但主证据范围不重叠；
- 每块都保存 source hash、段落和 Unicode 偏移。

当前 4,000 字常量只可作为某个缺少 tokenizer 的默认 fallback，不得继续作为整个系统的硬上限。

### 8.3 AnalysisNode

每个可复用分析节点至少包含：

- node_id、source_version_id、source_span；
- node_type：CHUNK_EVENTS / CHAPTER_SUMMARY / ARC_SUMMARY / GLOBAL_CANON / TIMELINE；
- schema_version、prompt_contract_version；
- profile_version_id、runtime_contract_hash；
- input_hash、output_hash；
- structured_json；
- evidence spans；
- quality flags、completed_job_id；
- superseded_at。

缓存键：

    source_version_hash
    + source_span
    + node_type
    + analysis_contract_version
    + prompt_schema_version
    + profile/runtime contract

相同输入与合同复用已成功节点；“重试”继续同一快照，“重新生成”创建新 Revision/合同快照。

### 8.4 分块输出契约

版本化 JSON Schema，不接受自由 Markdown 作为权威结果。每块提取：

- events：发生事实、因果、时间、地点；
- character state deltas：身份、关系、能力、伤病、服装/年龄阶段；
- locations / props state；
- reveals、promises、foreshadowing、payoffs；
- conflict intensity、adaptation importance；
- candidate boundaries；
- exact evidence spans；
- uncertainty / ambiguity。

所有摘要事实必须携带 evidence。无法定位的模型结论只能作为低置信建议，不能进入批准前的 Canon。

### 8.5 全局规划

规划模型读取：

- 递归故事摘要树；
- 事件因果图与角色状态；
- 用户的表现形式、时长和集数/季策略；
- 已锁定故事弧/计划分集；
- 对连续追加模式读取最后批准状态；
- 必要的局部原文证据，而不是全文。

输出：

- StoryArc；
- 可选 SeasonGroup 候选；
- PlannedEpisode；
- 覆盖/遗漏说明；
- 全局风险与待人工决定项。

### 8.6 确定性校验

AI 生成后必须由代码校验：

- 主来源覆盖率 100%，或每个排除区间有原因；
- 主范围按顺序单调；
- 不允许非声明式空洞或重叠；
- SourceVersion/hash/offset 全部有效；
- logical ID 唯一、display ordinal 连续；
- 目标时长与容差；
- 每集摘要、冲突、承接、结尾承诺完整；
- 人物/道具状态不出现已知逆序；
- 季边界不能截断已锁定故事弧，除非用户显式确认；
- 现有项目结构映射冲突；
- 远程模型发送授权；
- Profile/Workflow 当前可执行。

钩子强度是可配置策略；大结局、单元剧或艺术表达可以显式豁免，不能把“3 秒钩子”硬编码为全局 invariant。

### 8.7 逐集拆解上下文

生成某集拆解草稿时只组合：

- 当前计划分集的主要原文和补充 spans；
- 当前集目标、时长合同、叙事节拍；
- 上一集 ending state；
- 下一集 opening promise / hook target；
- 与本集相关的角色、场景、道具状态；
- 全局风格和禁忌；
- 用户修订。

这样既保持连续性，也避免每集重复发送整本书。

---

## 9. 应用架构

### 9.1 保持模块化单体

遵守 ADR-0102：

- React Web；
- FastAPI API；
- Python Worker；
- SQLite WAL；
- 项目媒体目录；
- durable Jobs、lease/heartbeat、Outbox。

API 与 Worker 可分进程，但仍是一个本地部署单元。不存在为了长文本而拆服务的容量证据。

### 9.2 新垂直切片

遵守 ADR-0103，新代码从正确边界开始：

    apps/api/local_drama/domain/adaptation_planning.py
    apps/api/local_drama/application/commands/adaptation_plans.py
    apps/api/local_drama/application/queries/adaptation_plans.py
    apps/api/local_drama/application/ports/adaptation_plans.py
    apps/api/local_drama/application/ports/token_budget.py
    apps/api/local_drama/infrastructure/database/adaptation_plan_repository.py
    apps/api/local_drama/infrastructure/runtime/token_budget_adapter.py
    apps/api/local_drama/application/worker_handlers/adaptation_analysis.py
    apps/api/local_drama/application/worker_handlers/adaptation_plan.py
    apps/api/local_drama/api/schemas/adaptation_plans.py
    apps/api/local_drama/api/routes/adaptation_plans.py

Route 不直接访问 SQLite；application 只依赖 Port；repository 按聚合/用例划分，不为每条 SQL 建接口。

### 9.3 Capability 与模型路由

仓库已有：

- LLM_EPISODE_PLAN：分集策划；
- LLM_STORY_PARSE：长文事实、事件与 Canon 抽取；
- LLM_STORYBOARD：单集场次与镜头规划。

实施时：

1. generation_model_catalog.action_for_capability() 将 LLM_EPISODE_PLAN 映射到 TEXT_PLANNING；
2. 建立并发布默认 LLM_EPISODE_PLAN Profile/Workflow；
3. 项目偏好可以为 LLM_EPISODE_PLAN 选择默认路由；
4. 规划预检解析实际可执行 route，并冻结 profile_version/workflow_version/runtime contract；
5. 新的单集拆解合同逐步迁到 LLM_STORYBOARD；迁移期旧 SCRIPT_BREAKDOWN_LOCAL_LLM 可调用同一新 use case，但不得复制执行逻辑；
6. 三类 Capability 分别冻结输出 Schema、Prompt、输入/输出预算，即使底层是同一个模型也不能混用合同；
7. 普通 UI 不把 LLM_EPISODE_PLAN 和 Prompt 术语直接展示给非高级用户。

### 9.4 Jobs、Planning Coordinator 与 Automation

建议 Job 类型：

| Job | Stage | 作用 |
|---|---|---|
| STORY_CHUNK_ANALYSIS_LLM | STORY_ANALYSIS | 分块结构化抽取 |
| STORY_ARC_REDUCE_LLM | STORY_ANALYSIS | 分层归并与故事图 |
| ADAPTATION_SEASON_PLAN_LLM | SHOT_PLANNING | 故事弧与可选季候选 |
| ADAPTATION_EPISODE_BOUNDARY_LLM | SHOT_PLANNING | 分组规划分集边界 |
| ADAPTATION_PLAN_VALIDATE | SHOT_PLANNING | 确定性校验，可作为 CPU task |
| EPISODE_BREAKDOWN_LLM | SHOT_PLANNING | 新的单集拆解核心 |
| PROJECT_STRUCTURE_SYNC | SHOT_PLANNING | 幂等同步 DB 派生目录/project manifest |

用户看到一个 AdaptationPlanRun。AdaptationPlanningCoordinator 创建带依赖的现有 Jobs：

    PREFLIGHT
      → CHUNK_ANALYSIS[*]
      → REDUCE level 1..N
      → SEASON/ARC PLAN
      → EPISODE BOUNDARY PLAN by group
      → VALIDATE
      → HUMAN_REVIEW_GATE

约束：

- Job 表是运行事实，不复制 queue/running/failed 字段到规划表；
- job_dependencies 是 fan-out/fan-in 执行依赖事实；
- AdaptationPlanRun/RunNode 只保存业务输入、节点结果、Job 引用和聚合进度，不实现 claim、lease、heartbeat、retry 或另一套调度；
- 现有通用 Automation Workflow 不适合内部 chunk/reduce DAG；它未来只能调用开始规划、批准后批量拆解等公开命令；
- 使用现有 dependency_job_ids、canonical scope/stage、lease、recovery 与 Outbox；
- 每个 Job 必须显式写 subject_kind、scope_project_id、必要时 scope_episode_id、stage_code、输入 Schema/Hash 和恢复策略，禁止落入 LEGACY_UNCLASSIFIED；
- 从大 application/worker.py 中注册专用 handler，不继续把长篇逻辑写入 local_llm.py；
- job_resources.py 从 Job 类型白名单重构为 provider + runtime_kind + resource_class 策略；本地 Ollama 与 Comfy 继续共享 GPU exclusive lease，远程 LLM 使用 provider-scoped 并发资源，纯验证走 CPU；
- 部分 chunk 失败时保留其他成功节点，重试只补失败节点。
- LLM 调用不能持有 SQLite 事务；Node 结果使用短事务幂等落库后再完成 Job；
- 远程请求已经发送但响应未知时标记 uncertain_side_effect / NEEDS_ATTENTION，不能静默自动重试造成重复计费；
- 批量取消先取消 QUEUED 子 Job，RUNNING 进入 CANCEL_REQUESTED；模型返回后再次核对 run/node/job 快照，已取消则不写业务输出。

### 9.5 幂等与并发

命令幂等键由服务端可验证输入构成：

    source_version_id + source_scope_hash
    + constraints_hash
    + profile/workflow/runtime snapshot
    + prompt/schema contract
    + logical node or revision id

- 相同 idempotency key 返回原 run；
- 前端重复点击不重复创建 Job；
- “重新生成”必须显式新建 revision，并使用新的 regenerate nonce/reason；
- 编辑 API 使用 expected_revision/ETag；
- stale revision 返回 409，携带当前 revision 摘要和差异入口；
- materialize 使用单 SQLite Unit of Work。

### 9.6 必须重构的既有模块

| 现有模块 | 重构要求 |
|---|---|
| application/local_llm.py | 拆出 Profile 解析、Prompt 合同、validator、单集 use case 和持久化；不再继续容纳长篇协调 |
| infrastructure/local_llm.py | 返回 typed LlmResult，保留 usage、finish_reason、provider_request_id 和 uncertain side effect |
| application/worker.py | 只注册 Handler；业务进入独立 worker_handlers/use cases |
| application/job_resources.py | 从两个 Job 类型白名单改为资源策略 |
| application/breakdown_apply.py | 修复跨集 Scene code，禁止假来源，写入 plan/source lineage |
| application/projects.py | 抽出批量结构 materialization；DB 为权威，目录/manifest 由幂等 PROJECT_STRUCTURE_SYNC 派生 |
| infrastructure/database/review_decision_repository.py | target resolver 改为注册策略并支持 ADAPTATION_PLAN |
| application/project_packages.py | 项目包导入/导出纳入新规划、来源 lineage 和批次事实 |
| api/routes/imports.py 与 api/routes/llm.py | 统一单集拆解入口；废弃忽略范围的 breakdown-local-llm |

---

## 10. 数据库设计

新增 additive migration；当前工作树已有 0069 相关内容，实施者必须先重新确认最新迁移号，预计从 0070 或下一可用编号开始，禁止复用编号。

### 10.1 主要表

#### source_document_units

保存 source_document_version_id、ordinal、unit_kind（HEADING/BODY）、Unicode start/end、text hash、chapter ordinal。不重复保存正文。首次规划时惰性、幂等建立，避免升级时扫描所有旧项目。

#### adaptation_plans

| 字段 | 说明 |
|---|---|
| id | UUID |
| project_id | 项目 |
| source_version_id | 权威来源版本 |
| mode | COMPLETE_WORK / SERIAL_INCREMENTAL / PRESEGMENTED_SCRIPT / SINGLE_EPISODE |
| artifact_status | DRAFT / IN_REVIEW / APPROVED / MATERIALIZED / SUPERSEDED / ARCHIVED |
| current_revision_id | 当前修订 |
| created_at / updated_at | 审计 |
| archived_at | 软归档 |

#### adaptation_plan_revisions

保存不可变修订快照、约束、模型合同、内容哈希、父修订与验证摘要。

#### adaptation_story_arcs

以 revision_id 为父，保存 stable logical_arc_id、顺序、来源证据、承诺和状态边界。

#### adaptation_season_groups

以 revision_id 为父，保存 stable logical_season_id、顺序和发行意图；允许为空。

#### adaptation_episode_items

以 revision_id 为父，保存 stable logical_episode_id、显示顺序、来源范围、标题摘要、承接/冲突/钩子、时长与 Canon delta。

#### adaptation_episode_source_spans

一集可以有一个主要连续范围和少量补充范围。字段包含 source_version_id、paragraph_start/end、unicode_start/end、role、evidence_hash。

#### adaptation_analysis_nodes

保存可重建的分层分析缓存、合同版本、输入/输出哈希、结构化 JSON、Job lineage。

#### adaptation_plan_runs / adaptation_plan_run_nodes

- Run 保存输入快照 hash、节点总数/完成/失败数、估算与实际 Token/成本/耗时；
- RunNode 保存 node key、阶段、core/context 范围、input fingerprint、job_id、output hash；
- 唯一键约束 run + node key + input fingerprint；
- 节点输出先于 Job 成功落库，恢复时可直接复用。

#### llm_invocations

保存 job_attempt_id、run_node_id、Profile/Provider/Model、Prompt 合同、request hash、input/output tokens、latency、finish reason、provider request id、估算/实际成本及 uncertain_side_effect。远程价格未知时成本可空，但调用次数与 Token 不可丢。

#### adaptation_plan_materializations

保存 approved revision 与真实 season_id / episode_id 映射、事务摘要、冲突决策和执行者。

#### adaptation_plan_episode_bindings / episode_source_spans

- Binding 显式关联 plan episode 与真实 season/episode；
- episode_source_spans 将已批准来源证据复制为生产 lineage，必须带 source_document_version_id、start/end 和 hash；
- 新生产链路不再依赖几乎未使用的 episodes.source_range_json；
- 旧 episode_scene_ranges 能唯一推断时回填来源版本，不能推断时标记 legacy/quarantine，禁止猜测和 (0,1) fallback。

#### breakdown_batches / breakdown_batch_items

这是领域批次与 Job 引用，不是第二套队列：

- batch 保存选择条件、规划 revision、统计快照；
- item 保存 logical_episode_id、target episode_id、job_id、draft_id；
- 运行状态由关联 Job 计算，不在 item 上维护另一套状态机。

#### Review 复用

优先扩展现有 review_templates / review_decisions / review_checks：

- target resolver 从硬编码 if/else 改为注册策略；
- 新增 ADAPTATION_PLAN target/template；
- Decision 冻结 subject_revision、plan_revision_id 和 content hash；
- 新 Revision 自动让旧 APPROVED Decision stale。

### 10.2 索引与约束

- plan(project_id, updated_at)；
- revision(plan_id, revision_no unique)；
- episode(revision_id, display_ordinal unique)；
- episode(revision_id, logical_episode_id unique)；
- span(source_version_id, unicode_start, unicode_end)；
- analysis(input_hash, contract_hash, node_type unique for active node)；
- materialization(revision_id, logical_episode_id unique)；
- materialization(target_episode_id unique where active)；
- run_node(run_id, node_key, input_fingerprint unique)；
- 外键与 ON DELETE 采用限制/软归档，不级联删除已产生下游事实；
- JSON 字段必须带 schema_version，并由应用层验证。

---

## 11. API v2 合同

新接口全部进入 OpenAPI 并生成前端客户端，不扩张手写 breakdownClient.ts。

### 11.1 诊断与预检

    POST /api/v2/projects/{project_id}/adaptation-plans:preflight

输入：

- source_version_id；
- source scopes；
- mode；
- target duration；
- episode/season strategy；
- adaptation style；
- optional profile version。

输出：

- document diagnosis；
- recommended modes；
- episode range estimate；
- chunk/run plan；
- executable route snapshot；
- privacy/remote transfer；
- estimated text cost；
- blockers/warnings。

预检不创建季度、分集、镜头或 Job。

### 11.2 创建与读取

    POST /api/v2/projects/{project_id}/adaptation-plans
    GET  /api/v2/projects/{project_id}/adaptation-plans
    GET  /api/v2/adaptation-plans/{plan_id}/workspace
    GET  /api/v2/adaptation-plans/{plan_id}/episodes?cursor=&limit=&arc=&season=&status=
    GET  /api/v2/adaptation-plans/{plan_id}/runs/{run_id}
    GET  /api/v2/adaptation-plans/{plan_id}/changes?after=

workspace 只返回 bounded 摘要、弧/季树、统计和选中项；1000 集不一次回传所有富字段。

### 11.3 编辑与生命周期

    PUT  /api/v2/adaptation-plans/{plan_id}/draft
    POST /api/v2/adaptation-plans/{plan_id}:validate
    POST /api/v2/adaptation-plans/{plan_id}:submit-review
    POST /api/v2/adaptation-plans/{plan_id}:approve
    POST /api/v2/adaptation-plans/{plan_id}:materialize-preflight
    POST /api/v2/adaptation-plans/{plan_id}:materialize

draft 命令使用 typed operations，而不是让前端回传整份 100 集 JSON：

- SPLIT_EPISODE；
- MERGE_ADJACENT_EPISODES；
- MOVE_EPISODE；
- UPDATE_SOURCE_BOUNDARY；
- UPDATE_EPISODE_CONTENT；
- ADD/REMOVE_SEASON_BOUNDARY；
- LOCK/UNLOCK；
- REGENERATE_UNLOCKED_RANGE。

每个写命令携带 expected_revision 和 idempotency_key。

### 11.4 批量拆解

    POST /api/v2/adaptation-plan-revisions/{revision_id}/breakdown-runs:preflight
    POST /api/v2/adaptation-plan-revisions/{revision_id}/breakdown-runs
    GET  /api/v2/breakdown-runs/{batch_id}
    POST /api/v2/breakdown-runs/{batch_id}:cancel-pending
    POST /api/v2/breakdown-runs/{batch_id}:retry-failed

选择语义支持：

- explicit logical episode IDs；
- filter snapshot + excluded IDs；
- 当前页与所有匹配结果必须可区分。

### 11.5 错误模型

统一 Problem Details：

- code；
- user_message；
- impact；
- recovery_actions；
- field_errors；
- blocker_refs；
- retryable；
- current_revision；
- correlation_id。

前端不能展示 raw Python/SQLite/Provider 错误。

---

## 12. 项目结构发布与冲突处理

批准规划不等于立即创建真实分集。materialize-preflight 必须展示四种策略：

1. **项目尚无结构**：按规划创建季度与分集。
2. **现有空结构数量相符**：一对一映射，例：规划 100 集映射现有 100 个空分集。
3. **数量不符但无生产事实**：明确预览创建、保留、归档或人工映射。
4. **已有生产事实**：任何会重排、删除或替换的操作成为 blocker；允许只映射兼容项。

项目中的集号是显示序号；规划 logical_episode_id 与真实 episode_id 通过 materialization 表关联。

单一 SQLite 事务完成：

- 乐观并发复核；
- blocker 复核；
- 季度/分集创建或映射；
- materialization 记录；
- audit/outbox；
- plan 状态切换。

失败整体回滚。绝不出现创建 57 集后事务中断却标记规划已发布的状态。

DB 提交后，PROJECT_STRUCTURE_SYNC 根据 DB 权威幂等创建/修复季度分集目录和 project manifest。同步未完成时相关生产动作显示可恢复 blocker，不能让 application/projects.py 的逐集追加逻辑继续分别维护 DB、目录和 project.json。

进入批量拆解前还必须完成：

- Scene code 改为 episode scope 或生成项目内真正唯一 code，第二集不能再次写入冲突的 SC01；
- 所有新 scene/shot source range 必须显式引用 SourceDocumentVersion；
- 没有证据的 AI 输出作为校验失败处理，绝不再写入 (0,1)；
- 单集来源超预算时先做 episode 内 beat map，再生成场镜，不能把规划后的大集重新挡在固定 4,000 字上限。

---

## 13. 前端工程重构

建议新增：

    apps/web/src/features/story-adaptation/
      api/
        adaptationPlanApi.ts
        breakdownBatchApi.ts
      model/
        types.ts
        queryKeys.ts
        selectors.ts
        validators.ts
        editorReducer.ts
      pages/
        SourceLibraryPage.tsx
        SourceDetailPage.tsx
        PlanListPage.tsx
        PlanSetupPage.tsx
        PlanEditorPage.tsx
        PlanRunPage.tsx
        DraftInboxPage.tsx
        DraftReviewPage.tsx
      components/
        SourceStructureInspector.tsx
        AdaptationModeSelector.tsx
        SourceScopeSelector.tsx
        PlanSetupWizard.tsx
        PlanSummaryBar.tsx
        StoryArcSeasonRail.tsx
        PlanEpisodeVirtualGrid.tsx
        PlanEpisodeInspector.tsx
        SourceEvidenceInspector.tsx
        CoverageTrack.tsx
        PlanValidationPanel.tsx
        StructurePublishPreviewDialog.tsx
        BreakdownBatchLauncher.tsx
        BreakdownBatchMonitor.tsx
        DraftApplyPreviewDialog.tsx
      styles/
        story-shell.module.css
        plan-editor.module.css
        draft-review.module.css

前端约束：

- StoryWorkspacePage 改为 StoryShell + Outlet；
- 声明式路由表供 Router、面包屑、命令搜索和上下文解析共享；
- URL 保存 plan、episode、draft、filter、season、view；
- TanStack Query 保存服务端状态；
- 向导和编辑器使用 reducer/discriminated union，不堆互相耦合 useState；
- 文本字段可自动保存，批准/发布/批量创建必须预检并确认；
- 抽取共享 JobStateBadge、JobProgress、useJobActions；
- CSS Modules 消费现有语义 token，不继续扩张全局 styles.css；
- 遵循 design-system/localdramastudio/MASTER.md 的暖中性高密度工作台、暗色 chrome、单主 CTA；不引入 AI 紫营销页、玻璃拟态或装饰性 hero。

旧组件的处理：

| 旧组件 | 处理 |
|---|---|
| ScriptImportPanel | 原稿保存能力迁入 Source 页面；目标集、任务和拆解逻辑删除 |
| AIDraftReviewPanel | 重写为 Inbox + Detail；旧草稿进入“未归档草稿” |
| BreakdownJobMonitor | 删除局部状态操作，复用统一 Job 组件 |
| ProjectStructureAppendPanel | 保留手工小规模追加，不承担规划批量发布 |
| SourcePassagePanel | 保留并增强为只读证据 Inspector |

---

## 14. 隐私、版权、审计与成本

### 14.1 隐私

- 默认本地处理；
- 选择远程 Profile 时，预检明确显示将发送的范围、字符/Token 估算和 Provider；
- 必须显式同意本次原稿范围外发；
- 不把全文或 Prompt 写入普通 audit/log；
- 日志只保存 hash、span、合同版本、correlation ID 和脱敏错误；
- 原文导出、备份和删除继续遵循现有不可变来源策略。

### 14.2 版权与来源

在 SourceVersion 附加：

- rights_basis：原创 / 已授权 / 公版 / 其他；
- authorization_reference；
- territory / expiry（可选）；
- AI adaptation notice；
- uploader attestation。

这不是法律判断，但可为后续交付、红果/抖音等平台备案与审计保留证据。[短剧创作者中心](https://www.shortdramas.com/) 的公开协议体系说明创作者仍需承担来源、授权、制作与发布合规责任。

### 14.3 成本

- 文本规划与视觉生产分账；
- 每次预检展示本步和整批预计成本；
- 支持项目软预算与硬预算；
- 结构抽取/校验优先用规则或经济模型，全局规划用高质量模型；
- 先做 1–3 集样片，再开放大批量；
- 缓存按来源+合同 hash；
- 上游修改只失效受影响节点；
- 单集、单镜、单句 TTS、单资产都可局部重做；
- 失败释放预留预算；成本按计划、集、镜头、模型和阶段可追溯。

### 14.4 审计事件

- ADAPTATION_PLAN_REQUESTED；
- ANALYSIS_NODE_COMPLETED；
- PLAN_REVISION_CREATED；
- PLAN_REVISION_EDITED；
- PLAN_SUBMITTED_FOR_REVIEW；
- PLAN_APPROVED；
- PLAN_MATERIALIZATION_PREFLIGHTED；
- PLAN_MATERIALIZED；
- BREAKDOWN_BATCH_REQUESTED；
- BREAKDOWN_DRAFT_REVIEWED/APPLIED。

审计记录 Actor、时间、对象、revision/hash、原因与 correlation；不记录原文正文。

---

## 15. 迁移与切换

### 15.1 数据库迁移批次

实施前重新核对最新迁移号；按当前审计从 0070 或下一可用编号开始，只做 additive/forward migration。

**0070：长篇规划基础**

- source_document_units；
- adaptation_plans / revisions / story_arcs / season_groups / episode_items / source_spans；
- adaptation_plan_runs / run_nodes；
- adaptation_analysis_nodes；
- llm_invocations；
- 新 Job stage/type 约束和索引。

不扫描全部旧文档；SourceUnit 首次规划时惰性建立。

**0071：审核与物化**

- adaptation_plan_materializations；
- adaptation_plan_episode_bindings；
- episode_source_spans；
- ADAPTATION_PLAN Review target/template；
- PROJECT_STRUCTURE_SYNC 所需事实。

**0072：批量拆解与 lineage**

- breakdown_batches / batch_items；
- plan episode ↔ script_breakdown_draft lineage；
- episode_scene_ranges 增加 nullable source version/span lineage；
- 可唯一推断的旧记录回填，无法推断的记录进入 migration quarantine，不构造假来源。

每批迁移必须执行：升级前备份、foreign_key_check、旧项目包 round-trip、恢复测试和新旧读取合同测试。

### 15.2 数据兼容

- 现有 SourceDocument/SourceVersion、import session、source offsets 保留；
- 现有单集 AI 草稿不强制回填规划，进入“未归档草稿”；
- 有 target_episode_id 的旧草稿保留只读目标映射；
- 现有季度、分集和生产内容不自动重排；
- 旧 request-breakdown API 在过渡期只服务旧页面/单集兼容，随后停止前端调用并标记废弃；
- /import-sessions/{id}:breakdown-local-llm 立即标记 deprecated；新 UI 不调用；
- 不做新旧双写；新规划只写新表，发布时通过明确 materialization 命令写真实结构。

### 15.3 切换顺序

1. 新表、领域、Port、Repository、OpenAPI 合同；
2. LLM_EPISODE_PLAN 路由和默认 Workflow；
3. 原稿库/详情与诊断；
4. 规划向导、运行与只读结果；
5. 规划编辑、校验、批准；
6. 结构发布；
7. 批量单集拆解；
8. 新草稿审核；
9. 旧路由兼容重定向；
10. 删除旧写入口、重复任务 UI 和全局旧样式。

任何阶段都不允许通过 Feature Flag 长期保留两套可写事实。Feature Flag 只用于短期部署切换，默认关闭旧写入口后必须在同一里程碑删除。

---

## 16. 分阶段开发实施

工作量是工程估算，不是承诺；按 2–3 名工程人员并行，预计 18–25 个工作日；总工程量约 31–44 人日。

### M0：契约与 ADR 收口（2–3 人日）

交付：

- 本文转为实施基线；
- 新增 Adaptation Planning ADR；
- OpenAPI 草案、状态与错误码；
- Prompt/JSON Schema v1；
- 数据迁移评审；
- 真实样本 golden expectation。

退出门：

- 产品、前后端、测试对“故事弧必备/季可选”“先批准后发布”无歧义；
- 明确 Job/Job dependency、PlanRun/RunNode、Artifact 三种状态归属，以及通用 Automation 的调用边界；
- 不存在第二套 episode 真值。

### M1：原稿库与诊断（4–5 人日）

后端：

- Source 列表/详情 bounded query；
- 确定性文档分类与 preflight；
- TokenBudgetPort；
- 权利与远程发送元数据。

前端：

- StoryShell 与嵌套路由；
- 原稿库、原稿详情、三模式入口；
- preview truncated/index 状态；
- legacy hash redirect。

退出门：

- 整本样本不再显示默认第 1 集；
- 无季度/分集的项目也能进入长篇规划预检；
- 原稿全文不会一次送到浏览器。

### M2：分层分析与规划生成（7–10 人日）

- migrations + aggregate/repository；
- LLM_EPISODE_PLAN → TEXT_PLANNING；
- chunk/reduce/plan/validate handlers；
- Planning Coordinator 与 Job dependency DAG；
- AnalysisNode 缓存与重试；
- PlanRun 投影；
- typed LlmResult / llm_invocations / uncertain side effect；
- 严格 JSON Schema、evidence 和校验。

退出门：

- 200k 中文字符 fixture 可中断恢复；
- worker crash 后成功节点复用；
- 重复提交不产生重复 Job；
- 部分失败可独立重试；
- 每个计划分集能打开来源证据。

### M3：规划审核工作台（6–8 人日）

- 虚拟化分集表、弧/季树、Inspector；
- split/merge/move/boundary/update/lock；
- revision/ETag、冲突比较；
- coverage track 与 validator；
- 批准流程。

退出门：

- 1000 计划分集 DOM ≤约 60 行；
- 键盘可拆、并、移动；
- source gap/overlap 可定位；
- stale revision 不覆盖他人/另一窗口修改；
- 已批准修订不可原地编辑。

### M4：结构发布（3–4 人日）

- materialize preflight；
- 空项目、新建结构、现有空结构映射；
- 有生产事实 blocker；
- 单事务发布和 audit/outbox；
- PROJECT_STRUCTURE_SYNC；
- 项目首页显示真实结构，不混入草稿计划。

退出门：

- 样本约 100 个计划项可安全映射现有 100 集；
- 模拟第 57 集失败时整个事务回滚；
- 重放同一幂等命令不重复建集；
- 已有媒体分集不会被自动重排或删除。

### M5：批量拆解与审核（6–8 人日）

- BreakdownBatch 领域聚合；
- filter selection + excluded IDs；
- 新 EPISODE_BREAKDOWN_LLM 核心与旧 SCRIPT_BREAKDOWN_LOCAL_LLM 兼容适配；
- 修复跨集 Scene code 与 source lineage，删除 (0,1) fallback；
- 批次监控、取消未开始、重试失败；
- Draft Inbox + Detail；
- 应用预览与原子应用。

退出门：

- 30 集批次部分失败不阻塞成功草稿审核；
- 页面刷新后批次恢复；
- 已映射草稿不能用普通下拉静默改投；
- 重复点击不重复扣费/建草稿；
- 应用冲突有明确 blocker。

### M6：切换、删旧与硬化（3–6 人日）

- 切换 Story 主路由；
- 删除 ScriptImportPanel 中目标集/任务/拆解职责；
- 退役 AIDraftReviewPanel 平铺模式；
- 删除 BreakdownJobMonitor 重复操作；
- 清理旧 CSS、Query Keys、手写客户端；
- 性能、无障碍、崩溃恢复和大数据 E2E；
- 文档与用户迁移说明。

退出门：

- 旧页面没有可写入口；
- 新旧事实守卫、架构测试通过；
- 所有旧草稿仍可读可审；
- 无长期双写/双页面；
- 整体 Definition of Done 通过。

---

## 17. 测试策略

### 17.1 领域与数据库

- stable logical_episode_id 在重排/改季后不变；
- approved revision 不可修改；
- source span/hash/Unicode offset 精确；
- gap/overlap/ordinal/duration/Canon validator；
- materialize 单事务与幂等；
- stale expected_revision；
- 已有生产事实 blocker；
- materialize preflight hash 与提交时快照不一致则拒绝；
- 第二集应用场次不与第一集 SC01 冲突；
- 每个新 scene range 均能追溯 SourceDocumentVersion；
- migration 前后旧草稿、旧来源可读；
- cascade delete 不得删除已审核下游事实。

### 17.2 AI 与 Job

- 2 章节选：推荐 SERIAL_INCREMENTAL；
- ≤单集预算短文：允许 SINGLE_EPISODE；
- 42 章样本：推荐 COMPLETE_WORK，约 95–110 集；
- 200k 中文字符：分块、归并、规划成功；
- chunk 中间失败、模型超时、非法 JSON、证据越界；
- worker crash、lease expiry、resume；
- 双 Worker 不重复 claim，本地 LLM 与 Comfy GPU Job 不并发；
- 相同 idempotency replay；
- 重试保持执行快照，重新生成创建新 Revision；
- 远程超时/未知响应进入 NEEDS_ATTENTION，不静默重试计费；
- finish reason 为 length 时识别为截断，不把半份 JSON 当成功；
- 本地模型不可用；
- 远程模型未同意外发；
- source hash mismatch；
- Profile 下线或 Workflow 不可执行。

### 17.3 API

- 所有列表 bounded/cursor；
- workspace 不返回全文与全部富分集；
- typed edit operations；
- 409 revision conflict；
- Problem Details 恢复动作；
- selection filter snapshot；
- cancel pending 不取消已成功项；
- retry failed 不复制成功项；
- materialize preflight 与执行之间再次校验。

### 17.4 前端

- 长文只推荐规划，不自动选第一集；
- 单集模式未显式目标时 CTA 禁用并说明原因；
- 超限提供切换/拆分/缩小范围；
- 全部新路由可刷新、分享和通知跳转；
- 返回列表恢复筛选与滚动；
- 规划生成部分失败仍显示成功结果；
- revision 冲突停止自动保存；
- 批次聚合计数与 Job 一致；
- 旧草稿进入未归档收件箱；
- 应用前展示目标已有事实；
- 375px 无横向页面滚动；
- 200% 缩放可用；
- axe 无 critical/serious；
- reduced motion；
- 1000 集虚拟化与性能预算。

### 17.5 端到端黄金路径

    上传整本小说
      → 保存不可变原稿版本
      → 诊断为长篇
      → 创建约 100 集规划
      → 中断并恢复
      → 审核、拆/并/改边界
      → 校验覆盖
      → 批准
      → 映射现有 100 集
      → 只生成 1–3 集样片拆解
      → 审核并原子应用
      → 批量第 4–30 集
      → 单项失败重试

---

## 18. 风险与控制

| 风险 | 控制 |
|---|---|
| 长篇规划质量不稳定 | 分层分析、证据、确定性校验、人工门、锁定后局部重规划 |
| “季可选”让用户困惑 | UI 先展示故事弧，季策略用清晰解释和 AI 候选 |
| 新表与既有 episode 重复真值 | 计划项只是草稿；materialization 后真实 episode 仍唯一生产事实 |
| Job 数量过多 | 分块预算、节点缓存、依赖 DAG、并发上限和批次投影 |
| local_llm.py/worker.py 继续膨胀 | 专用 command/query/ports/handler 模块，架构测试 hard fail |
| 规划编辑器性能差 | cursor API、固定行高虚拟化、Inspector 编辑、按需详情 |
| 发布破坏已有生产 | preflight、blocker、单事务、稳定 ID、无级联删除 |
| Prompt/模型变化导致不可复现 | 冻结 Profile/Workflow/Runtime/Schema 合同和输入 hash |
| 远程模型泄露原稿 | 默认本地、显式范围与同意、最小化发送、日志脱敏 |
| 旧页面长期共存 | 里程碑内切换并删除旧写入口，事实守卫禁止双写 |

---

## 19. Definition of Done

只有全部满足才算完成：

1. 整本书、几章节选、多集剧本、单集短文都进入正确模式。
2. 任何路径都不会在用户未选择时默认第 1 季第 1 集。
3. 长篇可以在没有任何真实季度/分集时生成可审核规划。
4. StoryArc 必备，Season 可选且可后补。
5. 每个计划分集有稳定 ID、来源证据、时长合同和连续性信息。
6. 长文本分层处理可中断、恢复、复用、局部重试。
7. AI 结果经过严格 Schema 和确定性覆盖/状态校验。
8. 规划批准前不写真实季度/分集；发布通过单事务和冲突预览。
9. 批量拆解部分失败可用，重试幂等，成功项不丢。
10. 旧草稿、旧原稿、旧项目结构继续可读可审。
11. Job/Job dependency、PlanRun/RunNode、规划产物各自只有一个事实来源；通用 Automation 不拥有内部规划 DAG。
12. 新 API 全部 OpenAPI 生成；新后端遵循 Ports & Adapters。
13. ScriptImportPanel、AIDraftReviewPanel 和局部 Job 监控的旧写职责被删除，不长期双轨。
14. 1000 集规划的分页、虚拟化、无障碍和响应式验收通过。
15. 真实“照骨灯”样本端到端通过，不再出现“全文为第 1 集生成”。
16. 第二集及后续分集应用不会发生 Scene code 冲突，所有新来源范围均带 SourceDocumentVersion 且不存在假 (0,1) 证据。

---

## 20. 推荐立即开工顺序

不要先做视觉稿或改按钮文案。第一个可合并的纵向切片应是：

1. 新 ADR + Schema + migrations；
2. LLM_EPISODE_PLAN 路由可执行；
3. preflight 诊断；
4. 创建 AdaptationPlan + 一个可恢复 PlanRun；
5. 只读规划结果与来源证据；
6. 用“照骨灯”样本证明约 100 集规划不会默认落到第 1 集。

这条切片打通后再做编辑器和结构发布。它能最早验证最难的领域边界、模型合同、长文本 DAG、Job 恢复和来源追溯，避免前端先做出一套没有正确后端事实支撑的页面。

---

## 21. 参考资料

### 产品

- [小云雀](https://xyq.jianying.com/)
- [小蓝梯 AI 分镜使用指南](https://manju.xiaolanti.cn/help-storyboard.html)
- [小蓝梯账户与消费说明](https://manju.xiaolanti.cn/help-account.html)
- [天翼云 DramaFlow 2.0 剧集创作指南](https://www.ctyun.cn/document/11057595/11097192)
- [万兴剧厂：百万字符小说改编与全链路](https://www.wondershare.cn/new/details/id/1195.html)
- [万兴剧厂：小说到剪辑工作流](https://www.wondershare.cn/new/details/id/1192.html)
- [DramaBuddy 漫剧助手](https://aicomic.yuewen.com/)
- [迅漫工厂](https://www.beiyinbook.com/)
- [Dreamina Storyboard AI](https://dreamina.capcut.com/create/storyboard-ai)
- [Toonany](https://github.com/casperkwok/toonany)
- [短剧创作者中心](https://www.shortdramas.com/)

### 长文本研究

- [Lost in the Middle: How Language Models Use Long Contexts](https://aclanthology.org/2024.tacl-1.9/)
- [RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval](https://openreview.net/pdf?id=GN921JHCRw)

### 仓库基线

- docs/plan/local-drama-studio-full-product-rewrite-design-2026-08-26.md
- docs/decisions/ADR-0102-modular-monolith-no-microservices.md
- docs/decisions/ADR-0103-incremental-ports-and-adapters.md
- design-system/localdramastudio/MASTER.md
- design-system/localdramastudio/pages/quick-create.md
