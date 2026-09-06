import { getShotCharacterPacks, bindShotCharacterPack } from "../asset-bible-v2/identityPackClient";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { bindStoryAssetToShot, createStoryAsset, listShotStoryAssets, listStoryAssets, markShotReadyV2, putShotDraftV2, resolveProfileCameraPlan, unbindStoryAssetFromShot, type CameraPlan, type Profile } from "../../generated/api";
import { readStoryAssetTransfer, STORY_ASSET_MIME, storyAssetDropIssue, type StoryAssetTransfer } from "./storyAssetDrag";
import { Dialog } from "../../components/ui/primitives";
import { CAMERA_CURVES, CAMERA_DIRECTION_LABELS, CAMERA_DIRECTIONS, CAMERA_MOVEMENTS, COMPOSITIONS, SHOT_ASSET_ROLES, SHOT_TYPES } from "../shared/directorOptions";
import { generateAssetCode } from "../shared/autoCode";
import { canonicalCapabilityLabel, normalizeCapability } from "../preferences-v2/canonicalCapabilities";
import { ProfileExecutionDetailButton } from "../model-config/ProfileExecutionDetailButton";
import { statusLabel } from "../shared/optionLabels";

const labels: Record<string, string> = { shot_type: "景别", composition: "构图", subject_action: "主体动作", camera_plan: "镜头运动", target_duration_ms: "时长", dialogue: "对白", environment: "环境", continuity: "连续性", creative_intent: "创作意图" };
const requiredNonEmpty = ["shot_type", "composition", "subject_action", "camera_plan", "target_duration_ms", "continuity", "creative_intent"];
const assetKindLabels: Record<string, string> = { CHARACTER: "角色", SCENE: "场景", PROP: "道具", COSTUME: "服装" };

const emptyCamera: CameraPlan = { mode: "UNSUPPORTED", shot_type: "", movement: "", prompt_text: "", direction: "FORWARD", intensity: 0.5, curve: "LINEAR", profile_version_id: null };

