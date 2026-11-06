// Paso 9 · Prueba final — chat nativo contra la IA de verdad. Habla con el
// mismo endpoint público que el widget de la web
// (POST /webhooks/web/{slug}/message), así que lo que responde aquí es
// exactamente lo que le responderá a un cliente.
import { useCallback, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import * as Crypto from "expo-crypto";
import { ApiError, isTimeout } from "@/lib/api";
import { useSetupWizard } from "@/context/SetupWizardContext";
import { sendWebChatMessage } from "@/lib/setup-api";
import { Notice, NoticeText } from "@/components/setup/kit";
import { useUnsavedStepGuard } from "@/components/setup/useUnsavedStepGuard";
import { SupportEmail, withSupportEmail } from "@/components/ui";
import { successHaptic, tapHaptic } from "@/lib/haptics";
// Interpolada, no escrita a mano: `withSupportEmail` parte el texto por esta
// misma constante para volver la dirección pulsable.
import { SUPPORT_EMAIL } from "@/lib/support";
import { colors, radius, spacing, type } from "@/lib/theme";

/** Viaja en el correo de soporte para que nos llegue ya situado. */
const SUPPORT_CONTEXT = "Puesta en marcha · Paso 9 (prueba final)";

type Bubble =
  | { id: string; role: "user" | "ai"; text: string }
  | { id: string; role: "error"; text: string; retryText: string };

const SUGGESTIONS = [
  "Hola, ¿tenéis pisos de 2 habitaciones?",
  "¿Podría ver uno esta semana?",
];

function MessageBubble({ item, onRetry }: { item: Bubble; onRetry: (text: string) => void }) {
  if (item.role === "error") {
    return (
      <View style={styles.errorWrap}>
        <View style={styles.errorBubble}>
          <Ionicons name="alert-circle-outline" size={16} color={colors.dangerInk} />
          {/* El texto se arma en el `catch`: la dirección se vuelve pulsable
              aquí, al pintarla. */}
          <Text style={styles.errorText}>{withSupportEmail(item.text, SUPPORT_CONTEXT)}</Text>
        </View>
        <Pressable
          accessibilityRole="button"
          onPress={() => {
            tapHaptic();
            onRetry(item.retryText);
          }}
          style={({ pressed }) => [styles.retryChip, pressed ? { opacity: 0.85 } : null]}
        >
          <Ionicons name="refresh-outline" size={14} color={colors.primary} />
          <Text style={styles.retryChipText}>Reintentar</Text>
        </Pressable>
      </View>
    );
  }
  const isUser = item.role === "user";
  return (
    <View style={[styles.bubbleWrap, isUser ? styles.right : styles.left]}>
      {!isUser ? (
        <View style={styles.aiRow}>
          <View style={styles.aiAvatar}>
            <Ionicons name="sparkles-outline" size={13} color={colors.primary} />
          </View>
          <Text style={styles.roleLabel}>Tu IA</Text>
        </View>
      ) : null}
      <View style={[styles.bubble, isUser ? styles.bubbleUser : styles.bubbleAi]}>
        <Text style={isUser ? styles.bubbleTextUser : styles.bubbleTextAi}>{item.text}</Text>
      </View>
    </View>
  );
}

export default function StepTest() {
  const { slug, channels, doneMap, markChecklist } = useSetupWizard();

  // useRef evalúa su argumento en cada render, y este componente se
  // re-renderiza con cada tecla: la sesión se crea una sola vez, a mano.
  const sessionIdRef = useRef<string | null>(null);
  if (sessionIdRef.current === null) {
    sessionIdRef.current = `app-${Crypto.randomUUID()}`;
  }
  const listRef = useRef<FlatList<Bubble>>(null);
  const markedRef = useRef(doneMap.test_call);

  const [messages, setMessages] = useState<Bubble[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);

  useUnsavedStepGuard(
    draft.trim().length > 0,
    "El mensaje de prueba que estás escribiendo todavía no se ha enviado."
  );

  const send = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text || sending || !slug) return;
      setSending(true);
      setDraft("");
      setMessages((cur) => [
        ...cur.filter((m) => m.role !== "error"),
        { id: `u-${Date.now()}`, role: "user", text },
      ]);
      setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 60);
      try {
        const out = await sendWebChatMessage(slug, sessionIdRef.current!, text);
        const reply = out.reply?.trim();
        setMessages((cur) => [
          ...cur,
          {
            id: `a-${Date.now()}`,
            role: "ai",
            text:
              reply ||
              "Tu asistente ha recibido el mensaje pero no ha devuelto texto. Míralo en tu bandeja.",
          },
        ]);
        if (!markedRef.current) {
          markedRef.current = true;
          successHaptic();
          void markChecklist({ test_call: true }).catch(() => {
            markedRef.current = false;
          });
        }
      } catch (e) {
        const status = e instanceof ApiError ? e.status : 0;
        setMessages((cur) => [
          ...cur,
          {
            id: `e-${Date.now()}`,
            role: "error",
            retryText: text,
            text:
              status === 429
                ? "Has enviado muchos mensajes seguidos. Espera un minuto y vuelve a probar."
                : status === 404
                  ? `No hemos encontrado el chat de tu agencia. Escríbenos a ${SUPPORT_EMAIL} y lo miramos.`
                  : isTimeout(e)
                    ? "Tu asistente está tardando demasiado en contestar. Vuelve a intentarlo; puede que la respuesta aparezca igualmente en tu bandeja."
                    : "No hemos podido enviar el mensaje. Comprueba tu conexión.",
          },
        ]);
      } finally {
        setSending(false);
        setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 60);
      }
    },
    [sending, slug, markChecklist]
  );

  const aiPhone = channels?.phone.number ?? null;

  return (
    <View style={styles.root}>
      <View style={styles.intro}>
        <Text style={styles.lead}>
          Habla con tu IA como lo haría un cliente: pregunta por un piso, pide una
          visita, ponle pegas. Es tu asistente real, así que la conversación aparecerá
          en tu bandeja y puede crear un lead de prueba.
        </Text>
        {doneMap.test_call ? (
          <View style={styles.doneRow}>
            <Ionicons name="checkmark-circle" size={16} color={colors.successInk} />
            <Text style={styles.doneText}>Ya has probado tu asistente y respondió.</Text>
          </View>
        ) : null}
        {!slug ? (
          <Notice tone="danger" title="No podemos abrir tu chat">
            <NoticeText tone="danger">
              No hemos podido identificar el chat de tu agencia, así que este paso no
              puede funcionar ahora mismo. Cierra la app y vuelve a entrar; si sigue
              igual, escríbenos a <SupportEmail context={SUPPORT_CONTEXT} /> y lo
              miramos.
            </NoticeText>
          </Notice>
        ) : null}
      </View>

      <FlatList
        ref={listRef}
        style={styles.list}
        data={messages}
        keyExtractor={(m) => m.id}
        renderItem={({ item }) => <MessageBubble item={item} onRetry={(t) => void send(t)} />}
        contentContainerStyle={styles.listContent}
        keyboardDismissMode="on-drag"
        keyboardShouldPersistTaps="handled"
        ListEmptyComponent={
          <View style={styles.empty}>
            <View style={styles.emptyIcon}>
              <Ionicons name="chatbubbles-outline" size={24} color={colors.primary} />
            </View>
            <Text style={styles.emptyTitle}>Escríbele lo que le escribiría un cliente</Text>
            <View style={styles.suggestions}>
              {SUGGESTIONS.map((s) => (
                <Pressable
                  key={s}
                  accessibilityRole="button"
                  disabled={!slug}
                  onPress={() => {
                    tapHaptic();
                    void send(s);
                  }}
                  style={({ pressed }) => [
                    styles.suggestion,
                    pressed ? { opacity: 0.85 } : null,
                  ]}
                >
                  <Text style={styles.suggestionText}>{s}</Text>
                </Pressable>
              ))}
            </View>
          </View>
        }
        ListFooterComponent={
          sending ? (
            <View style={styles.typingRow}>
              <ActivityIndicator size="small" color={colors.primary} />
              <Text style={styles.typingText}>Tu IA está escribiendo...</Text>
            </View>
          ) : null
        }
      />

      {aiPhone ? (
        <Text style={styles.phoneNote}>
          ¿Prefieres oírlo? Llama al {aiPhone}: es el mismo asistente, con la voz que
          elegiste.
        </Text>
      ) : null}

      <View style={styles.composer}>
        <TextInput
          style={styles.input}
          value={draft}
          onChangeText={setDraft}
          placeholder="Escribe tu mensaje"
          placeholderTextColor={colors.inkSecondary}
          multiline
          maxLength={4000}
          accessibilityLabel="Mensaje para tu IA"
        />
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Enviar mensaje"
          disabled={!draft.trim() || sending || !slug}
          onPress={() => {
            tapHaptic();
            void send(draft);
          }}
          style={({ pressed }) => [
            styles.sendBtn,
            !draft.trim() || sending || !slug ? styles.sendBtnDisabled : null,
            pressed ? { opacity: 0.85 } : null,
          ]}
        >
          <Ionicons name="arrow-up" size={20} color={colors.white} />
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, gap: spacing(3) },
  intro: { gap: spacing(2) },
  lead: { ...type.bodySecondary, lineHeight: 22 },
  doneRow: { flexDirection: "row", alignItems: "center", gap: spacing(2) },
  doneText: { ...type.meta, color: colors.successInk },

  list: { flex: 1 },
  listContent: { gap: spacing(3), paddingVertical: spacing(2) },

  bubbleWrap: { maxWidth: "86%", gap: spacing(1) },
  left: { alignSelf: "flex-start" },
  right: { alignSelf: "flex-end" },
  aiRow: { flexDirection: "row", alignItems: "center", gap: spacing(1) },
  aiAvatar: {
    width: 22,
    height: 22,
    borderRadius: 11,
    backgroundColor: colors.primarySoft,
    alignItems: "center",
    justifyContent: "center",
  },
  roleLabel: { ...type.meta, fontSize: 11 },
  bubble: {
    borderRadius: radius.card,
    paddingHorizontal: spacing(4),
    paddingVertical: spacing(3),
  },
  bubbleAi: { backgroundColor: colors.primarySoft, borderTopLeftRadius: 4 },
  bubbleUser: { backgroundColor: colors.neutralSoft, borderTopRightRadius: 4 },
  bubbleTextAi: { ...type.body, color: colors.ink, lineHeight: 21 },
  bubbleTextUser: { ...type.body, lineHeight: 21 },

  errorWrap: { alignSelf: "flex-start", maxWidth: "86%", gap: spacing(2) },
  errorBubble: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing(2),
    backgroundColor: colors.dangerSoft,
    borderRadius: radius.card,
    paddingHorizontal: spacing(3),
    paddingVertical: spacing(3),
  },
  errorText: { flex: 1, fontSize: 13, lineHeight: 19, color: colors.dangerInk },
  retryChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(1),
    alignSelf: "flex-start",
    paddingHorizontal: spacing(3),
    paddingVertical: spacing(2),
    borderRadius: radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    backgroundColor: colors.surface,
  },
  retryChipText: { fontSize: 13, fontWeight: "600", color: colors.primary },

  empty: { alignItems: "center", gap: spacing(3), paddingVertical: spacing(6) },
  emptyIcon: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: colors.surface,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    alignItems: "center",
    justifyContent: "center",
  },
  emptyTitle: { ...type.bodySecondary, textAlign: "center" },
  suggestions: { gap: spacing(2), alignSelf: "stretch" },
  suggestion: {
    backgroundColor: colors.surface,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    borderRadius: radius.pill,
    paddingHorizontal: spacing(4),
    paddingVertical: spacing(3),
  },
  suggestionText: { ...type.body, fontSize: 14, textAlign: "center" },

  typingRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(2),
    paddingVertical: spacing(2),
  },
  typingText: { ...type.meta },

  phoneNote: { ...type.meta, fontWeight: "400", fontSize: 12, lineHeight: 18 },

  composer: { flexDirection: "row", alignItems: "flex-end", gap: spacing(2) },
  input: {
    flex: 1,
    minHeight: 48,
    maxHeight: 120,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    backgroundColor: colors.surface,
    borderRadius: radius.input,
    paddingHorizontal: spacing(4),
    paddingTop: spacing(3),
    paddingBottom: spacing(3),
    fontSize: 15,
    color: colors.ink,
  },
  sendBtn: {
    width: 48,
    height: 48,
    borderRadius: radius.input,
    backgroundColor: colors.primary,
    alignItems: "center",
    justifyContent: "center",
  },
  sendBtnDisabled: { opacity: 0.4 },
});
