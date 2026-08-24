import { type FormEvent, useEffect, useMemo, useState } from "react";
import { Dialog, Drawer, StatusBadge } from "../../components/ui";
import { authorizeWorkspaceAsset } from "../../generated/api";
import { MediaPicker } from "../media-picker/MediaPicker";
import { generateMachineCode } from "../shared/autoCode";
import {
  type CharacterIdentityPack,
  type CharacterIdentityPackVersion,
  type IdentityPackVersionComparison,
  type IdentityPackVersionImpact,
  approveCharacterIdentityPackVersion,
  compareCharacterIdentityPackVersions,
  createCharacterIdentityPack,
  createCharacterIdentityPackVersion,
  getCharacterIdentityPack,
  getCharacterIdentityPackVersion,
  getCharacterIdentityPackVersionImpact,
  listCharacterIdentityPacks,
  removeCharacterIdentityPackSlot,
  retireCharacterIdentityPackVersion,
  setCharacterIdentityPackSlot,
} from "./identityPackClient";
import "./character-identity-pack.css";

const STANDARD_SLOTS = [
  { kind: "FRONT", label: "正面", angle: "0°", required: true },
  { kind: "LEFT", label: "左侧", angle: "−90°", required: true },
  { kind: "RIGHT", label: "右侧", angle: "+90°", required: true },
  { kind: "BACK", label: "背面", angle: "180°", required: false },
  { kind: "FACE", label: "面部特写", angle: "近景", required: false },
] as const;

const IMMUTABLE_STATUSES = new Set(["APPROVED", "SUPERSEDED", "RETIRED"]);

const STATUS_LABELS: Record<string, string> = {
  DRAFT: "草稿",
  READY_FOR_REVIEW: "待审核",
  APPROVED: "已批准",
  REJECTED: "已拒绝",
  SUPERSEDED: "已被新版替代",
  RETIRED: "已废弃",
};

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function thumbnailUrl(mediaVersionId: string): string {
  return `/api/v1/media-versions/${encodeURIComponent(mediaVersionId)}/thumbnail?size=small&frame=poster`;
}

export interface CharacterIdentityPackPanelProps {
  projectId: string;
  storyAssetId: string;
  assetName: string;
  initialPackId?: string;
  onVersionApproved?: (versionId: string) => void;
  onRequestMissingSlots?: (missingSlots: string[]) => void;
}

