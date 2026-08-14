import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "../../tests/e2e",
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    launchOptions: { executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" },
  },
  webServer: {
    command: "pnpm dev",
    cwd: ".",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: true,
  },
});
