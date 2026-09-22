/**
 * 审片与导出 (review): coverage, issues, frame stepping, decisions and the
 * export package.
 *
 * Coverage is reported per layer, and the three decision kinds stay visibly
 * separate: machine acceptance, human approval and publication authorization.
 */

import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams, useSearchParams } from "react-router-dom";
import {
  controlExplainerRun,
  planExplainerRepairs,
  preflightExplainerPlan,
  recordExplainerDecision,
  startExplainerExport,
  startExplainerRender,
} from "../../generated/api";
import { stableIdempotencyKey } from "../../services/commandId";
import { queryKeys } from "../../query/queryKeys";
import { AuthorityBadge, InlineError, InlineOk, MediaPlaceholder, Panel, SettingRow, StateNotice, type PageState } from "./components";
import { SEVERITY_LABELS, coverageRows, formatMs, issueSeverityTone, localeLabel, subtitleModeLabel } from "./viewModels";
import { useExplainerEditions, useExplainerOverview, useExplainerQc, useExplainerRun } from "./useExplainerQueries";
import "./explainers.css";

export function ExplainerReviewPage() {
  const { projectId = "" } = useParams();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const overview = useExplainerOverview(projectId);
  const editions = useExplainerEditions(projectId);
  const editionList = (editions.data?.editions ?? []) as Array<Record<string, unknown>>;
  const editionId = searchParams.get("edition") ?? (editionList[0] ? String(editionList[0].id) : null);
  const activeEdition = editionList.find((edition) => String(edition.id) === editionId) ?? editionList[0] ?? null;
  const qc = useExplainerQc(editionId, null);
  const latestRun = overview.data?.latest_run ?? null;
  const run = useExplainerRun(latestRun?.id ?? null);
  const [frame, setFrame] = useState(0);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const issues = ((qc.data?.open_issues ?? []) as Array<Record<string, unknown>>).concat(
    ((qc.data?.issues ?? []) as Array<Record<string, unknown>>).filter(
      (issue) => !(qc.data?.open_issues ?? []).some((open) => String((open as Record<string, unknown>).id) === String(issue.id)),
    ),
  );
  const selectedIssueId = searchParams.get("issue");
  const selectedIssue = issues.find((issue) => String(issue.id) === selectedIssueId) ?? issues[0] ?? null;

  const coverage = coverageRows(qc.data?.coverage as Record<string, unknown> | undefined);

  const render = useMutation({
    mutationFn: async () => {
      if (!editionId) throw new Error("选择一个输出版本");
      const plan = await startExplainerRender(
        editionId,
        { freeze: true, confirm: false },
        stableIdempotencyKey("explainer-render-plan", { editionId }),
      );
      return startExplainerRender(
        editionId,
        { freeze: true, confirm: true, composition_revision_id: (plan as Record<string, unknown>).composition_revision_id ?? null },
        stableIdempotencyKey("explainer-render", { editionId, composition: (plan as Record<string, unknown>).composition_revision_id }),
      );
    },
    onSuccess: async (result) => {
      setError(null);
      setFeedback(`已提交分块渲染（manifest ${String((result as Record<string, unknown>).manifest_hash ?? "").slice(0, 12)}…）。`);
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => setError(mutationError instanceof Error ? mutationError.message : String(mutationError)),
  });

  const repair = useMutation({
    mutationFn: async () => {
      if (!selectedIssue) throw new Error("选择一个要修复的问题");
      const revision = Number(overview.data?.video?.revision ?? 1);
      const planned = await planExplainerRepairs(projectId, {
        issue_ids: [String(selectedIssue.id)],
        expected_revision: revision,
        confirm: false,
      });
      return planExplainerRepairs(projectId, {
        issue_ids: [String(selectedIssue.id)],
        expected_revision: revision,
        confirm: true,
      }).then(() => planned);
    },
    onSuccess: async (result) => {
      setError(null);
      const plan = (result as Record<string, unknown>).plan as Record<string, unknown> | undefined;
      setFeedback(
        `已提交局部返工：影响 ${Number(plan?.task_count ?? 0)} 个任务；人工锁定镜头不在批次操作范围内。`,
      );
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => setError(mutationError instanceof Error ? mutationError.message : String(mutationError)),
  });

  const decide = useMutation({
    mutationFn: async ({ kind }: { kind: "HUMAN_APPROVED" | "REJECTED" | "PUBLICATION_AUTHORIZED" | "CHANGES_REQUESTED" }) => {
      if (!editionId) throw new Error("选择一个输出版本");
      const subjectHash = String(selectedIssue?.subject_hash ?? (activeEdition?.current_render as Record<string, unknown> | null)?.sha256 ?? "");
      if (!subjectHash) throw new Error("该版本还没有可绑定的内容哈希，不能登记决定");
      return recordExplainerDecision(editionId, {
        decision_kind: kind,
        subject_kind: "COMPOSITION_RENDER",
        subject_revision_id: String((activeEdition?.current_render as Record<string, unknown> | null)?.id ?? editionId),
        subject_hash: subjectHash,
        actor: "local-user",
        reviewed_intervals: [],
        note: "在说明中记录的审阅范围与结论。",
      });
    },
    onSuccess: async (_result, variables) => {
      setError(null);
      setFeedback(
        variables.kind === "PUBLICATION_AUTHORIZED"
          ? "已记录发布授权（独立于人工确认与机器检查）。"
          : "已记录人工决定；本地安装没有登录体系，这是操作者在本机的真实动作，不等于认证到某个自然人。",
      );
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => setError(mutationError instanceof Error ? mutationError.message : String(mutationError)),
  });

  const exportPackage = useMutation({
    mutationFn: async () => {
      if (!editionId) throw new Error("选择一个输出版本");
      return startExplainerExport(
        editionId,
        { intended_territories: ["GLOBAL"], include_stems: true, include_subtitles: true, confirm: true },
        stableIdempotencyKey("explainer-export", { editionId }),
      );
    },
    onSuccess: async (result) => {
      setError(null);
      setFeedback(`已开始构建发布包 ${String((result as Record<string, unknown>).status ?? "")}。全球导出请求不代表所选资产已取得全球许可，预检仍会判定。`);
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => setError(mutationError instanceof Error ? mutationError.message : String(mutationError)),
  });

  const state = useMemo<PageState | null>(() => {
    if (editions.isPending) return { kind: "loading", message: "正在载入审片数据…" };
    if (editions.isError) return { kind: "failed", title: "无法载入输出版本", body: editions.error instanceof Error ? editions.error.message : "未知错误" };
    if (editionList.length === 0) return { kind: "empty", title: "还没有输出版本", body: "先在总览提交预检并完成生产，才会有成片可审。" };
    if (!activeEdition?.current_render) {
      return {
        kind: "empty",
        title: "还没有可审的成片",
        body: "渲染完成后才能按问题审片；已提交渲染不等于制作成功。",
      };
    }
    if (run.data?.run?.projected_status === "QC_RUNNING" || run.data?.run?.projected_status === "RUNNING") {
      return { kind: "running", title: "生产或质检进行中", body: "覆盖报告会在对应阶段完成后更新。", progress: null };
    }
    if (qc.data?.status === "STALE") {
      return { kind: "stale", title: "质检报告已过期", body: "对象哈希已变化，旧报告不能用于放行；请重新检查受影响节点。" };
    }
    return null;
  }, [activeEdition, editionList.length, editions.error, editions.isError, editions.isPending, qc.data?.status, run.data?.run?.projected_status]);

  return <div className="explainer-page">
    <Panel
      title="成片版本"
      subtitle="导出必须绑定冻结版本，不能读取“最新”。"
      actions={
        <>
          {editionList.length > 1 ? (
            <label className="explainer-field">
              输出版本
              <select
                value={editionId ?? ""}
                onChange={(event) => setSearchParams((params) => {
                  params.set("edition", event.target.value);
                  return params;
                })}
              >
                {editionList.map((edition) => (
                  <option key={String(edition.id)} value={String(edition.id)}>
                    {localeLabel(String(edition.voice_locale))} · {String(edition.aspect_ratio)} · {subtitleModeLabel(String(edition.subtitle_mode))}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <AuthorityBadge kind={qc.data?.human_decision ? "human" : qc.data?.machine_decision ? "machine" : "none"} />
        </>
      }
    >
      <StateNotice state={state} />
      <InlineOk message={feedback} />
      <InlineError message={error} />
      <div className="explainer-actions" style={{ marginTop: 12 }}>
        <button type="button" className="primary-action" disabled={!editionId || render.isPending} onClick={() => render.mutate()}>
          {render.isPending ? "正在冻结并提交…" : "冻结 manifest 并渲染"}
        </button>
        <button type="button" disabled={!latestRun} onClick={() => controlExplainerRun(String(latestRun?.id), "pause", { reason: "审片暂停" })}>暂停生产</button>
        <button type="button" disabled={!editionId || exportPackage.isPending} onClick={() => exportPackage.mutate()}>导出发布包</button>
      </div>
    </Panel>

    <div className="explainer-grid">
      <div className="explainer-stack">
        <Panel title="完整成片播放器" subtitle="逐帧前进/后退需要真实成片；这里不伪造画面。">
          <MediaPlaceholder
            label="成片预览"
            detail={activeEdition?.current_render ? `渲染版本 ${String((activeEdition.current_render as Record<string, unknown>).id ?? "").slice(0, 8)}…` : "尚未渲染"}
          />
          <div className="explainer-actions" style={{ marginTop: 10 }}>
            <button type="button" onClick={() => setFrame((value) => Math.max(0, value - 1))}>前一帧</button>
            <button type="button" onClick={() => setFrame((value) => value + 1)}>后一帧</button>
            <span className="badge">帧 {frame} · {formatMs(frame * 40)}</span>
          </div>
          <div className="explainer-timeline">
            <div className="explainer-timeline-lane">
              <span>画面</span>
              <div className="explainer-timeline-clips">
                <b style={{ flex: 1 }}>起始</b><b style={{ flex: 2 }}>主体</b><b style={{ flex: 1 }}>收束</b>
              </div>
            </div>
            <div className="explainer-timeline-lane">
              <span>旁白</span>
              <div className="explainer-timeline-clips audio"><b style={{ flex: 4 }}>{localeLabel(String(activeEdition?.voice_locale ?? ""))}旁白</b></div>
            </div>
            <div className="explainer-timeline-lane">
              <span>音乐</span>
              <div className="explainer-timeline-clips bgm"><b style={{ flex: 2 }}>背景音乐</b><b style={{ flex: 1 }}>结尾</b></div>
            </div>
            <div className="explainer-timeline-lane">
              <span>字幕</span>
              <div className="explainer-timeline-clips sub"><b style={{ flex: 1 }}>cue</b><b style={{ flex: 1 }}>cue</b><b style={{ flex: 1 }}>cue</b></div>
            </div>
          </div>
          <p className="explainer-note">时间轴为结构示意；真实帧/PTS 由冻结的 RenderManifest 决定。</p>
        </Panel>

        <Panel title="审查覆盖范围" subtitle="不同检测分别陈述，不把抽样写成逐帧人工审核。">
          <div className="explainer-coverage">
            {coverage.map((row) => (
              <div key={row.key}>
                <small>{row.label}</small>
                <strong>{row.detail}</strong>
                <small>{row.note}</small>
              </div>
            ))}
          </div>
          {qc.data?.unverified_checks && qc.data.unverified_checks.length > 0 ? (
            <p className="explainer-note warn">
              未检查项：
              <span>{qc.data.unverified_checks.join("、")}</span>
            </p>
          ) : qc.data ? (
            <p className="explainer-note">未检查项：本版本没有记录未检查项。</p>
          ) : null}
          <p className="explainer-note">
            技术全量解码 100% 不等于全帧语义理解；未抽样区域不会被写成“已检查”。缺少视觉能力时按所选策略阻塞或标记 UNCHECKED。
          </p>
        </Panel>
      </div>

      <div className="explainer-stack">
        <Panel title="问题定位" subtitle="点击问题 → 看证据 → 局部修复">
          <div className="explainer-actions">
            {["BLOCKER", "MAJOR", "MINOR", "UNKNOWN"].map((severity) => (
              <span className={`badge ${issueSeverityTone(severity) === "danger" ? "danger" : issueSeverityTone(severity) === "warn" ? "warn" : ""}`} key={severity}>
                {SEVERITY_LABELS[severity]} {issues.filter((issue) => String(issue.severity) === severity).length}
              </span>
            ))}
          </div>
          {issues.length === 0 ? <p className="muted">没有记录到问题。issues 为空不等于检测已运行。</p> : issues.slice(0, 8).map((issue) => (
            <div className={`explainer-issue-row${String(issue.severity) === "BLOCKER" ? " danger" : ""}`} key={String(issue.id)}>
              <span className="explainer-issue-mark">!</span>
              <div>
                <p>{String(issue.observed || issue.issue_kind)}</p>
                <p className="muted">
                  {issue.start_ms !== null && issue.start_ms !== undefined ? `${formatMs(Number(issue.start_ms))} · ` : ""}
                  {String(issue.detector ?? "")} · {String(issue.status ?? "")}
                </p>
                <button
                  type="button"
                  className="explainer-source-link"
                  onClick={() => setSearchParams((params) => {
                    params.set("issue", String(issue.id));
                    return params;
                  })}
                >
                  定位并处理 →
                </button>
              </div>
            </div>
          ))}
          {selectedIssue ? (
            <div className="explainer-note">
              <strong>建议修复：</strong>{String(selectedIssue.suggested_repair || "按根因节点生成新版本，并重查受影响下游。")}
              <p className="muted">预期：{String(selectedIssue.expected || "—")}</p>
              <p className="muted">证据：{String(selectedIssue.evidence_text_span || JSON.stringify(selectedIssue.evidence_json ?? {}))}</p>
            </div>
          ) : null}
          <div className="explainer-actions" style={{ marginTop: 10 }}>
            <button type="button" disabled={!selectedIssue || repair.isPending} onClick={() => repair.mutate()}>
              {repair.isPending ? "正在提交…" : "只修这个问题"}
            </button>
          </div>
        </Panel>

        <Panel title="人工确认与发布授权" subtitle="三种决定分开记录，互不代替。">
          <div className="explainer-actions">
            <button type="button" disabled={decide.isPending} onClick={() => decide.mutate({ kind: "HUMAN_APPROVED" })}>确认当前成片</button>
            <button type="button" disabled={decide.isPending} onClick={() => decide.mutate({ kind: "CHANGES_REQUESTED" })}>要求修改</button>
            <button type="button" disabled={decide.isPending} onClick={() => decide.mutate({ kind: "PUBLICATION_AUTHORIZED" })}>记录发布授权</button>
          </div>
          <SettingRow label="机器检查" value={qc.data?.machine_decision ? "政策接受（自动）" : "尚未产生"} />
          <SettingRow label="人工确认" value={qc.data?.human_decision ? String((qc.data.human_decision as Record<string, unknown>).actor ?? "已记录") : "尚未记录"} />
          <SettingRow label="发布授权" value={qc.data?.publication_decision ? "已记录" : "尚未记录"} />
          <p className="explainer-note">
            机器政策接受只声明规则、阈值与检测证据，不等于人工审阅，也不构成发布授权；HTTP 客户端不能自填机器接受。
          </p>
        </Panel>

        <Panel title="导出内容" subtitle="母版、语言/字幕、stems、封面、文案、QA 与许可清单。">
          <p className="muted">导出包绑定冻结版本而不是“最新”，并保留逐资产许可链与 AI 披露字段。</p>
          <p className="explainer-note">
            无账号授权或接口能力时输出完整手工发布包并标记 NEEDS_MANUAL_PUBLISH；不会为了上传重新生成作品。
          </p>
          <div className="explainer-actions" style={{ marginTop: 10 }}>
            <button type="button" className="primary-action" disabled={!editionId || exportPackage.isPending} onClick={() => exportPackage.mutate()}>
              {exportPackage.isPending ? "正在构建…" : "构建发布包"}
            </button>
          </div>
          <details style={{ marginTop: 10 }}>
            <summary>重新预检计划</summary>
            <div className="explainer-actions" style={{ marginTop: 8 }}>
              <button
                type="button"
                onClick={async () => {
                  try {
                    const report = await preflightExplainerPlan(projectId, {});
                    setError(null);
                    setFeedback(
                      report.executable
                        ? `预检通过，计划哈希 ${report.plan_hash.slice(0, 12)}…`
                        : `预检存在 ${report.blockers.length} 项阻塞：${report.blockers.map((blocker) => blocker.message).join("；")}`,
                    );
                  } catch (mutationError) {
                    setFeedback(null);
                    setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
                  }
                }}
              >
                运行预检
              </button>
            </div>
          </details>
        </Panel>
      </div>
    </div>
  </div>;
}
