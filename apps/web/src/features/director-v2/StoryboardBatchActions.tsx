import { useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import {
  commitStoryboardBatch,
  getStoryboardWorkspace,
  planStoryboardBatch,
  type StoryboardBatchPayload,
} from "../../generated/api";
import {
  planStoryboardGenerationBatch,
  submitStoryboardGenerationBatch,
  type StoryboardGenerationBatchPlan,
  type StoryboardGenerationBatchTarget,
} from "./storyboardGenerationBatchApi";
import {
  planShotKeyframeBatch,
  submitShotKeyframeBatch,
  type ShotFrameStrategy,
  type ShotKeyframeBatchPlan,
} from "./shotKeyframeBatchApi";
import "./storyboard-batch-actions.css";

type PreparedModifier = { payload: StoryboardBatchPayload; planHash: string; count: number };
type PreparedGeneration = { targets: StoryboardGenerationBatchTarget[]; plan: StoryboardGenerationBatchPlan; submitKey: string };
type PreparedKeyframes = { targets: StoryboardGenerationBatchTarget[]; plan: ShotKeyframeBatchPlan; submitKey: string };

type StoryboardBatchActionsProps = {
  episodeId: string;
  selectedShotIds: string[];
  disabled?: boolean;
  onChanged?: () => void | Promise<void>;
};

function commandKey(prefix: string): string {
  return globalThis.crypto?.randomUUID?.() ?? `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function parseModifiers(value: string): string[] {
  const seen = new Set<string>();
  return value.replaceAll("，", ",").split(",").map((item) => item.trim()).filter((item) => {
    const folded = item.toLocaleLowerCase();
    if (!item || seen.has(folded)) return false;
    seen.add(folded);
    return true;
  });
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function StoryboardBatchActions({ episodeId, selectedShotIds, disabled = false, onChanged }: StoryboardBatchActionsProps) {
  const [modifierText, setModifierText] = useState("");
  const [preparedModifier, setPreparedModifier] = useState<PreparedModifier | null>(null);
  const [preparedGeneration, setPreparedGeneration] = useState<PreparedGeneration | null>(null);
  const [preparedKeyframes, setPreparedKeyframes] = useState<PreparedKeyframes | null>(null);
  const [frameStrategy, setFrameStrategy] = useState<ShotFrameStrategy>("FIRST_ONLY");
  const [candidateCount, setCandidateCount] = useState(2);
  const [feedback, setFeedback] = useState("");
  const selectionKey = useMemo(() => selectedShotIds.join(","), [selectedShotIds]);

  useEffect(() => {
    setPreparedModifier(null);
    setPreparedGeneration(null);
    setPreparedKeyframes(null);
    setFeedback("");
  }, [episodeId, selectionKey]);

  const modifierPlan = useMutation({
    mutationFn: async () => {
      const modifiers = parseModifiers(modifierText);
      if (!modifiers.length) throw new Error("请输入至少一个提示词修饰符");
      const { storyboard } = await getStoryboardWorkspace(episodeId);
      const selected = storyboard.items.filter((item) => selectedShotIds.includes(item.id));
      if (selected.length !== selectedShotIds.length) throw new Error("镜头选择已变化，请刷新后重试");
      const payload: StoryboardBatchPayload = {
        ordered_shot_ids: storyboard.items.map((item) => item.id),
        edits: selected.map((item) => ({
          shot_id: item.id,
          expected_revision: item.revision,
          fields: { prompt_modifiers: modifiers },
        })),
        copies: [],
      };
      const { plan } = await planStoryboardBatch(episodeId, payload);
      if (!plan.valid) throw new Error(plan.issues.map((item) => item.message).join("；") || "批量修饰词计划未通过校验");
      return { payload, planHash: plan.plan_hash, count: selected.length };
    },
    onSuccess: (prepared) => {
      setPreparedModifier(prepared);
      setPreparedGeneration(null);
      setFeedback("修饰词计划已检查；确认后会建立新的镜头 revision，不覆盖历史。");
    },
  });

  const modifierCommit = useMutation({
    mutationFn: async () => {
      if (!preparedModifier) throw new Error("请先检查修饰词计划");
      return commitStoryboardBatch(episodeId, preparedModifier.payload, preparedModifier.planHash);
    },
    onSuccess: async () => {
      setPreparedModifier(null);
      setPreparedGeneration(null);
      setFeedback("修饰词已写入所选镜头的新 revision；现在可以检查批量生成。");
      await onChanged?.();
    },
  });

  const generationPlan = useMutation({
    mutationFn: async () => {
      const { storyboard } = await getStoryboardWorkspace(episodeId);
      const selected = storyboard.items.filter((item) => selectedShotIds.includes(item.id));
      if (selected.length !== selectedShotIds.length) throw new Error("镜头选择已变化，请刷新后重试");
      const targets = selected.map((item) => ({ shot_id: item.id, expected_revision: item.revision }));
      const { plan } = await planStoryboardGenerationBatch(episodeId, targets);
      return { targets, plan, submitKey: commandKey("storyboard-generation-batch") };
    },
    onSuccess: (prepared) => {
      setPreparedGeneration(prepared);
      setFeedback(prepared.plan.valid ? "批量生成条件已检查；确认后每镜新增一个视频 Take。" : "部分镜头尚未满足生成条件。");
    },
  });

  const keyframePlan = useMutation({
    mutationFn: async () => {
      const { storyboard } = await getStoryboardWorkspace(episodeId);
      const selected = storyboard.items.filter((item) => selectedShotIds.includes(item.id));
      if (selected.length !== selectedShotIds.length) throw new Error("镜头选择已变化，请刷新后重试");
      const targets = selected.map((item) => ({ shot_id: item.id, expected_revision: item.revision }));
      const { plan } = await planShotKeyframeBatch(episodeId, targets, frameStrategy, candidateCount);
      return { targets, plan, submitKey: commandKey("shot-keyframe-batch") };
    },
    onSuccess: (prepared) => {
      setPreparedKeyframes(prepared);
      setPreparedGeneration(null);
      setFeedback(prepared.plan.valid
        ? `关键帧方案已检查；确认后创建 ${prepared.plan.summary.jobs} 个图片候选任务。`
        : "关键帧方案存在阻塞；请按镜头和资产提示修复后重新检查。");
    },
  });

  const keyframeSubmit = useMutation({
    mutationFn: async () => {
      if (!preparedKeyframes?.plan.valid) throw new Error("请先通过关键帧方案检查");
      return submitShotKeyframeBatch(
        episodeId, preparedKeyframes.targets, preparedKeyframes.plan.frame_strategy,
        preparedKeyframes.plan.candidate_count, preparedKeyframes.plan.plan_hash, preparedKeyframes.submitKey,
      );
    },
    onSuccess: async ({ batch }) => {
      setPreparedKeyframes(null);
      setFeedback(`已建立 ${batch.summary.total} 个可恢复关键帧任务；完成后会自动出现在镜头候选区。`);
      await onChanged?.();
    },
  });

  const generationSubmit = useMutation({
    mutationFn: async () => {
      if (!preparedGeneration?.plan.valid) throw new Error("请先通过批量生成检查");
      return submitStoryboardGenerationBatch(
        episodeId,
        preparedGeneration.targets,
        preparedGeneration.plan.plan_hash,
        preparedGeneration.submitKey,
      );
    },
    onSuccess: ({ batch }) => {
      setPreparedGeneration(null);
      setFeedback(`已建立 ${batch.selected_shot_ids.length} 镜的可恢复生成任务；可在任务中心查看进度。`);
    },
  });

  const busy = modifierPlan.isPending || modifierCommit.isPending || keyframePlan.isPending || keyframeSubmit.isPending || generationPlan.isPending || generationSubmit.isPending;
  const error = modifierPlan.error || modifierCommit.error || keyframePlan.error || keyframeSubmit.error || generationPlan.error || generationSubmit.error;
  return <details className="director-storyboard-batch-actions">
    <summary>批量修饰与生成</summary>
    <div className="director-storyboard-batch-body">
      <label htmlFor="director-batch-modifiers">统一提示词修饰符</label>
      <input
        id="director-batch-modifiers"
        value={modifierText}
        maxLength={400}
        placeholder="例如：雨夜，冷色调"
        disabled={disabled || busy}
        onChange={(event) => { setModifierText(event.target.value); setPreparedModifier(null); }}
      />
      <small>用逗号分隔；只写入独立修饰层，不改写镜头动作、对白或创作意图。</small>
      <div className="director-storyboard-batch-buttons">
        <button type="button" disabled={disabled || busy || !selectedShotIds.length} onClick={() => modifierPlan.mutate()}>检查修饰词</button>
        {preparedModifier && <button type="button" className="confirm" disabled={disabled || busy} onClick={() => modifierCommit.mutate()}>确认应用到 {preparedModifier.count} 镜</button>}
      </div>
      <div className="director-storyboard-batch-buttons">
        <label>帧策略<select value={frameStrategy} disabled={disabled || busy} onChange={(event) => { setFrameStrategy(event.target.value as ShotFrameStrategy); setPreparedKeyframes(null); }}><option value="FIRST_ONLY">只生成首帧</option><option value="FIRST_AND_LAST">同时生成首帧和尾帧</option></select></label>
        <label>每镜候选<select value={candidateCount} disabled={disabled || busy} onChange={(event) => { setCandidateCount(Number(event.target.value)); setPreparedKeyframes(null); }}>{[1, 2, 3, 4].map((count) => <option key={count} value={count}>{count} 张</option>)}</select></label>
      </div>
      <div className="director-storyboard-batch-buttons">
        <button type="button" disabled={disabled || busy || !selectedShotIds.length} onClick={() => keyframePlan.mutate()}>检查关键帧方案</button>
        {preparedKeyframes?.plan.valid && <button type="button" className="confirm" disabled={disabled || busy} onClick={() => keyframeSubmit.mutate()}>确认生成 {preparedKeyframes.plan.summary.jobs} 张</button>}
      </div>
      {preparedKeyframes && <div className={`director-storyboard-batch-plan ${preparedKeyframes.plan.valid ? "ready" : "blocked"}`} aria-label="关键帧生成检查结果">
        <strong>{preparedKeyframes.plan.summary.shots} 镜 · {preparedKeyframes.plan.summary.jobs} 个任务 · {preparedKeyframes.plan.summary.blocked} 项阻塞</strong>
        {preparedKeyframes.plan.issues.length > 0 && <ul>{preparedKeyframes.plan.issues.slice(0, 12).map((item, index) => <li key={`${item.code}-${item.shot_id ?? index}-${item.frame_role ?? "frame"}`}>{item.shot_id ? `${item.shot_id.slice(0, 8)}：` : ""}{item.message}</li>)}</ul>}
      </div>}
      <div className="director-storyboard-batch-buttons">
        <button type="button" disabled={disabled || busy || !selectedShotIds.length} onClick={() => generationPlan.mutate()}>检查批量生成</button>
        {preparedGeneration?.plan.valid && <button type="button" className="confirm" disabled={disabled || busy} onClick={() => generationSubmit.mutate()}>确认新增 {preparedGeneration.plan.summary.ready} 个 Take</button>}
      </div>
      {preparedGeneration && <div className={`director-storyboard-batch-plan ${preparedGeneration.plan.valid ? "ready" : "blocked"}`} aria-label="批量生成检查结果">
        <strong>{preparedGeneration.plan.summary.ready} 镜可生成 · {preparedGeneration.plan.summary.blocked} 镜阻塞</strong>
        {preparedGeneration.plan.issues.length > 0 && <ul>{preparedGeneration.plan.issues.map((item, index) => <li key={`${item.code}-${item.shot_id ?? index}`}>{item.shot_id ? `${item.shot_id.slice(0, 8)}：` : ""}{item.message}</li>)}</ul>}
      </div>}
      {error && <p className="error" role="alert">{errorMessage(error)}</p>}
      {feedback && <p className="feedback" role="status" aria-live="polite">{feedback}</p>}
    </div>
  </details>;
}
