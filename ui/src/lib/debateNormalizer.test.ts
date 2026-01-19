import { describe, it, expect } from "vitest";
import { normalizeDebateRound, normalizeDebateRounds } from "./debateNormalizer";

describe("debateNormalizer", () => {
  describe("normalizeDebateRound", () => {
    it("extracts standard fields", () => {
      const round = {
        round_number: 1,
        speaker: "Advocate",
        message: "I propose this deal is strong",
        timestamp: "2024-01-15T10:00:00Z",
      };

      const result = normalizeDebateRound(round, 0);

      expect(result.roundNumber).toBe(1);
      expect(result.speaker).toBe("Advocate");
      expect(result.message).toBe("I propose this deal is strong");
      expect(result.timestamp).toBe("2024-01-15T10:00:00Z");
      expect(result.rawData).toEqual(round);
    });

    it("falls back to index for missing round_number", () => {
      const round = { speaker: "Advocate", message: "Test" };
      const result = normalizeDebateRound(round, 5);
      expect(result.roundNumber).toBe(6); // index + 1
    });

    it("extracts speaker from role field", () => {
      const round = { role: "Sanad Breaker", message: "Test" };
      const result = normalizeDebateRound(round, 0);
      expect(result.speaker).toBe("Sanad Breaker");
    });

    it("extracts speaker from agent field", () => {
      const round = { agent: "Risk Officer", message: "Test" };
      const result = normalizeDebateRound(round, 0);
      expect(result.speaker).toBe("Risk Officer");
    });

    it("defaults to Unknown Speaker if no speaker field", () => {
      const round = { message: "Test" };
      const result = normalizeDebateRound(round, 0);
      expect(result.speaker).toBe("Unknown Speaker");
    });

    it("extracts message from content field", () => {
      const round = { speaker: "Test", content: "This is the content" };
      const result = normalizeDebateRound(round, 0);
      expect(result.message).toBe("This is the content");
    });

    it("extracts message from text field", () => {
      const round = { speaker: "Test", text: "This is the text" };
      const result = normalizeDebateRound(round, 0);
      expect(result.message).toBe("This is the text");
    });

    it("returns empty message if no message field", () => {
      const round = { speaker: "Test" };
      const result = normalizeDebateRound(round, 0);
      expect(result.message).toBe("");
    });

    it("extracts timestamp from created_at field", () => {
      const round = {
        speaker: "Test",
        message: "Test",
        created_at: "2024-01-15T11:00:00Z",
      };
      const result = normalizeDebateRound(round, 0);
      expect(result.timestamp).toBe("2024-01-15T11:00:00Z");
    });

    it("returns undefined timestamp if no timestamp field", () => {
      const round = { speaker: "Test", message: "Test" };
      const result = normalizeDebateRound(round, 0);
      expect(result.timestamp).toBeUndefined();
    });

    it("handles non-object input gracefully", () => {
      const result = normalizeDebateRound(null, 0);
      expect(result.roundNumber).toBe(1);
      expect(result.speaker).toBe("Unknown Speaker");
      expect(result.message).toBe("");
      expect(result.timestamp).toBeUndefined();
    });

    it("handles empty object", () => {
      const result = normalizeDebateRound({}, 2);
      expect(result.roundNumber).toBe(3);
      expect(result.speaker).toBe("Unknown Speaker");
      expect(result.message).toBe("");
      expect(result.timestamp).toBeUndefined();
    });

    it("preserves raw data for fallback", () => {
      const round = { custom_field: "custom_value" };
      const result = normalizeDebateRound(round, 0);
      expect(result.rawData).toEqual(round);
    });
  });

  describe("normalizeDebateRounds", () => {
    it("normalizes array of rounds", () => {
      const rounds = [
        { speaker: "Agent1", message: "First" },
        { speaker: "Agent2", message: "Second" },
        { speaker: "Agent3", message: "Third" },
      ];

      const results = normalizeDebateRounds(rounds);

      expect(results).toHaveLength(3);
      expect(results[0].speaker).toBe("Agent1");
      expect(results[0].message).toBe("First");
      expect(results[0].roundNumber).toBe(1);
      expect(results[1].speaker).toBe("Agent2");
      expect(results[1].message).toBe("Second");
      expect(results[1].roundNumber).toBe(2);
      expect(results[2].speaker).toBe("Agent3");
      expect(results[2].message).toBe("Third");
      expect(results[2].roundNumber).toBe(3);
    });

    it("handles empty array", () => {
      const results = normalizeDebateRounds([]);
      expect(results).toHaveLength(0);
    });

    it("handles mixed format rounds", () => {
      const rounds = [
        { round_number: 1, speaker: "A", message: "First" },
        { role: "B", content: "Second" },
        { agent: "C", text: "Third", created_at: "2024-01-15T10:00:00Z" },
      ];

      const results = normalizeDebateRounds(rounds);

      expect(results).toHaveLength(3);
      expect(results[0].roundNumber).toBe(1);
      expect(results[0].speaker).toBe("A");
      expect(results[0].message).toBe("First");
      expect(results[1].roundNumber).toBe(2);
      expect(results[1].speaker).toBe("B");
      expect(results[1].message).toBe("Second");
      expect(results[2].roundNumber).toBe(3);
      expect(results[2].speaker).toBe("C");
      expect(results[2].message).toBe("Third");
      expect(results[2].timestamp).toBe("2024-01-15T10:00:00Z");
    });
  });
});
