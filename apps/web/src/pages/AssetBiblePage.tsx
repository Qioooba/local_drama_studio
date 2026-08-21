import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import {
  archiveStoryAsset,
  createStoryAsset,
} from "../generated/api";
import { createStoryAssetReference, createStoryAssetState, getAssetBible } from "../features/asset-bible-v2/api";
import { GenerateMultiViewPanel } from "../features/asset-bible-v2/GenerateMultiViewPanel";
import { CharacterIdentityPackPanel } from "../features/asset-bible-v2/CharacterIdentityPackPanel";
import { GenerateExpressionPanel } from "../features/asset-bible-v2/GenerateExpressionPanel";
import { GenerateDetailPanel } from "../features/asset-bible-v2/GenerateDetailPanel";
import { SceneBiblePanel, type SceneReferenceKind } from "../features/asset-bible-v2/SceneBiblePanel";
import { AssetUsagePanel } from "../features/asset-bible-v2/AssetUsagePanel";
import { ReferenceVersionCompare } from "../features/asset-bible-v2/ReferenceVersionCompare";
import { MediaPicker } from "../features/media-picker/MediaPicker";
import { EmptyState, ErrorState, MediaThumb, Skeleton, StatusBadge } from "../components/ui";
import { queryKeys } from "../query/queryKeys";

const KIND_TABS = [
  { kind: "CHARACTER", label: "角色" },
  { kind: "SCENE", label: "场景" },
  { kind: "PROP", label: "道具" },
  { kind: "COSTUME", label: "服装" },
] as const;

const REF_KIND_LABELS: Record<string, string> = {
  HERO: "主参考",
  FRONT: "正面",
  LEFT: "左侧",
  RIGHT: "右侧",
  BACK: "背面",
  THREE_VIEW_SHEET: "三视图",
  FULL_BODY: "全身",
  CLOSEUP: "特写",
  EXPRESSION_GRID: "表情表",
  SCENE_WIDE: "大全景",
  SCENE_REVERSE: "反打",
  PANORAMA: "全景拼接",
  LIGHTING_REFERENCE: "光线参考",
  OTHER: "其他",
};

const KIND_LABELS: Record<string, string> = { CHARACTER: "角色", SCENE: "场景", PROP: "道具", COSTUME: "服装" };

function thumbnailUrl(mediaVersionId: string): string {
  return `/api/v1/media-versions/${encodeURIComponent(mediaVersionId)}/thumbnail?size=small&frame=poster`;
}

