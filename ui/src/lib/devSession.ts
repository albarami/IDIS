export const DEV_AUTH_BYPASS_ENV = "IDIS_UI_DEV_AUTH_BYPASS";
export const DEV_API_KEY_ENV = "IDIS_DEV_API_KEY";
export const SESSION_COOKIE_NAME_ENV = "IDIS_SESSION_COOKIE_NAME";

export type DevSessionDecision =
  | { allowed: true; apiKey: string }
  | { allowed: false; status: 403 | 404; reason: string };

export interface DevSessionConfig {
  nodeEnv: string | undefined;
  bypassEnabled: string | undefined;
  host: string | null;
  devApiKey: string | undefined;
}

export function isLocalhostHost(host: string | null): boolean {
  if (!host) {
    return false;
  }

  const normalized = host.trim().toLowerCase();
  if (normalized === "[::1]" || normalized.startsWith("[::1]:")) {
    return true;
  }

  const hostname = normalized.split(":")[0];
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
}

export function evaluateDevSessionRequest(config: DevSessionConfig): DevSessionDecision {
  if (config.nodeEnv === "production") {
    return { allowed: false, status: 404, reason: "disabled_in_production" };
  }

  if (config.bypassEnabled !== "true") {
    return { allowed: false, status: 404, reason: "bypass_not_enabled" };
  }

  if (!isLocalhostHost(config.host)) {
    return { allowed: false, status: 403, reason: "non_localhost_host" };
  }

  const apiKey = config.devApiKey?.trim();
  if (!apiKey) {
    return { allowed: false, status: 404, reason: "missing_dev_api_key" };
  }

  return { allowed: true, apiKey };
}

export function getSessionCookieName(env: NodeJS.ProcessEnv = process.env): string {
  return env[SESSION_COOKIE_NAME_ENV] || "idis_api_key";
}

export function getSessionMaxAge(env: NodeJS.ProcessEnv = process.env): number {
  return Number.parseInt(env.IDIS_SESSION_MAX_AGE || "28800", 10);
}
