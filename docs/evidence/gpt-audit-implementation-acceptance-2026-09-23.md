# 审查文档实施验收记录（2026-09-23）

对应两份审查文档：

- `D:\kairo\docs\gpt\LocalDramaStudio_20260922_Review.md`
- `D:\kairo\docs\gpt\Local_Drama_Studio_20260922_审查与实施方案.md`

基线：`f1251e1aed7d5b1a8f00d55b2c824cb6a45b7d41`（两份文档的审计快照）。
本记录只写**实际执行过的测试结果**；未取得证据的能力按审查文档第 705 行的要求记为
**BLOCKED**，不写成通过。

验收方式：每一项都配真实回归测试（真实 FFmpeg / 真实 SQLite+API / 真实解析器 /
官方 OpenTimelineIO 0.18.1），并配**因果对照（negative control）**——把修复移除后
该测试必须失败。对照结果写在每项的“负控”一栏。

---

## 1. 合成器与时间线

| 条目 | 实施 | 真实测试 | 负控 |
| --- | --- | --- | --- |
| TM-03 叠化音画同步 | 音频改用 `acrossfade=d=<转换窗>`，与 `xfade` 同窗；末尾 `atrim` 降级为取整保护 | `test_timeline_transition_audio_sync.py`：真实 FFmpeg 渲染三色三频成片，按解码 PCM 测音调归属（红 440 → 蓝 800 → 绿 1500），并断言末尾窗口承载第三段音调 | 改回音频 `concat`：末尾绿音由 **-21.1 dB 掉到 -48.3 dB**，蓝色串到 -21.6 dB（与审查报告一致） |
| TM-04 冻结转场计划 | 计划写入 `transitions[{duration_frames,offset_frames}]` 与 `expected_video_frames`；渲染器只读该表；登记前用 ffprobe 帧数/时长/AAC 有效时长校验，不符抛 `RENDER_FRAME_COUNT_MISMATCH` 并删除产物 | `test_timeline_frozen_transition_plan.py`：2/0.5/2 秒案例，断言计划 96 帧、执行转场逐条等于计划、真实编码帧数一致；另有篡改计划后的拒绝测试 | 让渲染器按累计长度重新推导：真实渲染 3.750 秒 / 90 帧与计划 96 帧不符 → 被 `RENDER_FRAME_COUNT_MISMATCH` 拒绝（旧快照会登记 VERIFIED） |
| TM-05 帧率优先级 | `_episode` 补选项目 fps；优先级 交付规格 → 项目配置 → 素材推断；记录 `fps_source`；保持有理数 | `test_timeline_frozen_transition_plan.py`：项目 24 / 首素材 30 → 输出 24；项目 30 / 首素材 24 → 输出 30；30000/1001 全程有理数 | 旧代码推断首素材帧率（30），项目 24 的断言失败 |
| TM-06 OTIO / 剪映保真 | 相邻 clip 间写入真正的 `otio.schema.Transition`（`SMPTE_Dissolve` + 有理数 handles），并按同窗裁剪相邻 clip range；按 writer 分别声明能力；剪映如实上报 `TRANSITION_DISSOLVE` 损失；`writer_version` 纳入 export hash | `test_timeline_export_transition_fidelity.py`：用**官方 OpenTimelineIO 0.18.1** 解析 `.otio`，断言 `[Clip, Transition, Clip]`、in/out offset、时长等于成片时长；`test_export_loss_report.py` 断言剪映逐格式 loss | 改回 `LinearTimeWarp` 写法：官方解析器直接报 `type mismatch ... expected Composable, found LinearTimeWarp` |
| TM-01 / TM-02 | 首份时间线保存、运行中暂停后恢复 | 既有 `test_edit_v2.py` / `test_job_state_machine_authority.py` 全绿 | — |
| TM-07~10 合成器模块 | `slices.py` 跨块源偏移、多轨旁白、显式黑场、`classify_process_failure` | `test_ffmpeg_renderer_contracts.py`（21 项，真实 FFmpeg） | 逐项负控 |

