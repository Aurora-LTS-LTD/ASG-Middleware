"use client";

/**
 * Business Owner Portal — auth context.
 * Bootstraps from the browser token store; exposes loginWithPassword + signOut.
 * A fatal 401 (AuthFatalError) bubbling up anywhere drops state to signed_out.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { api, AuthFatalError } from "@/lib/api/client";
import { setSession, getStoredUser, getToken, clearSession } from "@/lib/auth/tokenStore";
import type { BusinessOwnerUser } from "@/types/api";

export type AuthStatus = "initializing" | "signed_out" | "signed_in";

interface AuthState {
  status: AuthStatus;
  user: BusinessOwnerUser | null;
}

interface AuthApi {
  loginWithPassword: (email: string, password: string) => Promise<void>;
  signOut: () => void;
}

const AuthContext = createContext<(AuthState & AuthApi) | null>(null);

export function useAuth(): AuthState & AuthApi {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "initializing", user: null });

  // Bootstrap from localStorage (offline-fast; the 24h token is validated lazily
  // by the first authed call, which clears the session on 401).
  useEffect(() => {
    const token = getToken();
    const user = getStoredUser();
    setState(token && user ? { status: "signed_in", user } : { status: "signed_out", user: null });
  }, []);

  const loginWithPassword = useCallback(async (email: string, password: string) => {
    const res = await api.login(email, password);
    const user: BusinessOwnerUser = {
      id: res.user_id,
      email,
      full_name: res.full_name,
      role: res.role,
      business_id: null,
    };
    setSession(res.access_token, user);
    setState({ status: "signed_in", user });
  }, []);

  const signOut = useCallback(() => {
    clearSession();
    setState({ status: "signed_out", user: null });
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const handler = (event: PromiseRejectionEvent) => {
      if (event.reason instanceof AuthFatalError) {
        clearSession();
        setState({ status: "signed_out", user: null });
      }
    };
    window.addEventListener("unhandledrejection", handler);
    return () => window.removeEventListener("unhandledrejection", handler);
  }, []);

  const value = useMemo(
    () => ({ ...state, loginWithPassword, signOut }),
    [state, loginWithPassword, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
