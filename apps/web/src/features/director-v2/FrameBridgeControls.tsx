import { useEffect, useMemo, useState } from "react";
import {
  ApiRequestError,
  createFrameAnchor,
  createShotTransitionConstraint,
  inheritFrameBridgeV2,
  setFrameBridgeCurrentFrameV2,
  setFrameBridgeLockV2,
  setFrameBridgeSourceFrameV2,
  type FrameBridge,
  type ShotStudio,
  type ShotStudioCandidate,
} from "../../generated/api";
import { frameCandidateIssue, readFrameCandidate, type FrameCandidateTransfer } from "./frameCandidateDrag";
import "./frame-bridge-controls.css";

type FrameBridgeAggregate = ShotStudio["current_shot"]["frame_bridge"];
type CurrentCandidate = (Partial<ShotStudioCandidate> & { media_version_id: string }) | null;
type FrameBridgeWrite = FrameBridge;

export type FrameBridgeControlsProps = {
  frameBridge: FrameBridgeAggregate;
  currentCandidate: CurrentCandidate;
  currentShotId: string;
  previousShotId?: string | null;
  nextShotId?: string | null;
  canEdit?: boolean;
  onChanged?: (result?: FrameBridgeWrite) => void | Promise<void>;
};

type Operation = "inherit" | "candidate" | "extract-end" | "lock" | "unlock" | "create-previous" | "create-next";
type DropTarget = "start" | "end";

const LOCKED_ENFORCEMENTS = new Set(["HARD", "LOCKED"]);

function shortId(value: string | null | undefined) {
  return value ? `${value.slice(0, 8)}…` : "未设置";
}

function commandKey(transitionId: string, operation: string, revision: number, source = "none") {
  return `shot-studio:frame-bridge:${transitionId}:${operation}:${revision}:${source}`;
}

