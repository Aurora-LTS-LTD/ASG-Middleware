"use client";

/**
 * Per-engine connectivity probe for the Founder's Cockpit health dots.
 *
 * Hits each backend's PUBLIC health endpoint (no auth) on an interval and
 * maps the result to a soft status the UI renders as a glowing dot:
 *
 *   online   → green   (200 OK)
 *   degraded → amber   (200 OK but reporting a caveat, e.g. M2 running on
 *                        stubbed compliance backends)
 *   offline  → red     (network error, timeout, or non-2xx)
 *   checking → zinc    (first load / refetch in flight)
 */

import { useQuery } from "@tanstack/react-query";
import type { EngineConfig } from "@/lib/cockpit/context";

export type HealthState = "online" | "degraded" | "offline" | "checking";

export interface EngineHealth {
  state: HealthState;
  detail: string;
  /** Round-trip latency in ms for the last successful probe, if any. */
  latencyMs: number | null;
}

const PROBE_TIMEOUT_MS = 4_000;
const PROBE_INTERVAL_MS = 15_000;

async function probe(engine: EngineConfig): Promise<EngineHealth> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  const startedAt = performance.now();
  try {
    const resp = await fetch(`${engine.url}${engine.healthPath}`, {
      method: "GET",
      cache: "no-store",
      signal: controller.signal,
    });
    const latencyMs = Math.round(performance.now() - startedAt);
    if (!resp.ok) {
      return { state: "offline", detail: `HTTP ${resp.status}`, latencyMs };
    }
    const body = (await resp.json().catch(() => ({}))) as {
      compliance_backends?: string;
      status?: string;
    };
    if (body.compliance_backends === "stubbed") {
      return {
        state: "degraded",
        detail: "up · compliance backends stubbed",
        latencyMs,
      };
    }
    return { state: "online", detail: `online · ${latencyMs}ms`, latencyMs };
  } catch (err) {
    const aborted = err instanceof DOMException && err.name === "AbortError";
    return {
      state: "offline",
      detail: aborted ? "timed out" : "unreachable",
      latencyMs: null,
    };
  } finally {
    clearTimeout(timer);
  }
}

export function useEngineHealth(engine: EngineConfig): EngineHealth {
  const { data, isFetching } = useQuery({
    queryKey: ["engine-health", engine.id, engine.url],
    queryFn: () => probe(engine),
    refetchInterval: PROBE_INTERVAL_MS,
    refetchOnWindowFocus: true,
    retry: false,
    staleTime: PROBE_INTERVAL_MS,
  });

  if (!data) {
    return { state: isFetching ? "checking" : "offline", detail: "probing…", latencyMs: null };
  }
  return data;
}
