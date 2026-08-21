import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  listGenerationPreferences,
  listPreferenceEpisodes,
  listPreferenceProfiles,
  listPreferenceProjects,
  listPreferenceSeasons,
  listPreferenceShots,
  PreferenceApiError,
  putGenerationPreference,
  resolveGenerationPreference,
} from "./api";
import type { GenerationPreference, PreferenceMode, PreferenceOwnerType } from "./types";
import { RecommendationFacts } from "./RecommendationFacts";
import "./preferences.css";

const CAPABILITY_GROUPS = [
  ["剧本与规划", ["LLM_STORY_PARSE", "LLM_EPISODE_PLAN", "LLM_STORYBOARD", "LLM_PROMPT_REWRITE"]],
  ["图像", ["IMAGE_CONCEPT", "IMAGE_CHARACTER", "IMAGE_SCENE", "IMAGE_EDIT", "IMAGE_MULTI_VIEW", "IMAGE_EXPRESSION"]],
  ["视频", ["VIDEO_T2V", "VIDEO_I2V", "VIDEO_FIRST_FRAME", "VIDEO_FIRST_LAST_FRAME", "VIDEO_REFERENCE", "VIDEO_MOTION_CONTROL"]],
  ["声音", ["TTS", "VOICE_CLONE", "LIPSYNC", "AUDIO_SFX", "AUDIO_MUSIC"]],
  ["处理与质检", ["FRAME_EXTRACT", "UPSCALE_IMAGE", "UPSCALE_VIDEO", "POST_PROCESS", "QC_VISUAL", "QC_FACE", "QC_IDENTITY", "QC_CONTINUITY", "QC_AUDIO"]],
] as const;

const OWNER_LABELS: Record<PreferenceOwnerType, string> = {
  PROJECT: "项目默认",
  EPISODE: "本集覆盖",
  SHOT: "本镜头覆盖",
};

function preferenceFor(
  items: GenerationPreference[],
  ownerType: PreferenceOwnerType,
  ownerId: string,
  capability: string,
) {
  return items.find((item) => item.owner_type === ownerType && item.owner_id === ownerId && item.capability === capability);
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "读取失败";
}

function formatResources(resources: Record<string, unknown>) {
  const entries = Object.entries(resources);
  return entries.length ? entries.map(([key, value]) => `${key}: ${String(value)}`).join(" · ") : "当前 Profile 未声明资源估算";
}

