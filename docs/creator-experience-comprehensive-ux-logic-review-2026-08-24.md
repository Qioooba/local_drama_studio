# 创作平台全方位体验评估（输入控件 + 逻辑 + 连贯性 + 自动化）

> 日期：2026-08-24  
> 版本：v1.0  
> 范围：`apps/web/src` 创作相关页面/工作流（项目创建、故事分解、导演、生成、分集策划、资产、审核、交付）

本评估基于现有页面交互中“用户可直接操作对象”的实际体验，目标是面向创作者降低手工负担，把可确定、可推导、可查询的内容下沉到后台或受控选择器。重点覆盖你提出的六类问题。

---

## 1. 先总结（先给结论）

1. **主要问题不是单点 Bug，而是交互范式偏“运维控制台”**。很多页面把模型能力、ID 管理、状态机规则直接暴露给创作者，导致高认知负担。
2. **最突出阻塞源是“必填文本 + 手工确认双保险”**：即使是确定性逻辑也要求用户重复点击、重复输入文本、重复确认。
3. **最该先改的是五类场景**  
   1）项目初始化参数  
   2）镜头/生成参数  
   3）角色与资产绑定  
   4）质量审核与交付闭环  
   5）跨页跳转缺乏统一上下文（项目/分集/镜头）。
4. **优先级建议**  
   P0：不改会直接阻塞、错单率高、或明显反直觉。  
   P1：会明显提升效率和体验。  
   P2：体验更平滑、减少认知负担，但不直接阻塞。

---

## 2. 文本框不合理（应改下拉/单选/自动生成/选择器）

以下为高频问题点，按页面列出，便于直接分工改造。

### 2.1 P0：应立即改造

1. [ProjectCreateWizard.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/projects/ProjectCreateWizard.tsx)
   1) `aspect_ratio` 当前文本输入。应改 `select`，并与宽高联动预设。  
   2) `width/height/fps numerator/fps denominator` 手输项应改 presets + 自动锁定衍生。  
   3) `primary_language/subtitle_language` 应改语言下拉，避免手输异常码。  
2. [GenerationControlPanel.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/generation/GenerationControlPanel.tsx)
   1) 多个 JSON 文本框（timed_direction/performance_binding/motion/media_mask）是结构化模型，应改为结构化表单控件。  
   2) 其中 `media_version_id`、`role` 等应为项目内选择器，不应手输。  
3. [StoryboardBatchWorkbench.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/projects/StoryboardBatchWorkbench.tsx)
   1) `shot_type` 与构图/运镜字段已在 DirectorIntentEditor 有枚举标准，当前批量编辑仍应使用统一枚举控件。  
   2) `copyCode` 等标识码建议从序列生成器自动分配，允许高级模式重写。  
4. [ShotGroupPlanner.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/episode-plan-v2/ShotGroupPlanner.tsx)
   1) `beat_code` 等编号应按计划序号自动生成。  
   2) 分镜参数使用统一枚举（景别/镜头运动/角色槽位）代替自由文本。  
5. [CharacterIdentityPackPanel.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/asset-bible-v2/CharacterIdentityPackPanel.tsx)
   1) 身份包代码 `BASE` 可默认生成，避免默认值手输。  
   2) 审核状态字段应沿用统一枚举。  
6. [StoryAssetLibraryPanel.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/production/StoryAssetLibraryPanel.tsx)
   1) 资产 code 建议自动生成并与类别绑定；仅在冲突时提供手动编辑。

### 2.2 P1：应改为选择器或可视化控件

1. [DialogueGovernanceActions.tsx / DialogueTTSPanel.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/status/)
   1) `emotion`、`model_ref`、`ttsJobId` 目前仍有明显枚举或可查询来源，不应让用户自由输入。  
2. [MotionControlPanel.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/generation/MotionControlPanel.tsx)
   1) `subject_role/mask` 与媒体版本应使用下拉+预览。  
3. [PostProcessPanel.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/generation/PostProcessPanel.tsx)
   1) LUT 路径应为项目内资源 picker，而不是裸文本。  
4. [AudioImportBindingForm.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/status/AudioImportBindingForm.tsx)
   1) 本地音频与授权证据应分别走文件选择器与项目资源选择器。  
5. [ProfileConfigurationPanel.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/profiles/ProfileConfigurationPanel.tsx)
   1) 证据相关 `job id` 查询后填充；无需用户手输。  
6. [ModelLicenseEvidenceForm.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/status/ModelLicenseEvidenceForm.tsx)
   1) 证据路径应来源于项目内可选列表（可检索）。

### 2.3 P2：可保留文本但加自动化增强

1. [DirectorIntentEditor.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/director-v2/DirectorIntentEditor.tsx)
   1) `creative_intent/subject_action/performance` 为创作文本，保留文本框。  
   2) 可加“提示词模板推荐”和“同角色历史复用”。  
2. [DialogueTTSPanel.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/status/DialogueTTSPanel.tsx)
   1) 整集情绪可保留文本创作，但建议默认预填+候选词提示。  
