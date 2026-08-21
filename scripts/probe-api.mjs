// 接口连通性实测：在浏览器上下文里逐个请求 api.ts 中的全部接口，
// 用真实项目/分集 id 替换路径参数，按响应体区分：
//   ROUTE_MISSING ({"detail":"Not Found"}) / METHOD_MISMATCH (405) / SERVER_500 / 业务4xx / OK
// 用法: node scripts/probe-api.mjs
import { chromium } from "playwright";
import { readFileSync, writeFileSync } from "node:fs";

const EXEC = process.env.PLAYWRIGHT_CHROMIUM_PATH || "C:\\Users\\Qi\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe";
const BASE = "http://127.0.0.1:5173";
const contract = JSON.parse(readFileSync("test-results/api-contract.json", "utf-8"));

const browser = await chromium.launch({ executablePath: EXEC, headless: true });
const page = await browser.newPage();
await page.goto(`${BASE}/?view=overview`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);

// 获取真实上下文 id
const ctx = await page.evaluate(async () => {
  const projects = await fetch("/api/v1/projects?limit=5").then((r) => r.json()).catch(() => ({ items: [] }));
  const projectId = projects.items?.[0]?.id || "00000000-0000-0000-0000-000000000000";
  let seasonId = "", episodeId = "", shotId = "", mediaId = "", artifactId = "";
  if (projectId.startsWith("0000") === false) {
    const seasons = await fetch(`/api/v1/projects/${projectId}/seasons`).then((r) => r.json()).catch(() => ({ items: [] }));
    seasonId = seasons.items?.[0]?.id || "";
    if (seasonId) {
      const eps = await fetch(`/api/v1/projects/seasons/${seasonId}/episodes`).then((r) => r.json()).catch(() => ({ items: [] }));
      episodeId = eps.items?.[0]?.id || "";
    }
    if (episodeId) {
      const prod = await fetch(`/api/v1/episodes/${episodeId}/production`).then((r) => r.json()).catch(() => ({ items: [] }));
      shotId = prod.items?.[0]?.id ? String(prod.items[0].id) : "";
    }
  }
  const jobs = await fetch("/api/v1/jobs?limit=3").then((r) => r.json()).catch(() => ({ items: [] }));
  const jobId = jobs.items?.[0]?.id || "";
  return { projectId, seasonId, episodeId, shotId, jobId };
});
console.log("上下文:", JSON.stringify(ctx));

// 变量值映射（按名称）
const VAL = {
  projectId: ctx.projectId,
  seasonId: ctx.seasonId,
  episodeId: ctx.episodeId,
  shotId: ctx.shotId,
  jobId: ctx.jobId,
  mediaVersionId: "00000000-0000-0000-0000-000000000000",
  id: "00000000-0000-0000-0000-000000000000",
  workflowId: "00000000-0000-0000-0000-000000000000",
  runId: "00000000-0000-0000-0000-000000000000",
  deliveryId: "00000000-0000-0000-0000-000000000000",
  renderId: "00000000-0000-0000-0000-000000000000",
  timelineRevisionId: "00000000-0000-0000-0000-000000000000",
  targetVersionId: "00000000-0000-0000-0000-000000000000",
  artifactId: "00000000-0000-0000-0000-000000000000",
  mediaVersionId2: "00000000-0000-0000-0000-000000000000",
  token: "probe",
  artifact: "00000000-0000-0000-0000-000000000000",
  bindingId: "00000000-0000-0000-0000-000000000000",
  subscriptionId: "00000000-0000-0000-0000-000000000000",
  deliveryId2: "00000000-0000-0000-0000-000000000000",
  grantId: "00000000-0000-0000-0000-000000000000",
  promptId: "00000000-0000-0000-0000-000000000000",
  revisionId: "00000000-0000-0000-0000-000000000000",
  scanId: "00000000-0000-0000-0000-000000000000",
  reviewId: "00000000-0000-0000-0000-000000000000",
  planToken: "probe",
  intentId: "00000000-0000-0000-0000-000000000000",
  variantId: "00000000-0000-0000-0000-000000000000",
  experimentId: "00000000-0000-0000-0000-000000000000",
  clientId: "00000000-0000-0000-0000-000000000000",
  testRunId: "00000000-0000-0000-0000-000000000000",
  episodeId2: ctx.episodeId,
  windowId: "00000000-0000-0000-0000-000000000000",
  proofId: "00000000-0000-0000-0000-000000000000",
  snapshotId: "00000000-0000-0000-0000-000000000000",
  versionId: "00000000-0000-0000-0000-000000000000",
  filePath: "probe",
};

