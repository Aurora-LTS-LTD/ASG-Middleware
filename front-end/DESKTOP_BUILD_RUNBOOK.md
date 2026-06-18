# Aurora LTS — Desktop App Build & Sign Runbook

Two surfaces ship as **native desktop apps** (Tauri 2), not web apps:

| App | Path | Target | Status |
|-----|------|--------|--------|
| **Accountant Portal** | `front-end/accountant-portal` | **Windows first**, macOS later | Tauri shell exists; builds locally; **needs code-signing config** |
| **CEO Dashboard** | `front-end/ceo-dashboard` | macOS only (internal) | ⚠️ **No Tauri shell yet** — must be scaffolded before it can be built |

> The **client/business portal** (`front-end/business-portal`) is the only **web** app — it is deployed to Firebase Hosting (`app.aurora-ltd.co.il`) and is NOT covered here.

The live API is `https://api-aurora-lts.com`. Tauri webviews load from the `tauri://localhost` origin, which **is** in the API CORS allowlist (alongside `https://tauri.localhost`). **All API calls must use the absolute base** `https://api-aurora-lts.com` — relative `/api/...` paths resolve against `tauri://localhost` and 404.

---

## 1. Common prerequisites (build machine)

- **Rust** ≥ 1.77.2 (`rustup`, `cargo`) — Tauri's native side
- **Node.js** ≥ 18 + npm ≥ 10
- **Tauri CLI** — already a devDependency (`@tauri-apps/cli ^2.2.7`); invoked via `npm run tauri`
- **Per-OS native toolchain:**
  - **Windows:** Visual Studio Build Tools (MSVC + Windows SDK) and the **WebView2 runtime** (preinstalled on Win11)
  - **macOS:** Xcode + Command Line Tools (`xcode-select --install`)
  - **Linux (optional):** `libwebkit2gtk-4.1-dev libssl-dev libgtk-3-dev libayatana-appindicator3-dev`

> Tauri builds a binary for the **host OS only** — you cannot cross-compile a Windows `.msi` from macOS. Build the Windows app on Windows, the macOS app on a Mac.

---

## 2. Accountant Portal (Windows-first)

Tauri config (`src-tauri/tauri.conf.json`): productName `Aurora LTS Accountant Portal`, identifier `com.aurora.accountant-portal`, `frontendDist: ../out`, `beforeBuildCommand: npm run build`, `bundle.targets: all`. Next.js is `output: 'export'` → produces `out/`. The `tauri build` step auto-runs the Next build first.

### 2a. Pre-build config
1. Copy env: `cp .env.local.example .env.local` and confirm:
   - `NEXT_PUBLIC_AURORA_API_BASE=https://api-aurora-lts.com`  ← already correct (absolute)
   - `NEXT_PUBLIC_USE_MOCK_API=false`
   - `NEXT_PUBLIC_AURORA_CORE_BASE=<real aurora-api-core URL>` — currently a placeholder. M2 is frozen; M2-only features (copilot) will 404 until set. Safe to leave for an M1-only build.
2. Narrow the bundle target to Windows (optional, faster than `all`): in `tauri.conf.json` set `"bundle": { "targets": ["msi", "nsis"] }` for the Windows build (or pass `--bundles` on the CLI).

### 2b. Local build (UNSIGNED — for testing)
```bash
cd front-end/accountant-portal
npm install
npm run tauri build            # runs `npm run build` (Next export) then bundles
# Output (Windows): src-tauri/target/release/bundle/{msi,nsis}/*.{msi,exe}
# Output (macOS):   src-tauri/target/release/bundle/{dmg,macos}/*.{dmg,app}
```

### 2c. Windows Authenticode signing (production)
Requires an **Authenticode code-signing certificate** (OV/EV `.pfx` from a CA, or an HSM/Azure Key Vault for EV). Then either:

**Option A — Tauri config (`src-tauri/tauri.conf.json` → `bundle.windows`):**
```json
"windows": {
  "certificateThumbprint": "<SHA1 thumbprint of the cert in the Windows cert store>",
  "digestAlgorithm": "sha256",
  "timestampUrl": "http://timestamp.digicert.com"
}
```
Install the `.pfx` into the user/machine cert store first; Tauri signs each artifact during `tauri build`.

**Option B — sign post-build with `signtool`** (if you prefer not to put the thumbprint in config):
```powershell
signtool sign /fd sha256 /f cert.pfx /p $env:CERT_PASSWORD /tr http://timestamp.digicert.com /td sha256 `
  "src-tauri\target\release\bundle\msi\Aurora LTS Accountant Portal_0.1.0_x64_en-US.msi"
