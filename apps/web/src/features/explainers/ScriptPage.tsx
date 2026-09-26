/**
 * 第 1 步：内容与讲稿 (script) — design §B2, §B9, §B12, §C1, §C4.1, §F3.
 *
 * Three product rules this page exists to keep:
 *
 * 1. **The input channel and the processing policy are orthogonal** (§C1).  The
 *    first screen asks “你现在有什么” with exactly two primary cards —
 *    已有口播稿 / 故事资料 — and 从主题开始 as a secondary entry.  The choice
 *    maps to `script_policy`; 粘贴 / 上传 / 链接 is only *how* the text arrived,
 *    so a `.txt` file never decides the policy by its extension.
 * 2. **PRESERVE_ORIGINAL never calls the AI** (§C4.1, §B2.3).  The original text is
 *    registered verbatim through the ordinary script-revision authority; the
 *    page never exposes a target-duration control for a finished script, so a
 *    3-minute setting cannot silently rewrite or truncate it.
 * 3. **One body editor** (§B2.3).  The canonical text is the only main editor;
 *    a paragraph's spoken text and pronunciation dictionary appear only after
 *    “发音修正”, and the fact sources open in a drawer.
 *
 * The shared content form (`ContentIntakeForm`) lives here and is reused by
 * `/explainers/new`, which is what makes “新建” and “已有作品” the same first
 * screen instead of two different paste forms (§B2.1).
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  breakdownExplainerStory,
  createExplainerScriptRevision,
  freezeExplainerScript,
  getExplainerClaimEvidence,
  importExplainerSource,
  patchExplainerSegment,
  preflightExplainerPlan,
  startExplainerResearchRun,
  startExplainerRun,
} from "../../generated/api";
import { routes } from "../../app/routeRegistry";
import { Drawer } from "../../components/ui/primitives";
import { queryKeys } from "../../query/queryKeys";
import { stableIdempotencyKey } from "../../services/commandId";
import { draftRegistry, type DraftDiscardResult, type DraftHandle, type DraftSaveResult } from "../drafts/draftRegistry";
import { CapabilityPicker, useCapabilityOptions } from "../model-config/CapabilityPicker";
import { InlineError, InlineOk, Panel, SettingRow, StateNotice, type PageState } from "./components";
import { useExplainerActionBar } from "./ExplainerStepActionBar";
import {
  CLAIM_STATUS_LABELS,
  STATEMENT_TYPE_LABELS,
  resolveOutputsForExplainer,
} from "./viewModels";
import { useExplainerOverview, useExplainerScript, useExplainerSegments } from "./useExplainerQueries";
import "./explainers.css";
import "./explainers.pages.css";

/* -------------------------------------------------------------------------- */
/* §C1 — input channel × processing policy                                     */
/* -------------------------------------------------------------------------- */

export type ScriptPolicy = "PRESERVE_ORIGINAL" | "ADAPT_SOURCES" | "CREATE_FROM_TOPIC";
export type ContentInputKind = "PASTED_SCRIPT" | "DOCUMENT_IMPORT" | "REFERENCE_LINKS" | "TOPIC";

export const SCRIPT_POLICY_LABELS: Record<ScriptPolicy, string> = {
  PRESERVE_ORIGINAL: "已有口播稿",
  ADAPT_SOURCES: "故事资料",
  CREATE_FROM_TOPIC: "从主题开始",
};

export const SCRIPT_POLICY_NOTES: Record<ScriptPolicy, string> = {
  PRESERVE_ORIGINAL: "原样制作：只分段、发音规范化与配画面规划，不主动改写、不扩写、不因时长改字。",
  ADAPT_SOURCES: "依据资料改写口播稿；事实与实体先提取，再按证据写稿。",
  CREATE_FROM_TOPIC: "输入题目与补充要求，先准备资料（资料不足时明确说明）再生成讲稿。",
};

export const INPUT_KIND_LABELS: Record<ContentInputKind, string> = {
  PASTED_SCRIPT: "粘贴正文",
  DOCUMENT_IMPORT: "上传文件",
  REFERENCE_LINKS: "参考链接",
  TOPIC: "题目",
};

/** Server-side CJK narration hint (`text_planner.CHINESE_CHARS_PER_SECOND_HINT`). */
export const NARRATION_CHARS_PER_SECOND_HINT = 4.9;

/** Channels each policy really accepts (§B2.1). */
export function channelsForPolicy(policy: ScriptPolicy): ContentInputKind[] {
  if (policy === "PRESERVE_ORIGINAL") return ["PASTED_SCRIPT", "DOCUMENT_IMPORT"];
  if (policy === "ADAPT_SOURCES") return ["PASTED_SCRIPT", "DOCUMENT_IMPORT", "REFERENCE_LINKS"];
  return ["TOPIC"];
}

export function defaultChannelForPolicy(policy: ScriptPolicy): ContentInputKind {
  return channelsForPolicy(policy)[0];
}

/** The stored policy of an existing work, or `null` when the read model has none. */
export function storedScriptPolicy(video: Record<string, unknown> | null | undefined): ScriptPolicy | null {
  if (!video) return null;
  const payload = video.input_payload_json;
  const record = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
  const value = record.script_policy ?? video.script_policy;
  return value === "PRESERVE_ORIGINAL" || value === "ADAPT_SOURCES" || value === "CREATE_FROM_TOPIC" ? value : null;
}

/* -------------------------------------------------------------------------- */
/* deterministic text helpers                                                  */
/* -------------------------------------------------------------------------- */

/** Count of characters that will actually be spoken (whitespace excluded). */
export function narrationCharacterCount(text: string): number {
  return text.replace(/\s+/g, "").length;
}

/**
 * Estimated narration seconds from the server's own documented hint rate.
 * It is always rendered with “预计”; the measured TTS duration replaces it.
 */
export function estimateNarrationSeconds(characters: number): number {
  return Math.max(0, Math.round(characters / NARRATION_CHARS_PER_SECOND_HINT));
}

export function formatDurationLabel(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return rest === 0 ? `${minutes} 分钟` : `${minutes} 分 ${rest} 秒`;
}

/**
 * Deterministic program-side split for a preserved original (§C4.1).
 * Paragraphs first, then sentence boundaries — no character is dropped,
 * reordered or rewritten, and the same input always yields the same split.
 */
export function splitPreservedSegments(text: string, maximumBlockLength = 400): Array<{ canonical_segment_id: string; display_text: string }> {
  const paragraphs = text
    .split(/\n{2,}/)
    .map((block) => block.replace(/^\n+|\n+$/g, ""))
    .filter((block) => block.trim().length > 0);
  const blocks: string[] = [];
  for (const paragraph of paragraphs) {
    if (paragraph.length <= maximumBlockLength) {
      blocks.push(paragraph);
      continue;
    }
    let current = "";
    for (const piece of paragraph.split(/(?<=[。！？；!?;])/)) {
      if (current && (current + piece).length > maximumBlockLength) {
        blocks.push(current);
        current = piece;
      } else {
        current += piece;
      }
    }
    if (current) blocks.push(current);
  }
  return blocks.map((block, index) => ({
    canonical_segment_id: `seg_${String(index + 1).padStart(3, "0")}`,
    display_text: block,
  }));
}

/* -------------------------------------------------------------------------- */
/* shared content intake form (§B2.1)                                          */
/* -------------------------------------------------------------------------- */

export type ContentIntakeValue = {
  policy: ScriptPolicy | null;
  inputKind: ContentInputKind;
  title: string;
  /** The 正文 body: a preserved manuscript or the story material. */
  body: string;
  topic: string;
  referenceUrls: string;
  /** Kept separate from the body on purpose (§B2.2 “额外要求”). */
  extraRequirements: string;
  file: File | null;
};

export const EMPTY_CONTENT_INTAKE: ContentIntakeValue = {
  policy: null,
  inputKind: "PASTED_SCRIPT",
  title: "",
  body: "",
  topic: "",
  referenceUrls: "",
  extraRequirements: "",
  file: null,
};

export function fileNameToTitle(name: string): string {
  return name.replace(/\.[^.]+$/, "").trim();
}

/**
 * Pasted material is registered through the *same* bounded upload path as a
 * chosen file, so there is one reader, one encoding decision and one hash rule
 * (spec §C2).  Nothing here decodes bytes in the browser.
 */
export function pastedMaterialFile(text: string, policy: ScriptPolicy | null): File | null {
  if (narrationCharacterCount(text) === 0) return null;
  const name = policy === "PRESERVE_ORIGINAL" ? "口播稿原文.txt" : "资料正文.txt";
  return new File([text], name, { type: "text/plain" });
}

