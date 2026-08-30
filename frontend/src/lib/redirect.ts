import { DASHBOARD_URL } from './env';
import { bridgeInvoke } from './bridge';

export async function openExternalUrl(url: string): Promise<void> {
  try {
    // Under Tauri this is the Rust `open_browser` command; in a browser the
    // bridge adapter is `window.open`. The catch below stays as a last resort
    // for a popup blocker rejecting the adapter's own call.
    await bridgeInvoke('open_browser', { url });
    return;
  } catch {
    if (typeof window !== 'undefined') {
      window.open(url, '_blank', 'noopener,noreferrer');
    }
  }
}

export function dashboardUrl(): string {
  // `lib/env` throws on a missing value, so this is always a real URL — the old
  // `?? ''` fallback could only ever have produced a link to nowhere.
  return DASHBOARD_URL;
}