function isVideoProfile(profile: Profile) {
  try {
    return normalizeCapability(profile.capability).startsWith("VIDEO_");
  } catch {
    return false;
  }
}

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
  const [pendingUnbind, setPendingUnbind] = useState<{ bindingId: string; name: string; code: string; kind: string; role: string } | null>(null);
  const [quickCreateOpen, setQuickCreateOpen] = useState(false);
  const [quickKind, setQuickKind] = useState<"CHARACTER" | "SCENE" | "PROP" | "COSTUME">("CHARACTER");
  const [quickName, setQuickName] = useState("");
  const quickCode = generateAssetCode(quickKind, quickName);
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
    onSuccess: async () => { setPendingUnbind(null); await refresh(); },
    onError: (error) => setSectionError(String(error)),
  });
  const upgradePack = useMutation({
    mutationFn: ({ storyAssetId, packVersionId }: { storyAssetId: string; packVersionId: string }) =>
      bindShotCharacterPack(shotId, { story_asset_id: storyAssetId, pack_version_id: packVersionId }),
    onSuccess: async () => { await refresh(); },
    onError: (error) => setSectionError(String(error)),
  });
  const createAndBind = useMutation({
    mutationFn: async () => {
      const name = quickName.trim();
      const code = quickCode.trim();
      if (!name || !code) throw new Error("请填写资产名称和编号");
      const created = await createStoryAsset(projectId, { kind: quickKind, code, name, description: `从镜头 ${shotId} 就地创建`, canonical_media_version_id: null });
      await bindStoryAssetToShot(shotId, { asset_id: created.asset.id, role_in_shot: role.trim() });
      return created.asset;
    },
    onSuccess: async () => {
      setQuickCreateOpen(false); setQuickName(""); setSectionError(null); await refresh();
    },
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
              onClick={() => setPendingUnbind({ bindingId: item.binding_id, name: item.name, code: item.code, kind: item.kind, role: item.role_in_shot })}
            >
              解绑
            </button>
          </div>
          {packInfo && (
            <div className="text-xs text-muted flex items-center gap-2 pl-2 border-l-2 border-slate-600">
              <label>
                身份包版本
                <select
                  aria-label={`身份包版本 ${item.code}`}
                  value={packInfo.identity_pack_version_id ?? ""}
                  disabled={!canEdit || upgradePack.isPending || packInfo.approved_versions.length === 0}
                  onChange={(event) => {
                    const packVersionId = event.target.value;
                    if (packVersionId) {
                      upgradePack.mutate({ storyAssetId: item.asset_id, packVersionId });
                    }
                  }}
                >
                  <option value="">未指定</option>
                  {packInfo.approved_versions.map((version) => (
                    <option key={version.version_id} value={version.version_id}>
              {version.pack_name} · 第 {version.version_no} 版
                    </option>
                  ))}
                </select>
              </label>
              {packInfo.approved_versions.length === 0 && <span>尚无可绑定的已批准身份包</span>}
              {packInfo.is_stale && <span className="text-amber-400">注意：当前绑定不是最新批准版本</span>}
            </div>
          )}
        </li>
      );
    })}</ul> : <p className="muted">本镜头尚未绑定故事资产。</p>}
    {assets.isLoading && <p className="muted" role="status">正在读取项目故事资产…</p>}
    {!assets.isLoading && available.length > 0 && <div className="shot-asset-catalogue" aria-label="可绑定故事资产">{available.map((asset) => <article key={asset.id} draggable={canEdit} onDragStart={(event) => { event.dataTransfer.effectAllowed = "copy"; event.dataTransfer.setData(STORY_ASSET_MIME, JSON.stringify({ id: asset.id, project_id: asset.project_id, kind: asset.kind, code: asset.code, name: asset.name, status: asset.status })); }}>
      {asset.canonical_media_version_id ? <img src={`/api/v1/media-versions/${encodeURIComponent(asset.canonical_media_version_id)}/thumbnail?size=small&frame=poster`} alt="" loading="eager" decoding="async" onError={(e) => { e.currentTarget.style.display = "none"; }} /> : <span className="shot-asset-card-placeholder" aria-hidden="true">{assetKindLabels[asset.kind]?.slice(0, 1) ?? "资"}</span>}
      <div><strong>{asset.name}</strong><small>{asset.code} · {assetKindLabels[asset.kind] ?? asset.kind}</small></div>
      <button type="button" disabled={!canEdit} onClick={() => requestBind(asset)} aria-label={`选择 ${asset.code} ${asset.name}`}>选择</button>
    </article>)}</div>}
    <div className={`shot-asset-drop-slot${dropActive ? " active" : ""}`} aria-label="镜头资产拖放槽" onDragEnter={(event) => { event.preventDefault(); setDropActive(true); }} onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; }} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropActive(false); }} onDrop={(event) => { event.preventDefault(); setDropActive(false); requestBind(readStoryAssetTransfer(event.dataTransfer)); }}><strong>拖到这里绑定到本镜头</strong><span>Drop 只打开确认；跨项目、归档和重复绑定不会提交。</span></div>
    <div className="shot-asset-bind-form">
      <label>绑定资产<select value={selectedAssetId} aria-label="绑定资产" onChange={(event) => setSelectedAssetId(event.target.value)}><option value="">请选择</option>{Object.entries(grouped).map(([kind, items]) => <optgroup key={kind} label={assetKindLabels[kind] ?? kind}>{items.map((item) => <option key={item.id} value={item.id}>{item.code} · {item.name}</option>)}</optgroup>)}</select></label>
      <label>镜头内角色<select value={role} onChange={(event) => setRole(event.target.value)}>{SHOT_ASSET_ROLES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <button type="button" className="secondary" disabled={!canEdit || !selectedAssetId || bind.isPending} title={!canEdit ? "当前权限只读" : undefined} onClick={() => requestBind(selectedAsset)}>{bind.isPending ? "绑定中…" : "绑定到本镜头"}</button>
    </div>
    <details className="shot-asset-quick-create" open={quickCreateOpen} onToggle={(event) => setQuickCreateOpen(event.currentTarget.open)}>
      <summary>资产库里没有？就地创建并绑定</summary>
      <div className="shot-asset-bind-form">
        <label>资产类型<select aria-label="新资产类型" value={quickKind} onChange={(event) => setQuickKind(event.target.value as "CHARACTER" | "SCENE" | "PROP" | "COSTUME")}>{Object.entries(assetKindLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>资产名称<input aria-label="新资产名称" value={quickName} onChange={(event) => setQuickName(event.target.value)} placeholder="例如：女主角阿宁" /></label>
        <div className="field-fact"><span>资产编号</span><strong>{quickCode || "填写名称后自动生成"}</strong></div>
        <button type="button" className="secondary" disabled={!canEdit || !quickName.trim() || !quickCode.trim() || createAndBind.isPending} onClick={() => createAndBind.mutate()}>{createAndBind.isPending ? "创建并绑定中…" : "创建并绑定到本镜头"}</button>
      </div>
      <p className="muted">先创建项目级故事资产，再用当前“镜头内角色”绑定；任一步失败都会显示原因，不会伪装完成。</p>
    </details>
    <p className="muted">确认后调用正式镜头资产命令；服务端继续裁决项目归属、资产状态、重复绑定与镜头当前生产状态。</p>
    <Dialog open={Boolean(pendingAsset)} title={pendingAsset ? `确认绑定 ${pendingAsset.name}？` : "确认绑定资产"} onClose={() => setPendingAsset(null)} footer={pendingAsset ? <><button type="button" onClick={() => setPendingAsset(null)}>取消</button><button type="button" className="secondary" disabled={bind.isPending} onClick={() => bind.mutate({ assetId: pendingAsset.id, targetRole: role.trim() })}>{bind.isPending ? "绑定中…" : "确认绑定"}</button></> : undefined}>
      {pendingAsset && <><p>{pendingAsset.code} · {assetKindLabels[pendingAsset.kind] ?? pendingAsset.kind} · 镜头内角色：{role.trim()}</p><p>这会新增可审计的镜头资产绑定，不会复制资产或媒体。</p></>}
    </Dialog>
    <Dialog open={Boolean(pendingUnbind)} title={pendingUnbind ? `确认解绑 ${pendingUnbind.name}？` : "确认解绑资产"} onClose={() => setPendingUnbind(null)} footer={pendingUnbind ? <><button type="button" onClick={() => setPendingUnbind(null)}>取消</button><button type="button" className="secondary" disabled={unbind.isPending} onClick={() => unbind.mutate(pendingUnbind.bindingId)}>{unbind.isPending ? "解绑中…" : "确认解绑"}</button></> : undefined}>
      {pendingUnbind && <><p>{pendingUnbind.code} · {assetKindLabels[pendingUnbind.kind] ?? pendingUnbind.kind} · 镜头内角色：{pendingUnbind.role}</p><p>这会移除当前镜头的资产关系并写入审计；不会删除资产或媒体，之后仍可重新绑定。</p></>}
    </Dialog>
    {(sectionError || bindings.error || assets.error) && <p className="inline-error" role="alert">{String(sectionError ?? bindings.error ?? assets.error)}</p>}
  </div>;
}

export function DirectorShotEditor({ shot, profiles = [], onChanged, projectId }: { shot: Record<string, unknown> | undefined; profiles?: Profile[]; onChanged: () => void; projectId?: string | null }) {
  const current = shot?.current_revision && typeof shot.current_revision === "object" ? shot.current_revision as Record<string, unknown> : {};
  const [fields, setFields] = useState<Record<string, string>>({});
  const [camera, setCamera] = useState<CameraPlan>(emptyCamera);
  const [freeze, setFreeze] = useState(true);
  const publishedProfiles = useMemo(() => profiles.filter((item) => item.status === "PUBLISHED" && isVideoProfile(item)), [profiles]);
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
  const expectedRevisionNo = typeof current.revision_no === "number" ? current.revision_no : undefined;
  const save = useMutation({ mutationFn: () => putShotDraftV2(String(shot?.id), { fields: { ...fields, target_duration_ms: Number(fields.target_duration_ms), ...(cameraResolved ? { camera_plan: camera } : {}) }, freeze, expected_revision_no: expectedRevisionNo }), onSuccess: onChanged });
  const ready = useMutation({ mutationFn: () => markShotReadyV2(String(shot?.id), { expected_revision_no: expectedRevisionNo }), onSuccess: onChanged });
  if (!shot) return <section className="panel director-editor"><p className="empty-state">选择镜头后编辑导演分镜。</p></section>;
  return <section className="panel director-editor" aria-labelledby="director-editor-title">
    <div className="panel-heading"><div><p className="eyebrow">不可变镜头版本</p><h3 id="director-editor-title">导演分镜字段</h3></div><span className="status-pill">{String(shot.code)} · {statusLabel(readiness?.state ?? String(shot.status))}</span></div>
    <div className="director-grid">
      <label>{labels.shot_type}<select value={fields.shot_type ?? ""} onChange={(event) => update("shot_type", event.target.value)}><option value="">请选择</option>{SHOT_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>{labels.composition}<select value={fields.composition ?? ""} onChange={(event) => update("composition", event.target.value)}><option value="">请选择</option>{fields.composition && !COMPOSITIONS.some(([value]) => value === fields.composition) && <option value={fields.composition}>历史构图（旧数据）</option>}{COMPOSITIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>{labels.subject_action}<textarea value={fields.subject_action ?? ""} onChange={(event) => update("subject_action", event.target.value)} placeholder="描述人物或物体在这一镜中的动作" /></label>
      <fieldset className="camera-plan-editor"><legend>{labels.camera_plan}计划</legend>
        <label>已发布模型配置<select value={camera.profile_version_id ?? ""} onChange={(event) => updateCamera({ profile_version_id: event.target.value || null })}><option value="">请选择</option>{publishedProfiles.map((profile) => <option value={profile.version_id} key={profile.version_id}>{profile.title} · {canonicalCapabilityLabel(profile.capability)}</option>)}</select></label><ProfileExecutionDetailButton profileVersionId={camera.profile_version_id} />
        <label>运动<select value={camera.movement} onChange={(event) => updateCamera({ movement: event.target.value })}><option value="">请选择</option>{CAMERA_MOVEMENTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>方向<select value={camera.direction} onChange={(event) => updateCamera({ direction: event.target.value })}>{CAMERA_DIRECTIONS.map((value) => <option key={value} value={value}>{CAMERA_DIRECTION_LABELS[value]}</option>)}</select></label>
        <label>运镜强度 <span>{Math.round(camera.intensity * 100)}%</span><input aria-label="运镜强度" type="range" min="0" max="1" step="0.1" value={camera.intensity} onChange={(event) => updateCamera({ intensity: Number(event.target.value) })} /></label>
        <label>运动节奏<select value={camera.curve} onChange={(event) => updateCamera({ curve: event.target.value })}>{CAMERA_CURVES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        {camera.mode === "PROMPT_FALLBACK" && <label>兼容运镜补充描述<textarea value={camera.prompt_text} onChange={(event) => updateCamera({ prompt_text: event.target.value })} placeholder="通常无需填写；只补充预设无法表达的特殊路径" /></label>}
        <button type="button" className="secondary" disabled={resolve.isPending || !camera.profile_version_id || !fields.shot_type || !camera.movement} onClick={() => resolve.mutate()}>{resolve.isPending ? "判断中…" : "按生成配置检查运镜能力"}</button>
        <p className={`camera-resolution ${cameraRunnable ? "ready" : "blocked"}`}><strong>{camera.mode === "NATIVE" ? "模型原生支持" : camera.mode === "PROMPT_FALLBACK" ? "提示词兼容" : "等待检查"}</strong>{camera.mode === "NATIVE" ? "，可进入生产" : camera.mode === "PROMPT_FALLBACK" ? "，可进入生产" : "；当前生成配置不支持时不能进入生产"}</p>
        {resolve.error && <p className="inline-error" role="alert">{resolve.error.message}</p>}
      </fieldset>
      <label>时长（秒）<input type="number" min="0.1" max="600" step="0.1" value={fields.target_duration_ms ? Number(fields.target_duration_ms) / 1000 : ""} onChange={(event) => update("target_duration_ms", String(Math.round(Number(event.target.value) * 1000)))} /></label>
      <label>{labels.dialogue}<textarea value={fields.dialogue ?? ""} onChange={(event) => update("dialogue", event.target.value)} /></label>
      <label>{labels.environment}<textarea value={fields.environment ?? ""} onChange={(event) => update("environment", event.target.value)} /></label>
      <label>{labels.continuity}<textarea value={fields.continuity ?? ""} onChange={(event) => update("continuity", event.target.value)} /></label>
      <label>{labels.creative_intent}<textarea value={fields.creative_intent ?? ""} onChange={(event) => update("creative_intent", event.target.value)} /></label>
    </div>
    {projectId && shot && <ShotAssetSection projectId={projectId} shotId={String(shot.id)} />}
    <div className="director-actions"><label className="checkbox-row"><input type="checkbox" checked={freeze} onChange={(event) => setFreeze(event.target.checked)} />保存时冻结版本</label><button type="button" className="secondary" disabled={save.isPending} onClick={() => save.mutate()}>{save.isPending ? "保存中…" : "保存新版本"}</button><button type="button" className="primary-action" disabled={ready.isPending || missing.length > 0 || shot.status !== "DIRECTED"} onClick={() => ready.mutate()}>{ready.isPending ? "校验中…" : "标记为可进入生产"}</button></div>
    <p className={missing.length ? "review-guidance" : "review-success"} role="status">{missing.length ? `还缺 ${missing.length} 项：${missing.map((key) => labels[key]).join("、")}` : shot.status === "DIRECTED" ? "九项字段完整，结构化运镜已通过生成配置检查，可以标记为可进入生产。" : "九项字段完整；当前状态已由服务器记录。"}</p>
    {readiness?.blockers && readiness.blockers.length > 0 && <p className="muted">服务端阻塞：{readiness.blockers.join("、")}</p>}
    {(save.error || ready.error) && <p className="inline-error" role="alert">{String(save.error ?? ready.error)}</p>}
  </section>;
}
