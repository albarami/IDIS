/**
 * Typed IDIS API client.
 * All calls go through the /api/idis proxy (never directly to backend).
 * The proxy handles authentication (HttpOnly cookie) and X-Request-Id injection.
 */

import { generateRequestId } from "./requestId";

// ============================================================================
// Error Envelope (per OpenAPI spec)
// ============================================================================

export interface IDISError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  request_id?: string;
}

export class IDISApiError extends Error {
  public readonly code: string;
  public readonly requestId: string | undefined;
  public readonly details: Record<string, unknown> | undefined;
  public readonly status: number;

  constructor(status: number, error: IDISError) {
    super(error.message);
    this.name = "IDISApiError";
    this.code = error.code;
    this.requestId = error.request_id;
    this.details = error.details;
    this.status = status;
  }
}

// ============================================================================
// API Types (derived from OpenAPI spec)
// ============================================================================

export type DealStatus =
  | "NEW"
  | "INTAKE"
  | "SCREENING"
  | "DEEP_DIVE"
  | "IC_READY"
  | "DECLINED"
  | "ARCHIVED";
export type DealStage =
  | "SEED"
  | "SERIES_A"
  | "SERIES_B"
  | "GROWTH"
  | "LATER"
  | "OTHER";
export type SanadGrade = "A" | "B" | "C" | "D";
export type CorroborationLevel = "AHAD" | "MUTAWATIR";
export type ClaimVerdict =
  | "VERIFIED"
  | "INFLATED"
  | "CONTRADICTED"
  | "UNVERIFIED"
  | "SUBJECTIVE";
export type ClaimAction =
  | "NONE"
  | "REQUEST_DATA"
  | "FLAG"
  | "RED_FLAG"
  | "HUMAN_GATE"
  | "PARTNER_OVERRIDE_REQUIRED";
export type Materiality = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type DefectSeverity = "FATAL" | "MAJOR" | "MINOR";
export type HumanGateAction = "APPROVE" | "CORRECT" | "REJECT";
export type RunStatus = "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED";

export interface Deal {
  deal_id: string;
  name: string;
  company_name: string;
  status: DealStatus;
  stage: DealStage;
  tags?: string[];
  created_at: string;
  updated_at?: string;
}

export interface Quantity {
  value: number;
  unit: string;
  currency?: string | null;
  as_of?: string | null;
  time_window?: {
    label: string;
    start_date?: string | null;
    end_date?: string | null;
  } | null;
}

export interface Claim {
  claim_id: string;
  deal_id: string;
  claim_class: string;
  claim_text: string;
  predicate?: string | null;
  value?: Quantity;
  sanad_id?: string | null;
  claim_grade: SanadGrade;
  corroboration: {
    level: CorroborationLevel;
    independent_chain_count: number;
  };
  claim_verdict: ClaimVerdict;
  claim_action: ClaimAction;
  defect_ids?: string[];
  materiality?: Materiality;
  ic_bound?: boolean;
  created_at: string;
}

export interface TruthDashboard {
  deal_id: string;
  summary: {
    total_claims: number;
    by_grade: Record<SanadGrade, number>;
    by_verdict: Record<ClaimVerdict, number>;
    fatal_defects: number;
  };
  claims: {
    items: Claim[];
    next_cursor?: string | null;
  };
}

export interface Deliverable {
  deliverable_id: string;
  deal_id: string;
  deliverable_type: string;
  status: string;
  created_at: string;
  uri?: string | null;
}

export interface HumanGate {
  gate_id: string;
  deal_id: string;
  claim_id?: string;
  gate_type: string;
  status: string;
  actor_id?: string;
  action?: HumanGateAction;
  reason?: string;
  created_at: string;
  resolved_at?: string;
}

export interface Override {
  override_id: string;
  deal_id: string;
  override_type: string;
  reason: string;
  actor_id: string;
  created_at: string;
}

export interface Run {
  run_id: string;
  status: RunStatus;
  started_at: string;
  finished_at?: string | null;
}

export interface DebateSession {
  debate_id: string;
  deal_id: string;
  protocol_version: string;
  rounds?: unknown[];
  created_at: string;
}

export interface AuditEvent {
  event_id: string;
  event_type: string;
  occurred_at: string;
}

export interface Sanad {
  sanad_id: string;
  claim_id: string;
  grade: SanadGrade;
  corroboration: {
    level: CorroborationLevel;
    independent_chain_count: number;
  };
  evidence_refs: unknown[];
  transmission_chain: unknown[];
  created_at: string;
}

// Paginated responses
export interface PaginatedResponse<T> {
  items: T[];
  next_cursor?: string | null;
  total?: number;
}

// ============================================================================
// API Client
// ============================================================================

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  idempotencyKey?: string;
  params?: Record<string, string | number | undefined>;
}

