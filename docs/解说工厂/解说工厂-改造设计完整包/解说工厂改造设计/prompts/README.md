# 解说内容链：7 套提示词、JSON Schema 与样例

状态：**设计态交付文件，尚未集成应用**。源码核验基线 `d91a4107b6733a4783d860689fb819720463214a`。

这些契约继续使用现有文本规划器、资料/实体/稿件/分镜服务和视觉检查 Provider；无需新 Agent 框架或新模型。沿现有单卡 GPU 租约调度。3090 Ti 如为用户本机配置，仅是设备约束，本轮未测量该设备。

## 1. 文件对应

| 前缀 | 模型任务 | 输出如何进入现有代码 |
|---|---|---|
| `01-content-extract` | 全文分块提取事实、实体、事件 | 程序消歧并分配 code/ID，转成现有 `FACT_EXTRACTION_SCHEMA`，调用 `ExplainerResearchService.apply_fact_extraction` |
| `02-preserved-script-annotations` | 已定稿正文的注释、引用、读音建议 | 程序原文切片与 ID 不变，组装段落后调用 `ExplainerNarrationService.create_script_revision/freeze_script` |
| `03-script-draft` | 根据资料编写口播 | 程序分配 canonical ID、解析 claim ID、派生朗读文本，进入同一稿件版本服务 |
| `04-storyboard` | 最终分镜与图/视频提示词 | 程序绑定 source/entity/state/style/media 版本，组装 `create_plan` 及统一生成命令 |
| `05-candidate-review` | 实际图像或视频抽帧的语义检查 | 扩展既有 `LocalLlmVisualQcProvider`，转成现有候选 QC/问题记录，不由模型直接采用 |
| `06-reference-design` | 人物/场景/道具独立设定图提示词 | 可选同一 LocalTextPlanner 整理，复用资产类型与多视角纯编译规则，不依赖最终分镜 |
| `07-fiction-seed` | 原创主题形成故事设定资料 | 同一 LocalTextPlanner 产出故事种子，程序登记 AUTHORED_FICTION_PACK 后进入共同提取/写稿流程 |

每个前缀包含 `.prompt.md`、`.schema.json` 和 `.example.json`。`validation-report.json` 记录文件契约检查结果。样例中的 `provided-...-id` 是占位符；生产调用必须替换为当前任务实际允许的 ID，不能将占位符入库。

三个 `script_policy` 与原有 `input_kind` 正交；前台保留“现成口播稿”和“故事或资料”两张主卡，“从主题开始”作为次入口。这里的七个 AI 任务不代表七个新的页面或七个 Agent。

## 2. Schema 如何加载和验证

JSON Schema 方言为 Draft 2020-12，包含 `$defs/$ref/anyOf`、固定版本值、长度上限和 `additionalProperties: false`。文件由严格 Pydantic 2 模型导出，同一模型用于样例验证；7 个正例通过，17 个负例被拒绝。负例包括未知字段、错误 schema_version、原稿注释夹带 display_text、非现有 render_type 值以及负帧号。

应用中可以用项目已有的 Pydantic 2 模型承担严格结构校验，或用支持 Draft 2020-12 的 JSON Schema 校验器。不能把这些带 `$ref` 的完整 schema 原样交给当前只支持有限字段的 `research.validate_model_payload` 后假定它已验证全部约束。建议在模型边界新增 7 个小 Pydantic 输出模型：`.model_validate(raw_response)` 成功后，转换成现有服务接受的 schema；保留现有服务第二次验证作为最终入库检查。

若所选本地模型的结构化输出接口不支持 schema 的某个关键字，可在**发送给模型**前做确定性的 schema 展开；服务端必须继续使用完整契约校验。不能因此放松未知字段、枚举、引用或覆盖规则。模型响应失败不写入正式事实/稿件对象。

七套契约中的下列现有枚举已经逐项比对固定提交的 `domain/explainers/contracts.py`：

