"use client";

import { useRouter } from "next/navigation";
import { LogOut, Settings, MonitorSmartphone, ChevronDown } from "lucide-react";
import { useAuth } from "@/lib/auth/context";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";

function initials(name?: string | null): string {
  if (!name) return "A";
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "A";
}

/** Topbar account menu: avatar + name → Settings / Devices / Sign out. */
export function UserMenu() {
  const router = useRouter();
  const { user, signOut } = useAuth();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex items-center gap-2 rounded-full border border-border bg-card py-1 pl-1 pr-2 text-left transition-colors hover:bg-accent"
        >
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary text-[10px] font-semibold text-primary-foreground">
            {initials(user?.name)}
          </span>
          <span className="hidden leading-tight sm:block">
            <span className="block text-xs font-medium text-foreground">{user?.name ?? "Account"}</span>
            {user?.firm_name && <span className="block text-[10px] text-muted-foreground">{user.firm_name}</span>}
          </span>
          <ChevronDown className="h-3 w-3 text-muted-foreground" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel>
          <div className="text-xs font-medium text-foreground">{user?.name ?? "Account"}</div>
          {user?.email && <div className="text-[10px] font-normal text-muted-foreground">{user.email}</div>}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => router.push("/settings")}>
          <Settings className="mr-2 h-3.5 w-3.5" />
          Settings
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => router.push("/devices")}>
          <MonitorSmartphone className="mr-2 h-3.5 w-3.5" />
          Devices
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => signOut()} className="text-red-400 focus:text-red-400">
          <LogOut className="mr-2 h-3.5 w-3.5" />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
