// Conversación completa desde el móvil: leer el histórico, tomar el control,
// responder al lead por su canal real y devolver la conversación a la IA.
//
// Dos decisiones que conviene no deshacer:
//
// 1. Quién manda en la conversación lo dice el SERVIDOR, no esta pantalla.
//    Antes era un booleano local: al salir y volver, el botón decía otra vez
//    "Tomar el control" aunque ya se hubiera tomado. Ahora el estado se pide a
//    GET /conversations/{id} y, cuando no se puede confirmar, se dice.
//    Ese mismo GET es lo único que distingue una conversación vacía de una que
//    ya no existe: /messages filtra por agencia y devuelve [] en los dos casos.
//
// 2. Los errores de envío se traducen uno a uno. El backend manda primero y
//    guarda después: si contesta error, NO ha guardado nada y el texto vuelve
//    al campo. Mismo criterio que `explain()` en el panel
//    (dashboard/src/app/(app)/conversations/[id]/ReplyComposer.tsx), más el
//    caso que solo existe en el móvil: el tiempo de espera agotado.
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  FlatList,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { router, useLocalSearchParams } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import {
  ApiError,
  Channel,
  ConversationMessage,
  fetchConversationState,
  fetchMessages,
  isTimeout,
  requestHandoff,
  resumeConversation,
  sendReply,
} from "@/lib/api";
import { useAgency } from "@/context/AgencyContext";
import {
  EmptyView,
  ErrorView,
  PrimaryButton,
  SecondaryButton,
  SkeletonList,
} from "@/components/ui";
import { formatTime } from "@/lib/format";
import { successHaptic, warningHaptic } from "@/lib/haptics";
import { colors, radius, spacing, type } from "@/lib/theme";

const ROLE_LABEL: Record<string, string> = {
  agent_ai: "IA",
  agent_human: "Agente",
};

/** Canales por los que el backend sabe enviar hoy (REPLYABLE_CHANNELS en
 * backend/app/api/dashboard.py). El resto contesta 501. */
const REPLYABLE: Channel[] = ["whatsapp", "email"];

/** Cómo se nombra el canal dentro de una frase ("se envía por correo"). */
const CHANNEL_IN_SENTENCE: Record<Channel, string> = {
  whatsapp: "WhatsApp",
  email: "correo",
  voice: "una llamada",
  sms: "SMS",
  web: "el chat de la web",
};

/** Mismo tope que `ReplyBody.text` en el backend. */
const MAX_LENGTH = 4000;

const KNOWN_STATUSES = ["active", "snoozed", "handed_off", "closed"];

/** Los parámetros de navegación se validan antes de creerlos: un enlace
 * directo puede traer cualquier cosa, y de un valor desconocido preferimos
 * decir que no sabemos el estado a afirmar que manda la IA. */
function seedHandedOff(status: string | undefined): boolean | null {
  if (!status || !KNOWN_STATUSES.includes(status)) return null;
  return status === "handed_off";
}

function seedChannel(channel: string | undefined): Channel | null {
  const known: Channel[] = ["whatsapp", "email", "voice", "sms", "web"];
  return known.includes(channel as Channel) ? (channel as Channel) : null;
}

// ---------------------------------------------------------------------------
// Mensajes de error honestos a partir de lo que devuelve el backend.
//
// En todos estos casos el mensaje NO ha salido y el backend no ha guardado
// nada, así que el texto vuelve al campo y aquí se explica qué ha pasado.
// ---------------------------------------------------------------------------

