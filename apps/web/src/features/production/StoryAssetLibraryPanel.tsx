import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { archiveStoryAsset, createStoryAsset, listStoryAssets, type StoryAsset } from "../../generated/api";
import { MediaPicker } from "../media-picker/MediaPicker";

const tabs = [
  { kind: "CHARACTER", label: "角色" },
  { kind: "SCENE", label: "场景" },
  { kind: "PROP", label: "道具" },
  { kind: "COSTUME", label: "服装" },
] as const;

const kindLabels: Record<string, string> = { CHARACTER: "角色", SCENE: "场景", PROP: "道具", COSTUME: "服装" };

export function StoryAssetLibraryPanel({ projectId }: { projectId: string }) {
  const client = useQueryClient();
  const [tab, setTab] = useState<(typeof tabs)[number]["kind"]>("CHARACTER");
  const assets = useQuery({ queryKey: ["story-assets", projectId], queryFn: () => listStoryAssets(projectId) });
  const items = assets.data?.items ?? [];
  const visible = items.filter((item) => item.kind === tab);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [canonicalMediaVersionId, setCanonicalMediaVersionId] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const refresh = async () => { await client.invalidateQueries({ queryKey: ["story-assets", projectId] }); };

  const create = useMutation({
    mutationFn: () => createStoryAsset(projectId, { kind: tab, code, name, description, canonical_media_version_id: canonicalMediaVersionId.trim() || null }),
    onSuccess: async () => { setCode(""); setName(""); setDescription(""); setCanonicalMediaVersionId(""); setFormError(null); await refresh(); },
    onError: (error) => setFormError(String(error)),
  });

  const archive = useMutation({
    mutationFn: (item: StoryAsset) => archiveStoryAsset(item.id, { expected_revision: item.revision }),
    onSuccess: async () => { await refresh(); },
    onError: (error) => setFormError(String(error)),
  });

  const createValid = Boolean(code.trim() && name.trim());
  return <section className="panel story-asset-library" aria-labelledby="story-asset-library-title">
    <div className="panel-heading"><div><p className="eyebrow">G11 P0-1/2 · 资产卡</p><h3 id="story-asset-library-title">故事资产库</h3></div><span className="status-pill">{items.length} 项</span></div>
    <div className="story-asset-tabs" role="tablist" aria-label="资产类别">
      {tabs.map((item) => <button key={item.kind} type="button" role="tab" aria-selected={tab === item.kind} className={tab === item.kind ? "selected" : ""} onClick={() => setTab(item.kind)}>{item.label}</button>)}
    </div>
    <div className="story-asset-grid">
      {visible.map((item) => <article className={`story-asset-card${item.status === "ARCHIVED" ? " archived" : ""}`} key={item.id}>
        {item.canonical_media_version_id ? <img src={`/api/v1/media-versions/${encodeURIComponent(item.canonical_media_version_id)}/thumbnail?size=small&frame=poster`} alt={`${item.name} 资产参考图的小尺寸缩略图`} width="96" height="54" loading="lazy" decoding="async" /> : <span className="media-kind-placeholder" aria-hidden="true">无参考图</span>}
        <div className="story-asset-card-body"><strong>{item.name}</strong><code>{item.code}</code><small>{item.description || "无描述"}</small><span className={item.status === "ARCHIVED" ? "status-pill archived" : "status-pill"}>{item.status === "ARCHIVED" ? "已归档" : "启用中"}</span><span className="muted">{kindLabels[item.kind] ?? item.kind}</span></div>
        <button type="button" className="secondary" aria-label={`归档 ${item.name}`} disabled={item.status === "ARCHIVED" || archive.isPending} onClick={() => archive.mutate(item)}>归档</button>
      </article>)}
      {visible.length === 0 && <p className="empty-state">当前类别暂无资产卡。</p>}
    </div>
    <details className="story-asset-create"><summary>新建{kindLabels[tab]}资产卡</summary>
      <div className="story-asset-create-grid">
        <label>代码<input value={code} onChange={(event) => setCode(event.target.value)} placeholder="CHAR_MOTHER" /></label>
        <label>名称<input value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label>描述<input value={description} onChange={(event) => setDescription(event.target.value)} /></label>
        <div className="wide">
          <MediaPicker projectId={projectId} value={canonicalMediaVersionId} onChange={setCanonicalMediaVersionId} disabled={create.isPending} label={`${kindLabels[tab]}主参考选择器`} />
          <p className="muted">可选。主参考固定到所选的不可变图片版本；普通流程无需复制版本标识。</p>
        </div>
        <button type="button" className="primary-action" disabled={!createValid || create.isPending} onClick={() => create.mutate()}>{create.isPending ? "创建中…" : `创建${kindLabels[tab]}资产卡`}</button>
      </div>
    </details>
    {(formError || assets.error) && <p className="inline-error" role="alert">{String(formError ?? assets.error)}</p>}
  </section>;
}
