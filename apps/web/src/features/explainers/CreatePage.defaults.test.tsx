/**
 * V03: a new project must request ONE captioned edition, not four.
 *
 * The page defaulted to bilingual subtitles, a vertical edition and a separate
 * English edition all switched on, so every request produced four editions — an
 * English TTS run and a second render nobody asked for (design §1.3, case V03).
 * Extra outputs are now opt-in, and every edition key is derived from the language,
 * subtitle treatment and aspect it actually describes.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createExplainer, preflightExplainerPlan } from "../../generated/api";
import { resetCommandIdCache } from "../../services/commandId";
import { ExplainerCreatePage } from "./CreatePage";

vi.mock("../../generated/api", () => ({
  createExplainer: vi.fn(),
  preflightExplainerPlan: vi.fn(),
  importExplainerSource: vi.fn(),
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

function lastOutputs(): OutputRequest[] {
  const calls = vi.mocked(createExplainer).mock.calls;
  const payload = calls[calls.length - 1][0] as unknown as { outputs: OutputRequest[] };
  return payload.outputs;
}

async function createOnce(title: string) {
  fireEvent.change(screen.getByLabelText("标题 / 主题"), { target: { value: title } });
  // The default input mode is TOPIC, which requires the topic description too.
  fireEvent.change(screen.getByLabelText("主题描述"), { target: { value: "为什么会这样" } });
  fireEvent.click(screen.getByRole("button", { name: "检查并一键生成" }));
  await waitFor(() => expect(createExplainer).toHaveBeenCalled());
}

describe("explainer default outputs (V03)", () => {
  beforeEach(() => {
    resetCommandIdCache();
    vi.mocked(createExplainer).mockReset();
    vi.mocked(preflightExplainerPlan).mockReset();
    vi.mocked(createExplainer).mockResolvedValue({ project: { id: "project-1" } } as never);
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

    const outputs = lastOutputs();
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
    fireEvent.change(screen.getByLabelText("输出画幅"), { target: { value: "9:16" } });
    await createOnce("竖版测试");

    const outputs = lastOutputs();
    expect(outputs).toHaveLength(1);
    // A vertical edition must not be labelled with the 16:9 key.
    expect(outputs[0].edition_key).toBe("zh-captioned-916");
    expect(outputs[0].aspect_ratio).toBe("9:16");
  });

  it("adds extra editions only when the operator opts in", async () => {
    mount();
    fireEvent.click(screen.getByLabelText(/额外输出无字幕干净版/));
    fireEvent.click(screen.getByLabelText(/中文配音 \+ 中英双语字幕/));
    fireEvent.click(screen.getByLabelText(/独立英语配音版/));
    fireEvent.click(screen.getByLabelText(/竖版输出/));
    await createOnce("额外输出版");

    const keys = lastOutputs().map((item) => item.edition_key);
    expect(keys).toEqual([
      "zh-bilingual-169",
      "zh-captioned-916",
      "zh-clean-169",
      "en-captioned-169",
    ]);
    expect(new Set(keys).size).toBe(keys.length);
  });
});
