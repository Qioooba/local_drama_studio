import { getShotCharacterPacks, bindShotCharacterPack } from "../asset-bible-v2/identityPackClient";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { bindStoryAssetToShot, createShotRevision, listShotStoryAssets, listStoryAssets, markShotProductionReady, resolveProfileCameraPlan, unbindStoryAssetFromShot, type CameraPlan, type Profile } from "../../generated/api";
import { readStoryAssetTransfer, STORY_ASSET_MIME, storyAssetDropIssue, type StoryAssetTransfer } from "./storyAssetDrag";

const labels: Record<string, string> = { shot_type: "景别", composition: "构图", subject_action: "主体动作", camera_plan: "镜头运动", target_duration_ms: "时长", dialogue: "对白", environment: "环境", continuity: "连续性", creative_intent: "创作意图" };
const requiredNonEmpty = ["shot_type", "composition", "subject_action", "camera_plan", "target_duration_ms", "continuity", "creative_intent"];
const assetKindLabels: Record<string, string> = { CHARACTER: "角色", SCENE: "场景", PROP: "道具", COSTUME: "服装" };

const emptyCamera: CameraPlan = { mode: "UNSUPPORTED", shot_type: "", movement: "", prompt_text: "", direction: "FORWARD", intensity: 0.5, curve: "LINEAR", profile_version_id: null };

function cameraFrom(value: unknown): CameraPlan {
  if (!value || typeof value !== "object") return emptyCamera;
  const item = value as Partial<CameraPlan>;
  return { ...emptyCamera, ...item, mode: item.mode ?? "UNSUPPORTED" };
}

