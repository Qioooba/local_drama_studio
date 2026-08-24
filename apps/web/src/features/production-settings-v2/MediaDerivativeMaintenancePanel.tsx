import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiRequestError, backfillProjectMediaDerivatives, type MediaDerivativeBackfill } from "../../generated/api";

type Props = { projectId: string };

type Totals = { scanned: number; submitted: number; replayed: number };

function failureMessage(reason: unknown): string {
  if (reason instanceof ApiRequestError) return reason.message;
  if (reason instanceof Error) return reason.message;
  return "补齐预览缓存失败，请稍后重试";
}

export function MediaDerivativeMaintenancePanel({ projectId }: Props) {
  const queryClient = useQueryClient();
  const [page, setPage] = useState<MediaDerivativeBackfill | null>(null);
  const [totals, setTotals] = useState<Totals>({ scanned: 0, submitted: 0, replayed: 0 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (continueFromPage: boolean) => {
    setBusy(true);
    setError(null);
    const cursor = continueFromPage ? page?.next_cursor ?? 0 : 0;
    try {
      const result = (await backfillProjectMediaDerivatives(projectId, cursor, 50)).backfill;
      setPage(result);
      setTotals((current) => {
        const base = continueFromPage ? current : { scanned: 0, submitted: 0, replayed: 0 };
        return {
          scanned: base.scanned + result.scanned,
          submitted: base.submitted + result.submitted,
          replayed: base.replayed + result.replayed,
        };
      });
      if (result.submitted > 0) {
        void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      }
    } catch (reason) {
      setError(failureMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel production-setting-card" aria-labelledby="media-derivative-maintenance-title">
      <div className="production-setting-card__head">
        <div><span>MEDIA CACHE</span><h3 id="media-derivative-maintenance-title">媒体预览缓存维护</h3></div>
        <span className={`status-pill ${page?.has_more ? "state-warning" : page ? "state-ready" : "neutral"}`}>
          {page?.has_more ? "仍有历史媒体" : page ? "本轮扫描完成" : "按需运行"}
        </span>
      </div>
      <p>为当前项目的图片、视频和音频补齐缩略图、胶片条、波形及低码率视频 proxy。转换只在显式后台任务中发生，浏览和播放接口不会偷偷启动 FFmpeg。</p>
      {page && (
        <dl aria-label="媒体预览缓存补队结果">
          <div><dt>已扫描</dt><dd>{totals.scanned} 个媒体版本</dd></div>
          <div><dt>新提交</dt><dd>{totals.submitted} 个后台任务</dd></div>
          <div><dt>已复用</dt><dd>{totals.replayed} 个既有任务</dd></div>
        </dl>
      )}
      <div className="action-row">
        <button type="button" className="secondary" disabled={busy || !projectId} onClick={() => void run(Boolean(page?.has_more))}>
          {busy ? "正在提交…" : page?.has_more ? "继续扫描下一批" : page ? "重新扫描当前项目" : "补齐当前项目预览缓存"}
        </button>
        {page?.submitted ? <span className="muted" role="status">任务已进入作业中心，可离开本页继续工作。</span> : null}
      </div>
      {error && <p className="inline-error" role="alert">{error}</p>}
    </section>
  );
}
