# G11 P2 设计文档（本批只设计，不开发）

> 按 `docs/plan/gap-closure-development-plan.md` 批次 4 交付。P2 各项均为设计稿，供后续批次实现时直接引用；实现前需按 G11 工程约定补齐 DoD（测试/审计/e2e）。

---

## P2-A 可视化时间线剪辑器（大工程，分阶段实施）

### 目标与定位
把现有"数据结构化时间线"（`TimelineService.create_timeline_revision` 的 items + `render_episode`）升级为**可视化轨道编辑器**：拖拽调整镜头顺序/时长、转场、局部重剪预览、轨道（视频/对白/BGM/SFX/字幕）分层。对齐调研结论中的"后剪辑闭环"（万兴喵影/剪映路径），但**不重新发明渲染器**——剪辑器只编辑 TimelineRevision items，渲染仍走现有 `render_episode`/`render_segmented_episode`（P1-9）。

### 数据模型（复用，不改表结构）
- 现有 `timeline_revisions.items`（track_type/media_version_id/start_us/end_us/parameters）即剪辑结果模型；剪辑器 = 可视化编辑器，落盘仍走 `create_timeline_revision`（不可变 revision + 冲突乐观锁）。
- 转场：现有 `shot_transition_constraints`（from/to/constraint_type/enforcement）承载约束语义；剪辑器渲染为轨道间转场标记。
- 字幕：`subtitle_revisions`（cue start_us/end_us/text/style）作为文本轨数据源，P1-12 样式模板直接复用。
- 音频：P1-11 的 audio_bindings（DIALOGUE/BGM/SFX）作为音频轨数据源。

### 交互设计（阶段一：只读时间线视图 + 基本编辑）
1. **轨道区**：视频轨（镜头卡片：缩略图/编号/时长/状态徽标）、音频轨（DIALOGUE/BGM/SFX 分组）、文本轨（字幕 cue 块）、转场标记行。
2. **时间标尺**：秒级刻度，播放头；点击镜头卡片 → 右侧属性面板（编辑 start/end/参数，保存 = 新建 TimelineRevision）。
3. **拖拽**（阶段二）：镜头卡片拖动调整顺序（落盘走 batch 编辑语义——参考 `StoryboardBatchPayload` 的 reorder 模式）；拖拽边缘调整时长（受 shot.target_duration_ms 与转场约束校验）。
4. **局部重剪**（阶段三）：选中镜头 → "重新生成"入口复用现有 generation 流程（intent/variant 提交 + 审批），产物晋升后自动替换轨道项（走新 revision）。
5. **预览**：播放头 + 关键帧缩略图序列（零原视频自动加载，遵守 preload="none" 规范）；"渲染预览"按钮调 render 现有链路。

### 组件划分（阶段一实现范围）
- `apps/web/src/features/timeline/TimelineEditorPanel.tsx`（轨道容器 + 标尺 + 播放头）
- `apps/web/src/features/timeline/TrackVideo.tsx` / `TrackAudio.tsx` / `TrackSubtitle.tsx` / `TransitionRow.tsx`
- `apps/web/src/features/timeline/TimelineItemInspector.tsx`（选中项属性编辑）
- 状态：纯前端 state + react-query 读写 TimelineRevision；无新后端端点（阶段一）。

### 验收（阶段一 DoD）
- vitest：组件渲染/选中/属性编辑/保存触发 create_timeline_revision 参数正确
- e2e：模拟环境打开剪辑器 → 渲染真实 timeline items → 拖动顺序 → 保存新 revision → 断言 revision_no+1 且 items 顺序落库 → 渲染不回归
- 三视口无溢出、零自动原媒体、零公网

### 分阶段排期
- 阶段一（只读+属性编辑）：0.5 迭代
- 阶段二（拖拽/时长调整）：0.5 迭代
- 阶段三（局部重剪集成）：0.5 迭代（依赖 generation 流程）
- 阶段四（转场可视化/播放头预览）：0.5 迭代

---

## P2-B 封面图生成（半页）

- **定位**：交付包标配封面（平台规格预设 P1-6 已含 cover_aspect）。
- **方案**：不引入图像生成模型（LOCAL_ONLY 约束）；封面 = 关键帧缩略图 + 标题/副标题文本排版 + 平台比例裁切，用 ffmpeg（drawtext/scale/crop）+ 现有关键帧媒体合成。新增 `cover_composer.py`（纯 ffmpeg 编排，审计/provenance 记录源关键帧 media_version_id）。
- **端点**：POST /episodes/{id}/covers（body: {keyframe_media_version_id, title, subtitle, style}）→ 产物以 IMAGE/Cover 媒体版本登记（复用 MediaService.promote 路径）。
- **验收**：真实 ffmpeg 合成三平台比例（9:16/3:4/16:9）各 1 张 → ffprobe 断言尺寸/时长 → e2e 页面按钮点击。

## P2-C 敏感内容预检（半页）

- **定位**：本地可选的审片辅助，不替代人工审批（正式批准仍必须人工）。
- **方案**：复用 `local_llm.py`（用户自备本地模型，无模型则置灰）：对文本（对白/字幕）做规则+LLM 双通道预检；输出 {risk_level, categories[], passages[]} 存入 machine_check_runs（复用现有机器检查表与 stale 语义）；风险条目在 ReviewInbox 显示标记。
- **边界**：只读辅助；任何预检结果都不能产生批准决策（与 automation 的 ai_approval_allowed=false 语义一致）。
- **验收**：规则通道单测（中文敏感词表 + 白名单）；LLM 通道按 local_llm 现有 profile 门禁；e2e 面板展示。

## P2-D 自由多模态画布增强（半页）

- **定位**：现有 `ProductionCanvasPanel` 是 DAG 布局（节点/边/preflight）；增强为自由多模态画布（对齐即梦小章鱼"多模态同屏共创"的本地化版本）。
- **方案**：分两步——① 布局自由化：canvas_layouts 现有 position 字段已支持任意坐标，补拖拽/缩放/分组 UI（不改 schema）；② 多模态贴片：画布节点可附加媒体贴片（关键帧/视频/音频波形/文本），数据源全部来自项目内 media_versions（零新存储）。
- **边界**：画布是组织视图，不承载生成/审批语义（节点执行仍走 preflight）。
- **验收**：拖拽布局落库/恢复、贴片渲染三视口、零公网。

---

## 实施顺序建议
P2-A 阶段一（剪辑器只读+属性编辑）→ P2-B（封面合成，独立小项）→ P2-C（预检，依赖 local_llm 配置）→ P2-A 阶段二/三 → P2-D。P2-B 可与 P2-A 阶段一并行。
