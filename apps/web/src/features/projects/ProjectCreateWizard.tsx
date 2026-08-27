import { useEffect, useMemo, useReducer, useRef, type ReactNode } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  createProject,
  listDeliveryPresets,
  planProjectCreation,
  type Profile,
  type Project,
  type ProjectCreatePayload,
  type ProjectCreationPlan,
} from "../../generated/api";
import { LANGUAGE_OPTIONS } from "../shared/formOptions";
import { canonicalCapabilityLabel } from "../preferences-v2/canonicalCapabilities";
import { ProfileExecutionDetailButton } from "../model-config/ProfileExecutionDetailButton";
import { StudioIcon } from "../../components/icons";

type Step = 1 | 2 | 3;
type SetupMode = "CREATE_FIRST" | "PRODUCTION_READY";

type CreationState = {
  step: Step;
  title: string;
  code: string;
  seasons: number;
  episodes: number;
  durationSeconds: number;
  presetId: string;
  language: string;
  subtitleMode: ProjectCreatePayload["subtitle_mode"];
  setupMode: SetupMode;
  profileBindings: Record<string, string>;
};

type CreationAction =
  | { type: "title"; value: string }
  | { type: "field"; field: "seasons" | "episodes" | "durationSeconds" | "language" | "subtitleMode"; value: string | number }
  | { type: "preset"; value: string }
  | { type: "setup-mode"; value: SetupMode; recommendedBindings: Record<string, string> }
  | { type: "profile"; capability: string; value: string }
  | { type: "step"; value: Step };

type FormatPreset = {
  id: string;
  title: string;
  description: string;
  ratio: string;
  width: number;
  height: number;
  fps: number;
};

const SAFE_FORMAT_FALLBACK: FormatPreset = { id: "safe_vertical_1080p", title: "竖屏 1080P", description: "9:16 · 1080P · 24 fps", ratio: "9:16", width: 1080, height: 1920, fps: 24 };

const DEFAULT_STATE: CreationState = {
  step: 1,
  title: "",
  code: "",
  seasons: 1,
  episodes: 10,
  durationSeconds: 90,
  presetId: "",
  language: "zh-CN",
  subtitleMode: "BOTH",
  setupMode: "CREATE_FIRST",
  profileBindings: {},
};

function generateSlug(text: string): string {
  if (!text.trim()) return "";
  let slug = text.toLowerCase().replace(/[^a-z0-9\u4e00-\u9fa5]+/g, "_").replace(/^_+|_+$/g, "");
  if (/[\u4e00-\u9fa5]/.test(slug)) {
    const ascii = slug.replace(/[\u4e00-\u9fa5]+/g, "").replace(/^_+|_+$/g, "");
    if (ascii && /^[a-z]/.test(ascii)) slug = ascii;
    else {
      const hash = Math.abs(text.split("").reduce((value, char) => ((value << 5) - value) + char.charCodeAt(0), 0)).toString(36).slice(0, 6);
      slug = `drama_${hash}`;
    }
  }
  if (!/^[a-z]/.test(slug)) slug = `project_${slug.replace(/^[^a-z0-9_]+/, "") || "main"}`;
  return slug.slice(0, 64);
}

function reducer(state: CreationState, action: CreationAction): CreationState {
  switch (action.type) {
    case "title":
      return { ...state, title: action.value, code: generateSlug(action.value) };
    case "field":
      return { ...state, [action.field]: action.value } as CreationState;
    case "preset":
      return { ...state, presetId: action.value };
    case "setup-mode":
      return {
        ...state,
        setupMode: action.value,
        profileBindings: action.value === "PRODUCTION_READY" ? action.recommendedBindings : {},
      };
    case "profile":
      return { ...state, profileBindings: { ...state.profileBindings, [action.capability]: action.value } };
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
    { id: 2, label: "创作方式" },
    { id: 3, label: "确认创建" },
  ] as const;
  return <ol className="creator-wizard-steps" aria-label="创建进度">
    {steps.map((step) => <li key={step.id} className={current === step.id ? "current" : current > step.id ? "complete" : "pending"} aria-current={current === step.id ? "step" : undefined}>
      <span aria-hidden="true">{current > step.id ? "✓" : step.id}</span><strong>{step.label}</strong>
    </li>)}
  </ol>;
}

