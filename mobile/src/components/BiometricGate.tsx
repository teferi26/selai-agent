import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { AppState, AppStateStatus, Pressable, StyleSheet, Text, View } from "react-native";
import * as LocalAuthentication from "expo-local-authentication";
import { getBiometricLockEnabled } from "@/lib/settings";
import { button, colors, spacing, type } from "@/lib/theme";

// How long the app can stay in background before asking to unlock again.
const BACKGROUND_GRACE_MS = 30_000;

/**
 * Mejora 150 — optional biometric lock.
 *
 * When the user enabled "Bloquear con Face ID / huella" in Ajustes, an opaque
 * overlay with the selai logo covers the whole app at cold start and after
 * returning from background (>30s) until LocalAuthentication succeeds.
 * If the device has no biometrics enrolled, the gate fails open.
 */
export function BiometricGate({ children }: { children: ReactNode }) {
  // null = still deciding (setting being read at cold start) → keep covered.
  const [locked, setLocked] = useState<boolean | null>(null);
  const [authFailed, setAuthFailed] = useState(false);
  const authInFlight = useRef(false);
  const backgroundAt = useRef<number | null>(null);

  const authenticate = useCallback(async () => {
    if (authInFlight.current) return;
    authInFlight.current = true;
    setAuthFailed(false);
    try {
      const result = await LocalAuthentication.authenticateAsync({
        promptMessage: "Desbloquea selai",
        cancelLabel: "Cancelar",
      });
      if (result.success) {
        setLocked(false);
      } else {
        setAuthFailed(true);
      }
    } catch {
      setAuthFailed(true);
    } finally {
      authInFlight.current = false;
    }
  }, []);

  /** True when the lock should apply on this device right now. */
  const shouldLock = useCallback(async (): Promise<boolean> => {
    const enabled = await getBiometricLockEnabled();
    if (!enabled) return false;
    try {
      const [hasHardware, enrolled] = await Promise.all([
        LocalAuthentication.hasHardwareAsync(),
        LocalAuthentication.isEnrolledAsync(),
      ]);
      // Fail open: never brick the app on a device without biometrics.
      return hasHardware && enrolled;
    } catch {
      return false;
    }
  }, []);

  // Cold start: decide whether to lock, then prompt right away.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const lock = await shouldLock();
      if (cancelled) return;
      setLocked(lock);
      if (lock) void authenticate();
    })();
    return () => {
      cancelled = true;
    };
  }, [shouldLock, authenticate]);

  // Re-lock after >30s in background.
  useEffect(() => {
    const sub = AppState.addEventListener("change", (state: AppStateStatus) => {
      if (state === "background" || state === "inactive") {
        // Only start counting once; "inactive" fires on iOS before "background".
        if (backgroundAt.current === null) backgroundAt.current = Date.now();
        return;
      }
      if (state === "active") {
        const since = backgroundAt.current;
        backgroundAt.current = null;
        // While the biometric prompt is up the app reports inactive/background;
        // don't treat that round-trip as leaving the app.
        if (authInFlight.current) return;
        if (since !== null && Date.now() - since > BACKGROUND_GRACE_MS) {
          void (async () => {
            if (await shouldLock()) {
              setLocked(true);
              void authenticate();
            }
          })();
        }
      }
    });
    return () => sub.remove();
  }, [shouldLock, authenticate]);

  return (
    <View style={styles.root}>
      <View
        style={styles.content}
        pointerEvents={locked === false ? "auto" : "none"}
        accessibilityElementsHidden={locked !== false}
        importantForAccessibility={locked === false ? "auto" : "no-hide-descendants"}
      >
        {children}
      </View>
      {locked !== false ? (
        <View
          style={styles.overlay}
          accessibilityViewIsModal
          importantForAccessibility="yes"
        >
          <View
            accessibilityElementsHidden
            importantForAccessibility="no-hide-descendants"
            style={styles.logoBadge}
          >
            <Text style={styles.logoText}>s</Text>
          </View>
          <Text accessibilityRole="header" style={styles.title}>
            selai
          </Text>
          {locked === true ? (
            <>
              <Text accessibilityLiveRegion="polite" style={styles.subtitle}>
                Desbloquea con Face ID o tu huella
              </Text>
              {authFailed ? (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Reintentar desbloqueo biométrico"
                  style={styles.retryBtn}
                  onPress={() => void authenticate()}
                >
                  <Text style={styles.retryText}>Reintentar</Text>
                </Pressable>
              ) : null}
            </>
          ) : (
            <Text accessibilityLiveRegion="polite" style={styles.subtitle}>
              Comprobando la protección de la app
            </Text>
          )}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  content: { flex: 1 },
  overlay: {
    ...StyleSheet.absoluteFill,
    backgroundColor: colors.bg,
    alignItems: "center",
    justifyContent: "center",
    padding: spacing(8),
    zIndex: 1000,
  },
  logoBadge: {
    width: 72,
    height: 72,
    borderRadius: 20,
    backgroundColor: colors.primary,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: spacing(3),
  },
  logoText: { color: colors.white, fontSize: 40, fontWeight: "700" },
  title: { ...type.titleLarge, letterSpacing: -1 },
  subtitle: { ...type.bodySecondary, marginTop: spacing(2), textAlign: "center" },
  retryBtn: {
    marginTop: spacing(5),
    backgroundColor: colors.primary,
    paddingHorizontal: spacing(6),
    height: button.primaryHeight,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: button.radius,
  },
  retryText: { color: colors.white, fontWeight: "600", fontSize: 15 },
});
