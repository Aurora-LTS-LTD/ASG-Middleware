import { RequireAuth } from "@/components/auth/RequireAuth"
import { Sidebar } from "@/components/shell/Sidebar"

export default function AuthedLayout({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth>
      <div className="flex h-screen overflow-hidden bg-zinc-950">
        <Sidebar />
        <div className="flex flex-1 flex-col overflow-hidden">
          {children}
        </div>
      </div>
    </RequireAuth>
  )
}
