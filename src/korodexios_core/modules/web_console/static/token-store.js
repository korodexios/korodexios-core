// One place that remembers the console token, shared by the shell and the classic console so
// the two can never disagree. The server sets the ceiling (GET /api/v1/ui/token-policy, no
// token needed: it holds no secret); the person chooses inside it on the token gate.
//
//   store  always | ttl | tab | none   how long the browser may keep the token
//   ttl    seconds a kept token stays valid (0: no expiry)
//   cookie the server keeps it as an HttpOnly cookie (POST /api/v1/ui/session); no script
//          can read it, and this file stores nothing
//
// The header X-Kdx-Token, "#token=" in the address and the CLI behave as before in every mode.
(function () {
  "use strict";
  const KEY = "kdx-console-token";
  const SESSION_URL = "/api/v1/ui/session";
  const POLICY_URL = "/api/v1/ui/token-policy";
  const CHOICES = [
    { id: "always", label: "until I forget it", seconds: 0 },
    { id: "7d", label: "7 days", seconds: 7 * 86400 },
    { id: "12h", label: "12 hours", seconds: 12 * 3600 },
    { id: "tab", label: "this tab only", seconds: -1 },
    { id: "none", label: "do not remember", seconds: -2 },
  ];
  const ceiling = { always: 0, ttl: 0, tab: -1, none: -2 };
  const state = { policy: { token_store: "always", ttl_seconds: 0, cookie: false } };

  function local() { try { return window.localStorage; } catch (e) { return null; } }
  function session() { try { return window.sessionStorage; } catch (e) { return null; } }
  function drop(storage) { try { if (storage) storage.removeItem(KEY); } catch (e) { /* private mode */ } }

  // choices the policy allows, most lasting first
  function options() {
    const { token_store: store, ttl_seconds: cap } = state.policy;
    const top = ceiling[store] === undefined ? 0 : ceiling[store];
    const allowed = CHOICES.filter((choice) => {
      if (top === -2) return choice.id === "none";
      if (top === -1) return choice.seconds < 0;
      if (choice.seconds < 0) return true;
      if (cap > 0) return choice.id !== "always" && choice.seconds < cap;
      return true;
    });
    return cap > 0 && top >= 0 ? [{ id: "ttl", label: humanize(cap), seconds: cap }, ...allowed] : allowed;
  }

  function humanize(seconds) {
    if (seconds % 86400 === 0) return `${seconds / 86400} d`;
    if (seconds % 3600 === 0) return `${seconds / 3600} h`;
    if (seconds % 60 === 0) return `${seconds / 60} min`;
    return `${seconds} s`;
  }

  function defaultChoice() {
    const list = options();
    return list.length ? list[0].id : "none";
  }

  // the stored token, or "" when none is kept, it expired, or the server keeps it (cookie)
  function read() {
    const store = local();
    if (store) {
      try {
        const raw = store.getItem(KEY);
        if (raw) {
          const record = JSON.parse(raw);
          if (record && record.token && (!record.exp || record.exp > Date.now())) return record.token;
          drop(store);
        }
      } catch (e) { drop(store); }
    }
    try { return (session() && session().getItem(KEY)) || ""; } catch (e) { return ""; }
  }

  function clearStored() { drop(local()); drop(session()); }

  async function remember(token, choice) {
    clearStored();
    if (!token) return;
    const list = options();
    const picked = list.find((item) => item.id === choice) || list.find((item) => item.id === defaultChoice());
    const seconds = picked ? picked.seconds : -2;
    if (state.policy.cookie) {
      // the cookie carries the token from now on; nothing readable is kept in the page
      const wanted = seconds > 0 ? seconds : 0;
      const lasting = seconds === 0 || seconds > 0;
      await fetch(SESSION_URL, {
        method: "POST",
        headers: { "X-Kdx-Token": token, "Content-Type": "application/json" },
        body: JSON.stringify({ seconds: wanted, persistent: lasting }),
        cache: "no-store",
      });
      return;
    }
    if (seconds === -2) return;
    try {
      if (seconds === -1) { session().setItem(KEY, token); return; }
      local().setItem(KEY, JSON.stringify({ token, exp: seconds > 0 ? Date.now() + seconds * 1000 : 0 }));
    } catch (e) { /* private mode: the token lives in memory for this page only */ }
  }

  async function forget() {
    clearStored();
    if (state.policy.cookie) {
      try { await fetch(SESSION_URL, { method: "DELETE", cache: "no-store" }); } catch (e) { /* offline */ }
    }
  }

  // true when the browser already holds a valid session cookie
  async function probe() {
    if (!state.policy.cookie) return false;
    try {
      const response = await fetch(SESSION_URL, { cache: "no-store" });
      return response.ok;
    } catch (e) { return false; }
  }

  const ready = fetch(POLICY_URL, { cache: "no-store" })
    .then((response) => (response.ok ? response.json() : null))
    .then((payload) => { if (payload && payload.token_store) state.policy = payload; })
    .catch(() => {});

  window.KdxToken = {
    ready, read, remember, forget, probe, options, defaultChoice,
    get policy() { return state.policy; },
  };
})();
