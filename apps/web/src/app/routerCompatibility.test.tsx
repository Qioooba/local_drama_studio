import { render, waitFor } from "@testing-library/react";
import { createMemoryRouter, Route, RouterProvider, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { ProjectSystemRouteRedirect } from "./router";

describe("project system route compatibility", () => {
  it.each([
    ["jobs", "jobs"],
    ["lab", "lab"],
  ] as const)("redirects old project-scoped %s bookmarks to the canonical system route and preserves filters", async (legacyPath, workspace) => {
    const router = createMemoryRouter([
      {
        path: "*",
        element: (
          <Routes>
            <Route path={`/projects/:projectId/${legacyPath}`} element={<ProjectSystemRouteRedirect workspace={workspace} />} />
            <Route path={`/${workspace}`} element={<div>canonical workspace</div>} />
          </Routes>
        ),
      },
    ], { initialEntries: [`/projects/project%201/${legacyPath}?job=job-7#attempts`] });
    render(<RouterProvider router={router} />);
    await waitFor(() => expect(router.state.location.pathname).toBe(`/${workspace}`));
    expect(router.state.location.search).toContain("job=job-7");
    expect(router.state.location.search).toContain("project=project+1");
    expect(router.state.location.hash).toBe("#attempts");
  });
});
