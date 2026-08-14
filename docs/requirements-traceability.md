# 需求追踪状态（G0 初始化）

权威详细映射仍为 `LocalDramaStudio_Blueprint_v2/13_需求追踪矩阵.md`；本文件记录本实现仓库的状态协议。每个阶段必须把状态从 `NOT_STARTED` 推进为 `IMPLEMENTED`、`VERIFIED`、`ACCEPTED`，不得以代码存在替代验证或 UAT。

## 基线计数

| 类别 | 蓝图基线 | G0 状态 | 规则 |
|---|---:|---|---|
| 功能需求 | 86/86 | NOT_STARTED（已登记） | 以 01 的需求表为准；P0/P1 最终必须逐项闭环 |
| 非功能需求 | 14/14 | NOT_STARTED（已登记） | 以 01 的非功能表和 13 的验证映射为准 |
| 主干测试 | 85 | NOT_STARTED（已登记） | 以 10 的 TC ID 为准；不得用 mock/static page 冒充 |
| 阶段门禁 | G0—G10 | G0 IN_PROGRESS | 只按 09 的顺序推进 |
| legacy 迁移 | G11 | DEFERRED | 本次禁止实施 |

## G0 P0 映射确认

| 需求域 | 实现边界 | 进入阶段 | 主要验证 |
|---|---|---|---|
| Project/Season/Episode/Shot | domain、repository、template、命令 | G2/G3 | TC-DOM-001—007 |
| Revision/Prompt/Production Ready | creative domain、blockers、next-actions | G2/G3/G6 | TC-DOM-005/006/007 |
| Media/Selection/Review | immutable media、review template/check、人工决策 | G3/G4 | TC-MED、TC-REV |
| GenerationVariant/Lineage/FrameAnchor/Transition | generation domain、semantic bindings、stale | G2/G4/G5/G6/G8 | TC-VAR |
| Job/Attempt/Lease/Outbox | persistent queue、scheduler、reconciler、SSE | G2/G5 | TC-JOB |
| Runtime/Profile/Workflow | local registry、manifest、capability contract | G3/G6/G7 | TC-WFL、TC-PRV |
| Timeline/Render/Delivery | immutable snapshots、FFmpeg、manifest/hash/verify | G8 | TC-TML、TC-DEL |
| Security/Recovery/Scale | localhost/CSRF/path/network/backup/60集 fixture | G1/G5/G7/G10 | NFR suite、UAT |

## G2 验证状态

| 需求/验证范围 | 状态 | 证据 |
|---|---|---|
| Project/Season/Episode/Shot 持久化、60 集模板、身份/排序 | VERIFIED | `apps/api/tests/test_project_commands.py`；G2 migration |
| Revision/Prompt/Production Ready 的显式字段与阻塞 | VERIFIED | `apps/api/tests/test_project_commands.py`、`test_domain_rules.py` |
| GenerationVariant lineage/binding/seed/replay 基础规则 | VERIFIED | `apps/api/tests/test_domain_rules.py`、`domain/generation.py` |
| SQLite WAL/外键/完整性/在线备份/迁移前备份 | VERIFIED | `apps/api/tests/test_migration.py`、`docs/evidence/g2/g2_validation.txt` |
| API 真实迁移存储、乐观并发冲突 | VERIFIED | `apps/api/tests/test_api_projects.py` |
| 统一 Idempotency-Key、持久 Job/Attempt/Lease/SSE | NOT_STARTED（G5） | 不在 G2 退出范围 |
| 86 FR、14 NFR、85 TC、完整本地 UAT、发布 | NOT_STARTED（G10） | 继续按 G3→G10 门禁 |

## G3 验证状态

