export function selectedItemOrFirst<T extends { id: string }>(items: T[] | undefined, selectedId: string | null): T | null {
  if (!items?.length) return null;
  return items.find((item) => item.id === selectedId) ?? items[0];
}
