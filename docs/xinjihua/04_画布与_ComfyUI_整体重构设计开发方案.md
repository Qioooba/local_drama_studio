# 画布与 ComfyUI 整体重构设计开发方案

> 状态：**批准进入实施设计**  
> 日期：2026-08-26  
> 范围：生产工作台、Visual Lab、Workflow App Contract、ComfyUI Designer/Production Runtime、任务事件、迁移与退役  
> 性质：替换式重构，不是现有 `ProductionCanvasPanel` 的增量优化

---

# 0. 本文效力与被取代结论

本文是画布与 ComfyUI 后续开发的单一实施依据。若既有文档与本文冲突，以本文为准。

明确取代以下旧结论：

1. 取代 `01_竞品研究与产品_UI_UX_总设计.md` 中“保留现有 `@xyflow/react` Canvas 并改名”的实现假设。
2. 取代 `02_架构与前后端重构规格.md` §67 中“`ProductionCanvasPanel` 保留专家模式”的迁移映射。
3. 取代 `03_实施迁移测试验收手册.md` §16.3 中“现有 `ProductionCanvasPanel` 保留专家入口”的安排。
4. 要求以新 ADR 取代 ADR-0005 中“每个正式任务启动并退出一个 Production Comfy 进程”的生命周期决策；保留 Designer/Production 隔离、按 `prompt_id/history` 收集输出、禁止猜测 output 目录等安全原则。
5. ADR-0014 只适用于旧生产 DAG。在新系统中，生产矩阵不再使用图焦点；Visual Lab 使用新的文档、节点和边契约。

本次不是：

- 把镜头加载上限从 60 改成 300；
- 给默认 React Flow 节点增加缩略图；
- 给旧五阶段节点补一个 `stage_job_id`；
- 在旧 `canvas_layouts.layout_json` 中继续塞更多业务字段；
- 把 ComfyUI 原始 workflow JSON 直接放进创作节点；
- 同时维护两套长期生产画布。

这些做法全部判定为补丁路线，禁止进入实施。

---

# 1. 执行摘要

重构后系统只有三种清晰、独立的工作空间：

```text
┌──────────────────────────────────────────────────────────────┐
│ 1. Production Cockpit / 结构化生产工作台                    │
│ 镜头 × 阶段 × 权威状态；回答“这一集做到哪、下一步做什么”      │
└───────────────────────────┬──────────────────────────────────┘
                            │ 引用 / 采纳 / 审核 / 提升
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ 共享事实层                                                   │
│ ShotRevision / StoryAsset / GenerationIntent / Variant       │
│ MediaAsset / MediaVersion / Job / Attempt / Review / Timeline│
└───────────────────────────▲──────────────────────────────────┘
                            │ 派生 / 实验 / 候选
┌───────────────────────────┴──────────────────────────────────┐
│ 2. Visual Lab / 高级创意画布                                 │
│ 语义素材节点、生成意图、分支、比较、序列预览；回答“怎么试”     │
└───────────────────────────┬──────────────────────────────────┘
                            │ Capability + Semantic Slots
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. Workflow & Runtime / ComfyUI 执行层                       │
│ App Contract → WorkflowVersion → RuntimeEnvironmentVersion   │
│ → Provider Execution；回答“本机具体怎样执行”                  │
└──────────────────────────────────────────────────────────────┘
```

核心决定：

- 生产工作台使用虚拟化矩阵，不使用自由 DAG。
- Visual Lab 新建独立领域模型，不复用旧生产 DAG 节点和布局。
- ComfyUI 节点图只存在于 Designer/诊断环境；普通创作者只看到语义输入输出。
- 生产 Comfy 默认采用受监管的常驻 Runtime，GPU 单任务独占；隔离临时 Runtime 只用于新环境验证、故障诊断和不可信工作流。
- 工作流必须绑定不可变的 RuntimeEnvironmentVersion，环境包含 Comfy commit、Python 锁、可信自定义节点、模型哈希和启动策略。
- Worker 使用 Comfy WebSocket 作为主要执行事件通道，history/queue 轮询仅作恢复和兜底。
- Job/Attempt、MediaVersion、GenerationVariant、Review、Timeline 继续作为权威事实；画布不新建第二套媒体、任务、审核或生产状态。

---

# 2. 产品目标与非目标

## 2.1 产品目标

### G1：结构化生产可扫视

制片或导演在一个视口内能回答：

- 这一集有多少镜头？
- 哪些镜头尚未完成导演意图？
- 哪些缺关键帧、代理赢家或正式批准？
- 当前 GPU 在生成哪个镜头，进度和预计剩余时间是多少？
- 哪些镜头因为一致性、审核、磁盘、模型或运行时阻塞？
- 从哪里恢复，而不会重复生成已完成且未 stale 的结果？

### G2：复杂创意可以非线性探索

专家可以在 Visual Lab 中：

- 引用角色、场景、道具、镜头、图片、视频、音频和文本；
- 建立多参考生成和首尾帧生成；
- 对提示词、模型、seed、分辨率、时长、LoRA、运动方案做分支实验；
- 并排比较候选；
- 将多个候选组成临时序列；
- 把满意结果显式采纳到正式生产域；
- 把稳定方法发布为 Skill/Recipe/Workflow App，而不是复制节点 ID。

### G3：ComfyUI 真正适合本地视频生产

- 长耗时任务浏览器关闭后仍继续；
- Runtime 重启后可恢复和对账；
- 不反复加载大模型；
- 错误能映射到“首帧缺失”“模型不存在”“分辨率不支持”等语义字段；
- 用户能看到当前阶段、采样进度、预览、缓存命中和产物注册；
- 取消不会误伤其他任务；
- 自定义节点可用但必须进入可信、固定、可复现的环境版本；
- 没有外网依赖和隐式下载。

## 2.2 非目标

- 不在 Studio 中复制完整 ComfyUI 节点编辑器。
- 不让普通创作者安装 Python 包或自定义节点。
- 不支持生产 Runtime 同时运行多个 GPU 重任务。
- 不把 Visual Lab 当作新手首屏或全剧生产唯一入口。
- 不让画布连线直接修改正式 Shot、Timeline 或 Review 事实。
- 不通过前端轮询串行调度整集任务。
- 不为兼容旧画布保留永久双写。

---

# 3. 必须删除的旧设计

## 3.1 删除“每镜头固定五个节点”的生产图

旧模型：

```text
Shot 001: DIRECT → KEYFRAME → PROXY → FORMAL → TIMELINE
Shot 002: DIRECT → KEYFRAME → PROXY → FORMAL → TIMELINE
...
```

问题：

- 它是规则矩阵，不是需要自由布局的图。
- 五个节点重复附带相同 shot jobs、variants、experiments 和 logs。
- 任一 shot job 的运行或失败会污染所有阶段节点状态。
- `TIMELINE` 被伪装成镜头节点，但时间线事实属于 episode revision。
- 搜索、聚焦、minimap 和手动布局增加操作成本，却没有增加决策信息。

处置：`ProductionCanvasService` 不演进为新服务；新生产矩阵稳定后删除。

## 3.2 删除旧布局作为未来画布数据模型的假设

旧 `canvas_layouts` 只有整份 JSON，存在分页覆盖和并发冲突问题。新 Visual Lab 使用节点级位置、节点级 revision、文档 revision 和明确的 typed edges。旧布局不迁移为新 Visual Lab。

## 3.3 删除“生产画布运行计划”概念

旧 `canvas_execution_plans` 使用节点数组代表执行范围，但节点不是实际可执行单元，也没有可靠计算依赖闭包。

新系统：

