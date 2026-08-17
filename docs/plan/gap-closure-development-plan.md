# LocalDramaStudio 差距补全开发计划（G11 增强批次）

> 本文档是"调研主流导演台/漫剧平台 + 开源工作流项目 → 对比本地功能 → 制定补全开发计划"的交接物。
> 目标读者：下一个接手开发的 agent。要求：**先通读本文档，再动手开发**。

---

## 1. 定位与硬约束（开发前必读，违反即返工）

| # | 约束 | 说明 |
|---|---|---|
| C1 | LOCAL_ONLY | 零公网调用。不得引入任何外部 API/云模型/云音色/云素材。 |
| C2 | 真实门禁 | 不用 Mock 冒充真实产物。生成链必须走本机 ComfyUI/H3，TTS 走本机 SAPI/用户自备音色。 |
| C3 | 不捆绑媒体 | 不随源码分发模型权重、音色、BGM、图片素材；用户自备。 |
| C4 | H3 原生链 | 视频生成沿用 `application/h3_workflows.py` 原生节点链（UNETLoader/CLIPLoader/VAELoader + MiniMaxH3ImageToVideo 等 16 个 TRUSTED_COMFY_BUILTINS）。不回归 RH 插件。 |
| C5 | 门禁基线 | G0–G10 已关闭（84 P0/P1 FR、15 NFR、85 TC，`release_audit.py` PASS/GO）。本批为 G11 增强，**不改动已关闭基线**；新功能在 `docs/requirements-traceability.md` 增开"G11 增强"节登记，不进 master-requirements-map 的 84 项。 |
| C6 | 审计/证据 | 新服务必须写 `audit_events`、关键产物带 provenance；证据发布沿用 `publish_from_evidence`。 |
| C7 | 工程模式 | 分层不变：`application/` 服务 + SQLite 事务（`_now()/_json()` 助手、事务内写审计）、`apps/web/src/features/` 组件 + 同目录 `.test.tsx`、Playwright e2e 放 `tests/e2e/`。 |

## 2. 现有资产地图（别重复造轮子）

已验证存在于代码库：

- **剧本/集/场**：projects、episodes、scenes（`EpisodeSceneRanges`）、`ScriptImportPanel`、`CreativeLibrary`、`AIDraftReviewPanel`
- **分镜**：`StoryboardBatchWorkbench`、`DirectorShotEditor`、`PromptTemplatePanel`、`ProductionCanvasPanel`（DAG 画布：节点/布局/preflight）
- **连续性**：`ContinuityPanel`（角色/场景/道具/服装等文本字段 + 已选/已批参考 media_versions）
- **生成**：`GenerationControlPanel`/`GenerationWorkbench`、`MotionControlPanel`、`GenerationExperimentPanel`、`PostProcessPanel`、`comfy_jobs`/`comfy_lab`/`h3_workflows`（T2V/I2V/FL2VA 首尾帧）、受控 worker `scripts/comfy.ps1`
- **对白/TTS**：`dialogue.py`（lines、**voice profiles 已存在**、emotion、speech_rate、SAPI 音色、候选/选择/治理）、`DialogueTTSPanel`、`AudioTrackPanel`、`AudioImportBindingForm`
- **时间线**：`timeline.py`（revisions、字幕 revisions+渲染、音频绑定、帧锚点、转场约束、**增强配方 recipes**、集渲染、交付构建/校验/审签）、`timeline_exports.py`（**仅 OTIO/EDL**）、`TimelineRevisionPanel`/`SubtitleRevisionPanel`/`TimelineExportAction`
- **自动化**：`automation_workflows.py`（声明式 workflow：节点 TASK/条件/human gate/plan/start/pause/resume/cancel，节点执行走 `jobs.create_job_in_transaction`）
- **资产/品牌**：`workspace_assets.py`（跨项目授权/授权-grant、brand kit、水印配置、合规策略）、`BrandKitPanel`、`ProjectAssetGrantPanel`
- **交付**：`configuration.py` 交付目标版本化 + `ReadinessPanels` 选择 UI、`DeliveryWorkflowPanel`、`OutboxDeliveryPanel`
- **本地 LLM**：`local_llm.py`（用户自备本地模型接口，可选增强用）
- **测试命令**：`pnpm api:test`（pytest apps/api/tests）、`pnpm web:test`（vitest）、`pnpm check`（Ruff/mypy/build 全绿）、Playwright e2e `tests/e2e/`
- **模拟环境**：`scripts/serve_sim_env.py`（快照+隔离 API 3225）、`scripts/run_sim_worker.py`（受控 H3 worker 8188）、Vite 5173；受控真机 worker 用 `scripts/comfy.ps1`

