import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import { queryKeys } from "../../query/queryKeys";
import { CapabilityPicker, useCapabilityOptions } from "../model-config/CapabilityPicker";
import { approveAdaptationPlan, getAdaptationAnalysisReadiness, getAdaptationMaterializationPreflight, getAdaptationPlanWorkspace, materializeAdaptationPlan, prepareAdaptationAnalysisManifest, submitAdaptationAnalysisRun } from "./adaptationPlanClient";
import "./adaptation-planning.css";

function modeTitle(mode: string) {
  if (mode === "COMPLETE_WORK") return "连续剧规划";
  if (mode === "SERIAL_INCREMENTAL") return "续接规划";
  if (mode === "SINGLE_EPISODE") return "单集规划";
  return "预分段剧本规划";
}

function idempotencyKey() {
  return globalThis.crypto?.randomUUID?.() ?? "adaptation-run-" + Date.now().toString(36);
}

export function AdaptationPlanWorkspacePage() {
  const { projectId, planId } = useParams();
  const queryClient = useQueryClient();
  const [profileVersionId, setProfileVersionId] = useState("");
  const [allowRemoteOutbound, setAllowRemoteOutbound] = useState(false);
  const [confirmAppend, setConfirmAppend] = useState(false);
  const profileOptions = useCapabilityOptions("LLM_STORY_PARSE", { projectId });
  const workspace = useQuery({
    queryKey: queryKeys.adaptationPlanning.workspace(planId ?? ""),
    queryFn: () => getAdaptationPlanWorkspace(planId!),
    enabled: Boolean(planId),
  });
  const manifest = useMutation({
    mutationFn: () => prepareAdaptationAnalysisManifest(planId!),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.adaptationPlanning.workspace(planId ?? "") });
    },
  });
  const readiness = useQuery({
    queryKey: ["adaptation-analysis-readiness", planId ?? "", profileVersionId],
    queryFn: () => getAdaptationAnalysisReadiness(planId!, profileVersionId),
    enabled: Boolean(planId && profileVersionId),
  });
  const submit = useMutation({
    mutationFn: () => submitAdaptationAnalysisRun(
      planId!,
      { profile_version_id: profileVersionId, allow_remote_outbound: allowRemoteOutbound },
      idempotencyKey(),
    ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.adaptationPlanning.workspace(planId ?? "") });
    },
  });
  const approve = useMutation({
    mutationFn: () => approveAdaptationPlan(planId!, workspace.data!.revision.content_sha256),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.adaptationPlanning.workspace(planId ?? "") });
    },
  });
  const planStatus = workspace.data?.plan.artifact_status;
  const materialization = useQuery({
    queryKey: ["adaptation-materialization-preflight", planId],
    queryFn: () => getAdaptationMaterializationPreflight(planId!),
    enabled: planStatus === "APPROVED" || planStatus === "MATERIALIZED",
  });
  const publish = useMutation({
    mutationFn: () => materializeAdaptationPlan(planId!, workspace.data!.revision.content_sha256, idempotencyKey()),
    onSuccess: async () => {
      setConfirmAppend(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.adaptationPlanning.workspace(planId ?? "") }),
        queryClient.invalidateQueries({ queryKey: ["adaptation-materialization-preflight", planId] }),
        queryClient.invalidateQueries({ queryKey: queryKeys.seasons.list(projectId ?? "") }),
      ]);
    },
  });

  if (!projectId || !planId) return <p className="inline-error" role="alert">缺少项目或改编规划上下文。</p>;
  if (workspace.isPending) return <main className="v2-page adaptation-planning-page"><p role="status">正在恢复改编规划工作区…</p></main>;
  if (workspace.isError || !workspace.data) return <main className="v2-page adaptation-planning-page"><p className="inline-error" role="alert">改编规划读取失败。<button type="button" className="link-button" onClick={() => void workspace.refetch()}>重试</button></p></main>;

  const data = workspace.data;
  const isApproved = data.plan.artifact_status === "APPROVED";
  const isMaterialized = data.plan.artifact_status === "MATERIALIZED";
  const range = data.revision.diagnosis.estimated_episode_range;
  return <main className="v2-page adaptation-planning-page">
    <header className="adaptation-planning-page__header">
      <div>
        <p className="eyebrow">故事 / 改编规划 / 修订 v{data.revision.revision_no}</p>
        <h2>{data.plan.source_title}</h2>
        <p className="muted">{modeTitle(data.plan.mode)} · 原稿 {data.plan.source_name} · {isMaterialized ? "已追加发布为真实项目结构" : "尚未发布到真实季度或分集"}</p>
      </div>
      <Link className="secondary v2-inline-link" to={routes.adaptationPlans(projectId)}>返回规划列表</Link>
    </header>

    <section className="adaptation-workspace-summary" aria-label="规划状态">
      <div><span>规划状态</span><strong>{isMaterialized ? "已发布" : isApproved ? "已批准，待发布" : data.plan.artifact_status === "IN_REVIEW" ? "待人工审核" : "草稿"}</strong></div>
      <div><span>分析运行</span><strong>{data.run.status === "QUEUED" ? "已入队，等待本机 Worker" : data.run.status === "RUNNING" ? "正在分层分析" : data.analysis_nodes.length ? `已编排 ${data.analysis_nodes.length} 个节点` : "尚未编排节点"}</strong></div>
      <div><span>建议规模</span><strong>约 {range.minimum}–{range.maximum} 集</strong></div>
      <div><span>当前已计划分集</span><strong>{data.episodes.length} 集</strong></div>
    </section>

    <section className="adaptation-panel" aria-labelledby="adaptation-next-step-title">
      <div className="adaptation-section-heading">
        <span>下一步</span>
        <div><h3 id="adaptation-next-step-title">{isMaterialized ? "发布结果已固定" : isApproved ? "发布已批准的规划" : "分层故事分析"}</h3><p>{isMaterialized ? "本修订已形成真实项目季集，并保留了逐集来源证据链接。" : isApproved ? "规划已通过人工审核；如需写入项目结构，必须在下方完成单独的追加发布确认。" : "本次已冻结来源范围、时长与策略。下一阶段会以“原稿分块 → 故事弧 → 分集边界 → 校验”的可恢复 Job DAG 生成待审核规划；不会直接创建镜头。"}</p></div>
      </div>
      <div className="adaptation-next-step">
        <p>{data.next_action === "MATERIALIZED"
          ? "真实项目结构已追加创建；此处的规划修订仍可追溯到原始来源范围。"
          : data.next_action === "PUBLISH_PLAN"
          ? "规划已批准，但尚未改变项目结构。请先阅读发布预检，再明确确认追加发布。"
          : data.next_action === "MONITOR_ANALYSIS"
          ? "分析 Job 已持久化到本机队列。刷新此页可查看节点与审核状态；当前仍不会生成媒体，也不会物化真实项目结构。"
          : data.analysis_nodes.length
          ? "节点清单已冻结。下一步需要选择已验证的文本规划 Profile，并确认隐私范围、预估成本与任务数后才会提交模型 Job。"
          : "先基于冻结原稿范围生成可恢复的 Map/Reduce 节点清单；此操作不会调用模型、不会发送原稿，也不会创建真实季度或分集。"}</p>
        {!isApproved && !isMaterialized ? <button
          type="button"
          className="primary"
          disabled={data.analysis_nodes.length > 0 || manifest.isPending || data.next_action === "MONITOR_ANALYSIS"}
          onClick={() => manifest.mutate()}
        >
          {manifest.isPending ? "正在编排分析节点…" : data.next_action === "MONITOR_ANALYSIS" ? "分析任务已入队" : data.analysis_nodes.length ? "分析节点已编排" : "生成分层分析节点"}
        </button> : null}
        {manifest.isError ? <p className="inline-error" role="alert">无法生成节点清单。<button type="button" className="link-button" onClick={() => manifest.reset()}>关闭提示</button></p> : null}
        <small>模型 Profile 绑定与实际 LLM 提交仍需单独确认，当前步骤的 LLM 调用数为 0。</small>
      </div>
    </section>

    {data.analysis_nodes.length ? <section className="adaptation-panel" aria-labelledby="adaptation-nodes-title">
      <div className="adaptation-section-heading">
        <span>运行图</span>
        <div><h3 id="adaptation-nodes-title">已冻结的分析节点</h3><p>每个节点持有不可变来源偏移和输入指纹；后续模型调用只能消费这份清单。</p></div>
      </div>
      <ol className="adaptation-node-list">
        {data.analysis_nodes.map((node) => <li key={node.node_key}>
          <strong>{node.stage}</strong>
          <span>{node.core_source_start === null ? "汇总节点" : `正文偏移 ${node.core_source_start}–${node.core_source_end}`}</span>
          <em>{node.state}</em>
        </li>)}
      </ol>
    </section> : null}

    {data.analysis_nodes.length && !isApproved && !isMaterialized ? <section className="adaptation-panel" aria-labelledby="adaptation-profile-title">
      <div className="adaptation-section-heading">
        <span>运行</span>
        <div><h3 id="adaptation-profile-title">选择文本规划 Profile</h3><p>后续运行必须显式固定一个已发布的 Profile；不会静默回退到默认模型。</p></div>
      </div>
      <CapabilityPicker
        capability="LLM_STORY_PARSE"
        value={profileVersionId}
        onChange={(value) => { setProfileVersionId(value); setAllowRemoteOutbound(false); }}
        query={profileOptions}
        migrationBusinessSurface="story"
        label="本次分层分析使用的 Profile"
        description="此处先进行只读运行预检；真正提交前会把选定版本冻结到运行快照。"
        allowAuto={false}
      />
      {readiness.isPending ? <p className="muted" role="status">正在核验节点规模、Profile 契约和数据出境范围…</p> : null}
      {readiness.isError ? <p className="inline-error" role="alert">运行预检失败。<button type="button" className="link-button" onClick={() => void readiness.refetch()}>重试</button></p> : null}
      {readiness.data ? <div className="adaptation-readiness" role="status">
        <strong>{readiness.data.execution.state === "READY" ? "可进入提交确认" : "暂不可提交"}</strong>
        <span>预计 {readiness.data.execution.map_node_count} 个文本分块、约 {readiness.data.execution.estimated_input_tokens.toLocaleString()} 输入 token。</span>
        {readiness.data.execution.requires_remote_outbound_confirmation ? <span>所选 Provider 为远程服务；提交时必须逐次明确确认原稿出境。</span> : <span>所选 Provider 为本机服务；当前预检没有发出网络请求。</span>}
        {readiness.data.blockers.map((blocker) => <span className="inline-error" key={blocker.code}>{blocker.message}</span>)}
        {readiness.data.execution.state === "READY" && data.next_action !== "MONITOR_ANALYSIS" ? <div className="adaptation-submit-run">
          {readiness.data.execution.requires_remote_outbound_confirmation ? <label>
            <input type="checkbox" checked={allowRemoteOutbound} onChange={(event) => setAllowRemoteOutbound(event.target.checked)} />
            我确认本次会将冻结原稿分块发送给所选远程 Provider，仅用于该规划运行。
          </label> : null}
          <button
            type="button"
            className="primary"
            disabled={submit.isPending || (readiness.data.execution.requires_remote_outbound_confirmation && !allowRemoteOutbound)}
            onClick={() => submit.mutate()}
          >{submit.isPending ? "正在提交分析 Job…" : "确认并提交分层分析"}</button>
          {submit.isError ? <p className="inline-error" role="alert">提交失败：{String(submit.error)}</p> : null}
        </div> : null}
      </div> : null}
    </section> : null}

      {data.episodes.length ? <section className="adaptation-panel" aria-labelledby="adaptation-episodes-title">
      <div className="adaptation-section-heading">
        <span>审阅</span>
        <div><h3 id="adaptation-episodes-title">待审核分集候选</h3><p>这些分集仍属于当前改编规划修订。每集保留来源证据，但尚未成为真实项目分集。</p></div>
      </div>
      <ol className="adaptation-episode-list">
        {data.episodes.map((episode) => <li key={episode.id}>
          <span className="adaptation-episode-list__ordinal">{String(episode.display_ordinal).padStart(2, "0")}</span>
          <div><strong>{episode.title}</strong><p>{episode.logline}</p></div>
          <small>{Math.round((episode.estimated_duration_ms ?? episode.target_duration_ms) / 1000)} 秒 · {episode.evidence_count} 条来源证据 · {episode.review_state}</small>
        </li>)}
      </ol>
      {data.plan.artifact_status === "IN_REVIEW" ? <div className="adaptation-review-action">
        <div><strong>批准当前规划修订</strong><p>批准只确认这份待审核规划；不会立即写入真实季度、分集、场次或镜头。</p></div>
        <button type="button" className="primary" disabled={approve.isPending} onClick={() => approve.mutate()}>{approve.isPending ? "正在批准…" : "批准规划草稿"}</button>
        {approve.isError ? <p className="inline-error" role="alert">批准失败：{String(approve.error)}</p> : null}
      </div> : null}
    </section> : null}

    {(isApproved || isMaterialized) ? <section className="adaptation-panel" aria-labelledby="adaptation-publish-title">
      <div className="adaptation-section-heading">
        <span>发布</span>
        <div><h3 id="adaptation-publish-title">追加为真实季集</h3><p>发布会新增季和分集，绝不覆盖现有结构；候选集的来源范围会随真实分集一并保留。</p></div>
      </div>
      {materialization.isPending ? <p className="muted" role="status">正在核验现有项目结构与发布清单…</p> : null}
      {materialization.isError ? <p className="inline-error" role="alert">无法生成发布预检。<button type="button" className="link-button" onClick={() => void materialization.refetch()}>重试</button></p> : null}
      {materialization.data ? <div className="adaptation-publish-action">
        <dl className="adaptation-facts">
          <div><dt>现有真实结构</dt><dd>{materialization.data.existing_structure.season_count} 季 / {materialization.data.existing_structure.episode_count} 集</dd></div>
          <div><dt>本次将新增</dt><dd>{materialization.data.would_create.season_count} 季 / {materialization.data.would_create.episode_count} 集</dd></div>
          <div><dt>发布策略</dt><dd>仅追加，不覆盖</dd></div>
          <div><dt>来源证据</dt><dd>随分集保留</dd></div>
        </dl>
        {materialization.data.already_materialized || isMaterialized ? <p className="adaptation-publish-success" role="status">这份规划修订已经发布为真实季集；重复提交不会再新增内容。<Link to={routes.projectHome(projectId)}>前往项目首页查看新分集</Link></p> : materialization.data.ready ? <div className="adaptation-submit-run">
          <label><input type="checkbox" checked={confirmAppend} onChange={(event) => setConfirmAppend(event.target.checked)} />我确认本次只会追加上述真实季集，不会覆盖项目内已有内容。</label>
          <button type="button" className="primary" disabled={!confirmAppend || publish.isPending} onClick={() => publish.mutate()}>{publish.isPending ? "正在发布真实结构…" : "确认并追加发布"}</button>
          {publish.isError ? <p className="inline-error" role="alert">发布失败：{String(publish.error)}</p> : null}
        </div> : <div className="inline-error" role="alert">{materialization.data.blockers.map((blocker) => blocker.message).join("；")}</div>}
      </div> : null}
    </section> : null}

    <section className="adaptation-panel" aria-labelledby="adaptation-frozen-title">
      <div className="adaptation-section-heading">
        <span>依据</span>
        <div><h3 id="adaptation-frozen-title">冻结的规划依据</h3><p>每次后续分析和审核都会使用这份不可变修订快照，而不是重新读取或覆盖原稿。</p></div>
      </div>
      <dl className="adaptation-facts">
        <div><dt>正文范围</dt><dd>第 {data.revision.source_scope.source_paragraph_start}–{data.revision.source_scope.source_paragraph_end} 段</dd></div>
        <div><dt>正文字符</dt><dd>{data.revision.diagnosis.character_count.toLocaleString()}</dd></div>
        <div><dt>章节候选</dt><dd>{data.revision.diagnosis.chapter_count}</dd></div>
        <div><dt>单集目标</dt><dd>{Math.round(data.revision.diagnosis.target_duration_ms / 1000)} 秒</dd></div>
      </dl>
    </section>
  </main>;
}
