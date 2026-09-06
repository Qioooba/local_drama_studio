import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ErrorState, Skeleton, StatusBadge } from "../../components/ui";
import {
  createProviderConnection,
  deleteProviderConnection,
  deleteProviderSecret,
  listProviderConnections,
  probeProviderConnection,
  replaceProviderSecret,
  revealProviderSecret,
  updateProviderConnection,
  type ProviderConnection,
} from "../../generated/api";
import "./provider-connections.css";
import { generateMachineCode } from "../shared/autoCode";

type Feedback = { kind: "success" | "error"; message: string } | null;
type ConnectionDraft = {
  code: string;
  title: string;
  provider_kind: string;
  base_url: string;
  model: string;
  credential_source: "NONE" | "OS_SECRET_STORE" | "WINDOWS_CREDENTIAL_MANAGER" | "ENVIRONMENT";
  environment_variable_name: string;
};

const emptyDraft: ConnectionDraft = {
  code: "",
  title: "",
  provider_kind: "DEEPSEEK",
  base_url: "https://api.deepseek.com/v1",
  model: "",
  credential_source: "OS_SECRET_STORE" as const,
  environment_variable_name: "",
};

function connectionTone(connection: ProviderConnection) {
  if (connection.status !== "ACTIVE") return "neutral" as const;
  if (connection.last_probe.status === "OK") return "success" as const;
  if (connection.last_probe.status === "FAILED") return "danger" as const;
  return "attention" as const;
}

function providerLabel(provider: ProviderConnection) {
  if (provider.provider_kind === "DEEPSEEK") return "DeepSeek";
  if (provider.provider_kind === "OLLAMA") return "Ollama";
  return provider.provider_kind;
}

