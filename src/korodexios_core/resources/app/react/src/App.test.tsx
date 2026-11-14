import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { App } from "./App";

const answers: Record<string, unknown> = {
  "/api/v1/status": { app: { state: "running" }, modules: { total: 2 } },
  "/api/v1/ui/prefs": { nav: {}, panels: {}, values: {}, defaults: {}, settings: {} },
  "/api/v1/ui/catalog": { commands: [] },
  "/api/v1/ui/i18n": { messages: {} },
  "/api/v1/ui/manifest": {
    version: 1,
    locale: "en",
    issues: [],
    modules: [
      {
        module: "demo",
        title: "Demo",
        icon: "",
        order: 1,
        panels: [
          {
            id: "rows",
            type: "table",
            title: "Rows",
            help: "",
            refresh: 0,
            source: "/api/v1/demo",
            path: "items",
            columns: [{ key: "name", label: "Name" }],
          },
        ],
      },
    ],
  },
  "/api/v1/demo": { items: [{ name: "alpha" }, { name: "beta" }] },
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const key = String(url).split("?")[0];
      return new Response(JSON.stringify(answers[key] ?? {}), {
        status: key in answers ? 200 : 404,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

test("draws a tab and its table from the manifest", async () => {
  render(<App />);
  expect(await screen.findByRole("tab", { name: "Demo" })).toBeInTheDocument();
  expect(await screen.findByText("alpha")).toBeInTheDocument();
  expect(screen.getByText("beta")).toBeInTheDocument();
  expect(await screen.findByTestId("state")).toHaveTextContent("running - 2 modules");
});

test("a refused session shows the sign-in form instead of an error", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ error: "token rejected" }), { status: 401 })),
  );
  render(<App />);
  expect(await screen.findByLabelText("token")).toBeInTheDocument();
});
