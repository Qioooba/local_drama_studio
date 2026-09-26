/**
 * Shared explainer candidate views (spec B9/B10).
 *
 * These are the three genuinely new shared views the design asks for:
 * `MediaCandidateGrid`, `MediaCandidateCompare` and `GenerationControls`.
 * They are pure presentation: every domain call arrives as a callback, so the
 * same components serve beat keyframes, beat video clips and entity reference
 * images without knowing about episodes, shots or explainer routes.
 *
 * Meaning of the three distinct states (spec B12 item 3):
 *  - `selected`  the adopted working version written by the adopt command;
 *  - `previewed` the candidate the user is only looking at — never adopted;
 *  - `locked`    an explicit human lock, set only by "采用并锁定"/lock checkbox.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import "./explainers.candidates.css";
import { Dialog, Drawer, EmptyState, MediaThumb, Skeleton } from "../../components/ui/primitives";
import type {
  ExplainerCandidatePage,
  ExplainerCandidatePurpose,
  ExplainerGenerationMode,
  ExplainerGenerationPlan,
  ExplainerMediaCandidate,
} from "../../generated/api";

export type CandidateActionHandlers = {
  /** Adopt as the current working version. Never implies a lock. */
  onAdopt?: (candidate: ExplainerMediaCandidate) => void;
  /** Adopt and set an explicit human lock in one atomic step. */
  onAdoptAndLock?: (candidate: ExplainerMediaCandidate) => void;
  /** Remove the human lock without clearing the current choice. */
  onUnlock?: (candidate: ExplainerMediaCandidate) => void;
  /** New creative intent: new candidate batch, new seed, old results kept. */
  onRegenerate?: (count: number) => void;
  /** Technical retry of the failed original task, keeping candidate/seed/snapshot. */
  onRetryFailed?: (candidate: ExplainerMediaCandidate) => void;
  /** Re-read the media or the list only. Never starts a generation task. */
  onReload?: () => void;
  /** Upload or pick from the project media library; the result only becomes a candidate. */
  onUpload?: () => void;
  /** Hide/archive a candidate that is not the only valid adopted one. */
  onReject?: (candidate: ExplainerMediaCandidate) => void;
  onCompare?: () => void;
};

export type GenerationControlsProps = {
  purpose: ExplainerCandidatePurpose;
  candidateCounts: number[];
  candidateCount: number;
  onCandidateCountChange: (value: number) => void;
  plan: ExplainerGenerationPlan | null;
  planPending?: boolean;
  planError?: string | null;
  onPlan?: () => void;
  onSubmit?: () => void;
  submitPending?: boolean;
  submitDisabledReason?: string | null;
  mode?: ExplainerGenerationMode | null;
  availableModes?: Array<{ mode: ExplainerGenerationMode; label: string; disabledReason?: string | null }>;
  onModeChange?: (mode: ExplainerGenerationMode) => void;
  budgetNote?: string | null;
  children?: React.ReactNode;
};

const CANDIDATE_KIND_LABELS: Record<string, string> = {
  CREATIVE: "创作候选",
  TECHNICAL_RETRY: "技术重试",
};

const STATUS_LABELS: Record<string, string> = {
  PENDING: "已排队",
  GENERATING: "生成中",
  READY: "可用",
  REJECTED: "未采用",
  FAILED: "生成失败",
  SUPERSEDED: "已被替换",
};

export function candidateStatusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

export function candidateShortLabel(candidate: ExplainerMediaCandidate): string {
  return candidate.short_label ?? `候选 ${candidate.variant_no}`;
}

/** A missing or unreadable candidate thumbnail must never remove the card (spec B9). */
export function CandidateThumb({ candidate, className }: { candidate: ExplainerMediaCandidate; className?: string }) {
  const altSuffix = candidate.media_kind === "VIDEO" ? "视频" : "图片";
  const alt = `${candidateShortLabel(candidate)}${altSuffix}预览`;
  return (
    <MediaThumb
      src={candidate.thumbnail_url}
      alt={alt}
      // The thumbnail endpoint answers 409 MEDIA_DERIVATIVE_NOT_READY while the small
      // preview has not been materialised, which an <img> cannot distinguish from a
      // broken file.  Saying "待生成" keeps a slow derivative from reading as missing
      // media.
      emptyLabel="缩略图待生成"
      aspectRatio="16 / 9"
      objectFit="cover"
      className={className}
    />
  );
}

