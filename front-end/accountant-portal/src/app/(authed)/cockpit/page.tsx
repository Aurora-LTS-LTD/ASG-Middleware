"use client";

import { CockpitProvider } from "@/lib/cockpit/context";
import { Cockpit } from "@/components/cockpit/Cockpit";

/**
 * Founder's Cockpit — the unified twin-engine command view.
 * Connects to BOTH backends (M1 tax + M2 core) at once and offers a
 * workspace-toggle and a side-by-side split view, each panel carrying a
 * live connectivity dot.
 */
export default function CockpitPage() {
  return (
    <CockpitProvider>
      <Cockpit />
    </CockpitProvider>
  );
}