- 整集/范围生产由既有 EpisodeProductionRun facade 计划和 dispatch。
- 单镜头/单能力生成由 GenerationIntent/Variant preflight。
- Visual Lab 节点执行由该节点对应的 GenerationIntent preflight。
- Comfy 子图执行只在 Workflow Designer 测试中使用，不等同于业务生产范围。

## 3.4 删除创作 UI 对 Comfy 节点 ID 的依赖

节点 ID 只允许存在于：

- 不可变 WorkflowVersion 内容；
- Workflow App Contract 的已编译 binding；
- Designer 验证报告；
- ProviderExecutionEvent 的技术证据。

创作页面、Visual Lab 节点和项目偏好只保存稳定 semantic slot key。

---

# 4. 信息架构与路由

## 4.1 项目导航

```text
项目
├─ 总览
├─ 剧本
├─ 资产圣经
├─ 分集
│  └─ Episode Production Cockpit
├─ 审核
├─ 音频
├─ 时间线
├─ 交付
└─ 工具
   ├─ Visual Lab
   ├─ Workflows
   ├─ Runtime
   └─ Diagnostics
```

Visual Lab 永不放在“分集生产”的默认下一步中。普通用户只有在下列入口进入：

- 镜头菜单“在 Visual Lab 中探索”；
- 资产详情“创建多视图实验”；
- 工具 → Visual Lab；
- 候选比较“建立对照实验”。

## 4.2 新路由

| 路由 | 用途 | URL 必须持久化 |
|---|---|---|
| `/projects/:projectId/episodes/:episodeId/production` | 结构化生产矩阵 | `shot`、`stage`、`filter`、`sort`、`run` |
| `/projects/:projectId/labs` | Visual Lab 文档列表 | `query`、`scope`、`status` |
| `/projects/:projectId/labs/:labId` | 全屏高级画布 | `node`、`panel`、`zoom` 可选 |
| `/workflows` | 工作流库 | `capability`、`status`、`environment` |
| `/workflows/:workflowId/versions/:versionId` | Contract、验证、发布证据 | `tab` |
| `/runtime` | Runtime 实例和环境版本 | `instance`、`tab` |
| `/jobs/:jobId` | 权威任务详情 | `attempt`、`event` |

## 4.3 旧路由处置

`/projects/:projectId/canvas` 在切换版本中只做一次显式导航到：

- 有 `episode` 参数：对应 episode production；
- 没有 episode：Visual Lab 文档列表。

导航兼容只保留一个发布周期，不渲染旧组件、不双写旧布局。随后删除旧路由。

---

# 5. Production Cockpit 详细设计

## 5.1 页面职责

Production Cockpit 是“生产状态与行动入口”，不是节点编辑器。

权威来源：

- Shot 与当前 ShotRevision；
- StoryAsset/ShotAssetBinding；
- GenerationIntent/Variant；
- MediaAsset/MediaVersion/Selection/Review；
- Job/Attempt；
- FrameBridge/TransitionConstraint；
- TimelineRevision；
- EpisodeProductionRun 聚合。

页面自身不存业务状态，仅存用户视图偏好。

## 5.2 桌面布局

1440px 及以上：

```text
┌─────────────────────────────────────────────────────────────────────┐
│ 48–56 Context Bar：集、总体进度、运行控制、过滤、搜索               │
├──────────────┬──────────────────────────────────────┬───────────────┤
│ Shot 目录    │ 虚拟化生产矩阵                       │ Inspector     │
│ 240–272      │ 剩余自适应                           │ 344–384       │
│              │                                      │               │
├──────────────┴──────────────────────────────────────┴───────────────┤
│ 可折叠 Active Jobs Strip：仅有任务/错误时出现，44–120                │
└─────────────────────────────────────────────────────────────────────┘
```

1280px：Shot 目录压缩为 216，Inspector 336。  
1024px：全局侧栏折叠；Inspector 改为右侧 Drawer；矩阵内部可以横向滚动，页面本身不横向滚动。  
移动端只提供只读监控，不提供完整生产编辑。

## 5.3 矩阵列

固定列：

1. 镜头：编号、时长、景别、当前缩略图、stale 标识。
2. 导演意图：ShotRevision 完整性与 Production Ready。
3. 关键帧：已批准关键帧、候选、当前生成。
4. 代理视频：winner、候选、当前生成。
5. 正式视频：正式版本、人工批准、QC。
6. 衔接：首尾帧、相邻约束、一致性。
7. 时间线：当前 TimelineRevision 是否引用该镜头的最新正式版本。

阶段列是 read model，不创建 `production_stage` 真值表。

## 5.4 单元格状态推导

状态优先级：

```text
FAILED > BLOCKED > RUNNING > NEEDS_REVIEW > STALE > READY > EMPTY
```

但 `FAILED` 只来自与该镜头、该 capability/purpose 相关的最新有效 Attempt，不得使用整个镜头全部历史 Job。

| 阶段 | READY 条件 | RUNNING 条件 | BLOCKED 示例 |
|---|---|---|---|
| DIRECT | 当前 ShotRevision 满足导演意图 schema 且 Production Ready | 不适用 | 缺主体动作、环境、机位、时长或资产引用 |
| KEYFRAME | 存在当前输入指纹下已批准的 IMAGE MediaVersion | 对应关键帧 Variant 的活动 Job | Profile 未发布、角色资产未批准、输入 stale |
| PROXY | 存在当前输入指纹下已选 winner | 对应 proxy capability 的活动 Job | 缺关键帧批准、Frame Bridge 无效 |
| FORMAL | 存在人工批准且 QC 合格的正式 VIDEO MediaVersion | 对应 formal capability 的活动 Job | 缺 proxy winner、授权/QC/磁盘/Runtime blocker |
| CONTINUITY | 相邻约束与 frame bridge 全部兼容 | 正在执行衔接检查 | 上一镜尾帧 stale、主体状态不一致 |
| TIMELINE | 当前 TimelineRevision 引用最新批准版本 | 合成/刷新任务运行 | 正式视频未批准或 timeline stale |

每个状态必须返回：

- `code`：稳定机器码；
- `label`：用户文本；
- `tone`：视觉语义；
- `reason`：一句解释；
- `next_action`：零或一个首要动作；
- `subject_refs`：权威事实深链；
- `active_job_summary`：只含对应任务；
- `freshness_fingerprint`。

## 5.5 交互

- 单击单元格：选择 shot + stage，打开 Inspector。
- `J/K`：上/下镜头；`H/L`：左/右阶段；`Enter`：打开详情。
- `G`：对当前可生成阶段执行 preflight，不直接提交。
- `Shift+J/K` 或复选框：范围选择。
- 批量生成只对同 capability、同输入完整性、同资源策略的项成组；其他项在预检结果中明确分组或阻塞。
- 状态不可只靠颜色；每格至少有 icon/shape + 文本或可访问名称。
- 失败单元格显示“重试运行”与“创建创意变体”两个不同动作，禁止混淆 retry 与 reroll。

## 5.6 Inspector

Tabs：

1. `Overview`：当前阶段状态、阻塞、下一步。
2. `Inputs`：准确媒体版本、资产状态、提示词版本、配置继承来源。
3. `Candidates`：缩略图、版本、选择、批准、stale。
4. `Job`：当前/最新 Job 与 Attempt 摘要。
5. `Lineage`：输入指纹、父 Variant、派生版本。
6. `Continuity`：仅涉及衔接阶段时显示。

Inspector 每次只有一个 primary CTA。禁用按钮旁必须有阻塞原因和解决入口。

## 5.7 数据量与性能

