// ---------------------------------------------------------------------------
// "Reportar un problema": modal reutilizable que manda POST /api/v1/support.
//
// Vive en un Modal y no en una ruta a propósito: se abre desde Ajustes, pero
// también desde el ErrorBoundary y desde las pantallas de error, donde el
// router puede ser justo lo que ha fallado y navegar no es una opción.
// ---------------------------------------------------------------------------
import { useCallback, useEffect, useState } from "react";
import {
  KeyboardAvoidingView,
  Linking,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import * as Clipboard from "expo-clipboard";
import { Ionicons } from "@expo/vector-icons";
import { PrimaryButton, SecondaryButton } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { successHaptic, tapHaptic } from "@/lib/haptics";
import {
  buildSupportRequest,
  MESSAGE_MAX,
  MESSAGE_MIN,
  sendSupportRequest,
  SUPPORT_EMAIL,
  supportMailtoUrl,
  supportPlainText,
  type SupportRequest,
} from "@/lib/support";
import { colors, radius, spacing, type } from "@/lib/theme";

type Phase = "form" | "sending" | "sent" | "failed";

export function SupportModal({
  visible,
  onClose,
  screen,
  errorRef,
}: {
  visible: boolean;
  onClose: () => void;
  /** Ruta o pantalla donde estaba el usuario ("/settings", "error-boundary"). */
  screen: string;
  /** Id de Sentry o referencia técnica del error, si venimos de uno. */
  errorRef?: string | null;
}) {
  const [message, setMessage] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const [phase, setPhase] = useState<Phase>("form");
  // El servidor limita a unos pocos informes por usuario cada diez minutos
  // (ver backend/app/api/support.py). Cuando responde 429, ofrecer
  // "Reintentar" sería mentirle: lo va a rechazar igual durante ese rato.
  const [rateLimited, setRateLimited] = useState(false);
  const [mailtoFailed, setMailtoFailed] = useState(false);
  const [copied, setCopied] = useState(false);

  // Cada apertura empieza en limpio: un informe anterior ya enviado no debe
  // reaparecer como texto a medio escribir.
  useEffect(() => {
    if (visible) {
      setPhase("form");
      setRateLimited(false);
      setMailtoFailed(false);
      setCopied(false);
    }
  }, [visible]);

  const tooShort = message.trim().length < MESSAGE_MIN;

  const payload = useCallback(
    (): SupportRequest =>
      buildSupportRequest({ message, screen, errorRef, contactEmail }),
    [message, screen, errorRef, contactEmail]
  );

  // Los botones del kit (PrimaryButton, SecondaryButton) ya vibran al pulsar,
  // así que estos manejadores no repiten el háptico.
  const submit = useCallback(async () => {
    if (tooShort || phase === "sending") return;
    setPhase("sending");
    try {
      await sendSupportRequest(payload());
      successHaptic();
      setPhase("sent");
      setMessage("");
      setContactEmail("");
    } catch (e) {
      // El detalle técnico no le sirve de nada al usuario: lo que necesita es
      // la otra vía para contárnoslo. Lo único que cambia el mensaje es si el
      // servidor nos ha pedido esperar.
      setRateLimited(e instanceof ApiError && e.status === 429);
      setPhase("failed");
    }
  }, [payload, phase, tooShort]);

  const openMail = useCallback(async () => {
    try {
      await Linking.openURL(supportMailtoUrl(payload()));
    } catch {
      setMailtoFailed(true);
    }
  }, [payload]);

  /** Copia el informe entero, no solo la dirección: si aquí solo dejásemos
   * hola@selai.es, el usuario tendría que reescribir lo que ya ha contado. */
  const copyReport = useCallback(async () => {
    try {
      await Clipboard.setStringAsync(supportPlainText(payload()));
      setCopied(true);
    } catch {
      // Sin portapapeles, el texto sigue en el campo y la dirección en pantalla.
    }
  }, [payload]);

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <KeyboardAvoidingView
        style={styles.backdrop}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <View style={styles.card}>
          <View style={styles.header}>
            <Text style={styles.title}>
              {phase === "sent" ? "Gracias" : "Reportar un problema"}
            </Text>
            <Pressable
              onPress={() => {
                tapHaptic();
                onClose();
              }}
              hitSlop={10}
              accessibilityRole="button"
              accessibilityLabel="Cerrar"
            >
              <Ionicons name="close" size={22} color={colors.inkSecondary} />
            </Pressable>
          </View>

          {phase === "sent" ? (
            <View style={styles.body}>
              <Text style={styles.paragraph}>
                Hemos recibido tu mensaje. Lo revisamos y, si hace falta, te
                escribimos.
              </Text>
              <PrimaryButton label="Cerrar" onPress={onClose} />
            </View>
          ) : (
            <ScrollView
              style={styles.scroll}
              contentContainerStyle={styles.body}
              keyboardShouldPersistTaps="handled"
            >
              <Text style={styles.paragraph}>
                Cuéntanos qué ha pasado. Enviamos tu mensaje junto con la
                versión de la app y la pantalla en la que estabas. No enviamos
                tus conversaciones ni los datos de tus leads.
              </Text>

              <TextInput
                style={styles.textArea}
                placeholder="¿Qué estabas haciendo y qué ha pasado?"
                placeholderTextColor={colors.inkSecondary}
                value={message}
                onChangeText={setMessage}
                multiline
                textAlignVertical="top"
                maxLength={MESSAGE_MAX}
                editable={phase !== "sending"}
                accessibilityLabel="Descripción del problema"
              />

              <TextInput
                style={styles.input}
                placeholder="Correo para respuesta (opcional)"
                placeholderTextColor={colors.inkSecondary}
                value={contactEmail}
                onChangeText={setContactEmail}
                autoCapitalize="none"
                keyboardType="email-address"
                editable={phase !== "sending"}
                accessibilityLabel="Correo para respuesta, opcional"
              />

              {phase === "failed" ? (
                <View style={styles.failedBox}>
                  <Text style={styles.failedText}>
                    {rateLimited
                      ? "Has enviado varios mensajes seguidos. Espera unos minutos antes de volver a intentarlo desde aquí; si es urgente, mándanoslo por correo."
                      : "No hemos podido enviarlo desde la app. Ábrelo en tu correo: ya va escrito, solo tienes que darle a enviar."}
                  </Text>
                  {mailtoFailed ? (
                    <>
                      <Text style={styles.failedText}>
                        Tampoco hemos podido abrir tu aplicación de correo.
                        Copia el mensaje y escríbenos a{" "}
                        <Text selectable style={styles.failedEmail}>
                          {SUPPORT_EMAIL}
                        </Text>
                        .
                      </Text>
                      <SecondaryButton
                        label={copied ? "Mensaje copiado" : "Copiar el mensaje"}
                        icon={copied ? "checkmark" : "copy-outline"}
                        onPress={() => void copyReport()}
                      />
                    </>
                  ) : (
                    <PrimaryButton
                      label="Escribirnos por correo"
                      icon="mail-outline"
                      onPress={() => void openMail()}
                    />
                  )}
                </View>
              ) : null}

              {/* Tras un 429 el botón desaparece en vez de fallar otra vez:
                  vuelve al reabrir el formulario. */}
              {rateLimited ? null : (
                <>
                  <PrimaryButton
                    label={phase === "failed" ? "Reintentar el envío" : "Enviar"}
                    icon={phase === "failed" ? "refresh-outline" : "send-outline"}
                    onPress={() => void submit()}
                    disabled={tooShort}
                    loading={phase === "sending"}
                  />
                  {tooShort ? (
                    <Text style={styles.hint}>
                      Escribe al menos unas palabras para que podamos entenderlo.
                    </Text>
                  ) : null}
                </>
              )}
            </ScrollView>
          )}
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: colors.overlay,
    alignItems: "center",
    justifyContent: "center",
    padding: spacing(5),
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    width: "100%",
    maxWidth: 420,
    maxHeight: "85%",
    overflow: "hidden",
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: spacing(4),
    paddingTop: spacing(4),
    paddingBottom: spacing(2),
  },
  title: { ...type.heading, fontSize: 18 },
  scroll: { flexGrow: 0 },
  body: { padding: spacing(4), paddingTop: spacing(2), gap: spacing(3) },
  paragraph: { ...type.bodySecondary, fontSize: 14, lineHeight: 20 },
  textArea: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    borderRadius: radius.input,
    backgroundColor: colors.bg,
    color: colors.ink,
    fontSize: 15,
    minHeight: 120,
    padding: spacing(3),
  },
  input: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    borderRadius: radius.input,
    backgroundColor: colors.bg,
    color: colors.ink,
    fontSize: 15,
    height: 48,
    paddingHorizontal: spacing(3),
  },
  failedBox: {
    backgroundColor: colors.warningSoft,
    borderRadius: radius.small,
    padding: spacing(3),
    gap: spacing(3),
  },
  failedText: { ...type.body, fontSize: 14, color: colors.warningInk, lineHeight: 20 },
  failedEmail: { fontWeight: "700", color: colors.warningInk },
  hint: { ...type.meta, fontWeight: "400", fontSize: 12, textAlign: "center" },
});
