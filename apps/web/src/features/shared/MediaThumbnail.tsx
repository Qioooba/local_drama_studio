import { useEffect, useState, type ImgHTMLAttributes } from "react";

type MediaThumbnailProps = Omit<ImgHTMLAttributes<HTMLImageElement>, "src"> & {
  src: string;
  fallbackLabel?: string;
};

export function MediaThumbnail({ src, fallbackLabel = "缩略图待生成", className, ...imageProps }: MediaThumbnailProps) {
  const [failed, setFailed] = useState(false);

  useEffect(() => setFailed(false), [src]);

  if (failed) {
    return (
      <span className={["media-thumbnail-fallback", className].filter(Boolean).join(" ")} role="img" aria-label={fallbackLabel}>
        <span aria-hidden="true">◫</span>
        <small>{fallbackLabel}</small>
      </span>
    );
  }

  return <img {...imageProps} className={className} src={src} onError={() => setFailed(true)} />;
}
