import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  archiveStoryAsset,
  createStoryAsset,
  restoreStoryAsset,
} from "../generated/api";
import { createStoryAssetReference, createStoryAssetState, getAssetBible } from "../features/asset-bible-v2/api";
import { GenerateMultiViewPanel } from "../features/asset-bible-v2/GenerateMultiViewPanel";
import { CharacterIdentityPackPanel } from "../features/asset-bible-v2/CharacterIdentityPackPanel";
import { GenerateExpressionPanel } from "../features/asset-bible-v2/GenerateExpressionPanel";
import { VoiceClonePanel } from "../features/asset-bible-v2/VoiceClonePanel";
import { GenerateDetailPanel } from "../features/asset-bible-v2/GenerateDetailPanel";
import { SceneBiblePanel, type SceneReferenceKind } from "../features/asset-bible-v2/SceneBiblePanel";
import { AssetUsagePanel } from "../features/asset-bible-v2/AssetUsagePanel";
import { ReferenceVersionCompare } from "../features/asset-bible-v2/ReferenceVersionCompare";
import { MediaPicker } from "../features/media-picker/MediaPicker";
import { ConceptGuide, Dialog, EmptyState, ErrorState, MediaThumb, Skeleton, StatusBadge } from "../components/ui";
import { queryKeys } from "../query/queryKeys";
import { generateAssetCode, generateMachineCode } from "../features/shared/autoCode";

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
const STATE_KIND_LABELS: Record<string, string> = { CUSTOM: "其他剧情状态", OUTFIT: "服装变化", INJURY: "伤势变化", EMOTION: "情绪变化", TIME_OF_DAY: "时段变化", WEATHER: "天气变化", LIGHTING: "光线变化" };
const REFERENCE_KINDS_BY_ASSET: Record<string, string[]> = {
  CHARACTER: ["HERO", "FRONT", "LEFT", "RIGHT", "BACK", "THREE_VIEW_SHEET", "FULL_BODY", "CLOSEUP", "EXPRESSION_GRID", "OTHER"],
  SCENE: ["HERO", "SCENE_WIDE", "SCENE_REVERSE", "PANORAMA", "LIGHTING_REFERENCE", "OTHER"],
  PROP: ["HERO", "FRONT", "LEFT", "RIGHT", "BACK", "FULL_BODY", "CLOSEUP", "OTHER"],
  COSTUME: ["HERO", "FRONT", "LEFT", "RIGHT", "BACK", "THREE_VIEW_SHEET", "FULL_BODY", "CLOSEUP", "OTHER"],
};

function thumbnailUrl(mediaVersionId: string): string {
  return `/api/v1/media-versions/${encodeURIComponent(mediaVersionId)}/thumbnail?size=small&frame=poster`;
}

