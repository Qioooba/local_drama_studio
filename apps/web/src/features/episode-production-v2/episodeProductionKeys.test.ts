import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { episodeProductionKeys, invalidateEpisodeProduction } from "./episodeProductionKeys";

describe("episode production query invalidation", () => {
  it("invalidates the shots parent so all and attention siblings refresh together", async () => {
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue();

    await invalidateEpisodeProduction(client, "episode-1");

    expect(invalidate).toHaveBeenCalledWith({ queryKey: episodeProductionKeys.shots("episode-1") });
    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: episodeProductionKeys.allShots("episode-1") });
    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: episodeProductionKeys.attentionShots("episode-1") });
  });
});
