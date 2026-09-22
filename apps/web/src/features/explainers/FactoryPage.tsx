/**
 * Explainer factory entry page: the work list, the topic pool hint and the
 * schedule entry point.  It deliberately shows no episode count anywhere.
 */

import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import { useExplainerList } from "./useExplainerQueries";
import "./explainers.css";

type KindFilter = "EXPLAINER" | "DRAMA";

const TARGET_MINUTES = [3, 5, 10, 20, 30];

export function ExplainerFactoryPage() {
  const navigate = useNavigate();
  const [kind, setKind] = useState<KindFilter>("EXPLAINER");
  const [search, setSearch] = useState("");
  const list = useExplainerList({ project_kind: kind, limit: 100 });

  const items = useMemo(() => {
    const rows = (list.data?.items ?? []) as Array<Record<string, unknown>>;
    if (!search.trim()) return rows;
    const needle = search.trim().toLowerCase();
    return rows.filter((row) =>
      `${row.title ?? ""} ${row.code ?? ""}`.toLowerCase().includes(needle),
    );
  }, [list.data?.items, search]);

  const needsAttention = items.filter((row) => Number(row.open_issue_count ?? 0) > 0);

  return <div className="explainer-page">
    <header className="explainer-header">
      <div>
        <p className="eyebrow">EXPLAINER FACTORY</p>
        <h1>把一个事件，讲成一部完整视频</h1>
        <p className="muted">选题、资料、讲解稿、画面、旁白、字幕和成片，放在同一条生产线上。</p>
      </div>
      <div className="explainer-actions">
        <button type="button" className="primary-action" onClick={() => navigate(routes.explainerNew())}>＋ 新建解说</button>
        <Link className="explainer-issue-link" to={routes.systemJobs()}>后台任务</Link>
      </div>
    </header>

    <div className="explainer-panel">
      <div className="explainer-actions">
        <label className="explainer-field" style={{ minWidth: 160 }}>
          类型
          <select value={kind} onChange={(event) => setKind(event.target.value as KindFilter)} aria-label="项目类型筛选">
            <option value="EXPLAINER">解说</option>
            <option value="DRAMA">短剧</option>
          </select>
        </label>
        <label className="explainer-field" style={{ minWidth: 220 }}>
          搜索
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="标题或代号" />
        </label>
      </div>
      <p className="explainer-note">
        解说作品不显示季、集或短剧对白入口；同一作品的中英文与横竖版是同一项目的 edition，而不是互相独立的项目。
      </p>
    </div>

    {list.isPending ? <p className="explainer-state" role="status">正在载入作品列表…</p> : null}
    {list.isError ? <p className="explainer-state danger" role="alert">无法载入作品列表：{list.error instanceof Error ? list.error.message : "未知错误"}</p> : null}

    {!list.isPending && !list.isError && items.length === 0 ? (
      <div className="explainer-panel">
        <h2>还没有{kind === "EXPLAINER" ? "解说" : "短剧"}作品</h2>
        <p className="muted">从“新建解说”开始：填入主题、目标时长、栏目风格、配音语言、输出画幅和自动程度，其余参数收在高级设置里。</p>
        <div className="explainer-actions" style={{ marginTop: 12 }}>
          <button type="button" className="primary-action" onClick={() => navigate(routes.explainerNew())}>去新建</button>
          <Link className="explainer-issue-link" to={routes.projects()}>查看全部项目</Link>
        </div>
      </div>
    ) : null}

    {needsAttention.length > 0 ? (
      <div className="explainer-panel">
        <h2>需要处理</h2>
        <p className="muted">只有需要用户决策的异常集中在这里；GPU 等待与自动重试属于运行提示。</p>
        {needsAttention.map((row) => (
          <div className="explainer-issue-row" key={String(row.project_id)}>
            <span className="explainer-issue-mark">!</span>
            <div>
              <p>{String(row.title ?? "未命名作品")}</p>
              <p className="muted">{Number(row.open_issue_count ?? 0)} 项待处理</p>
              <Link className="explainer-source-link" to={String(row.video_id ? routes.explainerPage(String(row.project_id), "review") : routes.explainerOverview(String(row.project_id)))}>
                查看证据与修复方案 →
              </Link>
            </div>
          </div>
        ))}
      </div>
    ) : null}

    {items.length > 0 ? (
      <div className="explainer-cards">
        {items.map((row) => {
          const projectId = String(row.project_id ?? "");
          const videoId = row.video_id ? String(row.video_id) : null;
          const openIssues = Number(row.open_issue_count ?? 0);
          return (
            <article className="explainer-card" key={projectId}>
              <div className="explainer-actions">
                <span className={`badge ${openIssues > 0 ? "warn" : "blue"}`}>
                  {openIssues > 0 ? `需处理 ${openIssues}` : row.video_id ? "已创建解说" : "待创建解说"}
                </span>
                {String(row.content_kind ?? "") === "ORIGINAL_FICTION" ? <span className="badge">原创虚构</span> : null}
              </div>
              <h3>{String(row.title ?? "未命名作品")}</h3>
              <p className="muted">
                {videoId
                  ? `目标 ${Math.round(Number(row.target_seconds ?? 0) / 60) || "—"} 分钟 · ${Number(row.edition_count ?? 0)} 个输出版本`
                  : "尚未创建解说作品"}
              </p>
              <div className="explainer-actions">
                {videoId ? (
                  <Link className="explainer-issue-link" to={routes.explainerOverview(projectId)}>继续制作</Link>
                ) : (
                  <button type="button" onClick={() => navigate(routes.explainerNew())}>去创建</button>
                )}
              </div>
            </article>
          );
        })}
      </div>
    ) : null}

    <div className="explainer-panel">
      <h2>支持的时长档位</h2>
      <p className="muted">软件本次即支持 3–30 分钟及自定义时长；几十分钟由章节与镜头组成，不要求单次视频模型输出几十分钟。</p>
      <div className="explainer-actions" style={{ marginTop: 10 }}>
        {TARGET_MINUTES.map((minutes) => <span className="badge" key={minutes}>{minutes} 分钟</span>)}
        <span className="badge">自定义</span>
      </div>
      <p className="explainer-note">
        时长是编辑目标，不是已测语音时长。真实时长必须由本机配音实测后重新排列镜头；预估会明确标出是题目粗估、讲稿估算还是 TTS 后精估。
      </p>
    </div>
  </div>;
}
