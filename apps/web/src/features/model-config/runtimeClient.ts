import { requestJson } from "../../generated/api";

export type RuntimeEnvironmentVersion = { id: string; runtime_environment_id: string; version_no: number; status: string; manifest: Record<string, unknown>; environment_fingerprint: string; validation: { status?: string; blockers?: Array<{ code: string; message: string }> } };
export type RuntimeEnvironment = { id: string; code: string; title: string; status: string; version_count?: number };
const json = (value: unknown) => ({ headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
export const listRuntimeEnvironments = () => requestJson<{ items: RuntimeEnvironment[] }>("/api/v1/runtime-environments");
export const getRuntimeEnvironment = (id: string) => requestJson<{ environment: RuntimeEnvironment; versions: RuntimeEnvironmentVersion[] }>(`/api/v1/runtime-environments/${encodeURIComponent(id)}`);
export const createRuntimeEnvironment = (payload: Record<string, unknown>) => requestJson<{ runtime_environment: { environment: RuntimeEnvironment; versions: RuntimeEnvironmentVersion[] } }>("/api/v1/runtime-environments", { method: "POST", ...json(payload) });
export const validateRuntimeVersion = (id: string) => requestJson<{ validation: { status: string; blockers: Array<{ code: string; message: string }> } }>(`/api/v1/runtime-environment-versions/${encodeURIComponent(id)}:validate`, { method: "POST" });
export const publishRuntimeVersion = (id: string) => requestJson<{ runtime_environment_version: RuntimeEnvironmentVersion }>(`/api/v1/runtime-environment-versions/${encodeURIComponent(id)}:publish`, { method: "POST" });
export const runtimeStatus = (id: string) => requestJson<{ observed_state: string; reachable: boolean; instance: { id: string } | null; health: Record<string, unknown> }>(`/api/v1/runtime-environment-versions/${encodeURIComponent(id)}/instance`);
export const startRuntime = (id: string, instanceKind: "MANAGED" | "EXTERNAL") => requestJson<Record<string, unknown>>(`/api/v1/runtime-environment-versions/${encodeURIComponent(id)}/instance:start`, { method: "POST", ...json({ instance_kind: instanceKind }) });
export const stopRuntime = (id: string, instanceId: string) => requestJson<Record<string, unknown>>(`/api/v1/runtime-environment-versions/${encodeURIComponent(id)}/instance:stop`, { method: "POST", ...json({ expected_instance_id: instanceId }) });
export const createAppContract = (workflowVersionId: string, payload: Record<string, unknown>) => requestJson<{ app_contract: { id: string; status: string; validation: { status: string; blockers: Array<{ code: string; message: string }> } } }>(`/api/v1/workflow-versions/${encodeURIComponent(workflowVersionId)}/app-contracts`, { method: "POST", ...json(payload) });
export const publishAppContract = (id: string) => requestJson<{ app_contract: { id: string; status: string } }>(`/api/v1/workflow-app-contracts/${encodeURIComponent(id)}:publish`, { method: "POST" });
export const bindWorkflowRuntime = (workflowVersionId: string, contractVersionId: string, runtimeVersionId: string) => requestJson<{ binding: Record<string, unknown> }>(`/api/v1/workflow-versions/${encodeURIComponent(workflowVersionId)}/runtime-binding`, { method: "PUT", ...json({ contract_version_id: contractVersionId, runtime_environment_version_id: runtimeVersionId }) });

