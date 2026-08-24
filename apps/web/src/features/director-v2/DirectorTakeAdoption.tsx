import { useEffect, useRef, useState, type ReactNode } from "react";
import type { DirectorDeskCandidate } from "./types";
import { FRAME_CANDIDATE_MIME, frameCandidateIssue } from "./frameCandidateDrag";

export type DirectorSelectionType = "KEYFRAME" | "PROXY_WINNER" | "FORMAL_SELECTION";

export function selectionTypeForCandidate(candidate: DirectorDeskCandidate): DirectorSelectionType | null {
  if (candidate.stage === "FORMAL") return "FORMAL_SELECTION";
  if (candidate.stage === "PROXY") return "PROXY_WINNER";
  if (candidate.stage === "KEYFRAME" && candidate.media_kind === "IMAGE") return "KEYFRAME";
  return null;
}

function disabledReason(candidate: DirectorDeskCandidate, currentCandidateId: string | null, pending: boolean) {
  if (candidate.is_stale) return `候选已失效${candidate.stale_reason ? `：${candidate.stale_reason}` : ""}，不能采用`;
  if (!selectionTypeForCandidate(candidate)) return "缺少可用的 selection_type，无法建立可审计的采用记录";
  if (candidate.media_version_id === currentCandidateId) return "这个候选是当前预览采用项";
  if (candidate.selected) return "这个候选已是所属阶段的有效选择";
  if (pending) return "正在保存采用结果";
  return null;
}

function candidateDecisionLabel(candidate: DirectorDeskCandidate, currentCandidateId: string | null) {
  if (candidate.approved) return "已批准";
  if (candidate.media_version_id === currentCandidateId) return "当前采用";
  if (candidate.selected && candidate.stage === "FORMAL") return "正式选择";
  if (candidate.selected && candidate.stage === "PROXY") return "代理选择";
  if (candidate.selected && candidate.stage === "KEYFRAME") return "关键帧选择";
  if (candidate.is_stale) return "已失效";
  return "待比较";
}

type Props = {
  candidates: DirectorDeskCandidate[];
  activeCandidateId: string | null;
  currentCandidateId: string | null;
  pending?: boolean;
  onActivate: (candidateId: string) => void;
  onAdopt: (candidate: DirectorDeskCandidate, selectionType: DirectorSelectionType) => Promise<void>;
  onFeedback?: (message: string) => void;
  onDialogOpenChange?: (open: boolean) => void;
  renderSecondaryAction?: (candidate: DirectorDeskCandidate) => ReactNode;
  thumbnailUrl?: (mediaVersionId: string) => string;
  undoWindowMs?: number;
};

type UndoState = {
  previous: DirectorDeskCandidate;
  selectionType: DirectorSelectionType;
};

