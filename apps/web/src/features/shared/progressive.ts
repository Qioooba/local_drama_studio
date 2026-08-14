export const INITIAL_LIST_WINDOW = 50;

export function progressiveSlice<T>(items: T[], visibleCount: number, selectedIndex = -1): T[] {
  return items.slice(0, Math.max(visibleCount, selectedIndex + 1));
}
