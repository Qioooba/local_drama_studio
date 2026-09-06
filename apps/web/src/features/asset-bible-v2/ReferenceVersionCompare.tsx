import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getAssetReferenceMediaVersion, type AssetReferenceMediaVersion, type StoryAssetReference, type StoryAssetState } from "./api";
import { fallbackToOriginalVideo, mediaContentUrl, mediaProxyUrl } from "../shared/mediaPlaybackPolicy";
import "./reference-version-compare.css";

const KIND_LABELS: Record<string, string> = {
  HERO: "主参考", FRONT: "正面", LEFT: "左侧", RIGHT: "右侧", BACK: "背面",
  THREE_VIEW_SHEET: "三视图", FULL_BODY: "全身", CLOSEUP: "特写", EXPRESSION_GRID: "表情表",
  SCENE_WIDE: "大全景", SCENE_REVERSE: "反打", PANORAMA: "全景拼接",
  LIGHTING_REFERENCE: "光线参考", OTHER: "其他",
};

type SemanticVersion = {
  mediaVersionId: string;
  label: string;
};

function thumbnailUrl(id: string) {
  return `/api/v1/media-versions/${encodeURIComponent(id)}/thumbnail?size=medium&frame=poster`;
}

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value < 0) return "未知";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function stateLabel(reference: StoryAssetReference, states: StoryAssetState[]) {
  if (!reference.asset_state_id) return "基础资产";
  const state = states.find((item) => item.id === reference.asset_state_id);
  return state ? `${state.label}（${state.code}）` : "历史状态";
}

function VersionPane({ side, item, loading, error, onRetry }: { side: "A" | "B"; item?: AssetReferenceMediaVersion; loading: boolean; error: unknown; onRetry: () => void }) {
  if (loading) return <article className="reference-compare-pane" aria-busy="true"><p role="status">正在读取版本 {side} 元数据…</p></article>;
  if (error || !item) return <article className="reference-compare-pane"><div className="reference-compare-error" role="alert"><p>版本 {side} 元数据不可用：{error instanceof Error ? error.message : "版本不存在"}</p><button type="button" className="secondary" onClick={onRetry}>重试版本 {side}</button></div></article>;
  const isVideo = item.media_kind === "VIDEO" || item.mime_type.startsWith("video/");
  return <article className="reference-compare-pane" aria-label={`参考版本 ${side}`}>
    <div className="reference-compare-media">
      {isVideo
        ? <video controls preload="none" poster={thumbnailUrl(item.id)} src={mediaProxyUrl(item.id)} data-original-src={mediaContentUrl(item.id)} onError={fallbackToOriginalVideo} aria-label={`版本 ${side} 视频，按播放后优先读取低码率 proxy`} />
        : <img src={thumbnailUrl(item.id)} alt={`版本 ${side} 参考缩略图`} loading="eager" decoding="async" onError={(e) => { e.currentTarget.style.display = "none"; }} />}
    </div>
    <dl>
      <div><dt>不可变版本</dt><dd>v{item.version_no}{item.take_no ? ` · Take ${item.take_no}` : ""}</dd></div>
      <div><dt>阶段 / 类型</dt><dd>{item.stage} · {item.media_kind}</dd></div>
      <div><dt>格式</dt><dd>{item.mime_type}</dd></div>
      <div><dt>大小 / 时长</dt><dd>{formatBytes(item.byte_size)}{item.duration_ms != null ? ` · ${(item.duration_ms / 1000).toFixed(2)}s` : ""}</dd></div>
      <div><dt>完整性</dt><dd>{item.integrity_status}</dd></div>
      <div><dt>SHA-256</dt><dd><code>{item.sha256 ? `${item.sha256.slice(0, 16)}…` : "未记录"}</code></dd></div>
      <div><dt>登记时间</dt><dd>{item.created_at || "未知"}</dd></div>
    </dl>
  </article>;
}

export function ReferenceVersionCompare({ references, states }: { references: StoryAssetReference[]; states: StoryAssetState[] }) {
  const versions = useMemo<SemanticVersion[]>(() => {
    const labels = new Map<string, string[]>();
    for (const reference of references.filter((item) => item.status !== "ARCHIVED")) {
      const semantic = `${reference.label || KIND_LABELS[reference.reference_kind] || reference.reference_kind} · ${stateLabel(reference, states)}`;
      const current = labels.get(reference.media_version_id) ?? [];
      if (!current.includes(semantic)) current.push(semantic);
      labels.set(reference.media_version_id, current);
    }
    return [...labels].map(([mediaVersionId, semanticLabels]) => ({
      mediaVersionId,
      label: `${semanticLabels.join(" / ")} · ${mediaVersionId.slice(0, 8)}…`,
    }));
  }, [references, states]);
  const [leftId, setLeftId] = useState(versions[0]?.mediaVersionId ?? "");
  const [rightId, setRightId] = useState(versions[1]?.mediaVersionId ?? "");
  useEffect(() => {
    const ids = versions.map((item) => item.mediaVersionId);
    const nextLeft = ids.includes(leftId) ? leftId : ids[0] ?? "";
    const nextRight = ids.includes(rightId) && rightId !== nextLeft
      ? rightId
      : ids.find((id) => id !== nextLeft) ?? "";
    if (nextLeft !== leftId) setLeftId(nextLeft);
    if (nextRight !== rightId) setRightId(nextRight);
  }, [leftId, rightId, versions]);
  const canCompare = versions.length >= 2;
  const left = useQuery({ queryKey: ["asset-reference-version", leftId], queryFn: () => getAssetReferenceMediaVersion(leftId), enabled: canCompare && Boolean(leftId) });
  const right = useQuery({ queryKey: ["asset-reference-version", rightId], queryFn: () => getAssetReferenceMediaVersion(rightId), enabled: canCompare && Boolean(rightId) });

  return <section className="panel reference-version-compare" aria-labelledby="reference-compare-title">
    <div className="panel-heading"><div><p className="eyebrow">只读版本事实</p><h4 id="reference-compare-title">参考版本比较</h4></div><span className="status-pill neutral">不修改采用或锁定</span></div>
    <p className="muted">从当前资产已绑定的不可变参考版本中选择两项。图片只读取缩略图；视频默认不预加载，播放时才读取内容。</p>
    {versions.length < 2 ? <p className="empty-state">至少需要两个不同的参考媒体版本才能比较；同一版本的多个语义绑定只合并展示。</p> : <>
      <div className="reference-compare-selectors">
        <label>版本 A<select aria-label="比较版本 A" value={leftId} onChange={(event) => setLeftId(event.target.value)}>{versions.map((item) => <option key={item.mediaVersionId} value={item.mediaVersionId} disabled={item.mediaVersionId === rightId}>{item.label}</option>)}</select></label>
        <label>版本 B<select aria-label="比较版本 B" value={rightId} onChange={(event) => setRightId(event.target.value)}>{versions.map((item) => <option key={item.mediaVersionId} value={item.mediaVersionId} disabled={item.mediaVersionId === leftId}>{item.label}</option>)}</select></label>
      </div>
      <div className="reference-compare-grid">
        <VersionPane side="A" item={left.data?.media_version} loading={left.isPending} error={left.error} onRetry={() => void left.refetch()} />
        <VersionPane side="B" item={right.data?.media_version} loading={right.isPending} error={right.error} onRetry={() => void right.refetch()} />
      </div>
    </>}
  </section>;
}
