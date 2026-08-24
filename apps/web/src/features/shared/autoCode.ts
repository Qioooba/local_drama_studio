const PREFIXES: Record<string, string> = {
  CHARACTER: "CHAR",
  SCENE: "SCENE",
  PROP: "PROP",
  COSTUME: "COSTUME",
};

function stableToken(value: string): string {
  const ascii = value
    .normalize("NFKD")
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (ascii) return ascii.slice(0, 40);
  const hash = Math.abs([...value].reduce((acc, character) => ((acc << 5) - acc) + character.charCodeAt(0), 0));
  return hash.toString(36).toUpperCase().slice(0, 8) || "ITEM";
}

export function generateMachineCode(prefix: string, label: string): string {
  if (!label.trim()) return "";
  return `${prefix.toUpperCase()}_${stableToken(label)}`.slice(0, 80);
}

export function generateAssetCode(kind: string, name: string): string {
  return generateMachineCode(PREFIXES[kind] ?? kind, name);
}

export function nextOrdinalCode(prefix: string, existingCodes: string[], separator = "-"): string {
  const normalizedPrefix = prefix.toUpperCase();
  const pattern = new RegExp(`^${normalizedPrefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}[\\-_](\\d+)$`);
  let width = 2;
  const next = existingCodes.reduce((highest, code) => {
    const match = code.toUpperCase().match(pattern);
    if (match) width = Math.max(width, match[1].length);
    return match ? Math.max(highest, Number(match[1])) : highest;
  }, 0) + 1;
  return `${normalizedPrefix}${separator}${String(next).padStart(width, "0")}`;
}