type GridProps = CandidateActionHandlers & {
  page: ExplainerCandidatePage | null;
  loading?: boolean;
  error?: string | null;
  previewedId: string | null;
  onPreview: (candidate: ExplainerMediaCandidate | null) => void;
  /** Number of candidates requested by the next generation action, shown next to the button. */
  nextBatchCount?: number;
  title?: string;
  compareDisabled?: boolean;
  emptyHint?: string;
};

/**
 * Candidate grid. First screen shows only thumbnail, ordinal, and the
 * generating/available/adopted/locked state; prompt, seed, profile and timings
 * live behind the per-candidate "详情" disclosure (spec B5.3).
 */
export function MediaCandidateGrid({
  page,
  loading = false,
  error = null,
  previewedId,
  onPreview,
  onAdopt,
  onAdoptAndLock,
  onUnlock,
  onRegenerate,
  onRetryFailed,
  onReload,
  onUpload,
  onReject,
  onCompare,
  nextBatchCount,
  title = "候选",
  compareDisabled,
  emptyHint,
}: GridProps) {
  const [detailId, setDetailId] = useState<string | null>(null);
  const candidates = page?.candidates ?? [];
  const adopted = candidates.find((item) => item.selected) ?? null;
  const previewed = candidates.find((item) => item.id === previewedId) ?? null;

  return (
    <section className="explainer-candidates" aria-label={title}>
      <header className="explainer-candidates__bar">
        <h3>{title}</h3>
        <span className="explainer-candidates__count">
          {page ? `${candidates.length} 个候选` : loading ? "正在读取候选" : "候选未读取"}
        </span>
        <div className="explainer-candidates__actions">
          {onCompare ? (
            <button
              type="button"
              className="explainer-btn"
              onClick={onCompare}
              disabled={Boolean(compareDisabled) || candidates.filter((item) => item.media_version_id).length < 2}
            >
              比较
            </button>
          ) : null}
          {onUpload ? (
            <button type="button" className="explainer-btn" onClick={onUpload}>
              上传 / 从媒体库选择
            </button>
          ) : null}
          {onRegenerate ? (
            <button type="button" className="explainer-btn explainer-btn--primary" onClick={() => onRegenerate(nextBatchCount ?? 1)}>
              再生成 {nextBatchCount ?? 1} {page?.owner_kind === "ENTITY" ? "张" : page?.purpose === "VISUAL" ? "段" : "张"}
            </button>
          ) : null}
        </div>
      </header>

      {error ? (
        <div className="explainer-inline-error" role="alert">
          <span>候选读取失败：{error}</span>
          {onReload ? (
            <button type="button" className="explainer-btn" onClick={onReload}>
              重新读取
            </button>
          ) : null}
          <span className="explainer-note">已保留当前采用的画面，不会因为列表读取失败而丢失选择。</span>
        </div>
      ) : null}

      {loading && !page ? <Skeleton label="正在读取候选" lines={3} /> : null}

      {!loading && !error && candidates.length === 0 ? (
        <EmptyState
          title="还没有候选"
          description={emptyHint ?? "点击生成会在服务端完成预检后创建真实任务；也可以先上传或从媒体库选择。"}
        />
      ) : null}

      <ul className="explainer-candidate-grid">
        {candidates.map((candidate) => {
          const isPreviewed = candidate.id === previewedId;
          const detailOpen = detailId === candidate.id;
          return (
            <li
              key={candidate.id}
              className={[
                "explainer-candidate-card",
                candidate.selected ? "is-selected" : "",
                isPreviewed ? "is-previewed" : "",
                candidate.status === "FAILED" ? "is-failed" : "",
              ]
                .filter(Boolean)
                .join(" ")}
            >
              <button
                type="button"
                className="explainer-candidate-card__preview"
                onClick={() => onPreview(candidate)}
                aria-pressed={isPreviewed}
                aria-label={`预览${candidateShortLabel(candidate)}`}
                disabled={!candidate.media_version_id && candidate.status !== "FAILED"}
              >
                <CandidateThumb candidate={candidate} />
              </button>
              <div className="explainer-candidate-card__meta">
                <span className="explainer-candidate-card__no">{candidateShortLabel(candidate)}</span>
                <span className={`explainer-candidate-card__status is-${candidate.status.toLowerCase()}`}>
                  {candidateStatusLabel(candidate.status)}
                </span>
                {candidate.selected ? <span className="explainer-chip is-adopted">当前采用</span> : null}
                {isPreviewed && !candidate.selected ? <span className="explainer-chip">正在预览</span> : null}
                {candidate.locked ? <span className="explainer-chip is-locked">已锁定</span> : null}
                {candidate.stale ? <span className="explainer-chip is-stale">需更新</span> : null}
                <span className="explainer-note">{CANDIDATE_KIND_LABELS[candidate.candidate_kind] ?? candidate.candidate_kind}</span>
                {candidate.render_type_actual ? <span className="explainer-note">{candidate.render_type_actual}</span> : null}
                {candidate.job_state ? <span className="explainer-note">任务 {candidate.job_state}</span> : null}
                {candidate.duration_ms ? <span className="explainer-note">{(candidate.duration_ms / 1000).toFixed(1)} 秒</span> : null}
              </div>

              {candidate.status === "FAILED" ? (
                <div className="explainer-candidate-card__error" role="alert">
                  <span>{candidate.error_message ?? "生成任务失败"}</span>
                  {onRetryFailed && candidate.retryable ? (
                    <button type="button" className="explainer-btn" onClick={() => onRetryFailed(candidate)}>
                      重试失败任务
                    </button>
                  ) : null}
                </div>
              ) : null}

              <div className="explainer-candidate-card__actions">
                {onAdopt && candidate.status === "READY" ? (
                  <button type="button" className="explainer-btn explainer-btn--primary" onClick={() => onAdopt(candidate)}>
                    采用
                  </button>
                ) : null}
                {onAdoptAndLock && candidate.status === "READY" ? (
                  <button type="button" className="explainer-btn" onClick={() => onAdoptAndLock(candidate)}>
                    采用并锁定
                  </button>
                ) : null}
                {onUnlock && candidate.locked ? (
                  <button type="button" className="explainer-btn" onClick={() => onUnlock(candidate)}>
                    解锁
                  </button>
                ) : null}
                {onReject && !candidate.selected ? (
                  <button type="button" className="explainer-btn explainer-btn--quiet" onClick={() => onReject(candidate)}>
                    不采用
                  </button>
                ) : null}
                <button
                  type="button"
                  className="explainer-btn explainer-btn--quiet"
                  onClick={() => setDetailId(detailOpen ? null : candidate.id)}
                  aria-expanded={detailOpen}
                >
                  详情
                </button>
              </div>

              {detailOpen ? (
                <dl className="explainer-candidate-detail">
                  <div>
                    <dt>候选 ID</dt>
                    <dd>{candidate.id}</dd>
                  </div>
                  <div>
                    <dt>媒体版本</dt>
                    <dd>{candidate.media_version_id ?? "尚无"}</dd>
                  </div>
                  <div>
                    <dt>种子</dt>
                    <dd>{candidate.seed ?? "由服务端分配"}</dd>
                  </div>
                  <div>
                    <dt>父候选</dt>
                    <dd>{candidate.parent_candidate_id ?? "无"}</dd>
                  </div>
                  <div>
                    <dt>计划呈现方式</dt>
                    <dd>{candidate.render_type_planned ?? "未记录"}</dd>
                  </div>
                  <div>
                    <dt>实际呈现方式</dt>
                    <dd>{candidate.render_type_actual ?? "未记录"}</dd>
                  </div>
                  <div className="explainer-candidate-detail__wide">
                    <dt>提示词</dt>
                    <dd>{candidate.prompt ?? "未记录"}</dd>
                  </div>
                  <div className="explainer-candidate-detail__wide">
                    <dt>媒体哈希</dt>
                    <dd>{candidate.media_sha256 ?? "未登记"}</dd>
                  </div>
                  <div className="explainer-candidate-detail__wide">
                    <dt>引用参考</dt>
                    <dd>{candidate.reference_media_version_ids.length ? candidate.reference_media_version_ids.join("、") : "无"}</dd>
                  </div>
                </dl>
              ) : null}
            </li>
          );
        })}
      </ul>

      {adopted && previewed && previewed.id !== adopted.id ? (
        <p className="explainer-note">
          正在预览 {candidateShortLabel(previewed)}；当前采用仍是 {candidateShortLabel(adopted)}。点“采用”才会改变当前选择。
        </p>
      ) : null}
    </section>
  );
}

