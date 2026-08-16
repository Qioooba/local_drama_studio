# 需求追踪状态（G0 初始化）

权威详细映射仍为 `LocalDramaStudio_Blueprint_v2/13_需求追踪矩阵.md`；本文件记录本实现仓库的状态协议。每个阶段必须把状态从 `NOT_STARTED` 推进为 `IMPLEMENTED`、`VERIFIED`、`ACCEPTED`，不得以代码存在替代验证或 UAT。

## 基线计数

| 类别 | 蓝图基线 | G0 状态 | 规则 |
|---|---:|---|---|
| 功能需求 | 86/86 | IN_PROGRESS（映射层 86/86 / P0-P1 84/84 / TC 85 均 PASS） | 以 01 的需求表为准；P0/P1 最终必须逐项闭环 |
| 非功能需求 | 15/15（14 P0 + 1 P1） | IN_PROGRESS（映射层 15/15 PASS） | 以 01 的非功能表和 13 的验证映射为准；旧“14/14”计数遗漏 P1 可访问性 |
| 主干测试 | 85 | IN_PROGRESS（映射层 85/85 PASS） | 以 10 的 TC ID 为准；不得用 mock/static page 冒充 |
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
| 86 FR（正式版阻塞 84 个 P0/P1）、15 NFR、85 TC、完整本地 UAT、发布 | IN_PROGRESS（G10） | `docs/evidence/g10/master-requirements-closure.json` 与 `docs/evidence/g10/release-readiness-live-check-2026-08-16-final.json`、`docs/evidence/g10/release-readiness-live-check-2026-08-17.json` 均为 PASS；`scripts/master_requirements_audit.py` 与 `scripts/release_audit.py` 2026-08-16 13:59:29Z（本地复核）结果保持一致。映射层计数已达 84/15/85 且 `full_chain_local_uat=PASS`。剩余为 H3/Comfy runtime 外部 BLOCKED 与正式三视口签字边界。 |

## G3 验证状态

| 需求/验证范围 | 状态 | 证据 |
|---|---|---|
| FR-PRV-001/003 本地 Runtime、模型 artifact、manifest-backed Profile 候选 | VERIFIED | `application/profiles.py`、`test_g3_configuration_media.py`、G3 screenshots |
| FR-WRT-001 故事圣经与创作资料版本化 | VERIFIED PRODUCTION READ-ONLY UAT | migration `0026_creative_entry_revisions`、`CreativeEntryService` 与 `CreativeLibrary`；七类资料每次保存新增不可变 revision，字段级比较只读，回退从历史内容派生新 revision 并保留 restored_from，历史无删除 API。API 3 项、Web 2 项、0025→0026 升级/精确恢复及三视口生产只读 UAT 通过，证据 `docs/evidence/g10/creative-library-uat-2026-08-15.json` |
| FR-PRJ-007 项目健康/路径与本地资源诊断基础 | VERIFIED | `application/diagnostics.py`、`/diagnostics/runs` |
| FR-PRJ-001 从版本化模板创建项目 | VERIFIED PRODUCTION UAT | `ProjectService.create_project/plan_project_creation` 使用 partial 目录原子发布，模拟失败无目录/DB 半成品；`0023_project_creation_spec` 持久化分辨率/语言/字幕，多季×每季集数生成 DB 与目录。请求可 inline 创建 ProductionPlan、按 capability 绑定 Published Profile、创建项目内 LOCAL_FILESYSTEM DeliveryTarget，全部预检后在项目事务内绑定；完整路线零 blocker，稍后配置路线保留三项真实 blocker。六步 UI 所有字段无默认值，先只读存储预检再配置/最终预检；API 真实配置创建与三视口生产 plan-only UAT 通过，证据 `docs/evidence/g10/project-create-wizard-uat-2026-08-15.json` |
| FR-PRJ-002 v2 标准项目包 | VERIFIED PRODUCTION UAT | `ProjectPackageService` 与 export/stage/dry-run/commit API/UI：结构/媒体状态、payload、manifest、逐文件 SHA/size、源前后复验、内容寻址 staged token；流式校验 zip-slip/duplicate/symlink/schema/hash/size/entry/展开/压缩比/磁盘/identity。副本导入重写 Project/Season/Episode/Shot/MediaAsset/MediaVersion 及 owner/parent identity，rebind 只恢复 identity 匹配的缺失目录且拒绝覆盖；filesystem/SQLite 双回滚。`0022_project_package_import_receipts` 提供幂等结果、原 ID 重试与受双重归属证明约束的陈旧 PREPARING 恢复。媒体文件再次复验后注册 VERIFIED，selection/review/job/artifact/cache 明确排除；隔离 UAT 真实完成 small WebP 缩略图重建。10 个服务/API 测试与三视口生产只读 UAT 通过，证据 `docs/evidence/g10/project-package-uat-2026-08-15.json` |
| FR-PRJ-003 项目列表搜索、状态筛选与安全归档 | VERIFIED PRODUCTION UAT | `ProjectService.list_projects` 支持标题/code 子串和 DRAFT/ACTIVE/PAUSED/ARCHIVED 筛选，转义 SQL wildcard 并拒绝非法状态；UI 真实转发筛选。既有归档为审计状态转换、不删目录、活动 Job 硬阻塞。API 2 项、Web 1 项与 1024×768 只读生产 UAT 通过，见 `docs/evidence/g10/project-list-filter-uat-2026-08-15.json` |
| FR-PRJ-004 季/集/场/镜头 CRUD、稳定编号与重排 | PARTIAL / WINDOWS LOCAL UAT (PASS mapping, FORMAL UAT pending) | `ProjectService` 已持久化 Season/Episode/Scene/Shot UUID、稳定 code 与不可变 ShotRevision；Episode 重排现在归一化同季 display_order，并接受 `expected_revision` 乐观并发保护；Storyboard batch 重排只改 shot order_key，不改 shot UUID/current_revision_id；scene 通过 episode_scene_ranges 引用而不复制实体。新增真实隔离 Windows x64 SQLite + loopback FastAPI UAT（2 季/6 集/2 场/3 镜头），验证读路径、Episode/Shot 重排、stale 409 与并发重排闭环；证据 `docs/evidence/g10/fr-prj-004-windows-uat-2026-08-16.json`、`docs/evidence/g10/fr-prj-004-windows-uat-2026-08-17-concurrency-pass.json`，`FR-PRJ-004` 在 map 中为 PASS，但本轮仍未覆盖子实体删除与生产规模三视口签字。 |
| FR-PRJ-006 项目复制为新剧模板 | VERIFIED PRODUCTION READ-ONLY UI UAT | `ProjectService.copy_as_template` 与 `POST /projects/{id}:copy-template`；新 UUID/DRAFT，只复制结构、解冻的当前镜头字段、ProductionPlan、本地交付目标与 Published ACTIVE Profile。媒体/授权资产/BrandKit/Job/审核/交付/审计历史明确排除；文件树+数据库失败双回滚、重码/孤立目录不覆盖。API 3 项、Web 1 项与三视口只读表单 UAT 通过，见 `docs/evidence/g10/project-template-copy-uat-2026-08-15.json` |
| FR-WRT-002 Master Scene 与分集 source range 映射 | VERIFIED PRODUCTION READ-ONLY UI UAT | migration `0025_episode_scene_ranges`、`ProjectService.create_scene/bind_episode_scene_range` 与项目页管理面板；项目级 Scene 可跨集复用且不复制实体，集内 scene/ordinal 唯一，显式起止位置及同项目归属由服务端校验。API 3 项、Web 2 项、`0024→0025` 升级/精确恢复和三视口生产只读 UAT 通过，证据 `docs/evidence/g10/episode-scene-ranges-uat-2026-08-15.json` |
| FR-WRT-003 导演分镜字段完整性 | VERIFIED PRODUCTION READ-ONLY UAT | `missing_shot_fields`/`validate_shot_ready`、生产 read model 与 `DirectorShotEditor`；九项字段可编辑，保存创建不可变 ShotRevision，可显式冻结，READY 只能从字段完整的 DIRECTED revision 进入且返回具体缺项。API 2 项、Web 2 项与三视口生产只读 UAT 通过，证据 `docs/evidence/g10/director-shot-editor-uat-2026-08-15.json` |
| FR-WRT-004 关键词和提示词模板 | VERIFIED PRODUCTION READ-ONLY UAT | `PromptService` 对 `GENERATION_TEMPLATE` 强制冻结 source_fields/template/expanded/negative/language/model profile，展开结果与 content_text 必须一致；创建/branch 均为 immutable FROZEN hash，owner 列表只读返回最新 revision 且历史保留。Web 2 项、API 2 项及三视口 UAT 通过，证据 `docs/evidence/g10/prompt-template-panel-uat-2026-08-15.json` |
| FR-WRT-005 生产就绪判断 | VERIFIED PRODUCTION READ-ONLY UAT | 生产 read model 明确投影 `OUTLINE/DIRECTED/PRODUCTION_READY`，并逐项返回导演字段与 Profile/ProductionPlan/DeliveryTarget/镜头状态 blocker；UI 显示服务端状态且只有完整 DIRECTED revision 可进入 READY。三视口真实 `SHOT_001 · PRODUCTION_READY` UAT 通过，证据 `docs/evidence/g10/director-shot-editor-uat-2026-08-15.json` |
| TC-DOM-007 上游 revision stale 传播 | PARTIAL / WINDOWS LOCAL UAT (PASS mapping, FORMAL UAT pending) | 真实本地 MP4 经 FORMAL QC、人工 APPROVED 与 FORMAL_SELECTION 后，新的 immutable ShotRevision 将 ReviewDecision 标记为 `shot_revision_changed` stale；原选择记录保留为不可变历史，新的 formal-selection preflight 通过 loopback API 以 `LATEST_HUMAN_APPROVAL_REQUIRED` 阻断。新增真实 loopback API 负向用例覆盖 `VARIANT_PARENT_STALE` / `FRAME_ANCHOR_STALE_INPUT` / `FRAME_ANCHOR_STALE`，并证明连续性 Anchor/Transition/Variant 已全对象置为 stale；隔离 SQLite/Windows x64 证据 `docs/evidence/g10/tc-dom-007-stale-windows-uat-2026-08-16.json`；保持 PARTIAL，因为正式签字、三视口与生产规模闭环尚在推进。 |
| FR-WRT-006 连续性面板 | VERIFIED PRODUCTION READ-ONLY UAT | `ProductionReadModelService.continuity_context` 与生成工作台三列对照；上一/当前/下一镜的 revision、人物外观、服装、道具、光线、空间方向、连续性、已选/已批 MediaVersion 和边界约束均来自真实本地数据，缺项不推断。API 2 项、Web 2 项与三视口 UAT 通过；只请求 small 缩略图，证据 `docs/evidence/g10/continuity-panel-uat-2026-08-15.json` |
| FR-WRT-007 AI 辅助提取先进入草稿且不覆盖人工内容 | VERIFIED PRODUCTION READ-ONLY UAT | 既有真实 `LocalLLMService.breakdown` 只保存 `DRAFT_READY`；新增项目级只读查询与 `AIDraftReviewPanel`，明确投影 `NOT_APPLIED`、`automatic_apply=false`、`requires_human_action=true`，无应用按钮。API 2 项证明查询不改变 Scene/Shot/CreativeEntry，Web 2 项覆盖真实草稿和无 mock 空态；正式项目三视口展示 1 份真实本地 LLM 草稿且零写入，证据 `docs/evidence/g10/ai-draft-review-uat-2026-08-15.json` |
| FR-IMG-001 媒体版本注册、probe、hash、缩略图缓存 | VERIFIED | `MediaService.import_file/verify_content_integrity/thumbnail`；不可变 MediaAsset/MediaVersion、源文件 hash/size 与篡改阻断，`apps/api/tests/test_keyframe_candidate.py` |
| FR-IMG-002 图片候选批量生成 | PARTIAL / REAL-RUNTIME UAT PENDING | `GenerationWorkbench` 显式 1—8 take 提交为独立 Variant/Job，seed batch 1—24 只读规划；服务端 preflight 返回仅来自 Published Profile `resource_policy` 的 bounded per-take 时长/显存/磁盘估算，未声明字段保持 unknown，UI 在确认前显示并仍受 preflight/镜头/Profile/输入 blocker 约束；真实用户 Profile/Runtime 批量生成仍待 UAT，证据 `docs/evidence/g10/fr-img-001-006-image-candidate-review-2026-08-16.json`、`docs/evidence/g10/fr-img-002-resource-estimate-2026-08-16.json` |
| FR-IMG-003 图片网格与比较 | PARTIAL / UAT PENDING (PASS mapping, FORMAL READONLY UAT pending) | `ImageCandidateGrid` 与 `ReviewInboxPanel` 均只使用 320px small 派生缩略图；网格新增 roving tabindex、ArrowLeft/ArrowRight/Home/End 键切换，详情保留 A/B、参考图置顶与 metadata；GET/HEAD 原图接口均硬拒绝，且 `image/*` MIME 在误标 media_kind 时仍回退到缩略图门禁，真实用户图片网格三视口 UAT 待补，见 `fr-img-001-006-image-candidate-review-2026-08-16.json` |
| FR-IMG-004 图片结构化审核清单 | VERIFIED AUTOMATED / UAT PENDING | `ReviewService` 的 `image_asset` 模板包含身份、服装、人体/手、场景、构图、光线、连续性、可视频化 8 项必填检查；`apps/api/tests/test_g4_reviews.py` |
| FR-IMG-005 图片批准与拒绝 | VERIFIED AUTOMATED / UAT PENDING | required fail 阻断批准，拒绝必须原因，reviewer/time/template 规则及 stale 保留；selection 与 approval 分离，见 `application/reviews.py`、`test_g4_reviews.py` |
| FR-IMG-006 关键帧选择与派生 | VERIFIED AUTOMATED / UAT PENDING | `create_keyframe_candidate` 生成 SHOT-owned immutable KEYFRAME 并冻结 `parent_version_id`；`select_version` 与人工 approval 分离，approval impact 事务传播 stale；`test_keyframe_candidate.py`、`test_generation_variants.py` |
| FR-ING-001 source document version、ImportSession、TXT/MD/DOCX preview | VERIFIED | `application/documents.py`、G3 import test |
| FR-SRC-001 FTS5 global search minimum | VERIFIED | `application/read_models.py`、G3 import/search test |
| G3 production read model / no N+1 page query | VERIFIED | `ProductionReadModelService`、browser production screenshot |
| FR-ING-002 local LLM breakdown | VERIFIED REAL LOCAL UAT | 显式 Published Profile 才可执行；强制结构化 scenes/置信度/问题/来源段落，quote 必须逐字存在且每场覆盖，写库前失败；真实 `deepseek-r1:14b` load test 与正式 DRAFT 通过，未创建正式 Scene/Shot，证据 `docs/evidence/g7/local-llm-structured-breakdown-2026-08-15.json` |
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
| FR-VID-008 视频时间码标记、截图与返工关联 | VERIFIED PRODUCTION READ-ONLY UAT | `0024_video_review_annotations`、`ReviewService.create_video_annotation` 与审核页时间码面板；时间码受真实 duration 约束，截图只接受同项目当前视频派生 IMAGE，返工 Job 只接受同项目引用，写入独立审计。API 2 项、Web 2 项及三档生产只读 UAT 通过，证据 `docs/evidence/g10/video-annotations-uat-2026-08-15.json` |
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
| FR-AUD-001 对白/TTS 候选、试听与选择 | PARTIAL / BLOCKED_NO_PUBLISHED_TTS_PROFILE (PASS mapping, BLOCKER hold) | migration `0027_tts_candidate_governance` 与 `DialogueService` 已实现不可变对白文本/发音 revision、项目内音色授权证据 SHA、导入试听候选、情绪/语速/seed/model/Profile/媒体 hash 与 duration provenance、显式选择与文本 stale 阻塞；PREVIEW 强制已探测 3—10 秒。新增真实 Windows SAPI `TTS_GENERATION` CPU Job：只接受最新文本、同项目 ACTIVE 音色、Published TTS Profile 与 `sapi:` 引用，冻结 LOCAL_ONLY 输入快照；worker 运行前复核快照/hash/profile，输出真实 PCM WAV 并由 FFprobe 验证；成功 Job 可幂等晋升为 VERIFIED AUDIO 与 FORMAL candidate。路由、Idempotency-Key、拒绝路径、生成客户端及显式提交/结束登记 UI 已覆盖。正式候选选择仍必须通过最新音频机器 QC 与人工 APPROVED。正式库真实为 0 对白、0 音色、0 候选、0 Published TTS Profile，三档只读 UI 不写入、不创建 Mock。证据 `docs/evidence/g10/dialogue-tts-governance-uat-2026-08-15.json`、`docs/evidence/g10/dialogue-tts-windows-uat-2026-08-16.json`；生产真实授权/Profile/Job/试听/QC/审核/选择尚未闭环，禁止标 VERIFIED |
| FR-AUD-002 音效/环境/音乐媒体管理 | PARTIAL / LICENSE_EVIDENCE_REQUIRED (PASS mapping, AUTHORITY blocker) | migration `0028_audio_binding_authority` 后新绑定强制同项目 VERIFIED AUDIO、轨道枚举、项目内授权文件 SHA/size、绑定范围、loop 与 fade 契约；未 loop 不得超过源时长。历史 4 轨的授权状态字符串不再计作证据，均投影 `LEGACY_INCOMPLETE`，正式 G8 音频检查已从假绿退回 FAIL（4 类轨道/0 条真实授权证据）。工作台显示四轨范围/gain/loop/fade/授权与 `preload=none` 本地播放器，并提供真实本地 AUDIO 导入、项目内证据校验、显式轨道/授权/范围/混音参数绑定表单；失败的导入版本不会冒充授权绑定。三档只读 UAT 展开表单后 3/3 PASS，零写入、公网、自动原音频请求、短控件和溢出，见 `docs/evidence/g10/audio-license-authority-uat-2026-08-15.json`。真实授权补录和生成链路尚未闭环，禁止标 VERIFIED |
| FR-AUD-003 波形、响度与削波检查 | VERIFIED PRODUCTION READ-ONLY UAT | `MediaService.audio_qc_metrics` 用本机 FFmpeg ebur128/astats 真实解析 LUFS/true peak/peak/clipping，写入不可变 machine check；audio_mix 模板及 `ReviewService.submit_review` 强制 AUDIO 最新 QC PASS，人工清单不可绕过。正式 4 轨为 2 PASS/2 低响度 FAIL；UI 仅加载 640×128 派生波形，三视口通过。证据 `docs/evidence/g8/audio-qc-production-2026-08-15.json`、`docs/evidence/g10/audio-qc-review-uat-2026-08-15.json` |
| FR-AUD-004 剧本权威字幕与 ASR 仅对齐 | VERIFIED PRODUCTION READ-ONLY UAT | 新字幕 revision 强制 `text_authority=SCRIPT` 与同项目已解析 SourceDocumentVersion，逐 cue 按剧本顺序定位并冻结 source offset/quote hash；缺来源、跨项目、文本不匹配、源文件哈希失败均在落库前拒绝。ASR 媒体与 Published ASR Profile 必须成对提供，且证据固定 `TIMING_ALIGNMENT_ONLY`/`asr_text_authority=false`。历史弱 revision 标记 `LEGACY_INCOMPLETE`。正式 v2 `fe39d0e0…` 为 `VERIFIED_SCRIPT`，SRT/VTT/ASS renderer、overlap/CPS 与负例回归通过；三视口只读 UAT 3/3 PASS。证据 `docs/evidence/g8/subtitle-authority-production-2026-08-15.json`、`docs/evidence/g10/subtitle-authority-uat-2026-08-15.json` |
| FR-SUB SRT/VTT/ASS rendering、overlap/CPS gate、immutable subtitle revision | VERIFIED | `subtitle_revisions`/`subtitle_cues` migration、G8 real API test |
| FR-CON FrameAnchor extraction、transition constraint persistence | VERIFIED | real FFmpeg frame extraction、`frame_anchors`/`shot_transition_constraints`；`0009_g6_continuity_stale`/`0010_g6_frame_anchor_stale` 测试覆盖批准影响预览、下游首帧与上游 winner 的事务化 stale 传播及实验选择隔离 |
| FR-MED/FR-CON live source integrity before generation/anchor | VERIFIED | `MediaService.verify_content_integrity`、TC-VAR-015 tamper tests；创建前 mismatch 零 Variant/Job/Anchor/派生媒体，既有 Anchor 的 source/extracted 后置篡改使 Transition `BLOCKED` 并标记 `CORRUPT` |
| FR-CON stale consumption gates | VERIFIED | Generation/Timeline service tests；stale Variant parent、stale Anchor extracted input、stale Transition anchor 均在新对象持久化前拒绝，历史保留 |
| FR-VAR plan/create integrity race | VERIFIED | API regression：合法 preflight 后篡改输入，create 实时复核并以 `SOURCE_INTEGRITY_FAILED` 零 Variant/Job/audit 阻塞 |
| FR-VAR unsupported First/Last capability action | VERIFIED | TC-VAR-007 API regression；Published Profile 缺 END_FRAME 时返回 Profile/roles/capability/suggested action，零 Variant/Job |
| FR-VAR operational retry isolation | VERIFIED | TC-VAR-003 CPU persistent queue regression；同一 Job attempt 1→2，Variant/Job/take 数不增，未使用 GPU_H3 |
| FR-ENH capability-driven technical enhancement chain | VERIFIED | persisted recipe/run、real FFmpeg output and MediaVersion registration |
| FR-DEL-001..004 local filesystem delivery candidate、manifest/hash verify、target versioning、tamper detection、history/withdraw | PARTIAL / UAT PENDING (PASS mapping, SIGN-OFF pending) | `TimelineService.build_delivery` now requires latest approved render, publishes a non-overwriting atomic local directory, and writes `delivery-manifest.v3` with source/encoding/subtitle/license/target/hash evidence. `GET/POST /delivery-packages/{id}:verify`, package/files/history/download routes preserve immutable files and withdrawn status; each successful local download appends a bounded `DOWNLOAD` audit event containing only manifest/file fingerprints and transport. Target version create/select API retires prior active versions. Automated regression: `apps/api/tests/test_g8_timeline_delivery.py` and generated client contract. `scripts/delivery_local_uat.py` additionally exercised a real Windows/F-path H3 MP4 with Unicode/space path, watermark, compliance fail→pass, human/platform approval, verify/download/withdraw and preserved manifest SHA. Formal three-viewport production UAT and final release sign-off remain pending; see `docs/evidence/g10/delivery-local-windows-uat-2026-08-16.json` and `fr-del-001-004-delivery-chain-2026-08-16.json`. |
| FR-IMG-007 分集已选媒体联系表与原文件导出 | VERIFIED PRODUCTION UAT | `ContactSheetExportService` 仅跟随 `selected_version_id` 权威指针，逐项复核源/副本 SHA 与大小，输出自包含 HTML、320px WebP 和 manifest；相同输入逐文件复验后幂等复用，篡改硬拒绝；SQLite 行数不变，runtime/network 均未接触。API 4 项、Web 2 项及三视口真实页面通过，见 `docs/evidence/g10/contact-sheet-export-uat-2026-08-15.json` |
| FR-TML-004 OTIO / EDL 专业 NLE 导出 | VERIFIED PRODUCTION UAT | 冻结 TimelineRevision 导出 OTIO `Timeline.1`/`Clip.2`、项目相对媒体 URL、媒体 ID/hash/size 与 CMX 3600 non-drop EDL；临时目录写入后原子发布，manifest 逐文件复验，源/导出篡改硬拒绝且失败不修改 revision/SQLite。API 3 项、Web 2 项及三视口生产 UAT 通过，见 `docs/evidence/g10/timeline-otio-edl-export-uat-2026-08-15.json` |
| G8 migration/OpenAPI/static/type/full API regression | VERIFIED | `0006_g8_timeline_audio_delivery`、generated OpenAPI、33 API tests, Ruff, mypy |
| G8 formal screenshots/sample/UAT and G8→G9 exit approval | IN_PROGRESS | Real production evidence now passes the read-only G8 projection: 3 SHOT-owned H3/Comfy videos, 4 authorized local audio tracks, subtitles, approved VERIFIED render, VERIFIED LOCAL_FILESYSTEM delivery, and reversible tamper probe; evidence `docs/evidence/g8/g8-production-evidence-2026-08-15.json`. Ordered phase exit remains pending while G7 is blocked and browser/UAT sign-off is not yet complete |

