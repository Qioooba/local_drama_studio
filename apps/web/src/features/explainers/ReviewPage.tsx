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
import { completeOperation, operationIdempotencyKey, stableIdempotencyKey } from "../../services/commandId";
import { queryKeys } from "../../query/queryKeys";
import { AuthorityBadge, InlineError, InlineOk, Panel, SettingRow, StateNotice, type PageState } from "./components";
import {
  NarrationPlayer,
  RenderPlayer,
  ReviewTimeline,
  clampFrame,
  frameForMs,
  lanesFromManifest,
  useSortedIssues,
  type RenderMedia,
} from "./media";
import { SEVERITY_LABELS, coverageRows, formatMs, issueSeverityTone, localeLabel, resolveOutputsForExplainer, subtitleModeLabel } from "./viewModels";
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
  const renderMedia = (activeEdition?.current_render as RenderMedia | null) ?? null;
  const qc = useExplainerQc(editionId, renderMedia?.id ?? null);
  const latestRun = overview.data?.latest_run ?? null;
  const run = useExplainerRun(latestRun?.id ?? null);
  const [requestedFrame, setRequestedFrame] = useState(0);
  const [playheadFrame, setPlayheadFrame] = useState(0);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reviewNote, setReviewNote] = useState("");

  const issues = ((qc.data?.open_issues ?? []) as Array<Record<string, unknown>>).concat(
    ((qc.data?.issues ?? []) as Array<Record<string, unknown>>).filter(
      (issue) => !(qc.data?.open_issues ?? []).some((open) => String((open as Record<string, unknown>).id) === String(issue.id)),
    ),
  );
  const issueList = useSortedIssues(issues, { pageSize: 8 });
  const selectedIssueId = searchParams.get("issue");
  const selectedIssue =
    issueList.sorted.find((issue) => String(issue.id) === selectedIssueId) ?? issueList.sorted[0] ?? null;

  const coverage = coverageRows(qc.data?.coverage as Record<string, unknown> | undefined);
  const timelineLanes = useMemo(
    () =>
      lanesFromManifest((activeEdition?.composition_items ?? []) as Array<Record<string, unknown>>, {
        fpsNum: Number((activeEdition?.composition as Record<string, unknown> | null)?.fps_num ?? 0),
        fpsDen: Number((activeEdition?.composition as Record<string, unknown> | null)?.fps_den ?? 0),
      }),
    [activeEdition],
  );

  const render = useMutation({
    mutationFn: async () => {
      if (!editionId) throw new Error("选择一个输出版本");
      const plan = await startExplainerRender(
        editionId,
        { freeze: true, confirm: false },
        stableIdempotencyKey("explainer-render-plan", { editionId }),
      );
      const planRecord = plan as Record<string, unknown>;
      const planStatus = String(planRecord.status ?? "");
      // Only READY_TO_START may be confirmed.  The previous implementation sent
      // confirm=true for *any* resolved plan response — including
      // ``{status: "BLOCKED", would_create_jobs: false}`` — and then displayed
      // "已提交分块渲染" for it.
      if (planStatus === "BLOCKED" || planStatus === "CAPABILITY_UNAVAILABLE") {
        return { outcome: "BLOCKED" as const, plan: planRecord };
      }
      if (planStatus !== "READY_TO_START") {
        return { outcome: "UNEXPECTED" as const, plan: planRecord };
      }
      const submitted = await startExplainerRender(
        editionId,
        { freeze: true, confirm: true, composition_revision_id: planRecord.composition_revision_id ?? null },
        stableIdempotencyKey("explainer-render", { editionId, composition: planRecord.composition_revision_id }),
      );
      return { outcome: "SUBMITTED" as const, submitted: submitted as Record<string, unknown> };
    },
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
      if (result.outcome === "BLOCKED") {
        const blockers = (result.plan.blockers as Array<Record<string, unknown>> | undefined) ?? [];
        setFeedback(null);
        setError(
          `渲染被阻塞，未提交任何任务：${blockers.map((item) => String(item.message ?? "")).join("；") || "缺少可渲染的前置条件"}`,
        );
        return;
      }
      if (result.outcome === "UNEXPECTED") {
        setFeedback(null);
        setError(`渲染预检返回了未预期的状态 ${String(result.plan.status ?? "")}，未提交。`);
        return;
      }
      const submitted = result.submitted;
      const jobId = submitted.job_id ? String(submitted.job_id) : "";
      if (submitted.status === "ACCEPTED" && jobId) {
        setError(null);
        setFeedback(`已受理分块渲染任务 ${jobId}（manifest ${String(submitted.manifest_hash ?? "").slice(0, 12)}…）。`);
        return;
      }
      // A stage with no registered worker reports CAPABILITY_UNAVAILABLE and creates
      // nothing; it must never be shown as "已提交".
      setFeedback(null);
      setError(
        `渲染未被接受（${String(submitted.status ?? submitted.reason ?? "未知")}）：${
          submitted.detail && typeof submitted.detail === "object"
            ? String((submitted.detail as Record<string, unknown>).note ?? "")
            : ""
        }`,
      );
    },
    onError: (mutationError) => setError(mutationError instanceof Error ? mutationError.message : String(mutationError)),
  });

  const repair = useMutation({
    mutationFn: async () => {
      if (!selectedIssue) throw new Error("选择一个要修复的问题");
      const revision = Number(overview.data?.video?.revision ?? 1);
      const issueScope = `explainer-repairs:${projectId}:${String(selectedIssue.id)}`;
      const request = {
        issue_ids: [String(selectedIssue.id)],
        expected_revision: revision,
      };
      const planned = await planExplainerRepairs(projectId, { ...request, confirm: false });
      // The confirming call creates real repair jobs, so it must carry an
      // operation key: a network retry then reuses the same key instead of
      // scheduling the same repair twice, and the server rejects a confirming
      // call without one.
      const confirmed = await planExplainerRepairs(
        projectId,
        { ...request, confirm: true },
        operationIdempotencyKey(issueScope, request),
      );
      completeOperation(issueScope);
      return { planned, confirmed };
    },
    onSuccess: async (result) => {
      setError(null);
      const plan = (result.planned as Record<string, unknown>).plan as Record<string, unknown> | undefined;
      const confirmed = result.confirmed as Record<string, unknown>;
      const jobIds = Array.isArray(confirmed.job_ids) ? (confirmed.job_ids as unknown[]) : [];
      const unschedulable = Array.isArray(confirmed.unschedulable) ? (confirmed.unschedulable as unknown[]) : [];
      if (confirmed.submitted === true && jobIds.length > 0) {
        setFeedback(
          `已提交局部返工：新增 ${jobIds.length} 个真实任务，影响 ${Number(plan?.task_count ?? 0)} 个任务；` +
            "人工锁定镜头不在批次操作范围内。",
        );
      } else {
        // No fake success: a repair that could not be scheduled says so and names
        // the responsible steps that have no standalone command.
        setFeedback(
          `未创建任务：${String(confirmed.status ?? "REPAIR_NOT_SCHEDULABLE")}；` +
            `${unschedulable.length} 个责任步骤无法单独执行，请重跑对应阶段。`,
        );
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => setError(mutationError instanceof Error ? mutationError.message : String(mutationError)),
  });

  const decide = useMutation({
    mutationFn: async ({
      kind,
      intervals,
    }: {
      kind: "HUMAN_APPROVED" | "REJECTED" | "PUBLICATION_AUTHORIZED" | "CHANGES_REQUESTED";
      intervals: number[][];
    }) => {
      if (!editionId) throw new Error("选择一个输出版本");
      // The confirmation subject comes from the *current render* alone.  The old
      // code merged the selected issue's hash first, so confirming the film could
      // submit a narration issue's hash against a render id — the server correctly
      // answered STALE_REVISION while the operator believed they had confirmed the
      // film they were looking at.
      if (!renderMedia) throw new Error("该版本还没有可确认的成片；没有媒体就不能登记“确认成片”");
      if (!renderMedia.sha256) throw new Error("当前成片还没有内容哈希，不能登记决定");
      return recordExplainerDecision(editionId, {
        decision_kind: kind,
        subject_kind: "COMPOSITION_RENDER",
        subject_revision_id: renderMedia.id,
        subject_hash: renderMedia.sha256,
        actor: "local-user",
        reviewed_intervals: intervals,
        note: reviewNote,
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

  const currentIntervals = (): number[][] => {
    if (!playheadFrame && !requestedFrame) return [[0, 0]];
    const start = Math.min(playheadFrame, requestedFrame);
    const end = Math.max(playheadFrame, requestedFrame);
    return [[start, end]];
  };

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
      const record = result as Record<string, unknown>;
      const accepted = String(record.status ?? "") === "ACCEPTED";
      const packageId = record.package_id ? String(record.package_id) : "";
      if (accepted && packageId) {
        setFeedback(`发布包已受理 ${packageId}；全球导出请求不代表所选资产已取得全球许可，预检仍会判定。`);
      } else if (String(record.status ?? "") === "CAPABILITY_UNAVAILABLE") {
        setFeedback(null);
        setError(`导出未被接受：${String(record.reason ?? "")}。没有创建发布包。`);
      } else {
        setFeedback(null);
        setError(`导出未产生发布包（${String(record.status ?? "未知")}）。`);
      }
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
        <Panel title="完整成片播放器" subtitle="逐帧前进/后退绑定真实成片；不伪造画面。">
          <RenderPlayer
            media={renderMedia}
            seekToFrame={requestedFrame}
            onFrameChange={setPlayheadFrame}
          />
          <div className="explainer-actions" style={{ marginTop: 10 }}>
            <button
              type="button"
              disabled={!renderMedia?.playable}
              onClick={() => setRequestedFrame((value) => clampFrame(value - 1, renderMedia?.frame_count))}
            >
              前一帧
            </button>
            <button
              type="button"
              disabled={!renderMedia?.playable}
              onClick={() => setRequestedFrame((value) => clampFrame(value + 1, renderMedia?.frame_count))}
            >
              后一帧
            </button>
            <button type="button" disabled={!renderMedia?.playable} onClick={() => setRequestedFrame(0)}>回到首帧</button>
            <span className="badge">
              目标帧 {requestedFrame}
              {renderMedia?.frame_count ? ` / ${Number(renderMedia.frame_count) - 1}` : ""} · 实际帧 {playheadFrame}
            </span>
          </div>
          <p className="explainer-note">
            按钮按冻结 manifest 的有理数帧率换算时间；浏览器对压缩关键帧的 seek 不保证逐帧精确，
            所以“实际帧”来自解码器的进度事件，而不是先写成目标帧已显示。
          </p>
          <ReviewTimeline lanes={timelineLanes} busy={editions.isPending} />
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
          {issues.length === 0 ? <p className="muted">没有记录到问题。issues 为空不等于检测已运行。</p> : issueList.visible.map((issue) => (
            <div
              className={`explainer-issue-row${String(issue.severity) === "BLOCKER" ? " danger" : ""}${
                selectedIssue && String(selectedIssue.id) === String(issue.id) ? " selected" : ""
              }`}
              key={String(issue.id)}
            >
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
                  onClick={() => {
                    setSearchParams((params) => {
                      params.set("issue", String(issue.id));
                      return params;
                    });
                    // Selecting an issue also moves the player's evidence position.
                    if (issue.start_ms !== null && issue.start_ms !== undefined && renderMedia?.fps_num && renderMedia?.fps_den) {
                      const frame = frameForMs(Number(issue.start_ms), renderMedia.fps_num, renderMedia.fps_den);
                      if (frame !== null) setRequestedFrame(clampFrame(frame, renderMedia.frame_count));
                    }
                  }}
                >
                  定位并处理 →
                </button>
              </div>
            </div>
          ))}
          {/* Every issue must be reachable; the list used to be ``slice(0, 8)`` with a
              count badge taken from *all* issues, so "阻塞 12" hid four of them. */}
          {issueList.hiddenCount > 0 ? (
            <div className="explainer-actions">
              <button type="button" onClick={issueList.showMore}>
                查看余下 {Math.min(8, issueList.hiddenCount)} 项（共 {issueList.sorted.length} 项）
              </button>
              <button type="button" onClick={issueList.showAll}>显示全部 {issueList.sorted.length} 项</button>
              <span className="badge">已显示 {issueList.visible.length} / {issueList.sorted.length}</span>
            </div>
          ) : issueList.sorted.length > 0 ? (
            <p className="muted">已显示全部 {issueList.sorted.length} 项问题（按严重度、时间、ID 稳定排序）。</p>
          ) : null}
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
          <label className="explainer-field">
            审阅范围（帧区间 {playheadFrame}–{requestedFrame}）
            <span className="muted">决定会绑定当前成片的 kind/id/hash 与该帧区间。</span>
          </label>
          <label className="explainer-field">
            审查说明
            <textarea
              value={reviewNote}
              onChange={(event) => setReviewNote(event.target.value)}
              rows={3}
              placeholder="例如：全片通看一遍；第 3 分钟旁白节奏偏快，其余可接受。"
            />
          </label>
          <div className="explainer-actions">
            <button
              type="button"
              disabled={decide.isPending || !renderMedia?.playable}
              onClick={() => decide.mutate({ kind: "HUMAN_APPROVED", intervals: currentIntervals() })}
            >
              确认当前成片
            </button>
            <button
              type="button"
              disabled={decide.isPending}
              onClick={() => decide.mutate({ kind: "CHANGES_REQUESTED", intervals: currentIntervals() })}
            >
              要求修改
            </button>
            <button
              type="button"
              disabled={decide.isPending || !renderMedia?.playable}
              onClick={() => decide.mutate({ kind: "PUBLICATION_AUTHORIZED", intervals: currentIntervals() })}
            >
              记录发布授权
            </button>
          </div>
          <SettingRow
            label="决定主体"
            value={
              renderMedia
                ? `${renderMedia.id.slice(0, 8)}… · ${String(renderMedia.sha256 ?? "").slice(0, 12)}…`
                : "没有可确认的成片"
            }
          />
          <SettingRow label="机器检查" value={qc.data?.machine_decision ? "政策接受（自动）" : "尚未产生"} />
          <SettingRow label="人工确认" value={qc.data?.human_decision ? String((qc.data.human_decision as Record<string, unknown>).actor ?? "已记录") : "尚未记录"} />
          <SettingRow label="发布授权" value={qc.data?.publication_decision ? "已记录" : "尚未记录"} />
          <p className="explainer-note">
            机器政策接受只声明规则、阈值与检测证据，不等于人工审阅，也不构成发布授权；HTTP 客户端不能自填机器接受。
            没有可播放成片时不提供无条件的“确认成片”成功路径。
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
                    const outputs = resolveOutputsForExplainer(
                      (editions.data?.editions ?? overview.data?.editions) as Array<Record<string, unknown>> | undefined,
                      overview.data?.video?.source_locale as string | undefined,
                      overview.data?.video?.aspect_ratio as string | undefined,
                    );
                    const report = await preflightExplainerPlan(projectId, { outputs });
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
