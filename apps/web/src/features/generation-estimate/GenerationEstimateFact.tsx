import { useEffect, useState } from "react";
import { getLocalGenerationEstimate, type GenerationEstimateRequest, type GenerationEstimateResponse } from "./client";
import "./generation-estimate.css";

type Props = {
  profileLabel: string | null;
  request: GenerationEstimateRequest | null;
};

function seconds(value: number) {
  return value >= 60 ? `${Math.floor(value / 60)}分${Math.round(value % 60)}秒` : `${Math.round(value * 10) / 10}秒`;
}

function dimensions(request: GenerationEstimateRequest) {
  return [
    `${request.width}×${request.height}`,
    request.durationSeconds !== undefined ? `${request.durationSeconds}秒` : null,
    request.frameCount !== undefined ? `${request.frameCount}帧` : null,
    request.steps !== undefined ? `${request.steps} 次生成迭代` : null,
  ].filter(Boolean).join(" · ");
}

export function GenerationEstimateFact({ profileLabel, request }: Props) {
  const [estimate, setEstimate] = useState<GenerationEstimateResponse | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "success" | "error">("idle");

  useEffect(() => {
    if (!request) {
      setEstimate(null);
      setState("idle");
      return;
    }
    let disposed = false;
    setEstimate(null);
    setState("loading");
    void getLocalGenerationEstimate(request)
      .then((result) => { if (!disposed) { setEstimate(result); setState("success"); } })
      .catch(() => { if (!disposed) setState("error"); });
    return () => { disposed = true; };
  }, [request?.durationSeconds, request?.frameCount, request?.height, request?.profileVersionId, request?.steps, request?.width]);

  return <section className="generation-estimate-fact" aria-labelledby="generation-estimate-title" aria-busy={state === "loading"}>
    <div><span id="generation-estimate-title">本机历史耗时</span><small>只读事实 · 不阻塞生成</small></div>
    {request ? <p className="generation-estimate-dimensions"><strong>{profileLabel ?? "当前生成配置"}</strong><span>{dimensions(request)}</span></p> : <p className="generation-estimate-empty"><strong>暂无本机估算</strong><span>{profileLabel ? "请选择生产档位，以明确分辨率、时长、帧数与生成迭代次数。" : "请先选择当前生成方式可用的配置。"}</span></p>}
    {request && state === "loading" && <p className="generation-estimate-empty" role="status"><strong>正在读取本机历史…</strong><span>估算读取不会阻塞预检或提交。</span></p>}
    {request && state === "error" && <p className="generation-estimate-empty" role="alert"><strong>暂无本机估算</strong><span>历史读取失败，仍可继续预检；系统不会用配置中的理论值冒充实测结果。</span></p>}
    {request && state === "success" && estimate?.status === "AVAILABLE" && estimate.p50_seconds !== null && estimate.p90_seconds !== null && <p className="generation-estimate-value" role="status"><strong>预计 {seconds(estimate.p50_seconds)}–{seconds(estimate.p90_seconds)}</strong><span>基于本机最近 {estimate.sample_count} 次同维度成功运行</span></p>}
    {request && state === "success" && estimate?.status === "NO_LOCAL_ESTIMATE" && <p className="generation-estimate-empty" role="status"><strong>暂无本机估算</strong><span>同维度成功样本 {estimate.sample_count}/{estimate.minimum_sample_count}；不猜测耗时。</span></p>}
    <small className="generation-estimate-source">仅统计本机真实成功的生成记录。显卡型号尚未记录，因此不能按具体硬件推断；生成配置中的资源预估只是理论值，不计入历史实测。</small>
  </section>;
}
