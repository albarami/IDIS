import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { getOrGenerateRequestId } from "@/lib/requestId";

const COOKIE_NAME = process.env.IDIS_SESSION_COOKIE_NAME || "idis_api_key";
const API_BASE_URL = process.env.IDIS_API_BASE_URL || "http://localhost:8000";

/**
 * Proxy handler for all IDIS API requests.
 * Routes: /api/idis/* → IDIS_API_BASE_URL/*
 *
 * Security:
 * - Reads API key from HttpOnly cookie (never from request)
 * - Adds X-IDIS-API-Key header to backend request
 * - Adds X-Request-Id header (preserves existing or generates new)
 * - Forwards Idempotency-Key if provided
 * - Fail-closed: returns 401 if no session cookie
 */
async function proxyHandler(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  // Get API key from HttpOnly cookie
  const cookieStore = await cookies();
  const apiKey = cookieStore.get(COOKIE_NAME)?.value;

  // Fail-closed: reject if no session
  if (!apiKey) {
    return NextResponse.json(
      {
        code: "unauthorized",
        message: "Not authenticated. Please log in.",
        request_id: getOrGenerateRequestId(request.headers.get("X-Request-Id")),
      },
      { status: 401 }
    );
  }

  // Build target URL
  const { path } = await params;
  const targetPath = "/" + path.join("/");
  const url = new URL(targetPath, API_BASE_URL);

  // Preserve query params
  request.nextUrl.searchParams.forEach((value, key) => {
    url.searchParams.set(key, value);
  });

  // Build headers for backend request
  const requestId = getOrGenerateRequestId(request.headers.get("X-Request-Id"));
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-IDIS-API-Key": apiKey,
    "X-Request-Id": requestId,
  };

  // Forward Idempotency-Key if present
  const idempotencyKey = request.headers.get("Idempotency-Key");
  if (idempotencyKey) {
    headers["Idempotency-Key"] = idempotencyKey;
  }

  // Get request body for non-GET methods
  let body: string | undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    try {
      const text = await request.text();
      if (text) {
        body = text;
      }
    } catch {
      // No body or failed to read
    }
  }

  try {
    // Make request to backend
    const response = await fetch(url.toString(), {
      method: request.method,
      headers,
      body,
    });

    // Get response body
    const responseText = await response.text();
    let responseData: unknown;
    try {
      responseData = JSON.parse(responseText);
    } catch {
      responseData = responseText;
    }

    // Build response with same status and headers
    const responseHeaders = new Headers();
    responseHeaders.set("Content-Type", response.headers.get("Content-Type") || "application/json");
    responseHeaders.set("X-Request-Id", requestId);

    // If error response, ensure request_id is in body
    if (!response.ok && typeof responseData === "object" && responseData !== null) {
      const errorData = responseData as Record<string, unknown>;
      if (!errorData.request_id) {
        errorData.request_id = requestId;
      }
      return NextResponse.json(errorData, {
        status: response.status,
        headers: responseHeaders,
      });
    }

    return new NextResponse(
      typeof responseData === "string" ? responseData : JSON.stringify(responseData),
      {
        status: response.status,
        headers: responseHeaders,
      }
    );
  } catch (error) {
    // Network or other error
    const errorMessage = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      {
        code: "proxy_error",
        message: `Failed to reach backend: ${errorMessage}`,
        request_id: requestId,
      },
      { status: 502 }
    );
  }
}

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  return proxyHandler(request, context);
}

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  return proxyHandler(request, context);
}

export async function PATCH(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  return proxyHandler(request, context);
}

export async function PUT(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  return proxyHandler(request, context);
}

export async function DELETE(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  return proxyHandler(request, context);
}
