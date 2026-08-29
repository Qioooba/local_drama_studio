# LocalDramaStudio 模型平台重构与 Windows Server 部署设计

- 文档状态：目标架构与开发实施基线
- 日期：2026-08-29
- 适用范围：模型发现、登记、存储、下载、运行时、执行配置、业务选择、Windows Server 部署
- 实施策略：建立完整 V2 领域与页面后一次性切换，不在现有多套模型页面和脚本上继续堆补丁

## 0. 决策摘要

LocalDramaStudio 不应继续把“模型”理解为一个下拉框、一条磁盘路径或一个 Ollama tag。目标是建立一个统一模型平台，把以下对象明确分开：

1. **模型发布版**：Qwen3.8 27B、Qwen3 Embedding 8B、VoxCPM2、MiniMax H3 等是什么。
2. **模型组件**：主权重、Text Encoder、VAE、LoRA、Tokenizer、Processor、辅助网络分别是什么。
3. **模型安装**：某个发布版实际安装在哪台服务器、哪个模型库、由哪个运行时访问。
4. **运行时**：Ollama、ComfyUI、PyTorch 子进程、Windows SAPI、FFmpeg 或远程 OpenAI-compatible 服务如何执行。
5. **能力**：故事理解、文生图、图生视频、TTS、ASR、强制对齐、唇形同步、Embedding 等能做什么。
6. **执行 Profile**：一个能力通过哪一版运行时、哪组模型组件、哪版工作流和哪套参数契约执行。
7. **业务分配**：系统、项目、分集、镜头或单次任务应选哪个已发布 Profile。

最终业务链路必须统一为：

    发现 → 识别 → 归类 → 完整性校验 → 运行时兼容验证
    → 能力冒烟 → 发布不可变 Profile → 业务分配
    → 参数解析 → 任务快照 → 执行 → 产物与证据

核心决策如下。

| 决策 | 结论 |
|---|---|
| 模型主页面 | 重构为“系统中心 / 模型平台”，不再把 Ollama、ComfyUI、本机文件和远端连接拼在一张杂乱长页里 |
| 默认浏览方式 | 先按创作能力分组，再按运行时、模态、安装状态筛选；运行时不是唯一分类 |
| ComfyUI | 管理图像、视频、音乐等图工作流及其多组件模型包，不承担所有本机模型 |
| Ollama | 只管理 Ollama 可原生加载的语言、视觉语言和 Embedding tag；是否放 Ollama 由执行方式和资源策略决定，不由“本地模型”四个字决定 |
| PyTorch | 用版本化本机 Adapter 进程执行需要专用 Python 代码或目录模型的 Embedding、VoxCPM、ASR、对齐、LatentSync 等 |
| SAPI/FFmpeg | 作为 OS 原生能力和工具运行时管理，不伪装成模型权重 |
| 模型路径 | 数据库保存 model_library_id + relative_path 或 runtime native id，绝对路径仅存在节点本地绑定中 |
| 模型发现 | 各运行时由 Adapter 自动扫描；扫描只产生观察和候选，不自动发布、不静默改变现有 Profile |
| 下载 | 下载、校验、暂存、安装、注册、冒烟是持久化任务；支持断点续传、哈希、许可证确认和原子提升 |
| 参数 | Capability、Parameter、Adapter Binding、Profile Policy、Scope Override、Run Snapshot 六层分离 |
| 页面选择 | 业务页选择“能力/Profile”，不选择磁盘路径、Comfy 节点、Python 环境或 Ollama endpoint |
| Windows 服务 | 运行时、模型、凭据必须在最终服务身份下安装和验证；禁止依赖开发机用户目录、源码脚本和硬编码 E/F 盘 |
| 切换方式 | 新建 V2 模块、迁移数据、影子比对，验收后一次切换路由与 Worker，再删除旧写路径 |

必须在重构中解决的 P0 问题：

1. apps/api/local_drama/application/profiles.py 当前先收集 manifest 全部 artifact_ids，再把同一全量列表写入每个 capability Profile。Qwen Image、MiniMax、ACE-Step 等组件会错误混入彼此 Profile。
2. apps/api/local_drama/application/local_llm.py 和 dialogue.py 使用 ON CONFLICT DO UPDATE 原地覆盖 ExecutionProfileVersion 1；Published Profile 并不真正不可变。
3. Ollama Profile 稳定身份没有完整包含 capability；同一模型承担故事解析和视觉质检时可能覆盖同一 Profile。
4. Capability 存在多套词表，主枚举没有 EMBEDDING 和 RERANK，Job Type 又使用 ASR_ALIGNMENT、EMBEDDING_INDEX、LIPSYNC_GENERATION 等另一套名字。
5. 当前 PyTorch 模型虽然已登记和完成脚本级验证，但 Worker 没有对应 handler，不能显示为“可执行”。
6. Windows 发布配置仍引用开发机 E/F 盘、源码脚本和用户态运行时；服务身份、发行物和模型目录没有形成可复制部署合同。
7. 系统模型页读取全局 registry，但许可证证据写接口要求 project_id；旧项目模型路由又已重定向，形成项目证据入口缺失。
8. 参数可能来自 parameter_schema_json、model_bundle.override_schema、模型 bundle defaults、Comfy 节点默认值和页面默认值，缺少唯一真相。

## 1. 审计范围与事实基线

### 1.1 审计范围

本设计基于以下本机代码和运行状态：

- config/model-lock.json
- config/config.json
- docs/evidence/local-ai-service-registration-20260829.json
- apps/api/local_drama/application/profiles.py
- apps/api/local_drama/application/local_llm.py
- apps/api/local_drama/application/model_compatibility.py
- apps/api/local_drama/application/generation_model_catalog.py
- apps/api/local_drama/application/effective_configuration.py
- apps/api/local_drama/application/job_resources.py
- apps/api/local_drama/application/worker.py
- apps/api/local_drama/infrastructure/local_ai_subprocess.py
- apps/web/src/pages/ModelsPage.tsx
- apps/web/src/pages/ProjectCapabilitiesPage.tsx
- apps/web/src/features/quick-create
- docs/deployment/windows.md
- docs/plan/windows-first-cross-platform-runtime-release-architecture-2026-08-26.md

现有系统有值得保留的基础：持久化 Job/Attempt/Artifact、Worker Session、资源租约、Profile/Workflow 版本、生成偏好继承、GPU runtime coordinator、项目媒体谱系和审计事件。问题不是“全部重写”，而是模型平台领域没有收敛。

### 1.2 当前本机模型清单

config/model-lock.json 当前声明 12 个逻辑模型项。

| 逻辑模型 | 当前运行时 | 主要组件 | 目标能力 |
|---|---|---|---|
| qwen-image-2512-q5-k-m | ComfyUI | GGUF diffusion、Qwen 2.5 VL Text Encoder、Image VAE | 文生图、概念图、角色图、场景图 |
| qwen-image-edit-2511-q5-k-m | ComfyUI | GGUF diffusion、共享 Text Encoder、共享 VAE | 图像编辑、表情、细节、多视图 |
| qwen-image-edit-2511-multiple-angles-lora | ComfyUI | LoRA | 多角度图像编辑的可选组件 |
| minimax-h3-fl2va | ComfyUI | DiT、Qwen3-VL 32B encoder、Video VAE、Audio VAE | 首帧/图生视频和带音频视频 |
| minimax-h3-ref2va | ComfyUI | DiT、共享 encoder、Video VAE、Audio VAE、Turbo LoRA | 参考图/参考视频生成 |
| ace-step-1.5-xl-sft | ComfyUI | 主模型、两个 Text Encoder、VAE | 音乐/BGM 生成 |
| qwen3-embedding-8b | PyTorch | 4 个 Safetensors shard | 文本向量、相似度、RAG 索引 |
| voxcpm2 | PyTorch | model.safetensors、Audio VAE | TTS、Voice Clone |
| qwen3-asr-1.7b-hf | PyTorch | 目录模型、Tokenizer | ASR |
| qwen3-forced-aligner-0.6b-hf | PyTorch | 目录模型、Tokenizer | 字词级强制对齐 |
| latentsync-1.6 | PyTorch | UNet、SyncNet、Whisper、多个辅助网络 | 唇形同步 |
| qwen3.8-27b | Ollama | Ollama tag qwen3.8:27b | 故事理解、策划、提示词、结构化文本、部分视觉理解 |

当前 Ollama 的 /api/tags 实际只返回 qwen3.8:27b，所以页面只显示一个 Ollama 模型是对运行时事实的真实反映，不是前端把其他模型隐藏了。ComfyUI 和 PyTorch 权重不会自动出现在 Ollama 列表里，因为它们不属于 Ollama registry。

### 1.3 当前运行状态不是同一个“可用”

docs/evidence/local-ai-service-registration-20260829.json 证明 PyTorch 模型已登记，并创建了以下候选 Profile：

- local-suite-voxcpm2-tts
- local-suite-voxcpm2-voice-clone
- local-suite-qwen3-asr
- local-suite-qwen3-alignment
- local-suite-latentsync

数据库审计显示这些仍为 CANDIDATE_UNVERIFIED，且没有 qwen3-embedding-8b 的正式 Capability/Profile。应用层的 LocalAISubprocessAdapter 已具备 embed、synthesize、transcribe、align、run_lipsync 方法，但 apps/api/local_drama/application/worker.py 没有 ASR_ALIGNMENT、EMBEDDING_INDEX、LIPSYNC_GENERATION、RAG_RETRIEVAL、VOICE_CLONE handler。TTS handler 当前仍只接受 WINDOWS_SAPI_LOCAL。

因此页面必须分开显示：

| 维度 | 含义 |
|---|---|
| 已发现 | 扫描器看到了 tag、文件、目录或 OS 资源 |
| 已登记 | 已形成稳定 ModelRelease/Installation 记录 |
| 完整性通过 | 组件、大小、哈希、结构和许可证要求满足 |
| 运行时兼容 | 对应 Adapter 能识别并加载 |
| 能力验证 | 对特定 Capability 冒烟成功 |
| 已发布 | 存在不可变 Published ProfileVersion |
| 当前可执行 | Profile 已发布，安装仍存在，运行时可达，资源策略允许 |

“已登记”绝不能再用绿色“可用”表达。

### 1.4 当前数据和接口的结构性冲突

1. models/model_versions 与 model_artifacts 并存；后者只能较自然地表示单文件。
2. local_runtimes 与 runtime_environments/runtime_environment_versions/runtime_instances 并存。
3. providers/provider_versions 与 provider_connections 并存。
4. project_profile_bindings 与 generation_preference_sets/versions 并存。
5. ModelCompatibilityService 使用 T2V/I2V/VIDEO/IMAGE/AUDIO/TTS/TEXT 旧词表，domain.capabilities 使用另一套 28 项能力。
6. generation_model_catalog 只覆盖故事解析、图像、视频，并以 workflow_version_id 是否存在判断 executable，错误排除无需 Comfy workflow 的 Ollama、TTS、ASR 等能力。
7. 文件系统扫描以单文件为中心，可能把 shard、VAE 和 LoRA 都当成独立模型，还会同步哈希大文件。
8. Comfy discovery 当前主要找 ComfyUI/Python 安装，不完整发现模型包；PyTorch 注册脚本直接写 SQLite 并硬编码盘符。

### 1.5 当前页面的结构性问题

ModelsPage 当前只有“能力目录”和“模型与服务”两个主视图，后者按“远端服务、智能理解模型、本机权重”纵向拼接。它把不同层次对象放在一起：

- Provider connection
- Ollama tag
- 本机模型文件
- Execution Profile
- Workflow contract
- 兼容报告

“专家配置”又以全屏 Dialog 承载 Profile 与工作流，不利于深链接、浏览器返回、审计定位和长期扩展。

项目页面只配置生成偏好是正确方向，但项目许可证证据入口发生断裂：

- 系统页 GET /model-registry 取得全局 snapshot；
- ModelCompatibilityPanel 只有传 projectId 才显示证据表单；
- POST 许可证证据只存在 /projects/{project_id}/model-license-evidence；
- /projects/{project_id}/models 已重定向至项目生成偏好。

目标架构必须把“系统模型许可证元数据”和“项目使用/授权证据”拆成两个清晰页面和 API。

## 2. 目标边界与不可违反的原则

### 2.1 目标边界

本轮重构覆盖：

- Windows Server 上的本机模型库和外部模型库
- Ollama、ComfyUI、PyTorch、SAPI、FFmpeg、远程 HTTP Provider
- 自动发现、导入、下载、安装、验证、发布和诊断
- 模型与组件的统一目录
- Profile 与参数合同
- 系统、项目、分集、镜头、角色和单次任务的选择规则
- 所有业务页面的模型选择边界
- Worker、GPU 调度和执行快照

本轮不把以下目标混在一起：

- 不开发公有模型市场或账户计费。
- 不自动接受第三方许可证。
- 不让 LAN 浏览器任意浏览服务器文件系统。
- 不让扫描结果自动成为生产能力。
- 不承诺不同模型/量化之间输出完全一致。
- 不把所有运行时强行改造成 HTTP 服务。

### 2.2 不可违反的原则

1. **身份与位置分离**：SHA-256、upstream id、release revision 是内容身份；Windows 绝对路径不是。
2. **能力与运行时分离**：Embedding 是能力，Ollama/PyTorch 是执行方式。
3. **组件与模型分离**：VAE、LoRA、Tokenizer 是组件角色，不能都显示成独立“生成模型”。
4. **声明与观察分离**：manifest/model-lock 表达期望，DiscoveryObservation 表达实际机器状态。
5. **发布与在线分离**：Published 是不可变配置状态；READY/UNREACHABLE 是实时运行状态。
6. **Published 不可变**：模型、能力、运行时、工作流、参数合同、Adapter binding 任一改变都派生新版本。
7. **扫描只读**：扫描不得删除文件、下载内容、启动大模型或修改已发布 Profile。
8. **下载显式**：用户或管理员确认来源、许可证、容量和目标后才下载。
9. **业务页不接触基础设施细节**：不得传绝对路径、endpoint、Python 路径、Comfy node id 或 secret。
10. **任务可重现**：每个 Job 冻结精确 Profile、模型 digest、运行时版本、参数值、参数来源、输入和资源策略。
11. **失败关闭**：能力不明确、组件不完整、版本漂移、路径越界或证据不匹配时不得静默回退。
12. **服务身份一致**：发现、安装、验证、执行使用同一个最终 Windows 服务身份和同一份配置。
13. **一个写模型**：V2 上线后只有 Model Platform application service 能修改模型平台数据；脚本不得直写 SQLite。
14. **一次切换、可回滚**：开发可以分工作包，生产切换必须是一次明确 cutover，不长期维持双写。

## 3. 统一分类体系

一个模型不能只用“在 Ollama / 在 ComfyUI / 在本机”单维分类。目标页面和 API 使用六个正交维度。

### 3.1 维度一：运行方式 Runtime Kind

| Runtime Kind | 说明 | 当前示例 |
|---|---|---|
| OLLAMA | 由 Ollama registry 管理，通过本机 API 调用 | qwen3.8:27b |
| COMFYUI | 由 Comfy workflow 组合多组件执行 | Qwen Image、MiniMax H3、ACE-Step |
| PYTORCH_PROCESS | 由版本化 Python Adapter 子进程直接加载 | Qwen Embedding、VoxCPM2、Qwen ASR、LatentSync |
| OS_NATIVE | 操作系统自带资源或能力 | Windows SAPI voice |
| TOOL_PROCESS | 非模型媒体工具 | FFmpeg/ffprobe |
| LOCAL_HTTP | 其他本机或受控局域网模型服务 | 将来 vLLM、TGI、第三方本地服务 |
| REMOTE_HTTP | 明确允许的数据出境 Provider | OpenAI-compatible、DeepSeek 等 |

运行时只说明“怎样执行”，不说明“模型做什么”。

### 3.2 维度二：能力 Capability

唯一 CapabilityDefinition 词表至少包括：

- 文本/理解：LLM_STORY_PARSE、LLM_EPISODE_PLAN、LLM_STORYBOARD、LLM_PROMPT_REWRITE
- 检索：EMBEDDING_TEXT、EMBEDDING_MULTIMODAL、RERANK
- 图像：IMAGE_CONCEPT、IMAGE_CHARACTER、IMAGE_SCENE、IMAGE_EDIT、IMAGE_MULTI_VIEW、IMAGE_EXPRESSION
- 视频：VIDEO_T2V、VIDEO_I2V、VIDEO_FIRST_FRAME、VIDEO_FIRST_LAST_FRAME、VIDEO_REFERENCE、VIDEO_MOTION_CONTROL
- 音频：TTS、VOICE_CLONE、AUDIO_SFX、AUDIO_MUSIC、ASR、AUDIO_ALIGNMENT
- 后期：LIPSYNC、FRAME_EXTRACT、UPSCALE_IMAGE、UPSCALE_VIDEO、POST_PROCESS
- 质检：QC_VISUAL、QC_FACE、QC_IDENTITY、QC_CONTINUITY、QC_AUDIO

