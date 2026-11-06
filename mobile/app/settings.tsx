import { useCallback, useEffect, useRef, useState } from "react";
import {
  FlatList,
  Linking,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  View,
} from "react-native";
import { router } from "expo-router";
import * as Application from "expo-application";
import Constants from "expo-constants";
import * as LocalAuthentication from "expo-local-authentication";
import { Ionicons } from "@expo/vector-icons";
import { useAuth, useUser } from "@clerk/expo";
import { useAgency } from "@/context/AgencyContext";
import { SupportModal } from "@/components/support";
import { IconName } from "@/components/ui";
import { clearDevEmail, getDevEmail } from "@/lib/api";
import { hasClerk } from "@/lib/auth";
import { tapHaptic, warningHaptic } from "@/lib/haptics";
import { unregisterPushToken } from "@/lib/push";
import {
  DEFAULT_QUIET_HOURS,
  getBiometricLockEnabled,
  getQuietHours,
  QuietHours,
  setBiometricLockEnabled,
  setQuietHours,
} from "@/lib/settings";
import { colors, radius, shadow, spacing, type } from "@/lib/theme";

const HOURS = Array.from({ length: 24 }, (_, h) => h);

function formatHour(h: number): string {
  return `${String(h).padStart(2, "0")}:00`;
}

// ---------------------------------------------------------------------------
// Building blocks: sections with cards, icon+text(+chevron) rows.
// ---------------------------------------------------------------------------

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      <View style={styles.sectionCard}>{children}</View>
    </View>
  );
}

function RowIcon({ icon, tone = "neutral" }: { icon: IconName; tone?: "neutral" | "danger" }) {
  return (
    <View
      style={[
        styles.rowIcon,
        tone === "danger" ? { backgroundColor: colors.dangerSoft } : null,
      ]}
    >
      <Ionicons
        name={icon}
        size={16}
        color={tone === "danger" ? colors.danger : colors.primary}
      />
    </View>
  );
}

function Row({
  icon,
  label,
  hint,
  right,
  last,
  onPress,
  destructive,
  disabled,
}: {
  icon: IconName;
  label: string;
  hint?: string;
  right?: React.ReactNode;
  last?: boolean;
  onPress?: () => void;
  destructive?: boolean;
  disabled?: boolean;
}) {
  const content = (
    <>
      <RowIcon icon={icon} tone={destructive ? "danger" : "neutral"} />
      <View style={styles.rowText}>
        <Text style={[styles.rowLabel, destructive ? { color: colors.dangerInk } : null]}>
          {label}
        </Text>
        {hint ? <Text style={styles.rowHint}>{hint}</Text> : null}
      </View>
      {right}
      {onPress && !right ? (
        <Ionicons name="chevron-forward" size={17} color={colors.inkSecondary} />
      ) : null}
    </>
  );
  if (onPress) {
    return (
      <Pressable
        style={({ pressed }) => [
          styles.row,
          last ? styles.rowLast : null,
          disabled ? { opacity: 0.6 } : null,
          pressed && !disabled ? { opacity: 0.8 } : null,
        ]}
        disabled={disabled}
        onPress={() => {
          tapHaptic();
          onPress();
        }}
        accessibilityRole="button"
        accessibilityState={{ disabled: !!disabled, busy: !!disabled }}
      >
        {content}
      </Pressable>
    );
  }
  return <View style={[styles.row, last ? styles.rowLast : null]}>{content}</View>;
}

