import { useEffect, useState } from "react";
import { getRef2VaCapability, listProductionTiers, type H3ProductionTier, type H3Ref2VaCapability } from "../../generated/api";
import { GenerationEstimateFact } from "../generation-estimate/GenerationEstimateFact";
import { MotionMaskEditor, PerformanceBindingEditor, ReferenceBindingEditor, TimedDirectionEditor } from "./GenerationControlEditors";

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
  allowedTierCodes?: string[];
  projectId?: string;
};

// Fallback ordering so the selector still works if the endpoint is unreachable;
// real labels/parameters come from GET /production-tiers.
const TIER_FALLBACK: Array<{ code: string; label: string }> = [
  { code: "FAST", label: "极速粗筛（速度优先）" },
  { code: "DRAFT", label: "日常生成（主力候选）" },
  { code: "SCREEN", label: "候选精筛（质量优先）" },
  { code: "PRODUCTION", label: "正式成片" },
  { code: "MASTER", label: "关键镜头精制" },
];

export function GenerationControlPanel({ timedDirections, performanceBindings, motionMasks, referenceBindings, onTimedDirectionsChange, onPerformanceBindingsChange, onMotionMasksChange, onReferenceBindingsChange, tier = "", onTierChange, profileVersionId = "", profileLabel = "", aspectRatio, allowedTierCodes, projectId }: Props) {
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

  const allTierOptions = tiers ?? TIER_FALLBACK;
  const tierOptions = allowedTierCodes?.length ? allTierOptions.filter((item) => allowedTierCodes.includes(item.code)) : allTierOptions;
  const selectedTier = tier ? tierOptions.find((item) => item.code === tier) ?? null : null;
  const selectedTierDetail = tier && tiers ? tiers.find((item) => item.code === tier) ?? null : null;
  const allowedTierLabels = allowedTierCodes
    ?.map((code) => allTierOptions.find((item) => item.code === code)?.label)
    .filter((label): label is string => Boolean(label));
  const estimateResolution = selectedTierDetail && aspectRatio ? selectedTierDetail.resolution[aspectRatio] : undefined;
  const estimateRequest = profileVersionId && estimateResolution?.[0] && estimateResolution?.[1] ? {
    profileVersionId,
    width: estimateResolution[0],
    height: estimateResolution[1],
    durationSeconds: selectedTierDetail?.duration_seconds,
    frameCount: selectedTierDetail?.frames,
    steps: selectedTierDetail?.steps,
  } : null;

  return <section className="generation-control-panel" aria-labelledby="generation-controls-title"><div className="section-title"><span id="generation-controls-title">导演控制与多种参考输入</span><small>保存到当前生成候选，不会改动原始素材</small></div>
    <div className="structured-control-grid">
      <TimedDirectionEditor value={timedDirections} onChange={onTimedDirectionsChange} projectId={projectId} />
      <PerformanceBindingEditor value={performanceBindings} onChange={onPerformanceBindingsChange} projectId={projectId} />
      <ReferenceBindingEditor value={referenceBindings} onChange={onReferenceBindingsChange} projectId={projectId} />
      <MotionMaskEditor value={motionMasks} onChange={onMotionMasksChange} projectId={projectId} />
    </div>
    <div className="production-tier-row">
      <label htmlFor="production-tier">生产档位<select id="production-tier" value={tier} onChange={(event) => onTierChange?.(event.target.value)}>
        <option value="">不指定（手动参数）</option>
        {tierOptions.map((item) => <option key={item.code} value={item.code}>{item.label}</option>)}
      </select><small>{allowedTierLabels?.length ? `当前生成流程只支持：${allowedTierLabels.join("、")}；其他不兼容档位已隐藏。` : "生产档位由当前模型能力提供；选择后会采用对应的分辨率与帧数。"}</small></label>
      {selectedTier && <div className="tier-summary" role="group" aria-label="生产档位参数摘要">{selectedTierDetail ? <dl><div><dt>帧数</dt><dd>{selectedTierDetail.frames}</dd></div><div><dt>分辨率</dt><dd>9:16 {selectedTierDetail.resolution["9:16"]?.join("×")} / 16:9 {selectedTierDetail.resolution["16:9"]?.join("×")}</dd></div><div><dt>默认生成数量</dt><dd>{selectedTierDetail.default_takes}</dd></div><div><dt>画面重绘强度</dt><dd>{selectedTierDetail.denoise}</dd></div><div><dt>生成迭代次数</dt><dd>{selectedTierDetail.steps}</dd></div></dl> : <p className="muted">档位参数加载中…</p>}</div>}
      {tiersError && <p className="inline-error" role="alert">生产档位读取失败：{tiersError}。提交不会自动附加档位。</p>}
      <GenerationEstimateFact profileLabel={profileLabel || null} request={estimateRequest} />
    </div>
    <div className="ref2v-capability-row" aria-label="参考视频生成能力">
      <strong>参考视频生成能力</strong>
      {ref2va === null ? <span className="status-pill neutral">读取中</span> : ref2va.supported ? <><span className="status-pill">试验性支持</span><small>本机已具备基础能力，但参考视频输入仍在评估，本期暂不开放提交。</small></> : <><span className="status-pill neutral">当前不可用</span><small>本机尚未安装所需的参考视频模型。</small></>}
      {ref2va?.manifest_hint?.ref2va_unet_name && <details><summary>高级：查看本机模型信息</summary><small className="muted">模型加载项：{ref2va.manifest_hint.ref2va_unet_name}</small></details>}
    </div>
    <p className="muted">这里保存的是可复用的导演控制设置。局部重绘、扩图、姿态控制、口型同步和动作驱动能否真正执行，取决于当前已发布生成配置支持的能力。</p></section>;
}
