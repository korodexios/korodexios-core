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
    notes: "M6 3h9l4 4v14H6z|M14 3v5h5M9 13h7M9 17h5",
    search: "M17 11a6 6 0 1 1-12 0 6 6 0 0 1 12 0z|M20 20l-4-4",
    review: "M5 4h14v12H9l-4 4z|M9 9l2 2 4-4",
    trust: "M12 3l7 3v5c0 5-3 8-7 10-4-2-7-5-7-10V6z",
    models: "M7 5h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z|M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3",
    health: "M3 12h4l3-7 4 14 3-7h4",
    system: "M12 3l8 4.5v9L12 21l-8-4.5v-9z|M14.5 12a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z",
    settings: "M4 7h10M18 7h2M4 17h2M10 17h10|M18 7a2 2 0 1 1-4 0 2 2 0 0 1 4 0z|M10 17a2 2 0 1 1-4 0 2 2 0 0 1 4 0z",
    module: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
    logo: "M12 3l8 4.5v9L12 21l-8-4.5v-9z|M12 12l8-4.5M12 12v9M12 12L4 7.5",
    "pane-ribbon": "M4 5h16v14H4z|M8 5v14",
    "pane-side": "M4 5h16v14H4z|M10 5v14M4 10h6M4 14h6",
    "pane-bottom": "M4 5h16v14H4z|M4 14h16",
    "pane-inspector": "M4 5h16v14H4z|M14 5v14",
    focus: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
    chevron: "M9 6l6 6-6 6",
    collapse: "M7 9l5-5 5 5M7 15l5 5 5-5",
    ribbon: "M9 6l6 6-6 6",
  };

  const state = {
    token: "", manifest: null, prefs: null, themes: null, status: null,
    area: "", tabs: [], active: "", selected: null,
    trees: {}, open: new Set(), badges: {}, timers: [], dockTimers: [],
  };
  const $ = (id) => document.getElementById(id);

  // ---- helpers ---------------------------------------------------------------

  function el(tag, props, children) {
    const node = document.createElement(tag);
    Object.entries(props || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === false) return;
      if (key === "text") node.textContent = String(value);
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
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  }

  function dig(value, path) {
    if (!path) return value;
    return String(path).split(".").reduce((node, key) => (node === null || node === undefined ? undefined : node[key]), value);
  }

  function fmt(value) {
    if (value === null || value === undefined) return "-";
    return typeof value === "object" ? JSON.stringify(value) : String(value);
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
      try { await api("/api/v1/ui/prefs", { method: "PATCH", body: { layout: body } }); $("status-layout").textContent = "layout saved"; }
      catch (error) { toast(`layout not saved: ${error.message}`, true); }
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
    const off = new Set(hidden());
    if (pane === "focus") setHidden(PANES.every((p) => off.has(p)) ? [] : PANES);
    else { if (off.has(pane)) off.delete(pane); else off.add(pane); setHidden([...off]); }
  }

  function buildToggles() {
    const box = $("pane-toggles");
    [["ribbon", "Ribbon"], ["side", "Side list"], ["bottom", "Bottom panel"], ["inspector", "Inspector"], ["focus", "Focus: hide or show all panels"]]
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
    $("status-theme").textContent = `theme: ${state.themes.active}`;
    const select = $("theme-select");
    select.replaceChildren(...state.themes.themes.map((theme) => el("option", { value: theme.name, text: theme.name })));
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
      { id: "system", title: "System", icon: "system", position: "bottom", builtin: true },
      { id: "settings", title: "Settings", icon: "settings", position: "bottom", builtin: true }];
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
      type: "button", class: "ribbon-item", title: "Expand or shrink the ribbon", "aria-label": "Expand or shrink the ribbon",
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
      { id: "status", type: "data", title: "Status", source: "/api/v1/status", refresh: 5 },
      { id: "config", type: "data", title: "Config", source: "/api/v1/ui/config", path: "entries" },
      { id: "logs", type: "log", title: "Logs", source: "/api/v1/ui/logs?lines=200", path: "lines" },
    ];
  }

  function settingsPanels() {
    return [{ id: "appearance", type: "appearance", title: "Appearance" }, { id: "layout", type: "layout", title: "Layout" }];
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
      const link = el("a", { class: "tree-node link-row", href: "./" });
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

  function treeUrl(section, parent) {
    if (!parent) return section.tree;
    return `${section.tree}${section.tree.includes("?") ? "&" : "?"}parent=${encodeURIComponent(parent)}`;
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
      row.append(chevron(isOpen, !node.has_children), el("span", { class: "name", text: node.label }));
      if (node.meta) row.append(el("span", { class: `meta${node.tone ? ` tone-${node.tone}` : ""}`, text: node.meta }));
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
      if (panel) openPanel(area, panel);
    }
    if (narrow()) $("app").classList.add("hide-side");
  }

  function renderInspector(pairs) {
    const body = $("inspector-body");
    if (!pairs.length) { body.replaceChildren(el("div", { class: "side-note", text: "Select an item to see its properties." })); return; }
    const list = el("dl", { class: "kv" });
    pairs.forEach(([key, value]) => list.append(el("dt", { text: key }), el("dd", { text: fmt(value) })));
    body.replaceChildren(list);
  }

  // ---- tabs and the main surface ---------------------------------------------------

  function openPanel(area, panel) {
    const key = `${area.id}:${panel.id}`;
    if (!state.tabs.some((tab) => tab.key === key)) state.tabs.push({ key, area: area.id, panel, title: panel.title || panel.id });
    activate(key);
  }

  function closeTab(key) {
    state.tabs = state.tabs.filter((tab) => tab.key !== key);
    if (state.active === key) {
      const next = state.tabs[state.tabs.length - 1];
      if (next) activate(next.key); else { state.active = ""; renderTabs(); renderEmpty(); }
    } else renderTabs();
  }

  function renderTabs() {
    $("tabs").replaceChildren(...state.tabs.map((tab) => {
      const button = el("button", { type: "button", role: "tab", class: "tab", "aria-selected": String(tab.key === state.active), onclick: () => activate(tab.key) });
      button.append(el("span", { text: tab.title }),
        el("span", { class: "muted", title: "Close", "aria-label": `Close ${tab.title}`, text: "×", onclick: (event) => { event.stopPropagation(); closeTab(tab.key); } }));
      return button;
    }));
  }

  function renderEmpty() {
    $("surface").replaceChildren(el("p", { class: "empty", text: "Pick something in the side list. Every panel a module declares in its [ui] table appears here, with no GUI code." }));
  }

  function clearTimers(list) { list.splice(0).forEach((id) => clearInterval(id)); }

  function activate(key) {
    state.active = key;
    renderTabs();
    renderSide();
    const tab = state.tabs.find((t) => t.key === key);
    clearTimers(state.timers);
    const surface = $("surface");
    surface.replaceChildren();
    if (!tab) { renderEmpty(); return; }
    const area = areas().find((a) => a.id === tab.area);
    surface.append(el("h1", { text: tab.title }));
    if (tab.panel.help) surface.append(el("p", { class: "muted", text: tab.panel.help }));
    surface.append(panelCard(tab.panel, area, state.timers));
  }

  function renderDock() {
    clearTimers(state.dockTimers);
    const area = currentArea();
    const docked = ((area.entry && area.entry.panels) || []).filter((panel) => panel.dock === "bottom");
    const body = $("bottom-body");
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
        const data = dig(await api(panel.source), panel.path) || {};
        const list = el("dl", { class: "kv" });
        Object.entries(data).forEach(([key, value]) => list.append(el("dt", { text: key }), el("dd", { text: fmt(value) })));
        content.replaceChildren(list);
        return;
      }
      case "log": {
        const data = dig(await api(panel.source), panel.path);
        const text = Array.isArray(data) ? data.join("\n") : fmt(data);
        content.replaceChildren(el("pre", { class: "log", text }));
        return;
      }
      case "table": renderTable(content, panel, dig(await api(panel.source), panel.path) || []); return;
      case "data": renderData(content, dig(await api(panel.source), panel.path)); return;
      default: {
        const link = el("a", { href: "./", text: "Open the classic console" });
        content.replaceChildren(el("p", { class: "muted", text: `A ${panel.type} panel needs input, which the classic console provides.` }), link);
      }
    }
  }

  function chip(text, tones) {
    const tone = (tones && tones[String(text).toLowerCase()]) || "";
    return el("span", { class: `chip${tone ? ` tone-${tone}` : ""}`, text: fmt(text) });
  }

  function renderTable(content, panel, rows) {
    const list = Array.isArray(rows) ? rows : Object.values(rows);
    const columns = (panel.columns && panel.columns.length) ? panel.columns : Object.keys(list[0] || {}).map((key) => ({ key, label: key }));
    if (!list.length) { content.replaceChildren(el("p", { class: "muted", text: "No rows." })); return; }
    const head = el("tr", {}, columns.map((c) => el("th", { text: c.label || c.key })));
    const body = list.map((row) => {
      const tr = el("tr", { tabindex: "0", onclick: () => renderInspector(Object.entries(row)) },
        columns.map((c) => {
          const value = dig(row, c.key);
          return el("td", {}, [c.kind === "chip" ? chip(value, c.tones) : document.createTextNode(c.kind === "code" ? fmt(value) : fmt(value))]);
        }));
      return tr;
    });
    content.replaceChildren(el("div", { class: "scroll-x" }, [el("table", {}, [el("thead", {}, [head]), el("tbody", {}, body)])]));
  }

  // a generic view for System: scalars as key/value, arrays of objects as tables
  function renderData(content, data) {
    if (Array.isArray(data)) { renderTable(content, { columns: null }, data); return; }
    if (!data || typeof data !== "object") { content.replaceChildren(el("pre", { class: "log", text: fmt(data) })); return; }
    const parts = [];
    const scalars = Object.entries(data).filter(([, value]) => typeof value !== "object" || value === null);
    if (scalars.length) {
      const list = el("dl", { class: "kv" });
      scalars.forEach(([key, value]) => list.append(el("dt", { text: key }), el("dd", { text: fmt(value) })));
      parts.push(list);
    }
    Object.entries(data).forEach(([key, value]) => {
      if (Array.isArray(value) && value.length && typeof value[0] === "object") {
        const holder = el("div", {});
        renderTable(holder, { columns: null }, value);
        parts.push(el("h2", { text: key }), holder);
      } else if (value && typeof value === "object" && !Array.isArray(value)) {
        const list = el("dl", { class: "kv" });
        Object.entries(value).forEach(([k, v]) => list.append(el("dt", { text: k }), el("dd", { text: fmt(v) })));
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
    content.replaceChildren(
      el("div", { class: "setting" }, [el("label", { for: "appearance-theme", text: "Theme" }), select]),
      el("p", { class: "muted", text: "Themes are CSS token files: built in, <project>/themes/ and data/ui/themes/. In the terminal: kdx ui theme list | set | check." }));
  }

  function renderLayoutSettings(content) {
    const rows = Object.keys(LIMITS).map((key) => {
      const input = el("input", { id: `size-${key}`, type: "number", min: LIMITS[key][0], max: LIMITS[key][1], value: String(sizeOf(key)), "aria-label": `${key} size` });
      input.addEventListener("change", () => { input.value = String(setSize(key, Number(input.value) || LIMITS[key][2], true)); });
      return el("div", { class: "setting" }, [el("label", { for: `size-${key}`, text: `${key} (px)` }), input]);
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
      const [manifest, prefs] = await Promise.all([api("/api/v1/ui/manifest"), api("/api/v1/ui/prefs")]);
      state.manifest = manifest;
      state.prefs = prefs;
      state.prefs.layout = prefs.layout || {};
      await loadTheme();
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
    await refreshStatus();
    try { await refreshBadges(); } catch (error) { /* a badge never blocks the shell */ }
    setInterval(() => { refreshStatus().catch(() => {}); refreshBadges().catch(() => {}); }, BADGE_MS);
  }

  function shortcuts(event) {
    if (!(event.ctrlKey || event.metaKey) || event.shiftKey) return;
    const key = event.key.toLowerCase();
    const pane = key === "b" ? (event.altKey ? "inspector" : "side") : (key === "j" && !event.altKey ? "bottom" : "");
    if (!pane) return;
    event.preventDefault();
    togglePane(pane);
  }

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
  $("brand-logo").append(icon("logo", 20));
  buildToggles();
  splitter($("split-side"), "side", "x", 1);
  splitter($("split-bottom"), "bottom", "y", -1);
  splitter($("split-inspector"), "inspector", "x", -1);
  document.addEventListener("keydown", shortcuts);
  window.addEventListener("resize", () => { if (state.prefs) applyLayout(); });
  renderInspector([]);
  state.token = readToken();
  if (!state.token) showGate(""); else boot();
})();