Job Type 另有自己的词表，例如 EMBEDDING_INDEX、ASR_TRANSCRIBE、AUDIO_FORCE_ALIGN、LIPSYNC_GENERATION。Job Type 引用 CapabilityDefinition，但二者不共用字符串。

### 3.3 维度三：模态 Modality

- TEXT
- IMAGE
- VIDEO
- AUDIO
- MULTIMODAL
- VECTOR

输入模态与输出模态都要声明。例如 ASR 是 AUDIO → TEXT；Embedding 是 TEXT → VECTOR；I2V 是 IMAGE + TEXT → VIDEO。

### 3.4 维度四：模型组件角色 Component Role

- PRIMARY_MODEL
- DIFFUSION_MODEL
- TEXT_ENCODER
- VISION_ENCODER
- VAE_IMAGE
- VAE_VIDEO
- VAE_AUDIO
- TOKENIZER
- PROCESSOR
- LORA
- CONTROLNET
- UPSCALER
- SYNC_NETWORK
- AUXILIARY_MODEL
- CONFIG

组件角色不会进入普通创作者模型下拉框。它们只在模型详情、工作流依赖和诊断中出现。

### 3.5 维度五：来源与管理方式

- MANAGED_DOWNLOAD：平台下载并管理
- MANAGED_IMPORT：用户导入后由平台接管
- EXTERNAL_READ_ONLY：外部目录，只读索引
- RUNTIME_NATIVE：由 Ollama 等运行时自己管理
- OS_DISCOVERED：由 Windows 枚举
- REMOTE_OFFERING：远程 Provider 提供

### 3.6 维度六：生命周期与可用性

列表中同时展示五个状态列，不能压成一个 badge：

- Presence：DISCOVERED、PRESENT、MISSING、STALE
- Integrity：UNKNOWN、HASHING、VERIFIED、INCOMPLETE、CORRUPT、QUARANTINED
- Runtime：UNKNOWN、READY、BUSY、DEGRADED、UNREACHABLE
- Validation：NOT_RUN、COMPATIBLE、SMOKE_PASSED、FAILED
- Publication：NONE、DRAFT、CANDIDATE、PUBLISHED、DEPRECATED、RETIRED

最终“可执行”是服务端计算值：

    executable =
      publication == PUBLISHED
      and presence == PRESENT
      and integrity == VERIFIED
      and runtime in READY or BUSY
      and required adapter route is available
      and no blocking policy violation

## 4. 目标领域模型

### 4.1 聚合关系

    ComputeNode
      ├─ ModelLibrary
      │    └─ ModelArtifactLocation
      ├─ RuntimeInstallation
      │    └─ RuntimeInstallationVersion
      │         └─ RuntimeInstance
      └─ RuntimeModelInstallation
             ├─ ModelRelease
             │    ├─ ModelArtifact
             │    └─ ModelComponent
             └─ CapabilityOffering

    ExecutionProfile
      └─ ExecutionProfileVersion
           ├─ CapabilityDefinition
           ├─ RuntimeInstallationVersion
           ├─ RuntimeModelInstallation[]
           ├─ WorkflowVersion?
           ├─ ParameterContractVersion
           ├─ AdapterBindingContractVersion
           └─ ResourcePolicyVersion

    CapabilityAssignment
      → ExecutionProfileVersion

    Job
      → ResolvedExecutionSnapshot

### 4.2 核心实体

#### ComputeNode

代表一台可执行节点：

- id、hostname、node fingerprint
- OS/architecture
- CPU、RAM
- GPU 列表、VRAM、driver/CUDA
- Agent/Host version
- last_seen_at、health

首版只有一台 Windows Server 也必须建模，否则以后无法安全接远程 GPU 节点。

#### ModelLibrary

代表节点上的受控模型根：

- id、node_id、code、title
- kind：MANAGED、EXTERNAL、COMFYUI、OLLAMA、PYTORCH_CACHE
- root_path_local
- managed、read_only
- capacity/free bytes
- scan policy
- allowed extensions
- reparse point policy

业务 API 不返回 LAN 用户可利用的任意绝对路径；只返回 library code 和脱敏 display path。

#### ModelFamily 与 ModelRelease

ModelFamily 是稳定产品家族，如 Qwen3 Embedding、MiniMax H3。

ModelRelease 是可精确识别的版本：

- upstream provider/repository
- upstream revision/tag/digest
- architecture、parameter size、quantization、format
- input/output modality
- license metadata
- release fingerprint

同一模型的 FP16、Q5_K_M、不同 revision 是不同 ModelRelease。

#### ModelArtifact、ModelArtifactLocation 与 ModelComponent

ModelArtifact 是内容对象，使用 SHA-256、size、format 识别，可为 FILE、DIRECTORY_MANIFEST 或 RUNTIME_TAG_MANIFEST。

ModelArtifactLocation 保存 library_id + relative_path。一个 Artifact 可以有多个位置。

ModelComponent 连接 ModelRelease 与 Artifact，并声明 role、ordinal、required、shared。共享 Text Encoder/VAE 只保存一次，但可被多个 Release bundle 引用。

目录型 Hugging Face 模型必须由目录 manifest 描述，不再把四个 shard 当四个模型。

#### RuntimeInstallation、Version 与 Instance

RuntimeInstallation 是逻辑安装，如 server-main-ollama。

RuntimeInstallationVersion 是不可变配置：

- kind、adapter code/version
- transport
- endpoint 或 executable reference
- runtime root、Python environment reference
- launch contract
- environment allowlist
- owner mode：HOST_MANAGED 或 EXTERNAL
- credential reference
- fingerprint

RuntimeInstance 是实时状态：STOPPED、STARTING、READY、BUSY、STOPPING、DEGRADED、UNREACHABLE。

#### RuntimeModelInstallation

表示 ModelRelease 如何被某 RuntimeInstallationVersion 使用：

- Ollama：native id/tag + digest
- ComfyUI：组件到模型目录类别的绑定
- PyTorch：目录 locator + adapter loader id
- SAPI：voice token
- Remote：provider model id

这解决“模型是什么”和“在哪儿调用”混在一起的问题。

#### CapabilityDefinition 与 CapabilityOffering

CapabilityDefinition 是唯一能力词表，包含：

- code、family、title、description
- semantic input/output contract
- business surfaces
- parameter contract family
- resource class
- UI group/order

CapabilityOffering 表示某 RuntimeModelInstallation 通过某 Adapter 可提供该能力。它只能来自运行时原生元数据、声明式 manifest 和验证证据，不能只凭文件名猜。

#### ExecutionProfileVersion

每个版本冻结：

- 一个 capability_definition_id
- 一个 runtime_installation_version_id
- 一组精确 runtime_model_installation_id
- 可选 workflow_version_id
- parameter_contract_version_id
- adapter_binding_contract_version_id
- resource_policy_version_id
- defaults、locked policy
- execution_fingerprint

稳定 Profile code 格式：

    {runtime-kind}.{model-release-code}.{capability-code}.{route-variant}

例如：

    ollama.qwen3-8-27b.llm-story-parse.default
    ollama.qwen3-8-27b.qc-visual.vision
    comfy.qwen-image-2512.image-concept.t2i
    pytorch.voxcpm2.tts.default

同一模型不同 capability 永不覆盖。

#### DiscoveryRun、Observation、ValidationRun 与 Evidence

DiscoveryRun 记录一次扫描边界和完成度。

DiscoveryObservation 记录 native id、size、mtime、digest、metadata、first/last seen，不直接修改 Published Profile。

ValidationRun 分层记录：

- ARTIFACT_INTEGRITY
- RUNTIME_REACHABILITY
- MODEL_LOAD
- ADAPTER_COMPATIBILITY
- CAPABILITY_SMOKE
- PRODUCTION_LINEAGE

Evidence 是不可变结果，可关联 Job/Attempt/Artifact/MediaVersion。

#### CapabilityAssignment

替代 project_profile_bindings 和分散选择逻辑：

- scope_type：SYSTEM、PROJECT、EPISODE、SHOT、CHARACTER
- scope_id
- capability_definition_id
- resolution_mode：AUTO、EXPLICIT
- execution_profile_version_id
- override_set_version_id
- revision、created_by、reason

查询器只回答：

    给定上下文和 capability，当前解析到哪一个 Published ProfileVersion？

### 4.3 状态机

模型资产：

    DISCOVERED → HASHING → VERIFIED
                    ├→ INCOMPLETE
                    ├→ CORRUPT
                    └→ QUARANTINED
    VERIFIED → MISSING → VERIFIED

下载/安装：

    PLANNED → DOWNLOADING → VERIFYING → STAGED
      → INSTALLED → REGISTERED → SMOKE_TESTED

    任一步可进入 FAILED 或 CANCELLED；
    只有 STAGED 且哈希通过后才能原子提升。

Profile：

    DRAFT → CONTRACT_VALIDATED → SMOKE_VERIFIED
      → PUBLISHED → DEPRECATED → RETIRED

Published payload 不可变。发布动作不得修改 capability_json、model bundle 或 parameter schema；它只创建 Publication 记录或改变独立 lifecycle 状态，且 payload hash 必须与验证证据一致。

运行时实时状态与 Profile 状态完全独立。

## 5. 当前模型的目标归属

### 5.1 ComfyUI 模型包

| 模型包 | 目标分类 | 业务入口 | 备注 |
|---|---|---|---|
| Qwen Image 2512 | 图像 / ComfyUI | 快速创作、资产圣经、镜头首帧 | 多组件 bundle，Text Encoder/VAE 可共享 |
| Qwen Image Edit 2511 | 图像编辑 / ComfyUI | 角色多视图、表情、细节、局部编辑 | 不应与基础 T2I Profile 混成一个 artifact 列表 |
| Multiple Angles LoRA | 图像组件 / ComfyUI | 仅作为多视图 route 依赖 | 不单独出现在创作者模型菜单 |
| MiniMax H3 FL2VA | 视频 / ComfyUI | 首帧生视频、图生视频 | Video/Audio VAE 是组件 |
| MiniMax H3 Ref2VA | 视频 / ComfyUI | 参考图/参考视频生成 | Turbo LoRA 是可选 route component |
| ACE-Step 1.5 | 音乐 / ComfyUI | 音频工作台/BGM | 发布 AUDIO_MUSIC Profile 后才可选 |

ComfyUI 的价值是可视化图和复杂多组件编排。并非“文件在 Comfy models 目录里就应该作为一个模型显示”。普通页面显示模型包/能力；专家详情才显示组件和 workflow。

### 5.2 Ollama 模型

qwen3.8:27b 目标登记为一个 ModelRelease + 一个 Ollama RuntimeModelInstallation。它可以形成多个 CapabilityOffering 和 Profile：

- LLM_STORY_PARSE
- LLM_EPISODE_PLAN
- LLM_STORYBOARD
- LLM_PROMPT_REWRITE
- QC_VISUAL（只有 /api/show 与真实视觉冒烟确认后）
- 未来可能的 EMBEDDING_TEXT（只有 Ollama native capability 确认后）

一个 Ollama tag 不是一个 Profile；它可以被多个不可变 Profile 使用。

Ollama 页面扫描必须：

1. 调用 /api/tags 获得 tag、digest、size 和基本详情。
2. 仅在新增或 digest 变化时调用 /api/show。
3. 保存 capabilities、context length、families、template、vision/tools/thinking/embed 支持。
4. 生成 DiscoveryObservation 和候选 CapabilityOffering。
5. 不自动发布，不因运行时暂时离线删除模型。

### 5.3 PyTorch 专用模型

| 模型 | 目标 Adapter | Capability | 首选业务入口 |
|---|---|---|---|
| Qwen3 Embedding 8B | pytorch.embedding.qwen3 | EMBEDDING_TEXT | 后台索引/RAG 管理 |
| VoxCPM2 | pytorch.tts.voxcpm2 | TTS | 对白/配音 |
| VoxCPM2 | pytorch.voice_clone.voxcpm2 | VOICE_CLONE | 角色音色管理 |
| Qwen3 ASR 1.7B | pytorch.asr.qwen3 | ASR | 字幕/音频工作台 |
| Qwen3 ForcedAligner 0.6B | pytorch.alignment.qwen3 | AUDIO_ALIGNMENT | 字幕精确时间轴 |
| LatentSync 1.6 | pytorch.lipsync.latentsync | LIPSYNC | 镜头后期/时间线 |

这些模型适合 PyTorch 而不是强行塞进 Ollama，因为它们需要专用 processor、音视频预处理、目录模型、多文件辅助网络、特定输出结构或独立 Python 依赖。

### 5.4 OS 原生与工具能力

- Windows SAPI：OS_NATIVE Runtime + Voice Resource + TTS Offering。
- FFmpeg/ffprobe：TOOL_PROCESS Runtime，不是模型。
- 将来人脸检测器、传统 CV 或 ONNX 工具：如果由通用 Adapter 执行，可以作为 ModelRelease；如果是纯工具，则作为 ToolCapability。

### 5.5 远端服务

远端 OpenAI-compatible/DeepSeek 等登记为 RuntimeInstallation + ProviderModelOffering。secret 只保存 reference，不进入 Profile、Job snapshot、日志或浏览器响应。

远端能力和本机能力进入同一 Capability Catalog，但必须清晰标注：

- 数据是否离开服务器
- endpoint trust class
- credential 状态
- 成本/限额
- 当前连接状态

## 6. 为什么 Embedding 属于后台能力

Embedding 模型把文本、图片或多模态内容变成向量。向量不是给用户阅读的内容，而是供机器比较距离。

在本项目中主要用途：

1. 对剧本段落、场景、角色设定和素材说明建立索引。
2. 从项目资料中检索与当前镜头最相关的设定。
3. 做角色名、场景名和术语的相似匹配与去重。
4. 为 RAG 提供候选上下文，再交给 LLM 生成或判断。
5. 查找相似镜头、相似提示词或重复素材。

正确链路是：

    文档/业务实体
      → 规范化与切块
      → Embedding Profile
      → 向量及 metadata
      → 本地向量索引
      → 相似度检索
      → 权限与项目范围过滤
      → LLM 或业务规则消费

Embedding 不应默认出现在“文生图模型”“剧本模型”下拉框，因为它不直接生成剧本、图片、视频或音频。它应出现在：

- 系统中心 / 模型平台 / 检索与向量
- 系统中心 / 搜索与索引设置
- 项目设置 / 知识与检索（高级）

普通用户只看到“项目知识索引：已就绪/正在更新/需要重建”，不需要每次选择 Embedding 模型。

必须冻结以下索引身份：

- embedding_profile_version_id
- model release digest
- tokenizer/processor fingerprint
- normalization policy
- chunking policy version
- vector dimension、distance metric
- source entity version/hash

任一影响向量语义的字段改变都建立新 IndexVersion，不能把新旧向量混写。RAG_RETRIEVAL 通常是 CPU/内存或数据库操作，不应一律占用 PyTorch GPU；只有 EMBEDDING_INDEX/EMBEDDING_QUERY 需要按 Profile 资源策略申请 GPU。

## 7. 运行时与单 GPU 编排

### 7.1 统一 Runtime Adapter 合同

每个 Adapter 实现：

- discover()
- describe_native_model()
- validate_installation()
- probe_runtime()
- validate_offering()
- smoke_capability()
- estimate_resources()
- prepare()
- execute()
- cancel()
- collect_outputs()
- release()

Adapter 返回稳定错误码和结构化 evidence，不把底层异常文本直接当业务合同。

### 7.2 单 GPU 资源模型

现有 GPU runtime coordinator 和 GPU_H3_HEAVY 互斥策略应保留。目标资源类：

- CPU_ONLY
- GPU_LIGHT
- GPU_HEAVY_EXCLUSIVE
- GPU_PERSISTENT
- DISK_IO_HEAVY
- NETWORK_OUTBOUND

在当前单卡 Windows Server 上：

- ComfyUI 图像/视频、Ollama 27B、PyTorch 大模型默认申请同一 GPU_HEAVY_EXCLUSIVE lease。
- 同一时刻只允许一个 heavy execution session。
- CPU 检索、ffprobe、轻量数据库任务可以并行。
- 运行时状态 BUSY 不等于不可用；页面显示排队和预计等待。

