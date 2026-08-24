# 全部文本框审计与重构结果

审计日期：2026-08-24  
范围：`apps/web/src` 中所有非测试 TSX；识别所有 `<textarea>`，以及未声明 `type`、`text`、`url`、`password` 和动态字符串类型的 `<input>`。  
结论：静态文本型控件由 137 个降到 102 个，减少 35 个；当前 102 个分布在 46 个文件中，已经逐项复核。

## 判定标准

- 用户在表达创作内容、名称、搜索词、审核结论或变更原因：保留文本输入，并提供可理解的标签、单位或示例。
- 值来自有限集合：改为下拉框、单选卡、复选组选项或滑杆。
- 值来自项目、模型、任务、媒体、文件或已有记录：改为选择器，由后台列出真实候选。
- 值是机器码、内部 ID、随机种子、并发数、技术模式或可推导结果：自动生成或只读展示，不再伪装成输入框。
- 任意 JSON：普通工作流改为结构化编辑器；完整契约只读核对，不要求用户手写。
- 密钥：只在专用安全配置页收集；一次性返回值用代码块和复制按钮展示。

## 本轮直接重构

1. 实验轴从 CSV/参数名/并发数字文本框改成语义化复选项；并发与展开上限由系统管理。
2. Seed 批次改成候选数量下拉框和“换一组随机 Seed”，不再输入逗号列表。
3. 项目、场次、镜头分组、复制/拆分、资产、配方、工作流和连接的机器码改为自动生成。
4. 媒体类型、运镜采用方式、拆分编号等只读输入框改为事实展示。
5. 审计 action、subject、actor 等过滤条件改为由真实记录生成的下拉框。
6. 模型页在缺少路由项目时提供项目下拉框，不再要求编辑 URL。
7. 一句话生成页移除 API Key 和“记住密钥”，统一进入安全模型配置。
8. Profile 工作流创建移除 code、seed、占位 prompt 等实现细节。
9. Profile 的 4 个裸 JSON 编辑区改为常用结构化控件加完整契约只读核对。
10. 创作资料库移除 JSON 模式，改为递归结构化表单；资料编号自动生成。
11. 项目包 inbox 文件名改为后台只读扫描后的下拉框；新增安全的 inbox 列表接口。
12. 字幕字体改为下拉框，字幕正文改为多行创作输入。
13. 导演运镜强度改为滑杆，时长统一以“秒”呈现并在内部转换为毫秒。
14. 对白编号和音色技术标识自动生成；成功的 TTS Job、Windows 音色等来自真实下拉列表。
15. 剧本文档默认使用拖放/本地文件选择器；高级服务端路径也只通过系统浏览器取得。
16. 删除了未挂载、重复要求用户手输绝对目录的 `LocalModelScanForm`。
17. 自动化 token 和 Provider secret 不再放进只读文本框，改为短时/一次性安全展示与复制。

## 当前全部 102 个文本型控件

下面不是抽样，而是当前源码的完整静态清单。“保留”代表该字段确实要求用户表达自然语言；“高级保留”代表只有技术管理场景才能提供该值，并已放在对应配置区域或条件分支中。