export function ShotAssetSection({ projectId, shotId, canEdit = true }: { projectId: string; shotId: string; canEdit?: boolean }) {
  const client = useQueryClient();
  const assets = useQuery({ queryKey: ["story-assets", projectId], queryFn: () => listStoryAssets(projectId), enabled: Boolean(projectId) });
  const bindings = useQuery({ queryKey: ["shot-story-assets", shotId], queryFn: () => listShotStoryAssets(shotId), enabled: Boolean(shotId) });
  const charPacks = useQuery({ queryKey: ["shot-character-packs", shotId], queryFn: () => getShotCharacterPacks(shotId), enabled: Boolean(shotId) });
  const [selectedAssetId, setSelectedAssetId] = useState("");
  const [role, setRole] = useState("main");
  const [sectionError, setSectionError] = useState<string | null>(null);
  const [dropActive, setDropActive] = useState(false);
  const [pendingAsset, setPendingAsset] = useState<StoryAssetTransfer | null>(null);
  const bound = bindings.data?.items ?? [];
  const boundAssetIds = new Set(bound.map((item) => item.asset_id));
  const available = (assets.data?.items ?? []).filter((item) => item.status === "ACTIVE" && !boundAssetIds.has(item.id));
  const grouped = available.reduce<Record<string, typeof available>>((acc, item) => { (acc[item.kind] ??= []).push(item); return acc; }, {});
  const selectedAsset = available.find((item) => item.id === selectedAssetId) ?? null;
  const refresh = async () => { await client.invalidateQueries({ queryKey: ["shot-story-assets", shotId] }); await client.invalidateQueries({ queryKey: ["story-assets", projectId] }); await client.invalidateQueries({ queryKey: ["shot-character-packs", shotId] }); };
  const bind = useMutation({
    mutationFn: ({ assetId, targetRole }: { assetId: string; targetRole: string }) => bindStoryAssetToShot(shotId, { asset_id: assetId, role_in_shot: targetRole }),
    onSuccess: async () => { setSelectedAssetId(""); setPendingAsset(null); setSectionError(null); await refresh(); },
    onError: (error) => setSectionError(String(error)),
  });
  const unbind = useMutation({
    mutationFn: (bindingId: string) => unbindStoryAssetFromShot(bindingId),
    onSuccess: async () => { await refresh(); },
    onError: (error) => setSectionError(String(error)),
  });
  const upgradePack = useMutation({
    mutationFn: ({ storyAssetId, packVersionId }: { storyAssetId: string; packVersionId: string }) =>
      bindShotCharacterPack(shotId, { story_asset_id: storyAssetId, pack_version_id: packVersionId }),
    onSuccess: async () => { await refresh(); },
    onError: (error) => setSectionError(String(error)),
  });
  const requestBind = (asset: StoryAssetTransfer | null) => {
    if (!canEdit) { setSectionError("当前权限只读，不能绑定镜头资产"); return; }
    if (!role.trim()) { setSectionError("镜头内角色不能为空"); return; }
    if (!asset) { setSectionError("请选择要绑定的故事资产"); return; }
    const issue = storyAssetDropIssue(asset, projectId, boundAssetIds);
    if (issue) { setSectionError(issue); return; }
    setSectionError(null);
    setSelectedAssetId(asset.id);
    setPendingAsset(asset);
  };
  return <div className="shot-asset-section" aria-label="故事资产">
    <h4>故事资产</h4>
    {bound.length > 0 ? <ul className="shot-asset-bindings">{bound.map((item) => {
      const packInfo = charPacks.data?.items?.find((p) => p.story_asset_id === item.asset_id);
      return (
        <li key={item.binding_id} className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <strong>{item.name}</strong>
            <code>{item.code}</code>
            <span>{assetKindLabels[item.kind] ?? item.kind}</span>
            <span className={item.status === "ARCHIVED" ? "status-pill archived" : "status-pill"}>
              {item.status === "ARCHIVED" ? "已归档" : "启用中"}
            </span>
            <span>角色：{item.role_in_shot}</span>
            <button
              type="button"
              className="secondary"
              disabled={!canEdit || unbind.isPending}
              title={!canEdit ? "当前权限只读" : undefined}
              onClick={() => unbind.mutate(item.binding_id)}
            >
              解绑
            </button>
          </div>
          {packInfo && (
            <div className="text-xs text-muted flex items-center gap-2 pl-2 border-l-2 border-slate-600">
              <span>
                身份包：{packInfo.pack_name || "未指定"} (v{packInfo.bound_version_no || 1})
              </span>
              {packInfo.is_stale && packInfo.latest_approved_version_id && (
                <span className="flex items-center gap-1 text-amber-400">
                  ⚠️ 发现更新的批准版本
                  <button
                    type="button"
                    className="btn btn-xs btn-warning ml-1"
                    disabled={!canEdit || upgradePack.isPending}
                    onClick={() =>
                      upgradePack.mutate({
                        storyAssetId: item.asset_id,
                        packVersionId: packInfo.latest_approved_version_id!,
                      })
                    }
                  >
                    升级至最新版
                  </button>
                </span>
              )}
            </div>
          )}
        </li>
      );
    })}</ul> : <p className="muted">本镜头尚未绑定故事资产。</p>}
    {assets.isLoading && <p className="muted" role="status">正在读取项目故事资产…</p>}
    {!assets.isLoading && available.length > 0 && <div className="shot-asset-catalogue" aria-label="可绑定故事资产">{available.map((asset) => <article key={asset.id} draggable={canEdit} onDragStart={(event) => { event.dataTransfer.effectAllowed = "copy"; event.dataTransfer.setData(STORY_ASSET_MIME, JSON.stringify({ id: asset.id, project_id: asset.project_id, kind: asset.kind, code: asset.code, name: asset.name, status: asset.status })); }}>
      {asset.canonical_media_version_id ? <img src={`/api/v1/media-versions/${encodeURIComponent(asset.canonical_media_version_id)}/thumbnail?size=small&frame=poster`} alt="" loading="lazy" decoding="async" /> : <span className="shot-asset-card-placeholder" aria-hidden="true">{assetKindLabels[asset.kind]?.slice(0, 1) ?? "资"}</span>}
      <div><strong>{asset.name}</strong><small>{asset.code} · {assetKindLabels[asset.kind] ?? asset.kind}</small></div>
      <button type="button" disabled={!canEdit} onClick={() => requestBind(asset)} aria-label={`选择 ${asset.code} ${asset.name}`}>选择</button>
    </article>)}</div>}
    <div className={`shot-asset-drop-slot${dropActive ? " active" : ""}`} aria-label="镜头资产拖放槽" onDragEnter={(event) => { event.preventDefault(); setDropActive(true); }} onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; }} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropActive(false); }} onDrop={(event) => { event.preventDefault(); setDropActive(false); requestBind(readStoryAssetTransfer(event.dataTransfer)); }}><strong>拖到这里绑定到本镜头</strong><span>Drop 只打开确认；跨项目、归档和重复绑定不会提交。</span></div>
    <div className="shot-asset-bind-form">
      <label>绑定资产<select value={selectedAssetId} aria-label="绑定资产" onChange={(event) => setSelectedAssetId(event.target.value)}><option value="">请选择</option>{Object.entries(grouped).map(([kind, items]) => <optgroup key={kind} label={assetKindLabels[kind] ?? kind}>{items.map((item) => <option key={item.id} value={item.id}>{item.code} · {item.name}</option>)}</optgroup>)}</select></label>
      <label>镜头内角色<input value={role} onChange={(event) => setRole(event.target.value)} /></label>
      <button type="button" className="secondary" disabled={!canEdit || !selectedAssetId || bind.isPending} title={!canEdit ? "当前权限只读" : undefined} onClick={() => requestBind(selectedAsset)}>{bind.isPending ? "绑定中…" : "绑定到本镜头"}</button>
    </div>
    <p className="muted">确认后调用正式镜头资产命令；服务端继续裁决项目归属、资产状态、重复绑定与镜头当前生产状态。</p>
    {pendingAsset && <div className="shot-asset-confirm" role="alertdialog" aria-modal="true" aria-labelledby="shot-asset-confirm-title"><strong id="shot-asset-confirm-title">确认绑定 {pendingAsset.name}？</strong><span>{pendingAsset.code} · {assetKindLabels[pendingAsset.kind] ?? pendingAsset.kind} · 镜头内角色：{role.trim()}</span><p>这会新增可审计的镜头资产绑定，不会复制资产或媒体。</p><div><button type="button" autoFocus onClick={() => setPendingAsset(null)}>取消</button><button type="button" className="secondary" disabled={bind.isPending} onClick={() => bind.mutate({ assetId: pendingAsset.id, targetRole: role.trim() })}>{bind.isPending ? "绑定中…" : "确认绑定"}</button></div></div>}
    {(sectionError || bindings.error || assets.error) && <p className="inline-error" role="alert">{String(sectionError ?? bindings.error ?? assets.error)}</p>}
  </div>;
}