export type ContentIntakeFormProps = {
  value: ContentIntakeValue;
  onChange: (patch: Partial<ContentIntakeValue>) => void;
  idPrefix?: string;
  /** Existing work: the stored policy is a fact, so the other cards explain instead of pretending to switch. */
  policyLockedReason?: string | null;
  /** “上传文稿 / 上传资料” (§B2.3). */
  upload?: { onClick: () => void; pending: boolean; disabledReason?: string | null; label?: string } | null;
  /** “AI 生成讲稿”, only for 资料/题目 (§B2.3). */
  generate?: { onClick: () => void; pending: boolean; disabledReason?: string | null } | null;
  showTitle?: boolean;
  titleExtras?: ReactNode;
  footer?: ReactNode;
  feedback?: string | null;
  error?: string | null;
};

export function ContentIntakeForm({
  value,
  onChange,
  idPrefix = "intake",
  policyLockedReason = null,
  upload = null,
  generate = null,
  showTitle = true,
  titleExtras = null,
  footer = null,
  feedback = null,
  error = null,
}: ContentIntakeFormProps) {
  const bodyId = `${idPrefix}-body`;
  const topicId = `${idPrefix}-topic`;
  const titleId = `${idPrefix}-title`;
  const fileId = `${idPrefix}-file`;
  const extraId = `${idPrefix}-extra`;
  const urlsId = `${idPrefix}-urls`;
  const channels = value.policy ? channelsForPolicy(value.policy) : [];
  const bodyLabel = value.policy === "PRESERVE_ORIGINAL" ? "口播稿正文（原样保留）" : "资料正文";
  const uploadLabel = value.policy === "PRESERVE_ORIGINAL" ? "上传文稿" : "上传资料";

  return <section className="explainer-intake" aria-label="你现在有什么">
    <div>
      <h3>你现在有什么</h3>
      <p className="muted">
        文件类型不决定稿件用途：同一份 TXT 既可能是已经定稿的口播稿，也可能是待整理的资料。
        先选内容用途，再选它是怎么进来的。
      </p>
    </div>

    <div className="explainer-intake__cards" role="group" aria-label="内容用途">
      <button
        type="button"
        className="explainer-intake__card"
        aria-pressed={value.policy === "PRESERVE_ORIGINAL"}
        onClick={() => onChange({ policy: "PRESERVE_ORIGINAL", inputKind: defaultChannelForPolicy("PRESERVE_ORIGINAL") })}
      >
        <strong>已有口播稿</strong>
        <small>script_policy = PRESERVE_ORIGINAL</small>
        <small>{SCRIPT_POLICY_NOTES.PRESERVE_ORIGINAL}</small>
      </button>
      <button
        type="button"
        className="explainer-intake__card"
        aria-pressed={value.policy === "ADAPT_SOURCES"}
        onClick={() => onChange({ policy: "ADAPT_SOURCES", inputKind: defaultChannelForPolicy("ADAPT_SOURCES") })}
      >
        <strong>故事资料</strong>
        <small>script_policy = ADAPT_SOURCES</small>
        <small>{SCRIPT_POLICY_NOTES.ADAPT_SOURCES}</small>
      </button>
    </div>

    <div className="explainer-intake__secondary">
      <button type="button" onClick={() => onChange({ policy: "CREATE_FROM_TOPIC", inputKind: "TOPIC" })}>
        从主题开始（次入口）
      </button>
      <span className="muted">{SCRIPT_POLICY_NOTES.CREATE_FROM_TOPIC}</span>
    </div>

    {policyLockedReason ? <p className="explainer-note warn" role="status">{policyLockedReason}</p> : null}

    {value.policy ? (
      <>
        <div className="explainer-intake__channels" role="group" aria-label="输入方式">
          {channels.map((channel) => (
            <button
              key={channel}
              type="button"
              className="explainer-intake__channel"
              aria-pressed={value.inputKind === channel}
              onClick={() => onChange({ inputKind: channel })}
            >
              {INPUT_KIND_LABELS[channel]}
            </button>
          ))}
          <span className="muted">当前策略：{SCRIPT_POLICY_LABELS[value.policy]}（{value.policy}）</span>
        </div>

        {showTitle ? (
          <div className="explainer-intake__body">
            <label htmlFor={titleId}>作品标题</label>
            <input
              id={titleId}
              value={value.title}
              placeholder="例如：灯塔最后一页值班记录"
              onChange={(event) => onChange({ title: event.target.value })}
            />
            <span className="muted">{titleExtras ?? "有文件时用文件名预填，可修改；机器编号由服务端自动生成。"}</span>
          </div>
        ) : null}

        {value.inputKind === "PASTED_SCRIPT" ? (
          <div className="explainer-intake__body">
            <div className="explainer-intake__title-row">
              <label htmlFor={bodyId}>{bodyLabel}</label>
              <span>
                <small>空行分段；{narrationCharacterCount(value.body)} 字</small>
                {upload ? (
                  <button
                    type="button"
                    disabled={upload.pending || Boolean(upload.disabledReason)}
                    title={upload.disabledReason ?? undefined}
                    onClick={upload.onClick}
                  >
                    {upload.pending ? "正在上传…" : (upload.label ?? uploadLabel)}
                  </button>
                ) : null}
              </span>
            </div>
            <textarea
              id={bodyId}
              aria-label={value.policy === "PRESERVE_ORIGINAL" ? "口播稿正文" : "资料正文"}
              value={value.body}
              placeholder={value.policy === "PRESERVE_ORIGINAL" ? "粘贴已经定稿的口播稿正文" : "粘贴故事或资料正文"}
              onChange={(event) => onChange({ body: event.target.value })}
            />
          </div>
        ) : null}

        {value.inputKind === "DOCUMENT_IMPORT" ? (
          <div className="explainer-intake__body">
            <div className="explainer-intake__title-row">
              <label htmlFor={fileId}>{value.policy === "PRESERVE_ORIGINAL" ? "文稿文件" : "资料文件"}</label>
              <small>{value.file ? `已选择：${value.file.name}` : "支持 TXT / MD / DOCX / PDF / EPUB"}</small>
            </div>
            <input
              id={fileId}
              type="file"
              aria-label={value.policy === "PRESERVE_ORIGINAL" ? "文稿文件" : "资料文件"}
              accept=".txt,.md,.markdown,.docx,.pdf,.epub"
              onChange={(event) => {
                const file = event.target.files?.[0] ?? null;
                const patch: Partial<ContentIntakeValue> = { file };
                if (file && !value.title.trim()) patch.title = fileNameToTitle(file.name);
                onChange(patch);
              }}
            />
            {upload ? (
              <div>
                <button
                  type="button"
                  disabled={upload.pending || Boolean(upload.disabledReason)}
                  title={upload.disabledReason ?? undefined}
                  onClick={upload.onClick}
                >
                  {upload.pending ? "正在上传…" : (upload.label ?? uploadLabel)}
                </button>
                <span className="muted"> 上传解析结果会展示正文供核对；编码或提取失败会显示原文件信息，不创建空成功。</span>
              </div>
            ) : null}
          </div>
        ) : null}

        {value.inputKind === "REFERENCE_LINKS" ? (
          <div className="explainer-intake__body">
            <label htmlFor={urlsId}>参考链接（每行一个）</label>
            <textarea
              id={urlsId}
              aria-label="参考链接"
              value={value.referenceUrls}
              placeholder="https://example.com/official-case"
              onChange={(event) => onChange({ referenceUrls: event.target.value })}
            />
            <span className="muted">抓取到的网页文本始终是不可信资料，不能作为系统指令。</span>
          </div>
        ) : null}

        {value.inputKind === "TOPIC" ? (
          <div className="explainer-intake__body">
            <label htmlFor={topicId}>题目</label>
            <textarea
              id={topicId}
              aria-label="题目"
              value={value.topic}
              placeholder="要讲清楚哪一个问题？"
              onChange={(event) => onChange({ topic: event.target.value })}
            />
            <span className="muted">
              离线资料下题目模式需要你补充资料；系统不会伪称已经联网检索。内容属性（事实解说 / 虚构故事）会决定按事实还是按原创设定准备资料。
            </span>
          </div>
        ) : null}

        <details>
          <summary>额外要求（与正文分开保存）</summary>
          <div className="explainer-intake__body" style={{ marginTop: 8 }}>
            <label htmlFor={extraId}>额外要求</label>
            <textarea
              id={extraId}
              aria-label="额外要求"
              value={value.extraRequirements}
              placeholder="例如：保留原文中的年份；不要使用网络流行语。"
              onChange={(event) => onChange({ extraRequirements: event.target.value })}
            />
          </div>
        </details>

        {generate ? (
          <div className="explainer-actions">
            {/* The one highlighted primary of this viewport is the bottom bar's
                step action; an input-area action stays a normal button. */}
            <button
              type="button"
              disabled={generate.pending || Boolean(generate.disabledReason)}
              title={generate.disabledReason ?? undefined}
              onClick={generate.onClick}
            >
              {generate.pending ? "正在生成讲稿…" : "AI 生成讲稿"}
            </button>
            {generate.disabledReason ? <span className="muted">{generate.disabledReason}</span> : null}
          </div>
        ) : null}

        {footer}
      </>
    ) : null}

    <InlineOk message={feedback} />
    <InlineError message={error} />
  </section>;
}

