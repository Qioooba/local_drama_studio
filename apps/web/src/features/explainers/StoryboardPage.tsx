/**
 * 分镜与画面 (storyboard): narration-to-beat mapping, render type, candidate
 * comparison, per-beat regeneration and lock.
 *
 * The page reports planned and actual render types side by side, so a degraded
 * shot is never mistaken for a successful I2V.
 */

import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams, useSearchParams } from "react-router-dom";
import {
  getExplainerBeatImpact,
  selectExplainerBeatCandidate,
} from "../../generated/api";
import { queryKeys } from "../../query/queryKeys";
import { MediaPlaceholder, Panel, SettingRow, StateNotice, type PageState } from "./components";
import { RENDER_TYPE_LABELS, formatMs, plannedVsActual } from "./viewModels";
import { useExplainerBeatCandidates, useExplainerBeats, useExplainerEditions } from "./useExplainerQueries";
import "./explainers.css";

export function ExplainerStoryboardPage() {
  const { projectId = "" } = useParams();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const editions = useExplainerEditions(projectId);
  const firstEditionId = editions.data?.editions?.[0] ? String((editions.data.editions[0] as Record<string, unknown>).id) : null;
  const editionId = searchParams.get("edition") ?? firstEditionId;
  const beatsQuery = useExplainerBeats(projectId, editionId);
  const beats = beatsQuery.data?.beats ?? [];
  const selectedBeatId = searchParams.get("beat") ?? beats[0]?.id ?? null;
  const selectedBeat = beats.find((beat) => beat.id === selectedBeatId) ?? null;
  const candidatesQuery = useExplainerBeatCandidates(projectId, selectedBeatId);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const adopt = useMutation({
    mutationFn: async ({ candidateId, lock }: { candidateId: string; lock: boolean }) =>
      selectExplainerBeatCandidate(projectId, selectedBeatId ?? "", {
        expected_revision: Number(selectedBeat?.revision ?? 1),
        candidate_id: candidateId,
        lock,
        actor: lock ? "local-user" : null,
      }),
    onSuccess: async (result) => {
      setError(null);
      const record = result as Record<string, unknown>;
      setFeedback(
        record.degraded
          ? `已采用候选；实际类型与计划不同，已记录回退原因：${String(record.fallback_reason ?? "未提供")}。`
          : "已采用候选；原版本仍可回看，批次操作不会改动人工锁定镜头。",
      );
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => setError(mutationError instanceof Error ? mutationError.message : String(mutationError)),
  });

  const impact = useMutation({
    mutationFn: () => getExplainerBeatImpact(projectId, selectedBeatId ?? ""),
    onSuccess: (result) => {
      setError(null);
      const record = result as Record<string, unknown>;
      setFeedback(
        `更换该镜头会使 ${Number(record.affected_edition_count ?? 0)} 个 edition 的后续时码与渲染过期；无依赖的图像素材可复用。`,
      );
    },
    onError: (mutationError) => setError(mutationError instanceof Error ? mutationError.message : String(mutationError)),
  });

  const state = useMemo<PageState | null>(() => {
    if (editions.isPending || beatsQuery.isPending) return { kind: "loading", message: "正在载入分镜计划…" };
    if (beatsQuery.isError) return { kind: "failed", title: "无法载入分镜", body: beatsQuery.error instanceof Error ? beatsQuery.error.message : "未知错误" };
    if (beats.length === 0) {
      return {
        kind: "empty",
        title: "还没有分镜计划",
        body: "分镜必须在旁白实测时长之后生成：先完成讲稿与 TTS，再按真实音频分配画面区间。",
      };
    }
    const unselected = beats.filter((beat) => !beat.active_selection);
    if (unselected.length > 0) {
      return {
        kind: "partial",
        title: `${unselected.length} / ${beats.length} 个画面段还没有选定画面`,
        body: "只补未完成部分；已通过的镜头不会被重做。",
      };
    }
    return null;
  }, [beats, beatsQuery.error, beatsQuery.isError, beatsQuery.isPending, editions.isPending]);

  const plannedCounts = beatsQuery.data?.render_type_counts ?? {};
  const actualCounts = beatsQuery.data?.actual_render_type_counts ?? {};

  return <div className="explainer-page">
    <Panel
      title="分镜计划"
      subtitle="旁白决定画面覆盖范围；每段选择适合的生成方式。"
      actions={
        <>
          {editions.data?.editions && editions.data.editions.length > 1 ? (
            <label className="explainer-field">
              输出版本
              <select
                value={editionId ?? ""}
                onChange={(event) => setSearchParams((params) => {
                  params.set("edition", event.target.value);
                  return params;
                })}
              >
                {editions.data.editions.map((edition) => (
                  <option key={String((edition as Record<string, unknown>).id)} value={String((edition as Record<string, unknown>).id)}>
                    {String((edition as Record<string, unknown>).edition_key)} · {String((edition as Record<string, unknown>).aspect_ratio)}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <span className="badge">{beats.filter((beat) => beat.active_selection).length} / {beats.length} 已选画面</span>
        </>
      }
    >
      <StateNotice state={state} />
      <div className="explainer-coverage" style={{ marginTop: 10 }}>
        <div>
          <small>计划类型分布</small>
          <strong>
            {Object.entries(plannedCounts).map(([type, count]) => `${RENDER_TYPE_LABELS[type] ?? type} ${count}`).join(" · ") || "—"}
          </strong>
        </div>
        <div>
          <small>实际类型分布</small>
          <strong>
            {Object.entries(actualCounts).map(([type, count]) => `${RENDER_TYPE_LABELS[type] ?? type} ${count}`).join(" · ") || "—"}
          </strong>
        </div>
      </div>
      <p className="explainer-note">
        降级后的镜头不会被算作图生视频成功；计划类型与实际类型分别记录，并保留回退原因。must_be_motion 的镜头不可降为静图。
      </p>
    </Panel>

    <div className="explainer-grid wide-main">
      <Panel title="画面段" subtitle="与旁白是多对多关系。">
        <div className="explainer-list">
          {beats.map((beat) => {
            const diff = plannedVsActual(beat as unknown as Record<string, unknown>);
            return (
              <button
                type="button"
                className={`explainer-list-item${beat.id === selectedBeatId ? " selected" : ""}`}
                key={beat.id}
                onClick={() => setSearchParams((params) => {
                  params.set("beat", beat.id);
                  return params;
                })}
              >
                <span>{String(beat.ordinal + 1).padStart(2, "0")}</span>
                <span>
                  {String(beat.code)}
                  <small>
                    {RENDER_TYPE_LABELS[beat.render_type] ?? beat.render_type}
                    {diff.degraded ? ` → 实际 ${RENDER_TYPE_LABELS[diff.actual ?? ""] ?? diff.actual}` : ""}
                    {beat.must_be_motion ? " · 必须运动" : ""}
                    {beat.locked_by_human ? " · 人工锁定" : ""}
                  </small>
                </span>
              </button>
            );
          })}
        </div>
      </Panel>

      <Panel
        title={selectedBeat ? `画面段 ${selectedBeat.code}` : "画面段"}
        subtitle={selectedBeat ? `${RENDER_TYPE_LABELS[selectedBeat.render_type] ?? selectedBeat.render_type} · ${selectedBeat.must_be_motion ? "必须运动" : "可用静帧动效"}` : "选择一个画面段"}
        actions={selectedBeat ? <span className="badge blue">{selectedBeat.active_selection ? "已选定" : "待选择"}</span> : null}
      >
        {selectedBeat ? (
          <>
            <MediaPlaceholder
              label={`画面段 ${selectedBeat.code} 预览`}
              detail={selectedBeat.visual_intent ? String(selectedBeat.visual_intent).slice(0, 120) : "尚未生成媒体"}
            />
            <div className="explainer-candidate-grid">
              {(candidatesQuery.data?.candidates as Array<Record<string, unknown>> | undefined ?? []).map((candidate) => (
                <button
                  type="button"
                  className={`explainer-candidate${String(candidate.id) === String((selectedBeat.active_selection as Record<string, unknown> | null)?.candidate_id ?? "") ? " selected" : ""}`}
                  key={String(candidate.id)}
                  onClick={() => adopt.mutate({ candidateId: String(candidate.id), lock: false })}
                  disabled={!candidate.media_version_id || adopt.isPending}
                >
                  <strong>候选 {String(candidate.variant_no)}</strong>
                  <small>{String(candidate.candidate_kind === "TECHNICAL_RETRY" ? "技术重试" : "创作候选")}</small>
                  <small>{candidate.media_sha256 ? `哈希 ${String(candidate.media_sha256).slice(0, 8)}…` : "尚未探测媒体"}</small>
                  {candidate.render_type_actual && candidate.render_type_actual !== selectedBeat.render_type
                    ? <small>实际：{RENDER_TYPE_LABELS[String(candidate.render_type_actual)] ?? String(candidate.render_type_actual)}</small>
                    : null}
                </button>
              ))}
              {candidatesQuery.isPending ? <p className="muted">正在载入候选…</p> : null}
              {candidatesQuery.data && ((candidatesQuery.data.candidates as unknown[]) ?? []).length === 0
                ? <p className="muted">还没有候选。普通镜头初次 1 个候选，必要时最多 2 次创作修复；关键人物设定初次 2 个候选。</p>
                : null}
            </div>
          </>
        ) : <p className="muted">选择一个画面段查看候选与要求。</p>}
      </Panel>

      <div className="explainer-stack">
        <Panel title="画面要求">
          {selectedBeat ? (
            <>
              <SettingRow label="对应旁白" value={`${(selectedBeat.narration_links ?? []).map((link) => String((link as Record<string, unknown>).canonical_segment_id ?? "")).join("、") || "—"}`} />
              <SettingRow label="生成方式" value={RENDER_TYPE_LABELS[selectedBeat.render_type] ?? selectedBeat.render_type} />
              <SettingRow label="视觉属性" value={String(selectedBeat.visual_factuality)} />
              <SettingRow label="计划时长" value={formatMs(selectedBeat.preferred_duration_ms)} />
              <SettingRow label="锁定状态" value={selectedBeat.locked_by_human ? "人工锁定" : "未锁定"} />
              {selectedBeat.fallback_reason ? <SettingRow label="回退原因" value={String(selectedBeat.fallback_reason)} /> : null}
              <p className="explainer-note">{String(selectedBeat.visual_intent || "尚未填写视觉意图。")}</p>
            </>
          ) : <p className="muted">选择画面段后显示。</p>}
          <div className="explainer-actions" style={{ marginTop: 12 }}>
            <button type="button" onClick={() => impact.mutate()} disabled={!selectedBeatId || impact.isPending}>查看更换影响</button>
            <button
              type="button"
              disabled={!selectedBeatId || adopt.isPending}
              onClick={() => {
                const active = selectedBeat?.active_selection as Record<string, unknown> | null;
                if (active?.candidate_id) adopt.mutate({ candidateId: String(active.candidate_id), lock: true });
                else setError("该画面段还没有可锁定的人工候选。");
              }}
            >
              以人工身份锁定当前候选
            </button>
          </div>
        </Panel>

        <Panel title="低抽卡机制">
          <p className="muted">普通镜头初次 1 个候选，必要时最多 2 次创作修复；关键人物设定初次 2 个候选，并受整片 GPU 时间上限约束。</p>
          <p className="muted" style={{ marginTop: 8 }}>技术重试与创作重抽分别计账；预算耗尽会停下并报告，而不是继续无限抽卡。</p>
          <p className="explainer-note">
            信息图里的地图、数字、日期、关系线和引用文字都由确定性图形/文字层生成；真实地理使用有来源的底图或标清“示意图”。
          </p>
        </Panel>
      </div>
    </div>
  </div>;
}