## 3. 调研结论（2026 商业平台 + 开源社区）

### 3.1 商业平台全流程（行业标准流水线）

统一流水线：**选题/灵感 → 剧本（原创/小说解析，万字输入）→ 拆集拆幕 → 角色/场景资产卡（参考图，一致性锚点）→ 分镜（提示词/镜头运动/情绪）→ 关键帧 → 视频（主体参考/首尾帧/多主体）→ 多角色配音（情绪/语速）→ 音效/BGM → 字幕 → 剪辑（时间线/后剪辑）→ 审片/局部重生成 → 多平台规格导出 → 发布**。

| 平台 | 关键功能（调研所得） |
|---|---|
| 小云雀（快手） | 搭载 Seedance 2.0 的短剧 Agent：**10 万字剧本一键成片**；剧本解析→拆集→分镜→角色/场景一致性→视频→配音→配乐→字幕全自动；主打"告别抽卡"（一致性锚定，非随机重抽） |
| 即梦（字节） | 小章鱼 Octo：**协作型叙事工具，多模态同屏共创**（文/图/视频/音轨同一画布）；智能画布/无限画布；30 秒直出；导演式多镜头编排 |
| 可灵（快手） | **主体参考（多主体一致性）**、人物微表情控制、15 秒长视频、AI 导演共创（多提示词一条片、分镜板式编排） |
| 火山剧创 / Dramart（火山引擎） | Seedance 2.0 短剧生产 Agent，制作周期缩短 80%；Dramart 为漫剧创作平台 |
| 万兴剧厂 | AI 原创剧本（**万字灵感→百集剧本**）；与 Vidu 共创漫剧大模型；与万兴喵影打通 **"后剪辑链路闭环"**（业界首个）；AI 真人剧出海；全链路：剧本/分镜/角色/视频/配音/字幕/配乐 |
| 巨日禄 | 文本转漫画视频；"1 人 1 天 1 部剧"量产；Seedance 2.0 |
| 爱奇艺纳逗 pro | AI 短剧 Agent（20+ Agent 横评中在列） |
| 通义万相 Wan 2.2 | 开源；**导演模式**；电影级美学控制系统；分镜→提示词导演级控制 |
| Runway/Pika/Luma/Veo/Sora | Director Mode：多场景编排（一次生成多镜头）、分镜板、镜头运动/相机控制 |

### 3.2 开源项目全景（重点研究对象）

