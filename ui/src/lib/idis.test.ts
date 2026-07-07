import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { IDISApiError } from "./idis";

describe("IDISApiError", () => {
  it("creates error with all fields", () => {
    const error = new IDISApiError(400, {
      code: "bad_request",
      message: "Invalid field",
      details: { field: "name" },
      request_id: "req_123",
    });

    expect(error.name).toBe("IDISApiError");
    expect(error.message).toBe("Invalid field");
    expect(error.code).toBe("bad_request");
    expect(error.status).toBe(400);
    expect(error.requestId).toBe("req_123");
    expect(error.details).toEqual({ field: "name" });
  });

  it("creates error with minimal fields", () => {
    const error = new IDISApiError(401, {
      code: "unauthorized",
      message: "Not authenticated",
    });

    expect(error.code).toBe("unauthorized");
    expect(error.message).toBe("Not authenticated");
    expect(error.status).toBe(401);
    expect(error.requestId).toBeUndefined();
    expect(error.details).toBeUndefined();
  });

  it("is instanceof Error", () => {
    const error = new IDISApiError(500, {
      code: "internal_error",
      message: "Server error",
    });

    expect(error).toBeInstanceOf(Error);
    expect(error).toBeInstanceOf(IDISApiError);
  });
});

describe("idis client error handling", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("surfaces request_id from error response", async () => {
    const mockResponse = {
      ok: false,
      status: 400,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => ({
        code: "bad_request",
        message: "Invalid input",
        request_id: "req_abc123",
      }),
    };

    global.fetch = vi.fn().mockResolvedValue(mockResponse);

    // Import the module dynamically to pick up the mocked fetch
    const { default: idis } = await import("./idis");

    try {
      await idis.deals.list();
      expect.fail("Should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(IDISApiError);
      const apiError = err as IDISApiError;
      expect(apiError.requestId).toBe("req_abc123");
      expect(apiError.code).toBe("bad_request");
    }
  });

  it("handles 401 unauthorized response", async () => {
    const mockResponse = {
      ok: false,
      status: 401,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => ({
        code: "unauthorized",
        message: "Not authenticated",
      }),
    };

    global.fetch = vi.fn().mockResolvedValue(mockResponse);

    const { default: idis } = await import("./idis");

    try {
      await idis.deals.list();
      expect.fail("Should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(IDISApiError);
      const apiError = err as IDISApiError;
      expect(apiError.status).toBe(401);
      expect(apiError.code).toBe("unauthorized");
    }
  });
});

describe("idis.documents.upload transport", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("POSTs the raw file as octet-stream (not JSON), preserving the exact Blob body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => ({ doc_id: "d1", deal_id: "deal-1", doc_type: "PITCH_DECK" }),
    });
    global.fetch = fetchMock;

    const { default: idis } = await import("./idis");
    const file = new File(["raw-file-bytes"], "deck.pdf", { type: "application/pdf" });

    await idis.documents.upload("deal-1", file, { filename: "deck.pdf", docType: "PITCH_DECK" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    // URL: correct upload path + query params.
    expect(url).toContain("/api/idis/v1/deals/deal-1/documents/upload");
    expect(url).toContain("filename=deck.pdf");
    expect(url).toContain("doc_type=PITCH_DECK");
    // Method + transport headers.
    expect(options.method).toBe("POST");
    expect((options.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/octet-stream",
    );
    expect(options.credentials).toBe("include");
    // Body is the EXACT File object — never JSON-stringified or coerced to text.
    expect(options.body).toBe(file);
    expect(typeof options.body).not.toBe("string");
  });
});
