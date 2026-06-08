"use client";

import { ThemeProvider as NextThemesProvider } from "next-themes";

/**
 * App theme provider (light / dark / system) backed by next-themes.
 * Adds `class="dark"` or `class="light"` on <html>, which the Tailwind v4
 * `@custom-variant dark (&:is(.dark *))` + CSS-variable palettes flip on.
 * Default is dark (banking-terminal brand); persisted to localStorage; an
 * inline script (injected by next-themes) sets it pre-paint so there's no flash.
 */
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="dark"
      enableSystem
      disableTransitionOnChange
    >
      {children}
    </NextThemesProvider>
  );
}
