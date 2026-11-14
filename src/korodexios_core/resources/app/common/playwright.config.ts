import { defineConfig } from "@playwright/test";

// End-to-end: a real daemon plus the built app. Headless by default (works on a server);
// PW_HEADED=1 opens the browser window when the machine has a display, and PW_CHANNEL=chrome
// (or msedge) uses an installed browser instead of Playwright's own download.
const pm = "@PM@";
const token = "e2e-token";

export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:4173",
    headless: !process.env.PW_HEADED,
    channel: process.env.PW_CHANNEL || undefined,
  },
  webServer: [
    {
      command: "uv run @SCRIPT@ run",
      cwd: "..",
      url: "http://127.0.0.1:18123/health",
      reuseExistingServer: true,
      // kdx.yml has port 0 (ephemeral); the health check and the vite proxy expect 18123
      env: { KDX_CORE__IPC__TOKEN: token, KDX_CORE__IPC__PORT: "18123" },
      timeout: 60_000,
    },
    {
      command: `${pm} run build && ${pm} run preview`,
      url: "http://127.0.0.1:4173",
      reuseExistingServer: true,
      timeout: 120_000,
    },
  ],
});