export function AssetBiblePage() {
  const { projectId } = useParams();
  const client = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedKind = searchParams.get("kind");
  const tab: (typeof KIND_TABS)[number]["kind"] = KIND_TABS.some((item) => item.kind === requestedKind)
    ? requestedKind as (typeof KIND_TABS)[number]["kind"]
    : "CHARACTER";
  const selectedId = searchParams.get("asset");
  const setAssetContext = (kind: (typeof KIND_TABS)[number]["kind"], assetId?: string | null) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (kind === "CHARACTER") next.delete("kind"); else next.set("kind", kind);
      if (assetId) next.set("asset", assetId); else next.delete("asset");
      return next;
    }, { replace: true });
  };
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [stateLabel, setStateLabel] = useState("");
  const [stateKind, setStateKind] = useState("CUSTOM");
  const [referenceVersionId, setReferenceVersionId] = useState("");
  const [referenceKind, setReferenceKind] = useState("HERO");
  const [referenceStateId, setReferenceStateId] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [lifecycleAction, setLifecycleAction] = useState<{ mode: "ARCHIVE" | "RESTORE"; id: string; revision: number; name: string } | null>(null);
  const [lifecycleReason, setLifecycleReason] = useState("");
  const [lifecycleMessage, setLifecycleMessage] = useState<string | null>(null);

  const assets = useQuery({
    queryKey: queryKeys.assetBible.overview(projectId as string),
    queryFn: () => getAssetBible(projectId as string),
    enabled: Boolean(projectId),
  });
  const bible = assets.data?.bible;
  const visible = (bible?.items ?? []).filter((item) => item.asset.kind === tab);
  const selected = selectedId ? visible.find((item) => item.asset.id === selectedId) ?? null : visible[0] ?? null;
  const allowedReferenceKinds = REFERENCE_KINDS_BY_ASSET[selected?.asset.kind ?? tab] ?? ["HERO", "OTHER"];

  useEffect(() => {
    setReferenceVersionId("");
    setReferenceKind("HERO");
    setReferenceStateId("");
  }, [selected?.asset.id]);

  const refresh = async () => {
    await client.invalidateQueries({ queryKey: queryKeys.assetBible.project(projectId as string) });
    await client.invalidateQueries({ queryKey: queryKeys.assetBible.storyAssets(projectId as string) });
  };

  const create = useMutation({
    mutationFn: () => createStoryAsset(projectId as string, { kind: tab, code: generateAssetCode(tab, newName), name: newName.trim(), description: newDescription.trim() }),
    onSuccess: async (result) => {
      setNewName(""); setNewDescription(""); setFormError(null);
      setAssetContext(tab, result.asset.id);
      setLifecycleMessage("资产已创建。沿准备路径添加主参考并补齐缺失视图后，即可用于镜头生产。");
      await refresh();
    },
    onError: (error) => setFormError(String(error)),
  });

  const archive = useMutation({
    mutationFn: (item: { id: string; revision: number; reason: string }) => archiveStoryAsset(item.id, { expected_revision: item.revision, reason: item.reason }),
    onSuccess: async () => { setLifecycleAction(null); setLifecycleReason(""); setLifecycleMessage("资产已归档；全部绑定与版本历史保留，可在此恢复。"); await refresh(); },
    onError: (error) => setFormError(String(error)),
  });

  const restore = useMutation({
    mutationFn: (item: { id: string; revision: number; reason: string }) => restoreStoryAsset(item.id, { expected_revision: item.revision, reason: item.reason }),
    onSuccess: async () => { setLifecycleAction(null); setLifecycleReason(""); setLifecycleMessage("资料已恢复为启用状态；历史版本未被覆盖。"); await refresh(); },
    onError: (error) => setFormError(String(error)),
  });

  const addState = useMutation({
    mutationFn: () => createStoryAssetState(selected!.asset.id, { code: generateMachineCode(stateKind, stateLabel), label: stateLabel.trim(), state_kind: stateKind }),
    onSuccess: async () => { setStateLabel(""); setFormError(null); await refresh(); },
    onError: (error) => setFormError(String(error)),
  });

  const addReference = useMutation({
    mutationFn: () => createStoryAssetReference(selected!.asset.id, { media_version_id: referenceVersionId.trim(), reference_kind: referenceKind, asset_state_id: referenceStateId || null, is_locked: referenceKind === "HERO" }),
    onSuccess: async () => { setReferenceVersionId(""); setFormError(null); await refresh(); },
    onError: (error) => setFormError(String(error)),
  });

  const createValid = Boolean(newName.trim());
  const stateDisabledReason = addState.isPending ? "状态正在创建，请稍候" : !stateLabel.trim() ? "请填写状态名称" : null;
  const referenceDisabledReason = addReference.isPending ? "参考正在绑定，请稍候" : !referenceVersionId.trim() ? "请先从项目媒体中选择图片" : null;
  const createDisabledReason = create.isPending ? "资产正在创建，请稍候" : !newName.trim() ? "请填写资产名称" : null;
  const chooseSceneReference = (kind: SceneReferenceKind) => {
    setReferenceKind(kind);
    document.getElementById("asset-reference-picker")?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  return (
    <div className="v2-page">
      <div className="panel-heading">
        <div><p className="eyebrow">创作资料库</p><h3>角色、场景、道具与服装</h3></div>
        <StatusBadge tone={assets.isPending ? "running" : "neutral"}>{bible?.asset_count ?? "…"} 项资料</StatusBadge>
      </div>
      <p className="muted">集中保存会在多个镜头中重复使用的角色外观、场景、道具和服装参考，避免同一对象在不同镜头里变样。</p>
      <ConceptGuide title="角色与场景库名词说明" items={[{ term: "主参考", description: "最能代表角色或场景标准外观的一张图，后续生成会优先以它为准。" }, { term: "生产参考", description: "正面、侧面、全身、表情等补充视图，帮助模型从不同角度保持一致。" }, { term: "剧情状态", description: "同一角色或场景在雨夜、受伤、换装等剧情节点下的外观变化，不会覆盖基础资料。" }]} />

      {assets.isPending && <section className="panel"><Skeleton label="正在读取资产、状态、参考与使用情况" lines={5} /></section>}
      {assets.error && <><ErrorState title="角色与场景库暂时无法打开" description={assets.error instanceof Error ? assets.error.message : String(assets.error)} onRetry={() => void assets.refetch()} /><Link className="secondary v2-inline-link" to={`/projects/${projectId}`}>返回项目总览</Link></>}
      {!assets.isPending && !assets.error && <div className="bible-layout">
        <aside className="bible-list" aria-label="资产列表">
          <div className="bible-tabs" role="tablist" aria-label="资产类别">
            {KIND_TABS.map((item) => (
              <button key={item.kind} type="button" role="tab" aria-selected={tab === item.kind} className={tab === item.kind ? "selected" : ""} onClick={() => setAssetContext(item.kind)}>{item.label}</button>
            ))}
          </div>
          <div className="bible-asset-list">
            {visible.map((item) => (
              <button key={item.asset.id} type="button" className={`bible-asset-row${selected?.asset.id === item.asset.id ? " selected" : ""}`} onClick={() => setAssetContext(tab, item.asset.id)}>
                <MediaThumb className="bible-asset-thumbnail" src={item.asset.canonical_media_version_id ? thumbnailUrl(item.asset.canonical_media_version_id) : null} alt={`${item.asset.name} 资产缩略图`} emptyLabel="无图" />
                <span className="bible-asset-name">{item.asset.name}</span>
                <small>{KIND_LABELS[item.asset.kind] ?? item.asset.kind}</small>
              </button>
            ))}
            {visible.length === 0 && <EmptyState title={`还没有${KIND_LABELS[tab] ?? "此类"}资产`} description="使用页面中的“新建资产”区域建立第一项资产；已有媒体可在创建后绑定为参考。" />}
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
                {selected.asset.status === "ARCHIVED"
                  ? <button type="button" className="secondary" disabled={restore.isPending} onClick={() => { setLifecycleAction({ mode: "RESTORE", id: selected.asset.id, revision: selected.asset.revision, name: selected.asset.name }); setLifecycleReason(""); setLifecycleMessage(null); setFormError(null); }}>恢复资产</button>
                  : <button type="button" className="secondary" disabled={archive.isPending} title={archive.isPending ? "正在归档，请稍候" : undefined} onClick={() => { setLifecycleAction({ mode: "ARCHIVE", id: selected.asset.id, revision: selected.asset.revision, name: selected.asset.name }); setLifecycleReason(""); setLifecycleMessage(null); setFormError(null); }}>归档</button>}
              </div>
            </div>
            {lifecycleMessage && <p className="review-success" role="status">{lifecycleMessage}</p>}

            {(() => {
              const hasHero = selected.base_references.some((reference) => reference.reference_kind === "HERO");
              const missing = selected.readiness.missing;
              const nextAction = !hasHero
                ? { label: "选择主参考", target: "asset-reference-picker" }
                : missing.length && selected.asset.kind === "CHARACTER"
                  ? { label: `生成 ${missing.length} 个缺失视图`, target: `multiview-title-${selected.asset.id}` }
                  : missing.length
                    ? { label: "补充缺失参考", target: "asset-reference-picker" }
                    : null;
              return <section className="asset-readiness-path" aria-label="资产生产准备路径">
                <div><span className="status-pill state-ready">1</span><strong>资产已建档</strong><small>系统标识已生成</small></div>
                <div className={hasHero ? "complete" : "current"}><span className="status-pill">2</span><strong>主参考</strong><small>{hasHero ? "已选择并锁定" : "下一步"}</small></div>
                <div className={!hasHero ? "pending" : missing.length ? "current" : "complete"}><span className="status-pill">3</span><strong>生产参考</strong><small>{missing.length ? `缺 ${missing.map((kind) => REF_KIND_LABELS[kind] ?? kind).join("、")}` : "已齐全"}</small></div>
                <div className={missing.length ? "pending" : "complete"}><span className="status-pill">4</span><strong>可用于生产</strong><small>{missing.length ? "仍需补充" : "准备完成"}</small></div>
                {nextAction && <button type="button" className="primary-action" onClick={() => document.getElementById(nextAction.target)?.scrollIntoView({ behavior: "smooth", block: "start" })}>{nextAction.label}</button>}
              </section>;
            })()}

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
                <div className="bible-empty-visual"><EmptyState title="还没有参考图" description={selected.asset.kind === "CHARACTER" ? "先添加一张主参考，再补充或生成正面、侧面等视图。" : "从项目媒体中选择一张能代表标准外观的参考图。"} /></div>
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
              {selected.asset.kind === "CHARACTER" && <VoiceClonePanel
                key={`voice-clone:${selected.asset.id}`}
                projectId={projectId as string}
                assetId={selected.asset.id}
                assetName={selected.asset.name}
                onChanged={refresh}
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
                <div className="panel-heading"><div><p className="eyebrow">造型状态</p><h4>剧情中的外观变化</h4></div></div>
                {selected.states.length === 0 ? <EmptyState title="还没有造型状态" description="状态用于区分服装、伤势、情绪、日夜或光线，不会覆盖基础资产。" /> : (
                  <ul className="bible-state-list">{selected.states.map((state) => <li key={state.id}><strong>{state.label}</strong><span className="muted">{STATE_KIND_LABELS[state.state_kind] ?? "其他剧情状态"}</span><StatusBadge>{state.references.length} 张参考</StatusBadge></li>)}</ul>
                )}
                <details className="story-asset-create"><summary>新增剧情状态</summary><div className="bible-create-grid">
                  <label>变化类型<select value={stateKind} onChange={(event) => setStateKind(event.target.value)}><option value="OUTFIT">服装变化</option><option value="INJURY">伤势变化</option><option value="EMOTION">情绪变化</option><option value="TIME_OF_DAY">时段变化</option><option value="WEATHER">天气变化</option><option value="LIGHTING">光线变化</option><option value="CUSTOM">其他剧情状态</option></select></label>
                  <label>状态名称<input value={stateLabel} onChange={(event) => setStateLabel(event.target.value)} placeholder="例如：雨夜湿发、战损服装" /></label>
                  <button className="secondary" type="button" disabled={Boolean(stateDisabledReason)} title={stateDisabledReason ?? undefined} onClick={() => addState.mutate()}>{addState.isPending ? "创建中…" : "添加剧情状态"}</button>
                </div></details>
              </section>
              <section className="panel">
                <div className="panel-heading"><div><p className="eyebrow">参考图</p><h4>选择或上传项目图片</h4></div></div>
                <p className="muted">选中后会固定到当前媒体版本，今后更新图片也不会改变历史生成结果。列表只读取低分辨率缩略图；主参考会自动锁定，避免误换。</p>
                <MediaPicker projectId={projectId as string} value={referenceVersionId} onChange={setReferenceVersionId} disabled={addReference.isPending} label="资产参考图选择器" />
                <div className="bible-create-grid">
                  <label>参考类型<select value={referenceKind} onChange={(event) => setReferenceKind(event.target.value)}>{allowedReferenceKinds.map((value) => <option key={value} value={value}>{REF_KIND_LABELS[value] ?? value}</option>)}</select></label>
                  <label>绑定状态<select value={referenceStateId} onChange={(event) => setReferenceStateId(event.target.value)}><option value="">基础参考</option>{selected.states.map((state) => <option key={state.id} value={state.id}>{state.label}</option>)}</select></label>
                  <button className="primary-action" type="button" disabled={Boolean(referenceDisabledReason)} title={referenceDisabledReason ?? undefined} onClick={() => addReference.mutate()}>{addReference.isPending ? "绑定中…" : "添加参考"}</button>
                </div>
              </section>
              <section className="panel">
                <div className="panel-heading"><div><p className="eyebrow">准备度</p><h4>生产准备情况</h4></div></div>
                {selected.readiness.missing.length > 0 ? <p className="blocker-text">缺参考：{selected.readiness.missing.map((kind) => REF_KIND_LABELS[kind] ?? kind).join("、")}</p> : <p className="approved-text">参考图齐全</p>}
              </section>
              {selected.voice && (
                <section className="panel">
                  <div className="panel-heading"><div><p className="eyebrow">声音</p><h4>角色声音</h4></div></div>
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
              <label>名称<input value={newName} onChange={(event) => setNewName(event.target.value)} placeholder={`例如：${tab === "CHARACTER" ? "女主角阿宁" : tab === "SCENE" ? "深夜便利店" : tab === "PROP" ? "旧录音机" : "雨夜外套"}`} /></label>
              <label className="wide">补充描述（可选）<textarea value={newDescription} onChange={(event) => setNewDescription(event.target.value)} placeholder="补充外观、用途或在剧情中的作用" /></label>
              <button type="button" className="primary-action" disabled={!createValid || create.isPending} title={createDisabledReason ?? undefined} onClick={() => create.mutate()}>{create.isPending ? "创建中…" : "创建资产"}</button>
              {!createValid && <small className="muted">填写名称即可，系统会自动生成内部标识。</small>}
            </div>
          </details>
          {formError && <p className="inline-error" role="alert">{String(formError)}</p>}
        </aside>
      </div>}

      <div className="v2-actions">
        <Link className="secondary v2-inline-link" to={`/projects/${projectId}`}>返回项目总览</Link>
      </div>
      <Dialog
        open={Boolean(lifecycleAction)}
        title={`${lifecycleAction?.mode === "RESTORE" ? "恢复" : "归档"}资产：${lifecycleAction?.name ?? ""}`}
        onClose={() => { if (!archive.isPending && !restore.isPending) { setLifecycleAction(null); setLifecycleReason(""); } }}
        footer={<><button type="button" className="secondary" onClick={() => { setLifecycleAction(null); setLifecycleReason(""); }} disabled={archive.isPending || restore.isPending}>取消</button><button type="button" className={lifecycleAction?.mode === "ARCHIVE" ? "danger" : "primary-action"} disabled={!lifecycleReason.trim() || archive.isPending || restore.isPending} onClick={() => { if (!lifecycleAction || !lifecycleReason.trim()) return; const payload = { id: lifecycleAction.id, revision: lifecycleAction.revision, reason: lifecycleReason.trim() }; if (lifecycleAction.mode === "ARCHIVE") archive.mutate(payload); else restore.mutate(payload); }}>{archive.isPending || restore.isPending ? "处理中…" : lifecycleAction?.mode === "RESTORE" ? "确认恢复" : "确认归档"}</button></>}
      >
        <p>{lifecycleAction?.mode === "ARCHIVE" ? "归档会停止该资料参与新的镜头绑定，但保留历史绑定、参考、状态和操作记录。" : "恢复会让资料重新参与新的镜头绑定，不会覆盖归档前后的历史版本。"}</p>
        <label>{lifecycleAction?.mode === "ARCHIVE" ? "归档原因" : "恢复原因"}<textarea autoFocus value={lifecycleReason} onChange={(event) => setLifecycleReason(event.target.value)} rows={3} maxLength={500} /></label>
      </Dialog>
    </div>
  );
}
