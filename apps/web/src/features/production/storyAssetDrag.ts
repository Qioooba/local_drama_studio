import type { StoryAsset } from "../../generated/api";

export const STORY_ASSET_MIME = "application/x-localdrama-story-asset";

export type StoryAssetTransfer = Pick<StoryAsset, "id" | "project_id" | "kind" | "code" | "name" | "status">;

export function readStoryAssetTransfer(dataTransfer: DataTransfer): StoryAssetTransfer | null {
  try {
    const value = JSON.parse(dataTransfer.getData(STORY_ASSET_MIME)) as Partial<StoryAssetTransfer>;
    if (!value.id || !value.project_id || !value.kind || !value.code || !value.name || !value.status) return null;
    return value as StoryAssetTransfer;
  } catch {
    return null;
  }
}

export function storyAssetDropIssue(asset: StoryAssetTransfer, projectId: string, boundAssetIds: Set<string>) {
  if (asset.project_id !== projectId) return "资产属于其他项目，不能绑定到当前镜头";
  if (!["CHARACTER", "SCENE", "PROP", "COSTUME"].includes(asset.kind)) return "资产类型不支持镜头绑定";
  if (asset.status !== "ACTIVE") return "已归档资产不能创建新绑定";
  if (boundAssetIds.has(asset.id)) return "本镜已绑定该资产";
  return null;
}
