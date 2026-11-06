// Agenda de visitas. Además de enseñar el día, es la única puerta de entrada
// a la nota de voz post-visita: la pantalla /visit/[id] existía desde hace
// tiempo, estaba registrada en el router y no había forma humana de llegar a
// ella desde la app.
//
// Las horas se pintan en la zona horaria de la AGENCIA, que viaja en cada
// visita (`timezone`), no en la del teléfono: un agente de viaje no puede ver
// sus visitas movidas dos horas.
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  FlatList,
  Linking,
  Pressable,
  RefreshControl,
  SectionList,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { router } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { fetchVisits, Visit } from "@/lib/api";
import { useAgency } from "@/context/AgencyContext";
import {
  Chip,
  EmptyView,
  ErrorView,
  IconCircleButton,
  Pill,
  SkeletonList,
} from "@/components/ui";
import { dayKeyInZone, formatDayHeading, formatTimeInZone } from "@/lib/format";
import { tapHaptic } from "@/lib/haptics";
import {
  colors,
  radius,
  shadow,
  spacing,
  type,
  visitStatusLabel,
  visitStatusTone,
} from "@/lib/theme";

type Section = { title: string; data: Visit[] };

/** Agrupa por día natural de la agencia, conservando el orden cronológico que
 * ya trae el backend. */
function groupByDay(visits: Visit[]): Section[] {
  const sections: Section[] = [];
  let currentKey: string | null = null;
  for (const visit of visits) {
    const key = dayKeyInZone(visit.scheduled_at, visit.timezone);
    if (key !== currentKey) {
      currentKey = key;
      sections.push({
        title: formatDayHeading(visit.scheduled_at, visit.timezone),
        data: [],
      });
    }
    sections[sections.length - 1].data.push(visit);
  }
  return sections;
}

function VisitCard({ item }: { item: Visit }) {
  const cancelled = item.status === "cancelled";

  function call() {
    if (!item.lead_phone) return;
    void Linking.openURL(`tel:${item.lead_phone}`);
  }

  function openVoiceNote() {
    tapHaptic();
    // El contexto viaja como parámetro para que la pantalla de la nota diga de
    // qué visita se trata sin tener que volver a pedir la agenda entera.
    router.push({
      pathname: "/visit/[id]",
      params: {
        id: item.id,
        lead: item.lead_name ?? "",
        property: item.property_title ?? "",
        when: `${formatDayHeading(item.scheduled_at, item.timezone)}, ${formatTimeInZone(
          item.scheduled_at,
          item.timezone
        )}`,
      },
    });
  }

  return (
    <View style={[styles.card, cancelled ? styles.cardMuted : null]}>
      <View style={styles.cardHead}>
        <Text style={styles.time}>
          {formatTimeInZone(item.scheduled_at, item.timezone)}
        </Text>
        <Pill
          label={visitStatusLabel[item.status] || item.status}
          tone={visitStatusTone[item.status] || "neutral"}
        />
        <View style={{ flex: 1 }} />
        <IconCircleButton
          icon="call-outline"
          disabled={!item.lead_phone}
          onPress={call}
          accessibilityLabel={`Llamar a ${item.lead_name || "el cliente"}`}
        />
      </View>

      <Text style={styles.leadName} numberOfLines={1}>
        {item.lead_name || "Sin nombre"}
      </Text>
      <Text style={styles.property} numberOfLines={2}>
        {item.property_title || "Inmueble sin título"}
      </Text>

      {item.briefing ? (
        <View style={styles.briefing}>
          <Ionicons name="sparkles-outline" size={14} color={colors.primary} />
          <Text style={styles.briefingText}>{item.briefing}</Text>
        </View>
      ) : null}

      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`Grabar nota de voz de la visita de ${item.lead_name || "este cliente"}`}
        style={({ pressed }) => [styles.noteBtn, pressed ? { opacity: 0.85 } : null]}
        onPress={openVoiceNote}
      >
        <Ionicons name="mic-outline" size={17} color={colors.primary} />
        <Text style={styles.noteBtnText}>Nota de voz de la visita</Text>
        <Ionicons name="chevron-forward" size={16} color={colors.inkSecondary} />
      </Pressable>
    </View>
  );
}

