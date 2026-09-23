/**
 * 声音与字幕 (audio): per-language narration takes, alignment, single-segment
 * re-read and subtitle layout preview.
 *
 * Switching language switches the clock.  Changing subtitle style must never
 * re-run TTS, and the page says so.
 */

import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams, useSearchParams } from "react-router-dom";
import { resynthesizeExplainerNarration } from "../../generated/api";
import { stableIdempotencyKey } from "../../services/commandId";
import { queryKeys } from "../../query/queryKeys";
import { InlineError, InlineOk, Panel, SettingRow, StateNotice, type PageState } from "./components";
import { NarrationPlayer } from "./media";
import { formatMs, localeLabel, subtitleModeLabel } from "./viewModels";
import { useExplainerEditions, useExplainerNarration, useExplainerSubtitles } from "./useExplainerQueries";
import "./explainers.css";

export function ExplainerAudioPage() {
  const { projectId = "" } = useParams();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const editions = useExplainerEditions(projectId);
  const editionList = (editions.data?.editions ?? []) as Array<Record<string, unknown>>;
  const [editionId, setEditionId] = useState<string | null>(null);
  const activeEdition = editionList.find((edition) => String(edition.id) === editionId) ?? editionList[0] ?? null;
  const activeEditionId = activeEdition ? String(activeEdition.id) : null;
  // The narration clock is the *edition's* voice locale and never the subtitle
  // preview language: switching the subtitle tab used to change the locale the
  // narration query ran with, so the page could show another language's takes and
  // durations while the label still said the edition's language.
  const voiceLocale = activeEdition ? String(activeEdition.voice_locale) : "zh-CN";
  const subtitleLocales = Array.isArray(activeEdition?.subtitle_locales_json)
    ? (activeEdition?.subtitle_locales_json as unknown[]).map(String)
    : [];
  const [subtitleLocaleParam, setSubtitleLocaleParam] = useState<string | null>(searchParams.get("locale"));
  const subtitleLocale = subtitleLocaleParam && subtitleLocales.includes(subtitleLocaleParam)
    ? subtitleLocaleParam
    : subtitleLocales[0] ?? null;
  const narration = useExplainerNarration(activeEditionId, voiceLocale);
  const subtitles = useExplainerSubtitles(activeEditionId, subtitleLocale, "JSON");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reRead = useMutation({
    mutationFn: async ({ canonicalSegmentId }: { canonicalSegmentId: string }) =>
      resynthesizeExplainerNarration(
        activeEditionId ?? "",
        canonicalSegmentId,
        "LOCAL_RE_READ",
        stableIdempotencyKey("explainer-reread", { editionId: activeEditionId, canonicalSegmentId }),
      ),
    onSuccess: async (result) => {
      setError(null);
      const record = result as Record<string, unknown>;
      const accepted = String(record.status ?? "") === "ACCEPTED";
      const jobId = record.job_id ? String(record.job_id) : "";
      setFeedback(
        accepted && jobId
          ? `已提交单段重读任务 ${jobId}；邻段会重新做拼接检查，新音频时长会真实进入后续时间线。`
          : `重读未被接受：${String(record.reason ?? record.status ?? "")}。没有创建后台任务。`,
      );
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => setError(mutationError instanceof Error ? mutationError.message : String(mutationError)),
  });

  const segments = (narration.data?.segments as Array<Record<string, unknown>> | undefined) ?? [];
  const segmentStates = (narration.data?.segment_states as Array<Record<string, unknown>> | undefined) ?? [];
  const takes = (narration.data?.takes as Array<Record<string, unknown>> | undefined) ?? [];
  const cues = (subtitles.data?.cues as Array<Record<string, unknown>> | undefined) ?? [];

  // Parent query first: editions pending → editions error → editions empty → then
  // the child narration query.  A *disabled* query reports ``isPending`` too, so
  // checking ``narration.isPending`` before "no editions" made an empty workspace
  // show "loading" forever.
  const state = useMemo<PageState | null>(() => {
    if (editions.isPending) return { kind: "loading", message: "正在载入输出版本…" };
    if (editions.isError) {
      return {
        kind: "failed",
        title: "无法载入输出版本",
        body: editions.error instanceof Error ? editions.error.message : "未知错误",
      };
    }
    if (editionList.length === 0) {
      return { kind: "empty", title: "还没有输出版本", body: "在总览提交预检后会按所选语言与画幅创建 edition。" };
    }
    if (narration.isPending) return { kind: "loading", message: "正在载入声音与字幕…" };
    if (narration.isError) {
      return {
        kind: "failed",
        title: "无法载入旁白",
        body: narration.error instanceof Error ? narration.error.message : "未知错误",
      };
    }
    if (segments.length === 0) {
      return { kind: "partial", title: "该版本还没有绑定讲稿", body: "先冻结一个讲稿版本，再进行分段合成。" };
    }
    if (takes.length === 0) {
      return {
        kind: "no_capability",
        title: "尚未生成配音",
        body: "旁白由真实本地 TTS 产生；没有音频时所有时长字段保持为空，不会补零并标记成功。",
      };
    }
    const missing = segments.filter(
      (segment) =>
        !segmentStates.some(
          (item) =>
            String(item.canonical_segment_id) === String(segment.canonical_segment_id) &&
            String(item.state) !== "NOT_GENERATED",
        ),
    );
    if (missing.length > 0) {
      return {
        kind: "partial",
        title: `${missing.length} 段还没有选定配音`,
        body: "漏读与错读会以问题条目呈现；失败段只重读该段，并重新检查邻段拼接。",
      };
    }
    return null;
  }, [
    editionList.length,
    editions.error,
    editions.isError,
    editions.isPending,
    narration.error,
    narration.isError,
    narration.isPending,
    segmentStates,
    segments,
    takes,
  ]);

  const alignmentLabel = (stateValue: string) => {
    switch (stateValue) {
      case "ALIGNED":
        return { text: "已对齐", className: "badge green" };
      case "ALIGNING":
        return { text: "对齐中", className: "badge" };
      case "AUDIO_READY":
        return { text: "有音频·未对齐", className: "badge warn" };
      case "FAILED":
        return { text: "对齐失败", className: "badge danger" };
      case "STALE":
        return { text: "对齐已过期", className: "badge danger" };
      default:
        return { text: "未生成", className: "badge warn" };
    }
  };

  return <div className="explainer-page">
    <Panel
      title="配音版本"
      subtitle="每种语言独立 TTS、独立对齐、独立重排。"
      actions={
        <>
          {editionList.length > 1 ? (
            <label className="explainer-field">
              输出版本
              <select value={activeEditionId ?? ""} onChange={(event) => setEditionId(event.target.value)}>
                {editionList.map((edition) => (
                  <option key={String(edition.id)} value={String(edition.id)}>
                    {String(edition.edition_key)} · {localeLabel(String(edition.voice_locale))} · {String(edition.aspect_ratio)}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {subtitleLocales.length > 0 ? (
            <div className="explainer-actions" role="tablist" aria-label="字幕语言">
              {subtitleLocales.map((value) => (
                <button
                  key={value}
                  type="button"
                  role="tab"
                  aria-selected={subtitleLocale === value}
                  className={subtitleLocale === value ? "primary-action" : undefined}
                  onClick={() => {
                    // Only the subtitle preview changes.  The narration clock stays
                    // on the active edition's voice locale.
                    setSubtitleLocaleParam(value);
                    setSearchParams((params) => {
                      params.set("locale", value);
                      return params;
                    });
                  }}
                >
                  {localeLabel(value)}
                </button>
              ))}
            </div>
          ) : null}
        </>
      }
    >
      <StateNotice state={state} />
      {activeEdition ? (
        <>
          <div className="explainer-summary-strip">
            <div><small>配音语言</small><strong>{localeLabel(voiceLocale)}</strong></div>
            <div><small>实测总时长</small><strong>{formatMs(narration.data?.measured_total_ms as number | null | undefined)}</strong></div>
            <div><small>时钟来源</small><strong>{String(activeEdition.duration_policy)}</strong></div>
            <div><small>字幕方式</small><strong>{subtitleModeLabel(String(activeEdition.subtitle_mode))}</strong></div>
          </div>
          <p className="explainer-note">
            英文版使用独立英文 TTS 时钟、独立剪辑点和字幕，不套用中文绝对时码。null 表示尚未生成，不会补零。
            实测总时长只统计当前冻结讲稿选中的 take，历史重读不会让它变长。
          </p>
        </>
      ) : null}
      <InlineOk message={feedback} />
      <InlineError message={error} />
    </Panel>

    <div className="explainer-grid">
      <Panel title="分段旁白" subtitle="按语义小段合成，保留跨段发音词典、音色版本与参数。">
        {segments.length === 0 ? <p className="muted">没有可显示的段落。</p> : segments.map((segment, index) => {
          const segmentState = segmentStates.find(
            (item) => String(item.canonical_segment_id) === String(segment.canonical_segment_id),
          );
          const take = takes.find((item) => String(item.canonical_segment_id) === String(segment.canonical_segment_id) && item.selected);
          const stateValue = String(segmentState?.state ?? "NOT_GENERATED");
          const badge = alignmentLabel(stateValue);
          return (
            <div className="explainer-segment" key={String(segment.id)}>
              <div className="explainer-segment-top">
                <span className="badge">段落 {String(index + 1).padStart(3, "0")}</span>
                <small>{take ? `实测 ${formatMs(take.measured_duration_ms as number | null | undefined)}` : "时长待实测"}</small>
                {/* "A take exists" is not alignment: the state comes from the
                    alignment revision, its hash and the take's media. */}
                <span className={badge.className}>{badge.text}</span>
              </div>
              <p>{String(segment.display_text ?? "")}</p>
              {segment.spoken_text && String(segment.spoken_text) !== String(segment.display_text) ? (
                <p className="spoken">朗读：{String(segment.spoken_text)}</p>
              ) : null}
              {stateValue === "ALIGNING" || segmentState?.alignment_error ? (
                <p className="muted">
                  {stateValue === "ALIGNING"
                    ? "正在对齐（此状态来自对齐 revision，不是猜测）。"
                    : `对齐失败：${String(segmentState?.alignment_error ?? "——")}`}
                </p>
              ) : null}
              {/* A real <audio> element: a take existing is not the same as being
                  able to hear it, and there is no fake waveform without audio. */}
              <NarrationPlayer
                src={segmentState?.audio_url ? String(segmentState.audio_url) : null}
                label={`第 ${String(index + 1).padStart(3, "0")} 段试听`}
                durationMs={take?.measured_duration_ms as number | null | undefined}
              />
              <div className="explainer-actions" style={{ marginTop: 8 }}>
                <button
                  type="button"
                  disabled={!activeEditionId || reRead.isPending}
                  onClick={() => reRead.mutate({ canonicalSegmentId: String(segment.canonical_segment_id) })}
                >
                  重读这一句
                </button>
                <span className="muted">波形与试听需要真实音频；没有音频时不会显示伪波形。</span>
              </div>
            </div>
          );
        })}
        <p className="explainer-note">
          修改发音只重生成有关段落；改动正文则同时更新对应字幕与语言时间线。对齐任务按小段处理并保持原采样偏移。
        </p>
      </Panel>

      <div className="explainer-stack">
        <Panel title="字幕预览" subtitle={`当前：${localeLabel(voiceLocale)}配音 + ${subtitleModeLabel(String(activeEdition?.subtitle_mode ?? "NONE"))}${subtitleLocale ? ` · 字幕 ${localeLabel(subtitleLocale)}` : ""}`}>
          <div className="explainer-subtitle-preview">
            <span className="line-primary">{cues.length > 0 ? String(cues[0].text ?? "（无字幕文本）") : "尚无字幕 revision"}</span>
            {cues.length > 0 && cues[0].paired_text ? <span className="line-secondary">{String(cues[0].paired_text)}</span> : null}
          </div>
          <SettingRow label="画幅" value={String(activeEdition?.aspect_ratio ?? "—")} />
          <SettingRow label="字幕条目" value={`${cues.length} 条`} />
          <SettingRow label="排版" value="每种语言最多两行 · 按画幅重算安全区" />
          <p className="explainer-note">
            样式编辑只触发布局与烧录，不会重跑 TTS。语言阅读速率默认中文 ≤8 字/秒、英文 ≤20 字符/秒，超限会给出提示或要求修排版。
          </p>
        </Panel>

        <Panel title="背景音乐与混音">
          <SettingRow label="背景音乐" value="本地已授权音乐库" />
          <SettingRow label="旁白压低背景" value="启用（sidechain ducking，约低 14–20 dB）" />
          <SettingRow label="响度档位" value="-16 LUFS ±1 · true peak ≤ -1 dBTP（产品默认，不是平台标准）" />
          <p className="explainer-note">
            人声歌曲会抢旁白，默认避免；ACE-Step 本地生成也可作为已校验来源，其许可同样进入资产记录。
          </p>
        </Panel>
      </div>
    </div>
  </div>;
}
