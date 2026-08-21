import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Drawer, TabPanel, Tabs, type TabItem } from "../components/ui";
import { getEpisodeTimelineStatus } from "../generated/api";
import { SubtitleRevisionPanel } from "../features/production/SubtitleRevisionPanel";
import { TimelineStatusPanel } from "../features/status/ReadinessPanels";
import { TimelineComposer } from "../features/timeline-v2/TimelineComposer";
import { TimelineExportPanel } from "../features/timeline-v2/TimelineExportPanel";
import "../features/timeline-v2/timeline-v2.css";
import "./creative-workspaces.css";

type TimelineTask = "edit" | "subtitles" | "export";

const TIMELINE_TASKS = new Set<TimelineTask>(["edit", "subtitles", "export"]);
const TIMELINE_TABS: TabItem[] = [
  { id: "edit", label: "编排与冻结" },
  { id: "subtitles", label: "字幕 revision" },
  { id: "export", label: "导出与合成" },
];

export function TimelinePage() {
  const { projectId, episodeId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const [factsOpen, setFactsOpen] = useState(false);
  const [composeOpen, setComposeOpen] = useState(false);
  const requestedTask = searchParams.get("view") as TimelineTask | null;
  const activeTask: TimelineTask = requestedTask && TIMELINE_TASKS.has(requestedTask) ? requestedTask : "edit";
  const selectTask = (task: string) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (task === "edit") next.delete("view");
      else next.set("view", task);
      return next;
    }, { replace: true });
  };
  const status = useQuery({ queryKey: ["episode", episodeId, "timeline-status"], queryFn: () => getEpisodeTimelineStatus(episodeId as string), enabled: Boolean(episodeId) });
  if (!projectId || !episodeId) return <p className="inline-error" role="alert">缺少项目或分集上下文。</p>;
  const snapshot = status.data?.status;
  const latestTimeline = snapshot?.timeline.latest;
  const frozenTimelineId = latestTimeline?.status === "FROZEN" && latestTimeline.id ? String(latestTimeline.id) : null;
  return <div className="v2-page timeline-page-v2 creative-task-page">
    <div className="panel-heading"><div><p className="eyebrow">本集时间线</p><h2>版本化成片编排</h2></div><div className="action-row"><button className="secondary" type="button" onClick={() => setFactsOpen(true)}>版本证据</button><Link className="secondary v2-inline-link" to={`/projects/${projectId}/episodes/${episodeId}/delivery`}>合成与交付</Link></div></div>
    <p className="muted">采用结果、声音与字幕编排为新的不可变时间线版本；上游选择变化时只标记 stale，必须显式载入后才能创建新版本。</p>
    {status.isError && <p className="inline-error" role="alert">读取时间线状态失败：{String(status.error)}</p>}
    <div className="card-grid v2-summary-grid" aria-label="时间线概况">
      <div className="status-card"><span>时间线版本</span><strong>{snapshot?.timeline.revision_count ?? "—"}</strong><small>{snapshot?.timeline.latest ? `最新 ${String(snapshot.timeline.latest.status ?? "未知")}` : "尚未创建"}</small></div>
      <div className="status-card"><span>音频绑定</span><strong>{snapshot?.audio.binding_count ?? "—"}</strong><small>{snapshot?.audio.verified_local_count ?? 0} 个已验证本地来源</small></div>
      <div className="status-card"><span>整集渲染</span><strong>{snapshot?.renders.count ?? "—"}</strong><small>{snapshot?.renders.verified_count ?? 0} 个完整性已验证</small></div>
    </div>
    <Tabs items={TIMELINE_TABS} selectedId={activeTask} onChange={selectTask} ariaLabel="时间线任务">
      <TabPanel id="edit" selectedId={activeTask}>
        {snapshot ? <TimelineComposer projectId={projectId} episodeId={episodeId} status={snapshot} onCreated={() => void status.refetch()} /> : !status.isError ? <p className="empty-state" role="status">正在准备编排与冻结工具…</p> : null}
      </TabPanel>
      <TabPanel id="subtitles" selectedId={activeTask}>
        <SubtitleRevisionPanel episodeId={episodeId} projectId={projectId} defaultSourceDocumentVersionId={String(snapshot?.subtitles.latest?.source_document_version_id ?? "")} onCreated={() => void status.refetch()} />
      </TabPanel>
      <TabPanel id="export" selectedId={activeTask}>
        <section className="panel creative-task-stage" aria-labelledby="timeline-export-title">
          <div className="panel-heading"><div><p className="eyebrow">冻结 revision</p><h3 id="timeline-export-title">专业交换与合成交接</h3></div><button className="secondary" type="button" onClick={() => setComposeOpen(true)}>查看合成检查</button></div>
          <TimelineExportPanel timelineRevisionId={frozenTimelineId} subtitleRevisionId={snapshot?.subtitles.latest?.id ? String(snapshot.subtitles.latest.id) : null} />
          {latestTimeline && !frozenTimelineId && <p className="timeline-policy-note">最新 revision 为 {String(latestTimeline.status)}；OTIO、EDL 与剪映导出只对冻结 revision 开放。</p>}
        </section>
      </TabPanel>
    </Tabs>

    <Drawer open={factsOpen} onClose={() => setFactsOpen(false)} title="时间线版本证据" width={560}>
      <div className="creative-task-drawer-content">{snapshot ? <TimelineStatusPanel status={snapshot} /> : <p className="empty-state">尚无可显示的时间线事实。</p>}</div>
    </Drawer>
    <Drawer open={composeOpen} onClose={() => setComposeOpen(false)} title="合成本集检查" width={480}>
      <div className="creative-task-gateway creative-task-gateway--drawer"><div><p className="eyebrow">COMPOSE</p><h3>只消费冻结 revision</h3><p className="muted">交付工作区会执行整集渲染登记、render evidence、审核与打包；不会回写或覆盖时间线输入。</p></div>{frozenTimelineId ? <Link className="primary-action" to={`/projects/${projectId}/episodes/${episodeId}/delivery`}>进入合成与交付</Link> : <p className="inline-error" role="note">先在“编排与冻结”任务中冻结 revision。</p>}</div>
    </Drawer>
  </div>;
}
