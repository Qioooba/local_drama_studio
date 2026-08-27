import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getClientCapabilities, listProjectLocalResources, uploadProjectLocalResource, type ProjectLocalResource } from "../../generated/api";

type ResourceKind = "LUT" | "LICENSE_EVIDENCE";

function fileSizeLabel(byteSize: number): string {
  if (byteSize < 1024) return `${byteSize} B`;
  if (byteSize < 1024 * 1024) return `${(byteSize / 1024).toFixed(1)} KB`;
  return `${(byteSize / (1024 * 1024)).toFixed(1)} MB`;
}

export function ProjectLocalResourceSelect({
  projectId,
  kind,
  value,
  onChange,
  label,
  required = false,
  emptyLabel = "不使用",
}: {
  projectId?: string;
  kind: ResourceKind;
  value: string;
  onChange: (value: string) => void;
  label: string;
  required?: boolean;
  emptyLabel?: string;
}) {
  const uploadId = useId();
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const capabilities = useQuery({ queryKey: ["client-capabilities"], queryFn: () => getClientCapabilities(), staleTime: Infinity });
  const resources = useQuery({
    queryKey: ["project-local-resources", projectId, kind],
    queryFn: () => listProjectLocalResources(String(projectId), kind),
    enabled: Boolean(projectId),
    staleTime: 10_000,
  });
  const items = resources.data?.items ?? [];
  const currentIsListed = items.some((item) => item.path_rel === value);
  const optionLabel = (item: ProjectLocalResource) => `${item.name} · ${fileSizeLabel(item.byte_size)} · ${item.path_rel}`;
  const upload = async (file?: File) => {
    if (!file || !projectId) return;
    setUploading(true); setUploadError(null);
    try {
      const result = await uploadProjectLocalResource(projectId, kind, file);
      onChange(result.resource.path_rel);
      await resources.refetch();
    } catch (error) { setUploadError(String(error)); }
    finally { setUploading(false); }
  };

  return <div className="project-resource-select">
    <label>{label}
      <select value={value} onChange={(event) => onChange(event.target.value)} required={required} disabled={!projectId || resources.isLoading}>
        <option value="">{resources.isLoading ? "正在扫描项目资源…" : emptyLabel}</option>
        {value && !currentIsListed && <option value={value}>当前自定义路径 · {value}</option>}
        {items.map((item) => <option key={item.path_rel} value={item.path_rel}>{optionLabel(item)}</option>)}
      </select>
    </label>
    {!projectId && <small className="muted">请先选择当前项目中的媒体。</small>}
    {projectId && !resources.isLoading && !resources.error && items.length === 0 && <small className="muted">项目中还没有符合用途的文件。</small>}
    {resources.data?.truncated && <small className="review-guidance">结果已达到 {resources.data.limit} 项；请整理项目资源目录后重试。</small>}
    {resources.error && <small className="inline-error" role="alert">资源扫描失败：{String(resources.error)}</small>}
    {projectId && <div className="project-resource-upload"><label className={`secondary${uploading ? " disabled" : ""}`} htmlFor={uploadId}>{uploading ? "正在上传…" : kind === "LUT" ? "从当前电脑上传 LUT" : "从当前电脑上传证据"}</label><input id={uploadId} className="project-resource-file" type="file" accept={kind === "LUT" ? ".cube" : ".json,.txt,.md,.pdf"} disabled={uploading} onChange={(event) => void upload(event.target.files?.[0])} /><small className="muted">文件会复制到服务端当前项目的受控资源目录。</small></div>}
    {uploadError && <small className="inline-error" role="alert">上传失败：{uploadError}</small>}
    {capabilities.data?.capabilities.server_file_dialogs && <details>
      <summary>高级：手动填写项目内相对路径</summary>
      <label>自定义相对路径<input value={value} onChange={(event) => onChange(event.target.value)} placeholder={kind === "LUT" ? "00_admin/color/look.cube" : "00_admin/licenses/model.json"} /></label>
      <small className="muted">仅用于文件尚未被列表发现时；提交时后端仍会校验用途目录、扩展名和越界。</small>
    </details>}
  </div>;
}
