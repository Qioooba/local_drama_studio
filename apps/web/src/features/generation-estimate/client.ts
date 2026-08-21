import { getGenerationEstimate, type GenerationEstimate } from "../../generated/api";

export type GenerationEstimateResponse = GenerationEstimate;

export type GenerationEstimateRequest = {
  profileVersionId: string;
  width: number;
  height: number;
  durationSeconds?: number;
  frameCount?: number;
  steps?: number;
  gpuClass?: string;
};

/** Feature facade keeps UI naming stable while the generated client owns the wire contract. */
export function getLocalGenerationEstimate(input: GenerationEstimateRequest): Promise<GenerationEstimateResponse> {
  return getGenerationEstimate({
    profile_version_id: input.profileVersionId,
    width: input.width,
    height: input.height,
    duration_seconds: input.durationSeconds,
    frame_count: input.frameCount,
    steps: input.steps,
    gpu_class: input.gpuClass,
  });
}
