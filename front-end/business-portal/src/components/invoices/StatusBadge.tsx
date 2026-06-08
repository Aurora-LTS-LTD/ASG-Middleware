import { cn } from "@/lib/utils";

/**
 * Lifecycle status badge. Brand-accent colors read on both light + dark; the
 * neutral "draft" uses theme tokens so it flips correctly.
 */
const MAP: Record<string, { label: string; cls: string }> = {
  draft: { label: "Draft", cls: "bg-muted text-muted-foreground border-border" },
  pending_allocation: { label: "Pending ITA", cls: "bg-amber-500/15 text-amber-400 border-amber-500/30" },
  finalized: { label: "Finalized", cls: "bg-blue-500/15 text-blue-400 border-blue-500/30" },
  sent: { label: "Sent", cls: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30" },
  cancelled: { label: "Cancelled", cls: "bg-red-500/15 text-red-400 border-red-500/30" },
};

export function StatusBadge({ status }: { status: string }) {
  const m = MAP[status] ?? { label: status, cls: "bg-muted text-muted-foreground border-border" };
  return (
    <span className={cn("inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium", m.cls)}>
      {m.label}
    </span>
  );
}

/** Allocation sub-status chip (only meaningful when an ITA number is required). */
const ALLOC: Record<string, { label: string; cls: string }> = {
  approved: { label: "ITA approved", cls: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30" },
  rejected: { label: "ITA rejected", cls: "bg-red-500/15 text-red-400 border-red-500/30" },
  retry_pending: { label: "ITA retrying", cls: "bg-amber-500/15 text-amber-400 border-amber-500/30" },
  failed: { label: "ITA failed", cls: "bg-amber-500/15 text-amber-400 border-amber-500/30" },
  pending: { label: "ITA pending", cls: "bg-muted text-muted-foreground border-border" },
};

export function AllocationBadge({ status }: { status: string }) {
  if (status === "not_required") return null;
  const m = ALLOC[status] ?? { label: status, cls: "bg-muted text-muted-foreground border-border" };
  return (
    <span className={cn("inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium", m.cls)}>
      {m.label}
    </span>
  );
}
