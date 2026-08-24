# LocalDramaStudio 密钥、模型详情与运行参数配置改造·实现设计

> 日期：2026-08-24  
> 版本：v1.0  
> 状态：READY_FOR_IMPLEMENTATION  
> 适用范围：Web、API、Profile、H3 工作流、音频工作区、审计与测试  
> 关联方案：`creator-experience-optimization-solution-2026-08-24.md` 中的模型配置与生产设置改造

---

## 1. 文档结论

本次改造不能只给下拉框增加一个“查看”按钮，而应建立一条完整、可验证的配置链路：

1. **远端连接与密钥可管理、可显式查看**：默认掩码，用户主动点击后短时明文展示；支持复制、替换、删除和连通性测试。密钥继续存放在 Windows Credential Manager，不写入 SQLite、日志、URL、缓存或审计详情。
2. **所有 Profile 选择器都能查看真实执行内容**：显示主模型、文本编码器、Video VAE、Audio VAE、LoRA、运行时、工作流、默认参数和证据哈希，而不只显示 Profile 名称。
3. **运行参数必须是结构化、可校验且真正生效的配置**：禁止继续把高级配置当作任意 JSON 保存。每个 Profile 通过 `override_schema` 声明可配置字段、范围、作用域和运行时绑定。
4. **提交生成前必须显示“本次实际配置”**：合并 Profile 默认值、项目/集/镜头偏好和本次覆盖值，返回来源、有效参数与指纹，用户确认的是最终会进入工作流的配置。
5. **LoRA、步数、档位、原生音频等必须进入工作流编译器**：未完成真实图绑定和自动化证据前，界面不得声称该参数已生效。
6. **音频模型按用途拆分**：视频原生音频、对白 TTS、音效/配乐是三条不同能力链路，分别选择和展示，避免一个“音频模型”字段同时表达三种语义。

该设计以桌面本地工作站为前提。密钥明文查看是受控的本地操作，不设计成远程控制台能力。

---

## 2. 目标、非目标与设计原则

### 2.1 目标

- 用户能在一个中心位置管理所有远端模型服务、连接地址和凭据。
- 用户在任何模型下拉框旁都能看到该选项的完整执行配置。
- 用户能按 Profile 能力选择可用参数，例如生成步数、是否启用 Turbo LoRA、LoRA 强度、是否生成原生音频。
- 项目级默认设置和单次运行设置有明确继承关系。
- 后端能证明配置已进入实际工作流，冻结任务不受后续设置变更影响。
- 对普通用户提供简洁摘要，对专家提供组件、路径、哈希和原始快照。

### 2.2 非目标

- 不在本期实现任意第三方 Provider 插件市场。
- 不允许浏览器长期保存、自动填充或导出全部密钥。
- 不允许用户通过自由 JSON 注入未声明的工作流参数。
- 不把本地模型文件复制进数据库。
- 不修改已经冻结或已提交任务的执行快照。
- 不把 H3 原生音频 VAE、对白 TTS、SFX/BGM 合并成一个 Profile。

### 2.3 设计原则

- **默认摘要，按需展开**：选择控件保持紧凑，详情通过行内摘要和右侧抽屉逐层展开。
- **密钥可见但不常驻**：默认掩码，显式查看，60 秒后自动清除。
- **配置即契约**：界面只渲染后端声明为可覆盖的参数。
- **所见即所得**：有效参数必须来自与任务提交相同的解析服务。
- **旧任务不可变**：设置变更只影响之后创建的 Variant/Job。
- **错误就近反馈**：字段校验、连接测试和运行时不支持信息显示在对应区域，不使用笼统 Toast 代替。

---

## 3. 现状与必须修复的事实缺口

| 领域 | 当前实现 | 问题 | 本次决策 |
|---|---|---|---|
| 远端密钥 | `LocalLLMConfigurationPanel` 只显示掩码；后端只支持 DeepSeek 固定凭据目标 | 无法查看原值，无法管理多个连接 | 建立通用 Provider Connection 和显式 Reveal 接口 |
| 模型选择 | 多处只有 Profile `<select>` | 用户不知道实际选择了哪些模型组件 | 统一使用 `ModelSelectWithDetails` 与 `ModelInspectorDrawer` |
| Profile 详情 | `model_bundle_json` 已存储但详情接口未返回 | 前端无法展示真实模型包 | 扩展 Profile Version Detail 并归一化组件 |
| 高级参数 | `GenerationPreferencePanel` 使用自由 JSON | 无类型、无范围、无运行时保证 | 由 `override_schema` 驱动结构化表单 |
| H3 档位 | 档位摘要显示 steps/denoise/cfg，但当前主要只覆盖尺寸和帧数 | UI 宣称与实际图不一致 | 参数必须由工作流编译器绑定；未绑定项不展示为“已生效” |
| H3 LoRA | 请求有 `acceleration`，但工作流未消费 | 开关会是假功能 | 实现 `OFF/TURBO_LORA` 枚举和真实 LoRA 节点 |
| 音频 | 原生视频音频、TTS、SFX/BGM 分散且命名含混 | 用户无法判断“音频模型”指什么 | 按用途分区并分别展示 Profile/组件 |
| 项目设置 | 只显示偏好/Profile 名称 | 无法检查继承值和组件 | 增加配置摘要、来源与详情入口 |
| 生成提交 | 选择后直接生成 | 缺少最终有效配置确认 | 增加服务器预检和 Effective Configuration Preview |

---

## 4. 统一领域模型

### 4.1 四个核心概念

#### Provider Connection

描述一个远端服务连接，包含 Provider 类型、协议、Base URL、凭据引用和探测状态。它不等同于模型 Profile，也不直接保存密钥明文。

示例：

- DeepSeek OpenAI Compatible
- 自建 OpenAI Compatible Gateway
- Ollama（无密钥）

#### Execution Profile Version

不可变的执行契约版本，描述某项能力使用的运行时、工作流、模型组件、默认参数和可覆盖参数。任务必须绑定具体版本，不能只绑定可变 Profile 名称。

#### Preference

项目、集、镜头等作用域上的默认 Profile 与参数覆盖。Preference 可变，只影响之后的配置解析。