export function AssetBiblePage() {
  const { projectId } = useParams();
  const client = useQueryClient();
  const [tab, setTab] = useState<(typeof KIND_TABS)[number]["kind"]>("CHARACTER");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [newCode, setNewCode] = useState("");
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [stateCode, setStateCode] = useState("");
  const [stateLabel, setStateLabel] = useState("");
  const [stateKind, setStateKind] = useState("CUSTOM");
  const [referenceVersionId, setReferenceVersionId] = useState("");
  const [referenceKind, setReferenceKind] = useState("HERO");
  const [referenceStateId, setReferenceStateId] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const assets = useQuery({
    queryKey: queryKeys.assetBible.overview(projectId as string),
    queryFn: () => getAssetBible(projectId as string),
    enabled: Boolean(projectId),
  });
  const bible = assets.data?.bible;
  const visible = (bible?.items ?? []).filter((item) => item.asset.kind === tab);
  const selected = selectedId ? visible.find((item) => item.asset.id === selectedId) ?? null : visible[0] ?? null;

  const refresh = async () => {
    await client.invalidateQueries({ queryKey: queryKeys.assetBible.project(projectId as string) });
    await client.invalidateQueries({ queryKey: queryKeys.assetBible.storyAssets(projectId as string) });
  };

  const create = useMutation({
    mutationFn: () => createStoryAsset(projectId as string, { kind: tab, code: newCode, name: newName, description: newDescription }),
    onSuccess: async (result) => {
      setNewCode(""); setNewName(""); setNewDescription(""); setFormError(null);
      setSelectedId(result.asset.id);
      await refresh();
    },
    onError: (error) => setFormError(String(error)),
  });

  const archive = useMutation({
    mutationFn: (item: { id: string; revision: number }) => archiveStoryAsset(item.id, { expected_revision: item.revision }),
    onSuccess: async () => { await refresh(); },
    onError: (error) => setFormError(String(error)),
  });

  const addState = useMutation({
    mutationFn: () => createStoryAssetState(selected!.asset.id, { code: stateCode.trim(), label: stateLabel.trim(), state_kind: stateKind }),
    onSuccess: async () => { setStateCode(""); setStateLabel(""); setFormError(null); await refresh(); },
    onError: (error) => setFormError(String(error)),
  });

  const addReference = useMutation({
    mutationFn: () => createStoryAssetReference(selected!.asset.id, { media_version_id: referenceVersionId.trim(), reference_kind: referenceKind, asset_state_id: referenceStateId || null, is_locked: referenceKind === "HERO" }),
    onSuccess: async () => { setReferenceVersionId(""); setFormError(null); await refresh(); },
    onError: (error) => setFormError(String(error)),
  });

  const createValid = Boolean(newCode.trim() && newName.trim());
  const stateDisabledReason = addState.isPending ? "状态正在创建，请稍候" : !stateCode.trim() ? "请填写状态代码" : !stateLabel.trim() ? "请填写显示名称" : null;
  const referenceDisabledReason = addReference.isPending ? "参考正在绑定，请稍候" : !referenceVersionId.trim() ? "请先从项目媒体中选择图片" : null;
  const createDisabledReason = create.isPending ? "资产正在创建，请稍候" : !newCode.trim() ? "请填写资产代码" : !newName.trim() ? "请填写资产名称" : null;
  const chooseSceneReference = (kind: SceneReferenceKind) => {
    setReferenceKind(kind);
    document.getElementById("asset-reference-picker")?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  return (
    <div className="v2-page">
      <div className="panel-heading">
        <div><p className="eyebrow">资产圣经</p><h3>角色 / 场景 / 道具 / 服装</h3></div>
        <StatusBadge tone={assets.isPending ? "running" : "neutral"}>{bible?.asset_count ?? "…"} 项资产</StatusBadge>
      </div>

      {assets.isPending && <section className="panel"><Skeleton label="正在读取资产、状态、参考与使用情况" lines={5} /></section>}
      {assets.error && <><ErrorState title="资产圣经暂时无法打开" description={assets.error instanceof Error ? assets.error.message : String(assets.error)} onRetry={() => void assets.refetch()} /><Link className="secondary v2-inline-link" to={`/projects/${projectId}`}>返回项目总览</Link></>}
      {!assets.isPending && !assets.error && <div className="bible-layout">
        <aside className="bible-list" aria-label="资产列表">
          <div className="bible-tabs" role="tablist" aria-label="资产类别">
            {KIND_TABS.map((item) => (
              <button key={item.kind} type="button" role="tab" aria-selected={tab === item.kind} className={tab === item.kind ? "selected" : ""} onClick={() => { setTab(item.kind); setSelectedId(null); }}>{item.label}</button>
            ))}
          </div>
          <div className="bible-asset-list">
            {visible.map((item) => (
              <button key={item.asset.id} type="button" className={`bible-asset-row${selected?.asset.id === item.asset.id ? " selected" : ""}`} onClick={() => setSelectedId(item.asset.id)}>
                <MediaThumb className="bible-asset-thumbnail" src={item.asset.canonical_media_version_id ? thumbnailUrl(item.asset.canonical_media_version_id) : null} alt={`${item.asset.name} 资产缩略图`} emptyLabel="无图" />
                <span className="bible-asset-name">{item.asset.name}</span>
                <code>{item.asset.code}</code>
              </button>
            ))}
            {visible.length === 0 && <EmptyState title={`还没有${KIND_LABELS[tab] ?? "此类"}资产`} description="使用右侧的新建入口建立第一项资产；已有媒体可在创建后绑定为参考。" />}
          </div>
        </aside>

        {selected ? (
          <section className="bible-detail" aria-label="资产详情">
            <div className="bible-detail-head">
              <div>
                <p className="eyebrow">{KIND_LABELS[selected.asset.kind] ?? selected.asset.kind}</p>
                <h4>{selected.asset.name}</h4>
                <p className="muted">{selected.asset.description || "无描述"}</p>
              </div>
              <div className="v2-actions">
                <StatusBadge tone={selected.asset.status === "ARCHIVED" ? "neutral" : "success"}>{selected.asset.status === "ARCHIVED" ? "已归档" : "启用中"}</StatusBadge>
                <button type="button" className="secondary" disabled={selected.asset.status === "ARCHIVED" || archive.isPending} title={selected.asset.status === "ARCHIVED" ? "资产已经归档" : archive.isPending ? "正在归档，请稍候" : undefined} onClick={() => archive.mutate({ id: selected.asset.id, revision: selected.asset.revision })}>归档</button>
              </div>
            </div>

            <div className="bible-main-visual">
              {selected.base_references.length > 0 || selected.states.some((s) => s.references.length > 0) ? (
                <div className="bible-ref-grid">
                  {[...selected.base_references, ...selected.states.flatMap((s) => s.references)].map((ref) => (
                    <figure className="bible-ref-card" key={ref.id}>
                      <MediaThumb src={thumbnailUrl(ref.media_version_id)} alt={`${ref.label || (REF_KIND_LABELS[ref.reference_kind] ?? ref.reference_kind)} 参考图`} emptyLabel="缩略图不可用" aspectRatio="4 / 3" />
                      <figcaption>{REF_KIND_LABELS[ref.reference_kind] ?? ref.reference_kind}{ref.is_locked ? " · 已锁定" : ""}</figcaption>
                    </figure>
                  ))}
                </div>
              ) : (
                <div className="bible-empty-visual"><EmptyState title="还没有参考图" description={selected.asset.kind === "CHARACTER" ? "先添加主参考（HERO），再生成三视图。" : "从项目媒体中选择一张低分辨率参考缩略图。"} /></div>
              )}
            </div>

            <ReferenceVersionCompare
              key={selected.asset.id}
              references={[...selected.base_references, ...selected.states.flatMap((state) => state.references)]}
              states={selected.states}
            />

            <div className="bible-aside-info">
              {selected.asset.kind === "SCENE" && <SceneBiblePanel item={selected} onChanged={refresh} onChooseReference={chooseSceneReference} />}
              {selected.asset.kind === "CHARACTER" && <GenerateMultiViewPanel
                key={selected.asset.id}
                projectId={projectId as string}
                assetId={selected.asset.id}
                assetKind={selected.asset.kind}
                assetStatus={selected.asset.status}
                states={selected.states}
                baseReferences={selected.base_references}
                initialBatches={selected.multiview_generations ?? []}
                onReferencesChanged={refresh}
              />}
              {selected.asset.kind === "CHARACTER" && <CharacterIdentityPackPanel
                key={`identity-pack:${selected.asset.id}`}
                projectId={projectId as string}
                storyAssetId={selected.asset.id}
                assetName={selected.asset.name}
                onRequestMissingSlots={() => document.getElementById(`multiview-title-${selected.asset.id}`)?.scrollIntoView({ behavior: "smooth", block: "start" })}
              />}
              {selected.asset.kind === "CHARACTER" && <GenerateExpressionPanel
                key={`expression:${selected.asset.id}`}
                projectId={projectId as string}
                assetId={selected.asset.id}
                assetStatus={selected.asset.status}
                states={selected.states}
                baseReferences={selected.base_references}
                initialBatches={selected.expression_generations ?? []}
                onReferencesChanged={refresh}
              />}
              {selected.asset.kind === "CHARACTER" && <GenerateDetailPanel
                key={`detail:${selected.asset.id}`}
                projectId={projectId as string}
                assetId={selected.asset.id}
                assetStatus={selected.asset.status}
                states={selected.states}
                baseReferences={selected.base_references}
                initialBatches={selected.detail_generations ?? []}
                onReferencesChanged={refresh}
              />}
              <section className="panel" id="asset-reference-picker">
                <div className="panel-heading"><div><p className="eyebrow">造型状态</p><h4>States</h4></div></div>
                {selected.states.length === 0 ? <EmptyState title="还没有造型状态" description="状态用于区分服装、伤势、情绪、日夜或光线，不会覆盖基础资产。" /> : (
                  <ul className="bible-state-list">{selected.states.map((state) => <li key={state.id}><strong>{state.label}</strong><code>{state.code}</code><span className="muted">{state.state_kind}</span><StatusBadge>{state.references.length} 参考</StatusBadge></li>)}</ul>
                )}
                <details className="story-asset-create"><summary>新增剧情状态</summary><div className="bible-create-grid">
                  <label>状态代码<input value={stateCode} onChange={(event) => setStateCode(event.target.value)} placeholder="INJURED" /></label>
                  <label>显示名称<input value={stateLabel} onChange={(event) => setStateLabel(event.target.value)} placeholder="受伤" /></label>
                  <label>状态类型<select value={stateKind} onChange={(event) => setStateKind(event.target.value)}><option value="CUSTOM">自定义</option><option value="OUTFIT">服装</option><option value="INJURY">伤势</option><option value="EMOTION">情绪</option><option value="TIME_OF_DAY">时段</option><option value="WEATHER">天气</option><option value="LIGHTING">光线</option></select></label>
                  <button className="secondary" type="button" disabled={Boolean(stateDisabledReason)} title={stateDisabledReason ?? undefined} onClick={() => addState.mutate()}>{addState.isPending ? "创建中…" : "创建状态"}</button>
                </div></details>
              </section>
              <section className="panel">
                <div className="panel-heading"><div><p className="eyebrow">参考图</p><h4>选择或上传项目图片</h4></div></div>
                <p className="muted">选择结果固定到不可变媒体版本，后续更新不会改变历史生成输入。列表只读取低分辨率缩略图；HERO 默认锁定。</p>
                <MediaPicker projectId={projectId as string} value={referenceVersionId} onChange={setReferenceVersionId} disabled={addReference.isPending} label="资产参考图选择器" />
                <div className="bible-create-grid">
                  <label>参考类型<select value={referenceKind} onChange={(event) => setReferenceKind(event.target.value)}>{Object.entries(REF_KIND_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
                  <label>绑定状态<select value={referenceStateId} onChange={(event) => setReferenceStateId(event.target.value)}><option value="">基础参考</option>{selected.states.map((state) => <option key={state.id} value={state.id}>{state.label}</option>)}</select></label>
                  <button className="primary-action" type="button" disabled={Boolean(referenceDisabledReason)} title={referenceDisabledReason ?? undefined} onClick={() => addReference.mutate()}>{addReference.isPending ? "绑定中…" : "添加参考"}</button>
                </div>
              </section>
              <section className="panel">
                <div className="panel-heading"><div><p className="eyebrow">准备度</p><h4>Readiness</h4></div></div>
                <p className="muted">级别：{selected.readiness.level}</p>
                {selected.readiness.missing.length > 0 ? <p className="blocker-text">缺参考：{selected.readiness.missing.map((kind) => REF_KIND_LABELS[kind] ?? kind).join("、")}</p> : <p className="approved-text">参考图齐全</p>}
              </section>
              {selected.voice && (
                <section className="panel">
                  <div className="panel-heading"><div><p className="eyebrow">声音</p><h4>Voice</h4></div></div>
                  <p className="muted">{selected.voice.voice_title}</p>
                </section>
              )}
              <AssetUsagePanel item={selected} projectId={projectId as string} />
            </div>
          </section>
        ) : (
          <section className="bible-detail">
            <div className="bible-empty-visual"><EmptyState title="请选择一项资产" description="从左侧打开现有资产，或使用新建入口开始。" /></div>
          </section>
        )}

        <aside className="bible-create" aria-label="新建资产">
          <details className="story-asset-create"><summary>新建{KIND_LABELS[tab] ?? tab}资产</summary>
            <div className="bible-create-grid">
              <label>代码<input value={newCode} onChange={(event) => setNewCode(event.target.value)} placeholder={`CHAR_${tab.slice(0, 3).toUpperCase()}`} /></label>
              <label>名称<input value={newName} onChange={(event) => setNewName(event.target.value)} /></label>
              <label className="wide">描述<input value={newDescription} onChange={(event) => setNewDescription(event.target.value)} /></label>
              <button type="button" className="primary-action" disabled={!createValid || create.isPending} title={createDisabledReason ?? undefined} onClick={() => create.mutate()}>{create.isPending ? "创建中…" : "创建资产"}</button>
            </div>
          </details>
          {formError && <p className="inline-error" role="alert">{String(formError)}</p>}
        </aside>
      </div>}

      <div className="v2-actions">
        <Link className="secondary v2-inline-link" to={`/projects/${projectId}`}>返回项目总览</Link>
      </div>
    </div>
  );
}
