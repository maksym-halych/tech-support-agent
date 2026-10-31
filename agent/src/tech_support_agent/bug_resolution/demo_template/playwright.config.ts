import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 15000,
  workers: 1,
  retries: 0,
  reporter: "line",
  outputDir: "/tmp/quiz-test-results",
  use: { baseURL: "http://127.0.0.1:5173", headless: true },
  webServer: {
    command:
      "node node_modules/vite/bin/vite.js --configLoader runner --host 0.0.0.0 --port 5173 --strictPort",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: true,
    timeout: 15000,
  },
});
