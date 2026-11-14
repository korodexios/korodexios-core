// Live bus events (GET /api/v1/ui/events, server-sent events) read with fetch, so the token
// header or the session cookie authenticates the stream; EventSource can do neither.
import { request } from "./api";

export const EVENTS_URL = "/api/v1/ui/events";
const RETRY_MS = 3000;
const DEBOUNCE_MS = 250;

export interface BusEvent {
  name: string;
  source: string;
  at: number;
  sequence: number;
  payload: unknown;
}

/** True when the event name starts with one of the prefixes ("*" matches every event). */
export function matches(name: string, prefixes: readonly string[]): boolean {
  return prefixes.some((prefix) => prefix === "*" || name.startsWith(prefix));
}

/** Feed decoded events from an SSE body to *onEvent* until the stream ends. */
export async function readFrames(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: BusEvent) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    pending += decoder.decode(value, { stream: true });
    const frames = pending.split("\n\n");
    pending = frames.pop() ?? "";
    for (const frame of frames) {
      const data = frame
        .split("\n")
        .filter((line) => line.startsWith("data: "))
        .map((line) => line.slice(6))
        .join("\n");
      if (!data) continue; // a comment (": keepalive") or the retry hint
      try {
        onEvent(JSON.parse(data) as BusEvent);
      } catch {
        /* a malformed frame is skipped */
      }
    }
  }
}

const pause = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const timer = window.setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      resolve();
    });
  });

export interface EventsOptions {
  retryMs?: number;
  onState?: (connected: boolean) => void;
}

/** Stream every bus event to *onEvent*, reconnecting after a drop. Returns a stop function. */
export function events(onEvent: (event: BusEvent) => void, options: EventsOptions = {}): () => void {
  const controller = new AbortController();
  const { signal } = controller;
  const run = async () => {
    while (!signal.aborted) {
      try {
        const response = await request(EVENTS_URL, { signal });
        if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
        options.onState?.(true);
        await readFrames(response.body, onEvent);
      } catch {
        /* the daemon stopped, the network dropped, or we were stopped */
      }
      if (signal.aborted) return;
      options.onState?.(false);
      await pause(options.retryMs ?? RETRY_MS, signal);
    }
  };
  void run();
  return () => controller.abort();
}

// One shared stream for every panel that declares refresh_on: it opens with the first
// subscriber and closes with the last.
const subscribers = new Set<{ prefixes: readonly string[]; fire: () => void }>();
let stop: (() => void) | null = null;

/**
 * Call *callback* (debounced) whenever a bus event whose name matches *prefixes* arrives.
 * This is what a panel's refresh_on means. Returns the function that unsubscribes.
 */
export function onTopics(prefixes: readonly string[], callback: () => void, debounceMs = DEBOUNCE_MS): () => void {
  if (!prefixes.length) return () => undefined;
  let timer: number | undefined;
  const entry = {
    prefixes,
    fire: () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(callback, debounceMs);
    },
  };
  subscribers.add(entry);
  stop ??= events((event) => {
    subscribers.forEach((item) => {
      if (matches(event.name, item.prefixes)) item.fire();
    });
  });
  return () => {
    window.clearTimeout(timer);
    subscribers.delete(entry);
    if (!subscribers.size && stop) {
      stop();
      stop = null;
    }
  };
}