| 需求/验证范围 | 状态 | 证据 |
|---|---|---|
| FR-PRV-001/003 本地 Runtime、模型 artifact、manifest-backed Profile 候选 | VERIFIED | `application/profiles.py`、`test_g3_configuration_media.py`、G3 screenshots |
| FR-PRJ-007 项目健康/路径与本地资源诊断基础 | VERIFIED | `application/diagnostics.py`、`/diagnostics/runs` |
| FR-IMG-001/FR-MED media register、probe、hash、poster/cache、Range | VERIFIED | `application/media.py`、G3 evidence sample |
| FR-ING-001 source document version、ImportSession、TXT/MD/DOCX preview | VERIFIED | `application/documents.py`、G3 import test |
| FR-SRC-001 FTS5 global search minimum | VERIFIED | `application/read_models.py`、G3 import/search test |
| G3 production read model / no N+1 page query | VERIFIED | `ProductionReadModelService`、browser production screenshot |
| FR-ING-002 local LLM breakdown | BLOCKED_BY_EXPLICIT_PROFILE | Refuses without published local LLM; no fake result; G6/G7 |
| FR-REV review/selection | VERIFIED | `application/reviews.py`、`test_g4_reviews.py`、`docs/evidence/g4/g4_validation.txt`；selection/approval 分离、机器 QC、stale 与 batch preflight 已验证 |
| FR-JOB queue/recovery, real generation | NOT_STARTED | G5/G6 |

## G4 验证状态

| 需求/验证范围 | 状态 | 证据 |
|---|---|---|
| FR-MED/FR-REV immutable MediaVersion stage、selection pointer、proxy winner、formal selection | VERIFIED | `application/media.py`、`application/reviews.py`、migration `0003_g4_review_selection`、G4 browser screenshots |
| FR-REV review templates、required checks、failed approval prevention | VERIFIED | `ReviewService.submit_review`、`test_g4_reviews.py` |
| FR-REV formal machine QC gate、persisted check results | VERIFIED | `ReviewService.machine_check`、真实 H3 MP4 probe、G4 validation evidence |
| FR-REV subject revision/stale propagation and void path | VERIFIED | `ProjectService.create_shot_revision`、G4 stale test and audit evidence |
| FR-REV batch preflight token and stale-safe commit | VERIFIED | `ReviewService.batch_preflight/batch_commit`、G4 invariant test |
| G4 API/OpenAPI/UI review inbox/context/selection | VERIFIED | `api/routes/reviews.py`、generated `api.ts`、browser evidence |
| G5 persistent queue/recovery, G6/G7 generation and G8 delivery | NOT_STARTED | Continue in strict WBS order |

## G5 验证状态

| 需求/验证范围 | 状态 | 证据 |
|---|---|---|
| FR-JOB Job/Attempt/Dependency/priority/channel/lease/heartbeat | VERIFIED | `application/jobs.py`、migration `0004_g5_job_queue`、`test_g5_jobs.py` |
| FR-JOB Idempotency-Key、retry/clone/cancel/backoff | VERIFIED | Job API、payload hash、G5 queue tests |
| FR-JOB outbox/SSE cursor and real task UI | VERIFIED | `/api/v1/events`、generated client、G5 browser screenshots |
| FR-JOB reconcile/orphan/worker process kill recovery | VERIFIED | `test_worker_process_kill_is_reconciled_without_duplicate_attempt`、G5 recovery evidence |
| FR-MED local FFmpeg/FFprobe proxy/thumbnail worker and artifact registration | VERIFIED | `application/worker.py`、real media worker test、artifact hash evidence |
| FR-VAR generation plan/estimate/confirm/lazy matrix/cell cancel | VERIFIED | `application/experiments.py`、generation-plan routes、G5 matrix test |
| G6/G7 real ComfyUI/local LLM generation | NOT_STARTED | G6 remains blocked by local runtime evidence |

## G8 验证状态（代码完成，阶段门禁待正式退出）

