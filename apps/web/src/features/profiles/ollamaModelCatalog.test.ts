import { describe, expect, it } from "vitest";
import type { OllamaModelCatalogItem } from "../story-workspace-v2/breakdownClient";
import {
  classifyOllamaModel,
  countOllamaModels,
  filterOllamaModels,
  formatOllamaModelSize,
} from "./ollamaModelCatalogUtils";

function model(name: string, family = "", parameterSize = "8B", quantization = "Q4_K_M"): OllamaModelCatalogItem {
  return {
    name,
    model: name,
    size_bytes: 5_000_000_000,
    digest: "digest",
    format: "gguf",
    family,
    families: family ? [family] : [],
    parameter_size: parameterSize,
    quantization_level: quantization,
  };
}

describe("ollama model catalog", () => {
  const items = [
    model("deepseek-r1:14b", "qwen2", "14.8B"),
    model("qwen2.5-vl:7b", "qwen2vl", "7B"),
    model("nomic-embed-text:latest", "nomic-bert", "137M", "F16"),
    model("gemma2:9b", "gemma2", "9B"),
  ];

  it("assigns one clear primary purpose to each model", () => {
    expect(items.map(classifyOllamaModel)).toEqual(["reasoning", "vision", "embedding", "text"]);
    expect(countOllamaModels(items)).toEqual({ all: 4, reasoning: 1, vision: 1, text: 1, embedding: 1 });
  });

  it("filters by purpose and searchable metadata", () => {
    expect(filterOllamaModels(items, "vision", "").map((item) => item.name)).toEqual(["qwen2.5-vl:7b"]);
    expect(filterOllamaModels(items, "all", "137m").map((item) => item.name)).toEqual(["nomic-embed-text:latest"]);
    expect(filterOllamaModels(items, "all", "q4_k_m")).toHaveLength(3);
  });

  it("formats disk size without overstating precision", () => {
    expect(formatOllamaModelSize(9_000_000_000)).toBe("8.4 GB");
    expect(formatOllamaModelSize(280_000_000)).toBe("267 MB");
    expect(formatOllamaModelSize(0)).toBe("大小未知");
  });
});
