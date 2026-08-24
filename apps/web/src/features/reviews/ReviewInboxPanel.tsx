import { useEffect, useRef, useState } from "react";
import {
  commitReviewBatch,
  getReviewContext,
  preflightReviewBatch,
  submitReview,
  type ReviewBatchPlan,
  type ReviewInboxItem,
  type ReviewTemplate,
} from "../../generated/api";
import { progressiveSlice } from "../shared/progressive";
import { VideoAnnotations } from "./VideoAnnotations";

const LIST_STEP = 12;
const REVIEW_REJECTION_PHRASES = [
  "人物身份或造型不一致",
  "动作或构图需要调整",
  "画面存在明显瑕疵",
  "连续性与相邻镜头不一致",
  "声音或字幕需要修正",
];

function appendReviewPhrase(current: string, phrase: string) {
  const normalized = current.trim();
  if (!normalized) return phrase;
  if (normalized.includes(phrase)) return current;
  return `${normalized}；${phrase}`;
}

function subjectLabel(item: ReviewInboxItem): string {
  return item.shot_code ?? item.episode_code ?? "项目级媒体";
}

const MEDIA_KIND_LABELS: Record<string, string> = {
  IMAGE: "图片",
  VIDEO: "视频",
  AUDIO: "音频",
  SUBTITLE: "字幕",
};
const STAGE_LABELS: Record<string, string> = {
  KEYFRAME: "关键帧",
  PROXY: "预览候选",
  FORMAL: "正式成片",
  RENDER: "整集成片",
  IMPORTED: "导入素材",
};
const DECISION_LABELS: Record<string, string> = {
  APPROVED: "已批准",
  REJECTED: "已拒绝",
  NEEDS_CHANGES: "需要修改",
  VOIDED: "已撤回",
};
const SELECTION_LABELS: Record<string, string> = {
  KEYFRAME: "当前关键帧",
  PROXY_WINNER: "首选预览",
  FORMAL_SELECTION: "正式采用版本",
};
const MACHINE_CHECK_LABELS: Record<string, string> = {
  integrated_loudness: "整体响度",
  true_peak: "峰值",
  peak: "峰值",
  clipping: "削波",
  file_integrity: "文件完整性",
  decode: "可解码",
  dimensions: "尺寸",
  fps: "帧率",
  duration: "时长",
  codec: "编码",
};

function mediaKindLabel(value: string | undefined) {
  return MEDIA_KIND_LABELS[String(value ?? "")] ?? String(value ?? "媒体");
}
function stageLabel(value: string | undefined) {
  return STAGE_LABELS[String(value ?? "")] ?? String(value ?? "候选");
}
function decisionLabel(value: string | null | undefined) {
  return value ? (DECISION_LABELS[value] ?? value) : "未审核";
}
function checkStatusLabel(value: unknown) {
  return value === "PASS"
    ? "通过"
    : value === "FAIL"
      ? "未通过"
      : value === "PENDING"
        ? "未运行"
        : String(value ?? "未运行");
}

function ThumbWithFallback({ src, label }: { src: string; label: string }) {
  const [failed, setFailed] = useState(false);
  if (failed)
    return (
      <span className="media-kind-placeholder" aria-hidden="true">
        {label}
      </span>
    );
  return (
    <img
      src={src}
      alt=""
      width="72"
      height="54"
      style={{ objectFit: "contain", background: "#0b0e12", borderRadius: 6 }}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
    />
  );
}

function PreviewImage({
  src,
  label,
  width,
  height,
}: {
  src: string;
  label: string;
  width: number;
  height: number;
}) {
  const [failed, setFailed] = useState(false);
  if (failed)
    return <strong className="preview-fallback">暂无法加载{label}预览</strong>;
  return (
    <img
      src={src}
      alt={label}
      style={{ maxWidth: "100%", maxHeight: height, width: "auto", height: "auto", objectFit: "contain", background: "#0b0e12", borderRadius: 8 }}
      decoding="async"
      onError={() => setFailed(true)}
    />
  );
}