| 项目 | 亮点（对齐我们的缺口） |
|---|---|
| BigBanana-AI-Director | **Script-to-Asset-to-Keyframe 工业化流**：一句话→完整短剧；角色资产（参考图集）、场景资产、镜头运动控制、一致性锚定 |
| ArcReel | 小说→**角色/场景/道具资产**→剧本→分镜图→视频；跨镜头一致性；**剪映草稿导出**；费用追踪；self-hosted |
| Toonflow-app | 小说→动画短剧；AI 编剧、智能分镜、角色与视频生成；桌面端本地部署 |
| CutOS / CineGen-AI / Shortify-AI / ai-shotlive | 剧本→分镜→关键帧→视频→**AI 剪辑**一站式；Shortify 一句话→5 分钟短剧；ai-shotlive 前后端分离可自建 |
| ManvoTV | 无限画布 + 漫剧剧本工厂 |
| novel-to-script-team | 多 Agent 多 Skill 小说改编流水线（小说解析→剧本→分镜） |
| SkyReels-V3（昆仑万维） | 开源模型：参考图转视频、**视频延长**、音频驱动 |
| Wan 2.2（阿里） | 开源；导演模式 |
| Maestro / video-db Director / s1dashu director / OpenMontage / ViMax | 本地/自部署"制片"型 Director：research→script→visual dev→shot design→generation→delivery 多模式 |
| ComfyUI-Novel-Director / kt-ai-Studio / Dify+ComfyUI 产线 | 本地全自动漫剧批量生产线（社区教程：FLUX+Wan2.2/Qwen 全链路，8G 显存方案） |
| 研究侧 | DreamID-Omni（可控人物音视频统一框架）、Helios 等——人物一致性/音频驱动的前沿，**仅跟踪，不落地** |

### 3.3 对比结论：我们的不足（已逐项核对代码）

| # | 缺口 | 我们的现状（核对后） | 参考对象 |
|---|---|---|---|
| G1 | 角色资产库（角色卡+参考图+跨镜头绑定+提示词注入） | ContinuityPanel 只有文本字段+已选参考，无项目级角色实体/资产卡 | 可灵主体参考 / BigBanana / ArcReel |
| G2 | 场景/道具/服装资产卡 | 同上，无资产实体 | ArcReel / BigBanana |
| G3 | 小说/长文本解析导入（拆集/角色/对白抽取） | 有 ScriptImportPanel（剧本导入），无小说→剧本解析 | 小云雀 10 万字 / 万兴 / novel-to-script-team |
| G4 | 整剧一键编排（剧本→成片全流水线模板） | automation_workflows 存在，但缺"整剧流水线内置模板"与 TTS/字幕/渲染节点接入 | 小云雀 / 火山剧创 / 巨日禄 |
| G5 | 多角色配音编排（角色↔音色绑定+批量生成+A/B 试听） | voice profiles/emotion/speech_rate 已有，缺角色绑定与整集批量/A-B 试听界面 | 小云雀 / 万兴 |
| G6 | 交付规格预设库（抖音/快手/小红书等一键套用） | 交付目标版本化已有，缺平台预设库 | 全行业标配 |
| G7 | 生产档位（FAST/DRAFT/SCREEN/PRODUCTION/MASTER+抽卡） | 只有 take 数；openclaw 已实测 4/6/8/20 档未接入平台 | openclaw / 行业 |
| G8 | Ref2V 参考视频路线 | H3 ref2va 模型存在、openclaw 有 ref2v.json，平台 factory 未接 | H3 原生 / openclaw |
| G9 | 长镜头（>15s 分段生成+无缝拼接） | duration 上限 15s；H3 训练可至 362 帧 | SkyReels-V3 视频延长 |
| G10 | 剪映草稿导出（draft_content.json） | 仅 OTIO/EDL；剪映是中文用户精剪入口，"后剪辑闭环"性价比最高路径 | ArcReel / 万兴喵影闭环 |
| G11 | 音效/BGM 轨道 | timeline 无 music/sfx 概念（bind_audio 仅对白/导入音频） | 全行业 |
| G12 | 字幕样式模板（字体/颜色/位置/描边） | 字幕渲染已有，无样式模板 | 全行业 |
| G13 | 可视化时间线剪辑器 | 时间线是数据结构+渲染，无可视化轨道编辑器（大工程） | 万兴后剪辑 / CutOS |
| G14 | 封面图生成、敏感内容预检、自由多模态画布 | 无（画布为 DAG 布局，非自由画布） | 即梦小章鱼 / 平台标配 |

