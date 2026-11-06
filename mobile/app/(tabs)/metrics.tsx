import { useCallback, useEffect, useState } from "react";
import {
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { fetchKpis, Kpis } from "@/lib/api";
import { useAgency } from "@/context/AgencyContext";
import { ErrorView, IconName, SkeletonMetrics } from "@/components/ui";
import { formatDuration } from "@/lib/format";
import { colors, radius, shadow, spacing, type } from "@/lib/theme";

type MetricDef = {
  key: "leads_new" | "leads_qualified" | "visits_scheduled" | "visits_completed" | "handoffs";
  label: string;
  icon: IconName;
  /** Whether an increase should read as positive (green). */
  upIsGood: boolean | null;
};

const METRICS: MetricDef[] = [
  { key: "leads_new", label: "Leads nuevos", icon: "person-add-outline", upIsGood: true },
  { key: "leads_qualified", label: "Leads cualificados", icon: "checkmark-circle-outline", upIsGood: true },
  { key: "visits_scheduled", label: "Visitas agendadas", icon: "calendar-outline", upIsGood: true },
  { key: "visits_completed", label: "Visitas completadas", icon: "home-outline", upIsGood: true },
  { key: "handoffs", label: "Traspasos a humano", icon: "hand-left-outline", upIsGood: null },
];

function DeltaRow({ delta, upIsGood }: { delta: number | null; upIsGood: boolean | null }) {
  if (delta === null) {
    return <Text style={styles.deltaMuted}>Sin datos del periodo anterior</Text>;
  }
  if (delta === 0) {
    return <Text style={styles.deltaMuted}>Sin cambios vs. 30 días anteriores</Text>;
  }
  const up = delta > 0;
  const color =
    upIsGood === null
      ? colors.inkSecondary
      : up === upIsGood
        ? colors.successInk
        : colors.dangerInk;
  return (
    <View style={styles.deltaRow}>
      <Ionicons name={up ? "trending-up-outline" : "trending-down-outline"} size={13} color={color} />
      <Text style={[styles.deltaText, { color }]}>
        {up ? "+" : ""}
        {delta} vs. 30 días anteriores
      </Text>
    </View>
  );
}

function MetricCard({
  icon,
  label,
  value,
  delta,
  upIsGood,
}: {
  icon: IconName;
  label: string;
  value: string;
  delta: number | null;
  upIsGood: boolean | null;
}) {
  return (
    <View style={styles.card}>
      <View style={styles.cardIconWrap}>
        <Ionicons name={icon} size={17} color={colors.primary} />
      </View>
      <Text style={styles.cardValue}>{value}</Text>
      <Text style={styles.cardLabel}>{label}</Text>
      <DeltaRow delta={delta} upIsGood={upIsGood} />
    </View>
  );
}

export default function MetricsScreen() {
  const { me } = useAgency();
  const agencyId = me?.agency_id;
  const [kpis, setKpis] = useState<Kpis | null>(null);
  const [previous, setPrevious] = useState<Kpis | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(false);

  const load = useCallback(
    async (opts: { silent?: boolean } = {}) => {
      if (!agencyId) return;
      if (!opts.silent) setKpis(null);
      setError(false);
      try {
        // Two windows: last 30 days and last 60. The difference gives the
        // previous 30-day period, so deltas are real numbers, never invented.
        const [k30, k60] = await Promise.all([
          fetchKpis(agencyId, 30),
          fetchKpis(agencyId, 60).catch(() => null),
        ]);
        setKpis(k30);
        setPrevious(k60);
      } catch {
        setError(true);
      }
    },
    [agencyId]
  );

  useEffect(() => {
    void load();
  }, [load]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await load({ silent: true });
    setRefreshing(false);
  }, [load]);

  if (error) return <ErrorView onRetry={() => void load()} />;
  if (kpis === null) {
    return (
      <View style={styles.screen}>
        <SkeletonMetrics />
      </View>
    );
  }

  /** current - (window60 - window30) = current vs previous 30-day period. */
  function deltaOf(key: MetricDef["key"]): number | null {
    if (!previous || !kpis) return null;
    const cur = kpis[key];
    const prevWindow = previous[key];
    if (typeof cur !== "number" || typeof prevWindow !== "number") return null;
    return cur - (prevWindow - cur);
  }

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={onRefresh}
          tintColor={colors.primary}
          colors={[colors.primary]}
        />
      }
    >
      <Text style={styles.heading}>{me?.agency_name || "Tu agencia"}</Text>
      <Text style={styles.subheading}>Últimos 30 días</Text>
      <View style={styles.grid}>
        {METRICS.map((m) => (
          <MetricCard
            key={m.key}
            icon={m.icon}
            label={m.label}
            value={String(kpis[m.key])}
            delta={deltaOf(m.key)}
            upIsGood={m.upIsGood}
          />
        ))}
        <View style={styles.card}>
          <View style={styles.cardIconWrap}>
            <Ionicons name="flash-outline" size={17} color={colors.primary} />
          </View>
          <Text style={styles.cardValue}>
            {formatDuration(kpis.avg_first_response_seconds)}
          </Text>
          <Text style={styles.cardLabel}>Primera respuesta (media)</Text>
          <Text style={styles.deltaMuted}>Tiempo medio de la IA</Text>
        </View>
      </View>
      <View style={styles.totalRow}>
        <Ionicons name="people-outline" size={15} color={colors.inkSecondary} />
        <Text style={styles.totalText}>Total de leads: {kpis.leads_total}</Text>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: spacing(4), paddingBottom: spacing(10) },
  heading: { ...type.title },
  subheading: { ...type.meta, marginTop: 2, marginBottom: spacing(4) },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: spacing(3) },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    padding: spacing(4),
    width: "47%",
    flexGrow: 1,
    ...shadow.card,
  },
  cardIconWrap: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: colors.primarySoft,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: spacing(3),
  },
  cardValue: { ...type.metric },
  cardLabel: { ...type.meta, marginTop: 2 },
  deltaRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(1),
    marginTop: spacing(2),
  },
  deltaText: { fontSize: 12, fontWeight: "500" },
  deltaMuted: {
    ...type.meta,
    fontWeight: "400",
    fontSize: 12,
    marginTop: spacing(2),
  },
  totalRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing(1),
    marginTop: spacing(5),
  },
  totalText: { ...type.meta, fontWeight: "400" },
});