### 7.3 Runtime Session 流程

    Job 已冻结 ResolvedExecutionSnapshot
      → Scheduler 计算资源需求
      → 获取 GPU/CPU/磁盘租约
      → RuntimeCoordinator 对目标 RuntimeInstallationVersion prepare
      → 按策略暂停或卸载冲突运行时
      → Adapter 执行
      → 心跳、进度、取消
      → 校验输出并登记 Artifact/MediaVersion
      → Adapter release
      → 释放租约

GPU coordinator 不得再只使用全局 llm_base_url。它必须读取 Job snapshot 的 runtime_installation_version_id，才能管理正确的 Ollama/Comfy/PyTorch 实例。

### 7.4 PyTorch 进程边界

当前 one-shot 子进程退出后释放 VRAM 的方向正确，但生产合同需重构为发行物内的版本化 Adapter Host：

- 使用 JSON Lines 或本机 Named Pipe 协议。
- 输入只含 installation id、artifact locator、语义参数和受控媒体引用。
- stdout 只输出协议消息；第三方日志转 stderr。
- 支持 heartbeat、progress、cancel、timeout。
- 强制离线环境和网络策略。
- 每种 Adapter 有独立锁定依赖或兼容依赖组。
- Job 完成后进程退出或进入有上限的 warm pool。

scripts/local_ai_runtime.py 只能作为开发/迁移工具，不可成为 Windows 发行版的长期服务合同。

### 7.5 可执行判断

不能再以 workflow_version_id 非空判断可执行。不同 Adapter 的 route readiness：

| Runtime | 可执行条件 |
|---|---|
| ComfyUI | Published Profile + Published Workflow + 显式 Binding Contract + 组件完整 + Runtime Ready |
| Ollama | Published Profile + tag digest 匹配 + native capability 支持 + Runtime Ready |
| PyTorch | Published Profile + Adapter version 存在 + 目录组件完整 + smoke passed + Worker handler 可用 |
| SAPI | Published Profile + voice token 在服务身份下仍可枚举 |
| Remote | Published Profile + connection version/secret 可解析 + 出境策略允许 + probe passed |

## 8. Windows Server 存储与目录设计

### 8.1 三个根必须分离

**ReleaseRoot**：只读程序版本，由安装器管理。

    C:/Program Files/LocalDramaStudio/versions/{version}/

**InstanceRoot**：机器级配置、数据库、日志和最小运行状态。

    C:/ProgramData/LocalDramaStudio/

**DataRoot/ModelRoot**：大容量项目、工作区、缓存和模型，可由安装向导选择数据盘。

    F:/LocalDramaStudioData/
    F:/AI_Models/LocalDramaStudio/

不能把源码仓库当 ReleaseRoot，不能把当前工作目录当路径基准。

### 8.2 InstanceRoot

    C:/ProgramData/LocalDramaStudio/
      config/
        config.json
        model-libraries.json
      data/
        local_drama.sqlite3
      runtime-state/
        host/
        workers/
        locks/
      logs/
        host/
        api/
        worker/
        adapters/
      backups/
      evidence/
      updates/

config 保存 library 映射和 runtime installation 引用，不保存模型自身绝对身份。Secret 使用 Windows Credential Manager/DPAPI 封装后的 reference。

### 8.3 ModelRoot

    <ModelRoot>/
      downloads/
        queue/
        partial/
        cache/
      staging/
      quarantine/
      manifests/
        catalog/
        imports/
        runtime-observations/
      artifacts/
        sha256/
      libraries/
        comfyui/
          diffusion_models/
          checkpoints/
          text_encoders/
          vae/
          loras/
          controlnet/
          upscale_models/
        pytorch/
          embedding/
          tts/
          voice_clone/
          asr/
          alignment/
          lipsync/
        ollama/
      runtime-views/
      cache/
        huggingface/
        torch/
        transformers/

Ollama 的内部 blob/manifest 结构由 Ollama 自己管理，平台不直接改写，只配置 OLLAMA_MODELS 并通过 API 观察。

受管 Artifact Store 和 Comfy/PyTorch runtime view 在同卷时可使用 NTFS hardlink 去重；跨卷或不支持时复制并记录引用计数。禁止未经验证的 junction/reparse point 穿越受控根。

### 8.4 外部模型库

现有 F:/AI_Models/LocalDramaStudio 可以作为 EXTERNAL_READ_ONLY 或迁移后 MANAGED library。外部库登记：

- node_id
- library code
- absolute local root
- read-only/managed
- allowed runtime kinds
- scan depth/pattern

数据库内的模型位置只保存：

    library_id = models-primary
    relative_path = pytorch/embedding/Qwen3-Embedding-8B

服务端解析并验证最终路径仍在 library root 内。LAN 客户端不得提交任意绝对路径。

### 8.5 Windows 服务身份

SERVER 模式使用专用低权限服务身份，禁止默认 LocalSystem。安装器必须：

1. 建立 Windows service 和 service SID/专用账户。
2. 给 InstanceRoot、DataRoot、ModelRoot 最小 ACL。
3. 在该身份下验证 GPU、模型目录、缓存目录、Ollama、SAPI、Credential Manager。
4. 不依赖交互用户的 APPDATA、桌面 Ollama 进程或用户 venv。
5. 配置 delayed auto-start、bounded restart、preshutdown drain。

如果使用 Host 管理的 Ollama，Ollama 也应在同一服务身份或明确的运行时服务身份下运行，并把 OLLAMA_MODELS 指向受管目录。用户桌面中 pull 的模型不应被假定对服务可见。

这也是 Windows Server 上“同一机器明明有模型，服务却只看到一个或看不到”的关键原因：环境变量、用户目录、Credential Manager 和 runtime registry 都按进程身份生效。

### 8.6 发行物合同

Windows release 必须包含或明确安装：

- 自包含 Python runtime 与锁定 wheels
- API/Worker application package
- Adapter Host 与内置 Adapter
- Web dist
- migrations
- capability catalog seed
- parameter/UI schema
- runtime manifest schema
- Host/service control binary

禁止生产配置引用：

- apps/api 源码路径
- scripts/local_ai_runtime.py
- E:/AI/ComfyDesktop/.../.venv
- 开发仓库 .venv
- 某个开发者用户目录

ComfyUI、Ollama、超大模型可作为外部受管依赖，但它们的版本、路径、服务身份和 fingerprint 必须通过 RuntimeInstallationVersion 固化。

### 8.7 首次启动自动创建

首次启动只创建目录、ACL、数据库和默认 library/runtime 草稿，不扫描全盘、不下载模型。启动检查：

- 盘符与根目录存在
- 可用空间
- ACL read/write/execute
- 长路径支持
- Windows 保留名/大小写规则
- reparse point 边界
- 同卷原子 rename 能力
- GPU driver/CUDA
- runtime executable/endpoint

检查完成后异步提交 DiscoveryRun。

## 9. 自动发现、扫描、下载与安装

### 9.1 发现总流程

    创建 DiscoveryRun
      → Adapter 枚举 native objects
      → 写 DiscoveryObservation
      → 轻量分组为候选 ModelRelease/Component
      → 异步完整性校验
      → 创建 RuntimeModelInstallation candidate
      → 推导 CapabilityOffering candidate
      → 管理员验证/发布

只有一次扫描完整成功，才能把上一轮存在、本轮未发现的 installation 标记 MISSING。网络/运行时失败只把扫描标记 INCOMPLETE，不能退休模型或 Profile。

### 9.2 Ollama 扫描

- /api/tags：每轮轻量枚举。
- /api/show：只对新 tag、digest 变化或管理员强制刷新调用。
- /api/ps：显示当前加载与 VRAM 状态，不作为安装清单。
- 记录 Ollama version 和 endpoint identity。
- tag rename 与 digest identity 分开；同 digest 可有多个 tag alias。
- capability 来自 native metadata + smoke，不能只按名称猜。

### 9.3 ComfyUI 扫描

ComfyUI 发现组合三种来源：

1. 受控 model library 文件索引。
2. ComfyUI /object_info、/system_stats 等运行时 metadata。
3. localdrama-model-bundle.json 声明式 bundle。

Bundle manifest 明确：

- release code/version
- component role 与相对路径
- 共享组件
- workflow route
- required custom nodes
- capability offerings
- expected hash/size

平台不应靠目录名把所有 safetensors 猜成独立模型。

### 9.4 PyTorch 扫描

每个受管目录提供 localdrama-model.json：

- schema version
- family/release/upstream revision
- adapter loader id
- required files/globs
- component roles
- expected hashes
- capabilities
- parameter contract family
- license/source

Discovery 先验证 manifest 和文件清单，再异步哈希。没有自描述文件的目录只进入 UNCLASSIFIED，不自动生成生产 Profile。

### 9.5 外部文件扫描

- 仅扫描已配置 ModelLibrary。
- 先读取目录项、size、mtime 和轻量 header。
- 仅新增/变化文件进入 hash queue。
- 大文件哈希有进度、限速和取消。
- shard 由 index/config 合并为 Directory Artifact。
- reparse point 默认拒绝。
- 结果缓存使用 file identity + size + mtime + quick fingerprint；正式发布仍需完整 hash。

### 9.6 下载与安装

DownloadPlan 至少包含：

- source URL/repository/revision
- expected file list、size、SHA-256
- license id/text/evidence requirement
- target ModelLibrary
- required free space
- runtime compatibility
- component/bundle manifest

执行流程：

    PLAN
      → LICENSE_CONFIRMATION
      → SPACE_PREFLIGHT
      → DOWNLOAD .part
      → HASH_VERIFY
      → MALWARE/FORMAT/STRUCTURE CHECK
      → STAGE
      → ATOMIC PROMOTE
      → REGISTER
      → RUNTIME PROBE
      → CAPABILITY SMOKE

要求：

- 支持 Range 断点续传和幂等重试。
- 所有临时文件只在 downloads/partial 或 staging。
- 哈希失败移入 quarantine，不覆盖旧安装。
- 安装成功后才更新 active location。
- 取消不删除已验证共享 blob。
- 引用计数为零且无历史 Job/Profile 引用时才可垃圾回收。
- 离线服务器支持“下载计划包 + 文件介质导入 + 本机校验”。

### 9.7 model-lock 的新角色

config/model-lock.json 不再作为在线事实，也不能由生产 API直接当数据库真相。它转为：

- 安装期望清单
- 离线交付清单
- 灾难恢复参考
- CI/发布验收输入

实际状态由 ModelRelease、ArtifactLocation、RuntimeModelInstallation 和最新 Discovery/Validation 记录决定。

### 9.8 建议扫描频率与成本分级

自动发现不能等于“每次打开页面就递归扫描并完整哈希几十 GB 权重”。建议统一调度：

| 触发 | 建议频率 | 工作内容 | 明确禁止 |
|---|---|---|---|
| Startup fast reconcile | 每次 Host 启动一次，异步执行 | 校验配置、ModelLibrary 根、runtime identity；读取目录 metadata；Ollama /api/tags；恢复未完成任务 | 阻塞 API 启动、同步完整哈希、自动加载大模型 |
| Managed root watcher | 事件驱动，5–15 秒 debounce | 监听受管目录 create/rename/delete/size stable，提交增量 observation/hash job | 对每次 write event 立刻哈希、监听任意全盘 |
| Managed root safety reconcile | 每日低峰一次 | 用目录 metadata 补偿 Windows watcher 丢事件；只对变化对象排队 | 无差别重哈希全部模型 |
| Attached runtime light probe | 默认每 5 分钟 | version、health、tag/list digest、当前 load 状态 | 每 5 分钟逐模型 load test |
| Offline runtime backoff | 失败后 5、15、30 分钟，最大 30 分钟 | 轻量 reachability，恢复后立刻 reconcile | 高频刷屏或改变 Published 状态 |
| 页面状态 | SSE 事件优先；可见页面 30 秒 polling fallback | 读取持久化 read model 和轻量 runtime summary | 页面请求触发目录递归扫描 |
| 完整 Artifact hash | 仅新增/size-mtime 变化、显式“重新验证”、安装 promote 前 | 后台限速 hash、可取消、可续跑 | startup 或普通页面同步运行 |
| Capability smoke | 安装/版本/digest/Adapter 变化后，发布前，或管理员手动 | 真正加载模型并验证能力 | 定时频繁占用 GPU |

Ollama registry 和 Comfy/PyTorch 文件根使用不同观察源，不能用同一个文件 watcher 逻辑硬套。重任务全部进入持久化 Job；页面刷新只读 Job 状态。

## 10. 参数、传参与最终执行配置

### 10.1 六层参数合同

当前最大的长期风险不是“参数少”，而是同一参数有多个真相来源。目标拆为六层。

#### 第一层：CapabilityContract

只定义业务语义，不包含运行时字段：

- 输入槽及数量：PROMPT、NEGATIVE_PROMPT、HERO_IMAGE、FIRST_FRAME、END_FRAME、REFERENCE_AUDIO 等
- 输入媒体类型、尺寸和授权要求
- 输出：IMAGE、VIDEO、AUDIO、TEXT、VECTOR
- 是否支持 seed、batch、stream、deterministic replay
- 能力特性：camera、reference、motion、voice clone 等

例如 IMAGE_CONCEPT 关心 prompt、画幅、候选数和输出图片，不关心 Comfy 节点编号。

#### 第二层：ParameterContractVersion

使用 JSON Schema 2020-12 表达数据约束，配套独立 UI Schema：

- type、enum、minimum、maximum、multipleOf
- units
- defaults
- required
- conditional constraints
- advanced/basic 分组
- label、help、control、order
- sensitive/hidden

每个 Capability 有基础合同，特定 Profile 可以收窄范围，不能放宽到 Adapter 不支持的值。

ParameterContract 必须覆盖：

- LLM：temperature、top_p、max_tokens、context policy、structured output
- 图像：width、height、aspect ratio、steps、cfg、seed、batch、quality tier
- 视频：duration、fps、width/height、steps、seed、camera/motion、audio
- TTS：speaker/voice profile、language、speed、emotion、temperature
- Voice Clone：reference audio、reference transcript、speaker policy
- ASR：language、timestamps、chunk size、VAD
- Alignment：language、granularity、text normalization
- Lipsync：resolution、crop/mask、guidance、audio policy
- Music：duration、lyrics/instrumental、style、tempo
- Embedding：max length、pooling、normalize、batch size、instruction

#### 第三层：AdapterBindingContractVersion

显式把语义参数和输入映射到运行时：

| Adapter | Binding 示例 |
|---|---|
| ComfyUI | generation.steps → workflow node alias sampler.steps |
| Ollama | llm.temperature → options.temperature |
| PyTorch | asr.language → adapter request.language |
| SAPI | tts.rate → voice runtime rate |

Comfy binding 必须引用不可变 WorkflowVersion 中的稳定 node alias/input name。迁移完成后禁止靠遍历 class_type、猜字段名或 LEGACY_UNBOUND 修改 workflow graph。

#### 第四层：ProfilePolicy

ProfileVersion 冻结：

- profile defaults
- locked values
- allowed override fields
- profile-specific min/max/enum
- quality presets
- resource limits
- deterministic policy

例如模型只支持 24 fps，Profile 可以锁定 fps；普通页面不显示可修改控件。

#### 第五层：ScopeOverrideSetVersion

保存业务范围的参数偏好：

- SYSTEM 默认
- PROJECT 覆盖
- EPISODE 覆盖
- SHOT 覆盖
- CHARACTER/VOICE 覆盖（仅适用能力）

Scope override 必须引用精确 ProfileVersion。AUTO 模式下不得保存依赖某一 Profile 的私有参数；只允许 CapabilityContract 中可跨 Profile 的语义参数。

#### 第六层：RunOverride 与 ResolvedExecutionSnapshot

单次运行只传允许覆盖的语义参数。服务端解析并冻结最终快照。

### 10.2 参数解析优先级

选择 Profile：

    shot explicit
      > episode explicit
      > project explicit
      > system explicit/default
      > newest policy-approved Published Profile

解析参数：

    Profile defaults
      ← Project override
      ← Episode override
      ← Shot override
      ← Run override

每一步都必须：

1. 确认 override 与当前 ProfileVersion 匹配。
2. 应用 locked field。
3. 按 ParameterContract 校验。
4. 执行跨字段约束。
5. 记录每个值的 provenance。

返回示例语义：

    {
      "steps": {
        "value": 28,
        "source": "SHOT_OVERRIDE",
        "locked": false
      },
      "fps": {
        "value": 24,
        "source": "PROFILE_LOCK",
        "locked": true
      }
    }

