import { useCallback, useEffect, useState } from "react";
import { View } from "react-native";
import { router, Stack, type ErrorBoundaryProps } from "expo-router";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { StatusBar } from "expo-status-bar";
import * as Updates from "expo-updates";
import { ClerkProvider, useAuth } from "@clerk/expo";
import { AgencyProvider, useAgency } from "@/context/AgencyContext";
import { BiometricGate } from "@/components/BiometricGate";
import { SupportModal } from "@/components/support";
import { ErrorView } from "@/components/ui";
import { registerTokenGetter } from "@/lib/api";
import { CLERK_PUBLISHABLE_KEY, clerkTokenCache, hasClerk } from "@/lib/auth";
import { useNotificationRouting } from "@/lib/push";
import { captureFatal, initSentry } from "@/lib/sentry";
import { installNotificationHandler } from "@/lib/settings";
import { errorReference } from "@/lib/support";
import { colors } from "@/lib/theme";

// Telemetría de errores. No-op mientras no haya EXPO_PUBLIC_SENTRY_DSN.
initSentry();

// Quiet-hours aware foreground notification handler (mejora 112).
installNotificationHandler();

/** Registers Clerk's getToken with the API client so every request is authed. */
function ClerkTokenBridge() {
  const { getToken } = useAuth();
  useEffect(() => {
    // Sin limpieza al desmontar a propósito: el ErrorBoundary de abajo sustituye
    // todo el árbol (este puente incluido) y necesita seguir pudiendo autenticar
    // el envío del informe de soporte. Cada montaje sobrescribe el anterior, y
    // al cerrar sesión getToken ya devuelve null por sí solo.
    registerTokenGetter(() => getToken());
  }, [getToken]);
  return null;
}

/**
 * Pantalla de último recurso: Expo Router la usa cuando una pantalla o layout
 * lanza durante el render. Antes de esto un error de JS dejaba la pantalla en
 * blanco, sin salida y sin que nos enterásemos.
 */
export function ErrorBoundary({ error, retry }: ErrorBoundaryProps) {
  const [reporting, setReporting] = useState(false);

  // Referencia que acompaña al informe de soporte: el id del evento de Sentry
  // si está activo y, si no, el propio error resumido. Se calcula ya en el
  // primer render para que el botón de reportar nunca vaya sin contexto, y el
  // efecto la sustituye por el id real cuando Sentry responde.
  const [errorRef, setErrorRef] = useState<string | null>(() => errorReference(error));
  useEffect(() => {
    setErrorRef(captureFatal(error, "error-boundary") ?? errorReference(error));
  }, [error]);

  const restart = useCallback(() => {
    void Updates.reloadAsync().catch(() => {
      // Fuera de una build con expo-updates (Expo Go, dev client) recargar no
      // está disponible: al menos devolvemos al usuario al inicio.
      try {
        router.replace("/");
      } catch {
        // Si ni el router responde, quedan Reintentar y el informe.
      }
    });
  }, []);

  return (
    <SafeAreaProvider>
      <View style={{ flex: 1, backgroundColor: colors.bg }}>
        <StatusBar style="dark" />
        <ErrorView
          title="La app se ha encontrado con un fallo"
          message="No hemos podido mostrar esta pantalla. Puedes reintentar; si vuelve a pasar, cuéntanoslo y lo revisamos."
          onRetry={() => void retry()}
          exitLabel="Reiniciar la app"
          onExit={restart}
          onReport={() => setReporting(true)}
        />
        <SupportModal
          visible={reporting}
          onClose={() => setReporting(false)}
          screen="error-boundary"
          errorRef={errorRef}
        />
      </View>
    </SafeAreaProvider>
  );
}

/** Lleva a la conversación cuando se toca un aviso de escalado. Vive dentro de
 * AgencyProvider porque solo debe navegar con la sesión ya resuelta: la
 * pantalla de conversación llama a la API nada más montarse. */
function NotificationRouter() {
  const { status } = useAgency();
  useNotificationRouting(status === "ready");
  return null;
}

function AppStack() {
  return (
    <AgencyProvider>
      <NotificationRouter />
      <BiometricGate>
        <StatusBar style="dark" />
        <Stack
          screenOptions={{
            headerStyle: { backgroundColor: colors.surface },
            headerTintColor: colors.ink,
            headerTitleStyle: { fontWeight: "600", color: colors.ink },
            headerShadowVisible: false,
            contentStyle: { backgroundColor: colors.bg },
          }}
        >
          <Stack.Screen name="index" options={{ headerShown: false }} />
          <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
          <Stack.Screen name="onboarding" options={{ headerShown: false }} />
          <Stack.Screen name="conversation/[id]" options={{ title: "Conversación" }} />
          <Stack.Screen name="lead/[id]" options={{ title: "Ficha del lead" }} />
          <Stack.Screen name="visit/[id]" options={{ title: "Visita" }} />
          <Stack.Screen name="settings" options={{ title: "Ajustes" }} />
          <Stack.Screen name="ai-settings" options={{ title: "Comportamiento de la IA" }} />
          <Stack.Screen name="billing" options={{ title: "Plan y facturación" }} />
          <Stack.Screen name="setup-checklist" options={{ title: "Puesta en marcha" }} />
          {/* Asistente de puesta en marcha: pantalla completa, cabecera propia. */}
          <Stack.Screen name="setup" options={{ headerShown: false }} />
        </Stack>
      </BiometricGate>
    </AgencyProvider>
  );
}

export default function RootLayout() {
  if (!hasClerk) {
    // Dev fallback: no Clerk key configured, the API client uses X-Dev-Email.
    return (
      <SafeAreaProvider>
        <AppStack />
      </SafeAreaProvider>
    );
  }
  return (
    <SafeAreaProvider>
      <ClerkProvider publishableKey={CLERK_PUBLISHABLE_KEY} tokenCache={clerkTokenCache}>
        <ClerkTokenBridge />
        <AppStack />
      </ClerkProvider>
    </SafeAreaProvider>
  );
}
