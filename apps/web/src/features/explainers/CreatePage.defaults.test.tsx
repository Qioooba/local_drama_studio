/**
 * V03: a new project must request ONE captioned edition, not four.
 *
 * The page used to default to bilingual subtitles, a vertical edition and a
 * separate English edition all switched on, so every request produced four
 * editions — an English TTS run and a second render nobody asked for (§1.3,
 * case V03).  Extra outputs are now opt-in, every edition key is derived from
 * the language, subtitle treatment and aspect it actually describes, and the
 * whole create flow starts from “你现在有什么” (§B2.1).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createExplainer, listCapabilityOptions, patchExplainerVisualPreferences, preflightExplainerPlan } from "../../generated/api";
import { resetCommandIdCache } from "../../services/commandId";
import { ExplainerCreatePage } from "./CreatePage";

vi.mock("../../generated/api", () => ({
  createExplainer: vi.fn(),
  preflightExplainerPlan: vi.fn(),
  importExplainerSource: vi.fn(),
  startExplainerRun: vi.fn(),
  patchExplainerVisualPreferences: vi.fn(),
  listCapabilityOptions: vi.fn(),
}));

type OutputRequest = {
  edition_key: string;
  voice_locale: string;
  subtitle_mode: string;
  subtitle_locales: string[];
  aspect_ratio: string;
};

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ExplainerCreatePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function lastPayload(): { outputs: OutputRequest[] } {
  const calls = vi.mocked(createExplainer).mock.calls;
  return calls[calls.length - 1][0] as unknown as { outputs: OutputRequest[] };
}

/** The shortest real path through the new first screen: 已有口播稿 + 正文. */
async function createOnce(title: string) {
  fireEvent.click(screen.getByRole("button", { name: /已有口播稿/ }));
  fireEvent.change(screen.getByLabelText("作品标题"), { target: { value: title } });
  fireEvent.change(screen.getByLabelText("口播稿正文"), { target: { value: "第一段口播稿正文。" } });
  fireEvent.click(screen.getByRole("button", { name: "一键生成到预览" }));
  await waitFor(() => expect(createExplainer).toHaveBeenCalled());
}

