import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const BASE_URL = 'http://127.0.0.1:5173';
const OUTPUT_DIR = path.resolve('flash_ui_audit');
const SCREENSHOTS_DIR = path.join(OUTPUT_DIR, 'screenshots');

const KNOWN_PROJECT_ID = 'e5eaa01d-d39a-4a63-acbf-026da30b46e7';
const KNOWN_PROJECT_ID_2 = '20f0ab54-cbfc-4577-96b0-10ce5ef797f0';
const KNOWN_EPISODE_ID = 'd4db1033-9517-4bd0-958d-4228e0abead1';
const KNOWN_EPISODE_ID_2 = 'b8950429-0e11-449e-9c16-09b2030e594b';
const KNOWN_SHOT_ID = '020f9248-14b7-4f92-9edd-ee587ffdedf3';

// Ensure directories exist
fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });

const auditResults = {
  timestamp: new Date().toISOString(),
  pages: [],
  globalIssues: [],
  summary: {
    totalPages: 0,
    totalButtonsTested: 0,
    totalInputsTested: 0,
    totalSelectsTested: 0,
    totalModalsCaptured: 0,
    totalConsoleErrors: 0,
    totalNetworkErrors: 0,
    totalLayoutAnomalies: 0,
  }
};

const allRoutes = [
  // Global pages
  { id: 'global_projects', name: '项目列表 (Projects)', url: '/projects', scope: 'GLOBAL' },
  { id: 'global_models', name: '全局模型配置 (Models)', url: '/models', scope: 'GLOBAL' },
  { id: 'global_jobs', name: '全局任务队列 (Jobs)', url: '/jobs', scope: 'GLOBAL' },
  { id: 'global_diagnostics', name: '全局诊断与审计 (Diagnostics)', url: '/diagnostics', scope: 'GLOBAL' },

  // Project-level pages
  { id: 'project_home', name: '项目总览 (Project Home)', url: `/projects/${KNOWN_PROJECT_ID}`, scope: 'PROJECT' },
  { id: 'project_story', name: '故事工作区 (Story Workspace)', url: `/projects/${KNOWN_PROJECT_ID}/story`, scope: 'PROJECT' },
  { id: 'project_assets', name: '资产圣经 (Asset Bible V2)', url: `/projects/${KNOWN_PROJECT_ID}/assets`, scope: 'PROJECT' },
  { id: 'project_qc_policies', name: '质检策略 (QC Policies)', url: `/projects/${KNOWN_PROJECT_ID}/qc-policies`, scope: 'PROJECT' },
  { id: 'project_director_recipes', name: '导演配方 (Director Recipes)', url: `/projects/${KNOWN_PROJECT_ID}/director-recipes`, scope: 'PROJECT' },
  { id: 'project_production_settings', name: '生产设置 (Production Settings)', url: `/projects/${KNOWN_PROJECT_ID}/production-settings`, scope: 'PROJECT' },
  { id: 'project_models', name: '项目模型配置 (Project Models)', url: `/projects/${KNOWN_PROJECT_ID}/models`, scope: 'PROJECT' },
  { id: 'project_jobs', name: '项目任务队列 (Project Jobs)', url: `/projects/${KNOWN_PROJECT_ID}/jobs`, scope: 'PROJECT' },
  { id: 'project_diagnostics', name: '项目诊断 (Project Diagnostics)', url: `/projects/${KNOWN_PROJECT_ID}/diagnostics`, scope: 'PROJECT' },
  { id: 'project_lab', name: '媒体实验室 (Media Lab)', url: `/projects/${KNOWN_PROJECT_ID}/lab`, scope: 'PROJECT' },
  { id: 'project_canvas', name: '高级画布 (Canvas)', url: `/projects/${KNOWN_PROJECT_ID}/canvas`, scope: 'PROJECT' },
  { id: 'project_operations', name: '项目运营与工具 (Operations)', url: `/projects/${KNOWN_PROJECT_ID}/operations`, scope: 'PROJECT' },

  // Episode-level pages
  { id: 'episode_plan', name: '分集策划 (Episode Plan)', url: `/projects/${KNOWN_PROJECT_ID}/episodes/${KNOWN_EPISODE_ID}/plan`, scope: 'EPISODE' },
  { id: 'episode_director', name: '导演工作台 (Director Desk)', url: `/projects/${KNOWN_PROJECT_ID}/episodes/${KNOWN_EPISODE_ID}/direct`, scope: 'EPISODE' },
  { id: 'episode_director_shot', name: '导演工作台-特定镜头 (Director Desk Shot)', url: `/projects/${KNOWN_PROJECT_ID}/episodes/${KNOWN_EPISODE_ID}/direct/${KNOWN_SHOT_ID}`, scope: 'EPISODE' },
  { id: 'episode_generation', name: '手动生成 (Generation)', url: `/projects/${KNOWN_PROJECT_ID}/episodes/${KNOWN_EPISODE_ID}/generation`, scope: 'EPISODE' },
  { id: 'episode_generation_shot', name: '手动生成-特定镜头 (Generation Shot)', url: `/projects/${KNOWN_PROJECT_ID}/episodes/${KNOWN_EPISODE_ID}/generation/${KNOWN_SHOT_ID}`, scope: 'EPISODE' },
  { id: 'episode_review', name: '本集审核 (Episode Review)', url: `/projects/${KNOWN_PROJECT_ID}/episodes/${KNOWN_EPISODE_ID}/review`, scope: 'EPISODE' },
  { id: 'episode_audio', name: '声音工作区 (Audio Workspace)', url: `/projects/${KNOWN_PROJECT_ID}/episodes/${KNOWN_EPISODE_ID}/audio`, scope: 'EPISODE' },
  { id: 'episode_timeline', name: '时间线 (Timeline)', url: `/projects/${KNOWN_PROJECT_ID}/episodes/${KNOWN_EPISODE_ID}/timeline`, scope: 'EPISODE' },
  { id: 'episode_delivery', name: '成片交付 (Delivery)', url: `/projects/${KNOWN_PROJECT_ID}/episodes/${KNOWN_EPISODE_ID}/delivery`, scope: 'EPISODE' },
  { id: 'episode_run', name: '分集生产运行 (Episode Run)', url: `/projects/${KNOWN_PROJECT_ID}/episodes/${KNOWN_EPISODE_ID}/run`, scope: 'EPISODE' },
];

