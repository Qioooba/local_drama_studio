import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { createStoryAsset } from "../generated/api";
import { createStoryAssetReference, getAssetBible } from "../features/asset-bible-v2/api";
import { ProjectAssetImageWorkbench } from "../features/asset-bible-v2/ProjectAssetImageWorkbench";
import { GenerateMultiViewPanel } from "../features/asset-bible-v2/GenerateMultiViewPanel";
import { CharacterIdentityPackPanel } from "../features/asset-bible-v2/CharacterIdentityPackPanel";
import { CharacterVoicePanel } from "../features/asset-bible-v2/CharacterVoicePanel";
import { AssetDescriptionEditor } from "../features/asset-bible-v2/AssetDescriptionEditor";
import { MediaPicker } from "../features/media-picker/MediaPicker";
import { EmptyState, ErrorState, MediaThumb, Skeleton, StatusBadge } from "../components/ui";
import { queryKeys } from "../query/queryKeys";
import { generateAssetCode } from "../features/shared/autoCode";

const KIND_TABS = [
  { kind: "CHARACTER", label: "人物" },
  { kind: "SCENE", label: "场景" },
  { kind: "PROP", label: "道具" },
] as const;
type AssetKind = (typeof KIND_TABS)[number]["kind"];

const KIND_LABELS: Record<AssetKind, string> = { CHARACTER: "人物", SCENE: "场景", PROP: "道具" };

function thumbnailUrl(mediaVersionId: string): string {
  return `/api/v1/media-versions/${encodeURIComponent(mediaVersionId)}/thumbnail?size=medium&frame=poster`;
}

