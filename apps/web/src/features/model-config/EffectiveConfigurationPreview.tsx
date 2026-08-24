import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { resolveEffectiveConfiguration, type EffectiveConfiguration } from "../../generated/api";

function useDebounced<T>(value: T, delay = 300) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [delay, value]);
  return debounced;
}

function displayValue(value: unknown) {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function ConfigurationBody({ configuration }: { configuration: EffectiveConfiguration }) {
  const settings = Object.entries(configuration.effective_settings);
  return (
    <>
      <div className="effective-config-summary">
        <span className={`status-pill ${configuration.blocking_errors.length ? "state-blocked" : "state-active"}`}>
          {configuration.blocking_errors.length ? "不可提交" : "预检通过"}
        </span>
        <span className="muted">指纹 {configuration.fingerprint.slice(0, 12)}…</span>
        <span className="muted">只读 · 本机</span>
      </div>
      <dl className="effective-config-facts">
        <div><dt>能力版本</dt><dd>{configuration.profile ? String(configuration.profile.title ?? configuration.profile.code ?? "已选择") : "自动推荐"}</dd></div>
        <div><dt>运行时</dt><dd>{configuration.runtime_status}</dd></div>
        <div><dt>组件</dt><dd>{configuration.components.length ? `${configuration.components.filter((item) => item.available !== false).length}/${configuration.components.length} 可用` : "未声明组件"}</dd></div>
      </dl>
      {settings.length > 0 && (
        <div className="effective-config-settings">
          <strong>最终参数</strong>
          {settings.map(([key, value]) => <span key={key}><b>{key}</b> {displayValue(value)} <small>{configuration.setting_sources[key] ?? "PROFILE_DEFAULT"}</small></span>)}
        </div>
      )}
      {configuration.blocking_errors.map((error) => <p className="inline-error" role="alert" key={`${error.code}:${error.message}`}>{error.message}</p>)}
      {configuration.warnings.map((warning) => <p className="inline-warning" role="status" key={warning}>{warning}</p>)}
    </>
  );
}

export function EffectiveConfigurationPreview({
  projectId,
  capability,
  episodeId,
  shotId,
  profileVersionId,
  settings,
  enabled = true,
}: {
  projectId: string;
  capability: string;
  episodeId?: string;
  shotId?: string;
  profileVersionId?: string | null;
  settings: Record<string, unknown>;
  enabled?: boolean;
}) {
  const debouncedSettings = useDebounced(settings);
  const settingsKey = useMemo(() => JSON.stringify(debouncedSettings), [debouncedSettings]);
  const preview = useQuery({
    queryKey: ["effective-configuration", projectId, capability, episodeId, shotId, profileVersionId, settingsKey],
    queryFn: async () => (await resolveEffectiveConfiguration({
      project_id: projectId,
      episode_id: episodeId || null,
      shot_id: shotId || null,
      capability_code: capability,
      requested_profile_version_id: profileVersionId || null,
      run_overrides: debouncedSettings,
    })).configuration,
    enabled: enabled && Boolean(projectId && capability),
    staleTime: 10_000,
  });

  return (
    <section className="effective-config-preview" aria-labelledby="effective-config-preview-title">
      <div className="effective-config-heading">
        <div><p className="eyebrow">提交前检查</p><h4 id="effective-config-preview-title">最终生效配置</h4></div>
        <span className="muted">修改后约 300ms 自动更新</span>
      </div>
      {preview.isPending && <p className="empty-state" aria-live="polite">正在计算最终配置…</p>}
      {preview.isError && <div className="workspace-error" role="alert"><p>预检失败：{preview.error instanceof Error ? preview.error.message : "无法计算最终配置"}</p><button className="secondary" type="button" onClick={() => void preview.refetch()}>重试</button></div>}
      {preview.data && <ConfigurationBody configuration={preview.data} />}
    </section>
  );
}
