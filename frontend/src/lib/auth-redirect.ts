import type { ReadonlyURLSearchParams } from 'next/navigation';

export function resolveAuthRedirect(
  searchParams: ReadonlyURLSearchParams | null | undefined,
  fallback = '/dashboard'
): string {
  const target = searchParams?.get('redirect');
  if (!target) return fallback;

  if (target.startsWith('/') && !target.startsWith('//')) {
    return target;
  }

  return fallback;
}