- `StatementType`：`FACT / ORIGINAL_EXPLANATION / TRANSITION / FICTION / QUESTION`。
- `EntityType`：`REAL_PERSON / FICTIONAL_CHARACTER / GROUP / LOCATION / PROP / ORGANIZATION / CONCEPT`。
- `EvidenceStance`：`SUPPORTS / REFUTES / CONTEXT`。
- `RenderType`：`STILL_MOTION / PARALLAX / I2V / INFOGRAPHIC / LICENSED_MEDIA`。
- `VisualFactuality`：`DOCUMENTED / RECONSTRUCTION / SYMBOLIC / FICTIONAL`。

第 6 套另行核对 `application/story_assets.py::KINDS`，使用 `CHARACTER/SCENE/PROP/COSTUME`；reference_kind 只允许现有 `application/commands/asset_bible.py::REFERENCE_KINDS` 的本次子集 `HERO/FRONT/LEFT/RIGHT/BACK/SCENE_WIDE/SCENE_REVERSE/DETAIL`。还要按人物/场景/道具和实际工作流缩小每次允许集合。

当前流水线某些位置曾写入 `MOTION_STILL`，它不属于上述正式 RenderType 枚举，本契约不继续增加该值；显示层“静图推拉”统一映射到 `STILL_MOTION`。历史值兼容转换由服务完成，不能让新响应继续写错枚举。

`camera_movement/reference_roles_required/criterion/issue_kind` 是任务参数或检查标签，静态 schema 限类型与长度；服务每次还必须依据现有执行 Profile/工作流/检查标准提供**允许值清单**并验证。它们不是新增的数据库全局枚举。样例 `CHARACTER_FRONT/SCENE_REFERENCE` 为说明业务角色的示例清单，不是现有 `SlotKind`：人物正面最终必须适配现有 `FRONT` 槽，场景映射到所选工作流真实存在的输入。没有对应槽位就报能力缺失。

## 3. 静态 schema 无法完成的服务端校验（必须实现）

以下检查是入库/执行前的硬步骤。通过 schema 只表示 JSON 形状正确，不等于内容可信、ID 可访问、覆盖完整或任务获准。

### 3.1 所有任务通用

1. 按冻结任务的 project/video/snapshot 校验；模型返回的任何引用只能来自此次提供的 `allowed_ids`。仅在数据库中存在还不够，必须属于当前项目、当前版本和当前任务的允许范围。
2. 校验 schema_version 与提示词版本；版本号和输入 hash 一致才可复用响应。
3. 任何持久 ID、revision、run、seed、candidate_count、文件路径、权限、人工批准和采用行为都由程序产生。模型不能为自己分配 ID 或提交任务。
4. 模型调用期间无长写事务；合法响应在短事务入库。幂等键包含阶段、输入版本/hash、分块编号；网络恢复不重复写版本。
5. 资料文本仅是数据。来源里的提示注入不能改变 system 规则、输出 schema 或运行权限。

### 3.2 `01-content-extract`

- 每个 `source_span_id` 必须属于此次块允许片段集合，并且 source_id/packet/video 与任务一致。
- `entity_indexes/participant_entity_indexes/carried_prop_entity_indexes/place_entity_index` 必须在本响应 entities 数组范围；`claim_indexes` 同理。索引不能只检查≥0。
- `same_as_entity_id` 只可取输入已存在实体集合；核查同名/别名冲突后程序决定合并，不直接信模型。
- `FACT` 必须有 evidence；FICTION 只能在作品内容属性允许时使用。`ORIGINAL_EXPLANATION` 不能用作藏匿无源事实的绕行标记。
- `known_appearance/state` 的具体年龄、服装、日期须对应原文证据；不在资料中的视觉创作选择转入视觉设定，不进入事实账本。
- 缺失日期保持 null；不能把未知月日补成某月1日并宣称是原文事实。复用现有日期精度解析并记录推导。
- 程序为新实体/事实/事件分配全片唯一 code/UUID，将模型局部索引改为规范引用，再调用旧 schema 服务。保存 appearance/ambiguity 证据到既有 JSON 元数据，不把转换时无法映射的字段静默丢弃。
- 全文处理完成由 `owned_span_ids` 的处理覆盖决定；模型说“完成”不算。重叠上下文不重复计数；同源转载不提升证据独立数。
- 同一人物不同状态是同实体的新状态版本；主要身份冲突进复核。`model_confidence` 不构成自动确认依据。

