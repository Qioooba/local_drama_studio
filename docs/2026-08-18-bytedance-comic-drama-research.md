# 字节「漫剧创作工具」调研报告：对 LocalDramaStudio 的灵感映射

> 调研日期：2026-08-18（字节产品消息当日）
> 方法：web_search 多轮检索公开报道（21财经、钛媒体、AI 前线、IT 之家、界面、机器之心等）+ 与用户提供的 GPT 深度分析交叉核对 + 对照本仓库真实代码与数据模型逐项映射。
> 证据分级：✅ = 我检索到的公开报道证实；🧠 = 来自用户转述的 GPT 分析（暂未在公开报道中单独证实，内测产品以官方为准）。

---

## 一、核实结论：字节在做什么

### 1.1 字节「漫剧创作工具」（2026-08-18 内测消息）✅

- 名字直白叫「漫剧创作工具」，Slogan「让创作更纯粹」，搭载豆包多个大模型，官方开通内测权限（[AI 前线独家](https://api.aitntnews.com/newDetail.html?newId=28337)、[网易转载](https://www.163.com/dy/article/L4JR68SB0519U3I5.html?clickfrom=w_tech)）。
- 支持最长 **20 万字**剧本上传；上传后设置剧名、画幅、画风，系统自动拆剧情、生成角色/场景资产、按集拆分分镜、合成视频（[21财经](https://m.21jingji.com/article/20260818/herald/870d7c1ac9f6ad7ae2149d95c9ca5509.html)、[钛媒体](https://www.tmtpost.com/8107419.html)、[起飞客](https://qifukexue.com/?p=24137)）。
- 主流程：**上传剧本 → 项目规划 → 剧本策划 → 资产设计 → 分镜生产 → 视频合成**；环节拆成独立节点，报道明确每个节点**允许人工干预**（[AI 前线独家](https://api.aitntnews.com/newDetail.html?newId=28337)）。
- 产品定位是**长篇连载工业化生产系统**，不是单集玩具（[起飞客](https://qifukexue.com/?p=24137)：「以 AI 工业化流程切入长篇连载生产」；21财经标题即「字节下场漫剧工业化，LibTV 们迎来大考」）。
- 🧠 团队空间支持 30 人同时在线、可绑定抖音短剧创作者中心做生产/分发数据互通——方向合理但公开报道暂未单独证实细节。

### 1.2 小云雀短剧 Agent（2026-03 上线）✅

- 字节旗下小云雀 AI 上线首个搭载 **Seedance 2.0** 的短剧 Agent，支持 **10 万字**剧本一键成片（[科技日报](https://www.stdaily.com/web/gdxw/2026-03/20/content_488925.html)、[IT 之家](https://m.ithome.com/html/930665.htm)、[新浪财经](https://k.sina.cn/article_2131593523_7f0d893302001n166.html?from=local)）。
- 流程：上传剧本 → 自动**故事蓝图、角色设计、分镜生成、成片**；开放**角色与分镜的人工编辑**（[重庆晨报](https://wap.cqcb.com/shangyou_news/NewsDetail?classId=9531&newsId=6096245)、[网易](https://www.163.com/dy/article/KOD8DJE305129QAF.html?spss=adap_pc#1)）。

### 1.3 火山剧创 Dramart（火山引擎，2026-05 企业级）✅

- 火山引擎推出的**企业级一站式 AIGC 短剧创作平台**，依托 Seedance 2.0 + 豆包大模型，剧本导入 → AI 自动完成剧本解析、角色/场景资产设定、分镜拆解、视频生成与成片拼接；宣传制作周期缩短 80%（[AIHub](https://www.aihub.cn/tools/huoshan-juchuang/)、[火山官方文档](https://docs.volcengine.com/docs/4/1864794?lang=en)、[鞭牛士](https://www.bianews.com/news/details?id=235165)、[异火出海](https://www.yfchuhai.com/article/10228191.html)）。

### 1.4 竞争格局：这不是字节一家在做

- **LibTV**（LiblibAI）：无限画布 + 节点式工作流，剧本→分镜→图像→视频→音频端到端（[量子位](https://www.qbitai.com/2026/03/390320.html)、[极客公园深度体验](http://www.geekpark.net/news/367815)）。
- **商汤 Seko**：无限画布、**三视图确保角色一致性**、Seko 2.0 创编一体多剧集生成（[搜狐](https://m.sohu.com/a/1034362904_99963310/)、[优设网实测](https://www.uisdc.com/hangye/seko)、[商汤官网](https://www.sensetime.com/cn/news/seko-3-0-ai-1)）。
- **万兴剧厂**：接入满血版 Seedance 2.0，全链路创作 + 后剪辑链路闭环（[万兴官网](http://ori-www.wondershare.cn/new/details/id/1186.html)、[和讯](https://tech.hexun.com/2026-03-15/223703953.html)）。
- **阿里 ManClaw / 幻漫**：阿里书旗内测 AI 漫剧创作工具，背靠 5 万部网文 IP（[鞭牛士](http://www.bianews.com/news/details?id=232237)、[择道](https://www.chooseai.net/news/4809/)）。

**结论**：漫剧生产工具赛道 2026 年全面升温，字节这套是「工业化生产系统」路线（项目/资产/团队/分发），业界普遍判断它给「一键生成」类工具带来大考。

---

## 二、GPT 分析与公开报道的交叉核对

| GPT 分析点 | 核实结果 |
|---|---|
| 产品名/口号/豆包大模型/内测 | ✅ 证实 |
| 20 万字、上传后设置剧名/画幅/画风、自动拆剧情/资产/分镜/合成 | ✅ 证实 |
| 六节点流程（上传剧本→项目规划→剧本策划→资产设计→分镜生产→视频合成） | ✅ 证实（AI 前线独家） |
| 每个节点允许人工干预 | ✅ 证实 |
| 对标 Production Studio 而非即梦加强版 | ✅ 证实（21财经/钛媒体定性） |
| 30 人团队空间、绑定抖音短剧创作者中心 | 🧠 方向合理，细节待官方 |
| 首页=上传剧本入口、编辑页=左画面/中分镜字段/右资产/底时间线 | 🧠 来自内测截图转述，未逐字出现在公开文字报道 |
| 分镜结构化为画面/人物/动作/声音/文本/转场/运镜/时长 | 🧠 同上 |
| 字节弱点=模型锁定豆包系、未见首尾帧一级 UI | 🧠 合理推断，非官方结论 |

---

## 三、值得学的产品抽象（心智模型）

1. **剧本是 Project Root**：入口是「上传剧本」而不是聊天框，一切从剧本长出。
2. **Shot 是最小生产单位**：不是「整集一个请求」，而是结构化的 Shot Object。
3. **资产是永久一级公民**：人物/场景/道具/服装有 ID，镜头只引用资产，跨集复用。
4. **AI 自动 + 人工接管共存**：全自动生成，但每个节点都能单独重做。
5. **NLE 式工作台**：中央预览 + 右侧 Inspector + 底部时间线，成熟且被验证。

---

## 四、LocalDramaStudio 现状对照（逐项核实代码）

| GPT 建议的能力 | 我们的现状（真实文件证据） | 差距 |
|---|---|---|
| 剧本为 Project Root | ✅ `ScriptImportPanel.tsx`（剧本导入）、`EpisodeSceneRanges.tsx`（自动拆集范围）、`AIDraftReviewPanel.tsx`（AI 草稿评审） | 首页/工作台不是「上传剧本」优先入口，入口分散 |
| 分集（Episode）层级 | ✅ 项目 → 分集 → 镜头 数据模型 + 生产总览/分集生产视图 | 无「分镜组 Storyboard Group」中间层（字节有分镜组 01/02…） |
| 结构化 Shot Object | ✅ `DirectorShotEditor.tsx`：九字段（景别/构图/主体动作/镜头运动/时长/对白/环境/连续性/创作意图）+ CameraPlan（运动/方向/强度/曲线）+ 资产绑定 | 缺**情绪、转场、声音引用**字段；字节还有「查看原文/历史」对照 |
| 资产库永久 ID + 镜头引用 | ✅ `StoryAssetLibraryPanel.tsx`：CHARACTER/SCENE/PROP/COSTUME 四类资产卡 + canonical 参考图 + 镜头绑定（含 role_in_shot） | 资产卡目前**单参考图**，无三视图/多角度/表情表/服装多套/声音 ID 的「角色圣经」结构 |
| 抽卡/Take 不覆盖 | ✅ `GenerationWorkbench.tsx`：BASE 分支 + 同图同词新 seed/精确重放/Provider random/改 Prompt/换图 + Seed 批量实验矩阵 +「只新增，不覆盖历史」+ `ImageCandidateGrid`/`FormalSelectionPanel` 选 Take | 基本完备；可加「导演选 Take 后自动入时间线」的顺滑度 |
| 首尾帧 | ⚠️ `GenerationWorkbench.tsx`：已有 FIRST_FRAME 锚点 +「用作首帧」；但 chip 明示「首尾帧能力尚未发布」 | **无自动继承**（上一镜尾帧→本镜首帧→下一镜），这是连续性引擎的第一步 |
| 多模型自由 | ⚠️ Profile 能力系统（H3 Worker + ComfyUI 回环，能力未配置不静默换模型，`GenerationWorkbench` 能力真相 + `ModelLicenseEvidenceForm`/`LocalModelScanForm`） | Profile 是**项目/能力级**，无「每个 Shot 单独选模型」的 Model Router 交互；远程模型（Seedance/Kling/Veo）未接入 |
| 连续镜头一致性 | ✅ `ContinuityPanel.tsx` + DirectorShotEditor 连续性字段 + 场记单 `EpisodeContactSheetAction.tsx` | 是「人工声明」层面，未到「自动继承服装/场景状态/首尾帧」 |
| 时间线 | ✅ `TimelineRevisionPanel.tsx`/`TimelineExportAction.tsx`/`VideoAnnotations.tsx`/`SubtitleRevisionPanel.tsx` | 是独立面板，不是「导演台」一体布局（中央预览+右 Inspector+底时间线+Take 条） |
| 音频 | ✅ `AudioTrackPanel.tsx`/`DialogueTTSPanel.tsx`（对白 TTS） | 声音未挂到角色卡（角色→Voice ID） |
| 成片交付 | ✅ `DeliveryWorkflowPanel.tsx`/`OutboxDeliveryPanel.tsx`/`ProjectPackageAction.tsx` | 已覆盖分发出口（本地/外发） |
| DAG 画布 | ✅ `ProductionCanvasPanel.tsx`（React Flow，实测 115 节点/114 连线） | 定位不同：字节是流程节点，我们已有生产图 |
| Director Agent 自动决策 | ⚠️ `AutomationPanel.tsx`/`AutomationWorkflowPanel.tsx`（自动化工作流） | 无「镜头级自动选模型 + 脸漂移自动重抽 + 一致性评分选 Take」的智能层 |

**总评**：GPT 建议的 10 项里，我们已经有 **6 项**（剧本入口、分集、结构化分镜、资产库、抽卡 Take、时间线/交付），且实现深度不输内测产品（不可变分支、Seed 矩阵、Profile 裁决这些都是字节公开信息没展示的）。真正的差距集中在 4 块：**角色圣经、连续性引擎、分镜组层级、导演台一体布局**。

---

## 五、值得抄的（差距分析，本地优先排序）

### P0 —— 低改动、高价值，建议立刻做

1. **角色圣经 Character Bible（资产卡升级）**
   - 现状：资产卡仅一张 canonical 参考图（`StoryAssetLibraryPanel`）。
   - 升级：角色卡挂**多张参考图**（正面/左 45°/右 45°/全身/表情表）+ **服装多套**（已有 COSTUME 资产类，改成角色卡可引用多套）+ **声音 ID**（挂到 `DialogueTTSPanel` 的 voice 上）。
   - 价值：直接对齐字节「资产设计」节点与 Seko「三视图保角色一致」，是我们「固定人物三视图」承诺的落地。
2. **连续性引擎第一步：首尾帧自动继承**
   - 现状：有 FIRST_FRAME 锚点，但无自动继承，chip 写「待能力发布」。
   - 升级：镜头 N 选定尾帧 → 自动成为镜头 N+1 首帧候选（可覆写）；同场景连续镜头顺滑衔接。
   - 价值：字节公开信息未展示此能力，这是差异化点。
3. **分镜字段补齐：情绪 / 转场 / 声音引用**
   - 现状：九字段齐全但缺这三个（字节分镜结构里有声音、转场、运镜、时长）。
   - 升级：DirectorShotEditor 增加 emotion（情绪）、transition（转场）、声音引用（对白已存在，补 SFX/BGM 引用字段）。

### P1 —— 中期方向

4. **分镜组 Storyboard Group 层级**：Episode → Shot Group → Shot。适合长篇连载组织「这场戏的 5 个镜头」；后端加一张分组表即可，前端在分镜列表上做分组折叠。
5. **镜头级 Model Router**：每个 Shot 可选择 Profile/模型（现在 Profile 是能力级）；做成「镜头卡片上的模型下拉」，默认可继承项目 Profile。远程模型接入是更大的工程，先做本地 Profile 的 per-shot 选择。
6. **导演台一体视图**：把中央 Preview + 右 Inspector（角色/场景/相机/运镜/音频）+ 底部 Shot 时间线 + Take 条合成一个「导演台」页面——正好是项目名「导演台」的应有之义，也是与字节/小云雀的最终差异化 UI。

### P2 —— 不做 / 远期

- 30 人团队协作、抖音/红果分发绑定：单机本地工具定位不碰（我们有 Outbox 交付出口）。
- 一键黑盒成片：定位冲突，不做。
- 多远程模型接入（Seedance/Kling/Veo）：等能力成熟与授权明确后再议；保留 Model Router 数据位。
- Director Agent（自动选模型 + 脸漂移自动重抽）：建立在 P0+P1 之上，留作远期。

---

## 六、结论

1. 字节漫剧创作工具 = **豆包生态的 Production OS**（项目/资产/团队/分发），我们打不赢也无需打；我们的立身之本是**本地可控 + 多模型自由 + 导演级抽卡**。
2. 它公开验证了三个我们早已坚持的判断：**剧本为根、Shot 为最小单位、资产永久化**——方向正确，无需转向。
3. 真正值得动手的是 P0 三项（角色圣经、首尾帧自动继承、分镜字段补齐），都是**数据结构层面的小改动**，不碰架构，立刻能做。
4. P1 的「导演台一体视图」是终极形态，建议在 P0 稳定后推进，与字节形成清晰区隔。