type CompareProps = {
  open: boolean;
  candidates: ExplainerMediaCandidate[];
  onClose: () => void;
};

/**
 * Side-by-side comparison of 2 (default) up to 4 candidates, with one shared
 * play/pause/rewind control so two videos cannot play over each other (spec B6.2).
 */
export function MediaCandidateCompare({ open, candidates, onClose }: CompareProps) {
  const shown = useMemo(() => candidates.slice(0, 4), [candidates]);
  const refs = useRef<Array<HTMLVideoElement | HTMLAudioElement | null>>([]);
  const [playing, setPlaying] = useState(false);

  const pauseAll = useCallback(() => {
    refs.current.forEach((node) => node?.pause());
    setPlaying(false);
  }, []);

  useEffect(() => {
    if (!open) pauseAll();
    return () => pauseAll();
  }, [open, pauseAll]);

  const toggleAll = useCallback(() => {
    const nodes = refs.current.filter(Boolean) as Array<HTMLVideoElement | HTMLAudioElement>;
    if (!nodes.length) return;
    if (playing) {
      nodes.forEach((node) => node.pause());
      setPlaying(false);
      return;
    }
    nodes.forEach((node) => {
      node.currentTime = 0;
      void node.play().catch(() => undefined);
    });
    setPlaying(true);
  }, [playing]);

  return (
    <Dialog open={open} title="候选比较" onClose={onClose} size="wide">
      <div className="explainer-compare__bar">
        <button type="button" className="explainer-btn" onClick={toggleAll} disabled={!shown.length}>
          {playing ? "全部暂停" : "同步播放"}
        </button>
        <button type="button" className="explainer-btn" onClick={pauseAll} disabled={!playing}>
          回到开头
        </button>
        <span className="explainer-note">最多同时比较 4 个候选；比较只是查看，不会改变当前采用。</span>
      </div>
      <div className={`explainer-compare explainer-compare--${shown.length}`}>
        {shown.map((candidate, index) => (
          <figure key={candidate.id} className={candidate.selected ? "is-selected" : ""}>
            <figcaption>
              <span>{candidateShortLabel(candidate)}</span>
              {candidate.selected ? <span className="explainer-chip is-adopted">当前采用</span> : null}
              {candidate.locked ? <span className="explainer-chip is-locked">已锁定</span> : null}
            </figcaption>
            {candidate.media_kind === "VIDEO" && candidate.playback_url ? (
              <video
                ref={(node) => {
                  refs.current[index] = node;
                }}
                src={candidate.playback_url}
                poster={candidate.thumbnail_url ?? undefined}
                controls
                preload="none"
                playsInline
              />
            ) : (
              <img src={candidate.preview_url ?? candidate.thumbnail_url ?? undefined} alt={`${candidateShortLabel(candidate)}预览`} />
            )}
          </figure>
        ))}
      </div>
    </Dialog>
  );
}