## G9 验证状态（代码完成，阶段门禁待正式退出）

| 需求/验证范围 | 状态 | 证据 |
|---|---|---|
| FR-WFL-003 React Flow 业务画布、node registry、edges、MiniMap | VERIFIED | `application/canvas.py`、React Flow `ProductionCanvasPanel`、web build |
| shot/episode lazy graph read model、分页与可见节点上限 | VERIFIED | 62 镜头测试按 20 镜头/100 节点分页，`test_g9_canvas.py` |
| 节点状态/take/variant/blocker/active job | VERIFIED | canvas graph API 基于 SQLite 权威状态汇总 |
| layout persistence 与业务依赖隔离 | VERIFIED | `canvas_layouts`、未知 node 拒绝、layout 后 edges 不变测试 |
| run node/from/to/range preflight | VERIFIED | `canvas_execution_plans`、max node/GPU concurrency/HITL/blocker 计划 |
| 画布性能基础、键盘/不可连接/不可删除替代约束 | VERIFIED PRODUCTION UAT | 生产 EPISODE_001 持久化 22 个真实 SHOT、110 节点；三档浏览器 canvas-ready 1.0—1.3s、零水平溢出、零 console/page error、零失败响应；`g9-production-scale-uat-2026-08-15.json`、`g9-production-canvas-uat-2026-08-15.json` |
| 三视图 route/selection 同步、画布搜索与上/下游聚焦 | VERIFIED PRODUCTION UAT | 三档真实生产页面验证语义画布、键盘节点列表、搜索过滤、URL shot 同步与选中态；`g9-production-accessibility-uat-2026-08-15.json`。只改变可视节点集合，不改变业务 edges |
| automation/webhook/产能看板 | IMPLEMENTED / UAT PENDING | 旧 `POST /events:deliver` 继续兼容；新增 `POST /automation-clients`、`POST/GET /webhook-subscriptions`、`POST/GET /webhook-deliveries` 和 `:retry`。client token 只显示一次并存 hash，scope 为 read/plan/submit/review/delivery；回调仅 literal loopback、每次请求重新校验、禁用代理与重定向、SQLite claim 防并发重复投递，HMAC 签名、指数退避（最多 5 次）、死信和审计；UI `AutomationPanel` 使用同一 command。真实隔离服务回归为 `apps/api/tests/test_automation_webhooks.py`，实现加固证据为 `docs/evidence/g10/fr-aut-002-loopback-hardening-2026-08-16.json`；正式三视口 UAT 与总账 PASS 证据仍待完成。产能仍标记 `OBSERVED_NOT_BENCHMARKED`。|
| G9-09 本机队列产能观测 | VERIFIED BASELINE | `GET /capacity/snapshot`；真实 SQLite Job/Attempt 状态、GPU 并发和近 24h 完成数；`OBSERVED_NOT_BENCHMARKED`、`would_create_jobs=false`、无 runtime/network/mutation；`test_capacity_snapshot.py` |
| G9-08 变体谱系、实验进度、相邻边界约束只读可视化 | VERIFIED BASELINE | Canvas graph read model 汇总真实 generation_variants/generation_experiments/experiment_cells/shot_transition_constraints；选中节点显示摘要，空数据不造数；`test_g9_canvas.py`、G9 validation evidence |
| G9 readiness / ordered exit | EVIDENCE PASS / ORDERED BLOCKED | readiness 五项全部 PASS；发布审计强制 G7→G8→G9 链式顺序，因 G7 许可证证据阻塞而保持 `ORDERED_G9=false`，禁止越级宣告 |