页面通过 provenance 解释“为什么是这个值”，而不是显示一个无法追踪的最终数字。

### 10.3 业务提交合同

业务页面只提交：

- capability_code
- scope context：project/episode/shot/character
- optional explicit profile_version_id
- semantic inputs：受控 media/document/entity references
- run_overrides
- idempotency key
- expected resolution hash

业务页面不得提交：

- model absolute path
- Ollama base URL
- Comfy node id
- Python executable
- API key
- runtime-specific raw kwargs

### 10.4 最终执行快照

ResolvedExecutionSnapshot 至少冻结：

- snapshot schema version
- capability_definition_id/code
- capability contract version/hash
- assignment chain 与 resolution reason
- execution_profile_version_id/payload hash
- runtime_installation_version_id/fingerprint
- adapter code/version
- runtime_model_installation ids
- model release/artifact/component digest
- workflow_version_id/content hash
- parameter_contract_version_id/hash
- adapter_binding_contract_version_id/hash
- resolved parameters + provenance
- semantic input bindings + source hashes
- resource policy
- secret reference id（不含 secret）
- network policy
- created_at

Worker 只执行快照，不在运行时重新“找最新模型”或读取页面临时默认值。

### 10.5 参数 UI 自动生成

ParameterFormRenderer 使用 JSON Schema + UI Schema 生成控件：

- basic/advanced 分组
- 单位与合法范围
- enum 标签
- 条件字段
- 锁定原因
- 当前值来源
- reset to inherited
- invalid/stale 状态

创作者页面只展示 basic 和与当前动作相关的字段；系统专家页展示完整合同和 Adapter binding。禁止每种模型在页面组件里硬编码一套参数表单。

## 11. System Center 页面信息架构

### 11.1 一级导航

系统一级入口命名为“模型平台”，包含六个稳定路由：

1. 概览
2. 模型库
3. 能力与执行配置
4. 运行时与服务
5. 下载与存储
6. 诊断与证据

建议路由：

    /system/model-platform/overview
    /system/model-platform/models
    /system/model-platform/capabilities
    /system/model-platform/runtimes
    /system/model-platform/storage
    /system/model-platform/diagnostics

详情使用子路由而不是全屏 Dialog，支持刷新、深链接、浏览器返回和审计定位。

### 11.2 概览

顶部回答四个问题：

- 有多少模型已发现/已验证/已发布？
- 哪些业务能力当前没有可执行 Profile？
- 哪个运行时离线或版本漂移？
- 下载、验证和 GPU 队列是否正常？

布局：

- 状态摘要：模型、能力、运行时、存储
- 需要处理：按严重度排序的阻塞项
- 能力覆盖矩阵：故事、图像、视频、声音、后期、检索、质检
- 当前任务：下载、扫描、校验、模型加载
- 最近变化：新增模型、digest 变化、Profile 发布、运行时升级

概览不展示大段路径或 JSON。

### 11.3 模型库

默认按“能力族”分组：

- 文本与理解
- 检索与向量
- 图像
- 视频
- 声音
- 后期
- 质检
- 未分类

支持切换按运行时分组：

- Ollama
- ComfyUI
- PyTorch
- Windows 原生
- 远端服务

筛选：

- 搜索 family/release/tag
- 运行时
- 模态
- Capability
- 来源/管理方式
- Presence/Integrity/Validation/Publication
- 可执行/不可执行
- 所在节点/模型库

列表一行显示：

- 模型名称和明确 release/quantization
- 主要用途 chips
- 运行时
- 安装大小
- 模型完整性
- 发布能力数
- 当前可执行状态

组件数量显示为“1 个主模型 + 3 个依赖”，不把 VAE、LoRA 和 shard 展开成同级模型。

### 11.4 模型详情

详情页标签：

- 概要：家族、release、格式、量化、来源、许可证
- 安装：节点、模型库、相对位置、大小、哈希、Presence
- 组件：角色、共享关系、required/optional
- 能力：CapabilityOffering、验证、已发布 Profile
- 使用位置：系统/项目 assignment、历史 Job 引用
- 维护：重新扫描、验证、迁移、停用、删除
- 证据：Discovery/Validation/License/Audit

删除按钮必须先返回引用分析；有 Published Profile、历史 Job、共享组件或项目证据时默认禁止物理删除。

### 11.5 能力与执行配置

以 CapabilityDefinition 为主，而不是以文件为主。每个能力卡展示：

- 业务说明
- Published Profile 数
- 系统默认 Profile
- 当前 Ready/Blocked
- 使用该能力的页面
- 最近冒烟证据

能力详情：

- Published、Candidate、Deprecated 版本
- Profile 对比
- 输入/输出合同
- 参数合同
- Workflow/Adapter binding
- 模型组件
- 资源策略
- 验证与发布

新 Profile 向导：

    选择 Capability
      → 选择 Runtime Offering
      → 选择模型安装/Workflow
      → 选择 Parameter Contract
      → 检查 Adapter Binding
      → 静态验证
      → 真实冒烟
      → 发布新不可变版本

### 11.6 运行时与服务

按 RuntimeInstallation 展示：

- ComfyUI
- Ollama
- PyTorch Adapter Host
- Windows SAPI
- FFmpeg
- Remote Provider

每项显示：

- owner：Host managed/external
- endpoint/executable（脱敏）
- version/fingerprint
- 服务身份
- 状态与最后心跳
- 当前加载模型
- GPU/内存占用
- 支持 Adapter/routes
- restart/probe/upgrade

远端 Provider 的 secret 状态与模型 offering 在此管理；不再与本机权重列表混排。

### 11.7 下载与存储

页面分为：

- 下载队列：进度、速度、剩余空间、校验阶段
- 安装计划：来源、版本、许可证、目标 library
- 模型库容量：总量、可用空间、共享/重复空间
- 暂存与隔离：失败原因、恢复/清理
- 缓存策略：HF/Torch/Comfy/Ollama
- 迁移工具：模型库间移动

下载模型使用向导，不要求用户手工输入最终磁盘路径。高级管理员可选择目标 ModelLibrary。

### 11.8 诊断与证据

- DiscoveryRun 历史
- Runtime probe
- Artifact integrity
- Capability smoke
- Profile publication receipt
- GPU lease timeline
- 服务身份与 ACL 检查
- 环境变量脱敏快照
- 下载/安装日志

提供“导出诊断包”，排除 secret 和业务敏感媒体。

### 11.9 菜单自动生成

能力菜单不能由 ModelsPage 中 if capability.startsWith 继续硬编码。前端读取 CapabilityDefinition：

- ui_group
- ui_order
- title
- description
- icon_key
- business_surfaces
- visibility

只有以下条件同时满足时，业务菜单/选择项才出现：

- capability 对当前 surface 可见
- 至少一个 Published Profile
- Profile route 对当前输入合同兼容
- 当前用户/项目策略允许

系统模型平台仍显示候选和阻塞能力，普通业务页只显示可执行能力或明确的“尚未配置”入口。

### 11.10 页面交互规范

- 一页只保留一个主要 CTA。
- 状态色只表达语义：Ready、Attention、Blocked、Info。
- 所有加载、空状态、错误和重试都有稳定区域，避免整页跳动。
- 长扫描、下载、冒烟为后台 Job，页面离开后继续。
- 不用颜色作为唯一状态表达。
- 表格支持键盘、焦点和 1024/1280/1440 宽度。
- 路径、digest、版本号使用可复制文本，但默认折叠。
- 危险操作显示影响分析和恢复策略。

## 12. 业务页面应该选择什么模型

### 12.1 总规则

业务页面选择的是“本次创作动作对应的能力/Profile”，不是底层运行时。

默认交互：

- 系统推荐：使用 resolver 按 scope 继承和当前 readiness 选择。
- 固定能力版本：专家或项目负责人选择 Published ProfileVersion。
- 临时参数：仅覆盖 ParameterContract 允许字段。

如果没有可执行 Profile，页面给出：

- 缺少的 Capability
- 阻塞原因
- 打开系统模型平台的精确链接

不展示无法执行的候选模型作为普通下拉选项。

### 12.2 页面—能力矩阵

| 页面/功能 | 用户选择 | 服务端 Capability | 不应暴露 |
|---|---|---|---|
| 快速创作：文字规划 | 系统推荐或已发布规划 Profile | LLM_STORY_PARSE / LLM_PROMPT_REWRITE | Ollama URL、temperature 之外的底层字段 |
| 快速创作：文生图 | 已发布图像 Profile | IMAGE_CONCEPT | Comfy workflow/node/path |
| 快速创作：文生视频 | 已发布视频 Profile | VIDEO_T2V | DiT/VAE 文件 |
| 快速创作：图生视频 | 已发布视频 Profile | VIDEO_I2V | Comfy endpoint |
| 故事工作台：拆解 | 项目默认故事能力，必要时一次覆盖 | LLM_STORY_PARSE | Ollama tag 列表 |
| 分集规划 | 项目/分集规划能力 | LLM_EPISODE_PLAN / LLM_STORYBOARD | Provider connection |
| 资产圣经：概念图 | 项目图像默认或一次覆盖 | IMAGE_CHARACTER / IMAGE_SCENE / IMAGE_CONCEPT | LoRA/VAE |
| 资产圣经：多视图 | 支持 HERO_IMAGE 的已发布 Profile | IMAGE_MULTI_VIEW | 多角度 LoRA 文件 |
| 资产圣经：表情/细节 | 支持相应输入的 Profile | IMAGE_EXPRESSION / IMAGE_EDIT | workflow binding |
| 导演台：首帧 | 镜头继承的图像能力 | IMAGE_CONCEPT/CHARACTER/SCENE | raw model path |
| 导演台：镜头视频 | 镜头/分集/项目视频能力 | VIDEO_I2V、VIDEO_REFERENCE 等 | runtime kind |
| 重抽/分支 | 沿用 Profile 或显式改用另一个 Published Profile | 原 capability | Candidate Profile |
| 对白与配音 | 角色 VoiceProfile + Published TTS Profile | TTS | VoxCPM/SAPI 实现字段 |
| 声音克隆 | 合法参考音频 + Published clone Profile | VOICE_CLONE | 模型目录、Python 参数 |
| 字幕生成 | 已发布 ASR Profile | ASR | Whisper/Qwen 文件 |
| 字幕精对齐 | 已发布 Alignment Profile | AUDIO_ALIGNMENT | tokenizer 路径 |
| 音乐/BGM | 已发布音乐 Profile | AUDIO_MUSIC | ACE-Step 组件 |
| 镜头唇形同步 | 已发布 Lipsync Profile | LIPSYNC | LatentSync 辅助网络 |
| 放大/后处理 | 已发布 Post Profile | UPSCALE_IMAGE/VIDEO、POST_PROCESS | tool command |
| 视觉/身份 QC | 策略选择的 QC Profile | QC_VISUAL/QC_FACE/QC_IDENTITY | Provider secret |
| 项目知识索引 | 一般不逐次选择；系统/项目高级设置 | EMBEDDING_TEXT | 每次请求的模型菜单 |
| 检索 | 自动使用对应 IndexVersion | RAG retrieval service | GPU 模型下拉 |
| 时间线/交付 | 不选择生成模型 | 已冻结媒体和工具 Profile | 模型平台对象 |

按当前产品正式页面进一步收敛如下：

| 产品页面 | 正常用户看到的选择 | Resolver/能力 | 页面责任边界 |
|---|---|---|---|
| Quick Create | 文字规划、文生图、文生视频/图生视频的“系统推荐/固定已发布版本” | LLM_STORY_PARSE、IMAGE_CONCEPT、VIDEO_T2V、VIDEO_I2V | 可以做 Run Override；不能发布 Profile |
| Project Settings / AI 能力偏好 | 每个能力 AUTO/EXPLICIT，显示系统默认与项目覆盖 | 全部项目可配置 CapabilityAssignment | 只配置项目继承，不扫描模型、不编辑运行时 |
| Story | 通常沿用项目故事能力；高级用户可做一次任务覆盖 | LLM_STORY_PARSE、LLM_PROMPT_REWRITE；Embedding 仅后台 | 不显示 Ollama tags；项目知识检索状态单独显示 |
| Assets | 按动作选择概念、角色、场景、多视图、表情、编辑能力 | IMAGE_CONCEPT、IMAGE_CHARACTER、IMAGE_SCENE、IMAGE_MULTI_VIEW、IMAGE_EXPRESSION、IMAGE_EDIT | 只显示输入合同兼容的 Published Profile |
| Episode Plan | 沿用项目/分集规划能力，可固定分集版本 | LLM_EPISODE_PLAN、LLM_STORYBOARD | 不显示 Provider/模型文件 |
| Shot Studio | 镜头级图像/视频能力和允许的语义参数 | IMAGE_*、VIDEO_* | 保存 Shot Assignment/Override；不编辑 Profile 合同 |
| Episode Production | 展示每个镜头解析到的 Profile、readiness 和队列；通常不再次选择 | Shot → Episode → Project Capability Resolution | 批量提交冻结快照；阻塞时链接到准确配置入口 |
| Post Review | 选择 QC policy；Profile 通常由 policy 解析 | QC_VISUAL、QC_FACE、QC_IDENTITY、QC_CONTINUITY、QC_AUDIO | 机器 QC evidence 不自动成为人工批准 |
| Post Audio | 选择角色音色、TTS/Clone、ASR/Alignment、音乐动作 | TTS、VOICE_CLONE、ASR、AUDIO_ALIGNMENT、AUDIO_MUSIC | 角色选 VoiceProfile，不直接选 VoxCPM/SAPI 文件 |
| Post Edit | 选择后期 recipe 或沿用项目策略 | LIPSYNC、UPSCALE_IMAGE、UPSCALE_VIDEO、POST_PROCESS | recipe 冻结所需 Profile；不暴露 CLI/node |
| Delivery | 不提供模型下拉 | 无生成 Capability；只消费冻结 Timeline/Media 与工具合同 | 不能在交付阶段偷偷重跑生成模型 |
| Visual Lab | 专家选择 Runtime、Workflow 草稿、候选 Offering 做隔离测试 | Comfy/Adapter 实验能力 | 只产出 sandbox evidence；通过 promote/publish 才进入生产 |
| System Workflows | 专家维护 WorkflowVersion、semantic node alias、Adapter Binding | 与 Capability/Profile 关联，但不产生项目偏好 | 不承担模型下载；不直接写 Published Profile |

如果某个页面暂时没有相应生产 handler，即使模型已发现，也必须显示“能力尚未接通”，不能把 Candidate 塞进选择器。

### 12.3 项目生成偏好

ProjectCapabilitiesPage 保留，但重命名和收敛为“项目设置 / AI 能力偏好”：

- 按能力族分组
- 每项 AUTO 或 EXPLICIT
- 只列 Published Profile
- 显示系统默认、项目覆盖和 readiness
- 显示影响页面
- 不允许在此创建/扫描/发布模型

Episode/Shot 的编辑器复用同一 CapabilityAssignment 组件，不各自实现模型枚举。

### 12.4 项目模型许可证与证据入口

新增：

    /projects/{project_id}/settings/model-evidence

页面只处理项目特有信息：

- 项目使用了哪些全局 ModelRelease/Profile
- 项目自己的许可证/授权证据
- 证据文件的项目内路径和 hash
- 缺失项与用户责任说明

系统模型详情处理上游许可证元数据和系统级安装证明。项目证据不再依赖旧 /projects/{project_id}/models 路由，也不塞回系统全局模型页。

### 12.5 Voice 与 Character 的特殊选择

角色选择 VoiceProfile，VoiceProfile 再冻结：

- 参考音频/授权证据
- TTS 或 Voice Clone ProfileVersion
- speaker token/voice resource
- 参数 override

角色页面不直接选择 VoxCPM2 或 SAPI。模型实现可以升级，但已有 VoiceProfileVersion 和历史 Job 仍可追踪。

## 13. API v2 设计

### 13.1 API 原则

- /api/v2/model-platform 为系统模型平台唯一写边界。
- 查询与命令分开。
- 长操作返回 job_id/run_id。
- 使用稳定 id 和 version；不接受业务客户端绝对路径。
- 所有 mutation 支持 idempotency key 与 optimistic revision。
- API response 明确区分 persisted status、observed status、computed executable。
- secret 永不回显。

### 13.2 概览与目录

