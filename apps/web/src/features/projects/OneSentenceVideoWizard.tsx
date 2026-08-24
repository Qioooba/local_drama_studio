import { useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  createGenerationIntent,
  createProject,
  createPrompt,
  createShotRevision,
  getJob,
  getProjectEpisodeCatalog,
  markShotProductionReady,
  planGenerationVariant,
  planProjectCreation,
  promoteJobArtifactToMedia,
  requestJson,
  resolveProfileCameraPlan,
  submitGenerationVariant,
  type CameraPlan,
  type GenerationVariantDraft,
  type Job,
  type Profile,
  type ProjectCreatePayload,
} from "../../generated/api";
import { ProfileExecutionDetailButton } from "../model-config/ProfileExecutionDetailButton";

type VideoPlan = {
  schema_version: "localdrama.one-sentence-video-plan.v1";
  title: string;
  video_prompt: string;
  director_intent: Record<string, unknown>;
  camera_movement: string;
  provider: string;
  model: string;
  remote: boolean;
};

type Result = {
  projectId: string;
  episodeId: string;
  shotId: string;
  jobId: string;
  mediaVersionId: string;
  prompt: string;
};

type Stage = "IDLE" | "EXPANDING" | "CREATING" | "DIRECTING" | "SUBMITTING" | "GENERATING" | "SUCCEEDED" | "FAILED";

const stageCopy: Record<Stage, string> = {
  IDLE: "等待一句话",
  EXPANDING: "DeepSeek 正在理解并扩写镜头",
  CREATING: "正在自动创建项目和分集",
  DIRECTING: "正在创建镜头并裁决运镜",
  SUBMITTING: "正在预检并提交文生视频任务",
  GENERATING: "本机 H3 正在生成视频",
  SUCCEEDED: "视频已生成，可直接播放",
  FAILED: "流程中断，可按提示修正后重试",
};

function capabilityContract(profile: Profile): Record<string, unknown> {
  return profile.capability_contract && typeof profile.capability_contract === "object"
    ? profile.capability_contract as Record<string, unknown>
    : {};
}

function isDeepSeek(profile: Profile): boolean {
  const contract = capabilityContract(profile);
  return profile.capability === "LLM_STORY_PARSE"
    && profile.status === "PUBLISHED"
    && String(contract.provider ?? "").toUpperCase() === "OPENAI_COMPAT"
    && `${String(contract.base_url ?? "")} ${String(contract.model ?? "")} ${profile.title}`.toLowerCase().includes("deepseek");
}

function isVerifiedCoreH3T2v(profile: Profile): boolean {
  if (profile.capability !== "VIDEO_T2V" || profile.status !== "PUBLISHED") return false;
  const contract = capabilityContract(profile);
  const manifest = contract.manifest_capability && typeof contract.manifest_capability === "object"
    ? contract.manifest_capability as Record<string, unknown>
    : {};
  const camera = contract.camera && typeof contract.camera === "object"
    ? contract.camera as Record<string, unknown>
    : {};
  return manifest.node_family === "comfy_extras.MiniMaxH3ImageToVideo"
    && manifest.playable_success_verified_in_this_run === true
    && ["NATIVE", "PROMPT_FALLBACK"].includes(String(camera.support ?? "").toUpperCase());
}

function projectCode(): string {
  return `video_${new Date().toISOString().replace(/\D/g, "").slice(2, 14)}_${crypto.randomUUID().slice(0, 6)}`;
}

function promotedMediaVersion(job: Job & { attempts?: Array<Record<string, unknown>> }): string | null {
  for (const attempt of [...(job.attempts ?? [])].reverse()) {
    const artifacts = Array.isArray(attempt.artifacts) ? attempt.artifacts : [];
    for (const artifact of [...artifacts].reverse()) {
      if (artifact && typeof artifact === "object" && typeof (artifact as Record<string, unknown>).promoted_media_version_id === "string") {
        return (artifact as Record<string, unknown>).promoted_media_version_id as string;
      }
    }
  }
  return null;
}

function latestVerifiedVideoArtifact(job: Job & { attempts?: Array<Record<string, unknown>> }): string | null {
  for (const attempt of [...(job.attempts ?? [])].reverse()) {
    const artifacts = Array.isArray(attempt.artifacts) ? attempt.artifacts : [];
    for (const artifact of [...artifacts].reverse()) {
      if (artifact && typeof artifact === "object") {
        const item = artifact as Record<string, unknown>;
        if (item.kind === "COMFY_OUTPUT" && item.status === "VERIFIED" && typeof item.id === "string") return item.id;
      }
    }
  }
  return null;
}

