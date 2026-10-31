import { defineConfig, devices } from "@playwright/test";
import { config } from "dotenv";
import authCheck from "./tests/auth-check";

config({ path: ".env.local", quiet: true });
config({ path: ".env", quiet: true });
// Fail before starting either server when the operator has no test session.
if (!process.argv.includes("--list")) authCheck();

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  globalSetup: "./tests/auth-check.ts",
  outputDir:
    process.env.BUG_E2E_OUTPUT_DIR || "/tmp/bug-handoff-playwright-results",
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:3100",
    storageState: process.env.BUG_E2E_STORAGE_STATE,
    trace: "off",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  webServer: [
    {
      command:
        "uv run --python 3.14 --directory ../agent python -m tests.bug_resolution.frontend_service",
      url: "http://127.0.0.1:2027/health",
      reuseExistingServer: false,
      timeout: 60_000,
      gracefulShutdown: { signal: "SIGTERM", timeout: 15_000 },
    },
    {
      command: "pnpm dev --hostname 127.0.0.1 --port 3100",
      url: "http://127.0.0.1:3100/sign-in",
      reuseExistingServer: false,
      timeout: 120_000,
      gracefulShutdown: { signal: "SIGTERM", timeout: 15_000 },
    },
  ],
});
