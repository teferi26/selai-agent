// Voice-note recorder for post-visit notes. Uploads to:
//   POST /api/v1/agencies/{agency}/visits/{id}/voice-note
import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import {
  RecordingPresets,
  requestRecordingPermissionsAsync,
  setAudioModeAsync,
  useAudioRecorder,
  useAudioRecorderState,
} from "expo-audio";
import { File } from "expo-file-system";
import { useLocalSearchParams } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { isTimeout, uploadVoiceNote } from "@/lib/api";
import { useAgency } from "@/context/AgencyContext";
import { successHaptic, tapHaptic } from "@/lib/haptics";
import { colors, radius, spacing, type } from "@/lib/theme";

type Feedback = { kind: "info" | "success" | "error"; text: string } | null;
type Operation = "starting" | "stopping" | "uploading" | null;

function deleteLocalRecording(uri: string | null): void {
  if (!uri) return;
  try {
    const file = new File(uri);
    if (file.exists) file.delete();
  } catch {
    // Es un fichero temporal de la caché. Si el sistema ya lo retiró, no
    // hay nada más que limpiar ni motivo para bloquear al usuario.
  }
}

async function leaveRecordingMode(): Promise<void> {
  await setAudioModeAsync({
    allowsRecording: false,
    playsInSilentMode: true,
    interruptionMode: "mixWithOthers",
  }).catch(() => {});
}

