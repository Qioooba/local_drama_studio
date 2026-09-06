export type ProjectLabelInput = { id: string; title: string; code?: string | null };

/**
 * Keep the common case readable while making same-title projects selectable.
 * Codes are the preferred stable discriminator; the short id is only a
 * fallback (or a tie-breaker when duplicate projects also share a code).
 */
export function projectDisplayLabels(projects: readonly ProjectLabelInput[]): Map<string, string> {
  const titleCounts = new Map<string, number>();
  const codeCounts = new Map<string, number>();
  for (const project of projects) {
    const title = project.title.trim();
    if (title) titleCounts.set(title, (titleCounts.get(title) ?? 0) + 1);
    const code = project.code?.trim();
    if (title && code) {
      const key = `${title}\u0000${code}`;
      codeCounts.set(key, (codeCounts.get(key) ?? 0) + 1);
    }
  }

  const labels = new Map<string, string>();
  for (const project of projects) {
    const title = project.title.trim();
    const code = project.code?.trim();
    const stableId = project.id.trim().slice(0, 12);
    const base = title || code || `项目 ${stableId.slice(0, 8)}`;
    if (!title || (titleCounts.get(title) ?? 0) < 2) {
      labels.set(project.id, base);
      continue;
    }
    const codeKey = `${title}\u0000${code ?? ""}`;
    const suffix = code && (codeCounts.get(codeKey) ?? 0) === 1 ? code : stableId;
    labels.set(project.id, `${base} · ${suffix}`);
  }
  return labels;
}