function ChoiceCard({ checked, title, description, name, value, onChange, badge }: {
  checked: boolean;
  title: string;
  description: string;
  name: string;
  value: string;
  onChange: () => void;
  badge?: string;
  children?: ReactNode;
}) {
  return <label className={`creator-choice-card${checked ? " selected" : ""}`}>
    <input type="radio" name={name} value={value} checked={checked} onChange={onChange} />
    <span className="creator-choice-copy"><span className="creator-choice-title"><strong>{title}</strong>{badge && <small>{badge}</small>}</span><span>{description}</span></span>
  </label>;
}

export function ProjectCreateWizard({ onCreated, profiles = [], profilesPending = false }: {
  onCreated: (project: Project) => void;
  profiles?: Profile[];
  profilesPending?: boolean;
}) {
  const [open, setOpen] = useReducer((_: boolean, value: boolean) => value, false);
  const [state, dispatch] = useReducer(reducer, DEFAULT_STATE);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const firstFieldRef = useRef<HTMLInputElement>(null);
  const wasOpen = useRef(false);
  const deliveryPresets = useQuery({ queryKey: ["delivery-presets", "project-create"], queryFn: () => listDeliveryPresets(), enabled: open });
  const formatPresets = useMemo<FormatPreset[]>(() => {
    const mapped = (deliveryPresets.data?.items ?? []).map((item) => ({ id: item.code, title: item.title, description: `${item.spec.cover_aspect} · ${item.spec.width}×${item.spec.height} · ${item.spec.fps} fps`, ratio: item.spec.cover_aspect, width: item.spec.width, height: item.spec.height, fps: item.spec.fps }));
    return mapped.length ? mapped : [SAFE_FORMAT_FALLBACK];
  }, [deliveryPresets.data?.items]);

  const publishedByCapability = useMemo(() => Object.entries(
    profiles
      .filter((profile) => profile.status === "PUBLISHED")
      .reduce<Record<string, Profile[]>>((groups, profile) => {
        (groups[profile.capability] ??= []).push(profile);
        return groups;
      }, {}),
  ).sort(([left], [right]) => left.localeCompare(right)), [profiles]);
  const recommendedBindings = useMemo(() => Object.fromEntries(
    publishedByCapability.map(([capability, items]) => [capability, items[0]?.version_id ?? ""]),
  ), [publishedByCapability]);
  const preset = formatPresets.find((item) => item.id === state.presetId) ?? formatPresets[0];
  const selectedBindings = useMemo(() => Object.entries(state.profileBindings).filter(([, versionId]) => Boolean(versionId)), [state.profileBindings]);
  const productionReady = state.setupMode === "PRODUCTION_READY";
  const payload = useMemo<ProjectCreatePayload>(() => ({
    code: state.code.trim(),
    title: state.title.trim(),
    season_count: state.seasons,
    episode_count: state.episodes,
    target_duration_ms: state.durationSeconds * 1000,
    aspect_ratio: preset.ratio,
    width: preset.width,
    height: preset.height,
    fps: { numerator: preset.fps, denominator: 1 },
    primary_language: state.language,
    subtitle_mode: state.subtitleMode,
    ...(state.subtitleMode === "NONE" ? {} : { subtitle_language: state.language }),
    allow_unconfigured_capabilities: !productionReady,
    ...(productionReady ? {
      production_plan: {
        code: `${state.code}_local`,
        title: `${state.title} · 本地制作方案`,
        plan: {
          mode: "LOCAL_ONLY",
          aspect_ratio: preset.ratio,
          resolution: { width: preset.width, height: preset.height },
          fps: { numerator: preset.fps, denominator: 1 },
          primary_language: state.language,
          subtitle_mode: state.subtitleMode,
        },
      },
      profile_bindings: selectedBindings.map(([capability, profile_version_id]) => ({ capability, profile_version_id })),
      delivery_target: {
        code: `${state.code}_master`,
        title: `${state.title} · 本地母版`,
        spec: {
          path_rel: "06_delivery/master",
          width: preset.width,
          height: preset.height,
          fps: { numerator: preset.fps, denominator: 1 },
          subtitle_mode: state.subtitleMode,
        },
      },
    } : {}),
  }), [preset, productionReady, selectedBindings, state]);

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
    if (formatPresets[0] && (!state.presetId || !formatPresets.some((item) => item.id === state.presetId))) {
      dispatch({ type: "preset", value: formatPresets[0].id });
    }
  }, [formatPresets, state.presetId]);

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
  const setupValid = !productionReady || selectedBindings.length > 0;

  return <div className="project-create-wizard-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false); }}>
    <section ref={dialogRef} className="project-create-wizard creator-wizard" role="dialog" aria-modal="true" aria-labelledby="project-create-title" aria-describedby="project-create-description" tabIndex={-1}>
      <header className="creator-wizard-header">
        <div><p className="eyebrow">创建工作区</p><h3 id="project-create-title">创建新作品</h3></div>
        <button className="secondary" type="button" onClick={() => setOpen(false)}>关闭</button>
      </header>
      <p id="project-create-description" className="muted">只填写创作决策。目录、项目标识、制作方案与交付路径由系统生成并在创建前自动检查。</p>
      <StepIndicator current={state.step} />

      {state.step === 1 && <div className="creator-wizard-stage">
        <div className="creator-wizard-lead"><span>01</span><div><h4>这部作品叫什么？</h4><p>先建立故事空间；模型和高级制作参数不会阻挡你开始写作。</p></div></div>
        <label className="creator-title-field">作品标题 <span aria-hidden="true">*</span><input ref={firstFieldRef} value={state.title} required autoComplete="off" placeholder="例如：重返十八岁" onChange={(event) => dispatch({ type: "title", value: event.target.value })} /></label>
        <div className="creator-structure-grid">
          <label>季度数<input type="number" inputMode="numeric" min={1} max={20} value={state.seasons} onChange={(event) => dispatch({ type: "field", field: "seasons", value: Math.max(1, Number(event.target.value)) })} /><small>创建后仍可继续追加季度</small></label>
          <label>每季计划集数<input type="number" inputMode="numeric" min={1} max={200} value={state.episodes} onChange={(event) => dispatch({ type: "field", field: "episodes", value: Math.max(1, Number(event.target.value)) })} /><small>只是初始结构，不限制后续追加</small></label>
          <label>单集目标时长（秒）<input type="number" min={15} max={86400} step={1} value={state.durationSeconds} onChange={(event) => dispatch({ type: "field", field: "durationSeconds", value: Number(event.target.value) })} /><small>输入业务需要的精确时长；后续仍可按集和镜头微调</small></label>
        </div>
        <div className="field-fact"><span>项目技术标识</span><strong>{state.code || "填写标题后自动生成"}</strong><small>由作品标题稳定生成；创建前会自动检查冲突。</small></div>
        <div className="creator-wizard-actions"><button type="button" disabled={!basicsValid} onClick={() => dispatch({ type: "step", value: 2 })}>继续选择创作方式</button></div>
      </div>}

      {state.step === 2 && <div className="creator-wizard-stage">
        <div className="creator-wizard-lead"><span>02</span><div><h4>作品要怎样发布？</h4><p>选择一个场景预设即可；分辨率、帧率和字幕参数会一起联动。</p></div></div>
        <fieldset className="creator-format-fieldset"><legend>发布画幅</legend>{deliveryPresets.isPending && <p className="muted">正在读取服务端发布规格…</p>}<div className="creator-format-grid">{formatPresets.map((item, index) => <ChoiceCard key={item.id} name="format" value={item.id} checked={preset.id === item.id} onChange={() => dispatch({ type: "preset", value: item.id })} title={item.title} description={item.description} badge={index === 0 ? "服务端默认" : undefined} />)}</div><small className="muted">规格来自后端发布预设；读取失败时仅显示一项可继续创建的安全缺省值。</small></fieldset>
        <div className="creator-language-grid">
          <label>作品语言<select value={state.language} onChange={(event) => dispatch({ type: "field", field: "language", value: event.target.value })}>{LANGUAGE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
          <label>字幕输出<select value={state.subtitleMode} onChange={(event) => dispatch({ type: "field", field: "subtitleMode", value: event.target.value })}><option value="BOTH">画面字幕 + 独立字幕文件</option><option value="BURN_IN">仅画面字幕</option><option value="SIDECAR">仅独立字幕文件</option><option value="NONE">暂不需要字幕</option></select></label>
        </div>
        <fieldset className="creator-setup-fieldset"><legend>开始方式</legend>
          <ChoiceCard name="setup" value="CREATE_FIRST" checked={!productionReady} onChange={() => dispatch({ type: "setup-mode", value: "CREATE_FIRST", recommendedBindings })} title="先开始创作" description="立即建立故事与分集；需要生成画面时，首页会引导绑定本机模型。" badge="推荐" />
          <ChoiceCard name="setup" value="PRODUCTION_READY" checked={productionReady} onChange={() => dispatch({ type: "setup-mode", value: "PRODUCTION_READY", recommendedBindings })} title="同时配置现有模型" description={publishedByCapability.length ? `系统已找到 ${publishedByCapability.length} 类已发布能力，并为每类预选一个版本。` : "本机还没有已发布能力，请先创建作品。"} />
        </fieldset>
        {productionReady && <details className="creator-advanced-details" open><summary>检查系统推荐的模型绑定</summary>
          {profilesPending ? <p className="muted" role="status">正在读取本机已发布能力…</p> : publishedByCapability.length ? <div className="profile-binding-grid">{publishedByCapability.map(([capability, items]) => <div className="profile-binding-control" key={capability}><label>{canonicalCapabilityLabel(capability)}<select value={state.profileBindings[capability] ?? ""} onChange={(event) => dispatch({ type: "profile", capability, value: event.target.value })}><option value="">暂不绑定</option>{items.map((profile) => <option key={profile.version_id} value={profile.version_id}>{profile.title}{profile.version_no ? ` · 第 ${profile.version_no} 版` : ""}</option>)}</select></label><ProfileExecutionDetailButton profileVersionId={state.profileBindings[capability]} /></div>)}</div> : <p className="review-guidance">当前没有可绑定的已发布模型。请选择“先开始创作”，创建后从首页按引导配置。</p>}
        </details>}
        <div className="creator-wizard-actions"><button type="button" className="secondary" onClick={() => dispatch({ type: "step", value: 1 })}>返回</button><button type="button" disabled={!setupValid || (productionReady && profilesPending)} onClick={enterReview}>继续并自动检查</button></div>
      </div>}

      {state.step === 3 && <div className="creator-wizard-stage">
        <div className="creator-wizard-lead"><span>03</span><div><h4>确认作品工作区</h4><p>系统正在后台检查目录、空间和配置；检查不会创建文件或调用模型。</p></div></div>
        <section className="creator-review-summary" aria-label="作品创建摘要"><div><small>作品</small><strong>{state.title}</strong><span>{state.seasons} 季 × 每季 {state.episodes} 集 · 单集约 {state.durationSeconds} 秒</span></div><div><small>发布规格</small><strong>{preset.title}</strong><span>{preset.width} × {preset.height} · {preset.fps} fps · {state.subtitleMode === "NONE" ? "无字幕" : "含字幕"}</span></div><div><small>开始方式</small><strong>{productionReady ? "创建并绑定现有模型" : "先建立故事工作区"}</strong><span>{productionReady ? `${selectedBindings.length} 类能力已选择` : "模型可稍后配置"}</span></div></section>
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
