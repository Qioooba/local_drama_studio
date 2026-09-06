import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import {
  listModelPlatformProjectKnowledgeIndexes,
  prepareModelPlatformProjectKnowledgeIndex,
  queueModelPlatformProjectKnowledgeIndex,
  searchModelPlatformProjectKnowledge,
  type ModelPlatformProjectKnowledgeIndex,
  type ModelPlatformProjectKnowledgeSearchHit,
} from "../model-platform-v2/api";

const STATUS: Record<string, string> = {
  PREPARED: "等待入队",
  QUEUING: "正在入队",
  QUEUED: "后台处理中",
  QUEUE_FAILED: "入队未完成",
  SUCCEEDED: "索引已就绪",
};

function latestForSource(items: ModelPlatformProjectKnowledgeIndex[], sourceDocumentVersionId: string) {
  return items.find((item) => item.source_document_version_id === sourceDocumentVersionId) ?? null;
}

/** A deliberately small, source-bound surface for the first V2 business vertical. */
export function ProjectKnowledgeIndexPanel({ projectId, sourceDocumentVersionId }: { projectId: string; sourceDocumentVersionId: string }) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<"prepare" | "queue" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [hits, setHits] = useState<ModelPlatformProjectKnowledgeSearchHit[] | null>(null);
  const indexes = useQuery({
    queryKey: ["model-platform", "project-knowledge-indexes", projectId],
    queryFn: () => listModelPlatformProjectKnowledgeIndexes(projectId),
    enabled: Boolean(projectId),
    refetchInterval: (query) => query.state.data?.items.some((item) => item.status === "QUEUED" || item.status === "QUEUING") ? 3_000 : false,
  });
  const index = useMemo(() => latestForSource(indexes.data?.items ?? [], sourceDocumentVersionId), [indexes.data?.items, sourceDocumentVersionId]);
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["model-platform", "project-knowledge-indexes", projectId] });
  };
  const prepare = async () => {
    setPending("prepare"); setError(null); setNotice(null);
    try {
      const result = await prepareModelPlatformProjectKnowledgeIndex(projectId, sourceDocumentVersionId);
      setNotice(result.index.reused ? "该文档版本已经有同一 V2 Profile 的索引记录，已恢复查看。" : `已创建第 ${result.index.attempt_no} 次索引尝试，冻结 ${result.index.chunk_count} 个文本块，尚未提交模型任务。`);
      await refresh();
    } catch (reason) {
      setError(`无法准备项目知识索引：${String(reason)}`);
    } finally { setPending(null); }
  };
  const queue = async () => {
    if (!index) return;
    setPending("queue"); setError(null); setNotice(null);
    try {
      const result = await queueModelPlatformProjectKnowledgeIndex(index.id);
      setNotice(`已提交 ${result.index.queued_batch_count} 个受控 Embedding 批次。可以离开此页，任务将继续在后台执行。`);
      await refresh();
    } catch (reason) {
      setError(`无法提交项目知识索引：${String(reason)}`);
    } finally { setPending(null); }
  };
  const search = async () => {
    const text = query.trim();
    if (!text) return;
    setSearching(true); setError(null);
    try {
      const result = await searchModelPlatformProjectKnowledge(projectId, text, 5);
      setHits(result.search.items);
    } catch (reason) {
      setError(`无法检索项目知识库：${String(reason)}`);
    } finally { setSearching(false); }
  };
  const status = index ? STATUS[index.status] ?? index.status : "尚未建立";
  return <section className="subpanel project-knowledge-index-panel" aria-labelledby="project-knowledge-index-title">
    <div className="section-title">
      <span id="project-knowledge-index-title">项目知识索引</span>
      <small>本机 Embedding · 受控后台任务</small>
    </div>
    <p className="muted">索引固定绑定当前已提交的文档版本。它不使用 Ollama 对话模型，也不会上传原文、暴露模型路径，或改变故事拆解的模型选择。</p>
    <div className="project-knowledge-index-status" role="status">
      <div><span>当前状态</span><strong>{status}</strong></div>
      {index && <div><span>进度</span><strong>第 {index.attempt_no} 次 · {index.completed_batch_count} / {index.batch_count} 批次</strong></div>}
    </div>
    {!index && <button type="button" className="secondary" disabled={pending !== null || indexes.isPending} onClick={() => { void prepare(); }}>{pending === "prepare" ? "正在冻结文本清单…" : "准备知识索引"}</button>}
    {index?.status === "PREPARED" && <button type="button" className="secondary" disabled={pending !== null} onClick={() => { void queue(); }}>{pending === "queue" ? "正在创建后台任务…" : "提交本机 Embedding"}</button>}
    {(index?.status === "QUEUE_FAILED" || index?.status === "FAILED") && <><p className="inline-error" role="alert">第 {index.attempt_no} 次索引未完成（{index.failure_code ?? "未知原因"}）。为避免混合数据，不能从这里直接续传。</p><button type="button" className="secondary" disabled={pending !== null} onClick={() => { void prepare(); }}>创建新的索引尝试</button></>}
    {indexes.error && <p className="inline-error" role="alert">无法读取索引状态：{String(indexes.error)}</p>}
    {error && <p className="inline-error" role="alert">{error}</p>}
    {notice && <p className="frame-feedback success" role="status">{notice}</p>}
    {index?.status === "SUCCEEDED" && <form className="project-knowledge-search" onSubmit={(event) => { event.preventDefault(); void search(); }}>
      <label>
        验证项目知识检索
        <input value={query} maxLength={8192} placeholder="输入一个与当前文档有关的问题" onChange={(event) => setQuery(event.target.value)} />
      </label>
      <button type="submit" className="secondary" disabled={searching || !query.trim()}>{searching ? "正在本机检索…" : "检索"}</button>
      {hits !== null && <ol className="project-knowledge-search-hits" aria-label="知识检索命中">
        {hits.length ? hits.map((hit) => <li key={`${hit.index_run_id}:${hit.ordinal}`}><strong>命中第 {hit.ordinal} 段</strong><p>{hit.excerpt}</p><span>相似度 {hit.score.toFixed(3)} · 原文偏移 {hit.source_start}–{hit.source_end}</span></li>) : <li>没有返回命中。</li>}
      </ol>}
    </form>}
    <small className="muted">Embedding Profile 由系统/项目模型配置控制；没有可执行 Profile 时，准备动作会安全拒绝。<Link to={routes.systemCapabilities()}>打开模型中心</Link></small>
  </section>;
}
