"use client";

/**
 * Invoices — list + detail with a lifecycle timeline.
 *
 * Static-export: no /invoices/[id] dynamic route. The selected invoice rides in
 * `?id=<n>` (read via useSearchParams, which must sit inside <Suspense>).
 */
import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import {
  ChevronLeft, ChevronRight, FileText, CircleDot, CheckCircle2, XCircle, Ban,
} from "lucide-react";

import { Topbar } from "@/components/shell/Topbar";
import { api, ApiClientError } from "@/lib/api/client";
import { formatILS } from "@/lib/format/currency";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { StatusBadge, AllocationBadge } from "@/components/invoices/StatusBadge";
import type { Invoice } from "@/types/api";

function fmt(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : format(d, "d MMM yyyy, HH:mm");
}

export default function InvoicesPage() {
  return (
    <>
      <Topbar title="Invoices" />
      <Suspense
        fallback={
          <main className="flex-1 overflow-y-auto p-6">
            <div className="text-sm text-muted-foreground">Loading…</div>
          </main>
        }
      >
        <InvoicesInner />
      </Suspense>
    </>
  );
}

function InvoicesInner() {
  const params = useSearchParams();
  const idParam = params.get("id");
  const id = idParam ? Number(idParam) : null;
  return (
    <main className="flex-1 overflow-y-auto p-6">
      {id == null ? <InvoiceList /> : <InvoiceDetail id={id} />}
    </main>
  );
}