const RANGE_FILTERS = [
  { value: "upcoming", label: "Próximas" },
  { value: "all", label: "Todas" },
] as const;

export default function AgendaScreen() {
  const { me } = useAgency();
  const agencyId = me?.agency_id;
  const [visits, setVisits] = useState<Visit[] | null>(null);
  const [range, setRange] = useState<"upcoming" | "all">("upcoming");
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(false);

  const load = useCallback(
    async (opts: { silent?: boolean } = {}) => {
      if (!agencyId) return;
      if (!opts.silent) setVisits(null);
      setError(false);
      try {
        setVisits(await fetchVisits(agencyId, range === "upcoming"));
      } catch {
        setError(true);
      }
    },
    [agencyId, range]
  );

  useEffect(() => {
    void load();
  }, [load]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await load({ silent: true });
    setRefreshing(false);
  }, [load]);

  const sections = useMemo(() => (visits ? groupByDay(visits) : null), [visits]);

  const filters = (
    <View style={styles.filtersWrap}>
      <FlatList
        horizontal
        data={RANGE_FILTERS}
        keyExtractor={(f) => f.value}
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.filters}
        renderItem={({ item }) => (
          <Chip
            label={item.label}
            active={range === item.value}
            onPress={() => setRange(item.value)}
          />
        )}
      />
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

  if (sections === null) {
    return (
      <View style={styles.screen}>
        {filters}
        <SkeletonList rows={4} />
      </View>
    );
  }

  return (
    <View style={styles.screen}>
      {filters}
      <SectionList
        sections={sections}
        keyExtractor={(v) => v.id}
        stickySectionHeadersEnabled={false}
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
          sections.length === 0 ? { flexGrow: 1 } : null,
        ]}
        renderSectionHeader={({ section }) => (
          <Text style={styles.sectionHeader}>{section.title}</Text>
        )}
        ListEmptyComponent={
          <EmptyView
            icon="calendar-outline"
            title={
              range === "upcoming" ? "No tienes visitas por delante" : "Aún no hay visitas"
            }
            subtitle={
              range === "upcoming"
                ? "Cuando la IA agende una visita aparecerá aquí, con su preparación."
                : "En cuanto la IA agende la primera visita la verás en esta pantalla."
            }
            ctaLabel={range === "upcoming" ? "Ver todas las visitas" : undefined}
            onCta={range === "upcoming" ? () => setRange("all") : undefined}
          />
        }
        renderItem={({ item }) => <VisitCard item={item} />}
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
  sectionHeader: {
    ...type.meta,
    textTransform: "uppercase",
    letterSpacing: 0.5,
    fontSize: 12,
    marginTop: spacing(2),
    marginBottom: spacing(2),
    marginLeft: spacing(1),
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    padding: spacing(4),
    gap: 3,
    ...shadow.card,
  },
  cardMuted: { opacity: 0.6 },
  cardHead: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(2),
    marginBottom: spacing(1),
  },
  time: { ...type.body, fontWeight: "700", fontSize: 17, fontVariant: ["tabular-nums"] },
  leadName: { ...type.body, fontWeight: "600" },
  property: { ...type.meta, fontWeight: "400" },
  briefing: {
    flexDirection: "row",
    gap: spacing(2),
    backgroundColor: colors.primarySoft,
    borderRadius: radius.small,
    padding: spacing(3),
    marginTop: spacing(2),
  },
  briefingText: {
    ...type.bodySecondary,
    color: colors.ink,
    fontSize: 13,
    lineHeight: 18,
    flexShrink: 1,
  },
  noteBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(2),
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.hairline,
    marginTop: spacing(3),
    paddingTop: spacing(3),
  },
  noteBtnText: { ...type.body, color: colors.primary, fontWeight: "600", flex: 1 },
});