| 文件 | 当前文本字段 | 结论 |
|---|---|---|
| `asset-bible-v2/CharacterIdentityPackPanel.tsx` | 造型名称；审核说明；废弃原因 | 保留：名称与人工判断 |
| `canvas/ProductionCanvasPanel.tsx` | 搜索节点 | 保留：搜索 |
| `commands/CommandPalette.tsx` | 命令/实体搜索 | 保留：搜索 |
| `director-v2/DirectorIntentEditor.tsx` | 主体动作；画面创作意图；身体动作；兼容运镜补充描述；环境差异与补充；连续性变化 | 保留：导演创作；兼容描述仅在系统裁决为 Prompt 降级时出现 |
| `director-v2/DirectorPerformanceControls.tsx` | 自定义情绪；自定义微表情；自定义视线 | 保留：仅在预设不足时使用 |
| `episode-plan-v2/ShotGroupPlanner.tsx` | 标题 | 保留：分组名称 |
| `episode-review-v2/EpisodeReviewWorkspace.tsx` | 搜索镜头 | 保留：搜索 |
| `generation/GenerationExperimentPanel.tsx` | 实验标题 | 保留：实验命名 |
| `generation/GenerationWorkbench.tsx` | 生成描述 | 保留：核心创作 Prompt |
| `generation/PostProcessPanel.tsx` | 标题 | 保留：配方命名；技术标识已自动生成 |
| `model-config/ProfileOverrideFields.tsx` | Schema 动态字符串字段 | 高级保留：只对 Profile 声明为字符串的覆盖项生成，枚举/布尔/数值已用对应控件 |
| `model-config/ProviderConnectionsPanel.tsx` | 连接名称；Base URL；默认 Model；环境变量名；替换密钥 | 名称保留；其余高级保留，仅用于远端模型连接管理；连接代码已自动生成 |
| `preferences-v2/GenerationPreferencePanel.tsx` | 审计备注 | 保留：可选背景说明 |
| `production/DeliveryWorkflowPanel.tsx` | 审核依据/撤回原因 | 保留：人工决策原因 |
| `production/DirectorShotEditor.tsx` | 资产名称；主体动作；兼容运镜补充描述；对白；环境；连续性；创作意图 | 保留：镜头创作；资产编号和运镜模式已自动化 |
| `production/EpisodeReviewPanel.tsx` | 备注/拒绝原因 | 保留：审核结论 |
| `production/PromptTemplatePanel.tsx` | 标题；模板；展开结果；负向词 | 保留：明确的高级 Prompt 创作工作台 |
| `production/StoryAssetLibraryPanel.tsx` | 名称；补充描述 | 保留：资产内容；描述已改为多行输入 |
| `production/SubtitleRevisionPanel.tsx` | 字幕文本；模板名称 | 保留：字幕创作与模板命名；字体已改下拉 |
| `profiles/LocalLLMConfigurationPanel.tsx` | 自定义 Base URL；自定义模型名；远端 API Key | 高级保留：只有选择“自定义”或远端服务时显示，预设用户只看事实 |
| `profiles/ProfileConfigurationPanel.tsx` | 两处显示标题；撤销原因 | 保留：用户可读名称与审计原因；code/seed/prompt 已后台生成 |
| `projects/AIDraftReviewPanel.tsx` | 场次标题；场次摘要；出场角色；画面；动作；对白；修改说明 | 保留：人工修订 AI 创作草稿 |
| `projects/CreativeLibrary.tsx` | 结构化数组文本；结构化长文本；变更说明；回退说明；标题；建立说明 | 保留：资料创作与版本原因；JSON 和机器码已移除 |
| `projects/EpisodeSceneRanges.tsx` | 场次标题；范围说明 | 保留：场次内容；编号自动递增 |
| `projects/OneSentenceVideoWizard.tsx` | 你想看到什么 | 保留：唯一核心创作输入 |
| `projects/ProjectAssetGrantPanel.tsx` | 撤回授权原因 | 保留：审计原因 |
| `projects/ProjectCreateWizard.tsx` | 作品标题 | 保留：唯一自由内容；技术标识和规格均自动/预设化 |
| `projects/ProjectPackageAction.tsx` | 新项目标题 | 保留：副本显示名称；包文件和技术标识自动处理 |
| `projects/ProjectStructureAppendPanel.tsx` | 季度标题；分集标题 | 保留：内容命名 |
| `projects/ProjectTemplateCopyAction.tsx` | 新项目标题 | 保留：副本显示名称；技术标识自动生成 |
| `qc-policy-v2/QcPolicyManager.tsx` | 变更原因 | 保留：策略审计原因 |
| `recipes-v2/DirectorRecipeManager.tsx` | 升级原因；标题；发布原因 | 保留：名称与版本审计；Recipe code 自动生成 |
| `reviews/ReviewInboxPanel.tsx` | 单条拒绝原因；批量拒绝原因 | 保留：审核反馈 |
| `reviews/VideoAnnotations.tsx` | 问题描述 | 保留：画面批注 |
| `shared/AutomationPanel.tsx` | 接口名称；回环端点 | 名称保留；端点高级保留，因为本机脚本可监听不同 loopback 端口和路径 |
| `shared/AutomationWorkflowPanel.tsx` | 任务名称；测试流程标题 | 保留：工作流命名 |
| `shared/BrandKitPanel.tsx` | 规范名称；水印文字 | 保留：品牌内容 |
| `shared/OutboxDeliveryPanel.tsx` | 接收端 URL | 高级保留：外部接收系统的真实地址无法由本项目推导 |
| `shared/ProjectLocalResourceSelect.tsx` | 自定义相对路径 | 高级保留：默认是项目资源下拉框，仅在显式选择“自定义路径”时出现 |
| `shared/WorkspaceAssetAuthorizationPanel.tsx` | 撤回授权原因 | 保留：审计原因 |
| `status/DeliveryTargetSetup.tsx` | 规格名称 | 保留：交付预设名称；尺寸/帧率等使用结构化控件 |
| `status/DialogueGovernanceActions.tsx` | 说话人；剧本文本；新文本；发音原词；读音；音色名称；真实冒烟短句 | 保留：对白与发音内容；编号、音色 code、候选、情绪、模型、任务均自动或选择化 |
| `status/LocalModelReferenceForm.tsx` | 工作站模型文件夹 | 高级保留：仅局域网浏览器访问另一台模型工作站时出现；本机默认使用文件选择器 |
| `status/ModelLicenseEvidenceForm.tsx` | 许可证名称 | 保留：证据的人类可读名称；证据文件来自项目资源选择器 |
| `pages/AssetBiblePage.tsx` | 状态名称；资产名称；补充描述；归档/恢复原因 | 保留：资产创作和生命周期原因 |
| `pages/ProjectsPage.tsx` | 搜索项目 | 保留：搜索 |

## 有意保留的技术输入边界

11 个技术输入没有被伪装成普通创作字段：Provider Connection 的 Base URL、Model、环境变量名和替换密钥；自定义/远端 LLM 的 URL、模型和密钥；Automation 回环端点；Outbox 接收 URL；项目资源的显式自定义相对路径；局域网工作站模型目录。它们分别只存在于模型、自动化、交付或高级条件分支中，且都无法从当前数据库可靠推导。除此之外，普通创作流程不再要求用户填写 ID、JSON、机器码、枚举常量、微秒、逗号列表或文件名。