- shot 行必须虚拟化；500 镜头 fixture 不允许创建 500×7 个完整媒体组件。
- 固定行高默认 72px；展开详情不改变矩阵行高，详情进入 Inspector。
- 缩略图固定尺寸，WebP、小图、lazy；视频 `preload="none"`，点击后才载入 proxy。
- API 按 shot 游标分页，每页建议 50–100 行；页面追加时不覆盖已保存选择。
- 活动 Job 通过项目事件增量更新对应 cell，不因一个 heartbeat 重取整集。

---

# 6. Visual Lab 详细设计

## 6.1 页面职责

Visual Lab 是非线性创意实验空间，处理“素材如何组合、生成如何分支、结果如何比较”。它不显示固定生产阶段，也不直接修改正式依赖。

## 6.2 布局

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Context Bar：Lab 名称、scope、保存状态、快照、分享/导出、运行        │
├───────────────┬──────────────────────────────────────┬───────────────┤
│ Asset Library │ Infinite Canvas                      │ Inspector     │
│ 232–264       │ warm-neutral background              │ 344–384       │
│ 搜索/资产/镜头│ floating add/search/fit controls      │ node-specific │
├───────────────┴──────────────────────────────────────┴───────────────┤
│ Live Run Strip：排队、当前阶段、进度、预览、取消；无任务时隐藏        │
└──────────────────────────────────────────────────────────────────────┘
```

画布节点可使用深色媒体预览表面，但整体遵循现有 Hybrid Professional Workstation，不引入霓虹、发光、玻璃拟态和装饰性动画。

## 6.3 节点类型

### 引用节点

| 节点 | 引用对象 | 可编辑内容 |
|---|---|---|
| `TEXT_REF` | CreativeEntryRevision / PromptRevision / 用户草稿 | 草稿文本可创建新 revision，不覆盖已冻结版本 |
| `STORY_ASSET_REF` | Character/Scene/Prop + AssetState | 选择具体状态、造型、视角；不能在节点内绕过正式资产变更流程 |
| `MEDIA_REF` | 精确 MediaVersion | 裁剪/标记为新的 TransformIntent，不修改源文件 |
| `SHOT_REF` | Shot + ShotRevision | 选择引用字段；正式 Shot 修改跳转导演台 |

### 操作节点

| 节点 | 作用 | 输出 |
|---|---|---|
| `GENERATION_INTENT` | 文/图/参考/首尾帧生成 | GenerationVariant candidates |
| `TRANSFORM_INTENT` | 扩图、抠图、重绘、超分、插帧、裁剪、音频处理 | 派生 MediaVersion |
| `COMPARE_SET` | 2–12 个候选的并排/盲评/标注 | 比较结论，不自动批准 |
| `SEQUENCE_PREVIEW` | 临时片段顺序、节奏和音频预览 | Draft sequence，不创建正式 TimelineRevision |
| `OUTPUT_DRAFT` | 汇总可导出/可采纳结果 | promotion action |
| `NOTE` | 标记、说明、区域标题 | 无执行输出 |

不创建 `COMFY_NODE`。需要复用 Comfy workflow 时使用 `GENERATION_INTENT` 或 `TRANSFORM_INTENT`，其 Inspector 选择发布的 Workflow App/Profile。

## 6.4 Typed ports 与边

端口类型：

```text
TEXT
STORY_ASSET
IMAGE
VIDEO
AUDIO
SHOT_CONTEXT
MASK
SEQUENCE
ANY_MEDIA
```

边语义：

| Edge kind | 含义 | 是否改变正式生产 |
|---|---|---|
| `REFERENCES` | 提供语义上下文 | 否 |
| `GUIDES` | 视觉/动作/风格/音频引导 | 否 |
| `DERIVES` | 输出由输入派生 | 否，只有产物血缘 |
| `COMPARES` | 加入候选比较集合 | 否 |
| `SEQUENCES` | 加入临时顺序 | 否 |

连线前必须验证端口兼容性。非法连接不可创建，不能先保存再靠运行报错。删除边只改变 Lab 文档，不删除已生成 Job、Variant 或 MediaVersion。

## 6.5 节点卡片

所有节点共享：

- 名称、类型 icon、revision/stale 标识；
- 一张 poster 或结构化摘要；
- 输入完整性；
- 最新运行状态；
- 候选数量；
- 一个主动作；
- typed ports；
- 右上更多菜单。

节点卡片不塞完整参数表。模型、参数、历史、血缘进入 Inspector。

建议尺寸：

- 引用节点 220×120；
- 媒体节点 260×200；
- 生成节点 280×220；
- Compare 360×240；
- Sequence 420×180。

缩放低于 0.55 时进入简化层级，只显示类型、标题、状态和端口；低于 0.3 只显示语义色块和状态形状，避免渲染视频和长文本。

## 6.6 分支与候选

一次生成不自动创建 N 个画布节点。默认行为：

- GenerationIntent 节点内显示 candidate tray；
- 用户选择“展开到画布”时，候选才成为 MEDIA_REF 节点；
- Reroll 创建新的 GenerationVariant，保留 parent_variant_id 和 branch_reason；
- Operational retry 不创建新 Variant；
- CompareSet 引用精确 MediaVersion，不引用“当前最新”。

## 6.7 运行语义

`Run node`：

1. 读取该节点当前 content revision。
2. 解析 incoming typed edges 的精确版本。
3. 解析 capability preference，禁止静默 fallback。
4. 生成 immutable effective configuration。
5. 执行 preflight。
6. 返回 plan hash、资源估算、blockers、warnings、exact workflow/environment。
7. 用户确认后创建/复用 GenerationIntent 和 GenerationVariant，再创建 durable Job。

`Run downstream` 只对 Visual Lab 中可执行 intent 节点做拓扑计划；遇到 Compare、人工选择、批准或 OutputDraft gate 必须停止，不自动跨过人工节点。

## 6.8 正式采纳

采纳是显式命令，不是拖线：

```text
Lab Candidate
  → 校验 MediaVersion 完整性与 freshness
  → 选择目标：StoryAsset reference / Shot candidate / Frame Bridge input
  → 显示影响预览
  → 创建 promotion plan hash
  → 人工确认
  → 调用现有 domain command
  → 写 promotion audit record
```

Visual Lab 不直接把结果设为 approved。批准仍走 Review 权威流程。

## 6.9 可访问性替代视图

无限画布必须同时提供 Outline：

- 按 group / node type / topology 浏览；
- 显示总节点、当前位置、上下游；
- 支持键盘选择、重命名、运行、删除、跳转；
- 连线有“从节点 A 的 IMAGE 输出连接到节点 B 的 FIRST_FRAME 输入”的表单式替代操作；
- 画布拖拽不是唯一操作方式。

---

# 7. Workflow App Contract

## 7.1 目的

把不稳定的 Comfy 节点图转换成稳定的产品能力接口。

```text
Creator semantic input
  → WorkflowAppContractVersion
  → compiled node bindings
  → Comfy API workflow JSON
