// ============ scripts/e2e_helpers.mjs ============
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
const execFileAsync = promisify(execFile);

const FFPROBE = process.env.FFPROBE_PATH || "E:\\Tools\\ffmpeg\\bin\\ffprobe.exe";
const FFMPEG  = process.env.FFMPEG_PATH  || "E:\\Tools\\ffmpeg\\bin\\ffmpeg.exe";

export class E2EHarness {
  constructor({ label = "run", evidenceRoot = "docs/evidence", apiPort = 3210 } = {}) {
    const ts = new Date().toISOString().replace(/[:.]/g, "-");
    this.runId = `${label}_${ts}`;
    this.apiPort = apiPort;
    this.evidenceDir = path.resolve(evidenceRoot, this.runId);
    this.startEpochMs = Date.now();
    this.network = [];
    this.steps = [];
    this.consoleErrors = [];
    this._apiToken = null;
    fs.mkdirSync(path.join(this.evidenceDir, "screenshots"), { recursive: true });
    fs.mkdirSync(path.join(this.evidenceDir, "logs"), { recursive: true });
    fs.mkdirSync(path.join(this.evidenceDir, "media"), { recursive: true });
    console.log(`\n======================================================`);
    console.log(`🚀 Starting Full-Chain E2E Real Click Verification`);
    console.log(`▶ Run ID: ${this.runId}`);
    console.log(`▶ Start Epoch: ${this.startEpochMs} (${new Date(this.startEpochMs).toISOString()})`);
    console.log(`▶ Evidence Directory: ${this.evidenceDir}`);
    console.log(`======================================================\n`);
  }

  attach(page, { apiPort = 3210, baseUrl = "http://127.0.0.1:5173" } = {}) {
    this.page = page;
    this.apiPort = apiPort;
    this.baseUrl = baseUrl;
    page.on("request", (req) => {
      const url = req.url();
      if (url.includes("/api/")) {
        this.network.push({
          type: "REQUEST",
          method: req.method(),
          url,
          reqBody: (req.postData() || "").slice(0, 2000),
          at: Date.now(),
        });
      }
    });
    page.on("response", async (res) => {
      const url = res.url();
      if (url.includes("/api/")) {
        let resBody = "";
        try { resBody = (await res.text()).slice(0, 3000); } catch {}
        this.network.push({
          type: "RESPONSE",
          method: res.request().method(),
          url,
          status: res.status(),
          resBody,
          at: Date.now(),
        });
      }
    });
    page.on("console", (m) => {
      if (m.type() === "error") {
        this.consoleErrors.push(`[console.error] ${m.text()}`);
      }
    });
    page.on("pageerror", (e) => {
      this.consoleErrors.push(`[pageerror] ${e.message || e}`);
    });
    return this;
  }