### 3.3 `02-preserved-script-annotations`

- 输入中的每个 canonical_segment_id 必须恰好出现一次；不接受遗漏、重复、乱造 ID。
- 模型不回传 display_text/spoken_text，schema 已禁止这两个字段；正文由程序切片取得。
- 程序保存的所有切片和 separator 必须按原顺序拼回 `script_source_text`，逐字符相等且 hash 相等。不能只比较去标点/去空格后的文本。
- `unverified_phrases[].phrase` 必须是对应原稿段落的原样 substring；标疑点不自动修改正文。
- 读音建议逐项校验 display 是本段实际子串、替换不交叉冲突；确认词典后由程序派生 spoken_text，再用既有等价校验。无确认建议默认朗读原文。
- chapter break 必须引用输入段落；只改变章节注释，不重排原文。原稿模式无自动字数扩写，实测时长差异只提示。

### 3.4 `03-script-draft`

- chapter_index 必须在 outline 范围，不能只检查≥0。
- 非 `insufficient_content` 时必须有有效 segments；`insufficient_content=true` 不作为成功稿件自动冻结。
- FACT 段至少引用一条已允许 claim；支持一个段落多个 claim ID。实体引用必须来自当前全文规范实体表。
- 对人名、数字、否定、时间、结果等执行确定性对应检查；无法用规则判断的断言列入内容复核，不把规则检查当自动证明。
- 程序分配 canonical ID；改稿保持未改段落 ID，拆/并记录前身映射。不能每轮从数组下标重新编号整稿。
- 朗读文本由可检查的读音映射派生。不得使用可能改写正文的“朗读修复”来绕开原稿模式。

### 3.5 `04-storyboard`

- 程序重新计算所有要求画面的片段覆盖；响应中的 uncovered_segment_ids 必须与结果一致；有 blocking_requirements 时阶段不能直接报已完成。
- `segment_ids` 属于冻结稿版本；`entity_ids`、`state_revision_ids` 之间对应合法；visible_entity_ids 必须是该镜头引用实体子集。
- 核验本次真实 `usable_render_types`，不是只核验正式枚举。正式枚举含 I2V 不代表这台机器本次有可运行的 I2V 工作流。
- 必须运动的关键内容无可执行运动能力时保留 blocking_requirements；不能默默改 must_be_motion=false，也不能用静图推镜当人物动作。
- 将 image_prompt 写入兼容字段 prompt_intent，完整图像/视频提示词放 shot_grammar_json.prompt_bundle；现有 create_plan 使用的 code/entity_codes/claim_codes 由程序从验证过的 ID 反向映射。
- 将参考角色解析为已采用媒体版本、hash 和真实工作流槽位，编译验证实际消费，之后才能登记 consumed roles。仅有引用 ID/槽位声明不能通过。
- 风格版本、人物/服装状态、场景参考、模型执行 Profile/工作流版本必须冻结。画面创新不能写回事实来源。
- `on_screen_text` 的事实文字要有 claim 引用；AI 标识等固定文案来自程序策略，不由模型声明新的许可。不得让生成图像承担精确日期/数字排版。
- 第 2 步资产使用初规划可复用未定时码的分镜草案；本契约用于最终分镜时，必须绑定第 3 步 TTS 实测时长/对齐再分配帧区间。不要让最终 beat ID 成为第 2 步开始的循环依赖。
- 候选次数不由分镜模型输出；图片前台 1/2/4 张（API 1–4），视频前台/API 1/2 段、默认 1 段，由用户命令和预算控制。

### 3.6 `05-candidate-review`