## 2. 创建、来源与命令接线

| 条目 | 实施 | 测试 |
| --- | --- | --- |
| LDS-02/03/04、EXP-03 | 创建命令 + 幂等凭据在同一事务；粘贴文本独立记录 | `test_explainer_creation_commands.py`（22 项） |
| LDS-01/13、EXP-01/02/05/06/08 | 阶段命令统一走既有 jobs（未注册阶段返回 `CAPABILITY_UNAVAILABLE` 且零写入） | `test_explainer_stage_commands.py`、`test_explainer_run_submission.py` |
| FE-A02 | 幂等键绑定完整请求体 | `CreatePage.idempotency.test.tsx`（2 项） | 只 hash `title:inputKind:targetSeconds`：修正主题后键不轮换，测试失败 |
| FE-A03 | 渲染 BLOCKED 不再自动确认 | `ReviewPage` 流程测试 + `test_explainer_delivery_scope.py` |

## 3. 作用域与读模型

| 条目 | 实施 | 测试 |
| --- | --- | --- |
| LDS-07/08 | 列表 keyset 分页（HMAC 签名游标）、范围隔离 | `test_explainer_listing_pagination.py`、`test_explainer_delivery_scope.py` |
| LDS-10、FE-A07/A08 | 旁白读模型按段落状态；配音语言与字幕语言分离 | `test_explainer_narration_read_model.py`、`AudioPage` 测试 |
| FE-A05 | 审片决策主体 = 当前渲染 id/hash + 已审帧区间 | `ReviewPage` 测试 |

## 4. 真实审片

| 条目 | 实施 | 测试 |
| --- | --- | --- |
| LDS-09/15、FE-A04/A06/A09/A11 | 真实 `<video controls>` 播放器、manifest 时间线、issue 定位与全量展开、分镜反馈、事实证据与修正闭环 | `media.test.tsx`、`StoryboardPage.feedback.test.tsx`、`test_explainer_claim_evidence.py`；<br>本轮修复新播放器违反仓库 media URL 策略（`preload="none"` + thumbnail poster），`media-url-policy.test.js` 恢复通过 |
| FE-A11 后端 | 新增 `GET /explainers/{project_id}/claims/{claim_id}/evidence`，返回冻结 span、来源与偏移 | `test_explainer_claim_evidence.py`（3 项：有证据 / 无证据 / 跨作品拒绝） |

## 5. 草稿与异步

