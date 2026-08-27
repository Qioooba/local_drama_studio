// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const sourceRoot = join(process.cwd(), "src");
const excluded = ["ProjectPackage"];
const files = (directory) => readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
  const path = join(directory, entry.name);
  if (entry.isDirectory()) return files(path);
  const local = relative(sourceRoot, path).replaceAll("\\", "/");
  return path.endsWith(".tsx") && !excluded.some((token) => local.includes(token)) ? [path] : [];
});

describe("frontend media URL policy", () => {
  it("never renders image tags from media content endpoints", () => {
    const violations = files(sourceRoot).flatMap((path) => [...readFileSync(path, "utf8").matchAll(/<img\b[\s\S]*?>/g)]
      .filter((match) => /\/content|contentUrl|content_url/.test(match[0])).map(() => relative(sourceRoot, path)));
    expect(violations).toEqual([]);
  });

  it("keeps video loading opt-in and MediaVersion posters on thumbnails", () => {
    const violations = [];
    for (const path of files(sourceRoot)) {
      for (const match of readFileSync(path, "utf8").matchAll(/<video\b[\s\S]*?\/>/g)) {
        const tag = match[0]; const local = relative(sourceRoot, path).replaceAll("\\", "/");
        if (/preload=["'](?:auto|metadata)["']/.test(tag)) violations.push(`${local}: eager preload`);
        const requiresPoster = /media-versions|episode-renders|contentUrl|directorContentUrl/.test(tag);
        if (requiresPoster && !/poster=/.test(tag)) violations.push(`${local}: missing poster`);
        if (/poster=/.test(tag) && !/thumbnail|thumbnailUrl|directorThumbnailUrl/.test(tag)) violations.push(`${local}: non-thumbnail poster`);
      }
    }
    expect(violations).toEqual([]);
  });
});
