/**
 * 资料与解说稿 (script): sources, claim ledger, chapters and narration segments.
 *
 * Facts, original explanations, transitions and deliberate fiction are labelled
 * individually; a source link always resolves to a stored span.  Freezing a
 * script is an explicit action and it is blocked while a core claim conflicts.
 */

import { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useSearchParams } from "react-router-dom";
import {
  createExplainerScriptRevision,
  freezeExplainerScript,
  getExplainerClaimEvidence,
  importExplainerSource,
  patchExplainerClaim,
  patchExplainerSegment,
  startExplainerResearchRun,
} from "../../generated/api";
import { stableIdempotencyKey } from "../../services/commandId";
import { queryKeys } from "../../query/queryKeys";
import { InlineError, InlineOk, Panel, SettingRow, StateNotice, type PageState } from "./components";
import { CLAIM_STATUS_LABELS, STATEMENT_TYPE_LABELS } from "./viewModels";
import { useExplainerScript } from "./useExplainerQueries";
import "./explainers.css";

export function ExplainerScriptPage() {
  const { projectId = "" } = useParams();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedSegmentId = searchParams.get("segment");
  const [file, setFile] = useState<File | null>(null);
  const [pasted, setPasted] = useState("");
  const [referenceUrls, setReferenceUrls] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // One draft per segment, keyed by project + script revision + canonical segment.
  // The editor used to hold a single ``editing`` object, so clicking "修改这一段"
  // on another paragraph replaced it and the unsaved text was gone — and the save
  // callback cleared the editor unconditionally, which could discard a paragraph
  // the operator started editing *while* the save was in flight.
  const [drafts, setDrafts] = useState<Record<string, { display: string; spoken: string }>>({});
  const [editingId, setEditingId] = useState<string | null>(null);

  const script = useExplainerScript(projectId);
  const revisionId = script.data?.revision ? String((script.data.revision as Record<string, unknown>).id) : null;
  const segments = script.data?.segments ?? [];
  const claims = script.data?.claims ?? [];
  const claimByCode = useMemo(() => {
    const map = new Map<string, Record<string, unknown>>();
    for (const claim of claims) map.set(String(claim.code), claim);
    return map;
  }, [claims]);

  const selected = segments.find((segment) => segment.id === selectedSegmentId) ?? segments[0] ?? null;
  // FE-A11: the paragraph's "事实 Cxxx" button only wrote ``?claim=`` into the URL;
  // nothing read it, so a conflicting fact could not be inspected or corrected and
  // the "查看证据 → 修正/排除 → 再冻结" loop was impossible from the page.
  const selectedClaimCode = searchParams.get("claim");
  const selectedClaim = useMemo(
    () => (selectedClaimCode ? claimByCode.get(selectedClaimCode) ?? null : null),
    [claimByCode, selectedClaimCode],
  );
  const [claimNote, setClaimNote] = useState("");
  const [claimFeedback, setClaimFeedback] = useState<string | null>(null);
  const [claimError, setClaimError] = useState<string | null>(null);

  const correctClaim = useMutation({
    mutationFn: async ({ status, importance }: { status?: string; importance?: string }) => {
      if (!selectedClaim) throw new Error("请先从段落或账本中选择一条事实");
      if (!claimNote.trim()) throw new Error("修正事实状态必须填写理由");
      const payload: Record<string, unknown> = {
        expected_revision: Number(selectedClaim.revision ?? 1),
        note: claimNote.trim(),
        confidence_reason: claimNote.trim(),
      };
      if (status) payload.status = status;
      if (importance) payload.importance = importance;
      return patchExplainerClaim(projectId, String(selectedClaim.id), payload);
    },
    onSuccess: async () => {
      setClaimError(null);
      setClaimNote("");
      setClaimFeedback("事实状态已更新；只有受该事实影响的讲稿与下游产物会失效，其它内容保持有效。");
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      setClaimFeedback(null);
      setClaimError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const draftKeyFor = useCallback(
    (segment: Record<string, unknown>) =>
      `${projectId}:${String(revisionId ?? "")}:${String(segment.canonical_segment_id ?? segment.id)}`,
    [projectId, revisionId],
  );
  const draftFor = useCallback(
    (segment: Record<string, unknown>) => drafts[draftKeyFor(segment)],
    [draftKeyFor, drafts],
  );
  const editing = useMemo(() => {
    const segment = segments.find((item) => String(item.id) === editingId);
    if (!segment) return null;
    const draft = drafts[draftKeyFor(segment)];
    if (!draft) return null;
    return {
      id: String(segment.id),
      canonicalSegmentId: String(segment.canonical_segment_id ?? ""),
      key: draftKeyFor(segment),
      display: draft.display,
      spoken: draft.spoken,
      revision: Number(segment.revision ?? 1),
      draftScriptRevisionId: revisionId,
    };
  }, [draftKeyFor, drafts, editingId, revisionId, segments]);
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
            },
          },
    );
    setEditingId(String(segment.id));
  };
  const updateDraft = (patch: { display?: string; spoken?: string }) => {
    if (!editingId) return;
    const segment = segments.find((item) => String(item.id) === editingId);
    if (!segment) return;
    // The key is derived here, from the values of *this* render.  A key captured
    // inside a memoised updater can be stale (for example before the revision id
    // resolves) and then the edit is written under a draft nobody displays.
    const key = draftKeyFor(segment);
    setDrafts((current) => ({
      ...current,
      [key]: {
        display: patch.display ?? current[key]?.display ?? "",
        spoken: patch.spoken ?? current[key]?.spoken ?? "",
      },
    }));
  };
  const closeEditing = () => setEditingId(null);

  const importSource = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("请选择要上传的资料文件");
      return importExplainerSource(projectId, file, { title: file.name });
    },
    onSuccess: async (result) => {
      setError(null);
      setFeedback(`已导入来源“${String((result.source as Record<string, unknown>).title ?? file?.name ?? "")}”。导入只表示编码与哈希已保存，不代表事实已核验。`);
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => setError(mutationError instanceof Error ? mutationError.message : String(mutationError)),
  });

  const research = useMutation({
    mutationFn: async () => {
      const urls = referenceUrls.split(/\s+/).filter(Boolean);
      if (urls.length === 0) throw new Error("请填写参考链接");
      return startExplainerResearchRun(
        projectId,
        { mode: "WEB_RESEARCH", reference_urls: urls, max_external_requests: urls.length },
        stableIdempotencyKey("explainer-research", { projectId, urls }),
      );
    },
    onSuccess: async () => {
      setError(null);
      setFeedback("已登记参考链接抓取。资料获取可以联网，但模型推理仍然保持纯本地。");
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => setError(mutationError instanceof Error ? mutationError.message : String(mutationError)),
  });

  const createScript = useMutation({
    mutationFn: async () => {
      if (!pasted.trim()) throw new Error("请粘贴讲解稿内容");
      const blocks = pasted
        .split(/\n{2,}/)
        .map((block) => block.trim())
        .filter(Boolean);
      return createExplainerScriptRevision(projectId, {
        locale: "zh-CN",
        title: "粘贴讲解稿",
        status: "DRAFT",
        segments: blocks.map((block, index) => ({
          canonical_segment_id: `seg_${String(index + 1).padStart(3, "0")}`,
          display_text: block,
          spoken_text: block,
          statement_type: "ORIGINAL_EXPLANATION",
          claim_codes: [],
          pronunciation_map: [],
        })),
      });
    },
    onSuccess: async () => {
      setError(null);
      setFeedback("已创建新的讲稿版本。旧版本不可变，可回看。");
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => setError(mutationError instanceof Error ? mutationError.message : String(mutationError)),
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

  const saveSegment = useMutation({
    mutationFn: async (submitted: { id: string; key: string; display: string; spoken: string; revision: number }) => {
      // The payload is frozen by the caller: the callbacks below act on this
      // snapshot, never on "whatever the editor holds when the response arrives".
      return patchExplainerSegment(projectId, submitted.id, {
        expected_revision: submitted.revision,
        expected_script_revision_id: revisionId,
        display_text: submitted.display,
        spoken_text: submitted.spoken,
        allow_locked: false,
      });
    },
    onSuccess: async (result, submitted) => {
      setError(null);
      const invalidated = Array.isArray((result as Record<string, unknown>).invalidated)
        ? ((result as Record<string, unknown>).invalidated as unknown[]).length
        : 0;
      setFeedback(`已保存新讲稿版本；受影响下游 ${invalidated} 项被标记过期。`);
      // Only the submitted segment's draft is retired.  Other paragraphs keep their
      // drafts, and a paragraph started during the save keeps its own text.
      setDrafts((current) => {
        const next = { ...current };
        delete next[submitted.key];
        return next;
      });
      setEditingId((current) => (current === submitted.id ? null : current));
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      // A 409 keeps the local text: the user must be able to re-apply it rather
      // than have the server version silently win.
      const message = mutationError instanceof Error ? mutationError.message : String(mutationError);
      setFeedback(null);
      setError(`${message}（本地未保存文本已保留，可重新核对后再次保存）`);
    },
  });

  const state = useMemo<PageState | null>(() => {
    if (script.isPending) return { kind: "loading", message: "正在载入资料与解说稿…" };
    if (script.isError) {
      return { kind: "failed", title: "无法载入讲稿", body: script.error instanceof Error ? script.error.message : "未知错误" };
    }
    if (!revisionId) {
      return {
        kind: "empty",
        title: "还没有讲稿版本",
        body: "可以先导入资料，或直接粘贴一份讲解稿建立第一个版本。",
      };
    }
    if (script.data && script.data.sources.length === 0 && claims.length === 0) {
      return {
        kind: "partial",
        title: "尚未建立事实账本",
        body: "讲稿存在，但还没有来源与事实记录；未核验的断言不会显示为“事实核验完成”。",
      };
    }
    return null;
  }, [claims.length, revisionId, script.data, script.error, script.isError, script.isPending]);

  return <div className="explainer-page">
    <div className="explainer-grid wide-main">
      <div className="explainer-stack">
        <Panel title="章节" subtitle={script.data?.revision ? `讲稿 ${String((script.data.revision as Record<string, unknown>).status ?? "")}` : "尚未创建"}>
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
          ) : <p className="muted">尚未建立章节纲要。</p>}
          <div className="explainer-actions" style={{ marginTop: 12 }}>
            <button type="button" disabled={!revisionId || freeze.isPending} onClick={() => freeze.mutate()}>冻结讲稿</button>
          </div>
        </Panel>

        <Panel title="来源" subtitle="每项事实都可追溯到来源原句。">
          <div className="explainer-form-grid">
            <label className="explainer-field">
              上传资料
              <input type="file" accept=".txt,.md,.markdown,.docx,.pdf,.epub,.json,.csv,.srt,.vtt" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
            </label>
            <div className="explainer-field">
              <span>操作</span>
              <button type="button" disabled={importSource.isPending} onClick={() => importSource.mutate()}>
                {importSource.isPending ? "正在导入…" : "导入资料"}
              </button>
            </div>
            <label className="explainer-field full">
              参考链接（联网研究，每行一个）
              <textarea value={referenceUrls} onChange={(event) => setReferenceUrls(event.target.value)} placeholder="https://example.com/official-case" />
            </label>
            <div className="explainer-field">
              <span>受控抓取</span>
              <button type="button" disabled={research.isPending} onClick={() => research.mutate()}>
                {research.isPending ? "正在登记…" : "抓取参考链接"}
              </button>
            </div>
          </div>
          <InlineOk message={feedback} />
          <InlineError message={error} />
          <p className="explainer-note">
            资料获取可以联网，但不会解锁模型出口；抓取到的网页文本始终是不可信资料，不能作为系统指令。
          </p>
        </Panel>

        <Panel title="粘贴讲解稿" subtitle="建立第一个讲稿版本（原文不可变）。">
          <label className="explainer-field">
            讲解稿正文（空行分段）
            <textarea value={pasted} onChange={(event) => setPasted(event.target.value)} placeholder="每段之间用一个空行分隔" />
          </label>
          <div className="explainer-actions" style={{ marginTop: 10 }}>
            <button type="button" className="primary-action" disabled={createScript.isPending} onClick={() => createScript.mutate()}>
              {createScript.isPending ? "正在创建…" : "保存为讲稿版本"}
            </button>
          </div>
          <p className="explainer-note">
            <code>display_text</code> 保留规范写法（如 1962），<code>spoken_text</code> 是朗读形式（如 一九六二年），两者通过发音映射保持等价。
          </p>
        </Panel>

        <Panel title="资料与时间线" subtitle="选中段落的依据">
          {script.data?.sources && script.data.sources.length > 0 ? (
            script.data.sources.map((source) => (
              <div className="explainer-segment" key={String(source.id)}>
                <strong>{String(source.title ?? "未命名来源")}</strong>
                <p className="muted">
                  发布日期：{String(source.published_at ?? "未知")} · 获取时间：{String(source.fetched_at ?? "—")}
                </p>
                <p className="muted">内容哈希 {String(source.body_sha256 ?? "").slice(0, 16)}…</p>
              </div>
            ))
          ) : <p className="muted">尚未导入来源。上传完成不会显示“事实已核验”。</p>}
        </Panel>
      </div>

      <Panel title="讲解稿" subtitle={revisionId ? `版本 ${revisionId.slice(0, 8)}… · 中文为主语言` : "尚未创建"}>
        <StateNotice state={state} />
        {segments.length > 0 ? (
          <div>
            {segments.map((segment) => {
              const isSelected = selected?.id === segment.id;
              return (
                <article className={`explainer-segment${isSelected ? " selected" : ""}`} key={segment.id} id={`segment-${segment.id}`}>
                  <div className="explainer-segment-top">
                    <button
                      type="button"
                      className="explainer-source-link"
                      onClick={() => setSearchParams((params) => {
                        params.set("segment", segment.id);
                        return params;
                      })}
                    >
                      段落 {String(segment.ordinal + 1).padStart(3, "0")}
                    </button>
                    <span className="badge">{STATEMENT_TYPE_LABELS[segment.statement_type] ?? segment.statement_type}</span>
                    {segment.content_locked_by_human ? <span className="badge warn">人工锁定</span> : null}
                    <small>{segment.target_duration_ms ? `计划 ${Math.round(segment.target_duration_ms / 100) / 10} 秒` : "时长待实测"}</small>
                  </div>
                  {editing?.id === segment.id ? (
                    <>
                      <label className="explainer-field">
                        显示文本
                        <textarea value={editing.display} onChange={(event) => updateDraft({ display: event.target.value })} />
                      </label>
                      <label className="explainer-field">
                        朗读文本
                        <textarea value={editing.spoken} onChange={(event) => updateDraft({ spoken: event.target.value })} />
                      </label>
                      <div className="explainer-actions" style={{ marginTop: 8 }}>
                        <button
                          type="button"
                          className="primary-action"
                          disabled={saveSegment.isPending}
                          onClick={() => {
                            // Read the live draft at click time rather than a value
                            // captured when the button was rendered.
                            const live = drafts[draftKeyFor(segment)];
                            saveSegment.mutate({
                              id: String(segment.id),
                              key: draftKeyFor(segment),
                              display: live?.display ?? String(segment.display_text ?? ""),
                              spoken: live?.spoken ?? String(segment.spoken_text ?? ""),
                              revision: Number(segment.revision ?? 1),
                            });
                          }}
                        >
                          保存为新版本
                        </button>
                        <button type="button" onClick={closeEditing}>取消</button>
                      </div>
                    </>
                  ) : (
                    <>
                      <p>{segment.display_text}</p>
                      {segment.spoken_text !== segment.display_text ? <p className="spoken">朗读：{segment.spoken_text}</p> : null}
                      <div className="explainer-actions" style={{ marginTop: 8 }}>
                        <button
                          type="button"
                          onClick={() => editSegment(segment)}
                        >
                          修改这一段
                        </button>
                        {/* Switching paragraphs keeps the other draft; the marker says
                            so instead of letting the text silently disappear. */}
                        {draftFor(segment) ? (
                          <span className="badge warn">
                            有未保存修改{drafts[draftKeyFor(segment)]?.display !== segment.display_text ? "" : "（内容与原文相同）"}
                          </span>
                        ) : null}
                      </div>
                    </>
                  )}
                  {segment.claim_ids_json.length > 0 ? (
                    <div style={{ marginTop: 8 }}>
                      {segment.claim_ids_json.map((code) => {
                        const claim = claimByCode.get(code);
                        return (
                          <button type="button" className="explainer-source-link" key={code} onClick={() => setSearchParams((params) => {
                            params.set("claim", code);
                            return params;
                          })}>
                            事实 {code} · {claim ? (CLAIM_STATUS_LABELS[String(claim.status)] ?? String(claim.status)) : "引用缺失"}
                          </button>
                        );
                      })}
                    </div>
                  ) : (
                    <p className="muted" style={{ marginTop: 8 }}>本段没有引用事实（说明是原创解释或过渡语）。</p>
                  )}
                </article>
              );
            })}
          </div>
        ) : null}
      </Panel>

      <div className="explainer-stack">
        <Panel title="事实账本" subtitle="支持 / 冲突 / 未核验分别记录。">
          {claims.length === 0 ? <p className="muted">尚未提取事实。</p> : (
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
          )}
          <p className="explainer-note">多家转载同一稿件不算多份独立证据；来源数量不决定真伪，置信分只用于排序。</p>
        </Panel>

        <Panel title="选中段落依据">
          {selected ? (
            <>
              <SettingRow label="段落" value={`${selected.ordinal + 1}`} />
              <SettingRow label="类型" value={STATEMENT_TYPE_LABELS[selected.statement_type] ?? selected.statement_type} />
              <SettingRow label="锁定" value={selected.content_locked_by_human ? "人工锁定" : "未锁定"} />
              <SettingRow label="发音映射" value={`${selected.pronunciation_map_json.length} 条`} />
            </>
          ) : <p className="muted">选择一个段落查看依据。</p>}
        </Panel>

        {/* FE-A11: the deep-linked fact's own evidence and correction form. */}
        {selectedClaimCode && (
          <Panel
            title={`事实 ${selectedClaimCode}`}
            subtitle={selectedClaim ? "证据与修正" : "引用缺失"}
            actions={<button type="button" className="pipeline-button quiet" onClick={() => setSearchParams((params) => { params.delete("claim"); return params; })}>关闭</button>}
          >
            {selectedClaim ? (
              <>
                <SettingRow label="状态" value={CLAIM_STATUS_LABELS[String(selectedClaim.status)] ?? String(selectedClaim.status)} />
                <SettingRow label="重要度" value={String(selectedClaim.importance ?? "—")} />
                <SettingRow label="修订" value={String(selectedClaim.revision ?? "—")} />
                <p className="explainer-note">{String(selectedClaim.statement ?? "（这条事实没有保存陈述文本）")}</p>
                <SettingRow label="独立来源数" value={String(selectedClaim.independent_source_count ?? "—")} />
                <label className="explainer-field full">
                  处理理由（必填）
                  <textarea
                    aria-label="处理理由"
                    value={claimNote}
                    onChange={(event) => setClaimNote(event.target.value)}
                    placeholder="例如：来源仅有一家转载，无法独立核实，改为未核验。"
                  />
                </label>
                <div className="explainer-actions">
                  {(["SUPPORTED", "DISPUTED", "UNVERIFIED", "EXCLUDED"] as const).map((status) => (
                    <button
                      key={status}
                      type="button"
                      disabled={correctClaim.isPending || !claimNote.trim()}
                      onClick={() => correctClaim.mutate({ status })}
                    >
                      标记为 {CLAIM_STATUS_LABELS[status] ?? status}
                    </button>
                  ))}
                </div>
                <InlineError message={claimError} />
                <InlineOk message={claimFeedback} />
                <ClaimEvidence projectId={projectId} claimId={String(selectedClaim.id)} />
              </>
            ) : (
              <p className="muted">该段落引用了不存在的事实代码；请修正段落引用或重新提取事实。</p>
            )}
          </Panel>
        )}
      </div>
    </div>
  </div>;
}

/**
 * FE-A11: the saved evidence windows for one fact.
 *
 * The ledger used to show only a code, a status and an importance, so a conflicting
 * fact gave the operator nothing to read.  This fetches the frozen spans and shows
 * each saved quote with its source and offsets, which is what turns "事实 C003" into
 * an inspectable citation.  A fact with no span is reported as unsupported rather
 * than as a load failure.
 */
function ClaimEvidence({ projectId, claimId }: { projectId: string; claimId: string }) {
  const evidence = useQuery({
    queryKey: ["explainer-claim-evidence", projectId, claimId],
    queryFn: () => getExplainerClaimEvidence(projectId, claimId),
    retry: false,
  });
  if (evidence.isPending) return <p className="muted">正在读取证据片段…</p>;
  if (evidence.isError) {
    return (
      <div className="explainer-inline-error" role="alert">
        <p>证据读取失败：{evidence.error instanceof Error ? evidence.error.message : "未知错误"}。这不表示该事实没有证据。</p>
        <button type="button" className="pipeline-button quiet" onClick={() => { void evidence.refetch(); }}>重新读取证据</button>
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
          {span.source_url ? (
            <a href={String(span.source_url)} target="_blank" rel="noreferrer noopener">打开来源</a>
          ) : null}
        </blockquote>
      ))}
    </div>
  );
}

