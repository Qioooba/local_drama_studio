# 整剧分集成片批量 AI 超分：详细开发设计

版本：1.1 · 2026-09-21 · 状态：主链路已实现，真实 NCNN/GPU 与人工验收待执行；实时进度见 [实施状态](implementation-status.md)。

配套：[测试与验收](test-and-acceptance.md)、[实施交接](sol-handoff.md)。

## 1. 目标、范围与完成定义

### 1.1 用户场景

一部漫剧有多集，每集已生成或交付 480p 成片。用户在项目内看到所有分集，勾选其中若干集或所有可处理集，应用统一的 1080p 方案，检查实际输入输出和资源需求后提交。关掉页面后继续处理，回来可以查看逐集进度；失败只处理失败项。用户对比结果，审核并采用满意版本，再生成对应的正式交付包。

“最终成片”包括已登记的整集渲染版本及已登记交付包中的主视频。直接读取项目登记和 manifest，不扫描某个目录就把所有 MP4 当作当前成片。

### 1.2 首发必须完成

- 项目级分集列表、多选、跨页全选符合条件项、按季/状态/分辨率筛选。
- 1080p 一键预设、用户预设版本、项目默认、本批调整、逐集覆盖。
- 可配置本机执行程序、模型目录、权重版本、支持参数和资源策略；发布前真实 smoke。
- 专用 AI 超分执行器，至少一条动漫模型真实可运行路线；同适配器可选择已验证的其他模型。
- 短片段试跑、同步对比、实测耗时估算。
- 持久批次、独立逐集任务、优先级、暂停/恢复/取消/失败重试、崩溃恢复、磁盘预算。
- 有来源的新成片版本、机器 QC、人工审核、采用、原版回退、批量正式打包。
- 原有镜头生成、剪辑渲染、单视频 FFmpeg 后处理和交付功能兼容。
- 合同、迁移、后端、前端、浏览器和真实 Windows/GPU 证据。

### 1.3 首发边界

首发支持本机受管单 GPU、SDR 8bit、逐行扫描、可判定 CFR 的常见 MP4 成片。VFR、HDR/10bit、隔行、未知旋转/像素比例、多个视频流等输入必须明确阻塞并指出原因，不以静默转换冒充“保留原片”。非标准输入转换可以后续单独加版本化正规化步骤。

首发不要求任意模型生态全部接入、多 GPU 并行、远端付费 API、镜头级自动重生成、实时视频超分或自动润色人物。PyTorch、专用时序超分和 ComfyUI 是后续适配器扩展位；首发可用列表只出现通过当前适配器及长视频合同验证的方案。

首发支持自定义**执行器包配置**，不承诺任何随意上传的 Python/PowerShell 脚本都会自动兼容。自定义包必须实现本设计的输入、输出、进度、取消和恢复协议，并经验证发布。

### 1.4 Definition of Done

使用真实低清成片完成：多选两集 → 应用预设 → 试跑 → 检查 → 入队 → 离开页面 → 恢复进度 → 一项失败后重试 → 得到新版本 → 对比审核 → 批量采用 → 批量打包 → 验证交付文件确实为目标像素、完整音视频且来源可追溯。只完成按钮、CLI 命令、模型名称枚举或输出临时 MP4 均不算完成。

## 2. 当前代码事实与接入点

以下事实来自 2026-09-21 源码勘察；实施时以最新符号为准。

| 当前模块 | 已有事实 | 本功能的使用方式 |
|---|---|---|
| `apps/web/src/pages/DeliveryPage.tsx` | 按集交付；`package` 视图通过 `reviewInbox(...episode_id...)` 向后处理面板提供视频 | 保留已有功能；增加成片版本选取和进入整剧工作区的链接 |
| `features/generation/PostProcessPanel.tsx` | 单个 `input_media_version_id`，预检和后台增强；默认保持源尺寸 | 不扩展成全剧数据源；明确其“单素材后处理”定位 |
| `application/timeline.py::_validated_enhancement_steps/run_enhancement` | `SCALE`、补帧、降噪、防抖、LUT、QC、编码；执行器固定 FFmpeg/ffprobe | 老增强链继续兼容；不得把 SCALE 显示为 AI 超分 |
| `timeline.py::_upscale_to_delivery` | `UPSCALE_COMPOSE` 在整集合成时使用 Lanczos 达到交付尺寸 | 新功能独立控制交付派生目标，不能通过修改项目生成规格来代替 |
| `timeline.py::_register_render` | 整集成片写 `episode_render_versions`，并不是普通 `media_versions` | 为超分输出提供明确的派生注册服务 |
| `application/episode_render_approval.py` | 交付只接受当前最新成片且最新人工决定有效；最新按 `created_at,id` 排序 | 必须改造成“当前合成主版本 + 显式选中的交付派生版本”语义 |
| `application/background_operations.py::delivery_plan/submit_delivery` | 预检冻结 render、target、approval，再创建 `DELIVERY_BUILD` | 批量打包复用；补全派生版本校验与幂等链接 |
| `timeline.py::build_delivery` | 以整集渲染为源，可能再烧水印，产生 immutable package、manifest、delivery_files | 必须校验最终输出规格，避免超分后二次降回 480p或重复水印 |
| `model_platform/domain/capabilities.py` | 已有 `UPSCALE_VIDEO`、`UPSCALE_IMAGE` | 复用 `UPSCALE_VIDEO`；不新造 `VIDEO_SR_V2` 能力 |
| `model_platform/application/production_execution_registry.py` | 已有通用 Comfy 能力声明与执行 handler；能力名存在不等于专用长视频超分已闭环 | 增加 NCNN 专用适配器和 handler，不宣称现有通用 Comfy 已满足本场景 |
| `ExecutionPlanningService/ExecutionSnapshotService/ExecutionSubmissionService` | 参数合同、Profile 发布、不可变执行快照和 `MODEL_PLATFORM_EXECUTION` Job | 所有正式模型执行继续走这条链，新增批量原子提交支持 |
| `ExecutionPreviewRequest` | 目前通过作用域 assignment 解析 Profile，无本次显式 Profile 字段 | 增加可选 `execution_profile_version_id`，仅影响当前请求，不临时改项目绑定 |
| `WorkerExecutionHandlerRegistry` | handler 当前只接收 snapshot 和 output_root | 增加向后兼容的执行上下文，传递进度、取消、checkpoint，覆盖现有 handler 回归 |
| `application/jobs.py` | Job/attempt、lease、进度、暂停恢复、批量动作、artifact | 复用任务事实；新增批次聚合，不另起无持久化队列 |
| `job_resources.py/gpu_runtime.py` | 单物理 GPU 互斥；运行时区分 COMFY/OLLAMA/PYTORCH/LLAMA_CPP | 新增 NCNN 运行时和生命周期适配器，仍竞争同一物理 GPU lease |
| `RuntimeKind` | 已有 `TOOL_PROCESS`；组件有 `UPSCALER` | NCNN 使用 TOOL_PROCESS，不把它假装成 PyTorch |
| `routeRegistry.ts/router.tsx/layouts/AppShell.tsx` | 项目与分集路由分离；目前无项目级整剧交付页 | 同时更新路由注册、上下文解析、侧栏、面包屑和测试 |
| `scripts/generate_client.py` | 生成 OpenAPI 和包含手工模板的 TS client | 新合同必须更新生成器并生成，不只手改 `generated/api.ts` |
| `design-system/localdramastudio/MASTER.md` | 专业工作站、暖中性色工作区、深色媒体对比、语义色 | 作为唯一视觉基础，不另造风格体系 |

### 2.1 必须特别处理的现有耦合

当前“最新 render 就是当前成片”会导致两个问题：第一个超分候选生成后可能挤掉原版；第二个实验候选又可能挤掉已批准的第一个候选。因此不能仅给 `episode_render_versions` 插入一行而不修正读取与审批规则。

必须审计以下所有查询：`episode_render_approval.py`、`timeline_status.py`、`g8_readiness.py`、`post_repository.py`、`product_context_repository.py`、`review_decision_repository.py`、`worker_handlers/automation_task.py`、`TimelineService._existing_render`，以及 `FROM/JOIN episode_render_versions` 全部命中。计数需区分合成版本与交付派生版本；合成缓存不能命中超分版本。

现有 `_register_render` 会按旧 `production_spec` 验证宽高；480p 源的超分结果不能照抄旧快照后直接调用此方法。新增 `EpisodeRenderDerivationService`，冻结独立的 `output_spec`，明确 source 与 delivery 的关系。

### 2.2 同日成片修订的兼容要求

文档编写期间工作区又出现成片相关修订，已核对源码差异，实施必须保留：`RENDERER_CONTRACT=TIMELINE_CURATED_AUDIO_AND_SUBTITLE_V6`；render snapshot增加`source_audio_policy`、`subtitle_mode`、`subtitle_burned_in`；模型原声默认不混入最终成片；production spec增加默认关闭的`allow_cross_orientation`；交付要求烧字幕时检查冻结烧录证据。

因此本功能“保留声音”是保留**已经批准的最终文件音轨**，不能重新从镜头视频导出模型原声或重新执行时间线混音。字幕状态优先使用这些已有冻结字段，不另造互相矛盾的布尔值；派生快照必须携带来源字段并由共享resolver解释。

