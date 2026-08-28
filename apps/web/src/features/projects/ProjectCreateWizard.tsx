import { useEffect, useMemo, useReducer, useRef } from "react";
import { useMutation } from "@tanstack/react-query";
import {
  createProject,
  planProjectCreation,
  type Project,
  type ProjectCreatePayload,
  type ProjectCreationPlan,
} from "../../generated/api";
import { LANGUAGE_OPTIONS } from "../shared/formOptions";
import { StudioIcon } from "../../components/icons";
import { generateProjectCode } from "./projectCode";
import { DEFAULT_PROJECT_FORMAT, ProjectFormatSelector, projectFormatIsValid, resolveProjectFormat, type ProjectFormatSelection } from "./ProjectFormatSelector";

type Step = 1 | 2 | 3;
type CreationState = {
  step: Step;
  title: string;
  code: string;
  seasons: number;
  episodes: number;
  durationSeconds: number;
  format: ProjectFormatSelection;
  language: string;
  subtitleMode: ProjectCreatePayload["subtitle_mode"];
};

type CreationAction =
  | { type: "title"; value: string }
  | { type: "field"; field: "seasons" | "episodes" | "durationSeconds" | "language" | "subtitleMode"; value: string | number }
  | { type: "format"; value: ProjectFormatSelection }
  | { type: "step"; value: Step };

const DEFAULT_STATE: CreationState = {
  step: 1,
  title: "",
  code: "",
  seasons: 1,
  episodes: 10,
  durationSeconds: 90,
  format: DEFAULT_PROJECT_FORMAT,
  language: "zh-CN",
  subtitleMode: "BOTH",
};