3. [EpisodeReviewPanel.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/production/EpisodeReviewPanel.tsx)
   1) 备注文本保留；可改为模板短语一键填充减少阻塞。

---

## 3. 功能逻辑不合理、用户容易卡住

### 3.1 手工触发可自动化流程（高优）

1. [DirectorDeskPage.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/pages/DirectorDeskPage.tsx)
   1) 关键建议：将“运镜能力裁决”从用户点击按钮改为字段变更即自动执行，避免来回误操作。  
2. [DirectorIntentEditor.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/director-v2/DirectorIntentEditor.tsx)
   1) 当前“保存 revision”和“标记 ready”存在两步分离，典型可合并为 Save+Ready 事务。  
3. [GenerationWorkbench.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/generation/GenerationWorkbench.tsx)
   1) “重抽”与参数约束可自动更新 seed 的场景很多，应减少用户重复填入。  

### 3.2 审批/状态流转逻辑对创作者过重

1. [DeliveryWorkflowPanel.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/production/DeliveryWorkflowPanel.tsx)
   1) 人工审核 + 平台审核双链路强制填写说明不适用于单机作者场景，建议提供一键快速审批模式。  
2. [DialogueTTSPanel.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/status/DialogueTTSPanel.tsx)
   1) TTS 任务状态若可自动查询，应由后台列出可用任务，用户只选。  
3. [ReviewInboxPanel.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/reviews/ReviewInboxPanel.tsx)
   1) 批量拒绝必须手填原因是对高质量工作流合理，但普通“拒绝一条”可提供“系统推荐模板 + 自定义补充”。

### 3.3 多步阻塞造成流转中断（需要流程重排）

1. [ProjectCreateWizard.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/projects/ProjectCreateWizard.tsx)
   1) 项目基础参数与创作配置应前置校验并自动联动；避免反复来回修正。  
2. [AssetBiblePage.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/pages/AssetBiblePage.tsx)
   1) 资产状态和代码在展示 + 创建时重复编辑，建议“创建成功后自动写入并可在详情编辑”。  
3. [WorkspaceAssetAuthorizationPanel.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/shared/WorkspaceAssetAuthorizationPanel.tsx)
   1) 逐条操作授权到项目时，撤回原因等阻塞可合并为轻提示 + 二次确认，或批量撤回策略。  

---

## 4. 冗余操作与重复输入

### 4.1 跨页面重复参数录入

1. [Shot 类型/景别/运镜方向] 在多个文件有不同表达方式  
   - [DirectorIntentEditor.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/director-v2/DirectorIntentEditor.tsx)  
   - [DirectorShotEditor.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/production/DirectorShotEditor.tsx)  
   - [StoryboardBatchWorkbench.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/projects/StoryboardBatchWorkbench.tsx)  
2. 建议建立共享枚举常量/组件（SHOT\_TYPES、COMPOSITIONS、DIRECTIONS），并全局复用。  

### 4.2 代码/标题可自动生成却常常手打

1. [StoryAssetLibraryPanel.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/production/StoryAssetLibraryPanel.tsx)  
2. [AssetBiblePage.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/pages/AssetBiblePage.tsx)  
3. [CharacterIdentityPackPanel.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/asset-bible-v2/CharacterIdentityPackPanel.tsx)  
4. [StoryboardBatchWorkbench.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/projects/StoryboardBatchWorkbench.tsx)  
5. 建议改为“可编辑生成”模式：默认生成，用户可点开覆盖。

### 4.3 路径/ID 仍以手输为主

1. [ModelLicenseEvidenceForm.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/status/ModelLicenseEvidenceForm.tsx)  
2. [AudioImportBindingForm.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/status/AudioImportBindingForm.tsx)  
3. [PostProcessPanel.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/generation/PostProcessPanel.tsx)  
4. [ProfileConfigurationPanel.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/profiles/ProfileConfigurationPanel.tsx)

---

## 5. 哪些应该后台做（减少用户手工）

1. [生成参数联动与建议值](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/projects/ProjectCreateWizard.tsx)  
   - 比例、语言、FPS、字幕相关字段应由预设+约束自动推断。  
2. [镜头/拍摄参数默认推荐](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/generation/GenerationControlPanel.tsx)  
   - 根据 Profile、上一个镜头、同角色镜头自动预填 shot direction / continuity / intensity。  
3. [音频与模型资源映射](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/status/DialogueTTSPanel.tsx)  
   - 自动筛选可用 `ttsJobId` / `voice_ref`，用户只确认。  
4. [审计与交付动作模板化](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/production/DeliveryWorkflowPanel.tsx)  
   - 一键记录标准化说明模板 + 可选展开编辑。  
5. [状态机可恢复策略](F:/AI_Projects/h3/local_drama_studio/apps/web/src/pages/DirectorDeskPage.tsx)  
   - 许多“为什么当前不能继续”的提示，应该转为自动修复动作（例如同步时间线后再开下一步）。  

