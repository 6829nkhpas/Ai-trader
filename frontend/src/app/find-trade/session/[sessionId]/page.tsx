// app/find-trade/session/[sessionId]/page.tsx
//
// Deep link to one analysis session.
//
// `params` is a PROMISE and must be awaited. That is the Next 15 change most likely to bite here —
// reading `params.sessionId` synchronously type-checks against nothing useful and yields `undefined` at
// runtime. The repo's existing dynamic route (`app/api/deepquant/[...path]/route.ts`) already types it
// as `Promise<...>`, and this follows it.
//
// NOTE: `node_modules/next/dist/docs/` (which `frontend/AGENTS.md` points at) is not present in this
// install, so the conventions here were verified against the installed Next version (15.5.19) and the
// existing route in this repo rather than against those bundled docs.

// Next does not require this import, but the test runner does: vitest is configured with the classic
// JSX transform, so `React` must be in scope wherever JSX appears. Every other component in this repo
// imports it explicitly for the same reason.
import React from 'react';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { FQ_MULTI_SESSION } from '@/lib/env';
import { FqQueryProvider } from '@/lib/fq/FqQueryProvider';
import SessionWorkspace from '@/components/quant/session/SessionWorkspace';

type PageProps = { params: Promise<{ sessionId: string }> };

/**
 * Rendered per request.
 *
 * The session belongs to the caller's cookie, so nothing here can be prerendered or cached at the
 * route level — a static shell would be one user's session id baked into the build.
 */
export const dynamic = 'force-dynamic';

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { sessionId } = await params;
  return {
    // Deliberately generic. The symbol and the user's own title are private to the session, and a page
    // title leaks into browser history, screenshots and shared tabs.
    title: 'Analysis session — Strat Ai',
    description: 'A saved market-analysis conversation.',
    // Session URLs are per-user and must never be indexed or previewed.
    robots: { index: false, follow: false },
    other: { 'x-fq-session': sessionId.slice(0, 8) },
  };
}

export default async function SessionPage({ params }: PageProps) {
  const { sessionId } = await params;

  // With the flag off there is no multi-session workspace to show, and the route must not render a
  // half-built one. 404 is the honest answer: on this build, that page does not exist.
  if (!FQ_MULTI_SESSION) notFound();

  // Shape-checked here rather than trusted into a fetch. Ownership is enforced server-side (the API
  // answers 404 for a session that is not yours), but an obviously malformed id should not cost a round
  // trip — and it must not be interpolated into a request URL unvalidated.
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(sessionId)) notFound();

  // The provider wraps the workspace rather than living inside it, because the workspace itself calls
  // `useSession`/`useActivateSession` — a component cannot consume a context it supplies.
  return (
    <FqQueryProvider>
      <SessionWorkspace sessionId={sessionId} />
    </FqQueryProvider>
  );
}