function explainReplyError(err: unknown, channel: Channel | null): string {
  // El POST se quedó sin respuesta. Es el único caso en el que no sabemos si
  // el mensaje salió, y decir "no se ha enviado" sería mentir: reenviar a
  // ciegas puede duplicarlo en el móvil del cliente.
  if (isTimeout(err)) {
    return (
      "Se ha agotado el tiempo de espera y no sabemos si el mensaje ha salido. " +
      "Recarga la conversación antes de volver a enviarlo."
    );
  }
  // Sesión y permisos: reintentar no arregla ninguno de los dos, así que el
  // texto no puede terminar en "inténtalo de nuevo".
  if (err instanceof ApiError && err.status === 401)
    return "Tu sesión ha caducado. Vuelve a entrar en selai e inténtalo otra vez.";
  const raw = err instanceof Error ? err.message : "";
  if (raw.includes("agency_access_denied") || raw.includes("role_required"))
    return "Tu cuenta no tiene permiso para responder en esta conversación.";
  if (raw.includes("lead_without_whatsapp"))
    return "No tenemos el teléfono de este cliente, así que no se puede enviar por WhatsApp.";
  if (raw.includes("lead_without_email"))
    return "No tenemos el correo de este cliente, así que no se puede enviar por email.";
  if (raw.includes("channel_not_supported"))
    return "Todavía no podemos responder por este canal desde selai.";
  if (raw.includes("whatsapp_window_expired"))
    return (
      "WhatsApp no deja escribir a un cliente pasadas 24 horas desde su último " +
      "mensaje, así que este no ha salido. Llámale, o espera a que te escriba " +
      "él para poder contestarle por aquí."
    );
  if (raw.includes("channel_not_connected"))
    return channel === "email"
      ? "Tu correo todavía no está conectado en selai, así que el mensaje no ha salido. Puedes conectarlo en Puesta en marcha."
      : "Tu WhatsApp todavía no está conectado en selai, así que el mensaje no ha salido. Puedes conectarlo en Puesta en marcha.";
  if (raw.includes("send_failed"))
    return "El mensaje no ha salido: el proveedor lo ha rechazado. No se ha guardado nada.";
  if (raw.includes("conversation_not_found") || raw.includes("lead_not_found"))
    return "Esta conversación ya no está disponible. Vuelve a la bandeja y ábrela de nuevo.";
  return "No se ha podido enviar el mensaje. Inténtalo de nuevo.";
}

// ---------------------------------------------------------------------------

function MessageBubble({ item }: { item: ConversationMessage }) {
  if (item.role === "system") {
    return (
      <View style={styles.systemWrap}>
        <Text style={styles.systemText}>{item.content}</Text>
      </View>
    );
  }
  // Agency side (IA / human agent) on the LEFT with a small avatar;
  // the client (lead) on the RIGHT in a light neutral bubble.
  const isLead = item.role === "lead";
  if (isLead) {
    return (
      <View style={[styles.bubbleWrap, styles.right]}>
        <View style={[styles.bubble, styles.bubbleLead]}>
          <Text style={styles.bubbleTextLead}>{item.content}</Text>
        </View>
        <Text style={styles.timestamp}>{formatTime(item.created_at)}</Text>
      </View>
    );
  }
  const isAi = item.role === "agent_ai";
  return (
    <View style={[styles.agentRow]}>
      <View style={styles.agentAvatar}>
        <Ionicons
          name={isAi ? "sparkles-outline" : "person-outline"}
          size={14}
          color={colors.primary}
        />
      </View>
      <View style={[styles.bubbleWrap, styles.left]}>
        <Text style={styles.roleLabel}>{ROLE_LABEL[item.role] || item.role}</Text>
        <View style={[styles.bubble, isAi ? styles.bubbleAgent : styles.bubbleHuman]}>
          <Text style={styles.bubbleTextAgent}>{item.content}</Text>
        </View>
        <Text style={styles.timestamp}>{formatTime(item.created_at)}</Text>
      </View>
    </View>
  );
}