export function DirectorTakeAdoption({
  candidates,
  activeCandidateId,
  currentCandidateId,
  pending = false,
  onActivate,
  onAdopt,
  onFeedback,
  onDialogOpenChange,
  renderSecondaryAction,
  thumbnailUrl = (id) => `/api/v1/media-versions/${encodeURIComponent(id)}/thumbnail?size=medium&frame=poster`,
  undoWindowMs = 8_000,
}: Props) {
  const [pendingCandidate, setPendingCandidate] = useState<DirectorDeskCandidate | null>(null);
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [dropActive, setDropActive] = useState(false);
  const [undo, setUndo] = useState<UndoState | null>(null);
  const [localPending, setLocalPending] = useState(false);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const busy = pending || localPending;

  useEffect(() => {
    if (!undo) return;
    const timer = window.setTimeout(() => setUndo(null), undoWindowMs);
    return () => window.clearTimeout(timer);
  }, [undo, undoWindowMs]);

  useEffect(() => {
    if (pendingCandidate) cancelRef.current?.focus();
  }, [pendingCandidate]);

  const closeConfirmation = () => {
    setPendingCandidate(null);
    onDialogOpenChange?.(false);
  };

  useEffect(() => {
    if (!pendingCandidate) return;
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      closeConfirmation();
    };
    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  });

  const requestAdoption = (candidate: DirectorDeskCandidate) => {
    const reason = disabledReason(candidate, currentCandidateId, busy);
    if (reason) {
      onFeedback?.(reason);
      return;
    }
    onActivate(candidate.media_version_id);
    setPendingCandidate(candidate);
    onDialogOpenChange?.(true);
  };

  const confirm = async () => {
    if (!pendingCandidate || busy) return;
    const selectionType = selectionTypeForCandidate(pendingCandidate);
    if (!selectionType) return;
    const previous = candidates.find((candidate) =>
      candidate.media_version_id !== pendingCandidate.media_version_id
      && candidate.selected
      && selectionTypeForCandidate(candidate) === selectionType,
    ) ?? null;
    setLocalPending(true);
    try {
      await onAdopt(pendingCandidate, selectionType);
      setUndo(previous ? { previous, selectionType } : null);
      onFeedback?.(previous ? "候选已采用；可在短时间内撤销本次采用。" : "候选已采用；批准状态保持独立。");
      closeConfirmation();
    } catch (error) {
      onFeedback?.(`采用失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setLocalPending(false);
    }
  };

  const undoAdoption = async () => {
    if (!undo || busy) return;
    setLocalPending(true);
    try {
      await onAdopt(undo.previous, undo.selectionType);
      onActivate(undo.previous.media_version_id);
      setUndo(null);
      onFeedback?.("已通过新的采用记录恢复此前候选；历史记录未删除。");
    } catch (error) {
      onFeedback?.(`撤销失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setLocalPending(false);
    }
  };

  const droppedCandidate = candidates.find((candidate) => candidate.media_version_id === draggedId) ?? null;

  return <>
    <div
      className={`director-adoption-slot${dropActive ? " drop-active" : ""}`}
      aria-label="候选采用槽"
      onDragEnter={(event) => { event.preventDefault(); setDropActive(true); }}
      onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; }}
      onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropActive(false); }}
      onDrop={(event) => {
        event.preventDefault();
        setDropActive(false);
        const candidateId = event.dataTransfer.getData("application/x-director-candidate") || draggedId;
        const candidate = candidates.find((item) => item.media_version_id === candidateId) ?? droppedCandidate;
        setDraggedId(null);
        if (candidate) requestAdoption(candidate);
      }}
    >
      <strong>拖到这里采用</strong>
      <span>Drop 只打开确认，不会直接写入</span>
      {undo && <button type="button" disabled={busy} onClick={() => void undoAdoption()}>撤销本次采用</button>}
    </div>
    {candidates.slice(0, 8).map((candidate, index) => {
      const reason = disabledReason(candidate, currentCandidateId, busy);
      const current = candidate.media_version_id === currentCandidateId;
      const decisionLabel = candidateDecisionLabel(candidate, currentCandidateId);
      const frameIssue = frameCandidateIssue(candidate);
      return <figure
        key={candidate.media_version_id}
        className={`director-take${candidate.selected || candidate.approved ? " has-decision" : ""}${activeCandidateId === candidate.media_version_id ? " active" : ""}`}
        draggable={!reason || !frameIssue}
        onDragStart={(event) => {
          if (reason && frameIssue) { event.preventDefault(); return; }
          setDraggedId(candidate.media_version_id);
          event.dataTransfer.effectAllowed = "copy";
          event.dataTransfer.setData("application/x-director-candidate", candidate.media_version_id);
          if (!frameIssue) event.dataTransfer.setData(FRAME_CANDIDATE_MIME, JSON.stringify({ media_version_id: candidate.media_version_id, media_kind: candidate.media_kind, integrity_status: candidate.integrity_status, is_stale: candidate.is_stale, stale_reason: candidate.stale_reason }));
        }}
        onDragEnd={() => { setDraggedId(null); setDropActive(false); }}
      >
        <button type="button" className="director-take-select" aria-pressed={activeCandidateId === candidate.media_version_id} aria-label={`查看 Take ${candidate.take_no ?? index + 1} · ${candidate.stage ?? "未分阶段"}，${decisionLabel}`} onClick={() => onActivate(candidate.media_version_id)}>
          <img src={thumbnailUrl(candidate.media_version_id)} alt="" loading="lazy" decoding="async" />
          <span className="director-take-caption"><span>Take {candidate.take_no ?? index + 1}<small>{candidate.stage ?? "未分阶段"}</small></span><strong>{decisionLabel}</strong></span>
        </button>
        <div className="director-take-actions">
          <button type="button" disabled={Boolean(reason)} aria-describedby={`adopt-reason-${candidate.media_version_id}`} title={reason ?? "打开采用确认"} onClick={() => requestAdoption(candidate)}>{current ? "当前采用" : candidate.selected ? `${decisionLabel}已选` : "采用"}</button>
          {renderSecondaryAction?.(candidate)}
        </div>
        <span id={`adopt-reason-${candidate.media_version_id}`} className="director-sr-only">{reason ?? "可采用；按钮和拖拽操作等价"}</span>
      </figure>;
    })}
    {pendingCandidate && <div className="director-dialog-scrim" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && closeConfirmation()}>
      <section className="director-dialog" role="alertdialog" aria-modal="true" aria-labelledby="adoption-title" aria-describedby="adoption-description">
        <div><span className="director-kicker">显式采用</span><h2 id="adoption-title">确认采用 Take {pendingCandidate.take_no ?? pendingCandidate.variant_no}？</h2><p id="adoption-description">确认后会新增 {selectionTypeForCandidate(pendingCandidate)} 选择记录。采用不等于批准，也不会删除此前选择历史。</p></div>
        <div className="director-dialog-actions"><button ref={cancelRef} type="button" className="director-button ghost" onClick={closeConfirmation}>取消</button><button type="button" className="director-button primary" disabled={busy} onClick={() => void confirm()}>{busy ? "正在采用…" : "确认采用"}</button></div>
      </section>
    </div>}
  </>;
}