export function GenerationPreferencePanel() {
  const queryClient = useQueryClient();
  const initial = useMemo(() => new URLSearchParams(window.location.search), []);
  const [projectId, setProjectId] = useState(initial.get("project") ?? "");
  const [seasonId, setSeasonId] = useState("");
  const [episodeId, setEpisodeId] = useState(initial.get("episode") ?? "");
  const [shotId, setShotId] = useState(initial.get("shot") ?? "");
  const [capability, setCapability] = useState("VIDEO_I2V");
  const [ownerType, setOwnerType] = useState<PreferenceOwnerType>("PROJECT");
  const [mode, setMode] = useState<PreferenceMode>("AUTO");
  const [profileVersionId, setProfileVersionId] = useState("");
  const [settingsText, setSettingsText] = useState("{}");
  const [reason, setReason] = useState("");
  const [expectedRevision, setExpectedRevision] = useState<number | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<PreferenceApiError | null>(null);

  const projects = useQuery({ queryKey: ["preference-v2", "projects"], queryFn: listPreferenceProjects });
  const profiles = useQuery({ queryKey: ["preference-v2", "profiles"], queryFn: listPreferenceProfiles });
  const seasons = useQuery({
    queryKey: ["preference-v2", "seasons", projectId],
    queryFn: () => listPreferenceSeasons(projectId),
    enabled: Boolean(projectId),
  });
  const episodes = useQuery({
    queryKey: ["preference-v2", "episodes", seasonId],
    queryFn: () => listPreferenceEpisodes(seasonId),
    enabled: Boolean(seasonId),
  });
  const shots = useQuery({
    queryKey: ["preference-v2", "shots", episodeId],
    queryFn: () => listPreferenceShots(episodeId),
    enabled: Boolean(episodeId),
  });
  const preferences = useQuery({
    queryKey: ["preference-v2", "preferences", projectId],
    queryFn: () => listGenerationPreferences(projectId),
    enabled: Boolean(projectId),
  });
  const resolution = useQuery({
    queryKey: ["preference-v2", "resolution", projectId, capability, episodeId, shotId],
    queryFn: () => resolveGenerationPreference(projectId, capability, episodeId || undefined, shotId || undefined),
    enabled: Boolean(projectId && capability),
  });

  useEffect(() => {
    if (!projectId && projects.data?.[0]) setProjectId(projects.data[0].id);
  }, [projectId, projects.data]);

  useEffect(() => {
    if (seasons.data && !seasons.data.some((item) => item.id === seasonId)) {
      setSeasonId(seasons.data[0]?.id ?? "");
      if (!initial.get("episode")) setEpisodeId("");
      setShotId("");
    }
  }, [initial, seasonId, seasons.data]);

  useEffect(() => {
    if (episodes.data && !episodes.data.some((item) => item.id === episodeId)) {
      setEpisodeId(episodes.data[0]?.id ?? "");
      setShotId("");
    }
  }, [episodeId, episodes.data]);

  useEffect(() => {
    if (shots.data && !shots.data.some((item) => item.id === shotId)) setShotId(shots.data[0]?.id ?? "");
  }, [shotId, shots.data]);

  const ownerId = ownerType === "PROJECT" ? projectId : ownerType === "EPISODE" ? episodeId : shotId;
  const current = preferenceFor(preferences.data ?? [], ownerType, ownerId, capability);
  const currentIdentity = current ? `${current.preference_set_id}:${current.current_version_id}:${current.revision}` : `new:${ownerType}:${ownerId}:${capability}`;

  useEffect(() => {
    setMode(current?.resolution_mode ?? "AUTO");
    setProfileVersionId(current?.execution_profile_version_id ?? "");
    setSettingsText(JSON.stringify(current?.settings ?? {}, null, 2));
    setReason(current?.reason ?? "");
    setExpectedRevision(current?.revision ?? null);
    setConflict(null);
    setLocalError(null);
  }, [currentIdentity]); // Reload the draft only when the selected immutable version changes.

  const publishedProfiles = (profiles.data ?? []).filter(
    (profile) => profile.status === "PUBLISHED" && profile.capability.toUpperCase() === capability,
  );

  const save = useMutation({
    mutationFn: async () => {
      if (!projectId || !ownerId) throw new Error("请先选择完整的偏好作用域");
      if (mode === "EXPLICIT" && !profileVersionId) throw new Error("EXPLICIT 模式必须选择已发布的 Profile");
      let settings: Record<string, unknown>;
      try {
        const parsed = JSON.parse(settingsText) as unknown;
        if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error();
        settings = parsed as Record<string, unknown>;
      } catch {
        throw new Error("高级设置必须是 JSON 对象");
      }
      return putGenerationPreference(projectId, {
        owner_type: ownerType,
        owner_id: ownerId,
        capability,
        resolution_mode: mode,
        execution_profile_version_id: mode === "EXPLICIT" ? profileVersionId : null,
        settings,
        reason: reason.trim(),
        expected_revision: expectedRevision,
      });
    },
    onMutate: () => { setLocalError(null); setConflict(null); },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["preference-v2", "preferences", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["preference-v2", "resolution", projectId] }),
      ]);
    },
    onError: (error) => {
      if (error instanceof PreferenceApiError && error.status === 409 && error.code === "GENERATION_PREFERENCE_REVISION_CONFLICT") {
        setConflict(error);
      } else {
        setLocalError(errorMessage(error));
      }
    },
  });

  const reloadConflict = async () => {
    await queryClient.invalidateQueries({ queryKey: ["preference-v2", "preferences", projectId] });
    setConflict(null);
  };

  const hierarchy: Array<{ type: PreferenceOwnerType; id: string; title: string }> = [
    { type: "PROJECT", id: projectId, title: projects.data?.find((item) => item.id === projectId)?.title ?? "未选择项目" },
    { type: "EPISODE", id: episodeId, title: episodes.data?.find((item) => item.id === episodeId)?.title ?? "未选择分集" },
    { type: "SHOT", id: shotId, title: shots.data?.find((item) => item.id === shotId)?.code ?? "未选择镜头" },
  ];

  return (
    <div className="generation-preferences">
      <section className="panel preference-context" aria-labelledby="preference-context-title">
        <div className="panel-heading">
          <div><p className="eyebrow">生产上下文</p><h3 id="preference-context-title">选择解析目标</h3></div>
          <span className="status-pill neutral">本机 API</span>
        </div>
        <div className="preference-context-grid">
          <label>项目
            <select value={projectId} onChange={(event) => { setProjectId(event.target.value); setSeasonId(""); setEpisodeId(""); setShotId(""); setOwnerType("PROJECT"); }}>
              <option value="">选择项目</option>
              {(projects.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.code} · {item.title}</option>)}
            </select>
          </label>
          <label>季度
            <select value={seasonId} onChange={(event) => { setSeasonId(event.target.value); setEpisodeId(""); setShotId(""); }} disabled={!projectId || seasons.isPending}>
              <option value="">无季度</option>
              {(seasons.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.code} · {item.title}</option>)}
            </select>
          </label>
          <label>分集
            <select value={episodeId} onChange={(event) => { setEpisodeId(event.target.value); setShotId(""); }} disabled={!seasonId || episodes.isPending}>
              <option value="">仅项目级</option>
              {(episodes.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.code} · {item.title}</option>)}
            </select>
          </label>
          <label>镜头
            <select value={shotId} onChange={(event) => setShotId(event.target.value)} disabled={!episodeId || shots.isPending}>
              <option value="">仅分集级</option>
              {(shots.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.code}</option>)}
            </select>
          </label>
          <label className="preference-capability">能力
            <select value={capability} onChange={(event) => setCapability(event.target.value)}>
              {CAPABILITY_GROUPS.map(([label, capabilities]) => (
                <optgroup key={label} label={label}>{capabilities.map((item) => <option key={item} value={item}>{item}</option>)}</optgroup>
              ))}
            </select>
          </label>
        </div>
        {(projects.isError || seasons.isError || episodes.isError || shots.isError) && (
          <p className="inline-error" role="alert">上下文读取失败：{errorMessage(projects.error ?? seasons.error ?? episodes.error ?? shots.error)}</p>
        )}
      </section>

      <div className="preference-workspace">
        <section className="panel preference-resolution" aria-labelledby="preference-resolution-title">
          <div className="panel-heading">
            <div><p className="eyebrow">当前解析结果</p><h3 id="preference-resolution-title">{capability}</h3></div>
            {resolution.data?.blocked_reason
              ? <span className="status-pill state-blocked">阻塞</span>
              : <span className="status-pill state-active">可执行</span>}
          </div>

          {resolution.isPending && <p className="empty-state" aria-live="polite">正在解析继承链…</p>}
          {resolution.isError && <div className="workspace-error" role="alert"><div><strong>解析失败</strong><p>{errorMessage(resolution.error)}</p></div><button type="button" className="secondary" onClick={() => void resolution.refetch()}>重试</button></div>}
          {resolution.data && (
            <>
              <div className={`resolution-summary ${resolution.data.blocked_reason ? "blocked" : "ready"}`}>
                <span className="resolution-kicker">生效来源</span>
                <strong>{resolution.data.source === "AUTO" ? "系统自动解析" : OWNER_LABELS[resolution.data.source]}</strong>
                <span>{resolution.data.profile ? `${resolution.data.profile.code} · v${resolution.data.profile.version_no}` : "无可执行 Profile"}</span>
                <p>{resolution.data.blocked_reason
                  ? `BLOCKER · ${resolution.data.blocked_reason}`
                  : resolution.data.native_support ? "原生能力已确认" : "原生能力未确认"}</p>
              </div>
              <RecommendationFacts resolution={resolution.data} />
              {resolution.data.warnings.map((warning) => <p className="inline-warning" role="status" key={warning}>{warning}</p>)}
              <dl className="resolution-facts">
                <div><dt>Fallback</dt><dd>{resolution.data.fallback_support ? "显式允许" : "不允许静默回退"}</dd></div>
                <div><dt>资源估算</dt><dd>{formatResources(resolution.data.estimated_resources)}</dd></div>
              </dl>
            </>
          )}

          <ol className="inheritance-ladder" aria-label="偏好继承层级">
            {hierarchy.map((level, index) => {
              const item = level.id ? preferenceFor(preferences.data ?? [], level.type, level.id, capability) : undefined;
              const isSource = resolution.data?.source === level.type;
              return (
                <li key={level.type} className={isSource ? "effective" : ""}>
                  <span className="inheritance-index">{index + 1}</span>
                  <div><strong>{OWNER_LABELS[level.type]}</strong><small>{level.title}</small></div>
                  {item
                    ? <div className="inheritance-value"><span className="status-pill neutral">{item.resolution_mode}</span><small>revision {item.revision}</small></div>
                    : <span className="muted">{level.id ? "继承上级" : "未选择"}</span>}
                </li>
              );
            })}
            <li className={resolution.data?.source === "AUTO" ? "effective" : ""}>
              <span className="inheritance-index">4</span>
              <div><strong>系统 AUTO</strong><small>仅在三级均未声明时使用</small></div>
              <span className="muted">最后一级</span>
            </li>
          </ol>
        </section>

        <form className="panel preference-editor" onSubmit={(event) => { event.preventDefault(); save.mutate(); }} aria-labelledby="preference-editor-title">
          <div className="panel-heading">
            <div><p className="eyebrow">偏好编辑</p><h3 id="preference-editor-title">配置覆盖层</h3></div>
            <span className="status-pill neutral">{current ? `revision ${current.revision}` : "新建"}</span>
          </div>

          <fieldset className="scope-selector">
            <legend>保存到</legend>
            {hierarchy.map((level) => (
              <button key={level.type} type="button" className={ownerType === level.type ? "selected" : ""} disabled={!level.id} onClick={() => setOwnerType(level.type)} aria-pressed={ownerType === level.type}>
                <strong>{OWNER_LABELS[level.type]}</strong><small>{level.id ? level.title : "当前不可用"}</small>
              </button>
            ))}
          </fieldset>

          <fieldset className="mode-selector">
            <legend>解析模式</legend>
            <label className={mode === "AUTO" ? "selected" : ""}>
              <input type="radio" name="preference-mode" value="AUTO" checked={mode === "AUTO"} onChange={() => setMode("AUTO")} />
              <span><strong>AUTO</strong><small>在当前层声明自动选择已发布 Profile；不可固定版本。</small></span>
            </label>
            <label className={mode === "EXPLICIT" ? "selected" : ""}>
              <input type="radio" name="preference-mode" value="EXPLICIT" checked={mode === "EXPLICIT"} onChange={() => setMode("EXPLICIT")} />
              <span><strong>EXPLICIT</strong><small>固定到一个已发布且能力完全匹配的 Profile 版本。</small></span>
            </label>
          </fieldset>

          {mode === "EXPLICIT" && (
            <label className="editor-field">执行 Profile 版本
              <select value={profileVersionId} onChange={(event) => setProfileVersionId(event.target.value)} required>
                <option value="">选择已发布 Profile</option>
                {publishedProfiles.map((profile) => <option key={profile.version_id} value={profile.version_id}>{profile.code} · {profile.title} · v{profile.version_no ?? "?"}</option>)}
              </select>
              {publishedProfiles.length === 0 && <small className="field-error">此能力没有已发布 Profile，EXPLICIT 保存会被阻止。</small>}
            </label>
          )}

          <label className="editor-field">变更原因
            <textarea value={reason} onChange={(event) => setReason(event.target.value)} maxLength={1000} placeholder="说明为什么在这一层覆盖模型偏好" />
            <small>{reason.length}/1000 · 会写入不可变偏好版本</small>
          </label>

          <details className="advanced-settings">
            <summary>高级设置 JSON</summary>
            <label>设置对象
              <textarea value={settingsText} onChange={(event) => setSettingsText(event.target.value)} spellCheck={false} />
            </label>
          </details>

          {conflict && (
            <div className="revision-conflict" role="alert">
              <strong>检测到 revision 冲突，未覆盖他人的更改</strong>
              <p>你的基线：{String(expectedRevision ?? "不存在")} · 服务端当前：{String(conflict.details.actual_revision ?? "不存在")}</p>
              <p>{conflict.message}{conflict.requestId ? ` · request ${conflict.requestId}` : ""}</p>
              <button type="button" className="secondary" onClick={() => void reloadConflict()}>读取最新版本后重新编辑</button>
            </div>
          )}
          {localError && <p className="inline-error" role="alert">{localError}</p>}
          {save.isSuccess && !save.isPending && !conflict && <p className="inline-success" role="status">偏好已保存，解析结果已重新计算。</p>}

          <div className="preference-save-row">
            <button className="primary-action" type="submit" disabled={save.isPending || !ownerId || (mode === "EXPLICIT" && !profileVersionId)}>{save.isPending ? "保存中…" : "保存新版本"}</button>
            <small>expected_revision: {expectedRevision ?? "null（首次创建）"}</small>
          </div>
        </form>
      </div>
    </div>
  );
}