## G6 历史进度状态（已由 2026-08-14 退出证据取代）

| 需求/验证范围 | 状态 | 证据 |
|---|---|---|
| FR-WFL workflow package、semantic slots、local node validation、publish/rollback | VERIFIED | `application/workflows.py`、`0005_g6_comfy_workflows`、`test_g6_workflows.py` |
| FR-WFL Comfy loopback client、history/output collect、artifact hash/register | VERIFIED | `infrastructure/comfy.py`、`application/comfy_jobs.py`、真实 prompt evidence |
| FR-ING-002 real local LLM adapter/profile/load gate | VERIFIED REAL LOCAL UAT | `deepseek-r1:14b` Published Profile 真实 load test PASS；正式 ImportSession 生成 evidence v1 `DRAFT_READY`，固化 Profile/model/confidence/questions/source ranges，所有 quote 逐字匹配原文；Scene/Shot 权威表未改变。`qwen3:8b` 仍只是未发布候选，不影响显式选定 Profile 的验收 |
| FR-PRV H3 candidate capability truthfulness | PARTIAL / BLOCKED | `/api/v1/h3/candidate-runtime`；真实 FL2VA sidecar layout缺失，Comfy execution_error |
| FR-VAR CameraPlan/MotionMask/TimedDirection/PerformanceBinding contracts | VERIFIED | `domain/generation_contracts.py`、G6 tests |
| TC-VAR-013 Profile A/B branch isolation | PARTIAL / FORMAL JOB+take audit pending | `PROFILE_BRANCH` derive-plan 仅改变 Published ProfileVersion，Candidate/混合 scope/零持久化已验证；最新 WINDOWS LOCAL UAT 脚本补齐 PROFILE_BRANCH 独立 Variant 与持久 Job，见 `docs/evidence/g10/tc-var-013-004-windows-uat.json`，但真实 artifact 注册/隔离 review 与 take 签字未完成 |
| TC-VAR-004 Provider random resubmit | PARTIAL / FORMAL JOB+take audit pending | `RESUBMIT_PROVIDER_RANDOM`、Profile seed support、唯一冻结 nonce、NON_REPRODUCIBLE 声明、plan/create 门禁已验证；最新 WINDOWS LOCAL UAT 脚本补齐两次独立 Provider-random submit 的独立 Job 持久化，见 `docs/evidence/g10/tc-var-013-004-windows-uat.json`，但真实 new Job/take/签字待确认 |
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

G7 已可按蓝图 09 顺序开始，且 G8/G9 Readiness 已按顺序 PASS；最终 86/86 FR、15/15 NFR、85 TC 与发布门禁仍由最终签字与 H3/Comfy runtime 真实阻塞约束接续推进。

## G7 进度状态（2026-08-14，未退出）

| 需求/验证范围 | 状态 | 证据 |
|---|---|---|
| G7 只读 readiness truth | VERIFIED | `application/g7_readiness.py`、`GET /projects/{id}/gates/g7`；`runtime_contacted=false`、`network_contacted=false`、`mutated=false`；公网 URL 伪装 LOOPBACK 负例硬拒绝 |
| ProductionPlan 项目显式绑定 | VERIFIED BASELINE | ProductionPlanVersion `5a0a2a07-8703-4642-9df5-3bd33ad41970`，冻结 LOCAL_ONLY、9:16、24fps、proxy/formal Profile 选择 |
| Project Profile binding matrix | VERIFIED BASELINE | I2V formal `2147a504...`、T2V `988b3fc3...`、SCRIPT_BREAKDOWN_LLM `08789ef4...` 均为 Published/ACTIVE；旧 Job 快照未改写 |
| DeliveryTargetVersion 显式选择 | PARTIAL / UAT PENDING | Existing LOCAL_FILESYSTEM target remains explicit and remote transport is hard-disabled; new immutable version creation/selection routes now retire the prior active version and package manifests freeze the selected target. Production multi-version selection UAT remains pending. |
| G7-05/G7-06 configuration snapshot and impact analysis | VERIFIED (read-only) | `GET /projects/{id}/configuration`；ProductionPlan/Profile/DeliveryTargetVersion 矩阵与 frozen Job/DeliveryPackage 计数，`mutated=false`；`test_configuration_impact.py`、三档 Playwright |
| G7-03/G7-07 local adapter SDK and REMOTE contract guard | VERIFIED (static contract) | `GET /adapters/contracts`；Comfy/Local LLM loopback（Ollama API）、Local CLI、FFmpeg/FFprobe 四类声明；公网/REMOTE/凭据/远程 executable 拒绝；`test_adapter_contracts.py`；无 runtime/network/mutation |
| Profile editor/test/publish | VERIFIED | 0015 contract editor；真实 DRAFT→validate→publish-evidence 证据，Web 9/9，三档 Profile Editor Playwright PASS |
| Capability compatibility | VERIFIED | 0016；Published v12 `f63b3ac7...` + PASS attestation `da5d5f66...`，涵盖 input/seed/extend/V2V/reference/motion |
| Zero-public-network full-chain | VERIFIED | 0017；真实 loopback Comfy/LLM/diagnostics harness，socket 层拒绝 `203.0.113.1`；attestation `51b05922-fce9-496e-bb9e-a627ba346f77` |
| Workspace asset authorization / BrandKit | VERIFIED | 0018；KEYFRAME 重新 hash/size 后授权 `853fd791-0bc8-4cf5-bf12-fc54c3542caa`，BrandKit ACTIVE v1 `5a0d3a95...` |
| User-supplied local model path/hash/quantization report | VERIFIED | 0019 + 0020 + 0029；平台只引用用户选择的电脑绝对路径，不复制、上传或随安装包分发权重。页面提供 Windows 原生文件选择器、手工路径回退、hash/格式/量化兼容检查；路径缺失、symlink 或 header 无法验证仍硬拒绝。许可证记录改为用户可选的风险追溯字段，缺失时显示 `USER_RESPONSIBILITY_UNKNOWN`，不阻塞平台发布。三档只读 UI 证据 `docs/evidence/g7/model-license-import-ui-2026-08-15.json` |
| G7 regression | PASS (current scope) | 安全 API 108 passed / 4 live deselected；Web 9/9、production build、Ruff、mypy 83 source files；三档 Playwright 全绿且视觉只读 720px WebP |

G7 当前已按“用户自带本机模型、平台只引用管理、不捆绑权重”的正式范围 PASS；G8、G9 亦已按顺序 PASS。

历史（2026-08-14）：G10 局部门禁证据曾为 PASS，但总设计复核后总体发布状态一度为 `IN_PROGRESS / NO-GO`；当前数据库与最近五份迁移前备份 `integrity=ok`，代码 migration head=`0038_outbox_delivery_ledger`；`0031→0038` 隔离升级与精确恢复演练已通过。规模、安全、干净新根恢复、本地只读 UAT、G7→G8→G9 有序退出、SBOM 和运行手册仍是有效局部证据，但不能替代 84 个 P0/P1 FR、15 个 NFR 与 85 个命名 TC 的总账闭环。正式范围保持 Windows x64 LOCAL_ONLY 本地源码发行版，不捆绑用户模型或媒体。此段为历史快照，当前请以本页 G10 最新审计与 `release-readiness-live-check-2026-08-16-final.json` 为准。

2026-08-15 用户自带模型策略闭环：新增本机模型引用 API 与页面原生文件选择器，返回绝对路径且 `copied=false/uploaded=false`；兼容报告将用户许可证缺失降级为可见风险，不改变 hash、量化、路径和 symlink 硬校验。生产数据库在线备份后迁移至 0029，G7/G8/G9 依次 PASS；完整门禁 API 178 passed / 4 live deselected、Web 60/60（以最终实际回归输出为准更新），三档模型路径 UI 3/3 PASS。G10 发布审计 PASS，GO 范围不包含模型权重、音色、媒体或 REMOTE Provider。

2026-08-15 总设计复核纠正：总体 GO 已撤回为 `IN_PROGRESS`，发布审计新增 `MASTER_REQUIREMENTS_CLOSURE` 硬门禁。蓝图真实清单为 86 FR（63 P0、21 P1、2 P2）、15 NFR（14 P0、1 P1）和 85 TC；旧“14/14 NFR”遗漏 P1 可访问性。局部 G7-G10 PASS 不再能绕过总需求闭环。

FR-CTL-001 结构化运镜批次：ShotRevision 的 CameraPlan 现保存景别、运动、方向、强度、曲线、显式 Prompt 降级文本和 ProfileVersion；服务端只按一个已发布 Profile 的 capability contract 裁决 `NATIVE` / `PROMPT_FALLBACK` / `UNSUPPORTED`，缺声明即不支持，不接触 Runtime 或网络。遗留自由文本和不支持的计划不能标记 Production Ready。API 180 passed / 4 deselected、Web 62/62、build/Ruff/mypy 101 files PASS。自动证据见 `docs/evidence/g10/structured-camera-plan-2026-08-15.json`、`docs/evidence/g10/fr-ctl-001-camera-windows-uat-2026-08-16.json`；正式 Variant 快照绑定和真实浏览器 UAT 仍待下一批闭环，因此总账不宣告 FR-CTL-001 最终 VERIFIED。

FR-CTL-001 提交链增量：生成工作台现按 `Intent + frozen PromptRevision → read-only Variant plan → 二次显式确认 → Variant + Job 原子提交` 执行；CameraPlan 由服务端用同一 ProfileVersion 重新裁决并冻结到 Job `semantic_inputs`，不接受客户端伪造或过期映射。已批准关键帧同时来自审核候选与权威 G6 I2V 探针，避免已处理批准项从收件箱消失后无法选择。API 181 passed / 4 deselected、Web 64/64、build/Ruff/mypy PASS。1280px 真实页面无横向溢出、无短于 40px 控件和 console error；当前生产 Profile 未显式声明 camera capability，页面按设计显示 `UNSUPPORTED` 并禁用预检。待配置一个显式 camera contract 的用户本地 Published Profile 后再完成成功路径 UAT，故仍不提前标最终 VERIFIED。

FR-PST-001 / TC-CAP-009 已闭环：PostProcessRecipe 采用逻辑 key + 不可变版本 + 显式 DRAFT 发布；运行必须先生成只读 plan hash，再由用户二次确认。真实本地执行按 `SCALE(FFV1 中间件) → TECHNICAL_QC(FFprobe) → ENCODE(H264)` 分步记录 executor、配置 profile、输入/输出 SHA-256 与结果，只有 QC 通过才注册带 `parent_version_id` 的新 ENHANCED MediaVersion，输入永不覆盖。1280×720 真实页面已完成创建、发布、预检、执行及双视频旁路比较，QC=true、无横向溢出或可见错误。证据见 `docs/evidence/g10/fr-pst-001-uat-2026-08-15.json`。

总账现由 `scripts/master_requirements_audit.py` 与 `docs/evidence/g10/master-requirements-map.json` 逐项校验，不能再靠手填计数放行；PASS 项必须引用现存的 PASS JSON 证据和自动化测试文件，未知 ID、重复 ID、缺证据或缺测试路径都会使 mapping 失效。该段为 2026-08-15 的历史快照；当前自动审计已为 84/84/85，映射一致通过。

FR-ING-001 / TC-CAP-001 已闭环（2026-08-15 历史快照）：用户可从页面调用 Windows 原生选择器或填写绝对路径导入本机 TXT、Markdown、DOCX；平台先注册不可变源文档、解析并生成带 hash 的预览，只有用户显式提交且预览 hash 未变化时才进入 COMMITTED。重复导入/提交幂等复用，源文件与已提取文本均不覆盖，symlink、不支持扩展名、过期预览及 hash 篡改硬拒绝。正式项目 1280×720 页面完成真实预览和提交，唯一提交审计事件及源/文本 SHA-256 已固化于 `docs/evidence/g10/fr-ing-001-uat-2026-08-15.json`。此段为历史快照。

FR-ING-003 / TC-CAP-003 已闭环（2026-08-15 历史快照）：分镜批量台将镜头 identity、order_key 与不可变 revision 分离，提供表格/故事板/时间线三视图；重排、复制和批量字段编辑先生成带来源快照的 plan hash，逐项列出编号、归属和 revision 冲突，必须显式确认后原子提交。正式项目 22 个镜头在 1280×720 页面通过三视图与只读校验，证据见 `docs/evidence/g10/fr-ing-003-uat-2026-08-15.json`。所有页面图片读取统一使用派生 thumbnail/waveform；图片原图 content 接口硬拒绝并返回 `IMAGE_CONTENT_REQUIRES_THUMBNAIL`，源文件不覆盖。此段为历史快照。

G10 安全 UAT 已补齐此前缺失的 instance CSRF token：每个 API 进程生成独立 token，同源客户端从无 CORS 的 bootstrap/安全 GET 获取，所有网络写请求同时验证受控 Origin 与 `X-Local-Instance-Token`。隔离真实 FastAPI 验证恶意 Origin、缺失/错误 token、路径逃逸、REMOTE Provider、未入清单自定义节点均被拒绝；socket guard 对 TEST-NET 公网目标在 connect 前阻断，OpenAPI 无远程 credential 字段。证据为 `docs/evidence/g10/security-uat-2026-08-15.json`；不替代 G7 模型许可证或最终发布签字。

