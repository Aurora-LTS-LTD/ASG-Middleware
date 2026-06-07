"use client";

/**
 * P2-14 — LocaleProvider
 *
 * Wraps next-intl's NextIntlClientProvider with locale state
 * management.  The locale is read from localStorage on first render
 * and can be changed at runtime via the `useLocale` hook.
 *
 * RTL layout direction is applied automatically when the locale
 * is Hebrew or Arabic.
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { NextIntlClientProvider } from "next-intl";
import {
  type Locale,
  LOCALE_DIR,
  getStoredLocale,
  setStoredLocale,
} from "./locale";

// ─────────────────────────────────────────────────────────────
// Context
// ─────────────────────────────────────────────────────────────

interface LocaleContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  dir: "ltr" | "rtl";
}

const LocaleContext = createContext<LocaleContextValue>({
  locale: "en",
  setLocale: () => {},
  dir: "ltr",
});

export function useLocale(): LocaleContextValue {
  return useContext(LocaleContext);
}

// ─────────────────────────────────────────────────────────────
// Provider
// ─────────────────────────────────────────────────────────────

interface LocaleProviderProps {
  children: React.ReactNode;
}

export function LocaleProvider({ children }: LocaleProviderProps) {
  const [locale, setLocaleState] = useState<Locale>("en");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [messages, setMessages] = useState<Record<string, any>>({});
  const [ready, setReady] = useState(false);

  // Hydrate from localStorage on mount (avoids SSR mismatch)
  useEffect(() => {
    const stored = getStoredLocale();
    setLocaleState(stored);
    loadMessages(stored).then((msgs) => {
      setMessages(msgs);
      setReady(true);
    });
  }, []);

  const setLocale = useCallback(async (next: Locale) => {
    setStoredLocale(next);
    const msgs = await loadMessages(next);
    setMessages(msgs);
    setLocaleState(next);

    // Update document-level lang + dir for accessibility and CSS
    document.documentElement.lang = next;
    document.documentElement.dir = LOCALE_DIR[next];
  }, []);

  // Apply dir on initial mount too
  useEffect(() => {
    if (ready) {
      document.documentElement.lang = locale;
      document.documentElement.dir = LOCALE_DIR[locale];
    }
  }, [locale, ready]);

  if (!ready) {
    // Render nothing until we have messages — avoids flash of wrong language
    return null;
  }

  return (
    <LocaleContext.Provider
      value={{ locale, setLocale, dir: LOCALE_DIR[locale] }}
    >
      <NextIntlClientProvider locale={locale} messages={messages}>
        {children}
      </NextIntlClientProvider>
    </LocaleContext.Provider>
  );
}

// ─────────────────────────────────────────────────────────────
// Message loader (dynamic import for code splitting)
// ─────────────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function loadMessages(locale: Locale): Promise<Record<string, any>> {
  try {
    const mod = await import(`../../../messages/${locale}.json`);
    return mod.default ?? mod;
  } catch {
    // Fallback to English if the file is missing
    const mod = await import(`../../../messages/en.json`);
    return mod.default ?? mod;
  }
}
