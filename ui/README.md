# IDIS Frontend UI

Enterprise-grade frontend for the IDIS (Institutional Deal Intelligence System) VC Edition v6.3.

## Security Model

This UI implements a **secure session pattern**:

- **No API keys in browser storage** — API keys are stored in HttpOnly cookies only
- **Server-side proxy** — All backend API calls route through `/api/idis/*`, never directly from the browser
- **Fail-closed auth** — Protected pages redirect to `/login` if no session exists
- **X-Request-Id tracking** — Every backend request includes a unique request ID for audit trails

## Quick Start

### Windows Local Development

On Windows, if you encounter `EPERM` errors during `npm ci` (file locks on `.next-swc` binaries):

```powershell
# 1. Stop any running Next dev server (Ctrl+C)

# 2. Kill stray Node processes
Get-Process node -ErrorAction SilentlyContinue | Stop-Process -Force

# 3. Remove build/install artifacts
cd ui
Remove-Item -Recurse -Force node_modules, .next -ErrorAction SilentlyContinue

# 4. Install dependencies
npm ci
```

### Standard Setup

```bash
# Install dependencies
npm ci

# Copy environment file and configure
cp .env.example .env.local
# Edit .env.local to set IDIS_API_BASE_URL

# Run development server
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

## Available Scripts

| Script | Description |
|--------|-------------|
| `npm run dev` | Start development server |
| `npm run build` | Build for production |
| `npm run start` | Start production server |
| `npm run lint` | Run ESLint |
| `npm run typecheck` | Run TypeScript type checking |
| `npm run test` | Run tests |

## Project Structure

```
ui/
├── src/
│   ├── app/                    # Next.js App Router pages
│   │   ├── api/               # API routes (session, proxy)
│   │   │   ├── session/       # POST/DELETE for login/logout
│   │   │   └── idis/          # Proxy to backend API
│   │   ├── login/             # Login page
│   │   ├── deals/             # Deals list + deal dashboard
│   │   ├── claims/            # Claim detail
│   │   ├── runs/              # Run/debate status
│   │   └── audit/             # Audit events viewer
│   ├── components/            # Reusable UI components
│   └── lib/                   # Utilities
│       ├── idis.ts            # Typed API client
│       └── requestId.ts       # UUID generation
├── .env.example               # Environment template
└── README.md                  # This file
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `IDIS_API_BASE_URL` | Yes | Backend API URL (e.g., `http://localhost:8000`) |
| `IDIS_SESSION_COOKIE_NAME` | No | Cookie name (default: `idis_api_key`) |
| `IDIS_SESSION_MAX_AGE` | No | Session duration in seconds (default: 28800 = 8 hours) |

**Important**: `IDIS_API_BASE_URL` is intentionally NOT prefixed with `NEXT_PUBLIC_` to keep it server-side only.

## API Proxy

All API calls go through `/api/idis/*`:

```typescript
// Client calls
fetch("/api/idis/v1/deals")

// Proxy adds:
// - X-IDIS-API-Key header (from session cookie)
// - X-Request-Id header (UUID for audit trail)
// - Forwards Idempotency-Key if provided
```

## Authentication Flow

1. User enters API key on `/login`
2. POST `/api/session` stores key in HttpOnly cookie
3. All subsequent API calls include cookie automatically
4. Logout via DELETE `/api/session` clears cookie

## CI Integration

The `ui-check` target runs:
- `npm ci` — Install dependencies
- `npm run lint` — ESLint
- `npm run typecheck` — TypeScript
- `npm run test` — Vitest
- `npm run build` — Production build
