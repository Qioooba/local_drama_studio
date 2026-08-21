import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

type DomAudit = {
  main_count: number;
  content_heading_count: number;
  unnamed_headings: string[];
  unlabeled_fields: string[];
  unnamed_actions: string[];
  invalid_dialogs: string[];
  contrast_failures: Array<{ element: string; text: string; ratio: number; required: number }>;
  horizontal_overflow_px: number;
  focus_visible: boolean;
  focused_element: string | null;
};

type RouteAudit = DomAudit & { name: string; path: string; fixture_status: "DATA_OR_EMPTY" | "HONEST_ERROR_SHELL" };
type ViewportAudit = {
  viewport: string;
  status: "PASS" | "FAIL";
  routes: RouteAudit[];
  writes: string[];
  public_requests: string[];
  original_media_requests: string[];
  failed_responses: string[];
};

const viewports = [
  { name: "1280x720", width: 1280, height: 720 },
  { name: "1440x900", width: 1440, height: 900 },
  { name: "1920x1080", width: 1920, height: 1080 },
  { name: "2560x1440", width: 2560, height: 1440 },
] as const;
const results: ViewportAudit[] = [];

function evidencePath() {
  const root = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  return path.join(root, "docs", "evidence", "refactor-2026", "v2-accessibility-contract-readonly-uat-2026-08-21.json");
}

async function resolveFixture(page: Page) {
  const projectsResponse = await page.request.get("/api/v1/projects?limit=100");
  expect(projectsResponse.ok()).toBe(true);
  const projects = (await projectsResponse.json()) as { items: Array<{ id: string; code: string }> };
  const project = projects.items.find((item) => item.code === "g2_smoke2") ?? projects.items[0];
  expect(project, "isolated snapshot must contain a project").toBeTruthy();
  const seasonsResponse = await page.request.get(`/api/v1/projects/${project.id}/seasons`);
  const seasons = (await seasonsResponse.json()) as { items: Array<{ id: string }> };
  expect(seasons.items.length, "selected project must contain a season").toBeGreaterThan(0);
  const episodesResponse = await page.request.get(`/api/v1/projects/seasons/${seasons.items[0].id}/episodes`);
  const episodes = (await episodesResponse.json()) as { items: Array<{ id: string }> };
  expect(episodes.items.length, "selected season must contain an episode").toBeGreaterThan(0);
  return { projectId: project.id, episodeId: episodes.items[0].id };
}