```

## 7.2 Contract 结构

```json
{
  "schema_version": "localdrama.workflow-app-contract.v1",
  "capability": "VIDEO_I2V_FIRST_LAST",
  "inputs": [
    {
      "key": "first_frame",
      "type": "IMAGE",
      "role": "FIRST_FRAME",
      "required": true,
      "label": "首帧",
      "accepted_media_kinds": ["IMAGE"]
    },
    {
      "key": "last_frame",
      "type": "IMAGE",
      "role": "LAST_FRAME",
      "required": false,
      "label": "尾帧"
    }
  ],
  "parameters": [
    {
      "key": "duration_seconds",
      "type": "NUMBER",
      "default": 5,
      "minimum": 1,
      "maximum": 15,
      "step": 1,
      "group": "video",
      "advanced": false
    }
  ],
  "outputs": [
    {
      "key": "video",
      "type": "VIDEO",
      "required": true,
      "selection": "PRIMARY"
    }
  ],
  "semantic_phases": [
    {"key": "encode", "label": "编码输入"},
    {"key": "sample", "label": "生成视频"},
    {"key": "decode", "label": "解码音视频"},
    {"key": "save", "label": "保存结果"}
  ]
}
```

每个公开字段还应支持：

- label、description、group、order；
- required、default、enum、min/max/step、pattern；
- visible_when 与 enabled_when；
- advanced；
- media role；
- sensitivity/redaction；
- estimated resource dimension key；
- UI hint，但 UI hint 不可改变领域类型。

## 7.3 Binding

编译 binding 是 Contract version 的一部分：

```json
{
  "first_frame": {
    "node_id": "12",
    "input": "image",
    "transform": "COMFY_INPUT_REL_PATH"
  },
  "duration_seconds": {
    "node_id": "44",
    "input": "length",
    "transform": "SECONDS_TO_FRAMES",
    "arguments": {"fps_slot": "fps"}
  }
}
```

Transform 必须来自受控 registry，禁止在数据库中保存任意 Python/JS 表达式。

## 7.4 发布门禁

WorkflowVersion 发布必须同时通过：

1. JSON 结构与内容哈希；
2. Contract schema；
3. required slot binding 完整；
4. 当前 RuntimeEnvironmentVersion 的 `/object_info` 节点存在性；
5. 模型 manifest 与哈希存在；
6. custom node allowlist；
7. 输出节点与 output contract；
8. loopback/no-network policy；
9. 最小 smoke workflow；
10. 生成 ValidationAttestation；
11. 绑定固定 RuntimeEnvironmentVersion；
12. 人工发布。

Workflow 内容或环境任一变化都创建新版本，禁止原地修改已发布版本。

---

# 8. ComfyUI 本地 Runtime 架构

## 8.1 进程拓扑

```text
FastAPI
  ├─ RuntimeControlPort
  ├─ WorkflowCompilerPort
  └─ Project Event Stream

Worker Supervisor
  ├─ GPU lease / durable Job claim
  ├─ Runtime Manager
  │   ├─ Production Runtime H3-A : port / user / input / output / temp
  │   └─ Validation Runtime      : isolated, disposable
  └─ Comfy Execution Bridge
      ├─ POST /prompt
      ├─ WS /ws                 primary events
      ├─ GET /history/{id}      recovery/final proof
      ├─ GET /queue             reconciliation only
      └─ POST /interrupt        guarded by runtime ownership
```

浏览器不直接连接 Comfy。浏览器只连接 LocalDramaStudio API 的项目事件通道。

## 8.2 Runtime 类型

### Production Runtime

- 常驻、受监管；
- 固定 RuntimeEnvironmentVersion；
- GPU 重任务并发 1；
- 不接受未发布 WorkflowVersion；
- 不安装节点、不下载模型；
- 输出只写 Attempt sandbox；
- 支持 drain、计划重启和崩溃拉起；
- 可保留模型驻留，但必须在任务间验证健康和剩余 VRAM。

### Validation Runtime

- 临时隔离；
- 用于 Designer capture 的验证、环境构建、custom node smoke；
- 不能写正式 MediaVersion；
- 验证通过后只产出 attestations 和环境版本；
- 进程退出后回收。

### Designer Runtime

- 专家编辑环境；
- 与项目正式目录隔离；
- 可导入/编辑 workflow；
- capture 只进入 staging；
- 不等于 Production 发布。

## 8.3 Runtime 状态机

```text
COLD → STARTING → READY → BUSY → READY
  │        │         │       │
  │        └──────→ DEGRADED ←┘
  │                    │
  └────────────────── DRAINING → STOPPED
```

状态定义：

- `COLD`：配置存在、进程未启动；
- `STARTING`：进程启动、等待 object_info/health；
- `READY`：环境指纹匹配、可接受任务；
- `BUSY`：持有一个 Attempt 的执行所有权；
- `DRAINING`：不接新任务，等待当前任务结束；
- `DEGRADED`：进程可达但环境/VRAM/节点/事件异常；
- `STOPPED`：人工或策略停止。

RuntimeInstance 必须记录 `owned_attempt_id`。只有该 Attempt 的取消命令可以调用全局 `/interrupt`。若 ownership 不匹配，返回冲突并进入 reconciliation，禁止盲目中断。

## 8.4 RuntimeEnvironmentVersion

必须冻结：

- ComfyUI repository URL、commit、dirty=false；
- Comfy frontend version；
- Python executable fingerprint；
- lockfile hash；
- torch/cuda versions；
- custom node packages：source、commit、tree hash、license、trusted status；
- model artifacts：logical model id、path ref、sha256、size、license status；
- launch flags；
- input/output/temp/user roots policy；
- network policy；
- supported capabilities；
- environment fingerprint。

Production 启动时计算 observed fingerprint；不匹配则 `DEGRADED`，不得继续接任务。

## 8.5 自定义节点策略

禁止两种极端：

- 禁止任意安装并直接投入生产；
- 禁止永久 `--disable-all-custom-nodes`，导致正常视频工作流无法运行。

采用 promote 流程：

```text
Designer install/capture
  → source/license/hash scan
  → Validation Runtime build
  → object_info + smoke
  → RuntimeEnvironmentVersion candidate
  → human promote
  → Production Runtime drain/restart into new environment
