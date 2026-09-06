import { test, expect, Page, ViewportSize } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";

// Test configuration
const BASE_URL = "http://127.0.0.1:5173";
const OUTPUT_DIR = "tests/e2e/output/visual-test";
const REPORT_PATH = "tests/e2e/output/visual-test-report.html";

// Resolution breakpoints to test
const RESOLUTIONS: { name: string; viewport: ViewportSize }[] = [
  { name: "Mobile (375x667)", viewport: { width: 375, height: 667 } },
  { name: "Mobile Landscape (667x375)", viewport: { width: 667, height: 375 } },
  { name: "Tablet (768x1024)", viewport: { width: 768, height: 1024 } },
  { name: "Tablet Landscape (1024x768)", viewport: { width: 1024, height: 768 } },
  { name: "Desktop (1366x768)", viewport: { width: 1366, height: 768 } },
  { name: "Desktop HD (1920x1080)", viewport: { width: 1920, height: 1080 } },
  { name: "Desktop 2K (2560x1440)", viewport: { width: 2560, height: 1440 } },
  { name: "Ultrawide (3440x1440)", viewport: { width: 3440, height: 1440 } },
];

// Pages to test
const PAGES = [
  { name: "Home (工作台)", path: "/" },
  { name: "Projects (项目)", path: "/projects" },
  { name: "Quick Create (快速生成)", path: "/quick-create" },
  { name: "System Capabilities (系统能力)", path: "/system/capabilities" },
  { name: "System Jobs (系统任务)", path: "/system/jobs" },
  { name: "System Diagnostics (系统诊断)", path: "/system/diagnostics" },
  { name: "System Workflows (系统工作流)", path: "/system/workflows" },
];

// Test results storage
interface TestResult {
  page: string;
  resolution: string;
  status: "pass" | "fail" | "warning";
  screenshot?: string;
  errors: string[];
  warnings: string[];
  elements: {
    buttons: number;
    inputs: number;
    textareas: number;
    selects: number;
    links: number;
    images: number;
  };
  layoutIssues: string[];
}

const testResults: TestResult[] = [];

// Helper: ensure output directory exists
function ensureDir(dir: string) {
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

// Helper: wait for page to be stable
async function waitForStable(page: Page, timeout = 1000) {
  await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(timeout);
}

// Helper: check for layout issues
async function checkLayoutIssues(page: Page): Promise<string[]> {
  const issues: string[] = [];
  
  // Check for horizontal overflow
  const hasHorizontalOverflow = await page.evaluate(() => {
    return document.documentElement.scrollWidth > document.documentElement.clientWidth;
  });
  if (hasHorizontalOverflow) {
    issues.push("⚠️ 水平溢出: 页面有水平滚动条");
  }
  
  // Check for elements outside viewport
  const outOfViewElements = await page.evaluate(() => {
    const elements = document.querySelectorAll("*");
    const outOfView: string[] = [];
    elements.forEach((el) => {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        if (rect.right > window.innerWidth + 2 || rect.left < -2) {
          outOfView.push(el.tagName.toLowerCase());
        }
      }
    });
    return [...new Set(outOfView)].slice(0, 5); // Limit to 5 unique types
  });
  if (outOfViewElements.length > 0) {
    issues.push(`⚠️ 元素超出视口: ${outOfViewElements.join(", ")}`);
  }
  
  // Check for text truncation
  const truncatedText = await page.evaluate(() => {
    const elements = document.querySelectorAll("*");
    const truncated: string[] = [];
    elements.forEach((el) => {
      if (el.scrollWidth > el.clientWidth + 4 || el.scrollHeight > el.clientHeight + 4) {
        const style = window.getComputedStyle(el);
        if (style.overflow === "hidden" && style.textOverflow !== "ellipsis" && el.textContent?.trim()) {
          truncated.push(el.textContent.trim().slice(0, 30));
        }
      }
    });
    return [...new Set(truncated)].slice(0, 3);
  });
  if (truncatedText.length > 0) {
    issues.push(`⚠️ 文本截断: ${truncatedText.join(", ")}...`);
  }
  
  return issues;
}

// Helper: count UI elements
async function countElements(page: Page) {
  return await page.evaluate(() => {
    return {
      buttons: document.querySelectorAll("button").length,
      inputs: document.querySelectorAll("input").length,
      textareas: document.querySelectorAll("textarea").length,
      selects: document.querySelectorAll("select").length,
      links: document.querySelectorAll("a").length,
      images: document.querySelectorAll("img").length,
    };
  });
}