| 条目 | 实施 | 测试 | 负控 |
| --- | --- | --- | --- |
| LDS-11/12、FE-A01 | 段落草稿注册表、提交冻结快照 | `ScriptPage.draft.test.tsx`、`OneClickPipelineWorkbench.drafts.test.tsx` | — |
| PR-02、PR-06 | `cancel_requested_at` 消费语义；outbox 逐次 claim token（迁移 `0103`） | `test_job_state_machine_authority.py`、`test_outbox_claim_ownership.py` | 移除 token：旧 owner 可释放/确认新 owner 的发送 |
| PR-01 | 输入窗口按 `unit_number` 聚合为分集；质量报告新增编号唯一/窗口映射 BLOCKER | `test_pipeline_orchestrator.py`：短章 + 31,300 字长章 → 计划恰为 `[1, 2]`，应用成功且正式库无重复编号 | 关闭聚合：计划变成 `[1, 2, 2]`，被 `PIPELINE_PLAN_UNIT_DUPLICATED` 拒绝 |
| PR-04 | 幂等重放读取 Job **实时**状态；失败批次经授权在同一事务重试；RUNNING 投影按 Job 终态对账 | `test_pipeline_orchestrator.py`：失败续接后再次续接 → `RETRIED_FAILED_BATCH`、运行回到 RUNNING；另测死 Job 对账后可正常重试 | 关闭实时状态与重试：第二次续接返回 `SUBMITTED`，批次未重新入队 |
| PR-03 | 续接把前批 compact digest 送入全剧综合，并合并前批资产与连续性事实 | `test_story_pipeline_ai.py`：第二批结果包含首批 `角色1` 与两批事实 | 移除前批输入与合并：`assets.characters` 只剩 `角色2`（与审查报告一致） |
| PR-05 | 应用水位改为 `applied_revision_hash` + `applied_episode_numbers`（迁移 `0104`）；已应用分集标题/范围不被改写；预先存在的 APPLIED 运行回填水位 | `test_pipeline_orchestrator.py`：首批应用→续接→差量预览→应用成功，已生产分集标题保持；同 revision 重复应用返回 `PIPELINE_ALREADY_APPLIED`；旧 APPLIED 运行被回填且不再可应用 | 恢复 run 级布尔：差量预览返回 `PIPELINE_STATE_INVALID`（与审查报告一致） |
| PR-05 自动授权 | 续接发布的**新 revision** 拿到自己的依赖 Job（`pipeline-apply:{run_id}:{revision_hash}`），并在同一事务内把授权重新绑定到该 revision | `test_pipeline_orchestrator.py::test_auto_authorized_continuation_gets_its_own_apply_job_for_the_new_revision` | 不创建新 Job：新 revision 复用首批 job id，测试失败 |
| PR-07 | 工作台接入服务端 `analysis_cursor`，提交服务端游标并显示真实 `job_state` | `OneClickPipelineWorkbench.continue.test.tsx`（2 项） | 隐藏入口按钮：测试失败 |

## 6. 性能、视觉与包

| 条目 | 实施 | 测试 |
| --- | --- | --- |
| EXP-07、LDS-14/16/17 | 列表聚合与分页、加载/空态/错误态收敛 | `test_explainer_listing_pagination.py`、前端页面测试 |
| S1 | 只读 readiness 使用 `mode=ro` URI，数据目录含 `#` 不再读错库 | `test_readiness_path_contracts.py` |
| S2 / S3 | Host 信任检查覆盖所有方法且不回显 Host | `test_startup_and_host_boundaries.py`（21 项）；`test_server_deployment.py` 按新边界修正断言（未放宽策略） |
| IMP-01 | EPUB 遍历改为 ENTER/TAIL/EXIT 事件脚本 | `test_document_extraction_contracts.py` |
| IMP-02 | 超限返回 `DOCUMENT_STRUCTURE_TOO_COMPLEX`，不再截断后当完整原稿 | `test_document_extraction_contracts.py` |
| IMP-04 | 抽取文本按 request 唯一短名 staging；失败分支只删自己的 partial | `test_document_import_concurrency_contract.py`；负控：改回 32 位 UUID 后，深层项目根路径超 Windows 260 字符导致 `FileNotFoundError` |
| PKG-01 | 原稿 metadata 的媒体身份随 `media_version_map` 重写，保留 `source_media_version_id` | `test_project_package_manuscript_ownership.py` |
| PKG-02 | 新增 EXPLAINER 领域适配器：`localdrama.project-state.explainer.v1` 状态、`_import_explainer_copy()` 按新身份重建整图并重写 JSON 引用、副本不带假季/集、媒体标记 `UNVERIFIED`、运行以 `IMPORTED_HISTORY` 终态保留、逐领域 `included/excluded/incomplete` 报告；无适配器的产品类型仍拒绝 | `test_project_package_product_kind.py`（8 项，含跨库往返） | 绕过 explainer 导入分支：往返测试以 `PROJECT_NOT_FOUND` 失败 |
| PKG-03 | 流式导出声明 `info.file_size` + `force_zip64` | `test_project_package_zip64_contract.py`；负控：去掉 `info.file_size` 触发 `RuntimeError: File size too large` |

---

## 7. 汇总测试结果（本轮实际执行）

