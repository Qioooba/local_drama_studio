import type { OllamaModelCatalogItem } from "../story-workspace-v2/breakdownClient";

export type OllamaModelCategory = "reasoning" | "vision" | "text" | "embedding";
export type OllamaModelCategoryFilter = "all" | OllamaModelCategory;

export const OLLAMA_MODEL_CATEGORIES: Array<{
  id: OllamaModelCategoryFilter;
  label: string;
  description: string;
}> = [
  { id: "all", label: "全部", description: "本机已安装的全部 Ollama 模型" },
  { id: "reasoning", label: "推理与策划", description: "适合复杂推理、策划和剧本拆解" },
  { id: "vision", label: "视觉理解", description: "可处理图像内容和视觉质检" },
  { id: "text", label: "通用文本", description: "适合写作、对话和结构化文本" },
  { id: "embedding", label: "向量模型", description: "用于检索和相似度计算，不用于内容生成" },
];

export function classifyOllamaModel(item: OllamaModelCatalogItem): OllamaModelCategory {
  const haystack = [item.name, item.family, ...item.families].join(" ").toLowerCase();
  if (/(embed|embedding|nomic|bge|e5(?:-|:|$)|gte(?:-|:|$)|all-minilm)/.test(haystack)) return "embedding";
  if (/(vision|\bvl\b|qwen[^ ]*-vl|llava|moondream|minicpm-v|bakllava)/.test(haystack)) return "vision";
  if (/(deepseek-r1|qwq|reason|thinking|qwen3)/.test(haystack)) return "reasoning";
  return "text";
}

export function countOllamaModels(items: OllamaModelCatalogItem[]): Record<OllamaModelCategoryFilter, number> {
  const counts: Record<OllamaModelCategoryFilter, number> = { all: 0, reasoning: 0, vision: 0, text: 0, embedding: 0 };
  for (const item of items) {
    counts.all += 1;
    counts[classifyOllamaModel(item)] += 1;
  }
  return counts;
}

export function filterOllamaModels(
  items: OllamaModelCatalogItem[],
  category: OllamaModelCategoryFilter,
  search: string,
): OllamaModelCatalogItem[] {
  const normalizedSearch = search.trim().toLowerCase();
  return items.filter((item) => {
    const categoryMatches = category === "all" || classifyOllamaModel(item) === category;
    const searchMatches = !normalizedSearch || [item.name, item.family, item.parameter_size, item.quantization_level]
      .join(" ").toLowerCase().includes(normalizedSearch);
    return categoryMatches && searchMatches;
  });
}

export function formatOllamaModelSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "大小未知";
  const gib = bytes / 1024 ** 3;
  return gib >= 1 ? `${gib.toFixed(gib >= 10 ? 0 : 1)} GB` : `${Math.max(1, Math.round(bytes / 1024 ** 2))} MB`;
}

export function formatOllamaScanTime(value?: string): string {
  if (!value) return "尚未扫描";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "刚刚扫描";
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(date);
}