// Helper: capture console errors
async function captureConsoleErrors(page: Page): Promise<string[]> {
  const errors: string[] = [];
  
  page.on("console", (msg) => {
    if (msg.type() === "error") {
      const text = msg.text();
      // Ignore some known benign errors
      if (!text.includes("favicon") && !text.includes("404")) {
        errors.push(text);
      }
    }
  });
  
  page.on("pageerror", (err) => {
    errors.push(`Page Error: ${err.message}`);
  });
  
  return errors;
}

// Helper: test a single page at a resolution
async function testPageAtResolution(
  page: Page,
  pageInfo: { name: string; path: string },
  resolution: { name: string; viewport: ViewportSize }
): Promise<TestResult> {
  const result: TestResult = {
    page: pageInfo.name,
    resolution: resolution.name,
    status: "pass",
    errors: [],
    warnings: [],
    elements: { buttons: 0, inputs: 0, textareas: 0, selects: 0, links: 0, images: 0 },
    layoutIssues: [],
  };
  
  try {
    // Set viewport
    await page.setViewportSize(resolution.viewport);
    
    // Navigate and wait
    await page.goto(`${BASE_URL}${pageInfo.path}`, { timeout: 15000 });
    await waitForStable(page);
    
    // Capture console errors
    const errors = await captureConsoleErrors(page);
    result.errors = errors;
    
    // Count elements
    result.elements = await countElements(page);
    
    // Check layout issues
    result.layoutIssues = await checkLayoutIssues(page);
    
    // Take screenshot
    const safeName = pageInfo.name.replace(/[()\s/]/g, "_").replace(/_+/g, "_");
    const safeResolution = resolution.name.replace(/[()]/g, "").replace(/\s+/g, "_");
    const screenshotPath = `${OUTPUT_DIR}/screenshots/${safeName}_${safeResolution}.png`;
    
    ensureDir(`${OUTPUT_DIR}/screenshots`);
    await page.screenshot({ path: screenshotPath, fullPage: false });
    result.screenshot = screenshotPath;
    
    // Determine status
    if (result.errors.length > 0) {
      result.status = "fail";
    } else if (result.layoutIssues.length > 0) {
      result.status = "warning";
    }
    
  } catch (error) {
    result.status = "fail";
    result.errors.push(`Navigation Error: ${(error as Error).message}`);
  }
  
  return result;
}

// Helper: test interactive elements
async function testInteractiveElements(page: Page, pageInfo: { name: string; path: string }) {
  await page.goto(`${BASE_URL}${pageInfo.path}`, { timeout: 15000 });
  await waitForStable(page);
  
  const results: { element: string; action: string; result: string }[] = [];
  
  // Test buttons
  const buttons = await page.locator("button").all();
  for (const button of buttons.slice(0, 10)) { // Test first 10 buttons
    const text = await button.textContent().catch(() => "Unknown");
    try {
      await button.hover();
      results.push({ element: `Button: ${text?.slice(0, 20)}`, action: "hover", result: "success" });
    } catch {
      results.push({ element: `Button: ${text?.slice(0, 20)}`, action: "hover", result: "failed" });
    }
  }
  
  // Test inputs
  const inputs = await page.locator("input").all();
  for (const input of inputs.slice(0, 5)) { // Test first 5 inputs
    const type = await input.getAttribute("type").catch(() => "text");
    try {
      await input.hover();
      results.push({ element: `Input[type=${type}]`, action: "hover", result: "success" });
    } catch {
      results.push({ element: `Input[type=${type}]`, action: "hover", result: "failed" });
    }
  }
  
  return results;
}

