const ALLOWED_APP_ENVIRONMENTS = new Set(["development", "preview", "production"]);
const PRODUCTION_API_URL = "https://api.selai.es";

function normalizeUrl(value) {
  return (value ?? "").trim().replace(/\/$/, "");
}

function validateClerkConfiguration(appEnvironment, publishableKey) {
  if (!ALLOWED_APP_ENVIRONMENTS.has(appEnvironment)) {
    throw new Error(
      `EXPO_PUBLIC_APP_ENV must be development, preview or production; received ${appEnvironment}.`,
    );
  }

  if (appEnvironment === "production" && !publishableKey.startsWith("pk_live_")) {
    throw new Error(
      "Production requires EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY with a pk_live_ value from the EAS production environment.",
    );
  }

  if (appEnvironment === "preview" && !publishableKey.startsWith("pk_test_")) {
    throw new Error(
      "Preview requires EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY with a pk_test_ value from the EAS preview environment.",
    );
  }

  if (
    appEnvironment === "development" &&
    publishableKey &&
    !publishableKey.startsWith("pk_test_")
  ) {
    throw new Error("Development only accepts a pk_test_ Clerk publishable key.");
  }
}

function validateApiConfiguration(appEnvironment, apiUrl) {
  if (!ALLOWED_APP_ENVIRONMENTS.has(appEnvironment)) {
    throw new Error(
      `EXPO_PUBLIC_APP_ENV must be development, preview or production; received ${appEnvironment}.`,
    );
  }

  const normalizedApiUrl = normalizeUrl(apiUrl);
  if (appEnvironment !== "development" && !normalizedApiUrl) {
    throw new Error(
      `${appEnvironment} requires an explicit EXPO_PUBLIC_API_URL in its EAS environment.`,
    );
  }

  if (normalizedApiUrl) {
    let parsed;
    try {
      parsed = new URL(normalizedApiUrl);
    } catch {
      throw new Error("EXPO_PUBLIC_API_URL must be a valid absolute URL.");
    }
    if (appEnvironment !== "development" && parsed.protocol !== "https:") {
      throw new Error(`${appEnvironment} requires an HTTPS API URL.`);
    }
  }

  if (appEnvironment === "production" && normalizedApiUrl !== PRODUCTION_API_URL) {
    throw new Error(`Production must use ${PRODUCTION_API_URL}.`);
  }

  if (appEnvironment === "preview" && normalizedApiUrl === PRODUCTION_API_URL) {
    throw new Error(
      "Preview must use an isolated non-production API and cannot point to api.selai.es.",
    );
  }

  return normalizedApiUrl;
}

module.exports = ({ config }) => {
  const appEnvironment =
    process.env.EXPO_PUBLIC_APP_ENV ?? process.env.EAS_BUILD_PROFILE ?? "development";
  const publishableKey = process.env.EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY?.trim() ?? "";
  const apiUrl = validateApiConfiguration(
    appEnvironment,
    process.env.EXPO_PUBLIC_API_URL,
  );
  const sentryDsn = process.env.EXPO_PUBLIC_SENTRY_DSN?.trim() ?? "";

  validateClerkConfiguration(appEnvironment, publishableKey);
  // Project-specific EAS identifiers come from the environment, not from git.
  const easProjectId = process.env.EAS_PROJECT_ID?.trim() ?? "";
  const expoOwner = process.env.EXPO_OWNER?.trim() ?? "";
  return {
    ...config,
    ...(expoOwner ? { owner: expoOwner } : {}),
    ...(easProjectId
      ? { updates: { ...config.updates, url: `https://u.expo.dev/${easProjectId}` } }
      : {}),
    extra: {
      ...config.extra,
      ...(easProjectId ? { eas: { ...config.extra?.eas, projectId: easProjectId } } : {}),
      appEnvironment,
      apiUrl,
      sentryDsn,
      sentryEnvironment: appEnvironment,
    },
  };
};

module.exports.validateClerkConfiguration = validateClerkConfiguration;
module.exports.validateApiConfiguration = validateApiConfiguration;
module.exports.PRODUCTION_API_URL = PRODUCTION_API_URL;
