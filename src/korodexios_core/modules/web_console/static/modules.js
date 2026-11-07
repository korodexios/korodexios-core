// Module panels: the part of the console drawn from /api/v1/ui/manifest, so a module
// that declares a [ui] table gets a tab with no GUI code. The same file holds the
// choices a person makes there (rename, hide, reorder, own shortcut buttons, remembered
// form values, pinned defaults); they are stored on the daemon (/api/v1/ui/prefs, the
// same file `kdx ui prefs` edits) with a copy in this browser as a fallback.
// Every value from the daemon or the person is written with textContent, never as HTML.
"use strict";

(function () {
  const K = window.kdxConsole;
  if (!K) return;
  const { api, el, t, toast, guarded, armed, state } = K;
  const CACHE_KEY = "kdx-ui-prefs";

  const ui = { manifest: { modules: [], issues: [] }, prefs: blankPrefs(), customizing: false, timers: [], loaded: false, rendered: false };

  function blankPrefs() {
    return { version: 1, nav: {}, panels: {}, buttons: [], values: {}, defaults: {}, settings: {} };
  }

  // A text the person or the daemon chose: shown as-is, never translated again.
  function raw(tag, props, text, children) {
    const node = el(tag, props || {}, children || []);
    node.textContent = text == null ? "" : String(text);
    (children || []).forEach((child) => node.append(child));
    return node;
  }

  function cachePrefs() {
    try { localStorage.setItem(CACHE_KEY, JSON.stringify(ui.prefs)); } catch (e) { /* private mode */ }
  }

  function cachedPrefs() {
    try {
      const saved = JSON.parse(localStorage.getItem(CACHE_KEY) || "null");
      return saved && typeof saved === "object" ? Object.assign(blankPrefs(), saved) : blankPrefs();
    } catch (e) { return blankPrefs(); }
  }

  async function loadPrefs() {
    try {
      const { payload } = await api("/api/v1/ui/prefs");
      ui.prefs = Object.assign(blankPrefs(), payload);
      cachePrefs();
    } catch (error) {
      ui.prefs = cachedPrefs(); // the daemon is the source of truth; this browser's copy is the fallback
    }
  }

  // Send a partial change to the daemon; keep the answer (the daemon validates it).
  async function save(changes) {
    try {
      const { payload } = await api("/api/v1/ui/prefs", { method: "PATCH", body: changes });
      ui.prefs = Object.assign(blankPrefs(), payload);
      cachePrefs();
    } catch (error) {
      toast(error.message, true);
      await loadPrefs();
    }
  }

  async function load() {
    const locale = K.locale();
    const [{ payload }] = await Promise.all([api(`/api/v1/ui/manifest?locale=${encodeURIComponent(locale)}`), loadPrefs()]);
    ui.manifest = payload;
    ui.loaded = true;
    render();
  }

  // ---- layout overlays ---------------------------------------------------------

  const overlay = (section, key) => ui.prefs[section][key] || {};

  function shownModules() {
    return ui.manifest.modules
      .map((entry) => {
        const mine = overlay("nav", entry.module);
        return Object.assign({}, entry, {
          title: mine.label || entry.title,
          order: mine.order !== undefined ? mine.order : entry.order,
          hidden: Boolean(mine.hidden),
        });
      })
      .filter((entry) => ui.customizing || !entry.hidden)
      .sort((a, b) => a.order - b.order || a.module.localeCompare(b.module));
  }

  function shownPanels(entry) {
    return entry.panels
      .map((panel, index) => {
        const mine = overlay("panels", `${entry.module}.${panel.id}`);
        return Object.assign({}, panel, {
          title: mine.label || panel.title,
          order: mine.order !== undefined ? mine.order : index * 10,
          hidden: Boolean(mine.hidden),
        });
      })
      .filter((panel) => ui.customizing || !panel.hidden)
      .sort((a, b) => a.order - b.order);
  }

  // Number the whole list in steps of 10, swap two neighbours, return the patch.
  function reorderPatch(items, keyOf, index, delta) {
    const target = index + delta;
    if (target < 0 || target >= items.length) return null;
    const keys = items.map(keyOf);
    [keys[index], keys[target]] = [keys[target], keys[index]];
    const patch = {};
    keys.forEach((key, position) => { patch[key] = { order: position * 10 }; });
    return patch;
  }

  // ---- values ------------------------------------------------------------------

  function dig(value, path) {
    if (!path) return value;
    return path.split(".").reduce((node, key) => (node == null ? undefined : node[key]), value);
  }

  function commandEntry(path) {
    return state.catalog.find((command) => command.path === path);
  }

  // Turn { dest: value } into the argv a terminal user would type for this command.
  function argvFrom(command, values) {
    const argv = [];
    command.args.forEach((arg) => {
      const value = values[arg.dest];
      if (value === undefined || value === null || value === "") return;
      const flag = arg.flags[arg.flags.length - 1];
      if (arg.kind === "flag") {
        const on = value === true || value === "true";
        if (on !== (arg.default === true)) argv.push(flag);
        return;
      }
      const many = Array.isArray(value) ? value.map(String)
        : (arg.nargs === "*" || arg.nargs === "+") ? String(value).split(/\s+/) : [String(value)];
      if (arg.positional) { argv.push(...many); return; }
      if (arg.action === "append") { many.forEach((part) => argv.push(flag, part)); return; }
      argv.push(flag, ...many);
    });
    return argv;
  }

  function runCommand(host, path, values) {
    const command = commandEntry(path);
    if (!command || !command.remote || !K.can(command.role)) {
      toast(command ? "this command is not available to you here" : "unknown command", true);
      return;
    }
    guarded(() => K.runIn(host, path, argvFrom(command, values)));
  }

  // ---- panels ------------------------------------------------------------------

  function panelCard(entry, panel, index, panels) {
    const key = `${entry.module}.${panel.id}`;
    const head = el("div", { class: "panel-head" });
    head.append(raw("h2", {}, panel.title));
    if (ui.customizing) {
      const rename = el("input", { type: "text", class: "inline", "aria-label": "Rename", maxlength: "100" });
      rename.value = panel.title;
      rename.addEventListener("change", () => guarded(async () => {
        await save({ panels: { [key]: { label: rename.value.trim() || null } } });
        render();
      }));
      const move = (delta) => guarded(async () => {
        const patch = reorderPatch(panels, (p) => `${entry.module}.${p.id}`, index, delta);
        if (patch) { await save({ panels: patch }); render(); }
      });
      head.append(
        rename,
        el("button", { type: "button", text: "Up", onclick: () => move(-1) }),
        el("button", { type: "button", text: "Down", onclick: () => move(1) }),
        el("button", {
          type: "button",
          text: panel.hidden ? "Show" : "Hide",
          onclick: () => guarded(async () => {
            await save({ panels: { [key]: { hidden: !panel.hidden } } });
            render();
          }),
        }),
      );
    }
    const body = el("div", { class: "panel-body" });
    const card = el("section", { class: `card panel${panel.hidden ? " ghost" : ""}`, "data-panel": key }, [head]);
    if (panel.help) card.append(raw("p", { class: "muted" }, panel.help));
    card.append(body);
    card.refresh = () => PANELS[panel.type](entry, panel, body, card);
    return card;
  }

  async function fetchSource(panel) {
    const { payload } = await api(panel.source);
    return dig(payload, panel.path);
  }

  function showError(body, error) {
    body.replaceChildren(raw("p", { class: "error" }, error.message || String(error)));
  }

  const PANELS = {
    async table(entry, panel, body) {
      try {
        const rows = await fetchSource(panel);
        const list = Array.isArray(rows) ? rows : (rows && typeof rows === "object" ? Object.entries(rows).map(([name, value]) => ({ name, value })) : []);
        const columns = panel.columns.length ? panel.columns
          : (list[0] && typeof list[0] === "object" ? Object.keys(list[0]).map((key) => ({ key, label: key })) : [{ key: "", label: "value" }]);
        const cell = (row, key) => {
          const value = key ? dig(row, key) : row;
          return value == null ? "-" : (typeof value === "object" ? JSON.stringify(value) : String(value));
        };
        const table = el("table", {}, [
          el("thead", {}, [el("tr", {}, columns.map((c) => raw("th", {}, c.label)))]),
          el("tbody", {}, list.length
            ? list.map((row) => el("tr", {}, columns.map((c) => raw("td", {}, cell(row, c.key)))))
            : [el("tr", {}, [el("td", { colspan: String(columns.length), class: "muted", text: "Nothing to show." })])]),
        ]);
        body.replaceChildren(el("div", { class: "table-wrap" }, [table]));
      } catch (error) { showError(body, error); }
    },

    async keyvalue(entry, panel, body) {
      try {
        const value = await fetchSource(panel);
        const pairs = value && typeof value === "object" ? Object.entries(value) : [["value", value]];
        body.replaceChildren(el("table", {}, [el("tbody", {}, pairs.map(([name, item]) => el("tr", {}, [
          raw("th", {}, name),
          raw("td", {}, item == null ? "-" : (typeof item === "object" ? JSON.stringify(item) : String(item))),
        ])))]));
      } catch (error) { showError(body, error); }
    },

    async log(entry, panel, body) {
      try {
        const value = await fetchSource(panel);
        const text = Array.isArray(value) ? value.join("\n") : (value == null ? "" : String(value));
        body.replaceChildren(raw("pre", { class: "terminal" }, text));
      } catch (error) { showError(body, error); }
    },

    text(entry, panel, body) {
      body.replaceChildren(raw("p", {}, panel.text));
    },

    form(entry, panel, body, card) {
      const command = commandEntry(panel.command);
      if (!command) { body.replaceChildren(raw("p", { class: "error" }, `unknown command: ${panel.command}`)); return; }
      const formKey = `${entry.module}.${panel.id}`;
      const fresh = () => Object.assign({}, panel.args, ui.prefs.defaults[formKey] || {});
      const remembered = () => Object.assign(fresh(), ui.prefs.values[formKey] || {});
      const inputs = new Map();
      const fields = command.args.map((arg) => {
        const label = arg.positional ? arg.dest : arg.flags[arg.flags.length - 1];
        let input;
        if (arg.kind === "flag") {
          input = el("input", { type: "checkbox" });
        } else if (arg.choices && arg.choices.length) {
          input = el("select", {}, [el("option", { value: "", text: "(default)" }), ...arg.choices.map((c) => raw("option", { value: c }, c))]);
        } else {
          input = el("input", { type: arg.kind === "int" || arg.kind === "float" ? "number" : "text", step: arg.kind === "float" ? "any" : undefined, placeholder: arg.default != null ? `default: ${arg.default}` : (arg.metavar || "") });
        }
        inputs.set(arg.dest, { input, arg });
        const hint = raw("small", { class: "muted" }, arg.help || "");
        return arg.kind === "flag"
          ? el("label", { class: "field check" }, [input, raw("span", {}, ` ${label}`), hint])
          : el("label", { class: "field" }, [raw("span", {}, label), input, hint]);
      });
      const apply = (values) => inputs.forEach(({ input, arg }, dest) => {
        const value = values[dest];
        if (arg.kind === "flag") input.checked = value === true || value === "true" || (value === undefined && arg.default === true);
        else input.value = value === undefined || value === null ? "" : String(value);
      });
      const collect = () => {
        const out = {};
        inputs.forEach(({ input, arg }, dest) => {
          if (arg.kind === "flag") out[dest] = input.checked;
          else if (input.value.trim() !== "") out[dest] = arg.kind === "int" ? Number(input.value) : (arg.kind === "float" ? Number(input.value) : input.value.trim());
        });
        return out;
      };
      apply(remembered());
      const runs = el("div", { class: "runs" });
      const form = el("form", {}, [
        ...fields,
        el("div", { class: "row" }, [
          el("button", { type: "submit", class: "primary", text: "Run" }),
          el("button", { type: "button", text: "Save as default", onclick: () => guarded(async () => {
            const current = collect();
            const wipe = {};
            Object.keys(ui.prefs.defaults[formKey] || {}).forEach((name) => { if (!(name in current)) wipe[name] = null; });
            await save({ defaults: { [formKey]: Object.assign(wipe, current) } });
            toast("Default saved");
          }) }),
          el("button", { type: "button", text: "Restore defaults", onclick: () => apply(fresh()) }),
        ]),
      ]);
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        const values = collect();
        const wipe = {};
        Object.keys(ui.prefs.values[formKey] || {}).forEach((name) => { if (!(name in values)) wipe[name] = null; });
        guarded(() => save({ values: { [formKey]: Object.assign(wipe, values) } }));
        runCommand(runs, panel.command, values);
      });
      body.replaceChildren(form, runs);
      card.refresh = null; // a form is drawn once; polling would erase what is being typed
    },

    actions(entry, panel, body) {
      const runs = el("div", { class: "runs" });
      const row = el("div", { class: "row" }, panel.buttons.map((button) => {
        const node = raw("button", { type: "button", class: button.danger ? "danger" : "" }, button.label);
        node.addEventListener("click", () => {
          if ((button.confirm || button.danger) && !armed(node, t("Click again to run"))) return;
          runCommand(runs, button.command, button.args);
        });
        return node;
      }));
      body.replaceChildren(row, runs);
    },
  };

  // ---- tabs --------------------------------------------------------------------

  function clearTimers() {
    ui.timers.forEach((timer) => clearInterval(timer));
    ui.timers = [];
  }

  function moduleSection(entry, index, entries) {
    const section = el("section", { id: `tab-mod-${entry.module}`, class: "tab", "data-dyn": "1" });
    if (ui.customizing) {
      const rename = el("input", { type: "text", class: "inline", "aria-label": "Rename", maxlength: "100" });
      rename.value = entry.title;
      rename.addEventListener("change", () => guarded(async () => {
        await save({ nav: { [entry.module]: { label: rename.value.trim() || null } } });
        render();
      }));
      const move = (delta) => guarded(async () => {
        const patch = reorderPatch(entries, (e) => e.module, index, delta);
        if (patch) { await save({ nav: patch }); render(); }
      });
      section.append(el("div", { class: "row toolbar" }, [
        rename,
        el("button", { type: "button", text: "Up", onclick: () => move(-1) }),
        el("button", { type: "button", text: "Down", onclick: () => move(1) }),
        el("button", { type: "button", text: entry.hidden ? "Show" : "Hide", onclick: () => guarded(async () => {
          await save({ nav: { [entry.module]: { hidden: !entry.hidden } } });
          render();
        }) }),
      ]));
    }
    const panels = shownPanels(entry);
    panels.forEach((panel, i) => {
      const card = panelCard(entry, panel, i, panels);
      section.append(card);
      guarded(async () => { if (card.refresh) await card.refresh(); });
      if (panel.refresh > 0 && panel.type !== "form") {
        ui.timers.push(setInterval(() => {
          if (section.classList.contains("active") && card.refresh) guarded(async () => { await card.refresh(); });
        }, panel.refresh * 1000));
      }
    });
    return section;
  }

  function render() {
    clearTimers();
    const nav = document.querySelector(".tabs");
    const main = document.querySelector("main");
    const active = document.querySelector(".tabs button.active");
    const wanted = active ? active.dataset.tab : "";
    nav.querySelectorAll("[data-dyn]").forEach((node) => node.remove());
    main.querySelectorAll("section[data-dyn]").forEach((node) => node.remove());
    const anchor = document.getElementById("tab-button-shortcuts");
    const entries = shownModules();
    entries.forEach((entry, index) => {
      const button = raw("button", { type: "button", role: "tab", "data-tab": `mod-${entry.module}`, "data-dyn": "1", class: entry.hidden ? "ghost" : "" }, entry.title);
      button.addEventListener("click", () => K.activateTab(`mod-${entry.module}`));
      nav.insertBefore(button, anchor);
      main.append(moduleSection(entry, index, entries));
    });
    renderShortcuts();
    document.getElementById("btn-customize").classList.toggle("active", ui.customizing);
    const remembered = ui.prefs.settings.last_tab || "";
    const first = !ui.rendered;
    ui.rendered = true;
    const exists = (name) => Boolean(name) && Boolean(document.getElementById(`tab-${name}`));
    if (first && exists(remembered)) K.activateTab(remembered);
    else if (exists(wanted)) K.activateTab(wanted);
    else K.activateTab("overview");
  }

  // ---- shortcuts: the person's own buttons and pinned defaults ----------------

  function parseArgs(text) {
    const out = {};
    String(text || "").split(",").map((part) => part.trim()).filter(Boolean).forEach((part) => {
      const at = part.indexOf("=");
      if (at < 1) return;
      const name = part.slice(0, at).trim();
      const value = part.slice(at + 1).trim();
      out[name] = value === "true" ? true : value === "false" ? false : value;
    });
    return out;
  }

  function renderShortcuts() {
    const list = document.getElementById("shortcut-list");
    const runs = document.getElementById("shortcut-runs");
    list.replaceChildren(...(ui.prefs.buttons.length ? ui.prefs.buttons.map((button) => {
      const run = raw("button", { type: "button", class: button.danger ? "danger" : "" }, button.label);
      run.addEventListener("click", () => {
        if (button.confirm && !armed(run, t("Click again to run"))) return;
        runCommand(runs, button.command, button.args || {});
      });
      const row = el("div", { class: "row" }, [run, raw("code", {}, `kdx ${button.command}`)]);
      if (ui.customizing) {
        row.append(el("button", { type: "button", text: "Remove", onclick: () => guarded(async () => {
          await save({ buttons: ui.prefs.buttons.filter((b) => b.id !== button.id) });
          renderShortcuts();
        }) }));
      }
      return row;
    }) : [el("p", { class: "muted", text: "No shortcuts yet. Use Customize, then add one below." })]));

    const select = document.getElementById("shortcut-command");
    const chosen = select.value;
    select.replaceChildren(...state.catalog.filter((c) => c.remote).map((c) => raw("option", { value: c.path }, c.path)));
    if (chosen) select.value = chosen;
    document.getElementById("shortcut-editor").hidden = !ui.customizing;

    const pinned = Object.entries(ui.prefs.defaults);
    document.getElementById("default-list").replaceChildren(...(pinned.length ? pinned.map(([key, values]) => el("div", { class: "row" }, [
      raw("code", {}, key),
      raw("span", { class: "muted" }, JSON.stringify(values)),
      el("button", { type: "button", text: "Clear default", onclick: () => guarded(async () => {
        await save({ defaults: { [key]: null } });
        renderShortcuts();
      }) }),
    ])) : [el("p", { class: "muted", text: "No pinned defaults." })]));
  }

  document.getElementById("shortcut-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const label = document.getElementById("shortcut-label").value.trim();
    const command = document.getElementById("shortcut-command").value;
    if (!label || !command) return;
    let id = label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
    if (!/^[a-z]/.test(id)) id = `b-${id || "x"}`;
    const taken = new Set(ui.prefs.buttons.map((b) => b.id));
    let unique = id; let n = 2;
    while (taken.has(unique)) unique = `${id}-${n++}`;
    guarded(async () => {
      await save({ buttons: [...ui.prefs.buttons, {
        id: unique, label, command,
        args: parseArgs(document.getElementById("shortcut-args").value),
        confirm: document.getElementById("shortcut-confirm").checked,
      }] });
      document.getElementById("shortcut-form").reset();
      renderShortcuts();
    });
  });

  document.getElementById("btn-customize").addEventListener("click", () => {
    ui.customizing = !ui.customizing;
    render();
  });

  document.getElementById("btn-reset-layout").addEventListener("click", () => {
    if (!armed(document.getElementById("btn-reset-layout"), t("Click again to reset"))) return;
    guarded(async () => {
      const { payload } = await api("/api/v1/ui/prefs", { method: "DELETE" });
      ui.prefs = Object.assign(blankPrefs(), payload);
      cachePrefs();
      toast("Layout reset");
      render();
    });
  });

  // Remember which tab the person was on, on the daemon too.
  document.addEventListener("kdx-tab", (event) => {
    if (!ui.loaded || ui.prefs.settings.last_tab === event.detail) return;
    ui.prefs.settings = Object.assign({}, ui.prefs.settings, { last_tab: event.detail });
    cachePrefs();
    api("/api/v1/ui/prefs", { method: "PATCH", body: { settings: { last_tab: event.detail } } }).catch(() => {});
  });

  window.kdxModules = { load, state: ui };
  if (state.catalog.length) guarded(load);
})();
