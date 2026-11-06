import * as SecureStore from "expo-secure-store";

export type AppEnvironment = "development" | "preview" | "production";

const configuredEnvironment = process.env.EXPO_PUBLIC_APP_ENV;
export const APP_ENVIRONMENT: AppEnvironment =
  configuredEnvironment === "development" ||
  configuredEnvironment === "preview" ||
  configuredEnvironment === "production"
    ? configuredEnvironment
    : __DEV__
      ? "development"
      : "production";

export const CLERK_PUBLISHABLE_KEY =
  process.env.EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY?.trim() ?? "";

if (APP_ENVIRONMENT === "production" && !CLERK_PUBLISHABLE_KEY.startsWith("pk_live_")) {
  throw new Error("La app de producción requiere una clave Clerk pk_live_.");
}

if (APP_ENVIRONMENT === "preview" && !CLERK_PUBLISHABLE_KEY.startsWith("pk_test_")) {
  throw new Error("La app preview requiere una clave Clerk pk_test_.");
}

if (
  APP_ENVIRONMENT === "development" &&
  CLERK_PUBLISHABLE_KEY &&
  !CLERK_PUBLISHABLE_KEY.startsWith("pk_test_")
) {
  throw new Error("El desarrollo local solo admite una clave Clerk pk_test_.");
}

/**
 * The email-only fallback is deliberately impossible in preview and release
 * bundles, even if a token refresh fails or a stale dev email remains stored.
 */
export const hasClerk = CLERK_PUBLISHABLE_KEY.length > 0;
export const isDevAuthEnabled = __DEV__ && APP_ENVIRONMENT === "development" && !hasClerk;

/** SecureStore-backed token cache for ClerkProvider. */
export const clerkTokenCache = {
  async getToken(key: string): Promise<string | null> {
    try {
      return await SecureStore.getItemAsync(key);
    } catch {
      return null;
    }
  },
  async saveToken(key: string, value: string): Promise<void> {
    try {
      await SecureStore.setItemAsync(key, value);
    } catch {
      // ignore — Clerk will re-fetch the token
    }
  },
};
