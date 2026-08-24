"""界面文案本地化：英文 eyebrow/状态徽章/标签 → 中文（保留门禁代码与状态枚举）。"""
import pathlib
import sys

root = pathlib.Path("apps/web/src")

# (相对路径, 旧文本, 新文本, 期望出现次数)
REPLACEMENTS = [
    ("features/canvas/ProductionCanvasPanel.tsx", "G9 PRODUCTION CANVAS", "G9 生产画布", 1),
    ("features/generation/GenerationExperimentPanel.tsx", "FR-GEN-008 · MATRIX SAFETY", "FR-GEN-008 · 矩阵安全", 1),
    ("features/generation/GenerationWorkbench.tsx", "G6 EXIT READINESS", "G6 退出就绪门禁", 1),
    ("features/generation/GenerationWorkbench.tsx", "READ-ONLY I2V PROBE", "只读 I2V 探针", 1),
    ("features/generation/GenerationWorkbench.tsx", ">Capability Profile<", ">能力 Profile<", 1),
    ("features/generation/GenerationWorkbench.tsx", "Plan hash <code>", "计划哈希 <code>", 1),
    ("features/generation/GenerationWorkbench.tsx", "· recipe <code>", "· 配方 <code>", 1),
    ("features/generation/GenerationWorkbench.tsx", "<dd>ComfyUI loopback</dd>", "<dd>ComfyUI 回环</dd>", 1),
    ("features/generation/PostProcessPanel.tsx", "FR-PST-001/002 · LOCAL PROCESS", "FR-PST-001/002 · 本地后处理", 1),
    ("features/jobs/JobDetailsPanel.tsx", "FR-JOB-005 · ARTIFACT LINEAGE", "FR-JOB-005 · 产物谱系", 1),
    ("features/jobs/JobsPanel.tsx", "G5 TASKS & MACHINES", "G5 任务与机器", 1),
    ("features/jobs/JobsPanel.tsx", "G9 CAPACITY OBSERVATION", "G9 产能观测", 1),
    ("features/jobs/JobsPanel.tsx", "SSE / OUTBOX", "SSE / 发件箱", 1),
    ("features/jobs/JobsPanel.tsx", "OBSERVED_NOT_BENCHMARKED", "仅观测 · 非基准", 1),
    ("features/production/EpisodeReviewPanel.tsx", "FR-TML-003 · EPISODE REVIEW", "FR-TML-003 · 整集审核", 1),
    ("features/production/PromptTemplatePanel.tsx", "FR-WRT-004 · FROZEN", "FR-WRT-004 · 冻结", 1),
    ("features/production/SubtitleRevisionPanel.tsx", "FR-AUD-004 · SUBTITLE REVISION", "FR-AUD-004 · 字幕修订", 1),
    ("features/production/SubtitleRevisionPanel.tsx", ">SCRIPT AUTHORITY<", ">剧本权威<", 1),
    ("features/production/TimelineRevisionPanel.tsx", "FR-TML-001 · LIGHT TIMELINE", "FR-TML-001 · 轻量时间线", 1),
    ("features/production/TimelineRevisionPanel.tsx", ">VERSIONED<", ">版本化<", 1),
    ("features/profiles/ProfileConfigurationPanel.tsx", "G7 PROFILE CONFIGURATION", "G7 能力配置", 1),
    ("features/profiles/ProfileConfigurationPanel.tsx", ">WORKFLOW HISTORY<", ">工作流历史<", 1),
    ("features/profiles/ProfileConfigurationPanel.tsx", ">LOCAL_ONLY<", ">仅本地<", 1),
    ("features/projects/AIDraftReviewPanel.tsx", "FR-WRT-007 · local LLM drafts", "FR-WRT-007 · 本地 LLM 草稿", 1),
    ("features/projects/CreativeLibrary.tsx", "FR-WRT-001 · immutable history", "FR-WRT-001 · 不可变历史", 1),
    ("features/projects/CreativeLibrary.tsx", "<label>Code<input", "<label>代码<input", 1),
    ("features/projects/ProjectAssetGrantPanel.tsx", "FR-AST-001 · PROJECT ASSET GRANTS", "FR-AST-001 · 项目资产授权", 1),
    ("features/reviews/FormalSelectionPanel.tsx", "FR-VID-007 · DELIVERY SELECTION", "FR-VID-007 · 交付选择", 1),
    ("features/reviews/ImageCandidateGrid.tsx", "FR-IMG-003 · IMAGE CANDIDATES", "FR-IMG-003 · 图片候选", 1),
    ("features/reviews/ReviewInboxPanel.tsx", ">G4 REVIEW INBOX<", ">G4 审核收件箱<", 1),
    ("features/reviews/ReviewInboxPanel.tsx", ">EXPLICIT BATCH REVIEW<", ">显式批量审核<", 1),
    ("features/reviews/ReviewInboxPanel.tsx", ">SYNC COMPARE<", ">同步比较<", 1),
    ("features/shared/AuditHistoryPanel.tsx", "FR-AUDT-001 · APPEND-ONLY HISTORY", "FR-AUDT-001 · 追加审计历史", 1),
    ("features/shared/AuditHistoryPanel.tsx", "只读 · LOCAL_ONLY · 脱敏", "只读 · 仅本地 · 脱敏", 1),
    ("features/shared/AutomationPanel.tsx", "FR-AUT-002 · LOCAL AUTOMATION", "FR-AUT-002 · 本机自动化", 1),
    ("features/shared/AutomationPanel.tsx", ">LOOPBACK ONLY<", ">仅回环<", 1),
    ("features/shared/AutomationPanel.tsx", ">Client code<", ">客户端代码<", 1),
    ("features/shared/AutomationPanel.tsx", ">Loopback endpoint<", ">回环端点<", 1),
    ("features/shared/AutomationWorkflowPanel.tsx", "G11 · P0-4 WHOLE_DRAMA", "G11 · P0-4 整剧编排", 1),
    ("features/shared/AutomationWorkflowPanel.tsx", "FR-AUT-001 · DECLARATIVE HITL", "FR-AUT-001 · 声明式人工闸门", 1),
    ("features/shared/AutomationWorkflowPanel.tsx", "BATCH_AUTOMATED · LOCAL ONLY", "批量自动化 · 仅本地", 1),
    ("features/shared/AutomationWorkflowPanel.tsx", "BOUNDED · LOCAL ONLY", "有界 · 仅本地", 1),
    ("features/shared/AutomationWorkflowPanel.tsx", ">Workflow code<", ">工作流代码<", 1),
    ("features/shared/BrandKitPanel.tsx", "FR-PST-003 · BRAND / WATERMARK / COMPLIANCE", "FR-PST-003 · 品牌 / 水印 / 合规", 1),
    ("features/shared/BrandKitPanel.tsx", ">LOCAL VERSIONED<", ">本地版本化<", 1),
    ("features/shared/BrandKitPanel.tsx", ">BrandKit Token JSON<", ">BrandKit 令牌 JSON<", 1),
    ("features/shared/ComfyLabPanel.tsx", "FR-WFL-004 · DESIGNER SANDBOX", "FR-WFL-004 · Designer 沙盒", 1),
    ("features/shared/ComfyLabPanel.tsx", "LOCAL ONLY · NO FORMAL WRITE", "仅本地 · 不写正式目录", 1),
    ("features/shared/ComfyLabPanel.tsx", '<h3 id="comfy-lab-title">ComfyUI Lab</h3>', '<h3 id="comfy-lab-title">ComfyUI 实验室</h3>', 1),
    ("features/shared/ComfyLabPanel.tsx", "ComfyUI Lab：", "ComfyUI 实验室：", 1),
    ("features/shared/GlobalSearchPanel.tsx", "FR-SRC-001 · SEARCH", "FR-SRC-001 · 全局搜索", 1),
    ("features/shared/OutboxDeliveryPanel.tsx", "FR-AUT-002 · LOOPBACK OUTBOX", "FR-AUT-002 · 回环发件箱", 1),
    ("features/shared/ProjectHealthPanel.tsx", "FR-PRJ-007 · HEALTH CHECK", "FR-PRJ-007 · 项目健康检查", 1),
    ("features/shared/ProjectHealthPanel.tsx", "SQLite integrity_check", "SQLite 完整性检查", 1),
    ("features/shared/WorkspaceAssetAuthorizationPanel.tsx", "FR-AST-001 · PROJECT AUTHORIZATION", "FR-AST-001 · 项目授权", 1),
    ("features/shared/WorkspaceAssetAuthorizationPanel.tsx", ">LOCAL HASH<", ">本地哈希<", 1),
    ("features/status/AudioTrackPanel.tsx", "FR-AUD-002 · AUDIO TRACKS", "FR-AUD-002 · 音频轨道", 1),
    ("features/status/DialogueTTSPanel.tsx", "FR-AUD-001 · DIALOGUE / TTS", "FR-AUD-001 · 对白 / TTS", 1),
    ("features/status/DialogueTTSPanel.tsx", "G11 · MULTI-VOICE ORCHESTRATION", "G11 · 多音色编排", 1),
    ("features/status/DialogueTTSPanel.tsx", '"TTS PROFILE READY" : "TTS PROFILE MISSING"', '"TTS 配置就绪" : "TTS 配置缺失"', 1),
    ("features/status/DialogueTTSPanel.tsx", "未绑定 Published TTS Profile", "未绑定已发布 TTS Profile", 1),
    ("features/status/DialogueTTSPanel.tsx", "<small>Published TTS Profile</small>", "<small>已发布 TTS Profile</small>", 1),
    ("features/status/LocalModelScanForm.tsx", "FR-OPS-002 · OFFLINE SCAN", "FR-OPS-002 · 离线扫描", 1),
    ("features/status/ReadinessPanels.tsx", "G7 PROJECT CONFIGURATION", "G7 项目配置", 1),
    ("features/status/ReadinessPanels.tsx", "FR-DEL-003 · EXPLICIT TARGET", "FR-DEL-003 · 显式交付目标", 1),
    ("features/status/ReadinessPanels.tsx", "G11 P1-6 · PLATFORM PRESETS", "G11 P1-6 · 平台预设", 1),
    ("features/status/ReadinessPanels.tsx", "G7 ADAPTER SDK", "G7 适配器 SDK", 1),
    ("features/status/ReadinessPanels.tsx", "G7 LOCAL MODEL REFERENCES", "G7 本机模型引用", 1),
    ("features/status/ReadinessPanels.tsx", "G8 TIMELINE / DELIVERY", "G8 时间线 / 交付", 1),
    ("features/status/ReadinessPanels.tsx", "G8 FORMAL EXIT READINESS", "G8 正式退出就绪", 1),
    ("features/status/ReadinessPanels.tsx", "G9 FORMAL EXIT READINESS", "G9 正式退出就绪", 1),
    ("features/status/ReadinessPanels.tsx", "只读 · LOCAL_ONLY", "只读 · 仅本地", 1),
    ("features/status/ReadinessPanels.tsx", ">LOCAL_FILESYSTEM<", ">本地文件系统<", 1),
    ("features/status/ReadinessPanels.tsx", "<small>Timeline revision</small>", "<small>时间线修订</small>", 1),
    ("features/generation/GenerationControlPanel.tsx", "manifest loader：", "manifest 加载器：", 1),
    ("features/production/ContinuityPanel.tsx", '" · FROZEN" : ""', '" · 已冻结" : ""', 1),
    ("features/projects/ProjectPackageAction.tsx", "hash/schema/disk PASS", "哈希 / 结构 / 磁盘 PASS", 1),
    ("features/generation/MotionControlPanel.tsx", "<label>subject role<input", "<label>主体角色<input", 1),
    ("features/status/DialogueGovernanceActions.tsx", "Published TTS Profile", "已发布 TTS Profile", 4),
    ("features/status/DialogueGovernanceActions.tsx", "AUDIO MediaVersion ID", "AUDIO 媒体版本 ID", 1),
    ("features/status/DialogueGovernanceActions.tsx", "TTS Job ID", "TTS 任务 ID", 2),
    ("features/production/StoryAssetLibraryPanel.tsx", "<label>Code<input", "<label>代码<input", 1),
]

