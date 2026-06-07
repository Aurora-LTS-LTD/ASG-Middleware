"use client";

import { useQuery } from "@tanstack/react-query";
import { Sparkles, GitBranch, Activity, Lock, ShieldCheck } from "lucide-react";
import { apiM2 } from "@/lib/api/client";
import { m2Key } from "@/lib/api/queryKeys";
import { ENGINES } from "@/lib/cockpit/context";
import { PanelShell, PanelSection, EndpointPending } from "@/components/cockpit/Panel";

/** Shape of GET /api/v1/core/health (main_core.py) — open + unauthenticated. */
interface CoreHealth {
  service: string;
  status: string;
  compliance_backends: "stubbed" | "live";
  timestamp: string;
}

/**
 * Right panel — AI Operations Core (M2).
 *
 * "Core posture" is the ONE live call that exercises the engine-bound M2
 * client (apiM2): it hits the open /api/v1/core/health and surfaces the
 * compliance-selector posture, so the cockpit genuinely talks to BOTH
 * backends through their isolated clients — not just a raw health dot.
 *
 * The Gemini Copilot endpoints exist under /api/v1/admin/exec/copilot/*
 * behind admin + IAP + WebAuthn step-up; an accountant-role token can't pass
 * that gate, so the chat stays an explicit, disabled scaffold (M2 feature
 * work is paused while we focus on M1). Blueprint logs / anomaly charts have
 * no router yet and remain honest placeholders.
 */
export function M2Panel() {
  const { data, isLoading, isError } = useQuery({
    queryKey: m2Key("core", "health"), // engine-namespaced (M2) — isolated from M1 cache
    queryFn: () => apiM2.get<CoreHealth>("/api/v1/core/health"), // open endpoint, no auth
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  const postureLive = data?.compliance_backends === "live";
  const posture = isLoading ? "checking…" : isError ? "unreachable" : data?.compliance_backends ?? "unknown";

  return (
    <PanelShell engine={ENGINES.m2} accent="text-fuchsia-300">
      <PanelSection title="Core posture" hint="live via apiM2">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="inline-flex items-center gap-1.5 text-sm text-foreground">
              <ShieldCheck className={`h-4 w-4 ${postureLive ? "text-emerald-400" : "text-amber-400"}`} />
              AI Core service
            </span>
            <span
              className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${
                isError
                  ? "bg-red-500/15 text-red-300"
                  : postureLive
                    ? "bg-emerald-500/15 text-emerald-300"
                    : "bg-amber-500/15 text-amber-300"
              }`}
            >
              {isError ? "unreachable" : (data?.status ?? "…")}
            </span>
          </div>
          <p className="mt-1.5 text-[11px] text-muted-foreground">
            Compliance backends:{" "}
            <span className={postureLive ? "text-emerald-400" : "text-amber-400"}>{posture}</span>
            {" — "}probed through the engine-bound M2 client.
          </p>
        </div>
      </PanelSection>

      <PanelSection title="Gemini Copilot" hint="engine: m2">
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-2 text-sm text-foreground">
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
            className="mt-3 h-20 w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground disabled:opacity-60"
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
