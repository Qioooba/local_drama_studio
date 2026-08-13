# G6 进度报告（未退出）

状态：`IN_PROGRESS`。本文件不是 G6 exit report，也不允许推进 G7。

2026-08-13 live continuation：

- 用户已释放 ComfyUI；4/4 标记为 `comfyui` 的 live 控制面测试通过，随后完整 API 套件为 68/68。新增 provider queue 状态映射测试后完整套件为 69/69，Ruff PASS，mypy 72 source files PASS。
- 单条受控 H3 T2VA 候选探针真实提交：workflow `d85d8777-4628-4395-9422-ab027d23c3d3` 本机 validation/publish PASS；Job `8e7c38d3-f9f3-47e2-91fb-4de3b9baf519`、Attempt `208a1ba9-b9e0-47c6-a502-0284631e9555`、Comfy prompt `0abb4afa-e701-4d0f-8c83-dff6de85fb02` 均真实持久化。
- 探针执行中 ComfyUI loopback 再次消失，无 history、无 artifact；队列按 uncertain-side-effect 路径落为 `NEEDS_ATTENTION` / `ORPHANED`，没有假成功或盲目重试。恢复后 VRAM 约从 2.4GB free 回到 24.4GB free。
- 修复状态真实性：history 尚未生成时读取 `/queue`，分别返回 `RUNNING`、`QUEUED` 或 `PROVIDER_UNCONFIRMED`，不再把运行中的 H3 误报为排队。
- 修复 Production Comfy 生命周期：启动状态记录 listener/launcher PID，固定独立 output/temp/user/log，stop 只终止已跟踪且命令匹配的 loopback 进程；start/stop/start 实测通过。
- 启动日志发现 ComfyUI-Manager `network_mode: public` 且访问 GitHub raw，触发零公网 stop-the-line。Production 启动器现用 `--disable-all-custom-nodes --whitelist-custom-nodes ComfyUI_RH_MinMaxH3 --disable-api-nodes`，Manager/API/custom-node 外联面被禁用；新日志仅加载 H3 节点、无公网 URL，H3 `/object_info` 仍 PASS。
- 第二条隔离探针 Job `81685244-ea72-4597-845c-573dd3982ed3` 在真实 denoise 完成、VAE decode 退出阶段复现 Windows `c10.dll` access violation；Attempt `39a113b3-0fe8-4244-ae2b-70d37882afde` 已如实 reconcile 为 `ORPHANED`，Job 为 `NEEDS_ATTENTION`。栈定位到 VAE session `finally` 的 `adapter.offload()`，当时 H3 text encoder 已占用 25.62GB 物理存储。
- 按“一任务一 Worker”生产策略新增显式 `LOCAL_DRAMA_H3_EPHEMERAL_WORKER=1`：成功解码后不再把 VAE 整体回迁 CPU，由受控进程退出统一回收。修复后 Job `48b87103-0729-4935-a6bf-1a1d5d2c14ab`、Attempt `a9da557c-89ad-4f46-8ddf-c9c8ba4daa0e`、prompt `29c8ac13-17f4-4a3f-ba26-9615f277a4fe` 真实 `SUCCEEDED`；产物 artifact `654ac99b-2249-415c-9b19-1ea614c8ec72` VERIFIED，SHA-256 `03131d5c89b325067a590a812eedcdf332db16f65b3b6beac20c61d639d89fe5`，316,346 bytes。FFprobe：H.264、1344×768、24fps、4.458333s；本次文件无音频 stream，不能据此宣称音频交付通过。
- 成功后 Production Comfy 由跟踪 PID 停止，loopback 消失，GPU 显存恢复至约 23.9GB free；启动日志公网关键字审计无命中。
- 新增 migration `0013_g6_artifact_media_lineage` 与 `POST /artifacts/{id}:promote-media`：只有 Job/Attempt 均成功且 artifact hash 仍匹配时，才原子复制进项目并登记不可变 MediaVersion；`source_artifact_id`、`source_job_attempt_id` 与审计事件保留完整谱系，重复晋升幂等，篡改或未完成 Attempt 均拒绝。
- 上述真实 artifact 已晋升为 MediaVersion `1f74dbd3-c2f9-4897-acac-925d3ceb0e0a`，FFprobe 与 machine check 均 PASS，并进入真实 review inbox；未进行人工批准或 proxy winner 选择，避免把技术探针冒充正式创意取舍。
- 新增真实证据门禁的 ProfileVersion 发布命令：仅接受 PUBLISHED workflow 与同一成功 Job→Attempt→Artifact→MediaVersion 完整谱系。生产库据此新建 T2V ProfileVersion `988b3fc3-f546-4300-b6ca-7f4756803179`（v2, `PUBLISHED`）；旧 Candidate 保持不可变，未原位改写状态。
- 新链路回归后安全 API 67/67（4 live deselected）、Web 3/3、生产构建 PASS、mypy 72 source files PASS、新改动 Ruff PASS。直接运行含 live marker 的全套在 Comfy 已按 ephemeral 策略停止后有 3 个 loopback 连接失败，故不宣称该次全套 PASS。
- 四条正式 T2V proxy 已通过独立 GenerationVariant + 独立 GPU_H3 Job 串行真实运行，seeds 260821—260824；每条均 provider history success、VERIFIED artifact、独立 MediaVersion、FFprobe PASS、machine check PASS，随后各自停止 Comfy 回收显存。四个 MediaVersion：`0e86ddf4-bfce-41b2-bd6a-b0d1a684aa19`、`b653b992-f3d0-4f15-921f-bdcdbb1ef0e8`、`7f4120b1-0e4f-4208-964a-4dfd785b582a`、`45b63e48-cc96-4ab8-894e-e8bdad3fc0e8`；时长均 4458ms、24/1fps，四个 SHA-256 均不同。
- Artifact 晋升现在同步将其 `GENERATION_VARIANT` 收敛为 `SUCCEEDED`；四个正式 Variant 状态已核验全为 SUCCEEDED。640×368、2.3KB 低码率联系表位于 `docs/evidence/g6/thumbnails/g6-four-proxy-contact-sheet-640.jpg`。缩略图整体很暗，不能据此代替人工完成身份/动作/连续性审核，因此 selection/review 仍未宣称通过。

