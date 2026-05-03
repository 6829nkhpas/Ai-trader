import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { SessionProvider } from "@/context/AuthContext";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "AI Trader - Trade Terminal",
  description: "Institutional-grade AI-powered trading, secured by design.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <body className="min-h-full flex flex-col" suppressHydrationWarning>
        {/*
          SessionProvider is rendered in a Client Component (AuthContext.tsx).
          Next.js App Router allows Server Components to import Client Component
          wrappers — children within are still individually server-rendered.
        */}
        <SessionProvider>{children}</SessionProvider>
      </body>
    </html>
  );
}
