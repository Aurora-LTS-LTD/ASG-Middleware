/** @type {import('tailwindcss').Config} */
module.exports = {
  // JIT scans these files for class names; if a class isn't here at build time
  // it won't be in the output CSS. Cover BOTH the source-of-truth files at the
  // root AND the bundled copies under ui/ (the Tauri webview reads from ui/).
  content: [
    "./dashboard.html",
    "./onboarding.html",
    "./accountant/**/*.html",
    "./ui/dashboard.html",
    "./ui/onboarding.html",
    "./ui/accountant/**/*.html",
  ],
  theme: {
    extend: {
      colors: {
        // ── Legacy brand palette ──
        // Kept as-is for backward compatibility with ~100 existing bg-brand-*,
        // text-brand-*, focus:ring-brand-* references throughout the dashboard.
        // These are secondary accents (form borders, table-row hovers, focus
        // rings) where the existing indigo tone still reads as on-brand.
        brand: {
          50: "#eef1fe",
          100: "#d9dffb",
          500: "#4f6ef7",
          600: "#3b55e6",
          700: "#2d42c9",
        },

        // ── Aurora-borealis palette (Bold theme refresh) ──
        // Northern-lights gradient stops matching the company logo and the
        // marketing site (aurora-ltd.co.il). Used explicitly on the signature
        // moments: login screen background, "Aurora LTS" wordmark gradient
        // text, and primary CTA gradient buttons with purple glow shadows.
        aurora: {
          deep:     "#3b0764",  // deepest purple — gradient start
          purple:   "#7c3aed",  // core purple
          indigo:   "#4f6ef7",  // bridge (matches legacy brand-500)
          teal:     "#14b8a6",  // teal
          green:    "#10b981",  // northern green
          // Convenience aliases for the canonical 3-stop gradient
          gradFrom: "#7c3aed",
          gradVia:  "#4f6ef7",
          gradTo:   "#14b8a6",
        },
      },
    },
  },
  plugins: [],
};
