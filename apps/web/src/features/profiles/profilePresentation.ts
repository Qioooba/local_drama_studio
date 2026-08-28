import { canonicalCapabilityLabel } from "../preferences-v2/canonicalCapabilities";

export type ProfileFamilyId = "story" | "image" | "video" | "audio" | "post" | "quality" | "other";

export const PROFILE_FAMILIES: Array<{ id: ProfileFamilyId; label: string; description: string }> = [
  { id: "story", label: "故事与文本", description: "剧本拆解、分集策划、分镜与提示词" },
  { id: "image", label: "图像生成", description: "概念图、角色、场景与画面编辑" },
  { id: "video", label: "视频生成", description: "文生视频、图生视频与参考控制" },
  { id: "audio", label: "声音生成", description: "对白、声音克隆、口型、音效与配乐" },
  { id: "post", label: "后期处理", description: "抽帧、超分、调色与合成" },
  { id: "quality", label: "质量检查", description: "画面、身份、连续性与音频质检" },
  { id: "other", label: "其他能力", description: "尚未归入标准创作阶段的能力" },
];

export function profileFamilyId(capability: string): ProfileFamilyId {
  const code = capability.toUpperCase();
  if (code.startsWith("LLM_")) return "story";
  if (code.startsWith("IMAGE_")) return "image";
  if (code.startsWith("VIDEO_")) return "video";
  if (["TTS", "VOICE_CLONE", "LIPSYNC", "AUDIO_SFX", "AUDIO_MUSIC"].includes(code)) return "audio";
  if (["FRAME_EXTRACT", "UPSCALE_IMAGE", "UPSCALE_VIDEO", "POST_PROCESS"].includes(code)) return "post";
  if (code.startsWith("QC_")) return "quality";
  return "other";
}

export function profileStageDescription(capability: string) {
  const family = profileFamilyId(capability);
  if (family === "story") return "用于前期策划、剧本整理和分镜准备阶段";
  if (family === "image") return "用于资产设定、概念图和镜头关键帧阶段";
  if (family === "video") return "用于镜头候选和正式视频生成阶段";
  if (family === "audio") return "用于对白、音效、配乐和音画同步阶段";
  if (family === "post") return "用于成片前的画面处理和交付准备阶段";
  if (family === "quality") return "用于候选审核和正式交付前的质量检查阶段";
  return `用于“${canonicalCapabilityLabel(capability)}”对应的执行阶段`;
}

const STATUS_PRESENTATION: Record<string, { label: string; description: string }> = {
  PUBLISHED: { label: "已发布", description: "可被所有项目和生成任务正式选择" },
  DRAFT: { label: "草稿", description: "正在编辑；验证并发布前不会进入正式生成" },
  CANDIDATE_UNVERIFIED: { label: "待验证候选", description: "从模型清单发现，尚未完成契约验证和真实生成验证" },
  CANDIDATE_BLOCKED: { label: "候选受阻", description: "已发现能力，但运行时、模型组件或验证条件尚未满足" },
  SUPERSEDED: { label: "已被替代", description: "保留用于历史追溯，新任务不应再选用" },
  RETIRED: { label: "已停用", description: "仅用于历史记录，不再进入新任务" },
};

export function profileStatusLabel(status: string) {
  return STATUS_PRESENTATION[status]?.label ?? status;
}

export function profileStatusDescription(status: string) {
  return STATUS_PRESENTATION[status]?.description ?? "该状态由版本治理流程维护";
}
