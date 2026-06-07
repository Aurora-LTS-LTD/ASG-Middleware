import type { ApiEngine } from "@/lib/api/client";

/**
 * Per-engine React Query key helpers (dual-context state isolation).
 *
 * Every cache key is namespaced by its engine, so an M1 fetch/invalidation
 * can never read or clobber M2 data (and vice-versa). Pair these with the
 * engine-bound clients (`apiM1` / `apiM2`) in `@/lib/api/client`:
 *
 *   useQuery({ queryKey: m1Key("dashboard", "kpis"), queryFn: () => apiM1.get(...) })
 *   useQuery({ queryKey: m2Key("copilot", "session"), queryFn: () => apiM2.get(...) })
 */
export const engineKey = (
  engine: ApiEngine,
  ...parts: ReadonlyArray<string | number>
): readonly (string | number)[] => [engine, ...parts];

export const m1Key = (...parts: ReadonlyArray<string | number>) => engineKey("m1", ...parts);
export const m2Key = (...parts: ReadonlyArray<string | number>) => engineKey("m2", ...parts);
