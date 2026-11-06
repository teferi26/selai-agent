// Pantalla final del asistente: qué quedó conectado y qué queda pendiente.
// Sin promesas: cada línea sale de lo que el backend confirma.
import { useEffect, useRef } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useSetupWizard } from "@/context/SetupWizardContext";
import { SETUP_TOTAL } from "@/lib/setup";
import { PrimaryButton, SecondaryButton, Skeleton } from "@/components/ui";
import { HonestNote } from "@/components/setup/kit";
import { successHaptic } from "@/lib/haptics";
import { colors, radius, shadow, spacing, type } from "@/lib/theme";

export default function StepDone({
  onFinish,
  onReview,
}: {
  onFinish: () => void;
  onReview: (stepNumber: number) => void;
}) {
  const { steps, doneCount, loading, reload, refreshChannels } = useSetupWizard();
  const refreshed = useRef(false);
  const celebrated = useRef(false);

  useEffect(() => {
    if (refreshed.current) return;
    refreshed.current = true;
    void reload();
    void refreshChannels();
  }, [reload, refreshChannels]);

  useEffect(() => {
    if (celebrated.current || loading || steps.length === 0) return;
    celebrated.current = true;
    successHaptic();
  }, [loading, steps.length]);

  const total = steps.length || SETUP_TOTAL;
  const pending = steps.filter((s) => !s.done);
  const allDone = steps.length > 0 && pending.length === 0;

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      keyboardShouldPersistTaps="handled"
    >
      <View style={styles.hero}>
        <View style={styles.heroRing}>
          <Ionicons name="sparkles" size={30} color={colors.gold} />
        </View>
        <Text style={styles.heroTitle}>
          {allDone ? "Tu IA ya está al 100%" : "Tu IA ya está trabajando"}
        </Text>
        <Text style={styles.heroSubtitle}>
          {allDone
            ? "Los nueve pasos están hechos. A partir de ahora tu asistente atiende, cualifica y te avisa."
            : `Has completado ${doneCount} de ${total} pasos. Lo que ya está conectado funciona desde este momento.`}
        </Text>
      </View>

      {loading && steps.length === 0 ? (
        <View style={styles.card}>
          <Skeleton width={"60%"} height={14} />
          <Skeleton width={"85%"} height={12} />
          <Skeleton width={"70%"} height={12} />
        </View>
      ) : (
        <View style={styles.card}>
          {steps.map((s, i) => (
            <View
              key={s.key}
              style={[styles.row, i === steps.length - 1 ? styles.rowLast : null]}
            >
              <Ionicons
                name={s.done ? "checkmark-circle" : "ellipse-outline"}
                size={20}
                color={s.done ? colors.success : colors.hairline}
              />
              <Text style={[styles.rowTitle, s.done ? styles.rowTitleDone : null]}>
                {s.title}
              </Text>
              <Text style={[styles.rowState, s.done ? styles.rowStateDone : null]}>
                {s.done ? "Conectado" : "Pendiente"}
              </Text>
            </View>
          ))}
        </View>
      )}

      {pending.length > 0 ? (
        <View style={{ gap: spacing(3) }}>
          <HonestNote>
            Lo pendiente no bloquea nada: puedes volver cuando quieras desde «Puesta en
            marcha» y retomarlo justo donde lo dejaste.
          </HonestNote>
          <SecondaryButton
            label="Volver al primer paso pendiente"
            icon="arrow-back-outline"
            onPress={() => {
              const idx = steps.findIndex((s) => !s.done);
              onReview(idx === -1 ? 1 : idx + 1);
            }}
          />
        </View>
      ) : null}

      <PrimaryButton label="Ir a la bandeja" icon="arrow-forward" onPress={onFinish} />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: spacing(5), paddingBottom: spacing(10), gap: spacing(5) },
  hero: { alignItems: "center", gap: spacing(3), paddingTop: spacing(6) },
  heroRing: {
    width: 84,
    height: 84,
    borderRadius: 42,
    backgroundColor: colors.goldSoft,
    borderWidth: 1,
    borderColor: colors.gold,
    alignItems: "center",
    justifyContent: "center",
  },
  heroTitle: { ...type.titleLarge, textAlign: "center" },
  heroSubtitle: { ...type.bodySecondary, textAlign: "center", lineHeight: 22 },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    overflow: "hidden",
    gap: spacing(2),
    padding: spacing(2),
    ...shadow.card,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(3),
    paddingHorizontal: spacing(3),
    paddingVertical: spacing(3),
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.hairline,
  },
  rowLast: { borderBottomWidth: 0 },
  rowTitle: { ...type.body, flex: 1, fontSize: 14, fontWeight: "600" },
  rowTitleDone: { color: colors.inkSecondary },
  rowState: { ...type.meta, fontSize: 12 },
  rowStateDone: { color: colors.successInk },
});
