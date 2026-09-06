import { useId, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listProjectMedia, mediaDisplayName, type MediaCatalogueItem } from "./mediaPickerClient";
import { MEDIA_KIND_LABELS, MEDIA_STAGE_LABELS, optionLabel } from "../shared/optionLabels";
import "./project-media-version-select.css";

type Props = {
  projectId?: string;
  value: string;
  onChange: (mediaVersionId: string) => void;
  label: string;
  mediaKinds?: Array<"IMAGE" | "VIDEO" | "AUDIO">;
  disabled?: boolean;
  required?: boolean;
};

export function ProjectMediaVersionSelect({ projectId, value, onChange, label, mediaKinds = ["IMAGE", "VIDEO", "AUDIO"], disabled = false, required = false }: Props) {
  const id = useId();
  const [query, setQuery] = useState("");
  const kindsKey = mediaKinds.join(",");
  const catalogue = useQuery({
    queryKey: ["project-media-version-select", projectId, kindsKey, query],
    queryFn: async () => (await Promise.all(mediaKinds.map((kind) => listProjectMedia(projectId!, query, kind)))).flat(),
    enabled: Boolean(projectId),
  });
  const options = useMemo(() => {
    const unique = new Map<string, MediaCatalogueItem>();
    for (const item of catalogue.data ?? []) unique.set(item.media_version_id, item);
    return [...unique.values()];
  }, [catalogue.data]);

  return <div className="project-media-version-select"><label htmlFor={`${id}-search`}>搜索媒体</label>
  <input id={`${id}-search`} aria-label={`搜索${label}`} type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="人物名、FRONT / LEFT / RIGHT、文件名" disabled={disabled || !projectId} />
  <label htmlFor={id}>{label}</label><select id={id} value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled || !projectId || catalogue.isLoading} required={required}>
    <option value="">{!projectId ? "请先选择项目" : catalogue.isLoading ? "正在读取项目媒体…" : options.length ? "选择不可变媒体版本" : "项目内暂无匹配媒体"}</option>
    {value && !options.some((item) => item.media_version_id === value) && <option value={value}>已选择的媒体版本 · {value.slice(0, 12)}（不在当前搜索结果中）</option>}
    {options.map((item) => <option key={item.media_version_id} value={item.media_version_id}>{mediaDisplayName(item)} · 第 {item.version_no} 版 · {optionLabel(MEDIA_KIND_LABELS, item.media_kind)} · {optionLabel(MEDIA_STAGE_LABELS, item.stage)}</option>)}
  </select>
  {catalogue.error && <small className="inline-error" role="alert">媒体列表读取失败：{String(catalogue.error)}</small>}
  {value && <small>已绑定不可变版本：{value.slice(0, 12)}</small>}</div>;
}
