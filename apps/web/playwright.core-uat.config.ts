import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "../../tests/e2e",
  testMatch: "core_chain_browser_readonly.spec.ts",
  timeout: 90_000,
  workers: 1,
  reporter: [["line"]],
  use: {
    baseURL: process.env.CORE_UAT_BASE_URL ?? "http://127.0.0.1:5175",
    trace: "retain-on-failure",
    launchOptions: { executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" },
  },
  webServer: [
    {
      command: ".venv\\Scripts\\python.exe scripts/serve_isolated_core_browser_uat.py --root temp/core-browser-uat-snapshot --port 3222",
      cwd: "../..",
      url: "http://127.0.0.1:3222/api/v1/health/live",
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: "pnpm dev --host 127.0.0.1 --port 5175",
      cwd: ".",
      url: "http://127.0.0.1:5175",
      reuseExistingServer: false,
      timeout: 60_000,
      env: { LOCAL_DRAMA_API_PROXY: "http://127.0.0.1:3222" },
    },
  ],
});
