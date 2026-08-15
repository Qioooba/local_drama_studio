import { useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { createFrameAnchor, createGenerationIntent, createKeyframeCandidate, createPrompt, deriveGenerationVariantSeedBatch, planGenerationVariant, submitGenerationVariant, type CameraPlan, type FrameAnchor, type G6Readiness, type GenerationVariantDraft, type GenerationVariantPlan, type I2VProbePlan, type Job, type Profile, type ReviewInboxItem } from "../../generated/api";
import { GateStatusIcon } from "../../components/icons";

type Shot = Record<string, unknown>;

type GenerationWorkbenchProps = {
  projectId: string | null;
  profiles: Profile[];
  candidates: ReviewInboxItem[];
  h3?: { status: string; release_root?: string; missing_sidecars?: string[] };
  g6Readiness?: G6Readiness;
  i2vProbePlan?: I2VProbePlan;
  shots: Shot[];
  selectedShotId: string | null;
  onSelectShot: (id: string) => void;
  onOpenProfiles: () => void;
  onOpenReviews?: (mediaVersionId?: string) => void;
  onSubmitted?: () => void;
};

const modes = [
  { id: "T2I", title: "文字生成图片", detail: "从镜头描述创建关键帧候选", icon: "文 / 图" },
  { id: "I2V", title: "图片生成视频", detail: "用已注册首帧生成代理 take", icon: "图 / 影" },
  { id: "T2V", title: "文字生成视频", detail: "不绑定首帧，探索动作和运镜", icon: "文 / 影" },
  { id: "R2V", title: "参考图生成视频", detail: "保持角色或风格参考的一致性", icon: "参 / 影" },
] as const;

type FrameAction = "FIRST_FRAME" | "CURRENT_FRAME" | "LAST_FRAME";

const frameActionLabels: Record<FrameAction, string> = {
  FIRST_FRAME: "首帧",
  CURRENT_FRAME: "当前帧",
  LAST_FRAME: "末帧",
};

const readinessLabels: Record<string, string> = {
  APPROVED_KEYFRAME: "批准一张真实关键帧",
  PUBLISHED_I2V_PROFILE: "用真实 I2V 成功证据发布能力",
  FOUR_REAL_PROXY_TAKES: "从同一批准关键帧生成 4 个真实代理",
  HUMAN_PROXY_WINNER: "由人工选择代理 winner",
  FORMAL_VIDEO: "从 winner 生成正式视频",
  FORMAL_MACHINE_QC: "正式视频通过机器 QC",
  FORMAL_HUMAN_APPROVAL: "由人工完成正式审核批准",
};

export function GenerationWorkbench({ projectId, profiles, candidates, h3, g6Readiness, i2vProbePlan, shots, selectedShotId, onSelectShot, onOpenProfiles, onOpenReviews, onSubmitted }: GenerationWorkbenchProps) {
  const [mode, setMode] = useState<(typeof modes)[number]["id"]>("I2V");
  const videos = useMemo(() => candidates.filter((item) => item.media_kind === "VIDEO"), [candidates]);
  const approvedKeyframeIds = useMemo(() => {
    const ids = candidates.filter((item) => item.media_kind === "IMAGE" && item.stage === "KEYFRAME" && item.decision === "APPROVED" && !item.is_stale).map((item) => item.media_version_id);
    const gateApproved = i2vProbePlan?.snapshot.approved_keyframe?.media_version_id;
    if (gateApproved) ids.push(gateApproved);
    return [...new Set(ids)];
  }, [candidates, i2vProbePlan]);
  const eligibleProfiles = useMemo(() => profiles.filter((profile) => profile.capability === mode), [mode, profiles]);
  const [profileVersionId, setProfileVersionId] = useState("");
  const [sourceVideoId, setSourceVideoId] = useState("");
  const [currentTimeSeconds, setCurrentTimeSeconds] = useState("0");
  const [draftAnchor, setDraftAnchor] = useState<FrameAnchor | null>(null);
  const [frameAction, setFrameAction] = useState<FrameAction | null>(null);
  const [promptText, setPromptText] = useState("");
  const [seedText, setSeedText] = useState("42");
  const [approvedKeyframeId, setApprovedKeyframeId] = useState("");
  const [prepared, setPrepared] = useState<{ draft: GenerationVariantDraft; plan: GenerationVariantPlan; idempotencyKey: string } | null>(null);
  const [submitted, setSubmitted] = useState<Job | null>(null);
  const [submittedVariantId, setSubmittedVariantId] = useState<string | null>(null);
  const [seedBatchText, setSeedBatchText] = useState("43,44,45,46");
  const [seedBatchPlan, setSeedBatchPlan] = useState<Awaited<ReturnType<typeof deriveGenerationVariantSeedBatch>>["batch"] | null>(null);
  useEffect(() => {
    setProfileVersionId(eligibleProfiles.find((item) => item.status === "PUBLISHED")?.version_id ?? eligibleProfiles[0]?.version_id ?? "");
  }, [eligibleProfiles]);
  useEffect(() => {
    if (!videos.some((item) => item.media_version_id === sourceVideoId)) setSourceVideoId(videos[0]?.media_version_id ?? "");
  }, [sourceVideoId, videos]);
  useEffect(() => {
    if (!approvedKeyframeIds.includes(approvedKeyframeId)) setApprovedKeyframeId(approvedKeyframeIds[0] ?? "");
  }, [approvedKeyframeId, approvedKeyframeIds]);
  useEffect(() => { setPrepared(null); setSubmitted(null); setSubmittedVariantId(null); setSeedBatchPlan(null); }, [mode, profileVersionId, selectedShotId, promptText, seedText, approvedKeyframeId]);
  const selected = eligibleProfiles.find((profile) => profile.version_id === profileVersionId);
  const selectedShot = shots.find((shot) => String(shot.id) === selectedShotId);
  const revision = selectedShot?.current_revision && typeof selectedShot.current_revision === "object" ? selectedShot.current_revision as Record<string, unknown> : {};
  const cameraPlan = revision.camera_plan && typeof revision.camera_plan === "object" ? revision.camera_plan as CameraPlan : null;
  const requiresCamera = mode !== "T2I";
  const cameraReady = !requiresCamera || Boolean(cameraPlan && cameraPlan.mode !== "UNSUPPORTED" && cameraPlan.profile_version_id === profileVersionId);
  const sourceReady = mode !== "I2V" || Boolean(approvedKeyframeId);
  const seed = Number(seedText);
  const runnable = selected?.status === "PUBLISHED" && Boolean(projectId && selectedShotId && promptText.trim() && Number.isInteger(seed) && cameraReady && sourceReady);
  const selectedVideo = videos.find((item) => item.media_version_id === sourceVideoId);
  const anchorMutation = useMutation({
    mutationFn: async (action: FrameAction) => {
      if (!sourceVideoId) throw new Error("当前项目没有可用视频");
      const seconds = Number(currentTimeSeconds);
      if (action === "CURRENT_FRAME" && (!Number.isFinite(seconds) || seconds < 0)) throw new Error("当前时间必须是大于或等于 0 的秒数");
      setFrameAction(action);
      return createFrameAnchor(sourceVideoId, action === "FIRST_FRAME"
        ? { position_mode: "FIRST_FRAME", role_hint: action }
        : action === "LAST_FRAME"
          ? { position_mode: "LAST_FRAME", role_hint: action }
          : { source_time_us: Math.round(seconds * 1_000_000), role_hint: action });
    },
    onSuccess: ({ frame_anchor }) => setDraftAnchor(frame_anchor),
  });
  const keyframeMutation = useMutation({
    mutationFn: async () => {
      if (!draftAnchor || !selectedShotId) throw new Error("必须先选择镜头并提取真实帧");
      return createKeyframeCandidate(draftAnchor.extracted_media_version_id, selectedShotId);
    },
    onSuccess: ({ media }) => onOpenReviews?.(media.id),
  });
  const preflightMutation = useMutation({
    mutationFn: async () => {
      if (!projectId || !selectedShotId || !selected) throw new Error("必须先选择项目、镜头和已发布 Profile");
      if (!promptText.trim()) throw new Error("Prompt 不能为空");
      if (!Number.isInteger(seed)) throw new Error("Seed 必须是整数");
      if (requiresCamera && !cameraReady) throw new Error("结构化运镜必须由当前同一 Profile 裁决为可执行");
      if (mode === "I2V" && !approvedKeyframeId) throw new Error("I2V 代理必须选择当前已批准关键帧");
      const intent = await createGenerationIntent({ project_id: projectId, owner_type: "SHOT", owner_id: selectedShotId, purpose: mode === "I2V" ? "I2V_PROXY" : mode, creative_goal: promptText.trim() });
      const prompt = await createPrompt({ project_id: projectId, owner_type: "SHOT", owner_id: selectedShotId, purpose: mode, title: `${String(selectedShot?.code ?? selectedShotId)} ${mode}`, content_text: promptText.trim(), structured: { camera_plan: cameraPlan } });
      const draft: GenerationVariantDraft = {
        intent_id: intent.intent.id,
        variant_type: "BASE",
        parent_variant_id: null,
        branch_reason: "UI_BASE_GENERATION",
        prompt_revision_id: prompt.revision.id,
        profile_version_id: selected.version_id,
        parameter_set: { PROMPT: promptText.trim(), SEED: seed, ...(cameraPlan ? { camera_plan: cameraPlan } : {}) },
        seed_policy: "EXPLICIT",
        explicit_seed: seed,
        bindings: mode === "I2V" ? [{ role: "FIRST_FRAME", media_version_id: approvedKeyframeId, ordinal: 0 }] : [],
      };
      const planned = await planGenerationVariant(draft);
      return { draft, plan: planned.plan, idempotencyKey: crypto.randomUUID() };
    },
    onSuccess: setPrepared,
  });
  const submitMutation = useMutation({
    mutationFn: async () => {
      if (!prepared) throw new Error("必须先完成当前输入的资源预检");
      return submitGenerationVariant({ ...prepared.draft, plan_hash: prepared.plan.plan_hash, idempotency_key: prepared.idempotencyKey });
    },
    onSuccess: ({ job, variant }) => { setSubmitted(job); setSubmittedVariantId(variant.id); onSubmitted?.(); },
  });
  const seedBatchMutation = useMutation({
    mutationFn: async () => {
      if (!submittedVariantId) throw new Error("请先提交一个生成 Variant");
      const seeds = seedBatchText.split(",").map((value) => Number(value.trim())).filter((value) => Number.isInteger(value));
      if (seeds.length === 0 || seeds.length > 24 || new Set(seeds).size !== seeds.length) throw new Error("批量 seed 必须是 1—24 个不重复整数");
      return deriveGenerationVariantSeedBatch(submittedVariantId, { seeds, branch_reason: "UI_SEED_BATCH_EXPERIMENT" });
    },
    onSuccess: ({ batch }) => setSeedBatchPlan(batch),
  });
  const extractedThumbnail = draftAnchor ? `/api/v1/media-versions/${encodeURIComponent(draftAnchor.extracted_media_version_id)}/thumbnail?size=small` : null;
  const draftRoleLabel = draftAnchor ? frameActionLabels[draftAnchor.role_hint as FrameAction] ?? "提取帧" : null;

  return <div className="creation-workbench">
    <section className="workflow-rail" aria-label="生成步骤">
      {["选择生成方式", "绑定输入", "选择本地能力", "资源预检", "比较与审核"].map((step, index) => <div className={`workflow-step${index === 0 ? " active" : ""}`} aria-current={index === 0 ? "step" : undefined} key={step}><span>{index + 1}</span><strong>{step}</strong></div>)}
    </section>
    {g6Readiness && <section className="panel gate-readiness" aria-labelledby="g6-readiness-title">
      <div className="panel-heading"><div><p className="eyebrow">G6 EXIT READINESS</p><h3 id="g6-readiness-title">真实生成闭环门禁</h3></div><span className={`status-pill${g6Readiness.status === "PASS" ? "" : " neutral"}`}>{g6Readiness.status}</span></div>
      <ol className="gate-checks">{g6Readiness.checks.map((check) => <li className={check.passed ? "passed" : "blocked"} key={check.code}><GateStatusIcon passed={check.passed} /><strong>{readinessLabels[check.code] ?? check.code}</strong>{check.count !== undefined && <small>{check.count} 项证据</small>}</li>)}</ol>
      {g6Readiness.next_required_action && <p className="gate-next"><strong>下一项真实动作：</strong>{readinessLabels[g6Readiness.next_required_action] ?? g6Readiness.next_required_action}。系统不会自动替代人工选择或批准。</p>}
    </section>}
    {i2vProbePlan && <section className="panel probe-plan" aria-labelledby="i2v-probe-plan-title">
      <div className="panel-heading"><div><p className="eyebrow">READ-ONLY I2V PROBE</p><h3 id="i2v-probe-plan-title">真实证据探针计划</h3></div><span className={`status-pill${i2vProbePlan.status === "READY" ? "" : " neutral"}`}>{i2vProbePlan.status}</span></div>
      {i2vProbePlan.status === "READY" ? <>
        <dl className="probe-plan-grid">
          <div><dt>批准关键帧</dt><dd><code>{i2vProbePlan.snapshot.approved_keyframe?.media_version_id.slice(0, 12)}</code></dd></div>
          <div><dt>批准记录</dt><dd><code>{i2vProbePlan.snapshot.approved_keyframe?.approval_id.slice(0, 12)}</code></dd></div>
          <div><dt>发布工作流</dt><dd><code>{i2vProbePlan.snapshot.workflow?.id.slice(0, 12)}</code></dd></div>
          <div><dt>候选 Profile</dt><dd>{i2vProbePlan.snapshot.candidate_profile?.status ?? "—"}</dd></div>
        </dl>
        <p className="probe-plan-hash"><strong>不可变计划哈希</strong><code>{i2vProbePlan.plan_hash}</code></p>
      </> : <p className="gate-next"><strong>阻塞项：</strong>{i2vProbePlan.blockers.join(" · ")}</p>}
      <p className="probe-plan-safety">只读检查完成：未创建 Job、未连接 ComfyUI。执行仍需单独确认，当前不会占用操作者的运行时。</p>
    </section>}
    <section className="panel composer-panel">
      <div className="panel-heading"><div><p className="eyebrow">生产上下文</p><h3>先锁定镜头，再创建不可变生成分支</h3></div><span className="status-pill neutral">不会覆盖历史</span></div>
      <div className="shot-context">
        <label htmlFor="generation-shot">当前镜头</label>
        <select id="generation-shot" value={selectedShotId ?? ""} onChange={(event) => onSelectShot(event.target.value)} disabled={!shots.length}>
          {!shots.length && <option value="">当前集没有可用镜头</option>}
          {shots.map((shot) => <option key={String(shot.id)} value={String(shot.id)}>{String(shot.code ?? shot.id)} · {String(shot.status ?? "UNKNOWN")}</option>)}
        </select>
        <small>{selectedShotId ? "镜头选择会保留在链接中，刷新后可恢复。" : "请先在分集生产中创建或选择真实镜头。"}</small>
      </div>

      <div className="section-heading"><p className="eyebrow">生成方式</p><h3>你想为当前镜头做什么？</h3></div>
      <div className="mode-grid">{modes.map((item) => <button key={item.id} className={`mode-card${mode === item.id ? " selected" : ""}`} aria-pressed={mode === item.id} onClick={() => setMode(item.id)}><span className="mode-icon">{item.icon}</span><strong>{item.title}</strong><small>{item.detail}</small>{mode === item.id && <span className="selected-mark">当前方式</span>}</button>)}</div>

      <div className="composer-grid">
        <div className="composer-main">
          <div className="section-title"><span>输入与创作意图</span><small>所有输入将冻结到 GenerationVariant</small></div>
          <div className="input-slot-row">
            <div className={`media-slot${draftAnchor ? " filled" : ""}`} aria-describedby="media-slot-help">{extractedThumbnail ? <img src={extractedThumbnail} alt={`当前未提交输入：视频${draftRoleLabel}缩略图`} width="220" height="124" decoding="async" /> : <span aria-hidden="true">+</span>}<strong>{draftAnchor ? `${draftRoleLabel}已填入当前草稿` : mode === "R2V" ? "选择参考图片" : "选择视频帧"}</strong><small id="media-slot-help">{mode === "T2V" || mode === "T2I" ? "当前方式不需要图片输入；已提取帧仅保留在未提交草稿。" : draftAnchor ? "FrameAnchor 已真实注册；创建 Variant 前仍可替换。" : "从下方已注册视频提取真实帧，不上传或读取原片。"}</small></div>
            <div className="prompt-field"><label htmlFor="generation-prompt">镜头 Prompt</label><textarea id="generation-prompt" value={promptText} onChange={(event) => setPromptText(event.target.value)} placeholder="描述主体动作、镜头运动、节奏与环境变化…" /><div className="prompt-tools"><span>结构化运镜</span><span>负向约束</span><span>版本化保存</span></div></div>
          </div>
          <section className="generation-submit-panel" aria-labelledby="generation-submit-title">
            <div className="section-title"><span id="generation-submit-title">计划 → 确认 → 提交真实任务</span><small>两阶段提交，不自动运行</small></div>
            <div className="generation-submit-fields">
              <label htmlFor="generation-seed">显式 Seed<input id="generation-seed" type="number" step="1" value={seedText} onChange={(event) => setSeedText(event.target.value)} /></label>
              {mode === "I2V" && <label htmlFor="generation-keyframe">已批准关键帧<select id="generation-keyframe" value={approvedKeyframeId} onChange={(event) => setApprovedKeyframeId(event.target.value)}><option value="">请选择</option>{approvedKeyframeIds.map((mediaVersionId) => <option key={mediaVersionId} value={mediaVersionId}>APPROVED KEYFRAME · {mediaVersionId.slice(0, 12)}</option>)}</select></label>}
              <div className={`capability-truth ${cameraReady ? "ready" : "blocked"}`}><strong>CameraPlan</strong><small>{!requiresCamera ? "图片任务不要求运镜。" : cameraPlan ? `${cameraPlan.mode} · ${cameraPlan.movement} · ${cameraPlan.profile_version_id === profileVersionId ? "Profile 一致" : "需用当前 Profile 重新裁决"}` : "当前镜头没有结构化 CameraPlan；请在下方导演分镜中配置。"}</small></div>
            </div>
            <div className="generation-submit-actions"><button type="button" className="secondary" disabled={!runnable || preflightMutation.isPending} onClick={() => preflightMutation.mutate()}>{preflightMutation.isPending ? "正在建立意图并预检…" : "建立意图并执行只读生成预检"}</button><button type="button" className="primary-action" disabled={!prepared || submitMutation.isPending || Boolean(submitted)} onClick={() => submitMutation.mutate()}>{submitMutation.isPending ? "提交中…" : "确认创建 Variant 与 Job"}</button></div>
            {prepared && !submitted && <p className="frame-feedback success" role="status"><strong>预检 READY，尚未创建 Job。</strong> Plan hash <code>{prepared.plan.plan_hash.slice(0, 16)}</code> · recipe <code>{prepared.plan.recipe_hash.slice(0, 16)}</code></p>}
            {submitted && <p className="frame-feedback success" role="status"><strong>真实任务已持久化：{submitted.state}</strong> Job <code>{submitted.id}</code>；关闭浏览器不会丢失。</p>}
            {(preflightMutation.error || submitMutation.error) && <p className="inline-error" role="alert">{(preflightMutation.error ?? submitMutation.error)?.message}</p>}
            {submittedVariantId && <section className="seed-batch-panel" aria-label="Seed 批量实验"><div className="section-title"><span>同图同词 · Seed 批量实验</span><small>仅生成受限规划，不自动创建 Variant 或 Job</small></div><label htmlFor="seed-batch-input">Seed 列表（逗号分隔）<input id="seed-batch-input" value={seedBatchText} onChange={(event) => setSeedBatchText(event.target.value)} /></label><button type="button" className="secondary" onClick={() => seedBatchMutation.mutate()} disabled={seedBatchMutation.isPending}>{seedBatchMutation.isPending ? "规划中…" : "生成批量实验矩阵"}</button>{seedBatchPlan && <div className="seed-batch-result"><strong>{seedBatchPlan.count} 个独立分支计划</strong><span>父 Variant <code>{seedBatchPlan.parent_variant_id.slice(0, 12)}</code></span><span>每格只改变 explicit_seed</span><span>可复现声明：沿用 Profile determinism</span></div>}{seedBatchMutation.error && <p className="inline-error" role="alert">Seed 批量规划失败：{seedBatchMutation.error.message}</p>}</section>}
          </section>
          <section className="frame-anchor-panel" aria-labelledby="frame-anchor-title">
            <div className="section-title"><span id="frame-anchor-title">从视频取帧并用作输入</span><small>真实 PTS 解析 · 只加载 small 缩略图</small></div>
            {videos.length ? <>
              <div className="frame-source-row">
                <label htmlFor="frame-source-video">已注册视频</label>
                <select id="frame-source-video" value={sourceVideoId} onChange={(event) => { setSourceVideoId(event.target.value); setDraftAnchor(null); }}>
                  {videos.map((item) => <option value={item.media_version_id} key={item.media_version_id}>{item.stage} · {item.media_version_id.slice(0, 12)}</option>)}
                </select>
                {sourceVideoId ? <img src={`/api/v1/media-versions/${encodeURIComponent(sourceVideoId)}/thumbnail?size=small&frame=poster`} alt="所选视频的小尺寸海报缩略图" width="128" height="72" loading="lazy" decoding="async" /> : <span className="media-kind-placeholder" aria-hidden="true">VIDEO</span>}
              </div>
              <div className="frame-actions">
                <button type="button" onClick={() => anchorMutation.mutate("FIRST_FRAME")} disabled={anchorMutation.isPending}>用作首帧</button>
                <label htmlFor="frame-current-time">当前时间（秒）<input id="frame-current-time" type="number" min="0" step="0.001" inputMode="decimal" value={currentTimeSeconds} onChange={(event) => setCurrentTimeSeconds(event.target.value)} /></label>
                <button type="button" onClick={() => anchorMutation.mutate("CURRENT_FRAME")} disabled={anchorMutation.isPending}>用作当前帧</button>
                <button type="button" onClick={() => anchorMutation.mutate("LAST_FRAME")} disabled={anchorMutation.isPending}>用作末帧</button>
              </div>
              {anchorMutation.isPending && <p className="frame-feedback" aria-live="polite">正在解析真实帧并注册 FrameAnchor…</p>}
              {anchorMutation.error && <p className="inline-error" role="alert">取帧失败：{anchorMutation.error.message}</p>}
              {draftAnchor && !anchorMutation.isPending && <p className="frame-feedback success" aria-live="polite"><strong>{frameActionLabels[draftAnchor.role_hint as FrameAction] ?? "视频帧"}已注册并填入当前未提交输入槽。</strong> 解析到第 {draftAnchor.source_frame_index} 帧 / {(draftAnchor.resolved_time_us / 1_000_000).toFixed(3)} 秒 · SHA {draftAnchor.sha256.slice(0, 12)} · {draftAnchor.extraction_method}</p>}
              {draftAnchor && <div className="keyframe-candidate-action"><button type="button" className="secondary" onClick={() => keyframeMutation.mutate()} disabled={!selectedShotId || keyframeMutation.isPending}>{keyframeMutation.isPending ? "正在创建候选…" : "创建关键帧候选并进入人工审核"}</button><small>{selectedShotId ? "仅创建不可变候选，不会自动选择或批准。" : "请先选择镜头，候选必须归属于真实镜头。"}</small></div>}
              {keyframeMutation.error && <p className="inline-error" role="alert">创建关键帧候选失败：{keyframeMutation.error.message}</p>}
            </> : <p className="empty-state">当前项目没有待处理的已注册 VIDEO 版本；不会用示例或 Mock 媒体替代。</p>}
          </section>
          <div className="variant-strip"><strong>创作分支</strong><button className="chip active">基础生成</button><button className="chip">同图同词 · 新 seed</button><button className="chip">改 Prompt</button><button className="chip">换图</button><button className="chip" disabled>首尾帧 · 待能力发布</button></div>
        </div>

        <aside className="capability-panel">
          <div className="section-title"><span>本地模型与能力</span><small>由已发布 Profile 决定</small></div>
          <label htmlFor="generation-profile">Capability Profile</label>
          <select id="generation-profile" value={profileVersionId} onChange={(event) => setProfileVersionId(event.target.value)} disabled={!eligibleProfiles.length}>
            {!eligibleProfiles.length && <option value="">没有匹配的本地 Profile</option>}
            {eligibleProfiles.map((profile) => <option key={profile.version_id} value={profile.version_id}>{profile.title} · {profile.status}</option>)}
          </select>
          {selected ? <div className={`capability-truth ${runnable ? "ready" : "blocked"}`}><strong>{runnable ? "可进入生产预检" : "尚不能提交"}</strong><span>{selected.code}</span><small>{!selectedShotId ? "必须先绑定真实镜头。" : selected.status === "PUBLISHED" ? "Profile 已发布；提交前仍会运行资源预检。" : `${selected.status}：需要先完成真实样片、测试并发布。`}</small></div> : <div className="capability-truth blocked"><strong>能力未配置</strong><small>该生成方式没有可选择的本地 Profile，不会静默改用其他模型。</small></div>}
          <dl className="resource-summary"><div><dt>Runtime</dt><dd>ComfyUI loopback</dd></div><div><dt>GPU 策略</dt><dd>1 个 H3 Worker</dd></div><div><dt>首尾帧</dt><dd>实验性 / 未发布</dd></div><div><dt>历史</dt><dd>只新增，不覆盖</dd></div></dl>
          <div className="preflight-summary" aria-label="生成预检摘要"><span><i className={runnable ? "ready" : "blocked"} />镜头绑定</span><span><i className={selected?.status === "PUBLISHED" ? "ready" : "blocked"} />Profile 发布</span><span><i />运行时待预检</span></div>
          <button className={runnable ? "primary-action" : "secondary profile-link"} onClick={onOpenProfiles}>{runnable ? "查看能力并进入预检" : "去模型与能力解决阻塞"}</button>
          <small className="action-help">{h3?.status === "PASS" ? "本机 H3 layout 已通过；运行状态仍在提交预检时读取。" : "Runtime 状态会在预检中再次确认。"}</small>
        </aside>
      </div>
    </section>
    <section className="candidate-shelf" aria-labelledby="candidate-shelf-title">
      <div className="candidate-shelf-heading"><div><p className="eyebrow">候选与审核</p><h3 id="candidate-shelf-title">真实代理候选</h3></div><span className="status-pill neutral">{videos.length} 个待处理视频</span></div>
      <p>这里只加载 small 海报缩略图，不自动播放原片。选择 winner 与人工批准严格分开；机器检查不能替代身份、动作和连续性判断。</p>
      {videos.length ? <div className="candidate-grid">{videos.slice(0, 8).map((item, index) => <article className="candidate-card" key={item.media_version_id}>
        <div className="candidate-poster"><img src={`/api/v1/media-versions/${encodeURIComponent(item.media_version_id)}/thumbnail?size=small&frame=poster`} alt={`代理候选 ${index + 1} 的低码率海报缩略图`} width="320" height="180" loading="lazy" decoding="async" /><span>TAKE {index + 1}</span></div>
        <div className="candidate-card-body"><strong>{item.stage} · {item.media_kind}</strong><code>{item.media_version_id.slice(0, 12)}</code><div className="candidate-state"><span className={item.decision ? "status-pill" : "status-pill neutral"}>{item.decision ?? "待人工审核"}</span>{item.is_stale ? <span className="blocker-text">STALE</span> : <span>谱系已登记</span>}</div><button type="button" className="secondary" onClick={() => onOpenReviews?.(item.media_version_id)}>打开审核</button></div>
      </article>)}</div> : <p className="empty-state">当前项目还没有真实视频候选；不会用示例或 Mock 卡片占位。</p>}
    </section>
  </div>;
}