另有[同日最终成片复核记录](../../evidence/final-video-av-audit-2026-09-21.md)记载某集854×480文件的有效内容约272×480，源于竖屏被置入横屏画布，并记载内容审核问题。此为已有审查文档的发现，本次没有重复实测该视频。它说明列表不能只显示“480p可超分”：预检应抽样检测大面积稳定边框，返回`SOURCE_LARGE_BORDERS`质量提醒和内容区域预览，推荐先修正方向重合成；用户显式接受后才处理带边框源，不能自动裁切或承诺超分消除错误构图。源若当前已被拒绝/失效，仍遵循不可选规则。

边框检测首发可采用12个均匀时间点，排除近全黑转场，稳定有效样本中至少80%存在一致边框且边框面积超过画布30%时提醒；少于4个有效样本只标“无法可靠估计”。阈值可在版本化QC策略配置。此为启发式质量提醒，不能凭它拒绝正常暗部/电影画幅；记录截图和置信依据，由用户判断。

## 3. 信息架构与页面设计

### 3.1 入口归属

| 位置 | 名称与动作 |
|---|---|
| 项目侧栏核心入口，核心资产之后 | “整剧交付”，任何已选项目都可进入，不依赖当前 episode |
| 项目首页分集区域 | “查看整剧交付”，显示已有成片数、待处理数 |
| `/projects/:projectId/delivery` | 项目级工作区，`view=episodes` 默认；`view=queue` 批次队列；`view=versions` 版本与交付 |
| 单集 `DeliveryPage` | 在成片摘要处展示“480p → 超分至 1080p”；链接携带 `episodeId` 初始定位 |
| 项目设置 → 交付 | “成片超分默认方案”，保存项目默认和预设，不启动任务 |
| 能力与模型 → 模型与服务 | “视频超分引擎”，接入、验证、发布；专家配置修改程序包与合同 |
| 后台任务 | 按 `UPSCALE_VIDEO`/业务 stage、项目、集、批次筛选，链接回业务工作区 |

建议路由帮助函数 `routes.projectDelivery(projectId, view?)`。单集 route `routes.delivery` 保留。`parseRouteContext` 要将新路由识别为 PROJECT scope，清空 episodeId，不沿用上次选中集误过滤整剧。

### 3.2 工作区布局

```text
项目名 / 整剧交付                 分集成片 | 超分队列 | 版本与交付
成片 24 集  可处理 18 集  待审核 3 集  队列 2 集    [刷新]

季 [全部] 状态 [待超分] 原分辨率 [低于目标] 搜索 [集名/编号]
[ ] 本页可处理项    [选择全部符合条件的 18 集]    已选 8 集

分集列表（主区域）                         本批超分设置（右侧）
□ 第1集  854×480  06:32  已确认交付        预设 [漫剧 1080p]
  来源：交付 v2  → 1920×1080              模型 [动漫视频 · 已验证]
□ 第2集  480×832  05:18  已批准成片        输出 [1080p · 跟随横竖]
  来源：渲染 v3  → 1080×1920              质量 [标准] 帧率 [保持]
□ 第3集  1920×1080  已达标                声音 [保留] 字幕 [继承]
  原因：已达目标；可查看现有版本            [高级参数] [逐集差异 1]
                                          [试跑 5 秒] [保存为预设]

已选 8 集 · 42分18秒 · 磁盘待检查        [检查所选 8 集]
```

检查完成后主按钮变为“将可执行的 N 集加入队列”，显示新建、已在队列、可复用、已达标、阻塞数量。默认不悄悄排除阻塞项；用户可点击“仅保留可处理的 N 集”，生成新的确定选择和检查计划，再提交。

1440×900 保留列表和约 340–380px inspector；1280×800 收窄非关键列；1024×768 收起全局侧栏，设置区域移到列表下方，底部操作栏保持可达，不出现页面级横向滚动。移动设备提供状态查看，不要求复杂质量审核。

### 3.3 列表数据与选择规则

每集一行，默认按季顺序、`display_order/number/id` 稳定排序；不要按创建任务时间重排剧集。列包含：勾选、集号标题、来源版本及审核状态、实测像素/帧率/时长、目标像素、最新超分状态、采用状态、动作。

- 默认 `source_policy=PREFER_FINAL_DELIVERY`：优先使用当前有效且人工确认的交付包主视频；没有符合条件的交付包时使用当前已批准合成成片。展示实际选择，不能用退回历史已批准版本隐藏最新版本未批准的问题。
- 用户可逐集选择“已确认交付文件”或“当前已批准合成成片”。系统默认不把上次超分结果再作为超分输入，防止连续增强。
- 同一集同一批只能选一个输入；需要比较模型时分别创建试跑或新批次。
- 不存在成片、源过期、最新决定拒绝、包撤回、完整性不通过、输入格式不支持的行禁选并提供原因与跳转。
- 已达目标默认排除；“仍按本方案重新增强”放高级选项，要求显式新批次模式。不能只看文件宽高就认为完成过 AI 超分。
- 表头 checkbox 只选本页可处理项；“全部符合条件”由服务端固化候选 ID 集合和版本，不靠浏览器已加载的 50 行。
- 改筛选时保留已选集合但明确提示“其中 X 集不在当前筛选”，提供查看已选和清空；跨项目全部清空。新出现在筛选结果里的集不自动加入既有选择。
- 分页默认 50，最大 100；单批上限默认 200 集，可由服务端运维配置调整。超过上限返回总数和分批建议，不能静默截断。

### 3.4 队列视图

批次摘要：名称、预设版本、模型、提交时间、总集数、已完成/运行/等待/失败/暂停/取消、真实累计进度、预计剩余时间及估算依据。点开显示逐集 Job、阶段、完成帧数、速度、错误、重试和对比。

批次操作：“暂停待处理项”（当前运行项继续）、“暂停全部”（包含当前任务）、“恢复”、“重试失败项”、“取消未完成项”。运行项的暂停/取消显示“正在停止，已完成分块将保留”，不宣称 GPU 内核可以瞬间中断。

成功表示超分处理及 QC 完成；显示“超分完成，待审核”。审核、采用、打包是独立状态，不把 Job 成功显示成“已交付”。

### 3.5 对比、审核、采用

- 点击结果进入同页全宽对比区或可深链详情 `?view=versions&renderId=...`。
- 原片和结果同一时间轴、默认只播放一侧声音；支持同步暂停/seek、A/B 切换、100% 局部裁切、统一输出尺寸的原片插值参考。
- 默认 poster，点击才请求预览；不能为每行自动下载两段完整视频。质量判断可查看原始结果，低码率预览要标“预览代理”。
- 试跑必须显示时间范围；5 秒样片不进入整集正式交付列表。
- 审核项：细线/文字、人物脸部、闪烁/纹理跳变、字幕水印、声音同步、首尾完整、画幅和裁切。
- 人工批准后可采用到该目标档位；提供显式批量审核入口，但每集必须独立确认检查项，未检查的项目不能被“全部勾选 PASS”。
- 批量“采用已批准结果”和“为已采用版本打包”需要完整预检，后续继续在后台执行。批量采用可以统一操作，不隐式代替审核。

### 3.6 UI 状态与可访问性

覆盖初次加载、无集、无成片、无模型、配置不兼容、检查中、计划过期、排队、暂停中、处理失败、待审核、已采用、源已更新。错误就近显示到字段/分集行；主按钮禁用原因和修复链接可读。使用语义 checkbox、半选状态、键盘操作、焦点恢复和文本状态；长表超过 50 可见项使用现有方案或虚拟化。表单分基本/高级/专家三级，所有参数都有名称、单位、默认值和生效范围。

## 4. 配置体系与一键预设

### 4.1 四层配置

| 层级 | 保存内容 | 生效方式 |
|---|---|---|
| 运行环境/模型版本 | 程序、脚本、权重及依赖定位，hash，adapter、参数合同、GPU、能力验证 | 全局能力与模型；发布不可变版本 |
| 预设版本 | 显示名、适用风格、模型引用、输出/编码/资源默认 | 内置只读；用户修改派生新版本 |
| 项目默认 | 预设版本及允许覆盖项 | 仅后续草稿/批次，不改生成规格、不改历史任务 |
| 本批/单集 | 本次显式参数和逐集差异 | 预检解析后冻结；已入队不能原地改参 |

默认值解析顺序：Profile 参数默认 → 预设 → 项目默认覆盖 → 本批覆盖 → 逐集覆盖。Profile 的 locked 值始终强制；禁止覆盖字段报错。预设/项目值作为有来源的 RUN 输入参与现有参数解析时，要额外保留来源链，不把合并后的值全部伪装成“模型默认”。

输出几何、编码、资源参数由受版本化的**流水线参数合同**校验；模型特有参数由 `mp_parameter_contract_versions` 校验。两者冻结到同一超分计划，避免向模型参数里塞不支持的任意字段。

HTTP schema和发布参数schema都拒绝未知字段（等价`additionalProperties=false`），限制数组数量、字符串长度、有限数字和范围；模型参数只能来自当前Profile允许覆盖集合。预设保存时先校验，执行预检再次校验，不能让保存合法但入队失真的两套默认值共存。