G10 干净新根恢复 UAT 使用 100 个真实 FFprobe PASS 的本地 WAV：online backup 后恢复数据库与完整项目树，100/100 MediaVersion SHA-256、数据库 integrity、健康/项目/审核入口全部通过；实测 RTO 0.627 秒、捕获备份后 RPO=0。该证据来自隔离环境且未接触生产库、runtime 或网络，见 `docs/evidence/g10/recovery-restore-uat-2026-08-15.json`。

G10 UI 可访问性复审已把设计系统的 12px 可见文字下限与 40px 可用控件下限落到真实计算样式；1440×900 生成、1280×800 审核、1024×768 画布均无水平溢出、console/page error、失败响应或原片请求。生成客户端现保留结构化错误 code/status/retry guidance/request ID，工作区活动查询提供区域级错误与显式重试。证据见 `docs/evidence/g10/typography-accessibility-uat-2026-08-15.json`；Web 18/18。此项不改变 G7 的许可证证据阻塞，也不宣告最终 G10 发布退出。

NFR-A11Y-001 增量（PARTIAL）：审核收件箱为候选列表提供选中态 `aria-current`、稳定可访问名称及 ArrowLeft/ArrowRight 键盘切换，并在切换后将焦点还原到新候选；生成工作台为方式卡提供 `aria-pressed` 与分组名称；项目创建向导实现 `role=dialog`/`aria-modal`、Escape 关闭、Tab 焦点环和关闭后焦点返回触发按钮；项目列表提供标记的选中项目。定向 Web 测试 11 项通过，证据见 `docs/evidence/g10/nfr-a11y-001-keyboard-focus-2026-08-16.json`。该证据不声称 axe、对比度测量、Windows 三视口或完整手动 UAT 已完成，故 NFR 仍保持 PARTIAL。

审核与任务长列表现采用首屏 50 行的渐进窗口，深链接选择在窗口外时会扩展到该项，用户可显式每次再显示 50 行；离屏行启用 `content-visibility:auto`。纯函数窗口边界已覆盖 120 行/深链场景，Web 回归升至 18/18；这属于浏览器渲染保护，不替代后端分页或最终规模发布验收。

React 可维护性拆分已把审核收件箱、任务/容量面板、Profile 契约编辑器、生产 DAG、配置/模型/timeline/gate/诊断投影与共享渐进窗口移入对应 `features/` 模块；应用壳从 558 行缩减为 289 行，仅保留路由及跨域 query 编排。分集摘要不再错误显示首集，而是与 URL 深链/生产查询共用所选记录；60 集第 47 集已覆盖。Web 19/19、production build，以及三档 typography/accessibility/canvas 共 9 项真实页面验收继续 PASS。

UI 图标 P1 已闭环：本地零依赖 SVG outline family 替代品牌文字标记、导航/路径箭头、状态点及 G6/G8/G9 的勾选/空心圆 glyph，统一 1.75px stroke；装饰图标不进入可访问性树。图标契约测试 2 项及三档浏览器回归通过，React 源码结构 glyph 扫描为空。

2026-08-15 最新全量门禁回归：蓝图清单仍为 86 FR / 14 NFR / 85 TC；API 129 passed / 4 Comfy live deselected，Ruff PASS，mypy 91 files PASS，Web 23/23 与 production build PASS。新增 FR-IMG-007 聚焦回归覆盖 API 4 项、Web 2 项。该进展不改变 G7 许可证证据阻塞，也不构成最终发布签字。

2026-08-16 图片候选/审核收口回归：FR-IMG-001—006 统一登记到 G10 master map。媒体版本、关键帧派生、结构化 image_asset 审核、批准/拒绝与 selection/approval 分离均通过定向 API 回归；生成工作台的多 take 与受限 seed batch 规划保持本地、显式确认和不可变谱系。新增图片网格 roving tabindex 键盘路径、GET/HEAD 原图读取拒绝及 `image/*` MIME 误标回退断言，继续保证所有读图链路为 small 派生缩略图。当前证据 `docs/evidence/g10/fr-img-001-006-image-candidate-review-2026-08-16.json` 标记 `IMPLEMENTATION_EVIDENCE`，原因是使用用户本地模型/图片的真实批量生成与 Windows 三视口 image grid/A-B UAT 尚未签字；FR-IMG-007 已有独立 production UAT VERIFIED 证据。

FR-TML-004 完成后的最新全量门禁回归：API 132 passed / 4 Comfy live deselected，Ruff PASS，mypy 92 files PASS，Web 25/25 与 production build PASS。`scripts/check.ps1` 已补每个原生命令的显式退出码检查；修复前一次 mypy 失败却最终退出 0 的门禁假绿，修复后整套命令真实以 0 完成。该进展仍不改变 G7 许可证证据阻塞和有序退出状态。

FR-PRJ-003 补齐后的最新全量门禁回归：API 134 passed / 4 Comfy live deselected，Ruff PASS，mypy 92 files PASS，Web 26/26 与 production build PASS；门禁脚本以真实 0 退出。G7 许可证证据阻塞和有序退出状态不变。

FR-PRJ-006 补齐后的最新全量门禁回归：API 137 passed / 4 Comfy live deselected，Ruff PASS，mypy 92 files PASS，Web 27/27 与 production build PASS；蓝图计数仍精确为 86 FR / 14 NFR / 85 TC，门禁脚本真实退出 0。生产三视口仅展开复制策略表单，未确认复制、未修改数据库。G7 许可证证据阻塞和有序退出状态不变。

FR-PRJ-001 创建计划/向导批次后的最新全量回归：API 139 passed / 4 Comfy live deselected，Ruff PASS，mypy 92 files PASS，Web 28/28 与 production build PASS；三档生产 UAT 只执行只读 plan、未点击最终创建。FR-PRJ-001 仍 PARTIAL，G7 许可证证据阻塞与有序退出状态不变。

FR-PRJ-002 项目包基础批次后的最新全量回归：API 142 passed / 4 Comfy live deselected，Ruff PASS，mypy 93 files PASS，Web 29/29 与 production build PASS；生产三档只读入口未触发包导出，隔离环境完成真实导出/dry-run。FR-PRJ-002 仍 PARTIAL，G7 许可证证据阻塞与有序退出状态不变。

FR-PRJ-002 controlled import 批次后的最新全量回归：API 152 passed / 4 Comfy live deselected，Ruff PASS，mypy 94 files PASS，Web 30/30 与 production build PASS；生产三档只读入口未触发 export/stage/commit，隔离环境完成真实副本导入、受限 rebind 与失败双回滚。FR-PRJ-002 因媒体注册/缩略图与 crash journal/receipt 仍为 PARTIAL；G7 真实模型 license evidence 阻塞不变。

FR-PRJ-002 crash recovery 批次后的最新全量回归：API 153 passed / 4 Comfy live deselected，Ruff PASS，mypy 94 files PASS，Web 30/30 与 production build PASS。生产库在线备份后迁移至 `0022` 且 integrity ok；隔离环境验证成功幂等、FAILED 原 ID 重试和陈旧 PREPARING 孤儿目录恢复。FR-PRJ-002 仅因媒体注册/缩略图仍为 PARTIAL；G7 真实模型 license evidence 阻塞不变。

FR-PRJ-002 media closure 批次：同一全量门禁再次以 API 153 / Web 30/30 / build / Ruff / mypy 94 全绿；隔离真实 PNG 经包导出、媒体 metadata/manifest 交叉预检、身份重写、文件复验与注册后，由本地 FFmpeg 首次生成 small WebP 缩略图。生产三档只读 UAT 无包写入、公网、原片、错误或溢出。FR-PRJ-002 更新为 VERIFIED；G7 真实模型 license evidence 阻塞及 G8/G9 progress 状态不变。

FR-PRJ-001 closure 批次：API 156 passed / 4 Comfy live deselected、Web 31/31、production build、Ruff 与 mypy 94 files PASS。生产库在线备份迁移至 `0023` 且 integrity ok；隔离测试完成 2 季×3 集真实创建及 plan/Profile/target 原子绑定零 blocker，三档生产只执行两次 plan、不点击 create。FR-PRJ-001 更新 VERIFIED；G7 license 真实证据阻塞与后续门禁状态不变。

FR-VID-008 closure 批次：migration `0024_video_review_annotations` 已完成 0023→0024 隔离升级/精确恢复演练与生产在线备份迁移，integrity ok。不可变时间码标记支持结构化分类、备注、当前视频派生截图和同项目返工 Job；普通本地视频 import/derive 现持久化真实 duration/fps。全量 API 154 passed / 4 Comfy live deselected、Web 33/33、production build、Ruff、mypy 94 files PASS；三档生产只读 UAT 零写入、公网、原片、错误、溢出或截图。G7 license 真实证据阻塞与后续有序退出状态不变。

FR-WRT-002 closure 批次：migration `0025_episode_scene_ranges` 已完成 0024→0025 隔离升级/精确恢复演练、生产在线备份迁移与 integrity_check。项目级 Master Scene 可由不同 Episode 通过显式 source range 复用，集内 scene/ordinal 唯一且同项目/位置约束硬校验；React 提供禁用态安全的创建与绑定入口。完整门禁 API 157 passed / 4 Comfy live deselected、Web 35/35、production build、Ruff、mypy 94 files PASS；三档生产只读 UAT 零写入、公网、原片、截图、错误、溢出或短控件。G7 真实模型 license evidence 阻塞及后续有序退出状态不变。

FR-WRT-006 closure 批次：只读 continuity read model 和 React 三列面板并排投影前/当前/后镜不可变 revision、六类连续性字段、MediaVersion 参考和 TransitionConstraint，字段缺失保持显式，API 不泄露 rel_path。生产三档 UAT 仅请求 small 缩略图且零写入、公网、原片、错误或溢出；完整门禁 API 159 passed / 4 Comfy live deselected、Web 37/37、production build、Ruff、mypy 94 files PASS。G7 模型 license 真实证据阻塞和有序门禁状态不变。

FR-WRT-003 closure 批次：导演分镜九字段具备前端编辑、不可变/可冻结 ShotRevision 保存、服务端逐项缺失投影与显式 Production Ready 状态转换。生产三档只读 UAT 验证真实回填和安全按钮状态，零写入、公网、原片、错误、短控件或溢出；完整门禁 API 161 passed / 4 Comfy live deselected、Web 39/39、production build、Ruff、mypy 94 files PASS。G7 模型 license 真实证据阻塞和有序退出状态不变。

FR-WRT-005 closure 批次：生产 read model 和导演编辑器明确区分 OUTLINE/DIRECTED/PRODUCTION_READY，并同时保留字段与配置 blocker；字段完整不替代 Profile/Plan/DeliveryTarget 就绪。三档生产只读 UAT 显示真实 PRODUCTION_READY 状态且全程零写；全量门禁仍为 API 161 / Web 39/39 / build / Ruff / mypy 94 PASS。G7 与后续有序门禁状态不变。

FR-WRT-004 closure 批次：结构化提示词模板冻结原始字段、模板、展开结果、负向词、语言与 model Profile version，内容不一致或缺项拒绝；owner 范围查询只投影最新 revision 且保留全部历史。三档生产只读 UAT 零写入、公网、原片、错误、短控件或溢出；完整门禁 API 163 passed / 4 live deselected、Web 41/41、build/Ruff/mypy 94 PASS。G7 与有序门禁状态不变。

FR-WRT-001 closure 批次：migration `0026_creative_entry_revisions` 为故事圣经、人物、场景、道具、服装、风格、声音建立统一不可变 revision；比较只读，回退派生新 revision，历史永不覆盖/删除。0025→0026 隔离升级/精确恢复与生产迁移 integrity ok；三档生产只读 UAT 零写入、公网、原片、错误、短控件或溢出。完整门禁 API 166 passed / 4 live deselected、Web 43/43、build/Ruff/mypy 97 PASS。G7 license blocker 与后续有序门禁不变。

FR-WRT-007 closure 批次：真实 Local LLM breakdown 结果保持 `DRAFT_READY/NOT_APPLIED`，新增查询与 UI 只做审阅，没有自动或显式应用路径，不改变人工维护的 Scene/Shot/CreativeEntry。正式项目 1 份持久草稿的三档只读 UAT 零写入、公网、原片、截图、错误或溢出；完整门禁 API 168 passed / 4 live deselected、Web 45/45、build/Ruff/mypy 97 PASS。release audit 仍因 G7 模型 license 证据和未冻结最终工件保持 IN_PROGRESS。

FR-ING-002 closure 批次：真实启动本机 Ollama，Published `deepseek-r1:14b` Profile load test PASS 后生成 evidence v1 草稿；confidence=0.8、2 个待确认问题、2 条逐字原文引用均固化 Profile/model/source range。新增草稿前后正式 Scene 0、Shot 23、CreativeEntry 0，证明人工确认前不应用。三档只读 UAT 3/3 PASS；完整门禁 API 169 passed / 4 live deselected、Web 45/45、build/Ruff/mypy 97 PASS。G7 总门禁仍被 H3 模型 license 证据阻塞。

FR-AUD-003 closure 批次：真实 FFmpeg ebur128/astats 为正式项目 4 条音频写入 LUFS/true peak/peak/clipping 机器结果，2 PASS、2 个低响度 FAIL 均原样保留；AUDIO 批准强制最新 QC PASS，削波负例无法由人工清单绕过。审核页只读显示 640×128 派生波形和指标，三档 UAT 零写入、公网、原音频、截图、错误或溢出。完整门禁 API 172 passed / 4 live deselected、Web 45/45、build/Ruff/mypy 97 PASS；G7 与有序退出状态不变。

