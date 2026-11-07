"""The 'react' GUI tier: a Vite + React + TypeScript app that draws the module manifest.

``kdx init NAME --ui react`` writes ``web/`` from these templates plus a tiny module
(``web_app``) that serves the built ``web/dist`` on the control plane at ``/app/``. The app
is a client of ``/api/v1/*`` like every other GUI: it reads ``/api/v1/ui/manifest`` and draws
one tab per module that declares a ``[ui]`` table, a project replaces any panel with its own
React component (``src/overrides.tsx``), and it honours the person's saved choices
(``/api/v1/ui/prefs``). Tests: Vitest (components, jsdom, no browser) and Playwright
(end to end against a real daemon, headless by default, ``PW_HEADED=1`` shows the window).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .init_project import ProjectTemplate

PM_COMMANDS: dict[str, dict[str, str]] = {
    "npm": {"install": "npm install", "run": "npm run"},
    "bun": {"install": "bun install", "run": "bun run"},
    "pnpm": {"install": "pnpm install", "run": "pnpm run"},
}

_PACKAGE_JSON = r"""{
  "name": "@DIST@-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview",
    "typecheck": "tsc --noEmit",
    "test": "vitest run",
    "e2e": "playwright test"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@playwright/test": "^1.48.2",
    "@testing-library/dom": "^10.4.0",
    "@testing-library/jest-dom": "^6.6.3",
    "@testing-library/react": "^16.0.1",
    "@types/node": "^22.9.0",
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.3",
    "jsdom": "^25.0.1",
    "typescript": "^5.6.3",
    "vite": "^5.4.10",
    "vitest": "^2.1.4"
  }
}
"""

_TSCONFIG = r"""{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noEmit": true,
    "skipLibCheck": true,
    "isolatedModules": true,
    "types": ["vite/client", "vitest/globals", "node"]
  },
  "include": ["src", "e2e", "vite.config.ts", "playwright.config.ts"]
}
"""

_VITE_CONFIG = r"""import react from "@vitejs/plugin-react";
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
"""

_PLAYWRIGHT_CONFIG = r"""import { defineConfig } from "@playwright/test";

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
      env: { KDX_CORE__IPC__TOKEN: token },
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
"""

_INDEX_HTML = r"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>@TITLE@</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
"""

_TEST_SETUP = r"""import "@testing-library/jest-dom/vitest";
"""

_MAIN = r"""import React from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./overrides";
import "./styles.css";

createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
"""

_TYPES = r"""export type PanelType = "table" | "keyvalue" | "log" | "text" | "form" | "actions";
export type Scalar = string | number | boolean;

export interface ButtonSpec {
  id: string;
  label: string;
  command: string;
  args: Record<string, Scalar>;
  confirm: boolean;
  danger: boolean;
}

export interface PanelSpec {
  id: string;
  type: PanelType;
  title: string;
  help: string;
  refresh: number;
  source?: string;
  path?: string;
  columns?: { key: string; label: string }[];
  text?: string;
  command?: string;
  args?: Record<string, Scalar>;
  buttons?: ButtonSpec[];
}

export interface ModuleSpec {
  module: string;
  title: string;
  icon: string;
  order: number;
  panels: PanelSpec[];
}

export interface Manifest {
  version: number;
  locale: string;
  modules: ModuleSpec[];
  issues: { module: string; panel?: string; message: string }[];
}

export interface Overlay {
  label?: string;
  hidden?: boolean;
  order?: number;
}

export interface Prefs {
  nav: Record<string, Overlay>;
  panels: Record<string, Overlay>;
  values: Record<string, Record<string, Scalar>>;
  defaults: Record<string, Record<string, Scalar>>;
  settings: { last_tab?: string; locale?: string };
}

export interface CliArgSpec {
  dest: string;
  flags: string[];
  positional: boolean;
  kind: string;
  action: string | null;
  nargs: string | number | null;
  choices: string[];
  default: unknown;
  required: boolean;
  help: string;
}

export interface CommandSpec {
  path: string;
  help: string;
  module: string;
  remote: boolean;
  danger: boolean;
  role: string;
  args: CliArgSpec[];
}

export interface Status {
  app: { name?: string; state: string; version?: string };
  modules: { total: number };
}

export const emptyPrefs = (): Prefs => ({
  nav: {},
  panels: {},
  values: {},
  defaults: {},
  settings: {},
});
"""