// 路径 id 变量白名单（其余变量视为查询构建变量 → 置空）
const PATH_ID_VARS = new Set([
  "projectId", "project_id", "seasonId", "season_id", "episodeId", "episode_id", "shotId", "shot_id",
  "mediaVersionId", "media_version_id", "jobId", "job_id", "workflowId", "workflow_id", "runId", "run_id",
  "deliveryId", "delivery_id", "renderId", "render_id", "timelineRevisionId", "timeline_revision_id",
  "targetVersionId", "target_version_id", "artifactId", "artifact_id", "id", "token", "subscriptionId",
  "grantId", "grant_id", "promptId", "prompt_id", "revisionId", "revision_id", "intentId", "intent_id",
  "variantId", "variant_id", "experimentId", "experiment_id", "clientId", "client_id", "testRunId",
  "test_run_id", "versionId", "version_id", "proofId", "proof_id", "snapshotId", "snapshot_id",
  "scanId", "scan_id", "reviewId", "review_id", "planToken", "plan_token", "bindingId", "binding_id",
  "mediaVersionId2", "windowId", "window_id", "deliveryId2", "episodeId2", "filePath", "file_path",
]);

function resolveUrl(url) {
  // 平衡扫描替换 ${...}（支持嵌套模板）
  let out = "";
  let i = 0;
  const n = url.length;
  while (i < n) {
    if (url[i] === "$" && url[i + 1] === "{") {
      let depth = 0;
      let j = i;
      while (j < n) {
        if (url[j] === "$" && url[j + 1] === "{") { depth += 1; j += 2; continue; }
        if (url[j] === "}") { depth -= 1; if (depth === 0) break; }
        j += 1;
      }
      const expr = url.slice(i + 2, j);
      // 取最后一个标识符（encodeURIComponent(projectId) → projectId）
      const names = expr.match(/[A-Za-z_][A-Za-z0-9_]*/g) || [];
      const name = names.length ? names[names.length - 1] : "";
      if (PATH_ID_VARS.has(name)) {
        out += VAL[name] !== undefined ? VAL[name] : "00000000-0000-0000-0000-000000000000";
      }
      // 查询构建变量（query/suffix/q/params 等）→ 空
      i = j + 1;
    } else {
      out += url[i];
      i += 1;
    }
  }
  // 去掉残留 query 模板
  out = out.replace(/\?([A-Za-z_]+)=/g, "");
  out = out.replace(/\?[A-Za-z_&=]*$/g, "");
  return out;
}

const results = [];
for (const f of contract.frontend) {
  const url = resolveUrl(f.url);
  const method = f.method;
  try {
    const res = await page.evaluate(async ({ url, method }) => {
      const opts = { method, headers: { "Content-Type": "application/json" } };
      if (method !== "GET" && method !== "HEAD") opts.body = "{}";
      const r = await fetch(url, opts);
      const text = await r.text();
      return { status: r.status, body: text.slice(0, 120) };
    }, { url, method });
    let verdict = "OK";
    if (res.status === 404 && /detail.*Not Found/i.test(res.body)) verdict = "ROUTE_MISSING";
    else if (res.status === 405) verdict = "METHOD_MISMATCH";
    else if (res.status >= 500) verdict = "SERVER_500";
    else if (res.status >= 400) verdict = "BUSINESS_4XX";
    results.push({ fn: f.fn, method, url, status: res.status, verdict, body: res.body.slice(0, 90) });
  } catch (e) {
    results.push({ fn: f.fn, method, url, status: 0, verdict: "FETCH_FAIL", body: String(e).slice(0, 80) });
  }
}

// 汇总
const byVerdict = {};
for (const r of results) byVerdict[r.verdict] = (byVerdict[r.verdict] || 0) + 1;
console.log("=== 实测汇总 ===", JSON.stringify(byVerdict));
const real = results.filter((r) => ["ROUTE_MISSING", "METHOD_MISMATCH", "SERVER_500", "FETCH_FAIL"].includes(r.verdict));
console.log(`=== 真实断链/异常（${real.length}）===`);
for (const r of real) console.log(`  ${r.verdict} ${r.method} ${r.url.slice(0, 90)} :: ${r.body.slice(0, 80)}`);

writeFileSync("test-results/api-probe.json", JSON.stringify({ context: ctx, summary: byVerdict, results }, null, 2));
await browser.close();
console.log("written test-results/api-probe.json");
