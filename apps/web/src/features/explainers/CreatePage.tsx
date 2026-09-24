/**
 * Explainer creation page: four input modes, the minimum viable parameters and
 * the single "检查并一键生成" primary action.
 *
 * The button performs preflight and then submits in one gesture.  When the
 * preflight reports blockers the user sees the categorised report and the submit
 * is not attempted, which is what makes the single click safe.
 */

import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import {
  createExplainer,
  importExplainerSource,
  preflightExplainerPlan,
  startExplainerRun,
  type ExplainerOutputRequest,
} from "../../generated/api";
import { stableIdempotencyKey } from "../../services/commandId";
import { queryKeys } from "../../query/queryKeys";
import { estimateStageLabel } from "./viewModels";
import "./explainers.css";

type InputKind = "TOPIC" | "PASTED_SCRIPT" | "DOCUMENT_IMPORT" | "REFERENCE_LINKS";
type AutomationMode = "AUTO_WITH_EXCEPTIONS" | "REVIEW_BEFORE_RENDER" | "MANUAL_REVIEW";

const INPUT_KIND_LABELS: Record<InputKind, string> = {
  TOPIC: "① 输入题目自动找资料",
  PASTED_SCRIPT: "② 粘贴讲解稿",
  DOCUMENT_IMPORT: "③ 上传资料（TXT/MD/DOCX/PDF/EPUB）",
  REFERENCE_LINKS: "④ 提供参考链接研究同一题材",
};

const DURATION_PRESETS = [
  { label: "3 分钟", seconds: 180 },
  { label: "5 分钟", seconds: 300 },
  { label: "10 分钟", seconds: 600 },
  { label: "20 分钟", seconds: 1200 },
  { label: "30 分钟", seconds: 1800 },
];

const ASPECTS: Array<ExplainerOutputRequest["aspect_ratio"]> = ["16:9", "9:16", "3:4", "1:1"];

/**
 * The edition key says what the edition *is*: language, subtitle treatment, aspect.
 *
 * A fixed key like `zh-clean-169` used for a vertical or English edition described
 * something other than the request, and the server refuses such a mismatch. The
 * same three inputs always produce the same key, which is also what makes replaying
 * a submission reuse the edition instead of forking a second one of the same shape.
 */
function editionKey(voiceLocale: string, subtitleMode: string, aspect: string): string {
  const language = (voiceLocale.split("-")[0] || "und").toLowerCase();
  const mode = subtitleMode.toUpperCase();
  const subtitleToken = mode === "NONE" ? "clean" : mode.startsWith("BILINGUAL") ? "bilingual" : "captioned";
  return `${language}-${subtitleToken}-${aspect.replace(":", "")}`;
}

