"use client";

import { Columns2, SquareStack } from "lucide-react";
import { cn } from "@/lib/utils";
import { useCockpit, type CockpitViewMode } from "@/lib/cockpit/context";
import type { ApiEngine } from "@/lib/api/client";

/** A generic dark segmented control. */
function Segmented<T extends string>({
  value,
  options,
  onChange,
  size = "sm",
}: {
  value: T;
  options: { value: T; label: string; icon?: React.ReactNode }[];
  onChange: (v: T) => void;
  size?: "sm" | "xs";
}) {
  return (
    <div className="inline-flex items-center gap-0.5 rounded-lg border border-zinc-800 bg-zinc-900 p-0.5">
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            aria-pressed={active}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md font-medium transition-colors",
              size === "sm" ? "px-2.5 py-1 text-xs" : "px-2 py-0.5 text-[11px]",
              active
                ? "bg-indigo-600/25 text-indigo-200 shadow-inner"
                : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100",
            )}
          >
            {opt.icon}
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

export function ViewSwitcher() {
  const { viewMode, workspace, setViewMode, setWorkspace, engines } = useCockpit();

  return (
    <div className="flex items-center gap-2">
      {/* Workspace picker — only meaningful in toggle (single-screen) mode */}
      {viewMode === "toggle" && (
        <Segmented<ApiEngine>
          value={workspace}
          onChange={setWorkspace}
          options={[
            { value: "m1", label: engines.m1.label },
            { value: "m2", label: engines.m2.label },
          ]}
        />
      )}

      {/* View-mode picker — toggle vs split */}
      <Segmented<CockpitViewMode>
        value={viewMode}
        onChange={setViewMode}
        options={[
          { value: "toggle", label: "Workspace", icon: <SquareStack className="h-3.5 w-3.5" /> },
          { value: "split", label: "Split", icon: <Columns2 className="h-3.5 w-3.5" /> },
        ]}
      />
    </div>
  );
}