export default function ConversationScreen() {
  // `status` y `channel` los pasa la tarjeta de la bandeja para pintar el pie
  // correcto en el primer frame; la carga los confirma contra el servidor.
  const { id, status, channel: channelParam } = useLocalSearchParams<{
    id: string;
    status?: string;
    channel?: string;
  }>();
  const { me } = useAgency();
  const agencyId = me?.agency_id;

  const [messages, setMessages] = useState<ConversationMessage[] | null>(null);
  const [error, setError] = useState(false);
  // El servidor dice que esta conversación no existe (o no es de esta agencia).
  // Es una respuesta, no un fallo: no hay nada que reintentar.
  const [gone, setGone] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  // null = no hemos podido confirmarlo con el servidor. Nunca se inventa.
  const [handedOff, setHandedOff] = useState<boolean | null>(() =>
    seedHandedOff(status)
  );
  const [channel, setChannel] = useState<Channel | null>(() =>
    seedChannel(channelParam)
  );

  const [modalOpen, setModalOpen] = useState(false);
  const [note, setNote] = useState("");
  const [sending, setSending] = useState(false);
  const [resuming, setResuming] = useState(false);

  const [draft, setDraft] = useState("");
  const [replying, setReplying] = useState(false);
  const [replyError, setReplyError] = useState<string | null>(null);
  const listRef = useRef<FlatList<ConversationMessage>>(null);
  // Solo llevamos al final al abrir y tras enviar nosotros. Una recarga manual
  // no debe arrancar al agente del mensaje antiguo que estaba consultando.
  const shouldScrollToLatestRef = useRef(true);

  const scrollToLatestIfNeeded = useCallback(() => {
    if (!shouldScrollToLatestRef.current) return;
    requestAnimationFrame(() => {
      listRef.current?.scrollToEnd({ animated: false });
      shouldScrollToLatestRef.current = false;
    });
  }, []);

  const load = useCallback(
    async (opts: { silent?: boolean } = {}) => {
      if (!agencyId || !id) return;
      if (!opts.silent) setMessages(null);
      setError(false);
      try {
        // El estado se pide aparte y no tumba la pantalla si falla: un fallo
        // suyo deja el histórico legible y el pie diciendo que no lo sabemos.
        // Un 404 sí es concluyente: la conversación no existe.
        const [msgs, state] = await Promise.all([
          fetchMessages(agencyId, id),
          fetchConversationState(agencyId, id).catch((e) =>
            e instanceof ApiError && e.status === 404 ? "gone" : null
          ),
        ]);
        if (state === "gone") {
          setGone(true);
          return;
        }
        setGone(false);
        if (!opts.silent) shouldScrollToLatestRef.current = true;
        setMessages(msgs);
        if (state) {
          setHandedOff(state.status === "handed_off");
          setChannel(state.channel);
        }
      } catch (e) {
        // /messages solo devuelve 404 si la ruta desaparece; un 403 sí puede
        // pasar si la sesión ya no pertenece a esta agencia.
        if (e instanceof ApiError && (e.status === 403 || e.status === 404)) setGone(true);
        else setError(true);
      }
    },
    [agencyId, id]
  );

  useEffect(() => {
    void load();
  }, [load]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await load({ silent: true });
    setRefreshing(false);
  }, [load]);

  async function confirmHandoff() {
    if (!agencyId || !id || sending) return;
    setSending(true);
    try {
      await requestHandoff(agencyId, id, {
        user_id: me?.user_id,
        note: note.trim(),
      });
      successHaptic();
      setHandedOff(true);
      setModalOpen(false);
      setNote("");
      // La nota deja un mensaje de sistema en el histórico.
      void load({ silent: true });
    } catch (e) {
      // Un 404 no se reintenta: la conversación ya no está.
      if (e instanceof ApiError && e.status === 404) {
        setModalOpen(false);
        setGone(true);
        return;
      }
      Alert.alert("Error", "No hemos podido traspasar la conversación. Inténtalo de nuevo.");
    } finally {
      setSending(false);
    }
  }

  function askResume() {
    warningHaptic();
    Alert.alert(
      "¿Devolver a la IA?",
      "El asistente volverá a responder a este cliente por su cuenta.",
      [
        { text: "Cancelar", style: "cancel" },
        { text: "Devolver", onPress: () => void resume() },
      ]
    );
  }

  async function resume() {
    if (!agencyId || !id || resuming) return;
    setResuming(true);
    try {
      await resumeConversation(agencyId, id);
      successHaptic();
      setHandedOff(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setGone(true);
        return;
      }
      Alert.alert(
        "No se ha podido devolver a la IA",
        "La conversación sigue en tus manos mientras tanto. Inténtalo de nuevo."
      );
    } finally {
      setResuming(false);
    }
  }

  async function send() {
    const text = draft.trim();
    if (!agencyId || !id || !text || replying) return;
    setReplying(true);
    setReplyError(null);
    try {
      const sent = await sendReply(agencyId, id, text);
      successHaptic();
      setDraft("");
      shouldScrollToLatestRef.current = true;
      // El backend devuelve el mensaje ya guardado: lo pintamos tal cual en
      // lugar de una copia optimista, así no hay nada que deshacer ni burbujas
      // duplicadas cuando la lista se recargue.
      setMessages((prev) => [
        ...(prev ?? []),
        {
          id: sent.id,
          role: sent.role,
          content: sent.content,
          created_at: sent.created_at,
        },
      ]);
      // Responder a mano ES tomar el control: el backend pausa la IA solo.
      if (sent.handed_off) setHandedOff(true);
    } catch (e) {
      // El texto se queda en el campo a propósito: nada se ha enviado, así que
      // reintentar es volver a pulsar Enviar.
      setReplyError(explainReplyError(e, channel));
      if (e instanceof ApiError && (e.status === 404 || e.status === 403)) {
        // Ya no sabemos quién atiende: la recarga lo confirmará o dirá que la
        // conversación ha dejado de existir.
        setHandedOff(null);
        void load({ silent: true });
      }
    } finally {
      setReplying(false);
    }
  }

  if (gone) {
    // Se llega aquí sobre todo desde un aviso push guardado en el teléfono:
    // el escalado de la semana pasada cuya conversación ya se cerró y se
    // borró. Antes esto se veía como "todavía no tiene historial".
    return (
      <EmptyView
        icon="chatbubble-ellipses-outline"
        title="Esta conversación ya no está disponible"
        subtitle="Puede que se haya eliminado o que pertenezca a otra agencia."
        ctaLabel="Volver a la bandeja"
        onCta={() => router.back()}
      />
    );
  }
  if (error) {
    return (
      <ErrorView
        onRetry={() => void load()}
        exitLabel="Volver a la bandeja"
        onExit={() => router.back()}
      />
    );
  }
  if (messages === null) return <SkeletonList rows={5} />;

  const canReply = channel !== null && REPLYABLE.includes(channel);
  // Cuando no podemos escribir por el canal, el pie explica qué hacer — y no
  // puede ofrecer "toma el control" a quien ya lo ha tomado, que es lo que
  // dice la línea de estado justo encima.
  const footerHint =
    channel === null
      ? "No sabemos por qué canal llegó esta conversación, así que no podemos enviar la respuesta desde aquí. Responde por el canal original."
      : handedOff === true
        ? `Esta conversación llegó por ${CHANNEL_IN_SENTENCE[channel]} y todavía no podemos responder por ahí desde selai. Habla tú con el cliente por ese mismo canal.`
        : `Esta conversación llegó por ${CHANNEL_IN_SENTENCE[channel]} y todavía no podemos responder por ahí desde selai. Puedes tomar el control para que la IA no conteste y hablar tú con el cliente.`;

  return (
    <KeyboardAvoidingView
      style={styles.screen}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      keyboardVerticalOffset={Platform.OS === "ios" ? 92 : 0}
    >
      <FlatList
        ref={listRef}
        data={messages}
        keyExtractor={(m) => m.id}
        onContentSizeChange={scrollToLatestIfNeeded}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
            tintColor={colors.primary}
            colors={[colors.primary]}
          />
        }
        contentContainerStyle={[
          styles.list,
          messages.length === 0 ? { flexGrow: 1 } : null,
        ]}
        ListEmptyComponent={
          <EmptyView
            icon="chatbubble-ellipses-outline"
            title="Sin mensajes"
            subtitle="Esta conversación todavía no tiene historial."
          />
        }
        renderItem={({ item }) => <MessageBubble item={item} />}
      />

      <View style={styles.footer}>
        {/* Quién está atendiendo, según el servidor. */}
        {handedOff === true ? (
          <View style={styles.statusRow}>
            <Ionicons name="hand-left-outline" size={15} color={colors.successInk} />
            <Text style={[styles.statusText, { color: colors.successInk }]}>
              La llevas tú. La IA no responde en esta conversación.
            </Text>
          </View>
        ) : handedOff === false ? (
          <View style={styles.statusRow}>
            <Ionicons name="sparkles-outline" size={15} color={colors.primary} />
            <Text style={styles.statusText}>
              Ahora responde la IA. Si escribes tú, deja de contestar a este cliente.
            </Text>
          </View>
        ) : (
          <View style={styles.statusRow}>
            <Ionicons name="help-circle-outline" size={15} color={colors.warningInk} />
            <Text style={[styles.statusText, { color: colors.warningInk }]}>
              No hemos podido confirmar quién está atendiendo esta conversación.
            </Text>
          </View>
        )}

        {canReply ? (
          <>
            <View style={styles.composerRow}>
              <TextInput
                style={styles.composerInput}
                placeholder={`Escribe y se envía por ${CHANNEL_IN_SENTENCE[channel]}...`}
                placeholderTextColor={colors.inkSecondary}
                value={draft}
                onChangeText={(t) => setDraft(t.slice(0, MAX_LENGTH))}
                multiline
                editable={!replying}
                accessibilityLabel="Escribe tu respuesta"
              />
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Enviar respuesta"
                style={({ pressed }) => [
                  styles.sendBtn,
                  draft.trim().length === 0 || replying ? styles.sendBtnDisabled : null,
                  pressed ? { opacity: 0.85 } : null,
                ]}
                disabled={draft.trim().length === 0 || replying}
                onPress={() => void send()}
              >
                <Ionicons
                  name={replying ? "ellipsis-horizontal" : "send"}
                  size={18}
                  color={colors.white}
                />
              </Pressable>
            </View>
            {draft.length > MAX_LENGTH - 400 ? (
              <Text style={styles.counter}>
                {draft.length}/{MAX_LENGTH}
              </Text>
            ) : null}
          </>
        ) : (
          <Text style={styles.footerHint}>{footerHint}</Text>
        )}

        {replyError ? (
          <View style={styles.errorBanner}>
            <Ionicons name="alert-circle-outline" size={16} color={colors.dangerInk} />
            <Text style={styles.errorBannerText}>{replyError}</Text>
          </View>
        ) : null}

        {handedOff === true ? (
          <SecondaryButton
            label={resuming ? "Devolviendo..." : "Devolver a la IA"}
            icon="sparkles-outline"
            onPress={askResume}
            disabled={resuming}
          />
        ) : (
          <PrimaryButton
            label="Tomar el control"
            icon="hand-left-outline"
            onPress={() => setModalOpen(true)}
          />
        )}
      </View>

      <Modal visible={modalOpen} transparent animationType="fade">
        <KeyboardAvoidingView
          style={styles.modalOverlay}
          behavior={Platform.OS === "ios" ? "padding" : undefined}
        >
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>Tomar el control</Text>
            <Text style={styles.modalSubtitle}>
              La IA dejará de responder en esta conversación. Puedes añadir una
              nota para el equipo (opcional).
            </Text>
            <TextInput
              style={styles.modalInput}
              placeholder="Nota (opcional)"
              placeholderTextColor={colors.inkSecondary}
              value={note}
              onChangeText={setNote}
              multiline
            />
            <View style={styles.modalActions}>
              <SecondaryButton
                label="Cancelar"
                onPress={() => setModalOpen(false)}
                disabled={sending}
                style={styles.modalBtn}
              />
              <PrimaryButton
                label={sending ? "Enviando..." : "Confirmar"}
                onPress={() => void confirmHandoff()}
                loading={sending}
                style={styles.modalBtn}
              />
            </View>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  list: { padding: spacing(4), gap: spacing(3) },
  systemWrap: { alignItems: "center", marginVertical: spacing(1) },
  systemText: {
    ...type.meta,
    fontSize: 12,
    textAlign: "center",
    backgroundColor: colors.neutralSoft,
    paddingHorizontal: spacing(3),
    paddingVertical: spacing(1),
    borderRadius: radius.pill,
    overflow: "hidden",
  },
  agentRow: {
    flexDirection: "row",
    gap: spacing(2),
    alignItems: "flex-end",
    maxWidth: "88%",
    alignSelf: "flex-start",
  },
  agentAvatar: {
    width: 26,
    height: 26,
    borderRadius: 13,
    backgroundColor: colors.primarySoft,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: spacing(4),
  },
  bubbleWrap: { maxWidth: "82%", flexShrink: 1 },
  left: { alignSelf: "flex-start", alignItems: "flex-start" },
  right: { alignSelf: "flex-end", alignItems: "flex-end" },
  roleLabel: { ...type.meta, fontSize: 11, marginBottom: 2 },
  bubble: {
    borderRadius: radius.card,
    paddingHorizontal: spacing(4),
    paddingVertical: spacing(3),
  },
  bubbleAgent: {
    backgroundColor: colors.primarySoft,
    borderBottomLeftRadius: radius.small / 2,
  },
  // Lo que ha escrito una persona se distingue de lo que ha escrito la IA:
  // al revisar el historial importa saber quién dijo qué.
  bubbleHuman: {
    backgroundColor: colors.successSoft,
    borderBottomLeftRadius: radius.small / 2,
  },
  bubbleLead: {
    backgroundColor: colors.neutralSoft,
    borderBottomRightRadius: radius.small / 2,
  },
  bubbleTextAgent: { ...type.body, color: colors.ink },
  bubbleTextLead: { ...type.body },
  timestamp: { ...type.meta, fontWeight: "400", fontSize: 11, marginTop: 3 },
  footer: {
    padding: spacing(4),
    paddingBottom: spacing(6),
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.hairline,
    backgroundColor: colors.surface,
    gap: spacing(2),
  },
  statusRow: { flexDirection: "row", alignItems: "flex-start", gap: spacing(2) },
  statusText: {
    ...type.meta,
    fontWeight: "400",
    fontSize: 12,
    lineHeight: 17,
    flexShrink: 1,
  },
  footerHint: {
    ...type.meta,
    fontWeight: "400",
    fontSize: 12,
    lineHeight: 17,
    textAlign: "center",
  },
  composerRow: { flexDirection: "row", alignItems: "flex-end", gap: spacing(2) },
  composerInput: {
    flex: 1,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    borderRadius: radius.input,
    paddingHorizontal: spacing(3),
    paddingTop: spacing(3),
    paddingBottom: spacing(3),
    minHeight: 44,
    maxHeight: 130,
    color: colors.ink,
    backgroundColor: colors.bg,
    fontSize: 15,
  },
  sendBtn: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: colors.primary,
    alignItems: "center",
    justifyContent: "center",
  },
  sendBtnDisabled: { opacity: 0.4 },
  counter: { ...type.meta, fontSize: 11, fontWeight: "400", textAlign: "right" },
  errorBanner: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing(2),
    backgroundColor: colors.dangerSoft,
    borderRadius: radius.small,
    padding: spacing(3),
  },
  errorBannerText: {
    ...type.bodySecondary,
    color: colors.dangerInk,
    fontSize: 13,
    lineHeight: 18,
    flexShrink: 1,
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: colors.overlay,
    justifyContent: "center",
    padding: spacing(6),
  },
  modalCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    padding: spacing(5),
    gap: spacing(3),
  },
  modalTitle: { ...type.heading, fontSize: 18 },
  modalSubtitle: { ...type.bodySecondary, fontSize: 13, lineHeight: 19 },
  modalInput: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    borderRadius: radius.small,
    padding: spacing(3),
    minHeight: 70,
    textAlignVertical: "top",
    color: colors.ink,
    backgroundColor: colors.bg,
    fontSize: 15,
  },
  modalActions: { flexDirection: "row", gap: spacing(2) },
  modalBtn: { flex: 1 },
});