#### Effective Configuration

任务提交前由服务器解析得到的最终配置，包括实际 Profile Version、模型组件、参数、来源、工作流指纹和模型包指纹。该结果是 UI 预览、任务输入快照和审计证据的共同真相源。

### 4.2 配置继承顺序

从低到高依次覆盖：

```text
Profile Version 不可变默认值
  → Project Preference
  → Episode Preference
  → Shot Preference
  → 本次 Run Override
```

约束：

- 每一层只能设置 `override_schema` 允许且包含该作用域的字段。
- `null` 表示继承，不表示把值清空；可清空字段需显式声明 `nullable: true`。
- 未知字段一律返回 `422`，不得静默忽略。
- UI 必须显示每个有效值的来源，例如“Profile 默认”“项目覆盖”“本次覆盖”。

---

## 5. 信息架构与页面改造

### 5.1 `/models` 全局模型配置中心

将现有视图调整为：

1. **生成偏好**：各能力的默认 Profile、参数摘要与继承规则。
2. **远端服务与密钥**：连接列表、密钥查看/替换、测试。
3. **本地运行时**：Ollama/ComfyUI 等本地运行时状态、路径和模型发现。
4. **模型兼容性**：保留现有兼容性视图。
5. **Profile 与执行契约（专家）**：Profile 版本、真实模型包、工作流和原始快照。
6. **工作流（专家）**：保留现有工作流视图。

旧的 `local-llm` 内容拆分：连接信息进入“远端服务与密钥”，本地运行时信息进入“本地运行时”。路由查询参数保留兼容映射：`view=local-llm` 首次进入时重定向到最接近的新视图。

### 5.2 远端服务与密钥页面

桌面布局：左侧连接列表，右侧详情面板。

```text
┌ 远端服务与密钥 ──────────────────────────────────────────┐
│ [＋添加连接]                                [刷新状态]   │
├──────────────────┬───────────────────────────────────────┤
│ DeepSeek         │ 名称       DeepSeek 主连接             │
│ ● 可用           │ 协议       OpenAI Compatible           │
│ 自建网关         │ Base URL   https://...                 │
│ ○ 未测试         │ API Key    sk-••••••••••9x2  [查看]    │
│ Ollama           │            [复制] [替换] [删除]        │
│ ● 可用/无密钥    │ [测试连接]                 最近成功…   │
└──────────────────┴───────────────────────────────────────┘
```

交互规则：

- 默认显示 `masked_secret`，不在列表接口返回明文。
- 点击“查看”触发一次显式请求，按钮进入 loading；成功后在当前组件本地 state 显示 60 秒倒计时。
- 明文区域提供“复制”和“立即隐藏”；复制后不在 Toast 中重复密钥内容。
- 抽屉关闭、页面失焦、`visibilitychange=hidden`、连接切换和组件卸载时立即清除明文。
- 明文不写入 URL、Query Cache、全局 Store、localStorage、sessionStorage、表单默认值或错误上报上下文。
- “替换”使用空密码框；浏览器自动填充关闭；保存后字段清空并刷新掩码。
- “删除密钥”与“删除连接”分开。删除连接前提示受影响的 Profile，存在已发布引用时默认禁止删除，只允许停用。
- 测试结果显示 DNS/连接、认证、模型探测三个阶段；后端返回安全摘要，不回显请求头和响应正文。
- 环境变量来源显示变量名和“进程环境只读”；本期不允许从 UI 覆盖环境变量。若执行显式查看，仍按 60 秒策略返回当前进程值。

### 5.3 模型选择器统一形态

所有 Profile 选择位置替换为同一模式：

```text
视频生成模型
[ H3 FL2VA · Production                    v ] [查看详情]
  H3 主模型 · 720p · 129 帧 · 原生音频 · Turbo LoRA 可用
```

`ModelSelectWithDetails` 必须提供：

- Profile 标题、版本、发布状态和能力标签。
- 一行执行摘要，不展示内部 ID 作为主要信息。
- “查看详情”打开右侧 `ModelInspectorDrawer`。
- 不可用选项说明原因，例如模型文件缺失、运行时离线、GPU 不满足、连接认证失败。
- 键盘可访问：选择器、详情按钮、抽屉关闭和折叠区域有明确焦点顺序。

### 5.4 模型详情抽屉

抽屉分四层，普通模式默认展开前两层，专家模式可展开全部：

1. **概览**：能力、版本、状态、运行时、工作流、预估资源。
2. **模型组件**：按角色列出主模型、编码器、Video VAE、Audio VAE、LoRA、ControlNet、Upscaler、TTS Engine。
3. **默认与可覆盖参数**：字段、默认值、允许范围、是否已锁定、当前来源。
4. **证据（专家）**：artifact ID、本机路径、大小、SHA-256、manifest/workflow/profile 指纹和原始只读 JSON。

组件行示例：

| 角色 | 模型 | 状态 | 摘要 |
|---|---|---|---|
| 主模型 | HunyuanVideo 1.5 FL2VA | 可用 | bf16 · 18.2 GB |
| 文本编码器 | Qwen Image Text Encoder | 可用 | 8.4 GB |
| Video VAE | Hunyuan Video VAE | 可用 | 1.2 GB |
| Audio VAE | Hunyuan Audio VAE | 可用 | 原生视频音轨 |
| 加速 LoRA | H3 Turbo LoRA | 可选 | 默认关闭 · 强度 1.0 |

路径和哈希按行折叠，避免普通用户被技术信息淹没；但在本地专家视图中允许完整查看和复制。

### 5.5 参数覆盖面板

`ModelParameterOverridePanel` 完全由 Profile 的 `override_schema` 渲染：

- 数字字段使用 number input + 合理 step；离开字段时校验。
- 枚举使用 segmented control 或 select，选项不超过 3 个时优先 segmented control。
- 布尔值使用明确的开关文案，例如“生成视频原生音轨”，不使用含糊的“启用音频”。
- 依赖字段按 `visible_if` 展示。例如仅当 `acceleration=TURBO_LORA` 时显示 LoRA 强度。
- 每个字段都有“恢复继承”操作和当前来源标签。
- 不可覆盖字段只读显示，并解释由 Profile 或工作流锁定。
- 参数改变后 300 ms 防抖调用配置预检；提交按钮在预检未完成或失败时禁用。

