// Korodexios web console: a thin client of the chassis control plane.
// Every value from the daemon is written with textContent, never as HTML.
"use strict";

(function () {
  const TOKEN_HEADER = "X-Kdx-Token";
  const STORE_KEY = "kdx-console-token";
  const REFRESH_MS = 5000;
  const FOLLOW_MS = 2000;
  const JOB_POLL_MS = 500;
  const CONFIRM_MS = 5000;
  const MAX_EVENTS = 300;
  const ROUTINE_EVENTS = /^(core\.tick|core\.heartbeat|core\.task\.)/;

  const state = { token: "", status: null, catalog: [], config: [], selected: null, events: [], streaming: false, jobsLoaded: false, me: null, audit: [] };
  const ROLE_RANK = { viewer: 0, operator: 1, admin: 2 };

  function can(role) {
    const mine = state.me && state.me.role;
    if (!mine) return true; // anonymous reads with auth off; the daemon still decides
    return (ROLE_RANK[mine] ?? -1) >= (ROLE_RANK[role] ?? 99);
  }

  // ---- language ------------------------------------------------------------
  // The page's texts live in the daemon's catalogues (domain korodexios-web); the
  // original English of each static element is kept in data-en so a switch always
  // translates from the source, never from an earlier translation.

  const LOCALE_KEY = "kdx-console-locale";
  const LANGUAGE_NAMES = { en: "English", sk: "Slovenčina", cs: "Čeština", de: "Deutsch", es: "Español", fr: "Français", pl: "Polski" };
  const i18n = { locale: "en", messages: {} };
  const KEEP_AS_IS = new Set(["state-pill", "events-state", "toast", "app-name", "app-version"]);

  function t(text) {
    const key = String(text);
    return Object.prototype.hasOwnProperty.call(i18n.messages, key) ? i18n.messages[key] : key;
  }

  function squash(text) { return text.replace(/\s+/g, " ").trim(); }

  function markStatic() {
    document.querySelectorAll("body *").forEach((node) => {
      if (KEEP_AS_IS.has(node.id) || ["SCRIPT", "STYLE", "OPTION", "CODE", "PRE"].includes(node.tagName)) return;
      if (node.children.length === 0 && node.childNodes.length === 1 && node.firstChild.nodeType === 3) {
        const text = squash(node.textContent);
        if (text) node.dataset.en = text;
      }
      ["placeholder", "title", "aria-label"].forEach((attr) => {
        if (node.hasAttribute(attr)) node.dataset["en" + attr.replace(/(^|-)([a-z])/g, (m, d, c) => c.toUpperCase())] = node.getAttribute(attr);
      });
    });
  }

  function applyStatic() {
    document.documentElement.lang = i18n.locale;
    document.querySelectorAll("[data-en]").forEach((node) => { node.textContent = t(node.dataset.en); });
    [["placeholder", "enPlaceholder"], ["title", "enTitle"], ["aria-label", "enAriaLabel"]].forEach(([attr, key]) => {
      document.querySelectorAll("*").forEach((node) => { if (node.dataset && node.dataset[key]) node.setAttribute(attr, t(node.dataset[key])); });
    });
  }

  async function loadI18n(requested) {
    const query = requested ? `?locale=${encodeURIComponent(requested)}` : "";
    const { payload } = await api(`/api/v1/ui/i18n${query}`);
    i18n.locale = payload.locale || "en";
    i18n.messages = payload.messages || {};
    const select = $("lang");
    select.replaceChildren(...(payload.locales || ["en"]).map((code) => el("option", { value: code, text: LANGUAGE_NAMES[code] || code })));
    select.value = i18n.locale;
    applyStatic();
  }

  // ---- small helpers -------------------------------------------------------

  const $ = (id) => document.getElementById(id);

  function el(tag, props, children) {
    const node = document.createElement(tag);
    Object.entries(props || {}).forEach(([key, value]) => {
      if (key === "text") node.textContent = value == null ? "" : t(value);
      else if (key === "class") node.className = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (value !== undefined && value !== null && value !== false) node.setAttribute(key, value === true ? "" : value);
    });
    (children || []).forEach((child) => node.append(child));
    return node;
  }

  function toast(message, isError) {
    const box = $("toast");
    box.textContent = t(message);
    box.className = isError ? "toast error" : "toast";
    box.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { box.hidden = true; }, 4000);
  }

  // A risky button asks twice instead of opening a blocking dialog: the first click
  // arms it for a few seconds and says so, the second one acts.
  function armed(button, label) {
    if (button.dataset.armed === "1") {
      button.dataset.armed = "";
      button.textContent = button.dataset.label;
      return true;
    }
    button.dataset.label = button.dataset.label || button.textContent;
    button.dataset.armed = "1";
    button.textContent = label;
    clearTimeout(button.armTimer);
    button.armTimer = setTimeout(() => {
      button.dataset.armed = "";
      button.textContent = button.dataset.label;
    }, CONFIRM_MS);
    return false;
  }

  function readToken() {
    const hash = new URLSearchParams(location.hash.slice(1));
    const fromHash = hash.get("token");
    if (fromHash) {
      try { sessionStorage.setItem(STORE_KEY, fromHash); } catch (e) { /* private mode */ }
      history.replaceState(null, "", location.pathname + location.search);
      return fromHash;
    }
    try { return sessionStorage.getItem(STORE_KEY) || ""; } catch (e) { return ""; }
  }

  class AuthError extends Error {}

  async function api(path, options) {
    const opts = options || {};
    const headers = { [TOKEN_HEADER]: state.token };
    let body;
    if (opts.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.body);
    }
    const response = await fetch(path, { method: opts.method || "GET", headers, body, cache: "no-store" });
    let payload = {};
    try { payload = await response.json(); } catch (e) { payload = {}; }
    if (response.status === 401) throw new AuthError(payload.error || "token rejected");
    if (!response.ok && !opts.allowError) throw new Error(payload.error || `HTTP ${response.status}`);
    return { status: response.status, payload };
  }

  function fmtSeconds(value) {
    const total = Math.floor(Number(value) || 0);
    const d = Math.floor(total / 86400), h = Math.floor((total % 86400) / 3600);
    const m = Math.floor((total % 3600) / 60), s = total % 60;
    if (d) return `${d}d ${h}h`;
    if (h) return `${h}h ${m}m`;
    if (m) return `${m}m ${s}s`;
    return `${s}s`;
  }

  function fmtValue(value) {
    if (value === null || value === undefined) return "-";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  function fill(tbody, rows) {
    tbody.replaceChildren(...rows);
  }

  // ---- token gate ----------------------------------------------------------

  function showGate(message) {
    $("token-gate").hidden = false;
    const error = $("token-error");
    error.hidden = !message;
    error.textContent = message || "";
    $("state-pill").textContent = "locked";
    $("state-pill").className = "pill warn";
  }

  $("token-form").addEventListener("submit", (event) => {
    event.preventDefault();
    state.token = $("token-input").value.trim();
    try { sessionStorage.setItem(STORE_KEY, state.token); } catch (e) { /* private mode */ }
    $("token-input").value = "";
    refreshAll();
  });

  async function guarded(task) {
    try {
      await task();
      $("token-gate").hidden = true;
    } catch (error) {
      if (error instanceof AuthError) showGate(error.message);
      else if (error instanceof TypeError) setOffline();
      else toast(error.message, true);
    }
  }

  function setOffline() {
    $("state-pill").textContent = t("offline");
    $("state-pill").className = "pill bad";
    $("uptime").textContent = "";
  }

  // ---- tabs ----------------------------------------------------------------

  document.querySelectorAll(".tabs button").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b === button));
      document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.id === `tab-${button.dataset.tab}`));
      if (button.dataset.tab === "logs") guarded(loadLogs);
      if (button.dataset.tab === "config") guarded(loadConfig);
      if (button.dataset.tab === "audit") guarded(loadAudit);
    });
  });

  // ---- overview and diagnostics -------------------------------------------

  function tile(label, value, tone) {
    return el("div", { class: `tile ${tone || ""}` }, [
      el("div", { class: "tile-value", text: value }),
      el("div", { class: "tile-label", text: label }),
    ]);
  }

  async function loadMe() {
    const { payload } = await api("/api/v1/whoami");
    state.me = payload.name ? payload : null;
    const badge = $("whoami");
    badge.textContent = state.me ? `${state.me.name} · ${state.me.role}` : "anonymous";
    badge.className = `pill ${state.me && state.me.role === "admin" ? "ok" : ""}`;
    const admin = can("admin");
    $("btn-reload").disabled = !admin;
    $("btn-stop").disabled = !admin;
    $("btn-reload").title = admin ? "Re-read configuration (kdx reload)" : "needs the admin role";
    $("btn-stop").title = admin ? "Stop the daemon (exit 0)" : "needs the admin role";
    $("tab-button-audit").hidden = !admin;
  }

  async function loadStatus() {
    const { payload } = await api("/api/v1/status");
    state.status = payload;
    const app = payload.app || {};
    $("app-name").textContent = (payload.project && payload.project.name) || app.name || "korodexios";
    $("app-version").textContent = `v${app.version || "?"} · core api ${app.core_api || "?"}`;
    document.title = `${$("app-name").textContent} · console`;
    const pill = $("state-pill");
    pill.textContent = app.state || "unknown";
    pill.className = `pill ${app.state === "running" ? "ok" : "warn"}`;
    $("uptime").textContent = `up ${fmtSeconds(app.uptime_seconds)}`;

    const modules = payload.modules || {};
    const diag = payload.diagnostics || {};
    const sched = payload.scheduler || {};
    const ipc = payload.ipc || {};
    $("tiles").replaceChildren(
      tile("modules", `${(modules.by_state || {}).running || 0}/${modules.total || 0} running`, (modules.failed || []).length ? "bad" : "ok"),
      tile("diagnostics", `${diag.errors || 0} errors · ${diag.warnings || 0} warnings`, diag.errors ? "bad" : diag.warnings ? "warn" : "ok"),
      tile("scheduler", `${sched.scheduled || 0} tasks · ${sched.failures || 0} failures`, sched.failures ? "warn" : ""),
      tile("memory", (payload.memory || {}).rss || "-"),
      tile("control plane", `${ipc.tls ? "https" : "http"}://${ipc.bound_host || "?"}:${ipc.port || "?"}${ipc.lan_accessible ? " (LAN)" : ""}`, ipc.lan_accessible && !ipc.tls ? "warn" : "ok"),
      tile("requests", `${ipc.requests || 0} served · ${ipc.errors || 0} errors`),
    );

    fill($("modules-table").tBodies[0], (modules.modules || []).map((m) => el("tr", {}, [
      el("td", { text: m.name }),
      el("td", { text: m.version }),
      el("td", {}, [el("span", { class: `pill ${m.state === "running" ? "ok" : m.state === "failed" ? "bad" : "warn"}`, text: m.state })]),
      el("td", { text: m.origin }),
      el("td", { text: (m.commands || []).join(", ") || "-" }),
      el("td", { text: m.error ? `${m.description} — ${m.error}` : m.description }),
    ])));

    fill($("tasks-table").tBodies[0], (sched.tasks || []).map((t) => el("tr", {}, [
      el("td", { text: t.name }),
      el("td", { text: t.module || "-" }),
      el("td", { text: `${t.interval_seconds}s` }),
      el("td", { text: t.runs }),
      el("td", { text: t.failures }),
      el("td", { text: t.last_error || "-" }),
    ])));

    $("diag-summary").replaceChildren(
      tile("errors", diag.errors || 0, diag.errors ? "bad" : "ok"),
      tile("warnings", diag.warnings || 0, diag.warnings ? "warn" : "ok"),
      tile("info", diag.info || 0),
    );
    $("diagnostics").replaceChildren(...(diag.checks || []).map((c) => el("li", { class: `check ${c.severity}` }, [
      el("span", { class: "sev", text: c.severity }),
      el("strong", { text: `${c.module} · ${c.name}` }),
      el("div", { text: c.message }),
      c.hint ? el("div", { class: "muted", text: c.hint }) : "",
    ])));
  }

  // ---- commands ------------------------------------------------------------

  async function loadCatalog() {
    const { payload } = await api("/api/v1/ui/catalog");
    state.catalog = payload.commands || [];
    renderCommandList();
  }

  function renderCommandList() {
    const needle = $("command-filter").value.trim().toLowerCase();
    const groups = new Map();
    state.catalog
      .filter((c) => !needle || c.path.toLowerCase().includes(needle) || (c.help || "").toLowerCase().includes(needle))
      .forEach((c) => {
        if (!groups.has(c.module)) groups.set(c.module, []);
        groups.get(c.module).push(c);
      });
    const items = [];
    groups.forEach((commands, module) => {
      items.push(el("li", { class: "group", text: module }));
      commands.forEach((command) => items.push(el("li", {}, [
        el("button", {
          type: "button",
          class: state.selected && state.selected.path === command.path ? "active" : "",
          title: command.help,
          onclick: () => selectCommand(command),
        }, [
          el("span", { text: command.path }),
          !command.remote ? el("span", { class: "badge", text: "terminal" }) : "",
          command.danger ? el("span", { class: "badge warn", text: "changes" }) : "",
          command.remote && !can(command.role) ? el("span", { class: "badge", text: `needs ${command.role}` }) : "",
        ]),
      ])));
    });
    if (!items.length) items.push(el("li", { class: "muted", text: "no module commands" }));
    $("commands").replaceChildren(...items);
  }

  $("command-filter").addEventListener("input", renderCommandList);

  function fieldFor(arg) {
    const id = `arg-${arg.dest}`;
    const label = arg.positional ? arg.dest : arg.flags[arg.flags.length - 1];
    const hint = [arg.help, arg.required || (arg.positional && !arg.nargs) ? "required" : "",
      arg.action === "append" ? "comma-separated for several" : "",
      arg.nargs === "*" || arg.nargs === "+" ? "space-separated" : ""].filter(Boolean).join(" · ");
    let input;
    if (arg.kind === "flag") {
      input = el("input", { id, type: "checkbox", "data-dest": arg.dest });
      if (arg.default === true) input.checked = true;
      return el("label", { class: "field check" }, [input, el("span", { text: ` ${label}` }), el("small", { class: "muted", text: hint })]);
    }
    if (arg.choices && arg.choices.length) {
      input = el("select", { id, "data-dest": arg.dest }, [
        el("option", { value: "", text: arg.default != null ? `(default: ${arg.default})` : "(none)" }),
        ...arg.choices.map((choice) => el("option", { value: choice, text: choice })),
      ]);
    } else {
      input = el("input", {
        id,
        "data-dest": arg.dest,
        type: arg.kind === "int" || arg.kind === "float" ? "number" : "text",
        step: arg.kind === "float" ? "any" : undefined,
        placeholder: arg.default != null ? `default: ${fmtValue(arg.default)}` : (arg.metavar || ""),
      });
    }
    return el("label", { class: "field", for: id }, [el("span", { text: label }), input, el("small", { class: "muted", text: hint })]);
  }

  function selectCommand(command) {
    state.selected = command;
    renderCommandList();
    $("command-empty").hidden = true;
    $("command-form").hidden = false;
    $("command-title").textContent = `kdx ${command.path}`;
    $("command-help").textContent = command.help || "";
    const notice = $("command-notice");
    const allowed = can(command.role);
    notice.hidden = command.remote && !command.danger && allowed;
    notice.className = command.remote && allowed ? "notice warn" : "notice";
    notice.textContent = !command.remote
      ? "This command runs only in a terminal on the daemon's machine (an interactive wizard or a local-only action). Copy the line below into a terminal there."
      : !allowed
        ? `This command needs the ${command.role} role; you are ${state.me.role}. Ask an admin: kdx users role ${state.me.name} ${command.role}`
        : "This command changes files or state. Run asks for a second click to confirm.";
    $("command-run").disabled = !command.remote || !allowed;
    $("command-run").dataset.armed = "";
    $("command-run").textContent = "Run";
    $("command-run").dataset.label = "Run";
    $("command-fields").replaceChildren(...command.args.map(fieldFor));
    $("command-fields").querySelectorAll("input,select").forEach((node) => {
      node.addEventListener("input", updatePreview);
      node.addEventListener("change", updatePreview);
    });
    updatePreview();
  }

  function shellQuote(token) {
    return /^[\w./:=@,+-]+$/.test(token) ? token : `'${token.replace(/'/g, "'\\''")}'`;
  }

  // Build the argv a terminal user would type; the daemon parses it with the
  // command's own argparse declaration, so validation is identical to the CLI.
  function buildArgv() {
    const argv = [];
    const command = state.selected;
    command.args.forEach((arg) => {
      const node = document.getElementById(`arg-${arg.dest}`);
      if (!node) return;
      const flag = arg.flags[arg.flags.length - 1];
      if (arg.kind === "flag") {
        if (node.checked !== (arg.default === true)) argv.push(flag);
        return;
      }
      const raw = String(node.value || "").trim();
      if (!raw) return;
      const many = arg.nargs === "*" || arg.nargs === "+" ? raw.split(/\s+/) : [raw];
      if (arg.positional) { argv.push(...many); return; }
      if (arg.action === "append") {
        raw.split(",").map((part) => part.trim()).filter(Boolean).forEach((part) => argv.push(flag, part));
        return;
      }
      argv.push(flag, ...many);
    });
    return argv;
  }

  function updatePreview() {
    if (!state.selected) return;
    $("command-preview").textContent = ["kdx", ...state.selected.path.split(" "), ...buildArgv()].map(shellQuote).join(" ");
  }

  $("command-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const command = state.selected;
    if (!command || !command.remote || !can(command.role)) return;
    if (command.danger && !armed($("command-run"), "Click again to run")) return;
    guarded(runSelected);
  });

  // Every command runs as a background job: the reply is immediate, the output is
  // polled while it grows, and a long backup never holds an HTTP request open.
  async function runSelected() {
    const command = state.selected;
    const argv = buildArgv();
    const line = $("command-preview").textContent;
    const card = runCard(line);
    const { status, payload } = await api("/api/v1/rpc", {
      method: "POST",
      body: { command: command.path, args: { argv }, background: true },
      allowError: true,
    });
    if (!payload.ok) {
      finishCard(card, payload.error || `HTTP ${status}`, `refused · HTTP ${status}`, true);
      return;
    }
    followJob(payload.job.id, card);
  }

  function runCard(line) {
    const output = el("pre", { class: "terminal", text: "starting…" });
    const meta = el("div", { class: "run-meta muted", text: "running" });
    const cancel = el("button", { type: "button", class: "run-cancel", hidden: true, text: "Cancel" });
    const head = el("div", { class: "run-head" }, [el("div", { class: "run-line", text: line.startsWith("kdx") ? `$ ${line}` : line }), cancel]);
    const card = el("div", { class: "run card running" }, [head, output, meta]);
    card.output = output;
    card.meta = meta;
    card.cancel = cancel;
    $("runs").prepend(card);
    while ($("runs").children.length > 20) $("runs").lastChild.remove();
    return card;
  }

  function finishCard(card, text, meta, failed) {
    if (card.cancel) card.cancel.hidden = true;
    card.output.textContent = text;
    card.meta.textContent = meta;
    card.classList.remove("running");
    card.classList.toggle("failed", Boolean(failed));
  }

  async function followJob(id, card) {
    for (;;) {
      let job;
      try {
        ({ payload: job } = await api(`/api/v1/jobs?id=${encodeURIComponent(id)}`));
      } catch (error) {
        finishCard(card, card.output.textContent, `lost track of ${id}: ${error.message}`, true);
        return;
      }
      const box = card.output;
      const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 8;
      if (job.state === "running" || job.state === "cancelling") {
        box.textContent = job.output || "running…";
        card.meta.textContent = `${id} · ${job.state} ${job.seconds}s${job.user ? ` · by ${job.user}` : ""}`;
        const mine = !state.me || state.me.role === "admin" || state.me.name === job.user;
        card.cancel.hidden = !mine || job.state === "cancelling";
        if (!card.cancel.onclick) {
          card.cancel.onclick = () => {
            if (!armed(card.cancel, "Click again to cancel")) return;
            guarded(async () => {
              await api("/api/v1/jobs", { method: "POST", body: { cancel: id } });
              card.cancel.hidden = true;
              toast(`asked ${id} to stop`);
            });
          };
        }
        if (atBottom) box.scrollTop = box.scrollHeight;
        await new Promise((resolve) => setTimeout(resolve, JOB_POLL_MS));
        continue;
      }
      const text = job.error ? `${job.output || ""}${job.error}` : (job.output || "(no output)");
      finishCard(card, text, `${id} · ${job.state} · exit ${job.exit_code ?? "-"} · ${job.seconds}s`, job.state !== "done");
      return;
    }
  }

  // Jobs started earlier (from another tab, or before a reload) show up again.
  async function loadJobs() {
    const { payload } = await api("/api/v1/jobs");
    if (state.jobsLoaded) return;
    state.jobsLoaded = true;
    (payload.jobs || []).slice(0, 10).reverse().forEach((job) => {
      followJob(job.id, runCard(`$ kdx ${job.command}`));
    });
  }

  // ---- logs and config -----------------------------------------------------

  async function loadLogs() {
    const lines = $("log-lines").value;
    const { payload } = await api(`/api/v1/ui/logs?lines=${encodeURIComponent(lines)}`);
    const needle = $("log-filter").value.trim().toLowerCase();
    const shown = (payload.lines || []).filter((line) => !needle || line.toLowerCase().includes(needle));
    $("log-file").textContent = payload.file || payload.note || "";
    const box = $("log-output");
    const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 8;
    box.textContent = shown.join("\n") || "(empty)";
    if (atBottom) box.scrollTop = box.scrollHeight;
  }

  ["log-lines", "log-filter"].forEach((id) => $(id).addEventListener("input", () => guarded(loadLogs)));
  $("btn-logs").addEventListener("click", () => guarded(loadLogs));
  setInterval(() => {
    if ($("log-follow").checked && $("tab-logs").classList.contains("active")) guarded(loadLogs);
  }, FOLLOW_MS);

  async function loadConfig() {
    const { payload } = await api("/api/v1/ui/config");
    state.config = payload.entries || [];
    renderConfig();
  }

  function renderConfig() {
    const needle = $("config-filter").value.trim().toLowerCase();
    fill($("config-table").tBodies[0], state.config
      .filter((e) => !needle || e.key.toLowerCase().includes(needle))
      .map((e) => el("tr", {}, [
        el("td", { class: "mono", text: e.key }),
        el("td", { class: e.secret ? "mono muted" : "mono", text: fmtValue(e.value) }),
        el("td", { text: e.source }),
      ])));
  }

  $("config-filter").addEventListener("input", renderConfig);

  // ---- live events -------------------------------------------------------------

  // fetch() streaming rather than EventSource: EventSource cannot send the token header,
  // and a token in the URL would land in history and logs.
  async function streamEvents() {
    if (state.streaming || !state.token) return;
    state.streaming = true;
    $("events-state").textContent = "connected";
    renderEvents();
    try {
      const response = await fetch("/api/v1/ui/events", { headers: { [TOKEN_HEADER]: state.token }, cache: "no-store" });
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let pending = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        pending += decoder.decode(value, { stream: true });
        const frames = pending.split("\n\n");
        pending = frames.pop();
        frames.forEach((frame) => {
          const data = frame.split("\n").filter((line) => line.startsWith("data: ")).map((line) => line.slice(6)).join("\n");
          if (data) {
            try { onEvent(JSON.parse(data)); } catch (e) { /* a malformed frame is skipped */ }
          }
        });
      }
    } catch (error) {
      /* the daemon stopped or the network dropped; retry below */
    }
    state.streaming = false;
    $("events-state").textContent = "reconnecting…";
    setTimeout(streamEvents, 3000);
  }

  function onEvent(event) {
    state.events.push(event);
    if (state.events.length > MAX_EVENTS) state.events.shift();
    if (event.name === "core.config.reloaded" || event.name.startsWith("core.module.")) guarded(loadStatus);
    if (!$("events-pause").checked) renderEvents();
  }

  function renderEvents() {
    const routine = $("events-routine").checked;
    const needle = $("events-filter").value.trim().toLowerCase();
    const hidden = routine ? 0 : state.events.filter((e) => ROUTINE_EVENTS.test(e.name)).length;
    $("events-state").textContent = `connected · ${state.events.length} received${hidden ? ` · ${hidden} routine hidden` : ""}`;
    const rows = state.events
      .filter((e) => routine || !ROUTINE_EVENTS.test(e.name))
      .filter((e) => !needle || e.name.toLowerCase().includes(needle) || JSON.stringify(e.payload).toLowerCase().includes(needle))
      .slice(-100)
      .reverse()
      .map((e) => el("tr", {}, [
        el("td", { class: "mono", text: new Date(e.at * 1000).toLocaleTimeString() }),
        el("td", { class: "mono", text: e.name }),
        el("td", { text: e.source || "-" }),
        el("td", { class: "mono", text: fmtValue(e.payload) }),
      ]));
    if (!rows.length) {
      rows.push(el("tr", {}, [el("td", {
        colspan: "4",
        class: "muted",
        text: hidden
          ? "Nothing but routine ticks so far. Run a command or reload the config, or tick 'show routine'."
          : "Waiting for the first event…",
      })]));
    }
    fill($("events-table").tBodies[0], rows);
  }

  ["events-routine", "events-filter", "events-pause"].forEach((id) => $(id).addEventListener("input", renderEvents));

  // ---- audit -----------------------------------------------------------------

  async function loadAudit() {
    const { payload } = await api("/api/v1/ui/audit?lines=500");
    state.audit = payload.entries || [];
    renderAudit();
  }

  function renderAudit() {
    const needle = $("audit-filter").value.trim().toLowerCase();
    const rows = state.audit
      .filter((e) => !needle || JSON.stringify(e).toLowerCase().includes(needle))
      .slice()
      .reverse()
      .map((e) => el("tr", { class: String(e.outcome || "").startsWith("denied") || String(e.outcome || "").startsWith("unauth") ? "denied" : "" }, [
        el("td", { class: "mono", text: new Date((e.at || 0) * 1000).toLocaleString() }),
        el("td", { text: e.user || "-" }),
        el("td", { text: e.action || "-" }),
        el("td", { class: "mono", text: e.command || "-" }),
        el("td", { text: e.outcome || "-" }),
        el("td", { class: "mono", text: e.address || "-" }),
      ]));
    if (!rows.length) rows.push(el("tr", {}, [el("td", { colspan: "6", class: "muted", text: "No records yet." })]));
    fill($("audit-table").tBodies[0], rows);
  }

  $("audit-filter").addEventListener("input", renderAudit);
  $("btn-audit").addEventListener("click", () => guarded(loadAudit));

  // ---- top-bar actions -----------------------------------------------------

  $("btn-refresh").addEventListener("click", refreshAll);

  $("btn-reload").addEventListener("click", () => guarded(async () => {
    const { payload } = await api("/api/v1/reload", { method: "POST" });
    const changed = payload.changed || [];
    toast(changed.length ? `reloaded: ${changed.join(", ")}` : "reloaded: nothing changed");
    await loadStatus();
    if ($("tab-config").classList.contains("active")) await loadConfig();
  }));

  $("btn-stop").addEventListener("click", () => {
    if (!armed($("btn-stop"), "Click again to stop")) return;
    guarded(async () => {
      await api("/api/v1/shutdown", { method: "POST" });
      toast("daemon is stopping");
      setTimeout(setOffline, 1000);
    });
  });

  $("lang").addEventListener("change", () => {
    const code = $("lang").value;
    try { localStorage.setItem(LOCALE_KEY, code); } catch (e) { /* private mode */ }
    guarded(() => loadI18n(code));
  });

  function savedLocale() {
    try { return localStorage.getItem(LOCALE_KEY) || ""; } catch (e) { return ""; }
  }

  function refreshAll() {
    guarded(async () => {
      await loadI18n(savedLocale());
      await loadMe();
      await loadStatus();
      await loadCatalog();
      await loadJobs();
      streamEvents();
      if ($("tab-logs").classList.contains("active")) await loadLogs();
      if ($("tab-config").classList.contains("active")) await loadConfig();
    });
  }

  markStatic();
  state.token = readToken();
  if (!state.token) showGate("");
  refreshAll();
  setInterval(() => { if (state.token && $("token-gate").hidden) guarded(loadStatus); }, REFRESH_MS);
})();