export function ReviewInboxPanel({
  items,
  templates,
  selectedVersionId,
  context: requestedContext,
  onSelect,
  onPromote,
  selecting,
  onMachineCheck,
  machineChecking,
  machineCheckError,
  onSubmit,
  submitting,
  submitError,
  submitSucceeded,
  onBatchChanged,
}: {
  items: ReviewInboxItem[];
  templates: ReviewTemplate[];
  selectedVersionId: string | null;
  context: Awaited<ReturnType<typeof getReviewContext>> | undefined;
  onSelect: (id: string) => void;
  onPromote: (mediaVersionId: string, selectionType: string) => void;
  selecting: boolean;
  onMachineCheck: (mediaVersionId: string) => void;
  machineChecking: boolean;
  machineCheckError: string | null;
  onSubmit: (
    mediaVersionId: string,
    payload: Parameters<typeof submitReview>[1],
  ) => void;
  submitting: boolean;
  submitError: string | null;
  submitSucceeded: boolean;
  onBatchChanged?: () => void;
}) {
  const [checks, setChecks] = useState<Record<string, "PASS" | "FAIL">>({});
  const [decision, setDecision] = useState<
    "APPROVED" | "REJECTED" | "NEEDS_CHANGES"
  >("APPROVED");
  const [reviewComment, setReviewComment] = useState("");
  const [awaitingRejectReason, setAwaitingRejectReason] = useState(false);
  const [rejectReasonDraft, setRejectReasonDraft] = useState("");
  const [compareVersionId, setCompareVersionId] = useState<string | null>(null);
  const [referencePinned, setReferencePinned] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [loopPreview, setLoopPreview] = useState(false);
  const [mutedPreview, setMutedPreview] = useState(false);
  const [videoCurrentTimeMs, setVideoCurrentTimeMs] = useState(0);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const reviewItemRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const keyboardSelection = useRef(false);
  const [syncVideoIds, setSyncVideoIds] = useState<string[]>([]);
  const syncVideoRefs = useRef<Record<string, HTMLVideoElement | null>>({});
  const [batchSelectedIds, setBatchSelectedIds] = useState<Set<string>>(
    new Set(),
  );
  const [batchPlan, setBatchPlan] = useState<ReviewBatchPlan | null>(null);
  const [batchDecision, setBatchDecision] = useState<
    "APPROVED" | "REJECTED" | "NEEDS_CHANGES"
  >("APPROVED");
  const [batchChecks, setBatchChecks] = useState<
    Record<string, "PASS" | "FAIL">
  >({});
  const [batchComment, setBatchComment] = useState("");
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchError, setBatchError] = useState<string | null>(null);
  const [batchNotice, setBatchNotice] = useState<string | null>(null);
  useEffect(() => {
    if (!batchNotice) return undefined;
    const timeout = window.setTimeout(() => setBatchNotice(null), 3_000);
    return () => window.clearTimeout(timeout);
  }, [batchNotice]);
  const [mediaFilter, setMediaFilter] = useState("ALL");
  const [reviewFilter, setReviewFilter] = useState("PENDING");
  const [projectFilter, setProjectFilter] = useState("ALL");
  const [episodeFilter, setEpisodeFilter] = useState("ALL");
  const [ageFilter, setAgeFilter] = useState("ALL");
  const [priorityFilter, setPriorityFilter] = useState("ALL");
  const [blockingFilter, setBlockingFilter] = useState("ALL");
  const [visibleCount, setVisibleCount] = useState(LIST_STEP);
  const selectedItem = items.find(
    (item) => item.media_version_id === selectedVersionId,
  );
  const selectedIndex = items.findIndex(
    (item) => item.media_version_id === selectedVersionId,
  );
  const projectOptions = Array.from(
    new Map(
      items.map((item) => [
        String(item.project_id),
        String(item.project_code ?? item.project_id),
      ]),
    ).entries(),
  ).sort((left, right) => left[1].localeCompare(right[1]));
  const episodeOptions = Array.from(
    new Map(
      items
        .filter(
          (item) =>
            (projectFilter === "ALL" ||
              String(item.project_id) === projectFilter) &&
            (episodeFilter === "ALL" ||
              String(item.episode_id ?? "") === episodeFilter),
        )
        .map((item) => [
          String(item.episode_id ?? ""),
          String(item.episode_code ?? item.episode_id ?? "未分集"),
        ]),
    ).entries(),
  ).sort((left, right) => left[1].localeCompare(right[1]));
  const filterAge = (item: ReviewInboxItem) => {
    const ageHours = Number(item.age_hours ?? 0);
    if (ageFilter === "NEW") return ageHours <= 24;
    if (ageFilter === "AGING") return ageHours > 24 && ageHours <= 168;
    if (ageFilter === "OLD") return ageHours > 168;
    return true;
  };
  const filteredItems = items.filter((item) => {
    const projectMatches =
      projectFilter === "ALL" || String(item.project_id) === projectFilter;
    const episodeMatches =
      episodeFilter === "ALL" ||
      String(item.episode_id ?? "") === episodeFilter;
    const priorityMatches =
      priorityFilter === "ALL" ||
      String(item.priority ?? "NORMAL") === priorityFilter;
    const blockingMatches =
      blockingFilter === "ALL" ||
      (blockingFilter === "BLOCKED"
        ? Number(item.is_blocked ?? 0) === 1
        : Number(item.is_blocked ?? 0) === 0);
    const statusMatches =
      reviewFilter === "PENDING"
        ? !item.decision ||
          item.decision === "NEEDS_CHANGES" ||
          item.decision === "VOIDED"
        : reviewFilter === "STALE"
          ? Boolean(item.is_stale)
          : true;
    return (
      projectMatches &&
      episodeMatches &&
      filterAge(item) &&
      priorityMatches &&
      blockingMatches &&
      (mediaFilter === "ALL" || item.media_kind === mediaFilter) &&
      statusMatches
    );
  });
  const filteredSelectedIndex = filteredItems.findIndex(
    (item) => item.media_version_id === selectedVersionId,
  );
  const visibleItems = progressiveSlice(
    filteredItems,
    visibleCount,
    filteredSelectedIndex,
  );
  const nextReviewItem =
    filteredItems[filteredSelectedIndex + 1] ??
    filteredItems.find((item) => item.media_version_id !== selectedVersionId);
  const selectionIsVisible = filteredSelectedIndex >= 0;
  // Keep the previous request cached, but never render its detail while the
  // controlled selection is moving to the first item that remains visible.
  const context = selectionIsVisible ? requestedContext : undefined;
  const selectedMediaKind =
    selectedItem?.media_kind ?? String(context?.media_version.media_kind ?? "");
  const selectedStage =
    selectedItem?.stage ?? String(context?.media_version.stage ?? "");
  const videoCandidates = items
    .filter(
      (item) => item.media_kind === "VIDEO" && item.stage === selectedStage,
    )
    .slice(0, 8);
  const supportsThumbnail = (mediaKind: string | undefined) =>
    mediaKind === "IMAGE" || mediaKind === "VIDEO";
  const selectionType =
    selectedMediaKind === "AUDIO" && selectedStage !== "FORMAL"
      ? null
      : selectedStage === "FORMAL"
        ? "FORMAL_SELECTION"
        : selectedStage === "PROXY"
          ? "PROXY_WINNER"
          : "KEYFRAME";
  const currentSelection = selectionType
    ? context?.selections.find(
        (item) =>
          item["media_version_id"] === selectedVersionId &&
          item["selection_type"] === selectionType,
      )
    : undefined;
  const currentApproval = context?.reviews.find(
    (item) => item["decision"] === "APPROVED" && !item["is_stale"],
  );
  const latestMachineCheck = context?.machine_checks[0];
  const machineResults = Array.isArray(latestMachineCheck?.["results"])
    ? (latestMachineCheck["results"] as Array<Record<string, unknown>>)
    : [];
  const audioQcPassed =
    selectedMediaKind !== "AUDIO" || latestMachineCheck?.["status"] === "PASS";
  const formalVideoQcPassed =
    selectedMediaKind !== "VIDEO" ||
    selectedStage !== "FORMAL" ||
    latestMachineCheck?.["status"] === "PASS";
  const templateCodeForItem = (item: ReviewInboxItem) =>
    item.media_kind === "AUDIO"
      ? "audio_mix"
      : item.media_kind === "VIDEO" && item.stage === "FORMAL"
        ? "formal_video"
        : item.media_kind === "VIDEO"
          ? "proxy_video"
          : "image_asset";
  const batchItems = items.filter((item) =>
    batchSelectedIds.has(item.media_version_id),
  );
  const batchTemplate =
    batchItems.length > 0
      ? templates
          .filter(
            (template) => template.code === templateCodeForItem(batchItems[0]),
          )
          .sort((left, right) => right.version_no - left.version_no)[0]
      : undefined;
  const batchCompatible =
    Boolean(batchTemplate) &&
    batchItems.every(
      (item) =>
        templateCodeForItem(item) === batchTemplate?.code &&
        item.project_id === batchItems[0]?.project_id,
    );
  const batchChecksComplete =
    batchTemplate?.items.every((item) => Boolean(batchChecks[item.id])) ??
    false;
  const videoFps =
    Number(context?.media_version.fps_num ?? 0) > 0 &&
    Number(context?.media_version.fps_den ?? 0) > 0
      ? Number(context?.media_version.fps_num) /
        Number(context?.media_version.fps_den)
      : 24;
  const videoFrameDurationMs = 1000 / videoFps;
  const videoDurationMs = Math.max(
    0,
    Number(context?.media_version.duration_ms ?? 0),
  );
  useEffect(() => {
    setChecks({});
    setDecision("APPROVED");
    setReviewComment("");
    setCompareVersionId(null);
    setPlaybackRate(1);
    setLoopPreview(false);
    setMutedPreview(false);
    setVideoCurrentTimeMs(0);
    setSyncVideoIds(
      selectedVersionId && selectedMediaKind === "VIDEO"
        ? [selectedVersionId]
        : [],
    );
    syncVideoRefs.current = {};
    setAwaitingRejectReason(false);
    setRejectReasonDraft("");
  }, [selectedVersionId, selectedMediaKind]);
  useEffect(() => {
    if (selectionIsVisible || filteredItems.length === 0) return;
    onSelect(filteredItems[0].media_version_id);
  }, [
    ageFilter,
    blockingFilter,
    episodeFilter,
    items,
    mediaFilter,
    onSelect,
    priorityFilter,
    projectFilter,
    reviewFilter,
    selectedVersionId,
    selectionIsVisible,
  ]);
  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = playbackRate;
  }, [playbackRate, selectedVersionId]);
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const onTimeUpdate = () =>
      setVideoCurrentTimeMs(Math.round(video.currentTime * 1000));
    video.addEventListener("timeupdate", onTimeUpdate);
    return () => video.removeEventListener("timeupdate", onTimeUpdate);
  }, [selectedVersionId, selectedMediaKind]);
  useEffect(() => {
    setVisibleCount(LIST_STEP);
  }, [items]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        !selectedVersionId ||
        target?.closest("input,select,textarea") ||
        (target?.closest("button") && !target.closest(".review-row"))
      )
        return;
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      const next = selectedIndex + (event.key === "ArrowRight" ? 1 : -1);
      if (next >= 0 && next < items.length) {
        event.preventDefault();
        keyboardSelection.current = true;
        onSelect(items[next].media_version_id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, onSelect, selectedIndex, selectedVersionId]);
  useEffect(() => {
    if (!keyboardSelection.current || !selectedVersionId) return;
    keyboardSelection.current = false;
    reviewItemRefs.current[selectedVersionId]?.focus({ preventScroll: true });
  }, [selectedVersionId]);
  const requiredComplete =
    context?.template.items.every(
      (item) => !item.required || Boolean(checks[item.id]),
    ) ?? false;
  const missingRequired = currentApproval
    ? []
    : (context?.template.items.filter(
        (item) => item.required && !checks[item.id],
      ) ?? []);
  const submitCurrentReview = () => {
    if (!context || !selectedVersionId) return;
    let comment = reviewComment.trim();
    if (decision === "REJECTED" && !comment) {
      setAwaitingRejectReason(true);
      return;
    }
    onSubmit(selectedVersionId, {
      template_version_id: context.template.id,
      decision,
      expected_subject_revision: context.subject_revision,
      checks: context.template.items.map((item) => ({
        item_id: item.id,
        result: checks[item.id],
      })),
      comment: comment || undefined,
    });
  };
  const submitRejectedReview = () => {
    if (!context || !selectedVersionId || !rejectReasonDraft.trim()) return;
    const comment = rejectReasonDraft.trim();
    setReviewComment(comment);
    setAwaitingRejectReason(false);
    setRejectReasonDraft("");
    onSubmit(selectedVersionId, {
      template_version_id: context.template.id,
      decision,
      expected_subject_revision: context.subject_revision,
      checks: context.template.items.map((item) => ({
        item_id: item.id,
        result: checks[item.id],
      })),
      comment,
    });
  };
  const toggleBatchItem = (mediaVersionId: string) => {
    setBatchNotice(null);
    setBatchError(null);
    setBatchPlan(null);
    setBatchSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(mediaVersionId)) next.delete(mediaVersionId);
      else next.add(mediaVersionId);
      return next;
    });
  };
  const preflightBatch = async () => {
    if (!batchCompatible || batchItems.length === 0 || !batchTemplate) return;
    setBatchBusy(true);
    setBatchError(null);
    setBatchNotice(null);
    try {
      const result = await preflightReviewBatch(
        batchItems[0].project_id,
        batchItems.map((item) => ({
          media_version_id: item.media_version_id,
          template_version_id: batchTemplate.id,
        })),
      );
      setBatchPlan(result.plan);
      // Preflight only freezes the batch membership and subject revisions. It
      // must never invent a human checklist decision.
      setBatchChecks({});
    } catch (error) {
      setBatchError(String(error));
    } finally {
      setBatchBusy(false);
    }
  };
  const commitBatch = async () => {
    if (
      !batchPlan ||
      !batchTemplate ||
      !batchChecksComplete ||
      (batchDecision === "REJECTED" && !batchComment.trim())
    )
      return;
    setBatchBusy(true);
    setBatchError(null);
    setBatchNotice(null);
    try {
      const result = await commitReviewBatch(batchPlan.plan_token, {
        decision: batchDecision,
        checks: batchTemplate.items.map((item) => ({
          item_id: item.id,
          result: batchChecks[item.id],
        })),
        comment: batchComment.trim() || undefined,
      });
      setBatchNotice(
        `批量审核已提交：${result.result.items.length} 项，计划 ${result.result.plan_id.slice(0, 12)}。`,
      );
      setBatchPlan(null);
      setBatchSelectedIds(new Set());
      onBatchChanged?.();
    } catch (error) {
      setBatchError(String(error));
    } finally {
      setBatchBusy(false);
    }
  };
  const toggleSyncVideo = (mediaVersionId: string) => {
    setSyncVideoIds((current) =>
      current.includes(mediaVersionId)
        ? current.filter((id) => id !== mediaVersionId)
        : current.length < 4
          ? [...current, mediaVersionId]
          : current,
    );
  };
  const syncVideoAction = (action: "play" | "pause") => {
    syncVideoIds.forEach((id) => {
      const video = syncVideoRefs.current[id];
      if (!video) return;
      if (action === "play") void video.play().catch(() => undefined);
      else video.pause();
    });
  };
  const syncVideoStep = (delta: number) => {
    const current =
      syncVideoIds.map(
        (id) => syncVideoRefs.current[id]?.currentTime ?? 0,
      )[0] ?? 0;
    syncVideoIds.forEach((id) => {
      const video = syncVideoRefs.current[id];
      if (video) video.currentTime = Math.max(0, current + delta);
    });
  };
  const stepVideoFrame = (direction: -1 | 1) => {
    if (!videoRef.current) return;
    const duration = Number.isFinite(videoRef.current.duration)
      ? videoRef.current.duration
      : videoDurationMs / 1000;
    const next = Math.min(
      Math.max(
        0,
        videoRef.current.currentTime +
          (direction * videoFrameDurationMs) / 1000,
      ),
      Math.max(0, duration - videoFrameDurationMs / 1000),
    );
    videoRef.current.currentTime = next;
    setVideoCurrentTimeMs(Math.round(next * 1000));
  };
  return (
    <section className="panel" aria-labelledby="review-inbox-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">审核收件箱</p>
          <h3 id="review-inbox-title">媒体版本审核与选择</h3>
        </div>
        <span
          className="status-pill"
          aria-label={`待处理媒体版本 ${items.length} 个`}
        >
          {items.length} 个待处理
        </span>
      </div>
      <p className="muted">
        审核表单、机器检查和当前采用版本均由系统管理；采用候选与批准内容是两项独立决定，上游改变后会明确提示重新审核。
      </p>
      {selectionIsVisible && context && (
        <p className="review-guidance" role="status">
          系统已固定本次审核表单；以后更新表单也不会改写这次结论。
        </p>
      )}
      <div className="review-filters" role="search" aria-label="审核收件箱筛选">
        <label>
          媒体类型
          <select
            value={mediaFilter}
            onChange={(event) => {
              setMediaFilter(event.target.value);
              setVisibleCount(LIST_STEP);
            }}
          >
            <option value="ALL">全部</option>
            <option value="IMAGE">图片</option>
            <option value="VIDEO">视频</option>
            <option value="AUDIO">音频</option>
          </select>
        </label>
        {projectOptions.length > 1 && <label>
          项目
          <select
            aria-label="项目筛选"
            value={projectFilter}
            onChange={(event) => {
              setProjectFilter(event.target.value);
              setVisibleCount(LIST_STEP);
            }}
          >
            <option value="ALL">全部项目</option>
            {projectOptions.map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
        </label>}
        {episodeOptions.length > 1 && <label>
          集
          <select
            aria-label="集筛选"
            value={episodeFilter}
            onChange={(event) => {
              setEpisodeFilter(event.target.value);
              setVisibleCount(LIST_STEP);
            }}
          >
            <option value="ALL">全部集</option>
            {episodeOptions.map(([id, label]) => (
              <option key={id || "none"} value={id}>
                {label}
              </option>
            ))}
          </select>
        </label>}
        <label>
          年龄
          <select
            aria-label="年龄筛选"
            value={ageFilter}
            onChange={(event) => {
              setAgeFilter(event.target.value);
              setVisibleCount(LIST_STEP);
            }}
          >
            <option value="ALL">全部年龄</option>
            <option value="NEW">≤ 1 天</option>
            <option value="AGING">1–7 天</option>
            <option value="OLD">&gt; 7 天</option>
          </select>
        </label>
        <label>
          优先级
          <select
            aria-label="优先级筛选"
            value={priorityFilter}
            onChange={(event) => {
              setPriorityFilter(event.target.value);
              setVisibleCount(LIST_STEP);
            }}
          >
            <option value="ALL">全部优先级</option>
            <option value="HIGH">高优先级</option>
            <option value="NORMAL">普通优先级</option>
          </select>
        </label>
        <label>
          阻塞
          <select
            aria-label="阻塞筛选"
            value={blockingFilter}
            onChange={(event) => {
              setBlockingFilter(event.target.value);
              setVisibleCount(LIST_STEP);
            }}
          >
            <option value="ALL">全部</option>
            <option value="BLOCKED">仅阻塞</option>
            <option value="READY">仅可处理</option>
          </select>
        </label>
        <label>
          审核状态
          <select
            value={reviewFilter}
            onChange={(event) => {
              setReviewFilter(event.target.value);
              setVisibleCount(LIST_STEP);
            }}
          >
            <option value="PENDING">待处理 / 需修改</option>
            <option value="STALE">需要重新审核</option>
            <option value="ALL">全部历史</option>
          </select>
        </label>
        <span className="muted" role="status" aria-live="polite">
          显示 {filteredItems.length}/{items.length} ·
          读取顺序稳定，筛选不改变审核决定
        </span>
      </div>
      {awaitingRejectReason && (
        <div
          className="inline-note-box"
          role="dialog"
          aria-label="填写拒绝原因"
        >
          <strong>拒绝必须填写原因</strong>
          <textarea
            autoFocus
            aria-label="单条拒绝原因"
            value={rejectReasonDraft}
            onChange={(event) => setRejectReasonDraft(event.target.value)}
            placeholder="说明需要修改的具体问题，会写入审核记录"
          />
          <div className="action-row" aria-label="常用拒绝原因">
            {REVIEW_REJECTION_PHRASES.map((phrase) => (
              <button
                key={phrase}
                type="button"
                className="secondary"
                onClick={() =>
                  setRejectReasonDraft((current) =>
                    appendReviewPhrase(current, phrase),
                  )
                }
              >
                {phrase}
              </button>
            ))}
          </div>
          <div className="action-row">
            <button
              className="primary-action"
              type="button"
              onClick={() => void submitRejectedReview()}
              disabled={!rejectReasonDraft.trim()}
            >
              确认拒绝
            </button>
            <button
              className="secondary"
              type="button"
              onClick={() => setAwaitingRejectReason(false)}
            >
              取消
            </button>
          </div>
        </div>
      )}
      {selectionIsVisible &&
        selectedVersionId &&
        selectionType &&
        currentSelection && (
          <div className="selection-history-actions">
            <span className="muted">
              当前已采用 {SELECTION_LABELS[selectionType] ?? selectionType}；记录可追溯，允许改选。
            </span>
            <button
              type="button"
              className="secondary"
              onClick={() => onPromote(selectedVersionId, selectionType)}
              disabled={selecting}
            >
              {selecting ? "保存中…" : `重新采用为${SELECTION_LABELS[selectionType] ?? selectionType}`}
            </button>
          </div>
        )}
      {selectionIsVisible && selectedItem && (
        <details className="review-technical-id">
          <summary>高级：当前媒体版本技术标识</summary>
          <code>{selectedItem.media_version_id}</code>
        </details>
      )}
      <section className="batch-review-panel" aria-label="批量审核保护">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">显式批量审核</p>
            <strong>批量审核保护</strong>
          </div>
          <span className="status-pill">已选 {batchItems.length} 项</span>
        </div>
        <p className="muted">
          必须显式勾选；同一项目、同一审核表单；系统会先锁定本批次再一次提交，不会静默产生部分成功。
        </p>
        <div className="batch-review-picker">
          {items.slice(0, 40).map((item) => (
            <label key={item.media_version_id}>
              <input
                type="checkbox"
                checked={batchSelectedIds.has(item.media_version_id)}
                onChange={() => toggleBatchItem(item.media_version_id)}
              />
              <span>
                {subjectLabel(item)} · {stageLabel(item.stage)} ·{" "}
                {mediaKindLabel(item.media_kind)}
              </span>
            </label>
          ))}
        </div>
        {batchItems.length > 0 && !batchCompatible && (
          <p className="review-guidance">
            批量项必须属于同一项目且使用同一审核表单（不能混合图片、预览视频、正式视频或音频）。
          </p>
        )}
        <div className="action-row">
          <button
            type="button"
            className="secondary"
            onClick={() => void preflightBatch()}
            disabled={!batchCompatible || batchBusy}
          >
            {batchBusy ? "处理中…" : batchPlan ? "重新检查" : "检查批量审核"}
          </button>
          {batchPlan && (
            <span className="ok-text">
              检查通过 · {batchPlan.items.length} 项 · 5 分钟内有效
            </span>
          )}
        </div>
        {batchPlan && batchTemplate && (
          <div className="batch-review-submit">
            <div className="review-checklist">
              {batchTemplate.items.map((item) => (
                <fieldset className="review-check" key={item.id}>
                  <legend>{item.label} *</legend>
                  <label>
                    <input
                      type="radio"
                      name={`batch-check-${item.id}`}
                      checked={batchChecks[item.id] === "PASS"}
                      onChange={() =>
                        setBatchChecks((value) => ({
                          ...value,
                          [item.id]: "PASS",
                        }))
                      }
                    />
                    通过
                  </label>
                  <label>
                    <input
                      type="radio"
                      name={`batch-check-${item.id}`}
                      checked={batchChecks[item.id] === "FAIL"}
                      onChange={() =>
                        setBatchChecks((value) => ({
                          ...value,
                          [item.id]: "FAIL",
                        }))
                      }
                    />
                    不通过
                  </label>
                </fieldset>
              ))}
            </div>
            <label>
              批量决定
              <select
                value={batchDecision}
                onChange={(event) =>
                  setBatchDecision(event.target.value as typeof batchDecision)
                }
              >
                <option value="APPROVED">批准</option>
                <option value="NEEDS_CHANGES">需要修改</option>
                <option value="REJECTED">拒绝</option>
              </select>
            </label>
            {batchDecision === "REJECTED" && (
              <>
                <label>
                  拒绝原因
                  <textarea
                    aria-label="批量拒绝原因"
                    value={batchComment}
                    onChange={(event) => setBatchComment(event.target.value)}
                    placeholder="填写批量拒绝原因"
                  />
                </label>
                <div className="action-row" aria-label="批量常用拒绝原因">
                  {REVIEW_REJECTION_PHRASES.map((phrase) => (
                    <button
                      key={phrase}
                      type="button"
                      className="secondary"
                      onClick={() =>
                        setBatchComment((current) =>
                          appendReviewPhrase(current, phrase),
                        )
                      }
                    >
                      {phrase}
                    </button>
                  ))}
                </div>
              </>
            )}
            <button
              type="button"
              className="primary-action"
              onClick={() => void commitBatch()}
              disabled={
                batchBusy ||
                !batchChecksComplete ||
                (batchDecision === "REJECTED" && !batchComment.trim())
              }
            >
              {batchBusy ? "提交中…" : "提交批量审核"}
            </button>
          </div>
        )}
        {batchError && (
          <p className="inline-error" role="alert">
            批量审核失败：{batchError}
          </p>
        )}
        {batchNotice && (
          <p className="review-success" role="status">
            {batchNotice}
          </p>
        )}
      </section>
      {selectedMediaKind === "VIDEO" && videoCandidates.length > 0 && (
        <section className="video-sync-compare" aria-label="视频候选同步比较">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">同步比较</p>
              <strong>多候选同步比较（最多 4 路）</strong>
            </div>
            <span className="muted">{syncVideoIds.length} 路</span>
          </div>
          <div className="video-sync-picker">
            {videoCandidates.map((item, index) => (
              <label key={item.media_version_id}>
                <input
                  type="checkbox"
                  checked={syncVideoIds.includes(item.media_version_id)}
                  disabled={
                    !syncVideoIds.includes(item.media_version_id) &&
                    syncVideoIds.length >= 4
                  }
                  onChange={() => toggleSyncVideo(item.media_version_id)}
                />
                <span>视频候选 {index + 1}</span>
              </label>
            ))}
          </div>
          <div className="video-sync-controls">
            <button
              type="button"
              className="secondary"
              onClick={() => syncVideoAction("play")}
              disabled={syncVideoIds.length < 2}
            >
              同步播放
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => syncVideoAction("pause")}
              disabled={syncVideoIds.length < 2}
            >
              同步暂停
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => syncVideoStep(-0.1)}
              disabled={syncVideoIds.length < 2}
            >
              全部短退 0.1s
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => syncVideoStep(0.1)}
              disabled={syncVideoIds.length < 2}
            >
              全部短进 0.1s
            </button>
          </div>
          <div className="video-sync-grid">
            {syncVideoIds.map((id, index) => (
              <figure key={id}>
                <video
                  ref={(node) => {
                    syncVideoRefs.current[id] = node;
                  }}
                  controls
                  preload="none"
                  muted
                  poster={`/api/v1/media-versions/${encodeURIComponent(id)}/thumbnail?size=small&frame=poster`}
                  src={`/api/v1/media-versions/${encodeURIComponent(id)}/content`}
                />
                <figcaption>视频候选 {index + 1}</figcaption>
              </figure>
            ))}
          </div>
        </section>
      )}
      {!selectionIsVisible && (
        <p className="empty-state" role="status">
          {filteredItems.length === 0
            ? "当前筛选没有可显示的审核详情。"
            : "正在切换到当前筛选项。"}
        </p>
      )}
      <div
        className={`review-layout${selectionIsVisible ? "" : " selection-filtered-out"}`}
      >
        <div className="review-list" role="list" aria-label="待审核媒体版本">
          {filteredItems.length === 0 ? (
            <p className="empty-state">当前筛选没有待审核媒体版本。</p>
          ) : (
            <>
              {visibleItems.map((item) => (
                <button
                  type="button"
                  id={`review-item-${item.media_version_id}`}
                  ref={(node) => {
                    reviewItemRefs.current[item.media_version_id] = node;
                  }}
                  className={`project-row review-row progressive-row${item.media_version_id === selectedVersionId ? " selected" : ""}`}
                  key={item.media_version_id}
                  aria-current={
                    item.media_version_id === selectedVersionId
                      ? "true"
                      : undefined
                  }
                  onClick={() => onSelect(item.media_version_id)}
                >
                  {supportsThumbnail(item.media_kind) ? (
                    <ThumbWithFallback
                      src={`/api/v1/media-versions/${encodeURIComponent(item.media_version_id)}/thumbnail?size=small&frame=poster`}
                      label={mediaKindLabel(item.media_kind)}
                    />
                  ) : (
                    <span className="media-kind-placeholder" aria-hidden="true">
                      {mediaKindLabel(item.media_kind)}
                    </span>
                  )}
                  <span>
                    <strong>
                      {subjectLabel(item)} · {stageLabel(item.stage)} ·{" "}
                      {mediaKindLabel(item.media_kind)}
                    </strong>
                    <small>
                      {String(item.episode_code ?? "当前分集")} ·{" "}
                      {decisionLabel(item.decision)}
                    </small>
                  </span>
                  <span
                    className={`status-pill state-${item.is_stale ? "stale" : Number(item.is_blocked ?? 0) === 1 ? "blocked" : "normal"}`}
                  >
                    {item.is_stale
                      ? "需重新审核"
                      : Number(item.is_blocked ?? 0) === 1
                        ? "有阻塞"
                        : "待处理"}
                  </span>
                </button>
              ))}
              {visibleItems.length < filteredItems.length && (
                <button
                  type="button"
                  className="secondary list-more"
                  onClick={() => setVisibleCount((count) => count + LIST_STEP)}
                >
                  继续显示审核项（{visibleItems.length}/{filteredItems.length}）
                </button>
              )}
            </>
          )}
        </div>
        <div className="review-detail">
          {!context ? (
            <p className="empty-state">选择一个媒体版本读取审核上下文。</p>
          ) : (
            <>
              {selectedMediaKind === "IMAGE" && (
                <div
                  className="image-compare-toolbar"
                  aria-label="图片缩略图比较"
                >
                  <label>
                    比较对象
                    <select
                      aria-label="图片比较对象"
                      value={compareVersionId ?? ""}
                      onChange={(event) =>
                        setCompareVersionId(event.target.value || null)
                      }
                    >
                      <option value="">不比较</option>
                      {items
                        .filter(
                          (item) =>
                            item.media_kind === "IMAGE" &&
                            item.media_version_id !== selectedVersionId,
                        )
                        .map((item) => (
                          <option
                            key={item.media_version_id}
                            value={item.media_version_id}
                          >
                            {stageLabel(item.stage)} · 版本 {item.media_version_id.slice(0, 12)}
                          </option>
                        ))}
                    </select>
                  </label>
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => setReferencePinned((value) => !value)}
                  >
                    {referencePinned ? "取消置顶参考图" : "置顶当前参考图"}
                  </button>
                  <span className="muted">
                    ←/→ 键切换候选；仅加载派生缩略图
                  </span>
                </div>
              )}
              {selectedMediaKind === "IMAGE" && compareVersionId ? (
                <div
                  className="image-compare-grid"
                  aria-label="图片 A/B 缩略图比较"
                >
                  <figure>
                    <img
                      src={`/api/v1/media-versions/${encodeURIComponent(referencePinned ? compareVersionId : (selectedVersionId ?? ""))}/thumbnail?size=small&frame=poster`}
                      alt="参考图缩略图"
                      loading="lazy"
                      decoding="async"
                    />
                    <figcaption>
                      参考 ·{" "}
                      {referencePinned ? compareVersionId.slice(0, 12) : "当前"}
                    </figcaption>
                  </figure>
                  <figure>
                    <img
                      src={`/api/v1/media-versions/${encodeURIComponent(referencePinned ? (selectedVersionId ?? "") : compareVersionId)}/thumbnail?size=small&frame=poster`}
                      alt="比较图缩略图"
                      loading="lazy"
                      decoding="async"
                    />
                    <figcaption>
                      比较 ·{" "}
                      {(referencePinned
                        ? selectedVersionId
                        : compareVersionId
                      )?.slice(0, 12)}
                    </figcaption>
                  </figure>
                </div>
              ) : selectedMediaKind === "VIDEO" ? (
                <div className="review-video-preview">
                  <video
                    ref={videoRef}
                    controls
                    preload="none"
                    loop={loopPreview}
                    muted={mutedPreview}
                    poster={`/api/v1/media-versions/${encodeURIComponent(selectedVersionId ?? "")}/thumbnail?size=small&frame=poster`}
                    src={`/api/v1/media-versions/${encodeURIComponent(selectedVersionId ?? "")}/content`}
                  />
                  <div
                    className="video-preview-controls"
                    aria-label="视频预览控制"
                  >
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => {
                        if (videoRef.current)
                          videoRef.current.currentTime = Math.max(
                            0,
                            videoRef.current.currentTime - 0.1,
                          );
                      }}
                    >
                      短退 0.1s
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => {
                        if (videoRef.current)
                          videoRef.current.currentTime += 0.1;
                      }}
                    >
                      短进 0.1s
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => setLoopPreview((value) => !value)}
                    >
                      {loopPreview ? "关闭循环" : "循环播放"}
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => setMutedPreview((value) => !value)}
                    >
                      {mutedPreview ? "打开声音" : "静音"}
                    </button>
                    <label>
                      倍速
                      <select
                        aria-label="播放倍速"
                        value={playbackRate}
                        onChange={(event) =>
                          setPlaybackRate(Number(event.target.value))
                        }
                      >
                        <option value="0.5">0.5×</option>
                        <option value="1">1×</option>
                        <option value="1.5">1.5×</option>
                        <option value="2">2×</option>
                      </select>
                    </label>
                    <span className="muted">
                      本机流式预览 · 原片不整体加载到内存
                    </span>
                  </div>
                </div>
              ) : (
                <div className="review-preview">
                  {selectedMediaKind === "AUDIO" ? (
                    <PreviewImage
                      src={`/api/v1/media-versions/${encodeURIComponent(selectedVersionId ?? "")}/waveform`}
                      label="音频波形"
                      width={640}
                      height={128}
                    />
                  ) : supportsThumbnail(selectedMediaKind) ? (
                    <PreviewImage
                      src={`/api/v1/media-versions/${encodeURIComponent(selectedVersionId ?? "")}/thumbnail?size=small&frame=poster`}
                      label="媒体"
                      width={320}
                      height={180}
                    />
                  ) : (
                    <strong>
                      {mediaKindLabel(selectedMediaKind)}暂无视觉缩略图
                    </strong>
                  )}
                  <span>
                    {selectedMediaKind === "AUDIO"
                      ? "固定请求 640×128 派生波形；不加载原音频"
                      : supportsThumbnail(selectedMediaKind)
                        ? "固定请求 320px small 缩略图；此处不加载原片"
                        : "不探测或下载原始媒体"}
                  </span>
                </div>
              )}
              <details className="review-technical-id">
                <summary>专家：媒体来源与技术标识</summary>
                <div className="review-meta image-metadata">
                  <span>媒体版本：{selectedVersionId}</span>
                  <span>内部阶段：{selectedStage}</span>
                  <span>内部类型：{selectedMediaKind}</span>
                  <span>源校验值：{String(context.media_version.sha256 ?? "未提供")}</span>
                </div>
              </details>
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">当前候选</p>
                  <h3>{stageLabel(String(context.media_version.stage))} · {mediaKindLabel(selectedMediaKind)}</h3>
                </div>
                {selectionType ? (
                  <button
                    type="button"
                    className="secondary"
                    onClick={() =>
                      selectedVersionId &&
                      onPromote(selectedVersionId, selectionType)
                    }
                    disabled={
                      !selectedVersionId ||
                      selecting ||
                      Boolean(currentSelection)
                    }
                  >
                    {selecting
                      ? "保存中…"
                      : currentSelection
                        ? `已采用为${SELECTION_LABELS[selectionType] ?? selectionType}`
                        : `采用为${SELECTION_LABELS[selectionType] ?? selectionType}`}
                  </button>
                ) : (
                  <button type="button" className="secondary" disabled={true}>
                    音轨绑定无需采用
                  </button>
                )}
              </div>
              {selectedMediaKind === "AUDIO" && (
                <section className="audio-qc-panel">
                  <div className="panel-heading">
                    <strong>音量、峰值与削波机器检查</strong>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() =>
                        selectedVersionId && onMachineCheck(selectedVersionId)
                      }
                      disabled={!selectedVersionId || machineChecking}
                    >
                      {machineChecking ? "检查中…" : "检查音频"}
                    </button>
                  </div>
                  <div className="review-meta">
                    {machineResults
                      .filter((item) =>
                        [
                          "integrated_loudness",
                          "true_peak",
                          "peak",
                          "clipping",
                        ].includes(String(item["item_id"])),
                      )
                      .map((item) => (
                        <span key={String(item["item_id"])}>
                          {MACHINE_CHECK_LABELS[String(item["item_id"])] ?? "检查项"}：{checkStatusLabel(item["result"])}
                        </span>
                      ))}
                  </div>
                  {!audioQcPassed && (
                    <p className="review-guidance">
                      音频机器检查尚未通过，暂不能正式批准。
                    </p>
                  )}
                  {machineCheckError && (
                    <p className="inline-error" role="alert">
                      音频检查失败：{machineCheckError}
                    </p>
                  )}
                </section>
              )}
              {selectedMediaKind === "VIDEO" && selectedStage === "FORMAL" && (
                <section className="audio-qc-panel">
                  <div className="panel-heading">
                    <strong>正式视频结构机器检查</strong>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() =>
                        selectedVersionId && onMachineCheck(selectedVersionId)
                      }
                      disabled={!selectedVersionId || machineChecking}
                    >
                      {machineChecking ? "检查中…" : "检查视频"}
                    </button>
                  </div>
                  <div className="review-meta">
                    {machineResults
                      .filter((item) =>
                        [
                          "file_integrity",
                          "decode",
                          "dimensions",
                          "fps",
                          "duration",
                          "codec",
                        ].includes(String(item["item_id"])),
                      )
                      .map((item) => (
                        <span key={String(item["item_id"])}>
                          {MACHINE_CHECK_LABELS[String(item["item_id"])] ?? "检查项"}：{checkStatusLabel(item["result"])}
                        </span>
                      ))}
                  </div>
                  {!formalVideoQcPassed && (
                    <p className="review-guidance">
                      视频机器检查尚未通过，暂不能正式批准。
                    </p>
                  )}
                  {machineCheckError && (
                    <p className="inline-error" role="alert">
                      视频检查失败：{machineCheckError}
                    </p>
                  )}
                </section>
              )}
              {currentApproval && (
                <p className="review-success" role="status">
                  当前版本已批准。如需改变结论，请先撤回当前审核；重复点击不会新增记录。
                </p>
              )}
              <div className="review-checklist">
                {context.template.items.map((item) => (
                  <fieldset
                    className="review-check"
                    key={item.id}
                    disabled={Boolean(currentApproval)}
                  >
                    <legend>
                      {item.label}
                      {item.required ? " *" : ""}
                    </legend>
                    <label>
                      <input
                        type="radio"
                        name={`check-${item.id}`}
                        checked={checks[item.id] === "PASS"}
                        onChange={() =>
                          setChecks((value) => ({
                            ...value,
                            [item.id]: "PASS",
                          }))
                        }
                      />
                      通过
                    </label>
                    <label>
                      <input
                        type="radio"
                        name={`check-${item.id}`}
                        checked={checks[item.id] === "FAIL"}
                        onChange={() =>
                          setChecks((value) => ({
                            ...value,
                            [item.id]: "FAIL",
                          }))
                        }
                      />
                      不通过
                    </label>
                  </fieldset>
                ))}
              </div>
              {missingRequired.length > 0 && (
                <p className="review-guidance" aria-live="polite">
                  还需完成 {missingRequired.length} 个必填检查：
                  {missingRequired.map((item) => item.label).join("、")}
                  。选择“批准”不会自动提交。
                </p>
              )}
              <div className="review-submit">
                <label htmlFor="review-decision">
                  审核决定
                  <select
                    id="review-decision"
                    value={decision}
                    onChange={(event) =>
                      setDecision(event.target.value as typeof decision)
                    }
                    disabled={Boolean(currentApproval)}
                  >
                    <option value="APPROVED">批准</option>
                    <option value="NEEDS_CHANGES">需要修改</option>
                    <option value="REJECTED">拒绝</option>
                  </select>
                </label>
                <button
                  type="button"
                  className="primary-action"
                  onClick={submitCurrentReview}
                  disabled={
                    Boolean(currentApproval) ||
                    !requiredComplete ||
                    submitting ||
                    (decision === "APPROVED" &&
                      (!audioQcPassed || !formalVideoQcPassed))
                  }
                >
                  {currentApproval
                    ? "已批准"
                    : submitting
                      ? "提交中…"
                      : "提交审核"}
                </button>
              </div>
              {submitError && (
                <p className="inline-error" role="alert">
                  提交失败：{submitError}
                </p>
              )}
              {submitSucceeded && context.reviews.length > 0 && (
                <p className="review-success" role="status">
                  审核已保存：{decisionLabel(String(context.reviews[0]?.decision ?? decision))}。是否采用该候选仍需通过上方独立按钮确认。
                </p>
              )}
              <div className="review-meta" aria-label="审核状态摘要">
                <span>
                  机器检查：
                  {checkStatusLabel(context.machine_checks[0]?.status)}
                </span>
                <span>审核记录：{context.reviews.length}</span>
                <span>
                  当前采用：{currentSelection && selectionType ? SELECTION_LABELS[selectionType] ?? selectionType : "未选择"}
                </span>
              </div>
            </>
          )}
        </div>
      </div>
      {submitSucceeded && context && (
        <div
          className="action-row review-next-actions"
          aria-label="审核完成后的下一步"
        >
          {nextReviewItem && (
            <button
              type="button"
              className="secondary"
              onClick={() => onSelect(nextReviewItem.media_version_id)}
            >
              审核下一项
            </button>
          )}
          {selectedItem?.project_id && selectedItem.episode_id && (
            <>
              <a
                className="secondary"
                href={`/projects/${encodeURIComponent(String(selectedItem.project_id))}/episodes/${encodeURIComponent(String(selectedItem.episode_id))}/direct${selectedItem.shot_id ? `/${encodeURIComponent(String(selectedItem.shot_id))}` : ""}`}
              >
                返回导演台
              </a>
              <a
                className="primary-action"
                href={`/projects/${encodeURIComponent(String(selectedItem.project_id))}/episodes/${encodeURIComponent(String(selectedItem.episode_id))}/timeline`}
              >
                进入时间线
              </a>
            </>
          )}
        </div>
      )}
      {context &&
        selectedMediaKind === "VIDEO" &&
        Number(context.media_version.duration_ms) > 0 && (
          <>
            <div className="video-frame-controls" aria-label="视频逐帧控制">
              <button
                className="secondary"
                type="button"
                onClick={() => stepVideoFrame(-1)}
              >
                逐帧退
              </button>
              <button
                className="secondary"
                type="button"
                onClick={() => stepVideoFrame(1)}
              >
                逐帧进
              </button>
              <span className="muted">
                按视频帧率逐帧移动 · 当前 {(videoRef.current?.currentTime ?? 0).toFixed(2)} 秒
              </span>
            </div>
            <VideoAnnotations
              projectId={selectedItem?.project_id}
              mediaVersionId={selectedVersionId ?? ""}
              durationMs={Number(context.media_version.duration_ms)}
              getCurrentTimeMs={() =>
                Math.round((videoRef.current?.currentTime ?? 0) * 1000)
              }
              onSeek={(timecodeMs) => {
                if (videoRef.current)
                  videoRef.current.currentTime = timecodeMs / 1000;
              }}
            />
          </>
        )}
    </section>
  );
}
