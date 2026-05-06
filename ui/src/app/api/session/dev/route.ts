import { NextRequest, NextResponse } from "next/server";
import {
  evaluateDevSessionRequest,
  getSessionCookieName,
  getSessionMaxAge,
} from "@/lib/devSession";

export async function POST(request: NextRequest) {
  const decision = evaluateDevSessionRequest({
    nodeEnv: process.env.NODE_ENV,
    bypassEnabled: process.env.IDIS_UI_DEV_AUTH_BYPASS,
    host: request.headers.get("host"),
    devApiKey: process.env.IDIS_DEV_API_KEY,
  });

  if (!decision.allowed) {
    return NextResponse.json(
      { code: "not_found", message: "Not found" },
      { status: decision.status }
    );
  }

  const response = NextResponse.json({
    authenticated: true,
    dev_bypass: true,
  });
  response.cookies.set(getSessionCookieName(), decision.apiKey, {
    httpOnly: true,
    secure: false,
    sameSite: "lax",
    maxAge: getSessionMaxAge(),
    path: "/",
  });
  return response;
}
