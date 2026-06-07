"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  LayoutDashboard,
  Gauge,
  Archive,
  Users,
  MonitorSmartphone,
  Settings,
  LogOut,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { useAuth } from "@/lib/auth/context"

// NOTE: route groups like (authed) are STRIPPED from the URL in Next.js 15,
// so the live paths are "/", "/cockpit", … — NOT "/(authed)/…" (which 404s).
const NAV_ITEMS = [
  { href: "/",        label: "Dashboard", icon: LayoutDashboard },
  { href: "/cockpit", label: "Founder's Cockpit", icon: Gauge },
  { href: "/vault",   label: "Document Vault",  icon: Archive },
  { href: "/clients", label: "Clients",   icon: Users },
  { href: "/devices", label: "Devices",   icon: MonitorSmartphone },
  { href: "/settings", label: "Settings", icon: Settings },
] as const

export function Sidebar() {
  const pathname = usePathname()
  const { signOut } = useAuth()

  return (
    <aside className="flex h-full w-56 flex-col border-r border-border bg-background">
      {/* Logo */}
      <div className="flex items-center gap-3 px-4 py-5 border-b border-border">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 shadow-lg shadow-indigo-500/20 shrink-0">
          <span className="text-sm font-bold text-white">A</span>
        </div>
        <div className="leading-tight">
          <p className="text-sm font-semibold text-foreground">Aurora LTS</p>
          <p className="text-[10px] text-muted-foreground">Accountant Portal</p>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-2 py-3 space-y-0.5">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || (href !== "/" && pathname.startsWith(href))
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-indigo-600/20 text-indigo-300"
                  : "text-muted-foreground hover:bg-card hover:text-foreground",
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
            </Link>
          )
        })}
      </nav>

      {/* Sign out */}
      <div className="px-2 py-3 border-t border-border">
        <button
          onClick={() => signOut()}
          className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-muted-foreground hover:bg-card hover:text-foreground transition-colors"
        >
          <LogOut className="h-4 w-4 shrink-0" />
          Sign out
        </button>
      </div>
    </aside>
  )
}
