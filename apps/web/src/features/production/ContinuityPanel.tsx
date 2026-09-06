import { useQuery } from "@tanstack/react-query";
import { listShotStoryAssets, type ContinuityContext, type ContinuityShot } from "../../generated/api";

const facetLabels: Record<string, string> = {
  appearance: "人物外观",
  costume: "服装",
  props: "道具",
  lighting: "光线",
  spatial_direction: "空间方向",
  continuity: "连续性",
};

const assetKindLabels: Record<string, string> = { CHARACTER: "角色", SCENE: "场景", PROP: "道具", COSTUME: "服装" };

function BoundAssetList({ shotId }: { shotId: string }) {
  const bindings = useQuery({ queryKey: ["shot-story-assets", shotId], queryFn: () => listShotStoryAssets(shotId), enabled: Boolean(shotId) });
  const items = bindings.data?.items ?? [];
  if (items.length === 0) return null;
  return <div className="continuity-bound-assets" aria-label="当前镜头绑定资产">
    <small>绑定资产 · {items.length}</small>
    {items.map((item) => <div className="continuity-bound-asset" key={item.binding_id}><strong>{item.name}</strong><span>{assetKindLabels[item.kind] ?? item.kind} · {item.role_in_shot}</span>{item.status === "ARCHIVED" ? <span className="status-pill archived">已归档</span> : null}</div>)}
  </div>;
}

function displayValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join("、");
  if (value && typeof value === "object") return JSON.stringify(value);
  return value === null || value === undefined || value === "" ? "未填写" : String(value);
}

function ShotColumn({ shot, label }: { shot: ContinuityShot | null; label: string }) {
  if (!shot) return <article className="continuity-shot empty"><p className="eyebrow">{label}</p><strong>无相邻镜头</strong><span>已到达当前分集边界</span></article>;
  return <article className={`continuity-shot ${shot.position}`} data-shot-position={shot.position}>
    <div className="continuity-shot-heading"><div><p className="eyebrow">{label}</p><h4>{shot.code}</h4></div><span className="status-pill">revision {shot.revision.revision_no ?? "—"}{shot.revision.is_frozen ? " · 已冻结" : ""}</span></div>
    <dl>{Object.entries(facetLabels).map(([key, facetLabel]) => <div key={key} className={shot.missing_facets.includes(key) ? "missing" : ""}><dt>{facetLabel}</dt><dd>{displayValue(shot.facets[key])}</dd></div>)}</dl>
    <div className="continuity-references">
      <small>已选/已批参考 · {shot.references.length}</small>
      {shot.references.map((reference) => <div className="continuity-reference" key={reference.media_version_id}>
        {reference.media_kind === "IMAGE" || reference.media_kind === "VIDEO" ? <img src={`/api/v1/media-versions/${encodeURIComponent(reference.media_version_id)}/thumbnail?size=small&frame=poster`} alt={`${shot.code} 连续性参考的小尺寸缩略图`} loading="eager" decoding="async" onError={(e) => { e.currentTarget.style.display = "none"; }} /> : <span className="media-kind-placeholder" aria-hidden="true">{reference.media_kind}</span>}
        <span><strong>{reference.selection_state}</strong><small>{reference.purpose} · v{reference.version_no} · {reference.integrity_status}</small></span>
      </div>)}
      {shot.references.length === 0 && <span className="muted">无已选或已批参考</span>}
    </div>
  </article>;
}

export function ContinuityPanel({ context }: { context: ContinuityContext | undefined }) {
  return <section className="panel continuity-panel" aria-labelledby="continuity-panel-title">
    <div className="panel-heading"><div><p className="eyebrow">只读对照</p><h3 id="continuity-panel-title">跨镜头连续性</h3></div><span className="status-pill">轻量缩略图</span></div>
    {!context ? <p className="empty-state">选择镜头后读取前后镜头连续性。</p> : <>
      <div className="continuity-grid">
        <ShotColumn shot={context.shots.previous} label="上一镜" />
        <ShotColumn shot={context.shots.current} label="当前镜" />
        <ShotColumn shot={context.shots.next} label="下一镜" />
      </div>
      <BoundAssetList shotId={context.selected_shot_id} />
      <div className="continuity-footer"><span>边界约束 {context.transitions.length}</span><span>runtime_contacted=false</span><span>network_contacted=false</span><span>mutated=false</span></div>
    </>}
  </section>;
}