| 范围 | 结果 |
| --- | --- |
| 前端（`vitest run`，全部，2026-09-23 16:30） | **168 文件 / 784 测试 通过**；`tsc --noEmit` 退出码 0 |
| **后端全量 `pytest apps/api/tests`（2026-09-23 16:26:38 → 18:00，实跑一次不中断）** | **2379 项：2377 通过 / 2 skipped / 0 failed，退出码 0**（负控：同一命令在本轮修复前把短摘要写成 24 项失败，含 ref2va 7、one_sentence 4、workflow_release_contracts 4、openapi 1、architecture-debt 1、g0 1、production-session-runner 1、one_click 1、h3_runtime 1、variant_submission 1、visual_lab 1、workflow_definitions 1；根因见 §9.1） |
| `scripts/g0_validate.py`（真实仓库、portable 模式，2026-09-23 17:0x） | **g0_status=PASS**（REQUIRED_COMPONENTS / SCHEMA_VERSIONS / API_CONTRACT_VERSION / GENERATED_ARTIFACTS / MIGRATION_HEAD / QUALITY_GATES 全 PASS，退出码 0） |
| `H3WorkflowFactory.runtime_layout()`（新清单） | status=**PASS**，`missing_model_files=[]`，`ref2va_supported=True` |
| 升级后 ComfyUI v0.37.1 真实出片 | prompt `812b7f9a-6e45-468c-9408-cf74bee4ebc6` → `status_str=success`，执行 205.7 s，产出 `deliverables/h3-first-film/h3_first_film_00002_.mp4` |
| 后端本次触及的全部套件（包 + 流水线 + 事实证据 + 迁移契约 + 部署边界 + 时间线 + 导出 + 文档 + 任务状态） | 通过（已包含在上面的全量结果内） |

> 免责：上面的全量结果是一次性实跑（未拆分、未重跑失败项）。`test_bkt03_g0_validate_portable.py`
> 与 `docs/release/g0-contract.json` 在全量跑启动**之后**又改过一次（迁移头契约修正 + 新增断言），
> 该文件随后单独复跑 **20 项全通过**；其余文件在全量跑期间未再改动。

## 8. 明确 BLOCKED（无证据，不写成通过）

| 条目 | 原因 |
| --- | --- |
| 真实 GPU 生成质量与吞吐基准（跨能力批量压测、画质评估） | 本轮已用升级后的 ComfyUI v0.37.1 真实跑通 H3 视频链路并产出 480×832/107 帧片段（§9.3），但没有做多能力批量压测、显存峰值/吞吐曲线或画质评分；这些仍无证据 |
| 剪映桌面端真实导入验收 | 未在真实目标客户端导入过；writer 保持 best-effort，并把转场记为逐格式 loss，不声称保真 |
| > 2 GiB 真实项目包往返 | ZIP64 由强制阈值测试覆盖，未做真实多 GB 字节的往返 |
| 跨进程并发导入 | 由 staging 唯一命名与并发契约测试覆盖，未做真实多进程竞争 |
| 浏览器端真实录屏 | 审查环境本身阻断浏览器访问本地 HTTP（审查报告第 46 行）；本轮未取得 UAT 截图 |

---

## 9. 复审轮补充（2026-09-23 下午：ComfyUI 升级 + 本机能力清单 + 由此暴露的真实缺陷）

### 9.1 本轮修复的缺陷（都有真实复现）

