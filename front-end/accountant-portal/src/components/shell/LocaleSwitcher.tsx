"use client";

/**
 * P2-14 — Language switcher button.
 *
 * Renders a small globe icon + current locale abbreviation.
 * Click to cycle through: en → he → ar → en.
 * Designed to sit in the topbar alongside other controls.
 */

import { Globe } from "lucide-react";
import { SUPPORTED_LOCALES, LOCALE_LABELS, type Locale } from "@/lib/i18n/locale";
import { useLocale } from "@/lib/i18n/LocaleProvider";

export function LocaleSwitcher() {
  const { locale, setLocale } = useLocale();

  const next = (): void => {
    const idx = SUPPORTED_LOCALES.indexOf(locale);
    const nextLocale: Locale = SUPPORTED_LOCALES[(idx + 1) % SUPPORTED_LOCALES.length];
    setLocale(nextLocale);
  };

  return (
    <button
      onClick={next}
      title={`Language: ${LOCALE_LABELS[locale]} — click to switch`}
      className="
        flex items-center gap-1.5 rounded-md px-2 py-1
        text-zinc-400 hover:text-zinc-100
        hover:bg-zinc-800 transition-colors
        text-xs font-medium
      "
      aria-label={`Current language: ${LOCALE_LABELS[locale]}. Click to switch.`}
    >
      <Globe className="h-3.5 w-3.5" />
      <span>{locale.toUpperCase()}</span>
    </button>
  );
}
