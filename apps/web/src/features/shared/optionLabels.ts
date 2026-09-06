export const MEDIA_KIND_LABELS: Record<string, string> = {
  IMAGE: "图片",
  VIDEO: "视频",
  AUDIO: "音频",
  DOCUMENT: "文档",
};

export const MEDIA_STAGE_LABELS: Record<string, string> = {
  PROXY: "预览代理",
  KEYFRAME: "关键帧",
  FORMAL: "正式成片",
  TIMELINE: "时间线成片",
  SOURCE: "原始素材",
  PREVIEW: "预览",
};

export const REVIEW_DECISION_LABELS: Record<string, string> = {
  APPROVED: "已批准",
  NEEDS_CHANGES: "需要修改",
  REJECTED: "已拒绝",
  PENDING: "待审核",
  STALE: "需要重新审核",
};

export const OWNER_SCOPE_LABELS: Record<string, string> = {
  PROJECT: "项目级",
  SEASON: "季度级",
  EPISODE: "分集级",
  SHOT: "镜头级",
  GLOBAL: "全局",
};

export const WORKFLOW_MODE_LABELS: Record<string, string> = {
  WHOLE_DRAMA: "整剧一键编排",
  EPISODE: "单集编排",
  MANUAL: "手动编排",
};

export const PRODUCTION_TIER_LABELS: Record<string, string> = {
  PREVIEW: "快速预览",
  BALANCED: "质量与速度平衡",
  FAST: "极速粗筛",
  DRAFT: "日常生成",
  SCREEN: "候选精筛",
  PRODUCTION: "正式成片",
  MASTER: "关键镜头精制",
  KEYFRAME: "关键帧生成",
};

export const ACCELERATION_LABELS: Record<string, string> = {
  OFF: "关闭加速",
  TURBO_LORA: "启用 Turbo LoRA 加速",
};

export const STATUS_LABELS: Record<string, string> = {
  ACTIVE: "启用中",
  APPLIED: "已应用",
  ARCHIVED: "已归档",
  AVAILABLE: "可用",
  BLOCKED: "已阻塞",
  BLOCKED_OFFLINE: "运行时离线",
  CANCELLED: "已取消",
  CANDIDATE_BLOCKED: "候选受阻",
  CANDIDATE_UNVERIFIED: "待验证候选",
  COMPLETE: "已完成",
  COMPLETED: "已完成",
  DIRECTED: "已完成导演设计",
  DRAFT: "草稿",
  DRAFT_READY: "草稿待审核",
  FAILED: "失败",
  FROZEN: "已冻结",
  NEEDS_ATTENTION: "需要处理",
  NOT_APPLIED: "尚未应用",
  NOT_AVAILABLE: "不可用",
  NOT_STARTED: "尚未开始",
  ORPHANED: "执行器已失联",
  PARTIALLY_APPLIED: "已部分应用",
  PAUSED_HITL: "等待人工确认",
  PAUSED: "已暂停",
  PENDING: "等待中",
  PLANNED: "已规划",
  PRODUCTION_READY: "可进入正式生产",
  PUBLISHED: "已发布",
  QUEUED: "排队中",
  READY: "可处理",
  RUNNING: "执行中",
  SUCCEEDED: "成功",
  SUPERSEDED: "已被新版本替代",
  VERIFIED: "已验证",
  WAITING: "等待中",
  CLAIMED: "正在处理",
  CANCEL_REQUESTED: "正在取消",
  CORRUPT: "文件异常",
  EXPIRED: "已过期",
  INACTIVE: "已停用",
  NEEDS_CHANGES: "需要修改",
  PASS: "已通过",
  REJECTED: "已拒绝",
  UNKNOWN: "状态未知",
  WITHDRAWN: "已撤回",
};

/**
 * These labels are deliberately user-facing, not literal translations of the
 * backend enum. The stable enum remains available in advanced/debug details.
 */
