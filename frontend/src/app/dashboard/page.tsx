/**
 * /dashboard — Protected route stub
 *
 * The middleware at src/middleware.ts redirects any unauthenticated request
 * for /dashboard to /auth/login. This page acts as the authenticated landing
 * target while the full trading terminal lives at the root (/). In a future
 * phase this will be replaced with a proper dashboard hub.
 */
import DashboardRedirect from './DashboardRedirect';

export const metadata = {
  title: 'AI Trader - Dashboard',
};

export default function DashboardPage() {
  return <DashboardRedirect />;
}