/**
 * Make a request to the IDIS API via the proxy.
 * Automatically handles:
 * - X-Request-Id generation
 * - Error envelope parsing
 * - Deterministic JSON parsing
 */
async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, idempotencyKey, params } = options;

  // Build URL with query params
  let url = `/api/idis${path}`;
  if (params) {
    const searchParams = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined) {
        searchParams.set(key, String(value));
      }
    });
    const queryString = searchParams.toString();
    if (queryString) {
      url += `?${queryString}`;
    }
  }

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Request-Id": generateRequestId(),
  };

  if (idempotencyKey) {
    headers["Idempotency-Key"] = idempotencyKey;
  }

  const response = await fetch(url, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
    credentials: "include", // Include cookies
  });

  // Handle non-JSON responses
  const contentType = response.headers.get("content-type");
  if (!contentType?.includes("application/json")) {
    if (!response.ok) {
      throw new IDISApiError(response.status, {
        code: "non_json_error",
        message: `HTTP ${response.status}: ${response.statusText}`,
      });
    }
    return {} as T;
  }

  const data = await response.json();

  if (!response.ok) {
    // Parse error envelope
    const error: IDISError = {
      code: data.code || "unknown_error",
      message: data.message || "An unknown error occurred",
      details: data.details,
      request_id: data.request_id,
    };
    throw new IDISApiError(response.status, error);
  }

  return data as T;
}

// ============================================================================
// API Methods
// ============================================================================

export const idis = {
  // Deals
  deals: {
    list: (params?: { limit?: number; cursor?: string; status?: DealStatus; stage?: DealStage }) =>
      request<PaginatedResponse<Deal>>("/v1/deals", { params }),

    get: (dealId: string) => request<Deal>(`/v1/deals/${dealId}`),

    getTruthDashboard: (dealId: string, params?: { limit?: number; cursor?: string }) =>
      request<TruthDashboard>(`/v1/deals/${dealId}/truth-dashboard`, { params }),
  },

  // Claims
  claims: {
    list: (
      dealId: string,
      params?: { limit?: number; cursor?: string; verdict?: ClaimVerdict; grade?: SanadGrade }
    ) => request<PaginatedResponse<Claim>>(`/v1/deals/${dealId}/claims`, { params }),

    get: (claimId: string) => request<Claim>(`/v1/claims/${claimId}`),

    getSanad: (claimId: string) => request<Sanad>(`/v1/claims/${claimId}/sanad`),
  },

  // Deliverables
  deliverables: {
    list: (dealId: string, params?: { limit?: number; cursor?: string }) =>
      request<PaginatedResponse<Deliverable>>(`/v1/deals/${dealId}/deliverables`, { params }),

    generate: (
      dealId: string,
      data: { deliverable_type: string },
      idempotencyKey?: string
    ) =>
      request<{ run_id: string }>(`/v1/deals/${dealId}/deliverables`, {
        method: "POST",
        body: data,
        idempotencyKey,
      }),
  },

  // Runs
  runs: {
    start: (dealId: string, data: { mode: "SNAPSHOT" | "FULL" }, idempotencyKey?: string) =>
      request<{ run_id: string; status: string }>(`/v1/deals/${dealId}/runs`, {
        method: "POST",
        body: data,
        idempotencyKey,
      }),

    get: (runId: string) => request<Run>(`/v1/runs/${runId}`),
  },

  // Debate
  debate: {
    start: (
      dealId: string,
      data: { protocol_version: string },
      idempotencyKey?: string
    ) =>
      request<{ run_id: string }>(`/v1/deals/${dealId}/debate`, {
        method: "POST",
        body: data,
        idempotencyKey,
      }),

    get: (debateId: string) => request<DebateSession>(`/v1/debate/${debateId}`),
  },

  // Human Gates
  humanGates: {
    list: (dealId: string, params?: { limit?: number; cursor?: string }) =>
      request<PaginatedResponse<HumanGate>>(`/v1/deals/${dealId}/human-gates`, { params }),

    submit: (
      dealId: string,
      data: { gate_id: string; action: string; notes?: string },
      idempotencyKey?: string
    ) =>
      request<HumanGate>(`/v1/deals/${dealId}/human-gates`, {
        method: "POST",
        body: data,
        idempotencyKey,
      }),
  },

  // Overrides
  overrides: {
    create: (
      dealId: string,
      data: { override_type: string; justification: string },
      idempotencyKey?: string
    ) =>
      request<Override>(`/v1/deals/${dealId}/overrides`, {
        method: "POST",
        body: data,
        idempotencyKey,
      }),
  },

  // Audit
  audit: {
    listEvents: (params?: {
      limit?: number;
      cursor?: string;
      dealId?: string;
      eventType?: string;
      after?: string;
      before?: string;
    }) => request<PaginatedResponse<AuditEvent>>("/v1/audit/events", { params }),
  },
};

export default idis;
