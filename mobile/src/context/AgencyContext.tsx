import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";
import { ApiError, fetchMe, Me } from "@/lib/api";
import { registerPushToken } from "@/lib/push";

export type AgencyStatus =
  | "idle"
  | "loading"
  | "ready"
  | "error"
  | "unauthenticated"
  | "not_onboarded";

type AgencyContextValue = {
  me: Me | null;
  status: AgencyStatus;
  error: string | null;
  /** Fetch /me and resolve the agency. Safe to call repeatedly. */
  bootstrap: () => Promise<void>;
  /** Clear all state (on sign out). */
  reset: () => void;
};

const AgencyContext = createContext<AgencyContextValue | null>(null);

export function AgencyProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [status, setStatus] = useState<AgencyStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  const bootstrap = useCallback(async () => {
    setStatus("loading");
    setError(null);
    try {
      const data = await fetchMe();
      setMe(data);
      setStatus("ready");
      // Fire-and-forget: best-effort push token registration (no-op in Expo Go).
      registerPushToken(data.agency_id).catch(() => {});
    } catch (e) {
      setMe(null);
      if (e instanceof ApiError && e.status === 404) {
        setStatus("not_onboarded");
      } else if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
        // Invalid/absent session (e.g. stale dev-email against production):
        // never strand the user on an error screen — send them to log in.
        setStatus("unauthenticated");
      } else {
        setStatus("error");
        setError(e instanceof Error ? e.message : "Error desconocido");
      }
    }
  }, []);

  const reset = useCallback(() => {
    setMe(null);
    setStatus("idle");
    setError(null);
  }, []);

  const value = useMemo(
    () => ({ me, status, error, bootstrap, reset }),
    [me, status, error, bootstrap, reset]
  );

  return <AgencyContext.Provider value={value}>{children}</AgencyContext.Provider>;
}

export function useAgency(): AgencyContextValue {
  const ctx = useContext(AgencyContext);
  if (!ctx) throw new Error("useAgency must be used inside AgencyProvider");
  return ctx;
}