---

## 6. 功能连贯性不合理点（跨页面）

1. [AppShell.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/layouts/AppShell.tsx) 与全局导航  
   - 当前项目/分集/镜头上下文在跳转后未能连续保留，导致用户“回到列表再找回”。  
   - 建议增加固定的三级上下文栏（Project / Season / Episode / Shot）。  
2. [ProjectsPage.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/pages/ProjectsPage.tsx) 与创作页跳转  
   - 侧边导航切换频繁后缺少“当前创作路径”提示；建议面包屑 + 上下文复位。  
3. [GenerationPage.tsx / GenerationWorkbench.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/pages/GenerationPage.tsx) 与 [DirectorDeskPage.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/pages/DirectorDeskPage.tsx)  
   - 生成与导演操作上下文有时未形成单向闭环，用户需要重复定位镜头。  
4. [EpisodePlanPage.tsx / ShotGroupPlanner.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/pages/EpisodePlanPage.tsx) 与 [DirectorDeskPage.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/pages/DirectorDeskPage.tsx)  
   - 分集规划与镜头导演编辑缺少接力机制，缺少“刚建镜头即进导演台”的快速入口。  

---

## 7. 布局与交互设计问题

1. [StoryboardBatchWorkbench.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/projects/StoryboardBatchWorkbench.tsx)
   - 抽屉内嵌大量二次弹窗导致上下文断裂。  
   - 建议改成左中右 3 列编辑（镜头列表、参数编辑、变体对比）或阶段化抽屉。  
2. [DirectorDeskPage.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/pages/DirectorDeskPage.tsx)
   - 关键按钮应做分层优先级：`保存并就绪`、`立即重抽`、`查看变体`应主按钮；高级动作折叠。  
3. [GenerationWorkbench.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/generation/GenerationWorkbench.tsx)
   - 当前参数区与预览区信息密度高，建议默认折叠次级参数，先露出创作相关最常用字段。  
4. [ReviewInboxPanel.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/reviews/ReviewInboxPanel.tsx)
   - 批量审核时复选 + 模板 + 理由框在同级展示，视觉结构略乱，建议“列表-评分卡-提交”三段式。  

---

## 8. 风险/影响评估（针对创作效率）

1. 若不改手工输入问题，长期将导致  
   1) 新人学习成本高  
   2) 错误码和 ID 填写率高  
   3) 流程阻塞和重复返工  
2. 若不改流程连贯性问题，后果是  
   1) 用户在不同入口来回切换，产生“状态迷失”  
   2) 审查与重算链路拉长  
3. 若不改自动化能力，交付端质量控制会出现  
   1) 规则依赖创作者记忆  
   2) 随机误操作上升，难保证跨项目一致性

---

## 9. 改造路线图（建议）

### 9.1 P0（立即执行）

1. `ProjectCreateWizard.tsx`  
   - 全部枚举输入改 select，宽高与fps联动，减少预检失败。  
2. `GenerationControlPanel.tsx`  
   - JSON 改结构化；media version & role 用 picker。  
3. `DirectorIntentEditor.tsx` + `DirectorDeskPage.tsx`  
   - 自动裁决模式替代手动按钮；增加 `保存并就绪`。  
4. `DeliveryWorkflowPanel.tsx`  
   - 一键审核链路 + 自动填充标准化说明。  

### 9.2 P1（下一阶段）

1. `StoryboardBatchWorkbench.tsx`  
   - 重构抽屉流程，减少双层弹窗。  
2. `AssetBiblePage.tsx`  
   - 资产创建和编辑统一成“自动生成 + 编辑覆盖”。  
3. [AppShell.tsx + routeRegistry.ts](F:/AI_Projects/h3/local_drama_studio/apps/web/src/routeRegistry.ts)  
   - 全局上下文导航（项目/分集/镜头）横向条。  
4. `GenerationWorkbench.tsx + MotionControlPanel.tsx`  
   - 对高频字段增加推荐值与历史复用。  

### 9.3 P2（中期）

1. [ReadinessPanels.tsx](F:/AI_Projects/h3/local_drama_studio/apps/web/src/features/status/ReadinessPanels.tsx)  
   - 交付目标创建从手填向预设创建迁移。  
2. `ProductionCanvasPanel.tsx` + `EpisodeReviewWorkspace.tsx`  
   - 把技术字段（如ID）默认折叠到高级面板。  
3. 全局统一枚举字典  
   - 建立共享常量源，避免同义不同控件的问题反复出现。  

---

## 10. 交付说明

这份评估聚焦于创作工作流的可用性收益，优先级按“阻塞率 × 误填率 × 返工率”排序。  
建议先按 P0 落地成 1~2 周小版本，再按 P1 做一次“主流程 8 小时体验回归”，确认卡点是否下降。  
文档已写入：

- `F:\AI_Projects\h3\local_drama_studio\docs\creator-experience-comprehensive-ux-logic-review-2026-08-24.md`