- candidate_id 必须与本次候选一致；frame_id 必须来自实际发送的帧清单且无重复。缺帧/漏返回必须计为未检查，不能补成 PASS。
- 参考图片和候选帧必须真正作为多模态 images 送入，manifest 清楚标注顺序/角色。给文本模型发媒体 ID 不是视觉检查。
- 每帧预期 criterion 必须覆盖；缺失标准结果为 UNKNOWN，不因为 issues=[] 推断全部通过。
- 输入单张静图时 motion_observation 不得为 OBSERVED；多帧也仅对应抽查时点，不能宣称全帧动作已验证。
- 图片不可读、参考不足、人物被遮挡、模型不可用等保持 UNKNOWN/UNCHECKED。运行失败不调用一个空结果冒充审查。
- issue_kind 必须映射到当前允许问题种类；未知标签保留原始响应并转为明确的未识别问题，不默默丢弃。
- 高美学分数不能覆盖关键角色/动作失败；模型不产出最终 adopted/HUMAN_APPROVED。自动推荐、用户采用、人工锁定使用既有独立命令。
- QC 记录绑定媒体 hash、选定参考版本、prompt/style/profile 版本及检查帧清单；换引用后旧检查不能沿用为新版本通过。

### 3.7 `06-reference-design`

- 每个请求 `(entity_id, reference_kind)` 恰好返回一项。实体归属、资产种类和已采用状态版本必须与冻结任务一致。
- `known_attribute_keys/user_setting_keys` 只能取输入清单；属性内容必须确实出现在输入，不允许借相同 key 改写含义。
- `reference_media_version_ids` 只可引用任务提供且可在同项目使用的媒体版本；无参考时不能输出“保持输入人物/场景”并标称受参考约束。
- `creative_choices` 只有调用前明确允许时才可非空；事实未知外貌不能被自动补成真人肖像。视觉选择只进入设定版本，不能成为事实证据。
- 编译器固定拼接类型→已知属性→用户设定→参考不变项→风格→视角/构图要求，稳定去重并验证硬约束仍在；超长报字段错误，不简单截尾。
- 人物标准图一图一人；场景默认无人；道具一图一物；多视角按各自 requested_reference_kind 分开生成，不能把三视图拼图作为单人物参考输入。
- 用户示意要求若与默认“面部清晰”冲突，编译器选用示意规则而不是拼接矛盾要求；不能改变用户批准的构图含义。
- LEFT/RIGHT 等派生视图参考已采用 HERO；工作流编译器必须验证真实图像槽位消费，prompt_anchors 的文本描述不代替图片。
- `unresolved_constraints` 非空时定位待处理资产；保留其他已完成资产，不能冻结冲突设定继续冒充一致。
- 优先复用 `asset_image_generation.py::ASSET_IMAGE_SPECS/prompt_for/visual_description`、`asset_multiview.py::VIEW_SPECS/draft_prompts` 的纯规则；本次不将解说重新绑回旧任务提交链。

### 3.8 `07-fiction-seed`

- 仅 `script_policy=CREATE_FROM_TOPIC` 且 `content_kind=ORIGINAL_FICTION` 的任务可调用；事实主题禁止通过此分支填补资料。
- `character_indexes` 必须在 characters 数组范围；事件先后、cause/result 与结局一致；不得重复改名或改变固定用户设定。
- 题材、受众、时长与禁止元素按用户任务检查，不能只验证有标题和结局。`scope_conflicts` 非空时不冻结为可用种子。
- 程序分配所有持久来源/实体/事实 ID；以既有 `AUTHORED_FICTION_PACK` 来源种类和 `AUTHORED_FICTION` 可信度种类保存，verified_as_history 保持 false。
- 原创种子保存完整设定文本、输入/响应/模型/提示词 hash，然后进入共同来源切片与事实/实体提取。模型在此处生成的剧情是内部虚构设定，不是外部证据。
- 初次生成与授权修订分别计账；续跑复用同 hash 已采用种子，不因再次进入页面重写故事。改种子产生新版本，保留旧稿/旧图并按依赖失效。

## 4. 验证范围

本目录已验证 JSON 可解析、Schema 与 7 个样例结构一致、17 个负例被拒绝、5 组解说正式枚举与当前提交一致，以及资产种类/参考种类子集合法。验证使用生成 Schema 的同一组严格 Pydantic 2.13.5 模型，属于设计文件验证。

尚未执行本机模型生成、实际 TTS、GPU 调度、ComfyUI 工作流、外部引用解析、全文覆盖、真实人物一致性或端到端视频生产测试。将本目录复制进应用后必须实现上面的服务检查并运行主设计的验收清单，不能将 validation-report 当成应用功能已通过。
