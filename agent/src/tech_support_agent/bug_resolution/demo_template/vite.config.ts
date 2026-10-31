import { defineConfig } from "vite";

export default defineConfig({
  cacheDir: "/tmp/quiz-vite-cache",
  server: { host: "0.0.0.0", port: 5173, strictPort: true, hmr: false },
});