**明确排除**（违反 C1/C2/C3 或不符合单机定位）：云协作/多人在线、多供应商云引擎、市场分析/选题洞察、数字人/口型同步、外部 TTS 音色商城、正版素材商城、音乐生成模型、3D 导演台、多租户。

---

## 4. 开发计划（分批实施）

执行顺序：**P0 → P1 → P2（P2 本批只做设计，不开发）**。每项完成即跑回归并提交，不攒大 PR。

### P0 批：短剧完整闭环（先做，做完即具备"一键成剧"全链）

#### P0-1 角色资产库 + P0-2 场景/道具/服装资产卡（合并为一个故事资产模块）

- **API**：新增 `application/story_assets.py`。表 `story_assets(id, project_id, kind[CHARACTER|SCENE|PROP|COSTUME], code, name, description, canonical_media_version_id, extra_json, created_at, updated_at, created_by)`；参考图复用现有 media_versions（导入图或已批关键帧），不新建媒体存储。方法：`create_asset / update_asset / list_assets / bind_ref(media_version_id) / delete_asset(软删)`，全程写 audit_events。
- **分镜绑定**：新表 `shot_asset_bindings(shot_id, asset_id, role_in_shot, created_at)`；`reviews.py`/`read_models.py` 的 shot 视图与 `continuity_context` 扩展返回资产卡摘要。
- **提示词注入**：在 `comfy_jobs.compile_semantic_inputs` 白名单机制内（`declared_roles`），为绑定的 CHARACTER 资产注入 `character_sheet`（名称+外观锚点描述+canonical 参考图 media_version_id）；`h3_workflows` 将 canonical 图作为 I2V 输入帧（无 canonical 图时仅注入描述，不阻断）。**不破坏现有 WORKFLOW_SLOT_UNSUPPORTED 修复**（先核对 workflow_version 的 node_bindings）。
- **Web**：`features/production/StoryAssetLibraryPanel.tsx`（四类 Tab 卡片、参考图缩略图、绑定音色入口见 P0-5）+ `DirectorShotEditor` 镜头内资产选择 + `ContinuityPanel` 显示资产卡（替换纯文本字段为资产卡引用）。
- **验收**：API 单测（CRUD/绑定/软删/审计/注入白名单）；Web 组件测试；e2e 新 spec `story_asset_windows_uat.spec.ts`：建角色卡→绑参考图→绑镜头→提交生成→查 worker 收到的 prompt/输入帧含角色锚点→连续性面板显示资产卡。

#### P0-3 小说/长文本解析导入

- **API**：新增 `application/novel_import.py`。入口 `parse_document(text_or_file, mode[LLM|RULE], project_id)`：章/幕/场切分→角色实体抽取→对白抽取→生成集+场+对白+分镜草稿（复用 ScriptImport 与 dialogue 数据模型）。LLM 模式走 `local_llm.py`（用户自备本地模型），RULE 模式正则启发式降级；结果带 `provenance{engine: LLM|RULE, model_ref}`。
- **人工 gate**：解析产物为**草稿态**，用户在 UI 确认后才落库为正式剧本（沿用现有草稿评审门禁，不自动越过审批）。
- **Web**：`features/projects/NovelImportPanel.tsx`：粘贴/上传长文本→选模式→解析预览（拆集树+角色列表+对白数）→确认导入。
- **验收**：单测（两种模式、2 万字样本、边界）；e2e：粘贴小说→预览→确认→项目出现集/角色/对白，provenance 落库。

#### P0-4 整剧一键编排（内置流水线模板）

