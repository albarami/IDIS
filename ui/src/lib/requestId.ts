/**
 * UUID generation helpers for X-Request-Id tracking.
 * Every backend request must include a unique request ID for audit trails.
 */

/**
 * Generate a UUID v4 for request tracking.
 * Uses crypto.randomUUID() when available (Node.js 19+, modern browsers).
 */
export function generateRequestId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // Fallback for older environments
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

/**
 * Validate that a string is a valid UUID format.
 */
export function isValidUUID(str: string): boolean {
  const uuidRegex =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  return uuidRegex.test(str);
}

/**
 * Get or generate a request ID from headers.
 * If X-Request-Id exists and is valid, use it; otherwise generate a new one.
 */
export function getOrGenerateRequestId(
  existingId: string | null | undefined
): string {
  if (existingId && isValidUUID(existingId)) {
    return existingId;
  }
  return generateRequestId();
}
