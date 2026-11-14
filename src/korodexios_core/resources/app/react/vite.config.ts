import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The daemon answers /api/*; during development and preview the app proxies to it, so
// the page and the API share one origin and no CORS setup is needed.
const daemon = process.env.KDX_DAEMON ?? "http://127.0.0.1:18123";

export default defineConfig({
  plugins: [react()],
  base: "./",
  server: { host: "127.0.0.1", port: 5173, proxy: { "/api": daemon } },
  preview: { host: "127.0.0.1", port: 4173, proxy: { "/api": daemon } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    exclude: ["e2e/**", "node_modules/**"],
  },
});
