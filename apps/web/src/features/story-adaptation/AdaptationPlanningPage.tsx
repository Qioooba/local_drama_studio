import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import { queryKeys } from "../../query/queryKeys";
import {
  createAdaptationPlan,
  listAdaptationPlans,
  listAdaptationSources,
  preflightAdaptationPlan,
  type AdaptationMode,
} from "./adaptationPlanClient";
import "./adaptation-planning.css";

const MODES: Array<{ id: AdaptationMode; title: string; description: string }> = [
  {
    id: "COMPLETE_WORK",
    title: "规划一部连续剧",
    description: "适合整本小说、多个章节或长剧本。先生成故事弧和分集规划草稿。",
  },
  {
    id: "SERIAL_INCREMENTAL",
    title: "续接已有改编规划",
    description: "适合按卷或按章节追加；后续会延续已批准的叙事状态。",
  },
  {
    id: "SINGLE_EPISODE",
    title: "精拆单集",
    description: "仅适合已经明确属于某集的短文本；不会默认选择第一集。",
  },
];

function formatDuration(milliseconds: number) {
  const seconds = Math.max(1, Math.round(milliseconds / 1000));
  return seconds % 60 === 0 ? String(seconds / 60) + " 分钟" : String(seconds) + " 秒";
}

function diagnosisLabel(type?: string) {
  return type === "NOVEL_LONG_FORM"
    ? "长篇连续小说"
    : type === "NOVEL_EXCERPT"
      ? "小说节选或连续章节"
      : "单集短文本";
}

function idempotencyKey() {
  return globalThis.crypto?.randomUUID?.() ?? "adaptation-" + Date.now().toString(36);
}