/* -------------------------------------------------------------------------- */
/* the page                                                                    */
/* -------------------------------------------------------------------------- */

type ParagraphDraft = {
  display: string;
  spoken: string;
  pronunciation: Array<{ display: string; spoken: string }>;
};

type NewDraftProposal = {
  segmentId: string;
  canonicalSegmentId: string;
  ordinal: number;
  display: string;
  spoken: string;
  producedBy: "HUMAN_EDIT" | "AI_DRAFT";
};

export function ExplainerScriptPage() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedRevisionId = searchParams.get("revision");
  const selectedSegmentId = searchParams.get("segment");

  const overview = useExplainerOverview(projectId);
  const script = useExplainerScript(projectId, requestedRevisionId);
  const segmentsQuery = useExplainerSegments(projectId, requestedRevisionId);
  const textOptions = useCapabilityOptions("LLM_STORY_PARSE", { projectId });
  const video = (overview.data?.video ?? null) as Record<string, unknown> | null;

  const revision = script.data?.revision ?? null;
  const revisionId = revision ? String(revision.id) : null;
  const revisionNo = revision ? Number(revision.revision_no ?? 1) : null;
  const revisionStatus = revision ? String(revision.status ?? "") : "";
  const currentRevisionId = overview.data ? String(video?.current_script_revision_id ?? "") : null;
  // A historical revision is read-only only once we know which revision is
  // current; before that the page must not claim the view is historical.
  const readOnly = Boolean(requestedRevisionId && revisionId && currentRevisionId !== null && requestedRevisionId !== currentRevisionId);
  const segments = useMemo(() => script.data?.segments ?? [], [script.data?.segments]);
  const claims = script.data?.claims ?? [];
  const sources = script.data?.sources ?? [];
  const claimByCode = useMemo(() => {
    const map = new Map<string, Record<string, unknown>>();
    for (const claim of claims) map.set(String(claim.code), claim);
    return map;
  }, [claims]);

  const automationMode = String(video?.automation_mode ?? "AUTO_WITH_EXCEPTIONS");
  const oneClickMode = automationMode === "AUTO_WITH_EXCEPTIONS";
  const storedPolicy = storedScriptPolicy(video);

  /* ------------------------------------------------------------- intake ---- */
  const [intake, setIntake] = useState<ContentIntakeValue>(EMPTY_CONTENT_INTAKE);
  const patchIntake = useCallback((patch: Partial<ContentIntakeValue>) => {
    setIntake((current) => ({ ...current, ...patch }));
  }, []);

  // The stored policy of an existing work is a fact.  It pre-selects the card
  // but the user is told where the value comes from instead of the page
  // pretending a click rewrote it.
  useEffect(() => {
    if (!storedPolicy) return;
    setIntake((current) => (current.policy === storedPolicy ? current : {
      ...current,
      policy: storedPolicy,
      inputKind: channelsForPolicy(storedPolicy).includes(current.inputKind)
        ? current.inputKind
        : defaultChannelForPolicy(storedPolicy),
    }));
  }, [storedPolicy]);

  const intakeBody = intake.inputKind === "TOPIC" ? intake.topic : intake.body;
  const intakeCharacters = narrationCharacterCount(intakeBody);

  /* ---------------------------------------------------- paragraph drafts ---- */
  const [drafts, setDrafts] = useState<Record<string, ParagraphDraft>>({});
  const [editingId, setEditingId] = useState<string | null>(null);
  const [pronunciationOpenId, setPronunciationOpenId] = useState<string | null>(null);
  const [proposal, setProposal] = useState<NewDraftProposal | null>(null);
  const [sourcesSegmentId, setSourcesSegmentId] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const draftHandlesRef = useRef(new Map<string, DraftHandle>());
  const draftVersionsRef = useRef(new Map<string, number>());
  const draftsRef = useRef(drafts);
  draftsRef.current = drafts;

  const draftKeyFor = useCallback(
    (segment: Record<string, unknown>) =>
      `${projectId}:${String(revisionId ?? "")}:${String(segment.canonical_segment_id ?? segment.id)}`,
    [projectId, revisionId],
  );

  const selected = segments.find((segment) => segment.id === selectedSegmentId) ?? segments[0] ?? null;

  const editSegment = (segment: Record<string, unknown>) => {
    const key = draftKeyFor(segment);
    setDrafts((current) =>
      current[key]
        ? current
        : {
          ...current,
          [key]: {
            display: String(segment.display_text ?? ""),
            spoken: String(segment.spoken_text ?? ""),
            pronunciation: ((segment.pronunciation_map_json as Array<{ display: string; spoken: string }> | undefined) ?? []).map((item) => ({ ...item })),
          },
        },
    );
    setEditingId(String(segment.id));
    setSearchParams((params) => {
      const next = new URLSearchParams(params);
      next.set("segment", String(segment.id));
      return next;
    }, { replace: true });
  };

  const updateDraft = (patch: Partial<ParagraphDraft>) => {
    if (!editingId) return;
    const segment = segments.find((item) => String(item.id) === editingId);
    if (!segment) return;
    const key = draftKeyFor(segment);
    const version = (draftVersionsRef.current.get(key) ?? 0) + 1;
    draftVersionsRef.current.set(key, version);
    setDrafts((current) => ({
      ...current,
      [key]: {
        display: patch.display ?? current[key]?.display ?? "",
        spoken: patch.spoken ?? current[key]?.spoken ?? "",
        pronunciation: patch.pronunciation ?? current[key]?.pronunciation ?? [],
      },
    }));
  };

  const closeEditing = () => {
    setEditingId(null);
    setPronunciationOpenId(null);
  };

  const patchSegment = useMutation({
    mutationFn: async (submitted: { id: string; key: string; display: string; spoken: string; pronunciation: Array<{ display: string; spoken: string }>; revision: number }) =>
      patchExplainerSegment(projectId, submitted.id, {
        expected_revision: submitted.revision,
        expected_script_revision_id: revisionId,
        display_text: submitted.display,
        spoken_text: submitted.spoken,
        pronunciation_map: submitted.pronunciation,
        allow_locked: false,
      }),
    onSuccess: async (result, submitted) => {
      setError(null);
      const invalidated = Array.isArray((result as Record<string, unknown>).invalidated)
        ? ((result as Record<string, unknown>).invalidated as unknown[]).length
        : 0;
      setFeedback(`已保存新讲稿版本；受影响下游 ${invalidated} 项被标记过期，旧版本仍可回看。`);
      setDrafts((current) => {
        const next = { ...current };
        delete next[submitted.key];
        return next;
      });
      draftVersionsRef.current.delete(submitted.key);
      setEditingId((current) => (current === submitted.id ? null : current));
      setPronunciationOpenId(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      // §B12.7: a 409 keeps the local text so the user can re-apply it; the
      // server version must not silently win.
      const message = mutationError instanceof Error ? mutationError.message : String(mutationError);
      setFeedback(null);
      setError(`${message}（本地未保存文本已保留，请核对当前版本后再次保存）`);
    },
  });

  /** “保存草稿” saves every paragraph that really has unsaved text, oldest first. */
  const saveAllDrafts = useMutation({
    mutationFn: async (keys: string[]) => {
      let saved = 0;
      for (const key of keys) {
        const segment = segments.find((item) => draftKeyFor(item) === key);
        const draft = draftsRef.current[key];
        if (!segment || !draft || !revisionId) continue;
        await patchExplainerSegment(projectId, String(segment.id), {
          expected_revision: Number(segment.revision ?? 1),
          expected_script_revision_id: revisionId,
          display_text: draft.display,
          spoken_text: draft.spoken,
          pronunciation_map: draft.pronunciation,
          allow_locked: false,
        });
        saved += 1;
        setDrafts((current) => {
          const next = { ...current };
          delete next[key];
          return next;
        });
      }
      return saved;
    },
    onSuccess: async (saved) => {
      setError(null);
      setEditingId(null);
      setPronunciationOpenId(null);
      setFeedback(saved > 0 ? `已保存 ${saved} 段的修改；旧版本保留可回看。` : "没有需要保存的段落修改。");
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      // §B12.7: keep the local text on a conflict so it can be re-applied.
      const message = mutationError instanceof Error ? mutationError.message : String(mutationError);
      setFeedback(null);
      setError(`${message}（本地未保存文本已保留，请核对当前版本后再次保存）`);
    },
  });

  const saveDraftRef = useRef<(key: string, expectedVersion: number) => Promise<DraftSaveResult>>(async () => ({ status: "blocked", reason: "讲稿草稿尚未就绪。" }));
  const discardDraftRef = useRef<(key: string, expectedVersion: number) => Promise<DraftDiscardResult>>(async () => ({ status: "blocked", reason: "讲稿草稿尚未就绪。" }));

  /** Keys whose local text really differs from the served revision (not merely “a draft exists”). */
  const dirtyDraftKeys = useMemo(() => segments
    .filter((segment) => {
      const draft = drafts[draftKeyFor(segment)];
      if (!draft) return false;
      return draft.display !== String(segment.display_text ?? "")
        || draft.spoken !== String(segment.spoken_text ?? "")
        || JSON.stringify(draft.pronunciation) !== JSON.stringify(segment.pronunciation_map_json ?? []);
    })
    .map((segment) => draftKeyFor(segment)), [draftKeyFor, drafts, segments]);

  saveDraftRef.current = async (key, expectedVersion) => {
    const segment = segments.find((item) => draftKeyFor(item) === key);
    const draft = draftsRef.current[key];
    if (!segment || !draft) return { status: "blocked", reason: "该段草稿已不存在。" };
    if (!revisionId) return { status: "blocked", reason: "还没有可保存的讲稿版本。" };
    try {
      await patchExplainerSegment(projectId, String(segment.id), {
        expected_revision: Number(segment.revision ?? 1),
        expected_script_revision_id: revisionId,
        display_text: draft.display,
        spoken_text: draft.spoken,
        pronunciation_map: draft.pronunciation,
        allow_locked: false,
      });
    } catch (failure) {
      return { status: "blocked", reason: failure instanceof Error ? failure.message : String(failure) };
    }
    setDrafts((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
    setEditingId((current) => (current === String(segment.id) ? null : current));
    await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    const handle = draftHandlesRef.current.get(key);
    if (handle) {
      draftRegistry.update(handle, { version: expectedVersion, dirty: false });
      draftRegistry.unregister(handle);
      draftHandlesRef.current.delete(key);
    }
    return { status: "saved", savedVersion: expectedVersion };
  };

  discardDraftRef.current = async (key, expectedVersion) => {
    setDrafts((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
    const handle = draftHandlesRef.current.get(key);
    if (handle) {
      draftRegistry.update(handle, { version: expectedVersion, dirty: false });
      draftRegistry.unregister(handle);
      draftHandlesRef.current.delete(key);
    }
    return { status: "discarded", discardedVersion: expectedVersion };
  };

  // A work that has no revision yet holds its whole manuscript in this form, so
  // that body is a draft as well and is registered the same way (§B12.1).
  const intakeHandleRef = useRef<DraftHandle | null>(null);
  const intakeSaveRef = useRef<(expectedVersion: number) => Promise<DraftSaveResult>>(async () => ({ status: "blocked", reason: "讲稿正文草稿尚未就绪。" }));
  const intakeDiscardRef = useRef<(expectedVersion: number) => Promise<DraftDiscardResult>>(async () => ({ status: "blocked", reason: "讲稿正文草稿尚未就绪。" }));

  // One draft owner per unsaved paragraph, so AppShell's leave protection really
  // fires and a conflict never silently drops the text (§B12.1, F3).
  useEffect(() => {
    const live = draftHandlesRef.current;
    const wanted = new Set<string>();
    for (const segment of segments) {
      const key = draftKeyFor(segment);
      const draft = drafts[key];
      if (!draft) continue;
      const dirty =
        draft.display !== String(segment.display_text ?? "") ||
        draft.spoken !== String(segment.spoken_text ?? "");
      if (!dirty) continue;
      wanted.add(key);
      const version = draftVersionsRef.current.get(key) ?? 1;
      const entityKey = `第 ${Number(segment.ordinal ?? 0) + 1} 段正文`;
      const existing = live.get(key);
      if (existing) {
        draftRegistry.update(existing, { version, dirty: true, entityKey });
        continue;
      }
      const handle = draftRegistry.register({
        ownerId: `explainer-script-segment:${projectId}:${String(segment.id)}`,
        entityKey,
        version,
        dirty: true,
        save: (expectedVersion: number) => saveDraftRef.current(key, expectedVersion),
        discard: (expectedVersion: number) => discardDraftRef.current(key, expectedVersion),
      });
      live.set(key, handle);
    }
    for (const [key, handle] of Array.from(live.entries())) {
      if (wanted.has(key)) continue;
      draftRegistry.unregister(handle);
      live.delete(key);
    }
  }, [draftKeyFor, drafts, projectId, segments]);

  useEffect(() => () => {
    for (const handle of Array.from(draftHandlesRef.current.values())) {
      draftRegistry.unregister(handle);
    }
    draftHandlesRef.current.clear();
  }, []);

  /* ------------------------------------------------------- real mutations -- */
  const importSource = useMutation({
    mutationFn: async (file: File) => importExplainerSource(projectId, file, { title: file.name }),
    onSuccess: async (result, file) => {
      const record = result as Record<string, unknown>;
      const extraction = (record.extraction ?? {}) as Record<string, unknown>;
      const preserved = record.preserved_script as Record<string, unknown> | null | undefined;
      setError(null);
      setFeedback(
        `已导入“${String((record.source as Record<string, unknown> | undefined)?.title ?? file.name)}”：` +
        `格式 ${String(extraction.format ?? "未知")}、编码 ${String(extraction.encoding ?? "未知")}（置信 ${String(extraction.confidence ?? "未提供")}）、` +
        `${Number(extraction.character_count ?? 0)} 字。导入只表示文本已登记哈希，不代表事实已核验。` +
        (preserved ? " 原稿保留模式下该文稿已登记为讲稿版本。" : ""),
      );
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const registerPreserved = useMutation({
    mutationFn: async () => {
      const text = intake.body;
      if (!narrationCharacterCount(text)) throw new Error("请先粘贴口播稿正文");
      const blocks = splitPreservedSegments(text);
      if (blocks.length === 0) throw new Error("正文没有可用段落");
      return createExplainerScriptRevision(projectId, {
        locale: "zh-CN",
        title: intake.title.trim() || "口播稿原文",
        status: "DRAFT",
        segments: blocks.map((block) => ({
          canonical_segment_id: block.canonical_segment_id,
          display_text: block.display_text,
          spoken_text: block.display_text,
          statement_type: "FACT",
          claim_codes: [],
          pronunciation_map: [],
        })),
      });
    },
    onSuccess: async () => {
      setError(null);
      setFeedback("已按原稿登记讲稿版本：只做分段与发音规范化，没有调用改写模型；旧版本仍可回看。");
      patchIntake({ body: "" });
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const generateScript = useMutation({
    mutationFn: async () => {
      if (narrationCharacterCount(intakeBody) === 0) throw new Error("请输入资料或题目");
      return breakdownExplainerStory(projectId, {
        story_text: intakeBody,
        profile_version_id: null,
        target_seconds: Number(video?.target_seconds ?? 180),
        style: null,
        title: intake.title.trim() || String(video?.title ?? "") || null,
      });
    },
    onSuccess: async (result) => {
      setError(null);
      setFeedback(
        `已生成新讲稿草稿：模型（${result.model_used}）输出 ${result.segment_count} 段。` +
        "草稿不会自动覆盖已采用的定稿，请核对后在“新稿比较区”采用。",
      );
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const adoptProposal = useMutation({
    mutationFn: async (submitted: NewDraftProposal) => {
      const nextSegments = segments.map((segment) => {
        const isTarget = String(segment.id) === submitted.segmentId;
        return {
          canonical_segment_id: String(segment.canonical_segment_id),
          display_text: isTarget ? submitted.display : String(segment.display_text ?? ""),
          spoken_text: isTarget ? submitted.spoken : String(segment.spoken_text ?? ""),
          statement_type: String(segment.statement_type ?? "FACT"),
          claim_codes: segment.claim_ids_json ?? [],
          pronunciation_map: (segment.pronunciation_map_json ?? []) as Array<{ display: string; spoken: string }>,
          content_locked_by_human: Boolean(segment.content_locked_by_human),
        };
      });
      return createExplainerScriptRevision(projectId, {
        locale: String((video?.source_locale as string) ?? "zh-CN"),
        title: `基于 v${revisionNo ?? 1} 的修订`,
        status: "DRAFT",
        source_script_revision_id: revisionId,
        segments: nextSegments,
      });
    },
    onSuccess: async (_result, submitted) => {
      setError(null);
      setProposal(null);
      setFeedback(
        `已采用新稿：第 ${submitted.ordinal + 1} 段更新为新版本；旧稿保留可回看。` +
        "受影响的配音与镜头已标记过期，只更新相关项。",
      );
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const freeze = useMutation({
    mutationFn: async () => {
      if (!revisionId) throw new Error("尚无可冻结的讲稿版本");
      return freezeExplainerScript(projectId, revisionId);
    },
    onSuccess: async () => {
      setError(null);
      setFeedback("讲稿已冻结：后续修改会创建新版本，不会覆盖已锁定内容。");
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => setError(mutationError instanceof Error ? mutationError.message : String(mutationError)),
  });

  /**
   * “一键生成到预览” on step 1 (§B2.3): create and adopt the script, then submit
   * the *same* pipeline the header uses.  `PRESERVE_ORIGINAL` performs no AI
   * rewrite call at all — the original is registered verbatim.
   */
  const oneClick = useMutation({
    mutationFn: async () => {
      if (!revisionId) {
        if (storedPolicy === null || storedPolicy === "PRESERVE_ORIGINAL") {
          if (!narrationCharacterCount(intake.body)) {
            throw new Error("已有口播稿模式需要先粘贴或上传文稿");
          }
          const blocks = splitPreservedSegments(intake.body);
          await createExplainerScriptRevision(projectId, {
            locale: "zh-CN",
            title: intake.title.trim() || "口播稿原文",
            status: "DRAFT",
            segments: blocks.map((block) => ({
              canonical_segment_id: block.canonical_segment_id,
              display_text: block.display_text,
              spoken_text: block.display_text,
              statement_type: "FACT",
              claim_codes: [],
              pronunciation_map: [],
            })),
          });
          if (intake.file) await importExplainerSource(projectId, intake.file, { title: intake.file.name });
        } else {
          // Pasted material goes through the same bounded import path as a file,
          // so the pipeline reads a real registered source instead of a value the
          // client only holds in memory.
          const material = intake.file ?? pastedMaterialFile(intakeBody, storedPolicy);
          if (!material) throw new Error("请先粘贴或上传资料");
          await importExplainerSource(projectId, material, { title: material.name });
          if (intake.inputKind === "REFERENCE_LINKS") {
            const urls = intake.referenceUrls.split(/\s+/).filter(Boolean);
            if (urls.length > 0) {
              await startExplainerResearchRun(
                projectId,
                { mode: "WEB_RESEARCH", reference_urls: urls, max_external_requests: urls.length },
                stableIdempotencyKey("explainer-research", { projectId, urls }),
              );
            }
          }
        }
      }
      const outputs = resolveOutputsForExplainer(
        overview.data?.editions,
        video?.source_locale as string | undefined,
        video?.aspect_ratio as string | undefined,
      );
      const report = await preflightExplainerPlan(projectId, { outputs });
      if (!report.executable) {
        const first = report.blockers[0];
        throw new Error(`预检未通过：${first ? `${first.message}（${first.next_step || first.code}）` : "存在阻塞项"}`);
      }
      return startExplainerRun(
        projectId,
        { plan_hash: report.plan_hash, outputs, start_workflow: true },
        stableIdempotencyKey("explainer-run", { projectId, planHash: report.plan_hash }),
      );
    },
    onSuccess: async (started) => {
      setError(null);
      setFeedback(`已提交同一条流水线：运行 ${started.id}（${started.projected_status}）。脚本已采用，后续步骤以它为准。`);
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const saveAndNext = useMutation({
    mutationFn: async () => {
      if (Object.keys(drafts).length > 0) throw new Error("还有未保存的段落修改，请先保存草稿");
      if (!revisionId) throw new Error("还没有讲稿版本：请先登记正文或生成讲稿");
      return true;
    },
    onSuccess: () => {
      setError(null);
      navigate(routes.explainerPage(projectId, "assets"));
    },
    onError: (mutationError) => setError(mutationError instanceof Error ? mutationError.message : String(mutationError)),
  });

  const intakeDirty = !revisionId
    && (narrationCharacterCount(intake.body) > 0
      || narrationCharacterCount(intake.topic) > 0
      || narrationCharacterCount(intake.referenceUrls) > 0
      || Boolean(intake.file));

  intakeSaveRef.current = async (expectedVersion) => {
    try {
      if (storedPolicy === "PRESERVE_ORIGINAL" || storedPolicy === null) await registerPreserved.mutateAsync();
      else await generateScript.mutateAsync();
    } catch (failure) {
      return { status: "blocked", reason: failure instanceof Error ? failure.message : String(failure) };
    }
    const handle = intakeHandleRef.current;
    if (handle) {
      draftRegistry.update(handle, { version: expectedVersion, dirty: false });
      draftRegistry.unregister(handle);
      intakeHandleRef.current = null;
    }
    return { status: "saved", savedVersion: expectedVersion };
  };

  intakeDiscardRef.current = async (expectedVersion) => {
    patchIntake({ body: "", topic: "", referenceUrls: "", file: null });
    const handle = intakeHandleRef.current;
    if (handle) {
      draftRegistry.update(handle, { version: expectedVersion, dirty: false });
      draftRegistry.unregister(handle);
      intakeHandleRef.current = null;
    }
    return { status: "discarded", discardedVersion: expectedVersion };
  };

  useEffect(() => {
    if (!intakeDirty) {
      const handle = intakeHandleRef.current;
      if (handle) {
        draftRegistry.unregister(handle);
        intakeHandleRef.current = null;
      }
      return;
    }
    if (intakeHandleRef.current) {
      draftRegistry.update(intakeHandleRef.current, { dirty: true });
      return;
    }
    intakeHandleRef.current = draftRegistry.register({
      ownerId: `explainer-script-intake:${projectId}`,
      entityKey: "讲稿正文",
      version: 1,
      dirty: true,
      save: (expectedVersion: number) => intakeSaveRef.current(expectedVersion),
      discard: (expectedVersion: number) => intakeDiscardRef.current(expectedVersion),
    });
  }, [intakeDirty, projectId]);

  /* --------------------------------------------------------------- state --- */
  const scriptText = segments.map((segment) => String(segment.display_text ?? "")).join("\n");
  const characters = narrationCharacterCount(scriptText);
  const measuredSeconds = segmentsQuery.data?.measured_total_ms ? Number(segmentsQuery.data.measured_total_ms) / 1000 : null;
  const estimatedSeconds = estimateNarrationSeconds(characters);
  const durationText = measuredSeconds !== null
    ? `实测 ${formatDurationLabel(measuredSeconds)}`
    : `预计 ${formatDurationLabel(estimatedSeconds)}`;
  const adoptedTakeCount = segmentsQuery.data?.selected_takes?.length ?? 0;
  const beatCount = Number(overview.data?.beat_count ?? 0);

  const state = useMemo<PageState | null>(() => {
    // §B12.4: a failed refresh keeps the last known payload and says so; only a
    // first load without data is an error empty state.
    if (script.isPending && !script.data) return { kind: "loading", message: "正在载入内容与讲稿…" };
    if (script.isError && !script.data) {
      return {
        kind: "failed",
        title: "无法载入讲稿",
        body: script.error instanceof Error ? script.error.message : "未知错误",
        action: <button type="button" onClick={() => { void script.refetch(); }}>重新读取</button>,
      };
    }
    if (!revisionId) {
      return {
        kind: "empty",
        title: "还没有讲稿版本",
        body: "先选内容用途：已有口播稿会原样登记，故事资料或题目会先生成讲稿草稿。",
      };
    }
    if (segments.length === 0) {
      return { kind: "empty", title: "该版本没有段落", body: "讲稿版本存在但没有段落内容，请重新登记或生成。" };
    }
    return null;
  }, [revisionId, script.data, script.error, script.isError, script.isPending, segments.length]);

  const capabilityReady = textOptions.data?.selection.ready === true || (textOptions.data?.summary.selectable_count ?? 0) > 0;
  const extraCapabilityHref = textOptions.data?.repair_href || routes.systemCapabilities(projectId);

  /* --------------------------------------------------------- action bar ---- */
  const hasContent = narrationCharacterCount(intakeBody) > 0 || Boolean(intake.file);
  const primaryLabel = oneClickMode ? "一键生成到预览" : "保存并下一步：人物与风格";
  const primaryBusy = oneClickMode ? oneClick.isPending : saveAndNext.isPending;
  const primaryDisabled = revisionId ? false : (oneClickMode ? !hasContent : true);
  const primaryDisabledReason = primaryDisabled
    ? (oneClickMode
      ? "还没有内容：请先粘贴 / 上传正文，或先登记讲稿版本。"
      : "还没有讲稿版本：请先登记或生成讲稿，再进入下一步。")
    : null;

  useExplainerActionBar({
    primary: {
      label: primaryLabel,
      onClick: () => (oneClickMode ? oneClick.mutate() : saveAndNext.mutate()),
      disabled: primaryDisabled,
      disabledReason: primaryDisabledReason,
      busy: primaryBusy,
    },
    save: dirtyDraftKeys.length > 0
      ? {
        label: "保存草稿",
        busy: saveAllDrafts.isPending || patchSegment.isPending,
        onClick: () => saveAllDrafts.mutate(dirtyDraftKeys),
      }
      : intakeDirty
        ? {
          label: "保存草稿",
          busy: registerPreserved.isPending || generateScript.isPending,
          onClick: () => { void intakeSaveRef.current(1); },
        }
        : null,
    summary: revisionId
      ? `当前稿 v${revisionNo ?? 1} · ${characters} 字 · ${measuredSeconds !== null ? "实测" : "预计"} ${formatDurationLabel(measuredSeconds ?? estimatedSeconds)}${dirtyDraftKeys.length > 0 ? ` · ${dirtyDraftKeys.length} 段有未保存修改` : ""}`
      : "尚未登记讲稿",
  });

  /* -------------------------------------------------------------- render --- */
  const sourcesSegment = sourcesSegmentId ? segments.find((segment) => segment.id === sourcesSegmentId) ?? null : null;
  const proposalSegment = proposal ? segments.find((segment) => String(segment.id) === proposal.segmentId) ?? null : null;

  return <div className="explainer-page">
    {script.isError && script.data ? (
      <p className="explainer-inline-error" role="alert">
        更新失败：{script.error instanceof Error ? script.error.message : "未知错误"}。以下是上一次成功读取的内容，不代表当前状态。
        <button type="button" onClick={() => { void script.refetch(); }}>重新读取</button>
      </p>
    ) : null}
    <StateNotice state={state} />

    {revisionId && readOnly ? (
      <p className="explainer-note warn" role="status">
        正在只读查看历史版本 v{revisionNo ?? "?"}（{String(video?.current_script_revision_id ?? "") === requestedRevisionId ? "当前稿" : `当前稿为 v${String(overview.data?.video?.current_script_revision_id ?? "")}`}）。
        采用历史版本是明确操作，不会因为查看而替换当前稿。
        <button type="button" onClick={() => setSearchParams((params) => { const next = new URLSearchParams(params); next.delete("revision"); return next; }, { replace: true })}>返回当前稿</button>
      </p>
    ) : null}

    <div className="explainer-script-layout">
      {/* ---------------------------------------------------- left: directory */}
      <aside className="explainer-script__toc" aria-label="段落与章节目录">
        <Panel title="段落 / 章节" subtitle="点段落只切换当前编辑对象，不改变采用结果。">
          {script.data?.chapters && script.data.chapters.length > 0 ? (
            <div className="explainer-list">
              {script.data.chapters.map((chapter) => (
                <div className="explainer-list-item" key={String(chapter.id)}>
                  <span>{String(chapter.ordinal ?? "")}</span>
                  <span>
                    {String(chapter.title ?? "")}
                    <small>{String(chapter.audience_question ?? "")}</small>
                  </span>
                </div>
              ))}
            </div>
          ) : null}
          {segments.length > 0 ? (
            <div className="explainer-list explainer-paragraph-list" style={{ marginTop: 8 }}>
              {segments.map((segment) => {
                const key = draftKeyFor(segment);
                const draft = drafts[key];
                const dirty = Boolean(draft) && (draft.display !== String(segment.display_text ?? "") || draft.spoken !== String(segment.spoken_text ?? ""));
                const isCurrent = selected?.id === segment.id;
                return (
                  <button
                    type="button"
                    key={segment.id}
                    className={`explainer-list-item${isCurrent ? " selected" : ""}`}
                    aria-current={isCurrent ? "true" : undefined}
                    onClick={() => {
                      setEditingId(String(segment.id));
                      setPronunciationOpenId(null);
                      setSearchParams((params) => {
                        const next = new URLSearchParams(params);
                        next.set("segment", String(segment.id));
                        return next;
                      }, { replace: true });
                    }}
                  >
                    <span>{String(Number(segment.ordinal ?? 0) + 1).padStart(3, "0")}</span>
                    <span>
                      {String(segment.display_text ?? "").slice(0, 18)}
                      <small>
                        {STATEMENT_TYPE_LABELS[String(segment.statement_type)] ?? String(segment.statement_type)}
                        {segment.claim_ids_json.length > 0 ? ` · 事实 ${segment.claim_ids_json.length}` : ""}
                        {segment.content_locked_by_human ? " · 人工锁定" : ""}
                      </small>
                      {dirty ? <small className="explainer-impact-note">有未保存修改</small> : null}
                    </span>
                  </button>
                );
              })}
            </div>
          ) : <p className="muted">还没有段落。</p>}
        </Panel>
      </aside>

      {/* ------------------------------------------------- centre: one editor */}
      <section className="explainer-script__editor" aria-label="讲稿正文">
        {revisionId ? (
          <div className="explainer-script__toolbar">
            <strong>当前稿 v{revisionNo ?? 1} · 约 {characters} 字 · {durationText}</strong>
            <span className="muted">
              {measuredSeconds !== null
                ? "时长来自已采用的配音实测；"
                : `按服务端提示速率 ${NARRATION_CHARS_PER_SECOND_HINT} 字/秒估算，配音后更新为实测。`}
              {String(revisionStatus) === "FROZEN" ? "已冻结。" : "仍是草稿。"}
            </span>
            <span className="explainer-script__toolbar-actions">
              {!readOnly ? (
                <button type="button" disabled={freeze.isPending} onClick={() => freeze.mutate()}>
                  {freeze.isPending ? "正在冻结…" : "冻结讲稿"}
                </button>
              ) : null}
              <button type="button" onClick={() => setHistoryOpen(true)}>历史版本</button>
            </span>
          </div>
        ) : null}

        {!revisionId ? (
          <Panel title="内容与讲稿" subtitle="选内容用途 → 录入正文；已有口播稿不会被改写。">
            <ContentIntakeForm
              value={intake}
              onChange={patchIntake}
              idPrefix="script"
              policyLockedReason={storedPolicy ? `该作品的内容用途在创建时已冻结为「${SCRIPT_POLICY_LABELS[storedPolicy]}」（${storedPolicy}）；改变用途请新建作品。` : null}
              upload={{
                onClick: () => {
                  const material = intake.file ?? pastedMaterialFile(intake.body, storedPolicy);
                  if (material) importSource.mutate(material);
                },
                pending: importSource.isPending,
                disabledReason: intake.file || narrationCharacterCount(intake.body) > 0
                  ? null
                  : "请先粘贴正文或选择要上传的文稿文件",
              }}
              generate={storedPolicy === "PRESERVE_ORIGINAL" ? null : {
                onClick: () => generateScript.mutate(),
                pending: generateScript.isPending,
                disabledReason: capabilityReady ? null : "本机没有可执行的写稿文本能力，请先在能力与模型中补齐。",
              }}
              feedback={feedback}
              error={error}
              footer={
                <div className="explainer-actions">
                  {storedPolicy === "PRESERVE_ORIGINAL" || storedPolicy === null ? (
                    <button
                      type="button"
                      disabled={registerPreserved.isPending || narrationCharacterCount(intake.body) === 0}
                      onClick={() => registerPreserved.mutate()}
                    >
                      {registerPreserved.isPending ? "正在登记…" : "保存为讲稿版本（不调用改写）"}
                    </button>
                  ) : null}
                  <Link to={extraCapabilityHref}>前往能力与模型</Link>
                </div>
              }
            />
          </Panel>
        ) : null}

        {revisionId && selected ? (
          <Panel
            title={`段落 ${String(Number(selected.ordinal ?? 0) + 1).padStart(3, "0")}`}
            subtitle="正文是唯一主编辑区；发音与来源分别按需展开。"
            actions={<span className="badge">{STATEMENT_TYPE_LABELS[String(selected.statement_type)] ?? String(selected.statement_type)}</span>}
          >
            {!readOnly && editingId === String(selected.id) ? (
              <div className="explainer-paragraph-editor">
                <label className="explainer-field">
                  <span>正文（屏幕字幕与朗读的基础文本）</span>
                  <textarea
                    aria-label="显示文本"
                    value={drafts[draftKeyFor(selected)]?.display ?? String(selected.display_text ?? "")}
                    onChange={(event) => updateDraft({ display: event.target.value })}
                  />
                </label>
                <div className="explainer-paragraph-actions">
                  <button
                    type="button"
                    disabled={patchSegment.isPending}
                    onClick={() => {
                      const key = draftKeyFor(selected);
                      const live = draftsRef.current[key];
                      patchSegment.mutate({
                        id: String(selected.id),
                        key,
                        display: live?.display ?? String(selected.display_text ?? ""),
                        spoken: live?.spoken ?? String(selected.spoken_text ?? ""),
                        pronunciation: live?.pronunciation ?? [],
                        revision: Number(selected.revision ?? 1),
                      });
                    }}
                  >
                    保存为新版本
                  </button>
                  <button type="button" onClick={closeEditing}>取消</button>
                  {drafts[draftKeyFor(selected)] ? <span className="badge warn">有未保存修改</span> : null}
                </div>
              </div>
            ) : (
              <>
                <p className="explainer-paragraph-body">{String(selected.display_text ?? "")}</p>
                <div className="explainer-paragraph-actions">
                  {!readOnly ? <button type="button" onClick={() => editSegment(selected)}>编辑这一段</button> : null}
                  <button
                    type="button"
                    aria-expanded={pronunciationOpenId === String(selected.id)}
                    onClick={() => setPronunciationOpenId((current) => (current === String(selected.id) ? null : String(selected.id)))}
                  >
                    发音修正
                  </button>
                  <button type="button" onClick={() => setSourcesSegmentId(String(selected.id))}>
                    来源 {selected.claim_ids_json.length + sources.length}
                  </button>
                  {!readOnly ? (
                    <details>
                      <summary>更多</summary>
                      <div className="explainer-actions" style={{ marginTop: 8 }}>
                        <button
                          type="button"
                          disabled={!capabilityReady && false}
                          title={capabilityReady ? "打开待采用的新段落草稿" : `本机文本能力未就绪（${textOptions.data?.summary.blocked_count ?? 0} 项不可用）`}
                          onClick={() => setProposal({
                            segmentId: String(selected.id),
                            canonicalSegmentId: String(selected.canonical_segment_id),
                            ordinal: Number(selected.ordinal ?? 0),
                            display: String(selected.display_text ?? ""),
                            spoken: String(selected.spoken_text ?? ""),
                            producedBy: "HUMAN_EDIT",
                          })}
                        >
                          改写这一段
                        </button>
                      </div>
                    </details>
                  ) : null}
                </div>
              </>
            )}

            {pronunciationOpenId === String(selected.id) ? (
              <div className="explainer-spoken-block">
                <label className="explainer-field">
                  <span>朗读文本（发音修正只改这里，不改展示正文）</span>
                  <textarea
                    aria-label="朗读文本"
                    value={drafts[draftKeyFor(selected)]?.spoken ?? String(selected.spoken_text ?? "")}
                    onChange={(event) => updateDraft({ spoken: event.target.value })}
                  />
                </label>
                <div className="explainer-pronunciation-row" aria-label="发音词典">
                  <small className="muted">发音词典：</small>
                  {((drafts[draftKeyFor(selected)]?.pronunciation ?? (selected.pronunciation_map_json as Array<{ display: string; spoken: string }> | undefined) ?? [])).map((entry, index) => (
                    <span className="badge" key={`${entry.display}-${index}`}>{entry.display} ➔ {entry.spoken}</span>
                  ))}
                  {((drafts[draftKeyFor(selected)]?.pronunciation ?? selected.pronunciation_map_json ?? []).length === 0) ? (
                    <small className="muted">尚无词典条目；多音字修订只更新语音映射，不改展示正文。</small>
                  ) : null}
                </div>
                {!readOnly ? (
                  <div className="explainer-actions" style={{ marginTop: 8 }}>
                    <button
                      type="button"
                      onClick={() => {
                        const list = drafts[draftKeyFor(selected)]?.pronunciation
                          ?? ((selected.pronunciation_map_json as Array<{ display: string; spoken: string }> | undefined) ?? []).map((item) => ({ ...item }));
                        updateDraft({ pronunciation: [...list, { display: "", spoken: "" }] });
                        setEditingId(String(selected.id));
                      }}
                    >
                      添加词典条目
                    </button>
                  </div>
                ) : null}
              </div>
            ) : null}

            {selected.claim_ids_json.length > 0 ? (
              <div style={{ marginTop: 8 }}>
                {selected.claim_ids_json.map((code) => {
                  const claim = claimByCode.get(code);
                  return (
                    <button
                      type="button"
                      className="explainer-source-link"
                      key={code}
                      onClick={() => setSourcesSegmentId(String(selected.id))}
                    >
                      事实 {code} · {claim ? (CLAIM_STATUS_LABELS[String(claim.status)] ?? String(claim.status)) : "引用缺失"}
                    </button>
                  );
                })}
              </div>
            ) : null}

            {proposal && proposalSegment ? (
              <div className="explainer-newdraft" aria-label="新稿比较区">
                <strong>待采用的新段落草稿（第 {proposal.ordinal + 1} 段）</strong>
                <label className="explainer-field">
                  <span>新段落正文</span>
                  <textarea
                    aria-label="新段落正文"
                    value={proposal.display}
                    onChange={(event) => {
                      const display = event.target.value;
                      setProposal((current) => (current ? { ...current, display, spoken: current.spoken || display } : current));
                    }}
                  />
                </label>
                <label className="explainer-field">
                  <span>新段落朗读文本</span>
                  <textarea
                    aria-label="新段落朗读文本"
                    value={proposal.spoken}
                    onChange={(event) => {
                      const spoken = event.target.value;
                      setProposal((current) => (current ? { ...current, spoken } : current));
                    }}
                  />
                </label>
                <p className="muted">
                  {proposal.producedBy === "AI_DRAFT" ? "来源：模型草稿。" : "来源：人工确认的文本。"}
                  本机尚未提供“按段 AI 改写”接口（改写口播 script-draft.v2 未接通），所以这里的草稿是你确认后的文本，
                  页面不会伪称它由模型生成。原段落始终可回到原样，旧版本保留。
                </p>
                <div className="explainer-actions">
                  <button
                    type="button"
                    className="primary-action"
                    disabled={adoptProposal.isPending || !narrationCharacterCount(proposal.display)}
                    onClick={() => adoptProposal.mutate(proposal)}
                  >
                    {adoptProposal.isPending ? "正在采用…" : "采用新稿"}
                  </button>
                  <button type="button" onClick={() => setProposal(null)}>回到原文</button>
                  <span className="explainer-impact-note">
                    {adoptedTakeCount > 0 || beatCount > 0
                      ? `将更新 ${adoptedTakeCount} 段配音、${beatCount} 个镜头`
                      : "尚未生成配音与镜头，采用新稿暂不影响下游产物"}
                  </span>
                </div>
                <details>
                  <summary>受影响的配音与镜头详情</summary>
                  <ul className="muted">
                    <li>该段配音与对齐需要重做（已采用 take {adoptedTakeCount} 项）。</li>
                    <li>引用该段的画面语义与画面段 {beatCount} 个需要重新检查。</li>
                    <li>未引用的段落、人物场景素材与不受语义变化影响的画面保持不变。</li>
                  </ul>
                </details>
              </div>
            ) : null}

            <InlineOk message={feedback} />
            <InlineError message={error} />
          </Panel>
        ) : null}
      </section>

      {/* ------------------------------------------- right: folded detail ---- */}
      <aside className="explainer-script__aside" aria-label="来源与发音详情">
        <Panel title="来源与发音详情" subtitle="默认折叠，不占首屏。">
          <details>
            <summary>来源与事实（{sources.length} 个来源 · {claims.length} 条事实）</summary>
            <div style={{ marginTop: 8 }}>
              {sources.length === 0 ? (
                <p className="muted">尚未导入来源。上传完成不会显示“事实已核验”。</p>
              ) : sources.map((source) => (
                <div className="explainer-segment" key={String(source.id)}>
                  <strong>{String(source.title ?? "未命名来源")}</strong>
                  <p className="muted">发布日期：{String(source.published_at ?? "未知")} · 获取时间：{String(source.fetched_at ?? "—")}</p>
                  <p className="muted">内容哈希 {String(source.body_sha256 ?? "").slice(0, 16)}…</p>
                </div>
              ))}
              <div className="explainer-table-wrap">
                <table className="explainer-table">
                  <thead><tr><th>代码</th><th>状态</th><th>重要度</th></tr></thead>
                  <tbody>
                    {claims.map((claim) => (
                      <tr key={String(claim.id)}>
                        <td>{String(claim.code)}</td>
                        <td>{CLAIM_STATUS_LABELS[String(claim.status)] ?? String(claim.status)}</td>
                        <td>{String(claim.importance)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="explainer-note">多家转载同一稿件不算多份独立证据；未核验的断言不会显示为“事实核验完成”。</p>
            </div>
          </details>
          <details>
            <summary>发音与词典</summary>
            <div style={{ marginTop: 8 }}>
              {selected ? (
                <>
                  <SettingRow label="当前段落词典" value={`${(selected.pronunciation_map_json ?? []).length} 条`} />
                  <SettingRow label="朗读文本与正文" value={String(selected.spoken_text ?? "") === String(selected.display_text ?? "") ? "相同（默认）" : "不同（已做发音修正）"} />
                  <SettingRow label="人工锁定" value={selected.content_locked_by_human ? "已锁定" : "未锁定"} />
                </>
              ) : <p className="muted">选择段落后显示。</p>}
            </div>
          </details>
          <details>
            <summary>作品设置与内容用途</summary>
            <div style={{ marginTop: 8 }}>
              <SettingRow label="内容用途（script_policy）" value={storedPolicy ? `${SCRIPT_POLICY_LABELS[storedPolicy]}（${storedPolicy}）` : "未记录（按资料改写）"} />
              <SettingRow label="自动程度" value={automationMode} />
              <SettingRow label="目标时长" value={storedPolicy === "PRESERVE_ORIGINAL" ? "按稿件自然时长（不暴露改写用目标）" : `${Number(video?.target_seconds ?? 0)} 秒`} />
              <SettingRow label="推理策略" value={String(video?.inference_mode ?? "LOCAL_ONLY")} />
            </div>
          </details>
        </Panel>
        {!readOnly ? (
          <Panel title="文本模型" subtitle="默认项目配置，只影响本次改写与生成。">
            <CapabilityPicker
              capability="LLM_STORY_PARSE"
              label="写稿文本模型"
              description="已有定稿并未请求改写时不额外要求可用写稿模型。"
              showDetails={false}
              value=""
              onChange={() => undefined}
              query={textOptions}
            />
          </Panel>
        ) : null}
      </aside>
    </div>

    {/* ------------------------------------------------------ sources drawer */}
    <Drawer open={Boolean(sourcesSegment)} title="来源与事实" onClose={() => setSourcesSegmentId(null)}>
      <div className="explainer-workspace explainer-facts-drawer">
        <p className="muted">
          {sourcesSegment
            ? `段落 ${Number(sourcesSegment.ordinal ?? 0) + 1} 引用了 ${sourcesSegment.claim_ids_json.length} 条事实。`
            : ""}
          完整来源与事实账本在这里按需查看，不占正文旁边。
        </p>
        {sourcesSegment?.claim_ids_json?.length ? sourcesSegment.claim_ids_json.map((code) => {
          const claim = claimByCode.get(code);
          return (
            <section key={code}>
              <h3>事实 {code} · {claim ? (CLAIM_STATUS_LABELS[String(claim.status)] ?? String(claim.status)) : "引用缺失"}</h3>
              {claim ? <ClaimEvidence projectId={projectId} claimId={String(claim.id)} /> : <p className="muted">该段落引用了不存在的事实代码。</p>}
            </section>
          );
        }) : <p className="muted">该段落没有引用事实，或事实尚未提取。</p>}
        {sources.length > 0 ? (
          <section>
            <h3>全部来源</h3>
            {sources.map((source) => (
              <div className="explainer-segment" key={String(source.id)}>
                <strong>{String(source.title ?? "未命名来源")}</strong>
                <p className="muted">{String(source.body_sha256 ?? "").slice(0, 16)}…</p>
              </div>
            ))}
          </section>
        ) : null}
      </div>
    </Drawer>

    {/* ------------------------------------------------------ history drawer */}
    <Drawer open={historyOpen} title="历史版本" onClose={() => setHistoryOpen(false)}>
      <div className="explainer-workspace">
        <SettingRow label="当前稿" value={revisionId ? `v${revisionNo ?? 1}（${revisionStatus || "状态未知"}）` : "尚未创建"} />
        <SettingRow label="内容哈希" value={revision ? `${String(revision.content_hash ?? "").slice(0, 16)}…` : "—"} />
        <p className="explainer-note">
          服务端当前只提供“按 revision_id 读取单个版本”，没有版本列表接口，所以这里不伪造历史列表。
          已知版本可通过 <code>?revision=&lt;id&gt;</code> 只读打开；采用历史版本需要在当前稿上明确执行。
        </p>
        {requestedRevisionId ? null : <p className="muted">当前正在查看的就是服务端返回的当前稿。</p>}
        <div className="explainer-actions">
          <button type="button" onClick={() => { void script.refetch(); }}>重新读取当前稿</button>
        </div>
      </div>
    </Drawer>
  </div>;
}

/* -------------------------------------------------------------------------- */
/* evidence                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * The saved evidence windows for one fact.  A fact with no span is reported as
 * unsupported rather than as a load failure, and a failed read keeps its own
 * “重试读取证据” action instead of pretending there is no evidence.
 */
export function ClaimEvidence({ projectId, claimId }: { projectId: string; claimId: string }) {
  const evidence = useQuery({
    queryKey: queryKeys.explainers.claimEvidence(projectId, claimId),
    queryFn: () => getExplainerClaimEvidence(projectId, claimId),
    retry: false,
  });
  if (evidence.isPending) return <p className="muted">正在读取证据片段…</p>;
  if (evidence.isError) {
    return (
      <div className="explainer-inline-error" role="alert">
        <p>证据读取失败：{evidence.error instanceof Error ? evidence.error.message : "未知错误"}。这不表示该事实没有证据。</p>
        <button type="button" onClick={() => { void evidence.refetch(); }}>重试读取证据</button>
      </div>
    );
  }
  const spans = evidence.data?.evidence ?? [];
  if (spans.length === 0) {
    return (
      <p className="explainer-note" role="status">
        该断言目前没有登记任何来源片段（{String(evidence.data?.empty_state ?? "NO_EVIDENCE_SPAN_RECORDED")}）：没有来源的断言不能靠标签变成有证据支持。
      </p>
    );
  }
  return (
    <div className="explainer-evidence-list" aria-label="事实证据">
      <small>独立来源 {Number(evidence.data?.independent_source_count ?? 0)} 个 · 片段 {spans.length} 条</small>
      {spans.map((span) => (
        <blockquote key={span.span_id}>
          <p>{span.quote_text ? String(span.quote_text) : "（该片段没有保存可读引用文本）"}</p>
          <small>
            {String(span.source_title ?? span.source_id)}
            {span.start_offset != null ? ` · [${span.start_offset}-${span.end_offset ?? span.start_offset}]` : ""}
            {span.stance ? ` · ${String(span.stance)}` : ""}
          </small>
          {span.source_url ? <a href={String(span.source_url)} target="_blank" rel="noreferrer noopener">打开来源</a> : null}
        </blockquote>
      ))}
    </div>
  );
}
