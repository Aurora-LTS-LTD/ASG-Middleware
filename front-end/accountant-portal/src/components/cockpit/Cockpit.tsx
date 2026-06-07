"use client";

import { ShieldCheck } from "lucide-react";
import { useAuth } from "@/lib/auth/context";
import { useCockpit, ENGINES, type EngineConfig } from "@/lib/cockpit/context";
import { useEngineHealth } from "@/lib/api/health";
import { HealthDot } from "@/components/cockpit/HealthDot";
import { ViewSwitcher } from "@/components/cockpit/ViewSwitcher";
import { M1Panel } from "@/components/cockpit/M1Panel";
import { M2Panel } from "@/components/cockpit/M2Panel";
import { ErrorBoundary } from "@/components/shell/ErrorBoundary";

/** Per-pane fallback — keeps one engine's render error from blanking the other. */
function PaneError({ engine }: { engine: string }) {
  return (
    <div className="flex h-full items-center justify-center p-6 text-center">
      <p className="max-w-xs text-sm text-muted-foreground">
        The {engine} panel hit an error and stopped rendering. The other engine is
        unaffected — reload to retry.
      </p>
    </div>
  );
}

/** Compact engine status chip for the cockpit header (React Query dedupes
 *  the probe with the per-panel dots — no double polling). */
function EngineStatusChip({ engine }: { engine: EngineConfig }) {
  const health = useEngineHealth(engine);
  return (
    <div className="hidden items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-1 md:flex">
      <HealthDot health={health} />
      <span className="text-[10px] font-medium text-muted-foreground">{engine.label}</span>
    </div>
  );
}

export function Cockpit() {
  const { user } = useAuth();
  const { viewMode, workspace } = useCockpit();

  return (
    <div className="flex h-full flex-col bg-background">
      {/* Header / command bar */}
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-border bg-background px-4">
        <div className="flex items-center gap-3">
          <h1 className="text-sm font-semibold text-foreground">Founder&apos;s Cockpit</h1>
          <ViewSwitcher />
        </div>

        <div className="flex items-center gap-2">
          <EngineStatusChip engine={ENGINES.m1} />
          <EngineStatusChip engine={ENGINES.m2} />
          {user && (
            <div className="ml-1 hidden text-right leading-tight lg:block">
              <p className="text-xs font-medium text-foreground">{user.name}</p>
              {user.firm_name && <p className="text-[10px] text-muted-foreground">{user.firm_name}</p>}
            </div>
          )}
          <div className="flex items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-1">
            <ShieldCheck className="h-3 w-3 text-emerald-500" />
            <span className="text-[10px] font-medium text-muted-foreground">Zero-Trust</span>
          </div>
        </div>
      </header>

      {/* Body — split-screen (HStack) or single workspace */}
      {viewMode === "split" ? (
        <div className="flex flex-1 overflow-hidden">
          <div className="min-w-0 flex-1 border-r border-border">
            <ErrorBoundary fallback={<PaneError engine="M1 (Tax/Compliance)" />}>
              <M1Panel />
            </ErrorBoundary>
          </div>
          <div className="min-w-0 flex-1">
            <ErrorBoundary fallback={<PaneError engine="M2 (AI Core)" />}>
              <M2Panel />
            </ErrorBoundary>
          </div>
        </div>
      ) : (
        <div className="flex-1 overflow-hidden">
          {workspace === "m1" ? (
            <ErrorBoundary fallback={<PaneError engine="M1 (Tax/Compliance)" />}>
              <M1Panel />
            </ErrorBoundary>
          ) : (
            <ErrorBoundary fallback={<PaneError engine="M2 (AI Core)" />}>
              <M2Panel />
            </ErrorBoundary>
          )}
        </div>
      )}
    </div>
  );
}