_API = r"""const TOKEN_KEY = "kdx-token";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// The token arrives in the URL fragment (kdx ui prints such a link); a fragment never
// reaches a server or a log. It is kept for the tab's lifetime and the address bar is cleaned.
export function readToken(): string {
  const fromHash = new URLSearchParams(window.location.hash.slice(1)).get("token");
  if (fromHash) {
    try {
      sessionStorage.setItem(TOKEN_KEY, fromHash);
    } catch {
      /* private mode */
    }
    history.replaceState(null, "", window.location.pathname + window.location.search);
    return fromHash;
  }
  try {
    return sessionStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

interface Init {
  method?: string;
  body?: unknown;
}

export async function api<T>(path: string, init: Init = {}): Promise<T> {
  const hasBody = init.body !== undefined;
  const response = await fetch(path, {
    method: init.method ?? "GET",
    headers: {
      "X-Kdx-Token": readToken(),
      ...(hasBody ? { "Content-Type": "application/json" } : {}),
    },
    body: hasBody ? JSON.stringify(init.body) : undefined,
    cache: "no-store",
  });
  const payload = (await response.json().catch(() => ({}))) as T & { error?: string };
  if (!response.ok) {
    throw new ApiError(response.status, payload.error ?? `HTTP ${response.status}`);
  }
  return payload;
}

export function dig(value: unknown, path: string): unknown {
  if (!path) return value;
  return path.split(".").reduce<unknown>((node, key) => {
    return node !== null && typeof node === "object"
      ? (node as Record<string, unknown>)[key]
      : undefined;
  }, value);
}
"""

_ARGV = r"""import type { CommandSpec } from "./types";

// Turn { dest: value } into the argv a terminal user would type; the daemon parses it with
// the command's own declaration, so validation is identical to the CLI.
export function argvFrom(command: CommandSpec, values: Record<string, unknown>): string[] {
  const argv: string[] = [];
  for (const arg of command.args) {
    const value = values[arg.dest];
    if (value === undefined || value === null || value === "") continue;
    const flag = arg.flags[arg.flags.length - 1];
    if (arg.kind === "flag") {
      const on = value === true || value === "true";
      if (on !== (arg.default === true)) argv.push(flag);
      continue;
    }
    const many = Array.isArray(value)
      ? value.map(String)
      : arg.nargs === "*" || arg.nargs === "+"
        ? String(value).split(/\s+/)
        : [String(value)];
    if (arg.positional) {
      argv.push(...many);
    } else if (arg.action === "append") {
      for (const part of many) argv.push(flag, part);
    } else {
      argv.push(flag, ...many);
    }
  }
  return argv;
}
"""

_RUN = r"""import { api } from "./api";

export interface RunResult {
  ok: boolean;
  output: string;
}

interface Job {
  state: string;
  output?: string;
  error?: string;
  exit_code?: number | null;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

// Every command runs as a background job: the reply is immediate and the output is polled,
// so a long command never holds an HTTP request open.
export async function runCommand(
  command: string,
  argv: string[],
  onOutput?: (text: string) => void,
): Promise<RunResult> {
  const started = await api<{ ok?: boolean; error?: string; job?: { id: string } }>(
    "/api/v1/rpc",
    { method: "POST", body: { command, args: { argv }, background: true } },
  );
  if (!started.ok || !started.job) return { ok: false, output: started.error ?? "refused" };
  for (;;) {
    const job = await api<Job>(`/api/v1/jobs?id=${encodeURIComponent(started.job.id)}`);
    if (job.state === "running" || job.state === "cancelling") {
      onOutput?.(job.output ?? "");
      await sleep(500);
      continue;
    }
    const text = `${job.output ?? ""}${job.error ?? ""}` || "(no output)";
    return { ok: job.state === "done", output: text };
  }
}
"""

