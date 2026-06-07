import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium transition-colors",
  {
    variants: {
      variant: {
        default:     "bg-muted text-foreground border border-border",
        indigo:      "bg-indigo-500/15 text-indigo-400 border border-indigo-500/30",
        green:       "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30",
        yellow:      "bg-yellow-500/15 text-yellow-400 border border-yellow-500/30",
        red:         "bg-red-500/15 text-red-400 border border-red-500/30",
        blue:        "bg-blue-500/15 text-blue-400 border border-blue-500/30",
        outline:     "border border-border text-muted-foreground bg-transparent",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