已完成并留证：

- `0005_g6_comfy_workflows` migration：workflow version、package path、Comfy prompt/client/sandbox 字段。
- loopback-only Comfy client：object_info、queue/history、interrupt、WebSocket、显式 output root、越界/symlink 拒绝、artifact hash/register。
- workflow package 原子落盘、semantic input-slot compiler、local node validation、publish/rollback，以及 H3 候选 workflow API。
- 持久 GPU_H3 单 worker 提交、heartbeat、provider history、失败/租约过期/reconcile/recovery 路径。
- Ollama loopback adapter、显式本地 LLM Profile、最小真实 load probe、脚本导入 breakdown 持久化链路；模型不可加载时拒绝伪造 draft。
- CameraPlan（NATIVE/PROMPT_FALLBACK/UNSUPPORTED）、MotionMask、TimedDirection、PerformanceBinding 契约。
- G6 真实前端生成实验视图与阻塞截图。
- GenerationExperiment 的 plan-before-jobs 门禁：当前 plan hash 确认、超过 24 cells 二次确认、DRAFT 禁止 expand；service/API 契约已证明拒绝路径不创建 Job。
- GenerationExperiment `cancel-remaining` 保留终态历史、取消排队/活动 cells、阻止后续懒展开并记录 outbox。
- GenerationExperiment 展开时 Job/cell/idempotency/audit/outbox 原子提交；故障注入证明无孤儿 Job 或半成品 cell。
- GenerationVariant 的服务端 preflight/plan-hash/Published Profile/semantic slot/integrity/lineage 门禁；manifest 与模型 bundle 的版本快照不会被启动同步原位改写。
- 不可变 PromptRevision、服务端 Exact Replay/Resample seed batch 派生、Prompt/Source branch derive-plan 与混合 scope 拒绝、First/End 尺寸比例门禁及 Transition anchor 静态验证已加入；这些只证明离线契约，不替代真实 H3 闭环。
- TC-VAR-014 的批准影响预览和事务化 stale propagation 已加入：仅正式替换 Shot 已批准首帧时影响 REQUIRED `END_AT_NEXT_FIRST` 与绑定旧首帧的下游 Variant；实验选择不污染主线。迁移、API、原子回滚、audit/outbox 均有本地测试证据。
- 上游视频 winner 替换同样纳入影响预览：旧 winner 派生的 `LAST_FRAME` Anchor、引用该 Anchor 的 REQUIRED Transition、绑定其 extracted image 的下游 Variant 会事务化 stale；真实本地 MP4/FFmpeg 测试已覆盖，仍不替代 H3 真实生成闭环。
- TC-VAR-015 已以真实篡改文件验证：Variant input 与 FrameAnchor source 在预检时重新计算当前 SHA-256/size；不一致即 `CORRUPT` 并零副作用阻塞，不会靠数据库旧状态冒充完整。
- Transition validate 同样实时复核既有 Anchor 的 source/extracted 文件；后置篡改任一端都会阻塞并持久化明确的对象级证据。
- stale Variant/Anchor 已成为服务端硬门禁而非 UI 标签：不能作为派生父节点、生成输入或新 Transition anchor；历史记录保持可查询。
- Variant 的 plan hash 不是文件完整性的永久通行证；create 阶段重新校验实际文件，plan→create 间篡改已用 API 测试证明零副作用阻塞。
- Profile A/B derive-plan 已支持 `PROFILE_BRANCH` 且强制 Published/单字段变化/零预检持久化；这只是 TC-VAR-013 的离线前置契约，真实双 Profile 生成、产物登记与 review 隔离仍未完成。
- Live ComfyUI 测试现有 `comfyui` marker 与进程级访问 guard；默认 API/check 命令只运行 `not comfyui`，当前为 69 passed / 4 deselected。此隔离只保护操作者占用边界，不构成 G6 live evidence。
- TC-VAR-004 Provider random 已有不可变 nonce、Profile seed capability、字段范围与非可复现声明的离线闭环；未创建 Job/take，不能宣称完整通过。当前安全套件为 62 passed / 4 deselected。
- Resample/Profile/Provider-random 三类分支现均在 commit-time 复核字段范围，不能通过直接 create 绕开 derive-plan；当前安全套件为 63 passed / 4 deselected，仍不构成真实生成证据。
- TC-VAR-009 已用真实三帧视频验证 FIRST/CURRENT/LAST 的 FFprobe PTS→frame-index 精确提取、源/输出 hash、不可变 MediaVersion、越界零副作用及临时文件清理；安全套件为 64 passed / 4 deselected。播放器菜单仍未完成。
- TC-VAR-007 的不支持尾帧路径现在给出 ProfileVersion/roles/capability/suggested action，不再只返回泛化 unknown role；仍仅证明提交前门禁，不替代真实 First/Last workflow。
- TC-VAR-003 已证明 operational retry 只给同一 Job 新增 Attempt，不新增 Variant/take；测试严格使用 CPU channel，未触碰当前用户占用的 ComfyUI。

