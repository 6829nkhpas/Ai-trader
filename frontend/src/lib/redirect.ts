import { DASHBOARD_URL } from './env';

export async function openExternalUrl(url: string): Promise<void> {
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('open_browser', { url });
    return;
  } catch {
    if (typeof window !== 'undefined') {
      window.open(url, '_blank', 'noopener,noreferrer');
    }
  }
}

export function dashboardUrl(): string {
  return DASHBOARD_URL ?? '';
}