H3 首期支持字段：

| 字段 | 类型 | 建议范围 | 作用域 | 运行时绑定 |
|---|---|---:|---|---|
| `production_tier` | enum | Preview/Balanced/Production | Project/Run | 尺寸、帧数与 tier defaults |
| `sigma_points` | integer | 2–1000，默认 50 | Project/Shot/Run | 采样步数 |
| `acceleration` | enum | OFF/TURBO_LORA | Project/Shot/Run | 是否插入 LoRA 节点 |
| `lora_strength` | number | 0–2，步进 0.05 | Project/Shot/Run | LoRA model strength |
| `native_audio` | boolean | true/false | Project/Shot/Run | Audio VAE/解码链路 |
| `take_count` | integer | 1–8 | Project/Shot/Run | 候选数量 |

`denoise`、`cfg`、`sampler`、`scheduler` 只有在当前 H3 工作流真正支持绑定后才加入 schema。档位中的展示值也必须来自编译后的有效配置，不得只来自静态文案。

### 5.6 生成工作台

在 `GenerationWorkbench`/`GenerationControlPanel` 中：

- Profile 选择改为统一选择器。
- 高级配置区域渲染结构化参数面板。
- 生成按钮上方增加“本次实际配置”摘要：Profile Version、主要组件、分辨率、帧数、步数、LoRA、原生音频、候选数和预计显存/时长。
- 点击摘要打开详情抽屉，详情数据来自服务器预检结果。
- 预检返回警告时允许继续的条件必须显式标记；模型缺失、连接认证失败、参数不合法、运行时离线属于阻断错误。
- 创建 Variant/Job 时同时提交预检 token 或预期配置指纹；服务器重新解析，指纹变化则返回 `409 CONFIGURATION_CHANGED`，要求用户重新确认。

### 5.7 项目生产设置

`ProductionSettingsOverview` 不再只显示 Profile 名称，改为每个能力一张摘要卡：

- 当前解析模式：显式选择/AUTO。
- 实际 Profile Version。
- 核心模型组件摘要。
- 项目级覆盖参数及继承来源。
- 当前可用性和最后探测时间。
- “编辑默认配置”和“查看模型详情”。

高级表格继续保留冻结任务计数，并新增“当前设置只影响新任务”的固定说明。

### 5.8 项目创建与一句话向导

- `ProjectCreateWizard` 和 `OneSentenceVideoWizard` 复用同一选择器与详情抽屉。
- 一句话向导删除重复的 DeepSeek 密钥输入逻辑，改为选择已配置的 Provider Connection；无连接时提供前往 `/models?view=connections` 的引导。
- 向导只展示关键参数；完整参数放入“高级设置”折叠区。
- 创建前的确认页展示模型、文本模型连接、视频配置和音频策略。

### 5.9 音频工作区

在 `AudioPage` 顶部增加“音频配置”摘要，明确三条链路：

| 用途 | 配置对象 | 详情内容 | 入口 |
|---|---|---|---|
| 视频原生音频 | 视频 Profile 的 Audio VAE + `native_audio` | Audio VAE、是否随视频生成、工作流状态 | 视频模型详情 |
| 对白 TTS | TTS Profile + 角色 Voice | Provider/Runtime、声音、语言、采样率 | 对白治理区 |
| 音效/配乐 | SFX/BGM Profile | 模型、时长/风格参数、输出格式 | Tracks 区 |

`DialogueGovernanceActions` 的 TTS Profile 选择器使用统一组件；`DialogueTTSPanel` 的候选卡从单一 `model_ref` 扩展为可查看 Profile Version、Voice、引擎和参数快照。

---

## 6. 前端组件与状态设计

### 6.1 建议目录

```text
apps/web/src/features/model-config/
  api/
    providerConnections.ts
    profileExecutionDetails.ts
    effectiveConfiguration.ts
  components/
    CredentialManager.tsx
    CredentialRow.tsx
    SecretRevealField.tsx
    ModelSelectWithDetails.tsx
    ModelInspectorDrawer.tsx
    ModelComponentList.tsx
    ModelParameterOverridePanel.tsx
    EffectiveConfigurationPreview.tsx
    ConfigurationSourceBadge.tsx
  hooks/
    useSecretReveal.ts
    useProfileExecutionDetail.ts
    useEffectiveConfigurationPreview.ts
  schemas/
    overrideSchema.ts
  types.ts
```

页面只负责场景编排，不自行实现密钥查看、Profile 详情和参数合并。

### 6.2 状态边界

| 数据 | 存放位置 | 缓存策略 |
|---|---|---|
| 连接列表/掩码/状态 | TanStack Query | 30 秒 stale，变更后失效 |
| Profile 执行详情 | TanStack Query | 按不可变 version ID 长缓存 |
| 明文密钥 | `SecretRevealField` 本地 state | 禁止 Query Cache；最长 60 秒 |
| 表单覆盖值 | 页面表单 state | 离开页面前未保存提示 |
| 有效配置预检 | TanStack Query 或 mutation result | 按输入指纹短缓存；禁止把 secret 纳入 key |
| 抽屉开关/折叠状态 | 页面本地 state | 不持久化明文相关状态 |

### 6.3 错误与加载状态

- 列表骨架、详情骨架和预检加载互不阻塞。
- Reveal 失败只影响密钥区域，不清空其他连接表单。
- 参数字段错误显示在字段下方，并在区域标题显示错误数量。
- 连通性测试显示阶段状态；可重试错误保留输入。
- 接口返回 `CONFIGURATION_CHANGED` 时保留用户覆盖值，重新拉取预检并突出变化项。

---

## 7. 后端数据模型与迁移

### 7.1 `provider_connections` 表