async function waitForVideoJob(jobId: string, onProgress: (job: Job) => void, signal: AbortSignal): Promise<{ job: Job; mediaVersionId: string }> {
  const deadline = Date.now() + 2 * 60 * 60 * 1000;
  while (Date.now() < deadline) {
    if (signal.aborted) throw new DOMException("已停止等待视频任务", "AbortError");
    const response = await getJob(jobId);
    const job = response.job;
    onProgress(job);
    if (job.state === "SUCCEEDED") {
      let mediaVersionId = promotedMediaVersion(job);
      if (!mediaVersionId) {
        const artifactId = latestVerifiedVideoArtifact(job);
        if (!artifactId) throw new Error("生成任务已完成，但没有找到已验证的视频 Artifact，请在任务详情检查输出收集记录。");
        const promoted = await promoteJobArtifactToMedia(artifactId, { purpose: "SHOT_VIDEO", media_kind: "VIDEO", stage: "FORMAL" });
        mediaVersionId = typeof promoted.media.media_version_id === "string"
          ? promoted.media.media_version_id
          : typeof promoted.media.id === "string" ? promoted.media.id : null;
      }
      if (!mediaVersionId) throw new Error("视频 Artifact 已验证，但媒体提升没有返回 MediaVersion；请在任务详情检查提升记录。");
      return { job, mediaVersionId };
    }
    if (["FAILED", "CANCELLED", "DEAD_LETTER"].includes(job.state)) {
      throw new Error(`视频生成失败：${job.last_error_code ?? job.state}${job.last_error_detail_redacted ? ` · ${job.last_error_detail_redacted}` : ""}`);
    }
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(resolve, 2500);
      signal.addEventListener("abort", () => { window.clearTimeout(timer); reject(new DOMException("已停止等待视频任务", "AbortError")); }, { once: true });
    });
  }
  throw new Error("视频生成等待超过 2 小时；任务仍保留，可前往任务中心继续查看。");
}