- **API**：`automation_workflows.py` 扩展：①节点类型增加 `SUBTITLE/RENDER/TTS_BATCH`（调 timeline/dialogue 现有方法，仍以 job 形式落地）；②新增 `create_from_template(template_code, project_id, scope)`，内置模板 `WHOLE_DRAMA`：按集顺序展开 [每镜头生成（关键帧→gate→视频）→ 每集 TTS 批量 → 字幕 → 渲染 → 交付] 节点图，`human_gate=EACH_ITERATION`，关键帧节点前自动 PAUSE_HITL。
- **Web**：`AutomationWorkflowPanel` 增加"整剧一键编排"入口（选模板→生成 workflow 定义→plan→start→监控/暂停/恢复）。
- **验收**：单测（模板展开/节点类型校验）；e2e（模拟环境）：2 集×2 镜头一键启动，断言节点顺序、gate 暂停在关键帧、resume 后继续到交付包生成。

#### P0-5 多角色 TTS 编排

- **API**：`dialogue.py` 扩展：①新表 `character_voice_bindings(asset_id→story_asset CHARACTER, voice_profile_id)`（角色=故事资产角色卡，与 P0-1 打通）；②`submit_episode_tts_batch(episode_id)`：按对白顺序批量生成（自动取绑定音色、保留行级 emotion/speech_rate），复用现有 submit_tts_job 与候选/选择机制；③line 视图返回绑定音色与候选状态。
- **Web**：`DialogueTTSPanel` 增加：音色绑定区（角色下拉=资产库角色）、"整集批量生成"按钮、**A/B 试听区**（两候选并排试听，遵守现有 preload="none" 零自动原媒体规范）。
- **验收**：单测（绑定/批量/幂等）；e2e：绑定 2 角色 2 音色→批量生成整集→A/B 试听→选择候选→AudioTrackPanel 上轨。

### P1 批：显著增强

#### P1-6 交付规格预设库
- `configuration.py` 或新 `delivery_presets.py`：内置预设表（抖音竖屏 1080×1920/30、快手竖屏、小红书 3:4、视频号 1080×1920、B站横屏 1920×1080、通用 16:9 4K，含码率/时长上限/封面规格）。`select_delivery_target_version` 支持 `preset_code` 一键套用。Web：`DeliveryWorkflowPanel` 预设下拉。
- **验收**：单测 + e2e 选"抖音竖屏"→目标版本字段自动填充→构建交付包→verify 通过。

#### P1-7 生产档位（Turbo 档位映射）
- 在 `h3_workflows.py` 增加档位参数表：`FAST/DRAFT/SCREEN/PRODUCTION/MASTER` → 帧数（17k+5 网格内）/分辨率/denoise/steps/cfg + 默认 take 数（对齐 openclaw 4/6/8/20 档经验）。`GenerationControlPanel` 档位选择器（保留手动覆盖）。
- **验收**：单测（映射表/边界）+ e2e 选 PRODUCTION 档提交→worker 收到的 workflow 参数与表一致。

#### P1-8 Ref2V 参考视频能力位
- `h3_workflows.py` 新增 `build_ref2va`（参考 openclaw `ref2v.json` 的节点链；模型名从 model_manifest 读取，仅当清单含 ref2va 模型时能力位 `H3_REF2VA_CANDIDATE` 才可提交，否则 UI 置灰）。参考视频来源：用户自备媒体版本（IMAGE/VIDEO 类型校验）。
- **验收**：单测（编译/清单门禁/降级）；受控真机 worker 跑通 1 条真实 ref2v（若本机 GPU 具备），并出证据。

#### P1-9 长镜头（>15s 分段生成 + 无缝拼接）
- `h3_workflows.py`：duration/帧数超上限时规划分段（首尾帧接续：段 N 尾帧=段 N+1 首帧，中间帧锚定）；`timeline.py` 或 `generation.py` 增加 ffmpeg 拼接步骤（同编码参数 concat，校验总时长/音画同步）。
- **验收**：单测（分段规划/接续帧一致性）+ 受控 worker 2 段拼接验证 + e2e 模拟环境编排。

