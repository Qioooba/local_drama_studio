/**
 * 第 4 步：分镜与画面 (storyboard) — 图片候选工作区 (spec §B5, §B9, §B10, §B11).
 *
 * This module also owns the *shared* shot-list + candidate workspace that 第 5 步
 * reuses (spec §B6: "页面结构沿用第 4 步的镜头列表与候选工作区；共享外壳，不复制
 * 另一套图片/视频卡实现").  Only files owned by this step may hold that shell, so
 * the shared pieces are exported from here and imported by `ClipsPage`.
 *
 * The three states the design keeps apart (§B12.3) are three separate values here:
 *
 *  * `adopted`   — the ACTIVE selection the server holds (per purpose + edition);
 *  * `previewed` — the candidate the operator is only looking at;
 *  * `draft`     — the editable description/settings of the current working object.
 *
 * Nothing on this page claims more than the server really said: a plan is a
 * read-only projection, an ACK is 已排队, and 可用 only appears once a candidate
 * record is READY.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  adoptExplainerGeneratedBeats,
  archiveExplainerCandidate,
  getExplainerBeatImpact,
  getExplainerOverview,
  getExplainerWorkspaceReadiness,
  listExplainerOwnerCandidates,
  patchExplainerBeat,
  planExplainerBeatGeneration,
  preflightExplainerPlan,
  registerExplainerCandidateFromMedia,
  selectExplainerBeatCandidate,
  startExplainerRun,
  submitExplainerBeatGeneration,
  unlockExplainerSelection,
  retryJob,
  type ExplainerBeat,
  type ExplainerCandidatePage,
  type ExplainerCandidatePurpose,
  type ExplainerGenerationMode,
  type ExplainerGenerationPlan,
  type ExplainerGenerationReceipt,
  type ExplainerGenerationRequest,
  type ExplainerMediaCandidate,
} from "../../generated/api";
import { newCommandId, stableIdempotencyKey } from "../../services/commandId";
import { queryKeys } from "../../query/queryKeys";
import { routes } from "../../app/routeRegistry";
import { Drawer, MediaThumb } from "../../components/ui/primitives";
import { draftRegistry, type DraftHandle } from "../drafts/draftRegistry";
import { CapabilityPicker, effectiveCapabilityProfile, useCapabilityOptions } from "../model-config/CapabilityPicker";
import { MediaPicker } from "../media-picker/MediaPicker";
import { mediaContentUrl, mediaProxyUrl } from "../shared/mediaPlaybackPolicy";
import { useExplainerActionBar } from "./ExplainerStepActionBar";
import { GenerationControls, MediaCandidateCompare, MediaCandidateGrid, candidateShortLabel } from "./candidates";
import { InlineError, InlineOk, Panel, SettingRow, StateNotice, type PageState } from "./components";
import { useExplainerAssets, useExplainerBeats, useExplainerEditions } from "./useExplainerQueries";
import { formatMs, isRetiredRenderType, plannedVsActual, renderTypeLabel, RETIRED_RENDER_TYPE_NOTE } from "./viewModels";
import "./explainers.css";
import "./explainers.steps45.css";

/* -------------------------------------------------------------------------- */
/* §B5.2 fixed business enums (models/durations are read from real capability) */
/* -------------------------------------------------------------------------- */

/** 画面来源 — suggestion enums that map onto the render types the backend stores. */
export type ShotSourceKey = "AI_IMAGE" | "UPLOADED_MEDIA" | "INFOGRAPHIC";
export const SHOT_SOURCE_OPTIONS: Array<{ value: ShotSourceKey; label: string }> = [
  { value: "AI_IMAGE", label: "AI 配图" },
  { value: "UPLOADED_MEDIA", label: "上传或媒体库" },
  { value: "INFOGRAPHIC", label: "图形卡片（仅已有受支持模板）" },
];

/**
 * 最终呈现方式 — the real `render_type` the beat will be composed as.
 *
 * 静图推拉 (deterministic local composition) was removed from the product: an
 * explainer picture is always produced by real AI 图生视频, so `I2V` is the only
 * picture route left.  图形动画 and 已有视频/授权素材 stay as their own, separate
 * non-AI-picture sources — they are never merged into AI 动态.
 */
export type PresentationKey = "AI_VIDEO" | "GRAPHIC_ANIMATION" | "SOURCE_VIDEO";
export const PRESENTATION_OPTIONS: Array<{ value: PresentationKey; label: string; renderType: string }> = [
  { value: "AI_VIDEO", label: "AI 动态（图生视频）", renderType: "I2V" },
  { value: "GRAPHIC_ANIMATION", label: "图形动画 / 信息图", renderType: "INFOGRAPHIC" },
  { value: "SOURCE_VIDEO", label: "已有视频 / 授权素材", renderType: "LICENSED_MEDIA" },
];

/**
 * The presentation a stored render type maps to.  A retired/unknown value is not a
 * legal target any more, so the only remaining picture route (`AI_VIDEO`) is
 * selected and the page says out loud that the stored plan is outdated instead of
 * showing a still-image motion label.
 */
export function presentationOf(renderType: string | null | undefined): PresentationKey {
  switch (String(renderType ?? "").toUpperCase()) {
    case "INFOGRAPHIC":
      return "GRAPHIC_ANIMATION";
    case "LICENSED_MEDIA":
      return "SOURCE_VIDEO";
    default:
      return "AI_VIDEO";
  }
}

export function renderTypeForPresentation(key: PresentationKey): string {
  return PRESENTATION_OPTIONS.find((item) => item.value === key)?.renderType ?? "I2V";
}

/** 图形动画 and 已有视频 are real choices but neither is an AI 图生视频 draw. */
export function presentationIsAiVideo(key: PresentationKey): boolean {
  return key === "AI_VIDEO";
}

/** §B6 short type label: AI 动态 / 图形动画 / 已有视频. */
export function shotTypeShortLabel(renderType: string | null | undefined): string {
  switch (String(renderType ?? "").toUpperCase()) {
    case "I2V":
      return "AI 动态（图生视频）";
    case "INFOGRAPHIC":
      return "图形动画";
    case "LICENSED_MEDIA":
      return "已有视频";
    default:
      // A retired value (a legacy STILL_MOTION/PARALLAX row) reads as an outdated
      // plan, never as a still-image motion label and never as AI 动态.
      return renderTypeLabel(renderType);
  }
}

/** §B5.2 构图 — semantic hint only, never presented as a precise camera parameter. */
export const COMPOSITION_OPTIONS = [
  { value: "AUTO", label: "自动" },
  { value: "LONG_SHOT", label: "远景" },
  { value: "FULL_SHOT", label: "全景" },
  { value: "MEDIUM_SHOT", label: "中景" },
  { value: "CLOSE_SHOT", label: "近景" },
  { value: "EXTREME_CLOSE_UP", label: "特写" },
] as const;

/** §B6.1 镜头运动 — semantic hint unless the selected Profile really accepts it. */
export const CAMERA_MOVEMENT_OPTIONS = [
  { value: "AUTO", label: "自动" },
  { value: "FIXED", label: "固定" },
  { value: "PUSH_IN", label: "缓慢推进" },
  { value: "PULL_OUT", label: "缓慢拉远" },
  { value: "PAN", label: "横移" },
] as const;

/* -------------------------------------------------------------------------- */
/* media urls                                                                 */
/* -------------------------------------------------------------------------- */

/** Real immutable thumbnail endpoint (the same one the review/audio steps use). */
export function mediaVersionThumbUrl(mediaVersionId: string, size: "small" | "medium" = "small"): string {
  return `/api/v1/media-versions/${encodeURIComponent(mediaVersionId)}/thumbnail?size=${size}&frame=poster`;
}

/**
 * A candidate card needs a *thumbnail* endpoint.  The candidate DTO carries the
 * media content URL in `thumbnail_url`/`preview_url`; the shared `MediaThumb`
 * deliberately refuses to treat a `/content` URL as a poster (it would download
 * the full media for a card), so the thumbnail endpoint is used instead when the
 * candidate only exposes its content URL.  Nothing is invented: the media version
 * is the candidate's own.
 */
