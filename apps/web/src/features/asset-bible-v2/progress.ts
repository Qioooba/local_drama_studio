export function normalizeGenerationPercent(value: unknown): number {
  const numeric = Number(value ?? 0);
  if (!Number.isFinite(numeric)) return 0;
  const scaled = numeric > 0 && numeric <= 1 ? numeric * 100 : numeric;
  return Math.min(100, Math.max(0, Math.round(scaled)));
}
