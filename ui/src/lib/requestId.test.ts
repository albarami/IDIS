import { describe, it, expect } from "vitest";
import {
  generateRequestId,
  isValidUUID,
  getOrGenerateRequestId,
} from "./requestId";

describe("requestId", () => {
  describe("generateRequestId", () => {
    it("generates a valid UUID v4", () => {
      const id = generateRequestId();
      expect(isValidUUID(id)).toBe(true);
    });

    it("generates unique IDs on each call", () => {
      const ids = new Set<string>();
      for (let i = 0; i < 100; i++) {
        ids.add(generateRequestId());
      }
      expect(ids.size).toBe(100);
    });
  });

  describe("isValidUUID", () => {
    it("returns true for valid UUIDs", () => {
      expect(isValidUUID("550e8400-e29b-41d4-a716-446655440000")).toBe(true);
      expect(isValidUUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")).toBe(true);
    });

    it("returns false for invalid UUIDs", () => {
      expect(isValidUUID("not-a-uuid")).toBe(false);
      expect(isValidUUID("550e8400-e29b-41d4-a716")).toBe(false);
      expect(isValidUUID("")).toBe(false);
      expect(isValidUUID("550e8400e29b41d4a716446655440000")).toBe(false);
    });
  });

  describe("getOrGenerateRequestId", () => {
    it("returns existing ID if valid", () => {
      const existingId = "550e8400-e29b-41d4-a716-446655440000";
      expect(getOrGenerateRequestId(existingId)).toBe(existingId);
    });

    it("generates new ID if existing is invalid", () => {
      const result = getOrGenerateRequestId("invalid");
      expect(isValidUUID(result)).toBe(true);
      expect(result).not.toBe("invalid");
    });

    it("generates new ID if existing is null", () => {
      const result = getOrGenerateRequestId(null);
      expect(isValidUUID(result)).toBe(true);
    });

    it("generates new ID if existing is undefined", () => {
      const result = getOrGenerateRequestId(undefined);
      expect(isValidUUID(result)).toBe(true);
    });
  });
});