| 需求/验证范围 | 状态 | 证据 |
|---|---|---|
| FR-TML immutable timeline revision、track item、project/media ownership | VERIFIED | `application/timeline.py`、`test_g8_timeline_delivery.py` |
| FR-AUD local audio binding、license status、range validation | VERIFIED | `audio_bindings` migration、G8 real API test |
| FR-SUB SRT/VTT/ASS rendering、overlap/CPS gate、immutable subtitle revision | VERIFIED | `subtitle_revisions`/`subtitle_cues` migration、G8 real API test |
| FR-CON FrameAnchor extraction、transition constraint persistence | VERIFIED | real FFmpeg frame extraction、`frame_anchors`/`shot_transition_constraints`；`0009_g6_continuity_stale`/`0010_g6_frame_anchor_stale` 测试覆盖批准影响预览、下游首帧与上游 winner 的事务化 stale 传播及实验选择隔离 |
| FR-MED/FR-CON live source integrity before generation/anchor | VERIFIED | `MediaService.verify_content_integrity`、TC-VAR-015 tamper tests；创建前 mismatch 零 Variant/Job/Anchor/派生媒体，既有 Anchor 的 source/extracted 后置篡改使 Transition `BLOCKED` 并标记 `CORRUPT` |
| FR-CON stale consumption gates | VERIFIED | Generation/Timeline service tests；stale Variant parent、stale Anchor extracted input、stale Transition anchor 均在新对象持久化前拒绝，历史保留 |
| FR-VAR plan/create integrity race | VERIFIED | API regression：合法 preflight 后篡改输入，create 实时复核并以 `SOURCE_INTEGRITY_FAILED` 零 Variant/Job/audit 阻塞 |
| FR-VAR unsupported First/Last capability action | VERIFIED | TC-VAR-007 API regression；Published Profile 缺 END_FRAME 时返回 Profile/roles/capability/suggested action，零 Variant/Job |
| FR-VAR operational retry isolation | VERIFIED | TC-VAR-003 CPU persistent queue regression；同一 Job attempt 1→2，Variant/Job/take 数不增，未使用 GPU_H3 |
| FR-ENH capability-driven technical enhancement chain | VERIFIED | persisted recipe/run、real FFmpeg output and MediaVersion registration |
| FR-DEL local filesystem delivery、manifest/hash verify、tamper detection、withdraw | VERIFIED | `delivery_events` migration、G8 real delivery/tamper/recovery test |
| G8 migration/OpenAPI/static/type/full API regression | VERIFIED | `0006_g8_timeline_audio_delivery`、generated OpenAPI、33 API tests, Ruff, mypy |
| G8 formal screenshots/sample/UAT and G8→G9 exit approval | IN_PROGRESS | Added read-only episode timeline/delivery status and formal G8 readiness projection; production EPISODE_001 still has 0 timeline SHOT-owned VIDEO / 0 authorized audio / 0 subtitle / 0 render / 0 delivery, so 3+ shot real sample, approved render, tamper verify and formal exit audit remain pending |

## G9 验证状态（代码完成，阶段门禁待正式退出）

| 需求/验证范围 | 状态 | 证据 |
|---|---|---|
| FR-WFL-003 React Flow 业务画布、node registry、edges、MiniMap | VERIFIED | `application/canvas.py`、React Flow `ProductionCanvasPanel`、web build |
| shot/episode lazy graph read model、分页与可见节点上限 | VERIFIED | 62 镜头测试按 20 镜头/100 节点分页，`test_g9_canvas.py` |
| 节点状态/take/variant/blocker/active job | VERIFIED | canvas graph API 基于 SQLite 权威状态汇总 |
| layout persistence 与业务依赖隔离 | VERIFIED | `canvas_layouts`、未知 node 拒绝、layout 后 edges 不变测试 |
| run node/from/to/range preflight | VERIFIED | `canvas_execution_plans`、max node/GPU concurrency/HITL/blocker 计划 |
| 画布性能基础、键盘/不可连接/不可删除替代约束 | PARTIAL / VERIFIED_BASELINE | lazy graph、300 node 上限、键盘节点列表、可命名画布/搜索控件；只读 G9 readiness 明确生产 1 shot/5 nodes 与 62-shot/100-node fixture 分离，正式 100—300 交互 benchmark 与完整可访问性清单待 UAT |
| 三视图 route/selection 同步、画布搜索与上/下游聚焦 | VERIFIED BASELINE | URL 状态同步；G9 画布新增本地搜索、传递闭包聚焦上游/下游；节点/键盘选择会回写 `shot` 查询参数并可从 URL 恢复；只改变可视节点集合，不改变业务 edges；App tests + 三档 Playwright + 1024 route-sync Playwright |
| automation/webhook/产能看板 | VERIFIED BASELINE | `POST /events:deliver` 仅支持显式 loopback、上限 100、2xx 后标记 outbox delivered；真实临时 loopback 服务回归、拒绝公网 URL；产能仍标记 `OBSERVED_NOT_BENCHMARKED`，不冒充 benchmark |
| G9-09 本机队列产能观测 | VERIFIED BASELINE | `GET /capacity/snapshot`；真实 SQLite Job/Attempt 状态、GPU 并发和近 24h 完成数；`OBSERVED_NOT_BENCHMARKED`、`would_create_jobs=false`、无 runtime/network/mutation；`test_capacity_snapshot.py` |
| G9-08 变体谱系、实验进度、相邻边界约束只读可视化 | VERIFIED BASELINE | Canvas graph read model 汇总真实 generation_variants/generation_experiments/experiment_cells/shot_transition_constraints；选中节点显示摘要，空数据不造数；`test_g9_canvas.py`、G9 validation evidence |

