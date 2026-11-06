// ---------------------------------------------------------------------------
// Asistente de puesta en marcha, a pantalla completa: 9 pasos que EJECUTAN la
// conexión (nada de mandar al usuario al ordenador).
//
// Ruta única con el número de paso como parámetro (`/setup/3`), y "fin" para
// la pantalla de cierre. Navegamos con `replace`, así que la pila no crece y
// cualquier fila del resumen puede saltar directamente a su paso.
// ---------------------------------------------------------------------------
import { ComponentType, useCallback, useEffect, useState } from "react";
import {
  BackHandler,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { router, useLocalSearchParams } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useAgency } from "@/context/AgencyContext";
import { useSetupWizard } from "@/context/SetupWizardContext";
import { ErrorView } from "@/components/ui";
import { StepSkeleton } from "@/components/setup/kit";
import StepAgency from "@/components/setup/StepAgency";
import StepVoice from "@/components/setup/StepVoice";
import StepPhone from "@/components/setup/StepPhone";
import StepWhatsApp from "@/components/setup/StepWhatsApp";
import StepPortals from "@/components/setup/StepPortals";
import StepCatalog from "@/components/setup/StepCatalog";
import StepWebchat from "@/components/setup/StepWebchat";
import StepCrm from "@/components/setup/StepCrm";
import StepTest from "@/components/setup/StepTest";
import StepDone from "@/components/setup/StepDone";
import { SETUP_STEP_KEYS, SETUP_STEP_TITLES, SETUP_TOTAL } from "@/lib/setup";
import { tapHaptic } from "@/lib/haptics";
import { colors, radius, spacing, type } from "@/lib/theme";

const INBOX = "/(tabs)/inbox";

/** Cada paso dice si necesita el scroll del contenedor. La prueba final no:
 * gestiona su propia lista de mensajes con el campo de texto fijo abajo. */
const STEP_VIEWS: { Component: ComponentType; scroll: boolean }[] = [
  { Component: StepAgency, scroll: true },
  { Component: StepVoice, scroll: true },
  { Component: StepPhone, scroll: true },
  { Component: StepWhatsApp, scroll: true },
  { Component: StepPortals, scroll: true },
  { Component: StepCatalog, scroll: true },
  { Component: StepWebchat, scroll: true },
  { Component: StepCrm, scroll: true },
  { Component: StepTest, scroll: false },
];

function goToStep(n: number) {
  router.replace(`/setup/${n}`);
}

/** Salida etiquetada ("Volver a la bandeja"): va donde dice que va. */
function exitWizard() {
  router.replace(INBOX);
}

/** La X de la cabecera: cerrar devuelve a la pantalla desde la que se abrió
 * el asistente (el resumen de puesta en marcha, o la bandeja), que es lo que
 * espera quien la pulsa. Sin nada detrás, a la bandeja. */
function closeWizard() {
  if (router.canGoBack()) router.back();
  else router.replace(INBOX);
}

