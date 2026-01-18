import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

const COOKIE_NAME = process.env.IDIS_SESSION_COOKIE_NAME || "idis_api_key";
const MAX_AGE = parseInt(process.env.IDIS_SESSION_MAX_AGE || "28800", 10); // 8 hours default

/**
 * POST /api/session
 * Set HttpOnly cookie with API key.
 * Request body: { api_key: string }
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const apiKey = body.api_key;

    if (!apiKey || typeof apiKey !== "string" || apiKey.trim().length === 0) {
      return NextResponse.json(
        { code: "invalid_api_key", message: "API key is required" },
        { status: 400 }
      );
    }

    // Use NextResponse cookies to reliably set cookie in Route Handlers
    const res = NextResponse.json({ authenticated: true });
    res.cookies.set(COOKIE_NAME, apiKey.trim(), {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      maxAge: MAX_AGE,
      path: "/",
    });
    return res;
  } catch {
    return NextResponse.json(
      { code: "invalid_request", message: "Invalid request body" },
      { status: 400 }
    );
  }
}

/**
 * DELETE /api/session
 * Clear the session cookie (logout).
 */
export async function DELETE() {
  const res = NextResponse.json({ authenticated: false });
  res.cookies.delete(COOKIE_NAME);
  return res;
}

/**
 * GET /api/session
 * Check authentication status.
 * Returns { authenticated: boolean } without exposing the API key.
 */
export async function GET() {
  const cookieStore = await cookies();
  const apiKey = cookieStore.get(COOKIE_NAME)?.value;
  const authenticated = !!apiKey && apiKey.length > 0;
  return NextResponse.json({ authenticated });
}
