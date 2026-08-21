import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { Drawer, TabPanel, Tabs, type TabItem } from "../../components/ui";
import {
  getEpisodeTimelineStatus,
  getReviewContext,
  listFormalSelectionCandidates,
  listReviewTemplates,
  reviewInbox,
  runMachineCheck,
  selectMediaVersion,
  submitReview,
  type FormalSelectionCandidate,
} from "../../generated/api";
import { getShotGroupWorkspace } from "../episode-plan-v2/shotGroupsApi";
import { EpisodeReviewPanel } from "../production/EpisodeReviewPanel";
import { FormalSelectionPanel } from "../reviews/FormalSelectionPanel";
import { ReviewInboxPanel } from "../reviews/ReviewInboxPanel";

type IssueFilter = "ALL" | "BLOCKED" | "MACHINE" | "STALE";
type ReviewTask = "shot" | "render" | "delivery";

const REVIEW_TASK_IDS = new Set<ReviewTask>(["shot", "render", "delivery"]);

async function listEpisodeFormalCandidates(projectId: string, episodeId: string) {
  const [candidatesResult, workspace] = await Promise.all([
    listFormalSelectionCandidates(projectId),
    getShotGroupWorkspace(episodeId),
  ]);
  const shotIds = new Set(workspace.shots.map((item) => item.id));
  const matches: FormalSelectionCandidate[] = [];
  const candidates = candidatesResult.items;

  // The formal-selection read model is project-scoped. Resolve its immutable
  // media contexts in small batches so this page never leaks another episode's
  // candidates into the current episode workflow.
  for (let offset = 0; offset < candidates.length; offset += 8) {
    const batch = candidates.slice(offset, offset + 8);
    const contexts = await Promise.all(batch.map(async (candidate) => {
      try {
        return await getReviewContext(candidate.media_version_id);
      } catch {
        return null;
      }
    }));
    batch.forEach((candidate, index) => {
      const media = contexts[index]?.media_version;
      const ownerType = String(media?.owner_type ?? "");
      const ownerId = String(media?.owner_id ?? "");
      if ((ownerType === "EPISODE" && ownerId === episodeId) || (ownerType === "SHOT" && shotIds.has(ownerId))) {
        matches.push(candidate);
      }
    });
  }
  return matches;
}

function SummaryCard({ label, value, detail, tone }: { label: string; value: number | string; detail: string; tone?: string }) {
  return <article className={`episode-review-summary-card${tone ? ` tone-${tone}` : ""}`}><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>;
}