function formatDuration(durationMillis: number): string {
  const seconds = Math.max(0, Math.floor(durationMillis / 1000));
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

export default function VisitVoiceNote() {
  // `lead`, `property` y `when` los pasa la agenda para que esta pantalla diga
  // de qué visita se trata. Son opcionales a propósito: si se llega por enlace
  // directo no hay contexto, pero grabar sigue funcionando igual.
  const { id, lead, property, when } = useLocalSearchParams<{
    id: string;
    lead?: string;
    property?: string;
    when?: string;
  }>();
  const { me } = useAgency();
  const audioRecorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const recorderState = useAudioRecorderState(audioRecorder, 250);
  const [recording, setRecording] = useState(false);
  const [recordingUri, setRecordingUri] = useState<string | null>(null);
  const [operation, setOperation] = useState<Operation>(null);
  const [feedback, setFeedback] = useState<Feedback>(null);

  // Los refs cierran la diminuta ventana entre dos pulsaciones y permiten
  // liberar el micrófono/fichero aunque la pantalla se desmonte.
  const operationRef = useRef<Operation>(null);
  const recordingRef = useRef(false);
  const recordingUriRef = useRef<string | null>(null);
  const mountedRef = useRef(true);

  function showFeedback(next: Feedback): void {
    if (mountedRef.current) setFeedback(next);
  }

  function beginOperation(next: Exclude<Operation, null>): boolean {
    if (operationRef.current) return false;
    operationRef.current = next;
    setOperation(next);
    return true;
  }

  function endOperation(): void {
    operationRef.current = null;
    if (mountedRef.current) setOperation(null);
  }

  function keepRecordingUri(uri: string | null): void {
    recordingUriRef.current = uri;
    if (mountedRef.current) setRecordingUri(uri);
  }

  useEffect(
    () => () => {
      mountedRef.current = false;
      const wasRecording = recordingRef.current;
      recordingRef.current = false;
      if (wasRecording) {
        void audioRecorder
          .stop()
          .catch(() => {})
          .finally(() => deleteLocalRecording(audioRecorder.uri));
      }

      // Una nota abandonada no debe acumular audio privado en la caché.
      // Durante una subida dejamos que termine: el propio flujo la limpia si
      // sale bien y la caché del sistema cubre el caso excepcional de cierre.
      if (operationRef.current !== "uploading") {
        deleteLocalRecording(recordingUriRef.current);
      }
      void leaveRecordingMode();
    },
    [audioRecorder]
  );

  useEffect(() => {
    if (!recordingRef.current || !recorderState.mediaServicesDidReset) return;
    recordingRef.current = false;
    setRecording(false);
    deleteLocalRecording(audioRecorder.uri);
    void leaveRecordingMode();
    showFeedback({
      kind: "error",
      text: "El sistema de audio ha interrumpido la grabación. No se ha subido nada; vuelve a intentarlo.",
    });
  }, [recorderState.mediaServicesDidReset]);

  async function start() {
    if (recordingRef.current || recordingUri || !beginOperation("starting")) return;
    tapHaptic();
    try {
      const perm = await requestRecordingPermissionsAsync();
      if (!perm.granted) {
        showFeedback({
          kind: "error",
          text: "Necesitamos permiso de micrófono. Actívalo en los ajustes del teléfono.",
        });
        return;
      }
      await setAudioModeAsync({
        allowsRecording: true,
        playsInSilentMode: true,
        interruptionMode: "doNotMix",
      });
      await audioRecorder.prepareToRecordAsync();
      audioRecorder.record();
      if (!mountedRef.current) {
        await audioRecorder.stop().catch(() => {});
        deleteLocalRecording(audioRecorder.uri);
        await leaveRecordingMode();
        return;
      }
      recordingRef.current = true;
      setRecording(true);
      showFeedback({ kind: "info", text: "Grabando..." });
    } catch {
      await audioRecorder.stop().catch(() => {});
      deleteLocalRecording(audioRecorder.uri);
      await leaveRecordingMode();
      showFeedback({
        kind: "error",
        text: "No hemos podido iniciar la grabación. Comprueba el micrófono e inténtalo de nuevo.",
      });
    } finally {
      endOperation();
    }
  }

  async function stop() {
    if ((!recordingRef.current && !recording) || !beginOperation("stopping")) return;
    tapHaptic();
    try {
      await audioRecorder.stop();
      const uri = audioRecorder.uri;
      if (!uri) throw new Error("no_uri");
      if (!mountedRef.current) {
        deleteLocalRecording(uri);
        return;
      }
      keepRecordingUri(uri);
      showFeedback({
        kind: "info",
        text: "Grabación lista. Súbela cuando quieras; si falla, podrás reintentarlo sin volver a grabar.",
      });
    } catch {
      await audioRecorder.stop().catch(() => {});
      deleteLocalRecording(audioRecorder.uri);
      showFeedback({
        kind: "error",
        text: "No hemos podido guardar la grabación. Comprueba el micrófono e inténtalo de nuevo.",
      });
    } finally {
      recordingRef.current = false;
      if (mountedRef.current) setRecording(false);
      await leaveRecordingMode();
      endOperation();
    }
  }

  async function upload() {
    const uri = recordingUriRef.current ?? recordingUri;
    if (!uri || !beginOperation("uploading")) return;

    const agencyId = me?.agency_id;
    if (!agencyId || !id) {
      showFeedback({ kind: "error", text: "Tu sesión no está disponible. Vuelve a entrar." });
      endOperation();
      return;
    }

    showFeedback({ kind: "info", text: "Subiendo la nota..." });
    try {
      const form = new FormData();
      // React Native usa este objeto para adjuntar un fichero local al FormData.
      form.append("file", { uri, name: "note.m4a", type: "audio/m4a" } as any);

      await uploadVoiceNote(agencyId, id, form);
      deleteLocalRecording(uri);
      keepRecordingUri(null);
      successHaptic();
      showFeedback({ kind: "success", text: "Nota subida correctamente." });
    } catch (error) {
      if (!mountedRef.current) {
        deleteLocalRecording(uri);
        recordingUriRef.current = null;
      } else {
        showFeedback({
          kind: "error",
          text: isTimeout(error)
            ? "La subida está tardando demasiado y no sabemos si terminó. Conservamos la grabación: comprueba la visita antes de volver a subirla."
            : "No hemos podido subirla. La grabación sigue guardada en esta pantalla para que puedas reintentarlo.",
        });
      }
    } finally {
      endOperation();
    }
  }

  function discard() {
    if (operationRef.current) return;
    tapHaptic();
    deleteLocalRecording(recordingUriRef.current);
    keepRecordingUri(null);
    showFeedback(null);
  }

  const context = [lead, property, when].filter(
    (v): v is string => typeof v === "string" && v.trim().length > 0
  );
  const uploadLabel =
    operation === "uploading"
      ? "Subiendo..."
      : feedback?.kind === "error"
        ? "Reintentar subida"
        : "Subir nota";

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Nota de voz post-visita</Text>
      {context.length > 0 ? (
        <View style={styles.contextCard}>
          {context.map((line, i) => (
            <Text key={i} style={i === 0 ? styles.contextMain : styles.contextMeta}>
              {line}
            </Text>
          ))}
        </View>
      ) : null}
      <Text style={styles.help}>
        Graba lo más importante de la visita y la IA lo añadirá a la ficha del lead.
      </Text>

      {!recording && !recordingUri ? (
        <Pressable
          style={({ pressed }) => [styles.recordBtn, pressed ? { opacity: 0.85 } : null]}
          onPress={() => void start()}
          disabled={operation !== null}
          accessibilityRole="button"
          accessibilityLabel="Grabar nota de voz"
          accessibilityState={{ disabled: operation !== null }}
        >
          {operation === "starting" ? (
            <ActivityIndicator color={colors.white} />
          ) : (
            <Ionicons name="mic-outline" size={36} color={colors.white} />
          )}
        </Pressable>
      ) : recording ? (
        <Pressable
          style={({ pressed }) => [
            styles.stopBtn,
            operation !== null ? styles.disabled : null,
            pressed ? { opacity: 0.85 } : null,
          ]}
          onPress={() => void stop()}
          disabled={operation !== null}
          accessibilityRole="button"
          accessibilityLabel="Detener grabación"
          accessibilityState={{ disabled: operation !== null }}
        >
          {operation === "stopping" ? (
            <ActivityIndicator color={colors.white} />
          ) : (
            <Ionicons name="stop" size={34} color={colors.white} />
          )}
        </Pressable>
      ) : (
        <View style={styles.readyActions}>
          <Pressable
            style={({ pressed }) => [
              styles.uploadBtn,
              operation !== null ? styles.disabled : null,
              pressed ? { opacity: 0.85 } : null,
            ]}
            onPress={() => void upload()}
            disabled={operation !== null}
            accessibilityRole="button"
            accessibilityLabel={
              operation === "uploading"
                ? "Subiendo nota de voz"
                : feedback?.kind === "error"
                  ? "Reintentar subida de la nota de voz"
                  : "Subir nota de voz"
            }
            accessibilityState={{ disabled: operation !== null, busy: operation === "uploading" }}
          >
            {operation === "uploading" ? (
              <ActivityIndicator color={colors.white} />
            ) : (
              <Ionicons name="cloud-upload-outline" size={22} color={colors.white} />
            )}
            <Text style={styles.uploadBtnText}>{uploadLabel}</Text>
          </Pressable>
          <Pressable
            style={({ pressed }) => [styles.discardBtn, pressed ? { opacity: 0.85 } : null]}
            onPress={discard}
            disabled={operation !== null}
            accessibilityRole="button"
            accessibilityLabel="Descartar grabación"
            accessibilityState={{ disabled: operation !== null }}
          >
            <Ionicons name="trash-outline" size={18} color={colors.dangerInk} />
            <Text style={styles.discardBtnText}>Descartar</Text>
          </Pressable>
        </View>
      )}
      <Text style={styles.btnCaption}>
        {recording
          ? `Grabando · ${formatDuration(recorderState.durationMillis)} · Toca para detener`
          : recordingUri
            ? "La grabación se conserva hasta subirla o descartarla"
            : "Toca para grabar"}
      </Text>

      {feedback ? (
        <View
          style={styles.feedbackRow}
          accessibilityLiveRegion="polite"
          accessibilityRole={feedback.kind === "error" ? "alert" : undefined}
        >
          {feedback.kind === "success" ? (
            <Ionicons name="checkmark-circle-outline" size={17} color={colors.successInk} />
          ) : feedback.kind === "error" ? (
            <Ionicons name="alert-circle-outline" size={17} color={colors.dangerInk} />
          ) : (
            <Ionicons name="radio-outline" size={17} color={colors.primary} />
          )}
          <Text
            style={[
              styles.feedbackText,
              feedback.kind === "success" ? { color: colors.successInk } : null,
              feedback.kind === "error" ? { color: colors.dangerInk } : null,
            ]}
          >
            {feedback.text}
          </Text>
        </View>
      ) : null}
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
  },
  title: { ...type.title, fontSize: 22, marginBottom: spacing(1) },
  contextCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    paddingHorizontal: spacing(4),
    paddingVertical: spacing(3),
    marginBottom: spacing(3),
    alignSelf: "stretch",
    gap: 2,
  },
  contextMain: { ...type.body, fontWeight: "600", textAlign: "center" },
  contextMeta: { ...type.meta, fontWeight: "400", textAlign: "center" },
  help: {
    ...type.bodySecondary,
    textAlign: "center",
    lineHeight: 21,
    marginBottom: spacing(8),
  },
  recordBtn: {
    width: 88,
    height: 88,
    borderRadius: 44,
    backgroundColor: colors.danger,
    alignItems: "center",
    justifyContent: "center",
  },
  stopBtn: {
    width: 88,
    height: 88,
    borderRadius: 44,
    backgroundColor: colors.ink,
    alignItems: "center",
    justifyContent: "center",
  },
  readyActions: { alignItems: "center", gap: spacing(3), alignSelf: "stretch" },
  uploadBtn: {
    minHeight: 52,
    minWidth: 180,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing(2),
    borderRadius: radius.button,
    backgroundColor: colors.primary,
    paddingHorizontal: spacing(5),
  },
  uploadBtnText: { fontSize: 15, fontWeight: "700", color: colors.white },
  discardBtn: {
    minHeight: 44,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing(2),
    paddingHorizontal: spacing(4),
  },
  discardBtnText: { fontSize: 14, fontWeight: "600", color: colors.dangerInk },
  disabled: { opacity: 0.5 },
  btnCaption: { ...type.meta, marginTop: spacing(3), textAlign: "center" },
  feedbackRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(2),
    marginTop: spacing(4),
    paddingHorizontal: spacing(4),
  },
  feedbackText: { ...type.bodySecondary, fontSize: 14, textAlign: "center", flexShrink: 1 },
});
