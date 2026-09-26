/**
 * 第 3 步：配音 (audio) — design §B4.
 *
 * Narration runs *before* the final storyboard timing, so this page is a
 * sequential reading + audition surface: the main column lists the paragraphs in
 * order with a small 播放 / 重读 action each, the 288 px settings column holds
 * the voice settings, and the top bar reports the current language, the segment
 * count and the **measured** total duration.  The internal clock enums
 * (`duration_policy`) are deliberately not shown any more — they are execution
 * details, not user wording (§B11).
 *
 * Hard rules carried from the design and kept by tests:
 *
 * * Nothing autoplays, and starting one player pauses every other media element
 *   on the page (`useExclusivePlayback`).
 * * A duration is never fabricated: without a take the paragraph says 时长待实测
 *   and the total says 尚未生成 (`null` is not `0`).
 * * 配音语言 lists only languages the **really available** TTS reports; extra
 *   English editions are added in step 6, never default-created here.
 * * 声音 is the real local voice list.  When no list exists the page shows the
 *   single fixed voice of the current configuration instead of inventing 男/女.
 * * 语速 is only offered when the profile declares allowed values; otherwise the
 *   row is hidden and says 「由当前声音配置决定」.
 * * 点击历史 take 只试听；采用需要明确动作, and since no adopt command exists the
 *   button states that instead of pretending the clock changed.
 * * Subtitle visual styling and background music live in step 6 and are not
 *   implemented here; the real cue list and cue timeline are.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  adoptExplainerNarrationTake,
  discoverLocalSapiVoices,
  listVoiceProfileVersions,
  patchExplainerSegment,
  publishProjectLocalSapiVoiceProfile,
  resynthesizeExplainerNarration,
  rerunExplainerNarrationAlign,
  type LocalSapiVoice,
  type VoiceProfileVersion,
} from "../../generated/api";
import { routes } from "../../app/routeRegistry";
import { queryKeys } from "../../query/queryKeys";
import { stableIdempotencyKey } from "../../services/commandId";
import { CapabilityPicker, useCapabilityOptions } from "../model-config/CapabilityPicker";
import { useExplainerActionBar } from "./ExplainerStepActionBar";
import { useExplainerPreviewStart } from "./ExplainerProgressDrawer";
import { InlineError, InlineOk, Panel, SettingRow, StateNotice, type PageState } from "./components";
import { NarrationPlayer } from "./media";
import { formatMs, localeLabel } from "./viewModels";
import {
  useExplainerEditions,
  useExplainerNarration,
  useExplainerOverview,
  useExplainerSubtitles,
} from "./useExplainerQueries";
import "./explainers.css";
import "./explainers.steps36.css";

/* -------------------------------------------------------------------------- */
/* pure helpers (exported so the page rules can be tested without a browser)   */
/* -------------------------------------------------------------------------- */

export type SegmentAlignmentState =
  | "NOT_GENERATED"
  | "AUDIO_READY"
  | "ALIGNING"
  | "ALIGNED"
  | "FAILED"
  | "STALE";

/** §B4/§F1 wording.  "有音频" is never reported as "已对齐". */
export const ALIGNMENT_LABELS: Record<SegmentAlignmentState, { text: string; tone: "green" | "warn" | "danger" | "" }> = {
  ALIGNED: { text: "已对齐", tone: "green" },
  ALIGNING: { text: "正在匹配字幕时间", tone: "" },
  AUDIO_READY: { text: "有音频·未对齐", tone: "warn" },
  FAILED: { text: "对齐失败", tone: "danger" },
  STALE: { text: "对齐已过期", tone: "danger" },
  NOT_GENERATED: { text: "未生成", tone: "warn" },
};

export function alignmentLabel(state: string | null | undefined): { text: string; tone: "green" | "warn" | "danger" | "" } {
  const key = String(state ?? "NOT_GENERATED") as SegmentAlignmentState;
  return ALIGNMENT_LABELS[key] ?? { text: key, tone: "warn" };
}

/** Shown instead of a 语速 control when the profile declares no allowed rates. */
export const SPEECH_RATE_CONFIGURED_BY_VOICE = "由当前声音配置决定";

/** The standard product suggestion (§B4), used as the intersection base only. */
export const STANDARD_SPEECH_RATES = [0.9, 1.0, 1.1, 1.2] as const;

/**
 * Real, profile-declared speech rates intersected with the standard set.
 *
 * There is no invented default: an empty result means "this profile does not
 * expose rates", and the page then hides the control and writes
 * `SPEECH_RATE_CONFIGURED_BY_VOICE`.
 */
export function supportedSpeechRates(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  const declared = value
    .map((item) => Number(item))
    .filter((item) => Number.isFinite(item) && item > 0);
  if (declared.length === 0) return [];
  return STANDARD_SPEECH_RATES.filter((rate) => declared.some((item) => Math.abs(item - rate) < 0.001));
}

/** Languages the *actually discovered* local TTS can speak (§B4). */
export function supportedTtsLanguages(voices: Array<Pick<LocalSapiVoice, "culture">>): string[] {
  const found = new Set<string>();
  for (const voice of voices) {
    const culture = String(voice.culture ?? "").trim();
    if (culture) found.add(culture);
  }
  return [...found].sort();
}

export function takeAudioUrl(take: Record<string, unknown> | null | undefined): string | null {
  const mediaVersionId = take ? String(take.media_version_id ?? "") : "";
  return mediaVersionId ? `/api/v1/media-versions/${encodeURIComponent(mediaVersionId)}/content` : null;
}

/**
 * §F1 task wording for step 3, derived from the real run rows only.
 *
 * A queued job waits for the GPU, a running one names the segment counter, and
 * an alignment job reports the subtitle timing work.  Nothing is invented when
 * the run carries no such task.
 */
