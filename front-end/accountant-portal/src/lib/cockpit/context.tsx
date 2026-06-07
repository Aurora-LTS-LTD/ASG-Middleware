"use client";

/**
 * Aurora LTS — Founder's Cockpit state manager.
 *
 * Holds the two independent backend configurations (M1 tax / M2 core) and
 * the cockpit view state (workspace-toggle vs split-screen). The actual
 * fetch routing lives in src/lib/api/client.ts (the `engine` option); this
 * context is the React-state layer the UI binds to — mirroring how
 * src/lib/auth/context.tsx sits on top of the token lifecycle.
 *
 *   useCockpit(): {
 *     engines:   { m1: EngineConfig, m2: EngineConfig },
 *     viewMode:  "toggle" | "split",
 *     workspace: "m1" | "m2",          // active panel when viewMode === "toggle"
 *     setViewMode, setWorkspace, toggleViewMode,
 *   }
 */

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import { M1_TAX_URL, M2_CORE_URL, type ApiEngine } from "@/lib/api/client";

export type CockpitViewMode = "toggle" | "split";

export interface EngineConfig {
  id: ApiEngine;
  /** Human label shown in the workspace switcher + panel header. */
  label: string;
  /** One-line description for tooltips / empty states. */
  blurb: string;
  /** Base URL this engine talks to. */
  url: string;
  /** Public health endpoint the cockpit polls for the connectivity dot. */
  healthPath: string;
}

export const ENGINES: Record<ApiEngine, EngineConfig> = {
  m1: {
    id: "m1",
    label: "Tax & Compliance Hub",
    blurb: "Invoices, receipts, WhatsApp loops, accountant sync",
    url: M1_TAX_URL,
    healthPath: "/api/v1/onboarding/health",
  },
  m2: {
    id: "m2",
    label: "AI Operations Core",
    blurb: "Gemini Copilot, blueprints, anomaly insights",
    url: M2_CORE_URL,
    healthPath: "/api/v1/core/health",
  },
};

interface CockpitContextValue {
  engines: Record<ApiEngine, EngineConfig>;
  viewMode: CockpitViewMode;
  workspace: ApiEngine;
  setViewMode: (m: CockpitViewMode) => void;
  setWorkspace: (w: ApiEngine) => void;
  toggleViewMode: () => void;
}

const CockpitContext = createContext<CockpitContextValue | null>(null);

export function useCockpit(): CockpitContextValue {
  const ctx = useContext(CockpitContext);
  if (!ctx) {
    throw new Error("useCockpit must be used inside <CockpitProvider>");
  }
  return ctx;
}

export function CockpitProvider({ children }: { children: React.ReactNode }) {
  const [viewMode, setViewMode] = useState<CockpitViewMode>("split");
  const [workspace, setWorkspace] = useState<ApiEngine>("m1");

  const toggleViewMode = useCallback(
    () => setViewMode((m) => (m === "split" ? "toggle" : "split")),
    [],
  );

  const value = useMemo<CockpitContextValue>(
    () => ({
      engines: ENGINES,
      viewMode,
      workspace,
      setViewMode,
      setWorkspace,
      toggleViewMode,
    }),
    [viewMode, workspace, toggleViewMode],
  );

  return <CockpitContext.Provider value={value}>{children}</CockpitContext.Provider>;
}
