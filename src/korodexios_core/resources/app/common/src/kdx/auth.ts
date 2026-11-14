// Who this app is to the daemon: a token sent as X-Kdx-Token, the HttpOnly session cookie, or
// nobody. Framework-neutral and shared by every app tier; the built-in shell follows the same
// rules (token-store.js) and the embed handshake is protocol v1 of kernel/ui_embed.py.
//
//   - the header goes out only while a token is held; an empty header is never sent
//   - a 401 to a request that carried the header drops that token (it is never sent again) and
//     the request is retried once without it, so a valid session cookie can answer
//   - "#token=" is read once and removed from the address bar
//   - inside an embed panel the page asks its host once, with one listener and at most one
//     question outstanding

export const TOKEN_HEADER = "X-Kdx-Token";
export const POLICY_URL = "/api/v1/ui/token-policy";
export const SESSION_URL = "/api/v1/ui/session";
export const EMBED_READY = "kdx-embed-ready";
export const EMBED_TOKEN = "kdx-embed-token";
export const EMBED_PROTOCOL = 1;

// the entry the built-in console keeps, so one sign-in serves /ui/ and /app/ on the same origin
const KEY = "kdx-console-token";
const ASK_TIMEOUT_MS = 3000;

export type TokenStore = "always" | "ttl" | "tab" | "none";
export type AuthMode = "token" | "cookie" | "none";

export interface TokenPolicy {
  token_store: TokenStore;
  ttl_seconds: number;
  cookie: boolean;
}

export interface EmbedAnswer {
  token: string;
  cookie: boolean;
}

const state = {
  token: "",
  cookie: false,
  refused: new Set<string>(),
  policy: { token_store: "always", ttl_seconds: 0, cookie: false } as TokenPolicy,
  asking: null as Promise<EmbedAnswer | null> | null,
};
const listeners = new Set<(mode: AuthMode) => void>();

function storage(kind: "local" | "session"): Storage | null {
  try {
    return kind === "local" ? window.localStorage : window.sessionStorage;
  } catch {
    return null; // private mode or a sandbox without storage
  }
}

function drop(store: Storage | null): void {
  try {
    store?.removeItem(KEY);
  } catch {
    /* nothing to drop */
  }
}

