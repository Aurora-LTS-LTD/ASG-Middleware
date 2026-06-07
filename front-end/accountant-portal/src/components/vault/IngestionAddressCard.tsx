"use client"

import { useQuery } from "@tanstack/react-query"
import { Copy, Check, MessageCircle, Mail, Loader2 } from "lucide-react"
import { useState } from "react"
import { vaultApi } from "@/lib/api/vault"
import { cn } from "@/lib/utils"

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false)

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // clipboard unavailable in some Tauri contexts — silent
    }
  }

  return (
    <button
      onClick={handleCopy}
      className="ml-2 rounded p-1 text-muted-foreground transition-colors hover:text-foreground focus:outline-none focus:ring-2 focus:ring-indigo-500"
      title="Copy to clipboard"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  )
}

interface Props {
  clientId: number
  className?: string
}

export function IngestionAddressCard({ clientId, className }: Props) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["vault", "ingestion-address", clientId],
    queryFn: () => vaultApi.getIngestionAddress(clientId),
    staleTime: 60_000,
  })

  return (
    <div
      className={cn(
        "rounded-xl border border-border bg-card/60 p-4",
        className,
      )}
    >
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        Document Ingestion Channels
      </h3>

      {isLoading && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Loading…
        </div>
      )}

      {isError && (
        <p className="text-xs text-red-400">Failed to load ingestion address.</p>
      )}

      {data && (
        <div className="space-y-3">
          {/* Email channel */}
          <div className="flex items-center justify-between rounded-lg border border-border bg-background px-3 py-2.5">
            <div className="flex items-center gap-2.5 min-w-0">
              <Mail className="h-4 w-4 shrink-0 text-blue-400" />
              <div className="min-w-0">
                <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">Email</p>
                <p className="truncate text-sm font-mono text-foreground">{data.email_full}</p>
              </div>
            </div>
            <CopyButton value={data.email_full} />
          </div>

          {/* WhatsApp channel */}
          {data.whatsapp_display ? (
            <div className="flex items-center justify-between rounded-lg border border-border bg-background px-3 py-2.5">
              <div className="flex items-center gap-2.5 min-w-0">
                <MessageCircle className="h-4 w-4 shrink-0 text-emerald-400" />
                <div className="min-w-0">
                  <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">WhatsApp</p>
                  <p className="text-sm font-mono text-foreground">{data.whatsapp_display}</p>
                </div>
              </div>
              <CopyButton value={data.ingestion_address.whatsapp_e164 ?? data.whatsapp_display} />
            </div>
          ) : (
            <p className="text-xs text-muted-foreground pl-1">
              WhatsApp not configured for this client.
            </p>
          )}

          <p className="pt-1 text-[11px] leading-relaxed text-muted-foreground">
            Share these channels with your client so they can send documents directly.
            Files are stored for 7 years in compliance with ITA retention requirements.
          </p>
        </div>
      )}
    </div>
  )
}