使用下一条 Alembic migration 创建：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | text/UUID PK | 连接 ID |
| `code` | text unique | 稳定代码 |
| `title` | text | 用户显示名称 |
| `provider_kind` | text | DEEPSEEK/OPENAI_COMPATIBLE/OLLAMA/CUSTOM |
| `protocol` | text | OPENAI_COMPATIBLE/OLLAMA/NATIVE |
| `base_url` | text | 服务地址 |
| `credential_source` | text | WINDOWS_CREDENTIAL_MANAGER/ENVIRONMENT/NONE |
| `credential_ref` | text nullable | 凭据目标引用，不是密钥 |
| `environment_variable_name` | text nullable | 环境变量来源名称 |
| `status` | text | ACTIVE/DISABLED |
| `last_probe_status` | text nullable | OK/AUTH_FAILED/UNREACHABLE/INVALID_RESPONSE |
| `last_probe_at` | datetime nullable | 最近探测时间 |
| `last_probe_summary_json` | text nullable | 安全摘要，不含响应正文 |
| `revision` | integer | 乐观并发版本 |
| `created_at/updated_at` | datetime | 时间戳 |
| `created_by/updated_by` | text | 本地操作者标识 |

禁止字段：`api_key`、`secret`、`token` 或任何明文凭据列。

Windows Credential Manager 目标格式：

```text
LocalDramaStudio/ProviderConnection/{connection_id}
```

现有 `LocalDramaStudio/DeepSeekAPI` 在迁移后首次读取时兼容：创建默认 DeepSeek Connection，并把旧目标值复制到新目标；复制成功和读取校验完成后再删除旧目标。迁移必须可重复执行，失败时保留旧目标。

### 7.2 Profile Model Bundle v2

新写入的 Profile Version 使用：

```json
{
  "schema_version": "localdrama.execution-profile-bundle.v2",
  "provider_connection_id": null,
  "runtime_id": "comfyui-local",
  "workflow_id": "h3-fl2va",
  "components": [
    {
      "artifact_id": "...",
      "role": "PRIMARY_MODEL",
      "required": true
    },
    {
      "artifact_id": "...",
      "role": "AUDIO_VAE",
      "required": true
    },
    {
      "artifact_id": "...",
      "role": "LORA",
      "purpose": "TURBO_ACCELERATION",
      "required": false
    }
  ],
  "evidence": {
    "manifest_sha256": "...",
    "workflow_sha256": "..."
  }
}
```

组件角色枚举首期包含：

```text
PRIMARY_MODEL
TEXT_ENCODER
VIDEO_VAE
AUDIO_VAE
LORA
CONTROLNET
UPSCALER
TTS_ENGINE
VOICE_MODEL
```

旧版只含 `artifact_ids` 的 bundle 不做破坏性回填；读取时通过 `model_artifacts` 连接和 manifest 角色归一化为 v2 响应。发布新版本时写 v2。

### 7.3 Override Schema

Profile Version 增加或复用可版本化 JSON 字段存储以下契约：

```json
{
  "schema_version": "localdrama.profile-overrides.v1",
  "additional_properties": false,
  "fields": {
    "sigma_points": {
      "type": "integer",
      "label": "生成步数",
      "default": 50,
      "minimum": 2,
      "maximum": 1000,
      "step": 1,
      "scopes": ["PROJECT", "SHOT", "RUN"],
      "runtime_binding": "SAMPLER_STEPS"
    },
    "acceleration": {
      "type": "enum",
      "label": "加速模式",
      "default": "OFF",
      "options": ["OFF", "TURBO_LORA"],
      "scopes": ["PROJECT", "SHOT", "RUN"],
      "runtime_binding": "H3_ACCELERATION"
    },
    "lora_strength": {
      "type": "number",
      "label": "LoRA 强度",
      "default": 1.0,
      "minimum": 0,
      "maximum": 2,
      "step": 0.05,
      "visible_if": {"field": "acceleration", "equals": "TURBO_LORA"},
      "scopes": ["PROJECT", "SHOT", "RUN"],
      "runtime_binding": "LORA_MODEL_STRENGTH"
    }
  }
}
```

后端使用同一验证器处理 Preference 保存、预检和任务提交。前端校验只用于即时反馈，不取代服务器校验。

### 7.4 有效配置快照

创建 Variant/Job 时至少持久化：

- `profile_version_id`
- `model_bundle_snapshot`
- `override_schema_version`
- `effective_settings`
- `setting_sources`
- `runtime_id` / `workflow_id`
- `profile_fingerprint`
- `model_bundle_fingerprint`
- `workflow_fingerprint`
- `effective_configuration_fingerprint`

可复用现有 `generation_variants.parameter_set_json` 和任务输入快照，优先增加 schema version，而不是为每个参数增加数据库列。

---

## 8. API 设计

### 8.1 Provider Connection

```text
GET    /api/v1/provider-connections
POST   /api/v1/provider-connections
GET    /api/v1/provider-connections/{connection_id}
PATCH  /api/v1/provider-connections/{connection_id}
DELETE /api/v1/provider-connections/{connection_id}
POST   /api/v1/provider-connections/{connection_id}:reveal-secret
PUT    /api/v1/provider-connections/{connection_id}/secret
DELETE /api/v1/provider-connections/{connection_id}/secret
POST   /api/v1/provider-connections/{connection_id}:probe
```

列表响应只返回：

```json
{
  "id": "pc_...",
  "title": "DeepSeek 主连接",
  "provider_kind": "DEEPSEEK",
  "protocol": "OPENAI_COMPATIBLE",
  "base_url": "https://api.deepseek.com/v1",
  "credential_source": "WINDOWS_CREDENTIAL_MANAGER",
  "has_secret": true,
  "masked_secret": "sk-••••••••9x2",
  "status": "ACTIVE",
  "last_probe": {"status": "OK", "at": "...", "latency_ms": 321},
  "revision": 3
}
```

Reveal 请求不接收密钥参数，响应：

```json
{
  "secret": "sk-...",
  "expires_in_seconds": 60
}
```

Reveal 响应头必须包含：

```text
Cache-Control: no-store, private
Pragma: no-cache
```

所有写操作、Reveal 和 Probe 继续要求本地实例令牌；服务仅绑定 loopback。密钥只允许放在请求 body，禁止 URL/query/header 日志字段。

