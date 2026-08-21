import { useMemo } from "react";
import { useMutation } from "@tanstack/react-query";
import { createStoryAssetState, type AssetBibleItem, type StoryAssetReference } from "./api";
import { MediaThumb, StatusBadge } from "../../components/ui";
import "./scene-bible.css";

export type SceneReferenceKind = "SCENE_WIDE" | "SCENE_REVERSE" | "PANORAMA" | "LIGHTING_REFERENCE";

const REFERENCE_SLOTS: Array<{ kind: SceneReferenceKind; label: string; hint: string }> = [
  { kind: "SCENE_WIDE", label: "大全景 / Wide", hint: "建立空间、出入口和角色相对位置" },
  { kind: "SCENE_REVERSE", label: "反打 / Reverse", hint: "记录轴线另一侧与反向背景关系" },
  { kind: "PANORAMA", label: "全景拼接 / Panorama", hint: "覆盖场景整体方位，供镜头规划参考" },
  { kind: "LIGHTING_REFERENCE", label: "光线参考 / Light", hint: "固定日夜、色温、主光方向和氛围" },
];

function thumbnailUrl(mediaVersionId: string) {
  return `/api/v1/media-versions/${encodeURIComponent(mediaVersionId)}/thumbnail?size=small&frame=poster`;
}

function statePeriod(code: string, label: string): "DAY" | "NIGHT" | null {
  const value = `${code} ${label}`.toUpperCase();
  if (/(^|[_\s-])NIGHT([_\s-]|$)|夜/.test(value)) return "NIGHT";
  if (/(^|[_\s-])DAY([_\s-]|$)|日景|白天/.test(value)) return "DAY";
  return null;
}

function textFact(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

export function SceneBiblePanel({ item, onChanged, onChooseReference }: {
  item: AssetBibleItem;
  onChanged: () => void | Promise<void>;
  onChooseReference: (kind: SceneReferenceKind) => void;
}) {
  const allReferences = useMemo(() => [...item.base_references, ...item.states.flatMap((state) => state.references)], [item]);
  const periods = useMemo(() => {
    const result = new Map<"DAY" | "NIGHT", AssetBibleItem["states"][number]>();
    for (const state of item.states) {
      const period = statePeriod(state.code, state.label);
      if (period && !result.has(period)) result.set(period, state);
    }
    return result;
  }, [item.states]);
  const createPeriod = useMutation({
    mutationFn: (period: "DAY" | "NIGHT") => createStoryAssetState(item.asset.id, {
      code: period,
      label: period === "DAY" ? "日景" : "夜景",
      state_kind: "TIME_OF_DAY",
      description: period === "DAY" ? "场景日间视觉与光线状态" : "场景夜间视觉与光线状态",
    }),
    onSuccess: async () => { await onChanged(); },
  });
  const sceneFacts = useMemo(() => {
    const scenes = new Map<string, { code: string; title: string | null; shots: number }>();
    for (const shot of item.usage.shots) {
      const id = textFact(shot.scene_id);
      const code = textFact(shot.scene_code);
      if (!id && !code) continue;
      const key = id ?? code!;
      const current = scenes.get(key);
      if (current) current.shots += 1;
      else scenes.set(key, { code: code ?? "未编号场景", title: textFact(shot.scene_title), shots: 1 });
    }
    return [...scenes.values()];
  }, [item.usage.shots]);

  return (
    <section className="scene-bible-panel panel" aria-label="场景专项资产圣经">
      <div className="panel-heading"><div><p className="eyebrow">Scene Bible</p><h4>日夜状态与空间参考</h4></div><StatusBadge>{sceneFacts.length} 使用场景 · {item.usage.shot_count} 镜头</StatusBadge></div>
      <p className="muted">同一场景以状态区分日景/夜景；参考槽绑定不可变媒体版本，只显示低分辨率缩略图。</p>

      <div className="scene-period-grid" aria-label="日夜状态">
        {(["DAY", "NIGHT"] as const).map((period) => {
          const state = periods.get(period);
          return <article key={period} className={`scene-period-card ${period.toLowerCase()}${state ? " ready" : " missing"}`}>
            <div><span>{period}</span><strong>{period === "DAY" ? "日景状态" : "夜景状态"}</strong></div>
            {state ? <><p>{state.label} · {state.references.length} 张状态参考</p><code>{state.code}</code></> : <><p>尚未建立{period === "DAY" ? "日间" : "夜间"}视觉状态。</p><button type="button" className="secondary" disabled={createPeriod.isPending} onClick={() => createPeriod.mutate(period)}>创建 {period}</button></>}
          </article>;
        })}
      </div>
      {createPeriod.error && <p className="inline-error" role="alert">状态创建失败：{createPeriod.error instanceof Error ? createPeriod.error.message : String(createPeriod.error)}</p>}

      <div className="scene-reference-slots" aria-label="场景参考快捷槽">
        {REFERENCE_SLOTS.map((slot) => {
          const references = allReferences.filter((reference) => reference.reference_kind === slot.kind && reference.status !== "ARCHIVED");
          const current: StoryAssetReference | undefined = references[0];
          return <article className={`scene-reference-slot${current ? " filled" : ""}`} key={slot.kind}>
            <div className="scene-reference-preview"><MediaThumb src={current ? thumbnailUrl(current.media_version_id) : null} alt={`${slot.label} 缩略图`} emptyLabel="待选择" aspectRatio="4 / 3" /></div>
            <div><strong>{slot.label}</strong><p>{slot.hint}</p><small>{references.length > 0 ? `${references.length} 个历史参考${current?.is_locked ? " · 当前已锁定" : ""}` : "尚未绑定参考"}</small></div>
            <button type="button" className="secondary" onClick={() => onChooseReference(slot.kind)}>{current ? "添加新版本" : "选择媒体"}</button>
          </article>;
        })}
      </div>

      <div className="scene-usage" aria-label="场景和镜头使用情况">
        <div><strong>Used scenes</strong>{sceneFacts.length > 0 ? <ul>{sceneFacts.map((scene) => <li key={`${scene.code}-${scene.title}`}><span>{scene.code}{scene.title ? ` · ${scene.title}` : ""}</span><small>{scene.shots} 镜</small></li>)}</ul> : <p className="muted">{item.usage.shot_count > 0 ? "已绑定镜头，但这些镜头尚未提供场景归属事实。" : "尚未用于任何场景。"}</p>}</div>
        <div><strong>Used shots</strong>{item.usage.shots.length > 0 ? <ul>{item.usage.shots.map((shot, index) => <li key={textFact(shot.binding_id) ?? `${textFact(shot.shot_code)}-${index}`}><span>{textFact(shot.shot_code) ?? "未编号镜头"}</span><small>{[textFact(shot.episode_code), textFact(shot.scene_code), textFact(shot.role_in_shot)].filter(Boolean).join(" · ") || "已绑定"}</small></li>)}</ul> : <p className="muted">尚未用于任何镜头。</p>}</div>
      </div>
    </section>
  );
}
