import { test } from "@playwright/test";

test("probe generation workbench blockers", async ({ page }) => {
  const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
  const episodeId = "d4db1033-9517-4bd0-958d-4228e0abead1";
  const shotId = "020f9248-14b7-4f92-9edd-ee587ffdedf3";
  await page.goto(`/?view=generation&project=${projectId}&episode=${episodeId}&shot=${shotId}`, { waitUntil: "networkidle", timeout: 60_000 });
  await page.waitForTimeout(2_000);
  const capability = await page.locator(".capability-panel").innerText();
  const shotSelect = await page.locator("#generation-shot option").allTextContents();
  const profileSelect = await page.locator("#generation-profile option").allTextContents();
  const keyframeSelect = await page.locator("#generation-keyframe option").allTextContents();
  const preflightSummary = await page.locator(".preflight-summary").innerText();
  // eslint-disable-next-line no-console
  console.log("PROBE_CAPABILITY " + JSON.stringify(capability));
  // eslint-disable-next-line no-console
  console.log("PROBE_SHOTS " + JSON.stringify(shotSelect));
  // eslint-disable-next-line no-console
  console.log("PROBE_PROFILES " + JSON.stringify(profileSelect));
  // eslint-disable-next-line no-console
  console.log("PROBE_KEYFRAMES " + JSON.stringify(keyframeSelect));
  // eslint-disable-next-line no-console
  console.log("PROBE_PREFLIGHT_SUMMARY " + JSON.stringify(preflightSummary));
  // eslint-disable-next-line no-console
  console.log("PROBE_MODE_SELECTED " + (await page.locator(".mode-card.selected strong").allTextContents()));
  // eslint-disable-next-line no-console
  console.log("PROBE_CAMERA " + JSON.stringify(await page.locator(".capability-truth").allInnerTexts()));
});
