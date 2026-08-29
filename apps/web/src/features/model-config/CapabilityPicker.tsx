import { useId, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  listCapabilityOptions,
  type CapabilityOption,
  type CapabilityOptions,
} from "../../generated/api";
import {
  getModelPlatformBusinessSelectionFacadeEvaluation,
  getModelPlatformBusinessSelectionShadow,
  type ModelPlatformBusinessSelectionFacadeEvaluation,
  type ModelPlatformBusinessSelectionShadow,
} from "../model-platform-v2/api";
import { ProfileExecutionDetailButton } from "./ProfileExecutionDetailButton";
import "./capability-picker.css";

export type CapabilityPickerQuery = {
  data: CapabilityOptions | undefined;
  isPending: boolean;
  error: Error | null;
  refetch: () => Promise<unknown>;
  migrationScope?: { projectId?: string | null; episodeId?: string | null; shotId?: string | null; characterId?: string | null };
};

type CapabilityPickerProps = {
  capability: string;
  value: string;
  onChange: (profileVersionId: string) => void;
  query: CapabilityPickerQuery;
  label?: string;
  description?: string;
  allowAuto?: boolean;
  disabled?: boolean;
  showDetails?: boolean;
  className?: string;
  migrationBusinessSurface?: string;
};

export function useCapabilityOptions(
  capability: string,
  scope: { projectId?: string | null; episodeId?: string | null; shotId?: string | null } = {},
) {
  const query = useQuery({
    queryKey: ["capability-options", capability, scope.projectId ?? "", scope.episodeId ?? "", scope.shotId ?? ""],
    queryFn: () => listCapabilityOptions({
      capability,
      project_id: scope.projectId,
      episode_id: scope.episodeId,
      shot_id: scope.shotId,
    }),
    enabled: Boolean(capability && (!scope.episodeId || scope.projectId) && (!scope.shotId || scope.projectId)),
    staleTime: 15_000,
  });
  return { ...query, migrationScope: scope };
}

function useBusinessSelectionShadow(
  capability: string,
  scope: CapabilityPickerQuery["migrationScope"],
  enabled: boolean,
) {
  return useQuery({
    queryKey: ["model-platform-v2", "business-selection-shadow", capability, scope?.projectId ?? "", scope?.episodeId ?? "", scope?.shotId ?? "", scope?.characterId ?? ""],
    queryFn: () => getModelPlatformBusinessSelectionShadow(capability, { projectId: scope!.projectId!, episodeId: scope?.episodeId, shotId: scope?.shotId, characterId: scope?.characterId }),
    enabled: enabled && Boolean(capability && scope?.projectId),
    staleTime: 30_000,
    retry: false,
  });
}

function useBusinessSelectionFacadeEvaluation(
  capability: string,
  scope: CapabilityPickerQuery["migrationScope"],
  businessSurface: string | undefined,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ["model-platform-v2", "business-selection-facade-evaluation", businessSurface ?? "", capability, scope?.projectId ?? "", scope?.episodeId ?? "", scope?.shotId ?? "", scope?.characterId ?? ""],
    queryFn: () => getModelPlatformBusinessSelectionFacadeEvaluation(businessSurface!, capability, { projectId: scope!.projectId!, episodeId: scope?.episodeId, shotId: scope?.shotId, characterId: scope?.characterId }),
    enabled: enabled && Boolean(businessSurface && capability && scope?.projectId),
    staleTime: 30_000,
    retry: false,
  });
}

function optionLabel(option: CapabilityOption) {
  const route = option.runtime.transport ? `${option.runtime.transport} · ${option.runtime.status}` : option.runtime.status;
  return `${option.model.name} · ${option.profile.title} v${option.profile.version_no} · ${route}`;
}

function issueSummary(option: CapabilityOption) {
  return [...option.blockers, ...option.warnings].map((item) => item.message).join("；");
}