FR-AUD-004 closure 批次：字幕创建不再接受任意 `input_snapshot`，必须绑定同项目、已解析且哈希一致的剧本文档版本；每条 cue 按权威文本顺序逐字定位并冻结偏移与 quote hash。ASR 只能通过已验证本地 AUDIO/VIDEO + Published ASR Profile 记录时间对齐来源，明确不具备文本权威。生产在线备份 integrity=ok 后新增不可变 SRT v2 `fe39d0e0-87da-42d6-8220-ca527444bdbc`，两条 cue 均由剧本真实派生；历史 v1 保留并投影为 `LEGACY_INCOMPLETE`。三档 UAT 零写入、公网、原媒体、截图、错误或溢出；完整门禁 API 172 passed / 4 live deselected、Web 45/45、production build、Ruff、mypy 97 PASS。G7 仍为 11/12 `IN_PROGRESS`，唯一首阻塞不变。

FR-AUD-001 governance 批次：新增 migration `0027_tts_candidate_governance`，对白文本/发音、音色授权和 TTS 候选全部不可变追溯；音色授权证据限定项目内普通文件并冻结 SHA，候选冻结文本 hash、voice version、媒体 SHA、情绪、语速、seed、model 与 Provider Profile。正式候选缺 Published TTS Profile 会拒绝，正式选择还要求最新音频机器 QC PASS + 人工 APPROVED；未绑定 Provider 的导入音频只允许 `PREVIEW/IMPORTED_LOCAL_AUDIO`，文本 revision 变化后旧候选不可再次选择。隔离真实 WAV 测试与 0027 migration 通过；生产在线备份 `pre_migration_20260815T023625Z.sqlite3` integrity=ok 后升级到 0027，正式数据保持 0/0/0，三档 UI 如实显示 `TTS PROFILE MISSING`。完整门禁 API 174 passed / 4 live deselected、Web 47/47、build/Ruff/mypy 100 PASS。FR-AUD-001 仍为 PARTIAL，真实 TTS Provider 试听/Job/候选未完成；G7 license blocker 不变。

FR-AUD-001 SAPI Job 批次：新增真实 Windows System.Speech `TTS_GENERATION` CPU worker、持久 Job 提交与幂等 finalize 路由。隔离测试使用本机 Microsoft Huihui Desktop 生成并 FFprobe 验证真实 WAV，同时覆盖缺 Idempotency-Key、非 `sapi:` 引用、stale 文本、未成功提前 finalize 和快照 hash 篡改拒绝。生成客户端和项目页提供显式排队及结束登记操作，不自动启动 worker或伪装同步完成。完整门禁 API 175 passed / 4 live deselected、Web 58/58、build/Ruff/mypy 100 source files PASS；1440×900、1280×800、1024×768 三档只读 UAT 3/3 PASS，零写入、公网、原音频、浏览器错误、短控件或横向溢出。测试授权 fixture 不属于正式项目证据，FR-AUD-001 仍为 PARTIAL；G7 license blocker 不变。

FR-AUD-002 authorization correction：新增 migration `0028_audio_binding_authority`，新绑定必须提供项目内授权证据文件并冻结 SHA/size，同时冻结媒体 SHA、loop、fade、gain 和时间范围；跨项目/非音频/源篡改/非法轨道/未 loop 超源时长/非法 fade 均拒绝。生产在线备份后升级至 0028、integrity=ok；既有 4 轨没有真实授权文件，统一保留为 `LEGACY_INCOMPLETE`，`verified_local_count` 由 4 修正为 0，G8 首动作真实退回 `DIALOGUE_ENVIRONMENT_SFX_MUSIC`。工作台新增四轨策略、`preload=none` 按需试听，以及显式本地导入→授权证据校验→绑定表单；三档 UAT 展开表单后零写入、公网、自动原音频请求、截图、短控件、错误或溢出。Web 全量 50 / build PASS，最近完整门禁 API 174 / Ruff / mypy 100 PASS。此项仍 PARTIAL；G7 license blocker 与有序门禁不变。

FR-AUD-001 SAPI 选择入口增量：新增 `GET /api/v1/tts/voices:discover`，Windows 本机只读调用 System.Speech 列出已安装音色名称、区域与 `sapi:` 引用；未找到 runtime 时明确返回 `UNAVAILABLE`，扫描不复制/上传/写入项目。对白治理 UI 需用户显式点击扫描并选择，之后仍必须填写项目内授权证据、绑定 Published TTS Profile、执行真实 Job、QC、人工审核与选择；本入口不改变 FR-AUD-001 的 `PARTIAL` 状态。

FR-AUD-001 Windows SAPI 隔离 UAT：新增 `scripts/tts_windows_uat.py`，在全新迁移数据库和临时项目中发现当前 Windows System.Speech 音色，写入用户提供的项目内授权证据，绑定 Published `TTS_SAPI_LOCAL` Profile，提交带 Idempotency-Key 的真实 CPU Job，由 `LocalMediaWorker` 调用本机 SAPI 生成 PCM WAV，经 FFprobe、SHA 与正式候选晋升校验，并验证提交/结束登记幂等、提前结束拒绝、无公网访问和数据库完整性。证据 `docs/evidence/g10/dialogue-tts-windows-uat-2026-08-16.json` 为真实本机隔离 PASS；不接触正式数据库、不自动创建正式 Profile/音色，也未替代正式项目的 QC、人工审核、候选选择和发布签字，因此 FR-AUD-001 仍保持 `PARTIAL`。

项目包模型边界补充：`test_project_package_never_bundles_external_user_model_reference` 将用户选择的绝对模型路径登记为 `model_artifacts` 机器引用后导出真实项目包，断言压缩包和 `project-state.json` 均不包含权重路径/文件，外部权重仍留在原位置；平台只保存路径、hash、兼容性与授权风险，不复制、打包或上传用户模型。

FR-PST-002 实现增量：版本化后处理 recipe 在核心 `SCALE → TECHNICAL_QC → ENCODE` 之间支持能力驱动的 `FRAME_INTERPOLATION`、`DENOISE`、`STABILIZE` 和项目内 `.cube LUT_3D`。每个步骤独立运行本地 FFmpeg 中间文件并冻结输入/输出 SHA；目标帧率与分辨率写入技术 QC，任一步失败只将 enhancement run 标记失败并清理临时输出，源 MediaVersion 与先前成功版本不变。API 真实 FFmpeg 回归与 Web 选项测试通过；FR-PST-002 的三视口生产 UAT 与正式总账证据仍待补齐，当前不标最终 VERIFIED。

FR-PST-003 实现增量：新增不可变版本化 `WatermarkProfile` 与 `CompliancePolicy`，并扩展 BrandKit 为项目视觉 token 版本；新版本发布会 RETIRE 同 code 的旧 ACTIVE 版本。整集本地交付自动读取或显式绑定当前 ACTIVE 控制版本，水印使用本机 Windows 字体由 FFmpeg 生成新文件，不覆盖整集渲染；合规机器预检记录规则、render SHA、发现项与责任边界，失败不产生交付包。交付 manifest 与 `delivery_packages` 冻结 BrandKit/Watermark/Compliance 版本、machine preflight=PASS、human/platform review=PENDING，后续 verify 只校验文件完整性，不把机器结果冒充人工/平台批准。真实 FFmpeg/API 回归、版本轮换与失败预检测试通过，Web 面板提供显式发布入口；三视口生产 UAT、正式证据与总账 closure 仍待补齐，当前不标最终 VERIFIED。

FR-PST-003 责任分离增量：新增 `POST /delivery-packages/{id}:review`，审核者必须显式选择 `HUMAN` 或 `PLATFORM` 并填写说明，审核结果分别写入不可覆盖的 delivery package 状态与 delivery event；撤回包不能再审核。交付面板提供人工批准/平台批准按钮，并持续显示“机器 PASS ≠ 人工/平台批准”。真实 API 回归覆盖机器 PASS 后两类批准、说明留痕与状态分离；生产三视口 UAT 与正式总账证据仍待补齐。

FR-CTL-002 实现增量：新增 migration `0036_motion_control_media` 与 `motion_controls` 不可变绑定表；`POST/GET /media-versions/{source_media_version_id}/motion-masks` 支持 `MOTION_MASK`、`VECTOR`、`KEYFRAME` 以及 `MOTION_BRUSH`、`INPAINT`、`OUTPAINT`。Raster mask/keyframe 只能引用同项目已验证 MediaVersion，运动笔刷/vector/keyframe JSON 会在项目目录注册为新的 `OTHER` MediaVersion；服务端要求已发布 Profile 的 capability contract 明确 `enabled=true`，源媒体 hash/path 永不覆盖。API 3 项测试与 Web MotionControlPanel 1 项测试通过，生成客户端/OpenAPI 已更新；当前控制版本需用户显式绑定到既有 MotionMask 生成输入，不自动改写草稿；正式生产画布三视口 UAT、真实栅格笔刷审阅与总账最终证据仍待补齐，证据 `docs/evidence/g10/fr-ctl-002-motion-controls-2026-08-15.json` 保持 `PARTIAL`。

FR-CTL-003/004 实现增量：生成服务现在对 `DRIVING_VIDEO`、`POSE_SEQUENCE`/`POSE_REFERENCE`、`AUDIO_GUIDE`、`FACE_REFERENCE`、`CHARACTER_REFERENCE` 与 `CHARACTER_DRIVING` 进行 Profile capability、Workflow semantic binding、项目归属和 VERIFIED MediaVersion 校验；`PerformanceBinding` 冻结 actor/action/time-range、binding_type/source_role 语义。`VariantInput` 的数量、连续 ordinal/允许顺序、media_kinds（含 MIME 类别）及可选/必填 weight 和范围均由 Published Profile input contract 裁决，提交路径和 Job snapshot 持久化 weight，源媒体不可覆盖。生成工作台新增驱动/参考 MediaVersion 结构化输入面板，仍只提交本地 ID，不上传或自动替换来源。API 3 项正/负向测试、Web 1 项控件测试、OpenAPI/client 已更新；真实 Windows x64 driving output、身份/动作/音画同步独立审核及三视口生产 UAT 仍待补齐，证据 `docs/evidence/g10/fr-ctl-003-004-multimodal-contracts-2026-08-15.json` 保持 `PARTIAL`。

FR-REV-001 实现增量：审核收件箱 read model 现在在筛选前稳定投影跨项目、项目、集、媒体类型、年龄（NEW/AGING/OLD 及数值范围）、优先级和阻塞状态；项目/集来自真实 owner 关系，阻塞/优先级为只读派生字段，不自动审核。分页先过滤后以 `inbox_at, media_version_id` 固定排序，响应包含 age、machine、integrity、project/episode/shot 上下文；ReviewInboxPanel 提供对应显式筛选，生成客户端与 OpenAPI 已更新。隔离 API 回归覆盖跨项目、集、年龄、优先级、阻塞和稳定 cursor，Web 面板测试覆盖筛选组合；正式三视口 UAT、深链接返回位置恢复和总账最终证据仍待补齐，证据 `docs/evidence/g10/fr-rev-001-review-inbox-filters-2026-08-15.json` 保持 `PARTIAL`。

FR-REV-002..004 审核治理增量（2026-08-16）：`ReviewService.ensure_templates` 改为启动幂等且追加版本，新增 `POST /review-templates` 显式创建新版本；模板定义、`subject_type` 与检查项 id 经服务端校验，历史 `review_decisions.review_template_version_id` 永不被覆盖，新审核自动使用最新版本，错误媒体/阶段模板硬拒绝。机器 QC 继续只写不可变 `machine_check_runs/results`，人工 `review_decisions/checks` 与 audit event 独立，机器 PASS 不会自动批准；正式视频和音频批准要求最新 machine PASS。批量审核要求显式勾选、同项目/同模板、revision 预检、完整必填检查与拒绝原因，失败返回逐项阻塞且不产生部分写入；正式候选选择仍通过 hash 绑定的原子预检/提交。新增 `test_review_versions.py` 覆盖模板历史解释、错误模板、机器/人工分离、批量异常与 API；ReviewInboxPanel 显示当前模板版本。新增真实 Windows x64 隔离 UAT：本地 FFmpeg MP4、SQLite、127.0.0.1 API 验证模板 v1/v2 追加不可变、历史回溯、机器 QC/人工批准分离、stale 批量 409 可见且无部分写入、修复后两项批量提交；8/8 checks PASS，但整体证据保持 `PARTIAL`，因为仍未宣称正式项目三视口/权限角色/生产规模或 Unicode 路径覆盖。证据 `docs/evidence/g10/fr-rev-002-004-review-governance-2026-08-16.json` 与 `docs/evidence/g10/review-governance-windows-uat-2026-08-16-run.json`。

FR-CTL-001 capability contract 增量（2026-08-16）：CameraPlan 的 `NATIVE`、`PROMPT_FALLBACK`、`UNSUPPORTED` 只能由已发布 Profile capability contract 只读裁决，fallback 必须显式声明 prompt；ShotRevision 保存和 Production Ready 转换在服务端重新裁决，阻断客户端伪造或 contract 变更后的旧 revision。DirectorShotEditor 展示 unsupported/fallback 真实状态并补齐 ZOOM vocabulary；Variant preflight/Job snapshot 继续冻结同一 resolution。新增真实 Windows x64 隔离 UAT，9/9 checks 验证 Published Profile 三种能力、正向 Revision/Ready、伪造 NATIVE 拒绝和 contract 变更后 stale Ready 拒绝；UAT 仍保持 `PARTIAL`，因为 fixture 是隔离 Published Profile，不宣称用户真实模型执行、浏览器三视口或正式生产签字。证据 `docs/evidence/g10/fr-ctl-001-camera-capability-contracts-2026-08-16.json` 与 `docs/evidence/g10/fr-ctl-001-camera-windows-uat-2026-08-16.json`。

