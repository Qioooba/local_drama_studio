/**
 * `/explainers/new` — the creation shell of 第 1 步 (design §B2.1, §B2.2, §C1, §E3).
 *
 * There is exactly one content form in the product, and this page reuses it:
 * the first screen asks “你现在有什么” with 已有口播稿 / 故事资料 as the two
 * primary cards and 从主题开始 as the secondary entry, then the same form
 * collects the body.  The chosen card is the `script_policy`; 粘贴 / 上传 /
 * 链接 is only the `input_kind` channel, so a `.txt` file never decides the
 * policy by its extension (§C1).
 *
 * The right-hand 288–304 px settings panel carries the new-work defaults of
 * §B2.2.  One of them is written through a real command instead of being
 * decorative:
 *
 * * 自定义风格 is applied with `patchExplainerVisualPreferences` immediately after
 *   creation (the create request itself has no field for it), before the plan is
 *   preflighted.
 *
 * There is no 片段策略 choice any more: every explainer picture is produced by real
 * AI 图生视频, so the only thing the panel does with 图生视频 is report whether an
 * executable profile exists and link to the capability page when it does not.
 *
 * Extra output versions (English / bilingual / vertical / clean) stay opt-in —
 * the default is exactly one captioned edition in the current language and
 * aspect (case V03).
 */

import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import {
  createExplainer,
  importExplainerSource,
  patchExplainerVisualPreferences,
  preflightExplainerPlan,
  startExplainerRun,
  type ExplainerCreateRequest,
  type ExplainerOutputRequest,
  type ExplainerVisualPreferences,
} from "../../generated/api";
import { stableIdempotencyKey } from "../../services/commandId";
import { queryKeys } from "../../query/queryKeys";
import { CapabilityPicker, useCapabilityOptions } from "../model-config/CapabilityPicker";
import { InlineError, InlineOk } from "./components";
import { ExplainerActionBarProvider, ExplainerStepActionBar, useExplainerActionBar } from "./ExplainerStepActionBar";
import { explainerStepStatuses } from "./ExplainerSteps";
import {
  ContentIntakeForm,
  EMPTY_CONTENT_INTAKE,
  SCRIPT_POLICY_LABELS,
  estimateNarrationSeconds,
  formatDurationLabel,
  narrationCharacterCount,
  pastedMaterialFile,
  type ContentIntakeValue,
  type ContentInputKind,
  type ScriptPolicy,
} from "./ScriptPage";
import { estimateStageLabel } from "./viewModels";
import "./explainers.css";
import "./explainers.pages.css";

type AutomationMode = "AUTO_WITH_EXCEPTIONS" | "REVIEW_BEFORE_RENDER" | "MANUAL_REVIEW";
type AspectRatio = ExplainerOutputRequest["aspect_ratio"];

const DURATION_PRESETS = [
  { label: "3 分钟", seconds: 180 },
  { label: "5 分钟", seconds: 300 },
  { label: "10 分钟", seconds: 600 },
];

const PRIMARY_ASPECTS: AspectRatio[] = ["16:9", "9:16"];
const MORE_ASPECTS: AspectRatio[] = ["3:4", "1:1"];

const AUTOMATION_OPTIONS: Array<{ value: AutomationMode; label: string; note: string }> = [
  { value: "AUTO_WITH_EXCEPTIONS", label: "自动生成（仅在异常时暂停）", note: "过程可返回修改：一键只补齐缺项，人工锁定的结果不会被覆盖。" },
  { value: "REVIEW_BEFORE_RENDER", label: "成片前确认一次", note: "成片前停下等你确认，之前各步仍可回改。" },
  { value: "MANUAL_REVIEW", label: "逐步制作", note: "每步由你确认后再进入下一步。" },
];

const CONTENT_KINDS: Array<{ value: "FACTUAL_EXPLAINER" | "ORIGINAL_FICTION"; label: string }> = [
  { value: "FACTUAL_EXPLAINER", label: "事实解说" },
  { value: "ORIGINAL_FICTION", label: "虚构故事" },
];

/**
 * The edition key says what the edition *is*: language, subtitle treatment and
 * aspect.  A fixed key like `zh-clean-169` used for a vertical or English edition
 * described something other than the request and the server refuses the
 * mismatch, so the key is always derived from the three real inputs.
 */