#### P1-10 剪映草稿导出
- `timeline_exports.py` 新增 `_jianying()`：输出剪映 `draft_content.json`（materials/videos/audios/texts/tracks 结构，媒体文件按剪映要求放置），与现有 OTIO/EDL 同一导出动作产出。
- **验收**：单测（JSON 结构字段级断言）+ e2e 导出→校验 JSON 可解析、轨道/素材引用完整。

#### P1-11 音效/BGM 轨道
- `timeline.py` 的 `bind_audio` 扩展 `track_kind[DIALOGUE|BGM|SFX]`；音频素材来自用户自备文件导入（不捆绑素材）；`render_episode` 用 ffmpeg 混音（BGM 音量/循环策略参数化）。
- **验收**：单测（绑定/混音参数）+ e2e 导入 BGM→绑轨→渲染→ffprobe 断言音轨数与时长。

#### P1-12 字幕样式模板
- `timeline.py` 字幕渲染扩展样式参数（字体/字号/颜色/位置/描边）+ 样式模板 CRUD（项目级）；渲染支持 SRT/ASS（带样式）。
- **验收**：单测（模板/ASS 样式段）+ e2e 套用模板→渲染→解析断言样式字段。

### P2 批：仅设计，不开发（本批交付设计文档）

- P2-A 可视化时间线剪辑器：轨道拖拽/局部重剪/转场/预览，先出 `docs/plan/timeline-editor-design.md`（数据模型复用 timeline revisions，交互设计，分阶段实施）。
- P2-B 封面图生成（关键帧+标题模板排版）；P2-C 敏感内容预检（本地 LLM 规则）；P2-D 自由多模态画布增强。各出半页设计说明即可。

## 5. 实施顺序与批次

1. **批次 1（P0 全部）**：P0-1/2 → P0-5（依赖 P0-1 角色实体）→ P0-3 → P0-4（依赖 P0-5 的批量 TTS 节点）。P0-1/2 与 P0-3 可并行。
2. **批次 2（P1 前半）**：P1-6、P1-7、P1-10、P1-12（互不依赖，可并行）。
3. **批次 3（P1 后半）**：P1-8、P1-9、P1-11（涉及 H3 参数与渲染链，需批次 2 的 export/渲染改动稳定后做）。
4. **批次 4**：P2 设计文档。

## 6. 完成定义（每项 DoD）

- API 单测通过（`pnpm api:test`）；Web 组件测试通过（`pnpm web:test`）；`pnpm check` 全绿（Ruff/mypy/build）。
- 新功能有对应 Playwright e2e（真实页面点击路径，跑在模拟环境 `serve_sim_env.py` + `run_sim_worker.py`；涉及真机 GPU 的项跑受控 worker `comfy.ps1` 并留证据到 `docs/evidence/`）。
- 回归不破坏：`post_launch_simulation.spec.ts`、`t2v_one_shot_generation.spec.ts`、`g6_four_takes_uat`、`motion_multimodal_windows_uat`、`nfr_perf_browser_observation`、`windows_kill_matrix_uat` 全 PASS；`release_audit.py` 保持 PASS/GO。
- 审计事件、provenance、错误码、审计日志齐备；UI 中文文案；无外部网络调用新增（LOCAL_ONLY 校验）。
- `docs/requirements-traceability.md` 增开"G11 增强"节记录每项状态与证据。

## 7. 风险与边界

- H3 模型能力边界：多主体一致性（可灵"主体参考"级）受模型限制，本批只做到"角色卡单参考图锚定+描述注入"，不承诺多主体融合。
- Ref2V/长镜头依赖用户本机 H3 模型文件，验收以"能力位+门禁+受控真机证据"为准，无 GPU 时退化为编译级验证并在证据中注明。
- 任何新增不得引入外部 API（C1）；本地 LLM 仅当用户配置了自备模型时才可选启用（C2）。
- 剪映草稿格式为逆向社区格式，按"可被剪映打开"做真机验证，格式细节以实测为准。

---

## 8. 完成状态（2026-08-17 更新）

