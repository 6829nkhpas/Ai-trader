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
  title: "AI Trader - Trade Terminal",
  description: "Institutional-grade AI-powered trading.",
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