function outputFor(
  voiceLocale: string,
  aspect: ExplainerOutputRequest["aspect_ratio"],
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
 * The default is exactly one captioned edition in the current language and aspect,
 * with its SRT exported alongside it. Bilingual subtitles, a separate voice
 * language, a second aspect and a clean master are opt-in additions: defaulting all
 * of them on produced four editions for every request (design §1.3, case V03).
 */
function defaultOutputs(
  voiceLocale: string,
  aspect: ExplainerOutputRequest["aspect_ratio"],
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

export function ExplainerCreatePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [inputKind, setInputKind] = useState<InputKind>("TOPIC");
  const [topic, setTopic] = useState("");
  const [pastedText, setPastedText] = useState("");
  const [referenceUrls, setReferenceUrls] = useState("");
  const [targetSeconds, setTargetSeconds] = useState(300);
  const [sourceLocale, setSourceLocale] = useState("zh-CN");
  const [aspect, setAspect] = useState<ExplainerOutputRequest["aspect_ratio"]>("16:9");
  // Defaults follow the design: one captioned edition in the current language and
  // aspect. Every extra output is an explicit opt-in.
  const [bilingual, setBilingual] = useState(false);
  const [portrait, setPortrait] = useState(false);
  const [englishEdition, setEnglishEdition] = useState(false);
  const [cleanMaster, setCleanMaster] = useState(false);
  const [automationMode, setAutomationMode] = useState<AutomationMode>("AUTO_WITH_EXCEPTIONS");
  const [researchMode, setResearchMode] = useState<"OFFLINE_IMPORT" | "WEB_RESEARCH">("OFFLINE_IMPORT");
  const [allowedDomains, setAllowedDomains] = useState("");
  const [contentKind, setContentKind] = useState<"FACTUAL_EXPLAINER" | "ORIGINAL_FICTION">("FACTUAL_EXPLAINER");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [sourceFile, setSourceFile] = useState<File | null>(null);
  const [status, setStatus] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [preflight, setPreflight] = useState<Awaited<ReturnType<typeof preflightExplainerPlan>> | null>(null);
  const [createdProjectId, setCreatedProjectId] = useState<string | null>(null);

  const outputs = useMemo(() => {
    const base = defaultOutputs(sourceLocale, aspect, bilingual, portrait, cleanMaster);
    if (!englishEdition) return base;
    const language = sourceLocale.split("-")[0].toLowerCase();
    if (language === "en") return base;
    return [...base, outputFor("en-US", aspect, "BURNED", ["en-US"])];
  }, [aspect, bilingual, cleanMaster, englishEdition, portrait, sourceLocale]);

  const createAndPreflight = useMutation({
    mutationFn: async () => {
      if (!title.trim()) throw new Error("请填写标题或主题");
      if (inputKind === "TOPIC" && !topic.trim()) throw new Error("题目模式必须填写主题");
      if (inputKind === "PASTED_SCRIPT" && !pastedText.trim()) throw new Error("请粘贴讲解稿");
      if (inputKind === "DOCUMENT_IMPORT" && !sourceFile) throw new Error("请选择要上传的资料文件");
      if (inputKind === "REFERENCE_LINKS" && !referenceUrls.trim()) throw new Error("请填写参考链接");

      const payload = {
        title: title.trim(),
        topic: inputKind === "TOPIC" ? topic.trim() : title.trim(),
        content_kind: contentKind,
        input_kind: inputKind,
        pasted_text: inputKind === "PASTED_SCRIPT" ? pastedText : null,
        reference_urls: inputKind === "REFERENCE_LINKS" ? referenceUrls.split(/\s+/).filter(Boolean) : [],
        duration_mode: "TARGET" as const,
        target_seconds: targetSeconds,
        tolerance_percent: 5,
        source_locale: sourceLocale,
        outputs,
        automation_mode: automationMode,
        inference_mode: "LOCAL_ONLY" as const,
        research_mode: researchMode,
        allowed_domains: researchMode === "WEB_RESEARCH" ? allowedDomains.split(/[\s,]+/).filter(Boolean) : [],
        aspect_ratio: aspect,
        primary_language: sourceLocale,
      };
      // FE-A02: the idempotency key must be bound to the WHOLE payload.  The old key
      // hashed only `title:inputKind:targetSeconds`, so fixing a wrong configuration
      // (topic, outputs, automation mode, pasted text) and clicking again reused the
      // same key with a different body and the server answered
      // IDEMPOTENCY_PAYLOAD_MISMATCH.  The scope stays stable per user operation, and
      // the payload decides whether this is a retry or a new command.
      const created = await createExplainer(
        payload,
        stableIdempotencyKey("explainer-create", payload),
      );
      const projectId = created.project.id;
      setCreatedProjectId(projectId);
      setStatus("已创建解说作品，正在导入资料并冻结预检计划…");

      if (inputKind === "DOCUMENT_IMPORT" && sourceFile) {
        await importExplainerSource(projectId, sourceFile, { title: sourceFile.name });
        setStatus("资料已导入（仅表示编码与哈希已保存，不代表事实已核验）。");
      }
      const report = await preflightExplainerPlan(projectId, { outputs });
      setPreflight(report);
      return { projectId, report };
    },
    onSuccess: async ({ projectId, report }) => {
      setError(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
      if (!report.executable) {
        setStatus("预检发现阻塞项，已停止提交；请按下面的下一步说明处理后重新点击。");
        return;
      }
      // Preflight passed: submit immediately.  The design requires that a
      // non-blocked plan needs no second confirmation click.
      setStatus("预检通过，正在提交后台生产…");
      const run = await startExplainerRun(
        projectId,
        { plan_hash: report.plan_hash, outputs, start_workflow: true },
        stableIdempotencyKey("explainer-run", { planHash: report.plan_hash }),
      );
      setStatus(`已提交生产：运行 ${run.id}（${run.projected_status}）。`);
      navigate(routes.explainerOverview(projectId));
    },
    onError: (mutationError) => {
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
      setStatus("");
    },
  });

  return <div className="explainer-page">
    <header className="explainer-header">
      <div>
        <p className="eyebrow">NEW EXPLAINER</p>
        <h1>新建解说</h1>
        <p className="muted">首屏只要求标题/主题、目标时长、栏目风格、配音语言、输出画幅和自动程度；来源范围与高级模型参数折叠在下面。</p>
      </div>
      <div className="explainer-actions">
        <button type="button" onClick={() => navigate(routes.explainers())}>返回作品列表</button>
      </div>
    </header>

    <section className="explainer-panel">
      <h2>输入方式</h2>
      <div className="explainer-actions" role="radiogroup" aria-label="输入方式">
        {(Object.keys(INPUT_KIND_LABELS) as InputKind[]).map((kind) => (
          <button
            key={kind}
            type="button"
            className={inputKind === kind ? "primary-action" : undefined}
            role="radio"
            aria-checked={inputKind === kind}
            onClick={() => setInputKind(kind)}
          >
            {INPUT_KIND_LABELS[kind]}
          </button>
        ))}
      </div>
      <p className="explainer-note">
        默认不下载他人完整视频；已获授权的音视频可作为补充来源导入。上传完成不等于史实审核通过。
      </p>

      <div className="explainer-form-grid" style={{ marginTop: 14 }}>
        <label className="explainer-field full">
          标题 / 主题
          <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="例如：灯塔最后一页值班记录" />
        </label>

        {inputKind === "TOPIC" ? (
          <label className="explainer-field full">
            主题描述
            <textarea value={topic} onChange={(event) => setTopic(event.target.value)} placeholder="要讲清楚哪一个问题？" />
          </label>
        ) : null}

        {inputKind === "PASTED_SCRIPT" ? (
          <label className="explainer-field full">
            讲解稿
            <textarea value={pastedText} onChange={(event) => setPastedText(event.target.value)} placeholder="粘贴已有的讲解稿" />
          </label>
        ) : null}

        {inputKind === "DOCUMENT_IMPORT" ? (
          <label className="explainer-field full">
            参考资料文件
            <input
              type="file"
              accept=".txt,.md,.markdown,.docx,.pdf,.epub,.json,.csv,.srt,.vtt"
              onChange={(event) => setSourceFile(event.target.files?.[0] ?? null)}
            />
          </label>
        ) : null}

        {inputKind === "REFERENCE_LINKS" ? (
          <label className="explainer-field full">
            参考链接（每行一个）
            <textarea value={referenceUrls} onChange={(event) => setReferenceUrls(event.target.value)} placeholder="https://example.com/article" />
          </label>
        ) : null}
      </div>
    </section>

    <section className="explainer-panel">
      <h2>最少参数</h2>
      <div className="explainer-form-grid">
        <label className="explainer-field">
          目标时长
          <select value={targetSeconds} onChange={(event) => setTargetSeconds(Number(event.target.value))}>
            {DURATION_PRESETS.map((preset) => <option key={preset.seconds} value={preset.seconds}>{preset.label}</option>)}
          </select>
        </label>
        <label className="explainer-field">
          内容类型
          <select value={contentKind} onChange={(event) => setContentKind(event.target.value as typeof contentKind)}>
            <option value="FACTUAL_EXPLAINER">事实解说</option>
            <option value="ORIGINAL_FICTION">原创虚构</option>
          </select>
        </label>
        <label className="explainer-field">
          配音语言
          <select value={sourceLocale} onChange={(event) => setSourceLocale(event.target.value)}>
            <option value="zh-CN">中文</option>
            <option value="en-US">English</option>
          </select>
        </label>
        <label className="explainer-field">
          输出画幅
          <select value={aspect} onChange={(event) => setAspect(event.target.value as ExplainerOutputRequest["aspect_ratio"])}>
            {ASPECTS.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label className="explainer-field">
          自动程度
          <select value={automationMode} onChange={(event) => setAutomationMode(event.target.value as AutomationMode)}>
            <option value="AUTO_WITH_EXCEPTIONS">自动成片 · 仅在异常时暂停</option>
            <option value="REVIEW_BEFORE_RENDER">成片前确认一次</option>
            <option value="MANUAL_REVIEW">人工审查</option>
          </select>
        </label>
      </div>

      <div className="explainer-actions" style={{ marginTop: 12 }}>
        <label className="badge"><input type="checkbox" checked={cleanMaster} onChange={(event) => setCleanMaster(event.target.checked)} /> 额外输出无字幕干净版</label>
        <label className="badge"><input type="checkbox" checked={bilingual} onChange={(event) => setBilingual(event.target.checked)} /> 中文配音 + 中英双语字幕</label>
        <label className="badge"><input type="checkbox" checked={englishEdition} onChange={(event) => setEnglishEdition(event.target.checked)} /> 独立英语配音版</label>
        <label className="badge"><input type="checkbox" checked={portrait} onChange={(event) => setPortrait(event.target.checked)} /> 竖版输出</label>
      </div>
      <p className="explainer-note">
        默认只生成当前语言、当前画幅的带字幕一版，并同步导出字幕 SRT；以下选项按需增加额外版本。英语配音版使用独立的英文 TTS 时钟、独立剪辑点和字幕，不会照搬中文绝对时码。竖版重新排版构图与安全区，不是把横屏居中裁切。
      </p>
    </section>

    <section className="explainer-panel">
      <div className="explainer-actions">
        <button type="button" onClick={() => setAdvancedOpen((open) => !open)} aria-expanded={advancedOpen}>
          {advancedOpen ? "收起高级设置" : "展开高级设置（来源范围与出站策略）"}
        </button>
      </div>
      {advancedOpen ? (
        <div className="explainer-form-grid" style={{ marginTop: 12 }}>
          <label className="explainer-field">
            资料获取方式
            <select value={researchMode} onChange={(event) => setResearchMode(event.target.value as typeof researchMode)}>
              <option value="OFFLINE_IMPORT">纯离线导入</option>
              <option value="WEB_RESEARCH">允许联网研究</option>
            </select>
          </label>
          <label className="explainer-field full">
            允许的域名（联网研究时生效，逗号或换行分隔）
            <input value={allowedDomains} onChange={(event) => setAllowedDomains(event.target.value)} disabled={researchMode === "OFFLINE_IMPORT"} />
          </label>
          <div className="explainer-field full">
            <span>推理出站策略</span>
            <span className="badge">纯本地（LOCAL_ONLY）</span>
            <span className="muted">允许网页研究只打开资料获取进程的受控出站，不会解锁模型出口，也不会把全文送往云端。</span>
          </div>
        </div>
      ) : null}
    </section>

    <section className="explainer-panel">
      <div className="explainer-actions">
        <button
          type="button"
          className="primary-action"
          disabled={createAndPreflight.isPending}
          onClick={() => createAndPreflight.mutate()}
        >
          {createAndPreflight.isPending ? "正在检查并生成…" : "检查并一键生成"}
        </button>
        {createdProjectId ? <button type="button" onClick={() => navigate(routes.explainerOverview(createdProjectId))}>打开总览</button> : null}
      </div>
      {status ? <p className="explainer-ok" role="status">{status}</p> : null}
      {error ? <p className="explainer-inline-error" role="alert">{error}</p> : null}
      <p className="explainer-note">
        点击后先冻结输入、栏目版本、能力、出站政策、预算上限和任务骨架；无阻塞时自动提交，不需要第二次确认。研究、讲稿与 TTS 产生的子计划属于已授权范围内的合法展开，不会使计划自身过期。
      </p>
    </section>

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

        {Object.entries(preflight.categories).map(([category, blockers]) => (
          blockers.length === 0 ? null : (
            <div key={category} style={{ marginTop: 12 }}>
              <h3>{categoryLabel(category)}（{blockers.length}）</h3>
              <div className="explainer-table-wrap">
                <table className="explainer-table">
                  <thead><tr><th>代码</th><th>说明</th><th>下一步</th><th>可重试</th></tr></thead>
                  <tbody>
                    {blockers.map((blocker, index) => (
                      <tr key={`${blocker.code}-${index}`}>
                        <td>{blocker.code}</td>
                        <td>{blocker.message}</td>
                        <td>{blocker.next_step || "—"}</td>
                        <td>{blocker.retryable ? "是" : "否"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )
        ))}

        {preflight.executable ? (
          <p className="explainer-ok">预检通过，已自动提交生产；未执行的任务会由后台持久工作流继续。</p>
        ) : null}

        <details style={{ marginTop: 12 }}>
          <summary>任务骨架（{preflight.task_skeleton.length} 步）</summary>
          <div className="explainer-table-wrap">
            <table className="explainer-table">
              <thead><tr><th>步骤</th><th>输出</th><th>依赖</th><th>必需能力</th></tr></thead>
              <tbody>
                {preflight.task_skeleton.map((step) => (
                  <tr key={step.step_code}>
                    <td>{step.title}</td>
                    <td>{step.output_kind}</td>
                    <td>{step.depends_on.join("、") || "—"}</td>
                    <td>{step.requires_capabilities.join("、") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
        <p className="explainer-note">
          {String((preflight.estimate as Record<string, unknown>).note ?? "")}
        </p>
      </section>
    ) : null}
  </div>;
}

function categoryLabel(category: string): string {
  switch (category) {
    case "EXECUTABLE":
      return "可执行";
    case "NEEDS_SOURCE_MATERIAL":
      return "需补资料";
    case "MISSING_LOCAL_CAPABILITY":
      return "缺少本地能力";
    case "INSUFFICIENT_ESTIMATED_RESOURCES":
      return "资源预计不足";
    case "LICENSE_SCOPE_UNCONFIRMED":
      return "授权范围待确认";
    default:
      return category;
  }
}
