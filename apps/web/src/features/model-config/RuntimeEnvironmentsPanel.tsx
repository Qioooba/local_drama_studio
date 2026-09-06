import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { bindWorkflowRuntime, createAppContract, createRuntimeEnvironment, getRuntimeEnvironment, listRuntimeEnvironments, publishAppContract, publishRuntimeVersion, runtimeStatus, startRuntime, stopRuntime, validateRuntimeVersion } from "./runtimeClient";
import "./runtime-environments.css";
import { listWorkflowAppContracts } from "./runtimeClient";

type WorkflowSummary = { id: string; code?: string; title?: string; status?: string };
const defaultManifest = { mode: "EXTERNAL", endpoint: "http://127.0.0.1:8188", python_path: null, root_path: null, launch_args: [], comfy_commit: null, custom_nodes: [], models: [], python_packages: {}, gpu: {} };
const defaultContract = { inputs: { PROMPT: { type: "TEXT", required: true } }, outputs: { VIDEO: { type: "VIDEO", required: true } } };
const shotKeyframeContract = {
  purpose: "SHOT_KEYFRAME_SINGLE_FRAME",
  output_layout: "SINGLE_FRAME",
  inputs: {
    PROMPT: { type: "TEXT", required: true },
    NEGATIVE_PROMPT: { type: "TEXT", required: true },
    SEED: { type: "INTEGER", required: true },
  },
  outputs: { IMAGE: { type: "IMAGE", layout: "SINGLE_FRAME", required: true } },
};
const shotKeyframeBindings = {
  PROMPT: { node_id: "5", input: "text" },
  NEGATIVE_PROMPT: { node_id: "6", input: "text" },
  SEED: { node_id: "8", input: "seed" },
};
const shotKeyframePhases = [
  { name: "正向提示词", node_ids: ["5"] },
  { name: "反向提示词", node_ids: ["6"] },
  { name: "单画幅采样", node_ids: ["8", "9"] },
  { name: "保存单帧", node_ids: ["10"] },
];