export function narrationRunWording(
  steps: Array<Record<string, unknown>> | null | undefined,
  produced: number,
  total: number,
): string | null {
  const rows = steps ?? [];
  const align = rows.find((step) => String(step.planned_step_code ?? "") === "NARRATION_ALIGN");
  const tts = rows.find((step) => String(step.planned_step_code ?? "") === "NARRATION_TTS");
  const jobState = String(align?.job_state ?? tts?.job_state ?? "").toUpperCase();
  const taskStatus = String(align?.status ?? tts?.status ?? "").toUpperCase();
  if (jobState === "QUEUED" || jobState === "PENDING") return "等待开始 / 等待 GPU";
  if (jobState === "PREFLIGHT") return "检查制作条件";
  if (align && (jobState === "RUNNING" || taskStatus === "RUNNING")) return "正在匹配字幕时间";
  if (tts && (jobState === "RUNNING" || taskStatus === "RUNNING")) {
    return `正在配音第 ${Math.min(produced + 1, Math.max(total, 1))}/${Math.max(total, 1)} 段`;
  }
  return null;
}

/**
 * "Starting one player pauses the others" (§B4/E3).
 *
 * The shared `NarrationPlayer` owns its own `<audio>` element, so this hook
 * listens in the capture phase for any `play` event below the container and
 * pauses every other media element there.  Nothing ever calls `play()` on its
 * own: media only starts from an explicit user action.
 */
export function useExclusivePlayback(ref: React.RefObject<HTMLElement | null>) {
  useEffect(() => {
    const root = ref.current;
    if (!root) return undefined;
    const onPlay = (event: Event) => {
      const target = event.target as HTMLElement | null;
      const tag = String(target?.tagName ?? "").toUpperCase();
      if (tag !== "AUDIO" && tag !== "VIDEO") return;
      root.querySelectorAll<HTMLMediaElement>("audio, video").forEach((element) => {
        if (element !== target) element.pause();
      });
    };
    root.addEventListener("play", onPlay, true);
    return () => root.removeEventListener("play", onPlay, true);
  }, [ref]);
}

/* -------------------------------------------------------------------------- */
/* payload shapes (the narration read model is a Record<string, unknown>)       */
/* -------------------------------------------------------------------------- */

type SegmentState = {
  canonical_segment_id: string;
  segment_id: string;
  ordinal: number;
  display_text: string;
  spoken_text: string;
  pause_after_ms: number;
  selected_take_id: string | null;
  take_no: number | null;
  measured_duration_ms: number | null;
  alignment_status: string | null;
  /**
   * The server reports alignment trouble either as a short string or as the per-token
   * list that made it fail.  Stringifying the list rendered `[object Object]` on screen,
   * so the shape is typed honestly and formatted by `alignmentFailureText`.
   */
  alignment_error: string | Array<Record<string, unknown>> | null;
  media_version_id: string | null;
  audio_url: string | null;
  state: string;
};

/**
 * Human-readable alignment failure.
 *
 * The backend answers with either a message or the offending token list
 * (`[{token, token_index, reason, fabricated}]`).  The list is summarised as the failing
 * reason plus the first few characters, which is what a person can act on; the raw list
 * stays available through the segment's own API payload.
 */
export function alignmentFailureText(error: unknown): string {
  if (error === null || error === undefined || error === "") return "——";
  if (typeof error === "string") return error;
  if (Array.isArray(error)) {
    if (error.length === 0) return "——";
    const first = (error[0] ?? {}) as Record<string, unknown>;
    const reason = String(first.reason ?? "ALIGNMENT_FAILED");
    const tokens = error
      .slice(0, 6)
      .map((item) => String((item as Record<string, unknown>).token ?? ""))
      .join("");
    const suffix = error.length > 6 ? `…（共 ${error.length} 个词元）` : "";
    return tokens ? `${reason}：${tokens}${suffix}` : `${reason}${suffix}`;
  }
  if (typeof error === "object") {
    const record = error as Record<string, unknown>;
    return String(record.reason ?? record.message ?? JSON.stringify(record));
  }
  return String(error);
}

type PronunciationRow = { display: string; spoken: string };

function asRecords(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? (value as Array<Record<string, unknown>>) : [];
}

/* -------------------------------------------------------------------------- */
/* page                                                                       */
/* -------------------------------------------------------------------------- */

