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
import { getProfileVersion } from "../../generated/api";
import type { GenerationPreference, PreferenceMode, PreferenceOwnerType } from "./types";
import { RecommendationFacts } from "./RecommendationFacts";
import { ProfileOverrideFields } from "../model-config/ProfileOverrideFields";
import { EffectiveConfigurationPreview } from "../model-config/EffectiveConfigurationPreview";
import { ModelInspectorDrawer } from "../model-config/ModelInspectorDrawer";
import { CAPABILITY_LABELS, creatorProfileTitle, type CanonicalCapability } from "./canonicalCapabilities";
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
  return entries.length ? entries.map(([key, value]) => `${key}: ${String(value)}`).join(" · ") : "由本机运行时检查";
}

function capabilityLabel(value: string) {
  return CAPABILITY_LABELS[value as CanonicalCapability] ?? value;
}

function modeLabel(value: PreferenceMode) {
  return value === "AUTO" ? "自动推荐" : "固定能力版本";
}

export function GenerationPreferencePanel({ initialProjectId }: { initialProjectId?: string } = {}) {
  const queryClient = useQueryClient();
  const initial = useMemo(() => new URLSearchParams(window.location.search), []);
  const [projectId, setProjectId] = useState(initialProjectId ?? initial.get("project") ?? "");
  const [seasonId, setSeasonId] = useState("");
  const [episodeId, setEpisodeId] = useState(initial.get("episode") ?? "");
  const [shotId, setShotId] = useState(initial.get("shot") ?? "");
  const [capability, setCapability] = useState("VIDEO_I2V");
  const [ownerType, setOwnerType] = useState<PreferenceOwnerType>("PROJECT");
  const [mode, setMode] = useState<PreferenceMode>("AUTO");
  const [profileVersionId, setProfileVersionId] = useState("");
  const [settings, setSettings] = useState<Record<string, unknown>>({});
  const [auditNote, setAuditNote] = useState("");
  const [expectedRevision, setExpectedRevision] = useState<number | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<PreferenceApiError | null>(null);
  const [inspectorVersionId, setInspectorVersionId] = useState<string | null>(null);

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
    setSettings(current?.settings ?? {});
    setAuditNote("");
    setExpectedRevision(current?.revision ?? null);
    setConflict(null);
    setLocalError(null);
  }, [currentIdentity]); // Reload the draft only when the selected immutable version changes.

  const publishedProfiles = (profiles.data ?? []).filter(
    (profile) => profile.status === "PUBLISHED" && profile.capability.toUpperCase() === capability,
  );
  const selectedProfile = publishedProfiles.find((profile) => profile.version_id === profileVersionId) ?? null;
  const inspector = useQuery({
    queryKey: ["profile-execution-detail", inspectorVersionId],
    queryFn: () => getProfileVersion(inspectorVersionId as string),
    enabled: Boolean(inspectorVersionId),
  });
  const resolvedSchema = resolution.data?.profile?.override_schema ?? selectedProfile?.override_schema ?? {};
  const save = useMutation({
    mutationFn: async () => {
      if (!projectId || !ownerId) throw new Error("请先选择完整的偏好作用域");
      if (mode === "EXPLICIT" && !profileVersionId) throw new Error("固定版本时必须选择可用生成能力");
      return putGenerationPreference(projectId, {
        owner_type: ownerType,
        owner_id: ownerId,
        capability,
        resolution_mode: mode,
        execution_profile_version_id: mode === "EXPLICIT" ? profileVersionId : null,
        settings,
        reason: `${OWNER_LABELS[ownerType]}：${capabilityLabel(capability)}使用${modeLabel(mode)}${auditNote.trim() ? `；补充：${auditNote.trim()}` : ""}`,
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
          <div><p className="eyebrow">创作范围</p><h3 id="preference-context-title">这项能力用在哪里？</h3></div>
          <span className="status-pill neutral">系统自动解析</span>
        </div>
        <div className="preference-context-grid">
          <label>项目
            <select value={projectId} onChange={(event) => { setProjectId(event.target.value); setSeasonId(""); setEpisodeId(""); setShotId(""); setOwnerType("PROJECT"); }}>
              <option value="">选择项目</option>
              {(projects.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
            </select>
          </label>
          <label>季
            <select value={seasonId} onChange={(event) => { setSeasonId(event.target.value); setEpisodeId(""); setShotId(""); }} disabled={!projectId || seasons.isPending}>
              <option value="">不限定季</option>
              {(seasons.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
            </select>
          </label>
          <label>分集
            <select value={episodeId} onChange={(event) => { setEpisodeId(event.target.value); setShotId(""); }} disabled={!seasonId || episodes.isPending}>
              <option value="">仅项目级</option>
              {(episodes.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
            </select>
          </label>
          <label>镜头
            <select value={shotId} onChange={(event) => setShotId(event.target.value)} disabled={!episodeId || shots.isPending}>
              <option value="">仅分集级</option>
              {(shots.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.code}</option>)}
            </select>
          </label>
          <label className="preference-capability">能力
            <select value={capability} onChange={(event) => { setCapability(event.target.value); setProfileVersionId(""); setSettings({}); }}>
              {CAPABILITY_GROUPS.map(([label, capabilities]) => (
                <optgroup key={label} label={label}>{capabilities.map((item) => <option key={item} value={item}>{capabilityLabel(item)}</option>)}</optgroup>
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
            <div><p className="eyebrow">当前生效结果</p><h3 id="preference-resolution-title">{capabilityLabel(capability)}</h3></div>
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
                <span>{resolution.data.profile ? `${creatorProfileTitle(resolution.data.profile.title)} · 第 ${resolution.data.profile.version_no} 版` : "没有可用能力版本"}</span>
                <p>{resolution.data.blocked_reason
                  ? `需要处理 · ${resolution.data.blocked_reason}`
                  : resolution.data.native_support ? "本机可直接执行" : "需要兼容工作流"}</p>
              </div>
              <RecommendationFacts resolution={resolution.data} />
              {resolution.data.warnings.map((warning) => <p className="inline-warning" role="status" key={warning}>{warning}</p>)}
              <dl className="resolution-facts">
                <div><dt>失败时改用其他能力</dt><dd>{resolution.data.fallback_support ? "已明确允许" : "不会擅自切换"}</dd></div>
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
                    ? <div className="inheritance-value"><span className="status-pill neutral">{modeLabel(item.resolution_mode)}</span><small>第 {item.revision} 版</small></div>
                    : <span className="muted">{level.id ? "继承上级" : "未选择"}</span>}
                </li>
              );
            })}
            <li className={resolution.data?.source === "AUTO" ? "effective" : ""}>
              <span className="inheritance-index">4</span>
              <div><strong>系统自动推荐</strong><small>仅在上方都没有单独设置时使用</small></div>
              <span className="muted">最后一级</span>
            </li>
          </ol>
        </section>

        <form className="panel preference-editor" onSubmit={(event) => { event.preventDefault(); save.mutate(); }} aria-labelledby="preference-editor-title">
          <div className="panel-heading">
            <div><p className="eyebrow">能力选择</p><h3 id="preference-editor-title">设置当前范围的生成能力</h3></div>
            <span className="status-pill neutral">{current ? `当前第 ${current.revision} 版` : "尚未单独设置"}</span>
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
            <legend>选择方式</legend>
            <label className={mode === "AUTO" ? "selected" : ""}>
              <input type="radio" name="preference-mode" value="AUTO" checked={mode === "AUTO"} onChange={() => { setMode("AUTO"); setSettings({}); }} />
              <span><strong>自动推荐</strong><small>系统从已发布且用途匹配的能力中选择合适版本。</small></span>
            </label>
            <label className={mode === "EXPLICIT" ? "selected" : ""}>
              <input type="radio" name="preference-mode" value="EXPLICIT" checked={mode === "EXPLICIT"} onChange={() => setMode("EXPLICIT")} />
              <span><strong>固定版本</strong><small>始终使用你明确选择的已发布能力版本。</small></span>
            </label>
          </fieldset>

          {mode === "EXPLICIT" && (
            <div className="editor-field-group">
              <label className="editor-field">生成能力版本
                <select value={profileVersionId} onChange={(event) => setProfileVersionId(event.target.value)} required>
                  <option value="">选择可用版本</option>
                  {publishedProfiles.map((profile) => <option key={profile.version_id} value={profile.version_id}>{creatorProfileTitle(profile.title)} · 第 {profile.version_no ?? "?"} 版</option>)}
                </select>
                {publishedProfiles.length === 0 && <small className="field-error">这项用途还没有可用的已发布能力版本。</small>}
              </label>
              {selectedProfile ? <button type="button" className="secondary profile-detail-button" onClick={() => setInspectorVersionId(selectedProfile.version_id)}>{inspector.isPending && inspectorVersionId === selectedProfile.version_id ? "读取详情中…" : "查看执行详情"}</button> : null}
            </div>
          )}

          <details className="advanced-settings" open={mode === "EXPLICIT" && Object.keys((resolvedSchema.fields as Record<string, unknown> | undefined) ?? {}).length > 0}>
            <summary>专家：审计备注与运行参数</summary>
            <label>审计备注（可选）
              <textarea value={auditNote} onChange={(event) => setAuditNote(event.target.value)} maxLength={1000} placeholder="只在需要说明特殊背景时补充" />
              <small>{auditNote.length}/1000 · 保存范围、能力和选择方式会由系统自动记录</small>
            </label>
            {mode === "AUTO" ? <p className="muted">AUTO 模式只保存能力选择，不保存 Profile 专属运行参数；固定版本后可展开对应参数。</p> : <ProfileOverrideFields schema={resolvedSchema} value={settings} scope={ownerType} onChange={setSettings} disabled={save.isPending} />}
            {mode === "EXPLICIT" && selectedProfile ? <small className="field-help">当前参数来自 {creatorProfileTitle(selectedProfile.title)} v{selectedProfile.version_no ?? "?"} 的声明契约；未知字段不会被静默保存。</small> : null}
          </details>

          {projectId && capability && (
            <EffectiveConfigurationPreview
              projectId={projectId}
              capability={capability}
              episodeId={episodeId}
              shotId={shotId}
              profileVersionId={mode === "EXPLICIT" ? profileVersionId : null}
              settings={mode === "EXPLICIT" ? settings : {}}
              enabled={mode === "AUTO" || Boolean(profileVersionId)}
            />
          )}

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
            <small>{expectedRevision === null ? "首次设置" : `基于第 ${expectedRevision} 版创建，不覆盖历史`}</small>
          </div>
        </form>
      </div>
      <ModelInspectorDrawer open={Boolean(inspectorVersionId)} profile={inspector.data?.profile_version ?? null} onClose={() => setInspectorVersionId(null)} />
    </div>
  );
}