export function FrameBridgeControls({ frameBridge, currentCandidate, currentShotId, previousShotId, nextShotId, canEdit = true, onChanged }: FrameBridgeControlsProps) {
  const boundary = frameBridge.previous;
  const [busy, setBusy] = useState<Operation | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ message: string; actualRevision?: number } | null>(null);
  const [dropActive, setDropActive] = useState<DropTarget | null>(null);
  const [pendingDrop, setPendingDrop] = useState<{ target: DropTarget; candidate: FrameCandidateTransfer } | null>(null);

  useEffect(() => {
    setMessage(null);
    setConflict(null);
  }, [boundary?.transition_id, boundary?.boundary_revision]);

  const locked = LOCKED_ENFORCEMENTS.has(boundary?.enforcement.toUpperCase() ?? "");
  const stale = Boolean(frameBridge.stale || boundary?.stale || boundary?.current_start?.stale || boundary?.previous_end?.stale);
  const staleReason = boundary?.stale_reason ?? boundary?.current_start?.stale_reason ?? boundary?.previous_end?.stale_reason;
  const hasCurrentStart = Boolean(boundary?.current_start);
  const nextBoundary = frameBridge.next;
  const candidateIssue = useMemo(() => {
    if (!currentCandidate) return "当前镜头尚未选择候选";
    if (currentCandidate.media_kind !== "IMAGE") return "当前候选不是图片帧";
    if (currentCandidate.integrity_status !== "VERIFIED") return "当前候选尚未通过完整性校验";
    if (currentCandidate.is_stale) return "当前候选已失效，不能设为首帧";
    return null;
  }, [currentCandidate]);
  const endFrameIssue = useMemo(() => {
    if (!nextBoundary) return "这是本集最后一个镜头，没有下游 Frame Bridge";
    if (!currentCandidate) return "请先在候选区选择当前视频";
    if (currentCandidate.media_kind !== "VIDEO") return "尾帧只能从视频候选提取";
    if (currentCandidate.integrity_status !== "VERIFIED") return "当前视频尚未通过完整性校验";
    if (currentCandidate.is_stale) return "当前视频已失效，不能作为尾帧来源";
    return null;
  }, [currentCandidate, nextBoundary]);

  const run = async (operation: Operation, droppedCandidate?: FrameCandidateTransfer) => {
    if (busy || !canEdit || (!["extract-end", "create-previous", "create-next"].includes(operation) && !boundary)) return;
    setBusy(operation);
    setMessage(null);
    setConflict(null);
    try {
      let result: FrameBridgeWrite;
      if (operation === "create-previous" || operation === "create-next") {
        const fromShotId = operation === "create-previous" ? previousShotId : currentShotId;
        const toShotId = operation === "create-previous" ? currentShotId : nextShotId;
        if (!fromShotId || !toShotId) return;
        const created = await createShotTransitionConstraint({
          from_shot_id: fromShotId,
          to_shot_id: toShotId,
          constraint_type: "START_FROM_PREVIOUS_LAST",
          enforcement: "ADVISORY",
          note: "Director Desk Frame Bridge",
        });
        result = { transition: created.constraint } as unknown as FrameBridgeWrite;
      } else if (operation === "inherit") {
        if (!boundary) return;
        result = (await inheritFrameBridgeV2(boundary.transition_id, {
          expected_boundary_revision: boundary.boundary_revision,
          idempotency_key: commandKey(boundary.transition_id, operation, boundary.boundary_revision, boundary.previous_end?.anchor_id),
        })).frame_bridge;
      } else if (operation === "candidate") {
        const candidate = droppedCandidate ?? currentCandidate;
        if (!boundary || !candidate || frameCandidateIssue(candidate)) return;
        if (candidate.media_kind === "VIDEO") {
          const extracted = await createFrameAnchor(candidate.media_version_id, { position_mode: "FIRST_FRAME", role_hint: "FIRST_FRAME" });
          result = (await setFrameBridgeCurrentFrameV2(boundary.transition_id, {
            expected_boundary_revision: boundary.boundary_revision,
            frame_anchor_id: extracted.frame_anchor.id,
            idempotency_key: commandKey(boundary.transition_id, operation, boundary.boundary_revision, extracted.frame_anchor.id),
          })).frame_bridge;
        } else {
          result = (await setFrameBridgeCurrentFrameV2(boundary.transition_id, {
            expected_boundary_revision: boundary.boundary_revision,
            media_version_id: candidate.media_version_id,
            idempotency_key: commandKey(boundary.transition_id, operation, boundary.boundary_revision, candidate.media_version_id),
          })).frame_bridge;
        }
      } else if (operation === "extract-end") {
        const candidate = droppedCandidate ?? currentCandidate;
        if (!candidate || !nextBoundary || frameCandidateIssue(candidate) || candidate.media_kind !== "VIDEO") return;
        const extracted = await createFrameAnchor(candidate.media_version_id, { position_mode: "LAST_FRAME", role_hint: "LAST_FRAME" });
        result = (await setFrameBridgeSourceFrameV2(nextBoundary.transition_id, {
          expected_boundary_revision: nextBoundary.boundary_revision,
          frame_anchor_id: extracted.frame_anchor.id,
          idempotency_key: commandKey(nextBoundary.transition_id, operation, nextBoundary.boundary_revision, extracted.frame_anchor.id),
        })).frame_bridge;
      } else {
        if (!boundary) return;
        result = (await setFrameBridgeLockV2(boundary.transition_id, {
          expected_boundary_revision: boundary.boundary_revision,
          locked: operation === "lock",
          idempotency_key: commandKey(boundary.transition_id, operation, boundary.boundary_revision),
        })).frame_bridge;
      }
      setMessage(operation === "create-previous" || operation === "create-next"
        ? `${operation === "create-previous" ? "上游" : "下游"} Frame Bridge 已创建；现在可以设置首尾帧来源。`
        : operation === "inherit"
        ? `${hasCurrentStart ? "已重新继承" : "已继承"}上一镜尾帧；旧锚点仍保留在历史中。`
        : operation === "candidate"
          ? "已将当前候选帧设为本镜首帧；旧锚点未被删除。"
          : operation === "extract-end"
            ? "已从当前视频真实尾帧注册 FrameAnchor，并连接到下一镜；旧锚点仍可追溯。"
          : operation === "lock" ? "Frame Bridge 已锁定。" : "Frame Bridge 已解除锁定。");
      await onChanged?.(result);
    } catch (error) {
      if (error instanceof ApiRequestError && error.status === 409) {
        const actual = error.details?.actual_boundary_revision;
        setConflict({ message: error.message, actualRevision: typeof actual === "number" ? actual : undefined });
      } else {
        setMessage(`操作失败：${error instanceof Error ? error.message : String(error)}`);
      }
    } finally {
      setBusy(null);
    }
  };

  const requestDrop = (target: DropTarget, dataTransfer: DataTransfer) => {
    setDropActive(null);
    const candidate = readFrameCandidate(dataTransfer);
    const issue = candidate ? frameCandidateIssue(candidate) : "拖拽数据不是可识别的媒体候选";
    const targetIssue = target === "start" ? (!boundary ? "本镜没有上游首帧边界" : null) : (!nextBoundary ? "本镜没有下游尾帧边界" : candidate?.media_kind !== "VIDEO" ? "尾帧目标只接受视频候选" : null);
    if (!canEdit || issue || targetIssue) {
      setMessage(`候选帧不可用：${!canEdit ? "当前权限只读" : issue ?? targetIssue}`);
      return;
    }
    setPendingDrop({ target, candidate: candidate as FrameCandidateTransfer });
  };

  const dropTarget = (target: DropTarget) => <div
    className={`frame-candidate-drop${dropActive === target ? " is-active" : ""}`}
    aria-label={target === "start" ? "拖放候选到本镜首帧" : "拖放视频候选到本镜尾帧"}
    onDragEnter={(event) => { event.preventDefault(); setDropActive(target); }}
    onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; }}
    onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropActive(null); }}
    onDrop={(event) => { event.preventDefault(); requestDrop(target, event.dataTransfer); }}
  ><strong>{target === "start" ? "拖到这里设为首帧" : "拖到这里提取尾帧"}</strong><span>{target === "start" ? "IMAGE 直接绑定；VIDEO 提取 FIRST_FRAME" : "VIDEO 提取 LAST_FRAME 并连接下一镜"}</span></div>;

  if (!boundary) {
    return <section className="frame-bridge-controls frame-bridge-empty" aria-label="Frame Bridge 连贯性操作">
      <strong>{previousShotId ? "尚未创建上游 Frame Bridge" : "这是本集第一个镜头"}</strong>
      <span>{previousShotId ? "先创建与上一镜的边界，再继承或指定本镜首帧。" : "没有上一镜边界，因此无需继承首帧。仍可为下一镜提取本镜真实尾帧。"}</span>
      {previousShotId && <button type="button" disabled={!canEdit || busy !== null} onClick={() => void run("create-previous")}>{busy === "create-previous" ? "正在创建…" : "创建上游 Frame Bridge"}</button>}
      {nextShotId && !nextBoundary && <button type="button" disabled={!canEdit || busy !== null} onClick={() => void run("create-next")}>{busy === "create-next" ? "正在创建…" : "创建下游 Frame Bridge"}</button>}
      {nextBoundary && dropTarget("end")}
      {nextBoundary && <button type="button" title={endFrameIssue ?? "提取真实视频尾帧并连接下一镜"} disabled={!canEdit || busy !== null || Boolean(endFrameIssue)} onClick={() => void run("extract-end")}>{busy === "extract-end" ? "正在解析真实尾帧…" : "从当前视频提取尾帧"}</button>}
      {endFrameIssue && nextBoundary && <span>尾帧来源不可用：{endFrameIssue}</span>}
      {message && <p className={message.startsWith("操作失败") ? "frame-bridge-message error" : "frame-bridge-message"} role="status">{message}</p>}
      {pendingDrop && nextBoundary && <div className="frame-bridge-drop-dialog" role="alertdialog" aria-modal="true" aria-labelledby="frame-drop-title-first">
        <strong id="frame-drop-title-first">确认提取本镜尾帧？</strong><span>确认后使用 boundary revision {nextBoundary.boundary_revision} 调用现有 Frame Bridge 语义命令；不会覆盖历史锚点。</span>
        <div><button type="button" autoFocus onClick={() => setPendingDrop(null)}>取消</button><button type="button" className="primary" disabled={busy !== null} onClick={() => { const pending = pendingDrop; setPendingDrop(null); void run("extract-end", pending.candidate); }}>确认执行</button></div>
      </div>}
    </section>;
  }

  return <section className={`frame-bridge-controls${stale ? " is-stale" : ""}`} aria-labelledby="frame-bridge-controls-title">
    <header className="frame-bridge-controls-head">
      <div><span>FRAME BRIDGE</span><h4 id="frame-bridge-controls-title">{boundary.from_shot_code} → {boundary.to_shot_code}</h4></div>
      <div className="frame-bridge-state"><strong>{stale ? "STALE" : boundary.compatibility}</strong><span>boundary revision {boundary.boundary_revision}</span></div>
    </header>

    <div className="frame-bridge-flow" aria-label="上一镜尾帧到本镜首帧">
      <article>{boundary.previous_end && <img src={`/api/v1/media-versions/${encodeURIComponent(boundary.previous_end.media_version_id)}/thumbnail?size=small&frame=poster`} alt="上一镜尾帧缩略图" loading="lazy" decoding="async" />}<small>上一镜尾帧</small><strong>{shortId(boundary.previous_end?.anchor_id)}</strong><span>{boundary.previous_end?.status ?? "MISSING"}</span></article>
      <span className="frame-bridge-arrow" aria-hidden="true">→</span>
      <article>{boundary.current_start && <img src={`/api/v1/media-versions/${encodeURIComponent(boundary.current_start.media_version_id)}/thumbnail?size=small&frame=poster`} alt="本镜首帧缩略图" loading="lazy" decoding="async" />}<small>本镜首帧</small><strong>{shortId(boundary.current_start?.anchor_id)}</strong><span>{boundary.current_start?.status ?? "MISSING"}</span></article>
    </div>

    {stale && <aside className="frame-bridge-stale" role="alert">
      <strong>来源已变化，当前桥接已失效</strong>
      <span>{staleReason || "上游候选、批准或媒体版本已更新。请重新继承后再锁定。"}</span>
    </aside>}

    {boundary.inheritance_reason && <aside className={`frame-bridge-history-note${boundary.inheritance_recommended ? " recommended" : ""}`}><strong>{boundary.inheritance_recommended ? "建议继承" : "需要导演判断"}</strong> · {boundary.inheritance_reason}</aside>}

    <p className="frame-bridge-history-note">继承和重新继承都会创建新的首帧锚点；旧锚点与生产历史不会被覆盖或删除。</p>

    <div className="frame-candidate-drop-grid" aria-label="候选帧拖放目标">{dropTarget("start")}{dropTarget("end")}</div>

    <div className="frame-bridge-source-menu" aria-label="首尾帧来源菜单">
      <div><strong>本镜首帧来源</strong><span>沿用上一镜，或使用当前已验证图片候选。</span></div>
      <div className="frame-bridge-actions">
      <button type="button" className={boundary.inheritance_recommended ? "primary" : undefined} title={boundary.inheritance_reason} disabled={!canEdit || busy !== null || !boundary.previous_end || boundary.previous_end.stale} onClick={() => void run("inherit")}>
        {busy === "inherit" ? "继承中…" : hasCurrentStart ? "重新继承上一镜尾帧" : "继承上一镜尾帧"}
      </button>
      <button type="button" title={candidateIssue ?? "将已验证图片候选设置为本镜首帧"} disabled={!canEdit || busy !== null || Boolean(candidateIssue)} onClick={() => void run("candidate")}>
        {busy === "candidate" ? "设置中…" : "用当前候选帧"}
      </button>
      </div>
      <div><strong>本镜尾帧来源</strong><span>从当前选中的真实视频解析最后一帧，并连接下一镜。</span></div>
      {nextShotId && !nextBoundary && <button type="button" disabled={!canEdit || busy !== null} onClick={() => void run("create-next")}>{busy === "create-next" ? "正在创建…" : "创建下游 Frame Bridge"}</button>}
      <button type="button" title={endFrameIssue ?? "提取真实视频尾帧并连接下一镜"} disabled={!canEdit || busy !== null || Boolean(endFrameIssue)} onClick={() => void run("extract-end")}>
        {busy === "extract-end" ? "正在解析真实尾帧…" : frameBridge.current_end ? "重新提取当前视频尾帧" : "从当前视频提取尾帧"}
      </button>
      <div><strong>本镜首帧边界策略</strong><span>锁定后 stale 来源必须先重新继承。</span></div>
      <button type="button" disabled={!canEdit || busy !== null || (stale && !locked)} onClick={() => void run(locked ? "unlock" : "lock")}>
        {busy === "lock" || busy === "unlock" ? "保存中…" : locked ? "解除锁定" : "锁定边界"}
      </button>
      </div>
    {candidateIssue && <p className="frame-bridge-candidate-note">候选帧不可用：{candidateIssue}</p>}
    {endFrameIssue && nextBoundary && <p className="frame-bridge-candidate-note">尾帧来源不可用：{endFrameIssue}</p>}

    {conflict && <div className="frame-bridge-conflict" role="alert">
      <div><strong>边界版本冲突</strong><span>{conflict.message}{conflict.actualRevision ? ` 服务端已到 revision ${conflict.actualRevision}。` : ""}</span></div>
      <button type="button" onClick={() => void onChanged?.()}>刷新最新 Frame Bridge</button>
    </div>}
    {message && <p className={message.startsWith("操作失败") ? "frame-bridge-message error" : "frame-bridge-message"} role="status">{message}</p>}
    {pendingDrop && <div className="frame-bridge-drop-dialog" role="alertdialog" aria-modal="true" aria-labelledby="frame-drop-title">
      <strong id="frame-drop-title">确认{pendingDrop.target === "start" ? "设置本镜首帧" : "提取本镜尾帧"}？</strong>
      <span>确认后使用 boundary revision {pendingDrop.target === "start" ? boundary.boundary_revision : nextBoundary?.boundary_revision} 调用现有 Frame Bridge 语义命令；不会覆盖历史锚点。</span>
      <div><button type="button" autoFocus onClick={() => setPendingDrop(null)}>取消</button><button type="button" className="primary" disabled={busy !== null} onClick={() => { const pending = pendingDrop; setPendingDrop(null); void run(pending.target === "start" ? "candidate" : "extract-end", pending.candidate); }}>确认执行</button></div>
    </div>}
  </section>;
}