_STATE = r"""import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { api } from "./api";
import { emptyPrefs } from "./types";
import type { CommandSpec, Prefs } from "./types";

interface Ctx {
  prefs: Prefs;
  save: (changes: Record<string, unknown>) => Promise<void>;
  commands: CommandSpec[];
  messages: Record<string, string>;
}

const AppState = createContext<Ctx>({
  prefs: emptyPrefs(),
  save: async () => undefined,
  commands: [],
  messages: {},
});

export const useApp = () => useContext(AppState);

// A text the daemon's catalogue knows is translated; anything else is shown as written.
export function useT(): (text: string) => string {
  const { messages } = useApp();
  return (text) => messages[text] ?? text;
}

export function AppProvider({ children, locale }: { children: ReactNode; locale: string }) {
  const [prefs, setPrefs] = useState<Prefs>(emptyPrefs());
  const [commands, setCommands] = useState<CommandSpec[]>([]);
  const [messages, setMessages] = useState<Record<string, string>>({});

  useEffect(() => {
    api<Prefs>("/api/v1/ui/prefs")
      .then((loaded) => setPrefs({ ...emptyPrefs(), ...loaded }))
      .catch(() => undefined);
    api<{ commands: CommandSpec[] }>("/api/v1/ui/catalog")
      .then((loaded) => setCommands(loaded.commands))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    api<{ messages: Record<string, string> }>(
      `/api/v1/ui/i18n${locale ? `?locale=${encodeURIComponent(locale)}` : ""}`,
    )
      .then((loaded) => setMessages(loaded.messages))
      .catch(() => undefined);
  }, [locale]);

  const save = useCallback(async (changes: Record<string, unknown>) => {
    const next = await api<Prefs>("/api/v1/ui/prefs", { method: "PATCH", body: changes });
    setPrefs({ ...emptyPrefs(), ...next });
  }, []);

  const value = useMemo(() => ({ prefs, save, commands, messages }), [prefs, save, commands, messages]);
  return <AppState.Provider value={value}>{children}</AppState.Provider>;
}
"""

_OVERRIDES = r"""import type { ComponentType } from "react";

import type { PanelSpec } from "./types";

export interface PanelProps {
  module: string;
  panel: PanelSpec;
}

const registry = new Map<string, ComponentType<PanelProps>>();

// Replace the generic drawing of one panel with your own component. The key is
// "<module>.<panel id>", exactly as in 'kdx ui manifest'.
export function overridePanel(key: string, component: ComponentType<PanelProps>): void {
  registry.set(key, component);
}

export function overrideFor(module: string, panel: string): ComponentType<PanelProps> | undefined {
  return registry.get(`${module}.${panel}`);
}

// Example (delete or replace):
//
//   (add this file's own call here, e.g. with a component imported from ./my-panels)
//   overridePanel("job_queue.tasks", ({ panel }) => <h3>{panel.title}: my own view</h3>);
"""

