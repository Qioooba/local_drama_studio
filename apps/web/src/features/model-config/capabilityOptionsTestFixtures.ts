import type { CapabilityOption, CapabilityOptions } from "../../generated/api";

export function capabilityOptionFixture(
  capability: string,
  profileVersionId: string,
  model: string,
  title: string,
  versionNo = 1,
): CapabilityOption {
  return {
    profile_version_id: profileVersionId,
    profile: { id: `profile-${profileVersionId}`, code: profileVersionId, title, version_no: versionNo, status: "PUBLISHED" },
    model: { name: model, provider: "LOCAL" },
    runtime: { id: "runtime-test", title: "测试运行时", status: "AVAILABLE", transport: "LOCAL_PROCESS" },
    workflow: { id: `workflow-${capability}`, title: "测试工作流", status: "PUBLISHED" },
    selectable: true,
    availability: "READY",
    blockers: [],
    warnings: [],
    execution_fingerprint: `sha256:${profileVersionId}`,
  };
}

export function capabilityOptionsFixture(capability: string, options: CapabilityOption[]): CapabilityOptions {
  const selected = options.find((item) => item.selectable) ?? null;
  return {
    capability,
    scope: { project_id: "project-1", episode_id: null, shot_id: null },
    selection: {
      mode: "AUTO",
      source: "AUTO",
      profile_version_id: selected?.profile_version_id ?? null,
      ready: Boolean(selected),
      option: selected,
      blockers: selected ? [] : [{ code: "NO_COMPATIBLE_PROFILE", message: "当前继承链没有可执行的已发布配置" }],
    },
    options,
    configured_runtime: null,
    summary: { total_count: options.length, selectable_count: options.filter((item) => item.selectable).length, blocked_count: options.filter((item) => !item.selectable).length },
    repair_href: "/system/capabilities?view=resources",
    read_only: true,
    runtime_contacted: false,
    network_contacted: false,
    mutated: false,
  };
}