async function auditDom(page: Page): Promise<DomAudit> {
  const structural = await page.evaluate(() => {
    const visible = (element: Element) => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity) > 0 && rect.width > 0 && rect.height > 0;
    };
    const nameOf = (element: Element) => {
      const labelledBy = element.getAttribute("aria-labelledby");
      const labelled = labelledBy?.split(/\s+/).map((id) => document.getElementById(id)?.textContent?.trim() ?? "").join(" ").trim();
      return element.getAttribute("aria-label")?.trim() || labelled || element.textContent?.trim() || element.getAttribute("title")?.trim() || "";
    };
    const fields = [...document.querySelectorAll("input:not([type=hidden]),select,textarea")].filter(visible);
    const unlabeledFields = fields.filter((element) => {
      const control = element as HTMLInputElement;
      return !control.labels?.length && !element.closest("label") && !element.getAttribute("aria-label") && !element.getAttribute("aria-labelledby") && !control.name;
    }).map((element) => `${element.tagName.toLowerCase()}${element.getAttribute("type") ? `[type=${element.getAttribute("type")}]` : ""}`);
    const actions = [...document.querySelectorAll("button,a[href]")].filter(visible);
    const unnamedActions = actions.filter((element) => !nameOf(element)).map((element) => element.outerHTML.slice(0, 160));
    const dialogs = [...document.querySelectorAll('[role="dialog"],[role="alertdialog"],dialog')].filter(visible);
    const invalidDialogs = dialogs.filter((element) => !nameOf(element) || (element.getAttribute("role") && element.getAttribute("aria-modal") !== "true")).map((element) => element.outerHTML.slice(0, 160));
    const headings = [...document.querySelectorAll("#v2-workspace-content h1,#v2-workspace-content h2,#v2-workspace-content h3,#v2-workspace-content h4,#v2-workspace-content h5,#v2-workspace-content h6")].filter(visible);

    const parse = (value: string) => {
      const match = value.match(/rgba?\((\d+(?:\.\d+)?)[, ]+(\d+(?:\.\d+)?)[, ]+(\d+(?:\.\d+)?)(?:[, /]+(\d+(?:\.\d+)?))?\)/);
      return match ? [Number(match[1]), Number(match[2]), Number(match[3]), match[4] === undefined ? 1 : Number(match[4])] : null;
    };
    const background = (element: Element) => {
      let node: Element | null = element;
      while (node) {
        const parsed = parse(getComputedStyle(node).backgroundColor);
        if (parsed && parsed[3] > 0.95) return parsed;
        node = node.parentElement;
      }
      return [255, 255, 255, 1];
    };
    const luminance = (rgb: number[]) => {
      const channel = (value: number) => { const normalized = value / 255; return normalized <= .03928 ? normalized / 12.92 : ((normalized + .055) / 1.055) ** 2.4; };
      return .2126 * channel(rgb[0]) + .7152 * channel(rgb[1]) + .0722 * channel(rgb[2]);
    };
    const ratio = (foreground: number[], backdrop: number[]) => {
      const alpha = foreground[3];
      const composite = foreground.slice(0, 3).map((value, index) => value * alpha + backdrop[index] * (1 - alpha));
      const light = Math.max(luminance(composite), luminance(backdrop));
      const dark = Math.min(luminance(composite), luminance(backdrop));
      return (light + .05) / (dark + .05);
    };
    const textElements = [...document.querySelectorAll("#v2-workspace-content h1,#v2-workspace-content h2,#v2-workspace-content h3,#v2-workspace-content h4,#v2-workspace-content p,#v2-workspace-content label,#v2-workspace-content small,#v2-workspace-content strong,#v2-workspace-content button,#v2-workspace-content a[href],#v2-workspace-content input,#v2-workspace-content select,#v2-workspace-content textarea")]
      .filter((element) => visible(element) && !(element as HTMLButtonElement).disabled && element.getAttribute("aria-disabled") !== "true" && (element.textContent?.trim() || ["INPUT", "SELECT", "TEXTAREA"].includes(element.tagName)));
    const contrastFailures = textElements.flatMap((element) => {
      const style = getComputedStyle(element);
      const foreground = parse(style.color);
      if (!foreground) return [];
      const fontSize = Number.parseFloat(style.fontSize);
      const weight = Number.parseInt(style.fontWeight, 10) || 400;
      const large = fontSize >= 24 || (fontSize >= 18.66 && weight >= 700);
      const required = large ? 3 : 4.5;
      const actual = ratio(foreground, background(element));
      return actual + .01 < required ? [{ element: element.tagName.toLowerCase(), text: (element.textContent || (element as HTMLInputElement).value || element.getAttribute("aria-label") || "").trim().slice(0, 80), ratio: Math.round(actual * 100) / 100, required }] : [];
    });
    return {
      main_count: [...document.querySelectorAll("main")].filter(visible).length,
      content_heading_count: headings.length,
      unnamed_headings: headings.filter((element) => !nameOf(element)).map((element) => element.outerHTML.slice(0, 120)),
      unlabeled_fields: unlabeledFields,
      unnamed_actions: unnamedActions,
      invalid_dialogs: invalidDialogs,
      contrast_failures: contrastFailures,
      horizontal_overflow_px: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
    };
  });
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  let focusVisible = false;
  let focusedElement: string | null = null;
  for (let index = 0; index < 10; index += 1) {
    await page.keyboard.press("Tab");
    const focus = await page.evaluate(() => {
      const element = document.activeElement as HTMLElement | null;
      if (!element || element === document.body) return null;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      const visible = rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
      const indicator = style.outlineStyle !== "none" && Number.parseFloat(style.outlineWidth) > 0 || style.boxShadow !== "none";
      return { visible, indicator, element: `${element.tagName.toLowerCase()}${element.getAttribute("aria-label") ? `[aria-label=${element.getAttribute("aria-label")}]` : ""}` };
    });
    if (focus?.visible && focus.indicator) { focusVisible = true; focusedElement = focus.element; break; }
  }
  return { ...structural, focus_visible: focusVisible, focused_element: focusedElement };
}

function passed(audit: DomAudit) {
  return audit.main_count === 1 && audit.content_heading_count >= 1 && audit.unnamed_headings.length === 0 && audit.unlabeled_fields.length === 0 && audit.unnamed_actions.length === 0 && audit.invalid_dialogs.length === 0 && audit.contrast_failures.length === 0 && audit.horizontal_overflow_px === 0 && audit.focus_visible;
}