```

环境升级不会修改旧 WorkflowVersion。旧任务的 effective snapshot 保留原环境版本。

## 8.6 执行事件

Worker 提交前先建立 WS client identity，再 POST `/prompt`。持久化经过清洗的事件：

```text
PROMPT_ACCEPTED
EXECUTION_STARTED
NODE_STARTED
NODE_PROGRESS
PREVIEW_AVAILABLE
NODE_CACHED
NODE_COMPLETED
EXECUTION_SUCCEEDED
EXECUTION_FAILED
EXECUTION_INTERRUPTED
RUNTIME_DISCONNECTED
RECOVERY_CONFIRMED
```

技术 node id 通过 Contract semantic phase map 转换为用户阶段。未知节点只在 Job 技术详情显示，不泄漏到生产矩阵主状态。

WebSocket 断开不立即判失败：

1. Attempt 标记 `PROVIDER_UNCONFIRMED`；
2. 尝试重连；
3. 查询 history；
4. 查询 queue；
5. 根据 prompt_id 对账；
6. 只有达到有界恢复条件后才完成为 FAILED/ORPHANED；
7. 若后续 history 证明成功，使用既有 uncertain-success recovery 注册产物。

## 8.7 输出收集

- 只按 prompt_id history 与 output contract 选择输出；
- 所有路径必须位于 Runtime output root；
- 拒绝符号链接和路径逃逸；
- 复制到 Attempt sandbox 时使用 `.partial` + atomic replace；
- 计算 sha256、size、probe；
- 注册 Artifact；
- 根据 generation purpose 创建 provisional MediaVersion；
- 自动生成 thumbnail/proxy 的后台任务；
- 不自动 selected、approved 或进入 Timeline。

---

# 9. 领域模型与数据库设计

## 9.1 原则

- 新表使用新语义名称，不扩展旧 canvas 表。
- 业务内容 revision 与视觉位置 revision 分离。
- Job/Media/Review 不复制。
- 所有写命令带 `expected_revision` 或 idempotency key。
- 所有 plan/commit 双阶段命令带 plan hash。
- immutable snapshot 不原地更新。

## 9.2 Visual Lab 表

### `visual_lab_documents`

| 字段 | 说明 |
|---|---|
| `id` | UUID |
| `project_id` | 必填 |
| `episode_id` | 可空，限定上下文但不形成正式依赖 |
| `code` / `title` | 用户可读 |
| `status` | ACTIVE / ARCHIVED |
| `topology_revision` | 节点/边/组结构乐观锁 |
| `created_at/updated_at/created_by/revision/schema_version` | 标准审计字段 |

### `visual_lab_nodes`

| 字段 | 说明 |
|---|---|
| `id` / `document_id` | 稳定节点身份 |
| `node_kind` | 枚举 |
| `current_content_revision_id` | 当前不可变内容版本 |
| `position_x/y`, `width/height`, `z_index` | 画布布局 |
| `collapsed` | 视觉状态 |
| `revision` | 节点位置和当前 revision 指针乐观锁 |

### `visual_lab_node_revisions`

| 字段 | 说明 |
|---|---|
| `id` / `node_id` / `revision_no` | 不可变版本 |
| `content_json` | typed content，按 node kind 校验 |
| `content_hash` | canonical hash |
| `parent_revision_id` | 版本链 |
| `change_note` | 修改原因 |

引用节点 content 必须保存 exact referenced revision/version id，不保存不稳定的文件路径。

### `visual_lab_edges`

字段：`id, document_id, source_node_id, source_port, target_node_id, target_port, edge_kind, metadata_json, created_at, created_by, revision, schema_version`。

唯一约束按允许的多重边规则设置；端口兼容由 domain service 验证。

### `visual_lab_groups`

只用于视觉整理和运行范围标签，不具有生产依赖语义。

### `visual_lab_snapshots`

完整 topology + exact node content revision 的不可变快照，用于：

- 发布 Skill/Recipe；
- 导出；
- 可重复运行；
- 恢复历史。

### `visual_lab_promotions`

记录 Lab 结果采纳动作的审计桥：source document/node/media version、target type/id、plan hash、调用的权威 command、结果 subject id。它不保存第二份 approval 状态。

## 9.3 Workflow/Runtime 表

### `workflow_app_contract_versions`

绑定 `workflow_version_id`，保存 contract JSON、binding JSON、semantic phase map、hash、status。

### `runtime_environments`

稳定环境身份，例如 `H3_PRODUCTION_VIDEO`。

### `runtime_environment_versions`

不可变 manifest、fingerprint、验证状态、published_at。

### `workflow_runtime_bindings`

`workflow_version_id → runtime_environment_version_id`，发布后不可修改。

### `runtime_instances`

当前进程观测：instance id、environment version、port、pid、state、owned_attempt_id、observed fingerprint、health、last heartbeat。PID 是观测值，不作为业务身份。

### `provider_execution_events`

字段：event id、job attempt id、provider prompt id、sequence、event type、semantic phase、progress、payload_redacted_json、occurred_at。对 `(attempt_id, sequence)` 或 provider event identity 幂等。

### `provider_previews`

临时预览 artifact 引用、attempt、node/phase、created_at、expires_at。预览不是 MediaVersion，过期可清理。

## 9.4 不新增的表

- 不新增 `production_stage_status`：状态必须可投影重建。
- 不新增 `canvas_jobs`：使用 jobs。
- 不新增 `canvas_media`：使用 MediaVersion。
- 不新增 `canvas_reviews`：使用 Review/Selection。
- 不新增第二套 `episode_queue`：使用 EpisodeProductionRun facade + jobs。

---

# 10. API 设计

## 10.1 Production Cockpit 查询

```http
GET /api/v1/episodes/{episode_id}/production-grid
    ?cursor=0&limit=75&filter=blocked,running&sort=order
```

响应：

```json
{
  "episode": {},
  "summary": {
    "shot_count": 120,
    "ready_count": 43,
    "blocked_count": 12,
    "running_count": 1,
    "needs_review_count": 8
  },
  "columns": ["DIRECT", "KEYFRAME", "PROXY", "FORMAL", "CONTINUITY", "TIMELINE"],
  "rows": [
    {
      "shot": {},
      "cells": {
        "PROXY": {
          "state": "RUNNING",
          "label": "生成 37%",
          "reason": "正在生成代理视频",
          "next_action": null,
          "active_job_summary": {},
          "subject_refs": [],
          "freshness_fingerprint": "..."
        }
      }
    }
  ],
  "page": {"next_cursor": 75},
  "observed_at": "...",
  "read_only": true
}
```

单元格详情：

```http
GET /api/v1/shots/{shot_id}/production-stages/{stage}
```

## 10.2 Visual Lab

```http
POST   /api/v1/projects/{project_id}/visual-labs
GET    /api/v1/projects/{project_id}/visual-labs
GET    /api/v1/visual-labs/{lab_id}
PATCH  /api/v1/visual-labs/{lab_id}
POST   /api/v1/visual-labs/{lab_id}/nodes
POST   /api/v1/visual-labs/{lab_id}/nodes:batch-move
POST   /api/v1/visual-labs/{lab_id}/edges
DELETE /api/v1/visual-lab-edges/{edge_id}
POST   /api/v1/visual-lab-nodes/{node_id}/revisions
POST   /api/v1/visual-labs/{lab_id}/snapshots
```

节点执行：

```http
POST /api/v1/visual-lab-nodes/{node_id}/runs:preflight
POST /api/v1/visual-lab-nodes/{node_id}/runs
POST /api/v1/visual-labs/{lab_id}/runs:preflight
POST /api/v1/visual-labs/{lab_id}/runs
```

采纳：

```http
POST /api/v1/visual-lab-nodes/{node_id}/promotions:preflight
POST /api/v1/visual-lab-nodes/{node_id}/promotions
```

所有 commit 接口必须验证：plan hash、expected document/node revision、精确 MediaVersion freshness。

## 10.3 Workflow 与环境

```http
POST /api/v1/workflow-versions/{id}/app-contracts
POST /api/v1/workflow-app-contract-versions/{id}:validate
POST /api/v1/workflow-versions/{id}:publish

POST /api/v1/runtime-environments
POST /api/v1/runtime-environments/{id}/versions
POST /api/v1/runtime-environment-versions/{id}:validate
POST /api/v1/runtime-environment-versions/{id}:publish

GET  /api/v1/runtime/instances
POST /api/v1/runtime/instances/{id}:start
POST /api/v1/runtime/instances/{id}:drain
POST /api/v1/runtime/instances/{id}:restart
POST /api/v1/runtime/instances/{id}:stop
```

危险 Runtime 操作必须在确认 dialog 中显示当前 owned Attempt 和影响；BUSY 时普通 restart 自动转为 drain，不直接 kill。

## 10.4 任务事件

Worker 不向浏览器暴露 Comfy WS。Worker 将 provider event 转为本地 outbox/project event：

```text
JOB_PROGRESS_CHANGED
JOB_PREVIEW_AVAILABLE
JOB_PROVIDER_VALIDATION_FAILED
JOB_RUNTIME_DISCONNECTED
JOB_RECOVERY_CONFIRMED
RUNTIME_STATE_CHANGED
```

前端根据 subject id 精确失效 production cell、Visual Lab node 或 Job detail query。

---

# 11. 后端模块划分

遵循 modular monolith 与 ports/adapters，不引入微服务。

```text
application/
├─ queries/
│  ├─ production_grid.py
│  ├─ production_stage_detail.py
│  ├─ visual_lab_document.py
│  ├─ workflow_app_contract.py
│  └─ runtime_observation.py
├─ commands/
│  ├─ visual_lab/
│  │  ├─ create_document.py
│  │  ├─ add_node.py
│  │  ├─ revise_node.py
│  │  ├─ connect_nodes.py
│  │  ├─ move_nodes.py
│  │  ├─ plan_run.py
│  │  ├─ commit_run.py
│  │  ├─ plan_promotion.py
│  │  └─ commit_promotion.py
│  ├─ workflows/
│  │  ├─ create_app_contract.py
│  │  ├─ validate_workflow_release.py
│  │  └─ publish_workflow_release.py
│  └─ runtime/
│     ├─ build_environment.py
│     ├─ publish_environment.py
│     ├─ start_runtime.py
│     ├─ drain_runtime.py
│     └─ reconcile_runtime.py
├─ ports/
│  ├─ visual_lab_repository.py
│  ├─ workflow_compiler.py
│  ├─ runtime_control.py
│  ├─ provider_execution.py
│  └─ provider_event_sink.py
└─ services/
   ├─ production_stage_projector.py
   ├─ visual_lab_graph_validator.py
   └─ generation_plan_resolver.py