### 4.2 内置一键设置

| 预设 | 引擎/模型 | 主要参数 | 用途 |
|---|---|---|---|
| 漫剧 1080p · 标准（默认） | NCNN / `realesr-animevideov3` | 跟随横竖屏；AUTO_NATIVE；H.264 CRF 18，veryfast；tile 自动；TTA 关 | 主路径 |
| 漫剧 1080p · 低显存 | 同一模型 | tile 128；推理线程 1；分块96帧；其他画质参数同标准 | 减少峰值，不承诺所有设备必定可跑 |
| 漫剧 1080p · 精细 | NCNN / `realesrgan-x4plus-anime` | 原生 x4；H.264 CRF 16，medium；TTA 默认关 | 风格候选；需样片确认，不能标注一定更好 |
| 通用成片 1080p | NCNN / `realesrgan-x4plus` | 原生 x4；CRF 18，veryfast | 写实/混合风格候选 |
| 自定义 | 已发布且通过专用合同的模型 | 从当前设置派生，字段仍由合同控制 | 自由组合 |

预设名称使用“推荐用途”，不许承诺恢复不存在的细节或保证优于原生 1080p。参数值是本项目设计默认，不是官方性能保证。

“应用预设”只修改本批草稿；存在逐集覆盖时先显示将保留/清除哪些差异，默认保留。“保存为预设”创建用户新版本。“设为项目默认”是独立动作，显示影响后续新批次。“恢复推荐设置”展示变更摘要，不动已入队任务。

### 4.3 输出和媒体参数合同

| 参数 | 类型/首发范围 | 默认 | 约束与文案 |
|---|---|---|---|
| `target.mode` | FOLLOW_ORIENTATION_1080 / CUSTOM | FOLLOW_ORIENTATION_1080 | 横屏 1920×1080、竖屏 1080×1920、方屏 1080×1080，逐集显示实值 |
| `target.width/height` | 偶数整数，64–4096，受部署上限限制 | 由 mode 解析 | 与 fit 一并验证；超上限禁提交 |
| `target.fit` | CONTAIN / COVER | CONTAIN | CONTAIN 留边；COVER 裁切必须显示损失区域 |
| `target.allow_cross_orientation` | boolean | false | CUSTOM目标与源横竖相反需本批显式确认；不继承或修改生成策略开关 |
| `target.pad_color` | 固定黑色，后续可扩展 | black | 首发不增加颜色拾取器 |
| `scale_policy` | AUTO_NATIVE / EXPLICIT_NATIVE | AUTO_NATIVE | 模型原生倍率从 manifest 解析 |
| `native_scale` | 当前模型已验证集合 | 自动最小足够倍率 | 不允许将 2.25 直接传给只接受 2/3/4 的 CLI |
| `fps_policy` | PRESERVE_CFR | PRESERVE_CFR | 保留有理数帧率；首发不提供补帧选项 |
| `audio_policy` | COPY_IF_COMPATIBLE_ELSE_AAC / COPY_STRICT | 前者 | 非兼容音轨按预先冻结规则转 AAC；不运行后才悄悄改变 |
| `aac_bitrate_kbps` | 128/192/256/320 | 192 | 只在确定需转码时生效；保留声道数与采样率可兼容值 |
| `subtitle_policy` | INHERIT | INHERIT | 已烧录随画面；外置保留 revision/原文件，不自动重复烧录 |
| `watermark_policy` | INHERIT_SOURCE_STATE | 同左 | 继承已应用效果；新目标有冲突需返回干净成片重新处理 |
| `encoder` | libx264；验证通过才启用 h264_nvenc | libx264 | UI 分别显示软件质量与硬件质量字段 |
| `crf` | 整数 14–28 | 18 | libx264 生效；数字越小文件通常越大 |
| `preset` | ultrafast/veryfast/medium/slow | veryfast | libx264 生效 |
| `nvenc_cq/preset` | adapter 探测到的明确集合 | 由已验证 Profile 提供 | 不拿 CRF 冒充 CQ；无硬件证据则隐藏 |
| `container/pix_fmt` | mp4 / yuv420p | 同左 | 首发输出 SDR 8bit；faststart 开 |
| `source_policy` | PREFER_FINAL_DELIVERY / APPROVED_COMPOSE | 前者 | 选择来源后冻结具体 ID/hash |
| `existing_result_policy` | REUSE_EQUIVALENT / NEW_VARIANT | 前者 | NEW_VARIANT 需独立 nonce，不能误用重试 |

### 4.4 NCNN 模型参数

| 参数 | UI | 默认/范围 | 实现映射 |
|---|---|---|---|
| 模型 | “动漫视频 / 动漫精细 / 通用”与版本、已验证状态 | 当前可用模型 | `-n`，model name 来自发布合同 |
| 模型原生倍率 | 高级区只读自动结果或显式选择 | 每模型验证集合 | `-s`；animevideo 可配置 2/3/4，其他以实际包验证为准 |
| `tile_size` | 自动/64/128/256/512/自定义 | 0 自动；非零 >=32 且满足合同 | `-t`；0 的含义是 NCNN 自动，不与 PyTorch 的不分块混用 |
| `tta` | “测试时增强（更慢）” | false | true 时 `-x`；低显存不自动开 |
| `load_threads/proc_threads/save_threads` | 专家区 | 1/1/2；各 1–4，受设备验证限制 | 拼成 `-j 1:1:2`，不是同时运行多集 |
| `gpu_device` | 已映射受管 GPU | 首发锁定 scheduler GPU0 对应 Vulkan 设备 | `-g`；不能假设 Vulkan index 与 CUDA index 相同 |
| 中间帧格式 | 专家只读 PNG | 无损 PNG | `-f png` |

