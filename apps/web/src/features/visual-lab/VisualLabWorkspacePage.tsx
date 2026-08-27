import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Link, useParams } from "react-router-dom";
import { ChevronRightIcon, StudioIcon } from "../../components/icons";
import {
  createVisualLabEdge,
  createVisualLabNode,
  deleteVisualLabNodes,
  duplicateVisualLabNodes,
  getVisualLab,
  listVisualLabSnapshots,
  moveVisualLabNodes,
  preflightVisualLabPromotion,
  preflightVisualLabRun,
  promoteVisualLabCandidate,
  restoreVisualLabSnapshot,
  reviseVisualLabNode,
  runVisualLabNode,
  saveVisualLabViewport,
  snapshotVisualLab,
  type VisualLabNode,
  type VisualLabNodeKind,
  type VisualLabSnapshot,
} from "./client";
import { VisualLabNodeCard } from "./VisualLabNodeCard";
import { listEpisodeProductionShotsV2 } from "../../generated/api";
import { MediaPicker } from "../media-picker/MediaPicker";
import "./visual-lab.css";

const nodeTypes = { visualLab: VisualLabNodeCard };

const palette: Array<{ kind: VisualLabNodeKind; label: string; hint: string }> = [
  { kind: "MEDIA_REF", label: "媒体参考", hint: "绑定已登记媒体" },
  { kind: "STORY_ASSET_REF", label: "故事资产", hint: "角色或场景参考" },
  { kind: "SHOT_REF", label: "镜头引用", hint: "引用正式镜头上下文" },
  { kind: "TEXT_REF", label: "文字参考", hint: "提示词与创作方向" },
  { kind: "GENERATION_INTENT", label: "生成意图", hint: "调用已发布生产能力" },
  { kind: "TRANSFORM_INTENT", label: "转换意图", hint: "编辑、重绘与变换" },
  { kind: "COMPARE_SET", label: "候选比较", hint: "并排评估多个结果" },
  { kind: "SEQUENCE_PREVIEW", label: "序列预览", hint: "组合预览，不写时间线" },
  { kind: "OUTPUT_DRAFT", label: "输出草稿", hint: "待采纳的实验结果" },
  { kind: "NOTE", label: "便签", hint: "记录判断与待办" },
];

type HistoryState = { undo: VisualLabSnapshot[]; redo: VisualLabSnapshot[] };
type CreateNodeInput = { kind: VisualLabNodeKind; position?: { x: number; y: number }; content?: Record<string, unknown>; size?: { width: number; height: number }; zIndex?: number };

function isTextInput(target: EventTarget | null): boolean {
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement || (target instanceof HTMLElement && target.isContentEditable);
}

function nodeTitle(node: VisualLabNode): string {
  return String(node.content.title || palette.find((item) => item.kind === node.node_kind)?.label || node.node_kind);
}

function StructuredFields({ nodeKind, content, onChange }: { nodeKind: VisualLabNodeKind; content: Record<string, unknown>; onChange: (patch: Record<string, unknown>) => void }) {
  const text = (key: string, label: string, rows = 1) => (
    <label key={key}>{label}{rows > 1
      ? <textarea rows={rows} spellCheck={false} value={String(content[key] ?? "")} onChange={(event) => onChange({ [key]: event.target.value })} />
      : <input value={String(content[key] ?? "")} onChange={(event) => onChange({ [key]: event.target.value })} />}</label>
  );
  if (nodeKind === "MEDIA_REF") return <>{text("media_version_id", "媒体版本 ID")}{text("title", "标题")}</>;
  if (nodeKind === "STORY_ASSET_REF") return <>{text("reference_id", "资产 ID")}{text("title", "标题")}</>;
  if (nodeKind === "SHOT_REF") return <>{text("reference_id", "镜头 ID")}{text("title", "标题")}</>;
  if (nodeKind === "GENERATION_INTENT") return <>{text("intent_id", "生成意图 ID")}{text("variant_plan", "variant_plan (JSON)", 4)}</>;
  if (nodeKind === "TEXT_REF" || nodeKind === "NOTE") return <>{text("title", "标题")}{text("body", "正文", 5)}</>;
  return <>{text("body", "正文", 8)}</>;
}

