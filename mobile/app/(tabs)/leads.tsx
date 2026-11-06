import { useCallback, useEffect, useMemo, useState } from "react";
import {
  FlatList,
  Linking,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { router } from "expo-router";
import { fetchLeads, Lead } from "@/lib/api";
import { useAgency } from "@/context/AgencyContext";
import {
  Avatar,
  Chip,
  EmptyView,
  ErrorView,
  IconCircleButton,
  Pill,
  SkeletonList,
} from "@/components/ui";
import { formatBudget, formatOperation } from "@/lib/format";
import { tapHaptic } from "@/lib/haptics";
import {
  colors,
  radius,
  shadow,
  spacing,
  temperatureIcon,
  temperatureLabel,
  temperatureTone,
  type,
} from "@/lib/theme";

const TEMP_FILTERS = [
  { value: "", label: "Todos", icon: undefined },
  { value: "hot", label: "Calientes", icon: temperatureIcon.hot },
  { value: "warm", label: "Templados", icon: temperatureIcon.warm },
  { value: "cold", label: "Fríos", icon: temperatureIcon.cold },
] as const;

function LeadCard({ item }: { item: Lead }) {
  const hasPhone = !!item.phone_e164;

  function call() {
    if (!item.phone_e164) return;
    void Linking.openURL(`tel:${item.phone_e164}`);
  }

  function whatsapp() {
    if (!item.phone_e164) return;
    const number = item.phone_e164.replace(/[^\d]/g, "");
    void Linking.openURL(`https://wa.me/${number}`);
  }

  return (
    <Pressable
      style={({ pressed }) => [styles.leadCard, pressed ? { opacity: 0.9 } : null]}
      onPress={() => {
        tapHaptic();
        router.push(`/lead/${item.id}`);
      }}
      accessibilityRole="button"
    >
      <Avatar name={item.full_name} size={44} />
      <View style={styles.leadBody}>
        <View style={styles.leadTitleRow}>
          <Text style={styles.leadName} numberOfLines={1}>
            {item.full_name || "Sin nombre"}
          </Text>
          <Pill
            label={temperatureLabel[item.temperature] || item.temperature}
            tone={temperatureTone[item.temperature] || "neutral"}
          />
        </View>
        <Text style={styles.leadDetail} numberOfLines={1}>
          {formatBudget(item.budget_min, item.budget_max)} · {formatOperation(item.operation)}
        </Text>
        <Text style={styles.leadScore}>Puntuación {Math.round(item.score ?? 0)}</Text>
      </View>
      <View style={styles.leadActions}>
        <IconCircleButton
          icon="call-outline"
          color={colors.primary}
          disabled={!hasPhone}
          onPress={call}
          accessibilityLabel={`Llamar a ${item.full_name || "lead"}`}
        />
        <IconCircleButton
          icon="logo-whatsapp"
          color={colors.whatsapp}
          disabled={!hasPhone}
          onPress={whatsapp}
          accessibilityLabel={`Abrir WhatsApp con ${item.full_name || "lead"}`}
        />
      </View>
    </Pressable>
  );
}

export default function LeadsScreen() {
  const { me } = useAgency();
  const agencyId = me?.agency_id;
  const [leads, setLeads] = useState<Lead[] | null>(null);
  const [temperature, setTemperature] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(false);

  const load = useCallback(
    async (opts: { silent?: boolean } = {}) => {
      if (!agencyId) return;
      if (!opts.silent) setLeads(null);
      setError(false);
      try {
        const data = await fetchLeads(agencyId, temperature || undefined);
        setLeads(data);
      } catch {
        setError(true);
      }
    },
    [agencyId, temperature]
  );

  useEffect(() => {
    void load();
  }, [load]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await load({ silent: true });
    setRefreshing(false);
  }, [load]);

  const sorted = useMemo(
    () => (leads ? [...leads].sort((a, b) => (b.score ?? 0) - (a.score ?? 0)) : null),
    [leads]
  );

  const filters = (
    <View style={styles.filtersWrap}>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.filters}
      >
        {TEMP_FILTERS.map((f) => (
          <Chip
            key={f.value}
            label={f.label}
            icon={f.icon}
            active={temperature === f.value}
            onPress={() => setTemperature(f.value)}
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

  if (sorted === null) {
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
        data={sorted}
        keyExtractor={(l) => l.id}
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
          sorted.length === 0 ? { flexGrow: 1 } : null,
        ]}
        ListEmptyComponent={
          <EmptyView
            icon="people-outline"
            title="Aún no hay leads"
            subtitle="En cuanto la IA cualifique contactos, los verás aquí ordenados por puntuación."
          />
        }
        renderItem={({ item }) => <LeadCard item={item} />}
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
  leadCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(3),
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    padding: spacing(4),
    ...shadow.card,
  },
  leadBody: { flex: 1, gap: 2 },
  leadTitleRow: { flexDirection: "row", alignItems: "center", gap: spacing(2) },
  leadName: { ...type.body, fontWeight: "600", flexShrink: 1 },
  leadDetail: { ...type.meta, fontWeight: "400" },
  leadScore: { ...type.meta, fontSize: 12 },
  leadActions: { flexDirection: "row", gap: spacing(2) },
});