/** Modal with the 00-23h list, no extra dependencies. */
function HourPickerModal({
  visible,
  title,
  selected,
  onSelect,
  onClose,
}: {
  visible: boolean;
  title: string;
  selected: number;
  onSelect: (hour: number) => void;
  onClose: () => void;
}) {
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={styles.modalBackdrop} onPress={onClose}>
        <Pressable style={styles.modalCard} onPress={(e) => e.stopPropagation()}>
          <Text style={styles.modalTitle}>{title}</Text>
          <FlatList
            data={HOURS}
            keyExtractor={(h) => String(h)}
            style={styles.modalList}
            initialScrollIndex={selected}
            getItemLayout={(_, index) => ({
              length: 44,
              offset: 44 * index,
              index,
            })}
            renderItem={({ item }) => (
              <Pressable
                style={[styles.hourRow, item === selected ? styles.hourRowActive : null]}
                onPress={() => {
                  tapHaptic();
                  onSelect(item);
                  onClose();
                }}
              >
                <Text
                  style={[styles.hourText, item === selected ? styles.hourTextActive : null]}
                >
                  {formatHour(item)}
                </Text>
              </Pressable>
            )}
          />
        </Pressable>
      </Pressable>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Account: Clerk email needs hooks that only exist under ClerkProvider,
// so it lives in its own component gated by hasClerk.
// ---------------------------------------------------------------------------

function ClerkAccountEmail() {
  const { user } = useUser();
  const email = user?.primaryEmailAddress?.emailAddress ?? "—";
  return <Text style={styles.rowValue}>{email}</Text>;
}

function DevAccountEmail() {
  const [email, setEmail] = useState<string | null>(null);
  useEffect(() => {
    void getDevEmail().then(setEmail);
  }, []);
  return <Text style={styles.rowValue}>{email ?? "—"}</Text>;
}

function ClerkSignOutRow({
  onBefore,
  onAfter,
}: {
  onBefore: () => Promise<void>;
  onAfter: () => Promise<void>;
}) {
  const { signOut } = useAuth();
  const signingOutRef = useRef(false);
  const [signingOut, setSigningOut] = useState(false);
  return (
    <Row
      icon="log-out-outline"
      label={signingOut ? "Cerrando sesión…" : "Cerrar sesión"}
      destructive
      last
      disabled={signingOut}
      onPress={() => {
        if (signingOutRef.current) return;
        signingOutRef.current = true;
        setSigningOut(true);
        warningHaptic();
        void (async () => {
          // Push-token cleanup is best effort: a network failure must never
          // leave someone trapped in an authenticated session.
          try {
            await onBefore();
          } catch {
            // Continue with local and Clerk sign-out.
          }
          try {
            await signOut();
          } catch {
            // even if Clerk fails, clear local state below
          }
          try {
            await onAfter();
          } finally {
            signingOutRef.current = false;
            setSigningOut(false);
          }
        })();
      }}
    />
  );
}

function DevSignOutRow({
  onBefore,
  onAfter,
}: {
  onBefore: () => Promise<void>;
  onAfter: () => Promise<void>;
}) {
  const signingOutRef = useRef(false);
  const [signingOut, setSigningOut] = useState(false);
  return (
    <Row
      icon="log-out-outline"
      label={signingOut ? "Cerrando sesión…" : "Cerrar sesión"}
      destructive
      last
      disabled={signingOut}
      onPress={() => {
        if (signingOutRef.current) return;
        signingOutRef.current = true;
        setSigningOut(true);
        warningHaptic();
        void (async () => {
          try {
            await onBefore();
          } catch {
            // Continue with local sign-out when the backend is unavailable.
          }
          try {
            await onAfter();
          } finally {
            signingOutRef.current = false;
            setSigningOut(false);
          }
        })();
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------

export default function SettingsScreen() {
  const { me, reset } = useAgency();

  // (150) Biometric lock
  const [biometricAvailable, setBiometricAvailable] = useState(false);
  const [biometricEnabled, setBiometricEnabled] = useState(false);

  // (112) Quiet hours
  const [quiet, setQuiet] = useState<QuietHours>(DEFAULT_QUIET_HOURS);
  const [picker, setPicker] = useState<"start" | "end" | null>(null);

  // Formulario de soporte
  const [reporting, setReporting] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const [hasHardware, enrolled] = await Promise.all([
          LocalAuthentication.hasHardwareAsync(),
          LocalAuthentication.isEnrolledAsync(),
        ]);
        setBiometricAvailable(hasHardware && enrolled);
      } catch {
        setBiometricAvailable(false);
      }
      setBiometricEnabled(await getBiometricLockEnabled());
      setQuiet(await getQuietHours());
    })();
  }, []);

  const toggleBiometric = useCallback(async (value: boolean) => {
    if (value) {
      // Ask for biometrics right now: don't let anyone lock the app for a
      // future user without proving they can unlock it themselves.
      try {
        const result = await LocalAuthentication.authenticateAsync({
          promptMessage: "Desbloquea selai",
        });
        if (!result.success) return;
      } catch {
        return;
      }
    }
    setBiometricEnabled(value);
    await setBiometricLockEnabled(value);
  }, []);

  const updateQuiet = useCallback(async (next: QuietHours) => {
    setQuiet(next);
    await setQuietHours(next);
  }, []);

  const afterSignOut = useCallback(async () => {
    await clearDevEmail();
    reset();
    router.replace("/");
  }, [reset]);

  const beforeSignOut = useCallback(async () => {
    if (me?.agency_id) await unregisterPushToken(me.agency_id);
  }, [me?.agency_id]);

  const version = Constants.expoConfig?.version ?? "?";
  const build = Application.nativeBuildVersion ?? "dev";

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Section title="Cuenta">
        <Row
          icon="mail-outline"
          label="Email"
          right={hasClerk ? <ClerkAccountEmail /> : <DevAccountEmail />}
        />
        <Row
          icon="card-outline"
          label="Plan y facturación"
          hint="Tu plan, el estado de la suscripción y los días de prueba."
          onPress={() => router.push("/billing")}
        />
        <Row
          icon="rocket-outline"
          label="Puesta en marcha"
          hint="Consulta qué pasos de configuración te quedan."
          onPress={() => router.push("/setup-checklist")}
        />
        <Row
          icon="information-circle-outline"
          label="Versión"
          right={<Text style={styles.rowValue}>{`v${version} (${build})`}</Text>}
          last
        />
      </Section>

      {/* Sección propia y no una fila más dentro de "Cuenta": es lo único de
          aquí que cambia lo que tus clientes oyen, no cómo funciona la app. */}
      <Section title="Tu asistente">
        <Row
          icon="sparkles-outline"
          label="Comportamiento de la IA"
          hint="Tono, frases prohibidas, instrucciones, horario y aviso de grabación."
          last
          onPress={() => router.push("/ai-settings")}
        />
      </Section>

      {biometricAvailable ? (
        <Section title="Seguridad">
          <Row
            icon="finger-print-outline"
            label="Bloquear con Face ID / huella"
            hint="Pide desbloquear al abrir la app o al volver tras un rato."
            last
            right={
              <Switch
                accessibilityLabel="Bloquear con Face ID o huella"
                value={biometricEnabled}
                onValueChange={(v) => {
                  tapHaptic();
                  void toggleBiometric(v);
                }}
                trackColor={{ true: colors.primary, false: colors.hairline }}
                thumbColor={colors.white}
              />
            }
          />
        </Section>
      ) : null}

      <Section title="Notificaciones">
        <Row
          icon="moon-outline"
          label="No molestar por horario"
          hint="Las notificaciones seguirán llegando en silencio."
          last={!quiet.enabled}
          right={
            <Switch
              accessibilityLabel="No molestar por horario"
              value={quiet.enabled}
              onValueChange={(v) => {
                tapHaptic();
                void updateQuiet({ ...quiet, enabled: v });
              }}
              trackColor={{ true: colors.primary, false: colors.hairline }}
              thumbColor={colors.white}
            />
          }
        />
        {quiet.enabled ? (
          <>
            <Row
              icon="time-outline"
              label="Desde"
              right={
                <Pressable
                  style={styles.hourBtn}
                  accessibilityRole="button"
                  onPress={() => {
                    tapHaptic();
                    setPicker("start");
                  }}
                >
                  <Text style={styles.hourBtnText}>{formatHour(quiet.start)}</Text>
                </Pressable>
              }
            />
            <Row
              icon="time-outline"
              label="Hasta"
              last
              right={
                <Pressable
                  style={styles.hourBtn}
                  accessibilityRole="button"
                  onPress={() => {
                    tapHaptic();
                    setPicker("end");
                  }}
                >
                  <Text style={styles.hourBtnText}>{formatHour(quiet.end)}</Text>
                </Pressable>
              }
            />
          </>
        ) : null}
      </Section>

      <Section title="Ayuda">
        <Row
          icon="chatbubble-ellipses-outline"
          label="Reportar un problema"
          hint="Nos llega tu mensaje con la versión de la app y la pantalla."
          onPress={() => setReporting(true)}
        />
        <Row
          icon="help-circle-outline"
          label="Centro de ayuda"
          onPress={() => void Linking.openURL("https://www.selai.es/ayuda")}
        />
        <Row
          icon="document-text-outline"
          label="Política de privacidad"
          last
          onPress={() => void Linking.openURL("https://www.selai.es/privacidad")}
        />
      </Section>

      <Section title="Sesión">
        {hasClerk ? (
          <ClerkSignOutRow onBefore={beforeSignOut} onAfter={afterSignOut} />
        ) : (
          <DevSignOutRow onBefore={beforeSignOut} onAfter={afterSignOut} />
        )}
      </Section>

      <HourPickerModal
        visible={picker === "start"}
        title="Inicio del silencio"
        selected={quiet.start}
        onSelect={(h) => void updateQuiet({ ...quiet, start: h })}
        onClose={() => setPicker(null)}
      />
      <HourPickerModal
        visible={picker === "end"}
        title="Fin del silencio"
        selected={quiet.end}
        onSelect={(h) => void updateQuiet({ ...quiet, end: h })}
        onClose={() => setPicker(null)}
      />

      <SupportModal
        visible={reporting}
        onClose={() => setReporting(false)}
        screen="/settings"
      />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: spacing(4), paddingBottom: spacing(12) },
  section: { marginBottom: spacing(5) },
  sectionTitle: {
    ...type.meta,
    textTransform: "uppercase",
    letterSpacing: 0.5,
    fontSize: 12,
    marginBottom: spacing(2),
    marginLeft: spacing(1),
  },
  sectionCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    overflow: "hidden",
    ...shadow.card,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(3),
    paddingHorizontal: spacing(4),
    paddingVertical: spacing(3),
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.hairline,
    minHeight: 52,
  },
  rowLast: { borderBottomWidth: 0 },
  rowIcon: {
    width: 30,
    height: 30,
    borderRadius: 15,
    backgroundColor: colors.primarySoft,
    alignItems: "center",
    justifyContent: "center",
  },
  rowText: { flex: 1 },
  rowLabel: { ...type.body, fontWeight: "500" },
  rowHint: { ...type.meta, fontWeight: "400", fontSize: 12, marginTop: 2 },
  rowValue: { ...type.meta, fontWeight: "400", fontSize: 14, flexShrink: 1 },
  hourBtn: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    borderRadius: radius.small,
    paddingHorizontal: spacing(4),
    paddingVertical: spacing(2),
    backgroundColor: colors.bg,
  },
  hourBtnText: { color: colors.primary, fontWeight: "600", fontSize: 15 },
  modalBackdrop: {
    flex: 1,
    backgroundColor: colors.overlay,
    alignItems: "center",
    justifyContent: "center",
    padding: spacing(8),
  },
  modalCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    padding: spacing(4),
    width: "100%",
    maxWidth: 320,
    maxHeight: "70%",
  },
  modalTitle: {
    ...type.heading,
    fontSize: 16,
    marginBottom: spacing(2),
    textAlign: "center",
  },
  modalList: { flexGrow: 0 },
  hourRow: {
    height: 44,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: radius.small,
  },
  hourRowActive: { backgroundColor: colors.primarySoft },
  hourText: { ...type.body },
  hourTextActive: { color: colors.primary, fontWeight: "700" },
});