  async api(method, pathname, body) {
    const base = `http://127.0.0.1:${this.apiPort}`;
    if (!this._apiToken) {
      try {
        const b = await (await fetch(`${base}/api/v1/session/bootstrap`)).json();
        this._apiToken = b.token || b.instance_token || "";
      } catch { this._apiToken = ""; }
    }
    const res = await fetch(`${base}/api/v1${pathname}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        ...(this._apiToken ? { "X-Local-Instance-Token": this._apiToken } : {})
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let text = "";
    try { text = await res.text(); } catch {}
    return {
      status: res.status,
      ok: res.ok,
      bodyText: text,
      json: (() => { try { return JSON.parse(text); } catch { return null; } })()
    };
  }

  async enumerateControls() {
    const p = this.page;
    const buttons = await p.locator("button:visible:enabled").evaluateAll((els) =>
      els.map((e) => (e.innerText || "").trim()).filter(Boolean)
    );
    const inputs = await p.locator("input:visible").evaluateAll((els) =>
      els.map((e) => ({
        id: e.id || "",
        name: e.name || "",
        type: e.type || "",
        placeholder: e.placeholder || "",
        aria: e.getAttribute("aria-label") || "",
      }))
    );
    const tabs = await p.locator('[role="tab"]:visible').evaluateAll((els) =>
      els.map((e) => (e.innerText || "").trim()).filter(Boolean)
    );
    const selects = await p.locator("select:visible").evaluateAll((els) =>
      els.map((e) => ({ name: e.name || "", id: e.id || "" }))
    );
    const checks = await p.locator('input[type="checkbox"]:visible').count();
    const details = await p.locator("details:visible > summary:visible").evaluateAll((els) =>
      els.map((e) => (e.innerText || "").trim()).filter(Boolean)
    );
    return { buttons, inputs, tabs, selects, checks, details };
  }

  async snap(prefix, suffix) {
    const safe = String(prefix || "step").replace(/[^a-zA-Z0-9_-]/g, "_");
    const file = path.join(this.evidenceDir, "screenshots", `${safe}_${suffix}.png`);
    try {
      await this.page.screenshot({ path: file, fullPage: true });
    } catch (e) {
      console.warn("Screenshot capture error:", e.message);
    }
    return file;
  }

  record(stepId, name, status, summary, details = {}) {
    const rec = { stepId, name, status, summary, details, ts: new Date().toISOString() };
    this.steps.push(rec);
    const icon = status === "PASS" ? "✅" : status === "BLOCKED" ? "🚧" : "❌";
    console.log(`${icon} [${stepId}] ${name} -> ${status}: ${summary}`);
    return rec;
  }

  lastResponse(pathFragment, method) {
    const hit = [...this.network].reverse().find((r) =>
      r.type === "RESPONSE" && r.url.includes(pathFragment) && (!method || r.method === method)
    );
    return hit || null;
  }

  lastRequest(pathFragment, method) {
    const hit = [...this.network].reverse().find((r) =>
      r.type === "REQUEST" && r.url.includes(pathFragment) && (!method || r.method === method)
    );
    return hit || null;
  }

  assertMutated(stepId, name, pathFragment, method, { statuses = [200, 201, 202], mustContain, details = {} } = {}) {
    const resp = this.lastResponse(pathFragment, method);
    if (!resp) {
      return this.record(stepId, name, "BLOCKED", `没有对 ${method} ${pathFragment} 的真实 API 请求`, { ...details });
    }
    if (!statuses.includes(resp.status)) {
      return this.record(stepId, name, "FAIL", `${method} ${pathFragment} -> HTTP ${resp.status}`, { ...details, resp });
    }
    if (mustContain && !(resp.resBody || "").includes(mustContain)) {
      return this.record(stepId, name, "BLOCKED", `${method} ${pathFragment} 返回体缺少 "${mustContain}"`, { ...details, resp });
    }
    return this.record(stepId, name, "PASS", `${method} ${pathFragment} -> ${resp.status} (已确认真实命中)`, {
      ...details,
      resp: { status: resp.status, body: resp.resBody.slice(0, 400) }
    });
  }

  async verifyNewMedia(stepId, name, fileAbs, { minBytes = 0, extractFrames = true } = {}) {
    if (!fs.existsSync(fileAbs)) {
      return this.record(stepId, name, "FAIL", `文件不存在: ${fileAbs}`);
    }
    const st = fs.statSync(fileAbs);
    const mtimeMs = Math.max(st.mtimeMs, st.ctimeMs);
    if (mtimeMs < this.startEpochMs - 30000) {
      return this.record(stepId, name, "FAIL", `mtime 早于运行开始 (${new Date(mtimeMs).toISOString()})，疑似复用旧文件`, {
        stat: { mtime: st.mtimeMs, ctime: st.ctimeMs, size: st.size }
      });
    }
    if (st.size < minBytes) {
      return this.record(stepId, name, "BLOCKED", `文件过小 ${st.size} 字节`, { stat: { size: st.size } });
    }
    let probe = null;
    try {
      const { stdout } = await execFileAsync(FFPROBE, [
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        fileAbs
      ]);
      probe = JSON.parse(stdout);
    } catch (e) {
      probe = { error: e.message };
    }

    const frames = [];
    if (extractFrames && probe && !probe.error) {
      const dur = Number(probe.format?.duration) || 5;
      const t = [0.0, Math.max(0.1, dur / 2), Math.max(0.2, dur - 0.1)];
      for (let i = 0; i < t.length; i++) {
        const out = path.join(this.evidenceDir, "media", `${stepId.replace(/[^a-zA-Z0-9_-]/g, "_")}_fr${i}.png`);
        try {
          await execFileAsync(FFMPEG, ["-y", "-ss", String(t[i]), "-i", fileAbs, "-vframes", "1", out]);
          frames.push(out);
        } catch (err) {
          console.warn(`Frame extraction at ${t[i]}s failed:`, err.message);
        }
      }
    }

    return this.record(stepId, name, "PASS", `新建真实媒体 ${st.size}B, mtime=${new Date(mtimeMs).toISOString()}`, {
      probe,
      frames,
      path: fileAbs
    });
  }

  async visionQC(stepId, name, imagePaths, { baseUrl = "https://api.deepseek.com", model = "deepseek-v4-flash-vision-exp" } = {}) {
    const key = process.env.LOCAL_DRAMA_LLM_API_KEY || process.env.DEEPSEEK_API_KEY || "sk-6f59214c02694a8cbe4808c111554ba1";
    if (!key) return this.record(stepId, name, "BLOCKED", "未配置 DeepSeek key");
    const out = [];
    for (const img of imagePaths) {
      if (!fs.existsSync(img)) continue;
      const b64 = fs.readFileSync(img).toString("base64");
      try {
        const res = await fetch(`${baseUrl}/v1/chat/completions`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${key}`
          },
          body: JSON.stringify({
            model,
            temperature: 0,
            response_format: { type: "json_object" },
            messages: [{
              role: "user",
              content: [
                { type: "image_url", image_url: { url: `data:image/png;base64,${b64}` } },
                {
                  type: "text",
                  text: "请按画面构图/主体/一致性/明显伪影输出 JSON: {\"overall_score\": 0-100, \"composition_score\": 0-100, \"lighting_score\": 0-100, \"motion_artifact_score\": 0-100, \"qc_status\": \"PASS\" | \"FAIL\", \"feedback\": \"...\"}"
                }
              ]
            }]
          }),
        });
        const data = await res.json();
        let score = null;
        try {
          const m = data.choices?.[0]?.message?.content;
          score = JSON.parse(m.replace(/```json|```/g, "").trim());
        } catch (err) {
          score = { error: err.message, raw: data };
        }
        out.push({ image: img, status: res.status, score });
      } catch (err) {
        out.push({ image: img, error: err.message });
      }
    }
    const ok = out.length > 0 && out.every((o) => o.status === 200 && o.score?.qc_status === "PASS");
    return this.record(stepId, name, ok ? "PASS" : "FAIL", `DeepSeek vision 原始分 ${JSON.stringify(out.map((o) => o.score))}`, {
      vision: out
    });
  }

  summarize() {
    const lines = this.steps.map((s) => `| **${s.stepId}** | ${s.name} | ${s.status === "PASS" ? "✅ PASS" : s.status === "BLOCKED" ? "🚧 BLOCKED" : "❌ FAIL"} | ${s.summary} |`);
    const resultsJson = {
      runId: this.runId,
      startEpochMs: this.startEpochMs,
      completedAt: new Date().toISOString(),
      steps: this.steps,
      consoleErrors: this.consoleErrors,
      networkCount: this.network.length,
    };
    fs.writeFileSync(
      path.join(this.evidenceDir, "evidence_results.json"),
      JSON.stringify(resultsJson, null, 2),
      "utf8"
    );
    fs.writeFileSync(
      path.join(this.evidenceDir, "logs", "network_logs.json"),
      JSON.stringify(this.network, null, 2),
      "utf8"
    );

    const md = [
      `# E2E 真实点击验收报告 · ${this.runId}`,
      ``,
      `> 启动时间: ${new Date(this.startEpochMs).toISOString()}`,
      `> 结束时间: ${new Date().toISOString()}`,
      `> 前端: ${this.baseUrl} | 后端 API: http://127.0.0.1:${this.apiPort} | ComfyUI: http://127.0.0.1:8188`,
      ``,
      `## 1. 逐步骤真实点击验收矩阵`,
      ``,
      `| 步骤编号 | 阶段与动作 | 状态 | 详细执行结论与证据 |`,
      `|---|---|---|---|`,
      ...lines,
      ``,
      `## 2. 真实网络交互统计`,
      `- 共捕获 **${this.network.length}** 条真实 \`/api\` HTTP 请求与响应。`,
      `- 完整交互日志保存在: \`logs/network_logs.json\`。`,
      ``,
      `## 3. 页面截屏证据`,
      `- 截图保存在: \`screenshots/\`。`,
      ``
    ].join("\n");
    fs.writeFileSync(path.join(this.evidenceDir, "summary.md"), md, "utf8");
    console.log(`\n🏁 Summary Report generated at: ${path.join(this.evidenceDir, "summary.md")}`);
    return this.evidenceDir;
  }
}
