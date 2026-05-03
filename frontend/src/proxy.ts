/**
 * middleware.ts — Next.js Edge Middleware
 * ─────────────────────────────────────────────────────────────────────────────
 * Route Guard: protects all routes under /dashboard (and any future
 * authenticated routes) by checking for the presence of the HttpOnly
 * session cookie set by the auth service.
 *
 * Security notes:
 *  ✅ Cookie NAME is checked — not its value. The actual JWT is never
 *     decoded in the Edge runtime (no secret needed here).
 *  ✅ If the cookie is absent → redirect to /auth/login?redirect=<original>
 *  ✅ If the user is already on /auth/* and the cookie IS present →
 *     redirect to /dashboard (prevents re-login loop)
 *  ✅ Static assets and API routes bypass this middleware entirely
 */

import { NextRequest, NextResponse } from 'next/server';

// Cookie name must match what the auth service sets via @fastify/cookie
const SESSION_COOKIE = 'access_token';
const REFRESH_COOKIE = 'refresh_token';

// Routes that require an authenticated session
const PROTECTED_PREFIXES = ['/dashboard', '/trade', '/portfolio', '/settings'];

// Routes that should redirect to /dashboard if already authenticated
const AUTH_PREFIXES = ['/auth/login', '/auth/signup'];

function hasSession(req: NextRequest): boolean {
  return (
    req.cookies.has(SESSION_COOKIE) ||
    req.cookies.has(REFRESH_COOKIE)   // refresh present → silent refresh will work
  );
}

export function proxy(req: NextRequest): NextResponse {
  const { pathname } = req.nextUrl;
  const authenticated = hasSession(req);

  // ── Guard: protected route without session → /auth/login ──────────────
  const isProtected = PROTECTED_PREFIXES.some((p) => pathname.startsWith(p));
  if (isProtected && !authenticated) {
    const loginUrl = req.nextUrl.clone();
    loginUrl.pathname = '/auth/login';
    loginUrl.searchParams.set('redirect', pathname);
    return NextResponse.redirect(loginUrl);
  }

  // ── Guard: auth page with valid session → /dashboard ──────────────────
  const isAuthPage = AUTH_PREFIXES.some((p) => pathname.startsWith(p));
  if (isAuthPage && authenticated) {
    const dashUrl = req.nextUrl.clone();
    dashUrl.pathname = '/dashboard';
    dashUrl.search = '';
    return NextResponse.redirect(dashUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    /*
     * Match all paths EXCEPT:
     *   - _next/static   (Next.js static files)
     *   - _next/image    (Next.js image optimization)
     *   - favicon.ico
     *   - public assets  (*.png, *.svg, *.jpg, *.webp)
     *   - /api/*         (API routes handle their own auth via authGuard)
     */
    '/((?!_next/static|_next/image|favicon\\.ico|.*\\.(?:png|svg|jpg|jpeg|webp|ico)).*)',
  ],
};
