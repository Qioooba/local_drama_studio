export const LANGUAGE_OPTIONS = [
  { value: "zh-CN", label: "简体中文" },
  { value: "zh-TW", label: "繁体中文" },
  { value: "en-US", label: "英语（美国）" },
  { value: "en", label: "英语" },
  { value: "ja-JP", label: "日语" },
  { value: "ko-KR", label: "韩语" },
] as const;

export const LOCAL_MODEL_KIND_OPTIONS = ["T2V", "I2V", "VAE", "TTS", "LLM", "CLIP", "CONTROLNET", "UPSCALE"] as const;

export const LOCAL_MODEL_KIND_LABELS: Record<string, string> = {
  T2V: "文字生成视频模型",
  I2V: "图片生成视频模型",
  VAE: "图像编解码模型",
  TTS: "语音合成模型",
  LLM: "语言理解与创作模型",
  CLIP: "文字与画面理解模型",
  CONTROLNET: "画面结构控制模型",
  UPSCALE: "画面放大与修复模型",
};

export const MEDIA_PURPOSE_OPTIONS = [
  "GENERATED_OUTPUT", "SHOT_VIDEO_CANDIDATE", "ASSET_REFERENCE", "CONTINUITY_REFERENCE", "PROFILE_EVIDENCE",
  "DIALOGUE_TTS", "MOTION_CONTROL", "ENHANCEMENT", "FRAME_ANCHOR", "SCRIPT_SOURCE",
] as const;

export const MEDIA_PURPOSE_LABELS: Record<(typeof MEDIA_PURPOSE_OPTIONS)[number], string> = {
  GENERATED_OUTPUT: "模型生成结果",
  SHOT_VIDEO_CANDIDATE: "镜头视频候选",
  ASSET_REFERENCE: "资产参考素材",
  CONTINUITY_REFERENCE: "连续性参考素材",
  PROFILE_EVIDENCE: "模型能力验证证据",
  DIALOGUE_TTS: "对白语音",
  MOTION_CONTROL: "运动控制素材",
  ENHANCEMENT: "画质增强结果",
  FRAME_ANCHOR: "首尾帧锚点",
  SCRIPT_SOURCE: "剧本原文",
};

export const SUBJECT_ROLE_OPTIONS = ["subject", "face", "body", "foreground", "background"] as const;
