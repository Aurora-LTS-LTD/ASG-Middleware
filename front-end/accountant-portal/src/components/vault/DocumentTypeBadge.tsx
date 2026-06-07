import { cn } from "@/lib/utils"
import type { DocumentType } from "@/types/vault"

const CONFIG: Record<DocumentType, { label: string; className: string }> = {
  expense:      { label: "Expense",      className: "bg-orange-500/10 text-orange-400 border-orange-500/20"  },
  revenue:      { label: "Revenue",      className: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" },
  statement:    { label: "Statement",    className: "bg-indigo-500/10 text-indigo-400 border-indigo-500/20"  },
  unclassified: { label: "Unclassified", className: "bg-muted/50 text-muted-foreground border-border"          },
}

interface Props {
  type: DocumentType
  className?: string
}

export function DocumentTypeBadge({ type, className }: Props) {
  const { label, className: colorClass } = CONFIG[type] ?? CONFIG.unclassified
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium",
        colorClass,
        className,
      )}
    >
      {label}
    </span>
  )
}
