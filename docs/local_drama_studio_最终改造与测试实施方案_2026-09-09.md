---
title: "Local Drama Studio 最终改造与测试实施方案"
version: "1.0"
date: "2026-09-09"
language: "zh-CN"
source_documents: 7
source_document_physical_lines: 9900
report_baseline_commit: "f15a354ec81fa56233154ac753c4499356b9c2a2"
verified_repository_head: "e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3"
repository_modified: false
repository_tests_executed: false
live_models_invoked: false
execution_policy: "一次一个 Phase；同一 Phase 内一次一个小 PR；先验证，再最小修改"
---

# Local Drama Studio 最终改造与测试实施方案

## 从七份审计报告收敛为一条可执行主线

> 2026-09-13 第二轮复核更正：第一轮已提交实现与测试记录，但全文功能验收尚在继续。下文各 K 项的“已完成”是第一轮记录，不能作为所有 V01—V54 场景已验证的证明。真实 4—6 镜用户流程、实际局部重跑/仅重合成和两集独立推进仍需对应证据；“全部完成”须待第二轮逐项复核。本文编制阶段的“不运行”说明仅描述原始编制时点。

> 2026-09-21 成片听看更正：页面可播放、渲染成功、审批成功和交付 VERIFIED 不能替代视听内容验收。首个 120 秒交付片被人工判定不合格；自动全帧/全音轨复核确认模型原声泄漏、后期对白叠音、字幕只覆盖两句、竖屏源横屏黑边。相关防错代码已补，正式 16:9 重生成与再次人工听看仍是完成门槛。

**最终目标：原稿不串、不漏；人工细节不被自动改掉；模型真正执行已确认的输入；失败能恢复；修改只影响必要下游；先稳定产出一集可播放预览，再扩到所选多集。**

本文不是把七份报告的功能数量相加，也不是要求一次实现全部建议。它将重复发现合并为 **30 个主线工作包、Phase 0—6 七个阶段**，另列默认不启动的条件性增强。工作包是验证和收敛的单位，不等于必须新增 30 个功能：被当前实现覆盖的事项可以“仅补测试”或“无需修改”结项。

**本轮不修改项目源码、不运行项目测试、不启动模型、不修改用户数据。** 文中所有验收用例均为后续实施要求，不能当成本次已通过的结果。

### 阅读导航