export function DirectorShotEditor({ shot, profiles = [], onChanged, projectId }: { shot: Record<string, unknown> | undefined; profiles?: Profile[]; onChanged: () => void; projectId?: string | null }) {
  const current = shot?.current_revision && typeof shot.current_revision === "object" ? shot.current_revision as Record<string, unknown> : {};
  const [fields, setFields] = useState<Record<string, string>>({});
  const [camera, setCamera] = useState<CameraPlan>(emptyCamera);
  const [freeze, setFreeze] = useState(true);
  const publishedProfiles = useMemo(() => profiles.filter((item) => item.status === "PUBLISHED"), [profiles]);
  const readiness = shot?.production_readiness && typeof shot.production_readiness === "object" ? shot.production_readiness as { state?: string; blockers?: string[] } : undefined;
  useEffect(() => {
    setFields(Object.fromEntries(Object.keys(labels).filter((key) => key !== "camera_plan").map((key) => [key, current[key] === undefined || current[key] === null ? "" : String(current[key])])))
    const restored = cameraFrom(current.camera_plan);
    setCamera({ ...restored, profile_version_id: restored.profile_version_id ?? publishedProfiles[0]?.version_id ?? null });
  }, [shot?.id, shot?.current_revision_id, publishedProfiles]);
  const cameraResolved = Boolean(camera.profile_version_id && camera.shot_type && camera.movement && camera.direction && camera.curve);
  const cameraRunnable = cameraResolved && camera.mode !== "UNSUPPORTED";
  const missing = useMemo(() => Object.keys(labels).filter((key) => key === "camera_plan"
    ? !cameraRunnable
    : requiredNonEmpty.includes(key) ? !fields[key]?.trim() || (key === "target_duration_ms" && Number(fields[key]) <= 0) : fields[key] === undefined), [cameraRunnable, fields]);
  const update = (key: string, value: string) => setFields((existing) => ({ ...existing, [key]: value }));
  const updateCamera = (change: Partial<CameraPlan>) => setCamera((existing) => ({ ...existing, ...change, mode: "UNSUPPORTED", prompt_text: change.prompt_text ?? existing.prompt_text }));
  const resolve = useMutation({
    mutationFn: () => resolveProfileCameraPlan(String(camera.profile_version_id), { shot_type: fields.shot_type, movement: camera.movement, direction: camera.direction, intensity: camera.intensity, curve: camera.curve, prompt_text: camera.prompt_text }),
    onSuccess: ({ resolution }) => setCamera(resolution.camera_plan),
  });
  const save = useMutation({ mutationFn: () => createShotRevision(String(shot?.id), { ...fields, target_duration_ms: Number(fields.target_duration_ms), ...(cameraResolved ? { camera_plan: camera } : {}) }, freeze), onSuccess: onChanged });
  const ready = useMutation({ mutationFn: () => markShotProductionReady(String(shot?.id)), onSuccess: onChanged });
  if (!shot) return <section className="panel director-editor"><p className="empty-state">选择镜头后编辑导演分镜。</p></section>;
  return <section className="panel director-editor" aria-labelledby="director-editor-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-WRT-003/005 · 不可变 revision</p><h3 id="director-editor-title">导演分镜字段</h3></div><span className="status-pill">{String(shot.code)} · {readiness?.state ?? String(shot.status)}</span></div>
    <div className="director-grid">
      <label>{labels.shot_type}<select value={fields.shot_type ?? ""} onChange={(event) => update("shot_type", event.target.value)}><option value="">请选择</option>{["ESTABLISHING", "WIDE", "MEDIUM", "CLOSEUP", "INSERT", "POV", "OTHER"].map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>{labels.composition}<input value={fields.composition ?? ""} onChange={(event) => update("composition", event.target.value)} /></label>
      <label>{labels.subject_action}<input value={fields.subject_action ?? ""} onChange={(event) => update("subject_action", event.target.value)} /></label>
      <fieldset className="camera-plan-editor"><legend>{labels.camera_plan} · CameraPlan</legend>
        <label>已发布 Profile<select value={camera.profile_version_id ?? ""} onChange={(event) => updateCamera({ profile_version_id: event.target.value || null })}><option value="">请选择</option>{publishedProfiles.map((profile) => <option value={profile.version_id} key={profile.version_id}>{profile.title} · {profile.capability}</option>)}</select></label>
        <label>运动<select value={camera.movement} onChange={(event) => updateCamera({ movement: event.target.value })}><option value="">请选择</option>{["STATIC", "PUSH_IN", "PULL_OUT", "PAN", "TILT", "TRUCK", "PEDESTAL", "ZOOM", "ORBIT", "ROLL"].map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>方向<input value={camera.direction} onChange={(event) => updateCamera({ direction: event.target.value })} /></label>
        <label>强度（0—1）<input type="number" min="0" max="1" step="0.1" value={camera.intensity} onChange={(event) => {
          const raw = event.target.value;
          if (raw === "") { updateCamera({ intensity: 0.5 }); return; }
          const normalized = raw.replace(/^(-?)0+(?=\d)/, "$1");
          const parsed = Number(normalized);
          updateCamera({ intensity: Number.isFinite(parsed) ? Math.min(1, Math.max(0, parsed)) : 0.5 });
        }} /></label>
        <label>运动曲线<select value={camera.curve} onChange={(event) => updateCamera({ curve: event.target.value })}>{["LINEAR", "EASE_IN", "EASE_OUT", "EASE_IN_OUT"].map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>Prompt 降级文本<textarea value={camera.prompt_text} onChange={(event) => updateCamera({ prompt_text: event.target.value })} placeholder="只有 Profile 明确声明 PROMPT_FALLBACK 时使用" /></label>
        <button type="button" className="secondary" disabled={resolve.isPending || !camera.profile_version_id || !fields.shot_type || !camera.movement} onClick={() => resolve.mutate()}>{resolve.isPending ? "裁决中…" : "按 Profile 裁决运镜能力"}</button>
        <p className={`camera-resolution ${cameraRunnable ? "ready" : "blocked"}`}><strong>{camera.mode}</strong>{camera.mode === "NATIVE" ? "原生参数映射，可进入生产" : camera.mode === "PROMPT_FALLBACK" ? "显式 Prompt 降级，可进入生产" : "当前未裁决或 Profile 不支持，禁止进入生产"}</p>
        {resolve.error && <p className="inline-error" role="alert">{resolve.error.message}</p>}
      </fieldset>
      <label>{labels.target_duration_ms}<input type="number" min="1" value={fields.target_duration_ms ?? ""} onChange={(event) => update("target_duration_ms", event.target.value)} /></label>
      <label>{labels.dialogue}<textarea value={fields.dialogue ?? ""} onChange={(event) => update("dialogue", event.target.value)} /></label>
      <label>{labels.environment}<textarea value={fields.environment ?? ""} onChange={(event) => update("environment", event.target.value)} /></label>
      <label>{labels.continuity}<textarea value={fields.continuity ?? ""} onChange={(event) => update("continuity", event.target.value)} /></label>
      <label>{labels.creative_intent}<textarea value={fields.creative_intent ?? ""} onChange={(event) => update("creative_intent", event.target.value)} /></label>
    </div>
    {projectId && shot && <ShotAssetSection projectId={projectId} shotId={String(shot.id)} />}
    <div className="director-actions"><label className="checkbox-row"><input type="checkbox" checked={freeze} onChange={(event) => setFreeze(event.target.checked)} />保存时冻结 revision</label><button type="button" className="secondary" disabled={save.isPending} onClick={() => save.mutate()}>{save.isPending ? "保存中…" : "保存新 revision"}</button><button type="button" className="primary-action" disabled={ready.isPending || missing.length > 0 || shot.status !== "DIRECTED"} onClick={() => ready.mutate()}>{ready.isPending ? "校验中…" : "标记 Production Ready"}</button></div>
    <p className={missing.length ? "review-guidance" : "review-success"} role="status">{missing.length ? `还缺 ${missing.length} 项：${missing.map((key) => labels[key]).join("、")}` : shot.status === "DIRECTED" ? "九项字段完整，结构化运镜已通过 Profile 裁决，可显式标记 Production Ready。" : `九项字段完整；当前状态 ${readiness?.state ?? String(shot.status)}。`}</p>
    {readiness?.blockers && readiness.blockers.length > 0 && <p className="muted">服务端阻塞：{readiness.blockers.join("、")}</p>}
    {(save.error || ready.error) && <p className="inline-error" role="alert">{String(save.error ?? ready.error)}</p>}
  </section>;
}
