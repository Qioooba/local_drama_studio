import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { bindProductionPlan, type ProjectConfiguration } from "../../generated/api";
import { queryKeys } from "../../query/queryKeys";
import { DEFAULT_PROJECT_FORMAT, ProjectFormatSelector, projectFormatIsValid, resolveProjectFormat, type ProjectFormatSelection } from "../projects/ProjectFormatSelector";

type Plan = NonNullable<ProjectConfiguration["production_plan"]>;
type CompositionPolicy = "LETTERBOX" | "COVER" | "CROP";

function selectionFromPlan(plan: Plan | null | undefined): ProjectFormatSelection {
  const presentation = plan?.plan?.presentation;
  const raw = presentation && typeof presentation === "object" ? presentation as Record<string, unknown> : plan?.plan ?? {};
  const width = Number(raw.width);
  const height = Number(raw.height);
  const fps = typeof raw.fps === "object" && raw.fps ? Number((raw.fps as Record<string, unknown>).numerator) / Number((raw.fps as Record<string, unknown>).denominator || 1) : Number(raw.fps);
  const known = Number.isFinite(width) && Number.isFinite(height) && Number.isFinite(fps);
  if (!known) return DEFAULT_PROJECT_FORMAT;
  const isLandscape = width >= height;
  const id = `${width}x${height}`;
  return {
    ...DEFAULT_PROJECT_FORMAT,
    mode: isLandscape ? "LANDSCAPE" : "PORTRAIT",
    landscapeId: isLandscape ? id : DEFAULT_PROJECT_FORMAT.landscapeId,
    portraitId: isLandscape ? DEFAULT_PROJECT_FORMAT.portraitId : id,
    customWidth: width,
    customHeight: height,
    fps,
  };
}

function compositionFromPlan(plan: Plan | null | undefined): CompositionPolicy {
  const generation = plan?.plan?.generation;
  const upscale = generation && typeof generation === "object" ? (generation as Record<string, unknown>).upscale : null;
  const fit = upscale && typeof upscale === "object" ? String((upscale as Record<string, unknown>).fit ?? "") : "";
  return fit === "COVER" || fit === "CROP" ? fit : "LETTERBOX";
}

function productionResolutionLabel(resolved: { width: number; height: number; title: string }): string {
  const pixels = `${resolved.width}x${resolved.height}`;
  if (["480x854", "854x480"].includes(pixels)) return "480P";
  if (["720x1280", "1280x720"].includes(pixels)) return "720P";
  if (["1080x1920", "1920x1080"].includes(pixels)) return "1080P";
  if (["1440x2560", "2560x1440"].includes(pixels)) return "2K";
  if (["2160x3840", "3840x2160"].includes(pixels)) return "4K";
  return resolved.title.replace(/^(横屏|竖屏)\s*/, "");
}

function canonicalPlan(selection: ProjectFormatSelection, compositionPolicy: CompositionPolicy) {
  const resolved = resolveProjectFormat(selection);
  return {
    schema_version: "localdrama.production-plan.v2",
    presentation: {
      aspect_ratio: resolved.ratio,
      width: resolved.width,
      height: resolved.height,
      fps: { numerator: selection.fps, denominator: 1 },
    },
    generation: {
      strategy: "CAPABILITY_RESOLVED",
      upscale: {
        enabled: true,
        required: true,
        stage: "COMPOSE_QC",
        executor: "builtin:ffmpeg",
        target: "PRESENTATION_SPEC",
        fit: compositionPolicy,
      },
    },
  };
}

function generationSummary(configuration: ProjectConfiguration | undefined): { status: string; text: string; tone: "ready" | "blocked" | "neutral" } {
  const spec = configuration?.production_spec as Record<string, any> | undefined;
  if (!spec) return { status: "待解析", text: "保存后会针对当前已发布 VIDEO workflow 解析实际生成规格。", tone: "neutral" };
  if (spec.status !== "READY") {
    const blocker = Array.isArray(spec.blockers) && spec.blockers[0] && typeof spec.blockers[0] === "object" ? String(spec.blockers[0].message ?? "当前规格无法执行") : "当前规格无法执行";
    return { status: "已阻塞", text: blocker, tone: "blocked" };
  }
  const generation = spec.generation as Record<string, any> | undefined;
  const actual = generation?.actual as Record<string, any> | undefined;
  const delivery = spec.delivery as Record<string, any> | undefined;
  if (generation?.mode === "UPSCALE_COMPOSE" && actual && delivery) {
    return {
      status: "代理 + 合成放大",
      text: `VIDEO 实际 ${actual.width} × ${actual.height} @ ${actual.fps}fps；${generation.upscale?.stage ?? "COMPOSE_QC"} 受审计放大至 ${delivery.width} × ${delivery.height}。`,
      tone: "ready",
    };
  }
  if (actual) return { status: "原生/graph 冻结", text: `VIDEO 实际 ${actual.width} × ${actual.height} @ ${actual.fps}fps，提交时会冻结到执行快照。`, tone: "ready" };
  return { status: "待解析", text: "保存后会针对当前已发布 VIDEO workflow 解析实际生成规格。", tone: "neutral" };
}

