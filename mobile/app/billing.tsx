// Plan y facturación en el móvil.
//
// Informa, no cobra. El checkout de Stripe vive en el panel web y montar aquí
// una pasarela a medias sería lo peor de los dos mundos, así que el botón
// abre el panel en el navegador del teléfono — que es donde el cliente ya
// tiene la sesión y donde Stripe puede hacer su trabajo.
//
// Lo que sí hace de verdad: decir en qué estado está la suscripción y avisar
// sin rodeos cuando ese estado significa que la IA ha dejado de atender.
import { useCallback, useState } from "react";
import {
  Alert,
  Linking,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { router } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { useAgency } from "@/context/AgencyContext";
import { SupportModal } from "@/components/support";
import { ErrorView, LoadingView, PrimaryButton, SecondaryButton } from "@/components/ui";
import {
  PANEL_BILLING_URL,
  statusLabel,
  statusNotice,
  trialDaysLeft,
  trialSentence,
  USAGE_LABEL,
} from "@/lib/billing";
import { formatDateTime } from "@/lib/format";
import { colors, radius, shadow, spacing, type } from "@/lib/theme";

function Row({ label, value, last }: { label: string; value: string; last?: boolean }) {
  return (
    <View style={[styles.row, last ? styles.rowLast : null]}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Text style={styles.rowValue}>{value}</Text>
    </View>
  );
}

/** Barra de consumo. Sin límite conocido (0 o negativo) no se pinta barra:
 * una barra al 100% cuando en realidad no hay tope asusta para nada. */
function UsageBar({ used, limit }: { used: number; limit: number }) {
  if (!limit || limit <= 0) return null;
  const ratio = Math.min(used / limit, 1);
  const over = used >= limit;
  return (
    <View style={styles.usageTrack}>
      <View
        style={[
          styles.usageFill,
          { width: `${Math.max(ratio * 100, 2)}%` },
          over ? { backgroundColor: colors.danger } : null,
        ]}
      />
    </View>
  );
}

export default function BillingScreen() {
  const { me, status, bootstrap } = useAgency();
  const [refreshing, setRefreshing] = useState(false);
  const [reporting, setReporting] = useState(false);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await bootstrap();
    setRefreshing(false);
  }, [bootstrap]);

  const openPanel = useCallback(() => {
    void Linking.openURL(PANEL_BILLING_URL).catch(() => {
      Alert.alert(
        "No hemos podido abrir el panel",
        `Entra desde el navegador en ${PANEL_BILLING_URL} para gestionar tu plan.`
      );
    });
  }, []);

  if (status === "loading" || status === "idle") {
    return <LoadingView label="Cargando tu plan..." />;
  }
  if (!me) {
    return (
      <ErrorView
        title="No hemos podido cargar tu plan"
        onRetry={() => void bootstrap()}
        exitLabel="Volver"
        onExit={() => router.back()}
      />
    );
  }

  const notice = statusNotice(me.subscription_status);
  const isTrial = me.subscription_status === "trialing";
  const days = trialDaysLeft(me.trial_ends_at);
  const trial = isTrial ? trialSentence(days) : null;
  const noticeTone = notice?.tone === "danger" ? colors.dangerInk : colors.warningInk;
  const noticeBg = notice?.tone === "danger" ? colors.dangerSoft : colors.warningSoft;

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
      {notice ? (
        <View style={[styles.notice, { backgroundColor: noticeBg }]}>
          <Ionicons
            name={notice.tone === "danger" ? "alert-circle-outline" : "warning-outline"}
            size={20}
            color={noticeTone}
          />
          <View style={{ flex: 1, gap: 2 }}>
            <Text style={[styles.noticeTitle, { color: noticeTone }]}>{notice.title}</Text>
            <Text style={[styles.noticeBody, { color: noticeTone }]}>{notice.body}</Text>
          </View>
        </View>
      ) : null}

      <View style={styles.card}>
        <Row label="Inmobiliaria" value={me.agency_name} />
        <Row label="Plan" value={me.plan_name || me.plan_code || "Sin plan"} />
        <Row label="Estado" value={statusLabel(me.subscription_status)} />
        {me.monthly_price_eur !== null ? (
          <Row label="Precio" value={`${me.monthly_price_eur} € al mes`} />
        ) : null}
        {isTrial ? (
          <Row
            label="Fin de la prueba"
            value={me.trial_ends_at ? formatDateTime(me.trial_ends_at) : "—"}
            last
          />
        ) : null}
      </View>

      {trial ? (
        <View style={styles.trialCard}>
          <Ionicons
            name={days !== null && days <= 3 ? "time-outline" : "gift-outline"}
            size={20}
            color={days !== null && days <= 3 ? colors.warningInk : colors.primary}
          />
          <Text style={styles.trialText}>
            {trial}
            {days !== null && days > 0
              ? " Cuando termine, si no hay plan contratado la IA deja de atender."
              : " Contrata un plan para que la IA vuelva a atender."}
          </Text>
        </View>
      ) : null}

      {me.usage.length > 0 ? (
        <>
          <Text style={styles.sectionTitle}>Consumo de tu plan</Text>
          <View style={styles.card}>
            {me.usage.map((u, i) => (
              <View
                key={u.resource}
                style={[
                  styles.usageRow,
                  i === me.usage.length - 1 ? styles.rowLast : null,
                ]}
              >
                <View style={styles.usageHead}>
                  <Text style={styles.rowLabel}>
                    {USAGE_LABEL[u.resource] || u.resource}
                  </Text>
                  <Text style={styles.rowValue}>
                    {u.limit > 0 ? `${u.used} de ${u.limit}` : String(u.used)}
                  </Text>
                </View>
                <UsageBar used={u.used} limit={u.limit} />
              </View>
            ))}
          </View>
        </>
      ) : null}

      <View style={styles.actions}>
        <PrimaryButton
          label="Gestionar mi plan en el panel"
          icon="open-outline"
          onPress={openPanel}
        />
        <Text style={styles.actionsHint}>
          El pago se hace en el panel web con Stripe. Se abrirá en el navegador
          del teléfono con tu misma cuenta.
        </Text>
        {/* El formulario, no un `mailto:` a pelo: envía por la API y solo cae
            al correo si eso falla. Un teléfono sin cuenta de correo configurada
            no puede quedarse sin forma de avisarnos justo en la pantalla del
            dinero. */}
        <SecondaryButton
          label="Escribir a soporte"
          icon="mail-outline"
          onPress={() => setReporting(true)}
        />
      </View>

      <SupportModal
        visible={reporting}
        onClose={() => setReporting(false)}
        screen="/billing"
      />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: spacing(4), paddingBottom: spacing(12), gap: spacing(4) },
  notice: {
    flexDirection: "row",
    gap: spacing(3),
    padding: spacing(4),
    borderRadius: radius.card,
  },
  noticeTitle: { ...type.heading, fontSize: 15 },
  noticeBody: { fontSize: 13, lineHeight: 19 },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    paddingHorizontal: spacing(4),
    ...shadow.card,
  },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: spacing(3),
    paddingVertical: spacing(3),
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.hairline,
  },
  rowLast: { borderBottomWidth: 0 },
  rowLabel: { ...type.meta },
  rowValue: { ...type.body, fontWeight: "500", flexShrink: 1, textAlign: "right" },
  usageRow: {
    paddingVertical: spacing(3),
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.hairline,
    gap: spacing(2),
  },
  usageHead: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: spacing(3),
  },
  usageTrack: {
    height: 6,
    borderRadius: 3,
    backgroundColor: colors.neutralSoft,
    overflow: "hidden",
  },
  usageFill: { height: 6, borderRadius: 3, backgroundColor: colors.primary },
  trialCard: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing(3),
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    padding: spacing(4),
    ...shadow.card,
  },
  trialText: { ...type.bodySecondary, fontSize: 14, lineHeight: 20, flexShrink: 1 },
  sectionTitle: {
    ...type.meta,
    textTransform: "uppercase",
    letterSpacing: 0.5,
    fontSize: 12,
    marginBottom: -spacing(2),
    marginLeft: spacing(1),
  },
  actions: { gap: spacing(2) },
  actionsHint: {
    ...type.meta,
    fontWeight: "400",
    fontSize: 12,
    lineHeight: 17,
    textAlign: "center",
  },
});