export function ProviderConnectionsPanel() {
  const queryClient = useQueryClient();
  const connections = useQuery({ queryKey: ["provider-connections"], queryFn: () => listProviderConnections() });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [draft, setDraft] = useState<ConnectionDraft>(emptyDraft);
  const [replacement, setReplacement] = useState("");
  const [connectionDraft, setConnectionDraft] = useState({ title: "", baseUrl: "", model: "" });
  const [revealed, setRevealed] = useState<{ connectionId: string; secret: string; expiresAt: number } | null>(null);
  const [feedback, setFeedback] = useState<Feedback>(null);

  const items = connections.data?.items ?? [];
  const selected = useMemo(() => items.find((item) => item.id === selectedId) ?? items[0] ?? null, [items, selectedId]);

  const hideSecret = useCallback(() => setRevealed(null), []);
  const copySecret = useCallback(async (secret: string) => {
    if (!navigator.clipboard) {
      setFeedback({ kind: "error", message: "当前浏览器不允许访问剪贴板，请手动选择并复制。" });
      return;
    }
    try {
      await navigator.clipboard.writeText(secret);
      setFeedback({ kind: "success", message: "密钥已复制到剪贴板；请注意剪贴板安全。" });
    } catch {
      setFeedback({ kind: "error", message: "复制失败，请手动选择并复制密钥。" });
    }
  }, []);
  useEffect(() => {
    if (!selectedId || !items.some((item) => item.id === selectedId)) setSelectedId(items[0]?.id ?? null);
  }, [items, selectedId]);
  useEffect(() => {
    if (!selected) return;
    setConnectionDraft({ title: selected.title, baseUrl: selected.base_url, model: selected.model ?? "" });
  }, [selected]);
  useEffect(() => {
    if (!revealed) return;
    const timeout = window.setTimeout(hideSecret, Math.max(0, revealed.expiresAt - Date.now()));
    const handleVisibility = () => { if (document.visibilityState === "hidden") hideSecret(); };
    window.addEventListener("blur", hideSecret);
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      window.clearTimeout(timeout);
      window.removeEventListener("blur", hideSecret);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [hideSecret, revealed]);
  useEffect(() => () => setRevealed(null), []);

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ["provider-connections"] });
  const create = useMutation({
    mutationFn: () => createProviderConnection({
      ...draft,
      code: generateMachineCode("provider", draft.title).toLowerCase(),
      model: draft.model.trim() || null,
      environment_variable_name: draft.credential_source === "ENVIRONMENT" ? draft.environment_variable_name.trim() || null : null,
    }),
    onSuccess: (data) => {
      setShowCreate(false);
      setDraft(emptyDraft);
      setSelectedId(data.connection.id);
      setFeedback({ kind: "success", message: "远端服务连接已创建；请在右侧保存或测试密钥。" });
      invalidate();
    },
    onError: (error) => setFeedback({ kind: "error", message: `创建连接失败：${String(error)}` }),
  });
  const reveal = useMutation({
    mutationFn: () => {
      if (!selected) throw new Error("请先选择连接。");
      return revealProviderSecret(selected.id);
    },
    onSuccess: (data) => setRevealed({ connectionId: selected!.id, secret: data.secret, expiresAt: Date.now() + data.expires_in_seconds * 1000 }),
    onError: (error) => setFeedback({ kind: "error", message: `查看密钥失败：${String(error)}` }),
  });
  const replace = useMutation({
    mutationFn: () => {
      if (!selected || !replacement.trim()) throw new Error("请输入要保存的密钥。");
      return replaceProviderSecret(selected.id, replacement.trim());
    },
    onSuccess: () => {
      setReplacement("");
      hideSecret();
      setFeedback({ kind: "success", message: "密钥已替换并安全保存；明文输入框已清空。" });
      invalidate();
    },
    onError: (error) => setFeedback({ kind: "error", message: `保存密钥失败：${String(error)}` }),
  });
  const remove = useMutation({
    mutationFn: () => {
      if (!selected) throw new Error("请先选择连接。");
      return deleteProviderSecret(selected.id);
    },
    onSuccess: () => {
      hideSecret();
      setFeedback({ kind: "success", message: "密钥已从 Windows 凭据管理器删除。" });
      invalidate();
    },
    onError: (error) => setFeedback({ kind: "error", message: `删除密钥失败：${String(error)}` }),
  });
  const removeConnection = useMutation({
    mutationFn: () => {
      if (!selected) throw new Error("请先选择连接。");
      return deleteProviderConnection(selected.id);
    },
    onSuccess: () => {
      hideSecret();
      setSelectedId(null);
      setFeedback({ kind: "success", message: "远端服务连接已删除。" });
      invalidate();
    },
    onError: (error) => setFeedback({ kind: "error", message: `删除连接失败：${String(error)}` }),
  });
  const probe = useMutation({
    mutationFn: () => {
      if (!selected) throw new Error("请先选择连接。");
      return probeProviderConnection(selected.id, { model: selected.model, load_test: false });
    },
    onSuccess: (data) => {
      setFeedback({ kind: data.probe.status === "PASS" ? "success" : "error", message: data.probe.status === "PASS" ? "连接测试通过。" : `连接测试未通过：${String(data.probe.error_code ?? "请检查配置")}` });
      invalidate();
    },
    onError: (error) => setFeedback({ kind: "error", message: `连接测试失败：${String(error)}` }),
  });
  const update = useMutation({
    mutationFn: () => {
      if (!selected) throw new Error("请先选择连接。");
      return updateProviderConnection(selected.id, {
        expected_revision: selected.revision,
        title: connectionDraft.title.trim(),
        base_url: connectionDraft.baseUrl.trim(),
        model: connectionDraft.model.trim() || null,
      });
    },
    onSuccess: () => {
      setFeedback({ kind: "success", message: "连接配置已更新；现在可以执行真实连接测试。" });
      invalidate();
    },
    onError: (error) => setFeedback({ kind: "error", message: `更新连接失败：${String(error)}` }),
  });

  if (connections.isPending) return <Skeleton label="正在读取远端服务与密钥" lines={5} />;
  if (connections.error) return <ErrorState description={`远端服务读取失败：${String(connections.error)}`} onRetry={() => void connections.refetch()} />;

  return (
    <section className="panel provider-connections-panel" aria-labelledby="provider-connections-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">远端服务与密钥</p>
          <h3 id="provider-connections-title">连接管理</h3>
        </div>
        <button type="button" className="primary-action" onClick={() => { setShowCreate((value) => !value); setFeedback(null); }}>＋添加连接</button>
      </div>
      <p className="muted">密钥默认掩码，只有主动点击查看才会在本地短时显示。明文不会写入项目、任务、浏览器缓存或审计内容。</p>

      {showCreate ? (
        <form className="provider-connection-create" onSubmit={(event) => { event.preventDefault(); create.mutate(); }}>
          <label>连接名称<input required value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} placeholder="DeepSeek 主连接" /></label>
          <div className="field-fact"><span>连接技术标识</span><strong>{generateMachineCode("provider", draft.title).toLowerCase() || "填写名称后自动生成"}</strong><small>系统自动生成并用于内部引用。</small></div>
          <label>模型服务类型<select value={draft.provider_kind} onChange={(event) => { const provider_kind = event.target.value; setDraft({ ...draft, provider_kind, base_url: provider_kind === "DEEPSEEK" ? "https://api.deepseek.com/v1" : "", credential_source: "OS_SECRET_STORE" }); }}><option value="DEEPSEEK">DeepSeek 官方服务</option><option value="OPENAI_COMPAT">兼容 OpenAI 接口的服务</option></select><small>127.0.0.1 / localhost 指部署应用的机器，不是当前浏览器电脑。</small></label>
          <label>服务地址（Base URL）<input required type="url" value={draft.base_url} onChange={(event) => setDraft({ ...draft, base_url: event.target.value })} /><small>通常使用服务商文档给出的 API 根地址。</small></label>
          <label>默认模型名称<input value={draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })} placeholder="例如：deepseek-chat" /></label>
          <label>密钥来源<select value={draft.credential_source} onChange={(event) => setDraft({ ...draft, credential_source: event.target.value as typeof draft.credential_source })}><option value="OS_SECRET_STORE">操作系统安全凭据库（推荐）</option><option value="ENVIRONMENT">进程环境变量</option><option value="NONE">无需密钥</option></select></label>
          {draft.credential_source === "ENVIRONMENT" ? <label>环境变量名<input required value={draft.environment_variable_name} onChange={(event) => setDraft({ ...draft, environment_variable_name: event.target.value })} placeholder="DEEPSEEK_API_KEY" /></label> : null}
          <div className="provider-form-actions"><button type="button" className="secondary" onClick={() => setShowCreate(false)}>取消</button><button type="submit" className="primary-action" disabled={create.isPending}>{create.isPending ? "创建中…" : "创建连接"}</button></div>
        </form>
      ) : null}

      <div className="provider-connections-layout">
        <aside className="provider-connection-list" aria-label="远端服务连接列表">
          {items.map((item) => <button key={item.id} type="button" className={selected?.id === item.id ? "selected" : ""} onClick={() => { hideSecret(); setSelectedId(item.id); setFeedback(null); }}><span><strong>{item.title}</strong><small>{providerLabel(item)} · {item.base_url}</small></span><StatusBadge tone={connectionTone(item)}>{item.last_probe.status === "OK" ? "可用" : item.has_secret ? "待测试" : "无密钥"}</StatusBadge></button>)}
          {items.length === 0 ? <p className="empty-state">尚未配置远端连接；可以先添加一个模型服务。</p> : null}
        </aside>

        {selected ? <div className="provider-connection-detail">
          <div className="provider-detail-heading"><div><p className="eyebrow">连接详情</p><h4>{selected.title}</h4></div><StatusBadge tone={connectionTone(selected)}>{selected.status}</StatusBadge></div>
          <dl className="provider-detail-facts"><div><dt>模型服务</dt><dd>{providerLabel(selected)} · {selected.protocol}</dd></div><div><dt>服务地址</dt><dd><code>{selected.base_url}</code></dd></div><div><dt>默认模型</dt><dd>{selected.model || "未设置"}</dd></div><div><dt>密钥保存位置</dt><dd>{selected.credential_source === "ENVIRONMENT" ? `环境变量 ${selected.environment_variable_name ?? "未命名"}` : ["OS_SECRET_STORE", "WINDOWS_CREDENTIAL_MANAGER"].includes(selected.credential_source) ? "操作系统安全凭据库" : "无密钥"}</dd></div></dl>

          <form className="provider-connection-edit" onSubmit={(event) => { event.preventDefault(); update.mutate(); }}>
            <label>连接名称<input required value={connectionDraft.title} onChange={(event) => setConnectionDraft({ ...connectionDraft, title: event.target.value })} /></label>
            <label>服务地址（Base URL）<input required type="url" value={connectionDraft.baseUrl} onChange={(event) => setConnectionDraft({ ...connectionDraft, baseUrl: event.target.value })} /></label>
            <label>默认模型名称<input required value={connectionDraft.model} onChange={(event) => setConnectionDraft({ ...connectionDraft, model: event.target.value })} placeholder="例如：deepseek-chat" /></label>
            <button type="submit" className="secondary" disabled={update.isPending || !connectionDraft.title.trim() || !connectionDraft.baseUrl.trim() || !connectionDraft.model.trim()}>{update.isPending ? "保存中…" : "保存连接配置"}</button>
          </form>

          <div className="provider-secret-row"><span>服务密钥（API Key）</span><div className="provider-secret-control"><code aria-label="当前密钥">{revealed?.connectionId === selected.id ? revealed.secret : selected.masked_secret ?? "未配置"}</code><button type="button" className="secondary" onClick={() => revealed?.connectionId === selected.id ? hideSecret() : reveal.mutate()} disabled={reveal.isPending}>{revealed?.connectionId === selected.id ? "立即隐藏" : reveal.isPending ? "读取中…" : "查看"}</button>{revealed?.connectionId === selected.id ? <button type="button" className="secondary" onClick={() => void copySecret(revealed.secret)}>复制</button> : null}</div>{revealed?.connectionId === selected.id ? <small className="provider-secret-warning">明文将在约 60 秒后、页面失焦或切换连接时自动隐藏。</small> : null}</div>

          {["OS_SECRET_STORE", "WINDOWS_CREDENTIAL_MANAGER"].includes(selected.credential_source) ? <form className="provider-secret-replace" onSubmit={(event) => { event.preventDefault(); replace.mutate(); }}><label htmlFor="provider-secret-replacement">替换密钥<input id="provider-secret-replacement" type="password" autoComplete="off" value={replacement} onChange={(event) => setReplacement(event.target.value)} placeholder="输入新密钥后保存" /></label><div><button type="submit" className="secondary" disabled={replace.isPending || !replacement.trim()}>{replace.isPending ? "保存中…" : "替换并保存"}</button><button type="button" className="secondary danger-outline" onClick={() => { if (window.confirm("确定删除当前连接的密钥吗？")) remove.mutate(); }} disabled={remove.isPending || !selected.has_secret}>{remove.isPending ? "删除中…" : "删除密钥"}</button></div></form> : null}

          <div className="provider-probe-actions"><button type="button" className="primary-action" onClick={() => probe.mutate()} disabled={probe.isPending || !selected.model}>{probe.isPending ? "测试中…" : "测试连接"}</button><button type="button" className="secondary danger-outline" onClick={() => { if (window.confirm("确定删除该远端服务连接及其本地密钥吗？如果已有执行配置引用它，系统会阻止删除。")) removeConnection.mutate(); }} disabled={removeConnection.isPending}>{removeConnection.isPending ? "删除中…" : "删除连接"}</button><small>{selected.model ? `使用 ${selected.model} 做一次轻量连接测试。` : "请先设置默认模型名称。"}</small></div>
          {selected.last_probe.summary && Object.keys(selected.last_probe.summary).length ? <details className="provider-probe-summary"><summary>查看最近测试摘要</summary><pre>{JSON.stringify(selected.last_probe.summary, null, 2)}</pre></details> : null}
          {feedback ? <p className={feedback.kind === "error" ? "inline-error" : "review-success"} role={feedback.kind === "error" ? "alert" : "status"}>{feedback.message}</p> : null}
        </div> : null}
      </div>
    </section>
  );
}