| Method | Endpoint | 用途 |
|---|---|---|
| GET | /api/v2/model-platform/overview | 系统摘要、能力缺口、阻塞项 |
| GET | /api/v2/model-platform/capabilities | 唯一 CapabilityDefinition 目录 |
| GET | /api/v2/model-platform/models | 搜索/筛选 ModelFamily/Release |
| GET | /api/v2/model-platform/model-releases/{id} | 模型详情 |
| GET | /api/v2/model-platform/model-releases/{id}/components | 组件与共享关系 |
| GET | /api/v2/model-platform/model-installations | 节点安装与实时状态 |
| GET | /api/v2/model-platform/model-installations/{id} | 安装详情、证据、引用 |

### 13.3 节点、模型库与发现

| Method | Endpoint | 用途 |
|---|---|---|
| GET | /api/v2/model-platform/compute-nodes | 节点 |
| GET/POST | /api/v2/model-platform/model-libraries | 模型库 |
| POST | /api/v2/model-platform/model-libraries/{id}:scan | 提交 DiscoveryRun |
| GET | /api/v2/model-platform/discovery-runs/{id} | 扫描进度/结果 |
| POST | /api/v2/model-platform/observations/{id}:classify | 人工确认未分类对象 |
| POST | /api/v2/model-platform/model-installations/{id}:verify | 完整性验证 |

新建 external library 时，服务器端管理员通过本机 Host/安装器选择路径；LAN Web API 只能引用预配置 allowed root token。

### 13.4 运行时

| Method | Endpoint | 用途 |
|---|---|---|
| GET/POST | /api/v2/model-platform/runtime-installations | 列表/创建草稿 |
| GET | /api/v2/model-platform/runtime-installations/{id} | 详情 |
| POST | /api/v2/model-platform/runtime-installations/{id}:discover | 原生模型发现 |
| POST | /api/v2/model-platform/runtime-installation-versions/{id}:probe | 版本化探测 |
| POST | /api/v2/model-platform/runtime-instances/{id}:start | 启动 Host-managed runtime |
| POST | /api/v2/model-platform/runtime-instances/{id}:stop | 停止 |
| POST | /api/v2/model-platform/runtime-instances/{id}:restart | drain 后重启 |
| GET | /api/v2/model-platform/runtime-instances/{id}/metrics | 运行状态 |

### 13.5 下载与安装

| Method | Endpoint | 用途 |
|---|---|---|
| POST | /api/v2/model-platform/install-plans | 校验来源、许可、空间和依赖 |
| POST | /api/v2/model-platform/install-plans/{id}:confirm | 显式确认 |
| GET | /api/v2/model-platform/install-jobs/{id} | 进度 |
| POST | /api/v2/model-platform/install-jobs/{id}:cancel | 取消 |
| POST | /api/v2/model-platform/install-jobs/{id}:retry | 从安全阶段重试 |
| POST | /api/v2/model-platform/model-installations/{id}:move | 模型库迁移 |
| POST | /api/v2/model-platform/model-installations/{id}:uninstall-plan | 引用分析 |
| POST | /api/v2/model-platform/uninstall-plans/{id}:confirm | 物理卸载 |

### 13.6 Capability Offering 与 Profile

| Method | Endpoint | 用途 |
|---|---|---|
| GET | /api/v2/model-platform/capability-offerings | 发现/验证到的能力 |
| POST | /api/v2/model-platform/capability-offerings/{id}:smoke | 真实冒烟 |
| GET/POST | /api/v2/model-platform/execution-profiles | Profile family |
| POST | /api/v2/model-platform/execution-profiles/{id}/versions | 派生新版本 |
| GET | /api/v2/model-platform/execution-profile-versions/{id} | 不可变详情 |
| POST | /api/v2/model-platform/execution-profile-versions/{id}:validate | 合同验证 |
| POST | /api/v2/model-platform/execution-profile-versions/{id}:smoke | 能力冒烟 |
| POST | /api/v2/model-platform/execution-profile-versions/{id}:publish | 发布 |
| POST | /api/v2/model-platform/execution-profile-versions/{id}:deprecate | 弃用 |
| POST | /api/v2/model-platform/execution-profile-versions/{id}:retire | 退休 |

publish request 只引用已存在且 hash 匹配的 ValidationRun/Evidence；不得同时提交变更后的 capability/model/schema payload。

### 13.7 Assignment、解析与参数

| Method | Endpoint | 用途 |
|---|---|---|
| GET/PUT | /api/v2/capability-assignments/{scope_type}/{scope_id}/{capability_code} | 读取/保存偏好 |
| GET | /api/v2/capability-resolution | 解析最终 Profile |
| POST | /api/v2/execution-resolution:preview | 参数和输入预检，不创建 Job |
| POST | /api/v2/executions | 冻结快照并创建 Job |
| GET | /api/v2/execution-snapshots/{id} | 可重现快照 |

preview response 包含：

- selected Profile
- selection reason
- effective parameters
- provenance
- blockers/warnings
- resource estimate
- current runtime readiness
- resolution hash

### 13.8 项目证据

| Method | Endpoint | 用途 |
|---|---|---|
| GET | /api/v2/projects/{project_id}/model-usage | 项目实际引用的模型/Profile |
| GET | /api/v2/projects/{project_id}/model-license-evidence | 项目证据 |
| POST | /api/v2/projects/{project_id}/model-license-evidence | 导入项目内证据 |
| DELETE | /api/v2/projects/{project_id}/model-license-evidence/{id} | 撤回证据记录，保留审计 |

系统级许可证 metadata 使用 /api/v2/model-platform/model-releases/{id}/license，不再与项目证据混淆。

### 13.9 事件与进度

扫描、下载、哈希、冒烟、运行时切换和 Job 进度统一进入系统事件流：

    GET /api/v2/events/stream

首版可用 SSE。事件只含 id、kind、state、progress、safe summary；详细日志通过鉴权 API 获取。

## 14. 代码重构边界

### 14.1 后端目标包

建议新增：

    apps/api/local_drama/model_platform/
      domain/
        capabilities.py
        models.py
        runtimes.py
        profiles.py
        assignments.py
        parameters.py
        states.py
      application/
        catalog_queries.py
        discovery_commands.py
        installation_commands.py
        runtime_commands.py
        profile_commands.py
        resolution_service.py
        execution_service.py
        evidence_service.py
      adapters/
        ollama/
        comfyui/
        pytorch/
        windows_sapi/
        ffmpeg/
        openai_compatible/
      infrastructure/
        repositories/
        filesystem/
        downloads/
        adapter_host/
      api_v2/
        routes/
        schemas/

业务应用只依赖：

- CapabilityResolver
- ExecutionPlanner
- ExecutionSubmissionService
- ModelUsageQuery

不得依赖具体 OllamaClient、ComfyClient、LocalAISubprocessAdapter 或磁盘路径。

### 14.2 前端目标包

    apps/web/src/features/model-platform/
      api/
      overview/
      catalog/
      model-detail/
      capabilities/
      profiles/
      runtimes/
      downloads/
      storage/
      diagnostics/
      assignments/
      parameter-form/
      evidence/

共享组件：

- CapabilityBadge
- AvailabilityMatrix
- RuntimeStatus
- ModelReleaseLabel
- ParameterFormRenderer
- AssignmentSelector
- ExecutionResolutionSummary
- BackgroundJobProgress

ModelsPage.tsx 退役，改为路由级工作区组件，不继续扩大单文件。

### 14.3 保留并接入的现有基础

保留：

- Job、JobAttempt、Artifact 生命周期
- WorkerSession、worker channels
- resource lease 与 GPU coordinator
- WorkflowVersion 和 Comfy job lineage
- MediaVersion 与项目资产谱系
- generation preference 的 scope 继承思想
- AuditEvent 与幂等提交

重构：

- generation preference → CapabilityAssignment
- ProfileService → model_platform Profile aggregate/service
- ModelCompatibilityService → Artifact integrity + Model Release/Installation
- LocalLLMService 专用 Profile 写逻辑 → Ollama Adapter + 通用 Profile 命令
- SAPI 专用 Profile upsert → OS Native Adapter + 通用 Profile 命令
- local_ai_subprocess → Versioned PyTorch Adapter Host
- generation_model_catalog → Capability Catalog/Resolution read model

### 14.4 必须删除的旧写路径

V2 cutover 后删除：

- 直接写 SQLite 的 register_local_ai_services.py 生产用途
- Ollama Profile Version 1 upsert
- SAPI Profile Version 1 upsert
- manifest 同步把全局 artifact_ids 塞入每个 Profile
- ModelCompatibilityService 的第二套 capability 猜测词表
- 业务页直接调用 /local-llm/models 做选择
- project_profile_bindings 写路径
- LEGACY_UNBOUND Comfy 执行
- 生产配置中的源码脚本/开发 venv/硬编码盘符

旧 API 可在一个限定版本窗口作为只读 facade，内部调用 V2 query；不得双写旧表。

### 14.5 P0 数据保护约束

在 V2 实现期间对旧体系冻结新增语义，并加入迁移门禁：

1. Manifest 转换器按 capability route 显式选择 component bundle；每个 Profile 的 artifact 集合必须通过集合断言。
2. 数据库触发器或 repository guard 禁止修改 PUBLISHED Profile payload 字段。
3. Profile stable identity 强制 capability + runtime + model release + route variant 唯一。
4. 唯一 CapabilityDefinition seed 由后端生成前端类型；禁止第二份手写词表。
5. Worker capability registry 必须证明 Published Profile 有可用 handler。
6. Release validator 拒绝 source repo path、用户 venv 和不存在的 model library。
7. 系统模型页和项目 evidence 页分别有完整路由测试。

## 15. 数据迁移与一次性切换

### 15.1 为什么不是继续打补丁

本方案不在当前 ModelsPage、ProfileService 和注册脚本中逐项加分支。实施采用平行 V2：

- 新表和新 repository 是唯一新模型。
- 旧数据只读取并转换。
- 新 UI 只调用 V2。
- 新 Worker handler 只消费 V2 snapshot。
- 完成影子比对后一次切换路由和 dispatcher。
- 回滚切换版本和数据库备份，不把新旧写模型长期混用。

### 15.2 阶段 A：冻结与盘点

产出不可变迁移清单：

- 当前 model-lock、manifest、Ollama tags
- model_artifacts、models/model_versions
- local_runtimes、runtime environments/instances
- provider connections
- execution profiles/versions
- project_profile_bindings、generation preferences
- 当前 Job/Attempt/Profile 引用
- 模型路径、哈希、缺失状态

创建迁移前数据库备份和 ModelRoot 文件清单。任何缺失 E/F 盘路径只标 MISSING，不删除记录。

### 15.3 阶段 B：建立 V2 schema 与 seed

一次 migration 新增：

- compute_nodes
- model_libraries
- model_families
- model_releases
- model_artifacts_v2
- model_artifact_locations
- model_components
- runtime_installations
- runtime_installation_versions
- runtime_instances_v2
- runtime_model_installations
- capability_definitions
- capability_offerings
- parameter_contract_versions
- adapter_binding_contract_versions
- resource_policy_versions
- execution_profiles_v2
- execution_profile_versions_v2
- profile_publications
- capability_assignments
- discovery_runs/observations
- validation_runs/evidence
- install_plans/install_jobs

Capability seed 是代码与数据库的唯一标准来源，迁移校验 code 不重复且 UI metadata 完整。

### 15.4 阶段 C：只读转换与对账

转换规则：

- models/model_versions + model_artifacts → ModelFamily/Release/Artifact
- 单文件 shard 按 model-lock/目录 manifest 合并
- manifest partition → 准确 ModelRelease bundle
- 绝对路径 → ModelLibrary + relative_path
- Ollama tag/digest → RuntimeModelInstallation
- local_runtimes/runtime environments → RuntimeInstallation/Version
- provider_connections → HTTP RuntimeInstallationVersion + secret reference
- 旧 Profile → 新不可变 ProfileVersion
- generation preferences/project bindings → CapabilityAssignment

冲突处理：

- generation preference 优先于 legacy project_profile_binding。
- 同 capability 多个 ACTIVE binding 进入 MIGRATION_CONFLICT，不猜。
- Published Profile payload 相同可合并 family，但历史 id 映射必须保留。
- Ollama 同模型不同 capability 拆成多个 Profile。
- artifact bundle 含不相关 partition 时按 manifest route 重建；原错误快照保留只读 migration evidence。

对账报告必须达到：

- 所有旧 Profile 有映射或明确阻塞原因。
- 所有历史 Job 的 Profile/Artifact 引用可解析。
- 所有 current model-lock 项有 ModelRelease/Installation 或明确 MISSING。
- 没有绝对路径作为 ModelRelease identity。

### 15.5 阶段 D：Adapter 与发现影子运行

在不影响业务的情况下运行 V2 discovery：

- Ollama tags/show
- Comfy bundle
- PyTorch directory manifest
- SAPI voice
- FFmpeg
- Remote Provider

将 V2 observed state 与旧 health/catalog 对比。影子运行不得写旧表或改变 Profile publication。

### 15.6 阶段 E：补齐生产执行链

按风险和业务价值：

1. Ollama LLM
2. Comfy image/video
3. Embedding
4. ASR + Alignment
5. VoxCPM TTS
6. Voice Clone
7. LatentSync
8. ACE-Step Music

每个能力必须成套完成：

- CapabilityDefinition
- Discovery/Offering
- ParameterContract
- AdapterBinding
- ResourcePolicy
- Worker handler
- smoke evidence
- Published Profile
- business resolver
- UI selector

不允许只登记模型、不接 handler 就在业务页显示。

#### 15.6.1 当前实施基线（2026-08-29）

已落地的是**可审计的执行骨架**，不是把旧 Profile 或全局运行时配置再包一层：

