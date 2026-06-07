"use client"

import { ShieldCheck } from "lucide-react"
import { useAuth } from "@/lib/auth/context"
import { LocaleSwitcher } from "@/components/shell/LocaleSwitcher" // P2-14

export function Topbar({ title }: { title: string }) {
  const { user } = useAuth()

  return (
    <header className="flex h-12 items-center justify-between border-b border-zinc-800 bg-zinc-950 px-6">
      <h1 className="text-sm font-semibold text-zinc-100">{title}</h1>

      <div className="flex items-center gap-4">
        {user && (
          <div className="text-right leading-tight">
            <p className="text-xs font-medium text-zinc-200">{user.name}</p>
            {user.firm_name && (
              <p className="text-[10px] text-zinc-500">{user.firm_name}</p>
            )}
          </div>
        )}
        {/* P2-14 — language switcher */}
        <LocaleSwitcher />
        <div className="flex items-center gap-1.5 rounded-full border border-zinc-800 bg-zinc-900 px-2.5 py-1">
          <ShieldCheck className="h-3 w-3 text-emerald-500" />
          <span className="text-[10px] font-medium text-zinc-400">Zero-Trust</span>
        </div>
      </div>
    </header>
  )
}