### 8.2 Profile 执行详情

扩展：

```text
GET /api/v1/profile-versions/{version_id}
```

新增响应字段：

```json
{
  "execution": {
    "schema_version": "localdrama.profile-execution-detail.v1",
    "runtime": {"id": "comfyui-local", "title": "Local ComfyUI", "status": "READY"},
    "workflow": {"id": "h3-fl2va", "title": "H3 FL2VA", "sha256": "..."},
    "components": [
      {
        "artifact_id": "...",
        "role": "PRIMARY_MODEL",
        "title": "HunyuanVideo 1.5 FL2VA",
        "status": "AVAILABLE",
        "size_bytes": 19542101120,
        "sha256": "...",
        "machine_path": "D:\\...\\model.safetensors",
        "required": true
      }
    ],
    "defaults": {"sigma_points": 50, "acceleration": "OFF", "native_audio": true},
    "override_schema": {},
    "worker_policy": {},
    "fingerprints": {"profile": "...", "model_bundle": "...", "workflow": "..."}
  }
}
```

本地专家 UI 可展示 `machine_path`。任何遥测、审计和错误上报都应删除或只保留 basename。

### 8.3 配置预检

```text
POST /api/v1/generation/effective-configuration:resolve
```

请求：

```json
{
  "project_id": "...",
  "episode_id": null,
  "shot_id": "...",
  "capability_code": "VIDEO_FL2VA",
  "requested_profile_version_id": "...",
  "run_overrides": {
    "production_tier": "PRODUCTION",
    "acceleration": "TURBO_LORA",
    "lora_strength": 1.0
  }
}
```

响应：

```json
{
  "profile_version_id": "...",
  "effective_settings": {
    "width": 1280,
    "height": 720,
    "frames": 129,
    "sigma_points": 50,
    "acceleration": "TURBO_LORA",
    "lora_strength": 1.0,
    "native_audio": true,
    "take_count": 4
  },
  "setting_sources": {
    "sigma_points": "PROFILE_DEFAULT",
    "acceleration": "RUN_OVERRIDE"
  },
  "components": [],
  "runtime_status": "READY",
  "warnings": [],
  "blocking_errors": [],
  "estimated_resources": {},
  "fingerprint": "sha256:...",
  "valid_until": "..."
}
```

提交生成时携带 `expected_effective_configuration_fingerprint`。服务器必须再次解析，避免预览和执行之间发生配置漂移。

### 8.4 Preference 更新

现有 Preference PUT 保留，但：

- `settings` 按选中 Profile Version 的 `override_schema` 校验。
- 首期只允许 `EXPLICIT` Profile 保存非空 settings；AUTO 模式暂时只允许空 settings，避免不同 Profile schema 不兼容。
- 响应返回规范化 settings 和解析警告。
- 未知字段、越界、作用域不允许分别返回稳定错误码。

---

## 9. H3 工作流真实绑定设计

### 9.1 编译入口

在 `apps/api/local_drama/application/h3_workflows.py` 抽取明确的编译阶段：

```python
compile_effective_h3_settings(profile_version, effective_settings, runtime_layout)
    -> CompiledH3Settings

build_t2va(compiled_settings)
build_fl2va(compiled_settings)
build_ref2va(compiled_settings)
```

`CompiledH3Settings` 使用强类型字段，不再传递任意字符串：

```text
production_tier: PREVIEW | BALANCED | PRODUCTION
width: int
height: int
frames: int
sigma_points: int
acceleration: OFF | TURBO_LORA
lora_name: str | null
lora_strength: float | null
native_audio: bool
```

### 9.2 Turbo LoRA

当 `acceleration=OFF`：

- UNET loader 输出直接进入现有 scheduler/guider 链路。
- 工作流中不得出现 LoRA 节点。

当 `acceleration=TURBO_LORA`：

- Profile bundle 必须包含 `purpose=TURBO_ACCELERATION` 的可用 LoRA artifact。
- 编译前验证 artifact 路径位于受信运行时模型目录且文件哈希/状态有效。
- 在 UNET loader 之后插入 ComfyUI `LoraLoaderModelOnly`，设置 `lora_name` 与 `strength_model`。
- 后续 scheduler/guider 必须使用 LoRA 节点输出的 model。
- 节点类型和输入键必须以本项目锁定的 ComfyUI 版本集成测试为准；若节点不可用，预检返回阻断错误，UI 不提供可选开关。

### 9.3 步数、档位与采样

- `sigma_points` 必须成为实际采样步数来源，并写入图快照。
- `production_tier` 解析为尺寸、帧数和已经实现绑定的参数。
- 未绑定的 `denoise/cfg/sampler/scheduler` 不出现在 `override_schema` 和“有效配置”中。
- 如果未来加入这些字段，必须同时补充图节点断言测试后才能发布 schema。

### 9.4 原生音频

当 `native_audio=true`：

- 加载 Profile 中的 Audio VAE。
- 保留音频 latent 解码及最终视频合成音轨链路。

当 `native_audio=false`：

- 不加载 Audio VAE，不执行音频解码。
- 最终输出使用已验证的无音轨视频合成图。
- 如当前 ComfyUI 节点不支持可选音频，先增加独立的无音频输出节点路径和集成测试；验证通过前 UI 将该字段锁定为 `true`，不得提供无效开关。

### 9.5 运行证据

每次 H3 Job 保存：

- 编译后的设置。
- 最终工作流图 SHA-256。
- 实际模型与 LoRA artifact ID/hash。
- 是否存在 LoRA 节点、是否存在 Audio VAE 节点。
- ComfyUI prompt ID 与输出证据。

这使“LoRA 已开启”“原生音频已关闭”等状态可以由图结构和证据验证，而不是依赖 UI 文案。

---

## 10. 密钥安全与审计

### 10.1 安全边界