export function VisualLabWorkspacePage() {
  const { projectId = "", labId = "" } = useParams();
  const queryClient = useQueryClient();
  const flowRef = useRef<ReactFlowInstance<Node, Edge> | null>(null);
  const canvasRef = useRef<HTMLElement | null>(null);
  const historyRef = useRef<HistoryState>({ undo: [], redo: [] });
  const dragCheckpointRef = useRef<Promise<VisualLabSnapshot | null> | null>(null);
  const selectedIdsRef = useRef<string[]>([]);
  const graph = useQuery({ queryKey: ["visual-lab", labId], queryFn: () => getVisualLab(labId), enabled: Boolean(labId), retry: false });
  const snapshots = useQuery({ queryKey: ["visual-lab-snapshots", labId], queryFn: () => listVisualLabSnapshots(labId), enabled: Boolean(labId), retry: false });
  const episodeId = graph.data?.document.episode_id ?? null;
  const shots = useQuery({ queryKey: ["visual-lab-shots", episodeId], queryFn: () => listEpisodeProductionShotsV2(episodeId as string), enabled: Boolean(episodeId), retry: false });
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [createKind, setCreateKind] = useState<VisualLabNodeKind | null>(null);
  const [referenceId, setReferenceId] = useState("");
  const [newTitle, setNewTitle] = useState("");
  const [draft, setDraft] = useState("");
  const [advancedJson, setAdvancedJson] = useState(false);
  const [promotionMediaId, setPromotionMediaId] = useState("");
  const [promotionKind, setPromotionKind] = useState<"IMAGE" | "VIDEO">("IMAGE");
  const [promotionShotId, setPromotionShotId] = useState("");
  const [feedback, setFeedback] = useState("");
  const [zoom, setZoom] = useState(1);
  const [, setHistoryVersion] = useState(0);
  selectedIdsRef.current = selectedIds;

  const refresh = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["visual-lab", labId] }),
      queryClient.invalidateQueries({ queryKey: ["visual-lab-snapshots", labId] }),
    ]);
  }, [labId, queryClient]);

  const selected = graph.data?.nodes.find((item) => item.id === selectedIds[0]) ?? null;
  const selectedFlowNodes = nodes.filter((item) => selectedIds.includes(item.id));

  const clearSelection = useCallback(() => {
    selectedIdsRef.current = [];
    setSelectedIds([]);
    setNodes((current) => current.map((node) => node.selected || node.data.selectedByApp
      ? { ...node, selected: false, data: { ...node.data, selectedByApp: false } }
      : node));
  }, []);

  const checkpoint = useCallback(async (): Promise<VisualLabSnapshot | null> => {
    if (!labId) return null;
    const { snapshot } = await snapshotVisualLab(labId);
    historyRef.current.undo.push(snapshot);
    historyRef.current.undo = historyRef.current.undo.slice(-40);
    historyRef.current.redo = [];
    setHistoryVersion((value) => value + 1);
    return snapshot;
  }, [labId]);

  const persistLayout = useCallback(async (changedNodes: Node[]) => {
    if (!graph.data || !changedNodes.length) return;
    const payload = changedNodes.flatMap((node) => {
      const source = graph.data.nodes.find((item) => item.id === node.id);
      if (!source) return [];
      return [{
        id: node.id,
        position_x: node.position.x,
        position_y: node.position.y,
        width: node.measured?.width ?? node.width ?? source.width,
        height: node.measured?.height ?? node.height ?? source.height,
        z_index: source.z_index,
        collapsed: Boolean(source.collapsed),
        expected_revision: source.revision,
      }];
    });
    if (payload.length) await moveVisualLabNodes(labId, payload);
  }, [graph.data, labId]);

  const toFlowNode = useCallback((source: VisualLabNode): Node => ({
    id: source.id,
    type: "visualLab",
    position: { x: source.position_x, y: source.position_y },
    style: { width: source.width, height: source.height },
    zIndex: source.node_kind === "FRAME" ? -10 : source.z_index,
    selected: selectedIdsRef.current.includes(source.id),
    data: {
      source,
      selectedByApp: selectedIdsRef.current.includes(source.id),
      onResize: (nodeId: string, width: number, height: number) => {
        const current = flowRef.current?.getNode(nodeId);
        if (!current) return;
        void checkpoint().then(() => persistLayout([{ ...current, width, height, measured: { width, height } }])).then(refresh).catch((error: Error) => setFeedback(error.message));
      },
    },
  }), [checkpoint, persistLayout, refresh]);

  useEffect(() => {
    if (!graph.data) return;
    setNodes(graph.data.nodes.map(toFlowNode));
    setEdges(graph.data.edges.map((item) => ({
      id: item.id,
      source: item.source_node_id,
      sourceHandle: item.source_port,
      target: item.target_node_id,
      targetHandle: item.target_port,
      label: item.edge_kind,
      type: "smoothstep",
    })));
  }, [graph.data, toFlowNode]);

  useEffect(() => {
    if (selected) setDraft(JSON.stringify(selected.content, null, 2));
  }, [selected?.content_hash, selected?.id]);

  const viewportCenter = useCallback(() => {
    const element = canvasRef.current;
    const flow = flowRef.current;
    if (!element || !flow) return { x: 120, y: 120 };
    const bounds = element.getBoundingClientRect();
    return flow.screenToFlowPosition({ x: bounds.left + bounds.width / 2, y: bounds.top + bounds.height / 2 }, { snapToGrid: true });
  }, []);

  const create = useMutation({
    mutationFn: async ({ kind, position, content, size, zIndex }: CreateNodeInput) => {
      await checkpoint();
      const needsReference = ["MEDIA_REF", "STORY_ASSET_REF", "SHOT_REF"].includes(kind);
      const needsIntent = kind === "GENERATION_INTENT";
      const nodeContent: Record<string, unknown> = content ?? { title: newTitle || palette.find((item) => item.kind === kind)?.label || kind };
      if (needsReference) nodeContent.reference_id = referenceId;
      if (needsIntent) { nodeContent.intent_id = referenceId; nodeContent.variant_plan = {}; }
      const point = position ?? viewportCenter();
      return createVisualLabNode(labId, {
        node_kind: kind,
        position_x: point.x,
        position_y: point.y,
        width: size?.width ?? 280,
        height: size?.height ?? 180,
        z_index: zIndex,
        content: nodeContent,
        expected_topology_revision: graph.data?.document.topology_revision,
      });
    },
    onSuccess: ({ node }) => {
      setSelectedIds([node.id]);
      setCreateKind(null);
      setReferenceId("");
      setNewTitle("");
      setPaletteOpen(false);
      void refresh();
    },
  });

  const save = useMutation({
    mutationFn: async () => {
      await checkpoint();
      return reviseVisualLabNode(selected!.id, { content: JSON.parse(draft) as Record<string, unknown>, change_note: "Visual Lab 检视器更新", expected_revision: selected!.revision });
    },
    onSuccess: () => { setFeedback("节点已保存为新内容修订"); void refresh(); },
  });

  const connect = useMutation({
    mutationFn: async (connection: Connection) => {
      await checkpoint();
      return createVisualLabEdge(labId, {
        source_node_id: connection.source,
        source_port: connection.sourceHandle,
        target_node_id: connection.target,
        target_port: connection.targetHandle,
        edge_kind: "DERIVES",
        metadata: {},
        expected_topology_revision: graph.data?.document.topology_revision,
      });
    },
    onSuccess: () => void refresh(),
    onError: () => void refresh(),
  });

  const duplicate = useMutation({
    mutationFn: async (ids: string[]) => {
      await checkpoint();
      return duplicateVisualLabNodes(labId, ids, graph.data!.document.topology_revision);
    },
    onSuccess: ({ nodes: created }) => { setSelectedIds(created.map((item) => item.id)); setFeedback(`已复制 ${created.length} 个节点`); void refresh(); },
  });

  const remove = useMutation({
    mutationFn: async (ids: string[]) => {
      await checkpoint();
      return deleteVisualLabNodes(labId, ids, graph.data!.document.topology_revision);
    },
    onSuccess: (_, ids) => { setSelectedIds([]); setFeedback(`已删除 ${ids.length} 个节点，可撤销`); void refresh(); },
  });

  const restore = useMutation({
    mutationFn: async ({ snapshot, direction }: { snapshot: VisualLabSnapshot; direction: "UNDO" | "REDO" | "HISTORY" }) => {
      const current = (await snapshotVisualLab(labId)).snapshot;
      const result = await restoreVisualLabSnapshot(snapshot.id);
      return { result, current, snapshot, direction };
    },
    onSuccess: ({ current, snapshot, direction, result }) => {
      if (direction === "UNDO") historyRef.current.redo.push(current);
      if (direction === "REDO") historyRef.current.undo.push(current);
      if (direction === "HISTORY") { historyRef.current.undo.push(current); historyRef.current.redo = []; }
      setHistoryVersion((value) => value + 1);
      setSelectedIds([]);
      const viewport = result.graph.document.viewport;
      void flowRef.current?.setViewport(viewport, { duration: 180 });
      setFeedback(`已恢复快照 #${snapshot.snapshot_no}`);
      void refresh();
    },
  });

  const undo = useCallback(() => {
    if (restore.isPending) return;
    const snapshot = historyRef.current.undo.pop();
    if (!snapshot) { setFeedback("当前会话没有可撤销操作"); return; }
    setHistoryVersion((value) => value + 1);
    restore.mutate({ snapshot, direction: "UNDO" });
  }, [restore]);

  const redo = useCallback(() => {
    if (restore.isPending) return;
    const snapshot = historyRef.current.redo.pop();
    if (!snapshot) { setFeedback("当前会话没有可重做操作"); return; }
    setHistoryVersion((value) => value + 1);
    restore.mutate({ snapshot, direction: "REDO" });
  }, [restore]);

  const preflight = useMutation({ mutationFn: () => preflightVisualLabRun(selected!.id), onSuccess: ({ plan }) => setFeedback(plan.status === "READY" ? `预检通过 · ${plan.plan_hash.slice(0, 10)}` : `预检阻塞：${plan.blockers.map((item) => item.message).join("；")}`) });
  const run = useMutation({ mutationFn: async () => { const { plan } = await preflightVisualLabRun(selected!.id); if (plan.status !== "READY") throw new Error(plan.blockers.map((item) => item.message).join("；")); return runVisualLabNode(selected!.id, plan.plan_hash); }, onSuccess: () => setFeedback("生成任务已进入持久队列") });
  const promote = useMutation({ mutationFn: async () => { const { plan } = await preflightVisualLabPromotion(selected!.id, promotionMediaId, promotionShotId); if (plan.status !== "READY") throw new Error(plan.blockers.map((item) => item.message).join("；")); return promoteVisualLabCandidate(selected!.id, promotionMediaId, promotionShotId, plan.plan_hash); }, onSuccess: ({ promotion }) => { setFeedback(`已采纳为镜头候选；采纳 ${promotion.id.slice(0, 8)}，仍需人工审核批准`); setPromotionMediaId(""); } });

  const createFrame = useCallback(() => {
    const members = selectedFlowNodes.filter((node) => graph.data?.nodes.find((item) => item.id === node.id)?.node_kind !== "FRAME");
    if (!members.length) { setFeedback("先框选要归入区域的节点"); return; }
    const minX = Math.min(...members.map((node) => node.position.x)) - 56;
    const minY = Math.min(...members.map((node) => node.position.y)) - 72;
    const maxX = Math.max(...members.map((node) => node.position.x + (node.measured?.width ?? node.width ?? 280))) + 56;
    const maxY = Math.max(...members.map((node) => node.position.y + (node.measured?.height ?? node.height ?? 180))) + 56;
    create.mutate({ kind: "FRAME", position: { x: minX, y: minY }, size: { width: maxX - minX, height: maxY - minY }, zIndex: -10, content: { title: "创作区域", member_ids: members.map((node) => node.id) } });
  }, [create, graph.data?.nodes, selectedFlowNodes]);

  const arrangeSelection = useCallback(async (mode: "LEFT" | "TOP" | "HORIZONTAL") => {
    if (selectedFlowNodes.length < 2) return;
    let arranged = [...selectedFlowNodes];
    if (mode === "LEFT") {
      const x = Math.min(...arranged.map((node) => node.position.x));
      arranged = arranged.map((node) => ({ ...node, position: { ...node.position, x } }));
    } else if (mode === "TOP") {
      const y = Math.min(...arranged.map((node) => node.position.y));
      arranged = arranged.map((node) => ({ ...node, position: { ...node.position, y } }));
    } else {
      arranged.sort((a, b) => a.position.x - b.position.x);
      const start = arranged[0].position.x;
      const end = arranged[arranged.length - 1].position.x;
      const step = arranged.length > 1 ? (end - start) / (arranged.length - 1) : 0;
      arranged = arranged.map((node, index) => ({ ...node, position: { ...node.position, x: start + index * step } }));
    }
    try {
      await checkpoint();
      const positions = new Map(arranged.map((node) => [node.id, node.position]));
      setNodes((current) => current.map((node) => positions.has(node.id) ? { ...node, position: positions.get(node.id)! } : node));
      await persistLayout(arranged);
      setFeedback(mode === "LEFT" ? "已左对齐" : mode === "TOP" ? "已顶对齐" : "已水平分布");
      await refresh();
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "排列节点失败");
      await refresh();
    }
  }, [checkpoint, persistLayout, refresh, selectedFlowNodes]);

  const toggleCollapsed = useCallback(async () => {
    if (!selected || !flowRef.current) return;
    const flowNode = flowRef.current.getNode(selected.id);
    if (!flowNode) return;
    try {
      await checkpoint();
      await moveVisualLabNodes(labId, [{
        id: selected.id,
        position_x: flowNode.position.x,
        position_y: flowNode.position.y,
        width: flowNode.measured?.width ?? flowNode.width ?? selected.width,
        height: flowNode.measured?.height ?? flowNode.height ?? selected.height,
        z_index: selected.z_index,
        collapsed: !selected.collapsed,
        expected_revision: selected.revision,
      }]);
      setFeedback(selected.collapsed ? "已展开节点" : "已折叠节点");
      await refresh();
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "节点状态保存失败");
      await refresh();
    }
  }, [checkpoint, labId, refresh, selected]);

  const focusNodes = useCallback((ids: string[]) => {
    const targets = nodes.filter((node) => ids.includes(node.id));
    if (!targets.length) return;
    setSelectedIds(targets.map((node) => node.id));
    setNodes((current) => current.map((node) => ({ ...node, selected: ids.includes(node.id) })));
    void flowRef.current?.fitView({ nodes: targets, padding: 0.35, minZoom: 0.45, maxZoom: 1.25, duration: 220 });
  }, [nodes]);

  const onNodesChange = useCallback((changes: NodeChange[]) => setNodes((current) => applyNodeChanges(changes, current)), []);
  const onEdgesChange = useCallback((changes: EdgeChange[]) => setEdges((current) => applyEdgeChanges(changes, current)), []);
  const onConnect = useCallback((connection: Connection) => {
    if (!connection.source || !connection.target || !connection.sourceHandle || !connection.targetHandle) return;
    setEdges((current) => addEdge({ ...connection, type: "smoothstep" }, current));
    connect.mutate(connection);
  }, [connect]);

  const onDragStart = useCallback(() => { dragCheckpointRef.current = checkpoint(); }, [checkpoint]);
  const onDragStop = useCallback(async (_: unknown, moved: Node) => {
    try {
      await dragCheckpointRef.current;
      let changed = flowRef.current?.getNodes().filter((node) => selectedIds.includes(node.id)) ?? [moved];
      const source = graph.data?.nodes.find((item) => item.id === moved.id);
      if (source?.node_kind === "FRAME") {
        const deltaX = moved.position.x - source.position_x;
        const deltaY = moved.position.y - source.position_y;
        const members = Array.isArray(source.content.member_ids) ? source.content.member_ids.map(String) : [];
        setNodes((current) => current.map((node) => members.includes(node.id) ? { ...node, position: { x: node.position.x + deltaX, y: node.position.y + deltaY } } : node));
        changed = [...changed, ...(flowRef.current?.getNodes().filter((node) => members.includes(node.id)).map((node) => ({ ...node, position: { x: node.position.x + deltaX, y: node.position.y + deltaY } })) ?? [])];
      }
      await persistLayout(Array.from(new Map(changed.map((node) => [node.id, node])).values()));
      await refresh();
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "节点位置保存失败");
      await refresh();
    } finally {
      dragCheckpointRef.current = null;
    }
  }, [graph.data?.nodes, persistLayout, refresh, selectedIds]);

  const handleDrop = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    const raw = event.dataTransfer.getData("application/x-localdrama-media-version") || event.dataTransfer.getData("text/plain");
    const parsed = raw.startsWith("media-version:") ? { media_version_id: raw.slice("media-version:".length), title: "媒体参考" } : (() => { try { return JSON.parse(raw) as { media_version_id?: string; title?: string }; } catch { return {}; } })();
    if (!parsed.media_version_id) { setFeedback("请从媒体库拖入已登记的媒体版本；本地文件需先导入媒体库"); return; }
    const position = flowRef.current?.screenToFlowPosition({ x: event.clientX, y: event.clientY }, { snapToGrid: true });
    create.mutate({ kind: "MEDIA_REF", position, content: { title: parsed.title || "媒体参考", reference_id: parsed.media_version_id } });
  }, [create]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (isTextInput(event.target)) return;
      const command = event.ctrlKey || event.metaKey;
      if (command && event.key.toLowerCase() === "f") { event.preventDefault(); setSearchOpen(true); return; }
      if (command && event.key.toLowerCase() === "z" && event.shiftKey) { event.preventDefault(); redo(); return; }
      if (command && event.key.toLowerCase() === "z") { event.preventDefault(); undo(); return; }
      if (command && event.key.toLowerCase() === "d" && selectedIds.length) { event.preventDefault(); duplicate.mutate(selectedIds); return; }
      if (command && event.key.toLowerCase() === "c" && selectedIds.length) { event.preventDefault(); localStorage.setItem("visual-lab-clipboard", JSON.stringify({ labId, nodeIds: selectedIds })); setFeedback(`已复制 ${selectedIds.length} 个节点`); return; }
      if (command && event.key.toLowerCase() === "v") {
        event.preventDefault();
        try {
          const clipboard = JSON.parse(localStorage.getItem("visual-lab-clipboard") || "{}") as { labId?: string; nodeIds?: string[] };
          if (clipboard.labId === labId && clipboard.nodeIds?.length) duplicate.mutate(clipboard.nodeIds);
          else setFeedback("当前剪贴板没有本画布可粘贴的节点");
        } catch { setFeedback("画布剪贴板内容无效"); }
        return;
      }
      if ((event.key === "Delete" || event.key === "Backspace") && selectedIds.length) { event.preventDefault(); remove.mutate(selectedIds); return; }
      if (event.key === "Escape") { setPaletteOpen(false); setHistoryOpen(false); setSearchOpen(false); setShortcutsOpen(false); clearSelection(); }
      if (event.key === "?" && !command) setShortcutsOpen((value) => !value);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [clearSelection, duplicate, labId, redo, remove, selectedIds, undo]);

  const searchResults = useMemo(() => {
    const term = search.trim().toLocaleLowerCase();
    if (!term || !graph.data) return [];
    return graph.data.nodes.filter((node) => `${nodeTitle(node)} ${node.node_kind} ${node.content.body ?? ""}`.toLocaleLowerCase().includes(term)).slice(0, 20);
  }, [graph.data, search]);
  const inspectorError = useMemo(() => { try { JSON.parse(draft); return ""; } catch (error) { return error instanceof Error ? error.message : "JSON 无效"; } }, [draft]);
  const contentObj = useMemo<Record<string, unknown>>(() => { try { return JSON.parse(draft) as Record<string, unknown>; } catch { return {}; } }, [draft]);
  const patchContent = useCallback((patch: Record<string, unknown>) => { setDraft(JSON.stringify({ ...contentObj, ...patch }, null, 2)); }, [contentObj]);
  const executable = selected?.node_kind === "GENERATION_INTENT" || selected?.node_kind === "TRANSFORM_INTENT";
  const title = graph.data?.document.title ?? "Visual Lab";
  const mutationError = create.error || save.error || connect.error || duplicate.error || remove.error || restore.error || preflight.error || run.error || promote.error;

  if (graph.isPending) return <main className="v2-page"><section className="empty-state">正在打开 Visual Lab…</section></main>;
  if (graph.error || !graph.data) return <main className="v2-page"><section className="workspace-error" role="alert"><div><strong>Visual Lab 打开失败</strong><p>{graph.error?.message ?? "画布不存在"}</p></div></section></main>;

  return (
    <main className="visual-lab-workspace">
      <header className="visual-lab-toolbar">
        <div className="visual-lab-toolbar__identity">
          <Link to={`/projects/${projectId}/labs`} aria-label="返回 Visual Lab 列表"><ChevronRightIcon /></Link>
          <div><p className="eyebrow">Visual Lab · 无限画布</p><h2 tabIndex={-1}>{title}</h2></div>
        </div>
        <div className="visual-lab-toolbar__actions">
          <button type="button" className="secondary" onClick={() => setSearchOpen(true)}><StudioIcon name="search" />查找</button>
          <button type="button" className="secondary" onClick={() => setPaletteOpen((value) => !value)}>添加节点</button>
          <button type="button" className="secondary" onClick={createFrame} disabled={!selectedIds.length}>建立区域</button>
          <button type="button" className="icon-button" aria-label="撤销" title="撤销 Ctrl+Z" onClick={undo} disabled={!historyRef.current.undo.length || restore.isPending}><StudioIcon name="undo" /></button>
          <button type="button" className="icon-button" aria-label="重做" title="重做 Ctrl+Shift+Z" onClick={redo} disabled={!historyRef.current.redo.length || restore.isPending}><StudioIcon name="redo" /></button>
          <button type="button" className="secondary" onClick={() => setHistoryOpen((value) => !value)}>历史</button>
          <button type="button" className="icon-button" aria-label="查看快捷键" onClick={() => setShortcutsOpen((value) => !value)}>?</button>
          <Link className="secondary v2-inline-link" to={graph.data.document.episode_id ? `/projects/${projectId}/episodes/${graph.data.document.episode_id}/production` : `/projects/${projectId}`}>正式生产</Link>
        </div>
      </header>

      {feedback && <div className="visual-lab-feedback" role="status" aria-live="polite">{feedback}<button type="button" aria-label="关闭消息" onClick={() => setFeedback("")}><StudioIcon name="close" /></button></div>}
      {mutationError && <div className="visual-lab-feedback is-error" role="alert">{mutationError.message}<button type="button" aria-label="关闭错误" onClick={() => void refresh()}><StudioIcon name="close" /></button></div>}

      {paletteOpen && <aside className="visual-lab-palette" aria-label="节点面板"><header><strong>添加创作对象</strong><button type="button" onClick={() => setPaletteOpen(false)} aria-label="关闭节点面板"><StudioIcon name="close" /></button></header>{palette.map((item) => <button key={item.kind} type="button" onClick={() => { if (["MEDIA_REF", "STORY_ASSET_REF", "SHOT_REF", "GENERATION_INTENT"].includes(item.kind)) setCreateKind(item.kind); else create.mutate({ kind: item.kind }); }}><strong>{item.label}</strong><small>{item.hint}</small></button>)}</aside>}

      {historyOpen && <aside className="visual-lab-history" aria-label="画布历史"><header><div><strong>快照历史</strong><small>恢复前会自动保存当前状态</small></div><button type="button" aria-label="关闭历史" onClick={() => setHistoryOpen(false)}><StudioIcon name="close" /></button></header><button className="primary-action" type="button" onClick={() => snapshotVisualLab(labId).then(({ snapshot }) => { setFeedback(`已创建快照 #${snapshot.snapshot_no}`); void refresh(); })}>创建命名时点</button><ol>{snapshots.data?.items.map((item) => <li key={item.id}><button type="button" onClick={() => restore.mutate({ snapshot: item, direction: "HISTORY" })}><span>快照 #{item.snapshot_no}</span><small>{new Date(item.created_at).toLocaleString()}</small></button></li>)}</ol></aside>}

      {searchOpen && <section className="visual-lab-search" role="dialog" aria-modal="false" aria-label="查找画布节点"><label><StudioIcon name="search" /><span className="sr-only">搜索节点</span><input autoFocus value={search} onChange={(event) => setSearch(event.target.value)} placeholder="输入标题、类型或正文…" /><kbd>Esc</kbd></label>{search && <ol>{searchResults.length ? searchResults.map((item) => <li key={item.id}><button type="button" onClick={() => { focusNodes([item.id]); setSearchOpen(false); }}><strong>{nodeTitle(item)}</strong><small>{item.node_kind}</small></button></li>) : <li className="empty">没有匹配节点</li>}</ol>}</section>}

      {shortcutsOpen && <aside className="visual-lab-shortcuts" aria-label="画布快捷键"><header><strong>快捷键</strong><button type="button" aria-label="关闭快捷键" onClick={() => setShortcutsOpen(false)}><StudioIcon name="close" /></button></header><dl><dt><kbd>拖动空白</kbd></dt><dd>平移画布</dd><dt><kbd>滚轮</kbd></dt><dd>缩放画布</dd><dt><kbd>Shift + 拖动</kbd></dt><dd>框选节点</dd><dt><kbd>Ctrl/⌘ + C / V</kbd></dt><dd>复制 / 粘贴</dd><dt><kbd>Ctrl/⌘ + D</kbd></dt><dd>快速复制</dd><dt><kbd>Ctrl/⌘ + Z</kbd></dt><dd>撤销</dd><dt><kbd>Delete</kbd></dt><dd>删除，可撤销</dd><dt><kbd>Ctrl/⌘ + F</kbd></dt><dd>查找定位</dd></dl></aside>}

      {createKind && <div className="visual-lab-modal-backdrop" role="presentation"><form className="visual-lab-modal" role="dialog" aria-modal="true" aria-labelledby="visual-lab-create-title" onSubmit={(event) => { event.preventDefault(); create.mutate({ kind: createKind }); }}><p className="eyebrow">绑定精确对象</p><h3 id="visual-lab-create-title">{palette.find((item) => item.kind === createKind)?.label}</h3><label>节点名称<input value={newTitle} onChange={(event) => setNewTitle(event.target.value)} placeholder="可选" /></label><label>{createKind === "GENERATION_INTENT" ? "Generation Intent ID" : "对象 ID"}<input autoFocus required value={referenceId} onChange={(event) => setReferenceId(event.target.value)} /></label><div><button className="secondary" type="button" onClick={() => setCreateKind(null)}>取消</button><button className="primary-action" type="submit" disabled={create.isPending}>{create.isPending ? "创建中…" : "创建节点"}</button></div>{create.error && <p role="alert">{create.error.message}</p>}</form></div>}

      <section ref={canvasRef} className="visual-lab-canvas" aria-label={`${title} 无限创作画布`} onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; }} onDrop={handleDrop}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onInit={(instance) => { flowRef.current = instance; setZoom(instance.getZoom()); }}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onSelectionChange={({ nodes: selectedNodes }) => {
            const next = selectedNodes.map((node) => node.id);
            setSelectedIds((current) => current.length === next.length && current.every((id, index) => id === next[index]) ? current : next);
          }}
          onPaneClick={clearSelection}
          onNodeDragStart={onDragStart}
          onNodeDragStop={onDragStop}
          onMove={(_, viewport) => setZoom(viewport.zoom)}
          onMoveEnd={(_, viewport) => void saveVisualLabViewport(labId, viewport)}
          defaultViewport={graph.data.document.viewport}
          minZoom={0.08}
          maxZoom={2.5}
          snapToGrid
          snapGrid={[12, 12]}
          selectionOnDrag
          multiSelectionKeyCode={["Shift", "Control", "Meta"]}
          panOnDrag={[0, 1, 2]}
          panActivationKeyCode="Space"
          deleteKeyCode={null}
          onlyRenderVisibleElements
          nodeDragThreshold={4}
          fitViewOptions={{ padding: 0.25, minZoom: 0.35, maxZoom: 1 }}
        >
          <Background gap={24} size={1} color="#c9c2b8" />
          <MiniMap pannable zoomable nodeColor={(node) => node.id && selectedIds.includes(node.id) ? "#2d7467" : node.zIndex === -10 ? "#c8b99f" : "#777168"} />
          <Controls showInteractive={false} />
        </ReactFlow>
        <div className="visual-lab-canvas-status" aria-live="polite"><span>{Math.round(zoom * 100)}%</span><span>{nodes.length} 节点</span><span>{selectedIds.length ? `已选 ${selectedIds.length}` : "Shift 框选"}</span></div>
      </section>

      {selectedIds.length > 1 && <div className="visual-lab-selection-bar" role="toolbar" aria-label="批量节点操作"><strong>已选 {selectedIds.length} 项</strong><button type="button" onClick={() => focusNodes(selectedIds)}>聚焦</button><button type="button" onClick={() => void arrangeSelection("LEFT")}>左对齐</button><button type="button" onClick={() => void arrangeSelection("TOP")}>顶对齐</button><button type="button" onClick={() => void arrangeSelection("HORIZONTAL")}>水平分布</button><button type="button" onClick={createFrame}>建立区域</button><button type="button" onClick={() => duplicate.mutate(selectedIds)}>复制</button><button type="button" className="danger" onClick={() => remove.mutate(selectedIds)}>删除</button></div>}

      {selected && selectedIds.length === 1 && <aside className="visual-lab-inspector"><header><div><p className="eyebrow">节点检视器</p><h3>{nodeTitle(selected)}</h3></div><button type="button" onClick={clearSelection} aria-label="关闭检视器"><StudioIcon name="close" /></button></header><dl><dt>类型</dt><dd>{selected.node_kind}</dd><dt>内容修订</dt><dd>r{selected.content_revision_no}</dd><dt>内容指纹</dt><dd><code>{selected.content_hash.slice(0, 12)}</code></dd></dl><div className="visual-lab-inspector__field"><div className="visual-lab-inspector__mode"><button type="button" className="secondary" onClick={() => setAdvancedJson((value) => !value)}>{advancedJson ? "切换到结构化表单" : "切换到高级 JSON"}</button></div>{advancedJson ? <label>内容 JSON<textarea rows={16} spellCheck={false} value={draft} onChange={(event) => setDraft(event.target.value)} /></label> : <StructuredFields nodeKind={selected.node_kind} content={contentObj} onChange={patchContent} />}</div>{inspectorError && <p className="field-error" role="alert">{inspectorError}</p>}<div className="visual-lab-inspector__actions"><button className="primary-action" type="button" disabled={Boolean(inspectorError) || save.isPending} onClick={() => save.mutate()}>{save.isPending ? "保存中…" : "保存新修订"}</button>{executable && <><button className="secondary" type="button" onClick={() => preflight.mutate()} disabled={preflight.isPending}>运行预检</button><button className="secondary" type="button" onClick={() => run.mutate()} disabled={run.isPending}>提交生成</button></>}<button className="secondary" type="button" onClick={() => void toggleCollapsed()}>{selected.collapsed ? "展开节点" : "折叠节点"}</button><button className="secondary" type="button" onClick={() => duplicate.mutate([selected.id])}>复制节点</button><button className="secondary danger" type="button" onClick={() => remove.mutate([selected.id])} disabled={remove.isPending}>{remove.isPending ? "删除中…" : "删除节点"}</button></div>{selected.node_kind !== "FRAME" && <section className="visual-lab-promotion"><h4>采纳为镜头候选</h4><p>选择源媒体和目标镜头后显式采纳；采纳只创建镜头候选，不会自动选择或批准。</p><div className="visual-lab-promotion__media"><label>源媒体类型<select value={promotionKind} onChange={(event) => setPromotionKind(event.target.value as "IMAGE" | "VIDEO")} aria-label="选择源媒体类型"><option value="IMAGE">图片（关键帧）</option><option value="VIDEO">视频（代理）</option></select></label><MediaPicker projectId={projectId} value={promotionMediaId} onChange={(mediaVersionId) => setPromotionMediaId(mediaVersionId)} label="源媒体（Lab 输出）" mediaKind={promotionKind} allowUpload disabled={promote.isPending} /></div><label>目标镜头<select value={promotionShotId} onChange={(event) => setPromotionShotId(event.target.value)} aria-label="选择采纳目标镜头"><option value="">{shots.isPending ? "加载镜头…" : shots.data?.items.length ? "选择镜头…" : "本集暂无镜头"}</option>{shots.data?.items.map((item) => <option key={item.shot_id} value={item.shot_id}>{item.shot_code || item.shot_id}</option>)}</select></label><button className="secondary" type="button" disabled={!promotionMediaId || !promotionShotId || promote.isPending} onClick={() => promote.mutate()}>{promote.isPending ? "预检并采纳中…" : "预检并采纳候选"}</button></section>}</aside>}
    </main>
  );
}
