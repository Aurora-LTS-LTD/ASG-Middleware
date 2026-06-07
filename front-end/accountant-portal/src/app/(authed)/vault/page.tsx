"use client"

import { useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Topbar } from "@/components/shell/Topbar"
import { DocumentTable } from "@/components/vault/DocumentTable"
import { IngestionAddressCard } from "@/components/vault/IngestionAddressCard"
import { vaultApi } from "@/lib/api/vault"
import { MOCK_CLIENTS } from "@/lib/api/mock"
import type { DocumentType, ListDocumentsFilters, UploadVector } from "@/types/vault"
import { Upload, Loader2 } from "lucide-react"

// ─────────────────────────────────────────────────────────────
// Filter option lists
// ─────────────────────────────────────────────────────────────

const TAX_YEARS = [2026, 2025, 2024]

const DOC_TYPE_OPTIONS: { value: DocumentType | ""; label: string }[] = [
  { value: "",             label: "All Types"     },
  { value: "expense",      label: "Expense"       },
  { value: "revenue",      label: "Revenue"       },
  { value: "statement",    label: "Statement"     },
  { value: "unclassified", label: "Unclassified"  },
]

const VECTOR_OPTIONS: { value: UploadVector | ""; label: string }[] = [
  { value: "",          label: "All Sources" },
  { value: "whatsapp",  label: "WhatsApp"    },
  { value: "email",     label: "Email"       },
  { value: "manual",    label: "Manual"      },
]

// ─────────────────────────────────────────────────────────────
// Filter pill component
// ─────────────────────────────────────────────────────────────

function FilterSelect<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T | ""
  options: { value: T | ""; label: string }[]
  onChange: (v: T | "") => void
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as T | "")}
        className="rounded-lg border border-border bg-card px-3 py-1.5 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-indigo-500"
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value} className="bg-card">
            {opt.label}
          </option>
        ))}
      </select>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────
// Page
// ─────────────────────────────────────────────────────────────

export default function VaultPage() {
  // Client selection
  const [clientId, setClientId] = useState<number>(MOCK_CLIENTS[0].id)

  // Filters
  const [taxYear, setTaxYear]         = useState<number | null>(null)
  const [docType,  setDocType]        = useState<DocumentType | "">("")
  const [vector,   setVector]         = useState<UploadVector | "">("")
  const [page,     setPage]           = useState(1)

  const PAGE_SIZE = 20

  // Build filters object — only include non-empty values
  const filters: ListDocumentsFilters = {
    ...(taxYear != null && { tax_year: taxYear }),
    ...(docType  && { document_type: docType }),
    ...(vector   && { uploaded_by_vector: vector }),
    page,
    page_size: PAGE_SIZE,
  }

  const { data, isLoading } = useQuery({
    queryKey: ["vault", "documents", clientId, filters],
    queryFn: () => vaultApi.listDocuments(clientId, filters),
    placeholderData: (prev) => prev,  // keep stale data while fetching
  })

  // P1-17: manual upload mutation.
  const qc = useQueryClient()
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)

  const uploadMutation = useMutation({
    mutationFn: (file: File) =>
      vaultApi.uploadManual(clientId, file, {
        document_type: docType || undefined,
        tax_year: taxYear ?? undefined,
      }),
    onSuccess: () => {
      setUploadError(null)
      qc.invalidateQueries({ queryKey: ["vault", "documents", clientId] })
      qc.invalidateQueries({ queryKey: ["dashboard-kpis"] })
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Upload failed"
      setUploadError(msg)
    },
  })

  function onFilePicked(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    if (!f) return
    uploadMutation.mutate(f)
    // Reset so the same file can be re-selected immediately.
    e.target.value = ""
  }

  function resetFilters() {
    setTaxYear(null)
    setDocType("")
    setVector("")
    setPage(1)
  }

  function handleClientChange(id: number) {
    setClientId(id)
    resetFilters()
  }

  const activeClient = MOCK_CLIENTS.find((c) => c.id === clientId) ?? MOCK_CLIENTS[0]
  const hasActiveFilters = taxYear != null || docType !== "" || vector !== ""

  return (
    <>
      <Topbar title="Document Vault" />
      <main className="flex flex-1 flex-col overflow-hidden">
        {/* ── Top bar: client selector ─────────────────────── */}
        <div className="border-b border-border bg-background/80 px-6 py-3">
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex flex-col gap-1">
              <label className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                Client
              </label>
              <select
                value={clientId}
                onChange={(e) => handleClientChange(Number(e.target.value))}
                className="rounded-lg border border-border bg-card px-3 py-1.5 text-sm font-medium text-foreground focus:outline-none focus:ring-2 focus:ring-indigo-500"
              >
                {MOCK_CLIENTS.map((c) => (
                  <option key={c.id} value={c.id} className="bg-card">
                    {c.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="h-10 w-px bg-muted self-end" />

            {/* Filters */}
            <FilterSelect<string>
              label="Tax Year"
              value={taxYear != null ? String(taxYear) : ""}
              options={[
                { value: "", label: "All Years" },
                ...TAX_YEARS.map((y) => ({ value: String(y), label: String(y) })),
              ]}
              onChange={(v) => { setPage(1); setTaxYear(v ? Number(v) : null) }}
            />

            <FilterSelect
              label="Document Type"
              value={docType}
              options={DOC_TYPE_OPTIONS}
              onChange={(v) => { setPage(1); setDocType(v) }}
            />

            <FilterSelect
              label="Upload Source"
              value={vector}
              options={VECTOR_OPTIONS}
              onChange={(v) => { setPage(1); setVector(v) }}
            />

            {hasActiveFilters && (
              <button
                onClick={resetFilters}
                className="self-end rounded-lg border border-border bg-transparent px-3 py-1.5 text-xs text-muted-foreground hover:border-border hover:text-foreground transition-colors"
              >
                Clear filters
              </button>
            )}

            <div className="ml-auto self-end flex items-center gap-3">
              <span className="text-xs text-muted-foreground">
                {activeClient.industry}
              </span>

              {/* P1-17 — manual upload */}
              <input
                ref={fileInputRef}
                type="file"
                onChange={onFilePicked}
                className="hidden"
                aria-hidden="true"
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={uploadMutation.isPending}
                className="inline-flex items-center gap-2 rounded-lg border border-indigo-500/40 bg-indigo-500/10 px-3 py-1.5 text-xs font-medium text-indigo-200 hover:border-indigo-400 hover:bg-indigo-500/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                title="Upload a document for this client"
              >
                {uploadMutation.isPending ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    Uploading…
                  </>
                ) : (
                  <>
                    <Upload className="h-3.5 w-3.5" />
                    Upload
                  </>
                )}
              </button>
            </div>
          </div>
          {uploadError && (
            <div className="mt-2 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs text-red-200">
              {uploadError}
            </div>
          )}
        </div>

        {/* ── Content area ─────────────────────────────────── */}
        <div className="flex flex-1 overflow-hidden">
          {/* Document table (main panel) */}
          <div className="flex-1 overflow-y-auto p-6">
            <DocumentTable
              documents={data?.documents ?? []}
              total={data?.total ?? 0}
              page={page}
              pageSize={PAGE_SIZE}
              onPageChange={setPage}
              isLoading={isLoading}
            />
          </div>

          {/* Ingestion address sidebar */}
          <div className="w-72 shrink-0 overflow-y-auto border-l border-border p-4">
            <IngestionAddressCard clientId={clientId} />
          </div>
        </div>
      </main>
    </>
  )
}
