/**
 * Debate transcript normalization utilities.
 * Normalizes debate rounds from various possible shapes to a consistent display format.
 * OpenAPI-safe: does not assume fields, uses best-effort extraction.
 */

export interface NormalizedRound {
  roundNumber: number;
  speaker: string;
  message: string;
  timestamp?: string;
  rawData: unknown;
}

/**
 * Normalize a single debate round to extract displayable fields.
 * Falls back gracefully if expected fields are missing.
 */
export function normalizeDebateRound(
  round: unknown,
  index: number
): NormalizedRound {
  const roundObj = round as Record<string, unknown>;

  // Best-effort field extraction
  const roundNumber =
    typeof roundObj?.round_number === "number"
      ? roundObj.round_number
      : index + 1;

  const speaker =
    typeof roundObj?.speaker === "string"
      ? roundObj.speaker
      : typeof roundObj?.role === "string"
      ? roundObj.role
      : typeof roundObj?.agent === "string"
      ? roundObj.agent
      : "Unknown Speaker";

  const message =
    typeof roundObj?.message === "string"
      ? roundObj.message
      : typeof roundObj?.content === "string"
      ? roundObj.content
      : typeof roundObj?.text === "string"
      ? roundObj.text
      : "";

  const timestamp =
    typeof roundObj?.timestamp === "string"
      ? roundObj.timestamp
      : typeof roundObj?.created_at === "string"
      ? roundObj.created_at
      : undefined;

  return {
    roundNumber,
    speaker,
    message,
    timestamp,
    rawData: round,
  };
}

/**
 * Normalize an array of debate rounds.
 */
export function normalizeDebateRounds(rounds: unknown[]): NormalizedRound[] {
  return rounds.map((round, index) => normalizeDebateRound(round, index));
}