## G6 历史进度状态（已由 2026-08-14 退出证据取代）

| 需求/验证范围 | 状态 | 证据 |
|---|---|---|
| FR-WFL workflow package、semantic slots、local node validation、publish/rollback | VERIFIED | `application/workflows.py`、`0005_g6_comfy_workflows`、`test_g6_workflows.py` |
| FR-WFL Comfy loopback client、history/output collect、artifact hash/register | VERIFIED | `infrastructure/comfy.py`、`application/comfy_jobs.py`、真实 prompt evidence |
| FR-ING-002 real local LLM adapter/profile/load gate | PARTIAL | `deepseek-r1:14b` 已发布、真实 load test PASS 且历史 `DRAFT_READY` breakdown 存在；`qwen3:8b` 文件可见但真实 load test BLOCKED，保持候选不发布，禁止伪造结果 |
| FR-PRV H3 candidate capability truthfulness | PARTIAL / BLOCKED | `/api/v1/h3/candidate-runtime`；真实 FL2VA sidecar layout缺失，Comfy execution_error |
| FR-VAR CameraPlan/MotionMask/TimedDirection/PerformanceBinding contracts | VERIFIED | `domain/generation_contracts.py`、G6 tests |
| TC-VAR-013 Profile A/B branch isolation | PARTIAL | `PROFILE_BRANCH` derive-plan 仅改变 Published ProfileVersion，Candidate/混合 scope/零持久化已验证；真实双 Profile Job、artifact、隔离 review 未完成 |
| TC-VAR-004 Provider random resubmit | PARTIAL | `RESUBMIT_PROVIDER_RANDOM`、Profile seed support、唯一冻结 nonce、NON_REPRODUCIBLE 声明、plan/create 门禁已验证；真实 new Job/take 未完成 |
| Variant branch commit-time scope invariants | VERIFIED | 直接 plan/create 绕过测试覆盖 Resample/Profile/Provider-random，非法混合字段零 Variant/Job |
| TC-VAR-009 视频首/当前/末帧提取 | VERIFIED (API/domain/UI) | 真实三帧视频、FFprobe PTS、resolved frame/time、source/output hash、新 MediaVersion、越界零副作用；生成工作台真实 VIDEO 首/当前/末帧操作和未提交输入槽已接 API，Playwright 末帧 201 并冻结第 106 帧 / 4.417 秒；Variant 持久化提交仍属后续 G6 工作 |
| ComfyUI operator-ownership test isolation | VERIFIED | `comfyui` marker、`pnpm api:test:safe`、进程级 `LOCAL_DRAMA_COMFY_ACCESS=disabled`；用户释放后 4/4 live 与完整 69/69 PASS |
| G6 Production Comfy lifecycle / zero-public startup | VERIFIED (control plane) | 独立 output/temp/user/log、listener/launcher PID ownership、start-stop-start、H3-only whitelist、API nodes disabled；启动日志无公网 URL，Manager public-mode 事件已 stop-the-line 并修复 |
| G6 real H3 candidate/crash isolation | VERIFIED (single probe) | 两次崩溃均 `NEEDS_ATTENTION`/`ORPHANED` 留证；定位 VAE exit offload access violation 后以显式 ephemeral-worker 策略修复；Job `48b87103-0729-4935-a6bf-1a1d5d2c14ab` 真实成功并登记 VERIFIED H.264 artifact，进程/显存回收通过 |
| G6 artifact → MediaVersion lineage | VERIFIED | migration `0013_g6_artifact_media_lineage`、晋升 API、成功/幂等/篡改/非终态测试；真实 artifact 晋升为 MediaVersion `1f74dbd3-c2f9-4897-acac-925d3ceb0e0a`，FFprobe/machine check PASS、review inbox 可见 |
| G6 evidence-gated T2V Profile publish | VERIFIED | PUBLISHED workflow + 成功 Job/Attempt + VERIFIED Artifact/MediaVersion 谱系校验；新 ProfileVersion `988b3fc3-f546-4300-b6ca-7f4756803179` v2 PUBLISHED，旧 Candidate 未原位覆盖 |
| G6 four real proxy takes、formal selection/review | PARTIAL | 四条独立 seed/Variant/Job/prompt/artifact/MediaVersion 全部真实 SUCCEEDED，FFprobe 与 machine QC PASS；低码率联系表留证，但人工 selection/review 尚未完成，不得宣告 G6 PASS |
| G6 generation workbench responsive acceptance | VERIFIED | 真实 review inbox 候选；修复空 sourceVideoId thumbnail 竞态；1440×900、1280×800、1024×768 均无水平溢出、console/page error 或失败响应；仅查看最大边 720px 低码率证据 |
| G6 exit readiness truth API/UI | VERIFIED | 只读七项门禁，无 selection/review 副作用；生产项目真实返回 `APPROVED_KEYFRAME` 首阻塞；三档 UI 验收 7 项完整且零浏览器错误 |
| G6 H3 FL2VA I2V workflow/input materialization | VERIFIED (workflow control plane) | 按本机节点契约构建 first-frame graph；独立 Comfy input root；VERIFIED MediaVersion 按 ID+SHA 物化；WorkflowVersion `4670a8f1-1c37-4a75-a9bc-7663818d7135` 本机节点/layout PASS 并发布；尚无真实 I2V 成功 artifact，Profile 未发布 |
| G6 keyframe candidate handoff | VERIFIED (candidate only) | 真实 FrameAnchor extracted image 可幂等派生为 shot-owned KEYFRAME MediaVersion；项目归属、parent lineage、VERIFIED image/stage 硬门禁；生成工作台显式进入人工审核且不自动选择/批准；`test_keyframe_candidate.py` |
| G6 keyframe candidate production UAT | VERIFIED (pre-HITL) | 候选 `0d389e44-0fc3-47e2-b492-f7e3501ccf0c`、machine QC PASS；审核页只请求 320px small，机器 PASS/KEYFRAME 动作可见且未显示已选择；Review/Selection/approval 均为空，门禁未越权推进 |
| G6 I2V evidence-gated publish | VERIFIED (negative/control plane) | Published FL2VA workflow capability、成功 Job/Attempt、VERIFIED Artifact/Video、唯一 FIRST_FRAME、同项目已批准 KEYFRAME 全部硬门禁；缺批准帧的成功视频仍拒绝发布；真实 I2V 成功证据尚未产生 |
| G6 Comfy input copy integrity | VERIFIED | 隔离 input root 物化后逐块重算 SHA-256；不一致删除目标并 `COMFY_INPUT_INTEGRITY_MISMATCH`；离线队列测试 2/2 |
| G6 approved-keyframe snapshot invariant | VERIFIED (control plane) | `I2V_PROXY`/`I2V_FORMAL` 强制当前批准 KEYFRAME + 非 stale ReviewDecision；`approval_id` 冻结到 Variant binding、Job snapshot、recipe hash；`test_generation_variants.py` |
| G6 duplicate approval idempotency | VERIFIED | 当前同版本重复 APPROVED 返回既有 ReviewDecision，不新增审计记录；既有历史保留；`test_g4_reviews.py` |
| G6 read-only I2V evidence probe plan | VERIFIED (no execution) | 生产计划 READY，冻结批准/工作流/Profile/hash；调用前后 Job 16→16，明确不连接 ComfyUI；`application/i2v_probe.py`、`test_i2v_probe_plan.py` |
| G6-07 workflow test/publish/rollback history UI | VERIFIED (read-only surface) | `GET /workflow-versions` 返回 5 条 immutable history/status/hash/capability，`runtime_contacted=false`；模型与能力页可视化，读取零 DB 变更；三档零溢出/零 console error；显式 validate/publish/rollback 写操作既有 API 测试覆盖 |
| G6 FL2VA runtime slow-path/decode budget | VERIFIED (proxy evidence) | FL2VA telemetry 慢路径不再被 75s 误杀；decode reserve 按画布面积 8—18GB，最大画布保持 18GB；真实 20/20 DiT + video/audio decode 成功，Worker 回收通过 |
| G6 real I2V Profile evidence publish | VERIFIED | workflow `6e09d8c3...`、Job `2b8190de...`、Attempt `29f68cda...`、artifact `690c65df...`、MediaVersion `dc26b0ad...`、approved FIRST_FRAME approval `c092a9f5...`；Profile v2 `050c5caf...` PUBLISHED |
| G6 four same-keyframe creative proxy takes | NOT_STARTED / BLOCKED_NEXT | readiness 真实 count=0；能力证据样片不计入创意候选，下一门禁须生成 4 条独立 seed/Variant/Job/MediaVersion |