export function CapabilityPicker({
  capability,
  value,
  onChange,
  query,
  label = "生成方式",
  description = "自动使用当前范围的项目偏好；指定版本只影响本次操作。",
  allowAuto = true,
  disabled = false,
  showDetails = true,
  className = "",
  migrationBusinessSurface,
}: CapabilityPickerProps) {
  const selectId = useId();
  const helpId = `${selectId}-help`;
  const [migrationAuditOpen, setMigrationAuditOpen] = useState(false);
  const migrationAudit = useBusinessSelectionShadow(capability, query.migrationScope, migrationAuditOpen);
  const facadeEvaluation = useBusinessSelectionFacadeEvaluation(capability, query.migrationScope, migrationBusinessSurface, migrationAuditOpen);
  const data = query.data;
  const selectable = useMemo(() => data?.options.filter((item) => item.selectable) ?? [], [data]);
  const unavailable = useMemo(() => data?.options.filter((item) => !item.selectable) ?? [], [data]);
  const selected = value
    ? data?.options.find((item) => item.profile_version_id === value) ?? null
    : data?.selection.option ?? null;
  const effectiveProfileVersionId = value || data?.selection.profile_version_id || "";
  const ready = value ? selected?.selectable === true : data?.selection.ready === true;
  const configuredMismatch = data?.configured_runtime
    && data.configured_runtime.publication_status !== "PUBLISHED"
    ? data.configured_runtime
    : null;
  const autoLabel = data?.selection.option
    ? `自动：${data.selection.option.model.name} · ${data.selection.source}`
    : "自动：当前没有可执行配置";

  return (
    <div className={`capability-picker ${className}`.trim()} data-capability={capability}>
      <div className="capability-picker__control">
        <label htmlFor={selectId}>
          <span>{label}</span>
          <select
            id={selectId}
            value={value}
            aria-describedby={helpId}
            aria-invalid={query.error || (data && !ready) ? "true" : undefined}
            disabled={disabled || query.isPending}
            onChange={(event) => onChange(event.target.value)}
          >
            {allowAuto ? <option value="">{autoLabel}</option> : <option value="">请选择已发布且可执行的版本</option>}
            {selectable.length ? (
              <optgroup label="可使用">
                {selectable.map((option) => (
                  <option value={option.profile_version_id} key={option.profile_version_id}>{optionLabel(option)}</option>
                ))}
              </optgroup>
            ) : null}
            {unavailable.length ? (
              <optgroup label="需要处理（不可选）">
                {unavailable.map((option) => (
                  <option value={option.profile_version_id} key={option.profile_version_id} disabled>
                    {option.model.name} · {issueSummary(option) || option.profile.status}
                  </option>
                ))}
              </optgroup>
            ) : null}
          </select>
        </label>
        {showDetails && effectiveProfileVersionId ? <ProfileExecutionDetailButton profileVersionId={effectiveProfileVersionId} /> : null}
      </div>

      <div id={helpId} className="capability-picker__help" aria-live="polite">
        {query.isPending ? <span className="muted">正在解析项目偏好、已发布版本和执行路线…</span> : null}
        {query.error ? (
          <span className="capability-picker__error" role="alert">
            能力选项读取失败：{String(query.error.message || query.error)}
            <button type="button" className="text-action" onClick={() => void query.refetch()}>重试</button>
          </span>
        ) : null}
        {!query.isPending && !query.error && data ? (
          <>
            <span className={`capability-picker__status ${ready ? "is-ready" : "is-blocked"}`}>
              {ready && selected
                ? `${value ? "本次固定" : "自动解析"}：${selected.model.name} · ${selected.runtime.title || selected.model.provider} · ${selected.runtime.status}`
                : data.selection.blockers.map((item) => item.message).join("；") || "没有可执行的已发布配置"}
            </span>
            <span className="muted">{description}</span>
            {selected?.warnings.map((warning) => <span className="capability-picker__warning" key={warning.code}>{warning.message}</span>)}
          </>
        ) : null}
      </div>

      {configuredMismatch ? (
        <div className="capability-picker__mismatch" role="status">
          <div>
            <strong>已配置的默认模型还不能用于这项能力</strong>
            <span>{configuredMismatch.model} · {configuredMismatch.provider}</span>
            <small>{configuredMismatch.message}。配置运行时不会自动改写已发布生产版本。</small>
          </div>
          <Link to={data?.repair_href || "/system/capabilities?view=resources"}>验证并发布</Link>
        </div>
      ) : null}
      {!query.isPending && !query.error && data && !ready && !configuredMismatch ? (
        <Link className="capability-picker__repair" to={data.repair_href}>前往能力与模型处理</Link>
      ) : null}
      {query.migrationScope?.projectId ? (
        <details
          className="capability-picker__migration-audit"
          onToggle={(event) => setMigrationAuditOpen((event.currentTarget as HTMLDetailsElement).open)}
        >
          <summary>V2 迁移双读对账（只读，不改变本次提交）</summary>
          {migrationAudit.isPending || facadeEvaluation.isPending ? <p className="muted" aria-live="polite">正在分别解析旧业务链、V2 能力分配和切换就绪状态…</p> : null}
          {migrationAudit.error ? (
            <p className="capability-picker__error" role="alert">
              V2 对账读取失败：{String(migrationAudit.error.message || migrationAudit.error)}
              <button type="button" className="text-action" onClick={() => void migrationAudit.refetch()}>重试</button>
            </p>
          ) : null}
          {facadeEvaluation.error ? <p className="capability-picker__error" role="alert">切换就绪状态读取失败：{String(facadeEvaluation.error.message || facadeEvaluation.error)}</p> : null}
          {migrationAudit.data ? <MigrationAuditSummary comparison={migrationAudit.data.comparison} facade={facadeEvaluation.data?.evaluation} /> : null}
        </details>
      ) : null}
    </div>
  );
}