// Generate HTML report
function generateReport() {
  ensureDir(OUTPUT_DIR);
  
  const passCount = testResults.filter(r => r.status === "pass").length;
  const failCount = testResults.filter(r => r.status === "fail").length;
  const warningCount = testResults.filter(r => r.status === "warning").length;
  
  const html = `
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>LocalDrama Studio UI 视觉测试报告</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: "Segoe UI", "Microsoft YaHei", sans-serif; background: #0b0e15; color: #f4f6fb; min-height: 100vh; }
    .container { max-width: 1600px; margin: 0 auto; padding: 40px 20px; }
    h1 { font-size: 32px; margin-bottom: 8px; color: #fff; }
    .subtitle { color: #9aa4b5; margin-bottom: 32px; }
    .summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 40px; }
    .summary-card { background: #121620; border: 1px solid rgba(255,255,255,0.11); border-radius: 13px; padding: 24px; text-align: center; }
    .summary-card.pass { border-color: rgba(80, 216, 144, 0.3); }
    .summary-card.fail { border-color: rgba(255, 107, 122, 0.3); }
    .summary-card.warning { border-color: rgba(242, 184, 75, 0.3); }
    .summary-number { font-size: 48px; font-weight: 700; }
    .summary-card.pass .summary-number { color: #50d890; }
    .summary-card.fail .summary-number { color: #ff6b7a; }
    .summary-card.warning .summary-number { color: #f2b84b; }
    .summary-label { color: #9aa4b5; margin-top: 8px; }
    .filters { display: flex; gap: 12px; margin-bottom: 24px; flex-wrap: wrap; }
    .filter-btn { padding: 8px 16px; border: 1px solid rgba(255,255,255,0.11); border-radius: 8px; background: #121620; color: #9aa4b5; cursor: pointer; transition: all 0.2s; }
    .filter-btn:hover { border-color: rgba(124, 92, 255, 0.5); color: #fff; }
    .filter-btn.active { background: rgba(124, 92, 255, 0.2); border-color: rgba(124, 92, 255, 0.5); color: #fff; }
    .test-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(400px, 1fr)); gap: 24px; }
    .test-card { background: #121620; border: 1px solid rgba(255,255,255,0.11); border-radius: 13px; overflow: hidden; transition: all 0.2s; }
    .test-card:hover { border-color: rgba(124, 92, 255, 0.4); transform: translateY(-2px); }
    .test-card-header { display: flex; justify-content: space-between; align-items: center; padding: 16px 20px; border-bottom: 1px solid rgba(255,255,255,0.075); }
    .test-card-page { font-weight: 600; font-size: 16px; }
    .test-card-resolution { font-size: 12px; color: #9aa4b5; }
    .status-badge { padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; }
    .status-badge.pass { background: rgba(80, 216, 144, 0.15); color: #50d890; border: 1px solid rgba(80, 216, 144, 0.3); }
    .status-badge.fail { background: rgba(255, 107, 122, 0.15); color: #ff6b7a; border: 1px solid rgba(255, 107, 122, 0.3); }
    .status-badge.warning { background: rgba(242, 184, 75, 0.15); color: #f2b84b; border: 1px solid rgba(242, 184, 75, 0.3); }
    .test-card-body { padding: 16px 20px; }
    .screenshot-container { margin-bottom: 16px; border-radius: 8px; overflow: hidden; background: #0b0e15; }
    .screenshot-container img { width: 100%; height: auto; display: block; }
    .elements-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 16px; }
    .element-stat { background: #1a202d; border-radius: 6px; padding: 8px 12px; text-align: center; }
    .element-stat-value { font-size: 18px; font-weight: 700; color: #a995ff; }
    .element-stat-label { font-size: 11px; color: #9aa4b5; margin-top: 2px; }
    .issues-list { font-size: 13px; }
    .issue-item { padding: 8px 12px; background: rgba(255,255,255,0.03); border-radius: 6px; margin-bottom: 6px; color: #9aa4b5; }
    .issue-item.error { background: rgba(255, 107, 122, 0.1); color: #ff6b7a; }
    .issue-item.warning { background: rgba(242, 184, 75, 0.1); color: #f2b84b; }
    .no-issues { color: #50d890; font-size: 13px; }
    .hidden { display: none !important; }
    @media (max-width: 768px) {
      .test-grid { grid-template-columns: 1fr; }
      .summary { grid-template-columns: 1fr 1fr; }
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>🎬 LocalDrama Studio UI 视觉测试报告</h1>
    <p class="subtitle">生成时间: ${new Date().toLocaleString("zh-CN")}</p>
    
    <div class="summary">
      <div class="summary-card pass">
        <div class="summary-number">${passCount}</div>
        <div class="summary-label">通过</div>
      </div>
      <div class="summary-card fail">
        <div class="summary-number">${failCount}</div>
        <div class="summary-label">失败</div>
      </div>
      <div class="summary-card warning">
        <div class="summary-number">${warningCount}</div>
        <div class="summary-label">警告</div>
      </div>
      <div class="summary-card">
        <div class="summary-number">${testResults.length}</div>
        <div class="summary-label">总测试数</div>
      </div>
    </div>
    
    <div class="filters">
      <button class="filter-btn active" data-filter="all">全部</button>
      <button class="filter-btn" data-filter="pass">仅通过</button>
      <button class="filter-btn" data-filter="fail">仅失败</button>
      <button class="filter-btn" data-filter="warning">仅警告</button>
    </div>
    
    <div class="test-grid">
      ${testResults.map(result => `
        <div class="test-card" data-status="${result.status}">
          <div class="test-card-header">
            <div>
              <div class="test-card-page">${result.page}</div>
              <div class="test-card-resolution">${result.resolution}</div>
            </div>
            <span class="status-badge ${result.status}">${result.status === "pass" ? "✓ 通过" : result.status === "fail" ? "✗ 失败" : "⚠ 警告"}</span>
          </div>
          <div class="test-card-body">
            ${result.screenshot ? `
              <div class="screenshot-container">
                <img src="${result.screenshot}" alt="${result.page} at ${result.resolution}">
              </div>
            ` : ""}
            
            <div class="elements-grid">
              <div class="element-stat">
                <div class="element-stat-value">${result.elements.buttons}</div>
                <div class="element-stat-label">按钮</div>
              </div>
              <div class="element-stat">
                <div class="element-stat-value">${result.elements.inputs}</div>
                <div class="element-stat-label">输入框</div>
              </div>
              <div class="element-stat">
                <div class="element-stat-value">${result.elements.links}</div>
                <div class="element-stat-label">链接</div>
              </div>
            </div>
            
            ${result.layoutIssues.length > 0 ? `
              <div class="issues-list">
                ${result.layoutIssues.map(issue => `<div class="issue-item warning">${issue}</div>`).join("")}
              </div>
            ` : ""}
            
            ${result.errors.length > 0 ? `
              <div class="issues-list" style="margin-top: 12px;">
                ${result.errors.slice(0, 3).map(err => `<div class="issue-item error">${err.slice(0, 100)}</div>`).join("")}
              </div>
            ` : ""}
            
            ${result.layoutIssues.length === 0 && result.errors.length === 0 ? `
              <div class="no-issues">✓ 无明显问题</div>
            ` : ""}
          </div>
        </div>
      `).join("")}
    </div>
  </div>
  
  <script>
    document.querySelectorAll(".filter-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        const filter = btn.dataset.filter;
        document.querySelectorAll(".test-card").forEach(card => {
          if (filter === "all" || card.dataset.status === filter) {
            card.classList.remove("hidden");
          } else {
            card.classList.add("hidden");
          }
        });
      });
    });
  </script>
</body>
</html>
  `;
  
  fs.writeFileSync(REPORT_PATH, html, "utf-8");
  console.log(`\n📊 Report generated: ${REPORT_PATH}`);
}