export function CharacterIdentityPackPanel({
  projectId,
  storyAssetId,
  assetName,
  initialPackId,
  onVersionApproved,
  onRequestMissingSlots,
}: CharacterIdentityPackPanelProps) {
  const [packs, setPacks] = useState<CharacterIdentityPack[]>([]);
  const [selectedPackId, setSelectedPackId] = useState<string | null>(initialPackId ?? null);
  const [activePack, setActivePack] = useState<CharacterIdentityPack | null>(null);
  const [activeVersion, setActiveVersion] = useState<CharacterIdentityPackVersion | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isCreatingPack, setIsCreatingPack] = useState(false);
  const [newPackName, setNewPackName] = useState("");
  const [pickerSlot, setPickerSlot] = useState<string | null>(null);
  const [pendingMediaId, setPendingMediaId] = useState("");
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [approvalComment, setApprovalComment] = useState("");
  const [retireOpen, setRetireOpen] = useState(false);
  const [retireReason, setRetireReason] = useState("");
  const [comparison, setComparison] = useState<IdentityPackVersionComparison | null>(null);
  const [impact, setImpact] = useState<IdentityPackVersionImpact | null>(null);

  const refreshPacks = async (preferredPackId?: string) => {
    const response = await listCharacterIdentityPacks(storyAssetId);
    setPacks(response.items);
    const nextId = preferredPackId ?? selectedPackId;
    const selected = response.items.find((pack) => pack.id === nextId) ?? response.items[0] ?? null;
    setSelectedPackId(selected?.id ?? null);
    if (!selected) {
      setActivePack(null);
      setActiveVersion(null);
    }
  };

  const loadVersionDetails = async (versionId: string) => {
    setLoading(true);
    setError(null);
    setComparison(null);
    setImpact(null);
    try {
      const response = await getCharacterIdentityPackVersion(versionId);
      setActiveVersion(response.version);
    } catch (requestError) {
      setError(errorText(requestError, "获取身份包版本失败"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    setError(null);
    void listCharacterIdentityPacks(storyAssetId)
      .then((response) => {
        if (!mounted) return;
        setPacks(response.items);
        const selected = response.items.find((pack) => pack.id === initialPackId) ?? response.items[0] ?? null;
        setSelectedPackId(selected?.id ?? null);
        if (!selected) {
          setActivePack(null);
          setActiveVersion(null);
        }
      })
      .catch((requestError) => mounted && setError(errorText(requestError, "加载角色身份包失败")))
      .finally(() => mounted && setLoading(false));
    return () => { mounted = false; };
  }, [initialPackId, storyAssetId]);

  useEffect(() => {
    if (!selectedPackId) return;
    let mounted = true;
    setLoading(true);
    setError(null);
    void getCharacterIdentityPack(selectedPackId)
      .then(async (response) => {
        if (!mounted) return;
        setActivePack(response.pack);
        const selectedVersion = response.pack.versions?.find((version) => version.id === response.pack.current_version_id)
          ?? response.pack.versions?.[0]
          ?? null;
        if (selectedVersion) {
          const details = await getCharacterIdentityPackVersion(selectedVersion.id);
          if (mounted) setActiveVersion(details.version);
        } else if (mounted) {
          setActiveVersion(null);
        }
      })
      .catch((requestError) => mounted && setError(errorText(requestError, "获取身份包详情失败")))
      .finally(() => mounted && setLoading(false));
    return () => { mounted = false; };
  }, [selectedPackId]);

  const reloadActivePack = async (versionId?: string) => {
    if (!activePack) return;
    const response = await getCharacterIdentityPack(activePack.id);
    setActivePack(response.pack);
    await refreshPacks(activePack.id);
    if (versionId) await loadVersionDetails(versionId);
  };

  const handleCreatePack = async (event: FormEvent) => {
    event.preventDefault();
    const generatedCode = generateMachineCode("LOOK", newPackName);
    if (!generatedCode || !newPackName.trim()) return;
    setLoading(true); setError(null);
    try {
      const response = await createCharacterIdentityPack(storyAssetId, {
        project_id: projectId,
        code: generatedCode,
        name: newPackName.trim(),
      });
      setIsCreatingPack(false); setNewPackName("");
      await refreshPacks(response.pack.id);
    } catch (requestError) {
      setError(errorText(requestError, "创建身份包失败"));
    } finally {
      setLoading(false);
    }
  };

  const handleCreateDraft = async () => {
    if (!activePack) return;
    setLoading(true); setError(null);
    try {
      const response = await createCharacterIdentityPackVersion(activePack.id, activeVersion?.id);
      await reloadActivePack(response.version.id);
    } catch (requestError) {
      setError(errorText(requestError, "创建草稿版本失败"));
    } finally {
      setLoading(false);
    }
  };

  const confirmSlot = async () => {
    if (!activeVersion || !pickerSlot || !pendingMediaId) return;
    setLoading(true); setError(null);
    try {
      // This is an explicit operator action: never authorize media on picker
      // selection alone, and never couple it to pack approval.
      await authorizeWorkspaceAsset(projectId, pendingMediaId);
      const response = await setCharacterIdentityPackSlot(activeVersion.id, {
        slot_kind: pickerSlot,
        media_version_id: pendingMediaId,
      });
      setActiveVersion(response.version);
      setPickerSlot(null); setPendingMediaId("");
    } catch (requestError) {
      setError(errorText(requestError, "设置参考槽位失败"));
    } finally {
      setLoading(false);
    }
  };

  const removeSlot = async (slotKind: string) => {
    if (!activeVersion) return;
    setLoading(true); setError(null);
    try {
      setActiveVersion((await removeCharacterIdentityPackSlot(activeVersion.id, slotKind)).version);
    } catch (requestError) {
      setError(errorText(requestError, "移除参考槽位失败"));
    } finally {
      setLoading(false);
    }
  };

  const approve = async () => {
    if (!activeVersion || !activePack || !approvalComment.trim()) return;
    setLoading(true); setError(null);
    try {
      const response = await approveCharacterIdentityPackVersion(activeVersion.id, approvalComment.trim());
      setApprovalOpen(false); setApprovalComment("");
      await reloadActivePack(response.version.id);
      onVersionApproved?.(response.version.id);
    } catch (requestError) {
      setError(errorText(requestError, "批准身份包版本失败"));
    } finally {
      setLoading(false);
    }
  };

  const compare = async () => {
    if (!activeVersion || !activePack) return;
    const other = activePack.versions?.find((version) => version.id !== activeVersion.id);
    if (!other) return;
    setLoading(true); setError(null);
    try {
      setComparison((await compareCharacterIdentityPackVersions(other.id, activeVersion.id)).comparison);
    } catch (requestError) {
      setError(errorText(requestError, "比较身份包版本失败"));
    } finally {
      setLoading(false);
    }
  };

  const inspectImpact = async () => {
    if (!activeVersion) return;
    setLoading(true); setError(null);
    try {
      setImpact((await getCharacterIdentityPackVersionImpact(activeVersion.id)).impact);
    } catch (requestError) {
      setError(errorText(requestError, "读取版本影响失败"));
    } finally {
      setLoading(false);
    }
  };

  const retire = async () => {
    if (!activeVersion || !retireReason.trim()) return;
    setLoading(true); setError(null);
    try {
      const response = await retireCharacterIdentityPackVersion(activeVersion.id, retireReason.trim());
      setRetireOpen(false); setRetireReason("");
      await reloadActivePack(response.version.id);
    } catch (requestError) {
      setError(errorText(requestError, "废弃身份包版本失败"));
    } finally {
      setLoading(false);
    }
  };

  const missingSlots = activeVersion?.missing_required_slots ?? STANDARD_SLOTS
    .filter((slot) => slot.required && !activeVersion?.slots_map?.[slot.kind])
    .map((slot) => slot.kind);
  const requiredUnique = new Set(
    STANDARD_SLOTS
      .filter((slot) => slot.required)
      .map((slot) => activeVersion?.slots_map?.[slot.kind])
      .filter(Boolean),
  ).size === 3;
  const approvalReady = Boolean(activeVersion?.approval_ready ?? (missingSlots.length === 0 && requiredUnique));
  const approvalBlockers = activeVersion?.approval_blockers ?? [];
  const editable = Boolean(activeVersion && !IMMUTABLE_STATUSES.has(activeVersion.status));
  const versionOptions = useMemo(() => activePack?.versions ?? [], [activePack]);

  return <section className="identity-pack-panel" aria-labelledby={`identity-pack-title-${storyAssetId}`}>
    <header className="identity-pack-header">
      <div>
        <p className="eyebrow">角色一致性</p>
        <h4 id={`identity-pack-title-${storyAssetId}`}>角色参考 · {assetName}</h4>
        <p className="muted">为不同造型补齐正面与左右侧参考。批准后，后续镜头会稳定沿用这一版外观。</p>
      </div>
      <button type="button" className="secondary" onClick={() => { setNewPackName(packs.length === 0 ? "基础造型" : "新造型"); setIsCreatingPack(true); }} disabled={loading || isCreatingPack}>新建造型</button>
    </header>

    {isCreatingPack && <form className="identity-pack-create" onSubmit={(event) => void handleCreatePack(event)}>
      <label>造型名称<input value={newPackName} onChange={(event) => setNewPackName(event.target.value)} placeholder="例如：基础造型、雨夜造型" required /></label>
      <div className="identity-pack-create-actions">
        <button type="submit" className="primary-action" disabled={loading || !newPackName.trim()}>创建并开始补参考图</button>
        <button type="button" className="secondary" onClick={() => setIsCreatingPack(false)}>取消</button>
      </div>
    </form>}

    {error && <p className="inline-error" role="alert">{error}</p>}
    {loading && <p className="muted" role="status">正在更新身份包…</p>}

    {!loading && packs.length === 0 && <div className="identity-pack-empty">
      <strong>还没有角色造型参考</strong>
      <p>先新建一个基础造型，再从项目图片中补齐正面和左右侧参考。</p>
    </div>}

    {packs.length > 0 && <>
      <div className="identity-pack-controls">
        <label>造型方案<select value={selectedPackId ?? ""} onChange={(event) => setSelectedPackId(event.target.value)}>{packs.map((pack) => <option key={pack.id} value={pack.id}>{pack.name}</option>)}</select></label>
        <div className="identity-pack-version-bar" role="list" aria-label="身份包版本">
          {versionOptions.map((version) => <button
            key={version.id}
            type="button"
            className={`version-pill${activeVersion?.id === version.id ? " active" : ""}${version.status === "APPROVED" ? " approved" : ""}`}
            onClick={() => void loadVersionDetails(version.id)}
            aria-current={activeVersion?.id === version.id ? "true" : undefined}
          >v{version.version_no} · {STATUS_LABELS[version.status] ?? version.status}</button>)}
        </div>
      </div>

      {activeVersion && <>
        <div className="identity-pack-version-summary">
          <div>
            <strong>版本 v{activeVersion.version_no}</strong>
            <StatusBadge tone={activeVersion.status === "APPROVED" ? "success" : activeVersion.status === "DRAFT" ? "attention" : "neutral"}>{STATUS_LABELS[activeVersion.status] ?? activeVersion.status}</StatusBadge>
            {activeVersion.content_hash && <code title={activeVersion.content_hash}>内容 {activeVersion.content_hash.slice(0, 12)}…</code>}
          </div>
          <div className="identity-pack-version-actions">
            <button type="button" className="secondary" onClick={() => void handleCreateDraft()} disabled={loading}>从此版本建新草稿</button>
            <button type="button" className="secondary" onClick={() => void compare()} disabled={loading || versionOptions.length < 2}>版本比较</button>
            <button type="button" className="secondary" onClick={() => void inspectImpact()} disabled={loading}>影响分析</button>
            {activeVersion.status !== "RETIRED" && <button type="button" className="danger" onClick={() => setRetireOpen(true)} disabled={loading}>废弃版本</button>}
          </div>
        </div>

        {missingSlots.length > 0 && <div className="identity-pack-blocker" role="status">
          <div><strong>还不能批准</strong><span>缺少必需视角：{missingSlots.join(" / ")}</span></div>
          <button type="button" className="secondary" onClick={() => onRequestMissingSlots?.(missingSlots)} disabled={!onRequestMissingSlots}>生成缺失三视图</button>
        </div>}

        {missingSlots.length === 0 && approvalBlockers.length > 0 && <div className="identity-pack-blocker" role="alert">
          <div><strong>媒体证据未通过门禁</strong><span>{approvalBlockers.map((blocker) => `${blocker.slot_kind ? `${blocker.slot_kind}：` : ""}${blocker.message}`).join("；")}</span></div>
        </div>}

        <div className="identity-slots-grid">
          {STANDARD_SLOTS.map((slot) => {
            const mediaId = activeVersion.slots_map?.[slot.kind];
            const slotRecord = activeVersion.slots?.find((item) => item.slot_kind === slot.kind);
            return <article key={slot.kind} className={`identity-slot-card${slot.required ? " required" : ""}${mediaId ? " filled" : ""}`} data-testid={`slot-${slot.kind}`}>
              <div className="identity-slot-title"><strong>{slot.label}</strong><span>{slot.kind} · {slot.angle}</span></div>
              <div className="identity-slot-preview">
                {mediaId ? <img src={thumbnailUrl(mediaId)} alt={`${assetName} ${slot.label}参考缩略图`} loading="lazy" decoding="async" /> : <div className="identity-slot-empty"><strong>{slot.required ? "必需参考缺失" : "可选参考"}</strong><span>{slot.required ? "选择图片或生成此视角" : "可补充更多身份细节"}</span></div>}
              </div>
              {mediaId && <div className="identity-slot-evidence"><span>{slotRecord?.integrity_status ?? "VERIFIED"}</span><span>{slotRecord?.authorization_status === "AUTHORIZED" ? "项目授权有效" : slotRecord?.authorization_status === "REVOKED" ? "项目授权已撤回" : "授权状态待校验"}</span><details><summary>版本证据</summary><code>{mediaId}</code></details></div>}
              {editable && <div className="identity-slot-actions">
                <button type="button" className="secondary" onClick={() => { setPickerSlot(slot.kind); setPendingMediaId(mediaId ?? ""); }}>{mediaId ? "替换图片" : "选择图片"}</button>
                {mediaId && <button type="button" className="danger" onClick={() => void removeSlot(slot.kind)} disabled={loading}>移除</button>}
              </div>}
            </article>;
          })}
        </div>

        {editable && <div className="identity-pack-approval-bar">
          <div><strong>{approvalReady ? "三视图已齐全" : "批准门禁未通过"}</strong><span>{approvalReady ? "仍需人工填写审核说明并确认锁定。" : "FRONT / LEFT / RIGHT 必须是三个不同的 VERIFIED 图片版本。"}</span></div>
          <button type="button" className="primary-action" onClick={() => setApprovalOpen(true)} disabled={!approvalReady || loading} title={!approvalReady ? "请先补齐三个不同的必需视角" : undefined}>人工审核并批准</button>
        </div>}

        {comparison && <section className="identity-pack-result" aria-label="版本比较结果">
          <div><strong>v{comparison.base.version_no} → v{comparison.target.version_no}</strong><button type="button" className="secondary" onClick={() => setComparison(null)}>关闭</button></div>
          <p>新增 {comparison.slots.added.length} · 移除 {comparison.slots.removed.length} · 替换 {comparison.slots.changed.length} · 未变 {comparison.slots.unchanged.length}</p>
          {comparison.slots.changed.length > 0 && <ul>{comparison.slots.changed.map((item) => <li key={item.slot_kind}>{item.slot_kind}：媒体版本已替换</li>)}</ul>}
        </section>}

        {impact && <section className="identity-pack-result" aria-label="版本影响分析">
          <div><strong>影响分析 · v{impact.version_no}</strong><button type="button" className="secondary" onClick={() => setImpact(null)}>关闭</button></div>
          <p>镜头绑定 {impact.summary.shot_binding_count} · 生成候选 {impact.summary.generation_variant_count} · 运行中任务 {impact.summary.running_job_count}</p>
          {impact.shots.length > 0 && <ul>{impact.shots.slice(0, 8).map((shot) => <li key={`${shot.shot_id}:${shot.story_asset_id}`}>{shot.episode_code} / {shot.shot_code} · {shot.role_in_shot}</li>)}</ul>}
        </section>}
      </>}
    </>}

    <Drawer open={Boolean(pickerSlot)} onClose={() => { setPickerSlot(null); setPendingMediaId(""); }} title={`选择 ${pickerSlot ?? ""} 参考图`} width={560} footer={<><button type="button" className="secondary" onClick={() => { setPickerSlot(null); setPendingMediaId(""); }}>取消</button><button type="button" className="primary-action" onClick={() => void confirmSlot()} disabled={!pendingMediaId || loading}>{loading ? "校验并绑定中…" : "授权并绑定此版本"}</button></>}>
      <p className="muted">只显示当前项目的 IMAGE 媒体。点击“授权并绑定”会校验文件哈希并登记项目授权，再保存确切 MediaVersion；不会批准身份包。</p>
      <MediaPicker projectId={projectId} value={pendingMediaId} onChange={setPendingMediaId} disabled={loading} label={`${pickerSlot ?? "视角"}项目图片选择器`} />
    </Drawer>

    <Dialog open={approvalOpen} onClose={() => setApprovalOpen(false)} title={`批准身份包 v${activeVersion?.version_no ?? ""}`} footer={<><button type="button" className="secondary" onClick={() => setApprovalOpen(false)}>取消</button><button type="button" className="primary-action" onClick={() => void approve()} disabled={!approvalReady || !approvalComment.trim() || loading}>{loading ? "批准中…" : "确认批准并锁定"}</button></>}>
      <p>此操作会冻结 FRONT / LEFT / RIGHT 及其他槽位的媒体、授权与内容哈希。后续修改必须创建新版本，系统不会自动批准。</p>
      <label className="identity-pack-dialog-field">审核说明<textarea value={approvalComment} onChange={(event) => setApprovalComment(event.target.value)} placeholder="记录角色外观、一致性和适用剧情状态的人工判断" required /></label>
    </Dialog>

    <Dialog open={retireOpen} onClose={() => setRetireOpen(false)} title={`废弃身份包 v${activeVersion?.version_no ?? ""}`} footer={<><button type="button" className="secondary" onClick={() => setRetireOpen(false)}>取消</button><button type="button" className="danger" onClick={() => void retire()} disabled={!retireReason.trim() || loading}>{loading ? "处理中…" : "确认废弃"}</button></>}>
      <p>废弃不会删除历史证据；所有引用该版本的镜头和生成候选会显示失效。请先查看影响分析。</p>
      <label className="identity-pack-dialog-field">废弃原因<textarea value={retireReason} onChange={(event) => setRetireReason(event.target.value)} placeholder="例如：角色定妆变更，旧版不再用于新镜头" required /></label>
    </Dialog>
  </section>;
}
