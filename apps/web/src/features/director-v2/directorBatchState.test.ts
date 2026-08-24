import { beforeEach, describe, expect, it } from "vitest";
import { persistDirectorBatch, readDirectorBatch, removeDirectorBatch, updateDirectorBatchDone } from "./directorBatchState";

describe("director batch local state", () => {
  beforeEach(() => window.localStorage.clear());

  it("keeps shot and completion collections out of the URL reference", () => {
    const reference = persistDirectorBatch("episode-1", ["shot-1", "shot-2", "shot-2"]);
    expect(reference).toMatch(/^ref:/);
    expect(reference).not.toContain("shot-1");
    expect(readDirectorBatch(reference, "episode-1")).toMatchObject({ shotIds: ["shot-1", "shot-2"], doneIds: [] });
    expect(updateDirectorBatchDone(reference, "episode-1", ["shot-2", "unknown"])).toBe(true);
    expect(readDirectorBatch(reference, "episode-1").doneIds).toEqual(["shot-2"]);
  });

  it("keeps old comma-separated batch links readable and removes referenced local state", () => {
    expect(readDirectorBatch("shot-1,shot-2", "episode-1", "shot-2")).toMatchObject({ shotIds: ["shot-1", "shot-2"], doneIds: ["shot-2"] });
    const reference = persistDirectorBatch("episode-1", ["shot-1"]);
    removeDirectorBatch(reference);
    expect(readDirectorBatch(reference, "episode-1").shotIds).toEqual([]);
  });
});