export function RuntimeEnvironmentsPanel({ workflows }: { workflows: WorkflowSummary[] }) {
  const client = useQueryClient();
  const environments = useQuery({ queryKey: ["runtime-environments"], queryFn: listRuntimeEnvironments, retry: false });
  const [selectedEnvironmentId, setSelectedEnvironmentId] = useState("");
  const [creating, setCreating] = useState(false);
  const [code, setCode] = useState("comfy-production");
  const [title, setTitle] = useState("本机 ComfyUI 生产环境");
  const [manifest, setManifest] = useState(JSON.stringify(defaultManifest, null, 2));
  const detail = useQuery({ queryKey: ["runtime-environment", selectedEnvironmentId], queryFn: () => getRuntimeEnvironment(selectedEnvironmentId), enabled: Boolean(selectedEnvironmentId), retry: false });
  useEffect(() => { if (!selectedEnvironmentId && environments.data?.items[0]) setSelectedEnvironmentId(environments.data.items[0].id); }, [environments.data, selectedEnvironmentId]);
  const version = detail.data?.versions[0];
  const status = useQuery({ queryKey: ["runtime-status", version?.id], queryFn: () => runtimeStatus(version!.id), enabled: version?.status === "PUBLISHED", retry: false, refetchInterval: 4_000 });
  const create = useMutation({ mutationFn: () => createRuntimeEnvironment({ code, title, manifest: JSON.parse(manifest) }), onSuccess: ({ runtime_environment }) => { setSelectedEnvironmentId(runtime_environment.environment.id); setCreating(false); void client.invalidateQueries({ queryKey: ["runtime-environments"] }); } });
  const validate = useMutation({ mutationFn: () => validateRuntimeVersion(version!.id), onSuccess: () => void detail.refetch() });
  const publish = useMutation({ mutationFn: () => publishRuntimeVersion(version!.id), onSuccess: () => void detail.refetch() });
  const start = useMutation({ mutationFn: () => startRuntime(version!.id, String(version!.manifest.mode) === "EXTERNAL" ? "EXTERNAL" : "MANAGED"), onSuccess: () => void status.refetch() });
  const stop = useMutation({ mutationFn: () => stopRuntime(version!.id, status.data!.instance!.id), onSuccess: () => void status.refetch() });
  const [workflowId, setWorkflowId] = useState("");
  const [capability, setCapability] = useState("I2V");
  const [contract, setContract] = useState(JSON.stringify(defaultContract, null, 2));
  const [bindings, setBindings] = useState(JSON.stringify({ PROMPT: { node_id: "1", input: "text" } }, null, 2));
  const [phases, setPhases] = useState(JSON.stringify([{ name: "编码提示词", node_ids: ["1"] }, { name: "视频采样", node_ids: ["2"] }, { name: "保存输出", node_ids: ["3"] }], null, 2));
  const [contractId, setContractId] = useState("");
  const [contractStatus, setContractStatus] = useState("");
  const savedContracts = useQuery({ queryKey: ["workflow-app-contracts", workflowId], queryFn: () => listWorkflowAppContracts(workflowId), enabled: !!workflowId });
  const boundContractId = savedContracts.data?.binding?.contract_version_id;
  useEffect(() => {
    const saved = savedContracts.data?.items.find((item) => item.id === boundContractId) ?? savedContracts.data?.items[0];
    setContractId(saved?.id ?? "");
    setContractStatus(saved?.status ?? "");
    setContractValidation(null);
    setCapability(saved?.capability ?? "I2V");
    setContract(JSON.stringify(saved?.contract ?? defaultContract, null, 2));
    setBindings(JSON.stringify(saved?.bindings ?? { PROMPT: { node_id: "1", input: "text" } }, null, 2));
    setPhases(JSON.stringify(saved?.semantic_phases ?? [], null, 2));
  }, [workflowId, savedContracts.data]);
  const editedContract = () => { setContractId(""); setContractStatus(""); setContractValidation(null); };
  const [contractValidation, setContractValidation] = useState<{ status?: string; blockers?: Array<{ code: string; message: string }> } | null>(null);
  const fillShotKeyframeTemplate = () => {
    setCapability("SHOT_KEYFRAME_SINGLE_FRAME");
    setContract(JSON.stringify(shotKeyframeContract, null, 2));
    setBindings(JSON.stringify(shotKeyframeBindings, null, 2));
    setPhases(JSON.stringify(shotKeyframePhases, null, 2));
    setContractId("");
    setContractValidation(null);
  };
  const contractMutation = useMutation({ mutationFn: () => createAppContract(workflowId, { capability, contract: JSON.parse(contract), bindings: JSON.parse(bindings), semantic_phases: JSON.parse(phases) }), onSuccess: ({ app_contract }) => { setContractId(app_contract.id); setContractStatus(app_contract.status); setContractValidation(app_contract.validation); } });
  const publishContract = useMutation({ mutationFn: () => publishAppContract(contractId), onSuccess: ({ app_contract }) => setContractStatus(app_contract.status) });
  const bind = useMutation({ mutationFn: () => bindWorkflowRuntime(workflowId, contractId, version!.id), onSuccess: () => void savedContracts.refetch() });
  const error = create.error || validate.error || publish.error || start.error || stop.error || contractMutation.error || publishContract.error || bind.error;
  const manifestError = useMemo(() => { try { JSON.parse(manifest); return ""; } catch (value) { return value instanceof Error ? value.message : "JSON 无效"; } }, [manifest]);
  return <section className="runtime-environments panel"><header className="runtime-environments__heading"><div><p className="eyebrow">Comfy Runtime</p><h3>运行环境与应用契约</h3><p className="muted">工作流、应用语义与本机运行环境分别版本化，再通过明确绑定进入正式生成。</p></div><button className="primary-action" type="button" onClick={() => setCreating(true)}>登记运行环境</button></header>
    {creating && <form className="runtime-create" onSubmit={(event) => { event.preventDefault(); create.mutate(); }}><label>代码<input value={code} onChange={(event) => setCode(event.target.value)} /></label><label>名称<input value={title} onChange={(event) => setTitle(event.target.value)} /></label><label>环境清单<textarea rows={12} spellCheck={false} value={manifest} onChange={(event) => setManifest(event.target.value)} /></label>{manifestError && <p className="field-error">{manifestError}</p>}<div><button className="secondary" type="button" onClick={() => setCreating(false)}>取消</button><button className="primary-action" type="submit" disabled={Boolean(manifestError) || create.isPending}>保存草稿版本</button></div></form>}
    <div className="runtime-environments__grid"><nav aria-label="运行环境">{(environments.data?.items ?? []).map((item) => <button key={item.id} type="button" className={item.id === selectedEnvironmentId ? "selected" : ""} onClick={() => setSelectedEnvironmentId(item.id)}><strong>{item.title}</strong><small>{item.code} · {item.version_count ?? 0} 个版本</small></button>)}{!environments.isPending && environments.data?.items.length === 0 && <p className="muted">尚未登记环境。</p>}</nav><article>{version ? <><div className="runtime-version"><div><strong>v{version.version_no}</strong><span className={`status-pill ${version.status === "PUBLISHED" ? "success" : "neutral"}`}>{version.status}</span></div><code>{version.environment_fingerprint.slice(0, 16)}</code></div><pre>{JSON.stringify(version.manifest, null, 2)}</pre><div className="runtime-actions"><button className="secondary" type="button" onClick={() => validate.mutate()}>验证本机文件</button>{version.status !== "PUBLISHED" && <button className="primary-action" type="button" onClick={() => publish.mutate()}>发布此版本</button>}{version.status === "PUBLISHED" && <button className="primary-action" type="button" onClick={() => start.mutate()} disabled={status.data?.observed_state === "RUNNING"}>启动 / 接管</button>}{status.data?.instance && status.data.observed_state !== "STOPPED" && String(version.manifest.mode) === "MANAGED" && <button className="secondary danger" type="button" onClick={() => stop.mutate()}>停止托管进程</button>}</div>{version.validation?.blockers?.map((item) => <p className="field-error" key={item.code}>{item.message} <code>{item.code}</code></p>)}{version.status === "PUBLISHED" && <p className="runtime-observed">观测状态：<strong>{status.data?.observed_state ?? "读取中"}</strong> · {status.data?.reachable ? "endpoint 可达" : "endpoint 不可达"}</p>}</> : <p className="muted">选择一个运行环境查看不可变版本。</p>}</article></div>
    <section className="workflow-contract-editor"><h4>Workflow App Contract</h4>{savedContracts.isFetching && workflowId && <p role="status">正在读取已保存契约…</p>}{savedContracts.error && <p role="alert">契约读取失败：{String(savedContracts.error)}</p>}{savedContracts.data?.binding && <p>当前运行绑定：契约 <code>{savedContracts.data.binding.contract_version_id}</code> · 运行环境 <code>{savedContracts.data.binding.runtime_environment_version_id}</code></p>}<p className="muted">把 Comfy 节点号封装成稳定语义输入、输出和进度阶段。底层 Workflow 必须先发布。</p><div className="workflow-contract-editor__fields"><label>Workflow 版本<select value={workflowId} onChange={(event) => setWorkflowId(event.target.value)}><option value="">请选择</option>{workflows.map((item) => <option key={item.id} value={item.id}>{item.code || item.title || item.id} · {item.status}</option>)}</select></label><label>能力<input value={capability} onChange={(event) => { setCapability(event.target.value); editedContract(); }} /></label></div><div className="workflow-contract-editor__template"><button className="secondary" type="button" onClick={fillShotKeyframeTemplate}>填充首尾帧单画幅契约模板</button><p className="muted">模板将正向 node 5、反向 node 6、seed node 8 作为页面可审计绑定；创建前请核对当前 Workflow 节点号。不会修改 Workflow graph。</p></div><div className="workflow-contract-editor__json"><label>输入 / 输出契约<textarea rows={10} value={contract} onChange={(event) => { setContract(event.target.value); editedContract(); }} /></label><label>语义输入绑定<textarea rows={10} value={bindings} onChange={(event) => { setBindings(event.target.value); editedContract(); }} /></label><label>语义进度阶段<textarea rows={10} value={phases} onChange={(event) => { setPhases(event.target.value); editedContract(); }} /></label></div><div className="runtime-actions"><button className="secondary" type="button" disabled={!workflowId || contractMutation.isPending} onClick={() => contractMutation.mutate()}>创建契约草稿</button><button className="secondary" type="button" disabled={!contractId || contractStatus === "PUBLISHED" || publishContract.isPending} onClick={() => publishContract.mutate()}>发布契约</button><button className="primary-action" type="button" disabled={!contractId || contractStatus !== "PUBLISHED" || !version || version.status !== "PUBLISHED" || bind.isPending} onClick={() => bind.mutate()}>绑定 Workflow + Contract + Runtime</button></div>{contractId && <p>当前契约版本：<code>{contractId}</code> · {contractStatus === "PUBLISHED" ? "已发布" : "待发布"} {boundContractId === contractId ? "· 已绑定" : ""}</p>}{contractValidation && <div className={`workflow-contract-validation ${contractValidation.status === "PASS" ? "success" : "blocked"}`} role="status"><strong>契约预检：{contractValidation.status === "PASS" ? "通过" : "阻塞"}</strong>{contractValidation.blockers?.map((item) => <p className="field-error" key={item.code}>{item.message} <code>{item.code}</code></p>)}</div>}</section>
    {error && <p className="field-error" role="alert">{error.message}</p>}
  </section>;
}
