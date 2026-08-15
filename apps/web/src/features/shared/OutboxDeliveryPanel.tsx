import { useState } from "react";
import { deliverOutboxEvents } from "../../generated/api";

export function OutboxDeliveryPanel({ projectId }: { projectId?: string | null }) {
  const [endpoint, setEndpoint] = useState("http://127.0.0.1:8765/hooks/local-drama");
  const [limit, setLimit] = useState("50");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Awaited<ReturnType<typeof deliverOutboxEvents>>["delivery"] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const send = async () => {
    setBusy(true); setError(null); setResult(null);
    try { const response = await deliverOutboxEvents({ endpoint_url: endpoint.trim(), project_id: projectId ?? undefined, limit: Number(limit) }); setResult(response.delivery); }
    catch (caught) { setError(String(caught)); }
    finally { setBusy(false); }
  };
  return <section className="panel outbox-delivery-panel" aria-labelledby="outbox-delivery-title"><div className="panel-heading"><div><p className="eyebrow">FR-AUT-002 · LOOPBACK OUTBOX</p><h3 id="outbox-delivery-title">本机自动化事件投递</h3></div><span className="status-pill neutral">LOOPBACK ONLY</span></div><p className="muted">只允许显式填写 loopback 接收端；每次投递有上限、状态和审计。公网地址会由服务端拒绝，不会自动重试或联网探测。</p><div className="field-grid"><label>接收端 URL<input value={endpoint} onChange={(event) => setEndpoint(event.target.value)} /></label><label>批量上限<input type="number" min="1" max="100" value={limit} onChange={(event) => setLimit(event.target.value)} /></label></div><button className="secondary" type="button" onClick={() => void send()} disabled={busy || !endpoint.trim()}>{busy ? "投递中…" : "投递未发送事件"}</button>{result && <div className="review-meta"><span>状态：{result.status}</span><span>成功：{result.delivered_count}</span><span>失败：{result.failed.length}</span><span>remote_transport_allowed={String(result.remote_transport_allowed)}</span></div>}{error && <p className="inline-error" role="alert">事件投递失败：{error}</p>}</section>;
}
