// Korodexios shell: one window (ribbon, side list, tabs, bottom panel, inspector) drawn from
// the control plane. It reads /api/v1/ui/manifest (areas, sections, panels), /api/v1/ui/theme
// and /api/v1/ui/prefs (layout); everything it can do, `kdx ui layout|theme|prefs` can do too.
// Every value from the daemon is written with textContent, never as HTML.
"use strict";

(function () {
  const TOKEN_HEADER = "X-Kdx-Token";
  const STORE_KEY = "kdx-console-token"; // shared with the classic console (same origin)
  const SAVE_DELAY_MS = 400;
  const BADGE_MS = 15000;
  const STEP = 12;
  const PANES = ["ribbon", "side", "bottom", "inspector"];
  const LIMITS = { side: [120, 600, 280], bottom: [60, 800, 150], inspector: [160, 700, 280] };
  const VARS = { side: "--side-w", bottom: "--bottom-h", inspector: "--inspector-w" };
  const SVG_NS = "http://www.w3.org/2000/svg";
  const ICONS = {
    // A general vocabulary a module names in its [ui] `icon`; an unknown name draws `module`.
    home: "M4 11l8-7 8 7|M6 10v10h12V10",
    list: "M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01",
    document: "M6 3h9l4 4v14H6z|M14 3v5h5M9 13h7M9 17h5",
    folder: "M3 6h6l2 2h10v11H3z",
    search: "M17 11a6 6 0 1 1-12 0 6 6 0 0 1 12 0z|M20 20l-4-4",
    task: "M5 4h14v12H9l-4 4z|M9 9l2 2 4-4",
    shield: "M12 3l7 3v5c0 5-3 8-7 10-4-2-7-5-7-10V6z",
    chip: "M7 5h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z|M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3",
    pulse: "M3 12h4l3-7 4 14 3-7h4",
    chart: "M4 20V10M10 20V4M16 20v-8M22 20H2",
    database: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z|M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6",
    bell: "M6 16v-5a6 6 0 0 1 12 0v5l2 2H4z|M10 20a2 2 0 0 0 4 0",
    user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z|M4 21a8 8 0 0 1 16 0",
    play: "M7 4l13 8-13 8z",
    system: "M12 3l8 4.5v9L12 21l-8-4.5v-9z|M14.5 12a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z",
    settings: "M4 7h10M18 7h2M4 17h2M10 17h10|M18 7a2 2 0 1 1-4 0 2 2 0 0 1 4 0z|M10 17a2 2 0 1 1-4 0 2 2 0 0 1 4 0z",
    module: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
    logo: "M12 3l8 4.5v9L12 21l-8-4.5v-9z|M12 12l8-4.5M12 12v9M12 12L4 7.5",
    "pane-ribbon": "M4 5h16v14H4z|M8 5v14",
    "pane-side": "M4 5h16v14H4z|M10 5v14M4 10h6M4 14h6",
    "pane-bottom": "M4 5h16v14H4z|M4 14h16",
    "pane-inspector": "M4 5h16v14H4z|M14 5v14",
    "pane-split": "M4 5h16v14H4z|M12 5v14M4 12h8",
    focus: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
    chevron: "M9 6l6 6-6 6",
    collapse: "M7 9l5-5 5 5M7 15l5 5 5-5",
    ribbon: "M9 6l6 6-6 6",
  };

  const state = {
    token: "", manifest: null, prefs: null, themes: null, status: null,
    catalog: [], me: null,
    area: "", tabs: [], active: "", activeB: "", focus: "a", selected: null,
    trees: {}, open: new Set(), badges: {}, timers: [], timersB: [], dockTimers: [],
  };
  const $ = (id) => document.getElementById(id);

  // ---- language ----------------------------------------------------------------
  // The shell's own texts live in the daemon's catalogues (domain korodexios-web), the same
  // ones the classic console uses. Static elements keep their English in data-en so a switch
  // always translates from the source; text from the daemon (labels, rows) is never looked up.

  const TRANSLATED_ATTRS = ["title", "aria-label", "placeholder"];
  const LANGUAGE_NAMES = { en: "English", sk: "Slovenčina", cs: "Čeština", de: "Deutsch", es: "Español", fr: "Français", pl: "Polski" };
  const KEEP_AS_IS = new Set(["state-pill", "toast", "app-name", "crumb-area", "crumb-item", "status-theme", "status-layout", "status-user"]);
  const i18n = { locale: "en", messages: {} };

  function t(text) {
    const key = String(text);
    return Object.prototype.hasOwnProperty.call(i18n.messages, key) ? i18n.messages[key] : key;
  }

  // a sentence with values in it: the sentence is translated, the values are filled in after
  function tf(text, fields) {
    return t(text).replace(/\{(\w+)\}/g, (match, name) => (name in fields ? String(fields[name]) : match));
  }

  const squash = (text) => text.replace(/\s+/g, " ").trim();

  function markStatic() {
    document.querySelectorAll("body *").forEach((node) => {
      if (KEEP_AS_IS.has(node.id) || ["SCRIPT", "STYLE", "OPTION", "CODE", "PRE"].includes(node.tagName)) return;
      if (node.children.length === 0 && node.childNodes.length === 1 && node.firstChild.nodeType === 3) {
        const text = squash(node.textContent);
        if (text) node.dataset.en = text;
      }
      TRANSLATED_ATTRS.forEach((attr) => { if (node.hasAttribute(attr)) node.setAttribute(`data-en-${attr}`, node.getAttribute(attr)); });
    });
  }

  function applyStatic() {
    document.documentElement.lang = i18n.locale;
    document.querySelectorAll("[data-en]").forEach((node) => { node.textContent = t(node.dataset.en); });
    TRANSLATED_ATTRS.forEach((attr) => {
      document.querySelectorAll(`[data-en-${attr}]`).forEach((node) => node.setAttribute(attr, t(node.getAttribute(`data-en-${attr}`))));
    });
  }

  async function loadI18n(requested) {
    const query = requested ? `?locale=${encodeURIComponent(requested)}` : "";
    const payload = await api(`/api/v1/ui/i18n${query}`);
    i18n.locale = payload.locale || "en";
    i18n.messages = payload.messages || {};
    const select = $("lang");
    select.replaceChildren(...(payload.locales || ["en"]).map((code) => el("option", { value: code, plain: LANGUAGE_NAMES[code] || code })));
    select.value = i18n.locale;
    applyStatic();
  }

  // ---- helpers ---------------------------------------------------------------

  function el(tag, props, children) {
    const node = document.createElement(tag);
    Object.entries(props || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === false) return;
      if (key === "text") node.textContent = t(value);
      else if (key === "plain") node.textContent = String(value);
      else if (TRANSLATED_ATTRS.includes(key)) node.setAttribute(key, t(value));
      else if (key === "class") node.className = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    });
    (children || []).forEach((child) => node.append(child));
    return node;
  }

  function icon(name, size) {
    const svg = document.createElementNS(SVG_NS, "svg");
    const px = String(size || 20);
    [["width", px], ["height", px], ["viewBox", "0 0 24 24"], ["fill", "none"], ["stroke", "currentColor"],
      ["stroke-width", "1.5"], ["stroke-linecap", "round"], ["stroke-linejoin", "round"], ["aria-hidden", "true"]]
      .forEach(([key, value]) => svg.setAttribute(key, value));
    (ICONS[name] || ICONS.module).split("|").forEach((d) => {
      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("d", d);
      svg.append(path);
    });
    return svg;
  }

  function toast(message, isError) {
    const box = $("toast");
    box.textContent = message;
    box.className = isError ? "toast error" : "toast";
    box.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { box.hidden = true; }, 4000);
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
    const { ok, status, payload } = await call(path, options);
    if (!ok) throw new Error(payload.error || `HTTP ${status}`);
    return payload;
  }

  // like api(), but a refusal (HTTP 4xx) is returned, not thrown: a command's own error is output
  async function call(path, options) {
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
    return { ok: response.ok, status: response.status, payload };
  }

  function dig(value, path) {
    if (!path) return value;
    return String(path).split(".").reduce((node, key) => (node === null || node === undefined ? undefined : node[key]), value);
  }

  function fmt(value) {
    if (value === null || value === undefined) return "-";
    return typeof value === "object" ? JSON.stringify(value) : String(value);
  }

  // {node} in a panel source or argument is the id of the tree node chosen in the side list
  // (empty until one is); in a URL it is encoded so no id can add a parameter or a path step
  const nodeId = () => (state.selected && state.selected.node ? String(state.selected.node.id) : "");
  const withNode = (text, urlSafe = true) => String(text).replace(/\{node\}/g, () => (urlSafe ? encodeURIComponent(nodeId()) : nodeId()));
  const usesNode = (panel) => [panel.source, panel.detail && panel.detail.source].some((s) => typeof s === "string" && s.includes("{node}"))
    || Object.values(panel.args || {}).some((v) => typeof v === "string" && v.includes("{node}"))
    || (panel.buttons || []).some((b) => Object.values(b.args || {}).some((v) => typeof v === "string" && v.includes("{node}")));
  const nodeArgs = (args) => {
    const out = {};
    Object.entries(args || {}).forEach(([name, value]) => { out[name] = typeof value === "string" ? withNode(value, false) : value; });
    return out;
  };

  // one long text is a wrapped block, a list or object folds away, a short value stays a line
  const LONG_TEXT = 200;
  function valueNode(value, kind) {
    if (value !== null && typeof value === "object") {
      const size = Array.isArray(value) ? value.length : Object.keys(value).length;
      const label = Array.isArray(value) ? tf("{count} items", { count: size }) : tf("{count} keys", { count: size });
      return el("details", { class: "fold" }, [el("summary", { plain: label }), el("pre", { class: "block", plain: JSON.stringify(value, null, 2) })]);
    }
    const text = fmt(value);
    const long = typeof value === "string" && (text.length > LONG_TEXT || text.includes("\n"));
    if (kind === "text" || (long && kind !== "line")) return el("pre", { class: "block", plain: text });
    return document.createTextNode(text);
  }

  function pairList(pairs, kinds) {
    const list = el("dl", { class: "kv" });
    pairs.forEach(([key, value]) => list.append(el("dt", { plain: key }), el("dd", {}, [valueNode(value, (kinds || {})[key])])));
    return list;
  }

  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const narrow = () => window.matchMedia("(max-width: 699px)").matches;

  // ---- layout (sizes, hidden panes, ribbon mode) kept on the daemon ----------------

  function layout() { return (state.prefs && state.prefs.layout) || {}; }
  function hidden() { return layout().hidden || []; }
  function sizeOf(key) { return (layout().sizes || {})[key] || LIMITS[key][2]; }

  let pending = null;
  function saveLayout(change) {
    const merged = Object.assign({}, state.prefs.layout || {});
    Object.entries(change).forEach(([key, value]) => {
      if (key === "sizes") {
        const sizes = Object.assign({}, merged.sizes || {});
        Object.entries(value).forEach(([pane, px]) => { if (px === null) delete sizes[pane]; else sizes[pane] = px; });
        merged.sizes = sizes;
      } else merged[key] = value;
    });
    state.prefs.layout = merged;
    pending = pending || {};
    Object.entries(change).forEach(([key, value]) => {
      pending[key] = key === "sizes" ? Object.assign({}, pending.sizes || {}, value) : value;
    });
    clearTimeout(saveLayout.timer);
    saveLayout.timer = setTimeout(async () => {
      const body = pending; pending = null;
      try { await api("/api/v1/ui/prefs", { method: "PATCH", body: { layout: body } }); $("status-layout").textContent = t("layout saved"); }
      catch (error) { toast(tf("layout not saved: {reason}", { reason: error.message }), true); }
    }, SAVE_DELAY_MS);
  }

  function applyLayout() {
    const root = document.documentElement;
    Object.keys(LIMITS).forEach((key) => root.style.setProperty(VARS[key], `${sizeOf(key)}px`));
    const off = hidden();
    $("ribbon").hidden = off.includes("ribbon");
    $("side").hidden = $("split-side").hidden = off.includes("side");
    $("bottom").hidden = $("split-bottom").hidden = off.includes("bottom");
    $("inspector").hidden = $("split-inspector").hidden = off.includes("inspector");
    $("ribbon").classList.toggle("labels", layout().ribbon === "labels");
    const mode = splitMode();
    $("surfaces").className = `surfaces ${mode === "off" ? "" : mode}`;
    $("surface-b").hidden = mode === "off";
    const split = document.querySelector('[data-pane="split"]');
    if (split) split.setAttribute("aria-pressed", String(mode !== "off"));
    PANES.forEach((pane) => {
      const button = document.querySelector(`[data-pane="${pane}"]`);
      if (button) button.setAttribute("aria-pressed", String(!off.includes(pane)));
    });
    const focus = document.querySelector('[data-pane="focus"]');
    if (focus) focus.setAttribute("aria-pressed", String(PANES.every((pane) => off.includes(pane))));
  }

  function setHidden(list) {
    saveLayout({ hidden: PANES.filter((pane) => list.includes(pane)) });
    applyLayout();
  }

  function togglePane(pane) {
    if (pane === "side" && narrow()) { $("app").classList.toggle("hide-side"); return; }
    if (pane === "inspector" && window.matchMedia("(max-width: 1099px)").matches) { $("app").classList.toggle("show-inspector"); return; }
    if (pane === "split") {
      const order = ["off", "vertical", "horizontal"];
      setSplit(order[(order.indexOf(layout().split || "off") + 1) % order.length]);
      return;
    }
    const off = new Set(hidden());
    if (pane === "focus") setHidden(PANES.every((p) => off.has(p)) ? [] : PANES);
    else { if (off.has(pane)) off.delete(pane); else off.add(pane); setHidden([...off]); }
  }

  function buildToggles() {
    const box = $("pane-toggles");
    box.replaceChildren();
    [["ribbon", "Ribbon"], ["side", "Side list"], ["bottom", "Bottom panel"], ["inspector", "Inspector"], ["split", "Split view (Ctrl+\\)"], ["focus", "Focus: hide or show all panels"]]
      .forEach(([pane, label]) => {
        const button = el("button", { type: "button", class: "toggle", "data-pane": pane, title: label, "aria-label": label, "aria-pressed": "true", onclick: () => togglePane(pane) });
        button.append(icon(pane === "focus" ? "focus" : `pane-${pane}`, 16));
        box.append(button);
      });
  }

  // ---- splitters: pointer, touch and arrow keys ---------------------------------

  function setSize(key, px, persist) {
    const [low, high] = LIMITS[key];
    const value = clamp(Math.round(px), low, high);
    document.documentElement.style.setProperty(VARS[key], `${value}px`);
    if (persist) saveLayout({ sizes: { [key]: value } });
    return value;
  }

  function splitter(button, key, axis, sign) {
    button.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      button.setPointerCapture(event.pointerId);
      button.classList.add("dragging");
      const start = axis === "x" ? event.clientX : event.clientY;
      const base = sizeOf(key);
      let last = base;
      const move = (ev) => { last = setSize(key, base + sign * ((axis === "x" ? ev.clientX : ev.clientY) - start), false); };
      const stop = () => {
        button.classList.remove("dragging");
        button.removeEventListener("pointermove", move);
        button.removeEventListener("pointerup", stop);
        button.removeEventListener("pointercancel", stop);
        saveLayout({ sizes: { [key]: last } });
      };
      button.addEventListener("pointermove", move);
      button.addEventListener("pointerup", stop);
      button.addEventListener("pointercancel", stop);
    });
    button.addEventListener("keydown", (event) => {
      const grow = axis === "x" ? (sign > 0 ? "ArrowRight" : "ArrowLeft") : "ArrowUp";
      const shrink = axis === "x" ? (sign > 0 ? "ArrowLeft" : "ArrowRight") : "ArrowDown";
      if (event.key !== grow && event.key !== shrink) return;
      event.preventDefault();
      setSize(key, sizeOf(key) + (event.key === grow ? STEP : -STEP), true);
    });
    button.addEventListener("dblclick", () => {
      setSize(key, LIMITS[key][2], false);
      saveLayout({ sizes: { [key]: null } });
    });
  }

  // ---- theme ------------------------------------------------------------------

  function applyTokens(tokens) {
    Object.entries(tokens || {}).forEach(([name, value]) => document.documentElement.style.setProperty(`--kdx-${name}`, value));
  }

  async function loadTheme() {
    let payload = await api("/api/v1/ui/theme");
    state.themes = payload;
    if (payload.system) {
      const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      payload = await api(`/api/v1/ui/theme?name=${encodeURIComponent(dark ? payload.system.dark : payload.system.light)}`);
    }
    applyTokens(payload.tokens);
    $("status-theme").textContent = tf("theme: {name}", { name: state.themes.active });
    const select = $("theme-select");
    select.replaceChildren(...state.themes.themes.map((theme) => el("option", { value: theme.name, plain: theme.name })));
    select.value = state.themes.active;
    (payload.problems || []).forEach((problem) => toast(problem, true));
  }

  async function chooseTheme(name) {
    await api("/api/v1/ui/prefs", { method: "PATCH", body: { settings: { theme: name } } });
    state.prefs.settings = Object.assign({}, state.prefs.settings, { theme: name });
    await loadTheme();
  }

  // ---- areas and the ribbon -------------------------------------------------------

  function areas() {
    const fromModules = (state.manifest.modules || []).map((entry) => ({
      id: entry.module, title: entry.title || entry.module, icon: entry.icon, position: entry.position || "top", entry,
    }));
    return [...fromModules,
      { id: "system", title: t("System"), icon: "system", position: "bottom", builtin: true },
      { id: "settings", title: t("Settings"), icon: "settings", position: "bottom", builtin: true }];
  }

  function currentArea() { return areas().find((area) => area.id === state.area) || areas()[0]; }

  function renderRibbon() {
    const nav = $("ribbon");
    const make = (area) => {
      const button = el("button", {
        type: "button", class: "ribbon-item", title: area.title, "aria-label": area.title,
        "aria-current": String(area.id === state.area), onclick: () => selectArea(area.id),
      });
      button.append(icon(area.icon), el("span", { class: "label", text: area.title }));
      if (state.badges[area.id]) button.append(el("span", { class: "badge", text: String(state.badges[area.id]) }));
      return button;
    };
    const all = areas();
    const expand = el("button", {
      type: "button", class: "ribbon-item expand", title: "Expand or shrink the ribbon", "aria-label": "Expand or shrink the ribbon",
      onclick: () => { saveLayout({ ribbon: layout().ribbon === "labels" ? "icons" : "labels" }); applyLayout(); },
    });
    expand.append(icon("ribbon"));
    nav.replaceChildren(...all.filter((a) => a.position === "top").map(make), el("span", { class: "spacer" }),
      ...all.filter((a) => a.position === "bottom").map(make), expand);
  }

  async function refreshBadges() {
    for (const area of areas()) {
      if (!area.entry || !area.entry.badge) continue;
      try {
        const payload = await api(area.entry.badge);
        const count = typeof payload === "number" ? payload : (payload.count ?? payload.total ?? 0);
        state.badges[area.id] = Number(count) || 0;
      } catch (error) { if (error instanceof AuthError) throw error; }
    }
    renderRibbon();
  }

  function selectArea(id) {
    state.area = id;
    state.selected = null;
    saveLayout({ area: id });
    $("crumb-area").textContent = currentArea().title;
    $("crumb-item").textContent = "";
    renderRibbon();
    renderSide();
    renderDock();
    const area = currentArea();
    const first = panelsOf(area)[0];
    if (first) openPanel(area, first); else if (!state.tabs.some((t) => t.area === area.id)) renderEmpty();
    else activate(state.tabs.filter((t) => t.area === area.id)[0].key);
    if (narrow()) $("app").classList.remove("hide-side");
  }

  // ---- panels of an area (module panels, System, Settings) ----------------------------

  function systemPanels() {
    return [
      { id: "status", type: "data", title: t("Status"), source: "/api/v1/status", refresh: 5 },
      { id: "config", type: "data", title: t("Config"), source: "/api/v1/ui/config", path: "entries" },
      { id: "logs", type: "log", title: t("Logs"), source: "/api/v1/ui/logs?lines=200", path: "lines" },
    ];
  }

  function settingsPanels() {
    return [{ id: "appearance", type: "appearance", title: t("Appearance") }, { id: "layout", type: "layout", title: t("Layout") }];
  }

  function panelsOf(area) {
    if (area.id === "system") return systemPanels();
    if (area.id === "settings") return settingsPanels();
    return ((area.entry && area.entry.panels) || []).filter((panel) => panel.dock !== "bottom");
  }

  // ---- side list ---------------------------------------------------------------------

  function chevron(open, none) { const c = icon("chevron", 14); c.setAttribute("class", `chev${open ? " open" : ""}${none ? " none" : ""}`); return c; }

  function renderSide() {
    const area = currentArea();
    $("side-title").textContent = area.title;
    const body = $("side-body");
    const sections = (area.entry && area.entry.sections) || [];
    const parts = [];
    if (sections.length) sections.forEach((section) => parts.push(...sectionRows(area, section)));
    const panels = panelsOf(area);
    if (panels.length) {
      if (sections.length) parts.push(el("div", { class: "tree-section-head", text: "Panels" }));
      panels.forEach((panel) => {
        const key = `${area.id}:${panel.id}`;
        const row = el("button", { type: "button", class: "tree-node", "aria-selected": String(state.active === key), onclick: () => openPanel(area, panel) });
        row.append(chevron(false, true), el("span", { class: "name", text: panel.title || panel.id }));
        row.style.paddingLeft = "8px";
        parts.push(row);
      });
    }
    if (area.id === "system") {
      const link = el("a", { class: "tree-node link-row", href: "console.html" });
      link.append(chevron(false, true), el("span", { class: "name", text: "Classic console (commands, events, audit)" }));
      parts.push(link);
    }
    body.replaceChildren(...(parts.length ? parts : [el("div", { class: "side-note", text: "This area declares nothing to list." })]));
  }

  function sectionKey(area, section) { return `sec:${area.id}/${section.id}`; }

  function sectionRows(area, section) {
    const key = sectionKey(area, section);
    const closed = state.open.has(`closed:${key}`);
    const head = el("button", { type: "button", class: "tree-section-head", "aria-expanded": String(!closed), onclick: () => { toggleSet(`closed:${key}`); renderSide(); } });
    head.append(chevron(!closed), el("span", { text: section.title }));
    const rows = [head];
    if (!closed) rows.push(...levelRows(area, section, "", 0));
    return rows;
  }

  function toggleSet(key) { if (state.open.has(key)) state.open.delete(key); else state.open.add(key); persistOpen(); }

  function persistOpen() {
    const ids = [...state.open].filter((key) => /^[A-Za-z0-9_.:/-]{1,96}$/.test(key));
    saveLayout({ open: ids.slice(0, 200) });
  }

  function treeUrl(section, parent, after) {
    const params = [];
    if (parent) params.push(`parent=${encodeURIComponent(parent)}`);
    if (after) params.push(`after=${encodeURIComponent(after)}`);
    if (!params.length) return section.tree;
    return `${section.tree}${section.tree.includes("?") ? "&" : "?"}${params.join("&")}`;
  }

  // a "more" node closes a long list: its cursor fetches the next page, which is appended
  function loadMore(area, section, parent, marker) {
    const cacheKey = `${area.id}/${section.id}/${parent}`;
    const cached = state.trees[cacheKey];
    if (!cached || cached.paging) return;
    cached.paging = true;
    renderSide();
    api(treeUrl(section, parent, marker.next)).then((payload) => {
      const kept = cached.nodes.filter((n) => !n.more);
      const known = new Set(kept.map((n) => n.id));
      state.trees[cacheKey] = { nodes: [...kept, ...(payload.nodes || []).filter((n) => !known.has(n.id))] };
      renderSide();
    }).catch((error) => { cached.paging = false; toast(error.message, true); renderSide(); });
  }

  function levelRows(area, section, parent, depth) {
    const cacheKey = `${area.id}/${section.id}/${parent}`;
    const cached = state.trees[cacheKey];
    if (!cached) {
      state.trees[cacheKey] = { loading: true, nodes: [] };
      api(treeUrl(section, parent)).then((payload) => { state.trees[cacheKey] = { nodes: payload.nodes || [] }; renderSide(); })
        .catch((error) => { state.trees[cacheKey] = { nodes: [], error: error.message }; renderSide(); });
      return [el("div", { class: "side-note", text: "Loading…" })];
    }
    if (cached.error) return [el("div", { class: "side-note error", text: cached.error })];
    if (!cached.nodes.length) return depth ? [] : [el("div", { class: "side-note", text: section.empty || "Nothing here." })];
    const rows = [];
    cached.nodes.forEach((node) => {
      if (node.more) {
        const more = el("button", { type: "button", class: "tree-node more", disabled: Boolean(cached.paging), onclick: () => loadMore(area, section, parent, node) });
        more.style.paddingLeft = `${8 + depth * 16}px`;
        more.append(chevron(false, true), el("span", { class: "name", plain: cached.paging ? t("Loading…") : node.label }));
        rows.push(more);
        return;
      }
      const openKey = `${area.id}/${section.id}/${node.id}`;
      const isOpen = node.has_children && state.open.has(openKey);
      const chosen = state.selected && state.selected.key === openKey;
      const row = el("button", {
        type: "button", class: "tree-node", "aria-selected": String(Boolean(chosen)),
        "aria-expanded": node.has_children ? String(Boolean(isOpen)) : null,
        onclick: () => {
          state.selected = { key: openKey, node, area: area.id, section: section.id };
          if (node.has_children) toggleSet(openKey);
          showNode(area, node);
          renderSide();
        },
      });
      row.style.paddingLeft = `${8 + depth * 16}px`;
      row.append(chevron(isOpen, !node.has_children), el("span", { class: "name", plain: node.label }));
      if (node.meta) row.append(el("span", { class: `meta${node.tone ? ` tone-${node.tone}` : ""}`, plain: node.meta }));
      rows.push(row);
      if (isOpen) rows.push(...levelRows(area, section, node.id, depth + 1));
    });
    return rows;
  }

  function showNode(area, node) {
    $("crumb-item").textContent = node.label;
    renderInspector(Object.entries({ id: node.id, label: node.label, meta: node.meta, tone: node.tone, opens: node.opens }).filter(([, v]) => v));
    if (node.opens) {
      const panel = ((area.entry && area.entry.panels) || []).find((p) => p.id === node.opens);
      if (panel) openPanel(area, panel); else refreshNodePanels(area);
    } else refreshNodePanels(area);
    if (narrow()) $("app").classList.add("hide-side");
  }

  // a panel whose source holds {node} shows a different answer for each chosen node
  function refreshNodePanels(area) {
    const shown = [state.active, state.activeB].map((key) => state.tabs.find((tab) => tab.key === key)).filter(Boolean);
    if (shown.some((tab) => usesNode(tab.panel))) renderPanes();
    if (((area.entry && area.entry.panels) || []).some((panel) => panel.dock === "bottom" && usesNode(panel))) renderDock();
  }

  function renderInspector(pairs) {
    const body = $("inspector-body");
    if (!pairs.length) { body.replaceChildren(el("div", { class: "side-note", text: "Select an item to see its properties." })); return; }
    body.replaceChildren(pairList(pairs));
  }

  // ---- tabs and the main surface ---------------------------------------------------

  function openPanel(area, panel) {
    const key = `${area.id}:${panel.id}`;
    if (!state.tabs.some((tab) => tab.key === key)) state.tabs.push({ key, area: area.id, panel, title: panel.title || panel.id });
    activate(key);
    if (narrow()) $("app").classList.add("hide-side"); // a panel picked from the list takes the page
  }

  function closeTab(key) {
    state.tabs = state.tabs.filter((tab) => tab.key !== key);
    const last = state.tabs[state.tabs.length - 1];
    if (state.activeB === key) state.activeB = last ? last.key : "";
    if (state.active === key) {
      if (last) { state.focus = "a"; activate(last.key); } else { state.active = ""; renderTabs(); renderPanes(); }
    } else { renderTabs(); renderPanes(); }
  }

  function renderTabs() {
    $("tabs").replaceChildren(...state.tabs.map((tab) => {
      const button = el("button", { type: "button", role: "tab", class: "tab", "aria-selected": String(tab.key === (state.focus === "b" && splitMode() !== "off" ? state.activeB : state.active)), onclick: () => activate(tab.key) });
      button.append(el("span", { text: tab.title }),
        el("span", { class: "muted", title: "Close", "aria-label": tf("Close {title}", { title: tab.title }), text: "×", onclick: (event) => { event.stopPropagation(); closeTab(tab.key); } }));
      return button;
    }));
  }

  function renderEmpty() { renderPanes(); }

  function clearTimers(list) { list.splice(0).forEach((id) => clearInterval(id)); }

  const splitMode = () => (narrow() ? "off" : (layout().split || "off"));
  const surfaceOf = (which) => $(which === "b" ? "surface-b" : "surface");

  // a tab opens in the pane that has the focus; with the split off that is always the first
  function activate(key) {
    if (state.focus === "b" && splitMode() !== "off") state.activeB = key; else state.active = key;
    renderTabs();
    renderSide();
    renderPanes();
  }

  function renderPanes() {
    clearTimers(state.timers);
    clearTimers(state.timersB);
    drawPane("a", state.active, state.timers);
    if (splitMode() !== "off") drawPane("b", state.activeB, state.timersB);
    $("surface").classList.toggle("focused", state.focus === "a");
    $("surface-b").classList.toggle("focused", state.focus === "b");
  }

  function drawPane(which, key, timers) {
    const surface = surfaceOf(which);
    surface.replaceChildren();
    const tab = state.tabs.find((t) => t.key === key);
    if (!tab) { surface.append(el("p", { class: "empty", text: "Pick something in the side list. Every panel a module declares in its [ui] table appears here, with no GUI code." })); return; }
    const area = areas().find((a) => a.id === tab.area);
    surface.append(el("h1", { text: tab.title }));
    if (tab.panel.help) surface.append(el("p", { class: "muted", text: tab.panel.help }));
    surface.append(panelCard(tab.panel, area, timers));
  }

  function setSplit(mode) {
    saveLayout({ split: mode });
    if (mode !== "off" && !state.activeB) state.activeB = state.active;
    state.focus = "a";
    applyLayout();
    renderTabs();
    renderPanes();
  }

  function renderDock() {
    clearTimers(state.dockTimers);
    const area = currentArea();
    const docked = ((area.entry && area.entry.panels) || []).filter((panel) => panel.dock === "bottom");
    const body = $("bottom-body");
    $("bottom").classList.toggle("empty", !docked.length);
    if (!docked.length) {
      body.replaceChildren(el("div", { class: "side-note", text: "Nothing is docked in this area. A module can dock a panel here with dock = \"bottom\"." }));
      return;
    }
    body.replaceChildren(...docked.map((panel) => panelCard(panel, area, state.dockTimers)));
  }

  // ---- panel renderers -----------------------------------------------------------------

  function panelCard(panel, area, timers) {
    const content = el("div", { class: "content" });
    const card = el("section", { class: "card" }, [el("h3", { text: panel.title || panel.id })]);
    if (panel.help && panel.type !== "text") card.append(el("div", { class: "help", text: panel.help }));
    card.append(content);
    const draw = async () => {
      try { await fill(content, panel, area); } catch (error) {
        if (error instanceof AuthError) { showGate(error.message); return; }
        content.replaceChildren(el("p", { class: "error", text: error.message }));
      }
    };
    draw();
    if (panel.refresh > 0) timers.push(setInterval(draw, panel.refresh * 1000));
    return card;
  }

  async function fill(content, panel, area) {
    switch (panel.type) {
      case "text": content.replaceChildren(el("p", { text: panel.text || "" })); return;
      case "appearance": renderAppearance(content); return;
      case "layout": renderLayoutSettings(content); return;
      case "keyvalue": {
        const data = dig(await api(withNode(panel.source)), panel.path) || {};
        content.replaceChildren(pairList(Object.entries(data)));
        return;
      }
      case "log": {
        const data = dig(await api(withNode(panel.source)), panel.path);
        const text = Array.isArray(data) ? data.join("\n") : fmt(data);
        content.replaceChildren(el("pre", { class: "log", text }));
        return;
      }
      case "table": renderTable(content, panel, dig(await api(withNode(panel.source)), panel.path) || [], area); return;
      case "data": renderData(content, dig(await api(withNode(panel.source)), panel.path)); return;
      case "form": renderForm(content, panel, area); return;
      case "actions": renderActions(content, panel); return;
      case "embed": renderEmbed(content, panel); return;
      case "settings": await renderSettings(content, panel, area); return;
      default: content.replaceChildren(el("p", { class: "muted", text: tf("Unknown panel type {type}.", { type: panel.type }) }));
    }
  }

  // An embed panel frames a page another module serves from this daemon. The token never goes
  // into the URL: the page asks for it with a message and gets it only if the asker is the very
  // frame this panel created and shares this origin.
  function renderEmbed(content, panel) {
    const frame = el("iframe", {
      class: "embed", src: panel.src, title: panel.title || panel.id,
      sandbox: "allow-scripts allow-same-origin allow-forms allow-downloads",
      referrerpolicy: "no-referrer",
    });
    frame.style.height = `${panel.height || 360}px`;
    const listen = (event) => {
      if (!frame.isConnected) { window.removeEventListener("message", listen); return; }
      if (event.source !== frame.contentWindow || event.origin !== location.origin) return;
      if (!event.data || event.data.type !== "kdx-embed-ready") return;
      frame.contentWindow.postMessage({ type: "kdx-embed-token", token: state.token }, location.origin);
    };
    window.addEventListener("message", listen);
    content.replaceChildren(frame);
  }

  function chip(text, tones) {
    const tone = (tones && tones[String(text).toLowerCase()]) || "";
    return el("span", { class: `chip${tone ? ` tone-${tone}` : ""}`, plain: fmt(text) });
  }

  const TONE_WORDS = {
    ok: ["ok", "done", "ready", "success", "succeeded", "passed", "running", "active", "enabled", "up", "true", "yes"],
    warn: ["warn", "warning", "degraded", "stale", "retrying", "slow"],
    bad: ["bad", "error", "failed", "failure", "dead", "down", "refused", "denied", "cancelled", "false", "no"],
    busy: ["busy", "queued", "pending", "starting", "stopping", "waiting"],
  };

  function toneOf(column, text) {
    if (column.tones && Object.prototype.hasOwnProperty.call(column.tones, text)) return column.tones[text];
    const word = String(text).toLowerCase();
    return Object.keys(TONE_WORDS).find((tone) => TONE_WORDS[tone].includes(word)) || "muted";
  }

  function fmtBytes(value) {
    let n = Number(value);
    if (!Number.isFinite(n)) return String(value);
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (Math.abs(n) >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
    return `${i ? n.toFixed(n < 10 ? 1 : 0) : n} ${units[i]}`;
  }

  function fmtDuration(value) {
    const total = Math.floor(Number(value));
    if (!Number.isFinite(total)) return String(value);
    const d = Math.floor(total / 86400), h = Math.floor((total % 86400) / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
    return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : m ? `${m}m ${s}s` : `${s}s`;
  }

  function fmtTime(value) {
    const n = Number(value);
    const date = Number.isFinite(n) && String(value).trim() !== "" ? new Date(n < 1e11 ? n * 1000 : n) : new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
  }

  // a link is followed only when it is a web address or a path of this host
  function safeHref(value) {
    const text = String(value).trim();
    return /^https?:\/\//i.test(text) || (text.startsWith("/") && !text.startsWith("//")) ? text : "";
  }

  function cellNode(column, value) {
    if (value === null || value === undefined) return el("td", { plain: "-" });
    const text = fmt(value);
    switch (column.kind) {
      case "chip": return el("td", {}, [el("span", { class: `chip tone-${toneOf(column, text)}`, plain: text })]);
      case "bytes": return el("td", { plain: fmtBytes(value) });
      case "duration": return el("td", { plain: fmtDuration(value) });
      case "time": return el("td", { plain: fmtTime(value) });
      case "code": return el("td", {}, [el("code", { plain: text })]);
      case "link": {
        const href = safeHref(text);
        return el("td", {}, [href ? el("a", { href, target: "_blank", rel: "noopener noreferrer", plain: text }) : document.createTextNode(text)]);
      }
      default: return el("td", { plain: text });
    }
  }

  // {column} placeholders in a row action's args or a detail route are filled from the row
  function fillRow(template, row, urlSafe) {
    return String(template).replace(/\{([^{}]+)\}/g, (match, name) => {
      let value = dig(row, name);
      if ((value === null || value === undefined) && name === "node") value = nodeId();
      const text = value === null || value === undefined ? "" : String(value);
      return urlSafe ? encodeURIComponent(text) : text;
    });
  }

  function rowArgs(args, row) {
    const out = {};
    Object.entries(args || {}).forEach(([name, value]) => { out[name] = typeof value === "string" ? fillRow(value, row, false) : value; });
    return out;
  }

  function renderTable(content, panel, rows, area) {
    const list = Array.isArray(rows) ? rows : Object.entries(rows).map(([name, value]) => ({ name, value }));
    const columns = (panel.columns && panel.columns.length) ? panel.columns : Object.keys(list[0] || {}).map((key) => ({ key, label: key }));
    if (!list.length) { content.replaceChildren(el("p", { class: "muted", text: "Nothing to show." })); return; }
    const actions = panel.actions || [];
    const extra = actions.length || panel.detail ? 1 : 0;
    const runs = el("div", { class: "runs" });
    const detail = el("div", { class: "detail" });
    const reload = () => fill(content, panel, area).catch(() => {});
    const actionCell = (row) => {
      const cell = el("td", { class: "row-actions" });
      if (panel.detail) {
        cell.append(el("button", { type: "button", class: "plain", text: "Details", onclick: async () => {
          try {
            const value = dig(await api(fillRow(panel.detail.source, row, true)), panel.detail.path);
            const pairs = value && typeof value === "object" ? Object.entries(value) : [["value", value]];
            detail.replaceChildren(pairList(pairs, panel.detail.kinds), el("button", { type: "button", class: "plain", text: "Close", onclick: () => detail.replaceChildren() }));
          } catch (error) { toast(error.message, true); }
        } }));
      }
      actions.forEach((action) => {
        const command = commandEntry(action.command);
        const button = el("button", { type: "button", class: action.danger ? "plain danger" : "plain", text: action.label });
        if (!command || !command.remote || !can(command.role)) button.disabled = true;
        button.addEventListener("click", () => {
          if ((action.confirm || action.danger) && !armed(button, "Click again to run")) return;
          runCommand(runs, action.command, rowArgs(action.args, row)).then(reload);
        });
        cell.append(button);
      });
      return cell;
    };
    const head = el("tr", {}, [...columns.map((c) => el("th", { text: c.label || c.key })), ...(extra ? [el("th")] : [])]);
    const body = list.map((row) => el("tr", { onclick: () => renderInspector(Object.entries(row)) },
      [...columns.map((c) => cellNode(c, c.key ? dig(row, c.key) : row)), ...(extra ? [actionCell(row)] : [])]));
    content.replaceChildren(el("div", { class: "scroll-x" }, [el("table", {}, [el("thead", {}, [head]), el("tbody", {}, body)])]), detail, runs);
  }

  // ---- running commands: forms, buttons, output ------------------------------------------

  const ROLE_RANK = { viewer: 0, operator: 1, admin: 2 };
  const JOB_POLL_MS = 500;
  const CONFIRM_MS = 5000;

  function can(role) {
    const mine = state.me && state.me.role;
    if (!mine) return true; // anonymous with auth off; the daemon still decides
    return (ROLE_RANK[mine] ?? -1) >= (ROLE_RANK[role] ?? 99);
  }

  const commandEntry = (path) => state.catalog.find((command) => command.path === path);
  const shellQuote = (token) => (/^[\w./:=@,+-]+$/.test(token) ? token : `'${token.replace(/'/g, "'\\''")}'`);

  // a risky button asks twice: the first click arms it for a few seconds, the second acts
  function armed(button, label) {
    if (button.dataset.armed === "1") { button.dataset.armed = ""; button.textContent = button.dataset.label; return true; }
    button.dataset.label = button.dataset.label || button.textContent;
    button.dataset.armed = "1";
    button.textContent = label;
    clearTimeout(button.armTimer);
    button.armTimer = setTimeout(() => { button.dataset.armed = ""; button.textContent = button.dataset.label; }, CONFIRM_MS);
    return false;
  }

  // the argv a terminal user would type; the daemon parses it with the command's own declaration
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

  function runCard(line, host) {
    const output = el("pre", { class: "log", text: "starting…" });
    const meta = el("div", { class: "muted", text: "running" });
    const cancel = el("button", { type: "button", class: "plain", hidden: true, text: "Cancel" });
    const card = el("div", { class: "run" }, [el("div", { class: "row" }, [el("code", { text: `$ ${line}` }), cancel]), output, meta]);
    card.output = output; card.meta = meta; card.cancel = cancel;
    host.prepend(card);
    while (host.children.length > 10) host.lastChild.remove();
    return card;
  }

  function finishCard(card, text, meta, failed) {
    card.cancel.hidden = true;
    card.output.textContent = text;
    card.meta.textContent = meta;
    card.classList.toggle("failed", Boolean(failed));
  }

  async function followJob(id, card) {
    for (;;) {
      let job;
      try { job = await api(`/api/v1/jobs?id=${encodeURIComponent(id)}`); }
      catch (error) { finishCard(card, card.output.textContent, `lost track of ${id}: ${error.message}`, true); return; }
      if (job.state === "running" || job.state === "cancelling") {
        card.output.textContent = job.output || "running…";
        card.meta.textContent = `${id} · ${job.state} ${job.seconds}s${job.user ? ` · by ${job.user}` : ""}`;
        const mine = !state.me || state.me.role === "admin" || state.me.name === job.user;
        card.cancel.hidden = !mine || job.state === "cancelling";
        if (!card.cancel.onclick) {
          card.cancel.onclick = () => {
            if (!armed(card.cancel, "Click again to cancel")) return;
            api("/api/v1/jobs", { method: "POST", body: { cancel: id } }).then(() => { card.cancel.hidden = true; }).catch((e) => toast(e.message, true));
          };
        }
        await new Promise((resolve) => setTimeout(resolve, JOB_POLL_MS));
        continue;
      }
      const text = job.error ? `${job.output || ""}${job.error}` : (job.output || "(no output)");
      finishCard(card, text, `${id} · ${job.state} · exit ${job.exit_code ?? "-"} · ${job.seconds}s`, job.state !== "done");
      return;
    }
  }

  async function runCommand(host, path, values) {
    const command = commandEntry(path);
    if (!command || !command.remote || !can(command.role)) {
      toast(command ? "this command is not available to you here" : "unknown command", true);
      return;
    }
    const argv = argvFrom(command, values || {});
    const card = runCard(["kdx", ...path.split(" "), ...argv].map(shellQuote).join(" "), host);
    try {
      const { status, payload } = await call("/api/v1/rpc", { method: "POST", body: { command: path, args: { argv }, background: true } });
      if (!payload.ok) { finishCard(card, payload.error || `HTTP ${status}`, `refused · HTTP ${status}`, true); return; }
      await followJob(payload.job.id, card);
    } catch (error) {
      if (error instanceof AuthError) showGate(error.message); else finishCard(card, error.message, "failed", true);
    }
  }

  async function savePrefs(changes) {
    const layoutKept = state.prefs.layout;
    state.prefs = await api("/api/v1/ui/prefs", { method: "PATCH", body: changes });
    state.prefs.layout = layoutKept;
  }

  function renderActions(content, panel) {
    const runs = el("div", { class: "runs" });
    const row = el("div", { class: "row" }, panel.buttons.map((button) => {
      const command = commandEntry(button.command);
      const node = el("button", { type: "button", class: button.danger ? "plain danger" : "plain", text: button.label });
      if (!command || !command.remote || !can(command.role)) node.disabled = true;
      node.addEventListener("click", () => {
        if ((button.confirm || button.danger) && !armed(node, "Click again to run")) return;
        runCommand(runs, button.command, nodeArgs(button.args));
      });
      return node;
    }));
    content.replaceChildren(row, runs);
  }

  function renderForm(content, panel, area) {
    const command = commandEntry(panel.command);
    if (!command) { content.replaceChildren(el("p", { class: "error", text: tf("unknown command: {command}", { command: panel.command }) })); return; }
    const formKey = `${area.id}.${panel.id}`;
    const prefs = () => state.prefs;
    const fresh = () => Object.assign({}, nodeArgs(panel.args),(prefs().defaults || {})[formKey] || {});
    const remembered = () => Object.assign(fresh(), (prefs().values || {})[formKey] || {});
    const inputs = new Map();
    const later = [];
    const numeric = (arg) => arg.kind === "int" || arg.kind === "float";
    const fields = command.args.map((arg) => {
      const label = arg.positional ? arg.dest : arg.flags[arg.flags.length - 1];
      const spec = (panel.fields || {})[arg.dest] || {};
      let input;
      let extra = null;
      const id = `f-${panel.id}-${arg.dest}`;
      if (arg.kind === "flag") input = el("input", { type: "checkbox", id });
      else if (spec.choices_from) { input = el("select", { id }, [el("option", { value: "", text: "(default)" })]); later.push({ input, arg, spec }); }
      else if (arg.choices && arg.choices.length) input = el("select", { id }, [el("option", { value: "", text: "(default)" }), ...arg.choices.map((c) => el("option", { value: c, plain: c }))]);
      else if (spec.kind === "textarea") input = el("textarea", { id, rows: String(spec.rows || 4), placeholder: arg.metavar || "" });
      else if (numeric(arg) && (spec.kind === "slider" || spec.kind === "number" || arg.minimum != null || arg.maximum != null)) {
        const slide = spec.kind === "slider" && arg.minimum != null && arg.maximum != null;
        input = el("input", { id, type: slide ? "range" : "number", min: arg.minimum != null ? String(arg.minimum) : null, max: arg.maximum != null ? String(arg.maximum) : null,
          step: arg.step != null ? String(arg.step) : (arg.kind === "float" ? "any" : "1"), placeholder: arg.default != null ? tf("default: {value}", { value: arg.default }) : "" });
        if (slide) { const shown = el("output", { class: "muted", text: input.value }); input.addEventListener("input", () => { shown.textContent = input.value; }); extra = shown; }
      } else input = el("input", { id, type: numeric(arg) ? "number" : "text", step: arg.kind === "float" ? "any" : null, placeholder: arg.default != null ? tf("default: {value}", { value: arg.default }) : (arg.metavar || "") });
      inputs.set(arg.dest, { input, arg, spec });
      const hint = el("small", { class: "muted", text: arg.help || "" });
      return arg.kind === "flag"
        ? el("div", { class: "field check" }, [input, el("label", { for: id, text: label }), hint])
        : el("div", { class: "field" }, [el("label", { for: id, text: label }), input, ...(extra ? [extra] : []), hint]);
    });
    later.forEach(({ input, arg, spec }) => api(spec.choices_from).then((payload) => {
      const wanted = remembered()[arg.dest];
      const items = dig(payload, spec.path);
      (Array.isArray(items) ? items : []).forEach((item) => {
        const value = spec.value ? dig(item, spec.value) : item;
        const text = spec.label ? dig(item, spec.label) : value;
        if (value === null || value === undefined || typeof value === "object") return;
        input.append(el("option", { value: String(value), plain: text === null || text === undefined ? String(value) : String(text) }));
      });
      if (wanted !== undefined && wanted !== null) input.value = String(wanted);
    }).catch((error) => toast(error.message, true)));
    const apply = (values) => inputs.forEach(({ input, arg }, dest) => {
      const value = values[dest];
      if (arg.kind === "flag") input.checked = value === true || value === "true" || (value === undefined && arg.default === true);
      else input.value = value === undefined || value === null ? "" : String(value);
    });
    const collect = () => {
      const out = {};
      inputs.forEach(({ input, arg, spec }, dest) => {
        if (arg.kind === "flag") out[dest] = input.checked;
        else if (input.value.trim() !== "") out[dest] = numeric(arg) ? Number(input.value) : (spec.kind === "textarea" ? input.value : input.value.trim());
      });
      return out;
    };
    const forget = (stored, current) => { const wipe = {}; Object.keys(stored || {}).forEach((name) => { if (!(name in current)) wipe[name] = null; }); return Object.assign(wipe, current); };
    apply(remembered());
    const runs = el("div", { class: "runs" });
    const form = el("form", {}, [...fields, el("div", { class: "row" }, [
      el("button", { type: "submit", text: "Run" }),
      el("button", { type: "button", class: "plain", text: "Save as default", onclick: () => savePrefs({ defaults: { [formKey]: forget((prefs().defaults || {})[formKey], collect()) } }).then(() => toast("Default saved")).catch((e) => toast(e.message, true)) }),
      el("button", { type: "button", class: "plain", text: "Restore defaults", onclick: () => apply(fresh()) }),
    ])]);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const values = collect();
      savePrefs({ values: { [formKey]: forget((prefs().values || {})[formKey], values) } }).catch(() => {});
      runCommand(runs, panel.command, values);
    });
    content.replaceChildren(form, runs);
  }

  // typed form of the declared config keys; one admin-only POST saves them to the local layer
  async function renderSettings(content, panel, area) {
    const query = new URLSearchParams();
    if (panel.module) query.set("module", panel.module);
    if (panel.group) query.set("group", panel.group);
    const payload = await api(`${panel.source}?${query}`);
    const mayEdit = can("admin");
    const prefix = panel.module ? `modules.${panel.module}.` : "";
    const inputs = new Map();
    const result = el("div", { class: "settings-result" });
    const rows = (payload.keys || []).map((item) => {
      const name = prefix && item.key.startsWith(prefix) ? item.key.slice(prefix.length) : item.key;
      const id = `s-${item.key}`;
      let input;
      if (item.type === "bool") { input = el("input", { id, type: "checkbox" }); input.checked = item.value === true; }
      else if (item.choices && item.choices.length) { input = el("select", { id }, item.choices.map((c) => el("option", { value: c, plain: c }))); input.value = item.value == null ? "" : String(item.value); }
      else if (item.type === "int" || item.type === "float") { input = el("input", { id, type: "number", step: item.type === "float" ? "any" : "1", min: item.minimum != null ? String(item.minimum) : null, max: item.maximum != null ? String(item.maximum) : null }); input.value = item.value == null ? "" : String(item.value); }
      else { input = el("input", { id, type: "text" }); input.value = Array.isArray(item.value) ? item.value.join(", ") : (item.value == null ? "" : String(item.value)); }
      input.disabled = !mayEdit || !item.editable;
      inputs.set(item.key, { input, item });
      const badges = el("span", { class: "row" }, [
        el("span", { class: "chip", plain: item.layer }),
        el("span", { class: `chip tone-${item.live ? "ok" : "warn"}`, text: item.live ? "applies live" : "needs restart" }),
        ...(item.overridden ? [el("span", { class: "chip tone-bad", text: "overridden by environment or CLI" })] : []),
        ...(item.secret ? [el("span", { class: "chip tone-bad", text: "secret: set it in .env" })] : []),
      ]);
      const unset = el("button", { type: "button", class: "plain", text: "Reset", hidden: !String(item.layer).startsWith("local") || !mayEdit, onclick: () => submit({}, [item.key]) });
      return el("div", { class: "field" }, [el("label", { for: id, text: name }), input, el("small", { class: "muted", text: item.description || "" }), badges, unset]);
    });
    const changes = () => {
      const set = {};
      inputs.forEach(({ input, item }, key) => {
        if (input.disabled) return;
        let value;
        if (item.type === "bool") value = input.checked;
        else if (input.value.trim() === "") return;
        else if (item.type === "int" || item.type === "float") value = Number(input.value);
        else if (item.type === "list") value = input.value.split(",").map((part) => part.trim()).filter(Boolean);
        else value = input.value;
        if (JSON.stringify(value) !== JSON.stringify(item.value)) set[key] = value;
      });
      return set;
    };
    async function submit(set, unset) {
      const { ok, payload: answer } = await call("/api/v1/ui/config/edit", { method: "POST", body: { set, unset } });
      if (!ok) { result.replaceChildren(el("p", { class: "error", text: [answer.error, answer.hint].filter(Boolean).join(" - ") })); return; }
      const words = { live: "applied now", restart: "saved; needs a restart", shadowed: "saved, but a higher layer still wins", saved: "saved" };
      const lines = answer.keys.map((row) => el("p", { class: row.applied === "shadowed" ? "error" : "muted", plain: `${row.key}: ${t(words[row.applied] || row.applied)}` }));
      toast("Settings saved");
      await renderSettings(content, panel, area);
      content.append(...lines);
    }
    const save = el("button", { type: "button", text: "Save changes", disabled: !mayEdit, onclick: async () => {
      const set = changes();
      if (!Object.keys(set).length) { toast("Nothing changed"); return; }
      try { await submit(set, []); } catch (error) { toast(error.message, true); }
    } });
    content.replaceChildren(...(rows.length ? rows : [el("p", { class: "muted", text: "No declared settings." })]),
      el("div", { class: "row" }, [save, ...(mayEdit ? [] : [el("span", { class: "muted", text: "Saving needs the admin role." })])]), result);
  }

  // a generic view for System: scalars as key/value, arrays of objects as tables
  function renderData(content, data) {
    if (Array.isArray(data)) { renderTable(content, { columns: null }, data); return; }
    if (!data || typeof data !== "object") { content.replaceChildren(el("pre", { class: "log", text: fmt(data) })); return; }
    const parts = [];
    const scalars = Object.entries(data).filter(([, value]) => typeof value !== "object" || value === null);
    if (scalars.length) {
      const list = el("dl", { class: "kv" });
      scalars.forEach(([key, value]) => list.append(el("dt", { plain: key }), el("dd", { plain: fmt(value) })));
      parts.push(list);
    }
    Object.entries(data).forEach(([key, value]) => {
      if (Array.isArray(value) && value.length && typeof value[0] === "object") {
        const holder = el("div", {});
        renderTable(holder, { columns: null }, value);
        parts.push(el("h2", { text: key }), holder);
      } else if (value && typeof value === "object" && !Array.isArray(value)) {
        const list = el("dl", { class: "kv" });
        Object.entries(value).forEach(([k, v]) => list.append(el("dt", { plain: k }), el("dd", { plain: fmt(v) })));
        parts.push(el("h2", { text: key }), list);
      }
    });
    content.replaceChildren(...parts);
  }

  // ---- settings: appearance and layout -------------------------------------------------

  function renderAppearance(content) {
    const select = el("select", { id: "appearance-theme", "aria-label": "Theme" }, state.themes.themes.map((theme) => el("option", { value: theme.name, text: `${theme.name} (${theme.source})` })));
    select.value = state.themes.active;
    select.addEventListener("change", () => chooseTheme(select.value).catch((e) => toast(e.message, true)));
    // the language is also here, because the top bar of a phone has no room for it
    const language = el("select", { id: "appearance-language", "aria-label": "Language" }, [...$("lang").options].map((o) => el("option", { value: o.value, plain: o.textContent })));
    language.value = $("lang").value;
    language.addEventListener("change", () => { $("lang").value = language.value; $("lang").dispatchEvent(new Event("change")); });
    content.replaceChildren(
      el("div", { class: "setting" }, [el("label", { for: "appearance-theme", text: "Theme" }), select]),
      el("div", { class: "setting" }, [el("label", { for: "appearance-language", text: "Language" }), language]),
      el("p", { class: "muted", text: "Themes are CSS token files: built in, <project>/themes/ and data/ui/themes/. In the terminal: kdx ui theme list | set | check." }));
  }

  function renderLayoutSettings(content) {
    const rows = Object.keys(LIMITS).map((key) => {
      const input = el("input", { id: `size-${key}`, type: "number", min: LIMITS[key][0], max: LIMITS[key][1], value: String(sizeOf(key)), "aria-label": tf("{pane} size", { pane: t(key) }) });
      input.addEventListener("change", () => { input.value = String(setSize(key, Number(input.value) || LIMITS[key][2], true)); });
      return el("div", { class: "setting" }, [el("label", { for: `size-${key}`, text: tf("{pane} (px)", { pane: t(key) }) }), input]);
    });
    const reset = el("button", { type: "button", class: "plain", text: "Reset layout", onclick: async () => {
      await api("/api/v1/ui/prefs?section=layout", { method: "DELETE" }).catch((e) => toast(e.message, true));
      state.prefs.layout = {};
      applyLayout();
      renderLayoutSettings(content);
    } });
    content.replaceChildren(...rows, reset,
      el("p", { class: "muted", text: "Drag a splitter, or focus it and use the arrow keys. In the terminal: kdx ui layout show | set | reset." }));
  }

  // ---- boot --------------------------------------------------------------------------

  function showGate(message) {
    $("token-gate").hidden = false;
    const error = $("token-error");
    error.hidden = !message;
    error.textContent = message || "";
    $("state-pill").textContent = "locked";
  }

  async function refreshStatus() {
    try {
      state.status = await api("/api/v1/status");
      const label = state.status.state || state.status.status || "online";
      $("state-pill").textContent = String(label);
      $("state-pill").className = "pill ok";
      const name = [state.status.app, state.status.name].find((value) => typeof value === "string" && value);
      if (name) $("app-name").textContent = name;
    } catch (error) {
      if (error instanceof AuthError) throw error;
      $("state-pill").textContent = "offline";
      $("state-pill").className = "pill bad";
    }
  }

  async function boot() {
    try {
      const prefs = await api("/api/v1/ui/prefs");
      await loadI18n((prefs.settings || {}).locale);
      buildToggles(); // built again now that the texts are translated
      state.manifest = await api(`/api/v1/ui/manifest?locale=${encodeURIComponent(i18n.locale)}`);
      state.prefs = prefs;
      state.prefs.layout = prefs.layout || {};
      await loadTheme();
      state.catalog = (await api("/api/v1/ui/catalog").catch(() => ({}))).commands || [];
      const me = await api("/api/v1/whoami").catch(() => ({}));
      state.me = me.name ? me : null;
      $("status-user").textContent = state.me ? `${state.me.name} · ${state.me.role}` : t("anonymous");
      $("token-gate").hidden = true;
    } catch (error) {
      if (error instanceof AuthError) showGate(error.message); else toast(error.message, true);
      return;
    }
    state.open = new Set(layout().open || []);
    applyLayout();
    (manifest => (manifest.issues || []).forEach((issue) => toast(`${issue.module}: ${issue.message}`, true)))(state.manifest);
    const all = areas();
    const wanted = layout().area;
    renderRibbon();
    selectArea(all.some((a) => a.id === wanted) ? wanted : all[0].id);
    if (narrow()) $("app").classList.add("hide-side"); // start on the content, the list is a tap away
    await refreshStatus();
    try { await refreshBadges(); } catch (error) { /* a badge never blocks the shell */ }
    setInterval(() => { refreshStatus().catch(() => {}); refreshBadges().catch(() => {}); }, BADGE_MS);
  }

  // ---- command palette and shortcut sheet --------------------------------------------------

  const palette = { items: [], shown: [], index: 0, returnTo: null };
  const SHORTCUTS = [
    ["Ctrl+K", "Command palette"], ["Ctrl+B", "Show or hide the side list"], ["Ctrl+Alt+B", "Show or hide the inspector"],
    ["Ctrl+J", "Show or hide the bottom panel"], ["Ctrl+\\", "Split the main surface: off, side by side, stacked"],
    ["?", "This list"], ["Esc", "Close the palette or this list"], ["Arrow keys", "Resize a focused splitter by 12 px"],
  ];

  function paletteItems() {
    const items = [];
    areas().forEach((area) => {
      items.push({ kind: "Area", label: area.title, run: () => selectArea(area.id) });
      panelsOf(area).forEach((panel) => items.push({
        kind: "Panel", label: `${area.title} / ${panel.title || panel.id}`,
        run: () => { if (state.area !== area.id) selectArea(area.id); openPanel(area, panel); },
      }));
    });
    [["ribbon", "Show or hide the ribbon"], ["side", "Show or hide the side list"], ["bottom", "Show or hide the bottom panel"], ["inspector", "Show or hide the inspector"]]
      .forEach(([pane, label]) => items.push({ kind: "View", label, run: () => togglePane(pane) }));
    items.forEach((item) => { if (item.kind === "View") item.translated = true; });
    items.push({ kind: "View", translated: true, label: "Focus: hide or show all panels", run: () => togglePane("focus") },
      { kind: "View", translated: true, label: "Split view: off, side by side, stacked", run: () => togglePane("split") });
    ((state.themes && state.themes.themes) || []).forEach((theme) => items.push({ kind: "Theme", label: tf("Theme: {name}", { name: theme.name }), run: () => chooseTheme(theme.name).catch((e) => toast(e.message, true)) }));
    items.push({ kind: "Help", translated: true, label: "Keyboard shortcuts", run: openShortcuts }, { kind: "Help", translated: true, label: "Classic console", run: () => { location.href = "console.html"; } });
    return items;
  }

  function renderPalette() {
    const words = $("palette-input").value.toLowerCase().split(/\s+/).filter(Boolean);
    palette.shown = palette.items.filter((item) => words.every((word) => `${item.kind} ${item.label}`.toLowerCase().includes(word))).slice(0, 50);
    palette.index = Math.min(palette.index, Math.max(0, palette.shown.length - 1));
    const list = $("palette-list");
    list.replaceChildren(...(palette.shown.length ? palette.shown.map((item, index) => {
      const row = el("li", { id: `palette-item-${index}`, role: "option", "aria-selected": String(index === palette.index), onclick: () => runPalette(index) },
        [el("span", { class: "kind", text: item.kind }), el("span", { plain: item.translated ? t(item.label) : item.label })]);
      return row;
    }) : [el("li", { class: "muted", text: "Nothing matches." })]));
    $("palette-input").setAttribute("aria-activedescendant", palette.shown.length ? `palette-item-${palette.index}` : "");
    const chosen = $(`palette-item-${palette.index}`);
    if (chosen) chosen.scrollIntoView({ block: "nearest" });
  }

  function openPalette() {
    if (!state.manifest) return;
    palette.returnTo = document.activeElement;
    palette.items = paletteItems();
    palette.index = 0;
    $("palette-input").value = "";
    $("palette").hidden = false;
    renderPalette();
    $("palette-input").focus();
  }

  function closePalette() {
    $("palette").hidden = true;
    if (palette.returnTo && palette.returnTo.focus) palette.returnTo.focus();
  }

  function runPalette(index) {
    const item = palette.shown[index];
    closePalette();
    if (item) item.run();
  }

  function openShortcuts() {
    palette.returnTo = document.activeElement;
    $("shortcuts-list").replaceChildren(...SHORTCUTS.flatMap(([keys, what]) => [el("dt", {}, [el("kbd", { text: keys })]), el("dd", { text: what })]));
    $("shortcuts").hidden = false;
    $("shortcuts-close").focus();
  }

  function closeShortcuts() {
    $("shortcuts").hidden = true;
    if (palette.returnTo && palette.returnTo.focus) palette.returnTo.focus();
  }

  function shortcuts(event) {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((event.target && event.target.tagName) || "");
    if (event.key === "Escape") {
      if (!$("palette").hidden) closePalette(); else if (!$("shortcuts").hidden) closeShortcuts();
      return;
    }
    if (event.key === "?" && !typing && !event.ctrlKey && !event.metaKey && !event.altKey) { event.preventDefault(); openShortcuts(); return; }
    if (!(event.ctrlKey || event.metaKey) || event.shiftKey) return;
    const key = event.key.toLowerCase();
    if (key === "k") { event.preventDefault(); if ($("palette").hidden) openPalette(); else closePalette(); return; }
    if (key === "\\") { event.preventDefault(); togglePane("split"); return; }
    const pane = key === "b" ? (event.altKey ? "inspector" : "side") : (key === "j" && !event.altKey ? "bottom" : "");
    if (!pane) return;
    event.preventDefault();
    togglePane(pane);
  }

  $("btn-palette").addEventListener("click", openPalette);
  $("palette-icon").append(icon("search", 14));
  $("palette-input").addEventListener("input", () => { palette.index = 0; renderPalette(); });
  $("palette-input").addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const count = palette.shown.length;
      if (count) palette.index = (palette.index + (event.key === "ArrowDown" ? 1 : count - 1)) % count;
      renderPalette();
    } else if (event.key === "Enter") { event.preventDefault(); runPalette(palette.index); }
  });
  $("palette").addEventListener("mousedown", (event) => { if (event.target === $("palette")) closePalette(); });
  $("shortcuts").addEventListener("mousedown", (event) => { if (event.target === $("shortcuts")) closeShortcuts(); });
  $("shortcuts-close").addEventListener("click", closeShortcuts);
  ["a", "b"].forEach((which) => surfaceOf(which).addEventListener("mousedown", () => {
    if (state.focus === which) return;
    state.focus = which;
    $("surface").classList.toggle("focused", which === "a");
    $("surface-b").classList.toggle("focused", which === "b");
    renderTabs();
  }));

  $("token-form").addEventListener("submit", (event) => {
    event.preventDefault();
    state.token = $("token-input").value.trim();
    try { sessionStorage.setItem(STORE_KEY, state.token); } catch (e) { /* private mode */ }
    $("token-input").value = "";
    boot();
  });
  $("theme-select").addEventListener("change", (event) => chooseTheme(event.target.value).catch((e) => toast(e.message, true)));
  $("btn-collapse").append(icon("collapse", 16));
  $("btn-collapse").addEventListener("click", () => {
    [...state.open].filter((key) => !key.startsWith("closed:")).forEach((key) => state.open.delete(key));
    persistOpen();
    renderSide();
  });
  $("btn-list").append(icon("list", 20));
  $("btn-list").addEventListener("click", () => $("app").classList.remove("hide-side"));
  document.querySelector(".center").addEventListener("click", () => { if (narrow()) $("app").classList.add("hide-side"); });
  document.querySelector(".crumbs").addEventListener("click", () => { if (narrow()) $("app").classList.remove("hide-side"); });
  $("side-back").addEventListener("click", () => $("app").classList.add("hide-side"));
  $("brand-logo").append(icon("logo", 20));
  markStatic();
  buildToggles();
  $("lang").addEventListener("change", (event) => {
    api("/api/v1/ui/prefs", { method: "PATCH", body: { settings: { locale: event.target.value } } }).then(() => location.reload()).catch((e) => toast(e.message, true));
  });
  splitter($("split-side"), "side", "x", 1);
  splitter($("split-bottom"), "bottom", "y", -1);
  splitter($("split-inspector"), "inspector", "x", -1);
  document.addEventListener("keydown", shortcuts);
  window.addEventListener("resize", () => { if (state.prefs) applyLayout(); });
  renderInspector([]);
  state.token = readToken();
  if (!state.token) showGate(""); else boot();
})();
