import { QueryClient } from "@tanstack/react-query"

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,           // data stays fresh for 30s
      retry: 1,                    // one retry on failure
      refetchOnWindowFocus: false, // desktop app — no tab-focus events
    },
  },
})
