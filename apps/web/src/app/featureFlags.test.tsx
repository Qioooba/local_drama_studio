import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { FeatureFlagRoute, featureEnabled, featureFlagStorageKey } from "./featureFlags";

afterEach(() => localStorage.removeItem(featureFlagStorageKey));

describe("local V2 feature flags", () => {
  it("defaults V2 on and accepts an explicit local disable override", () => {
    expect(featureEnabled("DIRECTOR_DESK_V2")).toBe(true);
    localStorage.setItem(featureFlagStorageKey, JSON.stringify({ DIRECTOR_DESK_V2: false }));
    expect(featureEnabled("DIRECTOR_DESK_V2")).toBe(false);
  });

  it("preserves project and episode context without reviving the retired shell", async () => {
    localStorage.setItem(featureFlagStorageKey, JSON.stringify({ DIRECTOR_DESK_V2: false }));
    const router = createMemoryRouter([
      { path: "/projects/:projectId/episodes/:episodeId/direct/:shotId", element: <FeatureFlagRoute flag="DIRECTOR_DESK_V2" fallbackView="generation"><div>V2</div></FeatureFlagRoute> },
      { path: "/projects/:projectId/episodes/:episodeId/plan", element: <output>V2 episode plan</output> },
    ], { initialEntries: ["/projects/project-1/episodes/episode-2/direct/shot-3"] });
    render(<RouterProvider router={router} />);
    await screen.findByText("V2 episode plan");
    expect(router.state.location.pathname).toBe("/projects/project-1/episodes/episode-2/plan");
    expect(router.state.location.search).toBe("");
  });
});
