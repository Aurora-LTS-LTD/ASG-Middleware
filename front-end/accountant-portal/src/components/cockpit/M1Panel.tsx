"use client";

import { useQuery } from "@tanstack/react-query";
import { Archive, Users, MonitorSmartphone, FileText, ReceiptText, MessageSquare } from "lucide-react";
import { api } from "@/lib/api/client";
import { m1Key } from "@/lib/api/queryKeys";
import { ENGINES } from "@/lib/cockpit/context";
import { PanelShell, PanelSection, EndpointPending } from "@/components/cockpit/Panel";

/**
 * Left panel — Tax & Compliance Hub (M1).
 * Live: accountant dashboard KPIs (real endpoint on the tax server).
 * Pending: invoice timeline / receipt queue / WhatsApp loops have backend
 * routers but no portal-facing endpoint wired yet, so they render as
 * explicit placeholders instead of faked data.
 */
export function M1Panel() {
  const { data, isLoading, isError } = useQuery({
    queryKey: m1Key("cockpit", "dashboard-kpis"), // engine-namespaced (M1)
    queryFn: () => api.getDashboardKpis(), // api.* is the M1 surface (routed via apiM1)
    refetchOnWindowFocus: true,
    staleTime: 30_000,
  });

  const ph = isLoading ? "…" : isError ? "—" : null;
  const kpis = [
    { label: "Vault docs / mo", value: ph ?? String(data?.vault_docs_this_month ?? 0), Icon: Archive, color: "text-indigo-400" },
    { label: "Active clients", value: ph ?? String(data?.active_clients ?? 0), Icon: Users, color: "text-emerald-400" },
    { label: "Active devices", value: ph ?? String(data?.active_devices ?? 0), Icon: MonitorSmartphone, color: "text-blue-400" },
  ];

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

      <PanelSection title="Invoices timeline">
        <EndpointPending
          label={<span className="inline-flex items-center gap-1.5"><FileText className="h-3.5 w-3.5" />Recent ITA-allocated invoices</span>}
          path="GET /api/v1/invoices?recent"
        />
      </PanelSection>

      <PanelSection title="Receipt review queue">
        <EndpointPending
          label={<span className="inline-flex items-center gap-1.5"><ReceiptText className="h-3.5 w-3.5" />Pending receipt OCR review</span>}
          path="GET /api/v1/receipts?status=pending"
        />
      </PanelSection>

      <PanelSection title="Meta / WhatsApp live loops">
        <EndpointPending
          label={<span className="inline-flex items-center gap-1.5"><MessageSquare className="h-3.5 w-3.5" />Active conversation threads</span>}
          path="GET /api/v1/whatsapp/threads"
        />
      </PanelSection>
    </PanelShell>
  );
}
