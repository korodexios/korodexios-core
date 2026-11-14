import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";

import { ApiError, manifest as loadManifest, signIn, status as loadStatus } from "./kdx";
import type { Manifest, ModuleSpec, Status } from "./kdx";
import { Panel } from "./panels";
import { AppProvider, useApp, useT } from "./state";

// Shown when the daemon refuses us: the token is typed here, never put into code or a URL.
function SignIn({ onDone }: { onDone: () => void }) {
  const t = useT();
  const [token, setToken] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    await signIn(token);
    setToken("");
    onDone();
  };
  return (
    <form className="card" onSubmit={(event) => void submit(event)}>
      <h2>{t("Sign in")}</h2>
      <p className="muted">{t("Paste the token from 'kdx ui --no-browser' or var/chassis.ipc.")}</p>
      <input
        type="password"
        aria-label="token"
        autoComplete="off"
        value={token}
        onChange={(event) => setToken(event.target.value)}
      />
      <button type="submit" className="primary">
        {t("Sign in")}
      </button>
    </form>
  );
}

function Shell({ onLocale, locale, onSignedIn }: { onLocale: (code: string) => void; locale: string; onSignedIn: () => void }) {
  const t = useT();
  const { prefs, save } = useApp();
  const [status, setStatus] = useState<Status | null>(null);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [error, setError] = useState("");
  const [locked, setLocked] = useState(false);
  const [tab, setTab] = useState("");

  const fail = (reason: Error) => {
    if (reason instanceof ApiError && reason.status === 401) setLocked(true);
    else setError(reason.message);
  };

  useEffect(() => {
    loadStatus().then(setStatus).catch(fail);
  }, []);

  useEffect(() => {
    loadManifest(locale).then(setManifest).catch(fail);
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

  if (locked) return <SignIn onDone={onSignedIn} />;

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
  // a new key after signing in mounts everything again, so every panel loads with the session
  const [session, setSession] = useState(0);
  return (
    <AppProvider key={session} locale={locale}>
      <Shell locale={locale} onLocale={setLocale} onSignedIn={() => setSession((n) => n + 1)} />
    </AppProvider>
  );
}