当前剩余门禁：

1. 四条真实 proxy takes、MediaVersion 与机器 QC 已完成；人工 selection/review 尚未完成，且低码率缩略图显示整体偏暗，不能由自动化冒充创意审核通过。
2. Ollama 标签可见但 `qwen3:8b` 最小真实 load probe 返回 HTTP 500 model-load failure；Profile 已降级为候选未验证，脚本拆解拒绝伪造 draft。

本轮再次启动本机 Ollama 0.6.5 复核：三份模型标签均可见；`qwen3:8b` load test 仍 `BLOCKED / LOCAL_LLM_LOOPBACK_UNAVAILABLE`，而已发布 `deepseek-r1:14b` load test PASS，历史真实 `DRAFT_READY` 拆解仍存在。测试完成后只终止本轮启动的 serve/runner，GPU 显存恢复约 24.0GB free。qwen 候选保持未发布；G6 可使用已发布 DeepSeek 路径，但 qwen 问题仍作为已知限制保留。

生成工作台候选架已使用真实 review inbox 媒体，且只请求 small 海报缩略图。修复初始空 `sourceVideoId` 的缩略图竞态后，1440×900、1280×800、1024×768 三档 Playwright 均为零水平溢出、零 console/page error、零失败响应；结构化证据在 `apps/web/output/playwright/generation-validation.json`，人工查看仅使用最大边 720px 的低码率 JPG。

本轮新增只读 `GET /projects/{project_id}/gates/g6`，从持久化数据逐项核验批准关键帧、Published I2V、同源 4 take、人工 winner、正式视频、机器 QC 与人工正式批准；接口明确 `mutated=false`，不会替人创建选择/审核。当前真实项目返回 `IN_PROGRESS / APPROVED_KEYFRAME`，批准关键帧数量为 0。

