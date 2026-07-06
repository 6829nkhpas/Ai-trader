/**
 * /dashboard — Redirects to the trading terminal at the application root.
 *
 * The terminal lives at `/`. This stub remains so historical bookmarks /
 * deep-links keep working after the auth & onboarding routes were removed.
 */
import { redirect } from "next/navigation";

export const metadata = {
  title: "AI Trader - Dashboard",
};

export default function DashboardPage() {
  redirect("/");
}