- API 仅绑定 `127.0.0.1`/`::1`，Reveal 请求需要本地实例令牌。
- Credential Manager 是明文凭据的唯一持久化位置；SQLite 只保存引用。
- API access log 对 Reveal endpoint 不记录响应体，所有请求体日志中递归删除 secret/token/key 字段。
- 前端错误上报、React Query Devtools 和 Redux/状态调试器不得包含明文。
- Clipboard 无法由浏览器可靠自动清除，复制时明确提示“密钥已复制，请注意剪贴板安全”。
- 窗口失焦即隐藏是隐私措施，不替代操作系统账户安全。

### 10.2 审计事件

记录以下元数据事件：

```text
PROVIDER_CONNECTION_CREATED
PROVIDER_CONNECTION_UPDATED
PROVIDER_CONNECTION_DISABLED
PROVIDER_CONNECTION_DELETED
PROVIDER_SECRET_REPLACED
PROVIDER_SECRET_DELETED
PROVIDER_SECRET_REVEALED
PROVIDER_CONNECTION_PROBED
```

审计记录只包含 connection ID、操作者、时间、结果和安全错误码。禁止包含 secret、请求头、响应正文、完整外部错误堆栈。

---

## 11. 具体代码影响面

### 11.1 Web 页面

| 文件 | 改造内容 |
|---|---|
| `apps/web/src/pages/ModelsPage.tsx` | 新视图导航、旧 query 兼容、连接和运行时分区 |
| `apps/web/src/pages/AudioPage.tsx` | 三类音频配置摘要 |
| `apps/web/src/features/profiles/LocalLLMConfigurationPanel.tsx` | 拆分/下沉为通用连接组件；移除固定 DeepSeek 逻辑 |
| `apps/web/src/features/preferences-v2/GenerationPreferencePanel.tsx` | 删除自由 JSON，改结构化 schema 表单 |
| `apps/web/src/features/profiles/ProfileContractEditors.tsx` | 增加真实 bundle/override schema 只读与编辑支持 |
| `apps/web/src/features/profiles/ProfileConfigurationPanel.tsx` | Profile Version 详情与组件列表 |
| `apps/web/src/features/generation/GenerationWorkbench.tsx` | 统一选择器、有效配置预览、提交指纹 |
| `apps/web/src/features/generation/GenerationControlPanel.tsx` | H3 参数控件改由 schema 驱动 |
| `apps/web/src/features/production-settings-v2/ProductionSettingsOverview.tsx` | Profile/参数/来源摘要 |
| `apps/web/src/features/projects/ProjectCreateWizard.tsx` | 模型详情与创建前确认 |
| `apps/web/src/features/projects/OneSentenceVideoWizard.tsx` | 删除重复密钥输入，选择 Provider Connection |
| `apps/web/src/features/status/DialogueGovernanceActions.tsx` | TTS Profile 统一选择器 |
| `apps/web/src/features/status/DialogueTTSPanel.tsx` | 候选的执行配置详情 |
| `apps/web/src/features/production/PromptTemplatePanel.tsx` | Profile 详情入口 |
| `apps/web/src/features/director-v2/DirectorIntentEditor.tsx` | Profile 详情入口 |
| `apps/web/src/app/routeRegistry.ts` | `/models` 新 query 视图注册/兼容 |
| `apps/web/src/generated/api.ts` | 重新生成连接、Profile 执行详情和预检类型 |

### 11.2 API 与领域服务

| 文件/目录 | 改造内容 |
|---|---|
| `apps/api/local_drama/infrastructure/windows_credentials.py` | 通用 credential target/read/write/delete，保留旧 DeepSeek 迁移兼容 |
| `apps/api/local_drama/application/profiles.py` | 返回 execution detail；v1 bundle 归一化；v2 写入 |
| `apps/api/local_drama/application/h3_workflows.py` | 强类型设置编译、真实 LoRA 与原生音频图分支 |
| `apps/api/local_drama/config.py` | 旧环境变量来源映射到默认 Connection，不再作为唯一 LLM key |
| API routes/schemas | Provider Connection CRUD/Reveal/Probe；Effective Configuration Resolve |
| Preference command/query service | schema 校验、分层合并和来源输出 |
| Generation/Variant/Job service | 二次解析、指纹校验和不可变快照 |
| Persistence/Alembic | `provider_connections` 与必要索引/约束 |
| `scripts/generate_client.py` | OpenAPI 变更后重新生成 TS 类型 |

具体新增模块名应遵循当前 API 的分层命名约定；实现前以相邻 feature 的 router/service/repository 组织方式为模板，不把所有逻辑写入路由函数。

---

## 12. 实施工作包

### WP-1：Profile 执行详情只读链路

- 扩展 Profile Version schema/service。
- 归一化旧 `artifact_ids` bundle。
- Web 实现 `ModelInspectorDrawer` 和组件列表。
- 先接入 `/models` 专家页和生成偏好页。

完成证据：H3、LLM、SAPI TTS 三类 Profile 均能显示正确且不同的组件结构。

### WP-2：通用 Provider Connection 与密钥管理

- 数据迁移、Repository、Credential Manager 通用化。
- CRUD/Reveal/Replace/Delete/Probe API。
- `CredentialManager` 与 60 秒 Reveal 生命周期。
- 迁移现有 DeepSeek 配置；一句话向导改为引用 Connection。

完成证据：数据库无明文；Reveal 响应 no-store；页面失焦明文消失；旧 DeepSeek 配置无损迁移。

### WP-3：Override Schema 与结构化偏好

- 定义 Profile override schema。
- 实现后端统一验证器和 Preference 合并。
- 替换自由 JSON 编辑器。
- 接入项目生产设置。

完成证据：未知/越界字段被拒绝；页面仅显示 Profile 允许的字段；来源标签正确。

### WP-4：有效配置预检与提交一致性

- 实现 resolve API。
- 生成工作台接入预览。
- Variant/Job 提交二次解析和 `409 CONFIGURATION_CHANGED`。
- 保存不可变配置快照与指纹。

完成证据：预览内容与任务输入快照逐字段一致；修改 Preference 后旧预检指纹不能直接提交。

### WP-5：H3 参数真实工作流绑定

- 抽取 H3 设置编译器。
- 实现 `OFF/TURBO_LORA` 图分支和强度绑定。
- 实现或锁定 `native_audio` 图分支。
- 修正档位摘要，只显示已绑定参数。