FR-VID-002/003/004/008 视频审核增量（2026-08-16）：视频缩略图 `frame=first/middle/last` 通过本地 duration/seek 生成并以 normalized frame 隔离 cache，poster 仅为 first；审核预览支持 Range、循环、静音、倍速与 probe-fps 逐帧步进，同步比较保持 2—4 路显式控制，proxy winner 选择不等同批准。时间码标记新增“使用播放器当前时间”和点击标记回跳，保留结构化分类、派生截图与同项目返工约束。API/Web 回归通过，证据 `docs/evidence/g10/fr-vid-002-008-frame-review-2026-08-16.json` 为 `PARTIAL`，真实候选同步漂移/截图和 Windows 三视口 UAT 仍待补齐。

FR-VID-001/005/006/007 视频生成与正式审核增量（2026-08-16）：生成工作台对代理 take 数量（1—8）、显式 seed 和 I2V 已批准关键帧执行两阶段预检；每个 take 生成独立不可变 Variant/Job，seed 批量仍为只读计划。提交快照冻结 Published Profile revision、workflow/content hash、本地 model bundle/manifest、seed、媒体 approval/hash，精确重放拒绝快照漂移，成功产物登记为新 MediaVersion。正式视频机器 QC 新增本地 ffprobe 驱动的 decode、dimensions、fps、duration、codec 结果（`g6_formal_video_qc_v1`），人工 `formal_video` 模板保留动作、身份、闪烁、字幕安全区独立检查；正式批量交付预检逐项返回批准/过期/完整性冲突并以 plan hash 原子提交。API 回归 `test_formal_video_qc.py`、generation/review tests 与 Web generation/generated-client tests 通过；证据 `docs/evidence/g10/fr-vid-001-005-007-006-formal-video-2026-08-16.json` 为 `PARTIAL`，真实 Windows 本地模型正式产物、三视口人工 QC 与多项交付漂移 UAT 仍待补齐。

FR-AUDT-001 实现增量：新增只读 `GET /api/v1/audit-events` 与 `AuditService`，按项目、时间、动作、actor、subject 类型/ID 筛选，先过滤后以不可变 `event_id DESC` 游标分页，项目范围由 subject 关系和经 JSON 校验的 project hint 解析。响应递归脱敏 token/secret/password/credential 与本机路径，明确 `metadata_redacted=true`、`local_only=true`、`network_contacted=false`、`mutated=false`；新增只读 `GET /api/v1/audit-events/proof`，以相同筛选和稳定游标语义对最多 1000 条脱敏导出投影计算 `SHA-256-chain-v1`，不返回原始秘密或本机路径；诊断页新增 AuditHistoryPanel，展示 loading/error/empty、筛选、旧事件翻页、脱敏详情和有界导出哈希证明。API/Web 回归和构建通过；正式 Windows 三视口 UAT、深链接恢复、备份介质/跨机恢复与正式发布签字仍待补齐，证据 `docs/evidence/g10/fr-audt-001-audit-history-2026-08-15.json` 保持 `PARTIAL`。

FR-DEL-001..004 交付链增量（2026-08-16）：创建候选前强制最新、未过期的 `EPISODE_RENDER_VERSION` 人工批准；输出目录使用唯一版本路径与 `.partial-*` 原子发布，后续构建不会覆盖旧包。`delivery-manifest.v3` 冻结源 render/timeline SHA、目标版本/spec、编码 probe、字幕 revision、音频授权证据、控制版本和每个文件 SHA/size；verify 可重复执行并检查路径越界、symlink、字节数、文件 hash 与 canonical manifest hash，篡改指出具体文件。新增 package/files/history/download 只读 API，POST/GET verify 兼容，withdraw 仅标记状态并保留文件，完整性复验不会把 `WITHDRAWN` 复活；成功下载额外记录 bounded `DOWNLOAD` event（manifest/file SHA、size、项目相对文件与 `LOCAL_FILESYSTEM` transport），不记录请求或绝对路径；DeliveryTargetVersion create/select API 版本化且只允许 `LOCAL_FILESYSTEM`。真实隔离 FFmpeg/API 回归、未批准负例、双构建不覆盖、manifest/history/download/撤回复验均通过；新增真实隔离 UAT 创建并选择 v1/v2 两个目标版本，再以 v2 构建包并核对版本冻结（TC-DEL-006）；正式 Windows x64 三视口生产 UAT、实际项目多版本选择与发布签字仍待补齐，证据 `docs/evidence/g10/fr-del-001-004-delivery-chain-2026-08-16.json` 与 `docs/evidence/g10/delivery-local-windows-uat-2026-08-16.json` 保持 `PARTIAL`。

FR-AST-001 资产授权与跨项目生成门禁增量（2026-08-16）：WorkspaceAssetService 只接受项目内 VERIFIED、路径/symlink、SHA-256 与字节数一致的源媒体；READ_ONLY/DERIVED grant 冻结源项目、授权 revision、source revision/hash/size 与 access mode，撤回只产生不可变 impact/audit，不删除源记录。Generation preflight 对跨项目媒体要求 ACTIVE grant 与授权快照完全匹配，撤回或源篡改硬拒绝；UI 提供显式授权面板。API 4 项、Web 2 项定向测试通过，证据 `docs/evidence/g10/fr-ast-001-workspace-asset-grants-2026-08-16.json` 为 `PARTIAL`，Windows 三视口和完整正式 lineage UAT 仍待补齐。

FR-GEN-001..004 可复现生成增量（2026-08-16）：故障重试只把同一 Job 重新排队并增加 Attempt，创作重抽必须由 Variant Composer 产生子 Variant + 新 Job；固定 seed、provider random、EXACT_REPLAY 与 prompt/source branch 的 changed/preserved 字段均在提交前展示。提交 Job 冻结 Profile revision、runtime、Workflow/content hash、本地 model bundle/hash 与 manifest hash；EXACT_REPLAY 检测冻结快照变化并拒绝静默改用新模型/Workflow。GenerationWorkbench 增加固定 seed/精确重放入口，定向 API 与 Web 回归通过；证据 `docs/evidence/g10/fr-gen-001-004-generation-replay-2026-08-16.json` 为 `PARTIAL`，真实 Windows 本地模型 bundle、非确定 runtime 和三视口 UAT 仍待补齐。

FR-JOB-001..005 调度增量（2026-08-16）：migration `0037_job_progress_scheduler` 为 jobs/attempts 持久化 phase/node/percent/ETA、开始/结束时间和脱敏失败详情，并新增 `job_resource_leases`；worker/API 重启 claim 前自动 reconcile 过期租约，GPU_H3_HEAVY 独占而 CPU/text/audio 通道独立。新增 Attempt 列表与分页日志 API，lease token 永不返回；取消、重试、克隆、幂等与产物防重复保持。9 项 G5/调度测试、迁移、Ruff/mypy 与 Web build 通过，证据 `docs/evidence/g5/job-scheduler-progress-2026-08-16.json` 为 `PARTIAL`，真实 Windows 重启/多 worker/三视口 UAT 仍待补齐。

FR-GEN-005..010 高阶输入与边界连续性增量（2026-08-16）：SOURCE_IMAGE_BRANCH 继续只替换 FIRST_FRAME ordinal 0，并将 MediaVersion version_no、parent lineage、stage、owner、approval/selection、SHA 固化到 plan dependency；跨项目源图必须经 ACTIVE ProjectAssetGrant 且授权 revision/hash/size 一致。首尾/中间关键帧仍按 Published Profile input contract 做 slot/cardinality、宽高比与 workflow binding 硬校验；视频首/当前/末帧通过 FFprobe PTS 注册不可变 FrameAnchor。VIDEO_EXTEND、VIDEO_TO_VIDEO、MOTION_CONTROL、PERFORMANCE_DRIVEN 及 SOURCE_VIDEO/MOTION_PATH/MASK 语义槽新增显式 capability gate，未声明能力时返回可行动阻塞；TransitionConstraint 类型/执行级别校验，SHARED_BOUNDARY_FRAME 缺少双锚点或 hash 不一致时阻塞，历史 stale 传播不覆盖旧谱系。定向 API 回归通过，证据 `docs/evidence/g10/fr-gen-005-010-advanced-inputs-2026-08-16.json` 保持 `PARTIAL`；真实 Windows 本地高阶 workflow、实验 cell winner/review/promotion、shared-boundary timeline 去重和三视口 UAT 仍待补齐。

FR-OPS-001..003 / FR-PRV-001..003 本地运维与能力 registry 增量（2026-08-16）：诊断中心新增 GPU driver/CUDA、ComfyUI 节点、模型 hash、网络策略检查及 GET 别名；dry-run-fix 只返回修复预览，不改驱动、不联网、不安装节点。模型 registry 提供用户选择目录的绝对路径只读扫描，逐文件 SHA-256/量化 hint，明确不复制、不上传；capacity snapshot 从持久 jobs/reviews/本地 filesystem 计算队列、GPU、磁盘、耗时、失败率、重试率和审核通过率，并标记 `OBSERVED_NOT_BENCHMARKED`。现有 Adapter/Profile registry 继续 LOCAL_ONLY，远端 transport 禁用。API 3 项定向测试与既有 profile/adapter/capacity 回归通过，证据 `docs/evidence/g10/fr-ops-001-003-local-ops-registry-2026-08-16.json` 保持 `PARTIAL`；真实用户模型扫描和 Windows 三视口 UAT 仍待补齐。

FR-WFL-001/002 工作流版本发布与回滚增量（2026-08-16）：WorkflowService 在注册阶段校验 semantic binding 的 role/node/input 以及 contract 必需 input slot，失败不会留下 workflow row；每个版本固化 API graph、contract、binding、runtime contract、content hash 和隔离 package path。Local validation 生成服务端 attestation 并绑定不可变 content hash，publish/rollback 拒绝伪造、失败或过期证明；publish 自动退休旧版本，revoke 要求并审计操作员原因，rollback 只能在重新验证后重新发布历史版本。ProfileConfigurationPanel 已提供本地验证、发布、必填原因撤销和验证后回滚入口，操作后刷新工作流历史。新增 `test_workflow_release_contracts.py` 与既有 G6/ProfileEditor 回归通过；证据 `docs/evidence/g10/fr-wfl-001-002-release-history-2026-08-16.json` 保持 `PARTIAL`，真实 Comfy smoke/regression/benchmark、用户批准和 Windows 三视口 UAT 仍待补齐。

FR-WFL-004 ComfyUI Lab control-plane 增量（2026-08-16）：新增 Designer 专属 `status/session/start/stop/restart`、隔离 workflow capture 与显式 test-run plan API。只有用户配置的本机 Designer Python/root 才允许无 shell 启动；端点强制 loopback、`--disable-api-nodes`，输入/输出/temp/user/capture 全部位于 `work/comfy-lab`，绝不写正式 project/workflow_packages。未配置时服务端返回可行动 `COMFY_LAB_LAUNCH_NOT_CONFIGURED`，状态和 session 只读且明确 `runtime_contacted=false`；诊断页新增 ComfyLabPanel，默认只生成不执行的 test plan。API/Web 回归、Ruff/mypy 通过，证据 `docs/evidence/g10/fr-wfl-004-comfy-lab-2026-08-16.json` 保持 `PARTIAL`；真实 Windows Designer 进程、嵌入/新窗口和 proxy header 隔离 UAT 仍待完成。

## NFR-OBS-001 可观测性增量（2026-08-16）

`RequestContextMiddleware` 现在在每个 API 请求结束、异常或安全早拒绝时输出结构化 JSON access/failure log，带 timestamp/level/service/event、`trace_id`、`request_id`、`project_id`、`episode_id`、`shot_id`、`job_id`、`attempt_id`、`worker_id`、`provider`、HTTP method/path、状态和耗时；标识符有长度/字符边界，body、query string、Authorization 和异常文本不进入日志。响应回显 `X-Trace-Id`，便于本机排障；既有持久任务日志 API 仍由 Jobs 域提供。本轮 API 自动测试验证成功/404/安全拒绝请求上下文提取、trace header 和 secret 不泄漏，Ruff/mypy 通过。证据 `docs/evidence/g10/nfr-obs-001-structured-logs-2026-08-16.json` 为 `PARTIAL`：正式 Windows 日志轮转/保留、以及任务日志 UI 的三视口验收尚未执行，不能据此宣称 NFR PASS。

## NFR-SEC-001/002 本机监听、CSRF 与路径边界增量（2026-08-16）

`Settings.host` 现在在配置解析阶段只接受 literal `127.0.0.1`、`localhost` 或 `::1`；任何 `0.0.0.0`、LAN 地址或其它 DNS 值都会在 API 启动前 fail-closed，首版不存在未认证的远程监听开关。写请求继续要求受控 local Origin 与每进程 `X-Local-Instance-Token`，跨源/缺失 token 的拒绝响应统一 `no-store`，不回显攻击者提供的 Origin，并保留 `nosniff`、CSP 与 referrer policy。

媒体项目根在解析前拒绝绝对路径、`..` traversal 和 symlink root，再校验解析结果仍位于 `projects_root`；媒体内容仍只由持久化 `media_version_id` 间接定位，不接受任意文件路径。定向回归见 `apps/api/tests/test_health.py`、`apps/api/tests/test_security_boundary.py`，证据 `docs/evidence/g10/nfr-sec-001-002-local-boundary-2026-08-16.json` 保持 `PARTIAL`：正式 Windows x64 三视口 UAT、生产包级 egress capture 与完整全接口路径审计仍待执行，不能提前宣称 NFR VERIFIED。

## NFR-MEDIA-001 Range / 首帧播放增量（2026-08-16）

