import type { GenerationResolution } from "./types";

const UNKNOWN_REASON: Record<string, string> = {
  SCHEMA_UNAVAILABLE: "本机历史结构不可用",
  NO_COMPARABLE_DIMENSION_HISTORY: "没有维度完整且可比较的本机记录",
  INSUFFICIENT_SAME_DIMENSION_SAMPLES: "同维度终态样本不足",
};

export function RecommendationFacts({ resolution }: { resolution: GenerationResolution }) {
  const profile = resolution.profile;
  const recommendation = resolution.recommendation;
  if (!profile || !recommendation) return null;
  const rate = recommendation.local_success_rate;
  return (
    <section className="recommendation-facts" aria-labelledby="recommendation-facts-title">
      <div>
        <span className="resolution-kicker">生效 Profile</span>
        <strong id="recommendation-facts-title">{profile.code} · {profile.title} · v{profile.version_no}</strong>
      </div>
      <ul aria-label="推荐依据">
        <li>{profile.capability} 能力精确匹配</li>
        <li>{profile.status === "PUBLISHED" ? "已发布版本" : `状态 ${profile.status}`}</li>
        <li>{resolution.native_support ? "原生支持" : "非原生支持"}</li>
      </ul>
      {rate.status === "AVAILABLE" && rate.value !== null ? (
        <p className="recommendation-rate">
          <strong>最近同维度成功率 {Math.round(rate.value * 100)}%</strong>
          <span>成功 {rate.successful_sample_count} / 终态 {rate.terminal_sample_count}</span>
        </p>
      ) : (
        <p className="recommendation-rate unknown">
          <strong>最近成功率未知</strong>
          <span>{UNKNOWN_REASON[rate.reason ?? ""] ?? "没有足够的权威本机样本"} · 同维度终态 {rate.terminal_sample_count}/{rate.minimum_sample_count}</span>
        </p>
      )}
      <small>仅统计相同分辨率、时长/帧数、步数与资源类别的本机成功/失败 attempt；不把 worker 名称当作 GPU 型号。</small>
    </section>
  );
}
