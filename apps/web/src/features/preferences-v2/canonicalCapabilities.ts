/**
 * Canonical Generation Capabilities and Normalization.
 * Matches backend local_drama.domain.capabilities.
 */

export const CANONICAL_CAPABILITIES = [
  // LLM
  "LLM_STORY_PARSE",
  "LLM_EPISODE_PLAN",
  "LLM_STORYBOARD",
  "LLM_PROMPT_REWRITE",

  // Image
  "IMAGE_CONCEPT",
  "IMAGE_CHARACTER",
  "IMAGE_SCENE",
  "IMAGE_EDIT",
  "IMAGE_MULTI_VIEW",
  "IMAGE_EXPRESSION",

  // Video
  "VIDEO_T2V",
  "VIDEO_I2V",
  "VIDEO_FIRST_FRAME",
  "VIDEO_FIRST_LAST_FRAME",
  "VIDEO_REFERENCE",
  "VIDEO_MOTION_CONTROL",

  // Audio
  "TTS",
  "VOICE_CLONE",
  "LIPSYNC",
  "AUDIO_SFX",
  "AUDIO_MUSIC",

  // Frame & Post
  "FRAME_EXTRACT",
  "UPSCALE_IMAGE",
  "UPSCALE_VIDEO",
  "POST_PROCESS",

  // QC
  "QC_VISUAL",
  "QC_FACE",
  "QC_IDENTITY",
  "QC_CONTINUITY",
  "QC_AUDIO",
] as const;

export type CanonicalCapability = (typeof CANONICAL_CAPABILITIES)[number];

export const CAPABILITY_ALIASES: Record<string, CanonicalCapability> = {
  // Script / Story Breakdown
  SCRIPT_BREAKDOWN_LLM: "LLM_STORY_PARSE",
  STORY_PARSE: "LLM_STORY_PARSE",
  STORY_BREAKDOWN: "LLM_STORY_PARSE",
  EPISODE_PLAN: "LLM_EPISODE_PLAN",
  PROMPT_REWRITE: "LLM_PROMPT_REWRITE",

  // Video
  I2V: "VIDEO_I2V",
  IMAGE_TO_VIDEO: "VIDEO_I2V",
  IMAGE2VIDEO: "VIDEO_I2V",
  T2V: "VIDEO_T2V",
  TEXT_TO_VIDEO: "VIDEO_T2V",
  TEXT2VIDEO: "VIDEO_T2V",
  FIRST_FRAME: "VIDEO_FIRST_FRAME",
  FIRST_LAST_FRAME: "VIDEO_FIRST_LAST_FRAME",
  VIDEO_FIRST_LAST: "VIDEO_FIRST_LAST_FRAME",
  MOTION_CONTROL: "VIDEO_MOTION_CONTROL",
  MOTION_BRUSH: "VIDEO_MOTION_CONTROL",

  // Audio
  AUDIO_TTS: "TTS",
  TEXT_TO_SPEECH: "TTS",
  AUDIO_CLONE: "VOICE_CLONE",
  AUDIO_VOICE_CLONE: "VOICE_CLONE",
  LIP_SYNC: "LIPSYNC",
  SFX: "AUDIO_SFX",
  MUSIC: "AUDIO_MUSIC",
  BGM: "AUDIO_MUSIC",
  BGM_GEN: "AUDIO_MUSIC",
  AUDIO_BGM: "AUDIO_MUSIC",

  // Image
  CHARACTER: "IMAGE_CHARACTER",
  SCENE: "IMAGE_SCENE",
  CONCEPT: "IMAGE_CONCEPT",
  MULTI_VIEW: "IMAGE_MULTI_VIEW",
  EXPRESSION: "IMAGE_EXPRESSION",
  EDIT: "IMAGE_EDIT",

  // Upscale / Post
  UPSCALE: "UPSCALE_IMAGE",
  SR_IMAGE: "UPSCALE_IMAGE",
  SR_VIDEO: "UPSCALE_VIDEO",
};

export const CAPABILITY_LABELS: Record<CanonicalCapability, string> = {
  LLM_STORY_PARSE: "剧本结构化拆解 (LLM)",
  LLM_EPISODE_PLAN: "分集策划与镜头规划 (LLM)",
  LLM_STORYBOARD: "分镜脚本细化 (LLM)",
  LLM_PROMPT_REWRITE: "导演提示词重写 (LLM)",

  IMAGE_CONCEPT: "概念图/设定图生成",
  IMAGE_CHARACTER: "角色立绘/设定图",
  IMAGE_SCENE: "场景背景图",
  IMAGE_EDIT: "画面局部重绘/编辑",
  IMAGE_MULTI_VIEW: "角色三视图生成",
  IMAGE_EXPRESSION: "角色多表情生成",

  VIDEO_T2V: "文生视频 (T2V)",
  VIDEO_I2V: "图生视频 (I2V)",
  VIDEO_FIRST_FRAME: "首帧驱动视频",
  VIDEO_FIRST_LAST_FRAME: "首尾帧过渡视频",
  VIDEO_REFERENCE: "参考角色动作视频",
  VIDEO_MOTION_CONTROL: "运动轨迹控制视频",

  TTS: "台词语音合成 (TTS)",
  VOICE_CLONE: "角色声音克隆",
  LIPSYNC: "对口型语音同步 (Lip Sync)",
  AUDIO_SFX: "动作音效生成 (SFX)",
  AUDIO_MUSIC: "配乐与背景音乐 (BGM)",

  FRAME_EXTRACT: "高精度关键帧提取",
  UPSCALE_IMAGE: "图像超分辨率超分",
  UPSCALE_VIDEO: "视频超分辨率超分",
  POST_PROCESS: "后期调色与合成",

  QC_VISUAL: "画面画质质检",
  QC_FACE: "人脸崩坏质检",
  QC_IDENTITY: "角色一致性质检",
  QC_CONTINUITY: "镜头连贯性质检",
  QC_AUDIO: "音频音画同步质检",
};

export function normalizeCapability(name: string): CanonicalCapability {
  const cleaned = (name || "").trim().toUpperCase();
  if (CANONICAL_CAPABILITIES.includes(cleaned as CanonicalCapability)) {
    return cleaned as CanonicalCapability;
  }
  if (cleaned in CAPABILITY_ALIASES) {
    return CAPABILITY_ALIASES[cleaned];
  }
  throw new Error(`未知的生成能力: ${name}`);
}

export function isCanonicalCapability(name: string): boolean {
  return CANONICAL_CAPABILITIES.includes((name || "").trim().toUpperCase() as CanonicalCapability);
}