const requestedRouteIds = new Set((process.env.UI_AUDIT_ROUTES || '').split(',').map(value => value.trim()).filter(Boolean));
const routeList = requestedRouteIds.size ? allRoutes.filter(route => requestedRouteIds.has(route.id)) : allRoutes;

function safeArtifactName(value) {
  return value.replace(/[/\\?%*:|"<>\r\n]+/g, '_').replace(/\s+/g, ' ').trim().slice(0, 80) || 'artifact';
}

async function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function runAudit() {
  console.log(`Starting Comprehensive UI & Visual Audit on ${BASE_URL}...`);
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });

  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 1,
  });

  const page = await context.newPage();

  // Enable feature flags in localStorage if any
  await page.goto(BASE_URL + '/projects');
  await page.evaluate(() => {
    localStorage.setItem('ENABLE_ASSET_BIBLE_V2', 'true');
    localStorage.setItem('ENABLE_DIRECTOR_DESK_V2', 'true');
    localStorage.setItem('ENABLE_EPISODE_AGENT_RUN_V2', 'true');
  });

  for (const route of routeList) {
    const pageDir = path.join(SCREENSHOTS_DIR, route.id);
    fs.mkdirSync(pageDir, { recursive: true });

    console.log(`\n========================================`);
    console.log(`[AUDITING] ${route.name} -> ${route.url}`);
    console.log(`========================================`);

    const consoleErrors = [];
    const consoleWarnings = [];
    const networkFailures = [];

    const handleConsole = msg => {
      if (msg.type() === 'error') {
        consoleErrors.push(msg.text());
      } else if (msg.type() === 'warning') {
        consoleWarnings.push(msg.text());
      }
    };

    const handleRequestFailed = req => {
      networkFailures.push({
        url: req.url(),
        method: req.method(),
        failure: req.failure()?.errorText || 'Unknown'
      });
    };

    page.on('console', handleConsole);
    page.on('requestfailed', handleRequestFailed);

    const pageReport = {
      id: route.id,
      name: route.name,
      url: route.url,
      scope: route.scope,
      pageTitle: '',
      screenshots: {},
      subpagesScreenshots: [],
      buttonAudit: [],
      inputAudit: [],
      selectAudit: [],
      tabAudit: [],
      layoutAudit: {
        horizontalOverflow: false,
        elementOverlaps: [],
        clippedTexts: [],
        brokenImages: [],
        footerVisibility: 'UNKNOWN',
      },
      consoleErrors: [],
      networkFailures: [],
      visualDefects: [],
    };

    try {
      await page.goto(BASE_URL + route.url, { waitUntil: 'networkidle', timeout: 15000 });
    } catch (e) {
      console.warn(`Direct navigation error for ${route.url}, waiting 3s...`, e.message);
      await sleep(3000);
    }

    await sleep(1500); // Allow react-query and animations to settle

    pageReport.pageTitle = await page.title();

    // 1. Capture Full Page Screenshot
    const fullPagePath = path.join(pageDir, '01_full_page.png');
    await page.screenshot({ path: fullPagePath, fullPage: true });
    pageReport.screenshots.fullPage = fullPagePath;
    console.log(`✓ Saved Full Page Screenshot: ${fullPagePath}`);

    // 2. Capture Top Viewport Screenshot
    await page.evaluate(() => window.scrollTo(0, 0));
    await sleep(200);
    const topViewportPath = path.join(pageDir, '02_viewport_top.png');
    await page.screenshot({ path: topViewportPath, fullPage: false });
    pageReport.screenshots.viewportTop = topViewportPath;

    // 3. Scroll to Bottom & Capture Bottom Viewport Screenshot
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(400);
    const bottomViewportPath = path.join(pageDir, '03_viewport_bottom.png');
    await page.screenshot({ path: bottomViewportPath, fullPage: false });
    pageReport.screenshots.viewportBottom = bottomViewportPath;
    console.log(`✓ Saved Top & Bottom Viewport Screenshots`);

    // 4. Detailed Layout & Visual Geometry Analysis
    const layoutAnalysis = await page.evaluate(() => {
      const issues = {
        hasHorizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 5,
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: window.innerWidth,
        scrollHeight: document.documentElement.scrollHeight,
        clientHeight: window.innerHeight,
        overlaps: [],
        clippedTexts: [],
        brokenImages: [],
        emptyContainers: [],
        misalignedRows: [],
      };

      // Check broken images
      const images = Array.from(document.querySelectorAll('img'));
      for (const img of images) {
        if (!img.complete || img.naturalWidth === 0) {
          issues.brokenImages.push({
            src: img.src || img.getAttribute('src') || '',
            alt: img.alt || '',
            className: img.className,
          });
        }
      }

      // Check text clipping
      const allElements = Array.from(document.querySelectorAll('h1, h2, h3, h4, p, span, label, button, .v2-badge, .status-pill'));
      for (const el of allElements) {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        const assistiveOnly = el.matches('.sr-only, .director-sr-only') || Boolean(el.closest('.sr-only, .director-sr-only'));
        const intentionallyExplained = Boolean(el.getAttribute('title'));
        const visiblyRendered = style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 1 && rect.height > 1;
        if (assistiveOnly || intentionallyExplained || !visiblyRendered) continue;
        if (style.overflow === 'hidden' || style.textOverflow === 'ellipsis') {
          if (el.scrollWidth > el.clientWidth + 2) {
            issues.clippedTexts.push({
              tag: el.tagName,
              text: el.innerText.slice(0, 40),
              className: el.className,
              scrollWidth: el.scrollWidth,
              clientWidth: el.clientWidth,
            });
          }
        }
      }

      // Check inline form / button row alignments
      const buttonGroups = Array.from(document.querySelectorAll('.button-group, .actions, .v2-toolbar, .panel-heading, .v2-action-bar, form'));
      for (const group of buttonGroups) {
        const children = Array.from(group.children).filter(c => ['BUTTON', 'A', 'INPUT', 'SELECT'].includes(c.tagName));
        if (children.length > 1) {
          const rects = children.map(c => c.getBoundingClientRect());
          const tops = rects.map(r => Math.round(r.top));
          const heights = rects.map(r => Math.round(r.height));
          // If in same horizontal row (lefts increase) but tops differ significantly (>4px)
          const isRow = rects.every((r, idx) => idx === 0 || r.left >= rects[idx - 1].left);
          if (isRow && Math.max(...tops) - Math.min(...tops) > 5) {
            issues.misalignedRows.push({
              containerClass: group.className,
              topDiff: Math.max(...tops) - Math.min(...tops),
              children: children.map(c => ({ tag: c.tagName, text: c.innerText?.slice(0, 20) || c.getAttribute('name') }))
            });
          }
        }
      }

      return issues;
    });

    pageReport.layoutAudit = layoutAnalysis;

    // 5. Audit all Tabs on the page
    const tabs = await page.$$('[role="tab"], .v2-tabs button, .v2-tab, nav.tabs a, nav.tabs button, .sub-nav a, .sub-nav button');
    console.log(`Found ${tabs.length} tabs to test`);
    let tabIndex = 0;
    for (const tab of tabs) {
      try {
        const tabText = (await tab.innerText()).trim() || `Tab_${tabIndex}`;
        const isVisible = await tab.isVisible();
        if (isVisible) {
          await tab.click({ timeout: 2000 });
          await sleep(500);
          const tabScreenshotPath = path.join(pageDir, `tab_${tabIndex}_${safeArtifactName(tabText)}.png`);
          await page.screenshot({ path: tabScreenshotPath, fullPage: true });
          pageReport.tabAudit.push({
            name: tabText,
            screenshot: tabScreenshotPath,
            status: 'CLICKED_SUCCESS'
          });
        }
      } catch (e) {
        pageReport.tabAudit.push({
          name: `Tab_${tabIndex}`,
          error: e.message,
          status: 'CLICK_FAILED'
        });
      }
      tabIndex++;
    }

    // Reset view to top
    await page.evaluate(() => window.scrollTo(0, 0));

    // 6. Audit & Test all Text Inputs and Textareas
    const textInputs = await page.$$('input[type="text"], input[type="search"], input[type="number"], input:not([type]), textarea');
    console.log(`Found ${textInputs.length} text inputs/textareas to test`);
    let inputIndex = 0;
    for (const input of textInputs) {
      try {
        const isVisible = await input.isVisible();
        if (!isVisible) continue;
        const placeholder = await input.getAttribute('placeholder') || '';
        const name = await input.getAttribute('name') || await input.getAttribute('aria-label') || await input.getAttribute('id') || `Input_${inputIndex}`;
        const readonly = await input.getAttribute('readonly');
        const disabled = await input.getAttribute('disabled');

        if (!readonly && disabled === null) {
          const originalVal = await input.inputValue();
          await input.focus();
          await input.fill('测试输入123');
          await sleep(100);

          pageReport.inputAudit.push({
            name,
            placeholder,
            tested: true,
            status: 'INPUT_SUCCESS',
            originalVal,
          });

          // Restore original value
          await input.fill(originalVal || '');
        } else {
          pageReport.inputAudit.push({
            name,
            placeholder,
            tested: false,
            status: disabled ? 'DISABLED' : 'READONLY'
          });
        }
      } catch (e) {
        pageReport.inputAudit.push({
          index: inputIndex,
          error: e.message,
          status: 'INPUT_ERROR'
        });
      }
      inputIndex++;
    }

    // 7. Audit & Test all Dropdowns / Select elements
    const selectElements = await page.$$('select, [role="combobox"], .v2-select');
    console.log(`Found ${selectElements.length} selects/dropdowns to test`);
    let selectIndex = 0;
    for (const select of selectElements) {
      try {
        const isVisible = await select.isVisible();
        if (!isVisible) continue;
        const tagName = await select.evaluate(el => el.tagName);
        const name = await select.getAttribute('name') || await select.getAttribute('aria-label') || await select.getAttribute('id') || `Select_${selectIndex}`;

        if (tagName === 'SELECT') {
          const options = await select.$$eval('option', opts => opts.map(o => ({ text: o.innerText.trim(), value: o.value })));
          const currentValue = await select.inputValue();
          pageReport.selectAudit.push({
            name,
            type: 'NATIVE_SELECT',
            optionsCount: options.length,
            optionsPreview: options.slice(0, 5),
            currentValue,
            status: 'TESTED_OK'
          });
        } else {
          // Custom dropdown: click to open dropdown menu and capture
          await select.click({ timeout: 1500 });
          await sleep(300);
          const dropdownScreenshot = path.join(pageDir, `dropdown_${selectIndex}_open.png`);
          await page.screenshot({ path: dropdownScreenshot, fullPage: false });
          // Close by clicking away
          await page.keyboard.press('Escape');
          pageReport.selectAudit.push({
            name,
            type: 'CUSTOM_DROPDOWN',
            screenshot: dropdownScreenshot,
            status: 'OPEN_TESTED'
          });
        }
      } catch (e) {
        pageReport.selectAudit.push({
          index: selectIndex,
          error: e.message,
          status: 'SELECT_ERROR'
        });
      }
      selectIndex++;
    }

    // 8. Audit ALL Buttons & Modals / Subpages
    const buttons = await page.$$('button, a.btn, a.primary-action, a.v2-inline-link, a.v2-button, [role="button"]');
    console.log(`Found ${buttons.length} buttons/clickable action elements to test`);
    let buttonIndex = 0;

    for (const btn of buttons) {
      try {
        const isVisible = await btn.isVisible();
        if (!isVisible) continue;

        const btnInfo = await btn.evaluate(el => {
          const rect = el.getBoundingClientRect();
          return {
            text: el.innerText?.trim() || el.getAttribute('aria-label') || el.getAttribute('title') || '',
            className: el.className,
            tagName: el.tagName,
            type: el.getAttribute('type') || '',
            href: el.getAttribute('href') || '',
            disabled: el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true',
            width: Math.round(rect.width),
            height: Math.round(rect.height),
            top: Math.round(rect.top),
            left: Math.round(rect.left),
          };
        });

        // Skip non-interactive or destructive batch delete buttons to preserve test state
        const isDestructive = /删除|清空|DESTROY|DELETE_ALL|ARCHIVE/i.test(btnInfo.text);
        const isTab = btnInfo.className.includes('tab') || btnInfo.type === 'tab';

        if (isDestructive || isTab || btnInfo.disabled) {
          pageReport.buttonAudit.push({
            ...btnInfo,
            status: btnInfo.disabled ? 'DISABLED' : (isDestructive ? 'SKIPPED_DESTRUCTIVE' : 'SKIPPED_TAB'),
          });
          continue;
        }

        // Test Click
        const beforeUrl = page.url();
        const beforeModalsCount = await page.$$eval('[role="dialog"], .modal, .drawer, .v2-dialog, .dialog-overlay, [aria-modal="true"]', m => m.filter(el => window.getComputedStyle(el).display !== 'none').length);

        try {
          await btn.click({ timeout: 2000 });
          await sleep(600);

          const afterUrl = page.url();
          const afterModals = await page.$$('[role="dialog"], .modal, .drawer, .v2-dialog, .dialog-overlay, [aria-modal="true"]');
          let openedModal = false;

          for (const modal of afterModals) {
            const isModalVis = await modal.isVisible();
            if (isModalVis) {
              openedModal = true;
              const modalTitle = (await modal.$eval('h1, h2, h3, h4, .modal-title, .dialog-title', h => h.innerText).catch(() => 'Modal')) || 'Modal';
              const modalPath = path.join(pageDir, `modal_${buttonIndex}_${safeArtifactName(btnInfo.text || 'dialog')}.png`);
              await page.screenshot({ path: modalPath, fullPage: false });
              pageReport.subpagesScreenshots.push({
                triggerButton: btnInfo.text,
                modalTitle,
                screenshot: modalPath
              });
              console.log(`  ★ Captured Modal/Subpage triggered by [${btnInfo.text}]: ${modalPath}`);

              // Try to close modal
              const closeBtn = await modal.$('button[aria-label="Close"], button.close, button:has-text("取消"), button:has-text("关闭"), button:has-text("Close")');
              if (closeBtn) {
                await closeBtn.click({ timeout: 1500 });
              } else {
                await page.keyboard.press('Escape');
              }
              await sleep(300);
            }
          }

          // If navigation happened away from current page, navigate back
          if (afterUrl !== beforeUrl && !afterUrl.includes(route.url)) {
            console.log(`  Navigated from ${beforeUrl} to ${afterUrl}, restoring...`);
            await page.goto(BASE_URL + route.url, { waitUntil: 'networkidle' });
            await sleep(1000);
          }

          pageReport.buttonAudit.push({
            ...btnInfo,
            status: openedModal ? 'OPENED_MODAL' : (afterUrl !== beforeUrl ? 'NAVIGATED' : 'CLICKED_OK'),
            openedModal,
          });

        } catch (clickErr) {
          pageReport.buttonAudit.push({
            ...btnInfo,
            status: 'CLICK_TIMEOUT_OR_BLOCKED',
            error: clickErr.message
          });
        }

      } catch (e) {
        pageReport.buttonAudit.push({
          index: buttonIndex,
          error: e.message,
          status: 'EVAL_ERROR'
        });
      }
      buttonIndex++;
    }

    // Clean up event listeners
    page.off('console', handleConsole);
    page.off('requestfailed', handleRequestFailed);

    pageReport.consoleErrors = consoleErrors;
    pageReport.networkFailures = networkFailures;

    // Summarize
    auditResults.summary.totalPages++;
    auditResults.summary.totalButtonsTested += pageReport.buttonAudit.length;
    auditResults.summary.totalInputsTested += pageReport.inputAudit.length;
    auditResults.summary.totalSelectsTested += pageReport.selectAudit.length;
    auditResults.summary.totalModalsCaptured += pageReport.subpagesScreenshots.length;
    auditResults.summary.totalConsoleErrors += consoleErrors.length;
    auditResults.summary.totalNetworkErrors += networkFailures.length;
    auditResults.summary.totalLayoutAnomalies += (pageReport.layoutAudit.clippedTexts?.length || 0) + (pageReport.layoutAudit.misalignedRows?.length || 0);

    auditResults.pages.push(pageReport);

    // Save intermediate page json
    fs.writeFileSync(path.join(pageDir, 'audit_data.json'), JSON.stringify(pageReport, null, 2), 'utf-8');
  }

  await browser.close();

  // Save master audit json
  const masterJsonPath = path.join(OUTPUT_DIR, 'master_audit_results.json');
  fs.writeFileSync(masterJsonPath, JSON.stringify(auditResults, null, 2), 'utf-8');
  console.log(`\n========================================`);
  console.log(`Audit Completed! Master data saved to ${masterJsonPath}`);
  console.log(`Total Pages: ${auditResults.summary.totalPages}`);
  console.log(`Total Buttons: ${auditResults.summary.totalButtonsTested}`);
  console.log(`Total Inputs: ${auditResults.summary.totalInputsTested}`);
  console.log(`Total Selects: ${auditResults.summary.totalSelectsTested}`);
  console.log(`Total Modals: ${auditResults.summary.totalModalsCaptured}`);
  console.log(`========================================\n`);
}

runAudit().catch(err => {
  console.error('Fatal audit failure:', err);
  process.exit(1);
});
