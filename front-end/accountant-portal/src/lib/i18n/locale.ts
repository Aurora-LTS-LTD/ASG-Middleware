/**
 * P2-14 — Locale management for the accountant portal.
 *
 * Supports: "en" (English, LTR), "he" (Hebrew, RTL), "ar" (Arabic, RTL).
 *
 * The user's locale preference is stored in localStorage under the key
 * "aurora_locale" so it survives app restarts. In Tauri, localStorage is
 * backed by the WebView data store and is not cleared between launches.
 */

export type Locale = "en" | "he" | "ar";

export const SUPPORTED_LOCALES: Locale[] = ["en", "he", "ar"];

export const LOCALE_LABELS: Record<Locale, string> = {
  en: "English",
  he: "עברית",
  ar: "العربية",
};

export const LOCALE_DIR: Record<Locale, "ltr" | "rtl"> = {
  en: "ltr",
  he: "rtl",
  ar: "rtl",
};

const STORAGE_KEY = "aurora_locale";
const DEFAULT_LOCALE: Locale = "en";

export function getStoredLocale(): Locale {
  if (typeof window === "undefined") return DEFAULT_LOCALE;
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored && (SUPPORTED_LOCALES as string[]).includes(stored)) {
    return stored as Locale;
  }
  // Browser language detection fallback
  const lang = navigator.language.split("-")[0];
  if ((SUPPORTED_LOCALES as string[]).includes(lang)) return lang as Locale;
  return DEFAULT_LOCALE;
}

export function setStoredLocale(locale: Locale): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(STORAGE_KEY, locale);
}
