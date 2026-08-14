import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "../../tests/e2e",
  testMatch: "g9_canvas_performance.spec.ts",
  timeout: 90_000,
  workers: 1,
  reporter: [["line"], ["../../tests/e2e/g9_benchmark_reporter.ts"]],
  use: {
    baseURL: "http://127.0.0.1:5174",
    trace: "retain-on-failure",
    launchOptions: { executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" },
  },
  webServer: [
    {
      command: ".venv\\Scripts\\python.exe scripts/serve_isolated_g9.py --root temp/g9-browser-benchmark --port 3221",
      cwd: "../..",
      url: "http://127.0.0.1:3221/api/v1/health/live",
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: "pnpm dev --host 127.0.0.1 --port 5174",
      cwd: ".",
      url: "http://127.0.0.1:5174",
      reuseExistingServer: false,
      timeout: 60_000,
      env: { LOCAL_DRAMA_API_PROXY: "http://127.0.0.1:3221" },
    },
  ],
});