export function OneSentenceVideoWizard({ profiles, onProjectCreated }: {
  profiles: Profile[];
  onProjectCreated?: () => void;
}) {
  const deepSeekProfiles = useMemo(() => profiles.filter(isDeepSeek), [profiles]);
  const t2vProfiles = useMemo(() => profiles
    .filter(isVerifiedCoreH3T2v)
    .sort((left, right) => Number(right.version_no ?? 0) - Number(left.version_no ?? 0)), [profiles]);
  const [story, setStory] = useState("");
  const [llmProfileId, setLlmProfileId] = useState("");
  const [t2vProfileId, setT2vProfileId] = useState("");
  const [allowRemote, setAllowRemote] = useState(true);
  const [stage, setStage] = useState<Stage>("IDLE");
  const [detail, setDetail] = useState(stageCopy.IDLE);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const selectedLlm = llmProfileId || deepSeekProfiles[0]?.version_id || "";
  const selectedT2v = t2vProfileId || t2vProfiles[0]?.version_id || "";
  const busy = !["IDLE", "FAILED", "SUCCEEDED"].includes(stage);

  const run = async () => {
    const sentence = story.trim();
    if (sentence.length < 2) {
      setError("请先输入一句完整的视频描述。");
      return;
    }
    if (!selectedLlm) {
      setError("没有可用的已发布 DeepSeek Profile；请先在系统设置完成连接测试并发布。");
      return;
    }
    if (!selectedT2v) {
      setError("没有可用的已发布文生视频 Profile；请先在系统设置发布 VIDEO_T2V Profile。");
      return;
    }
    if (!allowRemote) {
      setError("DeepSeek 是远端接口，必须确认允许把这一句话发送给所选 Provider。");
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setError(null);
    setResult(null);
    setJob(null);

    try {
      setStage("EXPANDING");
      setDetail(stageCopy.EXPANDING);
      const expanded = await requestJson<{ plan: VideoPlan }>("/api/v1/local-llm/video-prompt:expand", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          profile_version_id: selectedLlm,
          story: sentence,
          allow_remote_outbound: true,
        }),
      });
      const videoPlan = expanded.plan;
      const code = projectCode();
      const payload: ProjectCreatePayload = {
        code,
        title: videoPlan.title || sentence.slice(0, 60),
        season_count: 1,
        episode_count: 1,
        target_duration_ms: 4000,
        aspect_ratio: "9:16",
        width: 480,
        height: 832,
        fps: { numerator: 24, denominator: 1 },
        primary_language: "zh-CN",
        subtitle_mode: "NONE",
        allow_unconfigured_capabilities: false,
        production_plan: {
          code: `${code}_local`,
          title: `${videoPlan.title} · 一句话视频方案`,
          plan: { mode: "LOCAL_ONLY", aspect_ratio: "9:16", resolution: { width: 480, height: 832 }, fps: { numerator: 24, denominator: 1 }, primary_language: "zh-CN", subtitle_mode: "NONE", source: "ONE_SENTENCE_VIDEO" },
        },
        profile_bindings: [
          { capability: "LLM_STORY_PARSE", profile_version_id: selectedLlm },
          { capability: "VIDEO_T2V", profile_version_id: selectedT2v },
        ],
        delivery_target: {
          code: `${code}_master`,
          title: `${videoPlan.title} · 本地视频`,
          spec: { path_rel: "06_delivery/master", width: 480, height: 832, fps: { numerator: 24, denominator: 1 }, subtitle_mode: "NONE" },
        },
      };

      setStage("CREATING");
      setDetail(stageCopy.CREATING);
      const preflight = await planProjectCreation(payload);
      if (preflight.plan.status === "BLOCKED") throw new Error(`项目预检未通过：${preflight.plan.blockers.join("、")}`);
      const created = await createProject(payload);
      onProjectCreated?.();
      const catalog = await getProjectEpisodeCatalog(created.project.id);
      const episodeId = catalog.catalog.seasons[0]?.episodes[0]?.id;
      if (!episodeId) throw new Error("项目已创建，但没有找到自动创建的第 1 集。");

      setStage("DIRECTING");
      setDetail(stageCopy.DIRECTING);
      const shotResponse = await requestJson<{ shot: Record<string, unknown> }>(`/api/v1/projects/${encodeURIComponent(created.project.id)}/episodes/${encodeURIComponent(episodeId)}/shots`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: "SHOT_001", target_duration_ms: 4000, shot_type: String(videoPlan.director_intent.shot_type ?? "MEDIUM") }),
      });
      const shotId = String(shotResponse.shot.id);
      const camera = await resolveProfileCameraPlan(selectedT2v, {
        shot_type: String(videoPlan.director_intent.shot_type ?? "MEDIUM"),
        movement: videoPlan.camera_movement || "STATIC",
        direction: "FORWARD",
        intensity: 0.5,
        curve: "EASE_IN_OUT",
        prompt_text: videoPlan.video_prompt,
      });
      if (!camera.resolution.submission_allowed) throw new Error(`当前 T2V Profile 不支持自动运镜：${camera.resolution.support}`);
      const cameraPlan: CameraPlan = { ...camera.resolution.camera_plan, profile_version_id: selectedT2v };
      const directorIntent = { ...videoPlan.director_intent, camera_plan: cameraPlan };
      await createShotRevision(shotId, directorIntent, false);
      await markShotProductionReady(shotId);

      setStage("SUBMITTING");
      setDetail(stageCopy.SUBMITTING);
      const intent = await createGenerationIntent({
        project_id: created.project.id,
        owner_type: "SHOT",
        owner_id: shotId,
        purpose: "T2V",
        creative_goal: sentence,
      });
      const prompt = await createPrompt({
        project_id: created.project.id,
        owner_type: "SHOT",
        owner_id: shotId,
        purpose: "T2V",
        title: `${videoPlan.title} · T2V`,
        content_text: videoPlan.video_prompt,
        structured: { camera_plan: cameraPlan, source_sentence: sentence, expanded_by: { provider: videoPlan.provider, model: videoPlan.model } },
      });
      const seed = crypto.getRandomValues(new Uint32Array(1))[0] & 0x7fffffff;
      const draft: GenerationVariantDraft = {
        intent_id: intent.intent.id,
        variant_type: "BASE",
        parent_variant_id: null,
        branch_reason: "ONE_SENTENCE_VIDEO",
        prompt_revision_id: prompt.revision.id,
        profile_version_id: selectedT2v,
        parameter_set: { PROMPT: videoPlan.video_prompt, SEED: seed, camera_plan: cameraPlan, timed_directions: [], performance_bindings: [], motion_masks: [] },
        seed_policy: "EXPLICIT",
        explicit_seed: seed,
        bindings: [],
      };
      const planned = await planGenerationVariant(draft);
      const submitted = await submitGenerationVariant({ ...draft, plan_hash: planned.plan.plan_hash, idempotency_key: crypto.randomUUID() });
      setJob(submitted.job);

      setStage("GENERATING");
      setDetail(stageCopy.GENERATING);
      const completed = await waitForVideoJob(submitted.job.id, (next) => {
        setJob(next);
        const progress = next.progress?.percent;
        const phase = next.progress?.phase ?? next.state;
        setDetail(`${stageCopy.GENERATING} · ${phase}${typeof progress === "number" ? ` ${Math.round(progress)}%` : ""}`);
      }, controller.signal);
      setJob(completed.job);
      setResult({ projectId: created.project.id, episodeId, shotId, jobId: submitted.job.id, mediaVersionId: completed.mediaVersionId, prompt: videoPlan.video_prompt });
      setStage("SUCCEEDED");
      setDetail(stageCopy.SUCCEEDED);
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      setStage("FAILED");
      setDetail(stageCopy.FAILED);
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  return <section className="one-sentence-video panel" aria-labelledby="one-sentence-video-title">
    <div className="one-sentence-video-heading">
      <div><p className="eyebrow">一句话成片</p><h3 id="one-sentence-video-title">描述画面，直接生成最后的视频</h3><p className="muted">DeepSeek 自动扩写导演提示，本机 H3 完成文生视频；项目、分集、镜头和生成任务会自动留档。</p></div>
      <span className={`status-pill${stage === "FAILED" ? " danger" : stage === "SUCCEEDED" ? "" : " neutral"}`}>{stageCopy[stage]}</span>
    </div>
    <label className="one-sentence-video-prompt">你想看到什么？<textarea value={story} onChange={(event) => setStory(event.target.value)} disabled={busy} rows={3} placeholder="例如：雨夜霓虹灯下，一只橘猫撑着透明雨伞穿过安静的街道，电影感镜头。" /></label>
    <details className="one-sentence-video-settings">
      <summary>模型与远端设置</summary>
      <div className="one-sentence-video-models">
        <label>DeepSeek 剧本拆解模型<select value={selectedLlm} onChange={(event) => setLlmProfileId(event.target.value)} disabled={busy}>{deepSeekProfiles.length === 0 && <option value="">没有已发布的 DeepSeek 模型配置</option>}{deepSeekProfiles.map((profile) => <option key={profile.version_id} value={profile.version_id}>{profile.title}</option>)}</select></label><ProfileExecutionDetailButton profileVersionId={selectedLlm} />
        <label>本机文字生成视频模型<select value={selectedT2v} onChange={(event) => setT2vProfileId(event.target.value)} disabled={busy}>{t2vProfiles.length === 0 && <option value="">没有已验证的本机文生视频模型</option>}{t2vProfiles.map((profile) => <option key={profile.version_id} value={profile.version_id}>{profile.title} · 第 {profile.version_no} 版</option>)}</select></label><ProfileExecutionDetailButton profileVersionId={selectedT2v} />
      </div>
      <div className="one-sentence-video-credential"><strong>远端凭据由系统安全配置</strong><span>这里不会重复索要 API Key；如连接不可用，请先到模型设置完成一次配置与验证。</span><Link to="/models?view=local-llm">打开故事拆解模型设置</Link></div>
      <label className="one-sentence-video-consent"><input type="checkbox" checked={allowRemote} onChange={(event) => setAllowRemote(event.target.checked)} disabled={busy} />允许将上面这一句话发送到所选 DeepSeek 远端接口；视频生成仍在本机执行。</label>
    </details>
    <div className="one-sentence-video-actions">
      <button type="button" onClick={() => void run()} disabled={busy || story.trim().length < 2 || !selectedLlm || !selectedT2v}>{busy ? "正在生成，请保持页面打开…" : stage === "FAILED" ? "修正后重新生成" : "一句话生成视频"}</button>
      {busy && <button type="button" className="secondary" onClick={() => { abortRef.current?.abort(); setStage("IDLE"); setDetail("已停止页面等待；已提交的后台任务不会被删除。"); }}>停止等待</button>}
    </div>
    <div className="one-sentence-video-progress" role="status" aria-live="polite">
      <strong>{detail}</strong>
      {job && <span>任务 {job.id.slice(0, 8)} · {job.state}{typeof job.progress?.percent === "number" ? ` · ${Math.round(job.progress.percent)}%` : ""}</span>}
    </div>
    {error && <p className="inline-error" role="alert">{error}</p>}
    {result && <div className="one-sentence-video-result">
      <video
        controls
        playsInline
        preload="none"
        poster={`/api/v1/media-versions/${encodeURIComponent(result.mediaVersionId)}/thumbnail?size=medium&frame=poster`}
        src={`/api/v1/media-versions/${encodeURIComponent(result.mediaVersionId)}/content`}
        aria-label="一句话生成的成片预览"
      >浏览器不支持视频播放。</video>
      <div><strong>成片已保存</strong><p className="muted">{result.prompt}</p><div className="one-sentence-video-links"><Link to={`/projects/${result.projectId}`}>打开项目</Link><Link to={`/projects/${result.projectId}/generation`}>查看生成记录</Link><Link to={`/projects/${result.projectId}/reviews`}>进入审片</Link></div></div>
    </div>}
  </section>;
}
