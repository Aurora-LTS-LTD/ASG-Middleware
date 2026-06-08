import { RequireAuth } from "@/components/shell/RequireAuth";
import { Sidebar } from "@/components/shell/Sidebar";
import { ErrorBoundary } from "@/components/shell/ErrorBoundary";

export default function AuthedLayout({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth>
      <div className="flex h-screen overflow-hidden bg-background">
        <Sidebar />
        <div className="flex flex-1 flex-col overflow-hidden">
          {/* Per-pane isolation: a render error in a view keeps the shell + nav
              alive and shows a recoverable fallback instead of blanking the app. */}
          <ErrorBoundary>{children}</ErrorBoundary>
        </div>
      </div>
    </RequireAuth>
  );
}
