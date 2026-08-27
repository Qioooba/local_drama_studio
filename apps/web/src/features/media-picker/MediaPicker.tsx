import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listProjectMedia, mediaThumbnailUrl, uploadProjectMediaFile, type MediaCatalogueItem } from "./mediaPickerClient";
import { fallbackToOriginalVideo, mediaContentUrl, mediaProxyUrl } from "../shared/mediaPlaybackPolicy";
import "./media-picker.css";

type MediaPickerProps = {
  projectId: string;
  value: string;
  onChange: (mediaVersionId: string, item?: MediaCatalogueItem) => void;
  disabled?: boolean;
  label?: string;
  mediaKind?: "IMAGE" | "VIDEO" | "AUDIO";
  allowUpload?: boolean;
};

function readableSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function MediaPicker({ projectId, value, onChange, disabled = false, label = "选择参考媒体", mediaKind = "IMAGE", allowUpload = true }: MediaPickerProps) {
  const searchId = useId();
  const uploadId = useId();
  const [query, setQuery] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const catalogue = useQuery({
    queryKey: ["media-catalogue", projectId, mediaKind, query],
    queryFn: () => listProjectMedia(projectId, query, mediaKind),
    enabled: Boolean(projectId),
  });
  const selected = catalogue.data?.find((item) => item.media_version_id === value);

  const acceptType = mediaKind === "VIDEO" ? "video/*" : mediaKind === "AUDIO" ? "audio/*" : "image/*";
  const uploadBtnText = mediaKind === "VIDEO" ? "上传视频" : mediaKind === "AUDIO" ? "上传音频" : "上传图片";
  const upload = async (file: File | undefined) => {
    if (!file) return;
    setUploading(true); setUploadError(null);
    try {
      const mediaVersionId = await uploadProjectMediaFile(projectId, file);
      onChange(mediaVersionId);
      await catalogue.refetch();
    } catch (error) {
      setUploadError(String(error));
    } finally {
      setUploading(false);
    }
  };

  return <div className="media-picker" aria-label={label}>
    <div className="media-picker-toolbar">
      <label htmlFor={searchId}>搜索项目媒体</label>
      <input id={searchId} type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="文件名、用途或阶段" disabled={disabled} />
      {allowUpload && <><label className={`secondary media-picker-upload${disabled || uploading ? " disabled" : ""}`} htmlFor={uploadId}>{uploading ? "上传中…" : uploadBtnText}</label>
      <input id={uploadId} className="media-picker-file" type="file" accept={acceptType} disabled={disabled || uploading} onChange={(event) => void upload(event.target.files?.[0])} /></>}
    </div>

    {catalogue.isLoading && <p className="muted" role="status">正在读取项目媒体库…</p>}
    {(catalogue.error || uploadError) && <p className="inline-error" role="alert">{String(uploadError ?? catalogue.error)}</p>}
    {!catalogue.isLoading && !catalogue.error && catalogue.data?.length === 0 && <p className="empty-state">没有匹配的{mediaKind === "VIDEO" ? "视频" : mediaKind === "AUDIO" ? "音频" : "图片"}。{allowUpload ? "可以直接从当前电脑上传。" : "请先在媒体导入或生成流程登记媒体。"}</p>}
    {catalogue.data && catalogue.data.length > 0 && <div className="media-picker-grid" role="radiogroup" aria-label={`项目${mediaKind === "VIDEO" ? "视频" : mediaKind === "AUDIO" ? "音频" : "图片"}版本`}>
      {catalogue.data.map((item) => <button
        type="button"
        role="radio"
        aria-checked={value === item.media_version_id}
        className={`media-picker-card${value === item.media_version_id ? " selected" : ""}`}
        key={item.media_version_id}
        disabled={disabled}
        onClick={() => onChange(item.media_version_id, item)}
      >
        {mediaKind === "AUDIO" ? <span className="media-kind-placeholder" aria-hidden="true">AUDIO</span> : <img src={mediaThumbnailUrl(item.media_version_id)} alt="" loading="lazy" decoding="async" />}
        <span className="media-picker-name">{item.source_name || (mediaKind === "AUDIO" ? "未命名音频" : mediaKind === "VIDEO" ? "未命名视频" : "未命名图片")}</span>
        <span className="muted">版本 {item.version_no} · {item.stage} · {readableSize(item.byte_size)}</span>
      </button>)}
    </div>}

    {value && <div className="media-picker-selection" role="status">
      <strong>已选择：</strong>{selected ? `${selected.source_name || `未命名${mediaKind === "AUDIO" ? "音频" : mediaKind === "VIDEO" ? "视频" : "图片"}`} · 不可变版本 ${selected.version_no}` : "已选择的不可变媒体版本"}
      <details><summary>高级：查看版本标识</summary><code>{value}</code></details>
    </div>}
    {value && <div className="media-picker-preview" aria-label="已选媒体预览">{mediaKind === "VIDEO" && <video controls preload="none" playsInline poster={`/api/v1/media-versions/${encodeURIComponent(value)}/thumbnail?size=medium&frame=poster`} src={mediaProxyUrl(value)} data-original-src={mediaContentUrl(value)} onError={fallbackToOriginalVideo} />}{mediaKind === "AUDIO" && <audio controls preload="metadata" src={mediaContentUrl(value)} />}{mediaKind === "IMAGE" && <img src={`/api/v1/media-versions/${encodeURIComponent(value)}/thumbnail?size=medium&frame=poster`} alt={selected?.source_name ? `${selected.source_name} 预览` : "已选图片预览"} />}</div>}
  </div>;
}
