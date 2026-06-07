"use client";

import { Sparkles, GitBranch, Activity, Lock } from "lucide-react";
import { ENGINES } from "@/lib/cockpit/context";
import { PanelShell, PanelSection, EndpointPending } from "@/components/cockpit/Panel";

/**
 * Right panel — AI Operations Core (M2).
 * The Gemini Copilot endpoints DO exist on the core server, but under
 * /api/v1/admin/exec/copilot/* behind admin + IAP + WebAuthn step-up — an
 * accountant-role token won't pass that gate. So this renders a Copilot
 * scaffold (wired to engine "m2") with an explicit auth note, plus honest
 * placeholders for blueprint logs and anomaly charts (no endpoints yet).
 */
export function M2Panel() {
  return (
    <PanelShell engine={ENGINES.m2} accent="text-fuchsia-300">
      <PanelSection title="Gemini Copilot" hint="engine: m2">
        <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-4">
          <div className="flex items-center gap-2 text-sm text-zinc-200">
            <Sparkles className="h-4 w-4 text-fuchsia-400" />
            Active Copilot session
          </div>
          <div className="mt-3 flex items-center gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-300">
            <Lock className="h-3.5 w-3.5 shrink-0" />
            Copilot lives at <code className="mx-1 font-mono">/api/v1/admin/exec/copilot/*</code>
            (admin + IAP + step-up). An accountant token can&apos;t open a session — gate the
            chat input on exec role before enabling.
          </div>
          <textarea
            disabled
            placeholder="Ask the Copilot… (disabled until exec auth is wired)"
            className="mt-3 h-20 w-full resize-none rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-300 placeholder:text-zinc-600 disabled:opacity-60"
          />
        </div>
      </PanelSection>

      <PanelSection title="Blueprint inheritance logs">
        <EndpointPending
          label={<span className="inline-flex items-center gap-1.5"><GitBranch className="h-3.5 w-3.5" />Template inheritance + activation trail</span>}
          path="GET /api/v1/admin/exec/blueprints/logs"
        />
      </PanelSection>

      <PanelSection title="Anomaly insights">
        <EndpointPending
          label={<span className="inline-flex items-center gap-1.5"><Activity className="h-3.5 w-3.5" />Reconciliation / anomaly charts</span>}
          path="(no router exists yet)"
        />
      </PanelSection>
    </PanelShell>
  );
}