| 缺陷 | 复现证据 | 修复 |
| --- | --- | --- |
| **测试污染本机模型清单** | `test_startup_and_host_boundaries.py::_app_client` 直接写 `settings.manifest_path`；conftest 的 `Settings` 未覆盖 `release_root`，仓库内无 `model_manifest.json`，于是 `manifest_path` 落到 `F:\AI_Projects\h3\model_manifest.json`。2026-09-23 16:00:29 该用例把本机清单覆盖成 413 B fixture 存根（`models.partitions={}`、`h3_capabilities={}`），全量套件中所有依赖 profile 的用例随之失败 | `_app_client(workspace, tmp_path, monkeypatch, manifest)` 改为 `model_manifest_override` 指向 `tmp_path` 下的独立文件，不再写本机清单 |
| **迁移图校验不认带注解的 Alembic 声明** | `scripts/g0_validate_core.py::read_migration_graph` 只处理 `ast.Assign`；`0103_outbox_claim_token.py`/`0104_pipeline_apply_watermark.py` 用 Alembic 现行模板的 `revision: str = "..."`（`AnnAssign`），CLI 以 `... declares no revision identifier` 直接退出，扩展审计（`EXTERNAL_BLUEPRINT_AUDIT`）永远跑不到 | 同时接受 `ast.Assign` 与 `ast.AnnAssign`；负控：改回只认 `Assign` → `test_extended_audit_cli_reports_not_configured_for_absent_inputs` 再次失败 |
| **生产会话「异常闸门」用例时间夹具错误** | 该用例把 `started_at` 写死 `2026-09-21T00:00:00Z`，而 `max_duration_seconds` 默认 86400；实测 `usage.elapsed_seconds=203128` → 两个分集都先撞 `PRODUCTION_SESSION_DURATION_BUDGET_EXHAUSTED`（`stage=BUDGET_WAIT`），被测的异常闸门分支根本没执行（`dispatched=[]`） | 夹具改为真实“刚开始”的时间戳，并注明预算来源；生产代码未放宽 |
| **生成客户端缺 FE-A11 端点封装** | `ScriptPage.tsx` 引用 `getExplainerClaimEvidence`，而 `scripts/generate_client.py` 模板没有它 → `tsc --noEmit` 报 `TS2305` / `TS7006`（即审计实现自身引入的前端类型缺口） | 在模板中新增 `ExplainerClaimEvidenceSpan`/`ExplainerClaimEvidence` 类型与 `getExplainerClaimEvidence()`，重新生成 `docs/openapi/openapi.json` 与 `apps/web/src/generated/api.ts`；`tsc --noEmit` 恢复 0 错误 |
| **应用层新增跨服务构造** | 审计实现把 `ProjectService`/`ExplainerProductionService`/`JobService`/`ExplainerCreationService` 直接构造在 `application/explainers/*`，`test_architecture_debt_manifest.py::test_legacy_architecture_debt_does_not_grow` 新增 `concrete_database_dependencies` / `cross_service_construction` 条目后失败 | 改为注入端口（`DatabaseUnitOfWork`）+ 组合根 `build_explainer_creation_service()` / `build_explainers_command_service()`；路由、`runtime_adapters`、测试全部改走组合根；**未放宽** `scripts/audit_architecture_debt.py` 基线 |
| **G0 契约声明的迁移头落后于真实图** | `docs/release/g0-contract.json` 仍声明 `expected_heads=["0102_explainer_factory_foundation"]`，而 `0103`/`0104` 已落地；`scripts/g0_validate.py` 在干净仓库上直接 `MIGRATION_HEAD=FAIL`（`reachable alembic heads are ['0104_pipeline_apply_watermark']`） | 契约更新为 `0104_pipeline_apply_watermark`；`test_committed_spec_covers_the_real_repository_components` 增加 `check_migration_head == PASS` 断言，防止再次漂移。实测 `g0_status=PASS`（6 项检查全 PASS，exit 0） |

### 9.2 本机模型清单重建（全部为真实探测值）

`F:\AI_Projects\h3\model_manifest.json`（生成脚本 `scripts/_build_model_manifest.py` + `scripts/_build_manifest_nodes.py`）：