| 交付项 | 当前事实 | 证据/约束 |
|---|---|---|
| 预检与选择 | 已交付 | Capability resolver 计算已发布 Profile、参数来源、网络策略与 resolution hash；请求不能携带路径、endpoint、node id 或 secret。 |
| Ollama 扫描 | 已交付 | API 服务身份可将已配置的本机 Ollama 注册为 V2 `DRAFT` RuntimeVersion，并通过 tags/show 写入 discovery evidence；重复扫描复用相同运行时版本，不创建 Release、Profile 或业务分配。 |
| ComfyUI / PyTorch 扫描 | 已交付 | 每个配置的模型库根目录都有独立 V2 Library 绑定；受控 `model-lock` 只检查相对组件路径，并分别记录 ComfyUI 与 PyTorch 观察。默认分库把历史 `ComfyUI/...` / `Services/...` 前缀规范化为各自库内路径；Ollama、Audio 库不参与此扫描，防止形成虚假缺失记录。模型库路径不作为模型身份，也不发送到浏览器。 |
| 扫描页面 | 已交付 | 模型中心读取受控 discovery projection，按 Ollama/ComfyUI/PyTorch 原生运行时列出最近发现资源与候选能力；浏览器不接收绝对路径、endpoint、密钥或原始 adapter payload。 |
| 候选登记 | 已交付 | 管理员只能从成功扫描且 `PRESENT` 的观察显式登记 V2 ModelFamily、Release、RuntimeModelInstallation 与 `NOT_RUN` CapabilityOffering；重复登记幂等复用，绝不自动创建 Validation 或 ExecutionProfile。 |
| 候选就绪度 | 已交付 | `GET /api/v2/model-platform/registered-candidates` 按已登记的安装与 CapabilityOffering 输出安全、逐能力的门禁：发现验证状态、Profile 版本/发布计数、安装状态和明确 blockers。默认 `DISCOVERED` 安装与 `NOT_RUN` offering 只能显示“需要验证”，绝不显示为可分配；响应不含模型库路径、endpoint、native locator 或 secret。 |
| 候选就绪度页面 | 已交付 | 模型中心将“扫描证据”与“已登记候选的能力门禁”并列展示。一个模型可在两者同时出现，这是不同状态层，不是重复模型；页面以文字状态和 blockers 说明下一步，避免用颜色或“已发现”暗示可执行。 |
| Ollama 文本能力 smoke | 已交付（范围受控） | `POST /registered-candidates/{installation}/capability-offerings/{capability}:smoke` 只对已声明的 Ollama `LLM_*` capability 使用服务身份下的最小 JSON 推理探针。每次运行写入 `CAPABILITY_OFFERING` ValidationRun 和不可变、无 endpoint 的 evidence；仅该 installation 的全部 Offering 通过时才标为 `READY`，且同一 RuntimeVersion 的全部 Offering 通过才激活为 `ACTIVE`。任一失败会把 installation 标为 `VALIDATION_FAILED`、RuntimeVersion 降级为 `DEGRADED`。其他 Embedding、ComfyUI、PyTorch、音频/视频能力会以 `MP_CAPABILITY_SMOKE_IMPLEMENTATION_UNAVAILABLE` fail closed，不能被文本探针误标通过。 |
| PyTorch Embedding 能力 smoke | 已交付（范围受控） | 同一 capability smoke 端点已为受控 `PYTORCH_PROCESS` 的 `qwen3-embedding-8b / EMBEDDING_TEXT` 接入最终服务身份下的离线 Adapter。它实际加载 Qwen3 Embedding，要求离线回执、`4096` 维输出、至少三条样本和“相关文本相似度高于无关文本”的断言；evidence 只保留 Adapter、维度、数量、语义次序和无网络事实，绝不保存模型路径或向量。其他 PyTorch/ComfyUI/媒体能力仍 fail closed。 |
| Ollama 标准 Profile 模板 | 已交付（范围受控） | 已为已激活的 Ollama `LLM_*` Offering 实现版本化的 `ollama.text.profile.v1`。`POST ...:provision-profile` 只创建绑定同一 `RuntimeModelInstallation` 的不可变草稿，并固化 `ollama.chat.v1` Adapter、参数合同、资源策略和默认参数；`POST /profile-versions/{id}:smoke` 以服务身份执行最小 JSON 输出合同验证，且必须引用该能力真实 smoke 的 Evidence；`POST ...:publish` 仍须携带通过的 Profile smoke 记录和人工确认理由。扫描、能力 smoke 或创建草稿均不会自动发布。 |
| PyTorch Embedding Profile 模板 | 已交付（范围受控） | 已为上述已激活 Qwen3 Embedding Offering 实现 `pytorch.embedding.qwen3.profile.v1`，冻结 `LOCAL_PROCESS` Adapter、受控 instruction 参数合同、`PYTORCH` GPU/LOCAL_ONLY 资源策略和安装绑定。Profile smoke 再次运行真实离线 Adapter，并强制引用能力 smoke Evidence；通过后仍须人工发布。公共 Profile API 按持久化模板精确分发，不接受页面传入 executable、路径或 Python 参数。 |
| ComfyUI Offering → Workflow 绑定 | 已交付（范围受控） | `POST /registered-candidates/{installation}/capability-offerings/{capability}:bind-workflow` 只接收已发布的不可变 `WorkflowVersion` 标识。服务端强制目标为已完成完整性验证的 ComfyUI Offering、工作流合同能力完全一致，并在当前服务身份的 Comfy 节点清单与输入 schema 下重新验证；绑定、ValidationRun 和 Evidence 只记录版本/内容哈希和统计事实。模型中心按能力显示“已做 schema 验证的工作流 / 全部绑定”计数。此状态仅为 `SCHEMA_VALIDATED`，**不是**真实执行、不是出图/出视频证据、不能使 Offering、Profile 或业务菜单变为可执行。 |
| Profile 生命周期恢复 | 已交付 | `GET /profile-versions` 是受控的 V2 只读投影：仅返回继续生命周期所需的 ProfileVersion、能力、绑定的安装 ID、最后一次验证 ID/状态和 `DRAFT / PROFILE_SMOKE_PASSED / PROFILE_SMOKE_FAILED / PUBLISHED`，不返回模型路径、native locator、endpoint 或 Evidence。模型中心刷新、导航或浏览器重启后据此恢复“继续 smoke”或“确认发布”，不把草稿状态保存在页面内存里。 |
| smoke/Profile 页面操作 | 已交付（范围受控） | 模型中心只在已安装真实实现的 Ollama 文本能力和 PyTorch Embedding 能力旁显示“运行能力冒烟”。在 installation `READY` 且 Runtime `ACTIVE` 后，页面依次显示“创建标准 Profile → 运行 Profile smoke → 发布为可选能力”；每一步都是独立、带反馈的用户动作，发布后才刷新到可分配状态。 |
| ComfyUI / PyTorch 安装完整性 | 已交付（范围受控） | `POST /registered-candidates/{installation}:verify-integrity` 在最终服务身份下重新读取受控 `model-lock`，并重新检查对应模型库内每个相对组件路径及字节数。结果写入 `RUNTIME_MODEL_INSTALLATION` ValidationRun/Evidence；通过仅标记 `INTEGRITY_VERIFIED`，不会加载权重、不会宣称 Adapter 或能力可执行。模型库绝对路径不进入响应或 evidence。 |
| 完整性页面操作 | 已交付（范围受控） | ComfyUI/PyTorch 候选卡明确显示“完整性未验证/已确认/失败”，只在可验证且尚未通过时显示“验证安装完整性”。验证完成后仍保留能力 smoke、Profile 和发布门禁。 |
| 离线导入与可信下载计划 | 已交付（Host 执行） | 统一的 `GET /installation-plans`、`GET /installation-targets` 提供安全的 V2 控制面投影；`POST /installation-plans/offline` 只接受已登记且仍受当前服务配置管理的目标 Library、发布标识、无路径的离线包标识、许可证标识以及逐组件的库内相对路径/SHA-256/字节数；计划状态先为 `AWAITING_OFFLINE_IMPORT`，不读写文件、不向浏览器返回离线包标识或服务器路径。`POST /installation-plans/trusted-download` 仅在机器 allowlist 中精确匹配 HTTPS 主机时持久化组件 URL、目标 Library、许可证、包标识、相对路径/哈希/大小；创建响应和列表同样不回显 URL、包标识或路径，也不会联网。停机确认后的 `local-drama-maintenance trusted-model-download <plan> --confirm-host-stopped` 把经逐文件校验的包从 `downloads` 原子推进到 staging（`AWAITING_TRUSTED_DOWNLOAD → DOWNLOADING → AWAITING_OFFLINE_IMPORT`），失败写 `DOWNLOAD_FAILED` 和脱敏 Job code，绝不写 Library。随后 `offline-model-import <plan> --confirm-host-stopped` 要求 staging 内容与计划精确一致，复制前后均验证哈希，拒绝覆盖，失败转 quarantine 并标为 `QUARANTINED`，成功才为 `IMPORTED`。两条路径完成后仍必须重新发现、完整性验证、能力 smoke 与 Profile 发布。 |
| Profile 绑定/验证来源门禁 | 已交付 | V2 Profile 草稿 payload 必须列出 `runtime_model_installation_ids`；每个安装必须属于草稿的同一 RuntimeVersion、声明该能力、Offering 已 `SMOKE_PASSED` 且 installation 为 `READY`。Profile validation 除 payload hash 外，还必须引用同一 Installation/Capability 的、带 Evidence 的 `CAPABILITY_SMOKE` 运行记录；不满足时拒绝创建或记录验证。 |
| 提交 | 已交付 | `POST /api/v2/model-platform/executions` 要求 `Idempotency-Key` 和确认过的 resolution hash；提交在同一事务创建/复用 V2 snapshot、创建 `MODEL_PLATFORM_EXECUTION` Job、写入一对一链接。 |
| Profile 来源 | 已隔离 | 旧 `jobs.execution_profile_version_id` 是旧 Profile 外键，V2 不写入它；V2 Profile 来源只存在于 immutable snapshot 与 `mp_execution_job_links`。 |
| 模型绑定与执行绑定快照 | 已交付 | 迁移 `0077` 将 Profile 绑定的 RuntimeModelInstallation ID、Release code 和 native locator 写入 V2 execution snapshot；迁移 `0080_model_platform_snapshot_execution_binding` 再把 Profile-owned 的声明式 execution binding 一并冻结。创建快照时再次确认模型绑定仍属于同一 RuntimeVersion 且 `READY`；Worker 仅从冻结的模型/执行绑定读取，不会在执行时重新解析可变 Profile、工作流绑定、模型目录或 endpoint。 |
| Handler 声明 | 已交付（受控扩展） | Published Profile 不能自动等于 Worker 支持；必须精确匹配 capability + adapter 的声明，未声明即拒绝提交。生产 registry 已有 `EMBEDDING_TEXT + pytorch.embedding.qwen3`，并为 `comfy.workflow.v1` 的已验证 Comfy Profile 声明 `comfy.workflow.v2` GPU handler；Ollama、其他 PyTorch、音频等仍拒绝提交。 |
| Worker 消费 | 已交付（首个真实实现） | Worker 只通过 Job→snapshot 一对一链接读取冻结配置，并再次匹配 handler code/version、capability、adapter 后才调用实现。Qwen Embedding Handler 进一步要求唯一、冻结的 `qwen3-embedding-8b` 绑定、`LOCAL_ONLY` 网络策略、1—32 条文本与受控 instruction，输出通过 Windows 原子 rename 写入 `EMBEDDING_RESULT` JSON artifact。 |
| GPU 调度 | 已交付（Embedding 已接入） | 提交从 handler 声明冻结 `scheduler_runtime`（仅 `COMFY`/`OLLAMA`/`PYTORCH`），调度器据此申请资源；Embedding Job 以 `PYTORCH` 取得 GPU 独占租约；不读取页面临时值或全局 LLM URL。 |
| GPU_H3 的 V2/旧 Comfy 消费边界 | 已交付 | `JobService.claim` 现支持显式 job type 包含/排除过滤。旧 `ComfyGenerationService` 在 `GPU_H3` 通道明确排除 `MODEL_PLATFORM_EXECUTION`；WorkerSupervisor 在旧消费者无任务时把同一通道交给 `LocalMediaWorker`，使 V2 Worker 只能读取 Job→snapshot 的冻结合同。这样 V2 Comfy Handler 接入后不会被旧 workflow compiler 误领取或把 snapshot 当作 legacy workflow。该路由重构本身不宣称已有 Comfy Handler。 |
| ComfyUI 真实能力 smoke 合同与 Job | 已交付（受控范围） | 只有带 `smoke_contract` 的 `SCHEMA_VALIDATED` Binding 可提交。`POST ...:queue-comfy-smoke` 要求 Idempotency-Key，服务端在创建 Job 前核对 URL 所属模型/能力，冻结 Binding、WorkflowVersion、工作流内容哈希、合同哈希、固定语义输入、预期产物和 `COMFY` GPU runtime 到 `MODEL_PLATFORM_COMFY_SMOKE` Job（迁移 `0079`）。Worker 重新核对所有冻结事实、排队并等待 Comfy、按合同复制和登记产物；**artifact 已登记后**才写 `CAPABILITY_SMOKE` ValidationRun/Evidence 并推进 Offering/installation/runtime。缺合同、工作流变更、输入/时限不一致、数量/类型不符、Comfy 错误都会 fail closed，不能得到 `SMOKE_PASSED`。 |
| `smoke_contract` 语义校验 | 已交付 | `localdramastudio.comfy-smoke-contract.v1` 已在工作流登记时校验（仅当版本选择声明该合同）。合同只能引用已有 semantic node binding，必须覆盖工作流合同的必需输入，只允许短文本/有限标量、5—300 秒时限和 1—4 个 IMAGE/VIDEO/AUDIO 预期产物；绝对路径、URL、任意 JSON 和未绑定节点输入都会被拒绝。历史版本不因缺少该可选字段而失效，但不能用于后续 V2 真实 capability smoke。 |
| ComfyUI 冒烟与 Profile 页面操作 | 已交付 | 模型中心在 Comfy 安装完整性确认后按需读取已发布工作流和持久化 Binding。操作员先完成 schema binding，再选择该 Binding 提交“真实 Comfy 冒烟”；只有该**同一 Binding**具有 artifact-backed `SMOKE_PASSED` 证据时，按钮才允许创建标准 Comfy Profile。Profile 会冻结 Binding、WorkflowVersion/content hash、smoke contract hash、时限和单一主产物合同；页面只反馈 GPU Job ID，并指向任务中心/刷新后的 read model，绝不在浏览器请求中等待推理或显示工作流节点、模型路径、endpoint、固定 smoke prompt。 |
| ComfyUI 正式 V2 执行 | 已交付（单主产物范围） | `comfy.workflow.profile.v1` 只接受明确指定、`SCHEMA_VALIDATED` 且 artifact-backed real smoke 已通过的 Binding；Profile smoke 复核该证据链，人工发布后才可被能力解析。正式 Worker 从 Job→V2 snapshot 的 `execution_binding_json` 读取版本、内容 hash、smoke hash、时限和输出合同，重新核对发布工作流与 smoke contract 后才编译业务语义输入、排队 Comfy、复制单一输出并登记 `COMFY_OUTPUT`。当前故意只支持 1 个 IMAGE/VIDEO/AUDIO 主产物；多产物工作流必须先扩展 Artifact 合同和 Worker 返回协议，不能被静默丢弃。 |

这里的“已交付”不表示所有新模型已获生产可执行认证。生产 registry 当前只含 Qwen3 Embedding，以及满足上述严格 Binding/Profile/单主产物条件的 Comfy 工作流；Ollama、其他 PyTorch、SAPI 等必须连同真实 Worker implementation、AdapterBinding、smoke evidence 和 Windows 服务身份验收一起登记。否则 API 会以 `MP_EXECUTION_HANDLER_UNAVAILABLE` 拒绝入队，Worker 也会以 `MP_EXECUTION_WORKER_HANDLER_UNAVAILABLE` 拒绝未安装实现的冻结 Job。这是刻意的 fail-closed 门槛。

候选就绪度是**读模型**，不是验证执行器：它不会伪造 `SMOKE_PASSED`，也不会把 `DISCOVERED` 直接 promote 为 `READY`。已受控落地 Ollama 文本、PyTorch Qwen3 Embedding 与满足绑定合同的 ComfyUI 工作流的真实验证；Ollama 文本 smoke 成功且该 installation 的全部 Offering 通过后才允许变为 `READY`，整个 RuntimeVersion 的全部 Offering 通过后才可 `ACTIVE`；任一失败会把 runtime 降级。ComfyUI/PyTorch 的**完整性**验证本身只允许变为 `INTEGRITY_VERIFIED`，绝不等同于 runtime 或 capability ready。Profile 也不能只靠表单发布：它必须绑定已就绪 Offering，并从真实能力 smoke 的证据链取得验证来源。下一步仍须为未实现的 Ollama/PyTorch、音频、视频等 capability 分别交付真实 Adapter、smoke 和 Worker 证据；下载/安装同样必须作为可审计 Job 实现，不能从页面直接写模型目录。

Ollama 的四项文本能力（故事解析、分集规划、分镜、提示词重写）现已具备 `ollama.text.v2` Worker handler。它只消费已发布 Profile 冻结的 `ollama.chat.v1` snapshot、一个 READY 的 Ollama tag、`LOCAL_ONLY` 网络策略、受限的 `system_prompt`/`user_prompt` 与已解析参数；Worker 还会把 snapshot 中 base URL 与服务身份当前的 loopback 配置作精确比对。输出为受控 work_root 下的 JSON artifact。它不接收浏览器传来的 endpoint、模型名、路径或自由 JSON wiring；实际业务 surface 仍必须先完成独立的 cutover-only 输入/产物合同和 UAT。

下一批按本节顺序交付的真实实现必须各自证明输出 artifact/业务写入、取消、超时、GPU lease、重试语义和端到端 smoke；不得仅把 handler 声明加入 registry。

### 15.7 阶段 F：UI 与业务双读对比

已交付第一层只读审计：`GET /api/v2/model-platform/business-selection-shadow` 强制传入 `project_id`，用现有的旧生成偏好解析器（镜头 → 分集 → 项目 → 自动）与 V2 CapabilityAssignmentResolver 同时计算。响应另列出旧 `project_profile_bindings` 的初始化绑定事实，但不把它误当成当前业务选择结果；不会写旧表、不会双写、不会提交 Job，也不返回路径、endpoint 或提示词。

迁移 `0081_model_platform_profile_version_crosswalks` 已提供版本化、可撤销的人工批准 crosswalk：`POST /profile-version-crosswalks` 只接受两侧都已发布、规范 capability 完全一致的 Profile，并记录批准理由、操作人和审计事件；一侧已有生效映射时拒绝猜测或覆盖。批准前服务端还会比较旧 Profile override schema 与 V2 ParameterContract 的字段类型、必填、允许作用域、约束以及**有效默认值**；不返回参数值，任何疑似 path/endpoint/secret/token/key 等运行时字段都会拒绝比较。任一形状或默认值差异都以 `MP_PROFILE_CROSSWALK_PARAMETER_CONTRACT_MISMATCH` 拒绝映射，不允许人工理由绕过。双读只有命中这条映射时才返回 `MAPPED_EQUIVALENT`，否则明确返回 `LEGACY_ONLY`、`V2_ONLY`、`BOTH_BLOCKED` 或 `BOTH_PRESENT_UNMAPPED`。若请求含 CHARACTER scope，则返回 `LEGACY_SCOPE_UNSUPPORTED`，因为旧业务解析器没有该层级，结果只能观察不能作为 cutover 依据。