function readStored(): string {
  const local = storage("local");
  try {
    const raw = local?.getItem(KEY);
    if (raw) {
      const record = JSON.parse(raw) as { token?: string; exp?: number };
      if (record.token && (!record.exp || record.exp > Date.now())) return record.token;
      drop(local);
    }
  } catch {
    drop(local);
  }
  try {
    return storage("session")?.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

function clearStored(): void {
  drop(storage("local"));
  drop(storage("session"));
}

function keep(token: string): void {
  const { token_store: store, ttl_seconds: ttl } = state.policy;
  try {
    if (store === "tab") storage("session")?.setItem(KEY, token);
    else if (store === "always" || store === "ttl") {
      const exp = ttl > 0 ? Date.now() + ttl * 1000 : 0;
      storage("local")?.setItem(KEY, JSON.stringify({ token, exp }));
    }
  } catch {
    /* the token lives in memory for this page only */
  }
}

function notify(): void {
  const current = mode();
  listeners.forEach((listener) => listener(current));
}

/** The token the address carried ("#token=..."), removed from the address bar on the way. */
export function takeHashToken(): string {
  const hash = new URLSearchParams(window.location.hash.slice(1));
  const token = hash.get("token") ?? "";
  if (!token) return "";
  hash.delete("token");
  const rest = hash.toString();
  history.replaceState(null, "", window.location.pathname + window.location.search + (rest ? `#${rest}` : ""));
  return token;
}

export function mode(): AuthMode {
  return state.token ? "token" : state.cookie ? "cookie" : "none";
}

export function policy(): TokenPolicy {
  return state.policy;
}

export function hasToken(): boolean {
  return state.token !== "";
}

/** Headers for a request: the token header only while a token is held. */
export function authHeaders(): Record<string, string> {
  return state.token ? { [TOKEN_HEADER]: state.token } : {};
}

/** Called after a 401 to a request that carried the header: forget that token for good. */
export function refuse(): void {
  if (!state.token) return;
  state.refused.add(state.token);
  if (readStored() === state.token) clearStored();
  state.token = "";
  state.cookie = state.policy.cookie; // the cookie, if the browser has one, may answer the retry
  notify();
}

export function onAuthChange(listener: (mode: AuthMode) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

async function loadPolicy(): Promise<void> {
  try {
    const response = await fetch(POLICY_URL, { cache: "no-store" });
    if (response.ok) {
      const payload = (await response.json()) as Partial<TokenPolicy>;
      if (payload.token_store) state.policy = { ...state.policy, ...payload };
    }
  } catch {
    /* an older daemon: keep the defaults */
  }
}

async function probeCookie(): Promise<boolean> {
  if (!state.policy.cookie) return false;
  try {
    return (await fetch(SESSION_URL, { cache: "no-store", credentials: "same-origin" })).ok;
  } catch {
    return false;
  }
}

/** True inside an iframe whose parent is a page of this same origin. */
export function framed(): boolean {
  try {
    return window.parent !== window && window.parent.location.origin === window.location.origin;
  } catch {
    return false; // a parent of another origin cannot be read and is never asked
  }
}

/**
 * Ask the embedding page (the shell or an app) for the session, protocol v1. One listener and
 * one question at a time: a second call while the first waits gets the same promise. A token the
 * server already refused is never taken again. Resolves null when nobody answers in time.
 */
export function askHost(timeoutMs = ASK_TIMEOUT_MS): Promise<EmbedAnswer | null> {
  if (state.asking) return state.asking;
  const host = window.parent;
  state.asking = new Promise<EmbedAnswer | null>((resolve) => {
    const finish = (answer: EmbedAnswer | null) => {
      window.removeEventListener("message", listen);
      window.clearTimeout(timer);
      state.asking = null;
      resolve(answer);
    };
    const listen = (event: MessageEvent) => {
      if (event.source !== host || event.origin !== window.location.origin) return;
      const data = event.data as { type?: string; token?: unknown; cookie?: unknown } | null;
      if (!data || data.type !== EMBED_TOKEN) return;
      const token = typeof data.token === "string" ? data.token : "";
      finish({ token: state.refused.has(token) ? "" : token, cookie: data.cookie === true });
    };
    const timer = window.setTimeout(() => finish(null), timeoutMs);
    window.addEventListener("message", listen);
    host.postMessage({ type: EMBED_READY }, window.location.origin);
  });
  return state.asking;
}

/** Take a session handed over by an embedding page; the token stays in memory only. */
export function acceptAnswer(answer: EmbedAnswer): AuthMode {
  state.token = answer.token;
  state.cookie = !answer.token && answer.cookie;
  notify();
  return mode();
}

/**
 * Work out who we are, once, before the first request: a token in the address, the embedding
 * page, a kept token, then the session cookie. Returns the mode the app starts in.
 */
export async function initAuth(): Promise<AuthMode> {
  await loadPolicy();
  const fromHash = takeHashToken();
  if (fromHash) {
    await signIn(fromHash);
    return mode();
  }
  if (framed()) {
    const answer = await askHost();
    if (answer && (answer.token || answer.cookie)) return acceptAnswer(answer);
  }
  const kept = readStored();
  if (kept && !state.refused.has(kept)) state.token = kept;
  else state.cookie = await probeCookie();
  notify();
  return mode();
}

/**
 * Sign in with a token typed or pasted by the person. With cookie sessions on, the token is
 * traded for the HttpOnly cookie and nothing readable stays in the page; otherwise it is kept
 * as the server's policy allows.
 */
export async function signIn(token: string): Promise<AuthMode> {
  const value = token.trim();
  clearStored();
  state.refused.delete(value);
  if (!value) {
    state.token = "";
    notify();
    return mode();
  }
  if (state.policy.cookie) {
    const response = await fetch(SESSION_URL, {
      method: "POST",
      headers: { [TOKEN_HEADER]: value, "Content-Type": "application/json" },
      body: JSON.stringify({ seconds: state.policy.ttl_seconds, persistent: state.policy.token_store !== "tab" }),
      cache: "no-store",
      credentials: "same-origin",
    }).catch(() => null);
    if (response?.ok) {
      state.token = "";
      state.cookie = true;
      notify();
      return mode();
    }
  }
  state.token = value;
  keep(value);
  notify();
  return mode();
}

/** Forget the token here and, with cookie sessions, end the cookie session too. */
export async function signOut(): Promise<void> {
  clearStored();
  state.token = "";
  if (state.policy.cookie) {
    await fetch(SESSION_URL, { method: "DELETE", cache: "no-store", credentials: "same-origin" }).catch(() => null);
  }
  state.cookie = false;
  notify();
}

/**
 * Be the host of an embed panel: answer the framed page's ask with this app's session. Only
 * that frame on this origin is answered. Returns a function that stops listening.
 */
export function serveEmbed(frame: HTMLIFrameElement): () => void {
  const listen = (event: MessageEvent) => {
    if (!frame.isConnected) {
      window.removeEventListener("message", listen);
      return;
    }
    if (event.source !== frame.contentWindow || event.origin !== window.location.origin) return;
    const data = event.data as { type?: string } | null;
    if (!data || data.type !== EMBED_READY) return;
    frame.contentWindow?.postMessage(
      { type: EMBED_TOKEN, version: EMBED_PROTOCOL, token: state.token, cookie: !state.token && state.cookie },
      window.location.origin,
    );
  };
  window.addEventListener("message", listen);
  return () => window.removeEventListener("message", listen);
}