// Main test suite
test.describe("LocalDrama Studio UI Visual Testing", () => {
  test.beforeAll(async () => {
    ensureDir(OUTPUT_DIR);
    ensureDir(`${OUTPUT_DIR}/screenshots`);
  });
  
  for (const pageInfo of PAGES) {
    test(`${pageInfo.name} - Visual Testing at Multiple Resolutions`, async ({ page }) => {
      test.setTimeout(120_000);
      console.log(`\n📄 Testing: ${pageInfo.name}`);
      
      for (const resolution of RESOLUTIONS) {
        console.log(`  📱 ${resolution.name}`);
        const result = await testPageAtResolution(page, pageInfo, resolution);
        testResults.push(result);
        
        if (result.status === "fail") {
          console.log(`    ❌ Failed: ${result.errors.join(", ")}`);
        } else if (result.status === "warning") {
          console.log(`    ⚠️  Warning: ${result.layoutIssues.join(", ")}`);
        } else {
          console.log(`    ✓ Passed`);
        }
      }
    });
  }
  
  test("Interactive Elements Test", async ({ page }) => {
    test.setTimeout(120_000);
    console.log("\n🔘 Testing Interactive Elements...");
    
    for (const pageInfo of PAGES.slice(0, 3)) { // Test first 3 pages
      console.log(`  📄 ${pageInfo.name}`);
      const results = await testInteractiveElements(page, pageInfo);
      results.forEach(r => {
        console.log(`    ${r.action === "success" ? "✓" : "✗"} ${r.element}: ${r.action}`);
      });
    }
  });
  
  test.afterAll(async () => {
    console.log("\n📊 Generating test report...");
    generateReport();
  });
});
