"use client"

import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  getPaginationRowModel,
  useReactTable,
  createColumnHelper,
  type SortingState,
} from "@tanstack/react-table"
import { useState } from "react"
import { formatDistanceToNow, format } from "date-fns"
import {
  ChevronUp, ChevronDown, ChevronsUpDown,
  ChevronLeft, ChevronRight, Archive,
} from "lucide-react"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Badge } from "@/components/ui/badge"
import { UploadSourceBadge } from "@/components/vault/UploadSourceBadge"
import { DocumentTypeBadge } from "@/components/vault/DocumentTypeBadge"
import type { ClientDocument } from "@/types/vault"

// ─────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────

function formatBytes(bytes: number): string {
  if (bytes < 1024)        return `${bytes} B`
  if (bytes < 1_048_576)   return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1_048_576).toFixed(1)} MB`
}

const STATUS_CONFIG: Record<ClientDocument["status"], { label: string; variant: "default" | "green" | "indigo" | "yellow" | "red" }> = {
  classified: { label: "Classified", variant: "green"   },
  received:   { label: "Received",   variant: "indigo"  },
  scanning:   { label: "Scanning",   variant: "yellow"  },
  error:      { label: "Error",      variant: "red"     },
  quarantined:{ label: "Quarantined",variant: "red"     },
}

function SortIcon({ isSorted }: { isSorted: false | "asc" | "desc" }) {
  if (isSorted === "asc")  return <ChevronUp   className="ml-1 h-3.5 w-3.5 text-indigo-400" />
  if (isSorted === "desc") return <ChevronDown className="ml-1 h-3.5 w-3.5 text-indigo-400" />
  return <ChevronsUpDown className="ml-1 h-3.5 w-3.5 text-muted-foreground" />
}

// ─────────────────────────────────────────────────────────────
// Column definitions
// ─────────────────────────────────────────────────────────────

const col = createColumnHelper<ClientDocument>()

const COLUMNS = [
  col.accessor("file_name", {
    header: "Document",
    cell: (info) => (
      <div className="flex flex-col">
        <span className="max-w-[220px] truncate text-sm font-medium text-foreground" title={info.getValue()}>
          {info.getValue()}
        </span>
        <span className="text-[11px] text-muted-foreground">{formatBytes(info.row.original.size_bytes)}</span>
      </div>
    ),
    enableSorting: true,
  }),

  col.accessor("uploaded_by_vector", {
    header: "Source",
    cell: (info) => <UploadSourceBadge vector={info.getValue()} />,
    enableSorting: true,
  }),

  col.accessor("document_type", {
    header: "Type",
    cell: (info) => <DocumentTypeBadge type={info.getValue()} />,
    enableSorting: true,
  }),

  col.accessor("tax_year", {
    header: "Tax Year",
    cell: (info) => (
      <span className="font-mono text-sm text-foreground">{info.getValue()}</span>
    ),
    enableSorting: true,
  }),

  col.accessor("created_at", {
    header: "Uploaded",
    cell: (info) => (
      <div className="flex flex-col">
        <span className="text-sm text-foreground">
          {formatDistanceToNow(new Date(info.getValue()), { addSuffix: true })}
        </span>
        <span className="text-[11px] text-muted-foreground">
          {format(new Date(info.getValue()), "dd MMM yyyy")}
        </span>
      </div>
    ),
    enableSorting: true,
  }),

  col.accessor("status", {
    header: "Status",
    cell: (info) => {
      const cfg = STATUS_CONFIG[info.getValue()] ?? STATUS_CONFIG.received
      return <Badge variant={cfg.variant}>{cfg.label}</Badge>
    },
    enableSorting: true,
  }),
]

// ─────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────

interface Props {
  documents: ClientDocument[]
  total: number
  page: number
  pageSize: number
  onPageChange: (page: number) => void
  isLoading?: boolean
}

export function DocumentTable({ documents, total, page, pageSize, onPageChange, isLoading }: Props) {
  const [sorting, setSorting] = useState<SortingState>([{ id: "created_at", desc: true }])

  const table = useReactTable({
    data: documents,
    columns: COLUMNS,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    pageCount: Math.ceil(total / pageSize),
  })

  const totalPages = Math.ceil(total / pageSize)

  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-14 w-full rounded-lg" />
        ))}
      </div>
    )
  }

  if (documents.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-xl border border-border bg-card/40 py-16 text-center">
        <Archive className="mb-4 h-10 w-10 text-muted-foreground" />
        <p className="text-sm font-medium text-muted-foreground">No documents found</p>
        <p className="mt-1 text-xs text-muted-foreground">
          Try adjusting the filters, or share the ingestion address with your client.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-border overflow-hidden">
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((hg) => (
              <TableRow key={hg.id} className="hover:bg-transparent">
                {hg.headers.map((header) => (
                  <TableHead key={header.id}>
                    {header.isPlaceholder ? null : (
                      <button
                        className={
                          header.column.getCanSort()
                            ? "flex items-center cursor-pointer select-none hover:text-foreground transition-colors"
                            : "flex items-center"
                        }
                        onClick={header.column.getToggleSortingHandler()}
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {header.column.getCanSort() && (
                          <SortIcon isSorted={header.column.getIsSorted()} />
                        )}
                      </button>
                    )}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows.map((row) => (
              <TableRow key={row.id}>
                {row.getVisibleCells().map((cell) => (
                  <TableCell key={cell.id}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between px-1">
        <p className="text-xs text-muted-foreground">
          {total} document{total !== 1 ? "s" : ""}
          {totalPages > 1 && ` · page ${page} of ${totalPages}`}
        </p>
        {totalPages > 1 && (
          <div className="flex items-center gap-1">
            <Button
              variant="outline"
              size="icon"
              onClick={() => onPageChange(page - 1)}
              disabled={page <= 1}
              className="h-7 w-7"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="outline"
              size="icon"
              onClick={() => onPageChange(page + 1)}
              disabled={page >= totalPages}
              className="h-7 w-7"
            >
              <ChevronRight className="h-3.5 w-3.5" />
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
