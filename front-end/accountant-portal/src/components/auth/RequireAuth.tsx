"use client"

import { useAuth } from "@/lib/auth/context"
import LoginPage from "@/app/page"

export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { status } = useAuth()

  if (status === "initializing") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-zinc-950">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-zinc-700 border-t-indigo-500" />
      </div>
    )
  }

  if (status !== "signed_in") {
    return <LoginPage />
  }

  return <>{children}</>
}