export function AssetBiblePage() {
  const { projectId } = useParams();
  const client = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedKind = searchParams.get("kind");
  const tab: AssetKind = KIND_TABS.some((item) => item.kind === requestedKind)
    ? requestedKind as AssetKind
    : "CHARACTER";
  const selectedId = searchParams.get("asset");
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [referenceVersionId, setReferenceVersionId] = useState("");
  const [message, setMessage] = useState("");

  const assets = useQuery({
    queryKey: queryKeys.assetBible.overview(projectId as string),
    queryFn: () => getAssetBible(projectId as string),
    enabled: Boolean(projectId),
  });
  const bible = assets.data?.bible;
  const visible = (bible?.items ?? []).filter((item) => item.asset.kind === tab && item.asset.status === "ACTIVE");
  const selected = selectedId ? visible.find((item) => item.asset.id === selectedId) ?? visible[0] ?? null : visible[0] ?? null;

  const setAssetContext = (kind: AssetKind, assetId?: string | null) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (kind === "CHARACTER") next.delete("kind"); else next.set("kind", kind);
      if (assetId) next.set("asset", assetId); else next.delete("asset");
      return next;
    }, { replace: true });
  };

  useEffect(() => {
    setReferenceVersionId("");
    setMessage("");
  }, [selected?.asset.id]);

  const refresh = async () => {
    await client.invalidateQueries({ queryKey: queryKeys.assetBible.project(projectId as string) });
    await client.invalidateQueries({ queryKey: queryKeys.assetBible.storyAssets(projectId as string) });
  };

  const create = useMutation({
    mutationFn: () => createStoryAsset(projectId as string, {
      kind: tab,
      code: generateAssetCode(tab, newName),
      name: newName.trim(),
      description: newDescription.trim(),
    }),
    onSuccess: async (result) => {
      setNewName("");
      setNewDescription("");
      setAssetContext(tab, result.asset.id);
      setMessage("资产已创建，AI 会在下一次生成时自动使用它。");
      await refresh();
    },
  });

  const setHero = useMutation({
    mutationFn: () => createStoryAssetReference(selected!.asset.id, {
      media_version_id: referenceVersionId,
      reference_kind: "HERO",
      is_locked: true,
    }),
    onSuccess: async () => {
      setReferenceVersionId("");
      setMessage("主参考已更新。后续镜头会自动使用这张图保持一致。");
      await refresh();
    },
  });

  if (!projectId) return <p className="inline-error" role="alert">缺少项目上下文。</p>;

  return (
    <div className="v2-page">
      <div className="panel-heading">
        <div><p className="eyebrow">核心资产</p><h3>人物、场景与关键道具</h3></div>
        <StatusBadge tone={assets.isPending ? "running" : "neutral"}>{bible?.asset_count ?? "…"} 项</StatusBadge>
      </div>
      <p className="muted">AI 只为跨镜头重复使用的对象建档。默认自动生成并选择主图，你只需在外观不满意时替换。</p>

      {assets.isPending && <section className="panel"><Skeleton label="正在读取核心资产" lines={4} /></section>}
      {assets.error && <ErrorState title="核心资产暂时无法打开" description={assets.error instanceof Error ? assets.error.message : String(assets.error)} onRetry={() => void assets.refetch()} />}

      {!assets.isPending && !assets.error && (
        <>
          <ProjectAssetImageWorkbench projectId={projectId} items={bible?.items ?? []} onChanged={refresh} />

          <div className="bible-tabs" role="tablist" aria-label="资产类别">
            {KIND_TABS.map((item) => (
              <button key={item.kind} type="button" role="tab" aria-selected={tab === item.kind} className={tab === item.kind ? "selected" : ""} onClick={() => setAssetContext(item.kind)}>
                {item.label}
              </button>
            ))}
          </div>

          <div className="bible-layout bible-layout--simple">
            <aside className="bible-list" aria-label="核心资产列表">
              <div className="bible-asset-list">
                {visible.map((item) => {
                  const heroId = item.asset.canonical_media_version_id
                    || item.base_references.find((reference) => reference.reference_kind === "HERO")?.media_version_id
                    || null;
                  return (
                    <button key={item.asset.id} type="button" className={`bible-asset-row${selected?.asset.id === item.asset.id ? " selected" : ""}`} onClick={() => setAssetContext(tab, item.asset.id)}>
                      <MediaThumb className="bible-asset-thumbnail" src={heroId ? thumbnailUrl(heroId) : null} alt={`${item.asset.name} 资产缩略图`} emptyLabel="待生成" aspectRatio="1 / 1" />
                      <span className="bible-asset-name">{item.asset.name}</span>
                      <small>{heroId ? "主图已就绪" : "AI 待生成"}</small>
                    </button>
                  );
                })}
                {visible.length === 0 && <EmptyState title={`还没有${KIND_LABELS[tab]}资产`} description="上传原稿后 AI 会自动识别；也可以在下方手动添加一个关键资产。" />}
              </div>
            </aside>

            {selected ? (
              <section className="bible-detail" aria-label="资产详情">
                {(() => {
                  const heroId = selected.asset.canonical_media_version_id
                    || selected.base_references.find((reference) => reference.reference_kind === "HERO")?.media_version_id
                    || null;
                  return (
                    <>
                      <div className="bible-detail-head">
                        <div><p className="eyebrow">{KIND_LABELS[selected.asset.kind as AssetKind]}</p><h4>{selected.asset.name}</h4><p className="muted">{selected.asset.description || "AI 会结合原稿上下文生成视觉描述。"}</p></div>
                        <StatusBadge tone={heroId ? "success" : "attention"}>{heroId ? "可用于生成" : "缺少主图"}</StatusBadge>
                      </div>
                      <AssetDescriptionEditor key={selected.asset.id} asset={selected.asset} onChanged={refresh} />
                      <div className="bible-main-visual">
                        <MediaThumb src={heroId ? thumbnailUrl(heroId) : null} alt={`${selected.asset.name} 主参考`} emptyLabel="主参考将在批量生成后自动出现" aspectRatio="4 / 3" />
                      </div>
                      <details className="story-asset-create asset-reference-replace">
                        <summary>{heroId ? "替换主参考" : "手动选择主参考"}</summary>
                        <MediaPicker projectId={projectId} value={referenceVersionId} onChange={setReferenceVersionId} disabled={setHero.isPending} label="主参考图片" />
                        <button type="button" className="primary-action" disabled={!referenceVersionId || setHero.isPending} onClick={() => setHero.mutate()}>
                          {setHero.isPending ? "正在保存…" : "设为主参考"}
                        </button>
                      </details>
                      <GenerateMultiViewPanel
                        projectId={projectId}
                        assetId={selected.asset.id}
                        assetKind={selected.asset.kind}
                        assetStatus={selected.asset.status}
                        states={selected.states}
                        baseReferences={selected.base_references}
                        initialBatches={selected.multiview_generations ?? []}
                        onReferencesChanged={refresh}
                      />
                      {selected.asset.kind === "CHARACTER" && <CharacterIdentityPackPanel
                        projectId={projectId}
                        storyAssetId={selected.asset.id}
                        assetName={selected.asset.name}
                        baseReferences={selected.base_references}
                        onVersionApproved={() => void refresh()}
                        onRequestMissingSlots={(missingSlots) => {
                          setMessage(`身份包仍缺少 ${missingSlots.join("、")}；请在下方生成并审核对应视图。`);
                          document.getElementById(`multiview-title-${selected.asset.id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
                        }}
                      />}
                      {selected.asset.kind === "CHARACTER" && <CharacterVoicePanel projectId={projectId} assetId={selected.asset.id} assetName={selected.asset.name} />}
                      {message && <p className="review-success" role="status">{message}</p>}
                      {(setHero.error || create.error) && <p className="inline-error" role="alert">{String(setHero.error || create.error)}</p>}
                    </>
                  );
                })()}
              </section>
            ) : (
              <section className="bible-detail"><EmptyState title="当前没有可编辑资产" description="AI 完成全剧分析后，核心资产会自动出现在这里。" /></section>
            )}
          </div>

          <details className="story-asset-create bible-manual-create">
            <summary>手动添加{KIND_LABELS[tab]}（可选）</summary>
            <div className="bible-create-grid">
              <label>名称<input value={newName} onChange={(event) => setNewName(event.target.value)} /></label>
              <label className="wide">一句话视觉描述<textarea value={newDescription} onChange={(event) => setNewDescription(event.target.value)} /></label>
              <button type="button" className="primary-action" disabled={!newName.trim() || create.isPending} onClick={() => create.mutate()}>{create.isPending ? "正在添加…" : "添加资产"}</button>
            </div>
          </details>
        </>
      )}

      <div className="v2-actions"><Link className="secondary v2-inline-link" to={`/projects/${projectId}`}>返回项目</Link></div>
    </div>
  );
}