export const JOB_TYPE_LABELS: Record<string, string> = {
  STORY_PIPELINE_DRAFT: "AI 全剧规划",
  SCRIPT_BREAKDOWN_LOCAL_LLM: "AI 分集方案与分镜",
  GENERATION_VARIANT: "生成镜头候选",
  PROFILE_EVIDENCE_PROBE: "验证模型真实出图或视频",
  AUTOMATION_WORKFLOW_TASK: "推进分集制作步骤",
  MEDIA_DERIVATIVE: "生成媒体预览",
  MEDIA_THUMBNAIL: "生成缩略图",
  EPISODE_COMPOSE: "合成整集视频",
  SEGMENTED_EPISODE_COMPOSE: "分段合成整集视频",
  EPISODE_RENDER: "合成整集视频",
  DELIVERY_BUILD: "制作交付包",
  DELIVERY_PACKAGE: "制作交付包",
  DIALOGUE_TTS: "生成对白配音",
  POST_PROCESS: "画面后期处理",
  CONTACT_SHEET: "生成分镜联系表",
  TTS_GENERATION: "生成对白配音",
};

export const JOB_PHASE_LABELS: Record<string, string> = {
  ENCODING: "正在编码媒体",
  GENERATING: "正在生成",
  VERIFYING_SOURCE: "正在校验输入文件",
  VERIFYING_OUTPUT: "正在校验输出文件",
  REGISTERING_ARTIFACT: "正在登记产物",
  RENDERING: "正在渲染",
  PACKAGING: "正在打包",
};

export const JOB_CHANNEL_LABELS: Record<string, string> = {
  CPU: "常规处理",
  GPU: "显卡处理",
  GPU_H3: "显卡生成",
  LOCAL: "本机处理",
};

export const AUDIO_CANDIDATE_LABELS: Record<string, string> = {
  PREVIEW: "试听版",
  FORMAL: "正式配音候选",
  IMPORTED: "导入音频",
};

export const AUDIO_TRACK_LABELS: Record<string, string> = {
  DIALOGUE: "对白",
  BGM: "背景音乐",
  MUSIC: "音乐",
  SFX: "音效",
  ENVIRONMENT: "环境声",
};

export const GRAPH_NODE_STAGE_LABELS: Record<string, string> = {
  DIRECT: "导演设计",
  KEYFRAME: "关键帧",
  PROXY: "预览片",
  FORMAL: "正式镜头",
  TIMELINE: "时间线",
};

export const DEPENDENCY_LABELS: Record<string, string> = {
  TRANSITION_CONSTRAINT: "转场衔接约束",
  CONTINUITY_CONSTRAINT: "画面连续性约束",
  INPUT_DEPENDENCY: "生成输入依赖",
  UPSTREAM: "上游依赖",
  DOWNSTREAM: "下游影响",
};

export const AUTHORIZATION_IMPACT_LABELS: Record<string, string> = {
  SOURCE_AUTHORIZATION_REVOKED: "源素材授权已撤回",
  LICENSE_EVIDENCE_MISSING: "缺少授权证据",
  MEDIA_INTEGRITY_CHANGED: "媒体文件完整性已变化",
};

export const ARTIFACT_KIND_LABELS: Record<string, string> = {
  IMAGE: "图片文件",
  VIDEO: "视频文件",
  AUDIO: "音频文件",
  JSON: "结构化记录",
  MANIFEST: "交付清单",
  LOG: "执行日志",
};

export const WEBHOOK_STATUS_LABELS: Record<string, string> = {
  DISABLED: "未启用",
  LOOPBACK_EXPLICIT_BOUNDED: "仅允许本机按需投递",
  READY: "可用",
};

export function optionLabel(labels: Record<string, string>, value: string | null | undefined, fallback = "未标注") {
  if (!value) return fallback;
  return labels[value] ?? value;
}

/** Use for status badges where leaking a new raw enum would confuse creators. */
export function statusLabel(value: string | null | undefined, fallback = "状态未知") {
  if (!value) return fallback;
  return STATUS_LABELS[value] ?? fallback;
}

/** Use in normal UI; callers can expose the raw code separately in advanced details. */
export function userFacingLabel(labels: Record<string, string>, value: string | null | undefined, fallback = "未识别类型") {
  if (!value) return fallback;
  return labels[value] ?? fallback;
}