export function ProductionSpecEditor({ projectId, configuration }: { projectId: string; configuration?: ProjectConfiguration }) {
  const queryClient = useQueryClient();
  const planVersion = configuration?.production_plan?.version_id ?? "new";
  const [selection, setSelection] = useState<ProjectFormatSelection>(() => selectionFromPlan(configuration?.production_plan));
  const [compositionPolicy, setCompositionPolicy] = useState<CompositionPolicy>(() => compositionFromPlan(configuration?.production_plan));
  const [initializedVersion, setInitializedVersion] = useState(planVersion);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (initializedVersion !== planVersion) {
      setSelection(selectionFromPlan(configuration?.production_plan));
      setCompositionPolicy(compositionFromPlan(configuration?.production_plan));
      setInitializedVersion(planVersion);
    }
  }, [configuration?.production_plan, initializedVersion, planVersion]);

  const valid = projectFormatIsValid(selection);
  const resolved = useMemo(() => resolveProjectFormat(selection), [selection]);
  const resolutionLabel = productionResolutionLabel(resolved);
  const save = useMutation({
    mutationFn: () => bindProductionPlan(projectId, {
      // The API derives the globally-unique plan identity from projectId.
      // This value is only the user's save intent and must not encode an
      // aspect ratio that may change between revisions.
      code: "project-production-plan",
      title: `${resolved.ratio} ${resolved.width}×${resolved.height} 生产规格`,
      plan: canonicalPlan(selection, compositionPolicy),
    }),
    onSuccess: async () => {
      setNotice(`生产规格已保存：${resolved.width} × ${resolved.height} · ${selection.fps} fps；请在镜头页确认代理规格与 COMPOSE_QC 解析结果。`);
      await queryClient.invalidateQueries({ queryKey: queryKeys.productionSettings.section(projectId, "configuration") });
    },
    onError: (error) => setNotice(`保存失败：${error instanceof Error ? error.message : String(error)}`),
  });
  const summary = generationSummary(configuration);

  return <section className="panel production-spec-editor" aria-labelledby="production-spec-editor-title">
    <div className="production-setting-card__head">
      <div><span>规格</span><h3 id="production-spec-editor-title">制作交付规格</h3></div>
      <span className={`status-pill ${configuration?.production_plan ? "state-active" : "state-blocked"}`}>{configuration?.production_plan ? "已绑定版本" : "需要选择"}</span>
    </div>
    <p className="muted">这是项目唯一的制作规格来源。交付画布与 VIDEO workflow 的实际生成/代理规格分开显示；保存后每次提交都会冻结 resolved spec。</p>
    <ProjectFormatSelector value={selection} onChange={setSelection} />
    <label className="production-spec-composition">画幅不一致时的构图策略
      <select value={compositionPolicy} onChange={(event) => setCompositionPolicy(event.target.value as CompositionPolicy)}>
        <option value="LETTERBOX">LETTERBOX · 保留完整画面（加边）</option>
        <option value="COVER">COVER · 铺满画布（居中裁切）</option>
        <option value="CROP">CROP · 居中裁切</option>
      </select>
      <span className="muted">仅在 VIDEO 代理画幅与交付画幅不一致时生效；策略会写入 ProductionSpec 快照。</span>
    </label>
    <div className={`production-spec-resolution production-spec-resolution--${summary.tone}`} aria-live="polite">
      <strong>{summary.status}</strong><span>{summary.text}</span>
    </div>
    {notice && <p className={notice.startsWith("保存失败") ? "inline-error" : "inline-success"} role={notice.startsWith("保存失败") ? "alert" : "status"}>{notice}</p>}
    {save.error && !notice ? <p className="inline-error" role="alert">保存失败：{String(save.error)}</p> : null}
    <div className="action-row"><button type="button" className="primary-action" disabled={!valid || save.isPending} onClick={() => save.mutate()}>{save.isPending ? "保存中…" : `保存 ${resolutionLabel} 生产规格`}</button><span className="muted">当前选择：{resolved.width} × {resolved.height} · {selection.fps} fps · {resolved.ratio}</span></div>
  </section>;
}