- `canonical_model_root` = `F:\AI_Models\LocalDramaStudio\ComfyUI`（本机真实模型根：MiniMax-H3 / Qwen-Image / ACE-Step …）
- `models.partitions.minimax_h3`：6 个组件的字节数与 SHA-256 由**当前文件内容**计算（fl2va 19.53 GB / ref2va 19.53 GB / text encoder 14.61 GB / video VAE 4.85 GB / audio VAE 0.56 GB / turbo LoRA 0.73 GB）
- `models.partitions.qwen_image`：Qwen-Image 2.1 `qwen_image_2.1_int8_convrot` + `qwen3vl_8b_int8_convrot` + `qwen_image_2.1_vae_bf16`
- `runtime.gpu`：`nvidia-smi` 实测 `NVIDIA GeForce RTX 3090 Ti` / `25757220864 B` / driver `610.62`（此前缺失该段，capability 预检报 `GPU_CAPACITY_UNAVAILABLE`）
- `authoritative_current_state.loader_assets`：`H3WorkflowFactory.loader_assets()` 直接读取的 6 个文件名
- `h3_capabilities`：`VIDEO_T2V`、`VIDEO_I2V`、`VIDEO_FIRST_LAST_FRAME`、`VIDEO_REFERENCE`、`AUDIO_SFX`、`IMAGE_CONCEPT`；`IMAGE_CONCEPT` 的 `required_nodes` 与 `workflow_sha256` 取自 `work/workflow_packages/QWEN_IMAGE_21_T2I_CONCEPT/v1.json` 的真实内容
- `nodes`：318 个可信节点类 + 各插件 `__init__.py` 的真实 SHA-256

清单自校验（真实执行）：`H3WorkflowFactory.runtime_layout()` → **PASS**（6 个 loader 资产在 canonical root 全部解析，`missing_model_files=[]`），`ref2va_supported=True`。

### 9.3 ComfyUI 升级与真实产物

- 版本：**v0.33.1 → v0.37.1**（tag `3f767e7f67bc587e88d6de6668eb424f725d4649`，2026-09-22 19:12:39 UTC）
- 运行中 `/system_stats`：`comfyui_version=0.37.1`、Python 3.13.12、torch 2.10.0+cu130、CUDA 可用、RTX 3090 Ti
- 一致性核对：tag 内 1216 个受版本控制文件在当前工作树中**全部存在**（missing=0）；`git describe --tags` 现为 `v0.37.1`（此前 HEAD 仍指向 v0.33.1，属“只更新文件未更新 HEAD”的状态，已纠正）
- v0.37.1 schema 适配：`SaveVideo` 移除 `codec` 入参、`format=mp4`；H3 采样器要求 `480×832`（480×854 / 854×480 会触发 `shape '[1, 24, 1, 1, 26, 2, 15, 2]' is invalid`）
- 真实产物（升级后重新渲染，2026-09-23 16:33）：`deliverables/h3-first-film/h3_first_film_00002_.mp4`
  → 480×832 / 24 fps / **107 帧** / 4.458 s / H.264 + AAC 32 kHz 立体声；`mean_volume=-14.0 dB`、`max_volume=-2.8 dB`；抽第 1/54/107 帧，字节数 173176 / 247470 / 304083（画面真实变化，非静止/黑帧）

## 10. 交付物（本轮可直接查看的成片）

| 文件 | 说明 | 实测 |
| --- | --- | --- |
| `deliverables/luna_iab_EPISODE_001_成片.mp4` | 本机既有项目 `luna_iab_quan_liu_cheng_yan_shou_20260914` 的 EPISODE_001 正式成片（冻结时间线 revision 6 / 12 items，交付包内文件原样复制） | 854×480 / 24 fps / **120.000 s** / H.264 + AAC 48 kHz 立体声 / 10.89 MB |
| `deliverables/luna_iab_EPISODE_001_字幕.srt` | 随成片交付的字幕 | — |
| `deliverables/luna_iab_EPISODE_001_manifest.json` | 交付清单（sha256/字节数） | — |
| `deliverables/h3-first-film/h3_first_film_00002_.mp4` | 升级后 ComfyUI v0.37.1 + 新清单上的真实 H3 首片 | 见 9.3 |
| `deliverables/h3-first-film/h3_smoke_00001_.mp4` | 64×64 / 5 帧冒烟片段（仅用于链路验证） | — |
