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
        // Aurora brand palette — matches the inline config that used to ship
        // via the CDN runtime config script in every dashboard file.
        brand: {
          50: "#eef1fe",
          100: "#d9dffb",
          500: "#4f6ef7",
          600: "#3b55e6",
          700: "#2d42c9",
        },
      },
    },
  },
  plugins: [],
};