function InvoiceList() {
  const router = useRouter();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["invoices"],
    queryFn: () => api.listInvoices(),
    staleTime: 30_000,
  });
  const invoices = data ?? [];

  if (isLoading) {
    return (
      <div className="space-y-2">
        {[0, 1, 2, 3].map((i) => <div key={i} className="h-12 animate-pulse rounded-lg bg-card" />)}
      </div>
    );
  }
  if (isError) {
    return <p className="py-16 text-center text-sm text-amber-400/80">Couldn&apos;t load your invoices — retry shortly.</p>;
  }
  if (invoices.length === 0) {
    return (
      <div className="flex h-64 flex-col items-center justify-center text-center">
        <FileText className="mb-3 h-10 w-10 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">No invoices yet.</p>
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-left text-[11px] uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-4 py-2.5 font-medium">Invoice</th>
            <th className="px-4 py-2.5 font-medium">Customer</th>
            <th className="px-4 py-2.5 font-medium text-right">Amount</th>
            <th className="px-4 py-2.5 font-medium">Status</th>
            <th className="px-4 py-2.5 font-medium">Created</th>
            <th className="px-2 py-2.5" />
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {invoices.map((inv) => (
            <tr
              key={inv.id}
              onClick={() => router.push(`/invoices?id=${inv.id}`)}
              className="cursor-pointer transition-colors hover:bg-accent/40"
            >
              <td className="px-4 py-3 font-medium text-foreground">{inv.invoice_number}</td>
              <td className="px-4 py-3 text-muted-foreground">{inv.beneficiary_name}</td>
              <td className="px-4 py-3 text-right text-foreground">{formatILS(inv.amount_total)}</td>
              <td className="px-4 py-3"><StatusBadge status={inv.status} /></td>
              <td className="px-4 py-3 text-[11px] text-muted-foreground">{fmt(inv.created_at) || "—"}</td>
              <td className="px-2 py-3 text-right"><ChevronRight className="inline h-3.5 w-3.5 text-muted-foreground" /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function InvoiceDetail({ id }: { id: number }) {
  const router = useRouter();
  const qc = useQueryClient();
  const [cancelling, setCancelling] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const { data: inv, isLoading, isError } = useQuery({
    queryKey: ["invoice", id],
    queryFn: () => api.getInvoice(id),
    staleTime: 15_000,
  });

  async function onCancel() {
    if (!inv) return;
    setCancelling(true);
    setActionError(null);
    try {
      await api.cancelInvoice(id, "cancelled from business portal");
      await qc.invalidateQueries({ queryKey: ["invoice", id] });
      await qc.invalidateQueries({ queryKey: ["invoices"] });
    } catch (err) {
      setActionError(
        err instanceof ApiClientError
          ? err.detail.message || "Couldn't cancel this invoice."
          : "Couldn't cancel this invoice.",
      );
    } finally {
      setCancelling(false);
    }
  }

  const back = (
    <button
      type="button"
      onClick={() => router.push("/invoices")}
      className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
    >
      <ChevronLeft className="h-4 w-4" /> All invoices
    </button>
  );

  if (isLoading) {
    return (
      <div>
        {back}
        <div className="h-40 animate-pulse rounded-lg bg-card" />
      </div>
    );
  }
  if (isError || !inv) {
    return (
      <div>
        {back}
        <p className="py-16 text-center text-sm text-amber-400/80">Couldn&apos;t load this invoice.</p>
      </div>
    );
  }

  const canCancel = inv.status === "draft" || inv.status === "pending_allocation";

  return (
    <div className="mx-auto max-w-2xl">
      {back}

      {/* Header */}
      <div className="mb-6 flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold text-foreground">{inv.invoice_number}</h2>
            <StatusBadge status={inv.status} />
            {inv.requires_allocation ? <AllocationBadge status={inv.allocation_status} /> : null}
          </div>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {inv.beneficiary_name}
            {inv.beneficiary_tax_id ? ` · ${inv.beneficiary_tax_id}` : ""}
          </p>
        </div>
        <div className="text-right">
          <div className="text-xl font-bold text-foreground">{formatILS(inv.amount_total)}</div>
          <div className="text-[11px] text-muted-foreground">
            net {formatILS(inv.amount_net)} · VAT {formatILS(inv.vat_amount)}
          </div>
        </div>
      </div>

      {/* Lifecycle timeline */}
      <Card className="mb-4 border-border bg-card">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-semibold text-foreground">Lifecycle</CardTitle>
          <CardDescription className="text-xs text-muted-foreground">
            {inv.allocation_number ? `ITA allocation #${inv.allocation_number}` : "Allocation not yet issued"}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Timeline invoice={inv} />
        </CardContent>
      </Card>

      {/* Actions */}
      <Card className="border-border bg-card">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-semibold text-foreground">Actions</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {actionError && <p className="text-sm text-red-400">{actionError}</p>}
          {canCancel ? (
            <Button
              variant="outline"
              onClick={onCancel}
              disabled={cancelling}
              className="border-red-500/40 bg-red-500/10 text-red-300 hover:bg-red-500/20"
            >
              <Ban className="mr-2 h-4 w-4" />
              {cancelling ? "Cancelling…" : "Cancel invoice"}
            </Button>
          ) : (
            <p className="text-xs text-muted-foreground">
              {inv.status === "cancelled"
                ? "This invoice was cancelled."
                : "Finalized invoices are tax-locked — issue a credit note to reverse them."}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Timeline({ invoice }: { invoice: Invoice }) {
  const cancelled = invoice.status === "cancelled";
  const steps: { label: string; at?: string | null; done: boolean; show: boolean }[] = [
    { label: "Created", at: invoice.created_at, done: !!invoice.created_at, show: true },
    {
      label: "Submitted to ITA",
      at: invoice.submitted_at,
      done: !!invoice.submitted_at,
      show: invoice.requires_allocation === 1,
    },
    { label: "Finalized", at: invoice.finalized_at, done: !!invoice.finalized_at, show: !cancelled },
    { label: "Sent to customer", at: invoice.sent_at, done: !!invoice.sent_at, show: !cancelled },
  ];
  if (cancelled) {
    steps.push({ label: "Cancelled", at: invoice.cancelled_at, done: true, show: true });
  }
  const visible = steps.filter((s) => s.show);

  return (
    <ol className="space-y-3">
      {visible.map((s, i) => {
        const isCancel = s.label === "Cancelled";
        const Icon = isCancel ? XCircle : s.done ? CheckCircle2 : CircleDot;
        const color = isCancel ? "text-red-400" : s.done ? "text-emerald-400" : "text-muted-foreground";
        return (
          <li key={s.label} className="flex items-start gap-3">
            <div className="flex flex-col items-center">
              <Icon className={`h-4 w-4 ${color}`} />
              {i < visible.length - 1 && <span className="mt-0.5 h-5 w-px bg-border" />}
            </div>
            <div className="-mt-0.5">
              <div className={`text-sm ${s.done ? "text-foreground" : "text-muted-foreground"}`}>{s.label}</div>
              <div className="text-[11px] text-muted-foreground">{s.at ? fmt(s.at) : s.done ? "" : "pending"}</div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
