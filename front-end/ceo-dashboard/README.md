# Aurora LTS — CEO Dashboard (Tauri desktop app)

macOS desktop app (internal distribution, not App Store). Scaffolded 2026-06-18.

## Layout
```
ceo-dashboard/
├── ui/                     # web assets (the webview frontend)
│   ├── dashboard.html      # main window entry (set as app.windows[0].url)
│   ├── onboarding.html
│   └── accountant/index.html
├── src-tauri/              # Tauri 2 native shell (Rust)
│   ├── tauri.conf.json     # identifier com.aurora.ceo-dashboard, frontendDist ../ui
│   ├── Cargo.toml, build.rs, src/
│   └── icons/              # ⚠️ Tauri DEFAULT icons — replace with Aurora branding
└── package.json            # npm run dev / build → tauri
```

## Build (on a Mac)
```bash
cd front-end/ceo-dashboard
npm install
npm run build          # = tauri build → src-tauri/target/release/bundle/{dmg,macos}/*
```
Signing + notarization for internal distribution: see [../DESKTOP_BUILD_RUNBOOK.md](../DESKTOP_BUILD_RUNBOOK.md) §2d.

## How API calls work
The webview loads from `tauri://localhost`, so **relative `/api/*` paths won't reach the API**. Each HTML file has a small `fetch` shim in `<head>` that rewrites `/api/*` → `https://api-aurora-lts.com`. That origin is CORS-allowlisted (`tauri://localhost` is too). The `connect-src` CSP in `tauri.conf.json` permits it.

## Known items to refine (desktop dev)
- **Cross-page nav uses clean URLs** (e.g. `href="/dashboard"`). The Tauri asset protocol serves files, so these won't resolve — convert to explicit `dashboard.html` / `onboarding.html`, or adopt a hash router. The main `dashboard.html` is self-contained (tab UI in JS), so it works as the entry; only inter-page links need this.
- **Icons** are Tauri placeholders. Generate real ones: `npx tauri icon path/to/aurora-logo.png`.
- **Auth** currently uses `localStorage` (works in the webview). For OS-keychain parity with the accountant portal, wire `tauri-plugin-keychain`/keyring later.
- **Endpoint fix applied:** `dashboard.html` calls `/api/v1/organizations` (was `/api/v1/businesses`, which 404s on the live API).
- **CSP** allows the Tailwind + Alpine CDNs and the live API; tighten/vendor the CDNs for production.
