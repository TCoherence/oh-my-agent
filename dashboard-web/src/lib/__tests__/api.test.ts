import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  AUTH_401_EVENT,
  fetchSessionHistory,
  fetchSessionList,
  fireAutomation,
  getToken,
  hasToken,
  setToken,
} from "@/lib/api";

function stubFetch(status: number, body: unknown) {
  const f = vi.fn(async (_url: string, _opts?: RequestInit) => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => (typeof body === "string" ? body : JSON.stringify(body)),
  }));
  vi.stubGlobal("fetch", f);
  return f;
}

function callArgs(f: ReturnType<typeof stubFetch>): [string, RequestInit] {
  const call = f.mock.calls[0]!;
  return [String(call[0]), (call[1] ?? {}) as RequestInit];
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("token management", () => {
  it("stores, reads, clears the token and fires oma-token-changed", () => {
    const changed = vi.fn();
    window.addEventListener("oma-token-changed", changed);
    expect(hasToken()).toBe(false);

    setToken("abc");
    expect(getToken()).toBe("abc");
    expect(hasToken()).toBe(true);

    setToken("");
    expect(getToken()).toBe("");
    expect(hasToken()).toBe(false);

    expect(changed).toHaveBeenCalledTimes(2);
    window.removeEventListener("oma-token-changed", changed);
  });
});

describe("read requests (apiGet via endpoint helpers)", () => {
  it("builds the URL with query params and parses JSON", async () => {
    const f = stubFetch(200, { items: [], next_cursor: null });
    const res = await fetchSessionList({ limit: 5, cursor: "c1" });
    expect(res.items).toEqual([]);
    const [url] = callArgs(f);
    expect(url).toContain("/api/v1/sessions?");
    expect(url).toContain("limit=5");
    expect(url).toContain("cursor=c1");
  });

  it("injects Authorization header only when a token is set", async () => {
    const noAuth = stubFetch(200, { items: [], next_cursor: null });
    await fetchSessionList({});
    expect((callArgs(noAuth)[1].headers as Record<string, string>).Authorization).toBeUndefined();
    vi.unstubAllGlobals();

    setToken("secret");
    const withAuth = stubFetch(200, { items: [], next_cursor: null });
    await fetchSessionList({});
    expect((callArgs(withAuth)[1].headers as Record<string, string>).Authorization).toBe(
      "Bearer secret",
    );
  });

  it("throws ApiError and dispatches AUTH_401_EVENT on 401", async () => {
    const onAuth = vi.fn();
    window.addEventListener(AUTH_401_EVENT, onAuth);
    stubFetch(401, { detail: "nope" });
    await expect(fetchSessionList({})).rejects.toBeInstanceOf(ApiError);
    expect(onAuth).toHaveBeenCalledOnce();
    window.removeEventListener(AUTH_401_EVENT, onAuth);
  });

  it("throws ApiError carrying the status on a non-2xx", async () => {
    stubFetch(500, "boom");
    await expect(fetchSessionList({})).rejects.toMatchObject({ status: 500 });
  });

  it("URL-encodes path segments", async () => {
    const f = stubFetch(200, []);
    await fetchSessionHistory({ platform: "disc ord", channelId: "c/1", threadId: "t#1" });
    const [url] = callArgs(f);
    expect(url).toContain(encodeURIComponent("disc ord"));
    expect(url).toContain(encodeURIComponent("c/1"));
    expect(url).toContain(encodeURIComponent("t#1"));
  });
});

describe("write requests (apiWrite)", () => {
  it("POSTs fire with the bearer token in the header", async () => {
    setToken("wtok");
    const f = stubFetch(200, { name: "my-job", result: "queued" });
    const res = await fireAutomation("my-job");
    expect(res.result).toBe("queued");
    const [url, opts] = callArgs(f);
    expect(url).toContain("/api/v1/automations/my-job/fire");
    expect(opts.method).toBe("POST");
    expect((opts.headers as Record<string, string>).Authorization).toBe("Bearer wtok");
  });
});
