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
        {children}
      </body>
    </html>
  );
}
