// Resumen de la puesta en marcha. Ya no manda al ordenador: cada fila es
// tocable y salta a su paso del asistente nativo (app/setup/[step].tsx), que
// es donde se ejecutan de verdad las conexiones.
import { useCallback, useRef, useState } from "react";
import {
  Linking,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { router, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { useAgency } from "@/context/AgencyContext";
import { ErrorView, PrimaryButton, SecondaryButton, Skeleton } from "@/components/ui";
import {
  SETUP_TOTAL,
  SETUP_WEB_URL,
  SetupStep,
  firstPendingStepNumber,
  useSetupStatus,
} from "@/lib/setup";
import { tapHaptic } from "@/lib/haptics";
import { colors, radius, shadow, spacing, type } from "@/lib/theme";

function StepRow({
  step,
  number,
  last,
}: {
  step: SetupStep;
  number: number;
  last?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`Paso ${number}: ${step.title}. ${step.done ? "Hecho" : "Pendiente"}`}
      onPress={() => {
        tapHaptic();
        router.push(`/setup/${number}`);
      }}
      style={({ pressed }) => [
        styles.stepRow,
        last ? styles.stepRowLast : null,
        pressed ? { opacity: 0.9 } : null,
      ]}
    >
      {step.done ? (
        <Ionicons name="checkmark-circle" size={24} color={colors.success} />
      ) : (
        <Ionicons name="ellipse-outline" size={24} color={colors.hairline} />
      )}
      <View style={styles.stepText}>
        <Text style={[styles.stepTitle, step.done ? styles.stepTitleDone : null]}>
          {step.title}
        </Text>
        <Text style={styles.stepDescription}>{step.description}</Text>
      </View>
      <Text style={[styles.stepState, step.done ? styles.stepStateDone : null]}>
        {step.done ? "Hecho" : "Pendiente"}
      </Text>
      <Ionicons name="chevron-forward" size={16} color={colors.inkSecondary} />
    </Pressable>
  );
}

export default function SetupChecklistScreen() {
  const { me } = useAgency();
  const setup = useSetupStatus(me?.agency_id);
  const [refreshing, setRefreshing] = useState(false);

  // `refresh` se recrea en cada render del hook: guardarlo en una ref evita
  // que el efecto de foco se dispare en bucle.
  const refreshRef = useRef(setup.refresh);
  refreshRef.current = setup.refresh;
  useFocusEffect(
    useCallback(() => {
      void refreshRef.current();
    }, [])
  );

  if (setup.error) {
    return (
      <ErrorView
        title="No hemos podido cargar tu puesta en marcha"
        onRetry={() => void setup.refresh()}
        exitLabel="Abrir el panel web"
        onExit={() => void Linking.openURL(SETUP_WEB_URL)}
      />
    );
  }

  if (setup.loading || setup.steps === null) {
    return (
      <View style={styles.screen}>
        <View style={styles.content}>
          <View style={[styles.progressCard, { gap: spacing(3) }]}>
            <Skeleton width={"60%"} height={22} />
            <Skeleton height={8} round />
            <Skeleton width={"85%"} height={12} />
          </View>
          <View style={styles.stepsCard}>
            {Array.from({ length: SETUP_TOTAL }, (_, i) => (
              <View
                key={i}
                style={[styles.stepRow, i === SETUP_TOTAL - 1 ? styles.stepRowLast : null]}
              >
                <Skeleton width={24} height={24} round />
                <View style={[styles.stepText, { gap: spacing(2) }]}>
                  <Skeleton width={"55%"} height={13} />
                  <Skeleton width={"80%"} height={11} />
                </View>
              </View>
            ))}
          </View>
        </View>
      </View>
    );
  }

  const { steps, doneCount, total, complete } = setup;
  const nextStep = firstPendingStepNumber(steps);

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={async () => {
            setRefreshing(true);
            await setup.refresh();
            setRefreshing(false);
          }}
          tintColor={colors.primary}
          colors={[colors.primary]}
        />
      }
    >
      <View style={styles.progressCard}>
        <View style={styles.progressHeader}>
          {complete ? (
            <View style={styles.completeBadge}>
              <Ionicons name="trophy-outline" size={16} color={colors.goldInk} />
              <Text style={styles.completeBadgeText}>Todo listo</Text>
            </View>
          ) : null}
          <Text style={styles.progressTitle}>
            {complete
              ? "Tu agencia está al 100%"
              : `${doneCount} de ${total} pasos completados`}
          </Text>
        </View>
        <View style={styles.progressTrack}>
          <View
            style={[
              styles.progressFill,
              { width: `${(doneCount / total) * 100}%` },
              complete ? { backgroundColor: colors.gold } : null,
            ]}
          />
        </View>
        <Text style={styles.progressHint}>
          Puedes hacerlo todo desde aquí, sin ordenador. Toca cualquier paso para ir
          directo a él.
        </Text>
      </View>

      <View style={styles.stepsCard}>
        {steps.map((s, i) => (
          <StepRow key={s.key} step={s} number={i + 1} last={i === steps.length - 1} />
        ))}
      </View>

      <PrimaryButton
        label={complete ? "Repasar la puesta en marcha" : "Continuar la puesta en marcha"}
        icon="rocket-outline"
        onPress={() => router.push(`/setup/${complete ? 1 : nextStep}`)}
      />

      <SecondaryButton
        label="Abrir el panel web"
        icon="open-outline"
        onPress={() => void Linking.openURL(SETUP_WEB_URL)}
      />
      <Text style={styles.webHint}>
        En el ordenador tienes lo mismo, más la clonación de voz y la exportación de
        leads en archivos grandes.
      </Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: spacing(4), paddingBottom: spacing(10), gap: spacing(4) },
  progressCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    padding: spacing(5),
    ...shadow.card,
  },
  progressHeader: { gap: spacing(2), marginBottom: spacing(3) },
  completeBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(1),
    alignSelf: "flex-start",
    backgroundColor: colors.goldSoft,
    paddingHorizontal: spacing(2),
    paddingVertical: 3,
    borderRadius: radius.pill,
  },
  completeBadgeText: { fontSize: 12, fontWeight: "600", color: colors.goldInk },
  progressTitle: { ...type.title, fontSize: 22 },
  progressTrack: {
    height: 8,
    borderRadius: 4,
    backgroundColor: colors.neutralSoft,
    overflow: "hidden",
  },
  progressFill: { height: 8, borderRadius: 4, backgroundColor: colors.primary },
  progressHint: {
    ...type.meta,
    fontWeight: "400",
    marginTop: spacing(3),
    lineHeight: 18,
  },
  stepsCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    overflow: "hidden",
    ...shadow.card,
  },
  stepRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(3),
    paddingHorizontal: spacing(4),
    paddingVertical: spacing(3),
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.hairline,
  },
  stepRowLast: { borderBottomWidth: 0 },
  stepText: { flex: 1 },
  stepTitle: { ...type.body, fontWeight: "600", fontSize: 14 },
  stepTitleDone: { color: colors.inkSecondary },
  stepDescription: { ...type.meta, fontWeight: "400", fontSize: 12, marginTop: 2 },
  stepState: { ...type.meta, fontSize: 12 },
  stepStateDone: { color: colors.successInk },
  webHint: {
    ...type.meta,
    fontWeight: "400",
    fontSize: 12,
    textAlign: "center",
    lineHeight: 17,
  },
});
