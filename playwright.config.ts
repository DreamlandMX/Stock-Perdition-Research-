import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";

const python =
  process.platform === "win32" ? ".\\.venv\\Scripts\\python.exe" : ".venv/bin/python";
const viteCli = resolve("node_modules/vite/bin/vite.js");

export default defineConfig({
  testDir: "./frontend/e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 45_000,
  expect: {
    timeout: 10_000,
    toHaveScreenshot: {
      animations: "disabled",
      caret: "hide",
      maxDiffPixelRatio: 0.01,
    },
  },
  use: {
    baseURL: "http://127.0.0.1:4173",
    channel: "chrome",
    colorScheme: "dark",
    locale: "zh-CN",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    viewport: { width: 1440, height: 1024 },
  },
  outputDir: "test-results/m5-e2e",
  snapshotPathTemplate: "{testDir}/__screenshots__/{testFilePath}/{arg}{ext}",
  webServer: [
    {
      command: `${python} -m uvicorn backend.tests.e2e_app:app --host 127.0.0.1 --port 8766`,
      env: { PYTHONPATH: process.cwd() },
      reuseExistingServer: false,
      timeout: 120_000,
      url: "http://127.0.0.1:8766/api/v1/health",
    },
    {
      command: `"${process.execPath}" "${viteCli}" ${process.env.PLAYWRIGHT_USE_PREVIEW === "1" ? "preview" : ""} --config frontend/vite.config.ts --host 127.0.0.1 --port 4173`,
      env: { VITE_API_PROXY_TARGET: "http://127.0.0.1:8766" },
      reuseExistingServer: false,
      timeout: 120_000,
      url: "http://127.0.0.1:4173",
    },
  ],
});
