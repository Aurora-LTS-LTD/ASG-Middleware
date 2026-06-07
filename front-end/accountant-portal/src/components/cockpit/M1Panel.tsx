"use client";

import { useQuery } from "@tanstack/react-query";
import { Archive, Users, MonitorSmartphone, Building2, FileText, Wallet, MessageSquare } from "lucide-react";
import { api, apiM1 } from "@/lib/api/client";
import { m1Key } from "@/lib/api/queryKeys";
import { ENGINES } from "@/lib/cockpit/context";
import { PanelShell, PanelSection, EndpointPending } from "@/components/cockpit/Panel";

/** One row of GET /api/v1/accountant/book (accountant.py) — one engaged Org. */
interface BookItem {
  id: number;
  display_name: string;
  invoice_count: number;
  outstanding_amount: number;
  review_queue_count: number;
  last_activity_at: string | null;
}
interface AccountantBook {
  count: number;
  items: BookItem[];
}

/**
 * Left panel — Tax & Compliance Hub (M1).
 * Live (both via the M1 surface): accountant dashboard KPIs + the accountant
 * "book" (one row per engaged client — invoice count, outstanding, review
 * queue). The book is the accountant-token-accessible window into each
 * client's tax state; the raw founder invoice/receipt routes
 * (GET /api/v1/invoices, /organizations/{id}/receipts) sit behind
 * business-owner/admin auth, and /whatsapp/threads has no endpoint — so those
 * stay out of scope (honest placeholder below), not faked.
 */
export function M1Panel() {
  const { data, isLoading, isError } = useQuery({
    queryKey: m1Key("cockpit", "dashboard-kpis"), // engine-namespaced (M1)
    queryFn: () => api.getDashboardKpis(), // api.* is the M1 surface (routed via apiM1)
    refetchOnWindowFocus: true,
    staleTime: 30_000,
  });

  const book = useQuery({
    queryKey: m1Key("accountant", "book"), // engine-namespaced (M1) — isolated cache
    queryFn: () => apiM1.get<AccountantBook>("/api/v1/accountant/book", { authRequired: true }),
    staleTime: 30_000,
  });

  const ph = isLoading ? "…" : isError ? "—" : null;
  const kpis = [
    { label: "Vault docs / mo", value: ph ?? String(data?.vault_docs_this_month ?? 0), Icon: Archive, color: "text-indigo-400" },
    { label: "Active clients", value: ph ?? String(data?.active_clients ?? 0), Icon: Users, color: "text-emerald-400" },
    { label: "Active devices", value: ph ?? String(data?.active_devices ?? 0), Icon: MonitorSmartphone, color: "text-blue-400" },
  ];

  const clients = book.data?.items ?? [];

  return (
    <PanelShell engine={ENGINES.m1} accent="text-indigo-300">
      <PanelSection title="Accountant sync state" hint="live">
        <div className="grid grid-cols-3 gap-3">
          {kpis.map(({ label, value, Icon, color }) => (
            <div key={label} className="rounded-lg border border-zinc-800 bg-zinc-900 p-3">
              <Icon className={`h-4 w-4 ${color}`} />
              <div className={`mt-2 text-xl font-bold ${color}`}>{value}</div>
              <div className="mt-0.5 text-[10px] text-zinc-600">{label}</div>
            </div>
          ))}
        </div>
        {isError && (
          <p className="mt-2 text-[11px] text-amber-400/80">
            KPI service unreachable — values are placeholders until it recovers.
          </p>
        )}
      </PanelSection>

      <PanelSection title="Client book" hint="live via apiM1">
        {book.isLoading ? (
          <p className="text-[11px] text-zinc-600">Loading engagements…</p>
        ) : book.isError ? (
          <p className="text-[11px] text-amber-400/80">Book service unreachable — retry shortly.</p>
        ) : clients.length === 0 ? (
          <p className="text-[11px] text-zinc-600">No active client engagements yet.</p>
        ) : (
          <ul className="space-y-2">
            {clients.map((c) => (
              <li key={c.id} className="rounded-lg border border-zinc-800 bg-zinc-900 p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="inline-flex min-w-0 items-center gap-1.5 truncate text-sm text-zinc-200">
                    <Building2 className="h-3.5 w-3.5 shrink-0 text-indigo-400" />
                    {c.display_name}
                  </span>
                  {c.review_queue_count > 0 && (
                    <span className="shrink-0 rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium text-amber-300">
                      {c.review_queue_count} to review
                    </span>
                  )}
                </div>
                <div className="mt-2 flex items-center gap-4 text-[11px] text-zinc-500">
                  <span className="inline-flex items-center gap-1">
                    <FileText className="h-3 w-3" />
                    {c.invoice_count} invoices
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <Wallet className="h-3 w-3" />
                    ₪{c.outstanding_amount.toLocaleString()} outstanding
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </PanelSection>

      <PanelSection title="Meta / WhatsApp live loops">
        <EndpointPending
          label={<span className="inline-flex items-center gap-1.5"><MessageSquare className="h-3.5 w-3.5" />Active conversation threads</span>}
          path="(no accountant-facing router yet)"
        />
      </PanelSection>
    </PanelShell>
  );
}
