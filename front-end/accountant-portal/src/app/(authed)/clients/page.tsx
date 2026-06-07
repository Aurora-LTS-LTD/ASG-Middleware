"use client";

/**
 * Clients — master/detail, all live from M1.
 *
 * Static-export note: `output: 'export'` can't build a `/clients/[id]` dynamic
 * route (no build-time IDs), so the selected client is carried in the URL as
 * `?org=<id>` and the detail renders client-side. `useSearchParams()` must sit
 * inside a <Suspense> boundary under static export — hence the split below.
 */

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  Building2, FileText, Wallet, Inbox, ChevronRight, ReceiptText, Download, TrendingUp,
} from "lucide-react";

import { Topbar } from "@/components/shell/Topbar";
import { api } from "@/lib/api/client";
import { m1Key } from "@/lib/api/queryKeys";
import { formatILS, toMajorUnits } from "@/lib/format/currency";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { IncomeExpenseChart, ExpenseCategoryChart } from "@/components/charts/FinanceCharts";
import type { BookItem } from "@/types/api";

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

export default function ClientsPage() {
  return (
    <>
      <Topbar title="Clients" />
      <Suspense
        fallback={
          <main className="flex-1 overflow-y-auto p-6">
            <div className="text-sm text-zinc-600">Loading clients…</div>
          </main>
        }
      >
        <ClientsInner />
      </Suspense>
    </>
  );
}

function ClientsInner() {
  const router = useRouter();
  const params = useSearchParams();
  const selectedId = params.get("org") ? Number(params.get("org")) : null;

  const book = useQuery({
    queryKey: m1Key("accountant", "book"),
    queryFn: () => api.getAccountantBook(),
    staleTime: 30_000,
  });

  const clients = book.data?.items ?? [];

  return (
    <main className="flex-1 overflow-hidden">
      <div className="flex h-full">
        {/* Master — client list */}
        <aside className="w-80 shrink-0 overflow-y-auto border-r border-zinc-800 p-4">
          <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-zinc-400">
            Your clients {clients.length > 0 && <span className="text-zinc-600">({clients.length})</span>}
          </h2>

          {book.isLoading ? (
            <div className="space-y-2">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-16 animate-pulse rounded-lg bg-zinc-900" />
              ))}
            </div>
          ) : book.isError ? (
            <p className="text-[11px] text-amber-400/80">Client book unreachable — retry shortly.</p>
          ) : clients.length === 0 ? (
            <p className="text-[11px] text-zinc-600">
              No active client engagements yet. Clients you&apos;re engaged with will appear here.
            </p>
          ) : (
            <ul className="space-y-2">
              {clients.map((c) => (
                <ClientRow
                  key={c.id}
                  client={c}
                  selected={c.id === selectedId}
                  onSelect={() => router.push(`/clients?org=${c.id}`)}
                />
              ))}
            </ul>
          )}
        </aside>

        {/* Detail */}
        <section className="flex-1 overflow-y-auto p-6">
          {selectedId == null ? (
            <div className="flex h-full flex-col items-center justify-center text-center">
              <Building2 className="mb-4 h-10 w-10 text-zinc-700" />
              <p className="text-sm text-zinc-500">Select a client to view their tax summary.</p>
            </div>
          ) : (
            <ClientDetail orgId={selectedId} />
          )}
        </section>
      </div>
    </main>
  );
}

function ClientRow({
  client, selected, onSelect,
}: { client: BookItem; selected: boolean; onSelect: () => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        className={`w-full rounded-lg border p-3 text-left transition-colors ${
          selected
            ? "border-indigo-500/50 bg-indigo-500/10"
            : "border-zinc-800 bg-zinc-900 hover:border-zinc-700"
        }`}
      >
        <div className="flex items-center justify-between gap-2">
          <span className="inline-flex min-w-0 items-center gap-1.5 truncate text-sm text-zinc-200">
            <Building2 className="h-3.5 w-3.5 shrink-0 text-indigo-400" />
            {client.display_name}
          </span>
          {client.review_queue_count > 0 && (
            <span className="shrink-0 rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium text-amber-300">
              {client.review_queue_count}
            </span>
          )}
        </div>
        <div className="mt-1.5 flex items-center justify-between text-[11px] text-zinc-500">
          <span className="inline-flex items-center gap-1">
            <FileText className="h-3 w-3" />
            {client.invoice_count} inv
          </span>
          <span>{formatILS(client.outstanding_amount)} due</span>
          <ChevronRight className="h-3.5 w-3.5 text-zinc-700" />
        </div>
      </button>
    </li>
  );
}

function StatCard({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent: string }) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-3">
      <div className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</div>
      <div className={`mt-1 text-lg font-bold ${accent}`}>{value}</div>
      {sub && <div className="mt-0.5 text-[10px] text-zinc-600">{sub}</div>}
    </div>
  );
}