export function EpisodeReviewWorkspace({ projectId, episodeId }: { projectId: string; episodeId: string }) {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const [issueFilter, setIssueFilter] = useState<IssueFilter>("ALL");
  const [shotSearch, setShotSearch] = useState("");
  const [selectionDrawerOpen, setSelectionDrawerOpen] = useState(false);
  const requestedTask = searchParams.get("view") as ReviewTask | null;
  const activeTask: ReviewTask = requestedTask && REVIEW_TASK_IDS.has(requestedTask) ? requestedTask : "shot";
  const selectTask = (task: string) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (task === "shot") next.delete("view");
      else next.set("view", task);
      return next;
    }, { replace: true });
  };

  const inbox = useQuery({
    queryKey: ["reviews", "inbox", projectId, episodeId],
    queryFn: () => reviewInbox(projectId, "", { episode_id: episodeId }),
  });
  const templates = useQuery({ queryKey: ["reviews", "templates"], queryFn: () => listReviewTemplates() });
  const timelineStatus = useQuery({
    queryKey: ["episode", episodeId, "timeline-status"],
    queryFn: () => getEpisodeTimelineStatus(episodeId),
  });
  const formalCandidates = useQuery({
    queryKey: ["reviews", "formal-selection", projectId, episodeId],
    queryFn: () => listEpisodeFormalCandidates(projectId, episodeId),
    enabled: activeTask === "shot" && selectionDrawerOpen,
  });

  const allItems = inbox.data?.items ?? [];
  const visibleItems = useMemo(() => {
    const needle = shotSearch.trim().toLocaleLowerCase();
    return allItems.filter((item) => {
      const issueMatches = issueFilter === "ALL"
        || (issueFilter === "BLOCKED" && Number(item.is_blocked ?? 0) === 1)
        || (issueFilter === "MACHINE" && String(item.machine_status ?? "NOT_RUN") !== "PASS")
        || (issueFilter === "STALE" && Boolean(item.is_stale));
      const searchMatches = !needle || [item.shot_code, item.media_version_id, item.stage, item.media_kind]
        .some((value) => String(value ?? "").toLocaleLowerCase().includes(needle));
      return issueMatches && searchMatches;
    });
  }, [allItems, issueFilter, shotSearch]);

  useEffect(() => {
    if (visibleItems.some((item) => item.media_version_id === selectedVersionId)) return;
    setSelectedVersionId(visibleItems[0]?.media_version_id ?? null);
  }, [selectedVersionId, visibleItems]);

  const selectedContext = useQuery({
    queryKey: ["reviews", "context", selectedVersionId],
    queryFn: () => getReviewContext(selectedVersionId as string),
    enabled: activeTask === "shot" && Boolean(selectedVersionId),
  });

  const refreshReviewData = () => {
    void queryClient.invalidateQueries({ queryKey: ["reviews", "inbox", projectId, episodeId] });
    void queryClient.invalidateQueries({ queryKey: ["reviews", "context"] });
    void queryClient.invalidateQueries({ queryKey: ["reviews", "formal-selection", projectId, episodeId] });
  };
  const selectMutation = useMutation({
    mutationFn: ({ mediaVersionId, selectionType }: { mediaVersionId: string; selectionType: string }) => selectMediaVersion(mediaVersionId, selectionType),
    onSuccess: refreshReviewData,
  });
  const reviewMutation = useMutation({
    mutationFn: ({ mediaVersionId, payload }: { mediaVersionId: string; payload: Parameters<typeof submitReview>[1] }) => submitReview(mediaVersionId, payload),
    onSuccess: refreshReviewData,
  });
  const machineCheckMutation = useMutation({
    mutationFn: (mediaVersionId: string) => runMachineCheck(mediaVersionId),
    onSuccess: refreshReviewData,
  });

  const blockedCount = allItems.filter((item) => Number(item.is_blocked ?? 0) === 1).length;
  const staleCount = allItems.filter((item) => Boolean(item.is_stale)).length;
  const machineIssueCount = allItems.filter((item) => String(item.machine_status ?? "NOT_RUN") !== "PASS").length;
  const latestRender = timelineStatus.data?.status.renders.latest ?? null;
  const loading = inbox.isPending || templates.isPending || timelineStatus.isPending;
  const failures = [inbox.error, templates.error, timelineStatus.error].filter(Boolean);
  const taskItems: TabItem[] = [
    { id: "shot", label: "Shot 审核", badge: allItems.length },
    { id: "render", label: "Render 审核", badge: latestRender ? "可审核" : "未生成" },
    { id: "delivery", label: "交付交接", badge: staleCount ? `${staleCount} 失效` : "待确认" },
  ];

  return <div className="episode-review-workspace">
    <section className="episode-review-summary" aria-label="本集审核摘要">
      <SummaryCard label="未解决候选" value={allItems.length} detail="仅当前分集" />
      <SummaryCard label="阻塞问题" value={blockedCount} detail="完整性、QC 或失效" tone={blockedCount ? "danger" : "ok"} />
      <SummaryCard label="机器证据待补" value={machineIssueCount} detail="未运行或未通过" tone={machineIssueCount ? "warning" : "ok"} />
      <SummaryCard label="失效审核" value={staleCount} detail="上游变更后需重审" tone={staleCount ? "warning" : "ok"} />
    </section>

    {loading && <p className="empty-state" aria-live="polite">正在读取本集候选、机器证据与整集版本…</p>}
    {failures.length > 0 && <div className="inline-error" role="alert"><strong>审核工作台有 {failures.length} 项读取失败。</strong><button className="secondary" type="button" onClick={() => { void inbox.refetch(); void templates.refetch(); void timelineStatus.refetch(); }}>重试</button></div>}

    <Tabs items={taskItems} selectedId={activeTask} onChange={selectTask} ariaLabel="本集审核任务">
      <TabPanel id="shot" selectedId={activeTask}>
        <section id="candidate-review" className="episode-review-stage" aria-labelledby="candidate-review-title">
          <div className="panel-heading"><div><p className="eyebrow">Shot · 候选收件箱</p><h3 id="candidate-review-title">比较、决定与证据一次完成</h3></div><button className="secondary" type="button" onClick={() => setSelectionDrawerOpen(true)}>采用正式版本</button></div>
          <p className="muted">批量审核使用同一模板、先预检再原子提交；机器检查只记录技术证据，不能代替人工决定。</p>
          <div className="episode-review-filters" role="group" aria-label="本集审核过滤器">
            <label>问题范围<select value={issueFilter} onChange={(event) => setIssueFilter(event.target.value as IssueFilter)}><option value="ALL">全部未解决</option><option value="BLOCKED">仅阻塞</option><option value="MACHINE">机器证据待补</option><option value="STALE">失效待重审</option></select></label>
            <label>镜头 / 版本<input value={shotSearch} onChange={(event) => setShotSearch(event.target.value)} placeholder="搜索镜头编号或版本 ID" /></label>
            <span className="status-pill">显示 {visibleItems.length} / {allItems.length}</span>
          </div>
          {!inbox.isPending && allItems.length === 0 ? <p className="empty-state">本集没有未解决的候选审核项。</p> : <ReviewInboxPanel
            items={visibleItems}
            templates={templates.data?.items ?? []}
            selectedVersionId={selectedVersionId}
            context={selectedContext.data}
            onSelect={(id) => { reviewMutation.reset(); machineCheckMutation.reset(); setSelectedVersionId(id); }}
            onPromote={(mediaVersionId, selectionType) => selectMutation.mutate({ mediaVersionId, selectionType })}
            selecting={selectMutation.isPending}
            onMachineCheck={(mediaVersionId) => machineCheckMutation.mutate(mediaVersionId)}
            machineChecking={machineCheckMutation.isPending}
            machineCheckError={machineCheckMutation.error ? String(machineCheckMutation.error) : null}
            onSubmit={(mediaVersionId, payload) => reviewMutation.mutate({ mediaVersionId, payload })}
            submitting={reviewMutation.isPending}
            submitError={reviewMutation.error ? String(reviewMutation.error) : null}
            submitSucceeded={reviewMutation.isSuccess}
          />}
        </section>
      </TabPanel>

      <TabPanel id="render" selectedId={activeTask}>
        <section id="episode-render-review" className="episode-review-stage" aria-labelledby="episode-render-stage-title">
          <div className="panel-heading"><div><p className="eyebrow">Render · 整集</p><h3 id="episode-render-stage-title">审核冻结时间线产出的 render</h3></div><span className="status-pill neutral">独立人工 Gate</span></div>
          <EpisodeReviewPanel render={latestRender} templates={templates.data?.items ?? []} onChanged={() => void timelineStatus.refetch()} />
        </section>
      </TabPanel>

      <TabPanel id="delivery" selectedId={activeTask}>
        <section className="episode-review-stage creative-task-gateway" aria-labelledby="review-delivery-title">
          <div><p className="eyebrow">Delivery · 交接</p><h3 id="review-delivery-title">审核结论不会自动发布交付包</h3><p className="muted">先在时间线确认冻结输入，再到交付工作区执行 render、manifest 校验、人工与平台审核。当前页不复制交付写操作。</p></div>
          <div className="creative-task-gateway__actions"><Link className="secondary" to={`/projects/${projectId}/episodes/${episodeId}/timeline`}>检查冻结时间线</Link><Link className="primary-action" to={`/projects/${projectId}/episodes/${episodeId}/delivery`}>进入交付工作区</Link></div>
        </section>
      </TabPanel>
    </Tabs>

    <Drawer open={selectionDrawerOpen} onClose={() => setSelectionDrawerOpen(false)} title="采用正式版本" width={560}>
      <div className="creative-task-drawer-content">
        <p className="muted">这里只采用已经人工批准、校验完整的 FORMAL 视频；不会在抽屉内创建审核结论。</p>
        {formalCandidates.isPending ? <p className="empty-state" aria-live="polite">正在限定本集正式候选…</p> : formalCandidates.isError ? <p className="inline-error" role="alert">读取本集正式候选失败：{String(formalCandidates.error)}</p> : <FormalSelectionPanel projectId={projectId} candidates={formalCandidates.data ?? []} onChanged={refreshReviewData} />}
      </div>
    </Drawer>
  </div>;
}