function MigrationAuditSummary({ comparison, facade }: { comparison: ModelPlatformBusinessSelectionShadow; facade?: ModelPlatformBusinessSelectionFacadeEvaluation }) {
  const detail = comparison.comparison;
  const parameters = comparison.parameter_contract_comparison;
  const status = detail.status === "MAPPED_EQUIVALENT"
    ? "已批准映射：双读可比较，当前页面仍使用旧链提交。"
    : detail.status === "BOTH_PRESENT_UNMAPPED"
      ? "两侧均有 Profile，但尚未批准版本映射，不能切换。"
      : detail.status === "LEGACY_SCOPE_UNSUPPORTED"
        ? "旧链不支持当前作用域，此结果只能观察。"
        : `对账状态：${detail.status}`;
  return <div className="capability-picker__migration-summary" role="status">
    <strong>{status}</strong>
    <span>旧链：{comparison.legacy_resolution.ready ? `${comparison.legacy_resolution.source} · 可执行` : comparison.legacy_resolution.blocked_reason || "不可执行"}</span>
    <span>V2：{comparison.v2_resolution.ready ? `${comparison.v2_resolution.resolution_reason} · 可执行` : comparison.v2_resolution.blocked_reason || "不可执行"}</span>
    <span>参数合同：{parameters.matches ? `字段、约束和有效默认值已一致（${parameters.common_fields.length} 项；参数值未返回）` : parameterContractIssue(parameters)}</span>
    {facade ? <span>Facade：{facade.decision === "CUTOVER_CANDIDATE" ? "可进入切换候选，但当前提交仍固定走旧链" : `保持旧链（${facade.blockers.join("、") || "切换条件未满足"}）`}</span> : <span>Facade：此页面尚未声明业务 surface，只展示双读对账。</span>}
    <small>{detail.reason}</small>
  </div>;
}

function parameterContractIssue(parameters: ModelPlatformBusinessSelectionShadow["parameter_contract_comparison"]) {
  if (parameters.status === "PROFILE_UNAVAILABLE") return "至少一侧没有可比较的已发布 Profile";
  if (parameters.status === "UNSAFE_FIELD_NAME") return "发现疑似运行时字段，已拒绝在迁移对账中比较";
  const differenceCount = parameters.legacy_only_fields.length + parameters.v2_only_fields.length
    + parameters.differences.type.length + parameters.differences.required.length
    + parameters.differences.scope.length + parameters.differences.constraint.length + parameters.differences.default.length;
  return `存在 ${differenceCount} 项字段/约束/默认值差异，不能切换`;
}

export function effectiveCapabilityProfile(query: CapabilityPickerQuery, explicitProfileVersionId: string) {
  const selected = explicitProfileVersionId
    ? query.data?.options.find((item) => item.profile_version_id === explicitProfileVersionId)
    : query.data?.selection.option;
  return {
    profileVersionId: explicitProfileVersionId || query.data?.selection.profile_version_id || "",
    option: selected ?? null,
    ready: explicitProfileVersionId ? selected?.selectable === true : query.data?.selection.ready === true,
  };
}
