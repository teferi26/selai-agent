import { useCallback, useEffect, useRef, useState } from "react";
import {
  FlatList,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { router, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { fetchInbox, InboxItem } from "@/lib/api";
import { useAgency } from "@/context/AgencyContext";
import {
  Avatar,
  Chip,
  EmptyView,
  ErrorView,
  Pill,
  SkeletonList,
} from "@/components/ui";
import { statusNotice, type StatusNotice } from "@/lib/billing";
import { timeAgo } from "@/lib/format";
import { tapHaptic } from "@/lib/haptics";
import { firstPendingStepNumber, useSetupStatus } from "@/lib/setup";
import {
  channelIcon,
  channelLabel,
  colors,
  conversationPill,
  radius,
  shadow,
  spacing,
  type,
} from "@/lib/theme";

const CHANNEL_FILTERS = [
  { value: "", label: "Todos", icon: undefined },
  { value: "whatsapp", label: "WhatsApp", icon: channelIcon.whatsapp },
  { value: "email", label: "Email", icon: channelIcon.email },
  { value: "voice", label: "Voz", icon: channelIcon.voice },
  { value: "sms", label: "SMS", icon: channelIcon.sms },
  { value: "web", label: "Web", icon: channelIcon.web },
] as const;

/** "Puesta en marcha" progress card shown while setup is incomplete. Opens the
 * native wizard straight on the first step still pending. */
function SetupCard({
  doneCount,
  total,
  nextStep,
}: {
  doneCount: number;
  total: number;
  nextStep: number;
}) {
  const remaining = total - doneCount;
  return (
    <Pressable
      style={({ pressed }) => [styles.setupCard, pressed ? { opacity: 0.9 } : null]}
      onPress={() => {
        tapHaptic();
        router.push(`/setup/${nextStep}`);
      }}
      accessibilityRole="button"
    >
      <View style={styles.setupIconWrap}>
        <Ionicons name="rocket-outline" size={20} color={colors.primary} />
      </View>
      <View style={{ flex: 1 }}>
        <Text style={styles.setupTitle}>Puesta en marcha</Text>
        <Text style={styles.setupSubtitle}>
          {remaining === 1
            ? "Te queda 1 paso para estar al 100%"
            : `Te quedan ${remaining} pasos para estar al 100%`}
        </Text>
        <View style={styles.progressTrack}>
          <View
            style={[styles.progressFill, { width: `${(doneCount / total) * 100}%` }]}
          />
        </View>
      </View>
      <Ionicons name="chevron-forward" size={18} color={colors.inkSecondary} />
    </Pressable>
  );
}

/** Aviso de facturación cuando la suscripción está rota (pausada, cancelada,
 * sin pagar) o a punto. La bandeja es la primera pantalla que ve el dueño al
 * abrir la app, y sin esto un "la IA no contesta a nadie" solo se explicaba
 * entrando a Ajustes a buscarlo. */
function BillingAlert({ notice }: { notice: StatusNotice }) {
  const danger = notice.tone === "danger";
  const ink = danger ? colors.dangerInk : colors.warningInk;
  return (
    <Pressable
      style={({ pressed }) => [
        styles.billingCard,
        { backgroundColor: danger ? colors.dangerSoft : colors.warningSoft },
        pressed ? { opacity: 0.9 } : null,
      ]}
      onPress={() => {
        tapHaptic();
        router.push("/billing");
      }}
      accessibilityRole="button"
      accessibilityLabel={`${notice.title}. Abrir plan y facturación`}
    >
      <Ionicons
        name={danger ? "alert-circle-outline" : "warning-outline"}
        size={20}
        color={ink}
      />
      <View style={{ flex: 1 }}>
        <Text style={[styles.billingTitle, { color: ink }]}>{notice.title}</Text>
        <Text style={[styles.billingBody, { color: ink }]}>{notice.body}</Text>
      </View>
      <Ionicons name="chevron-forward" size={18} color={ink} />
    </Pressable>
  );
}

function ConversationCard({ item }: { item: InboxItem }) {
  const pill = conversationPill(item.status, item.lead_temperature);
  return (
    <Pressable
      style={({ pressed }) => [styles.convCard, pressed ? { opacity: 0.9 } : null]}
      onPress={() => {
        tapHaptic();
        // El estado y el canal viajan con la navegación para que el pie de la
        // conversación se pinte bien en el primer frame; allí se reconfirman
        // contra el servidor antes de dejar actuar a ningún botón.
        router.push({
          pathname: "/conversation/[id]",
          params: {
            id: item.conversation_id,
            status: item.status,
            channel: item.channel,
          },
        });
      }}
      accessibilityRole="button"
    >
      <Avatar name={item.lead_name} size={44} />
      <View style={styles.convBody}>
        <View style={styles.convTitleRow}>
          <Text style={styles.convName} numberOfLines={1}>
            {item.lead_name || "Sin nombre"}
          </Text>
          <Text style={styles.convTime}>{timeAgo(item.last_message_at)}</Text>
        </View>
        <View style={styles.convMetaRow}>
          <Ionicons
            name={channelIcon[item.channel] || "chatbubble-outline"}
            size={13}
            color={colors.inkSecondary}
          />
          <Text style={styles.convMetaText}>
            {channelLabel[item.channel] || item.channel}
          </Text>
          <Pill label={pill.label} tone={pill.tone} />
          {item.unread ? <View style={styles.unreadDot} /> : null}
        </View>
        <Text style={styles.convPreview} numberOfLines={2}>
          {item.last_message_preview ||
            `Conversación por ${channelLabel[item.channel] || item.channel}`}
        </Text>
      </View>
    </Pressable>
  );
}

export default function InboxScreen() {
  const { me } = useAgency();
  const agencyId = me?.agency_id;
  const [items, setItems] = useState<InboxItem[] | null>(null);
  const [channel, setChannel] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(false);
  const setup = useSetupStatus(agencyId);

  const load = useCallback(
    async (opts: { silent?: boolean } = {}) => {
      if (!agencyId) return;
      if (!opts.silent) setItems(null);
      setError(false);
      try {
        const data = await fetchInbox(agencyId, channel || undefined);
        setItems(data);
      } catch {
        setError(true);
      }
    },
    [agencyId, channel]
  );

  useEffect(() => {
    void load();
  }, [load]);

  const setupRefresh = setup.refresh;
  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await Promise.all([load({ silent: true }), setupRefresh()]);
    setRefreshing(false);
  }, [load, setupRefresh]);

  // Al volver del asistente de puesta en marcha la tarjeta tiene que reflejar
  // lo que se acaba de conectar. `refresh` se recrea en cada render, así que
  // va por ref para no reejecutar el efecto en bucle.
  const setupRefreshRef = useRef(setupRefresh);
  setupRefreshRef.current = setupRefresh;
  // Y la lista también: esta tarjeta es la que le pasa `status` y `channel` a
  // la pantalla de conversación para pintar el pie en el primer frame. Si no
  // se recarga al volver, quien acaba de tomar el control vuelve a entrar y lee
  // "ahora responde la IA" hasta que el servidor contesta. La primera vez no,
  // que de eso ya se encarga el efecto de montaje.
  const loadRef = useRef(load);
  loadRef.current = load;
  const mounted = useRef(false);
  useFocusEffect(
    useCallback(() => {
      void setupRefreshRef.current();
      if (!mounted.current) {
        mounted.current = true;
        return;
      }
      void loadRef.current({ silent: true });
    }, [])
  );

  const showSetupCard = setup.steps !== null && !setup.complete && !setup.error;
  const billingNotice = statusNotice(me?.subscription_status);

  const filters = (
    <View style={styles.filtersWrap}>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.filters}
      >
        {CHANNEL_FILTERS.map((f) => (
          <Chip
            key={f.value}
            label={f.label}
            icon={f.icon}
            active={channel === f.value}
            onPress={() => setChannel(f.value)}
          />
        ))}
      </ScrollView>
    </View>
  );

  if (error) {
    return (
      <View style={styles.screen}>
        {filters}
        <ErrorView onRetry={() => void load()} />
      </View>
    );
  }

  if (items === null) {
    return (
      <View style={styles.screen}>
        {filters}
        <SkeletonList rows={6} />
      </View>
    );
  }

  return (
    <View style={styles.screen}>
      {filters}
      <FlatList
        data={items}
        keyExtractor={(it) => it.conversation_id}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
            tintColor={colors.primary}
            colors={[colors.primary]}
          />
        }
        contentContainerStyle={[
          styles.listContent,
          items.length === 0 ? { flexGrow: 1 } : null,
        ]}
        ListHeaderComponent={
          billingNotice || showSetupCard ? (
            <>
              {billingNotice ? <BillingAlert notice={billingNotice} /> : null}
              {showSetupCard ? (
                <SetupCard
                  doneCount={setup.doneCount}
                  total={setup.total}
                  nextStep={firstPendingStepNumber(setup.steps)}
                />
              ) : null}
            </>
          ) : null
        }
        ListEmptyComponent={
          <EmptyView
            icon="chatbubbles-outline"
            title="Sin conversaciones todavía"
            subtitle="En cuanto la IA atienda a tu primer contacto, la conversación aparecerá aquí."
            ctaLabel="Ver puesta en marcha"
            onCta={() => router.push("/setup-checklist")}
          />
        }
        renderItem={({ item }) => <ConversationCard item={item} />}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  filtersWrap: {
    backgroundColor: colors.surface,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.hairline,
  },
  filters: { gap: spacing(2), paddingHorizontal: spacing(4), paddingVertical: spacing(3) },
  listContent: { padding: spacing(4), gap: spacing(3) },
  setupCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(3),
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    padding: spacing(4),
    marginBottom: spacing(3),
    ...shadow.card,
  },
  billingCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(3),
    borderRadius: radius.card,
    padding: spacing(4),
    marginBottom: spacing(3),
  },
  billingTitle: { ...type.heading, fontSize: 15 },
  billingBody: { fontSize: 12, lineHeight: 17, marginTop: 2 },
  setupIconWrap: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: colors.primarySoft,
    alignItems: "center",
    justifyContent: "center",
  },
  setupTitle: { ...type.heading, fontSize: 15 },
  setupSubtitle: { ...type.meta, fontWeight: "400", marginTop: 2 },
  progressTrack: {
    height: 4,
    borderRadius: 2,
    backgroundColor: colors.neutralSoft,
    marginTop: spacing(2),
    overflow: "hidden",
  },
  progressFill: {
    height: 4,
    borderRadius: 2,
    backgroundColor: colors.primary,
  },
  convCard: {
    flexDirection: "row",
    gap: spacing(3),
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    padding: spacing(4),
    ...shadow.card,
  },
  convBody: { flex: 1, gap: 3 },
  convTitleRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: spacing(2),
  },
  convName: { ...type.body, fontWeight: "600", flexShrink: 1 },
  convTime: { ...type.meta, fontWeight: "400", fontSize: 12 },
  convMetaRow: { flexDirection: "row", alignItems: "center", gap: spacing(1) },
  convMetaText: { ...type.meta, fontSize: 12, marginRight: spacing(1) },
  convPreview: { ...type.bodySecondary, fontSize: 14, lineHeight: 19 },
  unreadDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: colors.primary,
    marginLeft: "auto",
  },
});