infrastructure/
├─ database/
│  ├─ production_grid_repository.py
│  ├─ visual_lab_repository.py
│  └─ runtime_environment_repository.py
└─ comfy/
   ├─ workflow_compiler.py
   ├─ runtime_manager.py
   ├─ execution_bridge.py
   ├─ event_mapper.py
   └─ output_collector.py
```

拆分要求：

- 新 application 代码不得 import concrete SQLite/Comfy。
- `comfy_jobs.py` 被分解后删除，不继续长成总控服务。
- `workflows.py` 的不可变版本和 attestation 行为迁入明确 commands/repositories。
- provider raw payload 只存在于 adapter 和 redacted evidence，不穿透到页面 DTO。

---

# 12. 前端模块划分

```text
features/
├─ production-grid/
│  ├─ ProductionCockpitPage.tsx
│  ├─ ProductionGrid.tsx
│  ├─ ProductionCell.tsx
│  ├─ ProductionInspector.tsx
│  ├─ ActiveJobsStrip.tsx
│  ├─ useProductionGrid.ts
│  └─ production-grid-state.ts
├─ visual-lab/
│  ├─ VisualLabListPage.tsx
│  ├─ VisualLabPage.tsx
│  ├─ VisualLabCanvas.tsx
│  ├─ VisualLabOutline.tsx
│  ├─ VisualLabInspector.tsx
│  ├─ nodes/
│  ├─ edges/
│  ├─ commands/
│  └─ useVisualLabDocument.ts
├─ workflow-apps/
│  ├─ WorkflowLibraryPage.tsx
│  ├─ AppContractEditor.tsx
│  ├─ BindingInspector.tsx
│  └─ ValidationReport.tsx
└─ runtime/
   ├─ RuntimePage.tsx
   ├─ RuntimeInstanceList.tsx
   ├─ EnvironmentVersionDetail.tsx
   └─ RuntimeEventTimeline.tsx
