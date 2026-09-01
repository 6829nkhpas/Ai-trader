// @vitest-environment jsdom
//
// app/find-trade/session/[sessionId]/__tests__/page.test.tsx
//
// The route guard, before any data is fetched.
//
// `params` is a PROMISE in Next 15. Reading `params.sessionId` synchronously yields `undefined`, which
// would sail past a naive test and produce a request for `/sessions/undefined` in production — so the
// awaiting is asserted directly, not assumed.

import React from 'react';
import { render } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

const { notFoundMock, flag, workspaceMock } = vi.hoisted(() => ({
  // Next's real `notFound()` throws to unwind to the nearest boundary.
  notFoundMock: vi.fn(() => {
    throw new Error('NEXT_NOT_FOUND');
  }),
  // A mutable holder, read through a getter below, because the two flag states cannot both be module
  // constants in one file — and both matter: one is what ships today, the other is what ships next.
  flag: { multiSession: true },
  // Typed on the props it receives, so the assertion below is checked rather than `any`.
  workspaceMock: vi.fn((_props: { sessionId: string }) => null),
}));

vi.mock('next/navigation', () => ({ notFound: notFoundMock }));
vi.mock('@/lib/env', () => ({
  get FQ_MULTI_SESSION() {
    return flag.multiSession;
  },
}));
vi.mock('@/lib/fq/FqQueryProvider', () => ({
  FqQueryProvider: ({ children }: { children: React.ReactNode }) => children,
}));
vi.mock('@/components/quant/session/SessionWorkspace', () => ({
  default: (props: { sessionId: string }) => workspaceMock(props),
}));

import SessionPage from '../page';

const VALID = 'sess_01JABCDEFGHJKMNPQRSTVWXYZ';

beforeEach(() => {
  flag.multiSession = true;
  notFoundMock.mockClear();
  workspaceMock.mockClear();
});

describe('the id', () => {
  it('is awaited off the params promise and handed to the workspace', async () => {
    // The Next 15 change most likely to bite. Read synchronously this is `undefined`, and the page
    // would render a workspace that fetches `/sessions/undefined`.
    const element = await SessionPage({ params: Promise.resolve({ sessionId: VALID }) });

    // Rendered, not merely constructed: an async server component returns an element tree, so the
    // child is not invoked until something renders it — asserting on the spy alone would pass even if
    // the page never used the id.
    render(element);

    expect(workspaceMock).toHaveBeenCalledWith(expect.objectContaining({ sessionId: VALID }));
    expect(notFoundMock).not.toHaveBeenCalled();
  });

  it.each([
    ['empty', ''],
    ['a path traversal', '../../etc/passwd'],
    ['a query injection', 'abc?status=archived'],
    ['a slash', 'a/b'],
    ['whitespace', 'sess 1'],
    ['over-long', 'x'.repeat(65)],
  ])('refuses %s without a round trip', async (_label, sessionId) => {
    // Ownership is enforced server-side, but a malformed id must not be interpolated into a request
    // URL — and it should not cost a fetch to be told what the shape already says.
    await expect(SessionPage({ params: Promise.resolve({ sessionId }) })).rejects.toThrow(
      'NEXT_NOT_FOUND',
    );
    expect(workspaceMock).not.toHaveBeenCalled();
  });

  it('accepts the id shapes the server actually mints', async () => {
    // Opaque, server-minted ids. Rejecting a legitimate one would make a valid link 404.
    for (const id of ['sess_01JABC', 'abc-DEF_123', 'a']) {
      notFoundMock.mockClear();
      await SessionPage({ params: Promise.resolve({ sessionId: id }) });
      expect(notFoundMock).not.toHaveBeenCalled();
    }
  });
});

describe('the rollout flag', () => {
  it('404s the whole route when multi-session is off', async () => {
    // With the flag off there is no workspace to show. Rendering a half-built one would be worse than
    // saying the page does not exist on this build — which is the truth.
    flag.multiSession = false;

    await expect(SessionPage({ params: Promise.resolve({ sessionId: VALID }) })).rejects.toThrow(
      'NEXT_NOT_FOUND',
    );
    expect(workspaceMock).not.toHaveBeenCalled();
  });
});