describe("explainer default outputs (V03)", () => {
  beforeEach(() => {
    resetCommandIdCache();
    vi.mocked(createExplainer).mockReset();
    vi.mocked(preflightExplainerPlan).mockReset();
    vi.mocked(listCapabilityOptions).mockResolvedValue({
      capability: "LLM_STORY_PARSE",
      scope: { project_id: null, episode_id: null, shot_id: null },
      selection: { mode: "AUTO", source: "NONE", profile_version_id: null, ready: false, option: null, blockers: [] },
      options: [],
      configured_runtime: null,
      summary: { total_count: 0, selectable_count: 0, blocked_count: 0 },
      repair_href: "/system/capabilities",
      read_only: true,
      runtime_contacted: false,
      network_contacted: false,
      mutated: false,
    } as never);
    vi.mocked(createExplainer).mockResolvedValue({ project: { id: "project-1" }, video: { revision: 3 } } as never);
    vi.mocked(preflightExplainerPlan).mockResolvedValue({
      executable: true,
      plan_hash: "p".repeat(64),
      blockers: [],
      capability_snapshot: { probed: true },
      estimate: { stage: "ESTIMATED", note: "" },
      categories: {},
      task_skeleton: [],
    } as never);
  });

  afterEach(() => {
    cleanup();
  });

  it("requests exactly one captioned edition in the current language and aspect", async () => {
    mount();
    await createOnce("深海生物为什么会发光");

    const outputs = lastPayload().outputs;
    expect(outputs).toHaveLength(1);
    expect(outputs[0].edition_key).toBe("zh-captioned-169");
    expect(outputs[0].subtitle_mode).toBe("BURNED");
    expect(outputs[0].aspect_ratio).toBe("16:9");
    expect(outputs[0].voice_locale).toBe("zh-CN");
    // No automatic English narration and no vertical render.
    expect(outputs.filter((item) => item.voice_locale.startsWith("en"))).toHaveLength(0);
    expect(outputs.filter((item) => item.aspect_ratio === "9:16")).toHaveLength(0);
    // The SRT comes from the captioned edition, so no subtitle-less default either.
    expect(outputs.filter((item) => item.subtitle_mode === "NONE")).toHaveLength(0);
  });

  it("keeps the edition key true to the chosen aspect", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: /已有口播稿/ }));
    fireEvent.change(screen.getByLabelText("作品标题"), { target: { value: "竖版测试" } });
    fireEvent.change(screen.getByLabelText("口播稿正文"), { target: { value: "竖版正文。" } });
    fireEvent.change(screen.getByLabelText("输出画幅"), { target: { value: "9:16" } });
    fireEvent.click(screen.getByRole("button", { name: "一键生成到预览" }));
    await waitFor(() => expect(createExplainer).toHaveBeenCalled());

    const outputs = lastPayload().outputs;
    expect(outputs).toHaveLength(1);
    // A vertical edition must not be labelled with the 16:9 key.
    expect(outputs[0].edition_key).toBe("zh-captioned-916");
    expect(outputs[0].aspect_ratio).toBe("9:16");
  });

  it("adds extra editions only when the operator opts in", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: /已有口播稿/ }));
    fireEvent.change(screen.getByLabelText("作品标题"), { target: { value: "额外输出版" } });
    fireEvent.change(screen.getByLabelText("口播稿正文"), { target: { value: "额外输出版正文。" } });
    fireEvent.click(screen.getByLabelText(/额外输出无字幕干净版/));
    fireEvent.click(screen.getByLabelText(/中文配音 \+ 中英双语字幕/));
    fireEvent.click(screen.getByLabelText(/独立英语配音版/));
    fireEvent.click(screen.getByLabelText(/竖版输出/));
    fireEvent.click(screen.getByRole("button", { name: "一键生成到预览" }));
    await waitFor(() => expect(createExplainer).toHaveBeenCalled());

    const keys = lastPayload().outputs.map((item) => item.edition_key);
    expect(keys).toEqual([
      "zh-bilingual-169",
      "zh-captioned-916",
      "zh-clean-169",
      "en-captioned-169",
    ]);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("never exposes a target-duration control for 已有口播稿", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: /已有口播稿/ }));
    // §B2.2: only 自然时长; a 3-minute target can never silently rewrite the稿.
    expect(screen.queryByLabelText("时长")).toBeNull();
    expect(screen.getByText("自然时长")).toBeTruthy();
    // 故事资料 keeps the real 3 / 5 / 10 分钟 presets.
    fireEvent.click(screen.getByRole("button", { name: /故事资料/ }));
    expect(screen.getByLabelText("时长")).toBeTruthy();
  });

  it("no longer offers any 片段策略 / 静图推拉 choice", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: /故事资料/ }));
    // The product removed 静图推拉 and the whole visual-strategy choice: an explainer
    // picture is always produced by real AI 图生视频, so there is no selector left.
    expect(screen.queryByLabelText("片段策略")).toBeNull();
    expect(screen.queryByText("静图推拉")).toBeNull();
    expect(screen.queryByText(/全部 AI 动态/)).toBeNull();
    expect(screen.queryByText(/关键镜头 AI 动态/)).toBeNull();
    // The 图生视频 capability is still reported honestly when it is missing.
    expect(screen.getByText(/需要真实可执行的图生视频能力/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "前往能力与模型" })).toBeTruthy();
  });

  it("writes only the style preferences and never a removed visual_strategy", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: /已有口播稿/ }));
    fireEvent.change(screen.getByLabelText("作品标题"), { target: { value: "只写风格" } });
    fireEvent.change(screen.getByLabelText("口播稿正文"), { target: { value: "正文。" } });
    fireEvent.change(screen.getByLabelText("风格描述"), { target: { value: "低饱和写实纪录片质感。" } });
    fireEvent.click(screen.getByRole("button", { name: "一键生成到预览" }));

    await waitFor(() => expect(patchExplainerVisualPreferences).toHaveBeenCalled());
    const [, patch] = vi.mocked(patchExplainerVisualPreferences).mock.calls[0];
    const preferences = (patch as { visual_preferences: Record<string, unknown> }).visual_preferences;
    expect(preferences.style_prompt_override).toBe("低饱和写实纪录片质感。");
    // The generated client still declares `visual_strategy` (pending regeneration);
    // the page must simply not send it any more.
    expect("visual_strategy" in preferences).toBe(false);
  });
});
