import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "@/lib/auth/context";
import { QueryProvider } from "@/lib/QueryProvider";
import { Toaster } from "@/components/ui/toaster";
import { ErrorBoundary } from "@/components/shell/ErrorBoundary";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider"; // P2-14

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Aurora LTS Accountant Portal",
  description: "Secure accountant terminal for Aurora LTS — Zero-Trust B2B Fintech.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      // Default to dark — banking terminal aesthetic + matches the existing UI.
      data-theme="dark"
    >
      <body className="min-h-full flex flex-col bg-zinc-950 text-zinc-100">
        {/* P1-18 — root error boundary wraps the entire app so a
            single uncaught render error doesn't crash the shell. */}
        <ErrorBoundary>
          {/* P2-14 — locale provider must wrap everything so all
              nested components can call useTranslations() */}
          <LocaleProvider>
            <QueryProvider>
              <AuthProvider>
                {children}
                <Toaster />
              </AuthProvider>
            </QueryProvider>
          </LocaleProvider>
        </ErrorBoundary>
      </body>
    </html>
  );
}