_PANELS = r"""import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { api, dig } from "./api";
import { argvFrom } from "./argv";
import { overrideFor } from "./overrides";
import type { PanelProps } from "./overrides";
import { runCommand } from "./run";
import { useApp, useT } from "./state";
import type { ButtonSpec, Scalar } from "./types";

function useSource(source: string, path: string, refresh: number) {
  const [state, setState] = useState<{ data?: unknown; error?: string }>({});
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const payload = await api<unknown>(source);
        if (alive) setState({ data: dig(payload, path) });
      } catch (error) {
        if (alive) setState({ error: (error as Error).message });
      }
    };
    void load();
    const timer = refresh > 0 ? window.setInterval(() => void load(), refresh * 1000) : undefined;
    return () => {
      alive = false;
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [source, path, refresh]);
  return state;
}

const show = (value: unknown): string =>
  value === null || value === undefined
    ? "-"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);

function TablePanel({ panel }: PanelProps) {
  const t = useT();
  const { data, error } = useSource(panel.source ?? "", panel.path ?? "", panel.refresh);
  if (error) return <p className="error">{error}</p>;
  const rows = Array.isArray(data) ? (data as unknown[]) : [];
  const first = rows[0];
  const columns = panel.columns?.length
    ? panel.columns
    : first && typeof first === "object"
      ? Object.keys(first as object).map((key) => ({ key, label: key }))
      : [{ key: "", label: "value" }];
  return (
    <table>
      <thead>
        <tr>
          {columns.map((column) => (
            <th key={column.key}>{column.label}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.length ? (
          rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column.key}>{show(column.key ? dig(row, column.key) : row)}</td>
              ))}
            </tr>
          ))
        ) : (
          <tr>
            <td colSpan={columns.length} className="muted">
              {t("Nothing to show.")}
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

function KeyValuePanel({ panel }: PanelProps) {
  const { data, error } = useSource(panel.source ?? "", panel.path ?? "", panel.refresh);
  if (error) return <p className="error">{error}</p>;
  const pairs: [string, unknown][] =
    data && typeof data === "object" ? Object.entries(data) : [["value", data]];
  return (
    <table>
      <tbody>
        {pairs.map(([key, value]) => (
          <tr key={key}>
            <th>{key}</th>
            <td>{show(value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function LogPanel({ panel }: PanelProps) {
  const { data, error } = useSource(panel.source ?? "", panel.path ?? "", panel.refresh);
  if (error) return <p className="error">{error}</p>;
  const text = Array.isArray(data) ? data.join("\n") : show(data === undefined ? "" : data);
  return <pre className="terminal">{text}</pre>;
}

function Output({ text }: { text: string }) {
  return text ? <pre className="terminal">{text}</pre> : null;
}

function FormPanel({ module, panel }: PanelProps) {
  const t = useT();
  const { commands, prefs, save } = useApp();
  const command = commands.find((item) => item.path === panel.command);
  const key = `${module}.${panel.id}`;
  const [values, setValues] = useState<Record<string, Scalar>>({
    ...(panel.args ?? {}),
    ...(prefs.defaults[key] ?? {}),
    ...(prefs.values[key] ?? {}),
  });
  const [output, setOutput] = useState("");
  if (!command) return <p className="muted">{panel.command}</p>;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    await save({ values: { [key]: values } });
    const result = await runCommand(command.path, argvFrom(command, values), setOutput);
    setOutput(result.output);
  };

  return (
    <form onSubmit={(event) => void submit(event)}>
      {command.args.map((arg) => {
        const label = arg.positional ? arg.dest : arg.flags[arg.flags.length - 1];
        const current = values[arg.dest];
        const set = (value: Scalar) => setValues({ ...values, [arg.dest]: value });
        return (
          <label key={arg.dest} className="field">
            <span>{label}</span>
            {arg.kind === "flag" ? (
              <input type="checkbox" checked={current === true} onChange={(e) => set(e.target.checked)} />
            ) : arg.choices.length ? (
              <select value={String(current ?? "")} onChange={(e) => set(e.target.value)}>
                <option value="">(default)</option>
                {arg.choices.map((choice) => (
                  <option key={choice}>{choice}</option>
                ))}
              </select>
            ) : (
              <input
                type={arg.kind === "int" || arg.kind === "float" ? "number" : "text"}
                value={String(current ?? "")}
                placeholder={arg.default != null ? `default: ${String(arg.default)}` : ""}
                onChange={(e) => set(e.target.value)}
              />
            )}
            <small className="muted">{arg.help}</small>
          </label>
        );
      })}
      <div className="row">
        <button type="submit" className="primary">
          {t("Run")}
        </button>
        <button
          type="button"
          onClick={() => void save({ defaults: { [key]: values } })}
        >
          {t("Save as default")}
        </button>
        <button
          type="button"
          onClick={() => setValues({ ...(panel.args ?? {}), ...(prefs.defaults[key] ?? {}) })}
        >
          {t("Restore defaults")}
        </button>
      </div>
      <Output text={output} />
    </form>
  );
}

function ActionButton({ button }: { button: ButtonSpec }) {
  const t = useT();
  const { commands } = useApp();
  const [armed, setArmed] = useState(false);
  const [output, setOutput] = useState("");
  const command = commands.find((item) => item.path === button.command);
  const click = async () => {
    if ((button.confirm || button.danger) && !armed) {
      setArmed(true);
      window.setTimeout(() => setArmed(false), 5000);
      return;
    }
    setArmed(false);
    if (!command) return;
    const result = await runCommand(command.path, argvFrom(command, button.args), setOutput);
    setOutput(result.output);
  };
  return (
    <div>
      <button
        type="button"
        className={button.danger ? "danger" : ""}
        disabled={!command}
        onClick={() => void click()}
      >
        {armed ? t("Click again to run") : button.label}
      </button>
      <Output text={output} />
    </div>
  );
}

function ActionsPanel({ panel }: PanelProps) {
  return (
    <div className="row">
      {(panel.buttons ?? []).map((button) => (
        <ActionButton key={button.id} button={button} />
      ))}
    </div>
  );
}

const GENERIC = {
  table: TablePanel,
  keyvalue: KeyValuePanel,
  log: LogPanel,
  text: ({ panel }: PanelProps) => <p>{panel.text}</p>,
  form: FormPanel,
  actions: ActionsPanel,
} as const;

export function Panel({ module, panel }: PanelProps) {
  const Custom = overrideFor(module, panel.id);
  const Component = Custom ?? GENERIC[panel.type];
  return (
    <section className="card panel" data-panel={`${module}.${panel.id}`}>
      <h2>{panel.title}</h2>
      {panel.help ? <p className="muted">{panel.help}</p> : null}
      <Component module={module} panel={panel} />
    </section>
  );
}
"""

