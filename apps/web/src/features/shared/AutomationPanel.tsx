import { useState } from "react";
import {
  createAutomationClient,
  createWebhookSubscription,
  deliverWebhookEvents,
  listWebhookDeliveries,
  listWebhookSubscriptions,
  retryWebhookDelivery,
} from "../../generated/api";
import { generateMachineCode } from "./autoCode";

export function AutomationPanel({ projectId }: { projectId?: string | null }) {
  const [title, setTitle] = useState("本机自动化");
  const [token, setToken] = useState("");
  const [endpoint, setEndpoint] = useState("http://127.0.0.1:8765/hooks/local-drama");
  const [subscriptionId, setSubscriptionId] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [subscriptions, setSubscriptions] = useState<Awaited<ReturnType<typeof listWebhookSubscriptions>>["items"]>([]);
  const [deliveries, setDeliveries] = useState<Awaited<ReturnType<typeof listWebhookDeliveries>>["items"]>([]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError(null); setMessage(null);
    try { await action(); } catch (caught) { setError(String(caught)); } finally { setBusy(false); }
  };
  const createClient = () => void run(async () => {
    const code = generateMachineCode("automation-client", title).toLowerCase().replace(/_/g, "-");
    const response = await createAutomationClient({ code, title: title.trim(), project_id: projectId ?? undefined, scopes: ["read", "plan", "submit", "review", "delivery"] });
    setToken(response.client.token ?? "");
    setMessage("Client 已创建。token 只在此刻显示一次，请复制到本机自动化脚本；服务端只保存 hash。");
  });
  const createSubscription = () => void run(async () => {
    const response = await createWebhookSubscription(token.trim(), { endpoint_url: endpoint.trim(), project_id: projectId ?? undefined });
    setSubscriptionId(response.subscription.id);
    setMessage("Webhook 已创建。签名 secret 只在此刻返回，回调仍严格限制 loopback。");
    setSubscriptions((items) => [response.subscription, ...items]);
  });
  const refresh = () => void run(async () => {
    const [subscriptionResponse, deliveryResponse] = await Promise.all([listWebhookSubscriptions(token.trim()), listWebhookDeliveries(token.trim(), { subscription_id: subscriptionId || undefined, limit: 100 })]);
    setSubscriptions(subscriptionResponse.items); setDeliveries(deliveryResponse.items); setMessage("已读取本机订阅和投递状态。");
  });
  const deliver = () => void run(async () => {
    const response = await deliverWebhookEvents(token.trim(), { subscription_id: subscriptionId || undefined, project_id: projectId ?? undefined, limit: 100 });
    setMessage(`投递状态：${response.delivery.status}；成功 ${response.delivery.delivered_count}，失败 ${response.delivery.failed.length}，死信 ${response.delivery.dead_letter_delivery_ids.length}。`);
    const deliveryResponse = await listWebhookDeliveries(token.trim(), { subscription_id: subscriptionId || undefined, limit: 100 });
    setDeliveries(deliveryResponse.items);
  });
  const retry = (deliveryId: string) => void run(async () => {
    const response = await retryWebhookDelivery(token.trim(), deliveryId);
    setMessage(`显式重试：${response.delivery.status}。`);
    const deliveryResponse = await listWebhookDeliveries(token.trim(), { subscription_id: subscriptionId || undefined, limit: 100 });
    setDeliveries(deliveryResponse.items);
  });
  const copyToken = () => void run(async () => {
    await navigator.clipboard.writeText(token);
    setMessage("token 已复制到剪贴板；请立即粘贴到本机自动化脚本中。");
  });

  return <section className="panel automation-panel" aria-labelledby="automation-title">
    <div className="panel-heading"><div><p className="eyebrow">本机自动化</p><h3 id="automation-title">自动化接口与回调</h3></div><span className="status-pill neutral">仅回环</span></div>
    <p className="muted">UI 和自动化脚本使用同一条 command。token、scope、HMAC 签名、指数退避和死信都在本机持久化；没有公网出站，也不能绕过人工审核。</p>
    <div className="field-grid"><label>接口名称<input value={title} onChange={(event) => setTitle(event.target.value)} /><small>机器代码由名称自动生成。</small></label></div>
    <button className="secondary" type="button" onClick={createClient} disabled={busy || !title.trim()}>创建本机接口凭据</button>
    {token && <div className="one-time-secret" role="status"><span>当前 token（仅显示一次）</span><code>{token}</code><button type="button" className="secondary" onClick={copyToken} disabled={busy}>复制 token</button><small>离开页面后无法再次查看；服务端只保存不可逆摘要。</small></div>}
    <div className="field-grid"><label>回环端点<input value={endpoint} onChange={(event) => setEndpoint(event.target.value)} /></label><label>订阅（可选）<select value={subscriptionId} onChange={(event) => setSubscriptionId(event.target.value)}><option value="">全部订阅 / 创建新订阅</option>{subscriptions.map((item) => <option key={item.id} value={item.id}>{item.endpoint_url}</option>)}</select></label></div>
    <div className="button-row"><button className="secondary" type="button" onClick={createSubscription} disabled={busy || !token.trim() || !endpoint.trim()}>创建签名订阅</button><button className="secondary" type="button" onClick={refresh} disabled={busy || !token.trim()}>刷新状态</button><button className="primary-action" type="button" onClick={deliver} disabled={busy || !token.trim()}>投递待处理事件</button></div>
    {subscriptions.length > 0 && <div className="review-meta"><span>订阅：{subscriptions.length}</span><span>当前回调：{subscriptions.find((item) => item.id === subscriptionId)?.endpoint_url ?? "未选择"}</span></div>}
    {deliveries.length > 0 && <div className="table-wrap"><table><caption className="sr-only">Webhook delivery 状态</caption><thead><tr><th>event</th><th>状态</th><th>尝试</th><th>操作</th></tr></thead><tbody>{deliveries.slice(0, 12).map((delivery) => <tr key={delivery.id}><td>{delivery.event_id}</td><td>{delivery.status}{delivery.last_error ? ` · ${delivery.last_error}` : ""}</td><td>{delivery.attempt_count}</td><td>{delivery.status === "DEAD_LETTER" && <button className="secondary" type="button" onClick={() => retry(delivery.id)} disabled={busy}>显式重试</button>}</td></tr>)}</tbody></table></div>}
    {message && <p className="review-success" role="status">{message}</p>}{error && <p className="inline-error" role="alert">自动化操作失败：{error}</p>}
  </section>;
}