function reducer(state: CreationState, action: CreationAction): CreationState {
  switch (action.type) {
    case "title":
      return { ...state, title: action.value, code: generateProjectCode(action.value) };
    case "field":
      return { ...state, [action.field]: action.value } as CreationState;
    case "format":
      return { ...state, format: action.value };
    case "step":
      return { ...state, step: action.value };
  }
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "待计算";
  if (bytes < 1024 ** 2) return `${Math.ceil(bytes / 1024)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

const blockerLabels: Record<string, string> = {
  PROFILE_NOT_BOUND: "生成模型尚未绑定（不影响先写故事）",
  PRODUCTION_PLAN_NOT_BOUND: "生产方案将在配置模型时补齐",
  DELIVERY_TARGET_NOT_BOUND: "交付目标将在首次交付前补齐",
};

const checkLabels: Record<string, string> = {
  PROJECT_ROOT_AVAILABLE: "项目目录可用",
  PROJECT_CODE_AVAILABLE: "项目标识可用",
  DISK_CAPACITY: "本机空间充足",
};

function StepIndicator({ current }: { current: Step }) {
  const steps = [
    { id: 1, label: "作品信息" },
    { id: 2, label: "制作规格" },
    { id: 3, label: "确认创建" },
  ] as const;
  return <ol className="creator-wizard-steps" aria-label="创建进度">
    {steps.map((step) => <li key={step.id} className={current === step.id ? "current" : current > step.id ? "complete" : "pending"} aria-current={current === step.id ? "step" : undefined}>
      <span aria-hidden="true">{current > step.id ? "✓" : step.id}</span><strong>{step.label}</strong>
    </li>)}
  </ol>;
}

export function ProjectCreateWizard({ onCreated }: {
  onCreated: (project: Project) => void;
}) {
  const [open, setOpen] = useReducer((_: boolean, value: boolean) => value, false);
  const [state, dispatch] = useReducer(reducer, DEFAULT_STATE);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const firstFieldRef = useRef<HTMLInputElement>(null);
  const wasOpen = useRef(false);
  const format = useMemo(() => resolveProjectFormat(state.format), [state.format]);
  const payload = useMemo<ProjectCreatePayload>(() => ({
    code: state.code.trim(),
    title: state.title.trim(),
    season_count: state.seasons,
    episode_count: state.episodes,
    target_duration_ms: state.durationSeconds * 1000,
    aspect_ratio: format.ratio,
    width: format.width,
    height: format.height,
    fps: { numerator: state.format.fps, denominator: 1 },
    primary_language: state.language,
    subtitle_mode: state.subtitleMode,
    ...(state.subtitleMode === "NONE" ? {} : { subtitle_language: state.language }),
    allow_unconfigured_capabilities: true,
  }), [format, state]);

  const preflight = useMutation({ mutationFn: (request: ProjectCreatePayload) => planProjectCreation(request) });
  const create = useMutation({
    mutationFn: () => createProject(preflight.variables ?? payload),
    onSuccess: ({ project }) => onCreated(project),
  });

  const enterReview = () => {
    preflight.reset();
    dispatch({ type: "step", value: 3 });
    preflight.mutate(payload);
  };

  useEffect(() => {
    if (!open) {
      if (wasOpen.current) triggerRef.current?.focus();
      wasOpen.current = false;
      return undefined;
    }
    wasOpen.current = true;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    firstFieldRef.current?.focus();
    const dialog = dialogRef.current;
    if (!dialog) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex='-1'])"));
      if (!focusable.length) { event.preventDefault(); dialog.focus(); return; }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    dialog.addEventListener("keydown", onKeyDown);
    return () => {
      dialog.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  if (!open) return <button ref={triggerRef} type="button" className="primary-action" aria-haspopup="dialog" onClick={() => setOpen(true)}><StudioIcon name="sparkles" />新建项目</button>;

  const plan: ProjectCreationPlan | undefined = preflight.data?.plan;
  const basicsValid = Boolean(state.title.trim() && state.code.trim() && state.seasons >= 1 && state.episodes >= 1 && state.durationSeconds >= 15);
  const formatValid = projectFormatIsValid(state.format);

  return <div className="project-create-wizard-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false); }}>
    <section ref={dialogRef} className="project-create-wizard creator-wizard" role="dialog" aria-modal="true" aria-labelledby="project-create-title" aria-describedby="project-create-description" tabIndex={-1}>
      <header className="creator-wizard-header">
        <div><p className="eyebrow">创建工作区</p><h3 id="project-create-title">创建新作品</h3></div>
        <button className="secondary" type="button" onClick={() => setOpen(false)}>关闭</button>
      </header>
      <p id="project-create-description" className="muted">这里只创建作品与基础制作规格。目录和项目标识由系统生成，模型能力在进入项目后配置。</p>
      <StepIndicator current={state.step} />

      {state.step === 1 && <div className="creator-wizard-stage">
        <div className="creator-wizard-lead"><span>01</span><div><h4>这部作品叫什么？</h4><p>先建立故事空间；模型能力在进入项目后按实际创作环节配置。</p></div></div>
        <label className="creator-title-field">作品标题 <span aria-hidden="true">*</span><input ref={firstFieldRef} value={state.title} required autoComplete="off" placeholder="例如：重返十八岁" onChange={(event) => dispatch({ type: "title", value: event.target.value })} /></label>
        <div className="creator-structure-grid">
          <label>季度数<input type="number" inputMode="numeric" min={1} max={20} value={state.seasons} onChange={(event) => dispatch({ type: "field", field: "seasons", value: Math.max(1, Number(event.target.value)) })} /><small>创建后仍可继续追加季度</small></label>
          <label>每季计划集数<input type="number" inputMode="numeric" min={1} max={200} value={state.episodes} onChange={(event) => dispatch({ type: "field", field: "episodes", value: Math.max(1, Number(event.target.value)) })} /><small>只是初始结构，不限制后续追加</small></label>
          <label>单集目标时长（秒）<input type="number" min={15} max={86400} step={1} value={state.durationSeconds} onChange={(event) => dispatch({ type: "field", field: "durationSeconds", value: Number(event.target.value) })} /><small>输入业务需要的精确时长；后续仍可按集和镜头微调</small></label>
        </div>
        <div className="field-fact"><span>项目技术标识</span><strong>{state.code || "填写标题后自动生成"}</strong><small>自动保留英文；中文转为无声调拼音。创建前会检查冲突。</small></div>
        <div className="creator-wizard-actions"><button type="button" disabled={!basicsValid} onClick={() => dispatch({ type: "step", value: 2 })}>继续设置制作规格</button></div>
      </div>}

      {state.step === 2 && <div className="creator-wizard-stage">
        <div className="creator-wizard-lead"><span>02</span><div><h4>作品要以什么规格制作？</h4><p>选择画幅、清晰度和帧率；发布平台的交付规格可以稍后单独配置。</p></div></div>
        <ProjectFormatSelector value={state.format} onChange={(value) => dispatch({ type: "format", value })} />
        <div className="creator-language-grid">
          <label>作品语言<select value={state.language} onChange={(event) => dispatch({ type: "field", field: "language", value: event.target.value })}>{LANGUAGE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
          <label>字幕输出<select value={state.subtitleMode} onChange={(event) => dispatch({ type: "field", field: "subtitleMode", value: event.target.value })}><option value="BOTH">画面字幕 + 独立字幕文件</option><option value="BURN_IN">仅画面字幕</option><option value="SIDECAR">仅独立字幕文件</option><option value="NONE">暂不需要字幕</option></select></label>
        </div>
        <div className="creator-wizard-actions"><button type="button" className="secondary" onClick={() => dispatch({ type: "step", value: 1 })}>返回</button><button type="button" disabled={!formatValid} onClick={enterReview}>继续并自动检查</button></div>
      </div>}

      {state.step === 3 && <div className="creator-wizard-stage">
        <div className="creator-wizard-lead"><span>03</span><div><h4>确认作品工作区</h4><p>系统正在后台检查目录、空间和配置；检查不会创建文件或调用模型。</p></div></div>
        <section className="creator-review-summary" aria-label="作品创建摘要"><div><small>作品</small><strong>{state.title}</strong><span>{state.seasons} 季 × 每季 {state.episodes} 集 · 单集约 {state.durationSeconds} 秒</span></div><div><small>制作规格</small><strong>{format.title}</strong><span>{format.width} × {format.height} · {state.format.fps} fps · {state.subtitleMode === "NONE" ? "无字幕" : "含字幕"}</span></div></section>
        {preflight.isPending && <div className="creator-preflight-state" role="status"><span className="creator-spinner" aria-hidden="true" /><div><strong>正在自动检查本机环境…</strong><p>通常只需几秒，可以安全返回修改。</p></div></div>}
        {preflight.isError && <div className="inline-error" role="alert"><strong>自动检查未完成</strong><p>{preflight.error instanceof Error ? preflight.error.message : String(preflight.error)}</p><button type="button" className="secondary" onClick={() => preflight.mutate(payload)}>重新检查</button></div>}
        {plan && <div className={`creator-preflight-result ${plan.status === "BLOCKED" ? "blocked" : "ready"}`}>
          <div className="creator-preflight-heading"><strong>{plan.status === "BLOCKED" ? "需要先处理一项问题" : "可以创建"}</strong><span className={`status-pill ${plan.status === "BLOCKED" ? "danger" : "state-ready"}`}>{plan.status === "READY" ? "制作配置就绪" : plan.status === "READY_WITH_CONFIGURATION_BLOCKERS" ? "创作就绪" : "已阻塞"}</span></div>
          <ul className="creator-check-list">{plan.checks.map((check) => <li key={check.code} className={check.passed ? "passed" : "failed"}><span aria-hidden="true">{check.passed ? "✓" : "!"}</span>{checkLabels[check.code] ?? check.code}</li>)}</ul>
          <p>预计初始占用 {formatBytes(plan.estimated_bytes)}；目录与技术标识由系统管理。</p>
          {plan.blockers.length > 0 && <ul className="creator-blocker-list">{plan.blockers.map((blocker) => <li key={blocker}>{blockerLabels[blocker] ?? blocker}</li>)}</ul>}
          {plan.configuration_blockers.length > 0 && <details className="creator-advanced-details"><summary>稍后需要完成的制作配置</summary><ul>{plan.configuration_blockers.map((blocker) => <li key={blocker}>{blockerLabels[blocker] ?? blocker}</li>)}</ul></details>}
        </div>}
        {create.isError && <p className="inline-error" role="alert">创建失败：{create.error instanceof Error ? create.error.message : String(create.error)}</p>}
        <div className="creator-wizard-actions"><button type="button" className="secondary" onClick={() => dispatch({ type: "step", value: 2 })}>返回修改</button><button type="button" disabled={!plan || plan.status === "BLOCKED" || create.isPending} onClick={() => create.mutate()}>{create.isPending ? "正在创建…" : "创建并进入故事工作区"}</button></div>
      </div>}
    </section>
  </div>;
}
