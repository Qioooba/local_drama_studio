import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createGenerationIntent,
  createProject,
  createPrompt,
  createShotRevision,
  getJob,
  getProjectEpisodeCatalog,
  markShotProductionReady,
  planGenerationVariant,
  planProjectCreation,
  promoteJobArtifactToMedia,
  requestJson,
  resolveProfileCameraPlan,
  submitGenerationVariant,
  type Profile,
} from "../../generated/api";
import { OneSentenceVideoWizard } from "./OneSentenceVideoWizard";

vi.mock("../../generated/api", () => ({
  createGenerationIntent: vi.fn(),
  createProject: vi.fn(),
  createPrompt: vi.fn(),
  createShotRevision: vi.fn(),
  getJob: vi.fn(),
  getProjectEpisodeCatalog: vi.fn(),
  markShotProductionReady: vi.fn(),
  planGenerationVariant: vi.fn(),
  planProjectCreation: vi.fn(),
  promoteJobArtifactToMedia: vi.fn(),
  requestJson: vi.fn(),
  resolveProfileCameraPlan: vi.fn(),
  submitGenerationVariant: vi.fn(),
}));

const deepSeek = {
  capability: "LLM_STORY_PARSE",
  status: "PUBLISHED",
  title: "DeepSeek Remote",
  version_id: "deepseek-v1",
  version_no: 1,
  capability_contract: {
    provider: "OPENAI_COMPAT",
    base_url: "https://api.deepseek.com",
    model: "deepseek-chat",
  },
} as unknown as Profile;

const verifiedH3 = {
  capability: "VIDEO_T2V",
  status: "PUBLISHED",
  title: "H3 Core",
  version_id: "h3-v5",
  version_no: 5,
  capability_contract: {
    camera: { support: "PROMPT_FALLBACK" },
    manifest_capability: {
      node_family: "comfy_extras.MiniMaxH3ImageToVideo",
      playable_success_verified_in_this_run: true,
    },
  },
} as unknown as Profile;

const knownCrashingLegacy = {
  ...verifiedH3,
  title: "Legacy RH H3",
  version_id: "h3-v2",
  version_no: 2,
  capability_contract: {
    camera: { support: "PROMPT_FALLBACK" },
    manifest_capability: {
      node_family: "Direct T2VA",
      playable_success_verified_in_this_run: true,
    },
  },
} as unknown as Profile;

describe("OneSentenceVideoWizard", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(requestJson).mockImplementation(async (path) => {
      if (path === "/api/v1/local-llm/video-prompt:expand") {
        return { plan: {
          schema_version: "localdrama.one-sentence-video-plan.v1",
          title: "雨夜橘猫",
          video_prompt: "cinematic orange cat walking in a neon rainy street",
          director_intent: { shot_type: "MEDIUM" },
          camera_movement: "DOLLY_IN",
          provider: "OPENAI_COMPAT",
          model: "deepseek-chat",
          remote: true,
        } } as never;
      }
      return { shot: { id: "shot-1" } } as never;
    });
    vi.mocked(planProjectCreation).mockResolvedValue({ plan: { status: "READY", blockers: [] } } as never);
    vi.mocked(createProject).mockResolvedValue({ project: { id: "project-1" } } as never);
    vi.mocked(getProjectEpisodeCatalog).mockResolvedValue({ catalog: { seasons: [{ episodes: [{ id: "episode-1" }] }] } } as never);
    vi.mocked(resolveProfileCameraPlan).mockResolvedValue({ resolution: {
      submission_allowed: true,
      support: "PROMPT_FALLBACK",
      camera_plan: { shot_type: "MEDIUM", movement: "DOLLY_IN" },
    } } as never);
    vi.mocked(createShotRevision).mockResolvedValue({} as never);
    vi.mocked(markShotProductionReady).mockResolvedValue({} as never);
    vi.mocked(createGenerationIntent).mockResolvedValue({ intent: { id: "intent-1" } } as never);
    vi.mocked(createPrompt).mockResolvedValue({ revision: { id: "prompt-revision-1" } } as never);
    vi.mocked(planGenerationVariant).mockResolvedValue({ plan: { plan_hash: "plan-hash" } } as never);
    vi.mocked(submitGenerationVariant).mockResolvedValue({ job: { id: "job-1", state: "QUEUED" } } as never);
    vi.mocked(getJob).mockResolvedValue({ job: {
      id: "job-1",
      state: "SUCCEEDED",
      attempts: [{ artifacts: [{ id: "artifact-1", kind: "COMFY_OUTPUT", status: "VERIFIED" }] }],
    } } as never);
    vi.mocked(promoteJobArtifactToMedia).mockResolvedValue({ media: { id: "media-1" } } as never);
  });

  it("hides known-bad profiles and completes the page flow through playable media promotion", async () => {
    render(<MemoryRouter><OneSentenceVideoWizard profiles={[knownCrashingLegacy, verifiedH3, deepSeek]} /></MemoryRouter>);

    fireEvent.click(screen.getByText("模型与远端设置"));
    const t2vSelect = screen.getByRole("combobox", { name: "本机文字生成视频模型" });
    expect(within(t2vSelect).getAllByRole("option")).toHaveLength(1);
    expect((within(t2vSelect).getByRole("option", { name: "H3 Core · 第 5 版" }) as HTMLOptionElement).value).toBe("h3-v5");
    expect(screen.queryByRole("option", { name: /Legacy RH/ })).toBeNull();

    fireEvent.change(screen.getByRole("textbox", { name: "你想看到什么？" }), {
      target: { value: "雨夜霓虹灯下，一只橘猫撑伞穿过街道。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "一句话生成视频" }));

    expect(await screen.findByText("成片已保存")).toBeTruthy();
    expect(promoteJobArtifactToMedia).toHaveBeenCalledWith("artifact-1", {
      purpose: "SHOT_VIDEO",
      media_kind: "VIDEO",
      stage: "FORMAL",
    });
    const projectPayload = vi.mocked(planProjectCreation).mock.calls[0][0];
    expect(projectPayload).toMatchObject({
      width: 480,
      height: 832,
      profile_bindings: [{ capability: "LLM_STORY_PARSE", profile_version_id: "deepseek-v1" }, { capability: "VIDEO_T2V", profile_version_id: "h3-v5" }],
      delivery_target: { spec: { width: 480, height: 832 } },
    });
    expect(vi.mocked(requestJson).mock.calls[0][1]).toMatchObject({
      method: "POST",
      body: expect.stringContaining('"allow_remote_outbound":true'),
    });
    await waitFor(() => expect(document.querySelector("video")?.getAttribute("src")).toBe("/api/v1/media-versions/media-1/content"));
    const resultVideo = screen.getByLabelText("一句话生成的成片预览");
    expect(resultVideo.getAttribute("preload")).toBe("none");
    expect(resultVideo.getAttribute("poster")).toBe("/api/v1/media-versions/media-1/thumbnail?size=medium&frame=poster");
  });
});
