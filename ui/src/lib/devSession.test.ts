import { describe, expect, it } from "vitest";
import {
  DEV_API_KEY_ENV,
  evaluateDevSessionRequest,
  isLocalhostHost,
} from "./devSession";

describe("dev session guard", () => {
  it("allows local dev bypass for localhost with configured dev API key", () => {
    const decision = evaluateDevSessionRequest({
      nodeEnv: "development",
      bypassEnabled: "true",
      host: "localhost:3000",
      devApiKey: "test-key-not-real",
    });

    expect(decision).toEqual({ allowed: true, apiKey: "test-key-not-real" });
  });

  it("rejects production even when bypass and dev API key are configured", () => {
    const decision = evaluateDevSessionRequest({
      nodeEnv: "production",
      bypassEnabled: "true",
      host: "localhost:3000",
      devApiKey: "test-key-not-real",
    });

    expect(decision).toEqual({
      allowed: false,
      status: 404,
      reason: "disabled_in_production",
    });
  });

  it("rejects non-localhost hosts", () => {
    const decision = evaluateDevSessionRequest({
      nodeEnv: "development",
      bypassEnabled: "true",
      host: "idis.example.com",
      devApiKey: "test-key-not-real",
    });

    expect(decision).toEqual({
      allowed: false,
      status: 403,
      reason: "non_localhost_host",
    });
  });

  it("rejects missing dev API key", () => {
    const decision = evaluateDevSessionRequest({
      nodeEnv: "development",
      bypassEnabled: "true",
      host: "127.0.0.1:3000",
      devApiKey: "",
    });

    expect(decision).toEqual({
      allowed: false,
      status: 404,
      reason: "missing_dev_api_key",
    });
  });

  it("recognizes localhost, 127.0.0.1, and ::1 only", () => {
    expect(isLocalhostHost("localhost:3000")).toBe(true);
    expect(isLocalhostHost("127.0.0.1:3000")).toBe(true);
    expect(isLocalhostHost("[::1]:3000")).toBe(true);
    expect(isLocalhostHost("example.localhost.evil.com")).toBe(false);
  });

  it("keeps the dev API key in a server-only env name", () => {
    expect(DEV_API_KEY_ENV).toBe("IDIS_DEV_API_KEY");
    expect(DEV_API_KEY_ENV.startsWith("NEXT_PUBLIC_")).toBe(false);
  });
});
