import { readdir, stat } from "node:fs/promises";
import { resolve } from "node:path";

const assetsDir = resolve("dist", "assets");
const maxChunkBytes = 500 * 1024;
const chunks = (await readdir(assetsDir)).filter((name) => name.endsWith(".js"));
const sizes = await Promise.all(chunks.map(async (name) => ({ name, bytes: (await stat(resolve(assetsDir, name))).size })));
const oversized = sizes.filter(({ bytes }) => bytes > maxChunkBytes);

if (oversized.length > 0) {
  const details = oversized.map(({ name, bytes }) => `${name}: ${(bytes / 1024).toFixed(1)} KiB`).join("\n");
  throw new Error(`JavaScript chunk budget exceeded (500 KiB):\n${details}`);
}

const largest = sizes.sort((left, right) => right.bytes - left.bytes)[0];
console.log(`bundle budget PASS: ${chunks.length} chunks; largest ${largest?.name ?? "none"} ${((largest?.bytes ?? 0) / 1024).toFixed(1)} KiB`);