export function editionKey(voiceLocale: string, subtitleMode: string, aspect: string): string {
  const language = (voiceLocale.split("-")[0] || "und").toLowerCase();
  const mode = subtitleMode.toUpperCase();
  const subtitleToken = mode === "NONE" ? "clean" : mode.startsWith("BILINGUAL") ? "bilingual" : "captioned";
  return `${language}-${subtitleToken}-${aspect.replace(":", "")}`;
}

export function outputFor(
  voiceLocale: string,
  aspect: AspectRatio,
  subtitleMode: ExplainerOutputRequest["subtitle_mode"],
  subtitleLocales: string[],
): ExplainerOutputRequest {
  return {
    edition_key: editionKey(voiceLocale, subtitleMode, aspect),
    voice_locale: voiceLocale,
    subtitle_locales: subtitleLocales,
    subtitle_mode: subtitleMode,
    aspect_ratio: aspect,
    fps: { num: 25, den: 1 },
    duration_policy: "NATURAL_NARRATION",
    allow_soft_subtitle_fallback: false,
  };
}

/**
 * The default is exactly one captioned edition in the current language and
 * aspect, with its SRT exported alongside it.  Bilingual subtitles, a separate
 * voice language, a second aspect and a clean master are opt-in additions:
 * defaulting all of them on produced four editions for every request (§1.3, V03).
 */
export function defaultOutputs(
  voiceLocale: string,
  aspect: AspectRatio,
  bilingual: boolean,
  portrait: boolean,
  cleanMaster: boolean,
): ExplainerOutputRequest[] {
  const language = voiceLocale.split("-")[0].toLowerCase();
  const englishScope = language !== "en" ? ["en-US"] : [];
  const outputs: ExplainerOutputRequest[] = [
    bilingual && englishScope.length > 0
      ? outputFor(voiceLocale, aspect, "BILINGUAL_BURNED", [voiceLocale, ...englishScope])
      : outputFor(voiceLocale, aspect, "BURNED", [voiceLocale]),
  ];
  if (portrait && aspect !== "9:16") {
    outputs.push(outputFor(voiceLocale, "9:16", "BURNED", [voiceLocale]));
  }
  if (cleanMaster) {
    outputs.push(outputFor(voiceLocale, aspect, "NONE", []));
  }
  return outputs;
}

/** The real `input_kind` for a chosen policy and channel (§C1). */
export function inputKindFor(policy: ScriptPolicy, channel: ContentInputKind): ExplainerCreateRequest["input_kind"] {
  if (policy === "CREATE_FROM_TOPIC") return "TOPIC";
  if (channel === "REFERENCE_LINKS") return "REFERENCE_LINKS";
  if (channel === "DOCUMENT_IMPORT") return "DOCUMENT_IMPORT";
  return "PASTED_SCRIPT";
}

/**
 * `/explainers/new` is not inside the workspace shell, so it mounts the one
 * shared bottom bar itself (design §B1.3: the bar is rendered once, pages only
 * declare content).  The provider must be *above* the component that calls
 * `useExplainerActionBar`, which is why the page body is a separate component.
 */
export function ExplainerCreatePage() {
  return <ExplainerActionBarProvider>
    <CreatePageBody />
  </ExplainerActionBarProvider>;
}

