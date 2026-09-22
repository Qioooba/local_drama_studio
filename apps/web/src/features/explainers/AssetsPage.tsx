/**
 * 人物与风格 (assets): channel profile snapshot, entities with real/fictional
 * marking and time-state revisions, reference slots and impact preview.
 */

import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { Panel, SettingRow, StateNotice, type PageState } from "./components";
import { useExplainerAssets } from "./useExplainerQueries";
import "./explainers.css";

const ENTITY_TYPE_LABELS: Record<string, string> = {
  REAL_PERSON: "真实人物",
  FICTIONAL_CHARACTER: "虚构人物",
  GROUP: "群体",
  LOCATION: "地点",
  PROP: "道具",
  ORGANIZATION: "机构",
  CONCEPT: "概念",
};

export function ExplainerAssetsPage() {
  const { projectId = "" } = useParams();
  const assets = useExplainerAssets(projectId);
  const entities = (assets.data?.entities ?? []) as Array<Record<string, unknown>>;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = entities.find((entity) => String(entity.id) === selectedId) ?? entities[0] ?? null;

  const state = useMemo<PageState | null>(() => {
    if (assets.isPending) return { kind: "loading", message: "正在载入人物与风格…" };
    if (assets.isError) return { kind: "failed", title: "无法载入资产", body: assets.error instanceof Error ? assets.error.message : "未知错误" };
    if (entities.length === 0) {
      return {
        kind: "empty",
        title: "还没有人物与场景资产",
        body: "事实提取完成后会建立实体记录；每个镜头引用身份包 revision、服装 revision、场景 revision、道具 revision 与风格 revision。",
      };
    }
    const unbound = entities.filter((entity) => Number(entity.beat_reference_count ?? 0) === 0);
    if (unbound.length > 0) {
      return {
        kind: "partial",
        title: "部分实体还没有镜头引用",
        body: `${unbound.length} 个实体尚未绑定到任何画面段；绑定会校验参考图槽位是否被工作流真实消费。`,
      };
    }
    return null;
  }, [assets.error, assets.isError, assets.isPending, entities]);

  const profile = assets.data?.channel_profile_version as Record<string, unknown> | null | undefined;

  return <div className="explainer-page">
    <Panel title="栏目风格" subtitle="整片引用一个已固定的风格版本；修改会产生新版本。">
      {profile ? (
        <>
          <div className="explainer-actions">
            <span className="badge blue">风格模板 v{String(profile.version_no ?? "?")}</span>
            <span className="badge">状态 {String(profile.status ?? "")}</span>
            <span className="badge">哈希 {String(profile.content_hash ?? "").slice(0, 12)}…</span>
          </div>
          <div className="explainer-form-grid" style={{ marginTop: 12 }}>
            <div className="explainer-field">
              <span>渲染风格</span>
              <p>{String(profile.render_style || "未设置")}</p>
            </div>
            <div className="explainer-field">
              <span>配色</span>
              <div className="explainer-actions">
                {Object.entries((profile.palette_json as Record<string, string> | undefined) ?? {}).map(([name, value]) => (
                  <span className="badge" key={name}>
                    <span aria-hidden="true" style={{ display: "inline-block", width: 10, height: 10, borderRadius: 3, background: value }} />
                    {name} {value}
                  </span>
                ))}
              </div>
            </div>
            <div className="explainer-field">
              <span>负面约束</span>
              <p className="muted">{((profile.negative_constraints_json as string[] | undefined) ?? []).join("、") || "未设置"}</p>
            </div>
          </div>
        </>
      ) : <p className="muted">尚未绑定栏目风格版本。每次生产都会冻结当前版本，全局模板编辑不会让旧作品悄悄变样。</p>}
    </Panel>

    <StateNotice state={state} />

    {entities.length > 0 ? (
      <div className="explainer-grid">
        <Panel title="实体" subtitle="稳定 ID 跨全文与镜头使用。">
          <div className="explainer-list">
            {entities.map((entity) => (
              <button
                type="button"
                className={`explainer-list-item${selected?.id === entity.id ? " selected" : ""}`}
                key={String(entity.id)}
                onClick={() => setSelectedId(String(entity.id))}
              >
                <span>{String(entity.code ?? "")}</span>
                <span>
                  {String(entity.name ?? "")}
                  <small>
                    {ENTITY_TYPE_LABELS[String(entity.entity_type)] ?? String(entity.entity_type)}
                    {entity.fictional ? " · 虚构" : ""}
                    {` · 引用 ${Number(entity.beat_reference_count ?? 0)}`}
                  </small>
                </span>
              </button>
            ))}
          </div>
        </Panel>

        <div className="explainer-stack">
          <Panel title="人物与风格状态" subtitle="年龄、服装与携带物随时间建立状态版本。">
            {selected ? (
              <>
                <SettingRow label="名称" value={String(selected.name ?? "")} />
                <SettingRow label="拉丁名" value={String(selected.latin_name ?? "—")} />
                <SettingRow label="真实/虚构" value={selected.fictional ? "虚构角色" : selected.descriptive_only ? "示意化形象（不宣称复原）" : "真实人物"} />
                <SettingRow label="别名" value={((selected.aliases_json as string[] | undefined) ?? []).join("、") || "无"} />
                <h3>状态版本</h3>
                {Array.isArray(selected.states) && (selected.states as unknown[]).length > 0 ? (
                  <div className="explainer-table-wrap">
                    <table className="explainer-table">
                      <thead><tr><th>版本</th><th>年龄</th><th>服装</th><th>有效期</th></tr></thead>
                      <tbody>
                        {(selected.states as Array<Record<string, unknown>>).map((revision) => (
                          <tr key={String(revision.id)}>
                            <td>v{String(revision.revision_no)}</td>
                            <td>{revision.age === null || revision.age === undefined ? "—" : String(revision.age)}</td>
                            <td>{String(revision.wardrobe || "—")}</td>
                            <td>{String(revision.valid_from_story_time ?? "?")} → {String(revision.valid_to_story_time ?? "?")}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : <p className="muted">还没有状态版本。</p>}
              </>
            ) : <p className="muted">选择一个实体查看详情。</p>}
          </Panel>

          <Panel title="参考与影响" subtitle="更换参考前先看清受影响的镜头数。">
            <p className="muted">
              单视角文件才是生成输入；带分隔线的三视图拼图只用于检查展示，默认不喂给图生视频，否则容易在同一画面生成多个人物。
            </p>
            <p className="explainer-note">
              参考图槽位不仅记录输入字段，还要验证工作流图中对应节点确实消费了该输入；只传参数而节点未使用会被判为能力不可用。
            </p>
            <p className="muted" style={{ marginTop: 10 }}>
              低置信或无法分辨的结果会进入少量人工复核队列，系统不会伪造 100% 一致。
            </p>
          </Panel>
        </div>
      </div>
    ) : null}
  </div>;
}
