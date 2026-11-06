import { useState } from "react";
import { Linking, StyleSheet, Text, View } from "react-native";
import { router } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { useAgency } from "@/context/AgencyContext";
import { SupportModal } from "@/components/support";
import { LinkButton, PrimaryButton, SecondaryButton } from "@/components/ui";
import { colors, radius, shadow, spacing, type } from "@/lib/theme";

const STEPS = [
  { n: 1, text: "Entra en selai.es desde tu ordenador." },
  { n: 2, text: "Completa el registro de tu agencia." },
  { n: 3, text: "Vuelve aquí y toca «Ya lo he completado»." },
];

export default function OnboardingRequiredScreen() {
  const { bootstrap } = useAgency();
  const [checking, setChecking] = useState(false);
  const [reporting, setReporting] = useState(false);

  async function retry() {
    setChecking(true);
    await bootstrap();
    setChecking(false);
    router.replace("/(tabs)/inbox");
  }

  return (
    <View style={styles.container}>
      <View style={styles.iconCircle}>
        <Ionicons name="business-outline" size={30} color={colors.primary} />
      </View>
      <Text style={styles.title}>Te falta un paso</Text>
      <Text style={styles.body}>
        Tu cuenta todavía no está vinculada a ninguna agencia. El alta se hace
        desde el navegador; solo te llevará unos minutos.
      </Text>

      <View style={styles.stepsCard}>
        {STEPS.map((s, i) => (
          <View
            key={s.n}
            style={[styles.stepRow, i === STEPS.length - 1 ? styles.stepRowLast : null]}
          >
            <View style={styles.stepNumber}>
              <Text style={styles.stepNumberText}>{s.n}</Text>
            </View>
            <Text style={styles.stepText}>{s.text}</Text>
          </View>
        ))}
      </View>

      <View style={styles.actions}>
        <PrimaryButton
          label="Abrir selai.es"
          icon="open-outline"
          onPress={() => void Linking.openURL("https://selai.es")}
        />
        <SecondaryButton
          label={checking ? "Comprobando..." : "Ya lo he completado"}
          onPress={() => void retry()}
          disabled={checking}
        />
        {/* Quien llega aquí ya ha iniciado sesión pero no tiene agencia: es
            justo el caso para el que el endpoint de soporte no exige agencia.
            Sin este botón, el único usuario al que le puede fallar el alta se
            quedaba sin forma de contárnoslo desde la app. */}
        <LinkButton
          label="Reportar un problema"
          icon="chatbubble-ellipses-outline"
          onPress={() => setReporting(true)}
        />
      </View>

      <SupportModal
        visible={reporting}
        onClose={() => setReporting(false)}
        screen="/onboarding"
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    padding: spacing(6),
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.bg,
    gap: spacing(2),
  },
  iconCircle: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: colors.surface,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: spacing(2),
  },
  title: { ...type.title },
  body: {
    ...type.bodySecondary,
    textAlign: "center",
    lineHeight: 21,
    marginBottom: spacing(3),
  },
  stepsCard: {
    alignSelf: "stretch",
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    overflow: "hidden",
    marginBottom: spacing(4),
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
  stepNumber: {
    width: 26,
    height: 26,
    borderRadius: 13,
    backgroundColor: colors.primarySoft,
    alignItems: "center",
    justifyContent: "center",
  },
  stepNumberText: { color: colors.primary, fontWeight: "700", fontSize: 13 },
  stepText: { ...type.body, flex: 1, lineHeight: 20 },
  actions: { alignSelf: "stretch", gap: spacing(2) },
});