function CreatePageBody() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [intake, setIntake] = useState<ContentIntakeValue>(EMPTY_CONTENT_INTAKE);
  const [contentKind, setContentKind] = useState<"FACTUAL_EXPLAINER" | "ORIGINAL_FICTION">("FACTUAL_EXPLAINER");
  const [durationPreset, setDurationPreset] = useState(180);
  const [customMinutes, setCustomMinutes] = useState("8");
  const [customDuration, setCustomDuration] = useState(false);
  const [aspect, setAspect] = useState<AspectRatio>("16:9");
  const [stylePrompt, setStylePrompt] = useState("");
  const [negativePrompt, setNegativePrompt] = useState("");
  const [automationMode, setAutomationMode] = useState<AutomationMode>("AUTO_WITH_EXCEPTIONS");
  const [researchMode, setResearchMode] = useState<"OFFLINE_IMPORT" | "WEB_RESEARCH">("OFFLINE_IMPORT");
  const [allowedDomains, setAllowedDomains] = useState("");
  const [textProfileId, setTextProfileId] = useState("");
  const [bilingual, setBilingual] = useState(false);
  const [portrait, setPortrait] = useState(false);
  const [englishEdition, setEnglishEdition] = useState(false);
  const [cleanMaster, setCleanMaster] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [preflight, setPreflight] = useState<Awaited<ReturnType<typeof preflightExplainerPlan>> | null>(null);
  const [createdProjectId, setCreatedProjectId] = useState<string | null>(null);

  const patchIntake = (patch: Partial<ContentIntakeValue>) => setIntake((current) => ({ ...current, ...patch }));

  const policy = intake.policy;
  const body = intake.body;
  const characters = narrationCharacterCount(body);
  const naturalSeconds = estimateNarrationSeconds(characters);
  const targetSeconds = policy === "PRESERVE_ORIGINAL"
    ? Math.min(7200, Math.max(30, naturalSeconds || 300))
    : customDuration
      ? Math.min(7200, Math.max(30, Math.round(Number(customMinutes || "0") * 60)))
      : durationPreset;

  const textOptions = useCapabilityOptions("LLM_STORY_PARSE");
  const videoOptions = useCapabilityOptions("VIDEO_I2V");
  const i2vReady = (videoOptions.data?.summary.selectable_count ?? 0) > 0;
  const i2vMissing = i2vReady
    ? null
    : videoOptions.data
      ? (videoOptions.data.selection.blockers[0]?.message
        ?? videoOptions.data.options.flatMap((option) => option.blockers)[0]?.message
        ?? videoOptions.data.configured_runtime?.message
        ?? "没有可执行的图生视频配置")
      : videoOptions.isError
        ? "能力检查失败，无法确认图生视频是否可执行"
        : "正在检查图生视频能力";
  const capabilityHref = videoOptions.data?.repair_href || textOptions.data?.repair_href || routes.systemCapabilities();

  const outputs = useMemo(() => {
    const base = defaultOutputs("zh-CN", aspect, bilingual, portrait, cleanMaster);
    if (!englishEdition) return base;
    return [...base, outputFor("en-US", aspect, "BURNED", ["en-US"])];
  }, [aspect, bilingual, cleanMaster, englishEdition, portrait]);

  const createRequest = (): ExplainerCreateRequest & { script_policy: ScriptPolicy | null } => {
    const resolvedPolicy: ScriptPolicy = policy ?? "PRESERVE_ORIGINAL";
    const inputKind = inputKindFor(resolvedPolicy, intake.inputKind);
    const hasBody = narrationCharacterCount(body) > 0;
    return {
      title: (intake.title.trim() || (intake.file ? intake.file.name : "")).trim() || "未命名解说",
      // §C4.1: the topic is never substituted for the body.
      topic: resolvedPolicy === "CREATE_FROM_TOPIC" ? intake.topic.trim() : "",
      content_kind: contentKind,
      input_kind: inputKind,
      script_policy: resolvedPolicy,
      input_payload: {
        script_policy: resolvedPolicy,
        extra_requirements: intake.extraRequirements.trim(),
        channel: intake.inputKind,
      },
      reference_urls: intake.inputKind === "REFERENCE_LINKS" ? intake.referenceUrls.split(/\s+/).filter(Boolean) : [],
      pasted_text: inputKind === "PASTED_SCRIPT" && hasBody ? body : null,
      duration_mode: "TARGET" as const,
      target_seconds: targetSeconds,
      tolerance_percent: 5,
      source_locale: "zh-CN",
      outputs,
      automation_mode: automationMode,
      inference_mode: "LOCAL_ONLY" as const,
      research_mode: researchMode,
      allowed_domains: researchMode === "WEB_RESEARCH" ? allowedDomains.split(/[\s,]+/).filter(Boolean) : [],
      aspect_ratio: aspect,
      primary_language: "zh-CN",
      width: aspect === "9:16" ? 1080 : aspect === "3:4" ? 1080 : aspect === "1:1" ? 1080 : 1920,
      height: aspect === "9:16" ? 1920 : aspect === "3:4" ? 1440 : aspect === "1:1" ? 1080 : 1080,
    };
  };

  /**
   * Create the workspace and apply the settings the create request cannot carry.
   * Never submits the run: that is the caller's decision (one-click vs stepwise).
   */
  const createWorkspace = async (): Promise<{ projectId: string; run: boolean }> => {
    if (!policy) throw new Error("请先回答“你现在有什么”：已有口播稿、故事资料，或从主题开始。");
    const payload = createRequest();
    const hasBody = narrationCharacterCount(body) > 0;
    if (payload.input_kind === "TOPIC" && !narrationCharacterCount(intake.topic)) throw new Error("题目模式必须填写题目");
    if (payload.input_kind === "DOCUMENT_IMPORT" && !intake.file) throw new Error("请选择要上传的文稿文件");
    if (payload.input_kind === "PASTED_SCRIPT" && !hasBody) throw new Error("请粘贴正文");

    // FE-A02: the idempotency key is bound to the WHOLE payload (including the
    // new script_policy), so fixing a wrong configuration rotates the key while a
    // pure network retry reuses it.
    const created = await createExplainer(
      payload as ExplainerCreateRequest,
      stableIdempotencyKey("explainer-create", payload),
    );
    const projectId = created.project.id;
    setCreatedProjectId(projectId);
    setStatus("已创建解说作品；正在登记正文与作品设置…");

    const material = intake.file ?? ((payload.script_policy === "PRESERVE_ORIGINAL" ? null : pastedMaterialFile(body, payload.script_policy)));
    if (material) {
      const imported = await importExplainerSource(projectId, material, { title: material.name });
      const preserved = (imported as Record<string, unknown>).preserved_script as Record<string, unknown> | null | undefined;
      setStatus(preserved
        ? "文稿已登记为讲稿版本（原样保留，未调用改写模型）。"
        : "资料已导入（只表示编码与哈希已保存，不代表事实已核验）。");
    } else if (payload.script_policy === "PRESERVE_ORIGINAL" && hasBody) {
      setStatus("口播稿已按原样登记为讲稿版本，没有调用改写模型。");
    }

    // 自定义风格 is a real write through the narrow PATCH endpoint.
    //
    // `visual_strategy` is deliberately NOT sent: the generated client still
    // declares it (pending regeneration), but the product removed 静图推拉 and the
    // whole 片段策略 choice — every explainer picture is produced by real AI 图生视频,
    // so the field is omitted rather than written with a removed value.
    const preferences: Partial<ExplainerVisualPreferences> = {};
    if (stylePrompt.trim()) preferences.style_prompt_override = stylePrompt.trim();
    if (negativePrompt.trim()) preferences.negative_prompt_override = negativePrompt.trim();
    if (Object.keys(preferences).length > 0) {
      await patchExplainerVisualPreferences(projectId, {
        expected_revision: Number(created.video.revision ?? 0),
        visual_preferences: preferences,
      });
      setStatus("作品设置已保存：风格与候选数量会冻结进本次计划。");
    }
    return { projectId, run: false };
  };

  const preflightAndRun = async (projectId: string) => {
    const report = await preflightExplainerPlan(projectId, { outputs });
    setPreflight(report);
    if (!report.executable) {
      setStatus("预检发现阻塞项，已停止提交；请按下面的下一步说明处理后重试。");
      return;
    }
    setStatus("预检通过，正在提交后台生产…");
    const run = await startExplainerRun(
      projectId,
      { plan_hash: report.plan_hash, outputs, start_workflow: true },
      stableIdempotencyKey("explainer-run", { planHash: report.plan_hash }),
    );
    setStatus(`已提交生产：运行 ${run.id}（${run.projected_status}）。浏览器关闭后由服务端继续。`);
    navigate(routes.explainerPage(projectId, "script"));
  };

  const oneClick = useMutation({
    mutationFn: async () => {
      const { projectId } = await createWorkspace();
      await preflightAndRun(projectId);
      return projectId;
    },
    onSuccess: async () => {
      setError(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      setStatus("");
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const saveDraft = useMutation({
    mutationFn: async () => {
      const { projectId } = await createWorkspace();
      setStatus("草稿已保存为真实作品；你可以继续修改，或进入第一步继续编辑。");
      return projectId;
    },
    onSuccess: async () => {
      setError(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      setStatus("");
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const stepwise = useMutation({
    mutationFn: async () => {
      const { projectId } = await createWorkspace();
      return projectId;
    },
    onSuccess: async (projectId) => {
      setError(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
      navigate(routes.explainerPage(projectId, "script"));
    },
    onError: (mutationError) => {
      setStatus("");
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const hasContent = narrationCharacterCount(body) > 0 || narrationCharacterCount(intake.topic) > 0 || Boolean(intake.file)
    || narrationCharacterCount(intake.referenceUrls) > 0;
  const oneClickMode = automationMode === "AUTO_WITH_EXCEPTIONS";
  const primaryBusy = oneClickMode ? oneClick.isPending : stepwise.isPending;
  const primaryDisabled = !policy || !hasContent;
  const primaryDisabledReason = !policy
    ? "请先选择内容用途：已有口播稿 / 故事资料 / 从主题开始。"
    : !hasContent
      ? "还没有内容：请粘贴正文、上传文稿或填写题目。"
      : null;

  useExplainerActionBar({
    primary: {
      label: oneClickMode ? "一键生成到预览" : "保存并下一步：人物与风格",
      busy: primaryBusy,
      disabled: primaryDisabled,
      disabledReason: primaryDisabledReason,
      onClick: () => (oneClickMode ? oneClick.mutate() : stepwise.mutate()),
    },
    save: hasContent
      ? { label: "保存草稿", busy: saveDraft.isPending, onClick: () => saveDraft.mutate() }
      : null,
    summary: createdProjectId
      ? `草稿已保存：作品 ${createdProjectId}`
      : policy
        ? `${SCRIPT_POLICY_LABELS[policy]} · ${characters} 字 · 目标 ${formatDurationLabel(targetSeconds)}`
        : "尚未选择内容用途",
  });

  // A brand-new work has no products yet, so the step bar starts from the real
  // "未开始" state instead of an invented progress value.
  const metadata = useMemo(() => explainerStepStatuses(null), []);

  return <div className="explainer-workspace explainer-page">
    <header className="explainer-header">
      <div>
        <h1>新建解说</h1>
        <p className="muted">
          先回答“你现在有什么”，再补作品设置。来源范围、模型与额外输出都在右侧折叠项里，不会因为默认值替你多做一步。
        </p>
      </div>
      <div className="explainer-actions">
        <Link className="explainer-action-bar__button" to={routes.explainers()}>返回作品列表</Link>
        {createdProjectId ? (
          <button type="button" onClick={() => navigate(routes.explainerPage(createdProjectId, "script"))}>打开第 1 步</button>
        ) : null}
      </div>
    </header>

    <div className="explainer-create-layout">
      <section className="explainer-panel" aria-label="内容输入">
        <ContentIntakeForm
          value={intake}
          onChange={patchIntake}
          idPrefix="create"
          showTitle
          titleExtras={intake.file ? `已用文件名预填：${intake.file.name}；机器编号由服务端自动生成。` : "有文件时用文件名预填，可修改；机器编号由服务端自动生成。"}
          generate={policy === "PRESERVE_ORIGINAL" ? null : {
            // §B2.3 puts “AI 生成讲稿” on an existing work's 资料/题目 area: a
            // draft revision cannot exist before the work does.  The button is
            // therefore disabled with the exact reason and the real route (the
            // primary action) instead of pretending a model ran.
            onClick: () => undefined,
            pending: false,
            disabledReason: "创建作品后才能生成讲稿草稿：底部主按钮会先创建作品，再按同一流水线生成。",
          }}
          feedback={null}
          error={null}
        />
      </section>

      <aside className="explainer-panel" aria-label="作品设置">
        <h2>作品设置</h2>
        <div className="explainer-settings">
          <label className="explainer-field">
            <span>内容属性</span>
            <select value={contentKind} onChange={(event) => setContentKind(event.target.value as typeof contentKind)}>
              {CONTENT_KINDS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
            <small className="explainer-settings__note">影响资料准备与画面表述；事实解说不会把模型记忆当来源。</small>
          </label>

          <div className="explainer-field">
            <span>时长</span>
            {policy === "PRESERVE_ORIGINAL" ? (
              <div className="explainer-estimate">
                <strong>自然时长</strong>
                <small>
                  {characters > 0
                    ? `按正文 ${characters} 字估算 ${formatDurationLabel(naturalSeconds)}（预计；配音后更新为实测）`
                    : "等待正文：登记口播稿后按稿件自然时长制作。"}
                </small>
                <small className="explainer-settings__note">
                  已有口播稿不提供目标分钟下拉：设置 3 分钟不会自动改写或截断原稿。需要精简稿请在第 1 步显式生成精简草稿再比较采用。
                </small>
              </div>
            ) : (
              <>
                <select
                  aria-label="时长"
                  value={customDuration ? "custom" : String(durationPreset)}
                  onChange={(event) => {
                    if (event.target.value === "custom") {
                      setCustomDuration(true);
                      return;
                    }
                    setCustomDuration(false);
                    setDurationPreset(Number(event.target.value));
                  }}
                >
                  {DURATION_PRESETS.map((preset) => <option key={preset.seconds} value={String(preset.seconds)}>{preset.label}</option>)}
                  <option value="custom">自定义…</option>
                </select>
                {customDuration ? (
                  <input
                    aria-label="自定义分钟数"
                    type="number"
                    min={1}
                    max={120}
                    step={1}
                    value={customMinutes}
                    onChange={(event) => setCustomMinutes(event.target.value)}
                  />
                ) : null}
                <small className="explainer-settings__note">时长只作为写稿字数预算；配音后展示实测时长。</small>
              </>
            )}
          </div>

          <label className="explainer-field">
            <span>画幅</span>
            <select aria-label="输出画幅" value={aspect} onChange={(event) => setAspect(event.target.value as AspectRatio)}>
              <option value="16:9">16:9 横屏</option>
              <option value="9:16">9:16 竖屏</option>
              <option value="3:4">3:4</option>
              <option value="1:1">1:1</option>
            </select>
            <small className="explainer-settings__note">一处选择影响整片默认构图；竖版重新排版，不是把横屏居中裁切。</small>
          </label>

          <label className="explainer-field">
            <span>风格</span>
            <select aria-label="风格" value="" onChange={() => undefined}>
              <option value="">项目默认（服务端默认风格版本）</option>
            </select>
            <small className="explainer-settings__note">
              已发布的栏目风格模板需要在作品创建后读取（当前请求没有该列表接口），因此这里只选服务端默认；
              第 2 步“更换风格”可在真实列表中切换，不会硬写品牌名。
            </small>
            <details>
              <summary>自定义风格</summary>
              <div className="explainer-intake__body" style={{ marginTop: 8 }}>
                <label>
                  风格描述
                  <textarea
                    aria-label="风格描述"
                    value={stylePrompt}
                    onChange={(event) => setStylePrompt(event.target.value)}
                    placeholder="例如：低饱和写实纪录片质感，40mm 视角。"
                  />
                </label>
                <label>
                  避免出现的内容
                  <textarea
                    aria-label="避免出现的内容"
                    value={negativePrompt}
                    onChange={(event) => setNegativePrompt(event.target.value)}
                    placeholder="例如：不要出现文字水印、现代服饰。"
                  />
                </label>
              </div>
            </details>
          </label>

          <div className="explainer-field">
            <span>画面生成方式</span>
            <p className="explainer-settings__note">
              每个画面段都由真实 AI 图生视频（图生视频模型）产出：没有「静图推拉」这类本地合成方式，
              也没有片段策略可选。
            </p>
            {!i2vReady ? (
              <small className="explainer-settings__note">
                需要真实可执行的图生视频能力：{i2vMissing}。<Link to={capabilityHref}>前往能力与模型</Link>
              </small>
            ) : (
              <small className="explainer-settings__note">已检测到可执行的图生视频能力，预检会再次确认。</small>
            )}
          </div>

          <label className="explainer-field">
            <span>自动程度</span>
            <select value={automationMode} onChange={(event) => setAutomationMode(event.target.value as AutomationMode)}>
              {AUTOMATION_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
            <small className="explainer-settings__note">{AUTOMATION_OPTIONS.find((item) => item.value === automationMode)?.note}</small>
          </label>

          <details>
            <summary>高级设置（资料获取 · 文本模型 · 额外输出）</summary>
            <div className="explainer-settings" style={{ marginTop: 8 }}>
              <label className="explainer-field">
                <span>资料获取</span>
                <select value={researchMode} onChange={(event) => setResearchMode(event.target.value as typeof researchMode)}>
                  <option value="OFFLINE_IMPORT">使用已提供资料（离线）</option>
                  <option value="WEB_RESEARCH">允许联网补充资料</option>
                </select>
              </label>
              {researchMode === "WEB_RESEARCH" ? (
                <label className="explainer-field">
                  <span>联网资料范围（域名，逗号分隔）</span>
                  <input value={allowedDomains} onChange={(event) => setAllowedDomains(event.target.value)} />
                </label>
              ) : policy === "CREATE_FROM_TOPIC" ? (
                <p className="explainer-note warn">
                  题目模式 + 离线资料：需要你补充资料或链接，系统不会伪称已经联网检索。
                </p>
              ) : null}

              <CapabilityPicker
                capability="LLM_STORY_PARSE"
                label="文本模型"
                description="默认项目配置；已有定稿且未请求改写时不需要可用写稿模型。"
                showDetails={false}
                value={textProfileId}
                onChange={setTextProfileId}
                query={textOptions}
              />

              <div className="explainer-settings__versions">
                <strong>额外输出版本（默认关闭）</strong>
                <label><input type="checkbox" checked={cleanMaster} onChange={(event) => setCleanMaster(event.target.checked)} /> 额外输出无字幕干净版</label>
                <label><input type="checkbox" checked={bilingual} onChange={(event) => setBilingual(event.target.checked)} /> 中文配音 + 中英双语字幕</label>
                <label><input type="checkbox" checked={englishEdition} onChange={(event) => setEnglishEdition(event.target.checked)} /> 独立英语配音版</label>
                <label><input type="checkbox" checked={portrait} onChange={(event) => setPortrait(event.target.checked)} /> 竖版输出</label>
                <small className="explainer-settings__note">
                  默认只生成当前语言、当前画幅的带字幕一版，并同步导出字幕 SRT。英语配音版使用独立时钟，不会照搬中文绝对时码。
                </small>
              </div>
            </div>
          </details>
        </div>
      </aside>
    </div>

    <InlineOk message={status || null} />
    <InlineError message={error} />

    {preflight ? (
      <section className="explainer-panel">
        <h2>预检报告</h2>
        <div className="explainer-actions">
          <span className={`badge ${preflight.executable ? "green" : "danger"}`}>
            {preflight.executable ? "可执行" : "存在阻塞"}
          </span>
          <span className="badge">计划哈希 {preflight.plan_hash.slice(0, 12)}…</span>
          <span className="badge">能力探查：{preflight.capability_snapshot.probed ? "已接入" : "未接入"}</span>
          <span className="badge">估算阶段：{estimateStageLabel(String(preflight.estimate.stage ?? ""))}</span>
        </div>
        {preflight.blockers.length > 0 ? (
          <div className="explainer-table-wrap">
            <table className="explainer-table">
              <thead><tr><th>代码</th><th>说明</th><th>下一步</th><th>可重试</th></tr></thead>
              <tbody>
                {preflight.blockers.map((blocker) => (
                  <tr key={blocker.code}>
                    <td>{blocker.code}</td>
                    <td>{blocker.message}</td>
                    <td>{blocker.next_step || "—"}</td>
                    <td>{blocker.retryable ? "是" : "否"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="explainer-ok">预检通过，已自动提交生产。</p>}
      </section>
    ) : null}

    {/* `/explainers/new` renders outside the workspace shell, so this page mounts
        the one shared bottom bar itself (the provider sits above this component).
        It never draws its own action row. */}
    <ExplainerStepActionBar
      projectId={createdProjectId ?? ""}
      activePage="script"
      statuses={metadata}
      summary={null}
    />
  </div>;
}