后续在测试环境中：

- 将该只读比较接入 Quick Create、Story/Breakdown、Asset Bible、Director/Shot 等业务 surface 的受控迁移面板。
- 为 `BOTH_PRESENT_UNMAPPED` 完成 ProfileVersion 合同人工核对后建立批准 crosswalk，并在每个 capability/scope 上记录差异。
- 通用 CapabilityPicker 在项目范围按需展开“V2 迁移双读对账”：显示旧/V2 resolver、映射状态及脱敏参数合同比较；任务仍走旧链。
- 不做双写。

差异必须归零或形成批准清单。

#### 15.7.1 当前迁移边界（2026-08-29）

当前交付停在**只读对账**，这是刻意的安全边界：创作业务的 `generation-preferences` 写入和旧 Job 提交仍只使用 V1 `GenerationPreferenceCommandService/QueryService`；V2 不会把不同身份的 ProfileVersion 直接返回给旧页面，也不会在页面保存时尝试双写。已建立的 crosswalk 只证明两份已发布 Profile 的能力和参数合同可以比较，**不等同于**允许切换运行时。

V2 参数层已补齐 Assignment 侧的不可变边界：迁移 `0083_model_platform_scope_override_set_versions` 新建 `ScopeOverrideSetVersion`，`CapabilityAssignment` 只引用其版本 ID，旧的 `override_json` 不再承载可变参数真相。仅 `EXPLICIT` Assignment 可保存范围参数；保存时必须使用当前已发布 Profile 的 ParameterContract、ProfilePolicy 和 scope 权限逐字段校验，并填写理由/操作人。每次修改创建新的版本、内容哈希与审计事件；解析和 execution preview 只读取 Assignment 引用的精确版本，并以 `PROJECT/EPISODE/SHOT_OVERRIDE` provenance 与 RunOverride 一起冻结。AUTO 继续拒绝 Profile 私有范围参数。

范围不是任意字符串：写入 `PROJECT`、`EPISODE`、`SHOT`、`CHARACTER` Assignment 前，V2 会分别验证项目、项目→季→集、项目→季→集→镜头，以及项目下 `ACTIVE CHARACTER` 资产的真实存在；解析/preview 若同时给出多个范围，还会验证它们归属于同一个项目，否则 fail-closed。这个中心 ownership boundary 是日后项目、集、镜头配置页共用的前置条件，页面不得自行猜测或拼接 scope。

为避免每个业务页再造一份模型选项，控制面已有 `GET /api/v2/model-platform/capability-assignment-catalog?scope_type=&scope_id=`：先执行上述 ownership 验证，再返回该范围可选的已发布 V2 Profile 与纯声明式 `override_fields`。SYSTEM 页面通过同一个核心投影适配为 `system_override_fields`。目录不返回运行时接线，也不投影 V1 偏好；后续项目/分集/镜头/角色配置页必须消费这个目录而非自行拼 Profile 列表。

模型中心已提供独立的 **SYSTEM V2 Assignment** 控制面（`GET /api/v2/model-platform/system-capability-assignments`）：操作员只能在当前已发布的 V2 Profile 中选择显式 Assignment，或恢复为 AUTO；可编辑项严格来自该 Profile 的非锁定、SYSTEM 允许且非敏感 ParameterContract 字段。页面不会展示模型路径、endpoint、密钥、Python/可执行文件或原生 locator；读取目录与参数合同共用同一运行时接线字段识别规则，连历史 `baseUrl` 这类 camelCase 字段也不会泄露。修改受控参数必须填写理由和操作人，并创建新的不可变 `ScopeOverrideSetVersion`；“恢复默认值”会移除该字段而不是写入一个伪默认。这个系统配置面只验证和配置 V2 解析，不会改写 `generation-preferences`，也不会让旧创作页开始使用 V2。

后续实施必须先把所有旧业务解析调用收敛到一个 `GenerationCapabilityConfigurationFacade`，并以持久化、可审计的 capability/scope rollout 状态控制 V2 是否能参与实际决策。该 Facade 只能在以下条件同时满足时把一次旧显式选择投影成 V2 Assignment：

- 所属 project/episode/shot 与 scope 一致；
- V1/V2 Profile 有仍生效的批准 crosswalk；
- capability、参数合同和有效默认值仍完全匹配；
- 参数仅映射为 V2 ParameterContract 允许的 override，且逐字段再次校验；
- 对应 V2 Profile 已发布、Offering/Runtime 已就绪，并且 V2 Worker 对该 capability + adapter 有已安装的 handler；
- rollout 状态明确批准该业务 surface 与 scope；否则保持 `LEGACY_ONLY`，记录审计/对账事实，绝不隐式回退或双写。

该收敛已从独立读模型开始：`GET /api/v2/model-platform/business-selection-facade-evaluation` 会将 shadow、crosswalk、参数合同、rollout、V2 preview 与精确 Worker handler 声明一次性求值。即使所有门禁都满足，它也只返回 `CUTOVER_CANDIDATE`，并固定 `execution_owner=LEGACY_V1`、`execution_switched=false`；它不会提交 Job、回写旧偏好或把 V2 Profile ID 传给创作页面。真正的业务命令接入只能新增明确的 cutover-only 入口，并在其上线前完成对应 surface 的端到端 UAT。

页面已将这份证据放入现有 CapabilityPicker 的按需“V2 迁移双读对账”区域：故事拆解（`story`）及资产的多视图、表情、细节生成（`assets`）会在展开后同时显示双读合同与 Facade 判断；“可进入切换候选”始终附带“当前提交仍固定走旧链”的文本。没有明确业务 surface 的泛用偏好编辑器不调用 Facade，避免由页面猜测业务语义。

在此集中入口完成真实切换前，任何 V2 Assignment 都只能用于 preview、shadow 或已独立接入 V2 执行合同的后台能力。`EMBEDDING_TEXT` 虽已有受控 PyTorch Handler，但它是 `project-knowledge` 的后台检索能力，不能据此宣称 Quick Create、分镜或素材生成已迁移。

#### 15.7.2 Windows ModelRoot 与安全控制面（已交付，2026-08-29）

配置合同已升级到 schema v3：`runtime.model_root` 是唯一的模型存储根，默认位于 `<InstanceRoot>/models`，其下 `downloads`、`staging`、`quarantine` 是操作区，四类 `libraries/{comfyui,pytorch,ollama,audio}` 才是默认可发现库。`runtime.model_download_source_hosts` 是机器拥有的可信 HTTPS 主机白名单，默认空列表（在线下载关闭）。v1 → v3 迁移只备份并改写配置，不移动、复制、删除或自动登记任何现有模型；已有自定义 library root 保持权威。

Windows Runtime Host 提供必须在 Host 停止后执行的 `configure-model-root --path <本地绝对路径>`：拒绝相对路径和 UNC，原子备份配置、创建规范目录、保留有效的自定义 library。安装器同样只创建默认目录，不下载/扫描/发布模型。V2 模型中心只显示存储策略、可发现库类别和操作区边界，浏览器绝不接收服务器绝对路径；实际路径配置只允许 Host CLI 完成。完整部署、升级、回滚和验收步骤以 `docs/release/windows-server-v2-model-platform-runbook.md` 为准。

受控 `model-lock` 仅适用于 ComfyUI/PyTorch 的组件发现与完整性验证：在默认四分库布局中，它将历史 `ComfyUI/...` 规范化到 `libraries/comfyui/...`，将 `Services/...` 规范化到 `libraries/pytorch/...`，数据库只持久化库内相对路径。Ollama 由 API 发现、Audio 由对应 Runtime Adapter 发现，均不由 model-lock 推断；旧的管理员聚合库保留兼容读取模式。这样“模型根的分类”与“谁有权观察哪一种模型”是同一个控制面规则，而不是页面上的标签约定。

可信 HTTPS 下载只接受机器配置中**精确匹配**的主机名、HTTPS、无凭据、默认端口、无 query/fragment 的地址；拒绝子域名替代和 HTTP 重定向。模型中心只在 allowlist 非空时允许管理员提交一次性下载计划，且 API 创建时只做来源策略校验和持久化，绝不发起网络请求；URL、包标识、主机名与服务器路径均不会出现在响应或计划列表。`trusted-model-download` 只能在 Host 停机确认后消费该计划，将每个组件写入 `<ModelRoot>/downloads/<受控包标识>/<库内相对路径>`，以 SHA-256/字节数验证后原子推进整个包到 staging；失败只保留脱敏 Job code，并绝不写入模型 Library。然后仍由 `offline-model-import` 执行精确 staging 校验与 Library 复制。在线计划、下载 Job 和离线导入 Job 共享 `mp_install_plans/mp_install_jobs` 的不可变审计边界，而不是把 URL 或文件写操作交给浏览器。

候选卡的当前状态不替代审计记录。`GET /api/v2/model-platform/registered-candidates/{id}/validation-history` 只读投影该安装及其 Offering 的不可变完整性/能力 smoke 事实：验证类型、范围、能力码、结果和完成时间。它**不**返回 ValidationRun 原始 payload、模型相对/绝对路径、runtime endpoint、native locator 或证书，因此 LAN 浏览器能解释“为何未就绪”，却不能把模型中心变成服务器文件浏览器或运行时配置泄露面。

#### 15.7.3 Quick Create V2 就绪合同（已交付只读预检，2026-08-29）

Quick Create 目前仍是 V1 业务提交面：它的计划、确认、Job 与产物记录均不得因模型中心页面或 Assignment 的存在而被悄悄改写。为使后续切换可验收，`GET /api/v2/model-platform/quick-create-v2-readiness` 以 `SYSTEM` scope 读取 V2 CapabilityAssignment，并对三种 Quick Create 模式固定计算以下能力：

- `TEXT_TO_IMAGE` → `IMAGE_CONCEPT`；
- `TEXT_TO_VIDEO` → `VIDEO_T2V`；
- `TEXT_TO_IMAGE_TO_VIDEO` → `IMAGE_CONCEPT` 与 `VIDEO_I2V`。

返回内容只包含模式、能力码、已解析的 V2 ProfileVersion 标识、`ready` 与脱敏 blocker；合同固定 `read_only=true`、`execution_switched=false`。`ready` 不再只是“有 Assignment”：单次 `TEXT_TO_IMAGE` 还必须绑定已发布的 `comfy.workflow.v1` Profile，其工作流只要求 `PROMPT` 这一必需语义槽位，且冻结的 execution binding 声明单一 `IMAGE` 产物；额外必需槽位、非 Comfy adapter、未发布 workflow 或非图像产物均明确阻断。`TEXT_TO_VIDEO` 和 `TEXT_TO_IMAGE_TO_VIDEO` 则固定返回 `QUICK_CREATE_V2_MULTI_STAGE_PIPELINE_REQUIRED`，因为提示词扩写、候选/首帧选择、跨 Job artifact hand-off 尚未有独立的 V2 聚合。页面按模式显示这些前置条件，但不将 V2 Profile 传入 V1 plan/commit，不创建 Job，也不写入任何 Assignment 或旧偏好。

首个 cutover-only command 已以受控的单次文生图形式落地：`POST /quick-create-v2/direct-image:preview` 只针对 `IMAGE_CONCEPT` 以 SYSTEM scope 计算 V2 resolution；`POST /quick-create-v2/direct-image:submit` 必须回传该 hash 和 `Idempotency-Key`，只接受 2—2000 字符的 `PROMPT` 与 V2 ParameterContract `RUN` overrides。它在创建 Job 前再次验证上述 Profile/工作流/产物合同，随后通过通用 `ExecutionSubmissionService` 冻结 V2 ExecutionSnapshot、创建 V2 Job 与专用 link；响应明确 `legacy_quick_generation_touched=false`，不调用 `QuickGenerationService`、不写 V1 quick run/Profile/Job。`GET /quick-create-v2/direct-image/jobs/{job_id}` 只接受这条 command 自己的 SYSTEM-scope、`quick-create-v2:image:` 幂等键、`comfy.workflow.v1`、`IMAGE_CONCEPT` V2 link；它只投影状态、进度、脱敏错误、snapshot 指纹及已验证 `COMFY_OUTPUT` 的受控下载 URL，绝不返回 sandbox 路径、泛用 Job 明细或 V1 run。页面将它放在迁移说明内的“V2 单次文生图试运行”，需先预检后确认，并轮询显示独立 V2 Job、已验证图片和下载入口。它是一个真实但窄范围的 cutover，不改变默认 V1 按钮。

`TEXT_TO_VIDEO` 和 `TEXT_TO_IMAGE_TO_VIDEO` 的真正 V2 cutover 仍需独立聚合：必须冻结 V2 提示词扩写步骤、候选/首帧选择、跨 Job artifact hand-off、视频产物归档与恢复语义，并完成该 surface 的 UAT。绝不以“就绪”API 或 UI 提示当作这些模式已切流的证据。

其首个复用基础已经独立落地：V2 Comfy Worker 现在识别快照中严格形如 `{"artifact_id":"…"}` 的 `FIRST_FRAME`、`END_FRAME`、`MIDDLE_KEYFRAME`、`REFERENCE_IMAGE` 输入引用。它仅接受 `MODEL_PLATFORM_EXECUTION` 产生、已验证的 `COMFY_OUTPUT`，并再次核验来源快照声明单一 `IMAGE` 输出；Worker 才会从受控 work root 复制并复哈希到隔离的 Comfy input root，再把生成的受控文件名编译入图。该机制不读取 V1 `quick_generation_*` 表，也不接受路径或 URL。V2 多阶段聚合会把候选选择冻结为这种引用，而不是把文件路径传回浏览器或伪造项目 MediaVersion。

`0086_model_platform_quick_create_v2_runs` 已为该聚合建立独立事实表：`mp_quick_create_v2_runs` 记录模式、原始描述哈希、幂等键、当前状态和唯一选择；`mp_quick_create_v2_steps` 对每一个提示词、候选图和下游视频阶段分别冻结 capability、ExecutionSnapshot、V2 Job、输入/输出 artifact、选择序号、内容哈希与脱敏错误。Step 只能关联 SYSTEM scope 的 `MODEL_PLATFORM_EXECUTION` Job/link；图片选择只能指向该 Step 自己产出的已验证 `COMFY_OUTPUT`。候选批次已经要求已发布 V2 图片 Profile 声明可选 `SEED` 槽位：每张候选都持久化独立 Seed、预检 hash、V2 Job 和 Step；Worker 在通用 artifact 注册完成后才回写 Step，并且整批都获得已验证图片才把 Run 推进为 `AWAITING_SELECTION`。这使恢复、重试和候选选择能在 V2 边界内追溯，不把 V1 quick run 当作事实来源。选择 API、I2V 下游 Job 与页面切流仍需在此表上实现，完成前视频路线继续保持未切流状态。

I2V 下游提交的最低合同也已固定：选择后的 Step 只能作为 `FIRST_FRAME` artifact 引用；目标 `VIDEO_I2V` Profile 必须为已发布 `comfy.workflow.v1`，必需槽位严格等于 `PROMPT` 和 `FIRST_FRAME`，且 execution binding 只声明一个 `VIDEO` 输出。任一条件不成立都不得创建视频 Job。该合同与 Worker 的 V2 artifact 输入物化共同保证浏览器不会传文件路径，运行时也不会从旧快速生成记录推断首帧。

#### 15.7.4 Project Knowledge / EMBEDDING_TEXT 的首个真实业务切换合同（实施中，2026-08-29）

`EMBEDDING_TEXT` 已有受控 PyTorch V2 Handler，但旧 `embedding_indexes` / `embedding_chunks` 只引用 legacy `model_artifact_id`，没有 V2 `ExecutionProfileVersion`、冻结 `ExecutionSnapshot`、精确 V2 Job 或 artifact 的可追溯关系。因此不能把 V2 向量直接写入旧表，也不能把旧表 status 当作 V2 成功证据。

首个真实 V2 business command 必须以独立的 `ProjectKnowledgeIndexRun`、`ProjectKnowledgeIndexBatch` 与 `ProjectKnowledgeVector` 持久化以下事实：