export function ExplainerAudioPage() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const editions = useExplainerEditions(projectId);
  const overview = useExplainerOverview(projectId);
  const editionList = asRecords(editions.data?.editions);
  const editionIdParam = searchParams.get("edition");
  const activeEdition = editionList.find((edition) => String(edition.id) === editionIdParam) ?? editionList[0] ?? null;
  const activeEditionId = activeEdition ? String(activeEdition.id) : null;
  // The narration clock is always the *edition's* voice locale.  Switching the
  // subtitle preview language must never move the voice clock (§B4).
  const voiceLocale = activeEdition ? String(activeEdition.voice_locale ?? "zh-CN") : "zh-CN";
  const subtitleLocales = Array.isArray(activeEdition?.subtitle_locales_json)
    ? (activeEdition?.subtitle_locales_json as unknown[]).map(String)
    : [];
  const subtitleLocaleParam = searchParams.get("locale");
  const subtitleLocale =
    subtitleLocaleParam && subtitleLocales.includes(subtitleLocaleParam)
      ? subtitleLocaleParam
      : subtitleLocales[0] ?? null;
  const narration = useExplainerNarration(activeEditionId, voiceLocale);
  const subtitles = useExplainerSubtitles(activeEditionId, subtitleLocale, "JSON");
  const segments = asRecords(narration.data?.segments);
  const segmentStates = asRecords(narration.data?.segment_states) as unknown as SegmentState[];
  const takes = asRecords(narration.data?.takes);
  const historicalTakes = asRecords(narration.data?.historical_takes);
  const cues = asRecords(subtitles.data?.cues);
  const frozenScriptRevisionId = narration.data?.frozen_script_revision_id
    ? String(narration.data.frozen_script_revision_id)
    : null;

  const [selectedSegmentLocator, setSelectedSegmentLocator] = useState<string | null>(null);
  const [auditionTake, setAuditionTake] = useState<{ segmentKey: string; takeId: string } | null>(null);
  const [pronunciationDraft, setPronunciationDraft] = useState<Record<string, PronunciationRow[]>>({});
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [voiceFeedback, setVoiceFeedback] = useState<string | null>(null);
  const [selectedVoiceRef, setSelectedVoiceRef] = useState("");
  const [ttsProfileId, setTtsProfileId] = useState("");
  const pageRef = useRef<HTMLDivElement | null>(null);
  useExclusivePlayback(pageRef);

  /* ----------------------------------------------------------- real TTS facts */
  const ttsOptions = useCapabilityOptions("TTS", { projectId });
  const voicesQuery = useQuery({
    queryKey: ["explainer-tts-voices"],
    queryFn: () => discoverLocalSapiVoices(),
    staleTime: 60_000,
  });
  const voiceProfilesQuery = useQuery({
    queryKey: ["explainer-voice-profiles", projectId],
    queryFn: () => listVoiceProfileVersions(projectId),
    enabled: Boolean(projectId),
    staleTime: 30_000,
  });
  const voices = voicesQuery.data?.items ?? [];
  const projectVoiceProfiles: VoiceProfileVersion[] = voiceProfilesQuery.data?.items ?? [];
  const languages = supportedTtsLanguages(voices);
  const declaredRates = supportedSpeechRates(
    narration.data?.supported_speech_rates ?? ttsOptions.data?.selection.option?.input_slots ?? [],
  );

  /* --------------------------------------------------------------- mutations */
  const reRead = useMutation({
    mutationFn: async (segment: Record<string, unknown>) =>
      resynthesizeExplainerNarration(
        activeEditionId ?? "",
        String(segment.canonical_segment_id),
        "LOCAL_RE_READ",
        stableIdempotencyKey("explainer-reread", {
          editionId: activeEditionId,
          canonicalSegmentId: String(segment.canonical_segment_id),
        }),
      ),
    onSuccess: async (result) => {
      setError(null);
      const record = result as Record<string, unknown>;
      const accepted = String(record.status ?? "") === "ACCEPTED";
      const jobId = record.job_id ? String(record.job_id) : "";
      setFeedback(
        accepted && jobId
          ? `已提交单段重读任务 ${jobId}；邻段会重新做拼接检查，新的实测时长会进入后续时间线。`
          : `重读未被接受：${String(record.reason ?? record.status ?? "未知")}。没有创建后台任务。`,
      );
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  /**
   * 「重新对齐旁白」.  Alignment revisions are append-only and the align stage skips
   * takes that already have one, so a take whose stored clock is unusable can only
   * be re-measured from here.  Each take becomes its own real job.
   */
  const realign = useMutation({
    mutationFn: async () =>
      rerunExplainerNarrationAlign(
        activeEditionId ?? "",
        stableIdempotencyKey("explainer-realign", { editionId: activeEditionId }),
      ),
    onSuccess: async (result) => {
      const record = result as Record<string, unknown>;
      const accepted = Number(record.accepted_count ?? 0);
      const jobIds = Array.isArray(record.job_ids) ? (record.job_ids as unknown[]).length : 0;
      if (String(record.status ?? "") === "ACCEPTED" && jobIds) {
        setError(null);
        setFeedback(`已提交 ${accepted} 个对齐任务；旧的时码在对齐完成前仍然有效。`);
      } else {
        const blockers = (record.blockers as Array<Record<string, unknown>> | undefined) ?? [];
        setFeedback(null);
        setError(
          `重新对齐未提交：${
            blockers.map((item) => String(item.message ?? "")).join("；") ||
            String(record.status ?? "没有可领取的执行器")
          }`,
        );
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const adoptTake = useMutation({
    mutationFn: async ({ take }: { take: Record<string, unknown> }) =>
      adoptExplainerNarrationTake(activeEditionId ?? "", String(take.id), {
        actor: "local-user",
        reason: "第 3 步选择已生成版本",
      }),
    onSuccess: async (result, variables) => {
      setError(null);
      const record = result as Record<string, unknown>;
      const superseded = Array.isArray(record.superseded_take_ids) ? record.superseded_take_ids.length : 0;
      setFeedback(
        `已采用 take ${String(variables.take.take_no ?? "?")}（实测 ${formatMs(
          record.measured_duration_ms as number | null | undefined,
        )}）；人工采用已记录${
          superseded ? `，被替换的旧采用 ${superseded} 条保留在历史` : ""
        }。请重新对齐，使字幕时码与新的实测时长一致。`,
      );
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const savePronunciation = useMutation({
    mutationFn: async ({ segment, rows }: { segment: Record<string, unknown>; rows: PronunciationRow[] }) => {
      if (!frozenScriptRevisionId) throw new Error("该输出版本还没有冻结讲稿，不能保存发音修正。");
      const map = rows
        .map((row) => ({ display: row.display.trim(), spoken: row.spoken.trim() }))
        .filter((row) => row.display && row.spoken);
      return patchExplainerSegment(projectId, String(segment.id), {
        expected_revision: Number(segment.revision ?? 1),
        expected_script_revision_id: frozenScriptRevisionId,
        pronunciation_map: map,
        note: "第 3 步按段落修正发音",
      });
    },
    onSuccess: async (_result, variables) => {
      setError(null);
      setFeedback(
        `发音修正已保存（段落 ${String(variables.segment.canonical_segment_id)}）：只新建讲稿版本，受影响的该段配音/对齐/字幕标记过期，其它段落与素材保持有效。`,
      );
      setPronunciationDraft((current) => {
        const next = { ...current };
        delete next[String(variables.segment.canonical_segment_id)];
        return next;
      });
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const auditionVoice = useMutation({
    mutationFn: async () =>
      publishProjectLocalSapiVoiceProfile(projectId, {
        voice_ref: selectedVoiceRef,
        smoke_text: auditionSampleText(segments),
      }),
    onSuccess: async (result) => {
      setError(null);
      const profile = result.voice_profile as unknown as Record<string, unknown>;
      const evidence = (profile.evidence ?? {}) as Record<string, unknown>;
      const durationMs = Number((evidence.ffprobe as Record<string, unknown> | undefined)?.duration_ms ?? 0);
      setVoiceFeedback(
        `已用所选音色在本机真实合成一段试听 WAV（${durationMs > 0 ? `时长 ${formatMs(durationMs)}` : "时长以本机探测为准"}，` +
          `sha256 ${String(evidence.smoke_sha256 ?? "").slice(0, 12)}…）；该音频是本机验收证据，不作为成片旁白。` +
          "该音色已绑定到本项目的声音配置；系统安装音色只在本机试用，商业授权需另行确认。",
      );
      await queryClient.invalidateQueries({ queryKey: ["explainer-voice-profiles", projectId] });
    },
    onError: (mutationError) => {
      setVoiceFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  // "生成全部配音（补齐剩余 N 段）" submits the same production run the rest of
  // the workspace uses: real preflight, then a real job.  Nothing is reported as
  // queued before the server answers with a run.
  const startProduction = useExplainerPreviewStart({ projectId, overview: overview.data });

  /* -------------------------------------------------------------- derivations */
  const totals = useMemo(() => {
    const total = segmentStates.length > 0 ? segmentStates.length : segments.length;
    const aligned = segmentStates.filter((state) => state.state === "ALIGNED").length;
    const withAudio = segmentStates.filter((state) => state.media_version_id).length;
    const missing = segmentStates.filter((state) => !state.selected_take_id).length;
    const aligning = segmentStates.filter((state) => state.state === "ALIGNING").length;
    return {
      total,
      aligned,
      withAudio,
      missing: segmentStates.length === 0 ? segments.length : missing,
      aligning,
      measuredTotalMs: (narration.data?.measured_total_ms as number | null | undefined) ?? null,
    };
  }, [narration.data?.measured_total_ms, segmentStates, segments.length]);

  const runWording = narrationRunWording(
    asRecords(overview.data?.latest_run?.steps),
    totals.withAudio,
    totals.total,
  );

  const selectedSegment = selectedSegmentLocator ?? searchParams.get("segment");

  useEffect(() => {
    if (!selectedSegment) return;
    const element =
      document.getElementById(`segment-${selectedSegment}`) ??
      document.querySelector(`[data-canonical-segment="${selectedSegment}"]`);
    element?.scrollIntoView?.({ block: "center" });
  }, [selectedSegment]);

  const state = useMemo<PageState | null>(() => {
    // Parent query first: `narration` is disabled while there is no edition, and
    // a disabled query also reports `isPending`.
    if (editions.isPending) return { kind: "loading", message: "正在载入输出版本…" };
    if (editions.isError && !editions.data) {
      return {
        kind: "failed",
        title: "无法载入输出版本",
        body: editions.error instanceof Error ? editions.error.message : "未知错误",
        action: <button type="button" onClick={() => { void editions.refetch(); }}>重新读取</button>,
      };
    }
    if (editionList.length === 0) {
      return {
        kind: "empty",
        title: "还没有输出版本",
        body: "在总览提交预检后会按所选语言与画幅创建输出版本，之后才能生成配音。",
      };
    }
    if (overview.data?.capability_snapshot && overview.data.capability_snapshot.probed === false && takes.length === 0) {
      return {
        kind: "no_capability",
        title: "尚未接入能力探查",
        body: "无法确认本机 TTS 能力，因此不会排队配音任务，也不会给出乐观的预计时长；已有音频仍可试听。",
        action: <Link to={routes.systemCapabilities(projectId)}>前往能力与模型</Link>,
      };
    }
    if (narration.isPending) return { kind: "loading", message: "正在载入分段旁白…" };
    if (narration.isError && !narration.data) {
      return {
        kind: "failed",
        title: "无法载入旁白",
        body: narration.error instanceof Error ? narration.error.message : "未知错误",
        action: <button type="button" onClick={() => { void narration.refetch(); }}>重新读取</button>,
      };
    }
    if (segments.length === 0) {
      return {
        kind: "partial",
        title: "该版本还没有绑定讲稿",
        body: "先冻结一个讲稿版本，再进行分段合成。",
      };
    }
    if (takes.length === 0) {
      return {
        kind: "no_capability",
        title: "尚未生成配音",
        body: "旁白由真实本地 TTS 产生；没有音频时所有时长字段保持为空，不会补零并标记成功。",
      };
    }
    if (totals.missing > 0) {
      return {
        kind: "partial",
        title: `${totals.missing} 段还没有选定配音`,
        body: "失败段只重读该段，并重新检查邻段拼接；已完成段不会重做。",
      };
    }
    if (totals.aligned < totals.total) {
      return {
        kind: "running",
        title: `${totals.total - totals.aligned} 段正在匹配字幕时间`,
        body: "对齐来自真实对齐 revision；有音频不等于时间轴就绪，完成前不能进入第 4 步。",
        progress: null,
      };
    }
    return null;
  }, [
    editionList.length,
    editions.data,
    editions.error,
    editions.isError,
    editions.isPending,
    narration.data,
    narration.error,
    narration.isError,
    narration.isPending,
    overview.data?.capability_snapshot,
    projectId,
    segments.length,
    takes.length,
    totals.aligned,
    totals.missing,
    totals.total,
  ]);

  /* --------------------------------------------------------------- bar + text */
  /** The TTS capability is real server data: never assume it is ready. */
  const ttsBlockedReason = ttsOptions.isSuccess && !ttsOptions.data?.selection.ready
    ? "本机还没有可执行的 TTS 配置；先去能力与模型选择并发布一个 TTS Profile。"
    : null;
  const canStartProduction = Boolean(activeEditionId && frozenScriptRevisionId && segments.length > 0 && !ttsBlockedReason);
  const startDisabledReason = !activeEditionId
    ? "先选择一个输出版本。"
    : !frozenScriptRevisionId
      ? "该输出版本还没有冻结讲稿；先在第 1 步确认并冻结讲稿。"
      : segments.length === 0
        ? "该版本还没有分段旁白，先在讲稿步骤完成分段。"
        : ttsBlockedReason;
  const nextDisabledReason = totals.total === 0
    ? "该版本还没有分段旁白。"
    : totals.aligned < totals.total
      ? `对齐仍在处理中：还有 ${totals.total - totals.aligned} 段没有完成对齐；音频文件存在不等于时间轴就绪。`
      : null;

  useExplainerActionBar({
    primary: totals.missing > 0 || totals.aligned === 0
      ? {
        label: totals.missing > 0 ? `生成全部配音（补齐剩余 ${totals.missing} 段）` : "生成全部配音",
        onClick: () => {
          setFeedback(null);
          setError(null);
          startProduction.start();
        },
        disabled: !canStartProduction || startProduction.pending,
        disabledReason: startDisabledReason,
        busy: startProduction.pending,
      }
      : {
        label: "下一步：分镜与画面",
        onClick: () => navigate(routes.explainerPage(projectId, "storyboard")),
        disabled: Boolean(nextDisabledReason),
        disabledReason: nextDisabledReason,
      },
    defer: {
      label: "稍后处理",
      onClick: () => navigate(routes.explainerPage(projectId, "review")),
    },
    summary: totals.total > 0
      ? `${totals.aligned} / ${totals.total} 段已对齐 · 实测总时长 ${formatMs(totals.measuredTotalMs)}`
      : "还没有可配音的段落",
  });

  /* ------------------------------------------------------------------ render */
  const setSelectedLocale = (value: string) => {
    setSearchParams((params) => {
      const next = new URLSearchParams(params);
      next.set("locale", value);
      return next;
    });
  };

  const cueTimelineTotalMs = cues.reduce((max, cue) => Math.max(max, Number(cue.end_ms ?? 0)), 0);

  return <div className="explainer-page" ref={pageRef}>
    <Panel
      title="配音版本"
      subtitle="每种语言独立 TTS、独立对齐、独立重排；本步骤只做配音与字幕文本。"
      actions={
        <>
          {editionList.length > 1 ? (
            <label className="explainer-field">
              当前输出版本
              <select
                value={activeEditionId ?? ""}
                onChange={(event) => setSearchParams((params) => {
                  const next = new URLSearchParams(params);
                  next.set("edition", event.target.value);
                  return next;
                })}
              >
                {editionList.map((edition) => (
                  <option key={String(edition.id)} value={String(edition.id)}>
                    {localeLabel(String(edition.voice_locale))} · {String(edition.aspect_ratio)}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {subtitleLocales.length > 1 ? (
            <div className="explainer-actions" role="tablist" aria-label="字幕预览语言">
              {subtitleLocales.map((value) => (
                <button
                  key={value}
                  type="button"
                  role="tab"
                  aria-selected={subtitleLocale === value}
                  className={`explainer-tab-button${subtitleLocale === value ? " is-selected" : ""}`}
                  onClick={() => setSelectedLocale(value)}
                >
                  {localeLabel(value)}
                </button>
              ))}
            </div>
          ) : null}
        </>
      }
    >
      <div className="explainer-summary-strip">
        <div><small>配音语言</small><strong>{localeLabel(voiceLocale)}</strong></div>
        <div><small>段落数</small><strong>{totals.total} 段</strong></div>
        <div>
          <small>实测总时长</small>
          <strong>{formatMs(totals.measuredTotalMs)}</strong>
          <small>{totals.measuredTotalMs === null ? "只统计当前冻结讲稿采用的 take" : "来自真实音频实测"}</small>
        </div>
        <div>
          <small>对齐就绪</small>
          <strong>{totals.aligned} / {totals.total}</strong>
          <small>{totals.aligning > 0 ? `${totals.aligning} 段正在匹配字幕时间` : "有音频不等于已对齐"}</small>
        </div>
      </div>
      {runWording ? <p className="explainer-note" role="status">当前任务：{runWording}</p> : null}
      <StateNotice state={state} />
      {/* A failed refetch keeps the last known data and says so (§B12.4). */}
      {narration.isError && narration.data ? (
        <InlineError message={`旁白更新失败：${narration.error instanceof Error ? narration.error.message : "未知错误"}。已保留上一次读取到的分段与时长。`} />
      ) : null}
      <p className="explainer-note">
        英文版使用独立英文 TTS 时钟、独立剪辑点和字幕，不套用中文绝对时码；额外语言版本在第 6 步显式添加后才可切换，
        本步骤不会默认创建英语版。字幕样式与背景音乐属于第 6 步。
      </p>
      <InlineOk message={feedback} />
      <InlineError message={error} />
      <InlineOk message={startProduction.feedback} />
      <InlineError message={startProduction.error} />
    </Panel>

    <div className="explainer-audio-layout">
      <div className="explainer-stack">
        <Panel
          title="分段旁白"
          subtitle="按段落连续阅读与试听；每段独立 take、独立对齐。"
          actions={<span className="badge">{totals.withAudio} / {totals.total} 段有音频</span>}
        >
          {segments.length === 0 ? (
            <p className="muted">没有可显示的段落；该输出版本还没有冻结讲稿。</p>
          ) : segments.map((segment, index) => {
            const canonical = String(segment.canonical_segment_id);
            const segmentState = segmentStates.find((item) => String(item.canonical_segment_id) === canonical);
            const selectedTake = takes.find(
              (item) => String(item.canonical_segment_id) === canonical && Boolean(item.selected),
            );
            const history = [...takes, ...historicalTakes].filter(
              (item) => String(item.canonical_segment_id) === canonical,
            );
            const stateValue = String(segmentState?.state ?? "NOT_GENERATED");
            const badge = alignmentLabel(stateValue);
            const measuredMs = (segmentState?.measured_duration_ms ?? selectedTake?.measured_duration_ms ?? null) as number | null;
            const auditioned = auditionTake?.segmentKey === canonical
              ? history.find((item) => String(item.id) === auditionTake.takeId) ?? null
              : null;
            const rows = pronunciationRowsFor(segment, pronunciationDraft[canonical]);
            const isSelected = selectedSegment === String(segment.id) || selectedSegment === canonical;
            return (
              <article
                className={`explainer-segment${isSelected ? " selected" : ""}`}
                key={String(segment.id ?? canonical)}
                id={`segment-${String(segment.id ?? canonical)}`}
                data-canonical-segment={canonical}
              >
                <div className="explainer-segment-top">
                  <span className="badge">段落 {String(index + 1).padStart(3, "0")}</span>
                  <small>{measuredMs !== null && measuredMs !== undefined ? `实测 ${formatMs(measuredMs)}` : "时长待实测"}</small>
                  <span className={badge.tone ? `badge ${badge.tone}` : "badge"}>{badge.text}</span>
                  {segmentState?.take_no ? <small>当前采用 take {String(segmentState.take_no)}</small> : null}
                </div>
                <p>{String(segment.display_text ?? "")}</p>
                {segment.spoken_text && String(segment.spoken_text) !== String(segment.display_text) ? (
                  <p className="spoken">朗读：{String(segment.spoken_text)}</p>
                ) : null}
                {stateValue === "ALIGNING" || segmentState?.alignment_error ? (
                  <p className="muted">
                    {stateValue === "ALIGNING"
                      ? "正在匹配字幕时间（状态来自对齐 revision，不是猜测）。"
                      : `对齐失败：${alignmentFailureText(segmentState?.alignment_error)}`}
                  </p>
                ) : null}
                {auditioned ? (
                  <p className="explainer-note">
                    正在试听历史 take {String(auditioned.take_no ?? "?")}（未采用）；试听不改变成片时钟。
                  </p>
                ) : null}
                {/* A real <audio> element: a take existing is not the same as being
                    able to hear it, and there is no fake waveform without audio. */}
                <NarrationPlayer
                  src={auditioned ? takeAudioUrl(auditioned) : segmentState?.audio_url ?? null}
                  label={`第 ${String(index + 1).padStart(3, "0")} 段${auditioned ? "（历史 take 试听）" : "试听"}`}
                  durationMs={
                    auditioned
                      ? (auditioned.measured_duration_ms as number | null | undefined)
                      : measuredMs
                  }
                />
                <div className="explainer-actions" style={{ marginTop: 8 }}>
                  <button
                    type="button"
                    onClick={() => playSegment(canonical, setError)}
                  >
                    播放
                  </button>
                  <button
                    type="button"
                    disabled={!activeEditionId || reRead.isPending}
                    onClick={() => {
                      setSelectedSegmentLocator(canonical);
                      reRead.mutate(segment);
                    }}
                  >
                    重读这一段
                  </button>
                  <Link className="explainer-source-link" to={`${routes.explainerPage(projectId, "script")}?segment=${encodeURIComponent(String(segment.id))}`}>
                    编辑本段正文 →
                  </Link>
                </div>
                <details className="explainer-folded">
                  <summary>发音修正（显示名 → 读法）</summary>
                  <p className="muted">只影响该段朗读，不改展示正文；中文输入法可直接输入。保存会新建讲稿版本。</p>
                  {rows.map((row, rowIndex) => (
                    <div className="explainer-pronunciation-row" key={`${canonical}-row-${rowIndex}`}>
                      <label className="explainer-field">
                        显示名
                        <input
                          type="text"
                          value={row.display}
                          onChange={(event) => updatePronunciationRow(canonical, rows, rowIndex, { display: event.target.value }, setPronunciationDraft)}
                        />
                      </label>
                      <label className="explainer-field">
                        读法
                        <input
                          type="text"
                          value={row.spoken}
                          onChange={(event) => updatePronunciationRow(canonical, rows, rowIndex, { spoken: event.target.value }, setPronunciationDraft)}
                        />
                      </label>
                      <button
                        type="button"
                        onClick={() => setPronunciationDraft((current) => ({
                          ...current,
                          [canonical]: rows.filter((_item, index2) => index2 !== rowIndex),
                        }))}
                      >
                        删除
                      </button>
                    </div>
                  ))}
                  <div className="explainer-actions">
                    <button
                      type="button"
                      onClick={() => setPronunciationDraft((current) => ({
                        ...current,
                        [canonical]: [...rows, { display: "", spoken: "" }],
                      }))}
                    >
                      新增一条读法
                    </button>
                    <button
                      type="button"
                      disabled={!frozenScriptRevisionId || savePronunciation.isPending}
                      title={frozenScriptRevisionId ? undefined : "该输出版本还没有冻结讲稿，不能保存发音修正"}
                      onClick={() => savePronunciation.mutate({ segment, rows })}
                    >
                      保存发音修正
                    </button>
                    {!frozenScriptRevisionId ? <span className="muted">该版本还没有冻结讲稿，保存不可用。</span> : null}
                  </div>
                </details>
                <details className="explainer-folded">
                  <summary>更多：已生成版本（{history.length} 条 take）</summary>
                  {history.length === 0 ? (
                    <p className="muted">这一段还没有历史 take；重读会追加新 take，旧 take 保留。</p>
                  ) : history.map((take) => {
                    const takeId = String(take.id);
                    const isAdopted = Boolean(take.selected) || String(segmentState?.selected_take_id ?? "") === takeId;
                    return (
                      <div className="explainer-take-row" key={takeId}>
                        <span className="badge">{isAdopted ? "当前采用" : "历史"}</span>
                        <span className="muted">
                          take {String(take.take_no ?? "?")} · 实测 {formatMs(take.measured_duration_ms as number | null | undefined)}
                          {take.reason ? ` · ${String(take.reason)}` : ""}
                        </span>
                        <button
                          type="button"
                          onClick={() => setAuditionTake({ segmentKey: canonical, takeId })}
                          disabled={!take.media_version_id}
                          title={take.media_version_id ? undefined : "该 take 没有可播放的媒体版本"}
                        >
                          试听
                        </button>
                        {/* §B4: clicking a historical take only auditions it; 采用此配音
                            is a separate, explicit action that records a HUMAN adoption
                            and switches the segment's clock to this take's measured
                            duration. */}
                        <button
                          type="button"
                          disabled={Boolean(isAdopted) || adoptTake.isPending || !take.measured_duration_ms}
                          title={
                            isAdopted
                              ? "这条已经是当前采用版本"
                              : take.measured_duration_ms
                                ? "把该段配音切换为这条已生成版本（人工采用，可回退）"
                                : "该 take 没有实测时长，不能作为时钟依据"
                          }
                          onClick={() => adoptTake.mutate({ take })}
                        >
                          {adoptTake.isPending ? "正在采用" : "采用此配音"}
                        </button>
                      </div>
                    );
                  })}
                  <p className="explainer-note">
                    历史 take 只试听；采用此配音会记录一次人工采用并切换该段的实测时长，旧 take 与旧成片都保留。
                  </p>
                </details>
              </article>
            );
          })}
        </Panel>

        <Panel
          title="字幕文本与时间"
          subtitle={`当前预览语言：${subtitleLocale ? localeLabel(subtitleLocale) : "无"}；样式调整在第 6 步。`}
        >
          {subtitles.isError && !subtitles.data ? (
            <InlineError message={`字幕读取失败：${subtitles.error instanceof Error ? subtitles.error.message : "未知错误"}。这不表示该版本没有字幕。`} />
          ) : null}
          {subtitles.isError && subtitles.data ? (
            <InlineError message={`字幕更新失败：${subtitles.error instanceof Error ? subtitles.error.message : "未知错误"}。已保留上一次读取到的字幕条目。`} />
          ) : null}
          {cues.length === 0 ? (
            <p className="muted">
              {subtitles.data?.empty_state === "NO_SUBTITLE_REVISION"
                ? "尚无字幕 revision；字幕随配音对齐后由字幕阶段生成。"
                : "没有可显示的字幕条目。"}
            </p>
          ) : (
            <>
              <div
                className="explainer-cue-timeline"
                role="img"
                aria-label={`字幕时间线：共 ${cues.length} 条，总长 ${formatMs(cueTimelineTotalMs)}`}
              >
                {cues.slice(0, 200).map((cue, index) => (
                  <span
                    key={String(cue.id ?? `${index}`)}
                    style={{ flexGrow: Math.max(1, Number(cue.end_ms ?? 0) - Number(cue.start_ms ?? 0)) }}
                    title={`${formatMs(Number(cue.start_ms ?? 0))}–${formatMs(Number(cue.end_ms ?? 0))} ${String(cue.text ?? "")}`}
                  >
                    {index + 1}
                  </span>
                ))}
              </div>
              <ol className="explainer-cue-list">
                {cues.map((cue, index) => (
                  <li key={String(cue.id ?? `${index}`)}>
                    <span className="badge">{index + 1}</span>
                    <span className="muted">
                      {formatMs(Number(cue.start_ms ?? 0))}–{formatMs(Number(cue.end_ms ?? 0))}
                      {cue.font_size_px !== undefined ? ` · ${String(cue.font_size_px)}px` : ""}
                    </span>
                    <p>{String(cue.text ?? "（无字幕文本）")}</p>
                    {cue.paired_text ? <p className="muted">{String(cue.paired_text)}</p> : null}
                  </li>
                ))}
              </ol>
              {cues.length > 200 ? <p className="muted">时间线只绘制前 200 条；下方列表显示全部 {cues.length} 条。</p> : null}
            </>
          )}
          <p className="explainer-note">
            这里只显示真实字幕条目与时间；字号/位置/安全区等视觉样式在第 6 步调整，改样式不会重跑 TTS 或图像。
          </p>
        </Panel>
      </div>

      <div className="explainer-stack">
        <Panel title="配音设置" subtitle="参数会随本次提交冻结；这里不创建额外的语言版本。">
          <label className="explainer-field">
            配音语言
            <select
              value={voiceLocale}
              onChange={(event) => {
                const target = editionList.find((edition) => String(edition.voice_locale) === event.target.value);
                if (!target) return;
                setSearchParams((params) => {
                  const next = new URLSearchParams(params);
                  next.set("edition", String(target.id));
                  return next;
                });
              }}
            >
              {(languages.length > 0 ? languages : [voiceLocale]).map((language) => {
                const edition = editionList.find((item) => String(item.voice_locale) === language);
                return (
                  <option key={language} value={language} disabled={!edition}>
                    {localeLabel(language)}{edition ? "" : "（需在第 6 步添加版本）"}
                  </option>
                );
              })}
            </select>
          </label>
          <p className="muted">
            只列出本机实际可用的 TTS 语言；额外英语版本在第 6 步显式添加后才出现，本步骤不会默认创建。
          </p>

          <SettingRow label="当前输出版本" value={`${localeLabel(voiceLocale)} · ${String(activeEdition?.aspect_ratio ?? "—")}`} />
          <SettingRow label="段落数" value={`${totals.total} 段`} />
          <SettingRow label="实测总时长" value={formatMs(totals.measuredTotalMs)} />

          <div className="explainer-field" style={{ marginTop: 10 }}>
            <CapabilityPicker
              capability="TTS"
              value={ttsProfileId}
              onChange={setTtsProfileId}
              query={ttsOptions}
              label="配音模型"
              description="默认使用项目配置的 TTS Profile；本次选择用于核对可用性。"
              showDetails={false}
            />
          </div>

          <label className="explainer-field" style={{ marginTop: 10 }}>
            声音
            <select
              value={selectedVoiceRef}
              onChange={(event) => setSelectedVoiceRef(event.target.value)}
              disabled={voices.length === 0}
            >
              {voices.length === 0 ? (
                <option value="">{fixedVoiceLabel(projectVoiceProfiles)}</option>
              ) : voices.map((voice) => (
                <option key={voice.voice_ref} value={voice.voice_ref}>{voice.name}（{voice.culture}）</option>
              ))}
            </select>
          </label>
          <p className="muted">
            {voicesQuery.isPending
              ? "正在读取本机声音列表…"
              : voicesQuery.isError
                ? "无法读取本机声音列表；这里不会伪造男/女选项，只显示当前配置的固定声音。"
                : voices.length === 0
                  ? "本机没有报告可选声音，当前使用项目配置的固定声音。"
                  : "声音名称来自本机实时扫描，可逐项试听；试听不改变成片旁白。"}
          </p>
          <div className="explainer-actions">
            <button
              type="button"
              disabled={!selectedVoiceRef || auditionVoice.isPending}
              title={selectedVoiceRef ? undefined : "先选择一个本机扫描到的声音"}
              onClick={() => auditionVoice.mutate()}
            >
              试听声音
            </button>
            <button
              type="button"
              disabled={!activeEditionId || realign.isPending || totals.withAudio === 0}
              title={
                totals.withAudio === 0
                  ? "还没有已采用的旁白 take，没有可对齐的音频"
                  : "对本版本已采用的每个 take 重新做强制对齐（追加新修订，旧时码保留）"
              }
              onClick={() => {
                setFeedback(null);
                setError(null);
                realign.mutate();
              }}
            >
              {realign.isPending ? "正在提交对齐…" : "重新对齐旁白"}
            </button>
            <Link className="explainer-source-link" to={routes.systemCapabilities(projectId)}>配置 TTS 能力 →</Link>
          </div>
          <InlineOk message={voiceFeedback} />

          {/* §B4: 语速 is only offered with profile-declared values. */}
          {declaredRates.length > 0 ? (
            <label className="explainer-field" style={{ marginTop: 10 }}>
              语速
              <select value={String(declaredRates[1] ?? declaredRates[0])} disabled>
                {declaredRates.map((rate) => <option key={rate} value={String(rate)}>{rate.toFixed(1)}</option>)}
              </select>
            </label>
          ) : (
            <SettingRow label="语速" value={SPEECH_RATE_CONFIGURED_BY_VOICE} />
          )}

          <p className="explainer-note">
            单段重读不会改动邻段正文；对齐修复只重做受影响的邻接范围。全片播放时开始单段试听会暂停前一个播放器，避免声音叠加。
          </p>
        </Panel>
      </div>
    </div>

    {/* The whole narration is the paragraph takes played in order — there is no
        separate full-length file, and this page refuses to pretend otherwise. */}
    <Panel title="整段口播播放" subtitle="按段落顺序连读已采用的 take；不会自动播放。">
      {totals.withAudio === 0 ? (
        <p className="muted">还没有可播放的口播音频；生成配音后这里才能连读真实 take。</p>
      ) : (
        <div className="explainer-actions">
          <button type="button" onClick={() => playWholeNarration(pageRef.current, setError)}>
            播放整段口播
          </button>
          <button
            type="button"
            onClick={() => {
              pageRef.current?.querySelectorAll<HTMLMediaElement>("audio, video").forEach((element) => element.pause());
            }}
          >
            全部暂停
          </button>
          <span className="muted">
            连读的是当前版本各段已采用的 take（{totals.withAudio} / {totals.total} 段有音频）；没有音频的段会跳过而不是静音占位。
          </span>
        </div>
      )}
    </Panel>
  </div>;
}

/* -------------------------------------------------------------------------- */
/* small local helpers                                                        */
/* -------------------------------------------------------------------------- */

function auditionSampleText(segments: Array<Record<string, unknown>>): string {
  const first = String(segments[0]?.display_text ?? "").trim();
  const sample = first || "本机语音合成验收通过";
  return sample.slice(0, 40);
}

function fixedVoiceLabel(profiles: VoiceProfileVersion[]): string {
  if (profiles.length > 0) return `${profiles[0].title}（固定声音）`;
  return "本机未报告声音列表（使用当前配置的固定声音）";
}

function pronunciationRowsFor(
  segment: Record<string, unknown>,
  draft: PronunciationRow[] | undefined,
): PronunciationRow[] {
  if (draft) return draft;
  const stored = Array.isArray(segment.pronunciation_map_json)
    ? (segment.pronunciation_map_json as Array<Record<string, unknown>>)
    : [];
  return stored.map((row) => ({ display: String(row.display ?? ""), spoken: String(row.spoken ?? "") }));
}

function updatePronunciationRow(
  canonical: string,
  rows: PronunciationRow[],
  index: number,
  patch: Partial<PronunciationRow>,
  setDraft: React.Dispatch<React.SetStateAction<Record<string, PronunciationRow[]>>>,
) {
  setDraft((current) => ({
    ...current,
    [canonical]: rows.map((row, rowIndex) => (rowIndex === index ? { ...row, ...patch } : row)),
  }));
}

/** Explicit user action only: nothing in this module plays media on mount. */
export function startPlayback(element: HTMLMediaElement, reportError: (message: string) => void) {
  try {
    const result = element.play() as Promise<void> | undefined;
    if (result && typeof result.catch === "function") {
      result.catch(() => reportError("音频播放失败：请检查该 take 的媒体版本是否仍然可用。"));
    }
  } catch {
    reportError("音频播放失败：请检查该 take 的媒体版本是否仍然可用。");
  }
}

/** Selector for one paragraph's own player element. */
function segmentAudio(canonical: string): HTMLMediaElement | null {
  return document.querySelector<HTMLMediaElement>(`[data-canonical-segment="${canonical}"] audio`);
}

function playSegment(canonical: string, reportError: (message: string) => void) {
  const target = segmentAudio(canonical);
  if (!target) {
    reportError("这一段还没有可播放的音频；没有音频时不会伪造播放。");
    return;
  }
  startPlayback(target, reportError);
}

/**
 * Play the taken paragraphs in order.  The chain only follows real `<audio>`
 * elements: a segment without audio is skipped (never replaced by silence) and
 * reaching the end simply stops.
 */
function playWholeNarration(root: HTMLElement | null, reportError: (message: string) => void) {
  const elements = Array.from(root?.querySelectorAll<HTMLMediaElement>("audio") ?? [])
    .filter((element) => Boolean(element.getAttribute("src")));
  if (elements.length === 0) {
    reportError("还没有可播放的口播音频。");
    return;
  }
  elements.forEach((element) => {
    element.onended = null;
  });
  elements.forEach((element, index) => {
    element.onended = () => {
      const next = elements[index + 1];
      if (next) startPlayback(next, reportError);
    };
  });
  startPlayback(elements[0], reportError);
}
