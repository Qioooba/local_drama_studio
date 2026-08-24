import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  archiveShotGroup, assignShotScene, createShotGroup, getShotGroupWorkspace,
  reorderShotGroups, replaceShotGroupMembers, type ShotGroup, type ShotGroupKind, type ShotGroupShot,
} from "./shotGroupsApi";
import "./shot-groups.css";
import { nextOrdinalCode } from "../shared/autoCode";

const KINDS: Array<{ value: ShotGroupKind; label: string }> = [
  { value: "BEAT", label: "节拍" }, { value: "DIALOGUE", label: "对白" },
  { value: "ACTION", label: "动作" }, { value: "MONTAGE", label: "蒙太奇" },
  { value: "CUSTOM", label: "自定义" },
];

export function ShotGroupPlanner({ episodeId }: { episodeId: string }) {
  const queryClient = useQueryClient();
  const key = ["shot-group-workspace", episodeId];
  const query = useQuery({ queryKey: key, queryFn: () => getShotGroupWorkspace(episodeId) });
  const refresh = () => queryClient.invalidateQueries({ queryKey: key });
  const mutation = useMutation({ mutationFn: (work: () => Promise<unknown>) => work(), onSuccess: refresh });
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<ShotGroupKind>("BEAT");
  const [sceneId, setSceneId] = useState("");
  const workspace = query.data;
  const activeGroups = useMemo(() => workspace?.groups.filter((group) => group.status === "ACTIVE") ?? [], [workspace]);
  const code = nextOrdinalCode(kind, activeGroups.map((group) => group.code));

  const submitGroup = () => mutation.mutate(async () => {
    await createShotGroup(episodeId, { kind, code, title: title.trim(), scene_id: sceneId || null });
    setTitle("");
  });
  const moveGroup = (index: number, offset: number) => {
    const target = index + offset;
    if (target < 0 || target >= activeGroups.length) return;
    const ordered = [...activeGroups];
    [ordered[index], ordered[target]] = [ordered[target], ordered[index]];
    mutation.mutate(() => reorderShotGroups(episodeId, ordered));
  };
  const setMembership = (shot: ShotGroupShot, targetId: string) => mutation.mutate(async () => {
    const source = activeGroups.find((group) => group.id === shot.group_id);
    if (!targetId) {
      if (source) await replaceShotGroupMembers(source, source.members.filter((member) => member.shot_id !== shot.id).map((member) => member.shot_id));
      return;
    }
    const target = activeGroups.find((group) => group.id === targetId);
    if (!target) return;
    const ids = target.members.map((member) => member.shot_id).filter((id) => id !== shot.id);
    await replaceShotGroupMembers(target, [...ids, shot.id]);
  });
  const moveMember = (group: ShotGroup, shotId: string, offset: number) => {
    const ids = group.members.map((member) => member.shot_id);
    const index = ids.indexOf(shotId), target = index + offset;
    if (index < 0 || target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    mutation.mutate(() => replaceShotGroupMembers(group, ids));
  };

  if (query.isPending) return <section className="subpanel"><p className="empty-state">正在读取场景与镜头分组…</p></section>;
  if (query.error) return <section className="subpanel" role="alert">{String(query.error)}</section>;
  if (!workspace) return null;
  const shotById = new Map(workspace.shots.map((shot) => [shot.id, shot]));
  return <section className="subpanel shot-group-planner" aria-labelledby="shot-groups-title">
    <div className="panel-heading"><div><p className="eyebrow">连续镜头组</p><h4 id="shot-groups-title">场景归属与镜头组编排</h4></div><span className="status-pill">{activeGroups.length} 组 · {workspace.shots.length} 镜</span></div>
    <p className="muted">未知场景保持为空；分组只组织镜头，不删除候选和生产历史。上移/下移按钮提供键盘可用的排序替代。</p>
    <form className="shot-group-create" onSubmit={(event) => { event.preventDefault(); submitGroup(); }}>
      <div className="field-fact"><span>编号</span><strong>{code}</strong><small>按类型和现有分组自动生成</small></div>
      <label>标题<input value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} placeholder="开场冲突" /></label>
      <label>类型<select value={kind} onChange={(event) => setKind(event.target.value as ShotGroupKind)}>{KINDS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
      <label>场景<select value={sceneId} onChange={(event) => setSceneId(event.target.value)}><option value="">暂不确定</option>{workspace.scenes.map((scene) => <option key={scene.id} value={scene.id}>{scene.code} · {scene.title}</option>)}</select></label>
      <button type="submit" className="primary-action" disabled={!title.trim() || mutation.isPending}>{mutation.isPending ? "创建中…" : "创建分组"}</button>
    </form>
    {mutation.error && <p className="error-text" role="alert">{String(mutation.error)}</p>}
    <div className="shot-group-grid">
      {activeGroups.map((group, groupIndex) => <article key={group.id} className="shot-group-card">
        <header><div><span className="status-pill">{group.kind}</span><strong>{group.code} · {group.title}</strong></div><div className="shot-group-actions"><button type="button" className="secondary" aria-label={`${group.code} 上移`} disabled={groupIndex === 0 || mutation.isPending} onClick={() => moveGroup(groupIndex, -1)}>↑</button><button type="button" className="secondary" aria-label={`${group.code} 下移`} disabled={groupIndex === activeGroups.length - 1 || mutation.isPending} onClick={() => moveGroup(groupIndex, 1)}>↓</button><button type="button" className="secondary" onClick={() => mutation.mutate(() => archiveShotGroup(group))}>归档</button></div></header>
        <ol>{group.members.map((member, memberIndex) => { const shot = shotById.get(member.shot_id); return <li key={member.shot_id}><span>{shot?.code ?? member.shot_id.slice(0, 8)}</span><button type="button" className="secondary" aria-label="镜头上移" disabled={memberIndex === 0} onClick={() => moveMember(group, member.shot_id, -1)}>↑</button><button type="button" className="secondary" aria-label="镜头下移" disabled={memberIndex === group.members.length - 1} onClick={() => moveMember(group, member.shot_id, 1)}>↓</button></li>; })}</ol>
        {!group.members.length && <p className="empty-state">尚未指派镜头</p>}
      </article>)}
    </div>
    <div className="shot-assignment-table" role="table" aria-label="镜头场景与分组指派">
      <div className="shot-assignment-head" role="row"><strong>镜头</strong><strong>类型 / 时长</strong><strong>场景</strong><strong>连续镜头组</strong></div>
      {workspace.shots.map((shot) => <div className="shot-assignment-row" role="row" key={shot.id}>
        <strong>{shot.code}</strong><span>{shot.shot_type} · {(shot.target_duration_ms / 1000).toFixed(1)}s</span>
        <select aria-label={`${shot.code} 场景`} value={shot.scene_id ?? ""} disabled={mutation.isPending} onChange={(event) => { const nextSceneId = event.currentTarget.value || null; mutation.mutate(() => assignShotScene(shot, nextSceneId)); }}><option value="">未确定</option>{workspace.scenes.map((scene) => <option key={scene.id} value={scene.id}>{scene.code} · {scene.title}</option>)}</select>
        <select aria-label={`${shot.code} 分组`} value={shot.group_id ?? ""} disabled={mutation.isPending} onChange={(event) => setMembership(shot, event.target.value)}><option value="">未分组</option>{activeGroups.map((group) => <option key={group.id} value={group.id}>{group.code} · {group.title}</option>)}</select>
      </div>)}
      {!workspace.shots.length && <p className="empty-state">当前分集尚无镜头。</p>}
    </div>
  </section>;
}
