import { RequireAuth } from "@/components/shell/RequireAuth";
import { Sidebar } from "@/components/shell/Sidebar";

export default function AuthedLayout({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth>
      <div className="flex h-screen overflow-hidden bg-background">
        <Sidebar />
        <div className="flex flex-1 flex-col overflow-hidden">{children}</div>
      </div>
    </RequireAuth>
  );
}