/**
 * Generation controls: candidate count (1/2/4 for images, 1/2 for video), mode,
 * the frozen plan summary returned by the read-only plan endpoint, and the
 * real receipt state. The count is always visible next to the button so the user
 * knows how many items this action submits (spec B9 last paragraph).
 */
export function GenerationControls({
  purpose,
  candidateCounts,
  candidateCount,
  onCandidateCountChange,
  plan,
  planPending,
  planError,
  onPlan,
  onSubmit,
  submitPending,
  submitDisabledReason,
  mode,
  availableModes,
  onModeChange,
  budgetNote,
  children,
}: GenerationControlsProps) {
  const unit = purpose === "VISUAL" ? "段" : "张";
  const blocked = plan?.status === "BLOCKED";
  const canSubmit = Boolean(onSubmit) && !blocked && !submitPending && !submitDisabledReason;

  return (
    <section className="explainer-generation" aria-label="生成设置">
      <header className="explainer-generation__bar">
        <h3>生成设置</h3>
        {onPlan ? (
          <button type="button" className="explainer-btn" onClick={onPlan} disabled={planPending}>
            {planPending ? "正在检查" : "检查生成条件"}
          </button>
        ) : null}
      </header>

      {availableModes?.length ? (
        <label className="explainer-field">
          <span>生成方式</span>
          <select value={mode ?? ""} onChange={(event) => onModeChange?.(event.target.value as ExplainerGenerationMode)}>
            {availableModes.map((item) => (
              <option key={item.mode} value={item.mode} disabled={Boolean(item.disabledReason)}>
                {item.label}
                {item.disabledReason ? `（${item.disabledReason}）` : ""}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      <label className="explainer-field">
        <span>每次候选数</span>
        <select value={String(candidateCount)} onChange={(event) => onCandidateCountChange(Number(event.target.value))}>
          {candidateCounts.map((value) => (
            <option key={value} value={String(value)}>
              {value} {unit}
            </option>
          ))}
        </select>
      </label>

      {plan ? (
        <dl className="explainer-generation__plan">
          <div>
            <dt>本次提交</dt>
            <dd>
              {plan.candidate_count} {unit}
            </dd>
          </div>
          <div>
            <dt>模型</dt>
            <dd>{plan.profile_title ?? plan.execution_profile_version_id ?? "项目默认"}</dd>
          </div>
          <div>
            <dt>参考图</dt>
            <dd>
              {plan.reference_capacity.resolved_image_references}
              {plan.reference_capacity.max_image_references === null
                ? " 张（上限未声明）"
                : ` / ${plan.reference_capacity.max_image_references} 张`}
            </dd>
          </div>
          <div>
            <dt>种子</dt>
            <dd>{plan.candidate_seeds.length ? plan.candidate_seeds.join("、") : "由服务端分配"}</dd>
          </div>
          {plan.planned_duration_ms ? (
            <div>
              <dt>片段时长</dt>
              <dd>{(plan.planned_duration_ms / 1000).toFixed(1)} 秒</dd>
            </div>
          ) : null}
        </dl>
      ) : null}

      {plan && blocked ? (
        <div className="explainer-inline-error" role="alert">
          {plan.blockers.map((blocker) => (
            <div key={blocker.code}>{blocker.message}</div>
          ))}
        </div>
      ) : null}
      {planError ? (
        <div className="explainer-inline-error" role="alert">
          {planError}
        </div>
      ) : null}

      {children}

      {budgetNote ? <p className="explainer-note">{budgetNote}</p> : null}

      {onSubmit ? (
        <div className="explainer-generation__submit">
          <button type="button" className="explainer-btn explainer-btn--primary" onClick={onSubmit} disabled={!canSubmit}>
            {submitPending ? "正在提交" : `生成 ${candidateCount} ${unit}`}
          </button>
          {submitDisabledReason ? <span className="explainer-note">{submitDisabledReason}</span> : null}
        </div>
      ) : null}
    </section>
  );
}

/** Drawer wrapper so callers can show per-candidate technical detail without a new modal system. */
export function CandidateDetailDrawer({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <Drawer open={open} title={title} onClose={onClose}>
      {children}
    </Drawer>
  );
}