_APP = r"""import { useEffect, useMemo, useState } from "react";

import { api } from "./api";
import { Panel } from "./panels";
import { AppProvider, useApp, useT } from "./state";
import type { Manifest, ModuleSpec, Status } from "./types";

function Shell({ onLocale, locale }: { onLocale: (code: string) => void; locale: string }) {
  const t = useT();
  const { prefs, save } = useApp();
  const [status, setStatus] = useState<Status | null>(null);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState("");

  useEffect(() => {
    api<Status>("/api/v1/status").then(setStatus).catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    api<Manifest>(`/api/v1/ui/manifest${locale ? `?locale=${encodeURIComponent(locale)}` : ""}`)
      .then(setManifest)
      .catch((e: Error) => setError(e.message));
  }, [locale]);

  // The manifest is the default; the person's saved labels, order and hiding overlay it.
  const modules: ModuleSpec[] = useMemo(() => {
    const list = (manifest?.modules ?? [])
      .filter((entry) => !prefs.nav[entry.module]?.hidden)
      .map((entry) => ({
        ...entry,
        title: prefs.nav[entry.module]?.label ?? entry.title,
        order: prefs.nav[entry.module]?.order ?? entry.order,
        panels: entry.panels
          .filter((panel) => !prefs.panels[`${entry.module}.${panel.id}`]?.hidden)
          .map((panel, index) => ({
            ...panel,
            title: prefs.panels[`${entry.module}.${panel.id}`]?.label ?? panel.title,
            order: prefs.panels[`${entry.module}.${panel.id}`]?.order ?? index * 10,
          }))
          .sort((a, b) => a.order - b.order),
      }));
    return list.sort((a, b) => a.order - b.order || a.module.localeCompare(b.module));
  }, [manifest, prefs]);

  const active =
    modules.find((entry) => entry.module === tab) ??
    modules.find((entry) => entry.module === prefs.settings.last_tab) ??
    modules[0];

  const choose = (module: string) => {
    setTab(module);
    void save({ settings: { last_tab: module } });
  };

  return (
    <div className="shell">
      <header className="topbar">
        <h1>@TITLE@</h1>
        {status ? (
          <span className="pill" data-testid="state">
            {status.app.state} - {status.modules.total} modules
          </span>
        ) : null}
        <select aria-label="language" value={locale} onChange={(e) => onLocale(e.target.value)}>
          <option value="">auto</option>
          <option value="en">English</option>
          <option value="sk">Slovenčina</option>
        </select>
      </header>
      {error ? <p className="error">{error}</p> : null}
      <nav className="tabs" role="tablist">
        {modules.map((entry) => (
          <button
            key={entry.module}
            role="tab"
            aria-selected={entry.module === active?.module}
            className={entry.module === active?.module ? "active" : ""}
            onClick={() => choose(entry.module)}
          >
            {entry.title}
          </button>
        ))}
      </nav>
      <main>
        {active ? (
          active.panels.map((panel) => <Panel key={panel.id} module={active.module} panel={panel} />)
        ) : (
          <p className="muted">
            {t("No module declares a [ui] table yet. Add one to a kdx_module.toml.")}
          </p>
        )}
        {manifest?.issues.map((issue) => (
          <p key={`${issue.module}.${issue.panel ?? ""}.${issue.message}`} className="error">
            {issue.module}
            {issue.panel ? `.${issue.panel}` : ""}: {issue.message}
          </p>
        ))}
      </main>
    </div>
  );
}

export function App() {
  const [locale, setLocale] = useState("");
  return (
    <AppProvider locale={locale}>
      <Shell locale={locale} onLocale={setLocale} />
    </AppProvider>
  );
}
"""