## G6 最终退出状态（2026-08-14，权威覆盖上方历史快照）

| 需求/验证范围 | 状态 | 证据 |
|---|---|---|
| 批准关键帧 + Published I2V Profile | VERIFIED | KEYFRAME `0d389e44...`、approval `c092a9f5...`；proxy Profile `7855a1b8...`、formal Profile `2147a504...`，均由真实成功谱系发布且旧版本不可变 |
| 四个同源真实 I2V proxy takes | VERIFIED | seeds 260826—260829；MediaVersion `7a620549...`、`8cc7c8d7...`、`63e59d0d...`、`2b5e49e4...`；独立 Variant/Job/Attempt/artifact，SUCCEEDED + machine QC PASS |
| 人工 proxy winner | VERIFIED | Selection `e627ad4b-3751-4a94-9676-c146a28dcc12`，winner `2b5e49e4...`；仅使用 <=720px 低码率接触表 |
| winner → formal Profile branch | VERIFIED | Variant `87f9f260...` 只改变 Profile；prompt/input/seed 260829/approval 快照保持，Job `933c891f...` SUCCEEDED |
| 正式视频与双重审核 | VERIFIED | FORMAL MediaVersion `bf6f2151...`，SHA `68cbc0b0...`；machine check `4a04f522...` PASS；ReviewDecision `c1b3cf70...` APPROVED |
| G6 exit readiness | PASS | 七项检查全部 PASS、`mutated=false`；详见 `docs/phase_reports/G6_exit_report.md` |
| G6 回归 | PASS | API 79 passed / 4 live deselected；Web 4/4；production build、Ruff、mypy PASS |
| 已知 prompt/source 语义不一致 | OPEN DATA QUALITY LIMITATION | 冻结提示的 candle/period costume 与现代室内粉衣源图不一致；审核只确认技术质量与源图连续性，不虚报语义目标实现 |

