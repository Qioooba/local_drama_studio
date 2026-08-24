import { useQuery } from "@tanstack/react-query";
import { listProjectLocalResources, type ProjectLocalResource } from "../../generated/api";

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
  const resources = useQuery({
    queryKey: ["project-local-resources", projectId, kind],
    queryFn: () => listProjectLocalResources(String(projectId), kind),
    enabled: Boolean(projectId),
    staleTime: 10_000,
  });
  const items = resources.data?.items ?? [];
  const currentIsListed = items.some((item) => item.path_rel === value);
  const optionLabel = (item: ProjectLocalResource) => `${item.name} · ${fileSizeLabel(item.byte_size)} · ${item.path_rel}`;

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
    <details>
      <summary>高级：手动填写项目内相对路径</summary>
      <label>自定义相对路径<input value={value} onChange={(event) => onChange(event.target.value)} placeholder={kind === "LUT" ? "00_admin/color/look.cube" : "00_admin/licenses/model.json"} /></label>
      <small className="muted">仅用于文件尚未被列表发现时；提交时后端仍会校验用途目录、扩展名和越界。</small>
    </details>
  </div>;
}