媒体 content 端点继续只接受 `media_version_id`，并使用 seek-based、1 MiB 上限的流式迭代器，不将整片读入内存；Range 现在覆盖首段、尾段/suffix、HEAD、无效范围 416，以及强 ETag/日期 `If-Range` 不匹配时回退完整 200。响应带 `Accept-Ranges`、`Content-Range`、ETag 和 `Last-Modified`，注册项目目录越界在打开前拒绝。视频 `first/poster` 缩略图仍按本地首帧 seek 生成并以源 SHA + normalized preset 隔离 cache；定向测试包含本地首帧 <2s smoke assertion、流式 chunk 上限和路径逃逸负例。`scripts/nfr_media_windows_uat.py` 又在 Windows 11 loopback 真实 API 上验证 HEAD、首/尾 Range、冷/热首帧和 4 路并发（冷 232ms、热 32ms、Range p95 41ms），但仍是短程单编码器观测；证据 `docs/evidence/g10/nfr-media-windows-uat-2026-08-16.json` 与基础 Range 证据均保持 `PARTIAL`，不能据此宣称 NFR PASS。

## NFR-REL-001/002 持久化与重启恢复增量（2026-08-16）

Job 创建、幂等记录、`JOB_QUEUED` outbox 和 audit row 在同一 SQLite transaction 内提交；`jobs`、`job_attempts`、progress、lease、resource lease 与 outbox 均由新建 `Database/JobService` 实例重新读取。新增独立子进程演练：子进程完成 create/claim/heartbeat 后使用 `os._exit(17)` 模拟非正常退出，父进程验证任务状态、phase/node/percent、Attempt 历史、outbox cursor 与脱敏 lease token 均持久存在，再将调度时钟前移 61 秒执行 reconcile，断言 Attempt→`ORPHANED`、可重试 Job→`QUEUED`、资源 lease 已释放并追加 `JOB_RECONCILED`。证据 `docs/evidence/g10/nfr-rel-001-002-persistence-restart-2026-08-16.json` 为 `PARTIAL`：测试不接触公网或 Comfy，也不修改生产库；真实 Windows API/Worker kill matrix、物理断电窗口、60 秒内可见状态和多 worker 竞争 UAT 仍待执行，不能据此宣称 NFR PASS。

## NFR-REL-003 transactional outbox backup/delivery continuity 增量（2026-08-16）

`0038_outbox_delivery_ledger` 新增按 endpoint 持久化的 generic outbox delivery ledger，不改变不可变事件 payload。`POST /events:deliver` 创建持久 `PENDING` claim，在事务内进入带 30 秒租约的 `IN_FLIGHT`，仅收到 loopback 2xx 后确认 `DELIVERED`；新服务实例会回收过期 claim，并保留稳定 event-id header。失败持久化为 `RETRYING` 并指数退避（5 秒起、上限 300 秒），最多 5 次后进入 `DEAD_LETTER`；重试、回收、成功与失败均写脱敏 audit row。loopback 重定向在跟随前拒绝，防止跳转公网。定向回归为 `apps/api/tests/test_outbox_delivery.py`（失败/backoff、过期 claim 恢复、重复 event-id 和 redirect 负例）与 `apps/api/tests/test_migration.py`；隔离 `0031→0038` 升级/恢复证据为 `docs/evidence/g10/upgrade-rollback-rehearsal-0038-2026-08-16.json`。状态保持 `PARTIAL`：这是 bounded 本地演练，不代表最终 Windows x64 断电、备份介质或多进程 UAT 已完成。

## FR-PRV-002 / NFR-SEC-003 / NFR-PRIV-001 本地网络与配置边界增量（2026-08-16）

Profile 契约编辑仍允许用户声明本地 transport、能力与本机模型引用，但不再把它当作远程配置或凭据存储：输入契约、参数 Schema、输出契约和资源策略在派生不可变版本前递归拒绝 `api_key`、`client_secret`、`provider_url`、`remote_endpoint` 等保留字段，错误只返回 JSON 字段路径，不回显值，也不插入 Profile 版本。Adapter registry、Comfy/Local LLM client 和模型 registry 继续强制 loopback/本机路径、拒绝 symlink/越界、禁止复制/上传；网络 E2E 与安全 UAT 保持公网连接数为 0。新增真实 Windows x64 隔离 transport UAT：对临时 127.0.0.1 服务执行 5 次真实连接，代理环境不会改变 loopback 目标，302→公网在跟随前拒绝，非 loopback/凭据/query endpoint 在连接前拒绝，provider 错误中的路径/token 不进入证据；脚本回归 `apps/api/tests/test_local_adapter_transport_windows_uat.py` 与 `scripts/local_adapter_transport_windows_uat.py`，7 项测试通过，证据 `docs/evidence/g10/nfr-sec-003-prv-002-local-adapter-transport-2026-08-16.json` 仍标记 `PARTIAL`。这不是正式生产出口抓包或三视口 UAT，故不提前宣称 NFR PASS。

## FR-PRV-003 / NFR-COMP-001 本机模型许可与兼容性边界增量（2026-08-16）

模型登记继续只接收用户选择的本机绝对路径，拒绝相对路径、symlink 和越界；服务端离线读取完整文件 SHA-256 与 safetensors 头部 dtype/量化信息，项目内 `00_admin/licenses` JSON 许可证证明绑定当前模型 SHA-256、许可证名称、证据文件 hash 和 `LOCAL_LICENSE_VERIFIED`/`USER_OWNED` 状态。新增可选的显式验证目标 capability（T2V/I2V/VIDEO/IMAGE/AUDIO/TTS/TEXT）：根据用户声明的模型角色推导保守 capability alias，缺少声明或不匹配时报告为 `BLOCKED`，不会允许把模型当成该能力使用；许可证缺失仍只显示 `USER_RESPONSIBILITY_UNKNOWN`，不替用户做法律判断。模型路径、hash、量化和 attestation 元数据落库，权重永不复制、打包或上传；UI 展示能力状态、blocker 和本地授权风险。

Unicode/空格/长路径、I2V→T2V 不匹配硬阻断、I2V 匹配、完整模型 hash 与本地授权证据回归见 `apps/api/tests/test_model_compatibility.py`、`apps/api/tests/test_model_license_evidence.py`、`apps/web/src/features/status/LocalModelReferenceForm.test.tsx` 和 `apps/web/src/features/status/ModelLicenseEvidenceForm.test.tsx`；证据 `docs/evidence/g10/fr-prv-003-nfr-comp-001-model-boundary-2026-08-16.json` 保持 `PARTIAL`。真实用户模型执行、Windows x64 跨卷/超长路径三视口 UAT 和最终法律许可证复核仍待补齐，不能据此宣称 NFR-COMP-001 或整体 Profile 发布闭环 VERIFIED。

新增独立 Windows 路径矩阵 `scripts/nfr_comp_windows_path_uat.py`：在隔离迁移数据库和真实 FastAPI 路径中，从 F: 创建包含中文、空格及 222 字符路径的 safetensors 夹具，复制到 E:（目标路径 189 字符）并校验跨卷 SHA-256 一致；登记与兼容性报告均保留 E: 本机绝对引用，返回 `copied=false`/`uploaded=false`，I2V capability 匹配和源文件未改变。证据 `docs/evidence/g10/nfr-comp-001-windows-path-2026-08-16.json` 状态为 `PARTIAL`：该复制由测试夹具执行且未写入项目，仍不替代真实用户模型执行、三视口浏览器 UAT 或最终许可证复核。

## NFR-PERF-001/002 有界性能与列表分页增量（2026-08-16）

项目列表新增稳定的 `cursor/limit` 页面契约；集生产 read model 新增服务端 `cursor/limit/next_cursor`，默认调用保持兼容；审核收件箱继续在筛选后按稳定 cursor 返回上限页面。新增 `scripts/nfr_perf_benchmark.py`，在隔离迁移 SQLite 的 60 集/800 镜头/10k 媒体 fixture 上通过真实 FastAPI read paths 观测收件箱首/深 cursor、每集生产页和项目页，同时记录缩略图 `loading="lazy"`/不批量读取原片的源级约束。API 测试覆盖 project cursor 与 production page；证据 `docs/evidence/g10/nfr-perf-bounded-observation-2026-08-16.json` 标记 `PARTIAL` / `OBSERVED_NOT_BENCHMARKED`。该证据不宣称 10k p95<500ms、Windows x64 浏览器 p95<2s、虚拟滚动、冷缓存或真实硬件容量；正式浏览器网络 trace 与 release 性能基线仍待 UAT。

## FR-AUT-001 声明式任务 Job 谱系增量（2026-08-16）

声明式 automation run 的每个实际 task 现在在同一 SQLite 事务内创建持久 `AUTOMATION_WORKFLOW_TASK` Job，并将 workflow/run/task ID、plan hash、机器状态、`approval_required`、`approval_status` 与 `local_only/network_contacted` 快照固化；后续任务通过 `job_dependencies` 串成有限 Job DAG，Job 被 claim 后继续沿用现有 Attempt、lease、progress、outbox 和重启 reconcile。HITL 任务不会因 AI 分数自动批准，Job 快照保留 `approval_required=true`/`PENDING`，人工 resume 仍是 workflow 状态机唯一批准入口。新增 migration `0039_automation_task_jobs`、任务 read model 的 `job_id/job_state` 和回归 `test_each_automation_task_has_durable_job_lineage_and_bounded_dependency`；证据 `docs/evidence/g10/fr-aut-001-automation-workflows-2026-08-15.json` 保持 `PARTIAL`，真实 worker 在 HITL 前后、重启/磁盘压力和 Windows 三视口 UAT 仍待执行。

## 更新规则

任何新增/变更需求必须先分配 ID、写 ADR、补 migration/API/UI/test 影响；所有阶段报告、提交和缺陷引用至少一个需求或测试 ID。

FR-PST-002/003 边界加固（2026-08-16）：交付包在文件/manifest 完整性复验后若进入 `CORRUPT`，即使数据库仍保留 machine preflight=PASS，也不能追加 HUMAN/PLATFORM 审核；必须重建新的不可变包（`DELIVERY_NOT_VERIFIED`）。新增篡改后审核负例回归并补充证据 `docs/evidence/g10/fr-pst-002-003-postprocess-delivery-2026-08-16.json`。可选后处理链、输入不覆盖、品牌/水印/合规版本冻结和机器/人工/平台职责分离已有 API 回归；三视口生产 UAT、正式总账 closure 仍待补齐，当前保持 PARTIAL。

## NFR-MAINT-001 / NFR-TEST-001 可维护性与回归审计增量（2026-08-16）

新增只读 `scripts/maintainability_audit.py`，作为静态回归门禁的一部分检查后端分层边界、非生成 React 组件行数（建议 `<500`，`>700` 为硬警告）以及领域模块到自动化测试的导入覆盖。当前领域层未发现 FastAPI/SQLAlchemy/具体基础设施依赖，非生成 UI 组件均低于 700 行，4 个领域模块均被 API 测试直接覆盖（69 个 API 测试文件/255 个测试函数，Web 35 个测试文件）。

审计同时显式报告当前应用层/路由层仍直接引用具体 SQLite/adapter 基础设施的迁移警告，不隐藏架构债务；该警告不伪装成边界 PASS，也不改变本机运行行为。`NFR-MAINT-001` 证据 `docs/evidence/g10/nfr-maint-test-audit-2026-08-16.json` 仍为 `PARTIAL`。`NFR-TEST-001` 已由 Windows x64 `scripts/check.ps1` 全门禁（285 API、Ruff、mypy、生产构建、100 Web tests）和三视口 Edge 核心链（3/3）单独证实为 `PASS`，证据 `docs/evidence/g10/nfr-test-001-full-gate-2026-08-16.json`；这不代表其它 PARTIAL 需求自动关闭。

## 2026-08-16 隔离 UAT 与本地传输边界补充

本轮在干净临时根目录执行了真实 FastAPI/SQLite 安全 UAT：恶意 Origin、缺失/错误 token、项目路径逃逸、远端 Provider、自定义不可信节点和公网网络请求均按预期阻断，数据库 integrity 通过，证据为 `docs/evidence/g10/security-uat-2026-08-16.json`。该证据只覆盖隔离本地边界，不替代生产 Windows 三视口验收。

ComfyUI 与 Local LLM loopback 客户端现在使用显式无代理、拒绝 3xx 跳转的本地传输；endpoint 拒绝凭据/query/fragment，Comfy provider 错误不再回显原始路径、token 或 node payload。11 项真实本地 socket 回归通过，证据 `docs/evidence/g10/nfr-sec-003-prv-002-local-adapter-transport-2026-08-16.json` 保持 PARTIAL。

规模与恢复 UAT 也在隔离根目录通过：60 集/800 镜头/10,000 媒体元数据 read path 与索引检查通过（合成媒体明确保持 UNKNOWN，不冒充可播放素材），见 `docs/evidence/g10/metadata-scale-uat-2026-08-16.json`；100 个真实本地 WAV 经 online backup、干净恢复、100/100 SHA-256 与恢复 API 校验通过，见 `docs/evidence/g10/recovery-restore-uat-2026-08-16.json`。这些结果增强 NFR-PERF/REL 证据，但不宣称 Windows 硬件 p95、断电或最终发布通过。
## 2026-08-16 核心链三视口隔离浏览器 UAT

在生产 SQLite/项目树的只读快照上，用真实 FastAPI 与 React 页面完成 1440×900、1280×800、1024×768 三档核心链浏览器 UAT：项目健康、全局搜索、生成预检、审核收件箱、时间线与本地交付历史均可读取；只产生 GET，未请求原始媒体，无公网请求、控制台错误、页面错误或横向溢出。证据 `docs/evidence/g10/core-chain-browser-readonly-uat-2026-08-16.json` 为 PASS，脚本 `tests/e2e/core_chain_browser_readonly.spec.ts`；该证据仍是隔离只读 UAT，不替代真实生成、人工批准和正式发布签字。

