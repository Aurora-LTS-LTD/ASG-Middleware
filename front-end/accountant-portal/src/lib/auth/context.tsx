"use client";

/**
 * Aurora Accountant Portal — Auth context.
 *
 * Bootstraps auth state from the OS keychain on mount, exposes:
 *
 *   useAuth(): {
 *     status:    "initializing" | "signed_out" | "signed_in",
 *     user:      AccountantUser | null,
 *     deviceId:  number | null,
 *     signIn(...) ──┐
 *     signOut() ────┴── wrap api.* with state updates
 *   }
 *
 * The actual token lifecycle (refresh rotation, secure storage) lives
 * in src/lib/api/client.ts + src/lib/tauri/keychain.ts. This context
 * is just the React-state layer on top.
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
import { keychainGet, keychainClearAll, getDeviceFingerprint, getPlatform } from "@/lib/tauri/keychain";
import { KEYCHAIN_KEYS } from "@/types/api";
import type { AccountantUser, OtpVerifyResponse } from "@/types/api";

// ─────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────

export type AuthStatus = "initializing" | "signed_out" | "signed_in";

export interface AuthState {
  status: AuthStatus;
  user: AccountantUser | null;
  deviceId: number | null;
  /** Set during the verify call — clears next time user signs out. */
  isNewDevice: boolean;
}

export interface AuthApi {
  /**
   * Step 1: request OTP. Returns the masked email/method shown to the user.
   * Throws ApiClientError with `error: "otp_rate_limited"` etc.
   */
  requestOtp: (email: string) => Promise<{ sent_to: string; method: "email" | "whatsapp"; expires_in_seconds: number }>;

  /**
   * Step 2: verify OTP + device. Persists tokens, transitions state to signed_in.
   * Throws ApiClientError with structured detail on failure.
   */
  verifyOtp: (args: { email: string; otp: string; device_label?: string }) => Promise<OtpVerifyResponse>;

  signOut: () => Promise<void>;
}

interface AuthContextValue extends AuthState, AuthApi {}

// ─────────────────────────────────────────────────────────────
// Context
// ─────────────────────────────────────────────────────────────

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return ctx;
}

// ─────────────────────────────────────────────────────────────
// Provider
// ─────────────────────────────────────────────────────────────

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>({
    status: "initializing",
    user: null,
    deviceId: null,
    isNewDevice: false,
  });

  // Bootstrap: on mount, check if the keychain already has an access
  // token. If so, transition straight to signed_in (skip the OTP flow).
  // If the token is expired and refresh fails, fall back to signed_out.
  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const token = await keychainGet(KEYCHAIN_KEYS.accessToken);
        if (!token) {
          if (!cancelled) {
            setState({
              status: "signed_out",
              user: null,
              deviceId: null,
              isNewDevice: false,
            });
          }
          return;
        }

        const userIdStr = await keychainGet(KEYCHAIN_KEYS.userId);
        const email = await keychainGet(KEYCHAIN_KEYS.userEmail);
        const name = await keychainGet(KEYCHAIN_KEYS.userName);
        const deviceIdStr = await keychainGet(KEYCHAIN_KEYS.deviceId);

        if (!userIdStr || !email) {
          // Token exists but no user metadata — inconsistent state, sign out.
          await keychainClearAll();
          if (!cancelled) {
            setState({
              status: "signed_out",
              user: null,
              deviceId: null,
              isNewDevice: false,
            });
          }
          return;
        }

        // Optimistically restore. If a subsequent API call fails with
        // AuthFatalError, the client will clear the keychain and the
        // caller can re-render.
        if (!cancelled) {
          setState({
            status: "signed_in",
            user: {
              id: Number(userIdStr),
              email,
              name: name || email,
              role: "accountant",
              firm_name: null,
              license_number: null,
            },
            deviceId: deviceIdStr ? Number(deviceIdStr) : null,
            isNewDevice: false,
          });
        }
      } catch (err) {
        console.error("[AuthProvider] bootstrap failed:", err);
        await keychainClearAll().catch(() => undefined);
        if (!cancelled) {
          setState({
            status: "signed_out",
            user: null,
            deviceId: null,
            isNewDevice: false,
          });
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  // ──────────────────────────────────────────────────────────
  // API actions
  // ──────────────────────────────────────────────────────────

  const requestOtp = useCallback(async (email: string) => {
    const res = await api.otpSend({ email });
    return {
      sent_to: res.sent_to,
      method: res.method,
      expires_in_seconds: res.expires_in_seconds,
    };
  }, []);

  const verifyOtp = useCallback(
    async ({ email, otp, device_label }: { email: string; otp: string; device_label?: string }) => {
      const [fingerprint, platform] = await Promise.all([
        getDeviceFingerprint(),
        getPlatform(),
      ]);
      const safePlatform = platform === "unknown" ? "macos" : platform;
      const label = device_label || `${platform[0].toUpperCase() + platform.slice(1)} device`;

      const result = await api.otpVerify({
        email,
        otp,
        device_fingerprint: fingerprint,
        platform: safePlatform,
        device_label: label,
      });

      setState({
        status: "signed_in",
        user: result.user,
        deviceId: result.device_id,
        isNewDevice: result.is_new_device,
      });

      return result;
    },
    [],
  );

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } catch (err) {
      // logout is idempotent + best-effort; if backend is down, still
      // clear local state so the user can re-auth.
      console.warn("[AuthProvider] logout API call failed:", err);
    } finally {
      await keychainClearAll();
      setState({
        status: "signed_out",
        user: null,
        deviceId: null,
        isNewDevice: false,
      });
    }
  }, []);

  // Catch AuthFatalError from any place in the tree — if a fatal
  // 401 bubbles up, drop to signed_out so the UI re-renders the
  // login screen.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const handler = (event: PromiseRejectionEvent) => {
      if (event.reason instanceof AuthFatalError) {
        console.warn("[AuthProvider] AuthFatalError — forcing sign-out");
        setState({
          status: "signed_out",
          user: null,
          deviceId: null,
          isNewDevice: false,
        });
      }
    };
    window.addEventListener("unhandledrejection", handler);
    return () => window.removeEventListener("unhandledrejection", handler);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      requestOtp,
      verifyOtp,
      signOut,
    }),
    [state, requestOtp, verifyOtp, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
