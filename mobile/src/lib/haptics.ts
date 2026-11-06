// Thin wrappers around expo-haptics: primary-action taps and success moments.
// Fire-and-forget, never throw (no-op on web / unsupported devices).
import * as Haptics from "expo-haptics";

/** Light selection tick for primary actions (button presses, toggles). */
export function tapHaptic(): void {
  Haptics.selectionAsync().catch(() => {});
}

/** Success notification for completed actions (handoff, upload, login). */
export function successHaptic(): void {
  Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
}

/** Warning notification for destructive confirmations. */
export function warningHaptic(): void {
  Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning).catch(() => {});
}
