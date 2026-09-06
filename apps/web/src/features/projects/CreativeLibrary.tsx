import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  compareCreativeEntryRevisions,
  createCreativeEntry,
  createCreativeEntryRevision,
  getCreativeEntry,
  listCreativeEntries,
  restoreCreativeEntryRevision,
} from "../../generated/api";

const CREATIVE_KINDS = [
  { value: "SERIES_BIBLE", label: "世界观与总设定", prefix: "BIBLE_" },
  { value: "CHARACTER", label: "角色档案", prefix: "CHAR_" },
  { value: "SCENE", label: "场景地点", prefix: "SCENE_" },
  { value: "PROP", label: "关键道具", prefix: "PROP_" },
  { value: "COSTUME", label: "服装造型", prefix: "COSTUME_" },
  { value: "STYLE", label: "视觉风格", prefix: "STYLE_" },
  { value: "VOICE", label: "角色音色", prefix: "VOICE_" },
];

function parseContent(raw: string): Record<string, unknown> | null {
  try {
    const value = JSON.parse(raw);
    return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}
function getDefaultContentForKind(kind: string, title: string): Record<string, string> {
  if (kind === "CHARACTER") {
    return {
      name: title || "",
      appearance: "",
      personality: "",
      background: "",
      motivation: "",
    };
  }
  if (kind === "SCENE") {
    return {
      name: title || "",
      location: "",
      atmosphere: "",
      visual_features: "",
    };
  }
  if (kind === "SERIES_BIBLE") {
    return {
      title: title || "总设定",
      synopsis: "",
      theme: "",
      setting: "",
      visual_tone: "",
    };
  }
  if (kind === "VOICE") {
    return {
      timbre: "",
      tone: "",
      reference: "",
    };
  }
  return { description: "" };
}

const FIELD_LABELS: Record<string, string> = {
  name: "名称", title: "标题", introduction: "完整介绍", description: "设定描述", aliases: "别名", role: "角色定位",
  biography: "人物小传", appearance: "外观特征", costume: "服装造型", personality: "性格", background: "人物背景",
  motivation: "核心动机", skills: "能力", weakness: "弱点", arc: "人物弧光", relationships: "人物关系", voice: "声音特征",
  location: "地点", geography: "空间地理", architecture: "建筑风貌", layout: "空间布局", atmosphere: "氛围", lighting: "环境光线",
  time: "时间特征", color_palette: "色彩方案", key_elements: "关键元素", story_function: "剧情作用", visual_features: "画面特征",
  material: "材质", function: "功能", story_significance: "剧情意义", owner: "归属", rules: "使用规则", visual_prompt: "后续文生图描述",
  logline: "一句话梗概", synopsis: "故事梗概", genre: "类型", audience: "目标观众", themes: "主题", theme: "主题",
  tone: "整体语气", worldview: "世界观", world_rules: "世界规则", timeline: "时间线", central_conflict: "核心冲突",
  narrative_structure: "叙事结构", visual_style: "视觉风格", color_language: "色彩语言", taboos: "创作禁区", ending_direction: "结局方向",
  setting: "时代与世界", visual_tone: "视觉基调", timbre: "音色", reference: "参考方向",
  pipeline_run_id: "来源草案运行", media_generation_started: "已启动媒体生成",
};
const FIELD_HINTS: Record<string, string> = {
  appearance: "例如：深色风衣、短发、眼神坚毅", personality: "例如：沉着冷静、行动力强", background: "人物经历与重要前史",
  motivation: "角色当前最想实现什么", location: "例如：海边旧灯塔", atmosphere: "天气、光线、情绪与空间感",
  visual_features: "可反复识别的建筑、陈设与色彩", synopsis: "用几句话说明故事主线", theme: "例如：成长、亲情与选择",
  setting: "故事发生的年代、城市与社会规则", visual_tone: "例如：低饱和冷色胶片感", timbre: "例如：温暖清亮的青年女声",
  tone: "语速、力度与情绪倾向", reference: "描述声音方向，不填写模型 ID", description: "写清外观、用途和剧情限制",
};

function StructuredContentEditor({ value, onChange, depth = 0 }: { value: Record<string, unknown>; onChange: (value: Record<string, unknown>) => void; depth?: number }) {
  return <div className={`creative-structured-fields depth-${depth}`}>{Object.entries(value).map(([key, entry]) => {
    const label = FIELD_LABELS[key] ?? key.replaceAll("_", " ");
    if (entry && typeof entry === "object" && !Array.isArray(entry)) {
      return <fieldset key={key}><legend>{label}</legend><StructuredContentEditor value={entry as Record<string, unknown>} depth={depth + 1} onChange={(next) => onChange({ ...value, [key]: next })} /></fieldset>;
    }
    if (Array.isArray(entry)) {
      return <fieldset key={key}><legend>{label}</legend><div className="creative-list-editor">{entry.map((item, index) => <div key={index}><input aria-label={`${label} ${index + 1}`} value={String(item ?? "")} onChange={(event) => onChange({ ...value, [key]: entry.map((current, itemIndex) => itemIndex === index ? event.target.value : current) })} /><button type="button" className="secondary" onClick={() => onChange({ ...value, [key]: entry.filter((_, itemIndex) => itemIndex !== index) })}>删除</button></div>)}<button type="button" className="secondary" onClick={() => onChange({ ...value, [key]: [...entry, ""] })}>添加一项</button></div></fieldset>;
    }
    if (typeof entry === "boolean") return <label key={key} className="check-row"><input type="checkbox" checked={entry} onChange={(event) => onChange({ ...value, [key]: event.target.checked })} />{label}</label>;
    if (typeof entry === "number") return <label key={key}>{label}<input type="number" value={entry} onChange={(event) => onChange({ ...value, [key]: Number(event.target.value) })} /></label>;
    return <label key={key}>{label}<textarea rows={2} value={String(entry ?? "")} placeholder={FIELD_HINTS[key]} onChange={(event) => onChange({ ...value, [key]: event.target.value })} /></label>;
  })}</div>;
}

function autoCode(kind: string, title: string): string {
  const prefix = CREATIVE_KINDS.find((k) => k.value === kind)?.prefix || "ENTRY_";
  let slug = title.toUpperCase().replace(/[^A-Z0-9_]+/g, "_").replace(/^_+|_+$/g, "");
  if (!slug) {
    const hash = Math.abs(title.split("").reduce((acc, char) => ((acc << 5) - acc) + char.charCodeAt(0), 0)).toString(36).toUpperCase().slice(0, 4);
    slug = hash || "MAIN";
  }
  return `${prefix}${slug}`.slice(0, 64);
}

export function CreativeLibrary({ projectId }: { projectId: string }) {
  const client = useQueryClient();
  const entries = useQuery({
    queryKey: ["creative-entries", projectId],
    queryFn: () => listCreativeEntries(projectId),
  });

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = selectedId ?? entries.data?.items[0]?.id ?? null;

  const detail = useQuery({
    queryKey: ["creative-entry", selected],
    queryFn: () => getCreativeEntry(selected as string),
    enabled: Boolean(selected),
  });

  const [kind, setKind] = useState("");
  const [code, setCode] = useState("");
  const [title, setTitle] = useState("");
  const [initialContent, setInitialContent] = useState("");
  const [initialNote, setInitialNote] = useState("");

  const [editContent, setEditContent] = useState("");
  const [editNote, setEditNote] = useState("");
  const [leftId, setLeftId] = useState("");
  const [rightId, setRightId] = useState("");
  const [restoreNote, setRestoreNote] = useState("");

  useEffect(() => {
    if (detail.data) {
      setEditContent(JSON.stringify(detail.data.entry.content, null, 2));
      setLeftId(detail.data.revisions[1]?.id ?? "");
      setRightId(detail.data.revisions[0]?.id ?? "");
    }
  }, [detail.data?.entry.current_revision_id]);

  const comparison = useQuery({
    queryKey: ["creative-comparison", selected, leftId, rightId],
    queryFn: () => compareCreativeEntryRevisions(selected as string, leftId, rightId),
    enabled: Boolean(selected && leftId && rightId && leftId !== rightId),
  });

  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ["creative-entries", projectId] });
    await client.invalidateQueries({ queryKey: ["creative-entry", selected] });
  };

  const create = useMutation({
    mutationFn: () =>
      createCreativeEntry({
        project_id: projectId,
        kind,
        code,
        title,
        content: parseContent(initialContent) as Record<string, unknown>,
        change_note: initialNote,
      }),
    onSuccess: async ({ entry }) => {
      setSelectedId(entry.id);
      setKind("");
      setCode("");
      setTitle("");
      setInitialContent("");
      setInitialNote("");
      await refresh();
    },
  });

  const save = useMutation({
    mutationFn: () =>
      createCreativeEntryRevision(selected as string, {
        content: parseContent(editContent) as Record<string, unknown>,
        change_note: editNote,
      }),
    onSuccess: async () => {
      setEditNote("");
      await refresh();
    },
  });

  const restore = useMutation({
    mutationFn: () => restoreCreativeEntryRevision(selected as string, leftId, restoreNote),
    onSuccess: async () => {
      setRestoreNote("");
      await refresh();
    },
  });

  const handleKindSelect = (newKind: string) => {
    setKind(newKind);
    setCode(title ? autoCode(newKind, title) : "");
    setInitialContent(JSON.stringify(getDefaultContentForKind(newKind, title), null, 2));
    if (!initialNote) {
      setInitialNote(`创建${CREATIVE_KINDS.find((k) => k.value === newKind)?.label || "资料"}`);
    }
  };

  const handleTitleChange = (newTitle: string) => {
    setTitle(newTitle);
    if (kind) {
      setCode(autoCode(kind, newTitle));
      const current = parseContent(initialContent) ?? getDefaultContentForKind(kind, newTitle);
      const identityField = kind === "SERIES_BIBLE" ? "title" : ["CHARACTER", "SCENE"].includes(kind) ? "name" : null;
      if (identityField) setInitialContent(JSON.stringify({ ...current, [identityField]: newTitle }, null, 2));
    }
  };

  const parsedInitial = useMemo(() => parseContent(initialContent) || {}, [initialContent]);
  const parsedEdit = useMemo(() => parseContent(editContent) || {}, [editContent]);

  const createValid = Boolean(kind && code && title.trim() && parseContent(initialContent) && initialNote.trim());
  const editValid = Boolean(selected && parseContent(editContent) && editNote.trim());
  const restoreValid = Boolean(selected && leftId && restoreNote.trim() && leftId !== detail.data?.entry.current_revision_id);
  const error = create.error ?? save.error ?? restore.error ?? entries.error ?? detail.error ?? comparison.error;

  return (
    <section className="creative-library" aria-labelledby="creative-library-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">FR-WRT-001 · 不可变版本历史</p>
          <h3 id="creative-library-title">故事圣经与创作资料</h3>
        </div>
        <span className="status-pill">{entries.data?.items.length ?? 0} 项</span>
      </div>

      <div className="creative-layout">
        <div className="creative-list">
          {entries.data?.items.map((entry) => (
            <button
              type="button"
              key={entry.id}
              className={entry.id === selected ? "selected" : ""}
              onClick={() => setSelectedId(entry.id)}
            >
              <strong>{entry.code}</strong>
              <span>
                {entry.title ? `${entry.title} · ` : ""}{entry.kind} · revision {entry.revision_no}
              </span>
            </button>
          ))}
          {entries.data?.items.length === 0 && <p className="empty-state">当前项目尚无创作资料，请在下方新建设定。</p>}
        </div>

        <div className="creative-detail">
          {detail.data ? (
            <>
              <div className="creative-title">
                <div>
                  <strong>{detail.data.entry.title}</strong>
                  <span style={{ marginLeft: "8px", opacity: 0.8 }}>({detail.data.entry.code})</span>
                </div>
                <span>
                  {CREATIVE_KINDS.find((k) => k.value === detail.data.entry.kind)?.label || detail.data.entry.kind} · 当前版本 v{detail.data.entry.revision_no}
                </span>
              </div>

              <div className="form-section-card">
                <StructuredContentEditor value={parsedEdit} onChange={(next) => setEditContent(JSON.stringify(next, null, 2))} />
                {Object.keys(parsedEdit).length === 0 && <p className="muted">当前资料没有可编辑属性。</p>}
              </div>

              <label>
                变更说明
                <input
                  value={editNote}
                  placeholder="例如：修改人物外貌与关键台词"
                  onChange={(event) => setEditNote(event.target.value)}
                />
              </label>
              <button
                type="button"
                className="secondary"
                disabled={!editValid || save.isPending}
                onClick={() => save.mutate()}
              >
                {save.isPending ? "保存中…" : "保存新 revision"}
              </button>

              <div className="creative-compare-section" style={{ marginTop: "16px", paddingTop: "12px", borderTop: "1px solid var(--border)" }}>
                <p className="section-title">历史版本对比与回退</p>
                <div className="creative-compare">
                  <label>
                    比较旧版
                    <select value={leftId} onChange={(event) => setLeftId(event.target.value)}>
                      <option value="">请选择</option>
                      {detail.data.revisions.map((revision) => (
                        <option key={revision.id} value={revision.id}>
                          第 {revision.revision_no} 版（{revision.change_note || "初版"}）
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    比较新版
                    <select value={rightId} onChange={(event) => setRightId(event.target.value)}>
                      <option value="">请选择</option>
                      {detail.data.revisions.map((revision) => (
                        <option key={revision.id} value={revision.id}>
                          第 {revision.revision_no} 版（{revision.change_note || "初版"}）
                        </option>
                      ))}
                    </select>
                  </label>
                </div>

                {comparison.data && (
                  <div className="creative-diff">
                    {comparison.data.comparison.changes.map((change) => (
                      <p key={change.field}>
                        <strong>{change.field}</strong>
                        <span>
                          {JSON.stringify(change.before)} → {JSON.stringify(change.after)}
                        </span>
                      </p>
                    ))}
                    {comparison.data.comparison.changes.length === 0 && <p>两版结构化内容一致。</p>}
                  </div>
                )}

                <div style={{ marginTop: "10px", display: "grid", gap: "6px" }}>
                  <label>
                    回退说明
                    <input
                      value={restoreNote}
                      onChange={(event) => setRestoreNote(event.target.value)}
                      placeholder="回退会派生新的 revision 版本"
                    />
                  </label>
                  <button
                    type="button"
                    className="secondary"
                    disabled={!restoreValid || restore.isPending}
                    onClick={() => restore.mutate()}
                  >
                    {restore.isPending ? "回退中…" : "从所选旧版派生回退 revision"}
                  </button>
                </div>
              </div>
            </>
          ) : (
            <p className="empty-state">请从左侧选择一项创作资料以查看其设定与版本记录。</p>
          )}
        </div>
      </div>

      <details className="creative-create" open>
        <summary>新建创作资料</summary>
        <div className="creative-create-grid">
          <label>
            类型
            <select value={kind} onChange={(event) => handleKindSelect(event.target.value)}>
              <option value="">请选择资料类型</option>
              {CREATIVE_KINDS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <div className="field-fact"><span>资料编号</span><strong>{code || "填写标题后自动生成"}</strong><small>按资料类型和标题自动生成，不需要记忆命名规则。</small></div>
          <label>
            标题
            <input
              value={title}
              placeholder="例如：林默（男主角）"
              onChange={(event) => handleTitleChange(event.target.value)}
            />
          </label>

          <div className="wide form-section-card">
            {kind ? <StructuredContentEditor value={parsedInitial} onChange={(next) => setInitialContent(JSON.stringify(next, null, 2))} /> : <p className="muted">先选择资料类型，系统会显示对应的可填写内容。</p>}
          </div>

          <label className="wide">
            建立说明
            <input
              value={initialNote}
              placeholder="例如：初版人物设定建立"
              onChange={(event) => setInitialNote(event.target.value)}
            />
          </label>
          <button
            type="button"
            className="primary-action"
            disabled={!createValid || create.isPending}
            onClick={() => create.mutate()}
          >
            {create.isPending ? "创建中…" : "建立并保存 revision 1"}
          </button>
        </div>
      </details>

      {error && <p className="inline-error" role="alert">{String(error)}</p>}
    </section>
  );
}