G7 已可按蓝图 09 顺序开始，但当前不是 PASS；G8/G9 仍只记 progress，最终 86/86 FR、14/14 NFR、85 TC 与发布门禁尚未完成。

## G7 进度状态（2026-08-14，未退出）

| 需求/验证范围 | 状态 | 证据 |
|---|---|---|
| G7 只读 readiness truth | VERIFIED | `application/g7_readiness.py`、`GET /projects/{id}/gates/g7`；`runtime_contacted=false`、`network_contacted=false`、`mutated=false`；公网 URL 伪装 LOOPBACK 负例硬拒绝 |
| ProductionPlan 项目显式绑定 | VERIFIED BASELINE | ProductionPlanVersion `5a0a2a07-8703-4642-9df5-3bd33ad41970`，冻结 LOCAL_ONLY、9:16、24fps、proxy/formal Profile 选择 |
| Project Profile binding matrix | VERIFIED BASELINE | I2V formal `2147a504...`、T2V `988b3fc3...`、SCRIPT_BREAKDOWN_LLM `08789ef4...` 均为 Published/ACTIVE；旧 Job 快照未改写 |
| DeliveryTargetVersion 显式选择 | VERIFIED BASELINE | LOCAL_FILESYSTEM `f001acd0-096f-44f1-b466-e0f11212a090`，项目内 `06_delivery/master`；REMOTE transport 继续硬禁用 |
| G7-05/G7-06 configuration snapshot and impact analysis | VERIFIED (read-only) | `GET /projects/{id}/configuration`；ProductionPlan/Profile/DeliveryTargetVersion 矩阵与 frozen Job/DeliveryPackage 计数，`mutated=false`；`test_configuration_impact.py`、三档 Playwright |
| G7-03/G7-07 local adapter SDK and REMOTE contract guard | VERIFIED (static contract) | `GET /adapters/contracts`；Comfy/Local LLM loopback（Ollama API）、Local CLI、FFmpeg/FFprobe 四类声明；公网/REMOTE/凭据/远程 executable 拒绝；`test_adapter_contracts.py`；无 runtime/network/mutation |
| Profile editor/test/publish | VERIFIED | 0015 contract editor；真实 DRAFT→validate→publish-evidence 证据，Web 9/9，三档 Profile Editor Playwright PASS |
| Capability compatibility | VERIFIED | 0016；Published v12 `f63b3ac7...` + PASS attestation `da5d5f66...`，涵盖 input/seed/extend/V2V/reference/motion |
| Zero-public-network full-chain | VERIFIED | 0017；真实 loopback Comfy/LLM/diagnostics harness，socket 层拒绝 `203.0.113.1`；attestation `51b05922-fce9-496e-bb9e-a627ba346f77` |
| Workspace asset authorization / BrandKit | VERIFIED | 0018；KEYFRAME 重新 hash/size 后授权 `853fd791-0bc8-4cf5-bf12-fc54c3542caa`，BrandKit ACTIVE v1 `5a0d3a95...` |
| Offline model license/hash/quantization report | IN_PROGRESS / BLOCKED_BY_LICENSE_EVIDENCE | 0019 + 0020；H3 video VAE SHA-256 `5a624684...ceb148`、5,207,806,104 bytes、560 tensors/F16；项目内 license evidence 导入与 SHA/symlink/path 校验已实现，但磁盘仍无真实许可证记录，报告 `d586967e...` 保持 BLOCKED |
| G7 regression | PASS (current scope) | 安全 API 108 passed / 4 live deselected；Web 9/9、production build、Ruff、mypy 83 source files；三档 Playwright 全绿且视觉只读 720px WebP |

G7 当前 `IN_PROGRESS`，首阻塞 `MODEL_LICENSE_HASH_QUANTIZATION_REPORT`，禁止进入 G8 退出验收或宣告 G7 PASS。

G10 发布准备已有只读 `scripts/release_audit.py`：当前数据库与最近五份迁移前备份 `integrity=ok`、migration head=`0020_g7_model_license_evidence`；G7/G8/G9 顺序门禁仍 `IN_PROGRESS`，安装升级回滚、最终 SBOM 和 go/no-go 工件均保持 DRAFT/NO-GO，不能宣告发布完成。

## 更新规则

任何新增/变更需求必须先分配 ID、写 ADR、补 migration/API/UI/test 影响；所有阶段报告、提交和缺陷引用至少一个需求或测试 ID。
