import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { LegacyRouteBoundary, legacyCompatibilityLinks, resolveLegacyRoute } from "./legacyRoute";

describe("legacy query route V2 decommission compatibility", () => {
  afterEach(() => cleanup());
  it.each([
    ["", "/projects"],
    ["?view=overview", "/projects"],
    ["?view=overview&project=project-1", "/projects/project-1"],
    ["?view=projects", "/projects"],
    ["?view=projects&project=project-1", "/projects/project-1"],
    ["?view=projects&project=project-1&episode=episode-2", "/projects/project-1/episodes/episode-2/plan"],
    ["?view=generation&project=project-1&episode=episode-2", "/projects/project-1/episodes/episode-2/studio?focus=generate"],
    ["?view=generation&project=project-1&episode=episode-2&shot=shot-3", "/projects/project-1/episodes/episode-2/studio/shot-3?focus=generate"],
    ["?view=reviews&project=project-1&episode=episode-2", "/projects/project-1/episodes/episode-2/post/review"],
    ["?view=canvas&project=project-1&episode=episode-2", "/projects/project-1/episodes/episode-2/production"],
    ["?view=profiles", "/system/capabilities"],
    ["?view=profiles&project=project-1", "/projects/project-1/settings/capabilities"],
    ["?view=jobs&project=project-1", "/system/jobs?project=project-1"],
    ["?view=diagnostics&project=project-1", "/system/diagnostics?project=project-1"],
  ])("maps %s to a lossless V2 route", (search, expected) => {
    expect(resolveLegacyRoute(search)).toBe(expected);
  });

  it("encodes legacy entity ids in both path and query destinations", () => {
    expect(resolveLegacyRoute("?view=projects&project=project%2Funsafe")).toBe("/projects/project%2Funsafe");
    expect(resolveLegacyRoute("?view=canvas&project=p%2F1&episode=e%2F2")).toBe("/projects/p%2F1/episodes/e%2F2/production");
  });

  it.each([
    "?view=unknown&project=project-1",
    "?view=generation&project=project-1",
    "?view=reviews&project=project-1&episode=episode-2&review=version-9",
    "?view=reviews&project=project-1&episode=episode-2&shot=shot-3",
    "?view=canvas&project=project-1&episode=episode-2&shot=shot-3",
    "?view=projects&project=project-1&episode=episode-2&shot=shot-3",
    "?view=projects&project=project-1&legacy=1",
  ])("uses the V2 chooser for unsupported or precision-sensitive URL %s", (search) => {
    expect(resolveLegacyRoute(search)).toBeNull();
    expect(legacyCompatibilityLinks(search).some((item) => item.to.startsWith("/projects"))).toBe(true);
  });

  it("redirects a lossless URL and renders a context-preserving V2 chooser otherwise", async () => {
    const routeTree = (entry: string) => render(
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/" element={<LegacyRouteBoundary />} />
          <Route path="/system/jobs" element={<div>jobs-v2</div>} />
        </Routes>
      </MemoryRouter>,
    );
    routeTree("/?view=jobs");
    expect(await screen.findByText("jobs-v2")).toBeTruthy();
    cleanup();
    routeTree("/?view=reviews&project=project-1&episode=episode-2&review=version-9");
    expect(await screen.findByRole("heading", { name: "旧链接需要选择新的工作区" })).toBeTruthy();
    expect(screen.getByText("version-9")).toBeTruthy();
    expect(screen.getByRole("link", { name: "打开本集审核" }).getAttribute("href")).toBe("/projects/project-1/episodes/episode-2/post/review");
    expect(screen.queryByText("legacy-shell")).toBeNull();
  });
});