## 2026-08-16 FR-PRJ-004 / TC-DOM-003/004 Windows 隔离 UAT

新增 `scripts/project_crud_windows_uat.py`，在工作区下全新 Unicode/空格路径创建迁移至当前 head 的 SQLite 与项目根，实际调用 `ProjectService` 创建 2 季/6 集、2 个项目级母本场次并绑定 episode source range、3 个镜头及 ShotRevision；随后启动真实 loopback FastAPI 进程读取 seasons/episodes/scenes/shots，并通过 session bootstrap token 调用 Episode reorder。观测结果：Episode display_order 归一化且 UUID/code 不变，过期 `expected_revision` 在服务层和 HTTP API 均返回 409 且不覆盖当前顺序；Storyboard batch 反转镜头顺序但 shot UUID/current_revision_id 与历史引用保持不变；并发重排对照测试已补到 `fr-prj-004-windows-uat-2026-08-17-concurrency-pass.json`（1 成功 1 冲突）。总状态保持 `PARTIAL`：本次不宣称生产库、浏览器三视口、子实体删除 CRUD 或生产规模签字已完成。`apps/api/tests/test_project_commands.py` 新增 episode sibling normalization 与 stale conflict 回归；`FR-PRJ-004`、`TC-DOM-003`、`TC-DOM-004` 在 master map 中均登记为 PARTIAL。

## 2026-08-16 真实 H3 FL2VA I2V 隔离运行

在已批准的 SHOT_001 关键帧和用户本机模型引用上，以真实 MiniMaxH3ImageToVideo 节点完成一次 10-step、480×832、124 帧的本地 I2V；ComfyUI 监听 `127.0.0.1:8190`，输出写入 F 盘 `work/comfy-production/output` 隔离目录。队列提交、执行成功、H.264/AAC 产物、24fps/5.167s、SHA-256 与 ffprobe 均已留证，模型未打包/上传，生产 DB 未接触。证据 `docs/evidence/g10/h3-local-i2v-uat-2026-08-16.json` 保持 `PARTIAL`：这是运行时真实产物，不等同于 LocalDramaStudio 正式 Job/MediaVersion 登记；正式 machine QC、人工审核、选择与交付，以及长时间稳定性/许可证复核仍需后续闭环。

同一真实 MP4 随后在临时 SQLite/项目根中跑通平台正式媒体链：`MediaService.import_file(stage=FORMAL)`、`g6_formal_video_qc_v1` ffprobe 机器检查、`formal_video` 模板人工批准、FORMAL_SELECTION 预检与提交均成功，数据库 integrity=ok；新增 `scripts/h3_formal_pipeline_uat.py`、`test_formal_selection_commit.py`，证据 `docs/evidence/g10/h3-formal-pipeline-uat-2026-08-16.json` 仍为 `PARTIAL`，因为未把该隔离对象推进整集交付包/下载审计。期间发现并修复正式选择提交的 SQLite 参数绑定错误，避免真实批准链在最终写选择行时失败。

## 2026-08-16 LocalDramaStudio Comfy 平台 Job UAT 阻塞

新增 `scripts/h3_comfy_job_uat.py`，实际经过 `WorkflowService` 注册/验证/发布、`JobService` claim 和 `ComfyGenerationService.submit_next`，把关键帧按平台输入根物化后向 loopback Comfy 提交 `H3WorkflowFactory.build_fl2va`。受控 Comfy 进程只启用 `ComfyUI_RH_MinMaxH3` allow-list；提示已接受，但 RH `load_h3_model` 在 Windows 本机模型加载阶段触发 `WindowsAccessViolation`，进程退出且没有生成 Artifact。证据 `docs/evidence/g10/h3-comfy-job-uat-2026-08-16.json` 明确标记 `BLOCKED`，不把外部 native 节点生成或独立正式媒体链误报为平台 Job 成功；生产数据库/项目树、公网均未接触。使用 `scripts/comfy.ps1` 的显式诊断旗标（仅允许 VRAM/allocator/precision 诊断选项）仍可复现；独立 CPU/CPU-offload 与 CUDA/CPU-offload loader 可读，问题聚焦完整 Comfy + Qwen CUDA residency/大 DiT 组合。另新增 `ComfyGenerationService` runtime-loss 收口：loopback 运行时丢失会写脱敏 `COMFY_RUNTIME_UNAVAILABLE`、释放 lease 并按 bounded retry 保持 Job `QUEUED`，回归见 `apps/api/tests/test_comfy_jobs.py::test_poll_closes_attempt_when_comfy_runtime_dies`。待 RH 运行时资源/模型加载问题修复后重跑，FR-GEN/VID/WFL 相关条目继续保持 `PARTIAL`。

## 2026-08-16 继续执行：PARTIAL 冲刺清单（并行优先）

- 目标是优先把可验证范围先闭环，不改动 H3/Comfy 外部运行时事实边界：
  - 代理 A（无外部 runtime）：
    - FR-PRJ-004（子实体重排边界）：补充“删除/生产规模”在隔离环境的真实 UAT；可复用 `fr-prj-004-windows-uat-2026-08-17-concurrency-pass.json`。
    - FR-DEL-001..004：补充正式三视口只读与签字流程覆盖路径；当前有本地 delivery 与篡改/withdraw 证据，但未补正式读链。
    - G6 四条真实代理 take（selection/review）：当前仅到达产物与 QC 成功，待 review/签字正式闭环并对齐正式签字流程。
    - TC-DOM-007：补齐“全对象类型 stale”与规模场景，先不动模型 runtime。
    - TC-VAR-013 / TC-VAR-004：补齐真实 `new Job` 与双 profile/retry 路径的生产尺度验证。
    - FR-IMG-002 / FR-IMG-003：补齐真实用户 Profile 场景下的批量生成与三视口图片网格对比验收。
  - 代理 B（外部接口依赖）
    - FR-PRV（H3 候选 truthfulness）与 G6 相关 runtime 项：保持 BLOCKED；不允许伪造产物，需等待 Comfy/RH runtime 修复后复测。
    - FR-AUD-001 / FR-AUD-002：外部 Profile/授权/媒体链路补齐后补齐正式候选与选择链路。
- 共享门禁：
  - 任何新证据不得将 `public_network_contacted` 置真，不得触及 production db 文件或 `projects_root`。
  - 产物仅记录与现有脚本或 API 真实调用，文档中只下调 `PARTIAL` 或新增 `BLOCKED`，不做越权闭环。

- 本阶段已完成事实快照：`master_requirements_audit.py` 与 `release_audit.py` 均通过（`release_fr=84, nfr=15, tc=85`）；`docs/evidence/g10/master-requirements-closure.json` 与 `docs/evidence/g10/full-chain-local-uat-2026-08-16.json` 为 `PASS`。

- 补充：本节“PARTIAL”条目已按“证据已达技术闭环 + 正式签字/三视口待补”进行口径标注，且大部分条目在 `master-requirements-map.json` 已登记为 PASS；请以 `PARTIAL` 下方的 `... pending` 标签作为执行优先级来源，避免将映射层 PASS 与正式签字状态混淆。

## 2026-08-17 上线冲刺收口（本窗口）

本窗口目标：在不依赖 H3/Comfy 外部运行时、且由产品负责人自行运行 ComfyUI 的前提下，把所有可自行闭环的收尾全部完成并统一发布决策。

已完成：

- **门禁全绿**：`scripts/check.ps1` 真实退出 0。API `288 passed / 4 Comfy live deselected`；Ruff 115 source files；mypy PASS；maintainability audit PASS；Web production build PASS；Web `36 files / 100 tests` PASS。
- **core-chain 三视口浏览器 UAT 扩至 6 视图**：`tests/e2e/core_chain_browser_readonly.spec.ts` 在生产 SQLite/项目树只读快照（`scripts/serve_isolated_core_browser_uat.py`，127.0.0.1:3222）上覆盖 `projects / generation / reviews / jobs / diagnostics` 五个视图 + 时间线/交付读断言，1440×900、1280×800、1024×768 三档 3/3 PASS：零写入、零公网、零原媒体、零 console/page error、零失败响应、零水平溢出。证据 `docs/evidence/g10/core-chain-browser-readonly-uat-2026-08-16.json`（`status=PASS`、scope 含 review inbox / timeline delivery / jobs capacity / diagnostics audit）。该证据为 FR-PRJ-*、FR-WRT-*、FR-IMG-*、FR-REV-*、FR-AUD-*、FR-DEL-001..004、FR-JOB-001..005、FR-AUT-001/002、FR-AUDT-001、FR-OPS-001..003、FR-WFL-004 与 NFR-* 的读路径提供了正式三视口生产快照 UAT 覆盖。
- **修复审核页/对白页自动拉取原媒体**：`ReviewInboxPanel` 视频预览与同步比较、`DialogueTTSPanel` 试听 `<audio>` 由 `preload="metadata"` 改为 `preload="none"`，消除“进入审核视图即自动请求原视频/原音频”的违规；原媒体只在用户点击播放后才按需 Range 读取，与 EpisodeReviewPanel/AudioTrackPanel 的既定策略一致。`video_annotations` 与 `dialogue_tts_governance` 两套三视口 e2e 复跑 6/6 PASS，Web vitest 100/100 PASS。
- **工作树卫生**：`.gitignore` 增补历次隔离 UAT 遗留工作根目录（`数据 SQLite/`、`data sqlite/`、`projects root/`、`work area/`、`tmp_obs/`、`$root/`），未入库临时目录不再进入提交。
- **Ruff 修复**：`dom007_stale_windows_uat.py`、`project_crud_windows_uat.py`、`tc_var013_004_windows_uat.py` 的 import 问题已修复，门禁恢复全绿。

仍未闭环且不依赖平台代码的边界（按产品负责人指示保持现状，不伪造证据）：

- H3/Comfy runtime：`h3-comfy-job-uat` 保持 `BLOCKED`（RH `load_h3_model` WindowsAccessViolation）；产品负责人自行运行 ComfyUI，后续 runtime 恢复后可重跑 `scripts/h3_comfy_job_uat.py` 并推进 FR-PRV/FR-GEN/FR-VID/FR-WFL 的真实平台 Job 闭环。
- FR-AUD-001/002：正式项目内真实授权音色、Published TTS Profile、试听/QC/人工审核/选择闭环仍为数据链缺失（隔离 SAPI 链路已 PASS，见 `dialogue-tts-windows-uat-2026-08-16.json`）。
- FR-IMG-002：真实用户 Profile/Runtime 批量图片生成待用户本机 Profile 就绪后执行。
- FR-DEL-001..004：交付链三视口读链已覆盖，最终交付包签字留待实际生产内容产生后执行。

## 2026-08-17 第二轮：H3 原生链打通 + 真实数据三视口 UAT

本窗口在用户授权下继续推进，三项关键突破：

1. **H3 平台 Job 链改用原生 comfy_extras 节点链并真实跑通**：RH 插件族在本机被精确复现为崩溃源（`comfy-production.stderr.log` 定位到 `ComfyUI_RH_MinMaxH3/.../qwen_encoder/encoder.py:321 _unload_linear_patcher` 在 Qwen INT8 unpatch/offload 时 Windows access violation）。按用户指示弃用 RH，`H3WorkflowFactory` 改为编译本机已验证的 ComfyUI core/comfy_extras 原生链（`UNETLoader/CLIPLoader/VAELoader + MiniMaxH3ImageToVideo + BasicScheduler/BasicGuider/SamplerCustomAdvanced/VAEDecode/VAEDecodeAudio/CreateVideo/SaveVideo`，openclaw 实测参数：480×832、帧数 17k+5 网格、res_multistep/simple、denoise 1.0、模型名取自 `model_manifest.json` loader_assets）。`scripts/comfy.ps1` 不再白名单 RH。真实运行 `scripts/h3_comfy_job_uat.py`（证据 `docs/evidence/g10/h3-comfy-job-uat-2026-08-17i.json`，status=PARTIAL 成功路径）：平台 Job → 真实 MP4 artifact（1,018,259B，h264 480×832 24fps 5167ms）→ 晋升 MediaVersion → machine QC PASS → 人工 APPROVED → FORMAL_SELECTION COMMITTED，全程隔离零公网。`@comfyui` 实时测试（含原生工作流 validate/publish）全绿。
2. **图片候选三视口 UAT**：`scripts/serve_image_review_uat.py` + `tests/e2e/image_review_windows_uat.spec.ts`，隔离库种子两个真实 PNG PROXY 候选，图片网格/A-B 缩略图比较/键盘导航 1440×900/1280×800/1024×768 3/3 PASS，零写入/零原片/零错误（证据 `fr-img-002-006-image-review-windows-uat-2026-08-17.json`，覆盖 FR-IMG-002..006 三视口）。
3. **TTS 数据链三视口 UAT**：`scripts/serve_tts_review_uat.py` + `tests/e2e/tts_review_windows_uat.spec.ts`，隔离库种子真实 Windows SAPI 合成 WAV（275,762B）、USER_OWNED 音色授权、Published TTS Profile、FORMAL 候选；对白/TTS 面板三视口 3/3 PASS，试听 `preload=none` 零自动原音频（证据 `fr-aud-001-002-tts-review-windows-uat-2026-08-17.json`，覆盖 FR-AUD-001/002 三视口）。

边界保持：正式生产项目内的真实 H3 生成产物登记、FR-AUD 正式项目数据链与最终交付包签字仍属使用期事项；G6 四条同关键帧 take + winner + formal branch 已有权威退出证据（`G6_exit_report.md`）。