```

规则：

- React Query 保存服务器事实；局部选择、viewport、拖拽中的临时位置使用组件/store 状态。
- 不把整个 graph JSON 复制到多个 state。
- 节点移动使用节流 batch command，拖拽结束必须 flush；失败回滚到服务器位置并显示局部错误。
- 节点组件 memo 化；选择和 Job heartbeat 不重渲染全部节点。
- 自定义 Node renderer，禁止继续使用 default label node。
- ProductionGrid 与 VisualLab 使用不同组件树、不同 API、不同领域类型，不做万能 `CanvasNode`。
- 路由参数持久化 selection，切换 episode/lab 时清理不合法选择。

---

# 13. 预检设计

所有生成入口共用同一个 `GenerationPlanResolver`，检查：

1. owner 与 project scope；
2. exact source revisions/media versions；
3. stale/freshness；
4. capability preference inheritance；
5. published ProfileVersion；
6. published WorkflowVersion；
7. Workflow App Contract；
8. RuntimeEnvironmentVersion；
9. Runtime state；
10. required models/custom nodes；
11. input media kind、hash、可读性；
12. width/height/fps/frame count/duration；
13. seed policy；
14. output contract；
15. GPU lease与并发；
16. VRAM policy；
17. 磁盘预算与 attempt sandbox；
18. authorization/license policy；
19. QC policy；
20. idempotency/fingerprint reuse。

结果结构：

```json
{
  "status": "READY|BLOCKED|READY_WITH_WARNINGS",
  "plan_hash": "...",
  "effective_configuration": {},
  "resolved": {
    "profile_version_id": "...",
    "workflow_version_id": "...",
    "contract_version_id": "...",
    "runtime_environment_version_id": "..."
  },
  "resource_estimate": {},
  "blockers": [
    {
      "code": "FIRST_FRAME_REQUIRED",
      "field": "first_frame",
      "message": "首帧尚未绑定",
      "remediation": {"action": "OPEN_INPUT_BINDING"}
    }
  ],
  "warnings": [],
  "would_create": {"variant": true, "job": true},
  "mutated": false
}
```

不得只显示 blocker 数量。

---

# 14. 迁移与切换策略

这是替换式迁移，但仍要保证项目数据可恢复。

## 14.1 阶段 M0：冻结旧语义

- 为旧 canvas API 标记 deprecated；
- 禁止新增功能；
- 记录现有调用方和 UAT；
- 新增数据库备份与 restore rehearsal；
- 写 ADR-0111（三层工作区）、ADR-0112（常驻 Runtime）、ADR-0113（Workflow App Contract/Environment Version）。

## 14.2 阶段 M1：建立新事实投影

- 实现 `ProductionStageProjector`；
- 用现有 shot/job/media/review/timeline fixture 验证每个 cell；
- 不调用旧 `ProductionCanvasService`；
- 对同一 episode 输出对账报告，人工核验异常状态。

## 14.3 阶段 M2：切换生产 UI

- 新 Production Cockpit 成为 episode 默认生产路由；
- 旧 Canvas route 只导航，不渲染；
- 停止写 `canvas_layouts` 和 `canvas_execution_plans`；
- EpisodeProductionRun 继续作为整集执行 facade。

## 14.4 阶段 M3：重构 Comfy 执行桥

- 新增 RuntimeEnvironmentVersion；
- 把当前生产环境捕获为第一个候选版本并完成验证；
- 建立常驻 Runtime Manager；
- 执行事件由 WS 主通道驱动；
- history/queue recovery 与旧 uncertain-success recovery 对账；
- 在切换窗口停止接受新 GPU 任务，等待在途 Attempt 完成，再切换 Worker；
- 不同时运行旧、新两个 production consumer。

## 14.5 阶段 M4：建设 Visual Lab

- 创建全新表、API、UI；
- 不导入旧 production nodes/edges/layout；
- 用户可从现有 Shot/Asset/Media 显式创建 Lab 引用节点；
- 旧 layout 仅在数据库备份中保留。

## 14.6 阶段 M5：删除旧实现

满足删除门禁后：

- 删除 `apps/web/src/pages/CanvasPage.tsx`；
- 删除 `apps/web/src/features/canvas/ProductionCanvasPanel.tsx`；
- 删除 `apps/api/local_drama/application/canvas.py`；
- 删除 `apps/api/local_drama/api/routes/canvas.py`；
- 从客户端生成器删除 `getProductionCanvas/saveProductionCanvasLayout/preflightProductionCanvasRun`；
- 删除旧 canvas tests、G9 readiness 中旧 canvas 数量要求，改为新验收；
- 数据迁移在确认无旧版本回滚需求后 drop `canvas_layouts` 与 `canvas_execution_plans`；
- 删除 ADR-0014 对应旧行为测试，并由新 ADR/测试取代。

旧表 drop 必须是独立 migration，在至少一次完整备份、恢复演练和一个稳定发布周期后执行；期间只读、不双写。

---

# 15. 开发工作包与 PR 顺序

每个 PR 必须可独立测试，但最终不以 feature flag 永久保留双架构。

## Track A：决策与契约

### PR-A01：ADR 与旧结论废止

- ADR-0111 三层图与权威边界；
- ADR-0112 常驻 Production Runtime；
- ADR-0113 Workflow App Contract + RuntimeEnvironmentVersion；
- 更新旧文档交叉引用。

退出条件：评审确认无页面直接使用 Comfy node id，无 Visual Lab 专属 Job/Media。

### PR-A02：OpenAPI 类型与错误词汇

- Production cell DTO；
- Visual Lab node/edge schema；
- Contract schema；
- Runtime states；
- provider event schema；
- blocker/remediation 通用结构。

退出条件：生成 TypeScript client，schema snapshot 通过。

## Track B：Production Cockpit

### PR-B01：ProductionStageProjector 单元测试

- 六阶段精确投影；
- capability/purpose 精确 Job 匹配；
- stale、审核、QC、timeline 规则。

### PR-B02：Production Grid API

- summary、cursor paging、filters、stage detail；
- 查询预算；
- 不触碰 Runtime。

### PR-B03：虚拟化矩阵骨架

- 路由、URL selection、loading/empty/error；
- 行列键盘导航；
- 1024/1280/1440 布局。

### PR-B04：Cell/Inspector/ActiveJobs

- 精确状态；
- 候选与 Job 深链；
- 项目事件局部刷新。

### PR-B05：范围 preflight 与 EpisodeRun 集成

- 批量选择；
- 计划分组；
- 人工 gate；
- pause/resume/retry。

### PR-B06：切换默认路由并停写旧 canvas

退出条件：500 镜头 fixture、恢复、URL、a11y、视觉 UAT 全通过。

## Track C：Runtime 与 Workflow

### PR-C01：RuntimeEnvironmentVersion schema/repository

### PR-C02：环境捕获、验证与 fingerprint

### PR-C03：Workflow App Contract schema/compiler transforms

### PR-C04：Workflow 发布联合门禁

### PR-C05：Production Runtime Manager

- start/health/drain/restart；
- PID/port/root ownership；
- exclusive Attempt ownership。

### PR-C06：Comfy WS Execution Bridge

- 建连、提交、事件、断线恢复；
- sanitized node_errors；
- semantic phase mapping。

### PR-C07：安全取消与输出收集

- owned Attempt interrupt；
- artifact/media registration；
- preview lifecycle。

### PR-C08：Runtime/Workflow UI

- 工作流库；
- Contract 表单；
- validation report；
- Runtime monitor。

退出条件：长视频任务在浏览器关闭、API 重启、WS 断连三种情况下都可最终对账。

## Track D：Visual Lab

### PR-D01：表与 repository

### PR-D02：节点/边 domain validation

### PR-D03：Lab CRUD 与乐观并发 API

### PR-D04：全屏 shell、Asset Library、Outline

### PR-D05：typed custom nodes/edges 与缩放层级

### PR-D06：Inspector 与 semantic input forms

### PR-D07：node preflight/run + live events

### PR-D08：candidate tray、CompareSet、SequencePreview

### PR-D09：promotion plan/commit 与审计

### PR-D10：snapshot/restore/export/Skill bridge

退出条件：从 Shot 打开 Lab，完成首尾帧实验、比较、采纳为 Shot candidate、正式 Review，全链路无手写 ID。

## Track E：退役

### PR-E01：删除旧前端与 generated client

### PR-E02：删除旧服务、路由和测试

### PR-E03：清理 G9/文档/导航

### PR-E04：稳定周期后 drop 旧表

退出条件：`rg ProductionCanvas|getProductionCanvas|canvas_execution_plans|canvas_layouts` 只允许出现在历史 migration/归档说明中。

---

# 16. 测试策略

## 16.1 Domain 单元测试

- 每阶段 READY/BLOCKED/RUNNING/FAILED/STALE/NEEDS_REVIEW；
- 旧失败 Job 不污染当前阶段；
- 同 shot 不同 capability 的任务互不污染；
- typed edge 全组合；
- downstream topo sort、cycle、人工 gate；
- semantic slot transform；
- environment fingerprint；
- owned Attempt cancel；
- node_errors 清洗与字段映射。

## 16.2 Repository 与 migration

- 空库升级；
- 真实旧库副本升级；
- 新表约束、FK、unique、revision conflict；
- 旧 canvas 停写；
- backup/restore；
- 最终 drop migration 只在独立 release 执行。

## 16.3 API contract

- cursor、filter、sort 稳定；
- plan/commit hash 防篡改；
- idempotency replay；
- project scope isolation；
- stale exact version 拒绝；
- Runtime offline/degraded/busy；
- provider validation error 可解释；
- 不返回本地绝对媒体路径、secret 或未经清洗 payload。

## 16.4 Comfy adapter integration（模拟 Runtime）

模拟：

- prompt accepted；
- node_errors；
- progress；
- preview binary；
- cached nodes；
- success + multiple outputs；
- execution_error；
- interrupt；
- WS 断线但 history success；
- WS 断线且 queue running；
- runtime crash；
- late success recovery；
- malicious output path/symlink。

## 16.5 Comfy live smoke

固定小工作流，不要求每次全量测试都加载 H3：

1. 环境 fingerprint；
2. object_info；
3. 发布 Contract；
4. 提交；
5. WS progress；
6. history success；
7. artifact hash；
8. drain/restart；
9. owned cancel；
10. custom node allowlist smoke。

H3 视频验收独立运行：T2V、I2V、First/Last、Ref2V 中当前声明为 Active 的能力分别至少一条成功证据。

## 16.6 前端组件测试

- 生产 cell 全状态；
- 选择与 URL；
- episode/lab 切换清理 stale selection；
- async error 就地展示；
- disabled reason；
- Outline 键盘操作；
- node move failure 回滚；
- event 只刷新目标 cell/node；
- video 不自动加载；
- reduced motion。

## 16.7 E2E

关键旅程：

1. 进入 120 镜头 episode，筛选阻塞项，定位第 87 镜头，刷新后保持上下文。
2. 对 10 个 proxy-ready 镜头批量 preflight，跳过 blocker，提交并观察局部进度。
3. 从镜头创建 Visual Lab，绑定角色、首帧、尾帧，生成四个候选。
4. 将候选加入 CompareSet，选择一个，采纳为 shot candidate。
5. 在正式 Review 中批准，再回到 Production Cockpit 看到状态变化。
6. Comfy WS 断线后恢复，页面不重复提交。
7. Runtime BUSY 时 drain，当前任务完成后重启到新 environment。
8. 取消当前 Attempt，不影响排队任务。
9. 键盘和 Outline 完成 Lab 节点选择、连接、运行。

## 16.8 性能门槛

在固定本机 fixture 与记录的测试硬件上：

- 500 镜头生产矩阵 DOM 只渲染可视窗口及有限 overscan；
- 100 行 production-grid API p95 小于 500ms；
- 300 个普通 Lab 节点首次可交互小于 2s；
- 拖拽期间不因 Job heartbeat 重渲染全图；
- 10 秒连续滚动/平移 trace 无持续性长任务；
- poster 之外的视频默认不下载；
- 预览和缩略图有固定尺寸，不发生明显 CLS。

性能测试必须保存 trace/fixture/机器信息，不能只写“感觉流畅”。

---

# 17. 视觉、交互与可访问性验收

遵循 `design-system/localdramastudio/MASTER.md`：

- 深色 persistent chrome，暖中性工作面；
- Creative 橙只用于当前主创作动作；
- selected 与 approved 使用不同语义；
- one primary CTA；
- 40px+ 控件、44px icon hit target；
- 150–220ms transform/opacity 动效；
- 文字与图形满足 WCAG AA；
- 所有状态使用文本 + icon/shape + color；
- 50+ 列表虚拟化；
- 错误靠近失败区域，含 cause、request id、retry、diagnostics；
- 不使用 emoji 作为结构 icon；
- 1440×900、1280×800、1024×768 三档都验收。

Visual Lab 特别要求：

- fit、zoom、pan 不能抢占浏览器/表单快捷键；
- 输入框聚焦时禁用全局字母快捷键；
- 删除节点需说明不会删除生成资产；
- 删除含未保存文本的节点需要确认；
- connection target 有明显可见和键盘焦点状态；
- 画布不是唯一信息路径，Outline 必须等价可用。

---

# 18. 可观测性与审计

必须回答：

- 哪个用户以哪个 plan hash 提交了哪个 Variant？
- 使用了哪个 PromptRevision、MediaVersion、ProfileVersion、WorkflowVersion、ContractVersion、EnvironmentVersion？
- Comfy 返回了哪个 prompt_id？
- 哪个 RuntimeInstance 拥有该 Attempt？
- WS 事件是否中断，怎样恢复？
- 输出由哪个 output node/contract slot 产生？
- 谁把 Lab candidate 采纳到哪个正式对象？
- 谁批准，批准时 subject revision 是什么？

日志分层：

- 创作 UI：语义阶段和可行动错误；
- Job Detail：Attempt、资源、provider phase、恢复；
- Diagnostics：redacted raw event、node id、request id、environment fingerprint；
- Audit：不可变人类动作和发布/采纳事件。

---

# 19. 风险与控制

| 风险 | 控制 |
|---|---|
| 常驻 Runtime 长期显存碎片或泄漏 | 每任务健康检查、阈值 drain、模型族切换重启、最大连续任务策略 |
| 自定义节点破坏稳定性 | EnvironmentVersion、allowlist、validation runtime、固定 commit/hash |
| Comfy WS 丢事件 | sequence/idempotency、history 最终证据、queue reconciliation |
| `/interrupt` 误伤 | Runtime exclusive ownership + owned_attempt_id 校验 |
| Visual Lab 再次成为第二真值 | promotion command、精确引用、禁止 Lab approval/job/media 表 |
| 生产投影查询变慢 | 专用 repository、批量查询、索引、游标分页、查询预算测试 |
| 新旧 UI 长期并存 | 明确 cutover 与删除 PR，禁止永久 feature flag/双写 |
| 旧文档继续误导 | 本文效力声明、ADR superseded 标记、交叉链接 |
| 大画布性能差 | LOD renderer、memo、viewport culling、poster-only、事件局部失效 |
| 用户误把 retry 当新创作 | 操作命名、Variant/Attempt 分离、不同按钮与确认文案 |

---

# 20. Definition of Done

只有以下条件全部满足，才算“画布与 ComfyUI 重构完成”：

## 产品

- 分集日常生产不再进入自由 DAG。
- Production Cockpit 可准确显示 500 镜头并完成范围操作。
- Visual Lab 能完成多参考、首尾帧、分支、比较、序列预览和正式采纳。
- 普通创作者无需看到或填写 Comfy node id、workflow JSON、本地绝对路径。

## 架构

- 三层工作区共享现有权威 Job/Media/Review/Timeline。
- WorkflowVersion 绑定 ContractVersion 和 RuntimeEnvironmentVersion。
- Production Runtime 常驻、受监管、GPU 独占。
- WS 是主要事件通道，history 是最终证据和恢复通道。
- cancel 有 Runtime ownership 校验。
- 新 application 模块只依赖 ports。

## 数据

- 旧 canvas 停止写入。
- Visual Lab 不迁移旧五阶段节点。
- 不存在画布专属 Job、Media、Review 或 stage truth。
- migration、backup、restore、late-success recovery 全通过。

## 质量

- 单元、API、migration、mock Comfy、live smoke、E2E、a11y、性能门槛通过。
- 120/500 镜头 fixture 通过。
- 300 节点 Lab fixture 通过。
- Runtime offline、WS disconnect、process crash、cancel、late success 均有证据。

## 清理

- 旧 `CanvasPage`、`ProductionCanvasPanel`、`ProductionCanvasService`、旧路由和客户端方法已删除。
- 旧 G9 canvas 验收被新验收取代。
- 稳定周期后旧表已 drop，或仅因明确版本回滚窗口暂时只读保留，并有确定删除版本。
- 代码库中没有“以后再切”的永久兼容分支。

---

# 21. 最终开发指令

实施团队必须遵守以下一句话：

> **保留 LocalDramaStudio 已经可靠的任务、版本、媒体、审核和本地安全底座；彻底退役把固定生产阶段伪装成自由画布的实现；新建结构化 Production Cockpit、独立 Visual Lab 和语义化 ComfyUI Runtime 三个清晰边界。**

任何 PR 如果只是让旧生产 DAG “看起来更像竞品”，或者继续把生产状态、创意连线、Comfy 技术节点塞回同一个 graph，应直接拒绝。

---

# 22. 实施交付状态（2026-08-26）

本轮已经按上述目标完成主干切换，不再保留旧画布作为可运行的第二套系统：

- Production Cockpit 已落地为服务端分页的六阶段镜头状态矩阵，并提供镜头/阶段详情投影；
- Visual Lab 已落地为真正的无限画布：坐标无边界、8%–250% 缩放、持久化 viewport、视口外节点裁剪、框选/多选、拖拽平移、搜索定位、缩略图导航、适配视图和跨刷新空间记忆；
- 画布编辑能力已经形成闭环：原子复制/批量删除、同 Lab 剪贴板、对齐/分布、可缩放节点、可折叠节点、可移动 FRAME 分组、选择状态栏，以及不会因部分请求成功而产生半套拓扑的服务端批量命令；
- Visual Lab 历史不是前端临时数组：快照持久化节点、边、几何和 viewport，恢复执行 preflight + plan hash + topology revision 校验；会删除已采纳节点的恢复被阻止，Undo/Redo 复用相同的持久化恢复通道；
- Visual Lab 同时保留语义节点、不可变内容修订、类型端口、无环连线、生成预检/提交以及带 plan hash 的正式采纳；媒体拖入只接受已经登记的 `MediaVersion`，不把系统文件路径直接变成生成输入；
- Runtime Environment、EnvironmentVersion、Workflow App ContractVersion、显式 Workflow/Contract/Runtime 绑定与运行实例监管已经落库并提供 API/UI；
- Comfy 执行已接入 provider WebSocket 事件、语义阶段映射、持久化事件流、history/queue 恢复与基于 runtime ownership 的安全取消；
- 旧 Canvas API、页面、生产画布组件、应用服务、客户端类型与生成模板已经删除；旧 URL 只做单向导航，不再承载旧实现；
- `0060_visual_lab_runtime_foundation` 提供新领域表、外键、唯一约束和查询索引；旧表仅作为历史迁移链和审计读取保留，不再有写入口；
- G9 已切换为 Production Cockpit 与 Visual Lab 的独立验收，不再以固定生产 DAG 的节点数量作为退出标准；
- 新 OpenAPI/TypeScript 客户端、后端核心回归、前端路由回归、正式构建与 bundle budget 已通过；包含远端负坐标/正坐标、FRAME、刷新后 viewport、搜索定位、复制、持久化 Undo/Redo 的真实浏览器走查已通过且控制台无错误。

真实模型和 GPU 上的最终视频 smoke 不是代码内伪造的 PASS：它必须在目标机器登记并发布实际 Runtime Environment、发布对应 Workflow App Contract、完成显式绑定后执行。代码、状态机、恢复和安全边界已经交付，该硬件验收是部署动作，不是遗留的旧画布兼容分支。