完成证据：工作流图断言、ComfyUI 集成运行和输出证据均能区分 ON/OFF。

### WP-6：全站选择器与音频语义统一

- 替换项目创建、工作台、导演桌、Prompt 模板、TTS 等剩余下拉框。
- 音频页增加三链路摘要。
- 清理重复选择与重复密钥输入。

完成证据：全仓搜索业务 Profile `<select>` 后，只保留确有理由的原生选择器，并有代码注释说明。

### 依赖关系

```text
WP-1 ─┬─→ WP-3 ─→ WP-4 ─→ WP-5
      └─→ WP-6
WP-2 ─────────────→ WP-4/WP-6
```

推荐按 WP-1 → WP-2 → WP-3 → WP-4 → WP-5 → WP-6 交付。WP-1 与 WP-2 可并行开发，但合并时分别完成迁移和契约测试。

---

## 13. 测试方案

### 13.1 单元测试

- Secret mask 对短/长/空密钥均不泄露完整值。
- Credential target 只由合法 connection ID 生成，拒绝路径分隔符等非法字符。
- Override schema 对类型、范围、enum、作用域、依赖字段和未知字段校验。
- Preference 五层合并与 `setting_sources` 正确。
- v1 bundle 能归一化为组件角色；缺失 artifact 返回明确状态而非 500。
- 有效配置指纹对字段顺序稳定，对真实参数变化敏感。

### 13.2 API 契约测试

- Connection 列表永不包含 `secret/api_key/token` 明文字段。
- Reveal 需要本地实例令牌并返回 no-store header。
- Reveal/Probe 的日志与审计 fixture 中无密钥。
- Preference 非法 settings 返回稳定 `422` 错误码和字段路径。
- 预检与任务提交解析一致；漂移返回 `409`。
- 已发布 Profile 被连接引用时删除/停用规则正确。

### 13.3 前端测试

- Reveal 60 秒、立即隐藏、失焦、切换连接和卸载均清除明文。
- 明文不进入 Query Cache；Devtools 状态快照无 secret。
- 选择器摘要、详情抽屉、折叠区域和键盘焦点正确。
- `visible_if`、恢复继承、字段错误和预检防抖正确。
- 屏幕宽度 1440/1280/1024 下无关键操作溢出；1024 下抽屉可滚动。

### 13.4 H3 图与集成测试

- OFF 图不含 `LoraLoaderModelOnly`；TURBO_LORA 图恰含一个且下游引用其 model 输出。
- LoRA artifact 缺失、哈希无效或节点不可用时预检阻断。
- `sigma_points` 改变会改变实际采样节点输入和配置指纹。
- `native_audio=false` 图不加载/解码 Audio VAE；若运行时暂不支持则 schema 锁定为 true。
- Preview/Balanced/Production 的尺寸、帧数与实际图输入一致。
- 至少完成一次短样本 OFF/TURBO_LORA、audio on/off 的真实 ComfyUI 冒烟运行并保存证据。

### 13.5 回归测试

- 旧 Profile、旧 DeepSeek key、旧 Preference 和旧冻结 Job 仍可读取。
- 旧 Job 重放使用原快照，不受新默认值影响。
- LLM 聊天、视频生成、TTS 提交主链路不因通用选择器改造回归。

---

## 14. 验收标准

### 14.1 密钥

- [x] 用户可显式查看、复制、替换、删除和测试远端密钥。
- [x] 明文默认不可见，60 秒或页面失焦后自动清除。
- [x] SQLite、日志、审计、URL、Query Cache 和错误上报中不存在明文。
- [x] 多个 Provider Connection 可独立配置，不再只有 DeepSeek 固定目标。

### 14.2 模型详情

- [x] 所有主要 Profile 选择位置都有一行摘要和“查看详情”。
- [x] H3 详情能显示主模型、编码器、Video VAE、Audio VAE 和可选 LoRA。
- [x] LLM 详情能显示 Provider Connection、协议、Base URL 和模型名。
- [x] TTS 详情能显示运行时/Provider、Voice 和输出能力。
- [x] 专家模式能查看 artifact path/hash 与 Profile/workflow/model bundle 指纹。

### 14.3 参数与执行一致性

- [x] 高级设置不再是任意 JSON。
- [x] 每个可编辑字段都有类型、范围、作用域和运行时绑定。
- [x] 提交前可查看服务器解析的最终有效配置及来源。
- [x] LoRA 开关会真实改变工作流图；不能生效时 UI 不提供该选项。
- [x] 档位、步数、原生音频的 UI 值与最终工作流图一致。
- [x] Job 保存不可变配置和指纹，旧 Job 不受新设置影响。

### 14.4 音频

- [x] 用户能区分视频原生音频、对白 TTS、音效/配乐配置。
- [x] 每条链路都能查看实际 Profile/组件与参数快照。
- [x] `native_audio` 只控制视频工作流的 Audio VAE，不影响 TTS 或 SFX/BGM。

---

## 15. 发布与兼容策略

1. **阶段 A：只读上线**。先发布 Profile 执行详情和抽屉，不改变任务提交。
2. **阶段 B：连接迁移**。发布通用 Connection 与旧 DeepSeek 兼容读取，观察迁移结果。
3. **阶段 C：结构化设置**。对新编辑启用 override schema；旧自由 JSON 只读展示并提供迁移提示，能无损映射的字段自动转换。
4. **阶段 D：预检强制**。生成提交要求有效配置指纹。
5. **阶段 E：H3 新参数启用**。仅在工作流图测试和真实运行证据通过后，在 schema 中开放 LoRA/native_audio 等字段。
6. **阶段 F：移除旧入口**。删除一句话向导的独立密钥输入和剩余重复 Profile 选择逻辑。

建议使用能力开关分离：

```text
provider_connections_v1
profile_execution_details_v1
structured_profile_overrides_v1
effective_configuration_preflight_v1
h3_turbo_lora_v1
h3_native_audio_toggle_v1
```

开关用于渐进启用，不用于长期维护两套真相源。

---

## 16. Definition of Done

