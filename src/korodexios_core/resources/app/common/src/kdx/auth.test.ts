import { afterEach, beforeEach, expect, test, vi } from "vitest";

// auth.ts keeps its state in the module: every test loads a fresh copy.
async function load() {
  vi.resetModules();
  const auth = await import("./auth");
  const api = await import("./api");
  return { auth, api };
}

type Reply = { status: number; body?: unknown };
let calls: { url: string; headers: Record<string, string>; method: string }[] = [];

function serve(route: (url: string, headers: Record<string, string>) => Reply) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const headers = (init.headers ?? {}) as Record<string, string>;
      calls.push({ url: String(url), headers, method: init.method ?? "GET" });
      const reply = route(String(url), headers);
      return new Response(JSON.stringify(reply.body ?? {}), { status: reply.status });
    }),
  );
}

const policy = (cookie: boolean) => ({ status: 200, body: { token_store: "always", ttl_seconds: 0, cookie } });

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  history.replaceState(null, "", "/app/");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.body.replaceChildren();
});

test("no token: no header at all, not an empty one", async () => {
  serve((url) => (url.endsWith("token-policy") ? policy(false) : { status: 200, body: { ok: true } }));
  const { auth, api } = await load();
  expect(await auth.initAuth()).toBe("none");
  await api.api("/api/v1/status");
  const last = calls[calls.length - 1];
  expect(last.headers).not.toHaveProperty(auth.TOKEN_HEADER);
});

test("a stale kept token is dropped after a 401 and the cookie answers the retry", async () => {
  localStorage.setItem("kdx-console-token", JSON.stringify({ token: "old", exp: 0 }));
  serve((url, headers) => {
    if (url.endsWith("token-policy")) return policy(true);
    if (url.endsWith("/api/v1/status")) {
      return headers["X-Kdx-Token"] ? { status: 401, body: { error: "bad token" } } : { status: 200, body: { app: { state: "running" } } };
    }
    return { status: 200 };
  });
  const { auth, api } = await load();
  expect(await auth.initAuth()).toBe("token");
  const reply = await api.api<{ app: { state: string } }>("/api/v1/status");
  expect(reply.app.state).toBe("running");
  const status = calls.filter((call) => call.url.endsWith("/api/v1/status"));
  expect(status.map((call) => call.headers["X-Kdx-Token"] ?? "")).toEqual(["old", ""]);
  expect(localStorage.getItem("kdx-console-token")).toBeNull();
  expect(auth.mode()).toBe("cookie");
  await api.api("/api/v1/status");
  expect(calls[calls.length - 1].headers).not.toHaveProperty("X-Kdx-Token"); // never sent again
});

test("a refused request without a header is not retried", async () => {
  serve((url) => (url.endsWith("token-policy") ? policy(false) : { status: 401, body: { error: "no" } }));
  const { auth, api } = await load();
  await auth.initAuth();
  await expect(api.api("/api/v1/status")).rejects.toMatchObject({ status: 401 });
  expect(calls.filter((call) => call.url.endsWith("/api/v1/status"))).toHaveLength(1);
});

test("#token= is read once and removed from the address", async () => {
  history.replaceState(null, "", "/app/?x=1#token=abc&tab=queue");
  serve((url) => (url.endsWith("token-policy") ? policy(false) : { status: 200 }));
  const { auth } = await load();
  expect(await auth.initAuth()).toBe("token");
  expect(window.location.hash).toBe("#tab=queue");
  expect(window.location.search).toBe("?x=1");
  expect(auth.authHeaders()).toEqual({ "X-Kdx-Token": "abc" });
});

test("with cookie sessions a typed token becomes the cookie and nothing readable stays", async () => {
  serve((url) => (url.endsWith("token-policy") ? policy(true) : { status: 200 }));
  const { auth } = await load();
  await auth.initAuth();
  expect(await auth.signIn("secret")).toBe("cookie");
  const post = calls.find((call) => call.method === "POST");
  expect(post?.url).toBe(auth.SESSION_URL);
  expect(post?.headers["X-Kdx-Token"]).toBe("secret");
  expect(localStorage.getItem("kdx-console-token")).toBeNull();
  expect(auth.authHeaders()).toEqual({});
});

function frame(): HTMLIFrameElement {
  const element = document.createElement("iframe");
  document.body.append(element);
  return element;
}

test("framed: one ask outstanding, wrong senders ignored, cookie answer understood", async () => {
  serve(() => ({ status: 200 }));
  const { auth } = await load();
  const host = frame().contentWindow as Window;
  const stranger = frame().contentWindow as Window;
  vi.spyOn(window, "parent", "get").mockReturnValue(host);
  const asked = vi.spyOn(host, "postMessage");
  const first = auth.askHost(1000);
  const second = auth.askHost(1000);
  expect(second).toBe(first);
  expect(asked).toHaveBeenCalledTimes(1);
  expect(asked).toHaveBeenCalledWith({ type: auth.EMBED_READY }, window.location.origin);
  const answer = { type: auth.EMBED_TOKEN, version: 1, token: "", cookie: true };
  window.dispatchEvent(new MessageEvent("message", { data: answer, origin: window.location.origin, source: stranger }));
  window.dispatchEvent(new MessageEvent("message", { data: answer, origin: "http://evil.test", source: host }));
  window.dispatchEvent(new MessageEvent("message", { data: answer, origin: window.location.origin, source: host }));
  expect(await first).toEqual({ token: "", cookie: true });
  expect(auth.acceptAnswer({ token: "", cookie: true })).toBe("cookie");
});

test("framed: an empty answer without cookie means signed out; a refused token is not taken again", async () => {
  serve((_url, headers) => (headers["X-Kdx-Token"] ? { status: 401 } : { status: 200 }));
  const { auth, api } = await load();
  const host = frame().contentWindow as Window;
  vi.spyOn(window, "parent", "get").mockReturnValue(host);
  const reply = (token: string, cookie: boolean) =>
    window.dispatchEvent(new MessageEvent("message", {
      data: { type: auth.EMBED_TOKEN, token, cookie }, origin: window.location.origin, source: host,
    }));
  let pending = auth.askHost(1000);
  reply("", false);
  expect(auth.acceptAnswer((await pending)!)).toBe("none");
  pending = auth.askHost(1000);
  reply("bad", false);
  auth.acceptAnswer((await pending)!);
  await api.request("/api/v1/status"); // 401 with the header: "bad" is refused
  pending = auth.askHost(1000);
  reply("bad", false);
  expect(await pending).toEqual({ token: "", cookie: false });
});

test("the host answers only its own frame, with protocol v1", async () => {
  serve((url) => (url.endsWith("token-policy") ? policy(false) : { status: 200 }));
  history.replaceState(null, "", "/app/#token=t1");
  const { auth } = await load();
  await auth.initAuth();
  const own = frame();
  const other = frame();
  const sent = vi.spyOn(own.contentWindow as Window, "postMessage");
  const stop = auth.serveEmbed(own);
  const ask = { type: auth.EMBED_READY };
  window.dispatchEvent(new MessageEvent("message", { data: ask, origin: window.location.origin, source: other.contentWindow }));
  expect(sent).not.toHaveBeenCalled();
  window.dispatchEvent(new MessageEvent("message", { data: ask, origin: window.location.origin, source: own.contentWindow }));
  expect(sent).toHaveBeenCalledWith(
    { type: auth.EMBED_TOKEN, version: 1, token: "t1", cookie: false },
    window.location.origin,
  );
  stop();
});