NCNN 首发不展示 denoise_strength、tile_pad、FP16/FP32、seed、prompt、face_enhance 等未经当前 CLI 合同支持的控件。模型自带复原效果与独立降噪滑杆是不同事实。后续 PyTorch 适配器才可按实际模型暴露其支持项。[NCNN 官方说明](https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan)、[官方 Python 视频脚本](https://github.com/xinntao/Real-ESRGAN/blob/master/inference_realesrgan_video.py)。

### 4.5 资源与恢复参数

| 参数 | 默认 | 首发规则 |
|---|---:|---|
| 同 GPU 并行集数 | 1 | 首发固定 1；排队不限于 1 集 |
| 分块大小 | 240 帧 | 允许 48–480，按磁盘预算向下解析；使用帧数而非近似秒数 |
| OOM 重试 | 开 | 发布合同冻结回退序列，例如 256→128→64；记录每次实际值 |
| 最多自动尝试 | 2 | 仅可恢复错误；tile 内回退独立有限次数，不无限重试 |
| 优先级 | 普通 | 普通/低优先，映射现有 scheduler；同批按集顺序 |
| 单帧无进度超时 | 120秒 | 每个 Profile 可在 30–600秒范围验证；冷启动单独预算 |
| 单集最长运行 | 12小时 | 运维可改 10分钟–48小时；不复用 Comfy 的 300秒或 FFmpeg 的 900秒常量 |
| 最低可用空间 | max(5GiB, 当前卷容量5%) | 必须同时满足临时空间、输出和保留片段预算 |
| 暂存成功结果 | 清理临时帧 | 注册成功后清除本任务临时帧，保留日志/报告/checkpoint 摘要 |
| 失败恢复保留期 | 7天 | 只清理无活跃 lease、过保留期的明确任务目录；提前显示影响 |

## 5. 模型与执行器接入

### 5.1 首发技术选择

选择 NCNN/Vulkan + Real-ESRGAN 专用适配器作为首发默认，理由是有独立本机可执行程序、明确图片/目录输入合同、可配置 tile 和模型，适合现有 Windows 工作站；视频帧处理、音频保留、进度与恢复由项目自己的流水线负责。官方动漫视频文档同样给出抽帧、推理、回组视频的使用路线。[动漫视频模型说明](https://github.com/xinntao/Real-ESRGAN/blob/master/docs/anime_video_model.md)。

这条路线属于对视频逐帧进行 AI 图像复原；不是保证时序一致的多帧模型。必须通过动态片段检查细线闪烁、纹理跳变和字幕变形。官方参考脚本是参数/模型依据，不原样当生产 runner；项目 runner 必须对任何缺帧失败、保留完整声音且能恢复。

### 5.2 注册与验证流程

“视频超分引擎”向导步骤：选择已安装程序包或配置受信下载源 → 选择模型包 → 校验文件和依赖 → 探测版本/CLI 能力/GPU/FFmpeg 编码器 → 在隔离目录对内置测试图片和 2 秒测试视频执行真实 smoke → 展示输入输出及日志 → 发布 `UPSCALE_VIDEO` Profile → 返回原项目草稿。

没有程序/权重时给出具体缺失项。下载是用户显式配置动作，执行 Job 不自动下载权重或更换模型。复用现有 installation/trusted_download 服务，不新增第二套下载缓存。URL、release tag、二进制/模型哈希在安装记录冻结；本文不猜测下载包 SHA256。

执行器包记录（使用现有 Runtime/Artifact/Binding 结构承载，不再建第二份模型库）：

```json
{
  "contract": "localdrama.upscale-executor.v1",
  "adapter_code": "ncnn.realesrgan.video.v1",
  "runtime_kind": "TOOL_PROCESS",
  "program": {"artifact_id": "<registered-binary>", "sha256": "<verified>"},
  "runner": {"artifact_id": "<registered-runner-package>", "version": "1", "sha256": "<verified>"},
  "model": {"installation_id": "<registered-model>", "name": "realesr-animevideov3", "native_scales": [2, 3, 4]},
  "input_contract": {"kind": "CFR_VIDEO", "bit_depth": 8, "transfer": "SDR"},
  "output_contract": {"kind": "VIDEO", "count": 1, "frame_count": "PRESERVE"},
  "features": {"tile": true, "tta": true, "temporal_context": false, "checkpoint": "CHUNK"},
  "parameter_contract_version_id": "<contract-version>",
  "resource_policy_version_id": "<resource-version>",
  "validation_receipt_id": "<real-smoke-receipt>"
}
```

上例是业务配置视图，不声称当前数据库已直接支持该 JSON。落库适配必须写清字段到现有 `mp_*` 版本表的映射；`execution_binding` 存协议标识和包引用，`runtime_configuration` 存受控定位，`model_bindings` 存安装引用，验证历史存 smoke。

### 5.3 自定义脚本和高级配置

可以配置注册过的 Python 可执行文件、runner 入口、模型包、受控环境变量、工作目录、超时和参数 schema。路径在运行环境管理页配置为库/相对路径，业务页只提交 ID。入口与依赖文件一起 hash，修改后需派生版本并重新验证，队列继续使用旧快照；旧文件不在则明确 `RUNTIME_ARTIFACT_CHANGED`。

不允许在普通交付页面输入一整行任意 shell 命令。适配器将已校验参数转换为 argv 数组，`shell=False`；环境变量使用 allowlist，线程数/缓存目录可以配置，不能覆盖路径和离线策略。任意 Python 并不因 JSON 协议而成为沙箱：只允许操作者明确安装的可信脚本包，前端不提供匿名上传即执行。

“一键配置推荐引擎”仅在现有可用安装中选择并形成草稿；缺安装则进入向导。不得把显示推荐卡片当作程序或模型已经安装。

## 6. 480p → 1080p 的处理规则

### 6.1 几何

从 probe 读取 coded width/height、SAR/DAR、rotation、帧率和时长。首发源须可正规解释为 SAR=1、rotation=0 的逐行 SDR CFR；非零 rotation 或非1 SAR 若已支持正规化，必须冻结像素变换并列入测试，否则阻塞。

对于 CONTAIN，计算内容目标比例 `r=min(target_w/source_w, target_h/source_h)`；COVER 为 `max(...)`。在支持集合中选最小 `native_scale >= r`；大于最大倍率则阻塞或要求选择受支持目标，不能偷偷链式多次超分。推理得到原生倍率尺寸，再用 Lanczos 收敛到确切内容尺寸，按规则补边/裁切，最终尺寸必须精确等于 target。

| 实测源 | 目标 | 推荐模型倍率 | 最终几何 |
|---|---|---|---|
| 864×480 | 1920×1080 | animevideo x3 | 3倍推理后等比缩小；CONTAIN 有少量上下留边 |
| 854×480 | 1920×1080 | animevideo x3 | 处理整数偶数取整，最多数像素补边；不拉伸 |
| 480×854 | 1080×1920 | animevideo x3 | 同上，竖屏 |
| 480×832 | 1080×1920 | animevideo x3 | 内容 1080×1872，上下各24像素留边 |
| 640×480 | 1920×1080 | animevideo x3 | 内容1440×1080，左右各240像素留边 |
| 1920×1080 | 1920×1080 | 默认跳过 | 显式增强模式可新建变体 |

1920/864 与 1080/480 不相等；“480p”不自动意味着16:9。每项 plan 返回 source、AI 中间尺寸、content_rect、output、crop_rect/padding，UI 直接展示服务端结果。

### 6.2 时间、声音、字幕、颜色

- 帧率使用有理数，例如 24000/1001；不先四舍五入成 23.98/24。按 frame index 切块，统一从源起始时间偏移生成时间线。
- CFR 判定不能只比较 avg_frame_rate 和 r_frame_rate；预检顺序读取帧时间戳并验证步长，允许容器时间基量化误差，保存首尾 PTS 和帧数。VFR 返回专用阻塞码。
- 每帧对应一个推理输出；有一帧失败不能吞掉异常继续编码。没有达到完整帧数即失败。
- 音频独立从原源保存和最终一次 mux；分块不分别编码音频，不累积 AAC delay。保留所有受支持音轨、language/disposition；多音轨不能默认只取第一个。
- 默认音频兼容时 stream copy；不兼容时按冻结规则转 AAC 并报告。校验音视频起点/终点差，处理负 PTS、encoder delay；不能滥用 `-shortest` 裁掉尾部。
- 已烧录字幕和水印作为画面的一部分随超分；独立 SRT/VTT/ASS 保留版本、内容和时间戳。既有烧录状态不明时不自动追加烧录。
- 保留明确的 SDR matrix/primaries/transfer/range；SD→HD不意味着源BT.601可以只改tag成BT.709，转换必须实际执行并记录。无色彩元数据按已验证的推断策略显示“推断”，不标“源提供”。
- 清晰度 QC 没有真实1080p参考时不能用 PSNR/SSIM 虚构质量提升；自动检查负责技术完整性，画质由样片与人工审核确认。

## 7. 数据模型与版本规则

### 7.1 复用和新增

复用：`mp_*` 模型/运行环境/参数/Profile/snapshot/job link；`jobs/job_attempts/job_artifacts/job_resource_leases`；`review_decisions/review_checks/machine_check_runs/results`；`episode_render_versions`；`delivery_packages/delivery_files/events`。

建议新增下列表，公共时间/创建者/revision/schema_version 沿仓库约定。名字可调整，职责不得混合。

| 表 | 关键字段/约束 |
|---|---|
| `video_upscale_presets` | id、project_id可空、code、title、builtin、current_version_id；内置与用户作用域唯一 |
| `video_upscale_preset_versions` | preset_id/version_no唯一、profile引用、pipeline_options_json、model_options_json、content_hash、parent_version_id；保存即不可变，后续编辑创建新版本 |
| `project_upscale_settings` | project_id唯一、preset_version_id、overrides_json、revision；不复用 generation.upscale |
| `video_upscale_plans` | id、project_id、status、request_hash、selection_snapshot、检查 Job ID、逐项计划、plan_hash、expires_at、supersedes_id；GET只读 |
| `video_upscale_batches` | id、project_id、preset_version_id、plan_id/hash、title、control_state、revision、idempotency_key/request_hash；scope内幂等唯一 |
| `video_upscale_batch_items` | batch_id/episode_id唯一、ordinal、source_descriptor_json、effective_options_json、item_fingerprint、current_run_id、participation_state(ACTIVE/PAUSED/CANCELLED)；批次成员冻结，控制需求可变 |
| `video_upscale_runs` | id、project_id、purpose FULL/PREVIEW、item_id可空、job_id唯一、execution_snapshot_id、fingerprint、variant_nonce、source/root_render_id、output_render_id、sample_artifact_id、qc_run_id、progress_summary |
| `video_upscale_chunks` | run_id/ordinal唯一、start_frame/end_frame_exclusive、source_manifest_hash、snapshot_hash、state、output_rel/hash、frame_count、attempt_id、fencing_token、actual_parameters_json |
| `episode_delivery_selections` | episode_id + target_slot唯一、selected_render_id、root_compose_render_id、approval_id、revision；选择变化追加 audit |
| `video_upscale_delivery_links` | batch_item_id、selected_render_id、target_version_id、delivery_fingerprint唯一、job_id、package_id；仅追踪后续打包 |

批次聚合不能取代 Job/attempt 权威状态。状态可以缓存，但读模型必须能从 link + Job + run 重建，并由 reconciler 修复。预览与完整运行同用 `video_upscale_runs`，预览不创建 `episode_render_versions`。

### 7.2 扩展整集渲染表

给 `episode_render_versions` 增加：

- `render_kind`：`COMPOSE` 默认回填现有记录；新增 `SUPER_RESOLUTION`。
- `parent_render_version_id`：派生来源的整集版本 FK；首发只允许根 COMPOSE，不链式超分。
- `upscale_run_id`：可空且唯一；SUPER_RESOLUTION 必须有值。
- `derivation_fingerprint`：可空，用于幂等派生识别。

根渲染保存原时间线；派生版本继承 episode_id/timeline_revision_id 仅用于来源关系，真实流水线信息写新 `input_snapshot` schema，包括 `source_descriptor`、`root_compose_render_id`、`output_spec`、`execution_snapshot_id/hash`、`applied_effects`、源字幕音轨快照引用、实际源文件 hash。不伪装为旧 `localdrama.episode-render-input.v1`。

增加 `(episode_id,render_kind,created_at,id)`、`parent_render_version_id`、batch/project/state、run/fingerprint、selection/root 等索引。FK与同项目同集关系在事务内验证；已被任务/结果引用的 Profile、preset、模型包只能退役，不级联物理删除。

### 7.3 来源描述

```json
{
  "kind": "DELIVERY_FILE",
  "episode_id": "<episode-id>",
  "root_compose_render_id": "<compose-render-id>",
  "render_revision": 1,
  "delivery_package_id": "<package-id>",
  "delivery_file_id": "<main-video-file-id>",
  "source_sha256": "<actual-file-sha256>",
  "source_byte_size": 123456789,
  "source_approval_id": "<exact-approval>",
  "source_manifest_hash": "<package-manifest-hash>",
  "applied_effects": {"subtitle_burned": true, "watermark_profile_snapshot": "<snapshot-reference>"}
}
```

`EPISODE_RENDER` 类型省略包字段，以该 render 的实际 hash 为源。服务端从受控 resolver 解析路径；source body 不允许任意绝对路径。delivery_files 当前没有可靠“主视频”role时，由 manifest和 MIME/probe验证；多个候选必须让用户明确选，不能按第一个文件或最大文件猜。

### 7.4 当前成片、派生版本与采用

新增统一 `EpisodeDeliverySourceResolver`：

1. `latest_compose_render` 仅查询 COMPOSE；旧业务“最新合成”统一调用它。
2. 一个明确 `target_slot`（如 `FHD_PORTRAIT`、`FHD_LANDSCAPE` 或 normalized geometry hash）可采用一个已批准且完整的版本。
3. 没有选择时原版继续是原交付默认；新超分输出不自动替换。
4. 创建更多超分候选不改变已采用版本；只有显式采用动作改变 selection revision。
5. 新 COMPOSE、源完整性失败、源 approval失效/拒绝、源包撤回都会使相关派生结果“来源已过期”，默认不能继续采用/交付。
6. 当前源变化不销毁在跑的旧快照；可让已冻结任务完成，结果标 stale 并禁止自动采用。入队尚未运行时发现变化则 NEEDS_ATTENTION。
7. “回到原版”显式更新选择；原版要满足该打包目标，否则提示目标尺寸不匹配，不伪装1080p。

现有`TimelineStatus.renders.latest`兼容字段继续指COMPOSE，新增`delivery_versions`/`selected_delivery_version`投影供新旧交付页消费。旧计数文案明确“合成版本”，派生计数独立。不要把同一字段在不同页面分别解释为最新创建/最新批准/已采用。

替换 `require_latest_episode_render_approval` 的调用语义：COMPOSE继续要求最新合成和其人工批准；SUPER_RESOLUTION要求其根仍是当前合成、源有效、自身QC PASS、自身人工批准、选中版本/目标匹配。用共享 validator 覆盖同步交付与后台交付，不能只放宽其中一个入口。

### 7.5 幂等与并发

`item_fingerprint = hash(project_id,episode_id,source_kind,source_actual_sha,source_version_refs,profile_snapshot_hash,pipeline_options,output_spec,runner_version,model_hashes,purpose,sample_range)`。预览与完整输出不可互相复用。

相同请求幂等键返回同一批；相同键不同请求返回409。相同 fingerprint 的活跃或完成 run 可共享引用，各批次有自己的 item；禁止跨项目复用来源。显式“新变体”增加 nonce。重试使用同一 run 和新 attempt，不新建视觉版本。

建立数据库唯一约束`(project_id,fingerprint,variant_nonce)`，普通复用nonce用空字符串而非NULL，避免SQLite多个NULL绕过唯一性；purpose和样片区间已包含在fingerprint。并发竞争使用冲突后读现有run的事务路径，不能仅先SELECT再INSERT。FULL输出`upscale_run_id`唯一，保证retry只产生一个正式结果。

取消/暂停共享 run 需引用感知：批次“取消本批未完成项”先解除该 item 的执行需求，仍被其他活跃 item使用时不杀共同 Job；UI显示“其他批次仍在使用”。单 Job强制取消由任务中心显式操作并显示关联批次。避免对一个共享任务的控制悄悄影响另一批。

批次暂停先将对应item的participation_state改为PAUSED；所有引用均暂停才暂停共同Job。单批resume只恢复本批需求。有活跃需求且Job已经SUCCEEDED时直接展示结果，不重跑；取消全部引用后该Job终止，后续再次选择同fingerprint需显式“恢复取消任务”服务创建新attempt或NEW_VARIANT，不能套用当前只允许FAILED/NEEDS_ATTENTION/ORPHANED的`JobService.retry`。首发采用NEW_VARIANT重新处理取消的run，UI明确提示并生成nonce。

检查批准后采用/打包必须基于 plan_hash + expected_revision。采用使用一次事务全量校验、全量更新；任何 stale不产生部分采用。文件hash计算不在 SQLite写事务中进行，昂贵检查在后台预检，执行前与注册前再校验真实文件。

## 8. API 合同

### 8.1 接口清单

所有路径以 `/api/v1` 为前缀；遵循现有 error envelope、session、项目作用域和 operation_id 约定。下列接口为新增提议。

| Method / 路径 | 功能与返回 |
|---|---|
| GET `/projects/{project_id}/delivery-episodes` | 聚合各集源、实测元数据、可选性、最新run、采用版本；cursor分页及项目总数 |
| POST `/projects/{project_id}/video-upscale-selections:resolve` | 固化跨页全选条件到具体来源集合，返回selection_hash、items/count，不运行GPU |
| GET `/video-upscale-options?project_id=...` | 已验证可用模型/Profile、参数/UI合同、预设、运行环境状态；不触发smoke |
| GET/PUT `/projects/{project_id}/video-upscale-settings` | 获取/保存项目默认；PUT必须expected_revision |
| GET/POST `/video-upscale-presets` | 列表/创建用户预设 |
| POST `/video-upscale-presets/{id}/versions` | 派生版本；内置内容不可覆盖 |
| POST `/projects/{project_id}/video-upscale-plans` | 冻结草稿并提交CPU预检Job，202返回plan_id/job；不启动超分 |
| GET `/video-upscale-plans/{plan_id}` | 预检进度、逐集阻塞/提醒/资源估算、plan_hash、有效期 |
| POST `/projects/{project_id}/video-upscale-batches` | 用READY plan和Idempotency-Key原子提交批次及完整Job links，202 |
| GET `/projects/{project_id}/video-upscale-batches` | 批次分页和聚合 |
| GET `/video-upscale-batches/{batch_id}` | 批次/逐集/关联Job/审核与交付状态，支持revision/ETag |
| POST `/video-upscale-batches/{batch_id}:control` | PAUSE_PENDING/PAUSE_ALL/RESUME/RETRY_FAILED/CANCEL_UNFINISHED，返回逐项影响 |
| POST `/projects/{project_id}/video-upscale-previews` | 使用已验证单集计划、样片区间，创建purpose=PREVIEW的模型Job，202 |
| GET `/video-upscale-runs/{run_id}` | progress、receipt、QC、preview或output_render、故障恢复信息 |
| GET `/episodes/{episode_id}/delivery-versions` | 合成根、超分候选、选择、审批、是否过期 |
| POST `/projects/{project_id}/episode-render-review-batches:plan` | 逐集校验并冻结 revision、文件 hash、当前合成根、机器 QC、当前 `episode_upscale` 模板、既有审核及每集独立检查结论；不写审核 |
| POST `/episode-render-review-batches:commit` | 使用短期 token + plan_hash 在单一事务中全部复核并原子写入审核/check/audit；任一项变化整批零写入 |
| POST `/projects/{project_id}/delivery-selections:plan` | 批量采用预检，包括逐集输出ID/approval/selection revision |
| POST `/projects/{project_id}/delivery-selections:commit` | 原子采用；明确render_id和目标档位 |
| POST `/projects/{project_id}/delivery-build-batches:plan` | 对已选择、已批准结果检查打包目标及已应用效果 |
| POST `/projects/{project_id}/delivery-build-batches:submit` | 统一提交，逐集DELIVERY_BUILD，返回关联batch/Job |

单集审核复用 `submit_episode_render_review` 对应 HTTP 入口，并加超分模板和机器 QC 门禁。批量审核使用上表专用 EPISODE_RENDER_VERSION 两阶段入口，不调用只接受 MEDIA_VERSION 的旧批量接口。前端按集渲染独立 fieldset，不提供全局“全部通过”；任何输入修改都会丢弃已冻结计划，要求重新预检。

程序安装、runtime、Profile发布复用模型平台API，必要时增专用引擎向导facade。业务页不要直接向后端传程序路径、CLI字符串或任意JSON override。

### 8.2 创建预检示例

```json
{
  "schema_version": "localdrama.video-upscale-request.v1",
  "selection_hash": "<resolved-selection-hash>",
  "items": [
    {"episode_id": "ep-01", "source": {"kind": "EPISODE_RENDER", "render_id": "render-01"}},
    {"episode_id": "ep-02", "source": {"kind": "DELIVERY_FILE", "package_id": "package-02", "file_id": "file-02"}}
  ],
  "preset_version_id": "preset-anime-1080-v1",
  "execution_profile_version_id": "<published-ncnn-profile>",
  "batch_overrides": {
    "pipeline": {"target": {"mode": "FOLLOW_ORIENTATION_1080", "fit": "CONTAIN"}, "fps_policy": "PRESERVE_CFR"},
    "model": {"tile_size": 0, "tta": false}
  },
  "item_overrides": [{"episode_id": "ep-02", "model": {"tile_size": 128}}],
  "existing_result_policy": "REUSE_EQUIVALENT"
}
```

`items`可来源于单集链接手选或selection resolve，服务端验证两者一致；UI不发伪造hash。检查Job读取每个源的实际文件、冻结批准/包状态、probe/hash、模型hash、目标几何、帧数和资源。`GET`无副作用，不在每次轮询重新跑ffprobe。预检可以写其自身计划/证据，不能写采用或批准事实。

返回每项：`disposition=NEW|REUSE_RUNNING|REUSE_SUCCEEDED|SKIP_ALREADY_TARGET|BLOCKED`、`source`、`resolved_profile`、`resolved_parameters`、`output_spec`、`geometry`、`frame_count`、`estimated_work`、`disk_budget`、`blockers`、`warnings`。

计划总体 `CHECKING → READY|BLOCKED|FAILED`；到期为EXPIRED，源/配置变化为STALE。默认有效期30分钟，但每次submit必须验证版本/源标识，并在Worker再次完整校验。昂贵全文件hash在预检与Worker，不在短提交事务持有锁。

### 8.3 提交示例和事务边界

```json
{
  "plan_id": "plan-123",
  "plan_hash": "<sha256>",
  "title": "第1季 1080p 交付超分",
  "acknowledged_warning_ids": ["ep-02:CONTAIN_PADDING"]
}
```

必须携带 `Idempotency-Key`。返回202：`batch_id`、`item_count`、`new_jobs`、`reused_runs`、`skipped`、`idempotent_replay`、`detail_url`。一个READY plan的完整成员统一提交；若用户只选部分可执行项，先派生新plan。批量创建部分成功而HTTP整体失败不可接受。

扩展 `ExecutionSubmissionService` 为有明确连接参数的 `prepare/submit_in_transaction` 或等价批量方法：所有新snapshot、Job、execution link、batch/run/item link在同一事务可见；rollback后不存在孤立可领取任务。现有单条submit复用新内部实现，保持返回兼容。不得在循环里调用会各自commit的submit后宣称原子性。

Runtime/hash/profile解析的昂贵部分事务外完成；事务内校验所引用不可变版本和当前可提交状态，生成snapshot时使用同一connection。计费/网络外部动作不得混入事务；首发是local-only。

### 8.4 显式Profile与执行上下文

给 `ExecutionPreviewRequest` 增加可选 `execution_profile_version_id`。未提供时维持assignment解析；提供时验证该Profile的项目可见性、PUBLISHED、capability=UPSCALE_VIDEO、runtime READY和专用adapter合同。返回 `resolution_reason=EXPLICIT_RUN_PROFILE`，hash包括显式选择和所有参数。不得临时修改全局assignment让一个用户选择污染其他任务。

`ExecutionSubmissionService`接受受校验的业务context以设置 `stage_code=UPSCALE_VIDEO` 和业务链接；Job `type`继续为 `MODEL_PLATFORM_EXECUTION`。同一模型推理不再外包一层另行启动GPU的`VIDEO_ENHANCEMENT` Job。

引入 `ExecutionContext`：`job_id/attempt_id/lease_token`、`report_progress()`、`should_stop()`、`checkpoint_store`、`fencing_token`。保留现有handler适配层；NCNN必须消费取消/进度，不以独立后台线程失联执行。执行context不是浏览器可写字段。

### 8.5 错误码

| code | 用户可执行的恢复 |
|---|---|
| UPSCALE_SOURCE_NOT_READY | 生成/批准当前成片或确认交付包 |
| UPSCALE_SOURCE_CHANGED / PLAN_STALE | 更新源并重新检查，旧结果仍可查看 |
| UPSCALE_SOURCE_INTEGRITY_FAILED | 检查原文件/恢复源，禁止自动切换另一文件 |
| UPSCALE_PROFILE_UNAVAILABLE | 返回模型页完成安装、验证、发布 |
| UPSCALE_PARAMETER_UNSUPPORTED | 定位具体字段和支持范围 |
| UPSCALE_TARGET_UNSUPPORTED / UPSCALE_CROSS_ORIENTATION_REQUIRED | 修改目标/选择支持倍率模型，或明确确认跨方向构图 |
| UPSCALE_INPUT_VFR / HDR / INTERLACED / MULTI_VIDEO | 显示不兼容事实和需要正规化的输入 |
| UPSCALE_GPU_MAPPING_UNVERIFIED | 验证物理GPU到Vulkan设备的映射 |
| UPSCALE_GPU_OOM | 有限tile回退；耗尽后建议低显存预设 |
| UPSCALE_DISK_LOW | 释放空间/改受控暂存位置，然后恢复 |
| UPSCALE_FRAME_MISSING / CHUNK_INVALID | 重算受影响分块；不可直接登记输出 |
| UPSCALE_PROCESS_EXITED / STALLED | 查看脱敏日志、重试；显示阶段与帧号 |
| UPSCALE_RUNTIME_ARTIFACT_CHANGED | 恢复冻结包或显式新建使用新版本的计划 |
| UPSCALE_QC_FAILED / AUDIO_SYNC_FAILED | 对比具体测量值与阈值，不能采用 |
| UPSCALE_SOURCE_EFFECT_CONFLICT | 改用干净合成源或兼容的交付目标 |
| IDEMPOTENCY_KEY_CONFLICT | 创建新操作键，不重复创建原批次 |

## 9. Worker、分块与恢复

### 9.1 任务生命周期

后台预检是CPU Job；试跑和正式推理是同一模型handler、不同purpose。批次只是业务聚合。一个完整run处理一集，不一次把全剧塞入一个不可恢复进程。

逐集阶段：`VERIFY_SOURCE → PREPARE → DECODE_CHUNK → UPSCALE_CHUNK → ENCODE_CHUNK → CONCAT → MUX → QC → REGISTER`。阶段可以循环，但进度基于完成帧数，不能循环时倒退成0。完整帧处理通过后才进入最后的注册。

Job继续使用既有QUEUED/CLAIMED/RUNNING/PAUSED/CANCEL_REQUESTED/CANCELLED/SUCCEEDED/FAILED/NEEDS_ATTENTION/ORPHANED。run的业务视图通过映射显示；不为了一个功能给jobs再造整套枚举。批次control_state只存ACTIVE/PAUSED/CANCELLED等操作者意图；aggregate状态从item和Job推导RUNNING/PARTIAL_SUCCESS/COMPLETED/FAILED等。

### 9.2 GPU生命周期

- 在 `GpuRuntime` 增加 `NCNN`，更新scheduler校验、状态序列化、任何数据库CHECK、UI枚举与测试。
- `RuntimeKind`继续使用TOOL_PROCESS；注册 `ManagedNcnnGpuLifecycleAdapter`，进程退出并确认清理后释放lease。
- 注册 `ExecutionHandlerDescriptor(... capability=UPSCALE_VIDEO, adapter=ncnn.realesrgan.video.v1, worker_channel=GPU_H3, gpu_runtime=NCNN)`；Worker registry匹配相同code/version。
- 互斥资源继续指向当前受管物理GPU，与Comfy、Ollama、PyTorch、llama共享，不另建“Vulkan空闲GPU”假象。
- NCNN自报Vulkan设备号与受管GPU核对，冻结设备映射。仅有设备号0不构成匹配证据；多卡无法验证时阻塞。
- 切换执行器使用既有runtime coordinator的卸载和外部busy规则；不杀用户外部Comfy任务。不擅自结束未由本任务拥有的进程。
- 首发运行一个完整run期间持有GPU租约，包括块间编码；实现简单且可靠。CPU编码与下一集推理重叠是后续优化，不能先释放GPU再留下推理进程。

### 9.3 有界分块处理

完整片长可能数分钟到几十分钟，不能抽完整集所有4倍PNG到磁盘。每次处理固定frame range，编码成独立闭合GOP视频分段，验证后清除该块源/放大PNG，保留可复用分段。

1. 建立轻量source frame manifest：frame index、PTS、duration、总帧数；不把全部图像放进内存。
2. 从确定keyframe向前解码并按全局frame index精确丢弃，产生 `[start_frame,end_frame)`；不能仅用近似 `-ss/-t` 断言正好无重帧。
3. 对块内PNG用冻结NCNN模型执行；输出名称固定序号，验证数量、顺序、可解码和尺寸。
4. 目标缩放/补边与编码按同一encoder/color/timebase参数生成segment；各块不能独立选不同profile或帧率。
5. 校验segment完整，写hash、frame_count、actual tile、pipeline hash，然后原子提交checkpoint。
6. 最后concat分段，验证PTS连续和总帧数；可stream copy前必须确认codec参数和extradata兼容，否则走明确统一编码策略并记录额外编码，不静默失败降级。
7. 从源一次合入音轨，处理metadata/字幕；做最终全文件解码QC和hash。
8. 写project内唯一输出路径的`.partial`，完成后同卷原子rename；登记新render和run链接。文件写入/DB之间崩溃由receipt对账，不重建重复版本。

暂停当前任务先发控制信号，runner在帧/进程轮询时停止；未完成块不commit。若一个NCNN调用暂不可协作取消，在超时后终止**本attempt的进程树**，从该块重新运行。已完成块保留。Windows使用Job Object/等价有所有权的进程树管理，后台程序不弹控制台窗口。

### 9.4 Checkpoint与租约

checkpoint至少包括source hash、snapshot hash、runner/codec版本、块范围/PTS、模型hash、实际tile、输出hash、attempt/fencing token。恢复前重新校验源和所有拟复用segment hash；坏块只重做该块。时序模型将来接入需halo/context，不能直接套用逐帧块边界。

旧attempt失去lease后不能提交checkpoint或render。所有DB写入验证当前attempt和fencing token；新attempt只在旧进程停止/对账后进入GPU。pause请求到确认停止期间仍保留lease；resume不得在旧attempt未settle时重新领取。

文件已生成但登记前崩溃：通过run ID固定final path和receipt识别，校验后完成幂等登记；DB已登记但Job未SUCCEEDED：恢复读已有run output，验证后补报告，不再超分。

对修改参数的“重做”创建新run/new fingerprint，不能拿旧checkpoint继续。同一冻结策略内允许的OOM回退记录actual_parameters；从一个tile完成块切到另一个tile要进行接缝/一致性检查，若模型验证不支持此混用则整集重算，不把adaptive fallback视为天然像素等价。

### 9.5 进度与资源估算

持久进度示例：

```json
{
  "stage": "UPSCALE_CHUNK",
  "frames_done": 4800,
  "frames_total": 14400,
  "chunk_index": 20,
  "chunk_count": 60,
  "inference_fps": 8.6,
  "eta_seconds": 1220,
  "eta_source": "SAME_RUNTIME_SAMPLE",
  "actual_tile_size": 128,
  "progress_revision": 73
}
```

runner最多每秒数次输出，DB合并写入默认每秒一次或阶段变化。阶段权重只是可解释估算，例如准备5%、帧处理80%、合并5%、QC/注册10%；没有帧数的阶段显示不定进度，最后10%不在QC前提前填满。跨集按帧数×像素量/实测耗时权重聚合，不简单平均一集1分钟和一集30分钟的百分比。

试跑提供同模型/相同tile/目标/设备的frames per second，保留cold start与steady state分别估算，展示范围和采样日期。没有实测值显示“首次运行后估算”，不显示伪精确剩余时间。

临时空间预算用未压缩帧上限：`frames_per_chunk × (source_w×source_h + ai_w×ai_h) × channels`，再加PNG容器安全余量、已完成segments、final临时输出和最终存储预留。流式管道/立刻缩小可减少实际占用，但没有测量前不能低估。示例864×480、x3、240帧、RGB仅两组raw像素约2.78GiB，不能按原MP4文件大小估算。

同一磁盘卷预算应合并：若work_root和project_root同卷，不能分别判定足够却合计超用。批次提交不预留全剧raw PNG，但要预留本批最终输出估算，运行前/每块前重新检查剩余量。CRF结果大小不确定，以样片码率估算范围并运行时保护。磁盘不足进入NEEDS_ATTENTION，清理空间后可恢复。

## 10. Runner 与脚本交付合同

### 10.1 实现文件分层

建议新建：

```text
apps/api/local_drama/
  application/video_upscale/
    sources.py           # 成片/交付文件只读resolver
    plans.py             # 配置合并、几何、预检
    batches.py           # 原子提交与批次控制
    presets.py           # 版本化预设和项目默认
    derivations.py       # 派生成片注册/有效性/采用
    qc.py                # 技术质检和报告
    reconciliation.py    # 丢失attempt/receipt/批次聚合恢复
  model_platform/application/
    ncnn_upscale_profiles.py
    ncnn_upscale_execution.py
  infrastructure/video_upscale/
    process.py           # 隐藏子进程、日志、取消、进程树
    ncnn.py              # argv映射和能力检测
    frame_pipeline.py    # 精确分块、时间戳、音频、mux
    checkpoints.py
  api/routes/video_upscale.py
  api/schemas/video_upscale.py

scripts/video_upscale/
  runner.py              # JSON协议CLI，调用共享执行模块
  inspect_runtime.py     # 探测/校验，不自动装模型
  smoke.py               # 可复现的真实模型短片测试
  benchmark.py           # 指定样片测量耗时/显存/磁盘
  windows_uat.py         # 隔离fixture→HTTP→worker→交付验收
```

这些是后续待创建的文件，本次只写文档。脚本与API共用模型/参数/几何/分块逻辑，不能出现CLI支持的参数与页面实际执行不同。生产runner位于可版本化发布包；诊断/验收脚本不接管生产数据库。

### 10.2 进程协议

固定入口：`<registered-python> <registered-runner> --request <controlled-json> --result <controlled-json>`。版本化JSON文件避免Windows命令行长度/转义问题；输入路径仅Worker在沙箱内解析后写入，HTTP业务请求不接受这些路径。NCNN是runner启动的受控child。

请求至少包含：protocol_version、run/job/attempt、source_file+hash+frame_manifest、output_partial、checkpoint_dir、已解析profile/模型/运行环境、pipeline/model参数、stop_control_file、plan_hash。runner不写应用DB，不判断人工批准，不启动另一个后台服务。

stdout逐行JSONL：`event=started|stage|progress|checkpoint|warning|completed`，含sequence/run_id/attempt_id/时间；stderr为有界日志。日志泄露控制与现有系统一致。JSON行不完整时允许等下一次read，不能把普通NCNN stderr当JSON；wrapper负责转译。

result receipt包括：schema、status、source hash、完整参数/实际fallback、程序/脚本/权重/FFmpeg版本和hash、各段frame数/PTS/hash、输出probe/hash、audio映射、subtitle/watermark继承、QC、network_used、取消原因。`status=SUCCEEDED`仅代表runner完整产物；Worker再验证并注册。

退出码建议：0成功，10用户停止，20配置/源不合法，30依赖/模型缺失，40显存或GPU失败，50磁盘不足，60子进程/超时失败，70输出/QC失败。应用错误码由receipt映射，不能只给“exit code 1”。

示意命令（仅解释映射，不包含真实安装路径，也不是可复制即生产的完整流水线）：

```text
realesrgan-ncnn-vulkan.exe
  -i <chunk-input-directory> -o <chunk-output-directory>
  -m <verified-model-directory> -n realesr-animevideov3
  -s 3 -t 128 -g <verified-vulkan-device> -j 1:1:2 -f png
```

参数从合同构造argv。上游CLI支持tile、线程、TTA等，但不同安装版本仍须探测并真实smoke；支持矩阵来自冻结包。[官方CLI用法](https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan#usages)。

### 10.3 路径和产物布局

暂存：`work/video_upscale/<run_id>/attempt-<id>/...`；跨attempt复用的已完成段在该run专属checkpoint区域，禁止共享可变临时文件名。最终render建议放项目的既有成片根之下 `upscaled/<episode-code>/<run_id>/...mp4`；实际目录由project storage resolver生成。

显示名可为 `S01E03_1080p_SR_v2.mp4`，唯一性来自目录/run ID，不靠用户集名。中文、空格、长路径测试必需。路径清理前解析真实绝对路径、验证处于该run控制目录、拒绝symlink/junction越界，确认无active lease；日志保留清理事件。

### 10.4 离线与打包

模型与引擎不因功能代码合入就视作随应用安装。安装manifest记录供应方、许可证说明、来源、版本、文件hash与验证回执。首发运行期禁止自动联网下载；可信脚本自身的离线行为需要验证，设置环境变量不是通用网络沙箱。

打包应用时runner入口不能只存在repo相对路径，必须通过resource_locator/installation locator定位。增加安装包/便携包的文件清单和依赖检查；不把多GB权重无提示加入应用release。

## 11. 交付与审核闭环

### 11.1 新结果机器QC

正式登记前检查：完整decode、目标宽高、SAR/rotation、帧数、CFR有理数、首尾PTS与时长、所有预期音轨及起止同步、像素格式和颜色声明、文件完整性。QC报告注册到 `machine_check_runs/results` 的EPISODE_RENDER_VERSION，或在render原子注册时同时关联预先生成的QC证据；不能只在UI显示一个`passed=true`。

机器QC不自动产生APPROVED；新review template如 `episode_upscale`，subject仍为EPISODE_RENDER_VERSION，包含§3.5检查项。批准入口对SUPER_RESOLUTION强制最新有效机器PASS及同一output hash。

### 11.2 已交付文件作为输入

当源是DELIVERY_FILE，要分别记录根render批准和交付包human_review决定/状态、manifest hash、主视频hash；`VERIFIED`只表示技术完整，不代替人工确认。源包若机器通过但human pending，列表展示未确认并默认不可选；可先去确认，不能自动借根render批准替代打包后画面审核。

已烧水印/字幕必须被`applied_effects`记录。派生render的交付检查如果目标请求同一效果，则标记“已包含，跳过重复施加”；目标要求去除/改变已烧水印或字幕，返回冲突并建议选择干净的COMPOSE源。未知效果状态不能自动再烧一次。

派生快照保留源冻结字幕、音频许可证、效果和来源链。`_subtitle_for_render_snapshot`及manifest构建必须支持派生schema并追溯冻结源；不能读取当下最新字幕/音频替代旧快照。源包有sidecar时复制其已验证字节，或按冻结revision重建后比hash。

### 11.3 打包目标与分辨率

项目生成仍可480p，超分输出1080p；打包使用独立delivery target。若现有ACTIVE target强制480p，工作区提示并支持“一键派生1080p交付目标版本”：沿现有target版本服务创建新版本，保留目录/品牌等设置，明确width/height/codec政策。不要原地修改被旧包引用的目标版本。

是否发布/切换目标必须是工作区显式动作；应用超分预设本身不修改全项目ACTIVE target。横竖混合项目逐集分组到兼容目标，不把一个1920×1080 target用于所有竖屏集。

打包前检查selected render的输出规格和target，打包后对实际输出再probe与完整性核验。target不匹配应阻塞；超分结果不能再被交付流程静默scale回低清。只有水印等确需重新编码时才重编码并记录，默认无效果变化应直接复制已编码输出。

### 11.4 清单与批量打包

新的交付manifest版本保留旧字段，同时增加`derivation`：root render、actual source type/id/hash、upscale run、snapshot/handler/model hash、output geometry、QC、人工review、selection revision、applied effects和打包转码事实。原v3 manifest读取/验证仍支持。

批量打包使用已采用且已批准列表，统一预检，逐集复用DELIVERY_BUILD。原服务同样需要transaction-aware enqueue使批次与job links原子可见；运行失败允许部分完成，UI逐集报告并只重试失败项。不同集的package都是不可变新目录；批量打包不等于整剧一个压缩包，可在结果页统一列出下载/打开目录。

原delivery human/platform review仍按项目现有规则保留；超分审核和最终包装后的审核分别记录。首发默认超分完成后停在待审核，不后台自动盖章和替换已交付文件。

## 12. 前端实现分解

新增 `pages/ProjectDeliveryPage.tsx`，功能组件集中 `features/video-upscale/`，建议：

| 组件/模块 | 职责 |
|---|---|
| EpisodeDeliveryTable | 服务端分页、稳定排序、跨页选择、源/目标与禁选原因 |
| UpscaleConfigurationPanel | 预设/模型/输出/高级参数，合同驱动控件 |
| UpscalePlanPanel | 新建/复用/跳过/阻塞摘要及逐集差异 |
| UpscaleBatchQueue | 批次和逐集进度、控制及恢复 |
| UpscaleComparePanel | source/result同步对比和质量检查入口 |
| UpscalePresetEditor | 保存预设版本与项目默认 |
| UpscaleEngineSetupPanel | 置于模型页的接入向导，共用参数和验证状态 |
| DeliveryVersionPicker | 单集/整剧共用采用版本选择；不再只取latest |
| hooks/queryKeys/types | 参数草稿、选择集合、polling和缓存失效 |

React Query key包含project、filters、cursor、batch/run、profile version。列表刷新保留数据和选择；新配置使prepared plan立刻失效。Job活跃时约2秒轮询，后台标签页降为10秒、终态停止；可复用既有event/outbox机制，不能每2秒轮询全剧每个独立Job。

草稿可保存在带project/user/schema版本的localStorage中，恢复后重新解析Profile与源revision；active batch从服务端恢复，不依赖localStorage是否还在。URL保留view、batchId、episodeId、renderId和过滤，不把大规模ID列表直接塞URL。

作用域说明直接在控件旁显示“仅本批 / 覆盖1集 / 项目默认”；切预设前的改动有dirty提示。使用仓库已有Dialog/Tabs/Button/字段样式，不为了本功能安装整套UI库。

## 13. 工作包、实施顺序与回归范围

所有WP均属于完整首发；每包完成含对应测试，不把真实模型验收拖成未标注的后续工作。

| WP | 工作内容 | 主要文件/交付 | 依赖与出口 |
|---|---|---|---|
| WP01 | 来源/目标/版本语义与API schema | 新domain/schema、geometry函数、source resolver | 固定输入/输出合同；T01–T08 |
| WP02 | 迁移与当前成片解析 | 新迁移、render_kind、selection、共享approval validator、读模型 | WP01；旧交付/合成语义回归全绿 |
| WP03 | NCNN安装/Profile/参数合同 | 模型平台adapter、runtime探测、向导facade | WP01；真实图片/短视频smoke；支持项可验证 |
| WP04 | 执行上下文/GPU/runner | registry、NCNN lifecycle、进度/取消context、分块流水线 | WP03；真实一段视频成功与停止恢复 |
| WP05 | 计划/批次/幂等 | 后台预检、atomic MP提交、run dedupe、控制/reconcile | WP02/04；竞争/崩溃测试通过 |
| WP06 | 预设、项目配置、样片 | 内置/用户版本、来源合并、benchmark | WP03/05；试跑不登记整集 |
| WP07 | 整剧交付页面 | 项目入口、表格/配置/计划/队列/对比；单集联动 | WP05/06；三视口浏览器流程 |
| WP08 | QC、审核采用、正式打包 | derived register、template、batch select/build、manifest | WP02/05；真实1080p包验收 |
| WP09 | 回归/发行/说明 | 全量合同与定向回归、runtime脚本、证据和操作指南 | 全部；达到测试文档G1–G5 |

建议先做“一集真实完成并登记派生render”的纵向切片（WP01/02/03/04必要部分），再扩批次与UI，最后完成交付矩阵；不能先做一张填满假模型的页面再假定引擎会补上。

必须同步触达：`api/routes`注册、`api/schemas`、`model_platform/production_execution_registry.py`、`execution_planning/submission`、`worker_execution_handlers`、`application/jobs.py`必要控制与stage、`job_resources/gpu_runtime`、GPU生命周期registry与安装schema约束、`timeline_status/g8_readiness/episode_render_approval`、`timeline.py`交付/查询、`post_repository/product_context_repository/review_decision_repository`、`automation_task.py`、`reviews.py`、`routeRegistry/router/AppShell`、`DeliveryPage/DeliveryWorkflowPanel`、`ProductionSettingsPage/ModelsPage/JobsPage`、`scripts/generate_client.py`及生成物。

不要继续把所有新逻辑塞进已很大的`timeline.py`；通过新的领域服务和端口扩展，并让旧入口调用共享规则。数据库迁移号在实施时读取Alembic head决定，本设计不预占下一号。

## 14. 验证计划与运维要求

详细用例见[测试与验收](test-and-acceptance.md)。必须测试真正的输出文件、音轨、帧数、选择和package，不只断言“接口返回202”。重点覆盖：多选边界、跨页全选、相同请求双击、两个批次复用、Profile变化、GPU互斥、OOM、磁盘不足、暂停旧进程未退、lease丢失、文件登记崩溃、音视频尾部、字幕水印重复、root更新后stale、最新候选不覆盖已采用版本。

提供操作者说明：首次安装和验证、480p整剧到1080p完整流程、试跑如何比较、预设如何保存、任务中心如何恢复、源更新后如何重跑、低空间如何清理、1080p交付目标如何派生、回原版方式。说明中明确处理速度因模型/设备/编码而异，给实测报告入口。

日志和诊断导出包含版本、hash、状态、阶段、输入输出规格、脱敏命令、错误和checkpoint摘要；不自动打包用户完整视频，不显示凭据。证据中写明真实运行的硬件/驱动/model binary版本，不把模拟器测试当GPU测试。

## 15. 设计依据与需实施时核实的外部事实

- [Real-ESRGAN NCNN/Vulkan 官方仓库](https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan)：程序输入、模型名称、倍率/tile/线程/TTA等CLI能力。实际安装包支持矩阵仍以该冻结版本测试为准。
- [Real-ESRGAN 动漫视频模型文档](https://github.com/xinntao/Real-ESRGAN/blob/master/docs/anime_video_model.md)：动漫模型定位及抽帧、推理、回组视频路线。
- [Real-ESRGAN 官方视频脚本](https://github.com/xinntao/Real-ESRGAN/blob/master/inference_realesrgan_video.py)：PyTorch与NCNN参数不能混用；某些降噪/人脸项有模型限制。首发不依赖其作为生产runner。
- [FFmpeg 官方文档](https://ffmpeg.org/ffmpeg.html)：映射、时间基、fps_mode、CFR/VFR语义；CFR模式可能复制或丢弃帧，因此“设置-r”不等于保证原帧完整。
- 本仓库 `design-system/localdramastudio/MASTER.md`：页面视觉和交互基线；UI/UX技能只用于补充批量操作、表单反馈和状态可读性。

查询日期2026-09-21。上述来源不构成本机驱动兼容、吞吐、许可证范围或模型效果的验收证明；选定release后把精确来源与文件指纹写入安装证据。设计中的默认值、数据表、批次策略与验收阈值是本项目工程决策。
