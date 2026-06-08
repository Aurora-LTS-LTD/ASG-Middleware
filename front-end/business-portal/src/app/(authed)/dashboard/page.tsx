"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { FileText, Clock, CheckCircle2, Wallet, ChevronRight } from "lucide-react";
import { Topbar } from "@/components/shell/Topbar";
import { api } from "@/lib/api/client";
import { formatILS } from "@/lib/format/currency";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/invoices/StatusBadge";

export default function DashboardPage() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["invoices"],
    queryFn: () => api.listInvoices(),
    staleTime: 30_000,
  });
  const invoices = data ?? [];
  const count = (s: string) => invoices.filter((i) => i.status === s).length;
  const outstanding = invoices
    .filter((i) => i.status !== "cancelled" && i.payment_status !== "paid")
    .reduce((sum, i) => sum + (i.amount_total - (i.amount_paid || 0)), 0);

  const ph = isLoading ? "…" : isError ? "—" : null;
  const cards = [
    { label: "Total invoices", value: ph ?? String(invoices.length), Icon: FileText, color: "text-indigo-400" },
    { label: "Awaiting ITA", value: ph ?? String(count("pending_allocation")), Icon: Clock, color: "text-amber-400" },
    { label: "Finalized / sent", value: ph ?? String(count("finalized") + count("sent")), Icon: CheckCircle2, color: "text-emerald-400" },
    { label: "Outstanding", value: ph ?? formatILS(outstanding), Icon: Wallet, color: "text-blue-400" },
  ];
  const recent = invoices.slice(0, 6);

  return (
    <>
      <Topbar title="Dashboard" />
      <main className="flex-1 overflow-y-auto p-6">
        <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
          {cards.map(({ label, value, Icon, color }) => (
            <Card key={label} className="border-border bg-card">
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between">
                  <CardDescription className="text-xs text-muted-foreground">{label}</CardDescription>
                  <Icon className={`h-4 w-4 ${color}`} />
                </div>
              </CardHeader>
              <CardContent>
                <div className={`text-2xl font-bold ${color}`}>{value}</div>
              </CardContent>
            </Card>
          ))}
        </div>

        <Card className="border-border bg-card">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-semibold text-foreground">Recent invoices</CardTitle>
              <Link href="/invoices" className="text-xs text-indigo-400 transition-colors hover:text-indigo-300">
                View all →
              </Link>
            </div>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="space-y-2">
                {[0, 1, 2].map((i) => <div key={i} className="h-10 animate-pulse rounded bg-muted/40" />)}
              </div>
            ) : isError ? (
              <p className="py-8 text-center text-sm text-amber-400/80">Couldn&apos;t load invoices — retry shortly.</p>
            ) : recent.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">No invoices yet.</p>
            ) : (
              <ul className="divide-y divide-border">
                {recent.map((inv) => (
                  <li key={inv.id}>
                    <Link
                      href={`/invoices?id=${inv.id}`}
                      className="-mx-2 flex items-center justify-between gap-3 rounded px-2 py-2.5 transition-colors hover:bg-accent/40"
                    >
                      <div className="min-w-0">
                        <div className="truncate text-sm text-foreground">
                          {inv.invoice_number} · {inv.beneficiary_name}
                        </div>
                        <div className="text-[11px] text-muted-foreground">{formatILS(inv.amount_total)}</div>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <StatusBadge status={inv.status} />
                        <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </main>
    </>
  );
}
