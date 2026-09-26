/**
 * Step 3 (配音) page tests — design §B4 and the quality rules of §E3.
 *
 * They lock the decisions the design calls out for the narration step:
 *
 * * a missing take never fabricates a duration (「时长待实测」/「尚未生成」),
 * * the voice list is the real local scan; without one the page shows the single
 *   fixed voice instead of inventing 男/女 options,
 * * 语速 is hidden behind 「由当前声音配置决定」 until the profile declares rates,
 * * nothing autoplays and starting one player pauses the others,
 * * clicking a historical take only auditions it; 采用此配音 states that no adopt
 *   command exists instead of pretending the clock changed,
 * * 下一步：分镜与画面 is only available when every segment is really aligned,
 * * there is no second body editor on this page (it links back to step 1),
 * * extra English editions are never default-created here.
 *
 * Matchers are plain Vitest assertions; this repository does not register
 * jest-dom matchers globally.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../generated/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../generated/api")>();
  return {
    ...actual,
    getExplainerOverview: vi.fn(),
    listExplainerEditions: vi.fn(),
    getExplainerNarration: vi.fn(),
    getExplainerSubtitles: vi.fn(),
    resynthesizeExplainerNarration: vi.fn(),
    adoptExplainerNarrationTake: vi.fn(),
    patchExplainerSegment: vi.fn(),
    discoverLocalSapiVoices: vi.fn(),
    listVoiceProfileVersions: vi.fn(),
    publishProjectLocalSapiVoiceProfile: vi.fn(),
    listCapabilityOptions: vi.fn(),
    preflightExplainerPlan: vi.fn(),
    startExplainerRun: vi.fn(),
  };
});

import * as api from "../../generated/api";
import { ExplainerActionBarProvider, ExplainerStepActionBar } from "./ExplainerStepActionBar";
import type { ExplainerStepStatusMap } from "./ExplainerSteps";
import {
  ExplainerAudioPage,
  alignmentLabel,
  supportedSpeechRates,
  supportedTtsLanguages,
  takeAudioUrl,
} from "./AudioPage";

const EDITION = {
  id: "e1",
  edition_key: "zh-captioned-169",
  voice_locale: "zh-CN",
  aspect_ratio: "16:9",
  subtitle_mode: "BURNED",
  duration_policy: "NATURAL_NARRATION",
  subtitle_locales_json: ["zh-CN"],
  frozen_script_revision_id: "rev-1",
  width: 854,
  height: 480,
};

const OVERVIEW = {
  project_id: "p1",
  video: { id: "v1", project_id: "p1", title: "灯塔", revision: 3, aspect_ratio: "16:9", source_locale: "zh-CN" },
  editions: [],
  beat_count: 0,
  render_type_counts: {},
  latest_run: null,
  open_issues: [],
  open_issue_count: 0,
  blocking_issue_count: 0,
  active_decisions: [],
  authority_labels: { machine: "自动检查结果", human: "人工确认", publication: "发布授权" },
  capability_snapshot: { probed: true, capabilities: [], unknown_count: 0, unavailable_count: 0 },
};

const SEGMENTS = [
  {
    id: "s1",
    canonical_segment_id: "seg_001",
    display_text: "第一段：灯塔在雾里亮起。",
    spoken_text: "第一段：灯塔在雾里亮起。",
    ordinal: 0,
    revision: 4,
    pronunciation_map_json: [],
  },
  {
    id: "s2",
    canonical_segment_id: "seg_002",
    display_text: "第二段：值班员记录下这次故障。",
    spoken_text: "第二段：值班员记录下这次闪光。",
    ordinal: 1,
    revision: 4,
    pronunciation_map_json: [{ display: "闪光", spoken: "shǎn guāng" }],
  },
];

const SEGMENT_STATES = [
  {
    canonical_segment_id: "seg_001",
    segment_id: "s1",
    ordinal: 0,
    display_text: SEGMENTS[0].display_text,
    spoken_text: SEGMENTS[0].spoken_text,
    pause_after_ms: 200,
    selected_take_id: "t1",
    take_no: 1,
    measured_duration_ms: 3200,
    alignment_status: "ALIGNED",
    alignment_error: null,
    media_version_id: "mv1",
    audio_url: "/api/v1/media-versions/mv1/content",
    state: "ALIGNED",
  },
  {
    canonical_segment_id: "seg_002",
    segment_id: "s2",
    ordinal: 1,
    display_text: SEGMENTS[1].display_text,
    spoken_text: SEGMENTS[1].spoken_text,
    pause_after_ms: 0,
    selected_take_id: "t3",
    take_no: 2,
    measured_duration_ms: 4100,
    alignment_status: "ALIGNED",
    alignment_error: null,
    media_version_id: "mv3",
    audio_url: "/api/v1/media-versions/mv3/content",
    state: "ALIGNED",
  },
];

const TAKES = [
  { id: "t1", canonical_segment_id: "seg_001", take_no: 1, selected: 1, measured_duration_ms: 3200, media_version_id: "mv1" },
  { id: "t2", canonical_segment_id: "seg_001", take_no: 2, selected: 0, measured_duration_ms: 3300, media_version_id: "mv2", reason: "LOCAL_RE_READ" },
  { id: "t3", canonical_segment_id: "seg_002", take_no: 2, selected: 1, measured_duration_ms: 4100, media_version_id: "mv3" },
];

const CAPABILITY_OPTIONS = {
  capability: "TTS",
  scope: { project_id: "p1", episode_id: null, shot_id: null },
  selection: {
    mode: "AUTO" as const,
    source: "PROJECT",
    profile_version_id: "pv-tts",
    ready: true,
    option: {
      profile_version_id: "pv-tts",
      profile: { id: "prof", code: "tts", title: "本机 TTS", version_no: 1, status: "PUBLISHED" },
      model: { name: "Windows SAPI", provider: "local" },
      runtime: { id: "rt", title: "本机", status: "READY", transport: "LOCAL" },
      workflow: { id: null, title: null, status: null },
      selectable: true,
      availability: "READY" as const,
      blockers: [],
      warnings: [],
      execution_fingerprint: null,
    },
  },
  options: [],
  configured_runtime: null,
  summary: { total_count: 1, selectable_count: 1, blocked_count: 0 },
  repair_href: "/system/capabilities",
  read_only: true as const,
  runtime_contacted: false as const,
  network_contacted: false as const,
  mutated: false as const,
};

const STATUSES: ExplainerStepStatusMap = {
  script: "DONE",
  assets: "DONE",
  audio: "RUNNING",
  storyboard: "NOT_STARTED",
  clips: "NOT_STARTED",
  review: "NOT_STARTED",
};

function narrationFixture(overrides: Record<string, unknown> = {}) {
  return {
    edition_id: "e1",
    video_id: "v1",
    voice_locale: "zh-CN",
    frozen_script_revision_id: "rev-1",
    segments: SEGMENTS,
    segment_states: SEGMENT_STATES,
    takes: TAKES,
    historical_takes: [],
    alignments: [],
    measured_total_ms: 7300,
    segment_count: 2,
    aligned_segment_count: 2,
    independent_clock: true,
    clock_source: "NATURAL_NARRATION",
    null_means_not_generated: true,
    ...overrides,
  };
}

function renderAudioPage(path = "/explainers/p1/audio") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/explainers/:projectId/audio"
            element={
              <ExplainerActionBarProvider>
                <ExplainerAudioPage />
                <ExplainerStepActionBar projectId="p1" activePage="audio" statuses={STATUSES} />
              </ExplainerActionBarProvider>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, client };
}

beforeEach(() => {
  vi.clearAllMocks();
  HTMLMediaElement.prototype.play = vi.fn() as unknown as () => Promise<void>;
  HTMLMediaElement.prototype.pause = vi.fn();
  vi.mocked(api.getExplainerOverview).mockResolvedValue(OVERVIEW as never);
  vi.mocked(api.listExplainerEditions).mockResolvedValue({
    video_id: "v1",
    editions: [EDITION],
    independent_clocks: {},
    english_timing_copied_from_source_locale: false,
  } as never);
  vi.mocked(api.getExplainerNarration).mockResolvedValue(narrationFixture() as never);
  vi.mocked(api.getExplainerSubtitles).mockResolvedValue({
    edition_id: "e1",
    locale: "zh-CN",
    revision: { id: "sub-1", revision_no: 2 },
    cues: [
      { start_ms: 0, end_ms: 3200, text: "字幕一：灯塔在雾里亮起。", segment_canonical_id: "seg_001", font_size_px: 48 },
      { start_ms: 3200, end_ms: 7300, text: "字幕二：值班员记录下这次故障。", segment_canonical_id: "seg_002", font_size_px: 48 },
    ],
    rendered: "1\r\n00:00:00,000 --> 00:00:03,200\r\n字幕一\r\n",
    format: "JSON",
  } as never);
  vi.mocked(api.discoverLocalSapiVoices).mockResolvedValue({
    status: "AVAILABLE",
    items: [
      { name: "Microsoft Huihui", culture: "zh-CN", gender: "Female", age: "Adult", voice_ref: "sapi:huihui" },
      { name: "Microsoft Zira", culture: "en-US", gender: "Female", age: "Adult", voice_ref: "sapi:zira" },
    ],
    message: null,
    runtime_contacted: true,
    network_contacted: false,
    mutated: false,
  } as never);
  vi.mocked(api.listVoiceProfileVersions).mockResolvedValue({
    items: [
      {
        id: "vp1",
        project_id: "p1",
        code: "sapi-local",
        version_no: 1,
        title: "Microsoft Huihui",
        voice_ref: "sapi:huihui",
        license_status: "VERIFIED_LOCAL",
        license_evidence: {},
        provider_profile_version_id: null,
        status: "ACTIVE",
      },
    ],
  } as never);
  vi.mocked(api.publishProjectLocalSapiVoiceProfile).mockResolvedValue({
    voice_profile: {
      id: "vp2",
      code: "sapi-local",
      version_no: 2,
      title: "Microsoft Huihui",
      voice_ref: "sapi:huihui",
      license_status: "VERIFIED_LOCAL",
      license_evidence: {},
      provider_profile_version_id: null,
      status: "ACTIVE",
      evidence: { smoke_sha256: "a".repeat(64), ffprobe: { duration_ms: 2400 } },
    },
  } as never);
  vi.mocked(api.listCapabilityOptions).mockResolvedValue(CAPABILITY_OPTIONS as never);
  vi.mocked(api.resynthesizeExplainerNarration).mockResolvedValue({
    status: "ACCEPTED",
    job_id: "job-reread-1",
    idempotency_key: "key-1",
  } as never);
  vi.mocked(api.patchExplainerSegment).mockResolvedValue({ segment: { id: "s1", revision: 5 } } as never);
  vi.mocked(api.adoptExplainerNarrationTake).mockResolvedValue({
    take_id: "take-2",
    adoption_authority: "HUMAN",
    measured_duration_ms: 1200,
    superseded_take_ids: [],
  } as never);
});

describe("audio helpers", () => {
  it("never reports a take as alignment", () => {
    expect(alignmentLabel("ALIGNED").text).toBe("已对齐");
    expect(alignmentLabel("AUDIO_READY").text).toBe("有音频·未对齐");
    expect(alignmentLabel("ALIGNING").text).toBe("正在匹配字幕时间");
    expect(alignmentLabel(null).text).toBe("未生成");
  });

  it("intersects the profile's real rates with the standard set", () => {
    expect(supportedSpeechRates([0.9, 1.1, 1.4])).toEqual([0.9, 1.1]);
    expect(supportedSpeechRates([])).toEqual([]);
    expect(supportedSpeechRates(["oops"])).toEqual([]);
    expect(supportedSpeechRates(undefined)).toEqual([]);
  });

  it("lists only languages the discovered TTS really reports", () => {
    expect(supportedTtsLanguages([{ culture: "zh-CN" }, { culture: "en-US" }, { culture: "" }])).toEqual(["en-US", "zh-CN"]);
    expect(supportedTtsLanguages([])).toEqual([]);
  });

  it("builds a real media URL for a historical take, and none without media", () => {
    expect(takeAudioUrl({ media_version_id: "mv9" })).toBe("/api/v1/media-versions/mv9/content");
    expect(takeAudioUrl({ media_version_id: null })).toBeNull();
    expect(takeAudioUrl(null)).toBeNull();
  });
});

describe("audio page — narration reading and audition", () => {
  it("shows the real measured total, the real voices and the real cue list", async () => {
    renderAudioPage();
    await waitFor(() => expect(screen.getByText("第一段：灯塔在雾里亮起。")).toBeTruthy());
    expect(screen.getAllByText("00:07").length).toBeGreaterThan(0); // 7300 ms measured total
    expect(screen.getAllByText("已对齐").length).toBe(2);
    // Real local voice names, never fabricated 男/女 options.
    await waitFor(() => expect(screen.getByLabelText("声音")).toBeTruthy());
    const voiceSelect = screen.getByLabelText("声音") as HTMLSelectElement;
    await waitFor(() =>
      expect(Array.from(voiceSelect.options).map((option) => option.textContent)).toEqual([
        "Microsoft Huihui（zh-CN）",
        "Microsoft Zira（en-US）",
      ]),
    );
    expect(screen.queryByText(/男声|女声/)).toBeNull();
    // The subtitle cue list is complete, not just cues[0].
    expect(screen.getByText("字幕二：值班员记录下这次故障。")).toBeTruthy();
    expect(screen.getByRole("img", { name: /字幕时间线：共 2 条/ })).toBeTruthy();
  });

  it("does not offer a speech-rate control the profile does not support", async () => {
    renderAudioPage();
    await waitFor(() => expect(screen.getByText("由当前声音配置决定")).toBeTruthy());
    expect(screen.queryByLabelText("语速")).toBeNull();
  });

  it("offers 语速 only when the profile really declares a supported rate", async () => {
    vi.mocked(api.getExplainerNarration).mockResolvedValue(
      narrationFixture({ supported_speech_rates: [0.9, 1.0, 1.1] }) as never,
    );
    renderAudioPage();
    await waitFor(() => expect(screen.getByLabelText("语速")).toBeTruthy());
    const select = screen.getByLabelText("语速") as HTMLSelectElement;
    expect(Array.from(select.options).map((option) => option.value)).toEqual(["0.9", "1", "1.1"]);
  });

  it("never autoplays, and starting one player pauses the others", async () => {
    renderAudioPage();
    await waitFor(() => expect(document.querySelectorAll("audio").length).toBe(2));
    expect(HTMLMediaElement.prototype.play).not.toHaveBeenCalled();

    // Per-element spies: the prototype spy is shared, so it cannot prove *which*
    // element was paused.
    const audios = Array.from(document.querySelectorAll("audio"));
    const pauseSpies = audios.map((audio) => {
      const spy = vi.fn();
      audio.pause = spy as unknown as () => void;
      return spy;
    });
    fireEvent(audios[0], new Event("play"));
    expect(pauseSpies[1]).toHaveBeenCalledTimes(1);
    expect(pauseSpies[0]).not.toHaveBeenCalled();
  });

  it("plays a paragraph only through the explicit 播放 action", async () => {
    renderAudioPage();
    await waitFor(() => expect(document.querySelectorAll("audio").length).toBe(2));
    const playButtons = screen.getAllByRole("button", { name: "播放" });
    expect(playButtons.length).toBe(2);
    expect(HTMLMediaElement.prototype.play).not.toHaveBeenCalled();
  });

  it("auditions a historical take without adopting it, and explains why 采用此配音 is disabled", async () => {
    renderAudioPage();
    await waitFor(() => expect(screen.getByText("第一段：灯塔在雾里亮起。")).toBeTruthy());
    const historySummaries = screen.getAllByText(/更多：已生成版本/);
    fireEvent.click(historySummaries[0]);
    const auditionButtons = screen.getAllByRole("button", { name: "试听" });
    fireEvent.click(auditionButtons[0]);
    await waitFor(() => expect(screen.getByText(/正在试听历史 take/)).toBeTruthy());
    // Auditioning writes nothing at all: no re-read, no adoption.
    expect(api.resynthesizeExplainerNarration).not.toHaveBeenCalled();
    expect(api.patchExplainerSegment).not.toHaveBeenCalled();
    expect(api.adoptExplainerNarrationTake).not.toHaveBeenCalled();

    // Each disabled 采用此配音 button names its real reason: already adopted, or the
    // take has no measured duration to use as the clock.
    const adoptButtons = screen.getAllByRole("button", { name: "采用此配音" });
    expect(adoptButtons.length).toBeGreaterThan(0);
    for (const button of adoptButtons) {
      if (button.hasAttribute("disabled")) {
        expect(button.getAttribute("title") ?? "").toMatch(/已经是当前采用版本|没有实测时长/);
      }
    }
  });

  it("adopts an existing take through the real command and reports the human adoption", async () => {
    renderAudioPage();
    await waitFor(() => expect(screen.getByText("第一段：灯塔在雾里亮起。")).toBeTruthy());
    fireEvent.click(screen.getAllByText(/更多：已生成版本/)[0]);
    const adoptable = screen
      .getAllByRole("button", { name: "采用此配音" })
      .filter((button) => !button.hasAttribute("disabled"));
    expect(adoptable.length).toBeGreaterThan(0);
    fireEvent.click(adoptable[0]);
    await waitFor(() => expect(api.adoptExplainerNarrationTake).toHaveBeenCalledTimes(1));
    const [editionId, takeId] = vi.mocked(api.adoptExplainerNarrationTake).mock.calls[0];
    expect(editionId).toBe("e1");
    expect(takeId).toBeTruthy();
    await waitFor(() => expect(screen.getByText(/已采用 take/)).toBeTruthy());
    // Adoption never regenerates audio: it only switches which existing take is used.
    expect(api.resynthesizeExplainerNarration).not.toHaveBeenCalled();
  });

  it("reports a re-read only when the server really accepted a job", async () => {
    renderAudioPage();
    await waitFor(() => expect(screen.getByText("第一段：灯塔在雾里亮起。")).toBeTruthy());
    fireEvent.click(screen.getAllByRole("button", { name: "重读这一段" })[0]);
    await waitFor(() => expect(api.resynthesizeExplainerNarration).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.resynthesizeExplainerNarration).mock.calls[0][0]).toBe("e1");
    expect(vi.mocked(api.resynthesizeExplainerNarration).mock.calls[0][3]).toMatch(/^[0-9a-f-]{36}$/);
    await waitFor(() => expect(screen.getByText(/已提交单段重读任务 job-reread-1/)).toBeTruthy());
  });

  it("says a re-read was not accepted instead of claiming success", async () => {
    vi.mocked(api.resynthesizeExplainerNarration).mockResolvedValue({
      status: "CAPABILITY_UNAVAILABLE",
      reason: "本机没有可执行的 TTS 工作流",
    } as never);
    renderAudioPage();
    await waitFor(() => expect(screen.getByText("第一段：灯塔在雾里亮起。")).toBeTruthy());
    fireEvent.click(screen.getAllByRole("button", { name: "重读这一段" })[0]);
    await waitFor(() => expect(screen.getByText(/重读未被接受：本机没有可执行的 TTS 工作流/)).toBeTruthy());
    expect(screen.queryByText(/已提交单段重读任务/)).toBeNull();
  });

  it("sends the operator back to step 1 for the body and keeps no second body editor", async () => {
    renderAudioPage();
    await waitFor(() => expect(screen.getByText("第一段：灯塔在雾里亮起。")).toBeTruthy());
    const links = screen.getAllByRole("link", { name: /编辑本段正文/ });
    expect(links[0].getAttribute("href")).toBe("/explainers/p1/script?segment=s1");
    expect(document.querySelectorAll("textarea").length).toBe(0);
  });

  it("saves a pronunciation correction as a new script revision and says what it invalidates", async () => {
    renderAudioPage();
    await waitFor(() => expect(screen.getByText("第一段：灯塔在雾里亮起。")).toBeTruthy());
    fireEvent.click(screen.getAllByText(/发音修正（显示名 → 读法）/)[0]);
    fireEvent.click(screen.getAllByRole("button", { name: "新增一条读法" })[0]);
    const displayInputs = screen.getAllByLabelText("显示名");
    const spokenInputs = screen.getAllByLabelText("读法");
    // The first paragraph's own row (the second paragraph already has a stored one).
    fireEvent.change(displayInputs[0], { target: { value: "闪光" } });
    fireEvent.change(spokenInputs[0], { target: { value: "shǎn guāng" } });
    fireEvent.click(screen.getAllByRole("button", { name: "保存发音修正" })[0]);
    await waitFor(() => expect(api.patchExplainerSegment).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.patchExplainerSegment).mock.calls[0][1]).toBe("s1");
    expect(vi.mocked(api.patchExplainerSegment).mock.calls[0][2]).toMatchObject({
      expected_revision: 4,
      expected_script_revision_id: "rev-1",
      pronunciation_map: [{ display: "闪光", spoken: "shǎn guāng" }],
    });
    await waitFor(() => expect(screen.getByText(/发音修正已保存/)).toBeTruthy());
  });

  it("shows the fixed voice when the local scan reports nothing, and never fakes one", async () => {
    vi.mocked(api.discoverLocalSapiVoices).mockResolvedValue({
      status: "UNAVAILABLE",
      items: [],
      message: "本机 SAPI 不可用",
      runtime_contacted: false,
      network_contacted: false,
      mutated: false,
    } as never);
    renderAudioPage();
    await waitFor(() => expect(screen.getByText(/本机没有报告可选声音/)).toBeTruthy());
    const voiceSelect = screen.getByLabelText("声音") as HTMLSelectElement;
    expect(voiceSelect.disabled).toBe(true);
    expect(voiceSelect.options[0].textContent).toBe("Microsoft Huihui（固定声音）");
    const audition = screen.getByRole("button", { name: "试听声音" });
    expect(audition.hasAttribute("disabled")).toBe(true);
    expect(audition.getAttribute("title")).toContain("先选择一个本机扫描到的声音");
  });

  it("auditions a real voice through the local smoke synthesis", async () => {
    renderAudioPage();
    const voiceSelect = (await screen.findByLabelText("声音")) as HTMLSelectElement;
    // Wait for the real local scan before selecting: the fallback select has no
    // voice options and would silently keep the empty value.
    await waitFor(() =>
      expect(Array.from(voiceSelect.options).some((option) => option.value === "sapi:huihui")).toBe(true),
    );
    fireEvent.change(voiceSelect, { target: { value: "sapi:huihui" } });
    const audition = screen.getByRole("button", { name: "试听声音" });
    expect(audition.hasAttribute("disabled")).toBe(false);
    fireEvent.click(audition);
    await waitFor(() => expect(api.publishProjectLocalSapiVoiceProfile).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.publishProjectLocalSapiVoiceProfile).mock.calls[0][1]).toMatchObject({
      voice_ref: "sapi:huihui",
      smoke_text: "第一段：灯塔在雾里亮起。",
    });
    await waitFor(() => expect(screen.getByText(/本机真实合成一段试听 WAV/)).toBeTruthy());
  });

  it("keeps the last known narration when a refetch fails", async () => {
    const { client } = renderAudioPage();
    await waitFor(() => expect(screen.getByText("第一段：灯塔在雾里亮起。")).toBeTruthy());
    vi.mocked(api.getExplainerNarration).mockRejectedValue(new Error("旁白接口 500"));
    await act(async () => {
      await client.invalidateQueries();
    });
    await waitFor(() => expect(screen.getByText(/旁白更新失败：旁白接口 500/)).toBeTruthy());
    expect(screen.getByText("第一段：灯塔在雾里亮起。")).toBeTruthy();
  });
});

describe("audio page — states and the bottom bar", () => {
  it("shows an explicit no-capability state without inventing a duration", async () => {
    vi.mocked(api.getExplainerNarration).mockResolvedValue({
      edition_id: "e1",
      video_id: "v1",
      locale: "zh-CN",
      segments: SEGMENTS,
      takes: [],
      alignments: [],
      measured_total_ms: null,
      frozen_script_revision_id: "rev-1",
      independent_clock: true,
      clock_source: "NATURAL_NARRATION",
      null_means_not_generated: true,
    } as never);
    renderAudioPage();
    await waitFor(() => expect(screen.getByText("尚未生成配音")).toBeTruthy());
    expect(screen.getAllByText("时长待实测").length).toBe(2);
    expect(screen.getAllByText("尚未生成").length).toBeGreaterThan(0);
  });

  it("disables 生成全部配音 with the real reason when no script is frozen", async () => {
    vi.mocked(api.listExplainerEditions).mockResolvedValue({
      video_id: "v1",
      editions: [{ ...EDITION, frozen_script_revision_id: null }],
      independent_clocks: {},
      english_timing_copied_from_source_locale: false,
    } as never);
    vi.mocked(api.getExplainerNarration).mockResolvedValue(
      narrationFixture({ frozen_script_revision_id: null, takes: [], segment_states: [], measured_total_ms: null }) as never,
    );
    renderAudioPage();
    await waitFor(() => {
      const primary = screen.getByRole("button", { name: /生成全部配音/ });
      expect(primary.hasAttribute("disabled")).toBe(true);
      expect(primary.getAttribute("title")).toContain("还没有冻结讲稿");
    });
  });

  it("keeps 下一步：分镜与画面 处理中 until every segment is really aligned", async () => {
    vi.mocked(api.getExplainerNarration).mockResolvedValue(
      narrationFixture({
        measured_total_ms: 3200,
        aligned_segment_count: 1,
        segment_states: [
          SEGMENT_STATES[0],
          { ...SEGMENT_STATES[1], alignment_status: "RUNNING", state: "ALIGNING" },
        ],
      }) as never,
    );
    renderAudioPage();
    const primary = await screen.findByRole("button", { name: "下一步：分镜与画面" });
    expect(primary.hasAttribute("disabled")).toBe(true);
    expect(primary.getAttribute("title")).toContain("对齐仍在处理中");
    expect(screen.getAllByText(/正在匹配字幕时间/).length).toBeGreaterThan(0);
  });

  it("offers 下一步：分镜与画面 once every segment is measured and aligned", async () => {
    renderAudioPage();
    const primary = await screen.findByRole("button", { name: "下一步：分镜与画面" });
    expect(primary.hasAttribute("disabled")).toBe(false);
  });

  it("never default-creates an English edition and says where to add one", async () => {
    renderAudioPage();
    const languageSelect = (await screen.findByLabelText("配音语言")) as HTMLSelectElement;
    await waitFor(() => {
      const english = Array.from(languageSelect.options).find((option) => option.value === "en-US");
      expect(english?.disabled).toBe(true);
      expect(english?.textContent).toContain("需在第 6 步添加版本");
    });
    expect(api.listExplainerEditions).toHaveBeenCalledTimes(1);
  });
});