export function candidateThumbUrl(candidate: ExplainerMediaCandidate): string | null {
  const declared = candidate.thumbnail_url ?? null;
  if (declared && !/\/content(?:[/?#]|$)/i.test(declared)) return declared;
  if (candidate.media_version_id) return mediaVersionThumbUrl(candidate.media_version_id);
  return declared;
}

/** The playable URL of a candidate, honouring the shared proxy-first policy. */
export function candidateVideoSrc(candidate: ExplainerMediaCandidate): string | null {
  if (candidate.media_version_id) return mediaProxyUrl(candidate.media_version_id);
  return candidate.playback_url ?? candidate.preview_url ?? null;
}

export function candidateVideoOriginalSrc(candidate: ExplainerMediaCandidate): string | null {
  if (candidate.media_version_id) return mediaContentUrl(candidate.media_version_id);
  return candidate.playback_url ?? candidate.preview_url ?? null;
}

/* -------------------------------------------------------------------------- */
/* candidate page adapter                                                     */
/* -------------------------------------------------------------------------- */

function readString(value: unknown): string | null {
  return value === null || value === undefined || value === "" ? null : String(value);
}

function readNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Normalise one candidate row into the shared neutral DTO (§B10).
 *
 * The list endpoint is the authority for status/selection/media; anything the row
 * does not carry stays `null` instead of being filled with a plausible-looking
 * default.  `media_kind` is only inferred from data the server did send: an I2V
 * render type is a video, and a VISUAL-purpose candidate is a composable clip.
 */
export function normalizeCandidate(
  raw: Record<string, unknown>,
  context: { purpose: ExplainerCandidatePurpose },
): ExplainerMediaCandidate {
  const mediaVersionId = readString(raw.media_version_id);
  const renderTypeActual = readString(raw.render_type_actual);
  const renderTypePlanned = readString(raw.render_type_planned);
  const declaredKind = readString(raw.media_kind);
  const mediaKind: ExplainerMediaCandidate["media_kind"] =
    declaredKind === "VIDEO" || declaredKind === "IMAGE"
      ? declaredKind
      : renderTypeActual === "I2V" || renderTypePlanned === "I2V"
        ? "VIDEO"
        : mediaVersionId
          ? context.purpose === "VISUAL"
            ? "VIDEO"
            : "IMAGE"
          : null;
  const declaredThumb = readString(raw.thumbnail_url);
  const declaredPreview = readString(raw.preview_url);
  // A card needs a *thumbnail* endpoint: the shared `MediaThumb` refuses to use a
  // media `/content` URL as a poster (it would download the whole medium for a
  // card), so the candidate's own media version supplies the thumbnail endpoint.
  const thumbnail =
    declaredThumb && !/\/content(?:[/?#]|$)/i.test(declaredThumb)
      ? declaredThumb
      : mediaVersionId
        ? mediaVersionThumbUrl(mediaVersionId)
        : declaredThumb;
  const status = String(raw.status ?? "PENDING") as ExplainerMediaCandidate["status"];
  const candidate: ExplainerMediaCandidate = {
    id: String(raw.id ?? ""),
    candidate_kind: String(raw.candidate_kind ?? "CREATIVE"),
    purpose: (readString(raw.purpose) as ExplainerCandidatePurpose | null) ?? context.purpose,
    owner_kind: raw.entity_id ? "ENTITY" : "BEAT",
    owner_id: String(raw.entity_id ?? raw.beat_id ?? ""),
    beat_id: readString(raw.beat_id),
    entity_id: readString(raw.entity_id),
    edition_id: readString(raw.edition_id),
    variant_no: readNumber(raw.variant_no) ?? 0,
    media_kind: mediaKind,
    media_version_id: mediaVersionId,
    media_sha256: readString(raw.media_sha256),
    thumbnail_url: thumbnail,
    preview_url: declaredPreview ?? declaredThumb,
    playback_url: readString(raw.playback_url),
    duration_ms: readNumber(raw.duration_ms),
    width: readNumber(raw.width),
    height: readNumber(raw.height),
    status,
    render_type_planned: renderTypePlanned,
    render_type_actual: renderTypeActual,
    fallback_reason: readString(raw.fallback_reason),
    selected: Boolean(raw.selected ?? raw.adopted),
    locked: Boolean(raw.locked),
    stale: Boolean(raw.stale),
    adopted: Boolean(raw.adopted ?? raw.selected),
    job_id: readString(raw.job_id),
    job_state: readString(raw.job_state),
    seed: readNumber(raw.seed),
    parent_candidate_id: readString(raw.parent_candidate_id),
    prompt: readString(raw.prompt),
    negative_prompt: readString(raw.negative_prompt),
    reference_media_version_ids: Array.isArray(raw.reference_media_version_ids)
      ? (raw.reference_media_version_ids as unknown[]).map(String)
      : [],
    short_label: readString(raw.short_label),
    error_code: readString(raw.error_code) ?? readString(raw.fallback_reason),
    error_message: readString(raw.error_message) ?? readString(raw.fallback_reason),
    retryable: Boolean(raw.retryable ?? status === "FAILED"),
    created_at: readString(raw.created_at),
  };
  return candidate;
}

/**
 * Build the shared page DTO from whatever the list endpoint returned.
 *
 * Both shapes are accepted because the neutral owner route and the older beat
 * route answer the same path: the owner route already returns this DTO, the older
 * one returns raw rows.  Either way the media facts come from the server.
 */
export function normalizeCandidatePage(
  raw: unknown,
  context: { projectId: string; ownerId: string; purpose: ExplainerCandidatePurpose; editionId: string | null },
): ExplainerCandidatePage {
  const record = (raw ?? {}) as Record<string, unknown>;
  const rows = Array.isArray(record.candidates) ? (record.candidates as unknown[]) : [];
  const candidates = rows.map((row) =>
    normalizeCandidate((row ?? {}) as Record<string, unknown>, { purpose: context.purpose }),
  );
  const counts: Record<string, number> = { REFERENCE: 0, KEYFRAME: 0, VISUAL: 0 };
  for (const candidate of candidates) {
    const key = String(candidate.purpose ?? context.purpose);
    counts[key] = (counts[key] ?? 0) + 1;
  }
  const selection = (record.active_selection ?? null) as Record<string, unknown> | null;
  const selectionPurpose = readString(selection?.purpose)?.toUpperCase() ?? null;
  // A selection of another purpose is not this purpose's adopted media; showing it
  // would silently lend an image to a clip (or the other way round).
  const activeSelection = selection && (!selectionPurpose || selectionPurpose === context.purpose) ? selection : null;
  return {
    project_id: context.projectId,
    owner_kind: "BEAT",
    owner_id: context.ownerId,
    purpose: context.purpose,
    edition_id: context.editionId,
    candidates,
    counts: (record.counts as Record<string, number> | undefined) ?? counts,
    active_selection: activeSelection,
    empty_state: readString(record.empty_state) ?? (candidates.length ? null : "NO_CANDIDATES_YET"),
    candidates_newest_first: true,
    read_error_keeps_known_selection: true,
  };
}

/** Poll while a batch is still running; a terminal candidate stops the poll (§B12.4). */
export const CANDIDATE_POLL_MS = 4_000;

export function candidateIsRunning(candidate: ExplainerMediaCandidate): boolean {
  return candidate.status === "PENDING" || candidate.status === "GENERATING";
}

export function useExplainerShotCandidates(options: {
  projectId: string;
  beatId: string | null;
  purpose: ExplainerCandidatePurpose;
  editionId: string | null;
}) {
  const { projectId, beatId, purpose, editionId } = options;
  const query = useQuery({
    queryKey: queryKeys.explainers.beatCandidates(projectId, beatId ?? "", purpose, editionId),
    queryFn: () =>
      listExplainerOwnerCandidates(projectId, "BEAT", beatId as string, {
        purpose,
        edition_id: editionId ?? undefined,
      }),
    enabled: Boolean(projectId && beatId),
    refetchInterval: (current) => {
      const rows = current.state.data?.candidates ?? [];
      return rows.some(candidateIsRunning) ? CANDIDATE_POLL_MS : false;
    },
  });
  const page = useMemo(
    () =>
      query.data
        ? normalizeCandidatePage(query.data, { projectId, ownerId: beatId ?? "", purpose, editionId })
        : null,
    [query.data, projectId, beatId, purpose, editionId],
  );
  return { query, page };
}

/* -------------------------------------------------------------------------- */
/* beat projections                                                           */
/* -------------------------------------------------------------------------- */

function readList(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? (value as Array<Record<string, unknown>>) : [];
}

/**
 * Candidates that can act as this beat's picture (§B5.1, §D2.2).
 *
 * The candidate table is purpose-scoped, so a beat's keyframe candidates are shown for
 * `KEYFRAME`.  A film produced before the layers were split has only `VISUAL` candidates
 * — its stills were stored as the final clip — and hiding them told the user "还没有画面"
 * while the picture existed and step 5 already listed it.  When a beat has no candidate of
 * the requested purpose but does have `VISUAL` ones, those are shown, and the caller keeps
 * the candidate's *own* purpose when adopting so the server still receives a consistent
 * request.
 */
export function beatCandidatesForPurpose(
  beat: ExplainerBeat,
  purpose: ExplainerCandidatePurpose,
): Array<Record<string, unknown>> {
  const all = readList((beat as unknown as Record<string, unknown>).candidates);
  const exact = all.filter((item) => String(item.purpose ?? "VISUAL").toUpperCase() === purpose);
  if (exact.length || purpose !== "KEYFRAME") return exact;
  return all.filter((item) => String(item.purpose ?? "VISUAL").toUpperCase() === "VISUAL");
}

/** The purpose a candidate must be adopted under: its own, never the viewer's step. */
export function candidateAdoptionPurpose(
  candidate: { purpose?: string | null } | null | undefined,
  fallback: ExplainerCandidatePurpose,
): ExplainerCandidatePurpose {
  const declared = String(candidate?.purpose ?? "").toUpperCase();
  if (declared === "KEYFRAME" || declared === "VISUAL" || declared === "REFERENCE") {
    return declared as ExplainerCandidatePurpose;
  }
  return fallback;
}

/**
 * The adopted candidate of one purpose, from the beats read model.
 *
 * `active_selection` on the beats payload is the VISUAL selection, so it is only
 * used when its own `purpose` matches what the caller asked for.
 */
export function beatAdoptedCandidate(
  beat: ExplainerBeat,
  purpose: ExplainerCandidatePurpose,
): Record<string, unknown> | null {
  const adopted = beatCandidatesForPurpose(beat, purpose).find((item) => Boolean(item.adopted));
  if (adopted) return adopted;
  const selection = (beat as unknown as Record<string, unknown>).active_selection as
    | Record<string, unknown>
    | null
    | undefined;
  if (!selection) return null;
  const selectionPurpose = String(selection.purpose ?? "").toUpperCase();
  if (selectionPurpose && selectionPurpose !== purpose) return null;
  if (!selectionPurpose) return null;
  return selection;
}

export function beatIsReady(beat: ExplainerBeat, purpose: ExplainerCandidatePurpose): boolean {
  return Boolean(beatAdoptedCandidate(beat, purpose));
}

export type ShotState = "READY" | "RUNNING" | "FAILED" | "NEEDS_SELECTION" | "NO_CANDIDATE";

export function beatShotState(beat: ExplainerBeat, purpose: ExplainerCandidatePurpose): ShotState {
  if (beatIsReady(beat, purpose)) return "READY";
  const rows = beatCandidatesForPurpose(beat, purpose);
  if (rows.some((row) => ["PENDING", "GENERATING"].includes(String(row.status ?? "").toUpperCase()))) return "RUNNING";
  if (rows.some((row) => String(row.status ?? "").toUpperCase() === "FAILED")) return "FAILED";
  return rows.length > 0 ? "NEEDS_SELECTION" : "NO_CANDIDATE";
}

export const SHOT_STATE_LABELS: Record<ShotState, string> = {
  READY: "已就绪",
  RUNNING: "生成中",
  FAILED: "生成失败",
  NEEDS_SELECTION: "待选择",
  NO_CANDIDATE: "缺少画面",
};

export function narrationSummaryOf(beat: ExplainerBeat): string {
  const texts = readList((beat as unknown as Record<string, unknown>).narration_links)
    .map((link) => String(link.display_text ?? "").trim())
    .filter(Boolean);
  if (!texts.length) return "尚未关联旁白段落";
  return texts.join(" ");
}

export function beatThumbnailUrl(beat: ExplainerBeat, purpose: ExplainerCandidatePurpose): string | null {
  const adopted = beatAdoptedCandidate(beat, purpose);
  const mediaVersionId = readString(adopted?.media_version_id);
  if (mediaVersionId) return mediaVersionThumbUrl(mediaVersionId);
  const ready = beatCandidatesForPurpose(beat, purpose).find(
    (row) => String(row.status ?? "").toUpperCase() === "READY" && row.media_version_id,
  );
  return ready?.media_version_id ? mediaVersionThumbUrl(String(ready.media_version_id)) : null;
}

/* -------------------------------------------------------------------------- */
/* shared left column                                                         */
/* -------------------------------------------------------------------------- */

export function ShotListPane({
  beats,
  selectedBeatId,
  onSelect,
  purpose,
  typeLabel,
  showState = true,
  title = "画面段",
  subtitle = "按旁白顺序排列，与讲稿段落是多对多关系。",
}: {
  beats: ExplainerBeat[];
  selectedBeatId: string | null;
  onSelect: (beatId: string) => void;
  purpose: ExplainerCandidatePurpose;
  typeLabel?: (beat: ExplainerBeat) => string;
  showState?: boolean;
  title?: string;
  subtitle?: string;
}) {
  return (
    <Panel title={title} subtitle={subtitle}>
      {beats.length === 0 ? (
        <p className="muted">还没有画面段。</p>
      ) : (
        <ul className="explainer-shot-list">
          {beats.map((beat) => {
            const state = beatShotState(beat, purpose);
            const isCurrent = beat.id === selectedBeatId;
            const thumbnail = beatThumbnailUrl(beat, purpose);
            return (
              <li key={beat.id}>
                <button
                  type="button"
                  className={`explainer-shot-item${isCurrent ? " selected" : ""}`}
                  aria-current={isCurrent ? "true" : undefined}
                  aria-label={`${title} ${beat.code}`}
                  onClick={() => onSelect(beat.id)}
                >
                  <span className="explainer-shot-item__thumb">
                    <MediaThumb
                      src={thumbnail}
                      alt={`${title} ${beat.code} 缩略图`}
                      emptyLabel="尚未生成"
                      aspectRatio="16 / 9"
                      objectFit="cover"
                    />
                  </span>
                  <span className="explainer-shot-item__body">
                    <span className="explainer-shot-item__title">
                      <span className="explainer-shot-item__no">{String(beat.ordinal + 1).padStart(2, "0")}</span>
                      <strong>{String(beat.code)}</strong>
                    </span>
                    {/* The list clamps the narration to two lines so 52 shots stay
                        scannable; the full text stays reachable through the title and
                        is shown in full in the middle pane. */}
                    <span className="explainer-shot-item__line" title={narrationSummaryOf(beat)}>
                      {narrationSummaryOf(beat)}
                    </span>
                    <span className="explainer-shot-item__meta">
                      <span>{formatMs(beat.preferred_duration_ms)}</span>
                      <span>·</span>
                      <span>{typeLabel ? typeLabel(beat) : shotTypeShortLabel(beat.render_type)}</span>
                      {showState ? <span>· {SHOT_STATE_LABELS[state]}</span> : null}
                      {beat.must_be_motion ? <span>· 必须运动</span> : null}
                      {beat.locked_by_human ? <span>· 人工锁定</span> : null}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

/* -------------------------------------------------------------------------- */
/* shared drawers                                                             */
/* -------------------------------------------------------------------------- */

/** 调整覆盖区间 (§B5.2): read the real coverage, validate it, and offer 重新规划. */
export function CoverageDrawer({
  open,
  onClose,
  beat,
  onReplan,
}: {
  open: boolean;
  onClose: () => void;
  beat: ExplainerBeat | null;
  onReplan: () => void;
}) {
  const links = beat ? readList((beat as unknown as Record<string, unknown>).narration_links) : [];
  const durationMs = beat?.preferred_duration_ms ?? null;
  return (
    <Drawer open={open} title="调整覆盖区间" onClose={onClose}>
      {beat ? (
        <>
          <SettingRow label="画面段" value={`${beat.code} · 第 ${beat.ordinal + 1} 段`} />
          <SettingRow label="覆盖时长" value={formatMs(durationMs)} />
          <SettingRow label="关联旁白段落" value={String(links.length)} />
          {links.length > 0 ? (
            /* The shot list clamps each narration line to two lines so a 52-shot film
               stays scannable; this is where the full text stays readable. */
            <ul className="explainer-coverage-lines">
              {links.map((link, index) => (
                <li key={String(link.canonical_segment_id ?? index)}>
                  <strong>{String(link.canonical_segment_id ?? `#${index + 1}`)}</strong>
                  <span>{String(link.display_text ?? "").trim() || "（该段落没有旁白文本）"}</span>
                </li>
              ))}
            </ul>
          ) : null}
          <p className="explainer-setting-note">
            覆盖区间来自旁白时间轴：{links.length > 0
              ? links.map((link) => String(link.canonical_segment_id ?? "")).join("、")
              : "尚未关联旁白段落"}
            。区间不允许出现缺口或越界；本页不提供自由拖拽剪辑。
          </p>
          <p className="explainer-setting-note">
            需要改变覆盖范围时提交重新规划：新草案会先给出影响清单，已锁镜头不会被清空。
          </p>
          <div className="explainer-draw-actions">
            <button type="button" className="explainer-btn" onClick={onReplan}>提交重新规划</button>
            <button type="button" className="explainer-btn explainer-btn--quiet" onClick={onClose}>关闭</button>
          </div>
        </>
      ) : (
        <p className="muted">先选择一个画面段。</p>
      )}
    </Drawer>
  );
}

/**
 * 上传 / 从媒体库选择 (§B5.3, §B6.2): the shared picker in a drawer.  `onChange`
 * only produces a candidate-shaped selection; the page never adopts from it.
 */
export function MediaPickDrawer({
  open,
  onClose,
  projectId,
  mediaKind,
  label,
  value,
  onPick,
  note,
}: {
  open: boolean;
  onClose: () => void;
  projectId: string;
  mediaKind: "IMAGE" | "VIDEO";
  label: string;
  value: string;
  onPick: (mediaVersionId: string) => void;
  note?: string;
}) {
  return (
    <Drawer open={open} title={label} onClose={onClose} width={460}>
      <p className="explainer-setting-note">{note ?? "选择或上传只会登记为候选，不会直接成为采用的画面。"}</p>
      <MediaPicker
        projectId={projectId}
        value={value}
        onChange={(mediaVersionId) => onPick(mediaVersionId)}
        mediaKind={mediaKind}
        label={label}
      />
      <div className="explainer-draw-actions">
        <button type="button" className="explainer-btn explainer-btn--quiet" onClick={onClose}>关闭</button>
      </div>
    </Drawer>
  );
}

/* -------------------------------------------------------------------------- */
/* shared generation draw: plan → submit → real receipt → poll                */
/* -------------------------------------------------------------------------- */

export type ExplainerDrawCommand = Omit<
  ExplainerGenerationRequest,
  "operation_id" | "expected_plan_hash" | "candidate_seeds" | "execution_profile_version_id" | "expected_resolution_hash"
> & {
  /** Accepted by the contract as `negative_override`; kept optional here. */
  negative_override?: string | null;
};

export function frozenSubmitBody(
  request: ExplainerGenerationRequest,
  plan: ExplainerGenerationPlan,
): ExplainerGenerationRequest {
  // §D7.1: the submit body is the plan request merged with the frozen additions.
  //
  // The frozen execution profile comes from the plan the server just returned; the
  // previous read used `plan.requested_execution_profile_version_id`, which no
  // generated type (nor the API schema) declares, so it was always undefined and the
  // frozen id fell through to whatever the request happened to carry.
  return {
    ...request,
    expected_plan_hash: plan.plan_hash,
    candidate_seeds: plan.candidate_seeds,
    execution_profile_version_id: plan.execution_profile_version_id ?? request.execution_profile_version_id ?? null,
    expected_resolution_hash: plan.expected_resolution_hash,
  };
}

export function receiptPendingCount(receipt: ExplainerGenerationReceipt | null): number {
  if (!receipt) return 0;
  return receipt.items.filter((item) => item.submission_status !== "NOT_SUBMITTED").length;
}

/**
 * One draw operation.
 *
 * A *new creative draw* mints a new `operation_id` (and therefore new seeds and a
 * new Idempotency-Key); replaying the same operation reuses both.  `plan()` is the
 * read-only preflight, `submit()` sends the plan request merged with the frozen
 * plan fields.  Nothing here reports 生成完成 — an ACK only ever means 已排队.
 */
export function useExplainerGenerationDraw(options: {
  projectId: string;
  beatId: string | null;
  purpose: ExplainerCandidatePurpose;
  command: ExplainerDrawCommand;
}) {
  const { projectId, beatId, purpose, command } = options;
  const [nonce, setNonce] = useState<string | null>(null);
  const [plan, setPlan] = useState<ExplainerGenerationPlan | null>(null);
  const [planSignature, setPlanSignature] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<ExplainerGenerationReceipt | null>(null);
  const [error, setError] = useState<string | null>(null);

  const commandSignature = useMemo(
    () => stableIdempotencyKey("explainer-beat-draw-command", command as unknown as Record<string, unknown>),
    [command],
  );
  const request = useMemo<ExplainerGenerationRequest | null>(() => {
    if (!nonce || !beatId) return null;
    return {
      ...command,
      operation_id: stableIdempotencyKey("explainer-beat-draw", {
        projectId,
        beatId,
        purpose,
        nonce,
        command: command as unknown as Record<string, unknown>,
      }),
    } as ExplainerGenerationRequest;
  }, [beatId, command, nonce, projectId, purpose]);

  const signature = useMemo(
    () => stableIdempotencyKey("explainer-beat-draw-signature", { purpose, commandSignature }),
    [commandSignature, purpose],
  );
  const planIsStale = Boolean(plan) && planSignature !== null && planSignature !== signature;

  const planMutation = useMutation({
    mutationFn: (body: ExplainerGenerationRequest) => planExplainerBeatGeneration(projectId, beatId as string, body),
    onSuccess: (result) => {
      setError(null);
      setPlan(result);
      setPlanSignature(signature);
    },
    onError: (mutationError) => {
      setPlan(null);
      setPlanSignature(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const submitMutation = useMutation({
    mutationFn: () => {
      if (!request || !plan) throw new Error("提交前必须先取得可执行的生成计划。");
      const key = stableIdempotencyKey("explainer-beat-draw-submit", { operation_id: request.operation_id });
      return submitExplainerBeatGeneration(projectId, beatId as string, frozenSubmitBody(request, plan), key);
    },
    onSuccess: (result) => {
      setError(null);
      setReceipt(result);
    },
    onError: (mutationError) => {
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  /** Start a brand-new draw: new operation id, new seeds, previous results kept. */
  const start = () => {
    if (!beatId) return;
    const freshNonce = newCommandId();
    const body = {
      ...command,
      operation_id: stableIdempotencyKey("explainer-beat-draw", {
        projectId,
        beatId,
        purpose,
        nonce: freshNonce,
        command: command as unknown as Record<string, unknown>,
      }),
    } as ExplainerGenerationRequest;
    setNonce(freshNonce);
    setPlan(null);
    setPlanSignature(null);
    setReceipt(null);
    setError(null);
    planMutation.mutate(body);
  };

  const reset = () => {
    setNonce(null);
    setPlan(null);
    setPlanSignature(null);
    setReceipt(null);
    setError(null);
  };

  return {
    request,
    plan: planIsStale ? null : plan,
    /** True when the frozen plan no longer matches the current settings. */
    planIsStale,
    receipt,
    error,
    planPending: planMutation.isPending,
    submitPending: submitMutation.isPending,
    canSubmit: Boolean(plan) && !planIsStale && plan?.status === "EXECUTABLE" && !submitMutation.isPending,
    start,
    submit: () => submitMutation.mutate(),
    reset,
  };
}

/** Reusable real receipt rendering (§D7.1): accepted items are 已排队, not done. */
export function GenerationReceiptView({
  receipt,
  unit = "张",
  candidates,
}: {
  receipt: ExplainerGenerationReceipt;
  unit?: string;
  candidates?: ExplainerMediaCandidate[];
}) {
  const accepted = receipt.items.filter((item) => item.submission_status !== "NOT_SUBMITTED");
  const rejected = receipt.items.filter((item) => item.submission_status === "NOT_SUBMITTED");
  const ready = (candidates ?? []).filter((candidate) => candidate.status === "READY").length;
  return (
    <div className="explainer-receipt" role="status">
      <h4>
        服务端回执：{receipt.status === "ACCEPTED" ? "全部已入队" : receipt.status === "PARTIALLY_ACCEPTED" ? "部分已入队" : "未入队"}
        （已受理 {receipt.accepted_count} / {receipt.requested_count}）
      </h4>
      {accepted.length ? (
        <ul>
          {accepted.map((item) => (
            <li key={`accepted-${item.ordinal}`}>
              第 {item.ordinal + 1} {unit}已排队：候选 {String(item.candidate_id ?? "").slice(0, 8)} · 任务{" "}
              {String(item.job_id ?? "").slice(0, 8)} · 状态 {item.job_state ?? "QUEUED"} · 种子 {item.seed ?? "—"}
            </li>
          ))}
        </ul>
      ) : null}
      {rejected.length ? (
        <ul>
          {rejected.map((item) => (
            <li key={`rejected-${item.ordinal}`}>
              第 {item.ordinal + 1} {unit}未入队：{item.error?.message ?? item.error?.code ?? "服务端未说明原因"}
              {item.error?.code ? `（${item.error.code}）` : ""}
              {item.error?.retryable ? " · 可重试" : ""}
            </li>
          ))}
        </ul>
      ) : null}
      <p className="explainer-setting-note">
        回执不等于生成完成：候选只有在服务端登记为「可用」后才会显示媒体。
        {candidates
          ? ` 当前该画面段已有 ${ready} 个可用候选${accepted.length ? "；其余仍在排队或生成中" : ""}。`
          : ""}
      </p>
      {receipt.idempotent_replay ? <p className="explainer-setting-note">这是同一操作的重放回执，未创建新批次。</p> : null}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* §B6 video model + motion controls (shared with step 5)                     */
/* -------------------------------------------------------------------------- */

/** Capability codes the backend binds for the two image/video modes (§D4.1). */
export const IMAGE_CAPABILITY = "IMAGE_CONCEPT";
export const IMAGE_EDIT_CAPABILITY = "IMAGE_EDIT";
export const VIDEO_CAPABILITY = "VIDEO_I2V";

export function profileSupportsInputSlot(
  option: { input_slots?: string[] } | null | undefined,
  pattern: RegExp,
): boolean {
  const slots = option?.input_slots ?? [];
  return slots.some((slot) => pattern.test(String(slot)));
}

/* -------------------------------------------------------------------------- */
/* §B5 第 4 步 页面                                                            */
/* -------------------------------------------------------------------------- */

type ShotDraft = {
  description: string;
  prompt: string;
  negative: string;
  composition: string;
  source: ShotSourceKey;
  presentation: PresentationKey;
  mode: ExplainerGenerationMode | "";
  stepCount: string;
};

/**
 * Units per executable generation mode.
 *
 * The map is deliberately not exhaustive: the generated client still declares the
 * removed `STILL_MOTION` literal (it is pending regeneration by its owner), and a
 * stale mode must fall back to 张 rather than be named here as if it still existed.
 */
const GENERATION_MODE_UNITS: Partial<Record<ExplainerGenerationMode, string>> = {
  TEXT_TO_IMAGE: "张",
  IMAGE_EDIT: "张",
  IMAGE_TO_VIDEO: "段",
};

/** How a registered upload is described per layer (design §B5.3 首帧 vs 片段). */
export const REGISTRATION_UNITS: Record<ExplainerCandidatePurpose, string> = {
  KEYFRAME: "首帧",
  VISUAL: "片段",
  REFERENCE: "参考",
};

export function ExplainerStoryboardPage() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const editions = useExplainerEditions(projectId);
  const firstEditionId = editions.data?.editions?.[0]
    ? String((editions.data.editions[0] as Record<string, unknown>).id)
    : null;
  const editionId = searchParams.get("edition") ?? firstEditionId;
  const beatsQuery = useExplainerBeats(projectId, editionId);
  const beats = beatsQuery.data?.beats ?? [];
  const selectedBeatId = searchParams.get("beat") ?? beats[0]?.id ?? null;
  const selectedBeat = beats.find((beat) => beat.id === selectedBeatId) ?? null;
  const assets = useExplainerAssets(projectId);

  const purpose: ExplainerCandidatePurpose = "KEYFRAME";
  const { query: candidatesQuery, page } = useExplainerShotCandidates({
    projectId,
    beatId: selectedBeatId,
    purpose,
    editionId,
  });

  const [previewId, setPreviewId] = useState<string | null>(null);
  const [candidateCount, setCandidateCount] = useState(1);
  const [compareOpen, setCompareOpen] = useState(false);
  const [compareLimit, setCompareLimit] = useState(2);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [coverageOpen, setCoverageOpen] = useState(false);
  const [replanOpen, setReplanOpen] = useState(false);
  const [pendingMediaVersionId, setPendingMediaVersionId] = useState<string | null>(null);
  const [editorText, setEditorText] = useState<{ beatId: string; text: string } | null>(null);
  const [feedback, setFeedback] = useState<{ beatId: string | null; text: string } | null>(null);
  const [error, setError] = useState<{ beatId: string | null; text: string } | null>(null);
  const [conflict, setConflict] = useState<{ candidateId: string; message: string } | null>(null);
  const [impactReport, setImpactReport] = useState<Record<string, unknown> | null>(null);
  const [drafts, setDrafts] = useState<Record<string, ShotDraft>>({});
  const [batchMessage, setBatchMessage] = useState<string | null>(null);

  // §B5.1: a failed candidate read keeps the last real page so the adopted image
  // does not disappear and the page never claims "还没有候选".
  const lastGoodPage = useRef<ExplainerCandidatePage | null>(null);
  if (page) lastGoodPage.current = page;
  const effectivePage = page ?? lastGoodPage.current;
  const candidates = effectivePage?.candidates ?? [];
  const adoptedCandidate = candidates.find((candidate) => candidate.selected) ?? null;
  const previewCandidate = candidates.find((candidate) => candidate.id === previewId) ?? null;
  const displayCandidate = previewCandidate ?? adoptedCandidate;

  const imageOptions = useCapabilityOptions(IMAGE_CAPABILITY, { projectId });
  const videoOptions = useCapabilityOptions(VIDEO_CAPABILITY, { projectId });
  const imageProfile = effectiveCapabilityProfile(imageOptions, "");

  const draft: ShotDraft = useMemo(() => {
    const stored = selectedBeat ? drafts[selectedBeat.id] : undefined;
    if (stored) return stored;
    const record = (selectedBeat ?? {}) as unknown as Record<string, unknown>;
    return {
      description: String(record.visual_intent ?? ""),
      prompt: String(record.prompt_intent ?? ""),
      negative: "",
      composition: "AUTO",
      source: "AI_IMAGE",
      presentation: presentationOf(selectedBeat?.render_type),
      mode: "",
      stepCount: "",
    };
  }, [drafts, selectedBeat]);

  const patchDraft = useCallback(
    (patch: Partial<ShotDraft>) => {
      if (!selectedBeat) return;
      setDrafts((current) => ({
        ...current,
        [selectedBeat.id]: { ...(current[selectedBeat.id] ?? draft), ...patch },
      }));
    },
    [draft, selectedBeat],
  );

  /* ---------- 人物与场景 references (§B5.2: real shared assets, multi-select) ---------- */

  const entities = useMemo(() => {
    const rows = (assets.data?.entities ?? []) as unknown as Array<Record<string, unknown>>;
    return rows.map((row) => {
      const reference = (row.reference ?? null) as Record<string, unknown> | null;
      const bindings = readList(row.identity_bindings);
      const states = readList(row.states);
      const referenceId =
        readString(reference?.id) ??
        readString(reference?.reference_id) ??
        readString(bindings.find((binding) => readString(binding.reference_id))?.reference_id) ??
        readString(states.find((state) => readString(state.reference_id))?.reference_id);
      const stateRevisionId =
        readString(reference?.entity_state_revision_id) ??
        readString(states[0]?.id) ??
        null;
      const mediaVersionId = readString(reference?.media_version_id);
      return {
        id: String(row.entity_id ?? row.id ?? ""),
        code: String(row.code ?? ""),
        name: String(row.name ?? ""),
        entityType: String(row.entity_type ?? ""),
        referenceId,
        stateRevisionId,
        mediaVersionId,
        thumbnail: mediaVersionId ? mediaVersionThumbUrl(mediaVersionId) : readString(reference?.thumbnail_url),
      };
    });
  }, [assets.data]);

  const plannedEntityIds = useMemo(() => {
    if (!selectedBeat) return [] as string[];
    const value = (selectedBeat as unknown as Record<string, unknown>).entity_refs_json;
    return Array.isArray(value) ? (value as unknown[]).map(String) : [];
  }, [selectedBeat]);

  const [selectedEntityIds, setSelectedEntityIds] = useState<string[]>([]);
  useEffect(() => {
    setSelectedEntityIds(plannedEntityIds);
  }, [plannedEntityIds, selectedBeatId]);

  const referenceSelections = useMemo(
    () =>
      selectedEntityIds
        .map((entityId) => {
          const entity = entities.find((item) => item.id === entityId);
          if (!entity?.referenceId || !entity?.mediaVersionId) return null;
          return {
            entity_id: entityId,
            entity_state_revision_id: entity.stateRevisionId,
            reference_id: entity.referenceId,
          };
        })
        .filter((item): item is { entity_id: string; entity_state_revision_id: string | null; reference_id: string } => Boolean(item)),
    [entities, selectedEntityIds],
  );
  const entitiesMissingReference = selectedEntityIds.filter(
    (entityId) => !entities.find((item) => item.id === entityId)?.mediaVersionId,
  );

  /* ---------- the frozen command for this draw ---------- */

  const effectiveMode: ExplainerGenerationMode =
    draft.mode !== ""
      ? draft.mode
      : "TEXT_TO_IMAGE";

  const drawCommand: ExplainerDrawCommand = useMemo(
    () => ({
      edition_id: editionId ?? null,
      purpose,
      mode: effectiveMode,
      candidate_count: candidateCount,
      expected_beat_revision: selectedBeat ? Number((selectedBeat as unknown as Record<string, unknown>).revision ?? 1) : null,
      expected_selection_id: readString((effectivePage?.active_selection ?? null)?.id),
      reference_selections: effectiveMode === "IMAGE_EDIT" ? referenceSelections : [],
      prompt_override: draft.prompt.trim() ? draft.prompt : null,
      negative_override: draft.negative.trim() ? draft.negative : null,
      run_overrides: {},
    }),
    [candidateCount, draft.negative, draft.prompt, editionId, effectiveMode, effectivePage, purpose, referenceSelections, selectedBeat],
  );

  const draw = useExplainerGenerationDraw({
    projectId,
    beatId: selectedBeatId,
    purpose,
    command: drawCommand,
  });
  const unit = GENERATION_MODE_UNITS[effectiveMode] ?? "张";
  const hasCandidates = candidates.length > 0;
  const drawLabel = hasCandidates ? `再生成 ${candidateCount} ${unit}` : `生成 ${candidateCount} ${unit}`;
  const videoProfileReady = effectiveCapabilityProfile(videoOptions, "").ready;
  /**
   * A legacy row may still store a removed render type.  It is not a legal target
   * any more, so the page says the plan is outdated and asks for AI 动态 instead of
   * showing a still-image motion label.
   */
  const retiredPlanOfSelectedBeat = Boolean(
    selectedBeat && isRetiredRenderType((selectedBeat as unknown as Record<string, unknown>).render_type as string | null),
  );
  // §B5.2 画面来源 is a real choice: 上传/媒体库 and 图形卡片 are not model draws, so
  // the model draw stays unavailable instead of quietly generating an AI image.
  const sourceBlocksDraw = draft.source !== "AI_IMAGE";
  const sourceBlockReason =
    draft.source === "UPLOADED_MEDIA"
      ? "画面来源为「上传或媒体库」：素材通过「上传图片 / 选择素材」登记，不使用生图模型。"
      : draft.source === "INFOGRAPHIC"
        ? "画面来源为「图形卡片」：只有已有受支持模板，本仓库尚未提供图形卡片生成命令。"
        : null;

  /* ---------- the seven §B9 candidate actions ---------- */

  const rollbackRef = useRef<{ key: readonly unknown[]; page: ExplainerCandidatePage | null } | null>(null);

  const adopt = useMutation({
    mutationFn: async ({ candidateId, lock }: { candidateId: string; lock: boolean }) => {
      const selectionId = readString((effectivePage?.active_selection ?? null)?.id);
      // Adopt under the candidate's own purpose: a legacy VISUAL still shown in step 4 is
      // still a VISUAL candidate, and claiming otherwise would make the server reject a
      // purpose mismatch (or adopt it into the wrong layer).
      const candidate = candidates.find((item) => item.id === candidateId) ?? null;
      return selectExplainerBeatCandidate(projectId, selectedBeatId ?? "", {
        expected_revision: Number((selectedBeat as unknown as Record<string, unknown> | null)?.revision ?? 1),
        candidate_id: candidateId,
        edition_id: editionId,
        purpose: candidateAdoptionPurpose(candidate, purpose),
        expected_selection_id: selectionId,
        lock,
        actor: lock ? "local-user" : null,
      });
    },
    onMutate: async ({ candidateId, lock }) => {
      const key = queryKeys.explainers.beatCandidates(projectId, selectedBeatId ?? "", purpose, editionId);
      await queryClient.cancelQueries({ queryKey: key });
      const previous = (queryClient.getQueryData(key) as ExplainerCandidatePage | undefined) ?? null;
      rollbackRef.current = { key, page: previous ?? lastGoodPage.current };
      const base = previous ?? lastGoodPage.current;
      if (base) {
        queryClient.setQueryData(key, {
          ...base,
          candidates: base.candidates.map((candidate) => ({
            ...candidate,
            selected: candidate.id === candidateId,
            adopted: candidate.id === candidateId,
            locked: candidate.id === candidateId ? lock : candidate.locked,
          })),
          active_selection: {
            ...(base.active_selection ?? {}),
            id: base.active_selection?.id ?? null,
            purpose,
            candidate_id: candidateId,
          },
        });
      }
      return { previous };
    },
    onSuccess: async (result) => {
      const record = result as Record<string, unknown>;
      setConflict(null);
      setError(null);
      const impact = impactReport ?? null;
      const editionsAffected = Number(impact?.affected_edition_count ?? 0);
      setFeedback({
        beatId: selectedBeatId,
        text: record.degraded
          ? `已采用候选；实际类型与计划不同（回退原因：${String(record.fallback_reason ?? "服务端未提供")}）。将影响下游：${editionsAffected || "按服务端依赖范围"} 个输出版本的合成需更新。`
          : `已采用候选（${previewCandidate ? candidateShortLabel(previewCandidate) : "当前候选"}）；将更新该画面段的片段与 ${editionsAffected || "相关"} 个输出版本的合成，旧版本仍可回看。`,
      });
      setPreviewId(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError, variables) => {
      // §B12.7: a 409 keeps the local intent and the editing text; never silently
      // adopt another candidate.
      const rollback = rollbackRef.current;
      if (rollback) queryClient.setQueryData(rollback.key, rollback.page);
      const message = mutationError instanceof Error ? mutationError.message : String(mutationError);
      if (/409|CONFLICT|STALE_REVISION/i.test(message)) {
        // §B12.7: keep the local intent and the editing text; never silently adopt
        // another candidate.  The conflict panel is the single alert for this case.
        setConflict({ candidateId: variables.candidateId, message });
      } else {
        setError({ beatId: selectedBeatId, text: message });
      }
      void queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onSettled: () => {
      rollbackRef.current = null;
    },
  });

  const unlock = useMutation({
    mutationFn: (candidate: ExplainerMediaCandidate) =>
      unlockExplainerSelection(projectId, selectedBeatId ?? "", {
        selection_id: readString((effectivePage?.active_selection ?? null)?.id) ?? candidate.id,
        purpose,
        edition_id: editionId,
        expected_revision: Number((selectedBeat as unknown as Record<string, unknown> | null)?.revision ?? 1),
        actor: "local-user",
      }),
    onSuccess: async () => {
      setError(null);
      setFeedback({ beatId: selectedBeatId, text: "已解锁该画面段的人工锁定；当前采用画面保留，将来允许被替换。" });
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) =>
      setError({ beatId: selectedBeatId, text: mutationError instanceof Error ? mutationError.message : String(mutationError) }),
  });

  /** §B9 item 4: technical retry of the *same* task — same candidate, same seed. */
  const retryFailed = useMutation({
    mutationFn: (candidate: ExplainerMediaCandidate) => {
      if (!candidate.job_id) throw new Error("该失败候选没有可重试的真实任务记录。");
      return retryJob(candidate.job_id);
    },
    onSuccess: async (result) => {
      const job = (result as { job?: Record<string, unknown> }).job;
      setError(null);
      setFeedback({
        beatId: selectedBeatId,
        text: `已重试原任务（任务 ${String(job?.id ?? "").slice(0, 8)}，状态 ${String(job?.status ?? "QUEUED")}）；沿用原候选与种子，不占用新的创作候选额度。`,
      });
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) =>
      setError({ beatId: selectedBeatId, text: mutationError instanceof Error ? mutationError.message : String(mutationError) }),
  });

  const impact = useMutation({
    mutationFn: () => getExplainerBeatImpact(projectId, selectedBeatId ?? ""),
    onSuccess: (result) => {
      setError(null);
      setImpactReport(result as Record<string, unknown>);
    },
    onError: (mutationError) =>
      setError({ beatId: selectedBeatId, text: mutationError instanceof Error ? mutationError.message : String(mutationError) }),
  });

  /** §B5.3 不采用: archiving hides the candidate while keeping its media reversible. */
  const archiveCandidate = useMutation({
    mutationFn: (candidate: ExplainerMediaCandidate) =>
      archiveExplainerCandidate(projectId, selectedBeatId ?? "", candidate.id, {
        actor: "local-user",
        reason: "第 4 步不采用该候选",
      }),
    onSuccess: async (result, candidate) => {
      setError(null);
      const record = result as Record<string, unknown>;
      setFeedback({
        beatId: selectedBeatId,
        text: record.idempotent_replay
          ? "该候选此前已归档，本次没有重复写入。"
          : `已归档${candidateShortLabel(candidate)}；物理媒体与记录都保留，可恢复，当前采用版本未受影响。`,
      });
      if (previewId === candidate.id) setPreviewId(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) =>
      setError({ beatId: selectedBeatId, text: mutationError instanceof Error ? mutationError.message : String(mutationError) }),
  });

  /** §B5.3 上传/媒体库: the picked media becomes a candidate, never the adopted choice. */
  const registerMedia = useMutation({
    mutationFn: ({ mediaVersionId, mediaPurpose }: { mediaVersionId: string; mediaPurpose: "KEYFRAME" | "VISUAL" }) =>
      registerExplainerCandidateFromMedia(projectId, selectedBeatId ?? "", {
        media_version_id: mediaVersionId,
        purpose: mediaPurpose,
        note: "第 4 步上传/媒体库选择",
        actor: "local-user",
      }),
    onSuccess: async (result) => {
      setError(null);
      const record = result as Record<string, unknown>;
      setPendingMediaVersionId(null);
      setFeedback({
        beatId: selectedBeatId,
        text: `已登记为候选（${String(record.purpose ?? "KEYFRAME")}，状态 ${
          String(record.status ?? "READY")
        }）；它还不是当前采用版本，请在上方候选中比较后显式采用。内容与角色一致性检查没有运行，采用前会保持未检查。`,
      });
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) =>
      setError({ beatId: selectedBeatId, text: mutationError instanceof Error ? mutationError.message : String(mutationError) }),
  });

  /** 保存镜头描述: persist this beat's own description and presentation (§B5.2/§B6.1). */
  const saveDescription = useMutation({
    mutationFn: async () => {
      if (!selectedBeatId) throw new Error("请先选择一个画面段。");
      const beat = (selectedBeat as unknown as Record<string, unknown> | null) ?? {};
      const payload: Parameters<typeof patchExplainerBeat>[2] = {
        expected_revision: Number(beat.revision ?? 1),
        actor: "local-user",
      };
      // The editor starts empty and shows the current description as its placeholder,
      // so an untouched field must save the beat's existing value rather than being
      // dropped as "no change".
      const promptIntent = draft.prompt.trim() || String(beat.prompt_intent ?? "").trim();
      const visualIntent = String(beat.visual_intent ?? "").trim();
      if (visualIntent) payload.visual_intent = visualIntent;
      if (promptIntent) payload.prompt_intent = promptIntent;
      return patchExplainerBeat(projectId, selectedBeatId, payload);
    },
    onSuccess: async (result) => {
      setError(null);
      const record = result as Record<string, unknown>;
      const changed = Array.isArray(record.changed_fields) ? record.changed_fields.join("、") : "描述";
      setFeedback({
        beatId: selectedBeatId,
        text: `已保存镜头${
          String(record.edit_authority ?? "") === "HUMAN" ? "（人工编辑）" : ""
        }：${changed}；已排队任务仍使用各自的旧快照，下一次生成才会读取新描述。`,
      });
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) =>
      setError({ beatId: selectedBeatId, text: mutationError instanceof Error ? mutationError.message : String(mutationError) }),
  });

  /* ---------- 补齐缺失画面 (bottom-bar primary while images are missing) ---------- */

  const missingBeats = useMemo(() => beats.filter((beat) => !beatIsReady(beat, purpose)), [beats, purpose]);

  const backfill = useMutation({
    mutationFn: async () => {
      let submitted = 0;
      let accepted = 0;
      const failures: string[] = [];
      for (const beat of missingBeats) {
        const nonce = newCommandId();
        const body = {
          ...drawCommand,
          mode: "TEXT_TO_IMAGE",
          reference_selections: [],
          prompt_override: null,
          negative_override: null,
          expected_selection_id: null,
          expected_beat_revision: Number((beat as unknown as Record<string, unknown>).revision ?? 1),
          operation_id: stableIdempotencyKey("explainer-beat-draw", {
            projectId,
            beatId: beat.id,
            purpose,
            nonce,
          }),
        } as ExplainerGenerationRequest;
        const plan = await planExplainerBeatGeneration(projectId, beat.id, body);
        if (plan.status !== "EXECUTABLE") {
          failures.push(`${beat.code}：${plan.blockers.map((blocker) => blocker.message).join("；") || "计划被阻塞"}`);
          continue;
        }
        const receipt = await submitExplainerBeatGeneration(
          projectId,
          beat.id,
          frozenSubmitBody(body, plan),
          stableIdempotencyKey("explainer-backfill-submit", { operation_id: body.operation_id }),
        );
        submitted += 1;
        accepted += receipt.accepted_count;
        for (const item of receipt.items) {
          if (item.submission_status === "NOT_SUBMITTED") {
            failures.push(`${beat.code} 第 ${item.ordinal + 1} 张：${item.error?.message ?? item.error?.code ?? "未入队"}`);
          }
        }
      }
      return { requested: missingBeats.length, submitted, accepted, failures };
    },
    onSuccess: async (result) => {
      setError(result.failures.length && result.submitted === 0 ? { beatId: null, text: result.failures.join("；") } : null);
      setFeedback({
        beatId: null,
        text: `补齐缺失画面：已为 ${result.submitted} / ${result.requested} 个画面段提交 ${result.accepted} 个任务（已排队，等待生成）。${
          result.failures.length ? `未提交：${result.failures.join("；")}` : ""
        }已锁镜头与已采用结果未被改动。`,
      });
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) =>
      setError({ beatId: null, text: mutationError instanceof Error ? mutationError.message : String(mutationError) }),
  });

  /* ---------- 批量处理同一类问题: preview the scope, then confirm it ---------- */

  const batchAdopt = useMutation({
    // Two steps, one operation: the preview writes nothing and therefore carries no
    // key; the confirming call records one HUMAN adoption per beat.
    mutationFn: async ({ confirm }: { confirm: boolean }) => {
      const revision = Number((beats[0] as unknown as Record<string, unknown> | undefined)?.revision ?? 1);
      const request = {
        expected_revision: revision,
        actor: "local-user",
        edition_id: editionId ?? null,
        confirm,
      };
      const key = confirm ? stableIdempotencyKey("explainer-batch-adopt", { projectId, ...request }) : undefined;
      return (await adoptExplainerGeneratedBeats(projectId, request, key)) as Record<string, unknown>;
    },
    onSuccess: async (record) => {
      setError(null);
      if (record.requires_confirmation === true) {
        const planned = Number((record.plan as Record<string, unknown> | undefined)?.planned_count ?? 0);
        const review = ((record.needs_review as unknown[]) ?? []).length;
        setBatchMessage(
          `预览：将采用 ${planned} 个已生成画面；${review} 个画面段没有可采用的候选。确认后按人工权威采用并记录操作者。`,
        );
      } else {
        const adopted = Number(record.adopted_count ?? 0);
        const review = Number(record.needs_review_count ?? 0);
        setBatchMessage(
          adopted > 0
            ? `已按人工权威采用 ${adopted} 个画面；${review} 个画面段仍需处理（技术硬错误不可采用）。`
            : `未采用任何画面；${review} 个画面段缺少可采用的候选，请先重新生成。`,
        );
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      setBatchMessage(null);
      setError({ beatId: null, text: mutationError instanceof Error ? mutationError.message : String(mutationError) });
    },
  });

  /* ---------- 重新规划 / 生成分镜计划 (real preflight + real run) ---------- */

  const replan = useMutation({
    mutationFn: async () => {
      const preflight = await preflightExplainerPlan(projectId, { outputs: [] });
      if (preflight.blockers?.length) {
        return { submitted: false as const, blockers: preflight.blockers.map((blocker) => blocker.message) };
      }
      const key = stableIdempotencyKey("explainer-replan-run", { projectId, plan_hash: preflight.plan_hash });
      const run = await startExplainerRun(projectId, { plan_hash: preflight.plan_hash, outputs: [], start_workflow: true }, key);
      return { submitted: true as const, run };
    },
    onSuccess: async (result) => {
      if (!result.submitted) {
        setError({ beatId: null, text: `重新规划预检被阻塞：${result.blockers.join("；")}` });
        return;
      }
      setError(null);
      setFeedback({
        beatId: null,
        text: `已提交重新规划运行（运行 ${result.run.id.slice(0, 8)}，状态 ${result.run.projected_status ?? result.run.status}）。新草案生成前会先比较影响；已锁镜头与已采用结果不会被清空。`,
      });
      setReplanOpen(false);
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) =>
      setError({ beatId: null, text: mutationError instanceof Error ? mutationError.message : String(mutationError) }),
  });

  const readiness = useQuery({
    queryKey: queryKeys.explainers.readiness(projectId),
    queryFn: () => getExplainerWorkspaceReadiness(projectId),
    enabled: Boolean(projectId),
    staleTime: 10_000,
  });

  const overview = useQuery({
    queryKey: queryKeys.explainers.workspace(projectId),
    queryFn: () => getExplainerOverview(projectId),
    enabled: Boolean(projectId),
    retry: 1,
  });

  /* ---------- draft registry (§B12.1: project + step + objectId) ---------- */

  const draftOwnerId = `explainer:storyboard:${projectId}:${selectedBeatId ?? "none"}`;
  const draftHandle = useRef<DraftHandle | null>(null);
  const dirtyText =
    selectedBeat !== null &&
    (draft.description !== String((selectedBeat as unknown as Record<string, unknown>).visual_intent ?? "") ||
      draft.prompt !== String((selectedBeat as unknown as Record<string, unknown>).prompt_intent ?? "") ||
      draft.negative.trim() !== "");
  const saveDescriptionRef = useRef<() => void>(() => {});
  saveDescriptionRef.current = () => saveDescription.mutate();

  useEffect(() => {
    const handle = draftRegistry.register({
      ownerId: draftOwnerId,
      entityKey: selectedBeat ? `${selectedBeat.code}` : "画面段",
      version: dirtyText ? 1 : 0,
      dirty: dirtyText,
      save: async () => saveDescriptionRef.current(),
    });
    draftHandle.current = handle;
    return () => {
      draftHandle.current = null;
      draftRegistry.unregister(handle);
    };
  }, [draftOwnerId, dirtyText, selectedBeat]);

  useEffect(() => {
    const handle = draftHandle.current;
    if (handle) draftRegistry.update(handle, { dirty: dirtyText, version: dirtyText ? 1 : 0 });
  }, [dirtyText]);

  /* ---------- derived page state ---------- */

  const readyCount = beats.filter((beat) => beatIsReady(beat, purpose)).length;
  const state = useMemo<PageState | null>(() => {
    if (editions.isPending || beatsQuery.isPending) return { kind: "loading", message: "正在载入分镜计划…" };
    if (overview.data && overview.data.capability_snapshot?.probed === false) {
      return {
        kind: "no_capability",
        title: "尚未接入能力探查",
        body: "无法确认本机图像/视频模型与工作流版本，因此不会排队 GPU；仍然可以上传或选用已有素材。",
        action: <Link to={routes.systemCapabilities(projectId)}>前往能力与模型</Link>,
      };
    }
    if (beatsQuery.isError) {
      return {
        kind: "failed",
        title: "无法载入分镜",
        body: beatsQuery.error instanceof Error ? beatsQuery.error.message : "未知错误",
        action: <button type="button" onClick={() => { void beatsQuery.refetch(); }}>重新读取</button>,
      };
    }
    if (beats.length === 0) {
      return {
        kind: "empty",
        title: "还没有分镜计划",
        body: "分镜必须在旁白实测时长之后生成：先完成讲稿与 TTS，再按真实音频分配画面区间。",
        action: (
          <button type="button" onClick={() => setReplanOpen(true)} disabled={replan.isPending}>
            生成分镜计划
          </button>
        ),
      };
    }
    return null;
  }, [beats.length, beatsQuery, editions.isPending, overview.data, replan.isPending]);

  const plannedCounts = beatsQuery.data?.render_type_counts ?? {};
  const actualCounts = beatsQuery.data?.actual_render_type_counts ?? {};

  /* ---------- §B5.3 bottom bar ---------- */

  const goNext = () => {
    const search = new URLSearchParams();
    if (selectedBeatId) search.set("beat", selectedBeatId);
    if (editionId) search.set("edition", editionId);
    const suffix = search.toString();
    navigate(`${routes.explainerPage(projectId, "clips")}${suffix ? `?${suffix}` : ""}`);
  };

  useExplainerActionBar({
    primary:
      missingBeats.length > 0
        ? {
            label: `补齐缺失画面（${missingBeats.length} 个画面段）`,
            onClick: () => backfill.mutate(),
            disabled: missingBeats.length === 0 || backfill.isPending || !imageProfile.ready,
            disabledReason: !imageProfile.ready
              ? `图像模型未配置或不可执行：${imageProfile.option?.blockers?.[0]?.message ?? "请先配置可执行 Profile"}`
              : null,
            busy: backfill.isPending,
          }
        : { label: "下一步：视频片段", onClick: goNext },
    save: undefined,
    summary: `共 ${beats.length} 个画面段，${readyCount} 个已就绪`,
    defer: { label: "稍后处理", onClick: () => navigate(routes.explainerPage(projectId, "review")) },
  });

  /* ---------- render ---------- */

  const canAdoptPreview =
    Boolean(previewCandidate) &&
    previewCandidate?.status === "READY" &&
    !adopt.isPending;

  return (
    <div className="explainer-page explainer-steps45">
      <Panel
        title="分镜与画面"
        subtitle="这段话配什么画面、引用谁、选哪张图；AI 动态片段在第 5 步生成。"
        actions={
          <>
            {editions.data?.editions && editions.data.editions.length > 1 ? (
              <label className="explainer-field">
                输出版本
                <select
                  value={editionId ?? ""}
                  onChange={(event) =>
                    setSearchParams((params) => {
                      const next = new URLSearchParams(params);
                      next.set("edition", event.target.value);
                      return next;
                    })
                  }
                >
                  {editions.data.editions.map((edition) => {
                    const record = edition as unknown as Record<string, unknown>;
                    return (
                      <option key={String(record.id)} value={String(record.id)}>
                        {String(record.edition_key)} · {String(record.aspect_ratio)}
                      </option>
                    );
                  })}
                </select>
              </label>
            ) : null}
            <button type="button" className="explainer-btn" onClick={() => setReplanOpen(true)}>
              重新规划
            </button>
            {/* 批量处理同一类问题: generation registers candidates, adoption is a
                separate decision the operator makes once for every beat. */}
            <button
              type="button"
              className="explainer-btn explainer-btn--quiet"
              disabled={batchAdopt.isPending}
              onClick={() => batchAdopt.mutate({ confirm: false })}
            >
              预览待采用画面
            </button>
            <button
              type="button"
              className="explainer-btn explainer-btn--quiet"
              disabled={batchAdopt.isPending}
              onClick={() => batchAdopt.mutate({ confirm: true })}
            >
              采用全部已生成画面
            </button>
          </>
        }
      >
        <StateNotice state={state} />
        <InlineOk message={batchMessage} />
        {/* §B5.1: the top summary is only 共 N 个画面段，M 个已就绪. */}
        <p className="explainer-shot-overview">
          共 {beats.length} 个画面段，{readyCount} 个已就绪
          {missingBeats.length > 0 ? `（${missingBeats.length} 个待补齐画面）` : ""}
        </p>
        <details className="explainer-details">
          <summary>详情：计划/实际分布、哈希与运行状态</summary>
          <div className="explainer-coverage">
            <div>
              <small>计划类型分布</small>
              <strong>
                {Object.entries(plannedCounts)
                  .map(([type, count]) => `${renderTypeLabel(type)} ${count}`)
                  .join(" · ") || "—"}
              </strong>
            </div>
            <div>
              <small>实际类型分布</small>
              <strong>
                {Object.entries(actualCounts)
                  .map(([type, count]) => `${renderTypeLabel(type)} ${count}`)
                  .join(" · ") || "—"}
              </strong>
            </div>
            <div>
              <small>画面段修订</small>
              <strong>{selectedBeat ? `r${Number((selectedBeat as unknown as Record<string, unknown>).revision ?? 0)}` : "—"}</strong>
            </div>
            <div>
              <small>生成计划哈希</small>
              <strong>{draw.plan ? `${draw.plan.plan_hash.slice(0, 16)}…` : "尚未检查生成条件"}</strong>
            </div>
            <div>
              <small>本步就绪度</small>
              <strong>
                {readiness.data?.steps?.find((step) => step.page === "storyboard")?.status ?? "尚未提供"}
              </strong>
            </div>
          </div>
          <p className="explainer-setting-note">
            降级后的镜头不会被算作图生视频成功；计划类型与实际类型分别记录并保留回退原因。
            本片只能用真实 AI 图生视频：静图推拉已从产品中移除，must_be_motion 的画面段必须保持 AI 动态（图生视频）。
          </p>
        </details>
      </Panel>

      <div className="explainer-shot-workspace">
        <ShotListPane
          beats={beats}
          selectedBeatId={selectedBeatId}
          purpose={purpose}
          onSelect={(beatId) => {
            setPreviewId(null);
            setFeedback(null);
            setError(null);
            setImpactReport(null);
            setPendingMediaVersionId(null);
            setSearchParams((params) => {
              const next = new URLSearchParams(params);
              next.set("beat", beatId);
              return next;
            });
          }}
        />

        <section className="explainer-shot-stage" aria-label="画面预览与候选">
          {selectedBeat ? (
            <>
              <div className="explainer-stage-frame no-source">
                <div className="explainer-stage-media">
                  {displayCandidate?.media_version_id ? (
                    <MediaThumb
                      src={mediaVersionThumbUrl(displayCandidate.media_version_id, "medium")}
                      alt={`画面段 ${selectedBeat.code} 预览`}
                      aspectRatio="16 / 9"
                      objectFit="contain"
                      loading="eager"
                    />
                  ) : displayCandidate ? (
                    <MediaThumb
                      src={candidateThumbUrl(displayCandidate)}
                      alt={`画面段 ${selectedBeat.code} 预览`}
                      aspectRatio="16 / 9"
                      objectFit="contain"
                      loading="eager"
                    />
                  ) : (
                    <div className="explainer-placeholder" role="status">
                      <div>
                        <strong>画面段 {selectedBeat.code}</strong>
                        <div>这一段还没有可读的图片候选。</div>
                        <div>没有媒体时不会显示占位画面假装已有画面。</div>
                      </div>
                    </div>
                  )}
                </div>
              </div>

              <div className="explainer-preview-state">
                <span className="explainer-chip is-adopted">
                  当前采用：{adoptedCandidate ? candidateShortLabel(adoptedCandidate) : "尚无"}
                </span>
                <span className="explainer-chip">
                  正在预览：{previewCandidate ? `${candidateShortLabel(previewCandidate)}（未采用）` : "无"}
                </span>
                {selectedBeat.must_be_motion ? <span className="badge">规划要求：必须运动</span> : null}
                {retiredPlanOfSelectedBeat ? <span className="badge warn">计划方式已停用</span> : null}
              </div>

              {/* §B5.1: 采用 always sits under the current candidate preview. */}
              <div className="explainer-adopt-row">
                <span className="explainer-adopt-row__label">
                  {previewCandidate
                    ? `「采用」将把 ${candidateShortLabel(previewCandidate)} 写为当前采用；预览不会改变任何选择。`
                    : "先在候选卡片中预览一张图片；预览不等于采用。"}
                </span>
                <button
                  type="button"
                  className="explainer-btn"
                  disabled={!canAdoptPreview}
                  onClick={() =>
                    previewCandidate && adopt.mutate({ candidateId: previewCandidate.id, lock: false })
                  }
                >
                  采用
                </button>
                <button
                  type="button"
                  className="explainer-btn"
                  disabled={!canAdoptPreview}
                  onClick={() =>
                    previewCandidate && adopt.mutate({ candidateId: previewCandidate.id, lock: true })
                  }
                >
                  采用并锁定
                </button>
                {adoptedCandidate?.locked ? (
                  <button type="button" className="explainer-btn" onClick={() => unlock.mutate(adoptedCandidate)}>
                    解锁
                  </button>
                ) : null}
                <button
                  type="button"
                  className="explainer-btn explainer-btn--quiet"
                  disabled={!previewCandidate || archiveCandidate.isPending || Boolean(previewCandidate?.selected)}
                  title={
                    !previewCandidate
                      ? "先预览一个候选，再决定不采用"
                      : previewCandidate.selected
                        ? "当前采用版本不能直接归档；请先采用其他候选或解锁"
                        : "归档该候选：物理媒体与记录保留，可恢复，不影响当前采用"
                  }
                  onClick={() => previewCandidate && archiveCandidate.mutate(previewCandidate)}
                >
                  {archiveCandidate.isPending ? "正在归档" : "不采用"}
                </button>
              </div>

              {conflict ? (
                <div className="explainer-inline-error" role="alert">
                  <p>
                    采用被服务端拒绝：{conflict.message}。画面段已被更新，已保留当前选择与编辑文本；
                    刷新后可以基于最新修订重试，不会自动改用其他候选。
                  </p>
                  <button
                    type="button"
                    className="explainer-btn"
                    onClick={async () => {
                      await candidatesQuery.refetch();
                      setConflict(null);
                    }}
                  >
                    刷新后重试
                  </button>
                </div>
              ) : null}

              <MediaCandidateGrid
                page={effectivePage}
                loading={candidatesQuery.isPending}
                error={
                  candidatesQuery.isError
                    ? `${candidatesQuery.error instanceof Error ? candidatesQuery.error.message : "未知错误"}`
                    : null
                }
                previewedId={previewId}
                onPreview={(candidate) => setPreviewId(candidate ? candidate.id : null)}
                onRetryFailed={(candidate) => retryFailed.mutate(candidate)}
                onReject={(candidate) => archiveCandidate.mutate(candidate)}
                onReload={() => { void candidatesQuery.refetch(); }}
                onUpload={() => setPickerOpen(true)}
                onUnlock={(candidate) => unlock.mutate(candidate)}
                onCompare={() => setCompareOpen(true)}
                nextBatchCount={candidateCount}
                title="图片候选"
                emptyHint="点「生成」会在服务端完成预检后创建真实任务；也可以先上传或从媒体库选择。"
              />
              {candidatesQuery.isError ? (
                <p className="explainer-setting-note" role="status">
                  候选列表读取失败：这不表示该画面段没有候选，也不代表已采用的画面丢失；已知采用仍保留在上方。
                </p>
              ) : null}
              <div className="explainer-actions">
                <button type="button" className="explainer-btn" onClick={() => setCompareLimit(4)} disabled={compareLimit === 4}>
                  比较（最多 4 张）
                </button>
                {/* §B9 item 5: re-reading media/lists is its own action and never
                    starts a generation task. */}
                <button
                  type="button"
                  className="explainer-btn explainer-btn--quiet"
                  disabled={candidatesQuery.isFetching}
                  onClick={() => { void candidatesQuery.refetch(); }}
                >
                  重新载入候选
                </button>
                <span className="explainer-setting-note">比较只查看，不改变采用；重新载入只重新读取媒体与列表。</span>
              </div>

              {pendingMediaVersionId ? (
                <div className="explainer-receipt" role="status">
                  <h4>已选择素材（待登记为候选）</h4>
                  <p className="explainer-setting-note">
                    媒体版本 {pendingMediaVersionId} 已从项目媒体库/上传选择。登记后它只成为候选，不会自动采用；
                    内容与角色一致性检查不会因此运行，采用前保持未检查。
                  </p>
                  <div className="explainer-actions">
                    <button
                      type="button"
                      className="explainer-btn explainer-btn--primary"
                      disabled={registerMedia.isPending}
                      onClick={() =>
                        registerMedia.mutate({
                          mediaVersionId: pendingMediaVersionId,
                          mediaPurpose: purpose,
                        })
                      }
                    >
                      {registerMedia.isPending ? "正在登记" : `登记为${REGISTRATION_UNITS[purpose]}候选`}
                    </button>
                    <button
                      type="button"
                      className="explainer-btn explainer-btn--quiet"
                      disabled={registerMedia.isPending}
                      onClick={() => setPendingMediaVersionId(null)}
                    >
                      取消
                    </button>
                  </div>
                </div>
              ) : null}

              <InlineError message={error && (error.beatId === null || error.beatId === selectedBeatId) ? error.text : null} />
              <InlineOk message={feedback && (feedback.beatId === null || feedback.beatId === selectedBeatId) ? feedback.text : null} />
            </>
          ) : (
            <Panel title="画面段" subtitle="选择一个画面段">
              <p className="muted">选择一个画面段查看候选与要求。</p>
            </Panel>
          )}
        </section>

        <div className="explainer-stack">
          <GenerationControls
            purpose={purpose}
            candidateCounts={[1, 2, 4]}
            candidateCount={candidateCount}
            onCandidateCountChange={setCandidateCount}
            plan={draw.plan}
            planPending={draw.planPending}
            planError={draw.error}
            mode={effectiveMode}
            availableModes={[
              { mode: "TEXT_TO_IMAGE", label: "AI 配图（文生图）" },
              {
                mode: "IMAGE_EDIT",
                label: "按参考重绘",
                disabledReason: referenceSelections.length > 0 ? null : "需要已采用的参考图",
              },
            ]}
            onModeChange={(mode) => patchDraft({ mode })}
            budgetNote={`本次 ${candidateCount} 张；跨批追加不限固定累计张数，但受项目预算与留存策略限制。`}
          >
            <div className="explainer-setting-block">
              <label className="explainer-field">
                <span>画面来源</span>
                <select
                  value={draft.source}
                  onChange={(event) => patchDraft({ source: event.target.value as ShotSourceKey })}
                >
                  {SHOT_SOURCE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </label>
              <label className="explainer-field">
                <span>最终呈现方式</span>
                <select
                  value={draft.presentation}
                  onChange={(event) => {
                    const next = event.target.value as PresentationKey;
                    // A beat planned as 必须运动 must remain AI 图生视频: the two
                    // non-AI-picture routes cannot satisfy it, so the choice is kept
                    // instead of silently degrading the plan.
                    if (selectedBeat?.must_be_motion && next !== "AI_VIDEO") return;
                    patchDraft({ presentation: next });
                  }}
                >
                  {PRESENTATION_OPTIONS.map((option) => (
                    <option
                      key={option.value}
                      value={option.value}
                      // A beat planned as 必须运动 must remain AI 图生视频; the two
                      // non-AI-picture routes cannot satisfy it.
                      disabled={Boolean(selectedBeat?.must_be_motion) && option.value !== "AI_VIDEO"}
                    >
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <p className="explainer-setting-note">
                修改最终呈现方式会更新第 5 步的生成方式：AI 动态（图生视频）会调用真实的图生视频能力；
                图形动画与已有视频是另外两种来源，本页不会把它们写成 AI 动态。
                {selectedBeat?.must_be_motion
                  ? " 该画面段规划为「必须运动」：只能使用 AI 动态（图生视频）。"
                  : ""}
              </p>
              {retiredPlanOfSelectedBeat ? (
                <div className="explainer-gap-notice" role="alert">
                  <p>{RETIRED_RENDER_TYPE_NOTE}</p>
                  <div className="explainer-actions">
                    <button
                      type="button"
                      className="explainer-btn"
                      onClick={() => patchDraft({ presentation: "AI_VIDEO" })}
                    >
                      改为 AI 动态（图生视频）
                    </button>
                  </div>
                </div>
              ) : null}
              {selectedBeat?.must_be_motion && draft.presentation !== "AI_VIDEO" ? (
                <div className="explainer-gap-notice" role="alert">
                  <p>必须运动的画面段只能用 AI 动态（图生视频）；本页不会静默修改 must_be_motion。</p>
                  <div className="explainer-actions">
                    <button
                      type="button"
                      className="explainer-btn"
                      onClick={() => patchDraft({ presentation: "AI_VIDEO" })}
                    >
                      保持 AI 动态
                    </button>
                    <Link className="explainer-btn" to={routes.explainerPage(projectId, "assets")}>
                      调整人物与风格
                    </Link>
                  </div>
                </div>
              ) : null}
              {draft.presentation === "AI_VIDEO" && !videoProfileReady ? (
                <div className="explainer-gap-notice" role="alert">
                  <p>
                    本机没有可执行的 AI 动态能力（缺少 VIDEO_I2V：图生视频模型/工作流）。
                    静图推拉已从产品中移除：本片只能用真实图生视频，请先补上该能力。
                  </p>
                  <div className="explainer-actions">
                    <button type="button" className="explainer-btn" onClick={() => setCoverageOpen(true)}>
                      调整镜头
                    </button>
                    <Link className="explainer-btn" to={routes.systemCapabilities(projectId)}>
                      配置模型
                    </Link>
                  </div>
                </div>
              ) : null}

              <CapabilityPicker
                capability={effectiveMode === "IMAGE_EDIT" ? IMAGE_EDIT_CAPABILITY : IMAGE_CAPABILITY}
                label="图片模型"
                value=""
                onChange={() => undefined}
                query={imageOptions}
                showDetails={false}
              />

              <label className="explainer-field">
                <span>构图（仅语义提示）</span>
                <select value={draft.composition} onChange={(event) => patchDraft({ composition: event.target.value })}>
                  {COMPOSITION_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </label>
              <p className="explainer-setting-note">构图只作为提示词语义，不宣称模型具有精确镜头参数能力。</p>
              {sourceBlockReason ? (
                <div className="explainer-gap-notice" role="status">
                  <p>{sourceBlockReason}</p>
                  <div className="explainer-actions">
                    <button type="button" className="explainer-btn" onClick={() => setPickerOpen(true)}>
                      上传图片 / 选择素材
                    </button>
                    <button type="button" className="explainer-btn" onClick={() => patchDraft({ source: "AI_IMAGE" })}>
                      改用 AI 配图
                    </button>
                  </div>
                </div>
              ) : null}
            </div>

            <div className="explainer-setting-block">
              <span className="explainer-field">人物与场景引用（项目共享资产）</span>
              {assets.isPending ? <p className="muted">正在载入人物与场景…</p> : null}
              {assets.isError ? (
                <div className="explainer-inline-error" role="alert">
                  人物与场景读取失败：{assets.error instanceof Error ? assets.error.message : "未知错误"}
                  <button type="button" className="explainer-btn" onClick={() => { void assets.refetch(); }}>重新读取</button>
                </div>
              ) : null}
              {!assets.isPending && !assets.isError && entities.length === 0 ? (
                <p className="muted">该项目还没有人物/场景资产；先在第 2 步建立参考。</p>
              ) : null}
              {entities.length > 0 ? (
                <div className="explainer-multi-select" role="group" aria-label="人物与场景引用">
                  {entities.map((entity) => {
                    const checked = selectedEntityIds.includes(entity.id);
                    return (
                      <label className="explainer-multi-select__row" key={entity.id}>
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(event) =>
                            setSelectedEntityIds((current) =>
                              event.target.checked
                                ? [...new Set([...current, entity.id])]
                                : current.filter((id) => id !== entity.id),
                            )
                          }
                        />
                        <span>
                          <strong>{entity.name || entity.code}</strong>
                          <small>
                            {entity.referenceId
                              ? "已采用参考图 · 可作为生成输入"
                              : "尚无已采用的参考图：不会被当作已消费的参考"}
                          </small>
                        </span>
                      </label>
                    );
                  })}
                </div>
              ) : null}
              {entitiesMissingReference.length > 0 ? (
                <p className="explainer-setting-note">
                  已选 {entitiesMissingReference.length} 个对象还没有已采用的参考图；按参考重绘会保持不可用，
                  请先在第 2 步采用参考图。
                </p>
              ) : null}
            </div>

            <div className="explainer-setting-block">
              <SettingRow label="画面时长（来自旁白时间轴）" value={formatMs(selectedBeat?.preferred_duration_ms ?? null)} />
              <button type="button" className="explainer-btn" onClick={() => setCoverageOpen(true)} disabled={!selectedBeat}>
                调整覆盖区间
              </button>

              <details className="explainer-details">
                <summary>编辑提示词 / 高级设置</summary>
                <label className="explainer-field">
                  <span>正向提示词</span>
                  <textarea
                    aria-label="正向提示词"
                    value={draft.prompt}
                    placeholder={String((selectedBeat as unknown as Record<string, unknown> | null)?.prompt_intent ?? "按画面描述生成")}
                    onChange={(event) => patchDraft({ prompt: event.target.value })}
                  />
                </label>
                <label className="explainer-field">
                  <span>负向提示词</span>
                  <textarea value={draft.negative} onChange={(event) => patchDraft({ negative: event.target.value })} />
                </label>
                <label className="explainer-field">
                  <span>步数（服务端可调整范围来自 Profile）</span>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={draft.stepCount}
                    onChange={(event) => patchDraft({ stepCount: event.target.value })}
                  />
                </label>
                <div className="explainer-seed-row">
                  <span>种子（只读）</span>
                  <code>{draw.plan?.candidate_seeds?.length ? draw.plan.candidate_seeds.join("、") : "尚未检查生成条件"}</code>
                  <button
                    type="button"
                    className="explainer-btn explainer-btn--quiet"
                    onClick={() => {
                      const seeds = draw.plan?.candidate_seeds?.join(",");
                      if (seeds && typeof navigator !== "undefined" && navigator.clipboard) {
                        void navigator.clipboard.writeText(seeds);
                      }
                    }}
                  >
                    复制
                  </button>
                </div>
                <p className="explainer-setting-note">再生成必须使用新种子；技术重试沿用原种子与输入。</p>
              </details>

              <div className="explainer-draw-actions">
                <button
                  type="button"
                  className="explainer-btn"
                  disabled={!selectedBeat || draw.planPending || sourceBlocksDraw}
                  onClick={() => draw.start()}
                  title={sourceBlockReason ?? undefined}
                >
                  {draw.planPending ? "正在检查生成条件" : drawLabel}
                </button>
                <button
                  type="button"
                  className="explainer-btn"
                  disabled={!draw.canSubmit || sourceBlocksDraw}
                  onClick={() => draw.submit()}
                  title={sourceBlockReason ?? (draw.canSubmit ? undefined : "先检查生成条件：可执行后才允许提交")}
                >
                  {draw.submitPending ? "正在提交" : `提交生成（本次 ${candidateCount} 张）`}
                </button>
                <button type="button" className="explainer-btn" disabled={!selectedBeatId || impact.isPending} onClick={() => impact.mutate()}>
                  查看更换影响
                </button>
                {/* §B5.2/§B5.3: saving this beat's own description is independent of a
                    generation plan — the plan's 检查生成条件 is about ability and budget,
                    while 保存镜头描述 writes the description itself.  The button is only
                    disabled while the save is in flight or there is no beat selected. */}
                <button
                  type="button"
                  className="explainer-btn"
                  disabled={saveDescription.isPending || !selectedBeat}
                  onClick={() => saveDescription.mutate()}
                >
                  保存镜头描述
                </button>
                <button type="button" className="explainer-btn" onClick={() => setPickerOpen(true)} disabled={!projectId}>
                  上传图片 / 选择素材
                </button>
                <span className="explainer-note">
                  {draw.planIsStale
                    ? "生成条件已改变：请重新检查生成条件后再提交。"
                    : draw.plan
                      ? `已冻结计划：${draw.plan.status === "EXECUTABLE" ? "可执行" : "被阻塞"}`
                      : "尚未检查生成条件"}
                </span>
              </div>
            </div>
          </GenerationControls>

          {impactReport ? (
            <Panel title="更换影响" subtitle="依赖范围由服务端返回，前端不猜测。">
              <div aria-label="更换影响">
                <SettingRow label="受影响输出版本" value={`${Number(impactReport.affected_edition_count ?? 0)} 个`} />
                <SettingRow label="将过期的渲染" value={`${Number(impactReport.stale_render_count ?? 0)} 个`} />
                <SettingRow label="可复用素材" value={`${Number(impactReport.reusable_asset_count ?? 0)} 项`} />
                <SettingRow
                  label="画面段"
                  value={`${beats.length} 个（其中 ${beats.filter((beat) => beat.locked_by_human).length} 个人工锁定）`}
                />
                <p className="explainer-setting-note">
                  {String(impactReport.note ?? "更换该画面段会使后续时码与渲染过期；已批准的人工锁定镜头不会被批次操作改动。")}
                </p>
              </div>
            </Panel>
          ) : null}

          {draw.plan ? (
            <Panel title="生成计划（只读）" subtitle="提交前先看清本次真实能力、参考、种子与预算。">
              <dl className="explainer-plan-grid">
                <div>
                  <dt>预算</dt>
                  <dd>{draw.plan.budget ? JSON.stringify(draw.plan.budget) : "服务端未返回余量（待检查）"}</dd>
                </div>
                <div>
                  <dt>计划哈希</dt>
                  <dd>{draw.plan.plan_hash.slice(0, 16)}…</dd>
                </div>
                <div>
                  <dt>提交状态</dt>
                  <dd>{draw.planIsStale ? "生成条件已改变，需重新检查" : draw.plan.status === "EXECUTABLE" ? "可提交" : "被阻塞"}</dd>
                </div>
              </dl>
              <p className="explainer-setting-note">
                计划是只读的：它不创建媒体或任务；模型、参考图容量、种子与本次提交数量显示在上方生成设置里。
              </p>
            </Panel>
          ) : null}

          {draw.receipt ? (
            <Panel title="本次生成回执" subtitle="回执只表示已排队；候选变为「可用」后才会显示媒体。">
              <GenerationReceiptView receipt={draw.receipt} unit={unit} candidates={candidates} />
            </Panel>
          ) : null}

          <Panel title="本步规则" subtitle="避免把降级当成成功。">
            <p className="muted">技术重试与创作重抽分开计账；技术重试沿用原任务与种子，创作重抽是新的抽卡意图。</p>
            <p className="muted" style={{ marginTop: 8 }}>
              信息图里的地图、数字、日期、关系线和引用文字都由确定性图形/文字层生成；真实地理使用有来源的底图或标清「示意图」。
            </p>
          </Panel>
        </div>
      </div>

      <MediaCandidateCompare
        open={compareOpen}
        candidates={candidates.filter((candidate) => candidate.media_version_id).slice(0, compareLimit)}
        onClose={() => setCompareOpen(false)}
      />
      <MediaPickDrawer
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        projectId={projectId}
        mediaKind="IMAGE"
        label="上传图片 / 选择素材"
        value={pendingMediaVersionId ?? ""}
        onPick={(mediaVersionId) => setPendingMediaVersionId(mediaVersionId)}
        note="选择或上传只会登记为候选来源，不会直接成为采用的画面；采用必须在候选预览下明确点击。"
      />
      <CoverageDrawer
        open={coverageOpen}
        onClose={() => setCoverageOpen(false)}
        beat={selectedBeat}
        onReplan={() => {
          setCoverageOpen(false);
          setReplanOpen(true);
        }}
      />
      <Drawer open={replanOpen} title="重新规划影响" onClose={() => setReplanOpen(false)}>
        <SettingRow label="画面段" value={`${beats.length} 个`} />
        <SettingRow label="已就绪" value={`${readyCount} 个`} />
        <SettingRow label="人工锁定" value={`${beats.filter((beat) => beat.locked_by_human).length} 个（不会被清空）`} />
        <SettingRow
          label="受影响输出版本"
          value={`${Number(impactReport?.affected_edition_count ?? 0)} 个`}
        />
        <p className="explainer-setting-note">
          重新规划会按当前讲稿与实测配音重建分镜草案：先生成新草案并比较影响，已锁镜头与已采用结果保留；
          提交后由服务端的同一制作运行执行，不在前端伪造计划。
        </p>
        <div className="explainer-draw-actions">
          <button type="button" className="explainer-btn" disabled={replan.isPending} onClick={() => replan.mutate()}>
            {replan.isPending ? "正在预检" : beats.length === 0 ? "生成分镜计划" : "提交重新规划"}
          </button>
          <button type="button" className="explainer-btn explainer-btn--quiet" onClick={() => setReplanOpen(false)}>取消</button>
        </div>
        {replan.isError ? (
          <InlineError message={replan.error instanceof Error ? replan.error.message : String(replan.error)} />
        ) : null}
      </Drawer>
    </div>
  );
}