```
**Never commit the `.pfx` or its password.** Use a secret store / CI secret; inject as env at build time.

### 2d. macOS signing + notarization (later)
Requires an **Apple Developer ID Application** cert in the login Keychain. Set env, then `tauri build`:
```bash
export APPLE_SIGNING_IDENTITY="Developer ID Application: <Name> (<TEAMID>)"
export APPLE_ID="<apple-id-email>"
export APPLE_PASSWORD="<app-specific-password>"   # appleid.apple.com → App-Specific Passwords
export APPLE_TEAM_ID="<TEAMID>"
npm run tauri build                                # Tauri signs, then notarizes via notarytool + staples
```
For **internal** (non-App-Store) distribution this Developer ID + notarization flow is correct.

### 2e. Known gaps to fix before/with the build
- **Vault upload response mismatch:** frontend `src/types/vault.ts` (≈L81–84) expects `{ ok: true, document: ClientDocument }`, but the backend `services/aurora-main-api/app/routers/accountant_vault.py` (≈L67) returns `{ document_id, status, sha256, bytes_size }`. Align one side (recommended: fix the frontend type/handler to the live backend shape). Blocks the upload UX.
- **Sprint-8.4 vault endpoints not live:** the frontend `vaultApi` calls `list documents`, `get document`, `ingestion-address`, `reclassify`, but only `POST .../documents/manual` is confirmed on the backend. Those vault **reads will 404** until the backend ships them (separate backend task).

---

## 3. CEO Dashboard (macOS, internal) — NOT YET A DESKTOP APP

Current state: three static HTML files (`dashboard.html`, `onboarding.html`, `accountant/index.html`) with vanilla JS + Alpine. **There is no `src-tauri/` shell**, and the code uses **relative `/api/...` paths** (broken from `tauri://localhost`). To make it a real macOS desktop app:

1. **Scaffold a Tauri shell** in `front-end/ceo-dashboard`:
   ```bash
   cd front-end/ceo-dashboard
   npm create tauri-app@latest -- --template vanilla   # or `npm i -D @tauri-apps/cli && npx tauri init`
   ```
   Configure `tauri.conf.json`:
   - `identifier: "com.aurora.ceo-dashboard"`, `productName: "Aurora LTS CEO Dashboard"`
   - `build.frontendDist: "."` (the static HTML dir; no build step), `devUrl` for dev
   - default page → `dashboard.html` (or rename to `index.html`)
   - **CSP** `connect-src`: include `https://api-aurora-lts.com`
   - `bundle.targets: ["app", "dmg"]`
2. **Absolute API base:** add a small config so all calls hit the live API, e.g. at the top of each file:
   ```html
   <script>/* route relative /api/* to the live API */
   (function(){var A="https://api-aurora-lts.com",f=window.fetch.bind(window);
   window.fetch=function(u,o){if(typeof u==="string"&&u.indexOf("/api/")===0)u=A+u;return f(u,o);};})();</script>
   ```
   (or define `const API_BASE='https://api-aurora-lts.com'` and prefix each path).
3. **Endpoint fix (already applied):** `dashboard.html` now calls `/api/v1/organizations` (was `/api/v1/businesses`, which 404s). Verify the response shape matches what `fetchBusinesses()` renders.
4. **Sign + notarize** for macOS exactly as in §2d.

---

## 4. Signing secrets — handling

- Never commit certs (`.pfx`, `.p12`), passwords, or Apple app-specific passwords. They are not in the repo today (correct).
- Inject via environment / a secret manager at build time (local: shell env; CI: encrypted secrets).
- Consider a CI build job (GitHub Actions `tauri-action` or Cloud Build) per-OS once certs exist — currently there is no release/signing workflow.

---

## 5. Quick reference

```bash
# Accountant portal — local unsigned build (run on the target OS)
cd front-end/accountant-portal && npm install && npm run tauri build

# Windows signed (Option A): set certificateThumbprint in tauri.conf.json, then `npm run tauri build` on Windows
# macOS signed+notarized: export APPLE_* env (see §2d), then `npm run tauri build` on a Mac
```

_Last updated: 2026-06-18. Recon basis: accountant-portal Tauri 2.x (`com.aurora.accountant-portal`); CEO dashboard static HTML (no shell)._
