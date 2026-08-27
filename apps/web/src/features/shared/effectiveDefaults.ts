export type ConfigurationSource = "PROFILE" | "PROJECT" | "CURRENT_VERSION" | "SAFE_FALLBACK";

export type SourcedValue<T> = {
  value: T;
  source: ConfigurationSource;
  sourceLabel: string;
};

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function normalized(key: string): string {
  return key.replace(/[^a-z0-9]/gi, "").toLowerCase();
}

export function findConfigValue(root: unknown, candidateKeys: string[]): unknown {
  const wanted = new Set(candidateKeys.map(normalized));
  const queue: unknown[] = [root];
  const visited = new Set<unknown>();
  while (queue.length) {
    const current = queue.shift();
    if (!current || typeof current !== "object" || visited.has(current)) continue;
    visited.add(current);
    if (Array.isArray(current)) {
      queue.push(...current);
      continue;
    }
    for (const [key, value] of Object.entries(current as Record<string, unknown>)) {
      if (wanted.has(normalized(key)) && value !== undefined && value !== null && value !== "") return value;
      if (value && typeof value === "object") queue.push(value);
    }
  }
  return undefined;
}

export function numberFrom(root: unknown, candidateKeys: string[], fallback: number): number {
  const value = Number(findConfigValue(root, candidateKeys));
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

export function stringFrom(root: unknown, candidateKeys: string[], fallback: string): string {
  const value = findConfigValue(root, candidateKeys);
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

export function schemaProperty(root: unknown, candidateKeys: string[]): Record<string, unknown> {
  const wanted = new Set(candidateKeys.map(normalized));
  const queue: unknown[] = [root];
  const visited = new Set<unknown>();
  while (queue.length) {
    const current = queue.shift();
    if (!current || typeof current !== "object" || visited.has(current)) continue;
    visited.add(current);
    const record = asRecord(current);
    const properties = asRecord(record.properties);
    for (const [key, value] of Object.entries(properties)) {
      if (wanted.has(normalized(key))) return asRecord(value);
      queue.push(value);
    }
    for (const value of Object.values(record)) if (value && typeof value === "object") queue.push(value);
  }
  return {};
}

export function enumFromSchema(root: unknown, candidateKeys: string[], fallback: string[]): string[] {
  const property = schemaProperty(root, candidateKeys);
  const values = Array.isArray(property.enum) ? property.enum.map(String).filter(Boolean) : [];
  return values.length ? [...new Set(values)] : fallback;
}

export function numericOptionsFromSchema(root: unknown, candidateKeys: string[], fallback: number[]): number[] {
  const property = schemaProperty(root, candidateKeys);
  const values = Array.isArray(property.enum) ? property.enum.map(Number).filter((value) => Number.isFinite(value) && value > 0) : [];
  return values.length ? [...new Set(values)] : fallback;
}

export function configurationSourceLabel(source: ConfigurationSource): string {
  return source === "PROFILE" ? "来自所选模型配置"
    : source === "PROJECT" ? "继承当前项目"
      : source === "CURRENT_VERSION" ? "来自当前生效版本"
        : "安全缺省值（可修改）";
}

export function resolutionFromPlan(plan: unknown, fallback = { width: 1080, height: 1920, fps: 24 }) {
  const width = numberFrom(plan, ["width", "output_width", "video_width"], fallback.width);
  const height = numberFrom(plan, ["height", "output_height", "video_height"], fallback.height);
  const fpsValue = findConfigValue(plan, ["fps", "frame_rate", "target_fps"]);
  const fpsRecord = asRecord(fpsValue);
  const fps = typeof fpsValue === "number" ? fpsValue : numberFrom(fpsRecord, ["numerator"], fallback.fps);
  return { width, height, fps };
}