H3 FL2VA I2V graph 已按本机节点真实契约实现：LoadImage → FirstFrameCondition → FL2VA Target/Encode → DualSigmaSampler → Decode/Save。Production Comfy 新增独立 input root，Variant 的 VERIFIED 媒体绑定在提交时按 MediaVersion ID+SHA 物化为相对文件名，避免宿主绝对路径进入 workflow；离线测试已证明复制内容和编译绑定一致。受控 Comfy 实测 3/3 live 测试通过，新 I2V WorkflowVersion `4670a8f1-1c37-4a75-a9bc-7663818d7135` 节点/runtime layout 验证 PASS 并发布；没有成功 I2V 媒体证据，因此 I2V Profile 仍保持候选，未越权发布。验证后 Comfy 已停止且 8188 无监听。

生成工作台已显示七项 G6 真实门禁与“下一项真实动作”，三档 Playwright 复验均有 7 项门禁、零水平溢出、零 console/page error、零失败响应，且明确下一步为“批准一张真实关键帧”。当前安全 API 为 72 passed / 4 Comfy live deselected，Web 3/3、生产构建、Ruff、mypy 74 source files 均 PASS。

续跑新增 `FrameAnchor → shot-owned KEYFRAME candidate` 的幂等命令和生成工作台入口。候选继承真实图片 hash/探测信息，保持不可变 parent lineage，只进入人工审核，不自动 selection/approval；同时修复了错误读取不存在的 `shots.project_id`，统一按 `shots → episodes → seasons → projects` 校验归属。I2V evidence publish 现同时强制 workflow capability 匹配、完整成功媒体谱系、唯一 FIRST_FRAME，且该帧必须是同项目 VERIFIED/KEYFRAME/approved version；T2V 或未批准图片均不能冒充 I2V 发布证据。Comfy input 物化后会重新计算 SHA-256，不一致即删除并硬失败。最终安全 API 75 passed / 4 Comfy live deselected，Web 4/4、生产构建、Ruff、mypy 74 source files 全部 PASS。

三档 Playwright 再验：1440×900、1280×800、1024×768 均为 7 项门禁、6 个真实候选 small 缩略图、零水平溢出、零 console/page error、零失败响应。视觉只检查 8—17KB、最大边 720px 的低码率 JPEG，未加载原始媒体。Figma 本轮只做一次 inspect 尝试，Starter MCP quota 明确拒绝，未写画布、未重复 foundations，按插件错误规范没有重试。

生产 UAT 已从现有 VERIFIED `LAST_FRAME` FrameAnchor 派生关键帧候选 `0d389e44-0fc3-47e2-b492-f7e3501ccf0c`，归属镜头 `020f9248-14b7-4f92-9edd-ee587ffdedf3`，parent/hash/PNG probe 均保留；机器 QC 的 file integrity 与 decode 为 PASS。1280×800 审核页 E2E 仅请求 320px small 缩略图，机器 PASS 与“选择为 KEYFRAME”动作可见，未显示已选择，零溢出/console/page error/失败响应。未创建 ReviewDecision/Selection，`approved_version_id` 与 `selected_version_id` 仍为空，G6 继续 `IN_PROGRESS / APPROVED_KEYFRAME (0)`。

门禁：四条真实 proxy 已完成，但仍缺人工 winner、从批准关键帧生成的正式视频及正式审核；因此不提交 G6 PASS，不进入 G7。

完整证据见 `docs/evidence/g6/g6_validation.txt`。

2026-08-13 接管后的安全测试、三档 Playwright 验收与实验确认门禁证据见 `docs/evidence/g6/handoff_validation_2026-08-13.md`。

用户完成关键帧人工批准后，生产 MediaVersion `0d389e44-0fc3-47e2-b492-f7e3501ccf0c` 已成为镜头当前 approved/selected KEYFRAME，机器 QC PASS；G6 七项门禁仅第一项通过，下一项仍为 `PUBLISHED_I2V_PROFILE`。快速重复点击留下的 9 条 APPROVED 审计记录按不可删除原则保留；服务端已增加同一当前版本重复批准幂等返回，审核深链接和已批准禁用态也已修复。

正式 `I2V_PROXY` / `I2V_FORMAL` Variant 现在必须绑定同项目、同镜头、当前 approved、VERIFIED KEYFRAME 和最新非 stale APPROVED ReviewDecision；`approval_id` 同时冻结进 Variant input binding、Job media snapshot，并纳入 recipe hash，plan→create 之间的批准替换不会静默沿用旧通行证。负向拒绝零 Variant/Job，PLANNED 与 QUEUED 两条路径均有回归覆盖。

