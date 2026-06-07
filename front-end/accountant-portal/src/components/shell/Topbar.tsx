"use client"

import { ShieldCheck } from "lucide-react"
import { LocaleSwitcher } from "@/components/shell/LocaleSwitcher" // P2-14
import { ThemeSwitcher } from "@/components/shell/ThemeSwitcher"   // P3 — light/dark
import { UserMenu } from "@/components/shell/UserMenu"             // P3 — account menu

export function Topbar({ title }: { title: string }) {
  return (
    <header className="flex h-12 items-center justify-between border-b border-border bg-background px-6">
      <h1 className="text-sm font-semibold text-foreground">{title}</h1>

      <div className="flex items-center gap-3">
        <div className="hidden items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-1 md:flex">
          <ShieldCheck className="h-3 w-3 text-emerald-500" />
          <span className="text-[10px] font-medium text-muted-foreground">Zero-Trust</span>
        </div>
        {/* P3 — theme toggle + P2-14 language switcher + account menu */}
        <ThemeSwitcher />
        <LocaleSwitcher />
        <UserMenu />
      </div>
    </header>
  )
}