# 测试同步
TEST_REPLACEMENTS = [
    ("app/App.test.tsx", 'getByText("LOCAL_ONLY")', 'getByText("仅本地")', 1),
    ("features/shared/AuditHistoryPanel.test.tsx", "getByText(/LOCAL_ONLY/)", "getByText(/仅本地/)", 1),
    ("features/shared/ComfyLabPanel.test.tsx", 'findByText("ComfyUI Lab")', 'findByText("ComfyUI 实验室")', 1),
    ("features/status/DialogueGovernanceActions.test.tsx", '"Published TTS Profile"', '"已发布 TTS Profile"', 1),
    ("features/status/DialogueGovernanceActions.test.tsx", '"TTS Job ID"', '"TTS 任务 ID"', 1),
    ("features/status/DialogueGovernanceActions.test.tsx", '"AUDIO MediaVersion ID"', '"AUDIO 媒体版本 ID"', 1),
    ("features/generation/MotionControlPanel.test.tsx", '"subject role"', '"主体角色"', 1),
    ("features/projects/CreativeLibrary.test.tsx", 'getByLabelText("Code")', 'getByLabelText("代码")', 1),
    ("features/production/StoryAssetLibraryPanel.test.tsx", 'getByLabelText("Code")', 'getByLabelText("代码")', 1),
    ("features/status/DialogueTTSPanel.test.tsx", '"TTS PROFILE MISSING"', '"TTS 配置缺失"', 1),
]

def apply(fpath, old, new, expect):
    p = root / fpath
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != expect:
        print(f"!! {fpath}: 期望 {expect} 次，实际 {count} 次: {old[:50]}")
        return False
    p.write_text(text.replace(old, new), encoding="utf-8")
    return True

ok = True
for fpath, old, new, expect in REPLACEMENTS + TEST_REPLACEMENTS:
    if not apply(fpath, old, new, expect):
        ok = False
print("ALL OK" if ok else "HAS MISMATCHES")
sys.exit(0 if ok else 1)
