import * as SecureStore from "expo-secure-store";
import * as Notifications from "expo-notifications";

// ---------------------------------------------------------------------------
// App settings persisted in SecureStore.
//
// - Biometric lock (mejora 150): boolean flag; the gate itself lives in
//   src/components/BiometricGate.tsx.
// - Quiet hours (mejora 112): {enabled, start, end} applied through
//   Notifications.setNotificationHandler — notifications still arrive, but
//   silently (no banner/sound) while the local time is inside the range.
// ---------------------------------------------------------------------------

const BIOMETRIC_KEY = "settings_biometric_lock";
const QUIET_HOURS_KEY = "settings_quiet_hours";

// --- Biometric lock ---------------------------------------------------------

export async function getBiometricLockEnabled(): Promise<boolean> {
  try {
    return (await SecureStore.getItemAsync(BIOMETRIC_KEY)) === "1";
  } catch {
    return false;
  }
}

export async function setBiometricLockEnabled(enabled: boolean): Promise<void> {
  try {
    if (enabled) await SecureStore.setItemAsync(BIOMETRIC_KEY, "1");
    else await SecureStore.deleteItemAsync(BIOMETRIC_KEY);
  } catch {
    // best effort — worst case the toggle doesn't persist
  }
}

// --- Quiet hours ------------------------------------------------------------

export type QuietHours = {
  enabled: boolean;
  /** Start hour, 0-23 (inclusive). */
  start: number;
  /** End hour, 0-23 (exclusive). May be lower than start (crosses midnight). */
  end: number;
};

export const DEFAULT_QUIET_HOURS: QuietHours = { enabled: false, start: 22, end: 8 };

// In-memory copy so the notification handler is synchronous-friendly and
// doesn't hit SecureStore on every incoming notification.
let cachedQuietHours: QuietHours = { ...DEFAULT_QUIET_HOURS };

export async function getQuietHours(): Promise<QuietHours> {
  try {
    const raw = await SecureStore.getItemAsync(QUIET_HOURS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<QuietHours>;
      cachedQuietHours = {
        enabled: parsed.enabled === true,
        start: clampHour(parsed.start, DEFAULT_QUIET_HOURS.start),
        end: clampHour(parsed.end, DEFAULT_QUIET_HOURS.end),
      };
    }
  } catch {
    // keep defaults
  }
  return cachedQuietHours;
}

export async function setQuietHours(value: QuietHours): Promise<void> {
  cachedQuietHours = {
    enabled: value.enabled,
    start: clampHour(value.start, DEFAULT_QUIET_HOURS.start),
    end: clampHour(value.end, DEFAULT_QUIET_HOURS.end),
  };
  try {
    await SecureStore.setItemAsync(QUIET_HOURS_KEY, JSON.stringify(cachedQuietHours));
  } catch {
    // best effort
  }
}

function clampHour(value: unknown, fallback: number): number {
  const n = typeof value === "number" ? Math.trunc(value) : NaN;
  return Number.isInteger(n) && n >= 0 && n <= 23 ? n : fallback;
}

/**
 * True when `hour` falls inside the silent range [start, end).
 * Handles ranges that cross midnight (e.g. 22 -> 8).
 * A zero-length range (start === end) never silences.
 */
export function isQuietHour(q: QuietHours, hour: number): boolean {
  if (!q.enabled || q.start === q.end) return false;
  if (q.start < q.end) return hour >= q.start && hour < q.end;
  return hour >= q.start || hour < q.end; // crosses midnight
}

/**
 * Installs the foreground notification handler. Called once at app start
 * (app/_layout.tsx). While the local time is inside the quiet range the
 * notification is delivered silently: no banner, no sound.
 */
export function installNotificationHandler(): void {
  Notifications.setNotificationHandler({
    handleNotification: async () => {
      const quiet = isQuietHour(await getQuietHours(), new Date().getHours());
      return {
        shouldShowBanner: !quiet,
        shouldShowList: true,
        shouldPlaySound: !quiet,
        shouldSetBadge: true,
      };
    },
  });
}
