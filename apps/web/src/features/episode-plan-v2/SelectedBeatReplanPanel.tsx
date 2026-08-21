import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getShotGroupWorkspace } from "./shotGroupsApi";
import {
  applyBeatReplan, planBeatReplan,
  type BeatReplanAction, type BeatReplanPlan,
} from "./beatReplanApi";
import { listScriptBreakdownDrafts } from "../../generated/api";
import { queryKeys } from "../../query/queryKeys";

const ACTION_LABEL: Record<BeatReplanAction, string> = {
  KEEP: "保留", ADD: "新增", MODIFY: "修改", DELETE: "移出并归档", PROTECTED: "冻结保护",
};

function description(fields: Record<string, unknown> | undefined) {
  if (!fields) return "—";
  return String(fields.visual || fields.action || fields.summary || "未提供画面描述");
}

export function SelectedBeatReplanPanel({ projectId, episodeId }: { projectId: string; episodeId: string }) {
  const queryClient = useQueryClient();
  const workspaceKey = ["shot-group-workspace", episodeId];
  const workspace = useQuery({ queryKey: workspaceKey, queryFn: () => getShotGroupWorkspace(episodeId) });
  const drafts = useQuery({
    queryKey: queryKeys.scriptBreakdown.all(projectId),
    queryFn: () => listScriptBreakdownDrafts(projectId),
    select: (response) => response.items,
  });
  const groups = useMemo(() => workspace.data?.groups.filter((item) => item.status === "ACTIVE" && item.kind === "BEAT") ?? [], [workspace.data]);
  const [groupId, setGroupId] = useState("");
  const [draftId, setDraftId] = useState("");
  const [sceneNo, setSceneNo] = useState(1);
  const [plan, setPlan] = useState<BeatReplanPlan | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const selectedGroup = groups.find((item) => item.id === groupId);
  const selectedDraft = drafts.data?.find((item) => item.id === draftId);
  const scenes = selectedDraft?.draft.scenes ?? [];

  const preview = useMutation({ mutationFn: async () => {
    if (!selectedGroup || !draftId) throw new Error("请先选择 Beat 与 AI 草稿");
    return planBeatReplan(episodeId, selectedGroup.id, {
      draft_id: draftId, proposal_scene_no: sceneNo, expected_group_revision: selectedGroup.revision,
    });
  }, onSuccess: (next) => { setPlan(next); setIdempotencyKey(crypto.randomUUID()); } });
  const apply = useMutation({ mutationFn: async () => {
    if (!plan || !selectedGroup) throw new Error("必须先生成并核对差异预览");
    return applyBeatReplan(episodeId, selectedGroup.id, {
      draft_id: plan.draft_id, proposal_scene_no: plan.proposal_scene_no,
      expected_group_revision: plan.group_revision, expected_plan_hash: plan.plan_hash,
      idempotency_key: idempotencyKey,
    });
  }, onSuccess: async () => {
    setPlan(null);
    await queryClient.invalidateQueries({ queryKey: workspaceKey });
    await queryClient.invalidateQueries({ queryKey: ["storyboard-workspace", episodeId] });
  } });

  const resetPreview = () => { setPlan(null); preview.reset(); apply.reset(); };
  return <section className="subpanel selected-beat-replan" aria-labelledby="beat-replan-title">
    <div className="panel-heading"><div><p className="eyebrow">人工审核 · Selected Beat AI Replan</p><h4 id="beat-replan-title">只重排选定 Beat</h4></div><span className="status-pill">plan → diff → apply</span></div>
    <p className="muted">预览不会写入；应用只触碰所选 Beat 的镜头。冻结镜头始终保留，删除项只归档，旧 revision 与候选不会删除。</p>
    <div className="shot-group-create">
      <label>选定 Beat<select value={groupId} onChange={(event) => { setGroupId(event.target.value); resetPreview(); }}><option value="">请选择</option>{groups.map((group) => <option key={group.id} value={group.id}>{group.code} · {group.title} · r{group.revision}</option>)}</select></label>
      <label>AI 拆解草稿<select value={draftId} onChange={(event) => { setDraftId(event.target.value); resetPreview(); }}><option value="">请选择</option>{drafts.data?.map((draft) => <option key={draft.id} value={draft.id}>{draft.source_document_title} · {draft.status}</option>)}</select></label>
      <label>建议场次<select value={sceneNo} onChange={(event) => { setSceneNo(Number(event.target.value)); resetPreview(); }}>{scenes.map((scene, index) => {
        const sNo = typeof scene.scene_no === "number" ? scene.scene_no : index + 1;
        const summary = typeof scene.summary === "string" ? scene.summary : "未命名场次";
        return <option key={sNo} value={sNo}>{sNo} · {summary}</option>;
      })}</select></label>
      <button type="button" disabled={!selectedGroup || !draftId || preview.isPending} onClick={() => preview.mutate()}>{preview.isPending ? "生成中…" : "生成差异预览"}</button>
    </div>
    {(workspace.error || drafts.error || preview.error || apply.error) && <p className="inline-error" role="alert">{String(workspace.error || drafts.error || preview.error || apply.error)}</p>}
    {plan && <div className="beat-replan-preview">
      <div className="panel-heading"><strong>{plan.group_code} · {plan.group_title}</strong><span className="status-pill">范围外触碰 {plan.scope.outside_group_shots_touched}</span></div>
      <p className="review-guidance">保留 {plan.summary.KEEP} · 新增 {plan.summary.ADD} · 修改 {plan.summary.MODIFY} · 归档 {plan.summary.DELETE} · 冻结保护 {plan.summary.PROTECTED}</p>
      {plan.issues.map((issue) => <p key={issue.code} className="inline-error">{issue.message}</p>)}
      <div className="shot-assignment-table" role="table" aria-label="Beat Replan 差异">
        <div className="shot-assignment-head" role="row"><strong>动作</strong><strong>当前</strong><strong>建议</strong><strong>安全规则</strong></div>
        {plan.diff.map((item, index) => <div className="shot-assignment-row" role="row" key={`${item.shot_id ?? "new"}-${index}`}>
          <strong>{ACTION_LABEL[item.action]}</strong>
          <span>{item.before?.code ?? "新镜头"} · {description(item.before?.fields)}</span>
          <span>{item.after?.code ?? "归档"} · {description(item.after?.fields)}</span>
          <small>{item.reason ?? (item.action === "MODIFY" ? "新建 revision，保留旧版本" : item.action === "ADD" ? "创建新镜头" : "不改事实")}</small>
        </div>)}
      </div>
      <button type="button" disabled={!plan.valid || apply.isPending} onClick={() => apply.mutate()}>{apply.isPending ? "应用中…" : "确认应用以上差异"}</button>
    </div>}
    {apply.data && <p className="frame-feedback success">应用完成：新增 {apply.data.created_shot_ids.length}，修改 {apply.data.modified_shot_ids.length}，归档 {apply.data.archived_shot_ids.length}，冻结保护 {apply.data.protected_shot_ids.length}；删除历史候选 {apply.data.historical_variants_deleted}。</p>}
    {!groups.length && !workspace.isPending && <p className="empty-state">当前没有 ACTIVE 的 Beat 镜头组，请先在下方建立镜头组。</p>}
  </section>;
}
