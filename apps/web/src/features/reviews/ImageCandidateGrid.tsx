import type { ReviewInboxItem } from "../../generated/api";

type Props = {
  items: ReviewInboxItem[];
  selectedVersionId: string | null;
  onSelect: (mediaVersionId: string) => void;
};

/**
 * Thumbnail-only candidate overview for image review.  The review detail owns
 * A/B comparison and decisions; this grid only changes the selected candidate.
 */
export function ImageCandidateGrid({ items, selectedVersionId, onSelect }: Props) {
  const images = items.filter((item) => item.media_kind === "IMAGE").slice(0, 40);
  if (images.length === 0) return null;
  return (
    <section className="panel image-candidate-grid-panel" aria-labelledby="image-candidate-grid-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">FR-IMG-003 · IMAGE CANDIDATES</p>
          <h3 id="image-candidate-grid-title">图片候选缩略图网格</h3>
        </div>
        <span className="status-pill">{images.length} 张</span>
      </div>
      <p className="muted">只读取本地 320px small 派生缩略图；点击候选进入结构化审核与 A/B 比较，不加载原图。</p>
      <div className="image-candidate-grid" role="list" aria-label="图片候选缩略图网格">
        {images.map((item) => {
          const selected = item.media_version_id === selectedVersionId;
          return (
            <button
              type="button"
              className={`image-candidate-card${selected ? " selected" : ""}`}
              key={item.media_version_id}
              aria-pressed={selected}
              aria-label={`选择图片候选 ${item.media_version_id.slice(0, 12)}`}
              onClick={() => onSelect(item.media_version_id)}
            >
              <img
                src={`/api/v1/media-versions/${encodeURIComponent(item.media_version_id)}/thumbnail?size=small&frame=poster`}
                alt=""
                width="160"
                height="90"
                loading="lazy"
                decoding="async"
              />
              <span>{item.stage} · {item.decision ?? "未审核"}</span>
              <code>{item.media_version_id.slice(0, 12)}</code>
            </button>
          );
        })}
      </div>
    </section>
  );
}
