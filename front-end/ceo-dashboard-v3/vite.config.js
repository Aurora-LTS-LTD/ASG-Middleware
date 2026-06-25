import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { viteSingleFile } from "vite-plugin-singlefile";

// Single self-contained index.html (JS + CSS inlined). WKWebView blocks
// crossorigin ES-module fetches from file://, so inlining everything is the
// robust way to host the SPA inside AuroraMacShell (loadFileURL index.html).
// base: './' keeps any residual refs relative.
export default defineConfig({
  plugins: [svelte(), viteSingleFile()],
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    target: "es2020",
  },
});
