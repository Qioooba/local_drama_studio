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
    request.steps !== undefined ? `${request.steps} steps` : null,
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
    {request ? <p className="generation-estimate-dimensions"><strong>{profileLabel ?? "当前解析 Profile"}</strong><span>{dimensions(request)}</span></p> : <p className="generation-estimate-empty"><strong>暂无本机估算</strong><span>{profileLabel ? "请选择生产档位，以明确分辨率、时长、帧数与 steps。" : "请先选择当前生成方式可用的 Profile。"}</span></p>}
    {request && state === "loading" && <p className="generation-estimate-empty" role="status"><strong>正在读取本机历史…</strong><span>估算读取不会阻塞预检或提交。</span></p>}
    {request && state === "error" && <p className="generation-estimate-empty" role="alert"><strong>暂无本机估算</strong><span>历史读取失败，仍可继续预检；不会用 Profile 声明值代替。</span></p>}
    {request && state === "success" && estimate?.status === "AVAILABLE" && estimate.p50_seconds !== null && estimate.p90_seconds !== null && <p className="generation-estimate-value" role="status"><strong>预计 {seconds(estimate.p50_seconds)}–{seconds(estimate.p90_seconds)}</strong><span>基于本机最近 {estimate.sample_count} 次同维度成功运行</span></p>}
    {request && state === "success" && estimate?.status === "NO_LOCAL_ESTIMATE" && <p className="generation-estimate-empty" role="status"><strong>暂无本机估算</strong><span>同维度成功样本 {estimate.sample_count}/{estimate.minimum_sample_count}；不猜测耗时。</span></p>}
    <small className="generation-estimate-source">仅统计真实成功 attempt；GPU 硬件型号尚未记录，不能按具体显卡推断。Profile resource_policy 是声明值，不属于此历史实测。</small>
  </section>;
}
