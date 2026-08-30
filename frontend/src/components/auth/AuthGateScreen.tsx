'use client';

// The screen shown while the terminal has no confirmed session.
//
// It replaced `AuthOverlay`, which rendered a "Login with Strat AI" button that
// drove a desktop-shaped handshake (open a browser, poll a session endpoint, race
// a `strat://` deep-link event, exchange the winner for localStorage tokens).
// There is no login form here any more: auth.stratai.live is the only sign-in
// surface, and because the session cookie is issued for `.stratai.live` the user
// arrives back on this origin already authenticated.
//
// Deliberately lightweight. The old overlay mounted a 5,000-particle WebGL scene;
// this screen normally exists for a few hundred milliseconds while either the
// session check or a browser navigation completes, so spinning up a GPU context
// for it would cost more than it shows.

import React from 'react';
import Image from 'next/image';
import { Loader2 } from 'lucide-react';
import type { AuthStatus } from '../../store/useAuthStore';
import { signInUrl } from '../../lib/authRedirect';

interface AuthGateScreenProps {
  /** `unknown` while the session check is in flight; `anonymous` once it failed. */
  status: AuthStatus;
}

export default function AuthGateScreen({ status }: AuthGateScreenProps) {
  const checking = status === 'unknown';

  return (
    <div className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-5 bg-[#0a0e17] px-6">
      <Image
        src="/strat.svg"
        alt="Strat AI"
        width={56}
        height={56}
        className="h-14 w-14 object-contain drop-shadow-lg"
        priority
      />

      <div className="flex items-center gap-2.5 text-white/70">
        <Loader2 size={15} className="animate-spin" aria-hidden="true" />
        <p className="text-sm font-medium" role="status">
          {checking ? 'Verifying your session…' : 'Taking you to sign in…'}
        </p>
      </div>

      {/*
        Manual fallback, shown only once we KNOW there is no session.
        `window.location.replace` can be blocked or simply slow, and without a
        visible link a user in that state is stranded on a spinner with no way
        forward. While the status is still `unknown` there is nothing to offer —
        they may well be signed in already.
      */}
      {!checking && (
        <a
          href={signInUrl()}
          className="rounded-lg border border-[#1e2a3a] bg-[#131922] px-5 py-2.5 text-xs font-semibold text-white/90 transition-colors hover:border-[#2a3a4e] hover:bg-[#1a2332]"
        >
          Continue to sign in
        </a>
      )}
    </div>
  );
}