用户当前重新占用 ComfyUI，因此本轮没有启动、提交、轮询、恢复或访问 8188。新增只读 I2V evidence probe plan，生产数据返回 READY：批准记录 `c092a9f5-20fc-4165-bd4b-19ef8487d7cc`、FL2VA workflow `4670a8f1-1c37-4a75-a9bc-7663818d7135`、候选 Profile 状态 `CANDIDATE_BLOCKED`、计划哈希 `c4fb920edffb441b2d941c63afac8f4bf50ab0033a5d37f36b407861bc8f563e`。读取前后 Job 数均为 16，接口明确不创建 Job、不连接 ComfyUI并要求后续单独确认；工作台同步显示这些冻结证据。

最终离线回归：安全 API `77 passed, 4 deselected`，Web 4/4，生产构建 PASS，mypy 75 source files PASS，全 API Ruff PASS；8 个既有 Alembic import-order 问题仅做排序修复，迁移测试 2/2 PASS。1440×900、1280×800、1024×768 Playwright 均为 7 项门禁、探针声明可见、零水平溢出、零 console warning/error，所有媒体 URL 均含 `size=small`。视觉证据为最大宽 720px、11—13KB WebP，未查看原图。G6 仍非 PASS，不进入 G7。

G6-07 新增不接触 runtime 的 workflow history read model/API/UI，列出 immutable version、DRAFT/PUBLISHED/RETIRED、capability、content hash 与发布时间，并明确验证/发布/回滚仍需显式操作。生产返回 5 个版本、4 个 PUBLISHED、`runtime_contacted=false`，FL2VA I2V 版本状态和 hash 与探针计划一致。新增只读零数据库变更测试后，安全 API 为 `78 passed, 4 deselected`；Web 4/4、build、全 API Ruff、mypy 仍 PASS。API 重启后 PID 23724，HEALTHY/LOCAL_ONLY；未访问 ComfyUI。

G6-07 页面三档 Playwright 复验：1440×900、1280×800、1024×768 均显示 5 条真实 workflow version，安全声明可见，零水平溢出、零 console warning/error。该页没有 runtime mutation 控件，因此不会在用户占用期间误触验证、发布、回滚或 ComfyUI。

用户释放 ComfyUI 后，批准 KEYFRAME 的首条真实 FL2VA 探针暴露两项 H3 runtime 控制面缺陷并按不可覆盖历史处理：固定 75 秒 telemetry abort 会误杀 24GB visual-conditioned slow path；固定 18GB decode reserve 会拒绝已完成采样的 352×640 proxy。前者仅将 FL2VA 与既有 Ref2VA 一样纳入慢路径豁免，900 秒 stall/OOM/cancel/error 仍保留；后者改为按输出面积在 8—18GB 之间缩放，最大 768×1344 仍保持 18GB。本轮 FAILED/CANCELLED Job/Attempt 均保留。

新 proxy-evidence workflow `6e09d8c3-bb15-4c48-9253-cbdf609a8670` 经真实本机 node/runtime validation 并以服务端 attestation 发布，参数 352×640、4 秒、21 sigma、accel off。最终 Job `2b8190de-cfe8-4dbc-87ea-cdaf743d8176`、Attempt `29f68cda-67e5-4f02-962b-b1946ef708bf`、prompt `7a3e259d-1c61-40f1-a1de-960b813509a5` 真实 SUCCEEDED；artifact `690c65df-f329-4e40-b014-553f39246be6` VERIFIED（562,600 bytes，SHA-256 `0219a3ddad4aa7545c33f2a80816ed1c09a61acb6e46a710b8fdbb5c9e87c37d`）。FFprobe 为 H.264 High、352×640、24fps、4.458008s、107 帧、无音频 stream；不能据此宣称音频能力。

artifact 已晋升为 MediaVersion `dc26b0ad-7341-43f9-aa5e-9c9ba97e9664`，file integrity/decode machine check PASS。I2V candidate Profile 以完整 approved FIRST_FRAME + workflow + Job/Attempt + artifact + MediaVersion 谱系发布为新 v2 `050c5caf-f47d-4641-8ec3-893e663efdc9`，未覆盖旧候选。G6 readiness 现在仅 `APPROVED_KEYFRAME` 与 `PUBLISHED_I2V_PROFILE` PASS；下一项为 `FOUR_REAL_PROXY_TAKES (0)`。该 evidence 媒体是能力发布样片，不计入 4 个创意候选。Worker 已停止且显存约 23.7GB free；安全 API 79/79（4 live deselected）、Web 4/4、build/Ruff/mypy PASS。

