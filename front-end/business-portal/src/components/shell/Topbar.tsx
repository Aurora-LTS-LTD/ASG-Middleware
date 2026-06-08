"use client";

import { ShieldCheck } from "lucide-react";
import { ThemeSwitcher } from "@/components/shell/ThemeSwitcher";
import { useAuth } from "@/lib/auth/context";

export function Topbar({ title }: { title: string }) {
  const { user } = useAuth();
  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-border bg-background px-6">
      <h1 className="text-sm font-semibold text-foreground">{title}</h1>
      <div className="flex items-center gap-3">
        <div className="hidden items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-1 md:flex">
          <ShieldCheck className="h-3 w-3 text-emerald-500" />
          <span className="text-[10px] font-medium text-muted-foreground">Secure</span>
        </div>
        <ThemeSwitcher />
        {user && <span className="hidden text-xs text-muted-foreground sm:block">{user.email}</span>}
      </div>
    </header>
  );
}
