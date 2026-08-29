import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  discoverOllamaModels,
  type OllamaModelCatalogItem,
} from "../story-workspace-v2/breakdownClient";
import {
  classifyOllamaModel,
  countOllamaModels,
  filterOllamaModels,
  formatOllamaModelSize,
  formatOllamaScanTime,
  OLLAMA_MODEL_CATEGORIES,
  type OllamaModelCategoryFilter,
} from "./ollamaModelCatalogUtils";

interface OllamaModelCatalogProps {
  selectedModel: string;
  selectedProvider: string;
  onSelect: (item: OllamaModelCatalogItem, baseUrl: string) => void;
}

export function OllamaModelCatalog({ selectedModel, selectedProvider, onSelect }: OllamaModelCatalogProps) {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<OllamaModelCategoryFilter>("all");
  const catalogQuery = useQuery({
    queryKey: ["local-llm-model-catalog"],
    queryFn: () => discoverOllamaModels(),
    staleTime: 30_000,
  });
  const items = catalogQuery.data?.catalog.items ?? [];
  const counts = useMemo(() => countOllamaModels(items), [items]);
  const visibleModels = useMemo(() => filterOllamaModels(items, category, search), [category, items, search]);

  return (
    <section className="ollama-catalog" aria-labelledby="ollama-catalog-title">
      <div className="ollama-catalog__header">
        <div>
          <div className="ollama-catalog__title-line">
            <h4 id="ollama-catalog-title">本机 Ollama 模型</h4>
            {!catalogQuery.isPending && !catalogQuery.error && <span className="model-count-badge">{counts.all} 个</span>}
          </div>
          <p>页面打开后自动读取 Ollama 模型目录；选择一个模型即可带入下方配置。</p>
        </div>
        <div className="ollama-catalog__runtime">
          <span className={`runtime-dot ${catalogQuery.error ? "offline" : catalogQuery.isPending || catalogQuery.isFetching ? "checking" : "online"}`} aria-hidden="true" />
          <div><strong>{catalogQuery.error ? "Ollama 未连接" : catalogQuery.isPending ? "正在连接 Ollama" : "Ollama 已连接"}</strong><small>{catalogQuery.data?.catalog.base_url ?? "http://127.0.0.1:11434"} · {formatOllamaScanTime(catalogQuery.data?.catalog.scanned_at)}</small></div>
          <button type="button" className="secondary compact-button" onClick={() => void catalogQuery.refetch()} disabled={catalogQuery.isFetching} aria-label="重新扫描 Ollama 模型">
            {catalogQuery.isFetching ? "扫描中…" : "重新扫描"}
          </button>
        </div>
      </div>

      {catalogQuery.isPending ? (
        <div className="model-catalog-loading" role="status" aria-live="polite"><span /><div><strong>正在扫描本机模型</strong><small>读取 Ollama /api/tags，通常几秒内完成</small></div></div>
      ) : catalogQuery.error ? (
        <div className="model-catalog-message error" role="alert"><div><strong>暂时无法读取本机 Ollama</strong><p>请确认 Ollama 已启动并监听 127.0.0.1:11434，然后重新扫描。现有配置不会受到影响。</p></div><button type="button" className="secondary" onClick={() => void catalogQuery.refetch()}>重试连接</button></div>
      ) : counts.all === 0 ? (
        <div className="model-catalog-message empty"><div><strong>Ollama 已连接，但还没有安装模型</strong><p>先在终端使用 <code>ollama pull 模型名</code> 安装；完成后点击“重新扫描”。</p></div></div>
      ) : (
        <>
          <div className="model-catalog-toolbar">
            <div className="model-category-tabs" role="group" aria-label="按模型用途筛选">
              {OLLAMA_MODEL_CATEGORIES.map((entry) => <button key={entry.id} type="button" aria-pressed={category === entry.id} onClick={() => setCategory(entry.id)}>{entry.label}<span>{counts[entry.id]}</span></button>)}
            </div>
            <label className="model-search"><span>搜索模型</span><input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="名称、参数量或量化" /></label>
          </div>
          <p className="model-category-description">{OLLAMA_MODEL_CATEGORIES.find((item) => item.id === category)?.description}</p>
          {visibleModels.length === 0 ? <div className="model-filter-empty">没有匹配的模型。请清除搜索词或切换分类。</div> : (
            <ul className="ollama-model-grid" aria-label="Ollama 模型列表">
              {visibleModels.map((item) => {
                const modelCategory = classifyOllamaModel(item);
                const selected = selectedProvider === "OLLAMA_LOOPBACK" && selectedModel === item.name;
                return <li key={item.name}><button type="button" className={`ollama-model-card${selected ? " selected" : ""}`} aria-pressed={selected} onClick={() => onSelect(item, catalogQuery.data!.catalog.base_url)}>
                    <span className="ollama-model-card__top"><strong>{item.name}</strong><span className={`model-kind ${modelCategory}`}>{OLLAMA_MODEL_CATEGORIES.find((entry) => entry.id === modelCategory)?.label}</span></span>
                    <span className="ollama-model-card__meta"><span>{item.parameter_size || "参数量未知"}</span><span>{item.quantization_level || item.format || "格式未知"}</span><span>{formatOllamaModelSize(item.size_bytes)}</span></span>
                    <span className="ollama-model-card__footer"><span>{item.family || "Ollama 模型"}</span><strong>{selected ? "已选择" : "选择此模型"}</strong></span>
                  </button></li>;
              })}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
