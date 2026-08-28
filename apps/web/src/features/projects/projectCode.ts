import { pinyin } from "pinyin-pro";

function stableHash(value: string): string {
  const hash = Math.abs([...value].reduce((acc, character) => ((acc << 5) - acc) + character.charCodeAt(0), 0));
  return hash.toString(36).slice(0, 8) || "main";
}

/**
 * Generates a readable, backend-safe project identifier.
 * Existing Latin words are preserved; Chinese characters are romanized to
 * tone-free pinyin so project folders and logs remain recognizable.
 */
export function generateProjectCode(title: string): string {
  const source = title.trim();
  if (!source) return "";

  const romanized = pinyin(source, { toneType: "none", nonZh: "consecutive" });
  let code = romanized
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");

  if (!code) code = `project_${stableHash(source)}`;
  if (!/^[a-z]/.test(code)) code = `project_${code}`;
  return code.slice(0, 64).replace(/_+$/g, "");
}
