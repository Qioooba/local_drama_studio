import { useEffect, useState } from "react";
import { getRef2VaCapability, listProductionTiers, type H3ProductionTier, type H3Ref2VaCapability } from "../../generated/api";
import { GenerationEstimateFact } from "../generation-estimate/GenerationEstimateFact";

type Props = {
  timedDirections: string;
  performanceBindings: string;
  motionMasks: string;
  referenceBindings: string;
  onTimedDirectionsChange: (value: string) => void;
  onPerformanceBindingsChange: (value: string) => void;
  onMotionMasksChange: (value: string) => void;
  onReferenceBindingsChange: (value: string) => void;
  tier?: string;
  onTierChange?: (tierCode: string) => void;
  profileVersionId?: string;
  profileLabel?: string;
  aspectRatio?: "9:16" | "16:9";
};

// Fallback ordering so the selector still works if the endpoint is unreachable;
// real labels/parameters come from GET /production-tiers.
const TIER_FALLBACK: Array<{ code: string; label: string }> = [
  { code: "FAST", label: "FAST" },
  { code: "DRAFT", label: "DRAFT" },
  { code: "SCREEN", label: "SCREEN" },
  { code: "PRODUCTION", label: "PRODUCTION" },
  { code: "MASTER", label: "MASTER" },
];

export function GenerationControlPanel({ timedDirections, performanceBindings, motionMasks, referenceBindings, onTimedDirectionsChange, onPerformanceBindingsChange, onMotionMasksChange, onReferenceBindingsChange, tier = "", onTierChange, profileVersionId = "", profileLabel = "", aspectRatio }: Props) {
  const [tiers, setTiers] = useState<H3ProductionTier[] | null>(null);
  const [tiersError, setTiersError] = useState<string | null>(null);
  const [ref2va, setRef2va] = useState<H3Ref2VaCapability | null>(null);

  useEffect(() => {
    if (typeof listProductionTiers !== "function") return;
    void listProductionTiers()
      .then((response) => setTiers(response.items))
      .catch((error) => setTiersError(error instanceof Error ? error.message : "生产档位读取失败"));
  }, []);
  useEffect(() => {
    if (typeof getRef2VaCapability !== "function") return;
    void getRef2VaCapability()
      .then((response) => setRef2va(response.capability))
      .catch(() => setRef2va(null));
  }, []);

  const tierOptions = tiers ?? TIER_FALLBACK;
  const selectedTier = tier ? tierOptions.find((item) => item.code === tier) ?? null : null;
  const selectedTierDetail = tier && tiers ? tiers.find((item) => item.code === tier) ?? null : null;
  const estimateResolution = selectedTierDetail && aspectRatio ? selectedTierDetail.resolution[aspectRatio] : undefined;
  const estimateRequest = profileVersionId && estimateResolution?.[0] && estimateResolution?.[1] ? {
    profileVersionId,
    width: estimateResolution[0],
    height: estimateResolution[1],
    durationSeconds: selectedTierDetail?.duration_seconds,
    frameCount: selectedTierDetail?.frames,
    steps: selectedTierDetail?.steps,
  } : null;

  return <section className="generation-control-panel" aria-labelledby="generation-controls-title"><div className="section-title"><span id="generation-controls-title">导演控制与多模态输入</span><small>写入冻结 Variant 参数，不修改源媒体</small></div>
    <div className="field-grid">
      <label>TimedDirection JSON<textarea value={timedDirections} onChange={(event) => onTimedDirectionsChange(event.target.value)} spellCheck={false} placeholder='[{"time_us":0,"direction":"PAN_LEFT","strength":0.5}]' /><small>时间戳、方向、强度；需由 Profile 显式声明能力。</small></label>
      <label>PerformanceBinding JSON<textarea value={performanceBindings} onChange={(event) => onPerformanceBindingsChange(event.target.value)} spellCheck={false} placeholder='[{"actor_id":"character-1","action":"walk","start_us":0,"end_us":2000000,"binding_type":"CHARACTER_DRIVING","source_role":"DRIVING_VIDEO"}]' /><small>驱动视频 / 姿态 / 口型绑定先保存为不可变语义，并指向同一 Variant 的来源 role。</small></label>
      <label>驱动与参考 MediaVersion JSON<textarea value={referenceBindings} onChange={(event) => onReferenceBindingsChange(event.target.value)} spellCheck={false} placeholder='[{"role":"DRIVING_VIDEO","media_version_id":"本地版本ID","ordinal":0,"weight":0.7},{"role":"CHARACTER_REFERENCE","media_version_id":"本地版本ID","ordinal":0,"weight":0.3}]' /><small>只填写项目内已注册 MediaVersion；角色、数量、顺序、媒体类型和 weight 由已发布 Profile input contract 校验。</small></label>
      <label>MotionMask JSON<textarea value={motionMasks} onChange={(event) => onMotionMasksChange(event.target.value)} spellCheck={false} placeholder='[{"media_version_id":"mask-v1","subject_role":"face","invert":false}]' /><small>Mask 只能引用项目内已验证媒体版本；能力未声明时提交被拒。</small></label>
    </div>
    <div className="production-tier-row">
      <label htmlFor="production-tier">生产档位<select id="production-tier" value={tier} onChange={(event) => onTierChange?.(event.target.value)}>
        <option value="">不指定（手动参数）</option>
        {tierOptions.map((item) => <option key={item.code} value={item.code}>{tiers ? `${item.code} · ${(item as H3ProductionTier).label}` : item.label}</option>)}
      </select><small>P1-7 档位来自 GET /production-tiers；选中后按档位覆盖 H3 编译时的分辨率与帧数。</small></label>
      {selectedTier && <div className="tier-summary" role="group" aria-label="生产档位参数摘要">{selectedTierDetail ? <dl><div><dt>帧数</dt><dd>{selectedTierDetail.frames}</dd></div><div><dt>分辨率</dt><dd>9:16 {selectedTierDetail.resolution["9:16"]?.join("×")} / 16:9 {selectedTierDetail.resolution["16:9"]?.join("×")}</dd></div><div><dt>默认 take</dt><dd>{selectedTierDetail.default_takes}</dd></div><div><dt>denoise</dt><dd>{selectedTierDetail.denoise}</dd></div><div><dt>steps</dt><dd>{selectedTierDetail.steps}</dd></div></dl> : <p className="muted">档位参数加载中…</p>}</div>}
      {tiersError && <p className="inline-error" role="alert">生产档位读取失败：{tiersError}。提交不会自动附加档位。</p>}
      <GenerationEstimateFact profileLabel={profileLabel || null} request={estimateRequest} />
    </div>
    <div className="ref2v-capability-row" aria-label="Ref2V 参考视频能力">
      <strong>Ref2V 参考视频能力</strong>
      {ref2va === null ? <span className="status-pill neutral">读取中</span> : ref2va.supported ? <><span className="status-pill">H3_REF2VA_CANDIDATE</span><small>参考图 Ref2V 原生链可编译；参考视频输入位待集成评估，本期不开放提交。</small></> : <><span className="status-pill neutral">H3_REF2VA_UNAVAILABLE</span><small>置灰原因：{ref2va.reason}</small></>}
      {ref2va?.manifest_hint?.ref2va_unet_name && <small className="muted">manifest 加载器：{ref2va.manifest_hint.ref2va_unet_name}</small>}
    </div>
    <p className="muted">当前只保存结构化控制快照；实际 inpaint/outpaint、pose、lip-sync 或 driving 执行由已发布 Profile 的 capability contract 决定。</p></section>;
}
