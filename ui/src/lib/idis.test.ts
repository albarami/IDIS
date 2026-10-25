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

describe("idis methodology listing and FULL run transport", () => {
  const CDD_ID = "9f8e7d6c-5b4a-43c2-9182-736455667788";
  const FDD_ID = "1a2b3c4d-5e6f-47a8-9b0c-1d2e3f4a5b6c";
  const originalFetch = global.fetch;

  function jsonResponse(status: number, body: unknown): Response {
    return new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    });
  }

  function safeRecord(id: string, type: string) {
    return {
      methodology_version_record_id: id,
      methodology_version_id: "mv-" + id.slice(0, 8),
      methodology_type: type,
      version_label: type + " v1",
      source_sha256: "0".repeat(64),
      source_byte_count: 10,
      source_contract_hash: "1".repeat(64),
      source_trace_digest: "2".repeat(64),
      registry_hash: "3".repeat(64),
      version_hash: "4".repeat(64),
      question_count: 5,
      source_section_count: 2,
      header_row: null,
      status: "FROZEN",
      imported_at: "2026-01-01T00:00:00Z",
      frozen_at: "2026-01-02T00:00:00Z",
    };
  }

  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("GETs /v1/methodology-versions with credentials and a request id", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, [safeRecord(CDD_ID, "commercial_dd")]));
    global.fetch = fetchMock;

    const { default: idis } = await import("./idis");
    const rows = await idis.methodology.list();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, options] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("/api/idis/v1/methodology-versions");
    expect(options?.method ?? "GET").toBe("GET");
    expect(options?.credentials).toBe("include");
    expect(new Headers(options?.headers).get("X-Request-Id")).toBeTruthy();
    expect(rows).toHaveLength(1);
    expect(rows[0].methodology_version_record_id).toBe(CDD_ID);
  });

  it("passes the methodology_type filter through the query string", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(() => Promise.resolve(jsonResponse(200, [])));
    global.fetch = fetchMock;

    const { default: idis } = await import("./idis");
    await idis.methodology.list({ methodology_type: "commercial_dd" });
    expect(String(fetchMock.mock.calls[0][0])).toBe(
      "/api/idis/v1/methodology-versions?methodology_type=commercial_dd",
    );

    await idis.methodology.list({ methodology_type: "financial_dd" });
    expect(String(fetchMock.mock.calls[1][0])).toBe(
      "/api/idis/v1/methodology-versions?methodology_type=financial_dd",
    );
  });

  it("surfaces catalog failures as IDISApiError with the request id", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(503, {
        code: "unavailable",
        message: "catalog down",
        request_id: "req-cat-503",
      }),
    );
    global.fetch = fetchMock;

    const { default: idis } = await import("./idis");
    const failure = await idis.methodology.list().then(
      () => null,
      (err: unknown) => err,
    );
    expect(failure).toBeInstanceOf(IDISApiError);
    if (failure instanceof IDISApiError) {
      expect(failure.status).toBe(503);
      expect(failure.requestId).toBe("req-cat-503");
    }
  });

  it("POSTs the exact FULL body with methodology_selection and idempotency key", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(202, { run_id: "run-1", status: "QUEUED" }));
    global.fetch = fetchMock;

    const { default: idis } = await import("./idis");
    await idis.runs.start(
      "deal-9",
      {
        mode: "FULL",
        methodology_selection: {
          commercial_dd_version_record_id: CDD_ID,
          financial_dd_version_record_id: FDD_ID,
        },
      },
      "idem-key-77",
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, options] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("/api/idis/v1/deals/deal-9/runs");
    expect(options?.method).toBe("POST");
    const headers = new Headers(options?.headers);
    expect(headers.get("Idempotency-Key")).toBe("idem-key-77");
    expect(JSON.parse(String(options?.body))).toEqual({
      mode: "FULL",
      methodology_selection: {
        commercial_dd_version_record_id: CDD_ID,
        financial_dd_version_record_id: FDD_ID,
      },
    });
  });

  it("keeps SNAPSHOT bodies free of methodology fields", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(202, { run_id: "run-2", status: "QUEUED" }));
    global.fetch = fetchMock;

    const { default: idis } = await import("./idis");
    await idis.runs.start("deal-9", { mode: "SNAPSHOT" });

    const [, options] = fetchMock.mock.calls[0];
    expect(JSON.parse(String(options?.body))).toEqual({ mode: "SNAPSHOT" });
  });
});
