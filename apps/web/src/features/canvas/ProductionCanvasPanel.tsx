import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Background, Controls, MiniMap, ReactFlow, applyNodeChanges, type Edge, type Node, type NodeChange, type ReactFlowInstance } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { getProductionCanvas, preflightProductionCanvasRun, saveProductionCanvasLayout } from "../../generated/api";

type CanvasFocus = "ALL" | "UPSTREAM" | "DOWNSTREAM";
const CANVAS_GRAPH_TIMEOUT_MS = 8_000;

function loadCanvasGraphWithTimeout(episodeId: string) {
  return new Promise<Awaited<ReturnType<typeof getProductionCanvas>>>((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error("业务画布读取超时，请重试")), CANVAS_GRAPH_TIMEOUT_MS);
    getProductionCanvas("EPISODE", episodeId, 0, 60).then(
      (value) => { window.clearTimeout(timer); resolve(value); },
      (error) => { window.clearTimeout(timer); reject(error); },
    );
  });
}
type CanvasNodeData = {
  label?: string;
  state?: string;
  blockers?: string[];
  thumbnailUrl?: string | null;
  takeCount?: number;
  variantCount?: number;
  logCount?: number;
  logs?: Array<{ event_id: number; type: string; occurred_at: string }>;
  variantLineage?: Array<Record<string, unknown>>;
  experimentProgress?: Array<Record<string, unknown>>;
  adjacentConstraints?: Array<Record<string, unknown>>;
};

export function canvasThumbnailUrl(mediaVersionId: string | null | undefined): string | null {
  return mediaVersionId ? `/api/v1/media-versions/${encodeURIComponent(mediaVersionId)}/thumbnail?size=small&frame=poster` : null;
}

export function focusCanvasNodeIds(selectedNodeId: string | null, edges: Array<{ source: string; target: string }>, focus: CanvasFocus): Set<string> {
  if (!selectedNodeId || focus === "ALL") return new Set();
  const adjacency = new Map<string, string[]>();
  edges.forEach((edge) => {
    const key = focus === "UPSTREAM" ? edge.target : edge.source;
    const value = focus === "UPSTREAM" ? edge.source : edge.target;
    adjacency.set(key, [...(adjacency.get(key) ?? []), value]);
  });
  const included = new Set<string>([selectedNodeId]);
  const queue = [selectedNodeId];
  while (queue.length > 0) {
    const current = queue.shift() as string;
    for (const neighbor of adjacency.get(current) ?? []) {
      if (included.has(neighbor)) continue;
      included.add(neighbor);
      queue.push(neighbor);
    }
  }
  return included;
}

