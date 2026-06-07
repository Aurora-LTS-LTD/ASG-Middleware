"use client"

import { useQuery } from "@tanstack/react-query"
import { Topbar } from "@/components/shell/Topbar"
import { useAuth } from "@/lib/auth/context"
import { api } from "@/lib/api/client"
import { m1Key } from "@/lib/api/queryKeys"
import { EarningsTrendChart } from "@/components/charts/FinanceCharts"
import { formatILS } from "@/lib/format/currency"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Archive, MonitorSmartphone, Users, ShieldCheck, AlertTriangle, ShieldAlert, TrendingUp } from "lucide-react"
import { type LucideIcon } from "lucide-react"

type SecurityStatus = "ok" | "warning" | "critical"

function securityValueAndIcon(status: SecurityStatus | undefined): {
  text: string
  Icon: LucideIcon
  color: string
} {
  switch (status) {
    case "critical":
      return { text: "Critical", Icon: ShieldAlert, color: "text-red-400" }
    case "warning":
      return { text: "Review", Icon: AlertTriangle, color: "text-amber-400" }
    case "ok":
    default:
      return { text: "✓", Icon: ShieldCheck, color: "text-emerald-400" }
  }
}

export default function DashboardPage() {
  const { user } = useAuth()
  const firstName = user?.name?.split(" ")[0] || "Accountant"

  // P1-16: live KPI fetch. Re-fetches when the window regains focus so
  // the dashboard reflects new vault uploads / device changes without
  // a manual reload.
  const { data, isLoading, isError } = useQuery({
    queryKey: ["dashboard-kpis"],
    queryFn: () => api.getDashboardKpis(),
    refetchOnWindowFocus: true,
    staleTime: 30_000,
  })

  // Revenue-share earnings (12-month trend chart). Independent of KPIs so a
  // slow/empty earnings response never blocks the hero cards.
  const earnings = useQuery({
    queryKey: m1Key("dashboard", "earnings"),
    queryFn: () => api.getEarnings(),
    staleTime: 60_000,
  })
  const hasEarnings = (earnings.data?.periods_last_12 ?? []).some(
    (p) => p.total_amount_minor_units > 0,
  )

  const placeholder = isLoading ? "…" : isError ? "—" : null
  const security = securityValueAndIcon(data?.security_status)

  const cards = [
    {
      label: "Document Vault",
      description: "Files received this month",
      icon: Archive,
      value: placeholder ?? String(data?.vault_docs_this_month ?? 0),
      color: "text-indigo-400",
    },
    {
      label: "Active Clients",
      description: "Engaged client organizations",
      icon: Users,
      value: placeholder ?? String(data?.active_clients ?? 0),
      color: "text-emerald-400",
    },
    {
      label: "Active Devices",
      description: "Registered devices for your account",
      icon: MonitorSmartphone,
      value: placeholder ?? String(data?.active_devices ?? 0),
      color: "text-blue-400",
    },
    {
      label: "Security Status",
      description: "Zero-trust advisory binding",
      icon: security.Icon,
      value: placeholder ?? security.text,
      color: security.color,
    },
  ]

  return (
    <>
      <Topbar title="Dashboard" />
      <main className="flex-1 overflow-y-auto p-6">
        <div className="mb-8">
          <h2 className="text-xl font-semibold text-zinc-100">
            Welcome back, {firstName}
          </h2>
          <p className="mt-1 text-sm text-zinc-500">
            {user?.firm_name ? `${user.firm_name} · ` : ""}Aurora LTS Accountant Portal
          </p>
        </div>

        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {cards.map(({ label, description, icon: Icon, value, color }) => (
            <Card key={label} className="border-zinc-800 bg-zinc-900">
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between">
                  <CardDescription className="text-xs text-zinc-500">{label}</CardDescription>
                  <Icon className={`h-4 w-4 ${color}`} />
                </div>
              </CardHeader>
              <CardContent>
                <div className={`text-2xl font-bold ${color}`}>{value}</div>
                <p className="mt-1 text-[11px] text-zinc-600">{description}</p>
              </CardContent>
            </Card>
          ))}
        </div>

        {isError && (
          <div className="mt-6 rounded-xl border border-amber-700/40 bg-amber-900/10 p-4 text-sm text-amber-200">
            KPI service unreachable. Numbers above are placeholders until the
            connection recovers.
          </div>
        )}

        <div className="mt-6">
          <Card className="border-zinc-800 bg-zinc-900">
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm font-semibold text-zinc-200">
                  Earnings — last 12 months
                </CardTitle>
                <TrendingUp className="h-4 w-4 text-indigo-400" />
              </div>
              <CardDescription className="text-xs text-zinc-500">
                {earnings.data
                  ? `Lifetime paid ${formatILS(earnings.data.lifetime_total_paid_minor_units, { minorUnits: true })}`
                  : "Revenue-share accruals"}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {earnings.isLoading ? (
                <div className="h-[200px] animate-pulse rounded-lg bg-zinc-800/40" />
              ) : earnings.isError ? (
                <p className="py-16 text-center text-sm text-amber-400/80">
                  Earnings service unreachable — retry shortly.
                </p>
              ) : hasEarnings ? (
                <EarningsTrendChart periods={earnings.data!.periods_last_12} />
              ) : (
                <p className="py-16 text-center text-sm text-zinc-600">
                  No earnings recorded yet. Your revenue-share accruals will appear here.
                </p>
              )}
            </CardContent>
          </Card>
        </div>
      </main>
    </>
  )
}