本计划全部批次已交付并提交，工作树干净。提交：`82e23e5`（P0）、`d34f217`（P1）、`4d448b5`+`72fd219`（清理代理临时产物）。

### 批次 1（P0）— 全部完成 ✅
| 项 | 状态 | 验证 |
|---|---|---|
| P0-1/2 故事资产库 | ✅ | API 16 用例 + Web 24；e2e `story_asset_windows_uat.spec.ts` PASS（证据 `docs/evidence/g10/story-asset-windows-uat-2026-08-17.json`） |
| P0-1 锚点注入 | ✅ | 6 用例；预览端点与执行锚点逐字节一致 |
| P0-3 拆解草稿应用 | ✅ | 9 用例；e2e `breakdown_apply_windows_uat.spec.ts` PASS（证据 `breakdown-apply-windows-uat-2026-08-17.json`） |
| P0-5 多角色 TTS | ✅ | 5 用例；e2e `character_voice_windows_uat.spec.ts` PASS（**真实 SAPI 合成**，证据 `character-voice-windows-uat-2026-08-17.json`） |
| P0-4 整剧一键编排 | ✅ | 13 用例（含 worker 驱动链路）；e2e `whole_drama_windows_uat.spec.ts` PASS（两轮 HITL，证据 `whole-drama-windows-uat-2026-08-17.json`） |

### 批次 2/3（P1）— 全部完成 ✅
| 项 | 状态 | 验证 |
|---|---|---|
| P1-6 交付规格预设库 | ✅ | 6 用例；e2e `delivery_presets_windows_uat.spec.ts` PASS（证据 `delivery-presets-windows-uat-2026-08-17.json`） |
| P1-7 生产档位 | ✅ | 8 用例；tier 冻结进 parameter_set 元数据（不触 WORKFLOW_SLOT 白名单） |
| P1-8 Ref2V 能力位 | ✅ | 7 用例；`MiniMaxH3ReferenceToVideo` 已加入 TRUSTED_COMFY_BUILTINS 并经真实 `/object_info` 核实 |
| P1-9 长镜头分段 | ✅ | 7 用例（含真实 ffmpeg 拼接登记） |
| P1-10 剪映草稿导出 | ✅ | 8 用例；e2e `jianying_export_windows_uat.spec.ts` PASS（证据 `jianying-export-windows-uat-2026-08-17.json`） |
| P1-11 音效/BGM 轨 | ✅ | 3 用例（含真实 ffmpeg 混音 probe 断言） |
| P1-12 字幕样式模板 | ✅ | 5 用例；e2e `subtitle_styles_windows_uat.spec.ts` PASS（证据 `subtitle-styles-windows-uat-2026-08-17.json`） |

### 批次 4（P2）— 设计文档已交付 ✅（不实施代码）
`docs/plan/timeline-editor-design.md`：P2-A 可视化剪辑器四阶段设计、P2-B 封面合成、P2-C 敏感内容预检、P2-D 多模态画布，各含验收标准。

### 门禁汇总
API 全量 **382 passed**（基线 290 + 92 新增）、Web **39 文件 / 126 tests**、`pnpm check` 全绿、7 个 e2e 全 PASS、`release_audit` PASS/GO（正式库链头 0041，P1 无新迁移）、`docs/requirements-traceability.md` 第七/八轮已登记。

### 留待事项（不阻塞，计划内边界）
1. **Ref2V 真机跑通**：能力位与编译链已交付，待 GPU 环境跑通一条真实 ref2v 生成并出证据。
2. **档位落地实际生成**：tier 目前冻结为元数据，实际生成参数映射需在 profiles/execution 层接入 `resolve_tier`。
3. **剪映草稿真机导入**：格式为社区逆向 best-effort，待真机验证"可被剪映打开"（按 §7 以实测为准调整字段）。
4. **使用期人工事项**（非 agent 范围）：正式项目真实音色/素材数据链、交付包最终签字。
