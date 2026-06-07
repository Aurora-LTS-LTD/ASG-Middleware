"use client";

import { useEngineHealth } from "@/lib/api/health";
import { HealthDot } from "@/components/cockpit/HealthDot";
import type { EngineConfig } from "@/lib/cockpit/context";

/**
 * The chrome every cockpit panel shares: a sticky header with the engine
 * name, its live connectivity dot, the base URL it's bound to, and a
 * scrollable body. Keeping both halves identical is what makes the
 * split-screen read as "two engines, one cockpit".
 */
export function PanelShell({
  engine,
  accent,
  children,
}: {
  engine: EngineConfig;
  /** Tailwind text-color class for the engine's accent (e.g. text-indigo-400). */
  accent: string;
  children: React.ReactNode;
}) {
  const health = useEngineHealth(engine);

  return (
    <section className="flex h-full min-w-0 flex-col bg-background">
      <header className="flex items-center justify-between gap-3 border-b border-border bg-card/40 px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-2.5">
          <HealthDot health={health} />
          <div className="min-w-0">
            <h2 className={`truncate text-sm font-semibold ${accent}`}>{engine.label}</h2>
            <p className="truncate text-[10px] text-muted-foreground">{engine.url}</p>
          </div>
        </div>
        <span className="shrink-0 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          {health.detail}
        </span>
      </header>
      <div className="flex-1 overflow-y-auto p-4">{children}</div>
    </section>
  );
}

export function PanelSection({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-5">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{title}</h3>
        {hint && <span className="text-[10px] text-muted-foreground">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

/**
 * Honest placeholder for a surface whose backend endpoint does not exist
 * yet (e.g. anomaly charts, blueprint logs). Renders as an explicit
 * "pending" card rather than faking data.
 */
export function EndpointPending({ label, path }: { label: React.ReactNode; path?: string }) {
  return (
    <div className="rounded-lg border border-dashed border-border bg-card/30 p-3">
      <div className="text-sm text-foreground">{label}</div>
      <div className="mt-0.5 text-[11px] text-muted-foreground">
        No backend endpoint yet{path ? ` — expected ${path}` : ""}. Wire this once the
        route ships.
      </div>
    </div>
  );
}