| 你要解决的问题 | 阅读位置 |
|---|---|
| 哪些建议应该保留、哪些应该删掉 | [第 1—3 节：证据、最终取舍、技术边界](#s1) |
| 从哪里开始，一阶段做多少 | [第 4 节：Phase 路线与准入条件](#s4) |
| 具体改哪些文件、参考什么、怎样测试 | [第 5 节：30 个工作包](#s5) |
| 什么先不做，何时才值得做 | [第 6 节：条件性增强](#s6) |
| 怎样证明没有“假完成”或无效重跑 | [第 7 节：验收矩阵与发版门槛](#s7) |
| 怎样迁移、回滚、派发给编码 AI | [第 8—9 节](#s8) |
| 某份原报告最终被怎样处理 | [第 10 节：原报告去向与代码借鉴索引](#s10) |
| 原始文件及本次源码复核范围 | [附录 A—B](#appendix-a) |

<a id="s1"></a>
## 1. 证据基础：本文件能证明什么

### 1.1 七份输入已经完整阅读，但不能把七份同源报告当成七次独立实测

七份附件合计 **9,900 个物理行**，包括问题分析、代码映射、任务卡、测试矩阵、YAML、限制和来源附录。行数按上传文件 UTF-8 文本的 `splitlines()` 统计，不采用检索输出的包装行号；文件清单和 SHA-256 见附录 A。

七份报告都审查本项目同一提交 `f15a354ec81fa56233154ac753c4499356b9c2a2`。相同问题被多份报告重复提及，有助于交叉定位，但不能因此升级为“已在用户机器复现”。原报告普遍声明只做关键路径静态审查，没有执行完整项目测试和真实 GPU 出片。

本次另外读取 GitHub 当前 `main` 提交及部分关键代码。读取到的 HEAD 为 `e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3`，提交时间为 `2026-09-08T16:51:10Z`，父提交即上述报告基线；该提交只修改 `README.md`。所以在本次读取的远端 HEAD 上，业务源码没有因这次 README 更新而修复。**这不证明用户本地工作区、其他分支、运行数据库和已安装工作流与远端完全相同。**

版本依据：[当前 HEAD 提交](https://github.com/Qioooba/local_drama_studio/commit/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3)。实施者仍须在 Phase 0 重新核对届时的 HEAD、工作区和实际运行入口。

### 1.2 全文证据标签

| 标签 | 含义 | 实施方式 |
|---|---|---|
| `SRC` | 本次补充读取源码，直接确认了相关分支或签名 | 先写针对当前装配/入口的失败测试，再修改 |
| `DOC` | 来源报告明确给出代码位置和静态事实，本次没有把该调用链重新全读 | 先复核文件、调用者、现有测试；不能直接认定部署故障 |
| `VERIFY` | 是否触发、是否已被补偿、实际输入是否生效需要验证 | 默认先测试；已覆盖则不新增实现 |
| `DESIGN` | 本文裁决后的目标行为、阶段安排或拟议字段 | 不得冒充当前 API、数据库字段或已发布模型能力 |

同一工作包可以同时具有 `SRC + VERIFY`：例如能确认分支使用 `len(jobs)`，但仍须验证实际 Worker 是否会重复进入该分支。本文没有任何工作包被标记为“真实 GPU 验收通过”。

### 1.3 本次源码复核带来的重要裁决

1. `FullStoryAIGenerationService.resolve_client()` 的 Profile 参数与 `LocalLLMService.client()` 签名确实不一致；真实装配注入的就是后者。这不是缺少 Agent，而是接口合同问题。见 K01。
2. 已有 `AdaptationAnalysisExecutionService` 的分层分析、同源知识检索和不可变分析节点。不能再按其他报告的泛化建议新造章节 MapReduce/RAG 平台；应复用并修补实际请求冻结。见 K11—K12。
3. 音轨需求查询确实读取所有镜头修订，而不是只读当前修订；Turbo 分支确实可能改写非 Turbo LoRA；normalizer 确实将表演强度写为 0.5。应优先小修。见 K02—K04。
4. 自动视频路径已读取到固定 `VIDEO_I2V`、前镜尾帧接当前 `END_FRAME`、候选上限 4、强制/过期分支在在途判定之前。先保障当前有效路线，不把这解读为整个项目没有其他视频模式。见 K06—K07、K15—K16。
5. 当前代码允许显式 ProviderConnection 与受控远端确认。**本次不新增默认云回退，也不因“本地优先”删除项目已存在、已明确授权的远端能力。**测试环境使用隔离 transport；生产网络策略沿用现有权限。

精确读取文件和范围见附录 B。参考仓库的具体实现主要沿用附件中的固定提交证据，**本次没有重新逐文件审计七个参考仓库**。

<a id="s2"></a>
## 2. 最终需求与取舍：真正需要的是可靠闭环

### 2.1 首轮产品验收只定义六件事

| 验收目标 | 用户应能看到的结果 | 不能用来冒充完成的东西 |
|---|---|---|
| 原文正确 | 每集能回到固定版本与范围；未处理内容明确列出 | 保存了整章范围，但模型实际只读了前半段 |
| 编辑受保护 | 改过的台词、表演、身份、首尾帧不被自动重置 | 历史版本还在，但当前指针被自动切到改写稿 |
| 执行一致 | 页面、预检、冻结快照、模型请求的 Profile/参考/规格一致 | 参数仅记录在 JSON，界面却声称已生效 |
| 生产可恢复 | 关页、重启、断线后复用原任务或明确待处理 | 本地等待超时就另发一次 GPU 采样 |
| 局部可返工 | 改一镜能看见真实影响；无关镜头不重做 | 忽略硬衔接依赖，宣称任何修改永远只重做一镜 |
| 成果真实 | 当前版本可播放，必要审核与交付状态独立可见 | HTTP 200、文件存在、历史 PASS 或样例视频 |

第一轮保留必要的创作确认和媒体审核，减少的是无意义跳页和重复确认，不是删除所有确认。**“稳定自动推进到可预览成果或明确决策点”比“无条件无人值守正式交付”更适合作为当前目标。**

### 2.2 已有系统继续做权威来源

沿用原稿版本、改编分析节点、EpisodePreparation、AutomationWorkflow、Job/attempt、GenerationVariant、PromptRevision、MediaVersion、工作版本槽位、MachineCheckRun、ReviewDecision、FrameBridge、TimelineRevision、ComposeService 和 GPU 协调器。

一键入口与精修入口只是同一组命令的不同交互密度。不要再造“简易生成实体”“导演 Agent 任务表”“H3 专属媒体库”或第二套审核状态。

依据：D1 §2、D2 §1—2、D3 §2、D4 §2、D5 §2、D6 §2、D7 §2；其中分层分析与实际请求链已在本次源码复核。

### 2.3 七份报告中的冲突与扩张建议，统一这样处理

| 原建议或潜在冲突 | 本文裁决 | 原因与落点 |
|---|---|---|
| 新增章节事件系统、MapReduce 或小说 RAG | **不新建平台**；复用已有分析节点与同源检索 | 现有能力已确认；缺的是范围到本集拆解的衔接，K11—K12 |
| 每份报告分别新增 renderer / compiler / execution plan 模块 | **不照单建立三个并行编译器** | 优先扩展 `shot_prompt_bundle.py` 与现有生成合同；模型语法只在薄适配层，K05/K15 |
| 为匹配计划把候选上限直接开放到 16 | **不默认放大生成量** | 先统一有效值；保留明确上限 4 也可，K06 |
| “一键”应自动通过关键帧批准 | **不采用** | 批量真实确认先行；机器草稿选择属于后续单独政策 |
| 预检只读，所以完全不能接触网络 | **纠正语义** | 允许既有授权范围内的有界健康探测，但必须如实记录；不允许预检偷偷生成/写业务数据，K14 |
| 所有声音默认改用 TTS，或默认全部改为原生 AV | **都不统一强制** | 使用当前已验证路线；同句对白只有一个主来源，K21 |
| 9 图/3 视频/3 音频、九分节、固定帧网格是 H3 通用合同 | **不能直接采用** | 以本机发布的具体模型/节点/Profile 合同为准；未验证扩展默认关闭 |
| 同角色跨镜必须使用相同 Picture 编号 | **稳定身份不等于稳定物理槽号** | 角色/媒体 ID 稳定；每次请求可重分配槽号，但必须同步编译映射，K15 |
| 没有前镜视频就无声跳过衔接 | **按意图区分** | 硬衔接等待；软连续允许明确降级；切镜不继承，K16 |
| 强制把所有 5 秒模型输出统一裁到 120 帧 | **不采用通用裁尾** | 可能丢掉显式尾帧或对白末尾；规格与剪辑端点分别校验，K15/K21 |
| 当前运行失败就把所有媒体都显示失败 | **运行状态与产物资格分开** | 当前运行可失败，但未受影响旧产物仍可合法复用，K18 |
| 只接受当前 run 新生成产物才算完成 | **不采用** | 输入匹配且当前有效的历史产物本来应复用，K17—K18 |
| 修复重复调用只靠前端禁用按钮/进程内锁 | **不足** | 命令身份、在途任务和外部受理证据需持久对应，K07/K22/K23 |
| 本地优先等于删除所有 ProviderConnection | **不采用** | 不新增默认外传，也不破坏用户已授权路线；权限边界保持 |
| 对七份报告所有 P0/P1 同时开工 | **不采用** | 优先级编号并不统一；按实际风险、执行路径和依赖重新排序 |
| 为“统一架构”立刻合并/删除旧 WHOLE_DRAMA 模板 | **暂不重写历史模板** | 新产品入口选定一个权威计划；旧快照保留兼容，K28/K30 |
| 空人物结果永远错误，或永远允许 | **按当前产品合同验证** | 已读故事综合当前要求人物；本轮不借质量聚合顺带支持全部新题材，K13 |

**停止线：**没有真实调用路径、没有可观察失败条件、没有消费者的新增字段，不进入实施；存在同义实现时优先接线和补测试。

<a id="s3"></a>
## 3. 最小目标设计：只收敛必要合同

### 3.1 一条主流程，不新增生产引擎

```text
用户明确选择：固定原稿范围 + 已有制作方案 + 目标 + 预算/检查点
    ↓
规划或复用已有分析结果：输出覆盖与来源证据
    ↓
按授权应用无冲突文本计划；有人工编辑时先显示影响
    ↓
本集缺镜头 → EpisodePreparationService.prepare → 等待既有 Job
    ↓
准备结果已应用 → 重新读取事实 → 形成下一阶段新冻结快照
    ↓
身份/参考候选与关键帧 → 集中人工选择/确认
    ↓
当前有效 Profile 与参考、时长、声音、候选预算的精确预检
    ↓
既有视频 Job → 制品校验 → 候选资格/QC → 工作版本采用
    ↓
现有对白/字幕/时间线/Compose → 可播放预览
    ↓
正式审核/交付（保留当前政策，不因预览存在而自动通过）
```

只读校验器负责回答“事实是否满足”，命令负责产生变化。`prepare()` 返回“已有镜头”只说明该命令无需再创建，不代表一集完整、更不代表生产就绪；没有待确认镜头也可能是根本没有镜头。

### 3.2 只需要收敛五类含义，不需要五张新表

| 合同含义 | 回答什么 | 优先承载位置 |
|---|---|---|
| 源范围绑定 | 本次读哪份不可变文本、哪一段 | 既有源版本关系、`source_range_json`、计划/草案快照 |
| 有效执行计划 | 实际模型、工作流、媒体槽位、提示词、采样和时长是什么 | 既有生成计划、Profile/Workflow 绑定、Variant 冻结数据 |
| 命令身份 | 同一次操作的重发，还是用户真的要新拍一版 | 现有命令幂等记录、Job/automation task 引用与唯一约束 |
| 当前产物资格 | 历史可看、当前有效、已采用、已批准分别是什么 | 现有生产读模型、工作槽、媒体完整性、QC/Review |
| 后端推进意图 | 完成前一步后，用户授权继续到哪里 | 现有 pipeline/run 元数据、Job 依赖与可靠完成事件 |

这些是需要统一的业务含义，不是本文件新发明的通用中间语言。实施前查同义字段；小的 JSON 合同扩展优于无收益的新表，但不能因为“不建表”而把需要事务一致的事实藏进不可查询的巨型字符串。

### 3.3 指纹与运行条件分开

**生成输入指纹**包含真正影响输出的版本、最终文本、有效参数、参考内容与硬衔接来源。**命令幂等键**标识一次用户操作。**attempt**标识同一任务的一次执行。**实际 LLM 请求摘要**覆盖最终 system/user、实际检索片段与推理配置。这几者不能互相替代。

磁盘余量、运行进度、浏览器导航窗口、探测时间、无关集草稿不应改变创作内容指纹；它们可参与启动门槛或展示。新模型、实际参考图、原生对白、前驱媒体版本变化，才可能使生成输入变更。

缓存不是首轮新权威。未来若引入条件编码缓存，它的依赖只覆盖编码阶段，不能用“角色姓名”错误复用，也不应把采样 seed 一律放进语义缓存键让复用失效。

### 3.4 输出统一解释，不统一制造新状态机

新读模型至少表达：运行是否活动、当前有效产物数、已采用数、待确认数、失败数、复用数、阻塞原因与下一步动作。具体枚举沿用现有 API，必要时增加展示字段，不把说明用标签直接写进数据库执行枚举。

一个正确结果可以是：“第 2 集仍在运行，第 1 集待确认首帧；已有 8 镜有效复用，2 镜需新生成。”不应压缩成一条模糊的 READY，也不能把机器 QC PASS、采用成功和人工批准合成一个绿色布尔值。

<a id="s4"></a>
## 4. Phase 路线：一阶段一个门槛，不按功能数量推进

### 4.1 总路线与可以停下来的位置

| 阶段 | 唯一主要目标 | 主线工作包 | 交付门槛 |
|---|---|---|---|
| **Phase 0** | 确认真实基线、实际路线、现有测试与最小复现 | 核验 K01—K30 的适用性，不改业务实现 | 每项有处理结论；真实入口、网络/GPU边界和基线失败已记录 |
| **Phase 1** | 小修止损：不误改、不假成功、不重复新增候选 | K01—K08 | 相关纯逻辑/装配/SQLite/UI用例实际通过；未引入新平台 |
| **Phase 2** | 源内容可信：不串稿、不漏范围、可恢复分析 | K09—K13 | 长短原稿范围、草案/请求身份、应用门禁一致 |
| **Phase 3** | 执行与结果可信：用对模型、参考、规格、声音与当前版本 | K14—K21 | 计划→冻结→执行→当前成果→显示/导出贯通 |
| **Phase 4** | 恢复与精修可信：重放不重抽，局部修改不扩散 | K22—K26 | 故障注入与局部修改的任务数/影响集合准确 |
| **Phase 5** | 一个单集的持久闭环：准备、集中确认、生产、预览 | K27—K29 | 关闭页面仍推进或停在正确决策点，不覆盖人工成果 |
| **Phase 6** | 小样发版与所选多集验证 | K30 | 分层验收通过，两个所选集不放大断点；可停止本轮开发 |

**Phase 1 是第一批可合并成果，Phase 5 是产品闭环目标，Phase 6 是是否推广使用的门槛。** Phase 6 后不是自动进入缓存、Agent 或全模式扩展；后续只能按第 6 节的触发条件立项。

一项工作包的合格结论可以是：`FIX_MINIMAL`、`TEST_ONLY`、`ALREADY_COVERED`、`DEFERRED_NOT_ON_ROUTE`、`NEEDS_DECISION`。这些是实施记录状态，不是要新增到业务数据库的枚举。尚未解决且处于当前主链的阻断问题，不能被标为 DEFERRED 后照常批量生产。

### 4.2 Phase 0：先建立真实基线

**要读：** 当前仓库规则/README、Python/前端配置、实际 API 路由、service composition、Worker 装配、当前项目使用的已发布 Profile 与工作流，以及每个候选修复的直接调用方和现有测试。用户本地未提交修改、真实模型安装与运行数据库未由本次读取提供，应从实施环境读取，不把报告中的机器型号作为当前配置。

**要产出：** 一份轻量基线记录，包含 commit/dirty files、当前单镜/单集/整剧入口、旧 GPU_H3 或 V2 实际分流、已启用声音/衔接政策、相关已有测试和已知失败、每个 K 编号的处置及证据。只记录版本与必要配置摘要，不导出密钥和整部小说。

**最小复现顺序：** 先 K01 客户端签名、K03 当前 cue、K04 多 LoRA、K06 数量、K07 重入，再核对 K02 人工保护及 K08 状态。尽量使用真实装配和 fixture，只有模型 transport 被替换。

**退出条件：** 核心主链无“只看文件名猜功能”的开发项；现有测试失败与新增复现失败可区分。确认 GPU 测试需单独授权；发现危险自动衔接或重复提交路径时，先形成禁用新批量运行的建议，不擅自中断操作者正在运行的任务。

**本阶段不做：** 架构重构、依赖升级、数据库清理、模型下载、整部小说生产。风险已被现有代码正确处理的，保留测试说明并关闭开发项。

### 4.3 Phase 1：八个小工作包，不是一个大提交

建议拆为：P1-A/K01；P1-B/K03；P1-C/K04；P1-D/K05；P1-E/K06；P1-F/K07；P1-G/K02 的规则与确认分两次；P1-H/K08。具体合并顺序可以按当前可复现阻断调整，但不要让多个 AI 同时无协调改 `episode_worker_actions.py`。

**退出条件：** 显式 Profile 可由真实装配解析；人工合法值保留；旧 cue 不污染当前；Turbo 只改目标；speaker 保留；候选目标一致；同意图重入不新增候选；失败/暂停/0/N 不报全成功。当前未用 Turbo 的图可用 fixture 验证，不必安装/启用 Turbo。

**本阶段价值：** 现有手工制作更可靠，即使后续阶段暂停也有独立收益。尚未解决的帧衔接、来源等风险不能因这批测试通过就宣称整条“一键”已安全。

### 4.4 Phase 2：先正确报告范围，再实现需要的有界续接

建议 P2-A/K09 源绑定及唯一回填；P2-B/K10 覆盖和部分完成止损；P2-C/K11 复用已有分层分析；P2-D/K12 草案/检查点，再单独请求摘要/计数；P2-E/K13 质量与应用影响。

**退出条件：** A/B 原稿不串；超长章尾部要么真正参与有界分析，要么准确标未处理；4,000 编号文本边界不在准备时突然无解；失败窗口可续接；检索不让重试输入漂移；应用旧草案不能污染当前人工作品。

**停止边界：** 已有分析节点满足本集预算时不增加摘要步骤；当前只需短篇时可以先上线准确的超限阻断，但必须将长篇续接记为未实现，不能继续宣传完整长小说自动制作。

### 4.5 Phase 3：同一份输入、同一份当前结果

建议 P3-A/K14 精确依赖；P3-B/K15 当前路线编译穿透；P3-C/K16 帧语义→写入一致性→依赖；P3-D/K17 与 K18 共用资格解析；P3-E/K19 两个独立 UI 修复；P3-F/K20 导出；P3-G/K21 声音与时长。

**退出条件：** 同一输入从手工和自动入口得到相同有效计划；上下镜没有错槽；已有好候选可优先复用；采用失败不被 QC PASS 掩盖；当前视频/音轨/时间线的版本关系真实；长列表和批次不遗漏；V2 当前工作版本可导出。

**停止边界：** 单一已验证 I2V 路线也可以通过本阶段。新增 T2V、Ref2VA、多视频/音频参考不是必需条件。已存在混音或参数解释能力时只补接口和测试。

### 4.6 Phase 4：故障与局部编辑不能制造新工作

建议 P4-A/K22 启动幂等；P4-B/K23 先核验已有补偿，必要时分别修 receipt/恢复和生产预算；P4-C/K24 依赖收窄；P4-D/K25 稳定镜头匹配；P4-E/K26 拆镜引用。

**退出条件：** 知道外部已受理时不重复采样；未知受理状态可安全停止并诊断；本地取消与外部停止可区分；无关集修改不使当前集失效；插入一镜不把后续全部认成修改；拆镜不丢身份、不复制批准和整句台词。

**停止边界：** 未启用 V2 时其专项延期不阻止旧路线完成验证；但即将通过新功能启用 V2 时，K23 必须重新成为前置门槛。没有使用 Beat Replan/拆镜的首个样片不要求提前扩展这些入口，现有能力维护则不能绕过回归。

### 4.7 Phase 5：后端持续推进，人工只做真正决策

建议 P5-A/K27 持久授权与文本应用；P5-B/K28 单集准备/生产衔接；P5-C/K29 操作和影响说明。K28 中一个阶段调用一个已有命令，缺哪个命令就明确暴露，不造包含所有行为的长函数。

**退出条件：** 从零镜头的已确认分集开始，系统能自动准备并推进到预览，或者稳定暂停在必要身份/关键帧/创作冲突决策点；处理之后继续原意图。浏览器关闭不丢续接，重复回调不重复应用，人工已选内容不被下一次一键覆盖。

**本阶段禁止：** 为追求零点击偷偷写 APPROVED；给模型任意 SQL/文件/网络工具；在同一冻结快照里替换准备后的新镜头；把占位片、缺镜拼接或长静帧标成完整成片。

### 4.8 Phase 6：验收后推广，不能先放大到全书

先按第 7 节执行离线与浏览器回归，再经操作者允许运行一个小型已验证模型样片，最后验证两个所选集的有限推进。视频模型、分辨率、采样步数、音频策略与候选数固定，便于辨别改代码带来的变化。

**退出条件：** 实际提交集合与预检预告一致；重复任务为 0 的断言仅针对已覆盖故障条件；当前成果和审核证据真实；至少一条单集闭环和一条局部修改/恢复路径有可复查证据；未做的环境或模型组合明确记录。

**此处可以结束本轮。** 只有具体制作任务仍反复遇到瓶颈，才从条件性增强中选一个小项试验。

<a id="s5"></a>
## 5. 三十个工作包：准确改动、依据与验收

下面的路径默认链接到本次复核的远端 HEAD；不表示每个链接文件都在本次完整重读。SRC 的实际读取范围见附录 B，其他依据来自对应附件。所有“新增字段/枚举/函数”均先查现有同义实现。

每个工作包限定一个可交付目标，复杂包可以拆多个 PR；不要按“一个编号＝一个新文件/新服务”执行。相关测试优先扩展现有文件和 fixture。

| 工作包 | 阶段 | 目标 |
|---|---|---|
| [K01](#k01) | Phase 1 | 修正故事规划的 Profile 客户端真实装配合同 |
| [K02](#k02) | Phase 1 | 自愈只补缺失技术值，保护人工语义与能力声明 |
| [K03](#k03) | Phase 1 | 当前音轨需求不再被历史镜头修订污染 |
| [K04](#k04) | Phase 1 | Turbo 参数只作用于已识别的 Turbo 节点 |
| [K05](#k05) | Phase 1 | 提示词保留说话人和逐字台词 |
| [K06](#k06) | Phase 1 | 统一候选数量、预算、执行目标与进度分母 |
| [K07](#k07) | Phase 1 | 修复 force_new_take / stale 路径的重入候选身份 |
| [K08](#k08) | Phase 1 | 整剧状态和 0/N 调度反馈先说真话 |
| [K09](#k09) | Phase 2 | 分集、准备、重规划和检查统一绑定原稿版本 |
| [K10](#k10) | Phase 2 | 显式原文覆盖与超预算止损，不再把部分分析显示成全书完成 |
| [K11](#k11) | Phase 2 | 复用已有分层分析，把本集范围落到有界拆解任务 |
| [K12](#k12) | Phase 2 | 草案/检查点复用与实际 LLM 请求身份一致 |
| [K13](#k13) | Phase 2 | 统一草案质量门禁，并在应用前显示影响 |
| [K14](#k14) | Phase 3 | 逐镜解析有效 Profile，并精确检查当前所需依赖 |
| [K15](#k15) | Phase 3 | 单镜和自动生成使用同一有效输入与规格编译结果 |
| [K16](#k16) | Phase 3 | 统一帧桥语义、前驱依赖、幂等与提取来源 |
| [K17](#k17) | Phase 3 | 先利用已有合格候选，并传播采用失败 |
| [K18](#k18) | Phase 3 | 当前生产完成度复用同一媒体资格读模型 |
| [K19](#k19) | Phase 3 | 把全量统计、分页列表与导演批次集合分开 |
| [K20](#k20) | Phase 3 | 联系表导出读取 V2 当前工作版本而非旧镜像关系 |
| [K21](#k21) | Phase 3 | 声音与时长按实际路线冻结，复用对白越界防线 |
| [K22](#k22) | Phase 4 | 整集/整剧启动具有稳定端到端命令身份 |
| [K23](#k23) | Phase 4 | 核验两条 Comfy 路线的受理、超时、取消与恢复 |
| [K24](#k24) | Phase 4 | 缩小指纹依赖到实际消费对象，保留真正共享约束 |
| [K25](#k25) | Phase 4 | Beat Replan 用稳定镜头身份匹配，避免插一镜重写后续 |
| [K26](#k26) | Phase 4 | 拆镜继承声明性身份引用，不继承已生成结果 |
| [K27](#k27) | Phase 5 | 将已授权的规划应用续接移到后端 |
| [K28](#k28) | Phase 5 | 从零镜头到单集预览，复用准备、审核、生产服务 |
| [K29](#k29) | Phase 5 | 明确四种操作与影响预览，精修复用同一命令 |
| [K30](#k30) | Phase 6 | 建立小样发版门槛，再验证所选多集有限推进 |

<a id="k01"></a>
### K01 · 修正故事规划的 Profile 客户端真实装配合同

**Phase 1｜证据：SRC + VERIFY｜状态：已完成实现与回归（2026-09-13）**

**原报告依据：** D4 §4.1 / F01、F36；D2 §3/F18 的穿透测试方法。

**前置条件：** Phase 0 已完成；确认该路径仍存在且在本轮范围内。

**本项目读取/修改位置：**

- [`apps/api/local_drama/infrastructure/service_composition.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/infrastructure/service_composition.py) — `build_story_ai`
- [`apps/api/local_drama/application/story_pipeline_ai.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/story_pipeline_ai.py) — `FullStoryAIGenerationService.resolve_client`
- [`apps/api/local_drama/application/local_llm.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/local_llm.py) — `LocalLLMService.client`
- [`apps/api/local_drama/application/ports/creative_generation.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/ports/creative_generation.py) — `LocalLLMClientProviderPort`

**最小改造：** 调用方使用 `client(profile_version_id=...)`，真实实现却不接受该关键字。先复用已有 Profile/连接解析，选择一个修复方向：为现有服务提供明确的 Profile 入口，或在装配处注入满足端口的薄适配器。端口、调用方、实现与测试一起对齐。所选已发布 Profile 的模型、ProviderConnection、网络边界和密钥来源必须真实生效；无显式选择时也要明确记录默认解析结果。

**参考代码怎么学：** 不需要移植参考仓库代码。依据本项目自身合同与回归用例修复。

**验收断言：** 用真实 `build_story_ai()` 装配，只 mock 最外层 HTTP transport：显式 Profile、默认 Profile、未发布/撤销/能力不符、ProviderConnection 与本地连接各一例。断言实际请求模型/端点来自解析结果，而不是只断言“不再抛 TypeError”。

**不做/停止线：** 禁止删掉 profile_version_id 改为无参调用；禁止用 **kwargs 接收后忽略；不新增模型平台，不改变用户默认模型。

**回滚与历史兼容：** 回退这一组接口和装配修改；新任务先暂停，历史计划与 Profile 不重写。

<a id="k02"></a>
### K02 · 自愈只补缺失技术值，保护人工语义与能力声明

**Phase 1｜证据：SRC + DOC + VERIFY｜状态：已完成实现与回归（2026-09-13）**

**原报告依据：** D3 §4/F09、F10、F11；D4 F38；D7 E07。

**前置条件：** Phase 0 已完成；确认该路径仍存在且在本轮范围内。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/shot_production_normalizer.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/shot_production_normalizer.py) — `normalize_fields / infer_camera_movement`
- [`apps/api/local_drama/application/episode_shot_ready.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_shot_ready.py) — `confirm / _camera_contract / _resolve_fields`
- [`apps/web/src/features/director-v2/DirectorIntentEditor.tsx`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/src/features/director-v2/DirectorIntentEditor.tsx) — `现有归一化与人工编辑`

**最小改造：** 分成两个小 PR：先修纯规则，再修确认命令。合法显式强度必须保留，0 不是缺值；只有缺失值可采用技术默认。角色“推门、拉椅子、摇头”不能凭单字改变运镜；无法推断时不虚构创作事实。UNSUPPORTED 仅在 Profile 允许文本回退时才能降级。冻结旧 revision 不等于任何自动新建当前稿都合理：确认真实冻结/锁定语义，自动语义改动只产建议分支，不能静默切换当前指针。审计标注真实 actor 与 decision kind。

**参考代码怎么学：** 不需要移植参考仓库代码。依据本项目自身合同与回归用例修复。

**验收断言：** 强度 0/0.2/0.9 保留；二次 normalize 幂等；动作词不误判运镜；允许/禁止 fallback 分别正确；冻结镜头经整剧 auto_heal 后台词、服装、机位不变。人工明确接受新分支仍可正常生效。

**不做/停止线：** 不写一个新导演规则引擎；不把所有空字段填上泛化语句后称为“创作完成”；不将机器技术确认登记为人工批准。

**回滚与历史兼容：** 可回退规则与入口；已经创建的建议分支保留历史。存在安全疑问时关闭自动语义补全，而不是恢复静默改写。

<a id="k03"></a>
### K03 · 当前音轨需求不再被历史镜头修订污染

**Phase 1｜证据：SRC + VERIFY｜状态：已完成实现与回归（2026-09-13）**

**原报告依据：** D6 LDS-04；D2 F06、D7 F05 是相关配音预检，不与本项混为同一缺陷。

**前置条件：** Phase 0 已完成；确认该路径仍存在且在本轮范围内。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/audio_requirements.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/audio_requirements.py) — `_field_cue_counts / canonical_audio_requirements`

**最小改造：** 字段 cue 查询改为由当前、未归档 Shot 关联其 current_revision_id；保留空值、可选旧 cue 表等既有兼容逻辑。当前修订缺失或关系损坏应显式诊断，不能退回扫描全部历史。先限定修 _field_cue_counts，不顺带重构对白和整个音频数据层。

**参考代码怎么学：** 不需要移植参考仓库代码。依据本项目自身合同与回归用例修复。

**验收断言：** SQLite fixture：旧版有 BGM、当前版删除；归档镜头有 SFX；当前仍有非空 cue；null/空字符串/空列表/空对象。断言需要的音轨与当前事实一致，历史 revision 数量不变。

**不做/停止线：** 不删除旧 cue/旧版本，不新增“音轨需求状态表”，不强迫没有对白或音乐事实的集补齐固定三条轨。

**回滚与历史兼容：** 代码级回滚即可；本项不应修改历史数据。

<a id="k04"></a>
### K04 · Turbo 参数只作用于已识别的 Turbo 节点

**Phase 1｜证据：SRC + VERIFY｜状态：已完成实现与回归（2026-09-13）**

**原报告依据：** D6 LDS-05、LDS-28；D2 B14 的输入注入测试方法。

**前置条件：** Phase 0 已完成；确认该路径仍存在且在本轮范围内。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/comfy_jobs.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/comfy_jobs.py) — `ComfyGenerationService._apply_effective_configuration`
- [`apps/api/local_drama/application/h3_workflows.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/h3_workflows.py) — `H3WorkflowFactory / Turbo 绑定`

**最小改造：** 当前分支虽识别 Turbo 节点，后续却对全部 LoraLoaderModelOnly 写 strength_model。最小补丁只修改已识别且契约允许的 Turbo 节点；其他身份/风格/必需 LoRA 原样保留。新增 Turbo 的模型链不得依靠“节点 1 永远是主模型”的无条件假设；不明确时阻塞或沿当前已验证模板执行。更广的 width/cfg 等全图覆盖在 K15 单独核验，不把两项变成任意图重写工程。

**参考代码怎么学：** 不需要移植参考仓库代码。依据本项目自身合同与回归用例修复。

**验收断言：** 图包含 Turbo、角色 LoRA、第二采样器。分别切换 OFF/TURBO、调整强度；对非授权节点做结构差异断言，原始发布图不变。目标缺失/歧义不能随便选一个 LoRA。

**不做/停止线：** 不重新扫描和改写所有历史工作流；不为小循环修复引入通用 Comfy 图编辑平台。

**回滚与历史兼容：** 回退编译补丁或停用相关新参数覆盖；既有发布图和已完成媒体不变。

<a id="k05"></a>
### K05 · 提示词保留说话人和逐字台词

**Phase 1｜证据：DOC + VERIFY｜状态：已完成实现与回归（2026-09-13）**

**原报告依据：** D1 F06/T17；D2 F15；D4 F19；D5 F19—F21；D6 LDS-10。

**前置条件：** Phase 0 已完成；确认该路径仍存在且在本轮范围内。

**本项目读取/修改位置：**

- [`apps/api/local_drama/domain/shot_prompt.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/domain/shot_prompt.py) — `compose_shot_prompt`
- [`apps/api/local_drama/domain/shot_prompt_bundle.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/domain/shot_prompt_bundle.py) — `compile_shot_prompt_bundle / apply_prompt_bundle_to_parameters`

**最小改造：** 先修 `text or speaker` 信息丢失，不先设计完整 H3 九分节输出。输入同时有 speaker 与 text 时保留两者；数据库稳定角色/对白身份优先，旁白独立处理。最终执行台词逐字来自已接受的对白版本，不在提交时润色。缺少说话人且该场景必须确定角色时返回待确认，而不是猜一个人。角色显示名改变不能自动改变声音绑定。

**参考代码怎么学：** D5 / [`console/rules/h3_expand.md`](https://github.com/qiukaihui/comfyui-auto-drama/blob/362c8fd860bbbafda4b504de4f7bbe0c9a3d0d24/console/rules/h3_expand.md) 的“对白正文与说话者/动作分离”方法；具体路径以 D5 来源索引核对，移植语义而非正则和整段模板。

**验收断言：** A/B 轮流说话、同名不同角色、旁白、无对白、冒号/引号/换行、部分标签已有。断言最终文本保留归属且不改变台词。自动/单镜入口使用同一编译结果；现有独立 TTS 绑定不得回归。

**不做/停止线：** 不增加第二份对白事实，不把 donor 的 <d> 或 Picture 语法强加所有模型，不因修文本自动切换声音路线。

**回滚与历史兼容：** 新编译版本只影响新计划；旧 Prompt/Variant 冻结内容保留，回滚不改历史字符串。

<a id="k06"></a>
### K06 · 统一候选数量、预算、执行目标与进度分母

**Phase 1｜证据：SRC + DOC + VERIFY｜状态：已完成实现与回归（2026-09-13）**

**原报告依据：** D1 F01/T01；D4 F12；D7 F06；D2 F16。

**前置条件：** Phase 0 已完成；确认该路径仍存在且在本轮范围内。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/episode_production_runs.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_production_runs.py) — `_resolved_mode_policies`
- [`apps/api/local_drama/application/episode_worker_actions.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_worker_actions.py) — `video_generation`
- [`apps/web/src/features/episode-production-v2/EpisodeProductionWorkspace.tsx`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/src/features/episode-production-v2/EpisodeProductionWorkspace.tsx) — `模式/计划展示`

**最小改造：** 一个解析结果产生请求数、有效数和限制原因，预检确认后冻结。第一版可以维持现有单机最高 4 的产品边界，但必须在预检显示并确认，不能前端估 16、Worker 偷偷执行 4。普通一键默认优先少量候选，额外候选仅按显式策略。候选总目标、已有效复用、活动候选、技术重试次数分别计数；不能通过减少进度分母掩盖失败。

**参考代码怎么学：** 不需要移植参考仓库代码。依据本项目自身合同与回归用例修复。

**验收断言：** 参数化 1/2/4/6/8/16 和三种模式；合法计划与最终目标一致，超限明确拒绝或确认有效值。已有 3 个有效候选、目标 4 时只补 1 个；在途候选计一次；同一任务 retry 不成为新创意候选。

**不做/停止线：** 不把候选数当 GPU 并发；不借修一致性提高默认成本；不新建预算中心。

**回滚与历史兼容：** 新策略带版本，回退只影响新 run；旧冻结计划按旧合同执行或明确停止，不在途中静默重算。

<a id="k07"></a>
### K07 · 修复 force_new_take / stale 路径的重入候选身份

**Phase 1｜证据：SRC + VERIFY｜状态：已完成实现与回归（2026-09-13）**

**原报告依据：** D7 F09/T04；D1 §13 数量恢复；D6 LDS-14、LDS-20。

**前置条件：** Phase 0 已完成；确认该路径仍存在且在本轮范围内。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/episode_worker_actions.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_worker_actions.py) — `video_generation / _submit_shot / _variant_jobs`
- [`apps/api/local_drama/application/worker_handlers/automation_task.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/worker_handlers/automation_task.py) — `生成动作重入与依赖处理`
- [`apps/api/local_drama/application/jobs.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/jobs.py) — `既有幂等/attempt`

**最小改造：** 同一次“新拍一版”在接受时固定命令 ID、输入指纹和候选序号；重入先找该意图对应的在途/已完成任务。不能每次用变化中的 len(jobs) 产生新幂等键。素材过期不意味着每次 Worker 进入都应再新增一个 take。用户明确再次换版才产生新命令；技术重试沿原候选/参数，不自动换 seed。检查当前 _variant_jobs 返回范围，活动任务必须匹配本次输入/模式，不能以无关历史 Job 抵充目标。

**参考代码怎么学：** 不需要移植参考仓库代码。依据本项目自身合同与回归用例修复。

**验收断言：** 同 run/task 连续调用两次；force_new_take 网络重发；stale 且已有匹配活动任务；提交后进程退出；同 key 不同 payload；用户第二次明确换版。断言 Job、Variant 数量与实际 provider 提交次数，不只检查返回状态。

**不做/停止线：** 不只加前端 disabled 或进程内锁；不承诺外部网络 exactly-once；不另建队列。

**回滚与历史兼容：** 回退实现前暂停新自动派发；保留已创建命令与候选映射，不通过删任务“去重”。无法安全重放的旧任务进入诊断。

<a id="k08"></a>
### K08 · 整剧状态和 0/N 调度反馈先说真话

**Phase 1｜证据：DOC + VERIFY｜状态：已完成实现与回归（2026-09-13）**

**原报告依据：** D1 F04；D3 F06/F08；D5 F02/F03；D6 LDS-03。

**前置条件：** Phase 0 已完成；确认该路径仍存在且在本轮范围内。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/whole_drama_orchestrator.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/whole_drama_orchestrator.py) — `inspect / run`
- [`apps/web/src/features/pipeline/pipelineClient.ts`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/src/features/pipeline/pipelineClient.ts) — `整剧响应类型`
- [`apps/web/src/features/pipeline/OneClickPipelineWorkbench.tsx`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/src/features/pipeline/OneClickPipelineWorkbench.tsx) — `wholeDramaMutation 结果展示`

**最小改造：** 先增加纯聚合真值表：无集、未开始、活动、暂停/待人工、失败、取消、部分完成、完成。混合状态保留分项计数，不用一个主状态吞掉失败。关联本次实际子 run/episode ID，不靠工作流首节点顺序或短 ID 的 LIKE 匹配。HTTP 200 且 0/N 应显示“未启动及原因”，部分成功显示具体阻塞集；已调度不是已生成。当前媒体资格的完整收口在 K18，而不是此处发明第三套 SQL。

**参考代码怎么学：** 不需要移植参考仓库代码。依据本项目自身合同与回归用例修复。

**验收断言：** 0 集、全失败、全暂停、全取消、全成功、运行+失败、成功+未开始；0/3、1/3、3/3 调度。更改 workflow 节点排序仍对应正确集；刷新/重新聚焦能看到真实活动与恢复状态。

**不做/停止线：** 不为所有展示标签增加数据库枚举；不改历史执行状态来配合界面；不建设新任务中心。

**回滚与历史兼容：** 响应扩展保持兼容，回退界面可保留旧字段；不要恢复将未知/失败统一显示 READY 的逻辑。

<a id="k09"></a>
### K09 · 分集、准备、重规划和检查统一绑定原稿版本

**Phase 2｜证据：DOC + VERIFY｜状态：已完成实现与回归（2026-09-13）**

**原报告依据：** D2 F02/T04—T05；D3 F01；D4 F02；D5 F06；D7 F02。

**前置条件：** Phase 0 已完成；确认该路径仍存在且在本轮范围内。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/pipeline_orchestrator.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/pipeline_orchestrator.py) — `apply_pipeline / 分集 source_range_json 写入`
- [`apps/api/local_drama/application/episode_preparation.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_preparation.py) — `_context / prepare`
- [`apps/api/local_drama/application/episode_replan.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_replan.py) — `request / _project_context`
- [`apps/api/local_drama/application/episode_front_half_actions.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_front_half_actions.py) — `_committed_source / _ready_breakdowns`

**最小改造：** 优先从现有计划、源版本和应用审计恢复明确来源。统一解析 source_document_version_id、import_session_id、提取文本 SHA 与范围；准备/重规划/草案复用/前半段检查都沿这一绑定，不再隐式选项目最新导入。旧数据只能在证据唯一时回填；多份候选源则待用户明确绑定。项目新导入只产生迁移提案，不自动替换旧集原文。只读检查草案也应限目标集和实际来源，不能拿别集 DRAFT_READY 当本集待办。

**参考代码怎么学：** D3 / [`src/agents/scriptAgent/tools.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/src/agents/scriptAgent/tools.ts)：`get_novel_text / get_novel_events` 只借明确寻址；你的定位必须进一步包含不可变源版本。

**验收断言：** A/B 两份原稿段号相同、人物不同；A 规划后再导 B，准备和重规划 A 仍读取 A。源内容改动、跨项目未授权引用、源版本不存在、旧数据歧义、同集旧草案各一例。合法授权素材引用和原文所属关系按各自既有规则校验，不机械套一个全局禁用规则。

**不做/停止线：** 不新建 Source 主数据库；不批量把旧集回填成最新原稿；不以文件名相同证明来源相同。

**回滚与历史兼容：** 先做迁移 dry-run 与一致性备份；新字段向后兼容。无法安全回退绑定的项目只读/暂停，禁止回滚到“猜最新稿”。

<a id="k10"></a>
### K10 · 显式原文覆盖与超预算止损，不再把部分分析显示成全书完成

**Phase 2｜证据：SRC + DOC + VERIFY｜状态：已完成实现与回归（2026-09-13）**

**原报告依据：** D2 F01；D3 F02/F03；D4 F03/F30；D6 LDS-01、LDS-18；D1 T33。

**前置条件：** K09；K01 已保证真实 LLM 装配可测试。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/pipeline_orchestrator.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/pipeline_orchestrator.py) — `_episode_specs / execute_draft_generation`
- [`apps/api/local_drama/application/story_pipeline_ai.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/story_pipeline_ai.py) — `_episode_prompt / generate`
- [`apps/web/src/features/pipeline/OneClickPipelineWorkbench.tsx`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/src/features/pipeline/OneClickPipelineWorkbench.tsx) — `覆盖与部分完成展示`

**最小改造：** 先交付可独立上线的止损：记录本次授权范围、实际已处理区间、未处理区间、明确排除原因和续接位置。60 是一批的安全上限，不是“小说只有前 60 个单元”；24,000 字符切片不能仍声称分析整章。尚未接通长范围处理时明确 PARTIAL/待拆分，禁止自动宣称全书完成。覆盖以成功完成约定分析与结构校验的源区间并集计算，区分已提交与已完成；摘要质量仍需检查，覆盖完整不等于语义理解绝对正确。

**参考代码怎么学：** D6 / [`segment_engine.py`](https://github.com/karuvanan/MiniMax-H3-Director-Cut-Studio/blob/e093e836a014b65a3fc079a166ba9c88685cd4ff/segment_engine.py)：`ranges_intersect / 窗口覆盖思路` 仅借区间不变量，不搬视频秒数和分段常量到原文。

**验收断言：** 61/120/121 章、超 24,000 字符单章、超长单段、无章节长文、重叠窗口、emoji、只选择前 10 章。尾部放唯一事件；要么有处理证据，要么准确显示未处理。区间并集不重复累计，失败窗口不算完成，源 SHA 改变拒绝旧游标。

**不做/停止线：** 不简单把 60 改成 600 或扩大 num_ctx；不在本包强行实现新的分块引擎；不把字符数称为精确 token。

**回滚与历史兼容：** 回退展示时仍保留覆盖记录；新旧覆盖 schema 分版本，禁止把旧的未知覆盖自动视为完整。

<a id="k11"></a>
### K11 · 复用已有分层分析，把本集范围落到有界拆解任务

**Phase 2｜证据：SRC + DOC + DESIGN｜状态：已完成实现与回归（2026-09-13）**

**原报告依据：** D7 F03/E18；D4 §4.3/F03；D2 T02/T03；D3 E01/E02 需按已有能力收敛。

**前置条件：** K09、K10；确认现有分析和拆解应用合同。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/adaptation_analysis_execution.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/adaptation_analysis_execution.py) — `execute_node / _user_input / 既有 CHUNK_MAP 等阶段`
- [`apps/api/local_drama/application/episode_preparation.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_preparation.py) — `prepare`
- [`apps/api/local_drama/application/local_llm.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/local_llm.py) — `enqueue_breakdown / _numbered_source_paragraphs`
- [`apps/api/local_drama/application/breakdown_execution.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/breakdown_execution.py) — `既有执行方案解析`
- [`apps/api/local_drama/application/breakdown_apply.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/breakdown_apply.py) — `BreakdownApplyService 应用权威`

**最小改造：** 先核对现有改编计划是否已能向本集提供可直接消费的分块和边界；若可以，仅补接入。确有缺口时，保持已确认 episode 与其来源范围不变，用既有分析节点/Job 表达少量相邻子范围。按真正发送的编号文本计量 4,000 字符限制，预留系统上下文与输出预算；超长单段按句/字符偏移切分并保留源映射。分段结果通过已有草案机制汇总，校验覆盖、顺序、重复场景/台词和时长后一次受版本约束地应用。短集不强制多一轮摘要。

**参考代码怎么学：** D3 / [`src/utils/cleanNovel.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/src/utils/cleanNovel.ts)：`processChapter` 的任务粒度与 D3 / [`src/agents/scriptAgent/tools.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/src/agents/scriptAgent/tools.ts)：`摘要后回读原文`；不移植内存队列，先复用本项目分析节点。

**验收断言：** 实际编号输入 3,999/4,000/4,001；5,001 字单段；乱序完成、中段失败重启、重叠边界对白。完成后场景/镜头只应用一次，尾段证据能追溯，所有输出不越过确认范围。不能把全部分块原文再次无界塞进最后一次综合。

**不做/停止线：** 不新建章节分析 DAG/RAG，不把每个解析 chunk 变成正式新集，不对本书所有角色状态提前批量生图。

**回滚与历史兼容：** 保留旧单范围路径；有界拆解分支关闭后，超限范围明确阻塞而不截断。已成功分析节点和原稿不删除。

<a id="k12"></a>
### K12 · 草案/检查点复用与实际 LLM 请求身份一致

**Phase 2｜证据：SRC + DOC + VERIFY｜状态：已完成实现、真实本地 LLM 与回归（2026-09-13）**

**原报告依据：** D7 F11；D2 F17/T05/T32/T33；D3 F16/E08；D4 F31/F33；D1 T34/T35。

**前置条件：** K09—K11；已有 request/invocation 承载位置已读清。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/adaptation_analysis_execution.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/adaptation_analysis_execution.py) — `execute_node / _knowledge_block / record_analysis_invocation 调用`
- [`apps/api/local_drama/application/story_pipeline_ai.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/story_pipeline_ai.py) — `generate / _generate_episode / _synthesise`
- [`apps/api/local_drama/application/episode_preparation.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_preparation.py) — `ready draft 复用条件`

**最小改造：** 分两个小 PR：一是来源/草案/检查点指纹；二是实际请求证据与计数。复用不仅按集号或 episode_id，还匹配源版本、范围、相关目标修订及生成合同。已读分层分析在计算 request_hash 之外追加检索正文：应在调用前固定实际同源片段及最终 system/user、推理配置，摘要覆盖实际输入；重试沿冻结片段，不因索引更新换上下文。沿已有 invocation/Job 元数据统计实际调用、修复、成功/失败和恢复复用，不用“集数+1”冒充消耗。

**参考代码怎么学：** D3 / [`src/utils/agent/memory.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/src/utils/agent/memory.ts)：`摘要可回源的设计` 只借追溯思想；不引入聊天向量记忆。

**验收断言：** 同集不同原稿、范围/Prompt 版本变化均不误命中；检索结果变化对应新请求身份，原任务重试不漂移；半程恢复、一次结构修复、分组件综合的计数与 fake transport 调用表一致。无 token usage 时保留未知。

**不做/停止线：** 不把密钥写入摘要/日志；不再建可观测或计费平台；不删除旧调用证据；摘要缺失不得自动猜回原文。

**回滚与历史兼容：** 新请求摘要和检查点规则版本化；旧产物可查看，但不能伪装成通过新规则复核。检索开关回退不影响原文读取。

<a id="k13"></a>
### K13 · 统一草案质量门禁，并在应用前显示影响

**Phase 2｜证据：DOC + VERIFY + DESIGN｜状态：已完成实现与回归（2026-09-13）**

**原报告依据：** D5 F07/F12；D6 LDS-18/LDS-19；D2 T14；D3 F11/E04。

**前置条件：** K09、K10、K12；K02 的人工保护原则已明确。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/pipeline_orchestrator.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/pipeline_orchestrator.py) — `execute_draft_generation / apply_pipeline`
- [`apps/web/src/features/pipeline/pipelineClient.ts`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/src/features/pipeline/pipelineClient.ts) — `PipelineQualityReport`
- [`apps/web/src/features/pipeline/OneClickPipelineWorkbench.tsx`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/src/features/pipeline/OneClickPipelineWorkbench.tsx) — `应用预览/确认`

**最小改造：** 检查项声明适用性与严重度，由同一规则派生 blockers/warnings/status；应用阶段重新核验对应草案、源范围、规则版本，不能只信历史 READY。按当前故事产品合同判断人物等必需项，本轮不顺带修改题材范围。应用新计划时返回新增/保留/跳过集、总纲指针切换、资产复用/新增、受影响范围。初始无冲突文本建档可以在明确授权下自动应用；已有人工作品或混合新旧总纲时必须展示差异与确认。

**参考代码怎么学：** 不需要移植参考仓库代码。依据本项目自身合同与回归用例修复。

**验收断言：** 任一适用阻断项失败不可自动应用；只有警告时按既有政策处理；旧草案在源变更后不可应用；已制作第 1 集再应用新规划，界面准确列出被保留内容和上下文变化；取消预览无写入，预览后 revision 变化则冲突。

**不做/停止线：** 不把质量检查器扩成艺术评分平台；不让绿色前端字段控制后端门禁；不伪造资产 canonical 图或媒体批准。

**回滚与历史兼容：** 影响预览只读，可独立回退；新草案保留版本。恢复旧应用行为前仍须保留来源/人工冲突校验。

<a id="k14"></a>
### K14 · 逐镜解析有效 Profile，并精确检查当前所需依赖

**Phase 3｜证据：DOC + SRC + VERIFY｜状态：已完成实现、真实运行探测与回归（2026-09-13）**

**原报告依据：** D1 F08；D2 F05—F07；D5 F09/F10；D7 F04/F05。

**前置条件：** K03、K06、K09；确认当前实际执行路线。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/episode_production_runs.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_production_runs.py) — `preflight`
- [`apps/api/local_drama/application/episode_worker_actions.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_worker_actions.py) — `_video_profile / video_generation_preflight`
- [`apps/api/local_drama/application/queries/generation_preferences.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/queries/generation_preferences.py) — `GenerationPreferenceQueryService.resolve`
- [`apps/api/local_drama/application/h3_workflows.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/h3_workflows.py) — `runtime_layout`
- [`apps/api/local_drama/application/audio_requirements.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/audio_requirements.py) — `当前音轨需求`

**最小改造：** 复用 shot→episode→project 偏好解析，按待生产镜头去重后的实际 Profile/工作流检查必需组件，而不是 bool(usable_models)。优先消费已有安装完整性、offering readiness、输入槽位和节点验证，不每次重新散列巨型权重。外部 TTS 仅检查确实需要生成的说话者/旁白与有效音色；已有有效配音可复用。预检按阶段区分：文本准备不因视频权重未装而完全不可做；进入昂贵生产前汇总已知的下游缺项。授权健康探测可进行，但 runtime_contacted/network_contacted 必须来自真实行为。

**参考代码怎么学：** 不需要移植参考仓库代码。依据本项目自身合同与回归用例修复。

**验收断言：** 项目默认可用但某镜覆盖不可用；只有无关模型、缺 VAE/节点；三人发声只绑一人；静音/原生音频/旁白独立策略；探测成功、失败、未探测。断言所选版本与最终冻结版本一致，预检不新建生成 Job 或媒体事实。

**不做/停止线：** 不按名称相似度替换权重；不增加平行模型/音色解析器；不把健康探测结果放进创意内容 hash。

**回滚与历史兼容：** 保留原运行期最后防线；回退新预检呈现也不能自动改用其他模型。未验证能力继续不可进入默认生产。

<a id="k15"></a>
### K15 · 单镜和自动生成使用同一有效输入与规格编译结果

**Phase 3｜证据：SRC + DOC + VERIFY + DESIGN｜状态：已完成实现、真实 H3 小样与回归（2026-09-13）**

**原报告依据：** D1 F06/F09；D2 F12—F15；D4 F07/F11/F20/F22；D5 F17—F20；D6 LDS-15/LDS-28；D7 E08/E12。

**前置条件：** K04—K06、K14；发生有意硬衔接的输入由 K16 提供。

**本项目读取/修改位置：**

- [`apps/api/local_drama/domain/shot_prompt_bundle.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/domain/shot_prompt_bundle.py) — `compile_shot_prompt_bundle`
- [`apps/api/local_drama/domain/generation_planning.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/domain/generation_planning.py) — `frozen_generation_contract`
- [`apps/api/local_drama/domain/video_geometry.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/domain/video_geometry.py) — `h3_frame_count / h3_render_duration_ms`
- [`apps/api/local_drama/application/episode_worker_actions.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_worker_actions.py) — `_submit_shot / 逐镜预检`
- [`apps/api/local_drama/application/comfy_jobs.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/comfy_jobs.py) — `_apply_effective_configuration`
- [`apps/api/local_drama/model_platform/application/comfy_workflow_execution.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/model_platform/application/comfy_workflow_execution.py) — `当前启用时的冻结执行绑定`
- [`apps/web/src/features/director-v2/ShotGenerationInspector.tsx`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/src/features/director-v2/ShotGenerationInspector.tsx) — `实际输入预览`

**最小改造：** 先只覆盖本机正在使用的已发布路线。共享解析 Profile、实际模式、最终 prompt/negative、seed、候选预算、参考 role/ordinal/media_version/hash、生成尺寸/帧数与声音策略。参考槽位先解析，再生成文字编号；不静默丢弃超额参考，不因缺首帧就擅自从 I2V 降为 T2V。沿现有编译层新增必要的模型 renderer 即可，不平行新建 compiler/plan 系统。叙事目标时长、合法采样帧、输出实测时长、时间线使用区间分开。K04 之外的参数覆盖按现有契约限定目标节点，不因任何节点有 width/cfg 字段就改它。

**参考代码怎么学：** D1 / [`tools/excel_to_multi_chain_json.py`](https://github.com/HEEEeeeeN/ComfyUI-H3-Conditioning-Cache-AI-Drama-Production-Suite/blob/5c44d76ca1ec301d4e3056c6b863c1f94e07a8dd/tools/excel_to_multi_chain_json.py)：`compute_slots` 借同源编号；D4 / [`director/plan.py`](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/3cea821640d02f16f24db3a8b61a34fa20d540f6/director/plan.py)：`concat_common_segment_prompt` 借公共/局部约束；D2 / [`tests/test_comfy_engine.py`](https://github.com/ReSerendipity/MiniMax-H3-lite/blob/89ba581b63aa7fb8532e127143958967e96fbc93/tests/test_comfy_engine.py) 借注入测试，但扩充其未覆盖边界。

**验收断言：** API→计划→冻结→实际图/文本的哨兵测试：seed=0、16:9、首/尾帧不同标识、参考排序/超限、角色名重复、第二采样器和参考缩放节点。单镜与自动入口同输入计划一致。不同档位不得静默改变故事长度；FL2VA 末端约束和对白不被通用裁尾破坏。实际输出仍须媒体探测。

**不做/停止线：** 不一次开放所有 H3 模式/音视频参考；不复制第三套帧数公式；不为通用 H3 标签污染领域模型；不把“文本回退”宣传成原生轨迹控制。

**回滚与历史兼容：** 编译/绑定版本只影响新计划；新工作流先候选、smoke、发布、显式选择。可回退到旧已验证发布路线，不回写历史图。

<a id="k16"></a>
### K16 · 统一帧桥语义、前驱依赖、幂等与提取来源

**Phase 3｜证据：SRC + DOC + VERIFY｜状态：已完成实现与回归（2026-09-13）**

**原报告依据：** D1 F02/F03；D4 F09/F10；D6 LDS-09/LDS-16；D7 F07/F08。

**前置条件：** K07、K15；K22/K23 完成后才允许连续链大范围自动派发。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/episode_worker_actions.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_worker_actions.py) — `_end_frame_chain / _last_frame_anchor / _submit_shot`
- [`apps/api/local_drama/application/frame_bridges.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/frame_bridges.py) — `FrameBridgeCommandService.inherit`
- [`apps/api/local_drama/application/frame_chaining.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/frame_chaining.py) — `auto_chain_shot_tail_to_next`
- [`apps/api/local_drama/application/timeline.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/timeline.py) — `既有 create_frame_anchor`
- [`apps/api/local_drama/application/worker_handlers/automation_task.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/worker_handlers/automation_task.py) — `已有依赖推进`

**最小改造：** 拆成“语义止损、稳定边界写入、依赖推进”三个可测小切片。上一镜尾帧是后镜起点/连续性参考，不是默认后镜结束目标；不能全文把 END_FRAME 换成 FIRST_FRAME 而制造双首帧。显式区分切镜、软连续和硬衔接；场景用稳定 ID 与转场意图，不用 environment 字符串或两个空值。已有锁定首帧与继承冲突时暂停。硬衔接必须等前驱确定工作版本可用后再冻结后继输入；GPU 串行并不意味着预先冻结的后继会自动获得正确尾帧。复用真实 frame anchor 和 FrameBridge 版本约束；幂等键不含每次新随机 UUID，失败不能先留一个“已继承”指针。

**参考代码怎么学：** D7 / [`nodes/stages/director.py`](https://github.com/jtydhr88/ComfyTV/blob/42ce39d96f0c16fc6a47ad745eafdd2e5026c862/nodes/stages/director.py)：`DirectorStage.execute / _clip_images` 借明确衔接与逐镜推进；不复制位置索引或浏览器大循环。

**验收断言：** 前镜尾 A、当前首 B、当前尾 C、身份 R：不继承为 B/C/R，显式采用继承为 A/C/R；锁定 B 冲突不提交，不得默认 B/A/R。硬前驱未完成等待、反打/切场不继承、场景未知不自动连、回放两次不增等价边界、inherit 失败不变当前指针、前驱换版只影响真实依赖。检查源视频版本、帧位置和 SHA。

**不做/停止线：** 不新增帧桥表；不默认全剧一条长镜链；不复制 Motion Context 全局补丁；未使用自动衔接的路线不因修 bug 被自动启用。

**回滚与历史兼容：** 优先暂停有疑问的自动衔接，保留手工已确认边界；新边界版本与旧历史兼容，不批量把旧 END_FRAME 改名。

<a id="k17"></a>
### K17 · 先利用已有合格候选，并传播采用失败

**Phase 3｜证据：DOC + VERIFY｜状态：已完成实现与回归（2026-09-13）**

**原报告依据：** D1 F07；D4 F13/F14；D2 F16；D7 E15。

**前置条件：** K06、K07、K15；当前产物资格与 K18 共用，不各写一套 SQL。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/episode_worker_actions.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_worker_actions.py) — `_shot_video / qc / _auto_select_video 调用结果`
- [`apps/api/local_drama/application/queries/qc_policies.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/queries/qc_policies.py) — `既有 QC 策略读取`
- [`apps/web/src/features/director-v2/CandidateCompareDialog.tsx`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/src/features/director-v2/CandidateCompareDialog.tsx) — `既有人工候选比较`

**最小改造：** 候选先匹配当前镜头、输入、模式和媒体完整性，再复用同一媒体/检查器/政策版本的 QC。先做可解码、时长、尺寸、必要音轨等技术检查；已有合格候选时不因较新坏候选立即再抽一条。人工采用/批准且未过期的版本优先；若过期，显示冲突，不静默覆盖。QC PASS 与自动采用结果分别保留：采用 BLOCKED 必须使生产进入待处理，不能被 PASS 总状态掩盖。重抽预算沿原意图消耗，有确定配置错误时不重抽。

**参考代码怎么学：** D5 / [`console/rules/failure_codes.md`](https://github.com/qiukaihui/comfyui-auto-drama/blob/362c8fd860bbbafda4b504de4f7bbe0c9a3d0d24/console/rules/failure_codes.md) 借失败分层、停止无效重试；它是规则资料，不是已可直接调用的自动分类器。

**验收断言：** A 合格、较新 B 失败，不生成 C；人工选 A 不被推荐 B 覆盖；所有候选失败才消耗重抽额度；重复 QC 不无限新增记录；技术 PASS + 采用 BLOCKED 正确暂停；相同媒体但检查版本变更应重验。

**不做/停止线：** 不新增审美 VLM 集群/电影总分；不把技术通过转成人工批准；不错误地把业务采用失败改写为技术 QC 失败。

**回滚与历史兼容：** 自动采用策略可回退到明确人工选片；历史 QC 与已采用版本保持，新的推荐不成为强制选择。

<a id="k18"></a>
### K18 · 当前生产完成度复用同一媒体资格读模型

**Phase 3｜证据：DOC + VERIFY｜状态：已完成实现与回归（2026-09-13）**

**原报告依据：** D2 F04；D3 F07；D4 F26；D5 F08；D6 LDS-03/LDS-22；D7 E03。

**前置条件：** K08、K09、K15、K17 的资格约定已统一。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/episode_production_runs.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_production_runs.py) — `_view / _state`
- [`apps/api/local_drama/application/episode_worker_actions.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_worker_actions.py) — `_stale_working_media_shots`
- [`apps/api/local_drama/application/episode_production.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_production.py) — `既有生产查询入口`
- [`apps/api/local_drama/application/compose.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/compose.py) — `preflight / compose_fingerprint`
- [`apps/api/local_drama/infrastructure/database/episode_production_repository.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/infrastructure/database/episode_production_repository.py) — `SqliteEpisodeProductionReadRepository；入口已确认，实施前核读查询实现`

**最小改造：** 资格基于当前输入/选择及其动态失效、完整性、必要 QC；不能仅凭存在历史 VERIFIED/PASS/render 判当前完成。分开 run 活动/终态与产物资格：失败 run 可保留未受影响成果，取消不等于历史媒体损坏。有效旧结果可以复用，不要求每个媒体都在当前 run 创建。整集渲染要对应当前时间线及声音选择。优先复用已有生产读仓库的判定，避免 Worker、工作区、run 门面再各实现一次。

**参考代码怎么学：** 不需要移植参考仓库代码。依据本项目自身合同与回归用例修复。

**验收断言：** 旧版视频 PASS 后修改动作，新任务失败；旧 render + 新时间线；改音频不改画面；取消但已有合法产物；新选择候选不能借用旧候选 QC。各视图相同口径，未变化镜头仍计有效复用。

**不做/停止线：** 不删除历史结果，不把所有历史媒体一律标 stale，不用创建时间代替输入相符，不用旧成功掩盖当前失败。

**回滚与历史兼容：** 以新增读模型字段兼容旧 API；旧状态继续可审计。回滚不伪造新的完成记录。

<a id="k19"></a>
### K19 · 把全量统计、分页列表与导演批次集合分开

**Phase 3｜证据：DOC + VERIFY｜状态：已完成实现与回归（2026-09-13）**

**原报告依据：** D2 F03/T06；D7 F12/T05/E11。

**前置条件：** K18 定义统计口径；导演窗口小修可以在 Phase 0 后独立提前，不需要等整条主线。

**本项目读取/修改位置：**

- [`apps/web/src/features/episode-production-v2/EpisodeProductionWorkspace.tsx`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/src/features/episode-production-v2/EpisodeProductionWorkspace.tsx) — `shots / aggregate / attentionGroups`
- [`apps/web/src/pages/DirectorDeskPage.tsx`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/src/pages/DirectorDeskPage.tsx) — `batchShotIds / batchDoneIds / openBatchShot`
- [`apps/api/local_drama/infrastructure/database/shot_studio_repository.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/infrastructure/database/shot_studio_repository.py) — `_navigator`
- [`apps/api/local_drama/application/episode_production.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_production.py) — `全量汇总的既有只读入口`

**最小改造：** 两个独立小 PR。第一：后端权威汇总完整目标集合，详情保持分页，不能只用第一页 100 项计算全体阶段与异常。第二：批次 ID 集合以已确认批次为权威，不用半径 25 的导航窗口过滤；“未加载”不是“不存在/不属于本批”。按已知 ID 跳转后再按需读取。删除/归档/非法跨集 ID 要明确反馈，不把它们与未加载混在一起。批量写入单批限制可保留，由后台有限分批执行。

**参考代码怎么学：** 不需要移植参考仓库代码。依据本项目自身合同与回归用例修复。

**验收断言：** 137 镜，前 100 完成、第 137 失败；首页统计和定位正确。批次 1/26/51/52/100 成员，跨窗口前后跳、刷新、恢复已完成记录，成员数和顺序不变。非法跨集请求仍由服务端拒绝。

**不做/停止线：** 不把 limit 改为 10,000；不一次加载全书镜头；不把前端缓存当生产权威。

**回滚与历史兼容：** 纯前端窗口修复可独立回退；服务端汇总兼容扩展，不修改批次历史事实。

<a id="k20"></a>
### K20 · 联系表导出读取 V2 当前工作版本而非旧镜像关系

**Phase 3｜证据：DOC + VERIFY｜状态：已完成实现与回归（2026-09-13）**

**原报告依据：** D7 F13/T05；D3 E11；D2 F11。

**前置条件：** K18；经测试证实当前采用路径有遗漏后才改实现。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/contact_sheets.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/contact_sheets.py) — `ContactSheetExportService._selected_items`
- [`apps/api/local_drama/infrastructure/database/shot_studio_repository.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/infrastructure/database/shot_studio_repository.py) — `当前候选/shot_working_media_slots 查询`
- [`apps/api/local_drama/application/shot_studio.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/shot_studio.py) — `现有工作区读取`

**最小改造：** 先用真实 V2 采用命令构造数据，确认 Variant 所属媒体是否被旧 SHOT/selections 查询遗漏。若是，统一消费当前工作槽位的 MediaVersion，旧 selections 仅作明确兼容回退，避免双重计数。继续走导出内容哈希、受控路径和媒体校验；无需复制一份素材来迎合旧 SQL。为后续集中审核提供正确联系表，不等于导出行为本身完成审核。

**参考代码怎么学：** 不需要移植参考仓库代码。依据本项目自身合同与回归用例修复。

**验收断言：** V2 采用但不写旧 SHOT 选择镜像仍可导出；旧项目仍可导出；归档按政策处理；一槽只输出所选版本而非最新版本；重复导出可复用，文件被篡改仍拒绝。

**不做/停止线：** 不另建联系表工具或媒体镜像表；不因需要批量审核而自动批准联系表上的全部素材。

**回滚与历史兼容：** 回退导出读取时保留新工作槽权威；不要回写旧选择关系造成双重真相。

<a id="k21"></a>
### K21 · 声音与时长按实际路线冻结，复用对白越界防线

**Phase 3｜证据：DOC + SRC + VERIFY｜状态：已完成实现、真实带音轨媒体与回归（2026-09-13）**

**原报告依据：** D1 §7.6/F09；D2 F06/F12/F15；D4 F18/F19；D5 F21/F22；D6 LDS-13/LDS-17/LDS-23；D7 E16。

**前置条件：** K03、K05、K14、K15；对白密集的音频优先流程只在实证需要时另作增强。

**本项目读取/修改位置：**

- [`apps/api/local_drama/domain/dialogue_timing.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/domain/dialogue_timing.py) — `dialogue_timing_issues / assert_dialogue_timing`
- [`apps/api/local_drama/application/episode_production_runs.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_production_runs.py) — `tts_enabled / _workflow_for_snapshot`
- [`apps/api/local_drama/application/episode_worker_actions.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_worker_actions.py) — `视频、TTS、字幕、时间线交接`
- [`apps/api/local_drama/application/comfy_jobs.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/comfy_jobs.py) — `native_audio / H3_NATIVE_AUDIO_REQUIRED`
- [`apps/api/local_drama/application/dialogue.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/dialogue.py) — `现有对白与音色`
- [`apps/api/local_drama/application/compose.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/compose.py) — `audio_bindings 与合成预检`

**最小改造：** 先审现有音频选择与混音合同，能表达则只接线。明确原生对白、外部 TTS/旁白、源音、静音的实际选择，不强制新默认。最终一句台词只由一个明确主声音来源承担；原生音频必需的工作流不能直接删 Audio VAE 假装支持关闭。若视频只有一条混合了人声和环境声的音轨，没有现成分轨证据就不能承诺“只去人声、完整保留环境声”；应显式整轨静音/重配，或进入单独增强评估。有实际配音时用实测长度调用既有 dialogue_timing；无音频只给估算风险。保留素材不足的合成阻断。

**参考代码怎么学：** D6 / [`segment_engine.py`](https://github.com/karuvanan/MiniMax-H3-Director-Cut-Studio/blob/e093e836a014b65a3fc079a166ba9c88685cd4ff/segment_engine.py)：`protect_segment_boundaries_from_speech` 借不切断对白的不变量；常量和自动分段不纳入本包。

**验收断言：** 双人两句只播一次；静音不被自动源音回退打开；TTS 缺角色音色提前提示；更换配音版本重验时长；超长对白不截词尾。改字幕样式仅重合成，改原生对白必须影响视频/音轨；4.458333 秒素材不能靠长冻结画面冒充 12.632 秒使用范围。

**不做/停止线：** 不默认安装新 TTS/口型/声源分离模型；不把音轨非空当口型或对白准确；不为所有镜头强制音频优先重排。

**回滚与历史兼容：** 保留已冻结声音策略与历史音轨；回退新策略只影响新运行。旧混合轨不能在回滚时被静默恢复并与 TTS 重叠。

<a id="k22"></a>
### K22 · 整集/整剧启动具有稳定端到端命令身份

**Phase 4｜证据：DOC + VERIFY｜状态：已完成实现与回归（2026-09-13）**

**原报告依据：** D3 F08；D5 F11；D1 T06；D6 LDS-02/LDS-06。

**前置条件：** K07、K08、K09；核对已有 command_idempotencies/等价机制。

**本项目读取/修改位置：**

- [`apps/web/src/features/pipeline/pipelineClient.ts`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/src/features/pipeline/pipelineClient.ts) — `runWholeDrama 与公共 requestJson`
- [`apps/api/local_drama/application/whole_drama_orchestrator.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/whole_drama_orchestrator.py) — `run`
- [`apps/api/local_drama/application/episode_production_runs.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_production_runs.py) — `start / start_run 调用`
- [`apps/api/local_drama/application/automation_workflows.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/automation_workflows.py) — `既有命令幂等与运行关联`

**最小改造：** 先追实际路由和公共客户端是否已经注入稳定 Idempotency-Key；已有就复用，不加第二个键。一次明确启动生成一个父命令身份，网络重发复用，子集键由父命令和 episode 派生。同键请求先找已接受结果，再处理是否需要新预检；不能已启动后因为磁盘余量改变而无法重放原响应。同键不同 payload 明确冲突。用户请求继续/新拍/仅合成各有语义，不能用相同参数 hash 永远禁止未来有意重拍。

**参考代码怎么学：** 不需要移植参考仓库代码。依据本项目自身合同与回归用例修复。

**验收断言：** 双击、并发标签页、响应丢失、刷新重试、已接受后磁盘/运行状态变化、同键不同参数、明确再次制作。检查实际 run/Job 数及返回的原 run ID；新意图的预检不能被旧缓存绕过。

**不做/停止线：** 不只做前端防抖；不新增“整剧任务总表”并复制子任务状态；不把参数相同当成永远同一次用户意图。

**回滚与历史兼容：** 保留已有幂等记录和父子关联，停止新调度后回退代码；无法稳定重放的遗留意图要求人工选择继续或新建。

<a id="k23"></a>
### K23 · 核验两条 Comfy 路线的受理、超时、取消与恢复

**Phase 4｜证据：DOC + SRC + VERIFY｜状态：已完成实现、真实 Comfy 受理/回收与恢复回归（2026-09-13）**

**原报告依据：** D1 F10；D2 F08/F09；D4 F15/F16；D6 LDS-06；D7 F09。

**前置条件：** K07、K22；实际执行路线与恢复事实已盘点。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/comfy_jobs.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/comfy_jobs.py) — `submit_next / run_once / 既有 uncertain-success 恢复`
- [`apps/api/local_drama/model_platform/application/comfy_workflow_execution.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/model_platform/application/comfy_workflow_execution.py) — `_binding / make_comfy_workflow_handler`
- [`apps/api/local_drama/application/worker.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/worker.py) — `WorkerSupervisor 与恢复入口`
- [`apps/api/local_drama/application/worker_sessions.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/worker_sessions.py) — `会话/心跳`
- [`apps/api/local_drama/application/jobs.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/jobs.py) — `attempt / provider 关联 / 取消`
- [`apps/api/local_drama/application/gpu_runtime.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/gpu_runtime.py) — `GpuRuntimeCoordinator`

**最小改造：** 必须先确认本轮实际走旧 GPU_H3 还是 V2；当前未用且不准备启用的 V2 只登记边界，不强制重构它。先阅读现有 attempt、execution_job_links、provider receipt 和对账逻辑；若已闭环，仅补故障测试。缺口才补：已知 prompt_id 恢复原 history/下载/晋升，受理未知先对账，不立即再采样。V2 正式生产若启用，短 smoke 与生产预算分别配置且有界，贯穿上游 binding 校验；超过 300 秒不自动等于失联。取消等待不等于 GPU 停止，租约/迟到输出/共享 runtime 的 interrupt 必须按任务归属处理。

**参考代码怎么学：** D6 / [`comfy_submit_worker.py`](https://github.com/karuvanan/MiniMax-H3-Director-Cut-Studio/blob/e093e836a014b65a3fc079a166ba9c88685cd4ff/comfy_submit_worker.py)：`wait_for_history` 借“已知 prompt 断线只继续监控”；不复制另一套 while 队列。

**验收断言：** 提交前失败；Comfy 已接受但响应丢失；拿到 prompt_id 后本地写入失败；301 秒后成功；下载失败而生成已成功；产物晋升失败；取消后外部迟到成功；Worker 重启且 provider 状态未知。已知受理的恢复不再次提交，无法判断则显式待处理；迟到结果不能覆盖当前新版本；资源状态可对账。

**不做/停止线：** 不把 timeout 设无限；不直接使用全局 interrupt 停掉其他人的任务；不承诺跨网络 exactly-once；不为单个带音轨 MP4 重做多主产物平台。

**回滚与历史兼容：** 部署前停止新派发并对账在途任务；保留外部 ID 与 receipt。回退处理器后未知任务继续隔离，不盲目重投；实验 V2 可退回已验证路线。

<a id="k24"></a>
### K24 · 缩小指纹依赖到实际消费对象，保留真正共享约束

**Phase 4｜证据：DOC + VERIFY｜状态：已完成实现与回归（2026-09-13）**

**原报告依据：** D3 F14；D7 F10；D5 F30；D6 LDS-14。

**前置条件：** K09、K12、K15—K18。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/episode_front_half_actions.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_front_half_actions.py) — `snapshot / _ready_breakdowns`
- [`apps/api/local_drama/application/episode_production_runs.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_production_runs.py) — `preflight fingerprint`
- [`apps/api/local_drama/domain/generation_planning.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/domain/generation_planning.py) — `既有冻结输入`
- [`apps/api/local_drama/application/compose.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/compose.py) — `合成指纹`

**最小改造：** 把项目概况与当前阶段内容输入分开：只纳入当前集实际应用的草案、源范围、采用的总纲/身份包、镜头修订、有效 Profile、参考、必要帧桥。别集新草案、未采用候选和探测状态不应触发本集失效；真实共享身份或边界变化必须保留。先固定各阶段必要依赖清单并复用现有 stale 判定，不做通用依赖图数据库。生成与合成指纹分开，局部返工先展示真实影响。

**参考代码怎么学：** D7 / [`tests/test_director_stage.py`](https://github.com/jtydhr88/ComfyTV/blob/42ce39d96f0c16fc6a47ad745eafdd2e5026c862/tests/test_director_stage.py)：`增量复用与调用次数断言`；你的失效身份要比 donor 文件名/URL 更严格。

**验收断言：** 第 2 集增加草案，第 1 集输入不变；第 1 集使用身份包变化则对应镜头失效；仅剪辑顺序/字幕样式变化不重做独立视频；上游尾帧变化影响真实硬后继，但在切镜处停止；GPU/磁盘状态变动不改变创意身份。

**不做/停止线：** 不关闭 stale 校验以换取“可恢复”；不按列表后半段全部失效；不承诺所有修改永远只影响一镜。

**回滚与历史兼容：** 新依赖规则版本化，旧快照不可重算后伪装原记录；回退时无法证明兼容的结果仅作为历史可看。

<a id="k25"></a>
### K25 · Beat Replan 用稳定镜头身份匹配，避免插一镜重写后续

**Phase 4｜证据：DOC + VERIFY｜状态：已完成实现与回归（2026-09-13）**

**原报告依据：** D3 F12；D5 F16；D4 F23/F38。

**前置条件：** K02、K09、K24。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/beat_replan.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/beat_replan.py) — `_build_plan / plan / apply`
- [`apps/web/src/features/director-v2/DirectorIntentEditor.tsx`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/src/features/director-v2/DirectorIntentEditor.tsx) — `现有差异/锁定入口`

**最小改造：** 优先匹配合法的既有 shot_id，其次使用既有稳定 beat/来源引用；真正新增项才分配新身份。不能继续简单按数组下标对齐 A/B/C 与 A/X/B/C。模型引用 ID 必须属于当前作用域；不确定配对只生成待确认 diff，不自动应用。保留已有 plan_hash、CAS、冻结保护和幂等。不为身份匹配增加向量相似度系统。

**参考代码怎么学：** D3 / [`src/agents/productionAgent/tools.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/src/agents/productionAgent/tools.ts)：`对象级操作思想`；执行仍走本项目 BeatReplanService。

**验收断言：** 头部/中部插镜、末尾新增、删镜、重排、仅改一镜、跨集伪造 ID、模型漏 ID。未变化镜头 ID 与输入版本保持；必要排序变化不错误继承旧生成成果；过期 diff 不能覆盖后来人工修改。

**不做/停止线：** 不整集重生成绕过匹配；不允许 LLM 自造已存在 ID；不以文本相似度静默合并两个不同镜头。

**回滚与历史兼容：** 新建议先只读预览；保留旧方案和版本。回退 matcher 不自动应用已生成的新 diff。

<a id="k26"></a>
### K26 · 拆镜继承声明性身份引用，不继承已生成结果

**Phase 4｜证据：DOC + VERIFY｜状态：已完成实现与回归（2026-09-13）**

**原报告依据：** D3 F13；D6 LDS-19；D7 E07。

**前置条件：** K02、K09、K24；先验证现有后续补齐是否已覆盖，覆盖则仅补测试。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/shot_editing.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/shot_editing.py) — `_copy_asset_bindings / plan / commit`
- [`apps/api/local_drama/application/episode_front_half_actions.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_front_half_actions.py) — `身份包版本检查`
- [`apps/api/local_drama/application/character_identity_packs.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/character_identity_packs.py) — `既有身份/状态关系`

**最小改造：** 核对 schema 后，复制合法的 identity_pack_version_id 和应继承的状态引用，而不是只复制 asset_id/role_in_shot。身份引用是声明性输入，可按意图继承；视频选择、媒体批准、QC 结论不能照抄给两个新镜头。拆分台词/动作要有明确分配与来源，不把全部台词复制两遍当拆镜完成。旧镜头归档和历史可追溯规则沿用已有实现。

**参考代码怎么学：** 不需要移植参考仓库代码。依据本项目自身合同与回归用例修复。

**验收断言：** 带身份包的镜头拆 A/B：身份版本保留、无伪批准视频、台词不重复、源镜头历史可回查、旧时间线按已有机制过期。非法/撤销状态引用不得通过复制绕过检查。

**不做/停止线：** 不新增角色库/状态库；不复制源镜头机器检查结果；不猜数据库中不存在的状态列。

**回滚与历史兼容：** 沿现有非破坏编辑与版本恢复机制回退；不删源镜头/旧媒体，恢复前核对后续人工编辑。

<a id="k27"></a>
### K27 · 将已授权的规划应用续接移到后端

**Phase 5｜证据：DOC + DESIGN + VERIFY｜状态：已完成实现、隔离浏览器与持久续接验证（2026-09-13）**

**原报告依据：** D3 F04；D5 F04；D6 LDS-02；D2 F10。

**前置条件：** K09—K13、K22；现有事务/事件机制已明确。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/pipeline_orchestrator.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/pipeline_orchestrator.py) — `start_pipeline / execute_draft_generation / apply_pipeline`
- [`apps/api/local_drama/application/worker_handlers/story_pipeline_draft.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/worker_handlers/story_pipeline_draft.py) — `run_story_pipeline_draft_job`
- [`apps/api/local_drama/application/automation_workflows.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/automation_workflows.py) — `既有完成事件/依赖推进`
- [`apps/web/src/features/pipeline/OneClickPipelineWorkbench.tsx`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/src/features/pipeline/OneClickPipelineWorkbench.tsx) — `自动 apply 的 useEffect`

**最小改造：** 提交时记录具体授权终点和允许应用的文本 sections，而非用一个含糊 auto_run_rendering 统管所有批准。生成完成后，既有可靠完成事件/待续接记录调用原 apply_pipeline 命令，失败可幂等重放；生成成功但未应用必须能诊断。未授权历史运行继续手动。浏览器只显示状态和发用户命令，不在挂载历史记录时隐式推进生产。已有改编分析节点声明 automatic_apply=False 的约束不能改掉：应用是分析完成后的独立授权命令，不让分析 Worker 偷偷写业务事实。

**参考代码怎么学：** 不需要移植参考仓库代码。依据本项目自身合同与回归用例修复。

**验收断言：** 开始后立即关页；两个标签页；完成事件重复；生成成功后进程重启；取消发生在应用前；仅生成草案目标；应用前有人改了计划。合法授权可继续，未授权/冲突不应用，重复回放不增版本或资产。

**不做/停止线：** 不直接在 Worker 写 episodes/资产表；不让一次文本授权等同媒体/发布批准；不增加平行 outbox 或第三个总控 Agent。

**回滚与历史兼容：** 新自动续接默认按显式授权启用；关闭后保留完成草案与手动应用入口。已有待续接命令先取消/对账，不能丢失。

<a id="k28"></a>
### K28 · 从零镜头到单集预览，复用准备、审核、生产服务

**Phase 5｜证据：DOC + DESIGN + VERIFY｜状态：已完成实现、隔离浏览器与真实运行验证（2026-09-13）**

**原报告依据：** D1 F05；D2 F10/F11；D3 F05；D4 F04—F06；D5 F01；D7 F01/E02。

**前置条件：** K01—K24 主链相关项通过；K27；K25/K26 只有涉及相应编辑行为时为准入条件。

**本项目读取/修改位置：**

- [`apps/api/local_drama/application/episode_preparation.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_preparation.py) — `EpisodePreparationService.prepare`
- [`apps/api/local_drama/application/whole_drama_orchestrator.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/whole_drama_orchestrator.py) — `prepare_all_episodes / run`
- [`apps/api/local_drama/application/episode_production_runs.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_production_runs.py) — `start / _workflow_for_snapshot`
- [`apps/api/local_drama/application/episode_front_half_actions.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_front_half_actions.py) — `只读校验`
- [`apps/api/local_drama/application/worker_handlers/automation_task.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/worker_handlers/automation_task.py) — `依赖与动作端口`
- [`apps/api/local_drama/application/shot_keyframe_generation.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/shot_keyframe_generation.py) — `既有批次生成`
- [`apps/web/src/features/episode-production-v2/EpisodeProductionWorkspace.tsx`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/src/features/episode-production-v2/EpisodeProductionWorkspace.tsx) — `主操作与待确认集合`

**最小改造：** 第一版只做一个已确认来源的分集。缺方案调用 prepare，保存其已有 Job ID；可应用草案走原应用命令；资料/身份冲突集中给出下一步，不改 validator 为隐式写库生成器。缺资产参考可调用已读清的既有服务，仅生成本集需要的候选；不存在可用命令时明确待处理，不编造“自动补全完成”。关键帧批量查看/选择/确认复用 K20 与现有 ReviewService。准备完成后重新读事实，下一阶段建立新的冻结快照/生产 run，而非改最初旧快照。流程目标是可播放预览及真实待审项，正式交付另守当前门禁。

**参考代码怎么学：** D7 / [`nodes/stages/director.py`](https://github.com/jtydhr88/ComfyTV/blob/42ce39d96f0c16fc6a47ad745eafdd2e5026c862/nodes/stages/director.py)：`明确编排与逐镜完成` 和 D3 / [`src/agents/productionAgent/tools.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/src/agents/productionAgent/tools.ts)：`就地操作对象` 只借职责；不是移植它们的执行宿主。

**验收断言：** 只有原稿与分集范围、没有镜头；只有一个手工镜头但计划并不完整；已有草案待应用；身份冲突；缺模型；批量关键帧仅退回 2/20；关页/重启/重复提交。流程能沿同一制作意图继续，已通过内容不重做，每个暂停定位到实体/原因/允许命令。

**不做/停止线：** 不把“无镜头需确认”解释为全部 READY；不把 auto_heal 当小说创作；不新增一张复制子状态的父运行真相；不默认无人审核正式交付。

**回滚与历史兼容：** 保留原手动 prepare/start/review 入口；关闭自动推进仍可查看和继续现有 Job。阶段快照不互相覆盖，未开始子任务可按现有命令取消。

<a id="k29"></a>
### K29 · 明确四种操作与影响预览，精修复用同一命令

**Phase 5｜证据：DOC + DESIGN + VERIFY｜状态：已完成实现、影响预览与零 GPU 重合成验证（2026-09-13）**

**原报告依据：** D7 E01/E03/E07；D5 F16/F27/F32/F36；D4 F23/F34；D6 LDS-14/LDS-19。

**前置条件：** K07、K15—K18、K22—K24；与 K28 同一交付主线。

**本项目读取/修改位置：**

- [`apps/web/src/pages/DirectorDeskPage.tsx`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/src/pages/DirectorDeskPage.tsx) — `镜头操作区`
- [`apps/web/src/features/episode-production-v2/EpisodeProductionWorkspace.tsx`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/src/features/episode-production-v2/EpisodeProductionWorkspace.tsx) — `继续/重拍/问题处理`
- [`apps/api/local_drama/application/episode_worker_actions.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_worker_actions.py) — `target_shot_ids / force_new_take`
- [`apps/api/local_drama/application/compose.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/compose.py) — `submit / preflight`
- [`apps/api/local_drama/domain/generation_planning.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/domain/generation_planning.py) — `现有 plan_hash 与冻结合同`

**最小改造：** 界面区分“继续未完成”“原输入重试”“新拍候选”“仅重新合成”，都落到现有命令，不产生四套执行器。提交前显示将复用、等待在途、需生成、因硬依赖等待、需人工确认和仅重合成的集合，说明每项原因。用户有意修改语义时走版本化字段编辑/差异，不把替换动作变成往旧提示词追加相反句子。主流程调用与单镜同输入的有效计划一致；计划过期必须刷新确认。

**参考代码怎么学：** D7 / [`src/components/stages/DirectorStageCard.vue`](https://github.com/jtydhr88/ComfyTV/blob/42ce39d96f0c16fc6a47ad745eafdd2e5026c862/src/components/stages/DirectorStageCard.vue)：`rerunClip / maybeRerollForRepeat` 借操作集中表达，但不复制“重复 Run 自动全体换 seed”。

**验收断言：** 仅字幕样式或独立镜头重排时 GPU 视频任务为零；原输入重试保持 seed/参考；新拍增加一条候选；改某镜后硬后继列出而独立镜头不重跑；取消 diff 无副作用；人工锁定内容不被隐式改写。

**不做/停止线：** 不在本包加入自然语言 Agent 或完整剪辑器；不承诺“智能修复”但暗中修改多个变量；不让按钮名称替代后端语义。

**回滚与历史兼容：** 保留老命令兼容映射与清晰文案；新影响预览只读，可回退 UI，不删除已有候选和计划。

<a id="k30"></a>
### K30 · 建立小样发版门槛，再验证所选多集有限推进

**Phase 6｜证据：DESIGN + VERIFY｜状态：已完成真实 H3 小样、正式采用/交付、有限两集与重启恢复验证（2026-09-13）**

**原报告依据：** D1 §13/§15；D2 F18/验收矩阵；D3 §9；D4 F44；D5 F28/F34；D6 §8.4；D7 §8。

**前置条件：** 本轮范围内 K01—K29 已按 FIX/TEST_ONLY/已覆盖结项；暂不启用项有明确理由，不以 DEFERRED 冒充通过。

**本项目读取/修改位置：**

- [`apps/api/tests/test_episode_worker_actions.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/tests/test_episode_worker_actions.py) — `已有候选/重试/依赖用例`
- [`apps/api/tests/test_whole_drama_orchestrator.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/tests/test_whole_drama_orchestrator.py) — `整剧汇总`
- [`apps/api/tests/test_compose_duration_guard.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/tests/test_compose_duration_guard.py) — `时长不足防线`
- [`apps/api/tests/test_comfy_workflow_bindings.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/tests/test_comfy_workflow_bindings.py) — `执行绑定`
- [`apps/web/src/features/pipeline/OneClickPipelineWorkbench.test.tsx`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/src/features/pipeline/OneClickPipelineWorkbench.test.tsx) — `一键入口`
- [`apps/api/local_drama/application/whole_drama_orchestrator.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/whole_drama_orchestrator.py) — `所选集调度；仅发现缺口时修改`

**最小改造：** 先离线：真实服务装配+隔离 transport+SQLite，再浏览器 mock/E2E，最后经操作者许可跑一个当前已验证 Profile 的小样。正文第 7 节规定范围和故障点。单集闭环通过后才验证两个所选集，复用同一分集计划与政策，准备和 GPU 派发保持有界；不提前铺开 60 集全部候选。若现有整剧服务已满足，只补测试与入口说明。正式全剧完成要求完整授权范围及当前交付条件；缺镜预览必须显式标缺项，不能当完整成片。

**参考代码怎么学：** D7 / [`tests/test_director_stage.py`](https://github.com/jtydhr88/ComfyTV/blob/42ce39d96f0c16fc6a47ad745eafdd2e5026c862/tests/test_director_stage.py)：`调用次数与局部重跑断言`；同时断言本项目持久实体，而非只用示例 URL 判断成功。

**验收断言：** 双人一个场景 4—6 镜、连续动作与硬切、改一镜、断线重启、只重合成；两个所选集一个待确认另一个独立推进。记录实际命令/Job/attempt/prompt_id/输入输出哈希、调用数和人工决策；mock 通过与真实 GPU 通过分别标记。

**不做/停止线：** 不以 61 章压力文本生成 61 集视频来验收覆盖；不新增 CI 基础设施，只复用已有测试机制；不虚构性能提升率和模型画质保证。

**回滚与历史兼容：** 保留单集入口和旧已验证 Profile；多集扩展可关闭，已完成单集不回滚、不删除。故障只隔离对应新运行。

<a id="s6"></a>
## 6. 条件性增强：默认不启动，不自动变成 Phase 7

**Phase 0—6 完成后，不要求继续开发本节。** 每次只选一个真实瓶颈，用同一小样比较前后收益。原报告把某项列为 P1，不等于它必须进入你的首轮；本文重新按当前已有能力和实际路线裁决。

本节编号 `E01—E09` 是本文自己的条件性增强编号，与 D3/D7 等原报告的同名编号不同。引用原报告时必须带 D 编号。

### E01 · 低成本分镜预演：优先候选，但仍先查已有实现

**本轮裁决：已有能力覆盖，未触发新增开发。** 现有本集同步时间线预览、联系表和序列预览已覆盖首轮检查；详见 `docs/evidence/conditional-enhancements-e01-e09-disposition-2026-09-13.md`。

**启动条件：** 视频生成前经常才发现镜头顺序、台词量、时长或场景跳跃不合理，现有时间线/联系表不能提供足够直观的检查。

**最小范围：** 用当前工作版本的关键帧按计划时长停留，显示镜号与台词摘要，通过既有 CPU Job、FFmpeg、受控媒体输出形成“分镜预演”。复用 [`apps/api/local_drama/application/compose.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/compose.py)、[`apps/api/local_drama/application/contact_sheets.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/contact_sheets.py) 与现有时间线；新增薄 renderer 前先找同类实现。

**参考：** D7 / [`runners/animatic.py`](https://github.com/jtydhr88/ComfyTV/blob/42ce39d96f0c16fc6a47ad745eafdd2e5026c862/runners/animatic.py)：`boards_to_animatic / _board_duration_ms / _letterbox_rgb`，对应 D7 E09。借单图定时、画幅适配和明确预演身份，不搬完整视频栈。

**验收/停止：** 视频模型提交数为 0；顺序、画幅、时长和素材版本可追溯；缺图可用标明缺失的占位，但不得假装正式镜头。无法帮助提前发现问题，就不继续加转场/特效。

### E02 · 少量创作增强：补上下文，不新建小说理解平台

**本轮裁决：未触发。** 两集样本未出现可重复的承接、别名合并或锁定结局改写失败。

**启动条件：** K09—K13 已通过，但真实样片仍有可定位的分集承接、人物别名、改编取舍或短镜动作负荷问题。

**最小范围：** 复用 [`apps/api/local_drama/application/adaptation_analysis_execution.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/adaptation_analysis_execution.py)、[`apps/api/local_drama/application/story_pipeline_ai.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/story_pipeline_ai.py) 和现有 Story Bible/资产提案。一次只改一类：保留有预算的结尾状态/核心冲突；相邻范围拆合建议；明确不可改设定；有证据的角色别名候选；或一项主要动作/起止状态。当前已有别名字段不另建同义实体表，已有连续性投影不再做聊天记忆系统。新增字段必须有编译、检查或 UI 消费者。

**参考：** D3 / [`src/utils/cleanNovel.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/src/utils/cleanNovel.ts)：`processChapter`、D3 / [`src/agents/scriptAgent/tools.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/src/agents/scriptAgent/tools.ts)：`get_novel_events / get_novel_text`；D5 / [`console/rules/h3_expand.md`](https://github.com/qiukaihui/comfyui-auto-drama/blob/362c8fd860bbbafda4b504de4f7bbe0c9a3d0d24/console/rules/h3_expand.md) 的状态分工。对应 D3 E01—E10 中的方法、D1 §10、D5 F13—F15/F23—F24。只借“摘要可回源、事实与改编决定分开”的边界，不复制固定题材、年代、情绪或投流规则。

**验收/停止：** 同名不同人不被合并；锁定台词/结局不改；尾部事件能追到源范围；上下文预算可解释。只有更长 prompt、没有减少具体失败时，回退模板，不扩成知识图谱。

### E03 · 对象级自然语言细改：最后才加交互层

**本轮裁决：未触发。** 没有“用户频繁重复同类局部自然语言修改”的证据，按停止线不新增 Agent/MCP 交互平台。

**启动条件：** 现有四种操作和结构化编辑已稳定，但用户频繁重复表达同一类局部修改，手工字段编辑确实繁琐。

**最小范围：** 只支持“解释阻塞”“仅改本镜运镜”“给两镜修订草案”等少量意图；读取准确对象与 revision，输出允许字段的 diff，经确认后调用既有命令。模型不直写 SQL、不直接批准、不自由改项目文件，不得把小说文字当工具指令。鉴权、作用域、预算和 expected revision 由服务端验证，不交给提示词保证。

**参考：** D3 / [`src/agents/productionAgent/tools.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/src/agents/productionAgent/tools.ts) 的对象操作；D7 / [`api/mcp_tools/director_scene.py`](https://github.com/jtydhr88/ComfyTV/blob/42ce39d96f0c16fc6a47ad745eafdd2e5026c862/api/mcp_tools/director_scene.py)：`_director_get / _director_edit` 的先读后改。落在现有 ShotStudioQuery、BeatReplan、ShotEditing 和导演台。**内部 Web 功能不需要为此先搭 MCP 平台。**

**验收/停止：** 只改运镜不改台词/角色；未知、跨作用域和旧 revision 请求在写入前拒绝；一次失败不会触发开放式多 Agent 循环。无法明显减少操作就保持结构化编辑。

### E04 · H3 条件编码缓存：先测热路径，再决定是否开发

**本轮裁决：未触发。** 真实 H3 小样没有证明条件编码是主要且可避免的瓶颈，不凭单次总墙钟引入张量缓存。

**启动条件：** 同一冻结提示词/参考下多 seed 或重启恢复的编码成本确实占据可避免开销；已有 Comfy 内存缓存与模型保温没有覆盖该收益。

**最小范围：** 一个实际使用的已验证 Profile、同模式/同尺寸试点，默认关闭。提交图前决定编码还是加载缓存；仅在 Save 节点判断文件存在可能已经来不及省掉上游编码。缓存是可重建中间 Artifact，不是可批准 MediaVersion。继续一镜一个既有 Job，预编码也受 GPU 协调，API 不导入 torch/插件运行时。

**参考：** D1 / [`h3_conditioning_cache.py`](https://github.com/HEEEeeeeN/ComfyUI-H3-Conditioning-Cache-AI-Drama-Production-Suite/blob/5c44d76ca1ec301d4e3056c6b863c1f94e07a8dd/h3_conditioning_cache.py)：`H3EncodeConditioning / H3SaveConditioning / H3LoadConditioning`、D1 / [`h3_for_loop.py`](https://github.com/HEEEeeeeN/ComfyUI-H3-Conditioning-Cache-AI-Drama-Production-Suite/blob/5c44d76ca1ec301d4e3056c6b863c1f94e07a8dd/h3_for_loop.py)：`H3ConditioningIndex`。本项目接入 [`apps/api/local_drama/application/h3_workflows.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/h3_workflows.py) 或当前实际 V2 handler 的受控扩展，而非两条路线同时改。

缓存身份覆盖实际编码 prompt、按序参考内容 SHA/预处理、编码器/processor/节点与格式兼容版本。仅采样 seed 改变是否复用取决于它是否参与编码；提示词中时长变化也会使语义输入变化。与完整生成指纹分开，不能用 shot ID 或文件名作为命中证明。manifest 最后原子发布；半文件、错摘要、模式不符不命中。只加载应用自身受控产物，不开放外部 `.pt` 上传或任意绝对路径；列表只读 manifest，不解包张量。

**验收/停止：** 比较冷启动、原路径多 seed 热运行、缓存热运行、进程重启、改一条对白、替换同路径图片。命中确实不执行编码，收益扣除读写/校验/重试；无稳定净收益则停止。Ref2VA 成功不证明 FL2VA 首尾约束兼容，跨尺寸重编码不在首个试点。

### E05 · H3 新模式、音视频参考、Motion Context、二采：每次只立一个小项

**本轮裁决：未触发。** 当前发布 H3 图片到视频路线已经真实生成带音轨 MP4，没有明确需求必须依赖新模式。

**启动条件：** 当前已验证路线无法完成一个明确素材/镜头需求，而不是 donor 有这个模式就想全部支持。

先区分四件独立事：新生成模式；视频/音频参考物化；连续动作上下文；首采后精修。不得包装成一个“全面升级 H3”任务。K15/K16/K23 是相应前置；只启用经本机 smoke、发布并显式选择的 Profile。

**参考：** D4 / [`director/external_groups.py`](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/3cea821640d02f16f24db3a8b61a34fa20d540f6/director/external_groups.py)：`infer_i2v_kind / pack_r2v_group` 与 D4 §4.5—4.9、§4.12；D2 F13；D1 §6。相关内部上下文/精修实现的精确文件按原报告索引继续核读，不能仅据函数名或目录猜行为。

**最小边界：** 新引用保留媒体 kind、作用域、SHA、角色、时间范围与数量限制，不放开任意路径/URL，不把嵌套张量对象塞进现有标量 JSON 槽位。Motion Context 仅对显式短连续组，不影响普通切镜；需要对帧相位、前缀裁剪、音频和端点一同验收。首采 latent 缓存与 E04 条件编码缓存不是同一功能，不混用指纹；精修次数小且有界，生成新 Variant 保留旧版。

**验收/停止：** 用可辨认哨兵素材证明模型实际接收输入；无首帧但身份不满足不能自动转 T2V；首/尾目标不丢失，混合尺寸/音轨/迟到结果正确。不能以“上传成功、节点安装成功”替代能力验收。无净收益或不兼容时恢复旧路线，不扩大输入合同。

### E06 · 超长创作镜头的内部执行分段：有真实长镜需求再做

**本轮裁决：未触发。** 样本没有一个必须超过当前 Profile 单次能力且不能通过创作拆镜表达的镜头。

**启动条件：** 用户确实需要一个创作镜头超过当前 Profile 单次能力，且通过创作拆镜/既有时间线不能合理解决。

**最小范围：** Shot 仍是原创作/审核对象，内部 Segment 只是冻结执行明细，复用现有 Job 和 Artifact 关系；不新建第二种正式镜头实体。先限定一个镜头、少量子请求，保证 core 范围连续覆盖、合法采样帧、每句对白只有一个执行归属。

**参考：** D6 / [`segment_engine.py`](https://github.com/karuvanan/MiniMax-H3-Director-Cut-Studio/blob/e093e836a014b65a3fc079a166ba9c88685cd4ff/segment_engine.py)：`plan_shot_render_segments / rebase_timed_rows / protect_segment_boundaries_from_speech`。扩展当前视频计划与 [`apps/api/local_drama/domain/dialogue_timing.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/domain/dialogue_timing.py)，不复制 donor 的固定 15 秒、0.5 秒、24 帧常量。

**验收/停止：** 全局 25—28 秒对白在从 24 秒起的请求中成为局部 1—4 秒；跨边界不能两次朗读；45°、50%、24fps 不能被正则误当时间；源端点与硬尾帧保留；中段失败仅恢复所需段。一整句本身超过能力时明确不可满足，不靠分段宣称原生语音必连续。普通短镜不受此功能影响。

### E07 · 候选同步比较：仅补一个小交互，不做剪辑器

**本轮裁决：已有部分覆盖，未触发共享 seek。** 现有 2-up/4-up、统一播放/暂停和回到开头可用；没有真实比较失败样本。

**启动条件：** 已有 2/4 路播放比较不能有效比较同一动作，用户确实需要共同拖动定位。

**最小范围：** [`apps/web/src/features/director-v2/CandidateCompareDialog.tsx`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/src/features/director-v2/CandidateCompareDialog.tsx) 增加共享 seek、明确绝对时间/归一化对齐、播放失败与缓冲提示，只开一路声音。主视频时钟校正其他视频即可，不追求每帧强制 seek 或专业逐帧编辑。

**依据：** D7 E10；D7 / [`src/components/stages/DirectorStageCard.vue`](https://github.com/jtydhr88/ComfyTV/blob/42ce39d96f0c16fc6a47ad745eafdd2e5026c862/src/components/stages/DirectorStageCard.vue) 只作时间轴交互参考，并非现成的同步比较实现。

**验收/停止：** 一路短片结束不反复重启其他片；一路链接失败不拖死全部；所有 play() 失败不能显示“正在播放”。先修错误反馈再讨论同步精度，没有比较需求就不做。

### E08 · 语义质检、音频优先或裁头建议：按失败样本逐个选择

**本轮裁决：未触发。** 没有重复语义误判或对白时长返工样本；首轮错误输入已由人工审核正确拦截。

**启动条件：** 技术 QC 已稳定，但同一类人物/动作问题反复影响采用，或对白密集场景频繁因时长返工；必须先拿到真实失败样本。

**最小范围：** 语义 QC 只输出带证据的建议/未知，不替代人工批准；检查器异常不能当 PASS。对白优先只能作为现有生产策略的局部依赖调整，先读音频/时间线，不简单调换任务数组。音频裁头/尾音修剪必须可预览、有实际静音/波形依据和用户确认，不能按固定秒数切所有音轨。

**参考：** D5 F26/F27/F35 的失败归因、D3 E14 的对白优先、D6 LDS-13/LDS-27 的尾音边界；D5 / [`console/rules/failure_codes.md`](https://github.com/qiukaihui/comfyui-auto-drama/blob/362c8fd860bbbafda4b504de4f7bbe0c9a3d0d24/console/rules/failure_codes.md) 为规则依据。复用 MachineCheckRun、原 TTS 和时间线命令。

**验收/停止：** 用固定样本记录误判、漏判、额外模型调用和实际采用效果；检查超时/解析失败保持未知；一项修复不顺带改其他创作变量。未证明减少返工就不增加 VLM/TTS/声源模型。

### E09 · 最小诊断包与阶段耗时：仅补已有记录确实缺的部分

**本轮裁决：已有能力覆盖。** Job/attempt、冻结输入、Profile/Workflow、媒体 SHA、错误码、真实总墙钟和 traceback 足以复现本轮问题；缺失的细分阶段保持 `UNKNOWN`。

**启动条件：** 日志无法复现具体镜头问题，或 E04/E05 评估缺少编码、采样、解码、等待等阶段证据。

**最小范围：** 复用 Job/attempt、编译效果报告、Profile/Workflow、媒体 SHA 和错误码，导出单镜摘要；默认不附整部小说、密钥、无关项目、机器完整路径和巨型张量。阶段耗时有真实回调才记录，没有则 UNKNOWN；不增加云日志、全量链路追踪平台或计费中心。

**依据：** D4 F27/F33/F39、D7 E19、D1 §15、D6 LDS-22。对照索引只是参考，导出以你已有冻结事实为主。

**验收/停止：** 同一失败能定位到同一版本输入，导出经脱敏且有范围；无法取得的阶段不可伪装精确 ETA。已有诊断足够时只补文档，不再建新页面。

### 6.10 本轮明确不开发

不整体迁入 donor 桌面 GUI/Vue 画布/内存队列；不新增 Redis/Celery/Kafka、通用 DAG、Agent 群、RAG/向量平台、图数据库、多机多 GPU 调度；不做 Excel 主数据或强制 MD→Excel→JSON 往返；不做完整专业剪辑器、3D/动作捕捉/特效平台、全剧艺术总分或大型目标时长动态规划。

也不默认开放所有模型、自动下载/删除权重、自动猜缺失模型、所有镜头多候选、无限自愈、外部 pickle/PT 导入、无授权云回退。原报告中的目标时长压缩、离线审阅回导、跨尺寸重编码等暂不列入主线：先使用现有手工剪辑、版本导出与已验证生成方案。

若后续需要其中一项，必须重新写出“现有能力为什么不够、哪个真实样本受阻、最小消费者是什么、怎样撤回”的小任务，不能仅引用原报告的 P1/P2 当作开发授权。

<a id="s7"></a>
## 7. 验收设计：先证明问题，再证明没有破坏已有能力

### 7.1 四层验证与结果口径

| 层级 | 运行对象 | 可以证明什么 | 不能证明什么 |
|---|---|---|---|
| L0 纯规则 | 参数解析、范围集合、状态归约、图差异 | 明确输入下规则/编译是否正确 | 数据库并发、真实装配、模型质量 |
| L1 应用集成 | 真实服务装配、SQLite fixture、隔离 LLM/Comfy transport | 版本关系、事务、幂等、实际调用/写入次数、恢复分支 | 本机节点/权重是否兼容、真实出片速度 |
| L2 浏览器流程 | 现有前端测试及隔离后端 E2E | 分页/批次、关页后状态、错误反馈、人工决策入口 | 真实推理和声音/画面质量 |
| L3 经授权的本机样片 | 固定已发布 Profile、真实小样与媒体探测 | 此机器/版本/配置下的实际输入使用、成片规格、恢复和人工观感 | 所有模型/设备通用可用，未覆盖故障的绝对保证 |

**以下测试全部是计划，不是本次执行结果。** 验收记录必须分别写“通过、失败、未运行、当前不适用”；不适用必须说明执行路线/产品范围，不能用来隐藏正在使用的高风险路径。关键 L3 未做时只能称“离线验证完成”，不能称“正式出片能力已验收”。

### 7.2 主线回归矩阵

`V01—V54` 是本文验收场景索引，不要求创建 54 个新测试文件；同一参数化测试可以覆盖多个场景，已有用例满足就复用。

| 场景 | 工作包 | 层级 | 输入/故障 | 必须验证的结果 |
|---|---|---|---|---|
| V01 | K01 | L1 | 真实 build_story_ai 装配选择已发布 Profile | 仅替换 HTTP transport；无签名错误；实际 model/connection 来自所选版本，不能吞掉 Profile 参数。 |
| V02 | K01/K14 | L1 | 默认方案、不存在/撤销方案、本地与已授权远端连接 | 错误明确；本地不误继承云密钥；不能静默换模型或跨越授权。 |
| V03 | K02 | L0/L1 | 表演强度 0/0.2/0.9，重复归一化 | 合法值保留且幂等；非法值有明确反馈，不混淆 camera 与 performance intensity。 |
| V04 | K02 | L0/L1 | 推门、拉椅子、摇头；Profile 禁止相机回退 | 主体动作不被当运镜；UNSUPPORTED 不被无条件改为支持。 |
| V05 | K02/K25 | L1 | 冻结当前稿、后来的人工编辑、自动技术补齐 | 旧版不变且当前指针不被自动语义改写；需要改变时产生 diff；审核来源准确。 |
| V06 | K03 | L1 | 旧版有 BGM 当前删除；归档 SFX 镜头；null/空 cue | 只读当前有效镜头需求；不删历史；当前有 cue 时仍正确要求。 |
| V07 | K04/K15 | L0/L1 | Turbo、角色 LoRA、参考缩放、第二采样器同图 | 只改授权节点字段；非目标名称/强度/连接/尺寸不变；发布原图不可变。 |
| V08 | K05 | L0/L1 | 双人对白、旁白、同名角色、冒号/引号/换行 | 说话者身份和台词逐字保留；显示名变化不改变角色/音色身份。 |
| V09 | K06 | L0/L1 | 候选 1/2/4/6/8/16 × 生产模式 | 接收的有效值在预检、快照、执行目标、预算和 UI 相同；越界明确拒绝/确认调整。 |
| V10 | K06/K07 | L1 | 目标 4，已有 3 个合格或 2 个合格+1 个匹配在途 | 仅补所需缺口；别模式/旧输入的任务不冒充有效候选；不增加 GPU 并发。 |
| V11 | K07 | L1 | stale/force_new_take 同 run/task 连续进入两次 | 一次意图只新增一个约定候选；第二次返回原 Job，不因 len(jobs) 改变而换键。 |
| V12 | K07/K22 | L1 | 原命令重发与用户明确再次新拍 | 重发复用，新的明确命令能合法新增；同键不同 payload 冲突。 |
| V13 | K08 | L0/L2 | 零集、未开始、全失败/暂停/取消、运行+失败、全成功 | 归约真实且保留分项计数；不把所有非运行状态统一 READY。 |
| V14 | K08/K22 | L1/L2 | HTTP 200，调度 0/3、1/3、3/3 | 未启动/部分启动/全部调度准确；调度不等于生成或交付；显示每集阻塞。 |
| V15 | K09 | L1 | A 规划后导入 B；两稿相同段号不同内容 | A 集准备/重规划/校验仍读 A；新导入不自动改绑。 |
| V16 | K09 | L1 | 旧数据源映射唯一、多候选、源哈希变更、非法跨作用域 | 唯一可证明时才回填；歧义/篡改/未授权引用拒绝；不自动选最新稿。 |
| V17 | K10/K11 | L0/L1 | 61/120/121 章、仅选择前几章、无章节长文 | 授权范围覆盖/未覆盖清楚；保留批次上限；游标续接稳定，不重建人工已改集。 |
| V18 | K10/K11 | L0/L1 | 单章 30,000 字符，唯一事件在 24,001 后；5,001 字符单段 | 尾部进入实际输入/有证据摘要或明确未处理；不得宣称已完整分析。 |
| V19 | K11 | L0/L1 | 实际编号输入 3,999/4,000/4,001 字符 | 含编号、换行预算正确；超限有界拆分/明确阻塞，不静默截断。 |
| V20 | K10/K11 | L0/L1 | emoji、组合字符、重叠窗口、乱序完成、跨段对白 | 后端权威 offset 一致；并集不重复计数；合并不乱序/越范围/重复对白。 |
| V21 | K12 | L1 | 同 episode_id 旧来源草案；恢复时模板/规格变化 | 不错误自动应用；匹配输入的检查点才复用，历史草案保留。 |
| V22 | K12 | L1 | 检索命中或文本内容变化、索引更新、其他源片段 | 实际请求 digest 改变；重试消费冻结片段；同源/项目边界保留。 |
| V23 | K12 | L1 | 提纲修复、综合拆组件、半程恢复、取消 | 真实调用列表与 attempt/success/failure/reuse 统计一致，不用集数+1。 |
| V24 | K13 | L0/L1 | 适用阻断检查失败、警告、不适用项、过期质量报告 | 统一规则派生 blockers/status；失败不得自动应用；当前产品要求不被顺手放宽。 |
| V25 | K13/K27 | L1/L2 | 旧集已制作用新规划应用；预览后又有人编辑 | 新增/保留/冲突/总纲切换影响可见；取消不写入；旧 revision 不能被覆盖。 |
| V26 | K14 | L1 | 无关模型存在，当前 Profile 缺 VAE/节点；逐镜覆盖无效 | 昂贵子任务前指出精确缺项；项目默认不能掩盖逐镜实际方案。 |
| V27 | K14/K21 | L1 | 三名说话角色仅一音色；只有一人说话；旁白；静音 | 只检查实际需要生成的说话者/路线；不要求所有出场角色音色、不偷换为同一个声音。 |
| V28 | K14 | L1 | loopback/授权私网探测、无运行时配置、探测失败 | I/O 标记与真实 mock 次数一致；预检不创建生成 Job，不改变创意指纹。 |
| V29 | K15 | L0/L1 | seed=0、16:9、显式首尾帧、模型不支持高级参数 | 从 UI/预检到冻结图字段逐项可追踪；unsupported 不伪称生效；0 不当缺值。 |
| V30 | K15 | L0/L1 | 参考排序/删中间项、纯场景、同名资产、超槽 | 稳定媒体 ID 与角色正确；文字编号同步；超限不静默丢图；未经验证模式不开放。 |
| V31 | K15/K21 | L0/L1/L3 | 叙事时长、合法采样帧、带显式末帧、输出实测时长 | 沿当前几何合同计算；不随档位改剧情长度；裁切不丢硬尾帧/词尾；实测与计划分别保存。 |
| V32 | K16 | L0/L1 | 前尾 A、本首 B、本尾 C、身份 R 的继承与冲突 | 不继承 B/C/R；明确继承 A/C/R；锁定 B 冲突不提交；不得默认 B/A/R。 |
| V33 | K16 | L1 | 首次硬连续批次，前驱未完成/未选；反打/切场/未知 scene | 硬依赖等确定前驱再冻结；独立镜头可推进；软降级有原因；未知不当同场。 |
| V34 | K16 | L1 | 帧桥同事件回放；inherit 失败；前驱视频换版 | 不增加等价边界；失败不留成功指针；源视频/帧位置可追溯；只失效真实后继。 |
| V35 | K17 | L1 | 旧候选 A 当前合格，较新 B 失败；人选 A | 先用合格当前候选，不立即抽 C；人工选择不被无声覆盖；过期 A 提示冲突。 |
| V36 | K17 | L1 | 技术 PASS，自动采用 BLOCKED；重复同一 QC | 技术与采用分开，生产待处理；相同条件复用 QC，不无限重抽。 |
| V37 | K18 | L1/L2 | 历史视频/QC/render 成功，新动作或时间线失败/取消 | 当前状态不被历史遮盖；未受影响历史成果合法复用；QC 对应同一媒体。 |
| V38 | K19 | L1/L2 | 137 镜，前 100 完成，第 137 失败 | 全量统计准确、失败可定位；详情仍分页，不能简单把 limit 改极大。 |
| V39 | K19 | L2 | 批次 1/26/51/52/100 项，跨窗口跳转和刷新 | 集合/完成数不随导航窗口缩小；未加载与归档/非法 ID 区分。 |
| V40 | K20 | L1/L2 | V2 working slot 选 Variant 媒体，旧 selections 不存在 | 联系表使用当前指定版；老项目兼容、不重复；文件损坏仍阻止导出。 |
| V41 | K21 | L1/L3 | 原生混合音轨加同句 TTS、静音模式、替换音轨 | 唯一主对白来源；不虚构无声源分离的“仅去人声”；字幕对应最终台词与时基。 |
| V42 | K21 | L0/L1/L3 | 长对白、短素材、新音轨变长、末句贴近片尾 | 提前显示不可满足范围；不硬加速/截词尾/长静帧掩盖短缺。 |
| V43 | K22 | L1 | 父命令接受后响应丢失、磁盘变化、节点顺序改变 | 返回原运行及明确子关系；不因重做预检误拒绝同意图重放，不靠 nodes[0]/短码猜集。 |
| V44 | K23 | L1 | Comfy 未接收、已接收但回包丢失、回执落库前崩溃 | 分别断言 submit 次数；不确定受理先对账，无法确认明确待处理，不能盲重投。 |
| V45 | K23 | L1 | 301 秒完成、持续进展、无进展、总预算到期 | 适用生产路线按独立有界预算判断；短 smoke 保持独立；心跳/租约准确。 |
| V46 | K23 | L1 | 生成成功下载失败、晋升失败、Worker 重启 | 复查原 prompt/产物，必要时仅重下载/晋升；不再采样；半文件不晋升。 |
| V47 | K23 | L1/L3 | 取消本地等待后外部迟到成功；共享运行时其他任务 | 迟到结果不能覆盖当前新版本；不能全局误停他人；未停 GPU 不能伪装资源已释放。 |
| V48 | K24 | L1 | 无关集新增草案、共享已采用身份变化、仅磁盘/进度变化 | 分别不失效、必要失效、不失效；内容与运行状态指纹分离。 |
| V49 | K25 | L1 | A/B/C → A/X/B/C，删镜/重排/旧 diff | 未改镜头 ID/输入版本保持；伪造 ID 拒绝；不按下标误改后续全部镜头。 |
| V50 | K26 | L1 | 有身份包的源镜拆成两个镜头 | 合法声明性身份继承；不继承视频选择/批准/QC；台词不重复；历史可追溯。 |
| V51 | K27 | L1/L2 | 关页、双标签、完成事件重复、应用前取消/人工修改 | 授权续接可靠；未授权仅草案；CAS 冲突明确；不重复建集/资产；分析节点仍不自动应用。 |
| V52 | K28 | L1/L2/L3 | 零镜头、半成品集、缺身份、批量审核仅退回 2/20 | 调用现有准备/生成，等待真实结果，必要确认后继续；18 个有效镜头不重做，无伪审批。 |
| V53 | K29 | L1/L2 | 继续、原输入重试、新拍、只合成；仅字幕样式变更 | 命令范围与预告一致；原输入 seed 不变；新拍才新增；仅合成视频 GPU 提交为 0。 |
| V54 | K30 | L1/L2/L3 | 两个所选集，一个待审核，另一个无共享硬依赖 | 有限推进独立集、保留阻塞；不铺开未授权全书；预览/完整交付状态分别真实。 |

### 7.3 测试文件与执行命令

优先复用报告已定位的 [`apps/api/tests/test_episode_worker_actions.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/tests/test_episode_worker_actions.py)、[`apps/api/tests/test_whole_drama_orchestrator.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/tests/test_whole_drama_orchestrator.py)、[`apps/api/tests/test_story_pipeline_ai.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/tests/test_story_pipeline_ai.py)、[`apps/api/tests/test_automation_whole_drama.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/tests/test_automation_whole_drama.py)、[`apps/api/tests/test_compose_duration_guard.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/tests/test_compose_duration_guard.py)、[`apps/api/tests/test_comfy_workflow_bindings.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/tests/test_comfy_workflow_bindings.py) 及其 fixture。实施前核读当前内容；文件存在并不代表本表边界已覆盖。

现有前端 [`apps/web/src/features/pipeline/OneClickPipelineWorkbench.test.tsx`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/src/features/pipeline/OneClickPipelineWorkbench.test.tsx) 的 mock 导出必须与真实组件依赖一致，特别是整剧状态/启动方法；实施阶段已完成核对、更新并由全量 Vitest 验证通过。分页/批次用例放到现有对应组件测试。

只有现有模块无法清楚承载时，才考虑**拟新增** `test_story_pipeline_composition.py`、`test_episode_source_binding.py`、`test_generation_command_reentry.py` 等专项文件；这些名字不是已存在接口。

当前代码读取到 Python 要求 `>=3.12,<3.13`，后端有 `comfyui` 实机标记，前端已有 `test/build/generate:client` 脚本。依据：[`apps/api/pyproject.toml`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/pyproject.toml)、[`apps/web/package.json`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/package.json)。下面是**后续执行示例，本次没有运行**：

```bash
# 在项目已有、版本正确、与生产工作区隔离的测试环境中
cd apps/api
python -m pytest -m "not comfyui" tests/test_episode_worker_actions.py -q
python -m pytest -m "not comfyui" tests/test_whole_drama_orchestrator.py tests/test_compose_duration_guard.py -q

# 专项实际文件按本次改动加入，再执行项目要求的离线全量回归。
# 不用全量绿色替代新的针对性失败复现。
```

```bash
cd apps/web
# 使用现有锁文件对应的包管理器。这里的 npm 仅表达已有脚本调用：
npm run test -- src/features/pipeline/OneClickPipelineWorkbench.test.tsx
npm run build

# 只有 API schema 真正改变，且生成器后端环境就绪时才执行：
# npm run generate:client
# 之后检查生成 diff，重跑相关测试和 build。
```

`-m "not comfyui"` 只排除带该标记的测试，不是网络/GPU 沙箱。先检查 fixture 和 transport，必要时采用项目既有网络隔离。不要误连正在生产的数据库、ComfyUI 或模型端口，也不要为跑测试自动升级依赖/重写锁文件。

### 7.4 三个小样，覆盖目标而不是堆生成量

**文字样本 T：** 合成的 61 章与超长单章，唯一揭示放在末段；另有 A/B 同段号不同内容、emoji 和超长单段。先用隔离客户端检查实际输入和覆盖，再用经授权的真实文本模型检查关键证据。无需生成 61 集视频。

**制作样本 M：** 两人、一个场景、一个道具、4—6 镜，包含反打和一次连续动作，固定台词/当前已验证 Profile。检查身份参考、首尾角色、说话人、音轨来源、字幕、素材时长、当前选片与完整性。首次验收不同时更换模型、步数、画幅和候选数。

**恢复样本 R：** 基于 M，在受理回包、下载、晋升各处注入隔离故障；仅修改第二镜，保留一个真实硬后继和一个独立硬切镜。证明恢复、局部失效和仅合成的范围。通过后再验两个所选集的有限推进，不直接全书并发。

每次记录 commit、源版本/范围、Profile/Workflow、实际参数/参考、命令/Job/attempt/prompt_id、输入输出 SHA、必要人工决策、真实提交数、复用集合、未验收项。人工观感记录具体偏差，不写不可复核的“质量提升 80%”。

### 7.5 完成标准与最少指标

| 项目 | 通过标准 | 统计边界 |
|---|---|---|
| 输入一致性 | 被覆盖用例中，确认计划与实际模型请求无未说明差异 | 同一有效 Profile/编译版本 |
| 同意图重复生成 | 故障注入用例额外新建 GPU 采样为 0 | 不计用户明确的新拍命令；不泛化为网络绝对 exactly-once |
| 原文覆盖 | 授权范围中无未说明遗漏/重复；未处理明确 | 已发送、已验证输出与完整语义理解不是同一指标 |
| 局部返工 | 实际重做集合等于影响预览；真实依赖可解释 | 硬衔接、原生对白、时间线变化分别计 |
| 用户交接 | 不需离开流程猜下一步；必要决策集中可恢复 | 不以删除审核来减少点击 |
| 产物与交付 | 当前版本、完整性、声音/时长及审核证据正确 | 静态预演/缺镜预览不能算正式完整交付 |

主线通过后停止新增功能。性能优化仅记录真实墙钟、失败/复用、已采用结果成本；未知阶段保持未知。E04 缓存需要额外正反例、受控加载、原子写入和净收益测试，不能借主线通过自动上线。

<a id="s8"></a>
## 8. 数据兼容、API 与回滚：修复不等于重写历史

### 8.1 先确认实际 schema，再决定是否迁移

本次没有重新完整读取所有迁移文件、所有生产库形态和本机已发布工作流，所以不给可能错误的建表/UPDATE 脚本。每个涉及持久化的 PR 必须先列出“现存字段、权威写入命令、缺失语义、最小兼容方式”。这是迁移前置，不是把数据设计留给任意发挥。

| 变更 | 最小兼容方式 | 明确禁止 |
|---|---|---|
| 分集源绑定 | 优先复用既有源关系/JSON；新记录写版本与范围；旧记录仅在证据唯一时回填 | 把所有旧集一键绑定项目最新原稿 |
| 覆盖/分块 | 给新计划/检查点增加可区分的范围格式和算法版本；已有结果保留 | 改写源文本以适配段号；把重复窗口都算覆盖 |
| 生成/请求指纹 | 新计划用新版本，历史快照可读；无法证明兼容的旧结果只供历史查看或明确复核 | 重算旧任务指纹后伪装“当时就是这样执行” |
| 候选命令身份 | 复用原幂等/Job/attempt 关联；新意图固定序号；遗留在途先对账 | 用变化中的任务总数恢复新键，或删除旧任务消除冲突 |
| 帧语义 | 新边界按新规则生成；旧首尾输入保持历史；必要时显示迁移建议 | 全库 END_FRAME→FIRST_FRAME 替换、自动解锁首帧 |
| 运行状态和当前资格 | 读模型派生新展示字段；兼容旧消费者并逐步切换 | 为了界面绿色改旧 Job、QC、Review 状态 |
| 后端续接 | 保存明确授权/目标和已有子任务引用；原命令负责落库 | 新父表复制一套子镜头完成状态；分析 Worker 直接批准/发布 |
| 声音/工作流 | 新策略随新快照冻结；明确旧混合音轨怎样消费 | 回滚时自动恢复原生人声却继续叠加 TTS |

同一类语义只设一个权威解释；不因两份报告给出不同建议字段名而全部新增。小 JSON 扩展也必须有 schema 校验、版本和消费者，不能任意堆字段。

### 8.2 部署与备份顺序

先暂停新派发或使用现有维护模式，盘点在途 Job、attempt、外部 prompt 与 GPU 租约；允许已知任务结束或按既有规则对账。使用项目支持的一致性备份方式保存 SQLite 与所需媒体/清单关系，不能在 WAL 活跃时只复制单个 `.db` 并宣称备份完整。

先在隔离副本验证迁移、旧数据读取和关键用例，再部署代码；增加字段优先采用兼容扩展，让旧读端至少能够明确拒绝不支持版本，而不是误读。新自动推进/实验路线通过明确策略启用，不影响未授权旧项目。

回滚前停止新命令并确认在途任务归属。优先回退代码或关闭新策略，保留已完成媒体、历史版本、回执和审计。数据回退只在有备份、无新增成果丢失风险且实际迁移可逆时执行；否则旧客户端只读或停止执行，不能做破坏性降级。

### 8.3 API 与前端同步

后端 schema 是接口权威，使用项目已有生成器更新客户端并检查 diff；不手改生成文件掩盖不兼容。状态聚合、范围覆盖、有效参数、下一步动作等新增字段先兼容旧调用者；需要改变语义时明确版本，不只改字段注释。

前端不再自主复制后端候选规则、当前媒体资格或模型帧数公式。前端可以做输入提示，但最终预检/冻结由后端判定。页面 refresh、focus、事件订阅用于刷新事实，不承担必须执行的业务推进。

### 8.4 每个 PR 的改动预算

一个 PR 围绕一个问题的权威服务、必要调用方、测试和接口变更展开。文件数不设机械硬上限，但新增服务/表/依赖/全局枚举都必须解释为何现有机制不够。多个无关问题、依赖升级、格式化全仓、拆大文件和命名重构不混入修复 PR。

涉及 `episode_worker_actions.py`、`episode_production_runs.py`、源绑定或编译规则的 PR 由一个集成顺序管理，避免多个 AI 同时大改同一职责。可以独立进行纯前端导航集合修复和隔离查询修复，但合并前仍跑共同回归。

<a id="s9"></a>
## 9. 给编码 AI 的派发方式：每次一个阶段、一个明确切片

### 9.1 第一条任务应这样下达

```text
项目：Qioooba/local_drama_studio。
依据：《Local Drama Studio 最终改造与测试实施方案》v1.0。
本次仅执行 Phase 0，不开始实现 Phase 1—6。

1. 读取当前仓库规则和工作区状态，保留用户未提交修改。
2. 对比报告基线 f15a354ec81fa56233154ac753c4499356b9c2a2、
   本报告复核 HEAD e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3 与当前 HEAD。
3. 定位实际使用的故事入口、分集入口、旧 GPU_H3/V2 执行路线、工作版本、
   批量审核与恢复写入命令。读取当前 schema 和相关测试，不凭文件名猜功能。
4. 将 K01—K30 标成：待复现、当前已修、现有能力已覆盖、本轮路线不适用。
   没有运行证据时不得标“通过”；给出相应文件/函数/测试依据。
5. 列出隔离测试环境、需要保护的数据、在途任务和最小备份/回滚方案。
6. 本次只交付基线记录与 Phase 1 的第一个小 PR 范围，不修改生产数据，
   不启动真实模型，不自动下载/删除权重，不擅自升级依赖。

建议下一条单独实施 K01 的真实装配回归与最小修复。
如果当前已修好 K01，则记录证据，补缺失回归或无需修改结项，不反向覆盖新实现。
```

### 9.2 后续每个工作包的通用执行约束

```text
仅实施本次指定的 Kxx / 子 PR，不顺手执行全文。
先完整读取目标函数、直接调用者、权威写入服务、实际 schema 和相关测试。
把报告静态事实与当前可达表现分开；用失败测试确认，不复制造出同一错误的假实现。

先找已有能力。已有机制覆盖时仅补测试或关闭实现项。
保留 Job/attempt、Variant、Prompt/MediaVersion、工作槽、ReviewDecision、
Profile/Workflow、FrameBridge 和时间线权威；不另建相同任务/媒体/审核系统。

不要忽略 Profile 参数、扩大所有上限、关闭 stale/preflight、伪造 APPROVED、
用历史结果冒充当前结果，或把任何网络错误都处理成换 seed 再抽。
冻结输入不可变；阶段完成产生新事实后，为下一阶段建立新快照。
人工锁定和 expected revision 必须保留；模型输出只是受限提案。

仅在用户授权的隔离范围执行测试。先 L0/L1，再 L2；真实 L3 需操作者确认。
外部源码复制先核验固定版本授权和依赖；不清楚时依据行为独立实现。
接口变更通过现有生成器同步客户端，不手改生成结果蒙混通过。

交付：复现条件、最小修改、被复用的服务、真实文件/函数、测试命令及实际结果、
未验证场景、数据/接口兼容方式、回滚、下一阶段准入结论。
没有执行的测试明确标“未运行”，不能把 mock 通过说成真实出片通过。
```

### 9.3 一个可直接使用的工作单示例

这是实施说明 YAML，不是项目现有 API 或运行配置。文件名为建议测试落点，不代表已经存在。

```yaml
work_order:
  id: K01
  phase: 1
  goal: 故事规划实际客户端遵循选中且已发布的 Profile 版本
  mode: verify_then_minimal_fix
  prerequisites:
    - Phase 0 完成，已核对当前装配与本地改动
  read_before_write:
    - apps/api/local_drama/infrastructure/service_composition.py
    - apps/api/local_drama/application/story_pipeline_ai.py
    - apps/api/local_drama/application/ports/creative_generation.py
    - apps/api/local_drama/application/local_llm.py
    - 现有 Profile/ProviderConnection 解析服务与相关测试
  allowed_change:
    - 在现有服务或装配适配器中补明确的 Profile 到客户端解析
    - 端口、调用方、实现采用同一合同
    - 复用已有密钥与网络权限解析
  forbidden:
    - 删除 profile_version_id 后调用默认模型
    - 接收但忽略 profile_version_id
    - 新建第二个模型平台
    - 调用真实 GPU 或远端 API 进行无授权试验
  acceptance:
    - 真实 build_story_ai 装配无签名错误
    - 显式 Profile 的 model/connection 实际生效
    - 无效或撤销 Profile 明确拒绝
    - 本地连接不继承云端凭据
  test_method: 仅 mock HTTP transport，保留真实服务装配与隔离数据库
  stop_when: 当前实现已经满足上述测试，或最小修复与回归完成
  delivery: 改动文件、实际测试结果、未验证项、兼容与回滚说明
```

### 9.4 阶段验收记录模板

```text
Phase / K / 子 PR：
开始 commit / 完成 commit：
原报告定位与本次确认的实际代码：
结论：修复 / 仅补测试 / 已覆盖无需修改 / 本轮不适用 / 阻塞
真实复现输入与最小失败：
实际修改文件与复用服务：
新增数据/接口/依赖及必要性：
已运行命令、结果和证据位置：
未运行场景及原因：
是否触及真实模型/生产数据：
兼容旧数据、在途任务和历史快照的方式：
回滚办法：
下一阶段是否具备准入条件：
```

验收记录可放在项目既有文档/PR 描述中，不需要为这份方案新增一套任务管理系统。

<a id="s10"></a>
## 10. 原报告去向与借鉴索引：哪些被合并，哪些被降级

### 10.1 去重规则

以下原编号均带 D 前缀识别。`Kxx` 指本文主线工作包，`本文 Eyy` 指第 6 节条件性增强。映射表示目标被保留或已明确取舍，不表示该项实现已经完成。原报告的任务卡和发现说明重复表达同一问题时，不再次计作新需求。

**重要：** donor 自身缺陷是迁移反例或测试向量，不自动登记成你项目的 bug。例如 MiniMax-H3-lite 的 seed=0、16:9 和多参考注入问题，应启发 K15 穿透测试；只有本项目实际失败才修改本项目实现。

### 10.2 D1：H3 条件缓存报告

| 原报告范围 | 本文去向与裁决 |
|---|---|
| F01 / T01 | K06：统一候选数，不提高默认并发或上限 |
| F02/F03 / T02/T04/T07 | K16：帧角色、来源、幂等和真实硬依赖，一并收敛 |
| F04 / T03/T05/T06 | K08/K22：状态与运行关联、启动幂等 |
| F05 / T09/T10 | K28：接现有 prepare，不造小说 Agent |
| F06 / T17—T21/T38 | K05/K15；字段级语义建议延后到本文 E02/E03 |
| F07 / T15/T16/T22—T24 | K07/K17/K29；推荐交互/同步仅按本文 E07 触发 |
| F08 / T11—T13 | K14：精确 Profile/权重/说话者预检 |
| F09/F10 / T14/T08 | K15/K21/K23：规格与声音、实际路线的超时恢复 |
| T25—T30 / §6/§15 | 本文 E04/E09：缓存与阶段度量，不进入首轮默认 |
| T31/T32 | 本文 E05/E09：跨尺寸/调度仅实证触发，保留原 GPU 协调 |
| T33—T37/T39 | K10—K12/K16/K21/K29；摘要/空间增强归本文 E02 |
| T40 | 不列主线；只有实际离线往返需求才提版本化审阅交换，Excel 不作权威 |

### 10.3 D2：MiniMax-H3-lite 报告

| 原报告范围 | 本文去向与裁决 |
|---|---|
| F01/F02 / T01—T05 | K09—K12；其中相邻分集叙事拆合 T03 归本文 E02，先修覆盖与绑定 |
| F03/F04 | K19/K18：分页总量与当前资格 |
| F05/F06/F07 | K14/K21：目标模型、逐说话者、真实 I/O 探测说明 |
| F08/F09 | K23：仅本轮实际使用/准备启用的路线需要改实现 |
| F10/F11 | K27/K28：后端续接和集中审核；不默认机器代替人类批准 |
| F12/F14/F15 | K15/K21/K05：规格、实际生效、说话者与音频来源 |
| F13 | K15 先保证当前图片输入；视频/音频参考扩展归本文 E05 |
| F16/F17/F18 | K06/K17/K12/K30：预算、实际调用、穿透验收 |
| B01—B16、RISK-01—RISK-12 | 按 K15 的交互/契约/注入测试选择借鉴；模型猜测、线程队列、固定风格、个人路径不搬入；复制前按 §10.9 核验 |

### 10.4 D3：Toonflow 报告

| 原报告范围 | 本文去向与裁决 |
|---|---|
| F01—F03 | K09—K11：固定来源、60/24k/4k 范围闭合 |
| F04/F05/F06/F07/F08 | K27/K28/K08/K18/K22：应用接力、零镜头准备、真实状态、幂等 |
| F09/F10/F11 | K02：显式值、相机能力与冻结当前稿/审计来源保护 |
| F12/F13/F14/F15/F16 | K25/K26/K24/K14/K12：稳定匹配、身份继承、依赖范围、声音、调用计数 |
| §4.17 | §7.3：前端 mock/整剧调用测试维护，未运行不能称失败已复现 |
| 原 E01—E06 | 来源证据/已有分析复用纳 K09—K12；进一步事件摘要/别名/改编力度归本文 E02，不新建平台 |
| 原 E07/E08 | K21/K13 及已有有限结构修复；额外创作节奏建议归本文 E02 |
| 原 E09—E13 | K15/K16/K20/K26/K28 先复用身份/参考/批量审核；新状态创作建议归本文 E02 |
| 原 E14 | 本文 E08：对白优先须有时长返工样本，不全局调换任务顺序 |
| 原 E15/E16 | K14/K21：现有音色与字幕时间依据先核验、缺口才改 |
| 原 E17/E18 | 本文 E03：局部助手与代码层工具权限，默认不开发 |
| 原 E19/E20 | K27/K29/K25：服务端事实、diff、人工采用与版本保护 |
| 原 E21—E27 | K07/K08/K17/K22/K23/K28/K29：失败分类、目标/预算、下一步、恢复与真实进度 |
| 原 E28/E31 | 本文 E09/E02：同 runtime 小批次需测量，阶段模板复用原 Prompt 版本 |
| 原 E29/E30/E32 | K14/K30；交付版本清单先核查既有导出，缺诊断再本文 E09，不新增交付数据库 |

### 10.5 D4：H3 Director 报告

| 原报告范围 | 本文去向与裁决 |
|---|---|
| F01/F36 | K01：真实 Profile 装配的高优先小修 |
| F02/F03/F30/F31/F32/F33 | K09—K13：来源、预算、覆盖、恢复输入、质量、实际调用 |
| F04/F05/F34/F35 | K27—K29：薄后端衔接与简化交互 |
| F06 | K28 保留集中真实审核；无人审核草稿选择不作为首轮要求，需独立产品政策 |
| F07/F08/F37 | K15/K14 保证当前路线真实能力；所有新模式/音视频物化归本文 E05 |
| F09/F10/F43 | K16：先尾到首、锁定冲突和依赖，Motion Context 不进主线 |
| F11/F17/F18/F19/F20/F22 | K05/K15/K21：输入/帧/声音合同；带音轨单 MP4 不需多产物平台 |
| F12/F13/F14/F28 | K06/K17：有效候选预算、先筛已有、采用失败传播 |
| F15/F16 | K23：生产预算与外部受理生命周期 |
| F21/F27/F29/F39 | 模型适配底线 K15；额外提示增强归本文 E02、阶段度量/诊断归本文 E09，沿用原 GPU 协调 |
| F23/F24/F26/F38 | K07/K18/K24—K26/K29：局部重拍、真实指纹、当前资格、人工优先 |
| F25/F40 | 本文 E04/E05：有收益才缓存/精修，不照抄超大次数上限 |
| F41/F42/F44 | §10.9 复制前核验；旧入口只按调用证据弃用；K30 小样发版 |

### 10.6 D5：comfyui-auto-drama 报告

| 原报告范围 | 本文去向与裁决 |
|---|---|
| F01/F02/F03/F04 | K28/K08/K27：零镜准备、聚合、0/N 反馈、后端应用 |
| F05/F06/F07/F12 | K09—K13：覆盖、固定稿、统一门控和应用影响 |
| F08/F09/F10/F11 | K18/K14/K22：当前成果、精确依赖、启动身份 |
| F13/F14/F15 | 本文 E02；已存在的来源/上下文底线由 K09—K12/K24 先保证 |
| F16/F17/F19/F20 | K25/K26/K29/K15/K05：局部保护、参考作用、确定性编译和说话者 |
| F18/F23/F24 | 当前合法槽位纳 K15；扩展参考策略/动作复杂度/新状态生成归本文 E02/E05，不强制多图 |
| F21/F22 | K21：同份台词/声音权威与现有时长防线 |
| F25/F27/F29/F30 | K16/K17/K06/K24：连续性、最小修复、有效预算、实际依赖 |
| F26/F31/F35 | 本文 E08/E09：语义 QC、耗时估算、音频裁头降为实证触发 |
| F28/F32/F33/F34/F36 | K30/K28/K29/K14：代表小样、可操作错误、发布验收、预览与交付、范围化操作 |

该报告把主编译函数部分写作 `_episode_user`；本次实际读取的是 `FullStoryAIGenerationService._episode_prompt`。本文按真实符号定位，不直接复制误名创建同义新函数。

### 10.7 D6：Director-Cut-Studio 报告

| 原报告范围 | 本文去向与裁决 |
|---|---|
| LDS-01/LDS-18 | K10—K13：覆盖与统一质量门控 |
| LDS-02/LDS-03/LDS-07 | K27/K28/K08/K22：后端推进、真实状态；不重写历史模板 |
| LDS-04/LDS-05/LDS-28 | K03/K04/K15：历史 cue、仅 Turbo、契约指定节点 |
| LDS-06/LDS-20 | K23/K07/K17：先验证现有对账、按失败类别有界恢复 |
| LDS-08/LDS-09/LDS-10 | K14/K16/K05/K15：精确依赖、帧桥、对白与参考 |
| LDS-11/LDS-12 | 本文 E06：长镜分段/局部时间，仅在当前 Profile 不满足实际长镜时启用 |
| LDS-13/LDS-17/LDS-23 | K21：对白、可行时长、主声音来源；高级分段保护随本文 E06 |
| LDS-14/LDS-19/LDS-21/LDS-22 | K24—K26/K29/K06/K12/K18：真实依赖、编辑、预算与统计 |
| LDS-15/LDS-16 | K15/K16：身份与物理槽位、运动边界；不要求所有模式多参考 |
| LDS-24/LDS-25 | 暂不开发目标时长自动剪辑/素材理解平台；有明确失败再独立立项，不塞主线 |
| LDS-26/LDS-27 | 证据追溯保留于 K09—K12；额外记忆/尾音策略归本文 E02/E08，不做长冻结填充 |
| LDS-29/LDS-30 | K30 小样验收；§10.9 只阻断未核验的外部代码复制，不阻断内部独立修复 |

### 10.8 D7：ComfyTV 报告

| 原报告范围 | 本文去向与裁决 |
|---|---|
| F01/F02/F03 | K28/K09/K11：准备主线、固定来源、既有分层分析到 4k 拆解 |
| F04/F05/F06 | K14/K06：模型粒度、逐说话者与候选数 |
| F07/F08/F09/F10/F11 | K16/K07/K24/K12：首尾/等待、重入、依赖范围、实际检索请求身份 |
| F12/F13 | K19/K20：批次不被窗口截短，联系表使用 V2 当前工作槽 |
| 原 E01/E02/E03 | K29/K28/K24：四种操作、一个流程、真实影响预览 |
| 原 E04/E05/E08 | K15/K16：参考角色、相邻事实、首尾静态画面与运动分工 |
| 原 E06 | 本文 E02：新增戏剧任务字段需实际消费者 |
| 原 E07/E11/E12/E14/E15 | K29/K19/K15/K06/K17：局部修改、批量状态、实际生效、预算、技术 QC |
| 原 E09/E10 | 本文 E01/E07：静态预演和同步比较，非强制主线 |
| 原 E13 | K15 的现有绑定诊断先满足；更广的工作流接入诊断只在新图接入受阻时小补，不搬通用图改写器 |
| 原 E16/E17/E18/E19 | K21；本文 E03；K09—K11；本文 E09。分别是声音、局部助手、覆盖、诊断 |

### 10.9 外部代码复用与许可：是复制前置，不是整个项目的额外平台

七份附件的许可结论、阅读范围和参考版本不完全相同；本次没有重新取得并审核全部 donor 的 LICENSE、嵌入代码来源、模型权重与示例素材授权，因此不发布新的“全部可以直接复制”结论。

实际复制前核对该固定提交中的 LICENSE/NOTICE/文件头以及二次移植来源，记录路径、commit、复制范围、修改内容与所需声明。Toonflow、Director-Cut-Studio 等原报告明确提示的授权不确定性不能忽略。即使某仓库根许可明确，也不能自动扩大到模型、第三方节点、素材和提示词的所有内容。

默认优先顺序：**复用本项目实现 → 将行为和反例写成测试 → 独立实现小规则 → 必要时受许可约束地复制小段代码**。授权不清阻断具体复制，不阻断 K01 等本项目自身缺陷的独立修复。本节不是法律意见；出现许可解释争议应交由合适的专业人员核验。

### 10.10 最值得保留的代码借鉴路线

这张表用于有针对性地查 donor，而不是要求再读完或 vendoring 整个仓库。主线中大量修复来自本项目自身矛盾，并不需要借第三方代码。

| 参考位置（固定提交） | 本文落点 | 只借什么 / 不借什么 |
|---|---|---|
| D1 / [`tools/excel_to_multi_chain_json.py`](https://github.com/HEEEeeeeN/ComfyUI-H3-Conditioning-Cache-AI-Drama-Production-Suite/blob/5c44d76ca1ec301d4e3056c6b863c1f94e07a8dd/tools/excel_to_multi_chain_json.py)：`compute_slots` | K15 | 参考编号和实际输入同源；用媒体稳定 ID 替换 donor 名称主键 |
| D1 / [`tools/shot_md_to_excel.py`](https://github.com/HEEEeeeeN/ComfyUI-H3-Conditioning-Cache-AI-Drama-Production-Suite/blob/5c44d76ca1ec301d4e3056c6b863c1f94e07a8dd/tools/shot_md_to_excel.py)：`spec_check_h3_prompt` | K15 / 本文 E02 | 只借字段问题定位，不搬固定画风/词语黑名单或 Excel 往返 |
| D1 / [`h3_conditioning_cache.py`](https://github.com/HEEEeeeeN/ComfyUI-H3-Conditioning-Cache-AI-Drama-Production-Suite/blob/5c44d76ca1ec301d4e3056c6b863c1f94e07a8dd/h3_conditioning_cache.py)：`H3EncodeConditioning / H3LoadConditioning` | 本文 E04 | 按需编码/加载；重写实际输入键、可信路径与原子 manifest |
| D2 / [`assets/js/shared.js`](https://github.com/ReSerendipity/MiniMax-H3-lite/blob/89ba581b63aa7fb8532e127143958967e96fbc93/assets/js/shared.js)：`renderRefs / getActiveParams` | K15 | 参考用途、数量与高级项折叠；只显示当前已验证能力 |
| D2 / [`tests/test_comfy_engine.py`](https://github.com/ReSerendipity/MiniMax-H3-lite/blob/89ba581b63aa7fb8532e127143958967e96fbc93/tests/test_comfy_engine.py) | K15/K30 | 节点注入测试；补 seed=0、首尾、比例、超限拒绝等边界 |
| D3 / [`src/utils/cleanNovel.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/src/utils/cleanNovel.ts)：`processChapter`；D3 / [`src/agents/scriptAgent/tools.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/src/agents/scriptAgent/tools.ts)：`get_novel_text` | K11 / 本文 E02 | 逐段摘要可回原文；复用现有分析节点，不复制内存并发 |
| D3 / [`src/agents/productionAgent/tools.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/src/agents/productionAgent/tools.ts) | K25/K29 / 本文 E03 | 对象级、有作用域的修改；后端自己读事实，不依赖 Socket 回调作为完成 |
| D4 / [`director/plan.py`](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/3cea821640d02f16f24db3a8b61a34fa20d540f6/director/plan.py)：`concat_common_segment_prompt / merge_indexed_refs` | K15 | 公共约束、局部动作和实际参考清单一致；不把整段 tensor pack 传 API |
| D4 / [`director/external_groups.py`](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/3cea821640d02f16f24db3a8b61a34fa20d540f6/director/external_groups.py)：`infer_i2v_kind / pack_r2v_group` | 本文 E05 | 明确输入与模式；AUTO 服从当前发布能力和身份要求，不盲目推断 |
| D5 / [`console/rules/h3_expand.md`](https://github.com/qiukaihui/comfyui-auto-drama/blob/362c8fd860bbbafda4b504de4f7bbe0c9a3d0d24/console/rules/h3_expand.md)；D5 / [`console/rules/failure_codes.md`](https://github.com/qiukaihui/comfyui-auto-drama/blob/362c8fd860bbbafda4b504de4f7bbe0c9a3d0d24/console/rules/failure_codes.md) | K05/K15/K17/K21 / 本文 E02/E08 | 对白归属、参考作用、失败最小修复；不照抄正则猜人/固定情绪 |
| D6 / [`comfy_submit_worker.py`](https://github.com/karuvanan/MiniMax-H3-Director-Cut-Studio/blob/e093e836a014b65a3fc079a166ba9c88685cd4ff/comfy_submit_worker.py)：`wait_for_history` | K23 | 断线后查询原 prompt；不复制一套新的轮询/任务状态 |
| D6 / [`segment_engine.py`](https://github.com/karuvanan/MiniMax-H3-Director-Cut-Studio/blob/e093e836a014b65a3fc079a166ba9c88685cd4ff/segment_engine.py)：`rebase_timed_rows / plan_shot_render_segments` | 本文 E06 | 局部时间、覆盖、对白边界；不复制固定时长/fps 常量 |
| D7 / [`nodes/stages/director.py`](https://github.com/jtydhr88/ComfyTV/blob/42ce39d96f0c16fc6a47ad745eafdd2e5026c862/nodes/stages/director.py)：`DirectorStage.execute / _director_clip_hash` | K16/K24/K29 | 逐镜完成与增量思想；指纹用本项目冻结版本而非 URL/工作流标签 |
| D7 / [`tests/test_director_stage.py`](https://github.com/jtydhr88/ComfyTV/blob/42ce39d96f0c16fc6a47ad745eafdd2e5026c862/tests/test_director_stage.py) | K07/K24/K30 | 实际调用次数、修改一镜的影响集合；另加本项目数据库事实断言 |
| D7 / [`runners/animatic.py`](https://github.com/jtydhr88/ComfyTV/blob/42ce39d96f0c16fc6a47ad745eafdd2e5026c862/runners/animatic.py)：`boards_to_animatic` | 本文 E01 | 低成本分镜预演；复用现有 CPU Job/FFmpeg，不用 FakeMultishotRunner |

<a id="appendix-a"></a>
## 附录 A：七份原始附件与完整阅读登记

以下文件均为本次用户上传的实际附件，完整阅读包括其附录、任务单和限制。文件校验摘要只用于识别本次材料，不证明材料中每个判断已经运行验证。D 编号是本文引用命名空间，避免原报告都使用 F01/T01 引起混淆。

<a id="d1"></a>
### D1 · H3 条件缓存套件

原文件：`local_drama_studio_H3_optimization_report.md`\
阅读：全文，**1,118 个物理行**。\
SHA-256：`971e32ba63c8f1d45dec694733b2a3b4d68f63c74368e97bf75cbe8efdc63061`\
参考仓库：[HEEEeeeeN/ComfyUI-H3-Conditioning-Cache-AI-Drama-Production-Suite](https://github.com/HEEEeeeeN/ComfyUI-H3-Conditioning-Cache-AI-Drama-Production-Suite/tree/5c44d76ca1ec301d4e3056c6b863c1f94e07a8dd)\
参考提交：`5c44d76ca1ec301d4e3056c6b863c1f94e07a8dd`。本项目原审计基线均为 `f15a354ec81fa56233154ac753c4499356b9c2a2`。

<a id="d2"></a>
### D2 · MiniMax-H3-lite

原文件：`local_drama_studio_code_review_2026-09-09.md`\
阅读：全文，**2,121 个物理行**。\
SHA-256：`189d469dfa76d604810964c9bae93a8647bdc89064bf5c216fd5826160d8a62b`\
参考仓库：[ReSerendipity/MiniMax-H3-lite](https://github.com/ReSerendipity/MiniMax-H3-lite/tree/89ba581b63aa7fb8532e127143958967e96fbc93)\
参考提交：`89ba581b63aa7fb8532e127143958967e96fbc93`。本项目原审计基线均为 `f15a354ec81fa56233154ac753c4499356b9c2a2`。

<a id="d3"></a>
### D3 · Toonflow

原文件：`local_drama_studio_vs_Toonflow_source_review_2026-09-09.md`\
阅读：全文，**1,607 个物理行**。\
SHA-256：`fdbe5c4fff1a64bf5edba1d8d48afb69c58c2054c314784e8dd729e8ba64a0fb`\
参考仓库：[HBAI-Ltd/Toonflow-app](https://github.com/HBAI-Ltd/Toonflow-app/tree/e03cf590eb0cab63534a4040db9acb4ec95b42a6)\
参考提交：`e03cf590eb0cab63534a4040db9acb4ec95b42a6`。本项目原审计基线均为 `f15a354ec81fa56233154ac753c4499356b9c2a2`。

<a id="d4"></a>
### D4 · H3 Director

原文件：`Local_Drama_Studio_源码审计与H3优化报告_2026-09-09.md`\
阅读：全文，**1,012 个物理行**。\
SHA-256：`e22ee3f925891c274500bbc478a807e63852162b99fe862aa7779df494f150eb`\
参考仓库：[AIMixer/ComfyUI_MiniMaxH3_Director](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/tree/3cea821640d02f16f24db3a8b61a34fa20d540f6)\
参考提交：`3cea821640d02f16f24db3a8b61a34fa20d540f6`。本项目原审计基线均为 `f15a354ec81fa56233154ac753c4499356b9c2a2`。

<a id="d5"></a>
### D5 · comfyui-auto-drama

原文件：`local_drama_studio_vs_comfyui_auto_drama_audit_2026-09-09.md`\
阅读：全文，**1,194 个物理行**。\
SHA-256：`708136eb8d47826de43e41b99c889467b6c36efb6d240d72e476e25934d3ddac`\
参考仓库：[qiukaihui/comfyui-auto-drama](https://github.com/qiukaihui/comfyui-auto-drama/tree/362c8fd860bbbafda4b504de4f7bbe0c9a3d0d24)\
参考提交：`362c8fd860bbbafda4b504de4f7bbe0c9a3d0d24`。本项目原审计基线均为 `f15a354ec81fa56233154ac753c4499356b9c2a2`。

<a id="d6"></a>
### D6 · Director-Cut-Studio

原文件：`local_drama_studio_源码对比与优化报告_2026-09-09.md`\
阅读：全文，**1,658 个物理行**。\
SHA-256：`e039c35bc2c7e0673647c2809f7d620d608bb2d72668dee47f03e28355da4537`\
参考仓库：[karuvanan/MiniMax-H3-Director-Cut-Studio](https://github.com/karuvanan/MiniMax-H3-Director-Cut-Studio/tree/e093e836a014b65a3fc079a166ba9c88685cd4ff)\
参考提交：`e093e836a014b65a3fc079a166ba9c88685cd4ff`。本项目原审计基线均为 `f15a354ec81fa56233154ac753c4499356b9c2a2`。

<a id="d7"></a>
### D7 · ComfyTV

原文件：`local_drama_studio_vs_ComfyTV_源码深度审计与改造报告.md`\
阅读：全文，**1,190 个物理行**。\
SHA-256：`7c81fc03d395fd574dd98a4917ef5658e3c9f86113e2932a28d380d13a876c12`\
参考仓库：[jtydhr88/ComfyTV](https://github.com/jtydhr88/ComfyTV/tree/42ce39d96f0c16fc6a47ad745eafdd2e5026c862)\
参考提交：`42ce39d96f0c16fc6a47ad745eafdd2e5026c862`。本项目原审计基线均为 `f15a354ec81fa56233154ac753c4499356b9c2a2`。

<a id="appendix-b"></a>
## 附录 B：本次补充源码核读范围

以下表格严格限制本次 SRC 证据。其他文件的定位来自已完整读取的附件，应在实施时复核。范围是 GitHub 源文件请求/实际可见范围，不使用工具 JSON 包装的 L2 行号充当源代码行号。

| 文件/资源 | 本次核读范围 | 直接支持的判断 |
|---|---|---|
| GitHub main 提交元数据 | 完整响应 | HEAD 与父提交、仅 README 修改 |
| [`apps/api/local_drama/infrastructure/service_composition.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/infrastructure/service_composition.py) | 全文件 | 真实 build_story_ai 注入 LocalLLMService |
| [`apps/api/local_drama/application/story_pipeline_ai.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/story_pipeline_ai.py) | 从文件头到 _synthesise 前半；返回后段截断，未声称全文 | Profile 调用、24,000 字符截取、compact digest 结构 |
| [`apps/api/local_drama/application/local_llm.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/local_llm.py) | 1—240；900—1110 | 4,000 上限、编号/源 offset；client 参数与 ProviderConnection/探测 |
| [`apps/api/local_drama/application/adaptation_analysis_execution.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/adaptation_analysis_execution.py) | 1—215 | 已有分层节点、同源检索、请求 hash、调用记录、禁止分析节点自动应用 |
| [`apps/api/local_drama/application/audio_requirements.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/audio_requirements.py) | 全文件（1—170 请求覆盖） | cue 查询未限当前修订/归档；已有空值/兼容处理 |
| [`apps/api/local_drama/application/episode_worker_actions.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/episode_worker_actions.py) | 1—70；290—600 请求后段截断，另完整读取 535—655 补齐关键分支 | 已有动态 stale 读模型、I2V 解析、首尾绑定、候选数、force/stale 与在途判定顺序 |
| [`apps/api/local_drama/application/shot_production_normalizer.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/shot_production_normalizer.py) | 1—230 | 表演强度默认覆盖、运镜字词推断与能力回退 |
| [`apps/api/local_drama/application/comfy_jobs.py`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/local_drama/application/comfy_jobs.py) | 410—590 | Turbo 改写、native_audio 边界、旧运行等待起始；未完整核读全部恢复实现 |
| [`apps/api/pyproject.toml`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/api/pyproject.toml) | 全文件 | Python 版本、pytest 与 live ComfyUI 标记 |
| [`apps/web/package.json`](https://github.com/Qioooba/local_drama_studio/blob/e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3/apps/web/package.json) | 全文件 | test/build/generate:client 脚本 |

**原始编制阶段没有做、但 2026-09-13 实施阶段已经补齐：** 完整工作区运行、pytest/Vitest、生产构建、隔离浏览器用户流、真实本地 LLM、真实 ComfyUI/H3、已发布工作流实测、代表性媒体抽查、正式采用与本地交付。生产数据库未迁移或写入，实施使用备份快照和独立 UAT 根目录；七个参考仓库没有再次整体复制，因本轮没有外部代码复制需求，许可停止线未触发。

## 最终执行结论

截至 2026-09-14，Phase 0—6 与 K01—K30 的第二轮实现复核、真实缺口修复和代表性 UAT 已收尾。目标仍为：**读对并冻结原稿范围、保护人工内容、准确执行已确认输入、复用有效成果而不重复花算力、正确显示当前状态、把单集推进到可审阅/正式采用/交付，并验证所选两集独立推进。** 第二轮没有由测试数量推定模型审美通过：6 镜样例中镜头 5/6 仍因生成质量被人工拒绝，EP2 保持 0 镜以证明隔离而非冒充完整交付。逐项证据、对标 F01—F08 处置和最终测试限制见 `docs/evidence/round2-final-uat-and-benchmark-closure-2026-09-14.md`。

真实环境已验证本地 LLM、ComfyUI/H3、H264 + AAC 媒体、机器 QC、人工批准、正式采用、FFmpeg 合成、交付 manifest/核验/下载；代表性视频只抽查三帧，并主动否决了错误 UI 首帧输入。隔离浏览器已验证两集有限选择、集中阻塞、跨集切换、“继续未完成”与“仅重新合成”影响预览，以及 API 真实重启后的状态恢复。生产数据库和现有生产服务未被改写。

第一轮最终全量门禁：API 242 个测试文件、1,423 项测试中 1,422 项通过，1 项为 Windows 主机按条件跳过的 POSIX 权限合同，零失败；Web 138 个测试文件、578 项全部通过；TypeScript/Vite 生产构建、bundle budget、Ruff、架构债务阻断与 `git diff --check` 全部通过。完整记录见 `docs/evidence/final-full-regression-and-runtime-closure-2026-09-13.md`。第二轮收尾时按用户“不要过度测试”的要求中止了约 60% 的 API 全量运行，Web 两次 594 项全量分别暴露一个测试 state 同步问题并以聚焦测试修复；因此第一轮全量结果不冒充第二轮最终全量通过，详见上述 2026-09-14 闭环记录。

E01—E09 已逐项按启动条件裁决：E01/E07/E09 由已有能力覆盖或部分覆盖，其余没有真实触发样本，按第 6 节停止线不扩大开发。详细证据见 `docs/evidence/conditional-enhancements-e01-e09-disposition-2026-09-13.md`、`docs/evidence/phase6-user-perspective-browser-and-restart-uat-2026-09-13.md`、`docs/evidence/phase6-h3-real-platform-uat-pass-2026-09-13.json` 与 `docs/evidence/phase6-delivery-real-v2-2026-09-13.json`。

## 2026-09-21 纯页面整集验收补记

原先中断的 Luna 唯一页面验收已从原项目恢复并完成。R05 现为 **CLOSED / PASS**：9/9 镜头采用、120 秒冻结时间线 v5、字幕 v1、854×480@24fps 整集渲染、最新成片人工批准、交付包 VERIFIED/APPROVED、MP4/SRT/manifest 与下载入口均经页面刷新复核。整集审核页的原生播放器崩页问题改为应用自有控件后，Luna 实测从 0:00 播放、跳转至 1:49 并继续到 1:57，页面保持稳定。

权威证据账本：`docs/evidence/luna-ui-full-episode-2026-09-14.md`。可靠性状态表：`docs/evidence/2026-09-14-reliability-closure.md`。该补记只关闭 R05，不把 R04 未定位的仓库级失败或顶栏“5 项异常”角标冒充为已清零；只读核对显示当前项目任务通过率 100%、失败率 0%，异常角标只在全局历史范围仍有旁路风险。