本改造只有在以下条件全部满足时才算完成：

- 页面、API、数据库和工作流图使用同一套 Profile/参数语义。
- 用户可以看到需要看到的密钥和模型详情，但明文不会被持久化或泄露到非必要系统。
- 每个可配置参数都能通过自动化测试证明进入了实际执行图或任务逻辑。
- 生成前预览、Job 输入快照和执行证据三者一致。
- H3、LLM、TTS 三类代表性 Profile 的端到端路径通过。
- 旧配置和冻结任务兼容，迁移失败可安全回退。
- 文档、OpenAPI、生成的 TypeScript 类型和 UI 文案同步更新。

---

## 17. 2026-08-25 实现状态与验收证据

本节记录本设计文档对应的首轮代码实现，避免把设计项与运行时证据混写。

### 17.1 已实现

- **远端连接与密钥**：新增 `provider_connections` 元数据表、Provider Connection CRUD、Windows Credential Manager 受控读写、显式 reveal/copy/replace/delete/probe 生命周期；数据库、日志、审计和 API 响应均不保存密钥明文。旧 DeepSeek 目标保留兼容读取。
- **模型执行详情**：Profile 列表和版本详情返回 execution detail；Models/Profile、生成工作台、提示词模板、导演运镜、项目创建、角色三视图/表情/细节、一句话成片和 TTS Profile 选择器均提供统一“查看执行详情”入口，展示 runtime、workflow、主模型、编码器、Video/Audio VAE、LoRA、Provider Connection、Base URL、model name、artifact path/hash 和指纹。
- **结构化高级参数**：override schema 已进入 API/UI；档位、sigma points、加速、LoRA strength、原生音频和 take count 均有类型、范围、作用域、依赖及运行时绑定。H3 档位兼容旧 `PREVIEW/BALANCED` 别名并规范化为 `FAST/DRAFT`。
- **有效配置与冻结**：新增 effective-configuration 预检，返回最终值、来源、阻断/警告、组件和 fingerprint；Variant plan 强制校验预期 fingerprint，Job input snapshot 保存不可变 effective configuration，Comfy 编译后继续保存 compiled workflow hash、runtime override evidence 和 prompt id。
- **H3 图绑定**：`TURBO_LORA` 插入/复用 `LoraLoaderModelOnly` 并重连模型链；sigma points 写入 scheduler；native audio 关闭时移除 Audio VAE/decode/mix 路径；Ref2V 在当前 Audio VAE 约束下 fail closed，不提供无效开关。
- **接口同步**：OpenAPI、TypeScript generated client、数据库 release/migration contract 和 UI 文案已同步。

### 17.2 自动化验收

截至 2026-08-25，以下命令通过：

```text
apps/api: pytest -q --disable-warnings       (exit code 0, 全量回归)
apps/web: npm test                            (130 files / 490 tests)
apps/web: npm run build                       (tsc + Vite + bundle budget)
apps/api: ruff check <本轮修改的后端文件>
```

定向测试覆盖 Provider Connection、Credential Manager target 路由、Profile execution detail、effective configuration、H3 runtime overrides、Comfy job snapshot、Variant stale fingerprint、OpenAPI/release contract 和 Web reveal/blur、最终配置预览。

### 17.3 真实运行证据

2026-08-25 已在本机 loopback ComfyUI 0.33.1（队列为空、模型布局 PASS、无公网连接）完成两条隔离平台 Job。两条 Job 都使用临时 SQLite、临时项目和本地 sandbox，均经历 Job → Comfy prompt → VERIFIED artifact → MediaVersion → machine QC PASS → review/selection；正式生产数据库未被修改。

| 配置 | Prompt | 执行图证据 | 输出证据 |
|---|---|---|---|
| `TURBO_LORA` + `native_audio=true` + sigma 2 | `45c888c9-1de1-4809-8c47-fa471f261b01` | compiled `d1dff48a…ca32`；`LoraLoaderModelOnly` 使用 `minimax_h3_turbo_v4_step600_ema.safetensors`、strength `0.6`；BasicScheduler steps `2` | [JSON evidence](F:/AI_Projects/h3/local_drama_studio/docs/evidence/h3-runtime-config-turbo-audio-on-2026-08-25.json)，`SHA-256=ab2582aac45f21e3dd50d55a501c09e7d16504595e52fba83d5ac295e08afac0`，H.264 480×832/24fps/5.167s + AAC |
| `OFF` + `native_audio=false` + sigma 2 | `32888650-46c8-49e4-97c9-34e52a63223d` | compiled `998b50b…3d6e`；无 LoRA 节点、无 Audio VAE/decode，CreateVideo 无 audio 输入；BasicScheduler steps `2` | [JSON evidence](F:/AI_Projects/h3/local_drama_studio/docs/evidence/h3-runtime-config-off-audio-off-2026-08-25.json)，`SHA-256=fb5ba8ae20fbfcad386e4bfd29334de18e08623e0b8ca86a649281b417481d94`，H.264 480×832/24fps/5.167s、无音频流 |

稳定保留的媒体文件为 [Turbo audio-on MP4](F:/AI_Projects/h3/local_drama_studio/docs/evidence/h3-runtime-config-turbo-audio-on-2026-08-25.mp4) 和 [OFF audio-off MP4](F:/AI_Projects/h3/local_drama_studio/docs/evidence/h3-runtime-config-off-audio-off-2026-08-25.mp4)。这两条证据证明 UI/预检值进入了实际 Comfy 图和可解码输出；仍不等同于整集交付包/下载审计。

### 17.4 可复现运行步骤

1. 确认 ComfyUI 进程使用与本项目 manifest 相符的模型目录、节点版本和可写 input/output sandbox。
2. 使用 `scripts/h3_comfy_job_uat.py` 的 `--acceleration`、`--native-audio`、`--sigma-points` 和 `--artifact-output` 参数，在隔离 Job 中复跑配置组合；不得使用正式生产数据库。
3. 将每次运行的 prompt id、effective fingerprint、compiled workflow hash、输出文件 SHA-256、ffprobe 和 QC 结果写入 `docs/evidence/`，由发布门禁复核。
