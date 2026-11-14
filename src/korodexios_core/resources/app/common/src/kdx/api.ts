// The control-plane client every app tier shares: one request path, so the auth rules in
// auth.ts hold for JSON calls, the event stream and media alike.
import { authHeaders, hasToken, refuse } from "./auth";
import { emptyPrefs } from "./types";
import type { CommandSpec, Manifest, Prefs, Status } from "./types";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export interface CallInit {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
}

/**
 * Send one request to the daemon. A 401 to a request that carried the token header drops that
 * token and tries once more without it, so a valid session cookie answers instead of the stale
 * token shadowing it.
 */
export async function request(path: string, init: CallInit = {}): Promise<Response> {
  const hasBody = init.body !== undefined;
  const send = () =>
    fetch(path, {
      method: init.method ?? "GET",
      headers: { ...authHeaders(), ...(hasBody ? { "Content-Type": "application/json" } : {}) },
      body: hasBody ? JSON.stringify(init.body) : undefined,
      cache: "no-store",
      credentials: "same-origin",
      signal: init.signal,
    });
  const carried = hasToken();
  const response = await send();
  if (response.status !== 401 || !carried) return response;
  refuse();
  return send();
}

export async function api<T>(path: string, init: CallInit = {}): Promise<T> {
  const response = await request(path, init);
  const payload = (await response.json().catch(() => ({}))) as T & { error?: string };
  if (!response.ok) {
    throw new ApiError(response.status, payload.error ?? `HTTP ${response.status}`);
  }
  return payload;
}

const withLocale = (path: string, locale: string) =>
  locale ? `${path}?locale=${encodeURIComponent(locale)}` : path;

export const status = () => api<Status>("/api/v1/status");

export const manifest = (locale = "") => api<Manifest>(withLocale("/api/v1/ui/manifest", locale));

export const prefs = async (): Promise<Prefs> => ({ ...emptyPrefs(), ...(await api<Prefs>("/api/v1/ui/prefs")) });

export const savePrefs = async (changes: Record<string, unknown>): Promise<Prefs> => ({
  ...emptyPrefs(),
  ...(await api<Prefs>("/api/v1/ui/prefs", { method: "PATCH", body: changes })),
});

export const catalog = async (): Promise<CommandSpec[]> =>
  (await api<{ commands?: CommandSpec[] }>("/api/v1/ui/catalog")).commands ?? [];

export const messages = async (locale = ""): Promise<Record<string, string>> =>
  (await api<{ messages?: Record<string, string> }>(withLocale("/api/v1/ui/i18n", locale))).messages ?? {};

/** Read a dotted path ("tasks", "report.rows") out of a JSON answer. */
export function dig(value: unknown, path: string): unknown {
  if (!path) return value;
  return path.split(".").reduce<unknown>((node, key) => {
    return node !== null && typeof node === "object"
      ? (node as Record<string, unknown>)[key]
      : undefined;
  }, value);
}
