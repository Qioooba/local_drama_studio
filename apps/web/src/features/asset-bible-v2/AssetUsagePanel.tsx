import { Link } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import type { AssetBibleItem } from "./api";
import { EmptyState, StatusBadge } from "../../components/ui";

function fact(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

export function AssetUsagePanel({ item, projectId }: { item: AssetBibleItem; projectId: string }) {
  return <section className="panel asset-usage-panel" aria-labelledby="asset-usage-title">
    <div className="panel-heading"><div><p className="eyebrow">使用情况</p><h4 id="asset-usage-title">已用于哪些分集和镜头</h4></div><StatusBadge>{item.usage.shot_count} 镜头</StatusBadge></div>
    {item.usage.shots.length === 0 ? <EmptyState title="此资产尚未绑定到镜头" description="在分集规划或导演台绑定后，会在这里提供直接镜头入口。" /> : <ul>
      {item.usage.shots.map((shot, index) => {
        const episodeId = fact(shot.episode_id);
        const shotId = fact(shot.shot_id);
        const episodeCode = fact(shot.episode_code) ?? "未编号分集";
        const shotCode = fact(shot.shot_code) ?? "未编号镜头";
        const label = `${episodeCode} · ${shotCode}`;
        return <li key={fact(shot.binding_id) ?? `${shotId}-${index}`}>
          <span><strong>{label}</strong><small>{[fact(shot.scene_code), fact(shot.scene_title), fact(shot.role_in_shot)].filter(Boolean).join(" · ") || "已绑定资产"}</small></span>
          {episodeId && shotId ? <Link className="secondary" to={routes.shotStudio(projectId, episodeId, shotId)} aria-label={`打开 ${label} 镜头工作台`}>打开镜头</Link> : <span className="muted">缺少导航事实</span>}
        </li>;
      })}
    </ul>}
  </section>;
}
