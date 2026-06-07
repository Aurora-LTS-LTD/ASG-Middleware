import { MessageCircle, Mail, Monitor } from "lucide-react"
import { cn } from "@/lib/utils"
import type { UploadVector } from "@/types/vault"

const CONFIG: Record<UploadVector, { label: string; icon: React.ElementType; className: string }> = {
  whatsapp: {
    label: "WhatsApp",
    icon: MessageCircle,
    className: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  },
  email: {
    label: "Email",
    icon: Mail,
    className: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  },
  manual: {
    label: "Manual",
    icon: Monitor,
    className: "bg-zinc-700/50 text-zinc-400 border-zinc-700",
  },
}

interface Props {
  vector: UploadVector
  showLabel?: boolean
  className?: string
}

export function UploadSourceBadge({ vector, showLabel = true, className }: Props) {
  const { label, icon: Icon, className: colorClass } = CONFIG[vector] ?? CONFIG.manual
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium",
        colorClass,
        className,
      )}
    >
      <Icon className="h-3 w-3 shrink-0" />
      {showLabel && label}
    </span>
  )
}
