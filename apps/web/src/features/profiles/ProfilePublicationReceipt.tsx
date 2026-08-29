import { canonicalCapabilityLabel } from "../preferences-v2/canonicalCapabilities";
import "./profile-publication-receipt.css";

export interface ProfilePublicationReceiptData {
  profileVersionId: string;
  profileCode?: string;
  versionNo?: number;
  capability: string;
  model?: string;
  provider?: string;
  publishedAt?: string;
}

export function ProfilePublicationTarget({ capability, model }: { capability: string; model?: string }) {
  return (
    <section className="profile-publication-target" aria-labelledby="profile-publication-target-title">
      <div className="profile-publication-target__marker" aria-hidden="true" />
      <div>
        <small>发布位置</small>
        <strong id="profile-publication-target-title">本机全局能力目录</strong>
        <span>
          发布后，所有项目都可以选择“{canonicalCapabilityLabel(capability)}”
          {model ? `（${model}）` : ""}；不会上传模型文件，也不会生成公网地址。
        </span>
      </div>
    </section>
  );
}

export function ProfilePublicationReceipt({
  receipt,
  onLocate,
}: {
  receipt: ProfilePublicationReceiptData;
  onLocate?: (receipt: ProfilePublicationReceiptData) => void;
}) {
  return (
    <section className="profile-publication-receipt" role="status" aria-labelledby="profile-publication-receipt-title">
      <header>
        <span className="profile-publication-receipt__check" aria-hidden="true" />
        <div>
          <h4 id="profile-publication-receipt-title">发布完成</h4>
          <small>该版本已经进入本机全局能力目录</small>
        </div>
      </header>
      <dl>
        <div><dt>发布到</dt><dd>系统 / 能力与模型 / 能力目录</dd></div>
        <div><dt>使用范围</dt><dd>本机全局 · 所有项目可选</dd></div>
        <div><dt>创作能力</dt><dd>{canonicalCapabilityLabel(receipt.capability)}</dd></div>
        {receipt.model ? <div><dt>模型</dt><dd>{receipt.model}</dd></div> : null}
        <div><dt>版本</dt><dd>{receipt.versionNo ? `v${receipt.versionNo}` : receipt.profileVersionId.slice(0, 12)}</dd></div>
      </dl>
      {onLocate ? (
        <button type="button" className="secondary" onClick={() => onLocate(receipt)}>
          在能力目录中定位
        </button>
      ) : null}
    </section>
  );
}