export function ProductionCanvasPanel({ episodeId, selectedShotId, onSelectShot }: { episodeId: string | null; selectedShotId: string | null; onSelectShot: (shotId: string) => void }) {
  const graphQuery = useQuery({ queryKey: ["canvas", episodeId], queryFn: () => loadCanvasGraphWithTimeout(episodeId as string), enabled: Boolean(episodeId), retry: false });
  const [nodes, setNodes] = useState<Node[]>([]);
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance<Node> | null>(null);
  const [fitFeedback, setFitFeedback] = useState("");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [focus, setFocus] = useState<CanvasFocus>("ALL");
  const [plan, setPlan] = useState<{ status: string; node_ids: string[]; blockers: Array<Record<string, unknown>>; estimate: Record<string, unknown> } | null>(null);
  const graph = graphQuery.data?.graph;
  useEffect(() => {
    if (!graph) return;
    setNodes(graph.nodes.map((item, index) => ({
      id: item.id,
      position: item.position ?? { x: (index % 5) * 230, y: Math.floor(index / 5) * 145 },
      data: { label: item.label, state: item.state, blockers: item.blockers, thumbnailUrl: item.thumbnail_url ?? canvasThumbnailUrl(item.thumbnail_media_version_id), takeCount: item.take_count, variantCount: item.variant_count, logCount: item.log_count, logs: item.logs, variantLineage: item.variant_lineage, experimentProgress: item.experiment_progress, adjacentConstraints: item.adjacent_constraints },
      className: `canvas-node state-${item.state.toLowerCase()}`,
    })));
  }, [graph]);
  useEffect(() => {
    if (!selectedShotId || !graph) return;
    const directId = `shot:${selectedShotId}:direct`;
    if (graph.nodes.some((item) => item.id === directId)) setSelectedNodeId(directId);
  }, [graph, selectedShotId]);
  const allEdges = useMemo<Edge[]>(() => graph?.edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: "smoothstep",
    animated: edge.kind === "TRANSITION_CONSTRAINT",
    selectable: true,
    focusable: true,
    interactionWidth: 24,
    ariaLabel: `依赖关系 ${edge.kind}，从 ${edge.source} 到 ${edge.target}`,
    selected: edge.id === selectedEdgeId,
    className: `canvas-edge kind-${edge.kind.toLowerCase()}${edge.id === selectedEdgeId ? " is-selected" : ""}`,
  })) ?? [], [graph, selectedEdgeId]);
  const visibleNodeIds = useMemo(() => {
    const query = search.trim().toLowerCase();
    const focused = focusCanvasNodeIds(selectedNodeId, allEdges, focus);
    return new Set(nodes.filter((node) => {
      const data = node.data as CanvasNodeData;
      const matchesSearch = !query || [node.id, data.label, data.state, ...(data.blockers ?? [])].filter(Boolean).some((value) => String(value).toLowerCase().includes(query));
      return matchesSearch && (focus === "ALL" || focused.has(node.id));
    }).map((node) => node.id));
  }, [allEdges, focus, nodes, search, selectedNodeId]);
  const visibleNodes = useMemo(() => nodes.filter((node) => visibleNodeIds.has(node.id)), [nodes, visibleNodeIds]);
  const edges = useMemo(() => allEdges.filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target)), [allEdges, visibleNodeIds]);
  const selectedGraphNode = graph?.nodes.find((item) => item.id === selectedNodeId) ?? null;
  const selectedGraphEdge = graph?.edges.find((item) => item.id === selectedEdgeId) ?? null;
  const selectedShotIdFromNode = selectedNodeId?.match(/^shot:([^:]+):/)?.[1] ?? null;
  const selectedNodeThumbnail = (selectedGraphNode as (typeof selectedGraphNode & { thumbnail_url?: string | null }) | null)?.thumbnail_url ?? null;
  const selectedNodeLogs = (selectedGraphNode as (typeof selectedGraphNode & { logs?: Array<{ event_id: number; type: string; occurred_at: string }> }) | null)?.logs ?? [];
  const onNodesChange = useCallback((changes: NodeChange[]) => setNodes((items) => applyNodeChanges(changes, items)), []);
  const selectGraphEdge = useCallback((edgeId: string | undefined) => {
    const sourceEdge = graph?.edges.find((item) => item.id === edgeId);
    if (!sourceEdge) return;
    setSelectedEdgeId(sourceEdge.id);
    setSelectedNodeId(sourceEdge.target);
  }, [graph]);
  useEffect(() => {
    if (!flowInstance || visibleNodes.length === 0) return;
    const timer = window.requestAnimationFrame(() => {
      const initialReadableNodes = visibleNodes.length > 12 ? visibleNodes.slice(0, 8) : visibleNodes;
      void flowInstance.fitView({ nodes: initialReadableNodes, padding: 0.18, minZoom: 0.55, maxZoom: 0.9, duration: 220 });
    });
    return () => window.cancelAnimationFrame(timer);
  }, [flowInstance, focus, graph?.layout.revision, graph?.nodes.length, search]);
  const saveMutation = useMutation({
    mutationFn: () => saveProductionCanvasLayout("EPISODE", episodeId as string, { expected_revision: graph?.layout.revision || undefined, positions: Object.fromEntries(nodes.map((node) => [node.id, node.position])), groups: graph?.layout.groups, viewport: graph?.layout.viewport }),
    onSuccess: () => { void graphQuery.refetch(); },
  });
  const planMutation = useMutation({
    mutationFn: () => preflightProductionCanvasRun("EPISODE", episodeId as string, { mode: "NODE", from_node_id: selectedNodeId as string, max_nodes: 20 }),
    onSuccess: (data) => setPlan(data.plan),
  });
  if (!episodeId) return <section className="panel"><p className="empty-state">请先选择一个包含集的项目。</p></section>;
  if (graphQuery.isPending) return <section className="panel"><p className="empty-state">正在加载业务依赖图…</p></section>;
  if (!graph) return <section className="panel"><div className="workspace-error" role="alert"><div><strong>业务画布读取失败</strong><p>{graphQuery.error?.message ?? String(graphQuery.error)}</p></div><button type="button" className="secondary" onClick={() => { void graphQuery.refetch(); }} disabled={graphQuery.isFetching}>{graphQuery.isFetching ? "重试中…" : "重试业务画布"}</button></div></section>;
  const hasNodes = nodes.length > 0;

  return <section className="panel canvas-panel">
    <div className="panel-heading"><div><p className="eyebrow">生产画布</p><h3>业务依赖图 · 拖动只保存布局</h3></div><div className="canvas-actions"><button type="button" className="secondary" onClick={() => { setFitFeedback("正在适配全部节点…"); void flowInstance?.fitView({ padding: 0.2, duration: 250 }).finally(() => setFitFeedback("已将全部节点适配到当前视图")); }} disabled={!flowInstance || !hasNodes}>适配全部节点</button><button type="button" className="primary-action" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending || !hasNodes} title={!hasNodes ? "本集暂无节点可保存" : undefined}>{saveMutation.isPending ? "保存中…" : "保存布局"}</button><button type="button" className="secondary" onClick={() => planMutation.mutate()} disabled={!selectedNodeId || planMutation.isPending || !hasNodes} title={!hasNodes ? "本集暂无镜头可预检" : !selectedNodeId ? "先在画布或列表中选择一个节点" : planMutation.isPending ? "预检中…" : undefined}>{planMutation.isPending ? "预检中…" : "运行节点预检"}</button></div></div>
    {fitFeedback && <p className="muted" role="status" aria-live="polite">{fitFeedback}</p>}
    <p className="muted">节点显示状态、take、variant 和阻塞；edges 来自后端业务依赖，布局接口无法修改它们。当前页 {graph.page.returned_shots}/{graph.page.total_shots} 个镜头，最多显示 {graph.invariants.max_visible_nodes} 个节点。</p>
    {!hasNodes ? (
      <div className="empty-state" role="status">本集暂无镜头，请切换分集。</div>
    ) : (
      <>
        <div className="canvas-filterbar" aria-label="画布搜索与聚焦"><label>搜索节点<input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="镜头、状态或阻塞" /></label><div className="canvas-focus-actions"><button type="button" className={focus === "ALL" ? "active" : ""} onClick={() => setFocus("ALL")}>全部</button><button type="button" className={focus === "UPSTREAM" ? "active" : ""} onClick={() => setFocus("UPSTREAM")} disabled={!selectedNodeId}>聚焦上游</button><button type="button" className={focus === "DOWNSTREAM" ? "active" : ""} onClick={() => setFocus("DOWNSTREAM")} disabled={!selectedNodeId}>聚焦下游</button></div></div>
        <details className="canvas-node-browser">
          <summary>镜头节点列表 <span>{visibleNodes.length} 个</span></summary>
          <div className="canvas-node-list" aria-label="键盘节点列表">{visibleNodes.map((node) => { const data = node.data as CanvasNodeData; return <button type="button" key={`keyboard-${node.id}`} className={selectedNodeId === node.id ? "selected" : ""} onClick={() => setSelectedNodeId(node.id)}>{data.thumbnailUrl ? <img className="canvas-node-list-thumb" src={data.thumbnailUrl} alt="" width="64" height="36" loading="lazy" decoding="async" /> : <span className="canvas-node-list-thumb placeholder" aria-label="无预览">无预览</span>}<span>{String(data.label ?? node.id)}</span></button>; })}</div>
        </details>
        <div
          className="canvas-workspace"
          aria-label="业务画布"
          onClickCapture={(event) => {
            const edgeElement = (event.target as Element).closest<SVGGElement>(".react-flow__edge[data-id]");
            selectGraphEdge(edgeElement?.dataset.id);
          }}
          onKeyDownCapture={(event) => {
            if (event.key !== "Enter" && event.key !== " ") return;
            const edgeElement = (event.target as Element).closest<SVGGElement>(".react-flow__edge[data-id]");
            if (!edgeElement) return;
            event.preventDefault();
            selectGraphEdge(edgeElement.dataset.id);
          }}
        ><ReactFlow nodes={visibleNodes} edges={edges} onNodesChange={onNodesChange} onNodeClick={(_, node) => { setSelectedNodeId(node.id); setSelectedEdgeId(null); }} onInit={setFlowInstance} minZoom={0.25} maxZoom={1.8} nodesConnectable={false} edgesReconnectable={false} deleteKeyCode={null}><Background /><Controls showFitView={false} /><MiniMap pannable zoomable nodeColor={(node) => String(node.className).includes("blocked") ? "#df9d4b" : String(node.className).includes("running") ? "#7457b5" : "#2d7467"} /></ReactFlow></div>
        <div className="canvas-status"><span>节点：{selectedNodeId ?? "无"}</span><span>依赖边：{selectedEdgeId ?? "无"}</span><span>显示：{visibleNodes.length}/{nodes.length} 节点</span><span>布局 revision：{graph.layout.revision}</span><span>业务依赖可编辑：否</span>{plan && <strong>计划 {plan.status} · {plan.node_ids.length} 节点 · {plan.blockers.length} 阻塞</strong>}</div>
        {selectedGraphEdge && <div className="canvas-node-detail canvas-edge-detail" role="status" aria-label="已选依赖关系"><span><strong>依赖类型</strong> {selectedGraphEdge.kind}</span><span><strong>上游</strong> {selectedGraphEdge.source}</span><span><strong>下游</strong> {selectedGraphEdge.target}</span><span>依赖关系来自后端业务图，只读可选，不会被布局保存修改。</span></div>}
        {selectedShotIdFromNode && <button type="button" className="secondary" onClick={() => onSelectShot(selectedShotIdFromNode)}>在导演台打开所选镜头</button>}
        {selectedGraphNode && <div className="canvas-node-detail" aria-label="节点缩略图、谱系、边界约束与日志"><div className="canvas-node-preview">{selectedNodeThumbnail ? <img src={selectedNodeThumbnail} alt={`${selectedGraphNode.label} 小尺寸缩略图`} width="128" height="72" loading="lazy" decoding="async" /> : <span className="canvas-node-preview-empty">暂无派生缩略图</span>}<span><strong>take</strong> {selectedGraphNode.take_count} · <strong>日志</strong> {selectedNodeLogs.length} 条（最近）</span></div><span><strong>变体谱系</strong> {selectedGraphNode.variant_lineage.length} 个 · {selectedGraphNode.variant_lineage.map((item) => `v${item.variant_no} ${item.status}${item.is_stale ? " · stale" : ""}`).join(" / ") || "暂无真实变体"}</span><span><strong>实验进度</strong> {selectedGraphNode.experiment_progress.map((item) => `${item.title}: ${item.succeeded_count}/${item.expanded_count || item.cell_count} succeeded${item.failed_count ? ` · ${item.failed_count} failed` : ""}`).join(" / ") || "暂无真实实验"}</span><span><strong>相邻边界约束</strong> {selectedGraphNode.adjacent_constraints.map((item) => `${item.constraint_type} · ${item.compatibility_status} · ${item.enforcement}`).join(" / ") || "暂无真实边界约束"}</span><span><strong>执行日志</strong> {selectedNodeLogs.map((item) => `${item.type} · ${item.occurred_at}`).join(" / ") || "暂无任务日志"}</span></div>}
      </>
    )}
  </section>;
}
