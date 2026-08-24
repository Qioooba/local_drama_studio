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
  CANCELLED: "已取消",
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
};

export function optionLabel(labels: Record<string, string>, value: string | null | undefined, fallback = "未标注") {
  if (!value) return fallback;
  return labels[value] ?? value;
}
