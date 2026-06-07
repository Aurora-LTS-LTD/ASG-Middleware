"use client"

import React from "react"
import { AlertOctagon, RotateCw } from "lucide-react"

/**
 * P1-18 — Root-level React error boundary.
 *
 * Wraps the entire authenticated portal. Without it, a single
 * uncaught render error (a bad API response that breaks a deep
 * component, a missing field, an unhandled type narrowing) crashes
 * the whole shell to a blank white screen — Tauri inherits the
 * default browser "Aw, snap" behaviour.
 *
 * On catch:
 *   - Show a sober, dark-mode fallback with the error class name
 *     (never the full stack — that leaks to potentially shared
 *     environments).
 *   - Offer a "Try again" button that resets state by reloading
 *     the route. Reloading wipes component state but preserves
 *     the Keychain JWT (no re-login needed).
 *   - Log the full error + componentStack to the browser console
 *     and to the Tauri devtools so we have a record.
 *
 * React's componentDidCatch + getDerivedStateFromError is the only
 * way to do this in 2026; there's no hook equivalent yet.
 */
interface State {
  hasError: boolean
  errorName: string
}

interface Props {
  children: React.ReactNode
  /**
   * Optional compact fallback to render on catch instead of the full-screen
   * shell. Used for per-pane isolation in the cockpit so one engine's render
   * error doesn't blank the whole window / the other engine's pane.
   */
  fallback?: React.ReactNode
}

export class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, errorName: "" }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, errorName: error.name || "Error" }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // Operator visibility (devtools console + Tauri stderr).
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary]", error, info.componentStack)
  }

  handleRetry = () => {
    // Reload the current route. Tauri's webview is process-internal,
    // so this is fast and keeps the Tauri shell + Keychain alive.
    if (typeof window !== "undefined") {
      window.location.reload()
    }
  }

  render() {
    if (!this.state.hasError) return this.props.children
    if (this.props.fallback !== undefined) return this.props.fallback

    return (
      <div className="flex min-h-screen flex-col items-center justify-center bg-background text-foreground p-6">
        <div className="max-w-md w-full rounded-2xl border border-red-500/30 bg-red-900/10 p-8 text-center">
          <div className="mx-auto mb-4 inline-flex h-14 w-14 items-center justify-center rounded-full bg-red-500/20">
            <AlertOctagon className="h-7 w-7 text-red-300" />
          </div>
          <h1 className="text-xl font-semibold text-foreground">
            Something went wrong
          </h1>
          <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
            The portal encountered an unexpected error and stopped rendering
            this view. Your session is still active. Try reloading — if it
            keeps happening, file a ticket and quote the error class below.
          </p>
          <code className="mt-4 inline-block rounded-md border border-border bg-card px-3 py-1 text-xs text-amber-300">
            {this.state.errorName}
          </code>
          <button
            onClick={this.handleRetry}
            className="mt-6 inline-flex items-center gap-2 rounded-lg border border-indigo-500/40 bg-indigo-500/10 px-4 py-2 text-sm font-medium text-indigo-200 hover:border-indigo-400 hover:bg-indigo-500/20 transition-colors"
          >
            <RotateCw className="h-4 w-4" />
            Try again
          </button>
        </div>
      </div>
    )
  }
}
