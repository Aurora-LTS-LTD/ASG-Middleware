"use client";

import { cn } from "@/lib/utils";
import type { EngineHealth, HealthState } from "@/lib/api/health";

const DOT: Record<HealthState, { dot: string; glow: string; ping: boolean }> = {
  online: { dot: "bg-emerald-400", glow: "shadow-[0_0_8px_2px_rgba(52,211,153,0.55)]", ping: true },
  degraded: { dot: "bg-amber-400", glow: "shadow-[0_0_8px_2px_rgba(251,191,36,0.55)]", ping: true },
  offline: { dot: "bg-red-500", glow: "shadow-[0_0_8px_2px_rgba(239,68,68,0.55)]", ping: false },
  checking: { dot: "bg-zinc-500", glow: "", ping: false },
};

export function HealthDot({
  health,
  showDetail = false,
}: {
  health: EngineHealth;
  showDetail?: boolean;
}) {
  const s = DOT[health.state];
  return (
    <span className="inline-flex items-center gap-2" title={health.detail}>
      <span className="relative inline-flex h-2.5 w-2.5">
        {s.ping && (
          <span
            className={cn(
              "absolute inline-flex h-full w-full animate-ping rounded-full opacity-60",
              s.dot,
            )}
          />
        )}
        <span className={cn("relative inline-flex h-2.5 w-2.5 rounded-full", s.dot, s.glow)} />
      </span>
      {showDetail && (
        <span className="text-[10px] font-medium uppercase tracking-wider text-zinc-500">
          {health.detail}
        </span>
      )}
    </span>
  );
}
