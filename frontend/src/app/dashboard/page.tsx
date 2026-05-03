/**
 * /dashboard — Protected route stub
 *
 * The middleware at src/middleware.ts redirects any unauthenticated request
 * for /dashboard to /auth/login. This page acts as the authenticated landing
 * target while the full trading terminal lives at the root (/). In a future
 * phase this will be replaced with a proper dashboard hub.
 */
import { redirect } from 'next/navigation';

export const metadata = {
  title: 'AI Trader - Dashboard',
};

export default function DashboardPage() {
  // Redirect to the trading terminal at root (existing page.tsx)
  redirect('/');
}