- run 固定 project、不可变 source document version/text hash、`EMBEDDING_TEXT` capability 和 V2 ProfileVersion；
- batch 在 Job 提交**之前**持久化 source offsets、text hash、ordinal 和 batch identity；冻结 semantic input 只引用该受控 batch identity 与最多 32 条文本；
- 每个 batch 绑定一个 V2 execution snapshot 和 Job，Worker 只从该快照读取 `texts`；
- `EMBEDDING_RESULT` artifact 验证后才写入 float32 vectors，逐条记录 source offsets/text hash；取消、失败或队列部分提交保持 run 未完成，绝不把部分向量标为 READY；
- 只有所有 batch 的 artifact、向量数量、维度和 source hash 都一致时，run 才能成为 `SUCCEEDED`；检索读模型只消费该成功 run；
- 重试产生新 batch attempt/Job 证据，不能覆盖既有已验证 vector；同一 source/profile 的幂等重放必须返回同一未完成或成功 run。

`0084_model_platform_project_knowledge_indexes` 已建立三张独立 V2 表，完全不复用 legacy `embedding_indexes` / `embedding_chunks`。当前命令面为：

- `POST /api/v2/model-platform/project-knowledge-indexes`：只接受 `project_id` 与已解析的 `source_document_version_id`；服务端受控读取文本、校验登记 hash、切块并持久化 `PREPARED` manifest。浏览器不能提交路径、向量、Python/endpoint 或 legacy model artifact；
- `POST /api/v2/model-platform/project-knowledge-indexes/{id}:queue`：对全部 manifest 预检同一已发布 V2 Profile，并在**同一数据库事务**中创建 execution snapshot、Job、V2 link 和 batch 的 snapshot/Job 绑定；
- Worker 将已登记 `EMBEDDING_RESULT` 反序列化并验证 schema、批次数、4096 维、有限 float 值、冻结 `texts` 与 manifest 的逐条一致性，才以 float32 写入 V2 vector 表。全部 batch 完成才把 run 标成 `SUCCEEDED`；列表 API 不返回源文本、路径或向量。

已入队 Job 若失败或取消，Worker 会将精确 batch/run 写为 `FAILED` 并保留 failure code；artifact 校验失败也走同一事实记录，后续完成的其他 batch 不能覆盖失败状态。`0085_model_platform_project_knowledge_retry_attempts` 为同一 source/profile 引入不可变 `attempt_no` 与 `retry_of_index_run_id`：成功或活跃 attempt 幂等复用；失败 attempt 只能新建下一次 run/batches/jobs，绝不原地重置、更改或覆盖既有 vector。首个 V2 检索读模型仍未接入，旧知识库读写也尚未切换。它是 Facade 之后首个可以从候选资格走向真实 V2 执行的业务面；Quick Create、资产和镜头生成继续等待各自语义输入、产物合同和 UAT 完成。

检索读侧现已独立于 legacy 表：`POST /api/v2/model-platform/project-knowledge-search` 只接收 project 与文本 query，先验证当前 `EMBEDDING_TEXT` V2 Profile/Handler，再以 `PYTORCH` GPU lease 在本机生成查询向量；只读取同一 Profile 下 `SUCCEEDED` run 的已验证 float32 vector。每个 source document version 选择最新成功 attempt，较晚的失败 attempt 不会抹除上一份已验证知识。响应只给出 run、不可变 source version、ordinal、Unicode offset 与相似度，不返回服务器路径、原文全文或向量；页面只在索引成功后提供这项端到端检索验证。

### 15.8 阶段 G：一次性 cutover

维护窗口步骤：

1. 停止接收新生成 Job。
2. drain Worker 和 Runtime。
3. 备份数据库、配置和 manifest。
4. 执行最终 migration/backfill。
5. 运行 integrity/referential checks。
6. 切换 API route、Web dist、resolver 和 Worker dispatcher。
7. 启动 Host，按服务身份扫描关键 runtime。
8. 运行 LLM、图像、视频、TTS/ASR 中已发布能力的 smoke。
9. 开放业务。

失败时：

- 停止新版本。
- 恢复数据库备份和上一 ReleaseRoot。
- 模型文件不回滚，因为 cutover 不应原地修改权重；staging 安装保持隔离。

### 15.9 阶段 H：退役

稳定观察一个发布周期后：

- 删除旧 UI 路由和组件。
- 旧 API 变为 410 或移除。
- 删除旧表写 repository。
- 删除直接注册脚本的生产文档。
- 清理确认无引用的 duplicate records。
- 保留 migration id map 和历史 evidence。

## 16. 开发工作包与依赖

### WP0：P0 契约与迁移基线

交付：

- 领域词典和 capability seed
- 当前数据导出/对账工具
- Published payload immutability test
- manifest bundle attribution test
- cutover/rollback runbook

完成标准：当前 8 个 P0 都有自动化失败用例或迁移门禁。

### WP1：V2 schema 与 repository

交付：

- 4.2 所列实体
- migration
- repository contract
- id/hash/version/state constraints
- audit integration

依赖：WP0。

### WP2：Windows Node、Library 与路径

交付：

- ModelLibrary 管理
- library + relative path resolver
- ACL/space/reparse/long-path preflight
- installer 自动建目录
- service identity doctor

依赖：WP1；与 WP3 可并行。

### WP3：Runtime Adapter SDK

交付：

- Adapter interfaces/protocol
- Host-managed/external runtime lifecycle
- typed errors/evidence
- Ollama、ComfyUI、PyTorch、SAPI、FFmpeg、Remote skeleton
- Worker capability advertisement

依赖：WP1。

### WP4：发现与完整性

交付：

- DiscoveryRun/Observation
- Ollama tags/show
- Comfy bundle manifest
- PyTorch directory manifest
- incremental hash queue
- classification UI/API

依赖：WP2、WP3。

### WP5：下载与安装

交付：

- DownloadPlan
- license/space confirmation
- range/resume
- staging/quarantine/atomic promote
- offline import
- reference-count cleanup

依赖：WP2、WP4。

### WP6：Capability、Profile 与参数合同

交付：

- 唯一 CapabilityDefinition
- Offering
- JSON Schema/UI Schema
- AdapterBindingContract
- Profile state machine/publication
- capability-specific smoke framework

依赖：WP1、WP3、WP4。

### WP7：Assignment 与执行解析

交付：

- CapabilityAssignment
- scope inheritance
- effective parameters/provenance
- ResolvedExecutionSnapshot
- preview/submit API
- old preference migration

依赖：WP6。

### WP8：PyTorch 生产接入

交付顺序：

- Embedding handler/index lifecycle
- ASR handler
- Alignment handler
- VoxCPM TTS handler
- Voice Clone handler
- LatentSync handler

每项包含 schema、binding、resource、cancel、evidence、UI readiness。

依赖：WP3、WP6、WP7。

### WP9：模型平台前端

交付：

- 六个系统路由
- 模型/能力/运行时/存储详情
- 自动菜单
- 状态矩阵
- 后台任务进度
- ParameterFormRenderer
- 项目 model evidence 页

依赖：WP1 API read models，可与 WP8 并行。

### WP10：业务页面迁移

交付：

- Quick Create
- Story/Breakdown
- Asset Bible
- Director/Shot
- Dialogue/Voice
- Subtitle/Timeline
- QC/Post
- Project/Episode/Shot preferences

全部使用 CapabilityResolver/AssignmentSelector。

依赖：WP7、WP9；各业务 surface 可并行。

### WP11：Windows 发布与 cutover

交付：

- 自包含 release payload
- installer/upgrade/rollback
- service identity runtime setup
- clean Windows Server VM UAT
- migration/cutover automation
- old surface retirement

依赖：WP2–WP10。

### 建议并行关系

    WP0 → WP1
            ├→ WP2 → WP4 → WP5
            └→ WP3 → WP4
                     └→ WP6 → WP7 → WP10
                              ├→ WP8
                              └→ WP9
    WP2 + WP8 + WP9 + WP10 → WP11

## 17. 测试与验收

### 17.1 领域与数据库

- ModelRelease identity 不含绝对路径。
- 同 Artifact 可有多 location。
- directory model/shard 正确聚合。
- Component 共享引用正确。
- Capability code 唯一且前后端一致。
- Job Type 不与 Capability 混用。
- Published Profile payload 不能 update。
- capability/model/runtime/workflow/schema 改变必生成新 ProfileVersion。
- 同一 Ollama model 的不同 capability 产生不同 Profile。
- migration id map 完整、幂等。

### 17.2 P0 回归

必须有专门测试：

1. Qwen Image Profile 的 component 集合不含 MiniMax/ACE-Step。
2. MiniMax Profile 的 component 集合只含其 route 所需组件。
3. 重扫 Ollama 不修改已发布版本。
4. 重发 SAPI voice 不覆盖已发布版本。
5. qwen3.8:27b 的故事解析与 QC Profile 共存。
6. EMBEDDING_TEXT 能建立 Offering/Profile/Job，而非仅存在资源映射。
7. Candidate PyTorch model 不出现在业务可执行菜单。
8. 项目 model evidence 从新路由可读写。
9. 任何生产 Release config 不含源码/开发 venv/E-F 固定路径。
10. 参数最终值只有一条可解释 provenance 链。

### 17.3 Adapter 合同

每个 Adapter 运行同一合同测试：

- discovery 幂等
- incomplete scan 不误标 missing
- digest drift
- runtime unreachable/recover
- model load failure
- timeout/cancel
- progress/heartbeat
- output schema
- secret redaction
- network policy
- release cleanup

### 17.4 模型发现与存储

- Ollama tags/show 增量调用。
- Comfy multi-component bundle。
- PyTorch directory manifest。
- 共享 VAE/Text Encoder 去重。
- 100GB 级文件异步 hash、可取消、可恢复。
- reparse point 越界被拒绝。
- 路径大小写和 Windows 保留名。
- 盘符移除/恢复。
- 跨卷移动的 copy-verify-promote。
- 空间不足不开始下载。
- 哈希失败进入 quarantine。

### 17.5 参数与解析

- 每种 Capability schema 正反例。
- Profile lock 不可被 run override 越过。
- Project/Episode/Shot/Run precedence。
- AUTO 不接受 Profile 私有参数。
- stale resolution hash 被拒绝并要求重新预检。
- Comfy 只通过显式 binding 修改节点。
- provenance 与最终值一致。
- Job snapshot 与重试/重放一致。

### 17.6 Worker 与 GPU

- 单 GPU heavy lease 互斥。
- Ollama → Comfy → PyTorch 切换不残留 VRAM。
- READY/BUSY/DEGRADED 状态准确。
- Worker 宣告 handler 与 Published capability 对齐。
- 未支持 Job Type 在发布前被阻止，不在运行时才失败。
- cancel 终止子进程并保留 Attempt evidence。
- crash recovery 不覆盖旧 Attempt。
- RAG retrieval CPU 路径不误占 GPU。
- V2 提交在同一事务写入 snapshot、Job 与一对一链接；相同幂等键只能重放相同 snapshot/handler，不得重绑。
- V2 Worker 只能经 Job→snapshot 链接读取冻结 runtime 配置；不存在精确实现时必须 fail closed，不能回退到旧 Profile 或全局端点。

### 17.7 Windows Server

至少在干净 Windows Server VM 验收：

- 无 Git、Node、系统 Python、开发 venv 仍可安装启动。
- Program Files release 只读。
- ProgramData 和数据盘 ACL 正确。
- 非 LocalSystem 服务身份。
- 重启后 API/Worker/Host/受管 runtime 恢复。
- 服务身份可见 Ollama model root、SAPI voice、Credential reference。
- 交互用户与服务用户不同也不会读取错误目录。
- LAN 客户端不能浏览任意服务器路径。
- 防火墙只开放配置端口与可信网段。
- upgrade/rollback 不覆盖 DB、projects、models、secrets。
- 断电/kill 后下载、SQLite WAL 和 staging 可恢复。

### 17.8 页面验收

视口：1440、1280、1024。

- 模型库默认分类清晰，可按 runtime 切换。
- 当前只有一个 Ollama tag 时明确显示“扫描到 1 个”，同时可看到 Comfy/PyTorch 在各自分类。
- 组件不会伪装成独立创作者模型。
- 状态不是单一“可用”，阻塞原因可解释。
- 系统/项目职责清晰。
- 下载离开页面仍继续。
- 长列表搜索、筛选、分页/虚拟化。
- 键盘导航、焦点、label、aria-live。
- 不依赖颜色表达状态。
- Candidate/Published/Runtime Offline 有不同文案。
- 参数表单能显示继承源、锁定值和重置。
- 业务页不出现路径、endpoint、node id、secret。

### 17.9 端到端能力验收

每个已宣称可执行的能力至少一条真实本机链：

| 能力 | 最小真实证据 |
|---|---|
| LLM_STORY_PARSE | Ollama 结构化输出通过 schema |
| IMAGE_CONCEPT | Comfy 生成 VERIFIED image Artifact |
| VIDEO_I2V | Comfy 生成 VERIFIED video + ffprobe |
| AUDIO_MUSIC | ACE-Step 输出 VERIFIED audio |
| EMBEDDING_TEXT | 建索引、query 命中、维度与 fingerprint 一致 |
| TTS | VoxCPM/SAPI 输出 VERIFIED WAV |
| VOICE_CLONE | 合法参考证据 + 输出 + speaker binding |
| ASR | 音频 → transcript + timestamps |
| AUDIO_ALIGNMENT | transcript/audio → word timings |
| LIPSYNC | video/audio → VERIFIED synced video |

机器冒烟证据不替代人工审美批准；项目许可证证据也不替平台作法律判断。

### 17.10 最终发布门禁

只有全部满足才 cutover：

1. P0 回归全绿。
2. 旧数据 100% 映射或有批准阻塞清单。
3. 已发布 Profile 均有 handler/readiness。
4. V2 resolver 与预期业务选择一致。
5. 干净 Windows Server 安装/升级/回滚通过。
6. 无生产路径依赖源码、开发 venv 或固定盘符。
7. 关键能力真实 smoke 通过。
8. 新 UI 三档视口和可访问性通过。
9. 备份与回滚演练通过。
10. 旧写路径在代码和文档中退役。

发布前的离线结构门禁还必须验证所有 `PUBLISHED` V2 Profile 的 `capability + AdapterBindingContract` 都能解析到当前 release 声明的 Worker handler；缺少 handler 的 Profile 即使历史 smoke 已通过，也不得激活发布。此检查只比对 SQLite 合同和代码注册表，不加载模型、不联系 Runtime、也不输出 adapter/路径细节。

## 18. 最终判断

用户提出的“模型应该自动列出来、归类清楚、知道放哪、哪些页面用哪一个”是正确方向，但不能只靠给 Ollama 列表再加几个卡片完成。

本项目的正确长期结构是：

    统一 Model Platform 领域
    + Runtime Adapter 插件边界
    + Windows 节点与 ModelLibrary
    + 不可变 Capability/Profile/Parameter 合同
    + 持久化 Discovery/Download/Validation Job
    + 单 GPU 确定性调度
    + 业务页统一 Capability Resolver
    + 可重现 Job Snapshot 与证据

当前模型的清晰归属是：

- **ComfyUI**：Qwen Image、Qwen Image Edit、MiniMax H3、ACE-Step 及其工作流组件。
- **Ollama**：当前 qwen3.8:27b；以后由 Ollama 原生管理并通过 tags/show 发现的 LLM/VLM/Embedding。
- **PyTorch 专用 Adapter**：Qwen3 Embedding、VoxCPM2、Qwen ASR、Forced Aligner、LatentSync。
- **OS/工具运行时**：Windows SAPI、FFmpeg 等。
- **远端服务**：明确允许出境的 OpenAI-compatible Provider。

其中 Embedding 是检索基础设施能力，不是内容生成模型。它通常在后台由系统或项目索引策略选择，而不是让用户在每次创作时手工选择。

“怎么只有一个模型”的当前直接答案是：Ollama 实际只安装/暴露了 qwen3.8:27b，其他权重属于 ComfyUI 或 PyTorch，不会出现在 Ollama tag 列表。重构后的模型平台会在同一系统中心看到全部模型，但仍保持它们各自正确的运行时和能力边界。

本设计不是继续打补丁。开发应按工作包建设完整 V2，在数据、Adapter、Worker、页面和 Windows 发行物全部验收后一次性切换。这样后续新增模型只需要：

1. 增加或发现 ModelRelease/Installation。
2. 由 Adapter 声明 CapabilityOffering。
3. 绑定已有或新增 Parameter/Adapter Contract。
4. 冒烟并发布新 ProfileVersion。
5. CapabilityDefinition 自动决定系统菜单和可用业务页面。

不再需要为每个新模型修改多个页面、硬编码盘符、复制参数表单或直接改 SQLite。这才是能够持续升级并稳定部署到 Windows Server 的模型架构。