function ClientDetail({ orgId }: { orgId: number }) {
  const summary = useQuery({
    queryKey: m1Key("client", orgId, "summary"),
    queryFn: () => api.getOrgSummary(orgId),
    staleTime: 30_000,
  });
  const exports = useQuery({
    queryKey: m1Key("client", orgId, "exports"),
    queryFn: () => api.getOrgExports(orgId),
    staleTime: 30_000,
  });

  if (summary.isLoading) {
    return (
      <div className="space-y-4">
        <div className="h-8 w-64 animate-pulse rounded bg-zinc-800/60" />
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => <div key={i} className="h-20 animate-pulse rounded-lg bg-zinc-900" />)}
        </div>
        <div className="h-48 animate-pulse rounded-lg bg-zinc-900" />
      </div>
    );
  }
  if (summary.isError || !summary.data) {
    return <p className="py-16 text-center text-sm text-amber-400/80">Couldn&apos;t load this client&apos;s summary. Retry shortly.</p>;
  }

  const s = summary.data;
  const incomeILS = s.income.total_amount;
  const expenseILS = toMajorUnits(s.expenses.total_amount_minor_units, true);
  const hasExpenses = s.expenses.by_category.length > 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold text-zinc-100">{s.organization.display_name}</h2>
          <span className="rounded-full border border-zinc-700 px-2 py-0.5 text-[10px] uppercase tracking-wider text-zinc-400">
            {s.organization.legal_structure}
          </span>
        </div>
        <p className="mt-0.5 text-xs text-zinc-500">
          Tax ID {s.organization.tax_id} · KYC {s.organization.kyc_status} · Period {fmtDate(s.period.start)} – {fmtDate(s.period.end)}
        </p>
      </div>

      {/* VAT + queue stat cards */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="VAT collected" value={formatILS(s.vat.collected_minor_units, { minorUnits: true })} sub={`${s.vat.rate_pct}% rate`} accent="text-emerald-400" />
        <StatCard label="VAT paid" value={formatILS(s.vat.paid_minor_units, { minorUnits: true })} sub="Input VAT" accent="text-blue-400" />
        <StatCard label="VAT due" value={formatILS(s.vat.due_minor_units, { minorUnits: true })} sub="Output − input" accent="text-amber-400" />
        <StatCard label="Review queue" value={String(s.review_queue_count)} sub="Receipts to review" accent={s.review_queue_count > 0 ? "text-amber-400" : "text-zinc-300"} />
      </div>

      {/* P&L + expenses-by-category */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card className="border-zinc-800 bg-zinc-900">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-semibold text-zinc-200">Income vs expenses</CardTitle>
              <TrendingUp className="h-4 w-4 text-emerald-400" />
            </div>
            <CardDescription className="text-xs text-zinc-500">
              {s.income.invoice_count} invoices · {formatILS(incomeILS)} in · {formatILS(expenseILS)} out
            </CardDescription>
          </CardHeader>
          <CardContent>
            {incomeILS === 0 && expenseILS === 0 ? (
              <p className="py-12 text-center text-sm text-zinc-600">No income or expenses this period.</p>
            ) : (
              <IncomeExpenseChart incomeILS={incomeILS} expenseILS={expenseILS} />
            )}
          </CardContent>
        </Card>

        <Card className="border-zinc-800 bg-zinc-900">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-semibold text-zinc-200">Expenses by category</CardTitle>
              <ReceiptText className="h-4 w-4 text-fuchsia-400" />
            </div>
            <CardDescription className="text-xs text-zinc-500">This period</CardDescription>
          </CardHeader>
          <CardContent>
            {hasExpenses ? (
              <ExpenseCategoryChart categories={s.expenses.by_category} />
            ) : (
              <p className="py-12 text-center text-sm text-zinc-600">No categorised expenses this period.</p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Exports */}
      <Card className="border-zinc-800 bg-zinc-900">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm font-semibold text-zinc-200">Exports</CardTitle>
            <Inbox className="h-4 w-4 text-indigo-400" />
          </div>
          <CardDescription className="text-xs text-zinc-500">Uniform-file / Hashavshevet history</CardDescription>
        </CardHeader>
        <CardContent>
          {exports.isLoading ? (
            <div className="h-16 animate-pulse rounded-lg bg-zinc-800/40" />
          ) : exports.isError ? (
            <p className="text-[11px] text-amber-400/80">Couldn&apos;t load exports.</p>
          ) : (exports.data?.items.length ?? 0) === 0 ? (
            <p className="text-[11px] text-zinc-600">No exports generated yet.</p>
          ) : (
            <ul className="divide-y divide-zinc-800">
              {exports.data!.items.map((e) => (
                <li key={e.id} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                  <div className="min-w-0">
                    <div className="truncate text-zinc-200">
                      {e.format} · {fmtDate(e.period_start)} – {fmtDate(e.period_end)}
                    </div>
                    <div className="text-[11px] text-zinc-600">
                      {e.status}
                      {e.record_count != null ? ` · ${e.record_count} records` : ""}
                      {` · ${fmtDate(e.created_at)}`}
                    </div>
                  </div>
                  {e.signed_url ? (
                    <a
                      href={e.signed_url}
                      className="inline-flex shrink-0 items-center gap-1 rounded-md border border-zinc-700 px-2 py-1 text-[11px] text-zinc-200 hover:border-indigo-500 hover:text-indigo-300 transition-colors"
                    >
                      <Download className="h-3 w-3" />
                      Download
                    </a>
                  ) : (
                    <span className="shrink-0 text-[11px] text-zinc-600">{e.status}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