_STYLES = r""":root {
  color-scheme: light dark;
  --line: color-mix(in srgb, currentColor 18%, transparent);
  --accent: #3157d5;
  font-family: ui-sans-serif, system-ui, sans-serif;
}
body { margin: 0; }
.shell { max-width: 64rem; margin: 0 auto; padding: 1rem; }
.topbar { display: flex; gap: 0.75rem; align-items: center; }
.topbar h1 { font-size: 1.2rem; margin: 0 auto 0 0; }
.pill { border: 1px solid var(--line); border-radius: 999px; padding: 0.1rem 0.6rem; }
.tabs { display: flex; gap: 0.25rem; margin: 1rem 0; border-bottom: 1px solid var(--line); }
.tabs button { border: 0; background: none; padding: 0.5rem 0.75rem; cursor: pointer; font: inherit; color: inherit; }
.tabs button.active { border-bottom: 2px solid var(--accent); }
.card { border: 1px solid var(--line); border-radius: 0.5rem; padding: 0.75rem 1rem; margin-bottom: 1rem; }
.card h2 { font-size: 1rem; margin: 0 0 0.5rem; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 0.25rem 0.5rem; border-bottom: 1px solid var(--line); }
.muted { opacity: 0.65; }
.error { color: #c0362c; }
.field { display: grid; gap: 0.15rem; margin-bottom: 0.5rem; }
.row { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
button, input, select { font: inherit; padding: 0.3rem 0.6rem; }
button.primary { background: var(--accent); color: #fff; border: 1px solid var(--accent); border-radius: 0.3rem; }
button.danger { color: #c0362c; }
.terminal { background: #10141b; color: #d6dde8; padding: 0.5rem; overflow: auto; border-radius: 0.3rem; }
"""

_APP_TEST = r"""import { render, screen } from "@testing-library/react";
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
"""

_ARGV_TEST = r"""import { expect, test } from "vitest";

import { argvFrom } from "./argv";
import type { CommandSpec } from "./types";

const command: CommandSpec = {
  path: "queue submit",
  help: "",
  module: "job_queue",
  remote: true,
  danger: false,
  role: "operator",
  args: [
    { dest: "kind", flags: ["kind"], positional: true, kind: "str", action: null, nargs: null, choices: [], default: null, required: true, help: "" },
    { dest: "delay", flags: ["--delay"], positional: false, kind: "float", action: null, nargs: null, choices: [], default: 0, required: false, help: "" },
    { dest: "as_json", flags: ["--json"], positional: false, kind: "flag", action: "store_true", nargs: null, choices: [], default: false, required: false, help: "" },
  ],
};

test("builds the argv a terminal user would type", () => {
  expect(argvFrom(command, { kind: "demo", delay: 2, as_json: true })).toEqual([
    "demo",
    "--delay",
    "2",
    "--json",
  ]);
  expect(argvFrom(command, { kind: "demo", delay: "", as_json: false })).toEqual(["demo"]);
});
"""

_E2E = r"""import { expect, test } from "@playwright/test";

// Needs the daemon and the built app: playwright.config.ts starts both.
test("the app shows the live daemon and its module tabs", async ({ page }) => {
  await page.goto("/#token=e2e-token");
  await expect(page.getByTestId("state")).toContainText("running");
  await expect(page.getByRole("tab").first()).toBeVisible();
});
"""

_WEB_README = r"""# @TITLE@ - web app

React + TypeScript + Vite. It is a client of the chassis control plane (`/api/v1/*`): it reads
`/api/v1/ui/manifest` and draws one tab per module that declares a `[ui]` table in its
`kdx_module.toml` (docs/AGENTS_GUIDE.md section 8.1). Nothing here holds core logic.

## Commands (package manager: @PM@)

```bash
@INSTALL@          # once
@RUN@ dev          # http://127.0.0.1:5173, proxies /api to the daemon (KDX_DAEMON overrides)
@RUN@ typecheck
@RUN@ test         # Vitest, jsdom: no browser, no daemon
@RUN@ build        # web/dist, served by the daemon at /app/ once built (module web_app)
@RUN@ e2e          # Playwright against a real daemon; headless unless PW_HEADED=1
```

Open the app with the token in the URL fragment: `kdx ui` prints such a link, or use
`http://127.0.0.1:5173/#token=<token from var/chassis.ipc>`. The fragment never reaches a server.

## Testing

Headless first. `kdx ui doctor --json` says whether this machine has a browser and a display.
`PW_CHANNEL=chrome` (or `msedge`) uses an installed browser instead of a Playwright download
(`npx playwright install chromium`); `PW_HEADED=1` opens the window when there is a display.

## Known limits (also in `kdx ui guide`)

- Renaming, hiding, reordering and own shortcut buttons are edited in the built-in console
  (`kdx ui`, Customize) or with `kdx ui prefs patch`; this app shows them and writes only
  remembered form values, pinned defaults and the last tab.
- npm is verified end to end; bun and pnpm use the same templates, so run
  `@RUN@ typecheck && @RUN@ test` first.
- A newer npm may block the esbuild install script. If vite fails with an esbuild error, run
  `npm install-scripts approve esbuild` and install again.
- `@RUN@ e2e` starts the daemon with `uv run @SCRIPT@ run`: run `uv sync --extra dev` in the
  project root first.

## Extending

- A new module needs no code here: declare `[ui]` panels and they appear.
- To draw one panel yourself, call `overridePanel("<module>.<panel>", Component)` from
  `src/overrides.tsx`.
- The person's saved choices (labels, hidden panels, order, remembered form values, last tab)
  come from `/api/v1/ui/prefs` (`kdx ui prefs`); keep per-person state there, not in code.
"""

