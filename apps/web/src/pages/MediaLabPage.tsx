import { ComfyLabPanel } from "../features/shared/ComfyLabPanel";

/** Expert-only sandbox. Formal production inputs remain in Director/Asset flows. */
export function MediaLabPage() {
  return (
    <div className="v2-page">
      <div className="panel-heading">
        <div><p className="eyebrow">工具区</p><h2>素材实验室</h2></div>
        <span className="status-pill neutral">本地沙盒</span>
      </div>
      <p className="muted">验证本机 Comfy 工作流和能力合同；实验结果不会自动进入正式资产、候选、采用或审核事实。</p>
      <ComfyLabPanel />
    </div>
  );
}
