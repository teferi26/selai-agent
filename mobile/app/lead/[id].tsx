import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Linking,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { router, useLocalSearchParams } from "expo-router";
import { ApiError, fetchLead, Lead, requestOutboundCall } from "@/lib/api";
import { useAgency } from "@/context/AgencyContext";
import {
  Avatar,
  EmptyView,
  ErrorView,
  Pill,
  PrimaryButton,
  SecondaryButton,
  Skeleton,
} from "@/components/ui";
import { formatBudget, formatDateTime, formatOperation } from "@/lib/format";
import { successHaptic } from "@/lib/haptics";
import {
  colors,
  radius,
  shadow,
  spacing,
  temperatureLabel,
  temperatureTone,
  type,
} from "@/lib/theme";

const STATUS_LABEL: Record<string, string> = {
  new: "Nuevo",
  contacted: "Contactado",
  qualified: "Cualificado",
  visit_scheduled: "Visita agendada",
  won: "Ganado",
  lost: "Perdido",
};

const LANGUAGE_LABEL: Record<string, string> = {
  es: "Español",
  en: "Inglés",
  fr: "Francés",
  de: "Alemán",
  nl: "Neerlandés",
  it: "Italiano",
  pt: "Portugués",
};

function FieldRow({ label, value, last }: { label: string; value: string; last?: boolean }) {
  return (
    <View style={[styles.fieldRow, last ? styles.fieldRowLast : null]}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <Text style={styles.fieldValue}>{value}</Text>
    </View>
  );
}

export default function LeadDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { me } = useAgency();
  const agencyId = me?.agency_id;

  const [lead, setLead] = useState<Lead | null | undefined>(undefined);
  const [error, setError] = useState(false);
  const [calling, setCalling] = useState(false);

  const load = useCallback(async () => {
    if (!agencyId || !id) return;
    setLead(undefined);
    setError(false);
    try {
      setLead(await fetchLead(agencyId, id));
    } catch (e) {
      // 404 es una respuesta, no un fallo: el lead no existe o no es de esta
      // agencia. Cualquier otra cosa (red, 500) sí es un error con reintento.
      if (e instanceof ApiError && e.status === 404) setLead(null);
      else setError(true);
    }
  }, [agencyId, id]);

  useEffect(() => {
    void load();
  }, [load]);

  function callMyself() {
    if (!lead?.phone_e164) return;
    void Linking.openURL(`tel:${lead.phone_e164}`);
  }

  function openWhatsApp() {
    if (!lead?.phone_e164) return;
    const number = lead.phone_e164.replace(/[^\d]/g, "");
    void Linking.openURL(`https://wa.me/${number}`);
  }

  function aiCall() {
    if (!agencyId || !lead) return;
    Alert.alert(
      "Llamada de la IA",
      `El agente IA llamará ahora a ${lead.full_name || "este lead"} (${lead.phone_e164 || "sin teléfono"}). ¿Continuar?`,
      [
        { text: "Cancelar", style: "cancel" },
        {
          text: "Llamar",
          onPress: async () => {
            setCalling(true);
            try {
              await requestOutboundCall(agencyId, lead.id);
              successHaptic();
              Alert.alert(
                "Llamada iniciada",
                "La IA está llamando al lead. Verás el resultado en la bandeja."
              );
            } catch {
              Alert.alert("Error", "No hemos podido iniciar la llamada. Inténtalo de nuevo.");
            } finally {
              setCalling(false);
            }
          },
        },
      ]
    );
  }

  if (error) {
    return (
      <ErrorView
        onRetry={() => void load()}
        exitLabel="Volver a leads"
        onExit={() => router.back()}
      />
    );
  }
  if (lead === undefined) {
    return (
      <View style={styles.screen}>
        <View style={styles.content}>
          <View style={styles.header}>
            <Skeleton width={56} height={56} round />
            <View style={{ flex: 1, gap: spacing(2) }}>
              <Skeleton width={"60%"} height={18} />
              <Skeleton width={"30%"} height={14} />
            </View>
          </View>
          <View style={[styles.card, { padding: spacing(4), gap: spacing(3) }]}>
            {Array.from({ length: 6 }, (_, i) => (
              <Skeleton key={i} height={14} width={i % 2 === 0 ? "85%" : "65%"} />
            ))}
          </View>
        </View>
      </View>
    );
  }
  if (lead === null) {
    return (
      <EmptyView
        icon="search-outline"
        title="Lead no encontrado"
        subtitle="Puede que se haya eliminado o que pertenezca a otra agencia."
        ctaLabel="Volver a leads"
        onCta={() => router.back()}
      />
    );
  }

  const hasPhone = !!lead.phone_e164;

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <Avatar name={lead.full_name} size={56} />
        <View style={{ flex: 1, gap: spacing(1) }}>
          <Text style={styles.name}>{lead.full_name || "Sin nombre"}</Text>
          <View style={styles.headerMeta}>
            <Pill
              label={temperatureLabel[lead.temperature] || lead.temperature}
              tone={temperatureTone[lead.temperature] || "neutral"}
            />
            <Text style={styles.score}>Puntuación {Math.round(lead.score ?? 0)}</Text>
          </View>
        </View>
      </View>

      <View style={styles.card}>
        <FieldRow label="Estado" value={STATUS_LABEL[lead.status] || lead.status} />
        <FieldRow label="Teléfono" value={lead.phone_e164 || "—"} />
        <FieldRow label="Email" value={lead.email || "—"} />
        <FieldRow
          label="Idioma"
          value={
            lead.language
              ? LANGUAGE_LABEL[lead.language.toLowerCase()] || lead.language
              : "—"
          }
        />
        <FieldRow label="Presupuesto" value={formatBudget(lead.budget_min, lead.budget_max)} />
        <FieldRow label="Operación" value={formatOperation(lead.operation)} />
        <FieldRow label="Creado" value={formatDateTime(lead.created_at)} last />
      </View>

      <View style={styles.actions}>
        <PrimaryButton
          label="Llamar desde mi teléfono"
          icon="call-outline"
          onPress={callMyself}
          disabled={!hasPhone}
        />
        <SecondaryButton
          label="Abrir WhatsApp"
          icon="logo-whatsapp"
          onPress={openWhatsApp}
          disabled={!hasPhone}
        />
        <SecondaryButton
          label={calling ? "Llamando..." : "Que le llame la IA"}
          icon="sparkles-outline"
          onPress={aiCall}
          disabled={!hasPhone || calling}
        />
        {!hasPhone && (
          <Text style={styles.noPhone}>Este lead no tiene teléfono registrado.</Text>
        )}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: spacing(4), paddingBottom: spacing(10) },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(4),
    marginBottom: spacing(4),
  },
  name: { ...type.title, fontSize: 22 },
  headerMeta: { flexDirection: "row", alignItems: "center", gap: spacing(2) },
  score: { ...type.meta },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    paddingHorizontal: spacing(4),
    marginBottom: spacing(5),
    ...shadow.card,
  },
  fieldRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: spacing(3),
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.hairline,
    gap: spacing(3),
  },
  fieldRowLast: { borderBottomWidth: 0 },
  fieldLabel: { ...type.meta },
  fieldValue: { ...type.body, fontWeight: "500", flexShrink: 1, textAlign: "right" },
  actions: { gap: spacing(2) },
  noPhone: {
    ...type.meta,
    fontWeight: "400",
    textAlign: "center",
    marginTop: spacing(1),
  },
});