export function AdaptationPlanningPage() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [sourceVersionId, setSourceVersionId] = useState("");
  const [mode, setMode] = useState<AdaptationMode | "">("");
  const [targetDurationMs, setTargetDurationMs] = useState(120_000);
  const [message, setMessage] = useState<string | null>(null);

  const sources = useQuery({
    queryKey: queryKeys.adaptationPlanning.sources(projectId ?? ""),
    queryFn: () => listAdaptationSources(projectId!),
    enabled: Boolean(projectId),
  });
  const plans = useQuery({
    queryKey: queryKeys.adaptationPlanning.plans(projectId ?? ""),
    queryFn: () => listAdaptationPlans(projectId!),
    enabled: Boolean(projectId),
  });
  const preflight = useQuery({
    queryKey: queryKeys.adaptationPlanning.preflight(projectId ?? "", sourceVersionId, targetDurationMs),
    queryFn: () => preflightAdaptationPlan(projectId!, {
      source_document_version_id: sourceVersionId,
      target_duration_ms: targetDurationMs,
    }),
    enabled: Boolean(projectId && sourceVersionId),
  });
  const selectedSource = useMemo(
    () => sources.data?.items.find((item) => item.source_document_version_id === sourceVersionId),
    [sourceVersionId, sources.data?.items],
  );
  const createPlan = useMutation({
    mutationFn: () => createAdaptationPlan(projectId!, {
      source_document_version_id: sourceVersionId,
      mode: mode as AdaptationMode,
      target_duration_ms: targetDurationMs,
      episode_strategy: "AI_ESTIMATE",
      season_strategy: "AI_SUGGESTED",
    }, idempotencyKey()),
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.adaptationPlanning.plans(projectId ?? "") });
      setMessage(created.idempotent ? "已打开之前创建的相同规划。" : "改编规划草稿已创建；下一步会进入分层分析队列。");
      navigate(routes.adaptationPlan(projectId!, created.plan_id));
    },
  });

  if (!projectId) return <p className="inline-error" role="alert">缺少项目上下文。</p>;

  const createReason = !sourceVersionId
    ? "先选择一个已解析的原稿版本"
    : !mode
      ? "先明确这次原稿要采用的改编模式"
      : preflight.isPending
        ? "正在诊断原稿规模"
        : preflight.isError
          ? "原稿诊断未完成，请先处理错误"
          : null;

  return <main className="v2-page adaptation-planning-page">
    <header className="adaptation-planning-page__header">
      <div>
        <p className="eyebrow">故事 / 改编规划</p>
        <h2>先规划整部作品，再进入逐集生产</h2>
        <p className="muted">原稿、改编规划和真实分集彼此独立。此处只创建待审核草稿，不会覆盖季度、分集、场次或镜头。</p>
      </div>
      <Link className="secondary v2-inline-link" to={routes.story(projectId)}>返回故事工作区</Link>
    </header>

    {message && <p className="adaptation-notice" role="status">{message}</p>}

    <section className="adaptation-panel" aria-labelledby="adaptation-source-title">
      <div className="adaptation-section-heading">
        <span>1</span>
        <div><h3 id="adaptation-source-title">选择原稿版本</h3><p>选择服务端已解析的不可变原稿版本。正文范围和规划用途属于本次规划，不会修改原文件。</p></div>
      </div>
      {sources.isPending && <p className="muted" role="status">正在读取原稿库…</p>}
      {sources.isError && <p className="inline-error" role="alert">原稿库读取失败。<button type="button" className="link-button" onClick={() => void sources.refetch()}>重试</button></p>}
      {!sources.isPending && !sources.isError && (sources.data?.items.length ?? 0) === 0 && (
        <div className="adaptation-empty">
          <p>尚无已解析原稿。</p>
          <Link className="secondary v2-inline-link" to={routes.story(projectId) + "#story-import"}>导入原稿</Link>
        </div>
      )}
      <div className="adaptation-source-list" role="radiogroup" aria-label="原稿版本">
        {(sources.data?.items ?? []).map((source) => {
          const checked = source.source_document_version_id === sourceVersionId;
          return <label key={source.source_document_version_id} className={"adaptation-source-option" + (checked ? " is-selected" : "")}>
            <input type="radio" name="adaptation-source" value={source.source_document_version_id} checked={checked} onChange={() => {
              setSourceVersionId(source.source_document_version_id);
              setMessage(null);
            }} />
            <span className="adaptation-source-option__content">
              <strong>{source.title}</strong>
              <span>{source.character_count.toLocaleString()} 字符 · {source.paragraph_count} 段 · {source.chapters.length} 章 · 版本 v{source.version_no}</span>
              <small>{source.preview_truncated ? "预览已截断，全文仍由服务器索引" : "全文已解析，可用于规划"}</small>
            </span>
          </label>;
        })}
      </div>
    </section>

    <section className="adaptation-panel" aria-labelledby="adaptation-diagnosis-title">
      <div className="adaptation-section-heading">
        <span>2</span>
        <div><h3 id="adaptation-diagnosis-title">原稿诊断与改编方式</h3><p>系统给出推荐，但不会自动选中模式、更不会默认绑定第 1 季第 1 集。</p></div>
      </div>
      {preflight.isPending && <div className="adaptation-diagnosis-skeleton" role="status">正在计算章节、段落和预计集数…</div>}
      {preflight.isError && <p className="inline-error" role="alert">原稿诊断失败。<button type="button" className="link-button" onClick={() => void preflight.refetch()}>重新诊断</button></p>}
      {preflight.data && <div className="adaptation-diagnosis">
        <div>
          <span>识别内容</span>
          <strong>{diagnosisLabel(preflight.data.diagnosis.type)}</strong>
        </div>
        <div>
          <span>建议规模</span>
          <strong>约 {preflight.data.diagnosis.estimated_episode_range.minimum}–{preflight.data.diagnosis.estimated_episode_range.maximum} 集</strong>
        </div>
        <div>
          <span>单集目标</span>
          <strong>{formatDuration(targetDurationMs)}</strong>
        </div>
        <p>检测到 {preflight.data.diagnosis.character_count.toLocaleString()} 个正文字符、{preflight.data.diagnosis.body_paragraph_count} 段正文和 {preflight.data.diagnosis.chapter_count} 个章节候选；当前步骤不会生成镜头或媒体。</p>
      </div>}
      <fieldset className="adaptation-mode-fieldset">
        <legend>这份原稿准备如何处理？</legend>
        <div className="adaptation-mode-grid">
          {MODES.map((item) => {
            const recommended = preflight.data?.diagnosis.recommended_mode === item.id;
            return <label key={item.id} className={"adaptation-mode-option" + (mode === item.id ? " is-selected" : "")}>
              <input type="radio" name="adaptation-mode" value={item.id} checked={mode === item.id} onChange={() => setMode(item.id)} />
              <span><strong>{item.title}{recommended ? <em>系统推荐</em> : null}</strong><small>{item.description}</small></span>
            </label>;
          })}
        </div>
      </fieldset>
      <label className="adaptation-duration">
        <span>单集目标时长</span>
        <select value={targetDurationMs} onChange={(event) => setTargetDurationMs(Number(event.target.value))}>
          <option value={60_000}>60 秒</option>
          <option value={90_000}>90 秒</option>
          <option value={120_000}>120 秒</option>
          <option value={180_000}>180 秒</option>
          <option value={300_000}>5 分钟</option>
        </select>
      </label>
    </section>

    <section className="adaptation-panel adaptation-panel--action" aria-labelledby="adaptation-submit-title">
      <div>
        <h3 id="adaptation-submit-title">创建待审核改编规划</h3>
        <p>将冻结本次来源范围、时长和策略；季度仍只是候选分组，真实项目结构会在审核批准后再发布。</p>
      </div>
      <div className="adaptation-submit">
        <button type="button" className="primary" disabled={Boolean(createReason) || createPlan.isPending} onClick={() => createPlan.mutate()}>
          {createPlan.isPending ? "正在创建…" : "生成改编规划草稿"}
        </button>
        {createReason && <small>{createReason}</small>}
        {createPlan.isError && <p className="inline-error" role="alert">创建失败：{String(createPlan.error)}</p>}
      </div>
    </section>

    <section className="adaptation-existing" aria-labelledby="adaptation-existing-title">
      <div className="adaptation-existing__heading"><h3 id="adaptation-existing-title">已有改编规划</h3><button type="button" className="secondary" onClick={() => void plans.refetch()}>刷新</button></div>
      {plans.isPending && <p className="muted" role="status">正在读取规划…</p>}
      {plans.isError && <p className="inline-error" role="alert">规划列表读取失败。</p>}
      {!plans.isPending && !plans.isError && (plans.data?.items.length ?? 0) === 0 && <p className="muted">尚未创建改编规划。</p>}
      <div className="adaptation-existing__list">
        {(plans.data?.items ?? []).map((plan) => <Link className="adaptation-existing__item" key={plan.id} to={routes.adaptationPlan(projectId, plan.id)}>
          <span><strong>{plan.source_title}</strong><small>{plan.mode === "COMPLETE_WORK" ? "连续剧规划" : plan.mode === "SERIAL_INCREMENTAL" ? "续接规划" : "单集规划"} · 修订 v{plan.revision_no}</small></span>
          <span className="status-pill neutral">{plan.latest_run_status === "PREPARED" ? "待进入分析队列" : plan.latest_run_status}</span>
        </Link>)}
      </div>
    </section>
  </main>;
}