_WEB_APP_MODULE = r'''"""Serves the built web app (web/dist) on the control plane at /app/."""

from __future__ import annotations

from pathlib import Path

from korodexios_core.kernel.contract import Module
from korodexios_core.kernel.ipc import IpcStaticMount


class WebAppModule(Module):
    """Publishes web/dist once it has been built; before that it serves nothing."""

    name = "web_app"
    description = "Serves the built React app (web/dist) at /app/ on the control plane."
    version = "0.1.0"

    def static_dirs(self) -> tuple[IpcStaticMount, ...]:
        """Mount web/dist when it exists; the files carry no secret, so no token is needed."""
        if self.context is None:
            return ()
        dist = Path(self.paths.root) / "web" / "dist"
        if not dist.is_dir():
            return ()
        return (IpcStaticMount(route_prefix="/app", directory=dist, auth=False),)


MODULE = WebAppModule()
'''


def react_files(project: ProjectTemplate) -> dict[str, str]:
    """Return the files of the react tier, keyed by project-relative path."""
    commands = PM_COMMANDS[project.pm]
    tokens = {
        "@DIST@": project.distribution,
        "@TITLE@": project.title,
        "@PM@": project.pm,
        "@SCRIPT@": project.script,
        "@INSTALL@": commands["install"],
        "@RUN@": commands["run"],
    }

    def fill(text: str) -> str:
        for token, value in tokens.items():
            text = text.replace(token, value)
        return text

    web = {
        "package.json": _PACKAGE_JSON,
        "tsconfig.json": _TSCONFIG,
        "vite.config.ts": _VITE_CONFIG,
        "playwright.config.ts": _PLAYWRIGHT_CONFIG,
        "index.html": _INDEX_HTML,
        "README.md": _WEB_README,
        "src/main.tsx": _MAIN,
        "src/App.tsx": _APP,
        "src/App.test.tsx": _APP_TEST,
        "src/api.ts": _API,
        "src/argv.ts": _ARGV,
        "src/argv.test.ts": _ARGV_TEST,
        "src/run.ts": _RUN,
        "src/state.tsx": _STATE,
        "src/panels.tsx": _PANELS,
        "src/overrides.tsx": _OVERRIDES,
        "src/types.ts": _TYPES,
        "src/styles.css": _STYLES,
        "src/test-setup.ts": _TEST_SETUP,
        "e2e/smoke.spec.ts": _E2E,
    }
    files = {f"web/{path}": fill(text) for path, text in web.items()}
    package = project.package
    files[f"src/{package}/modules/web_app/__init__.py"] = '"""Serves the built web app."""\n'
    files[f"src/{package}/modules/web_app/module.py"] = _WEB_APP_MODULE
    files[f"src/{package}/modules/web_app/kdx_module.toml"] = "\n".join(
        [
            "[module]",
            'name = "web_app"',
            'description = "Serves the built React app (web/dist) at /app/ on the control plane."',
            'entrypoint = "module.MODULE"',
            'version = "0.1.0"',
            "priority = 90",
            'requires_core = ">=0.1,<1.0"',
            "depends_on = []",
            "",
        ]
    )
    return files


__all__ = ["PM_COMMANDS", "react_files"]
