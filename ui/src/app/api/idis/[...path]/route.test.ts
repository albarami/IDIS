import { afterEach, describe, expect, it, vi } from "vitest";

// The proxy reads the API key from an HttpOnly cookie; provide one so it does not fail closed.
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: () => ({ value: "test-api-key" }),
  }),
}));

async function callProxy(
  url: string,
  init: { method: string; headers: Record<string, string>; body?: BodyInit },
  path: string[],
) {
  const { POST } = await import("./route");
  const { NextRequest } = await import("next/server");
  const request = new NextRequest(url, init);
  return POST(request, { params: Promise.resolve({ path }) });
}

describe("proxy binary upload forwarding (behavioral)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("forwards a non-JSON body to the backend as raw bytes with the original Content-Type", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ doc_id: "d1" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await callProxy(
      "http://localhost/api/idis/v1/deals/d1/documents/upload?filename=f.pdf&doc_type=OTHER",
      {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: new Uint8Array([1, 2, 3, 4, 5]),
      },
      ["v1", "deals", "d1", "documents", "upload"],
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = options.headers as Record<string, string>;
    // Incoming octet-stream content type is preserved (never coerced to application/json).
    expect(headers["Content-Type"]).toBe("application/octet-stream");
    // Body forwarded as raw bytes, not a JSON/text string.
    expect(options.body).toBeInstanceOf(ArrayBuffer);
    expect(typeof options.body).not.toBe("string");
  });

  it("still forwards JSON bodies to the backend as text with application/json", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await callProxy(
      "http://localhost/api/idis/v1/deals",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: "x" }),
      },
      ["v1", "deals"],
    );

    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = options.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBe("application/json");
    expect(typeof options.body).toBe("string");
  });
});
