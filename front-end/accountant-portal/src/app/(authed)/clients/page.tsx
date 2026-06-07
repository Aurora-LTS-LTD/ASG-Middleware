"use client"

import { Topbar } from "@/components/shell/Topbar"
import { Users } from "lucide-react"

export default function ClientsPage() {
  return (
    <>
      <Topbar title="Clients" />
      <main className="flex-1 overflow-y-auto p-6">
        <div className="flex flex-col items-center justify-center h-64 text-center">
          <Users className="h-10 w-10 text-zinc-700 mb-4" />
          <h2 className="text-base font-semibold text-zinc-400">Client list — coming in P1</h2>
          <p className="mt-2 text-sm text-zinc-600 max-w-xs">
            Client management, engagement assignments, and COA editor will be available
            after the Document Vault Engine is live.
          </p>
        </div>
      </main>
    </>
  )
}
