export const TTS_EMOTION_OPTIONS = [
  { value: "NEUTRAL", label: "中性" },
  { value: "CALM", label: "平静" },
  { value: "HAPPY", label: "开心" },
  { value: "SAD", label: "悲伤" },
  { value: "ANGRY", label: "愤怒" },
  { value: "FEARFUL", label: "恐惧" },
  { value: "SURPRISED", label: "惊讶" },
  { value: "TENSE", label: "紧张 / 警觉" },
] as const;

export const TTS_EMOTION_LABELS = Object.fromEntries(
  TTS_EMOTION_OPTIONS.flatMap((option) => [[option.value, option.label], [option.value.toLowerCase(), option.label]]),
) as Record<string, string>;

export const TTS_MODEL_REF_OPTIONS = [
  { value: "IMPORTED_LOCAL_AUDIO", label: "导入的本地音频" },
  { value: "WINDOWS_SAPI_LOCAL", label: "Windows 系统语音（本机生成）" },
] as const;

export const TTS_CANDIDATE_KIND_LABELS: Record<string, string> = {
  PREVIEW: "试听候选",
  FORMAL: "正式候选",
};