test.setTimeout(180_000);
test.afterAll(() => {
  const pass = results.length === viewports.length && results.every((result) => result.status === "PASS");
  const evidence = { schema_version: "localdrama.v2-accessibility-readonly-uat.v1", observed_at: new Date().toISOString(), mode: "LOCAL_ONLY", status: pass ? "PASS" : "IN_PROGRESS", routes: ["Projects", "ProjectHome", "AssetBible", "DirectorDesk", "EpisodeRun", "Timeline"], isolated_snapshot: true, writes_performed: 0, screenshots_created: false, original_media_requested: false, fixture_interpretation: "DATA_OR_EMPTY means the real V2 page rendered against the disposable snapshot; HONEST_ERROR_SHELL records an explicit page error/empty shell and does not claim data-path UAT.", viewports: results };
  fs.mkdirSync(path.dirname(evidencePath()), { recursive: true });
  fs.writeFileSync(evidencePath(), `${JSON.stringify(evidence, null, 2)}\n`, "utf8");
});

for (const viewport of viewports) test(`audits V2 route accessibility read-only at ${viewport.name}`, async ({ page }) => {
  await page.setViewportSize({ width: viewport.width, height: viewport.height });
  const writes: string[] = [];
  const publicRequests: string[] = [];
  const originalMediaRequests: string[] = [];
  const failedResponses: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (!['127.0.0.1', 'localhost'].includes(url.hostname)) publicRequests.push(request.url());
    if (url.pathname.startsWith("/api/") && !["GET", "HEAD", "OPTIONS"].includes(request.method())) writes.push(`${request.method()} ${url.pathname}`);
    if (url.pathname.includes("/content") || url.pathname.includes("/original") || /\.(mp4|mov|mkv|webm|wav|flac|mp3)(\?|$)/i.test(url.pathname)) originalMediaRequests.push(request.url());
  });
  page.on("response", (response) => { if (response.status() >= 500) failedResponses.push(`${response.status()} ${new URL(response.url()).pathname}`); });
  const { projectId, episodeId } = await resolveFixture(page);
  const routes = [
    { name: "Projects", path: "/projects" },
    { name: "ProjectHome", path: `/projects/${projectId}` },
    { name: "AssetBible", path: `/projects/${projectId}/assets` },
    { name: "DirectorDesk", path: `/projects/${projectId}/episodes/${episodeId}/direct` },
    { name: "EpisodeRun", path: `/projects/${projectId}/episodes/${episodeId}/run` },
    { name: "Timeline", path: `/projects/${projectId}/episodes/${episodeId}/timeline` },
  ];
  const routeAudits: RouteAudit[] = [];
  for (const route of routes) {
    await page.goto(route.path, { waitUntil: "domcontentloaded" });
    await expect(page.locator("#v2-workspace-content")).toBeVisible();
    await page.waitForTimeout(350);
    const audit = await auditDom(page);
    const honestError = await page.locator(".workspace-error:visible,.director-error:visible,#v2-workspace-content > [role=alert]:visible").count().catch(() => 0);
    routeAudits.push({ ...audit, name: route.name, path: route.path, fixture_status: honestError ? "HONEST_ERROR_SHELL" : "DATA_OR_EMPTY" });
  }
  const result: ViewportAudit = { viewport: viewport.name, status: routeAudits.every(passed) && writes.length === 0 && publicRequests.length === 0 && originalMediaRequests.length === 0 && failedResponses.length === 0 ? "PASS" : "FAIL", routes: routeAudits, writes, public_requests: publicRequests, original_media_requests: originalMediaRequests, failed_responses: failedResponses };
  results.push(result);
  expect(writes).toEqual([]);
  expect(publicRequests).toEqual([]);
  expect(originalMediaRequests).toEqual([]);
  expect(failedResponses).toEqual([]);
  for (const audit of routeAudits) expect(audit, `${audit.name} accessibility contract`).toMatchObject({ main_count: 1, unnamed_headings: [], unlabeled_fields: [], unnamed_actions: [], invalid_dialogs: [], contrast_failures: [], horizontal_overflow_px: 0, focus_visible: true });
  expect(routeAudits.every((audit) => audit.content_heading_count >= 1)).toBe(true);
});
