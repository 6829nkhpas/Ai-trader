import type { Metadata } from "next";
import { Inter, Geist } from "next/font/google";
import "./globals.css";
import { cn } from "@/lib/utils";

const geist = Geist({subsets:['latin'],variable:'--font-sans'});

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  // Compliance: user-facing copy. "AI Trader" / "AI-powered trading" claimed the
  // product trades — it has no order path at all (providers::BrokerProvider has no
  // order method). See docs/compliance/BRAND_GUIDELINES.md §1.1 rule 11.
  title: "Strat Ai — Market Analysis Terminal",
  description: "Market analysis and charting terminal for NSE and NFO.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const isTestMode =
    process.env.ALPHA_TEST_MODE === "1" ||
    process.env.ALPHA_TEST_MODE === "true";

  return (
    <html
      lang="en"
      className={cn("h-full", "antialiased", inter.variable, "font-sans", geist.variable)}
      suppressHydrationWarning
    >
      <head>
        {/*
          Apply the persisted theme BEFORE first paint.

          The theme lives in `useChartUIStore` and is mirrored to
          localStorage under `stratai.theme` (see THEME_STORAGE_KEY). Without
          this script the document starts un-classed on every load, so a user
          on the light theme saw the shell flash — and then stay — dark after a
          refresh. Runs synchronously in <head>, so the class is on <html>
          before the first frame. Keep the key in sync with useChartUIStore.
        */}
        <script
          dangerouslySetInnerHTML={{
            __html: `try{if(localStorage.getItem('stratai.theme')==='light'){document.documentElement.classList.add('light')}}catch(e){}`,
          }}
        />
      </head>
      <body className="min-h-full flex flex-col" suppressHydrationWarning>
        {/* Inject test mode flag for client-side detection */}
        {isTestMode && (
          <script
            dangerouslySetInnerHTML={{
              __html: `window.__ALPHA_TEST_MODE__ = true;`,
            }}
          />
        )}
        {/*
          `FqQueryProvider` is deliberately NOT here.

          Hoisting it to the layout was tried, to give the terminal at `/` and the standalone workspace
          at `/find-trade/session/{id}` one shared cache. It cost 12 kB of shared JS on EVERY page —
          including `/dashboard`, which has no session UI at all — and bought nothing:

            * the two pages never mount together, so they cannot disagree about anything;
            * what actually has to survive navigation is the session STATE, and that lives in
              `useSessionStore`, a module-scoped zustand store that outlives any React tree. Its
              `hydratedAt` marker persists, so `useActivateSession` short-circuits after a navigation
              instead of replaying a transcript;
            * the query cache only carries the session list and summaries, which are one cheap request.

          So each entry point mounts its own provider. If a third consumer appears, prefer a route-group
          layout over the root one, so pages with no session UI keep paying nothing.
        */}
        {children}
      </body>
    </html>
  );
}