export default function SetupStepScreen() {
  const { step } = useLocalSearchParams<{ step: string }>();
  const { me, status, bootstrap } = useAgency();
  const wizard = useSetupWizard();
  const [nextBusy, setNextBusy] = useState(false);
  // Saltar puede abrir un aviso ("tienes datos sin guardar"): mientras está en
  // pantalla no debe poder pulsarse otra vez ni encadenarse con «Siguiente».
  const [skipBusy, setSkipBusy] = useState(false);
  const [leaveBusy, setLeaveBusy] = useState(false);
  const busy = nextBusy || skipBusy || leaveBusy;

  const raw = String(step ?? "1");
  const isFinal = raw === "fin";
  const parsed = Number.parseInt(raw, 10);
  const current = Number.isFinite(parsed)
    ? Math.min(Math.max(parsed, 1), SETUP_TOTAL)
    : 1;

  const goNext = useCallback(() => {
    if (current >= SETUP_TOTAL) router.replace("/setup/fin");
    else goToStep(current + 1);
  }, [current]);

  const handleClose = useCallback(async () => {
    if (nextBusy || skipBusy || leaveBusy) return;
    setLeaveBusy(true);
    try {
      const ok = await wizard.runSkip("close");
      if (ok) closeWizard();
    } finally {
      setLeaveBusy(false);
    }
  }, [leaveBusy, nextBusy, skipBusy, wizard]);

  const handleBack = useCallback(async () => {
    if (current === 1 || nextBusy || skipBusy || leaveBusy) return;
    setLeaveBusy(true);
    try {
      const ok = await wizard.runSkip("back");
      if (ok) goToStep(current - 1);
    } finally {
      setLeaveBusy(false);
    }
  }, [current, leaveBusy, nextBusy, skipBusy, wizard]);

  useEffect(() => {
    const subscription = BackHandler.addEventListener("hardwareBackPress", () => {
      if (isFinal) return false;
      void handleClose();
      return true;
    });
    return () => subscription.remove();
  }, [handleClose, isFinal]);

  const handleNext = useCallback(async () => {
    if (nextBusy || skipBusy || leaveBusy) return;
    setNextBusy(true);
    try {
      const ok = await wizard.runNext();
      if (ok) goNext();
    } finally {
      setNextBusy(false);
    }
  }, [nextBusy, skipBusy, leaveBusy, wizard, goNext]);

  // Saltar ya no es "avanzar sin más": el paso puede tener datos escritos que
  // nadie ha guardado todavía y es él quien decide qué preguntar (ver
  // `useStepSkip`). Si dice que no, nos quedamos donde estamos.
  const handleSkip = useCallback(async () => {
    if (nextBusy || skipBusy || leaveBusy) return;
    setSkipBusy(true);
    try {
      const ok = await wizard.runSkip("skip");
      if (ok) goNext();
    } finally {
      setSkipBusy(false);
    }
  }, [nextBusy, skipBusy, leaveBusy, wizard, goNext]);

  // Sin agencia cargada no hay nada que configurar: ofrecemos reintentar y
  // una salida, nunca una pantalla muerta.
  if (!me) {
    return (
      <SafeAreaView style={styles.screen} edges={["top", "bottom"]}>
        <ErrorView
          title="No hemos podido cargar tu agencia"
          message={
            status === "unauthenticated"
              ? "Tu sesión ha caducado. Vuelve a entrar y retomamos donde lo dejaste."
              : "Comprueba tu conexión e inténtalo de nuevo."
          }
          onRetry={() => void bootstrap()}
          exitLabel="Volver a la bandeja"
          onExit={exitWizard}
        />
      </SafeAreaView>
    );
  }

  if (isFinal) {
    return (
      <SafeAreaView style={styles.screen} edges={["top", "bottom"]}>
        <StepDone onFinish={exitWizard} onReview={(n) => goToStep(n)} />
      </SafeAreaView>
    );
  }

  const stepKey = SETUP_STEP_KEYS[current - 1];
  const { Component, scroll } = STEP_VIEWS[current - 1];
  const isLast = current === SETUP_TOTAL;

  // El reintento reintenta las DOS cosas que pueden estar rotas: la carga y
  // la sesión. Si el token ya no vale, `bootstrap()` deja el estado en
  // "unauthenticated" y esta misma pantalla pasa a decirlo con todas las
  // letras, en vez de repetir "comprueba tu conexión" para siempre.
  const retryAll = () => {
    void wizard.reload();
    void bootstrap();
  };

  const body = wizard.error ? (
    <ErrorView
      title="No hemos podido cargar tu configuración"
      message="Sin ella no podemos enseñarte qué falta. Comprueba tu conexión."
      onRetry={retryAll}
      exitLabel="Volver a la bandeja"
      onExit={exitWizard}
    />
  ) : wizard.loading && !wizard.settings ? (
    <StepSkeleton rows={3} />
  ) : (
    <Component />
  );

  // El estado de error ocupa toda la altura (ErrorView centra con flex), así
  // que no puede vivir dentro del ScrollView.
  const useScroll = scroll && !wizard.error;

  return (
    <View style={styles.screen}>
      <SafeAreaView edges={["top"]} style={styles.headerSafe}>
        <View style={styles.headerRow}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Salir de la puesta en marcha"
            hitSlop={10}
            onPress={() => {
              tapHaptic();
              void handleClose();
            }}
            style={({ pressed }) => [styles.closeBtn, pressed ? styles.pressed : null]}
          >
            <Ionicons name="close" size={22} color={colors.ink} />
          </Pressable>
          <Text style={styles.stepCounter}>
            Paso {current} de {SETUP_TOTAL}
          </Text>
          <View style={styles.closeBtn} />
        </View>

        <View
          style={styles.progressTrack}
          accessibilityRole="progressbar"
          accessibilityValue={{ min: 0, max: SETUP_TOTAL, now: current }}
        >
          <View style={[styles.progressFill, { width: `${(current / SETUP_TOTAL) * 100}%` }]} />
        </View>

        <Text style={styles.title}>{SETUP_STEP_TITLES[stepKey]}</Text>
      </SafeAreaView>

      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        {useScroll ? (
          <ScrollView
            style={styles.flex}
            contentContainerStyle={styles.content}
            keyboardShouldPersistTaps="handled"
            keyboardDismissMode="on-drag"
          >
            {body}
          </ScrollView>
        ) : (
          <View style={[styles.flex, wizard.error ? null : styles.content]}>{body}</View>
        )}

        <SafeAreaView edges={["bottom"]} style={styles.footer}>
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ disabled: current === 1 || busy }}
            disabled={current === 1 || busy}
            onPress={() => {
              tapHaptic();
              void handleBack();
            }}
            style={({ pressed }) => [
              styles.backBtn,
              current === 1 || busy ? styles.disabled : null,
              pressed && current > 1 && !busy ? styles.pressed : null,
            ]}
          >
            <Ionicons name="chevron-back" size={18} color={colors.ink} />
            <Text style={styles.backBtnText}>Anterior</Text>
          </Pressable>

          <Pressable
            accessibilityRole="button"
            accessibilityState={{ disabled: busy }}
            disabled={busy}
            onPress={() => {
              tapHaptic();
              void handleSkip();
            }}
            style={({ pressed }) => [
              styles.skipBtn,
              busy ? styles.disabled : null,
              pressed && !busy ? styles.pressed : null,
            ]}
          >
            <Text style={styles.skipBtnText}>Saltar por ahora</Text>
          </Pressable>

          <Pressable
            accessibilityRole="button"
            disabled={busy}
            onPress={() => void handleNext()}
            style={({ pressed }) => [
              styles.nextBtn,
              busy ? styles.disabled : null,
              pressed && !busy ? styles.pressed : null,
            ]}
          >
            <Text style={styles.nextBtnText}>
              {nextBusy ? "Guardando..." : isLast ? "Terminar" : "Siguiente"}
            </Text>
            {!nextBusy ? (
              <Ionicons
                name={isLast ? "checkmark" : "chevron-forward"}
                size={18}
                color={colors.white}
              />
            ) : null}
          </Pressable>
        </SafeAreaView>
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  flex: { flex: 1 },
  headerSafe: {
    backgroundColor: colors.surface,
    paddingHorizontal: spacing(4),
    paddingBottom: spacing(4),
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.hairline,
    gap: spacing(3),
  },
  headerRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingTop: spacing(2),
  },
  closeBtn: { width: 32, height: 32, alignItems: "center", justifyContent: "center" },
  stepCounter: { ...type.meta, fontSize: 13 },
  progressTrack: {
    height: 6,
    borderRadius: 3,
    backgroundColor: colors.neutralSoft,
    overflow: "hidden",
  },
  progressFill: { height: 6, borderRadius: 3, backgroundColor: colors.primary },
  title: { ...type.title, fontSize: 22 },

  content: { padding: spacing(4), paddingBottom: spacing(6), gap: spacing(4) },

  footer: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(2),
    paddingHorizontal: spacing(4),
    paddingTop: spacing(3),
    paddingBottom: spacing(3),
    backgroundColor: colors.surface,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.hairline,
  },
  backBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(1),
    height: 46,
    paddingHorizontal: spacing(3),
    borderRadius: radius.button,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
  },
  backBtnText: { fontSize: 14, fontWeight: "600", color: colors.ink },
  skipBtn: { flex: 1, alignItems: "center", justifyContent: "center", height: 46 },
  skipBtnText: { ...type.meta, fontSize: 13, textDecorationLine: "underline" },
  nextBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(1),
    height: 46,
    paddingHorizontal: spacing(4),
    borderRadius: radius.button,
    backgroundColor: colors.primary,
  },
  nextBtnText: { fontSize: 15, fontWeight: "700", color: colors.white },
  disabled: { opacity: 0.4 },
  pressed: { opacity: 0.85 },
});
