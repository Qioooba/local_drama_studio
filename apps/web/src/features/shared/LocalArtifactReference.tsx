import { useEffect, useState } from "react";
import type { LocalArtifactReference as LocalArtifactReferenceValue } from "../../generated/api";

type Props = {
  artifact: LocalArtifactReferenceValue;
  title?: string;
  note?: string;
  relativeLabel?: string;
  downloadLabel?: string | null;
  compact?: boolean;
};

export function LocalArtifactReference({
  artifact,
  title = "文件与保存位置",
  note,
  relativeLabel = artifact.scope === "PROJECT" ? "项目相对路径" : "数据目录相对路径",
  downloadLabel = "下载到当前电脑",
  compact = false,
}: Props) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");

  useEffect(() => setCopyState("idle"), [artifact.server_absolute_path]);

  const copyAbsolutePath = async () => {
    try {
      await navigator.clipboard.writeText(artifact.server_absolute_path);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  };

  return <section className={`local-artifact-reference${compact ? " local-artifact-reference--compact" : ""}`} aria-label={title}>
    <div className="local-artifact-reference__content">
      <span className="local-artifact-reference__eyebrow">{title}</span>
      <strong aria-label="文件显示名" title={artifact.display_name}>{artifact.display_name}</strong>
      <dl>
        <div><dt>服务器绝对路径</dt><dd><code aria-label="服务器绝对路径" title={artifact.server_absolute_path}>{artifact.server_absolute_path}</code></dd></div>
        <div><dt>{relativeLabel}</dt><dd><code aria-label={relativeLabel} title={artifact.rel_path}>{artifact.rel_path}</code></dd></div>
      </dl>
      {note && <small>{note}</small>}
    </div>
    <div className="local-artifact-reference__actions">
      {downloadLabel && <a className="secondary" href={artifact.download_url} download={artifact.download_filename}>{downloadLabel}</a>}
      <button type="button" className="secondary" onClick={() => void copyAbsolutePath()}>{copyState === "copied" ? "已复制" : copyState === "failed" ? "复制失败，请手动选择" : "复制绝对路径"}</button>
    </div>
    <span className="sr-only" role="status" aria-live="polite">{copyState === "copied" ? "服务器绝对路径已复制" : copyState === "failed" ? "复制失败，请手动选择路径" : ""}</span>
  </section>;
}
