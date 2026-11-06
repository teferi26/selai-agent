import { useEffect, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { Tabs, router } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { useAuth } from "@clerk/expo";
import { useAgency } from "@/context/AgencyContext";
import { SupportModal } from "@/components/support";
import { ErrorView, LoadingView } from "@/components/ui";
import { clearDevEmail } from "@/lib/api";
import { hasClerk } from "@/lib/auth";
import { tapHaptic } from "@/lib/haptics";
import { errorReference } from "@/lib/support";
import { colors } from "@/lib/theme";

// La bandeja es la primera pestaña pase lo que pase. Sin esto el grupo abre
// por el fichero que toque alfabéticamente, y al añadir "agenda" eso dejó de
// ser la bandeja.
export const unstable_settings = { initialRouteName: "inbox" };

/** The backend rejected our session (401/403). If we only navigated back to
 * the login, Clerk would still be signed in there and bounce us straight
 * back here — an infinite loop with the splash color flashing. Sign out of
 * Clerk first so the login screen actually stays. */
function ForceSignOut() {
  const { signOut } = useAuth();
  useEffect(() => {
    void (async () => {
      try {
        await signOut();
      } catch {
        // Already signed out or offline — either way, leaving is correct.
      }
      await clearDevEmail().catch(() => {});
      router.replace("/");
    })();
  }, [signOut]);
  return <LoadingView label="Cerrando sesión..." />;
}

/** Header gear that opens the Ajustes screen. */
function SettingsButton() {
  return (
    <Pressable
      onPress={() => {
        tapHaptic();
        router.push("/settings");
      }}
      hitSlop={10}
      style={{ marginRight: 16 }}
      accessibilityLabel="Ajustes"
      accessibilityRole="button"
    >
      <Ionicons name="settings-outline" size={22} color={colors.ink} />
    </Pressable>
  );
}

export default function TabsLayout() {
  const { status, error, bootstrap } = useAgency();
  const [reporting, setReporting] = useState(false);

  useEffect(() => {
    if (status === "idle") void bootstrap();
  }, [status, bootstrap]);

  useEffect(() => {
    if (status === "not_onboarded") router.replace("/onboarding");
  }, [status]);

  useEffect(() => {
    if (status === "unauthenticated" && !hasClerk) {
      // Dev mode: clear the stale dev-email credential and go log in again.
      void clearDevEmail().finally(() => router.replace("/"));
    }
  }, [status]);

  if (status === "unauthenticated" && hasClerk) {
    return <ForceSignOut />;
  }
  if (status === "idle" || status === "loading" || status === "unauthenticated") {
    return <LoadingView label="Cargando tu agencia..." />;
  }
  if (status === "error") {
    // `error` holds the raw technical detail (often English). Log it for
    // support, never show it to the user.
    if (error) console.log("[agency] bootstrap error:", error);
    return (
      // El aviso de "escríbenos" era texto muerto: ahora el formulario se abre
      // desde aquí mismo, que es donde el usuario está atascado.
      <View style={styles.errorScreen}>
        <ErrorView
          title="No hemos podido conectar con selai"
          message="Comprueba tu conexión e inténtalo de nuevo. Si el problema sigue, cuéntanoslo y lo miramos."
          onRetry={() => void bootstrap()}
          exitLabel="Volver a iniciar sesión"
          onExit={() => void clearDevEmail().finally(() => router.replace("/"))}
          onReport={() => setReporting(true)}
        />
        <SupportModal
          visible={reporting}
          onClose={() => setReporting(false)}
          screen="/(tabs)"
          errorRef={errorReference(error)}
        />
      </View>
    );
  }
  if (status === "not_onboarded") {
    return <LoadingView label="Redirigiendo..." />;
  }

  return (
    <Tabs
      screenOptions={{
        headerStyle: { backgroundColor: colors.surface },
        headerTintColor: colors.ink,
        headerTitleStyle: { fontWeight: "600", color: colors.ink },
        headerShadowVisible: false,
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.inkSecondary,
        tabBarStyle: {
          backgroundColor: colors.surface,
          borderTopWidth: StyleSheet.hairlineWidth,
          borderTopColor: colors.hairline,
        },
        tabBarLabelStyle: { fontSize: 11, fontWeight: "500" },
        sceneStyle: { backgroundColor: colors.bg },
        headerRight: () => <SettingsButton />,
      }}
    >
      <Tabs.Screen
        name="inbox"
        options={{
          title: "Bandeja",
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="mail-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="leads"
        options={{
          title: "Leads",
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="people-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="agenda"
        options={{
          title: "Agenda",
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="calendar-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="properties"
        options={{
          title: "Inmuebles",
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="home-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="metrics"
        options={{
          title: "Métricas",
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="stats-chart-outline" size={size} color={color} />
          ),
        }}
      />
    </Tabs>
  );
}

const styles = StyleSheet.create({
  errorScreen: { flex: 1, backgroundColor: colors.bg },
});
