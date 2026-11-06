// Paso 8 · Tu CRM — avisos automáticos de verdad: GET/POST/DELETE de
// webhook-subscriptions y POST /{id}/test. La clave de firma se enseña UNA vez
// (el backend solo la devuelve al crear) con copiar y aviso.
import { useCallback, useEffect, useState } from "react";
import { Alert, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { ApiError, isTimeout } from "@/lib/api";
import {
  type LeaveReason,
  useSetupWizard,
  useStepNext,
  useStepSkip,
} from "@/context/SetupWizardContext";
import {
  WEBHOOK_EVENTS,
  WebhookSubscription,
  WebhookTestOut,
  createWebhookSubscription,
  deleteWebhookSubscription,
  fetchWebhookSubscriptions,
  testWebhookSubscription,
  webhookEventLabel,
} from "@/lib/setup-api";
import { PrimaryButton, SupportEmail } from "@/components/ui";
import {
  CopyButton,
  Field,
  HonestNote,
  Notice,
  Panel,
  ResultLine,
  SectionTitle,
  StepError,
  StepLead,
  StepSkeleton,
  Tabs,
} from "@/components/setup/kit";
import { successHaptic, tapHaptic, warningHaptic } from "@/lib/haptics";
import { colors, radius, spacing, type } from "@/lib/theme";

type TestState = { busy: boolean; result?: WebhookTestOut; failed?: boolean };

function SubscriptionRow({
  sub,
  test,
  onTest,
  onDelete,
}: {
  sub: WebhookSubscription;
  test: TestState | undefined;
  onTest: () => void;
  onDelete: () => void;
}) {
  return (
    <View style={styles.subRow}>
      <View style={styles.subHead}>
        <View style={{ flex: 1 }}>
          <Text style={styles.subUrl} numberOfLines={2}>
            {sub.target_url}
          </Text>
          <Text style={styles.subEvents}>
            {sub.events.map(webhookEventLabel).join(" · ")}
            {sub.is_active ? "" : " · en pausa"}
          </Text>
        </View>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Borrar estos avisos"
          hitSlop={8}
          onPress={onDelete}
          style={({ pressed }) => [styles.iconBtn, pressed ? { opacity: 0.8 } : null]}
        >
          <Ionicons name="trash-outline" size={18} color={colors.dangerInk} />
        </Pressable>
      </View>

      <Pressable
        accessibilityRole="button"
        disabled={test?.busy}
        onPress={onTest}
        style={({ pressed }) => [
          styles.testBtn,
          test?.busy ? { opacity: 0.5 } : null,
          pressed ? { opacity: 0.85 } : null,
        ]}
      >
        <Ionicons name="paper-plane-outline" size={15} color={colors.primary} />
        <Text style={styles.testBtnText}>
          {test?.busy ? "Enviando..." : "Enviar evento de prueba"}
        </Text>
      </Pressable>

      {test?.result ? (
        <ResultLine tone={test.result.delivered ? "success" : "warning"}>
          {test.result.delivered
            ? `Entregado. Tu sistema respondió ${test.result.status_code}.`
            : test.result.error === "blocked_target_url"
              ? "Esa dirección no es pública: apunta a una red interna y no podemos enviarle nada desde internet. Pídesela a quien lleve tu CRM en su forma pública (https://)."
              : `No se ha podido entregar${
                  test.result.status_code
                    ? ` (respondió ${test.result.status_code})`
                    : " (no contestó a tiempo)"
                }. Comprueba la dirección con quien lleve tu CRM.`}
        </ResultLine>
      ) : null}
      {test?.failed ? (
        <ResultLine tone="danger">
          No hemos podido enviar la prueba. Inténtalo de nuevo.
        </ResultLine>
      ) : null}
    </View>
  );
}

export default function StepCrm() {
  const { agencyId, markChecklist, doneMap } = useSetupWizard();

  const [tab, setTab] = useState("notify");
  const [subs, setSubs] = useState<WebhookSubscription[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [tests, setTests] = useState<Record<string, TestState>>({});

  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<string[]>(["lead.created"]);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [freshSecret, setFreshSecret] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!agencyId) return;
    setLoadError(false);
    setSubs(null);
    try {
      setSubs(await fetchWebhookSubscriptions(agencyId));
    } catch {
      setLoadError(true);
    }
  }, [agencyId]);

  useEffect(() => {
    void load();
  }, [load]);

  const create = useCallback(async () => {
    if (!agencyId || creating) return;
    if (!url.trim()) {
      setCreateError("Escribe la dirección de notificaciones de tu CRM.");
      return;
    }
    if (events.length === 0) {
      setCreateError("Elige al menos un aviso.");
      return;
    }
    setCreating(true);
    setCreateError(null);
    try {
      const created = await createWebhookSubscription(agencyId, {
        target_url: url.trim(),
        events,
      });
      successHaptic();
      setSubs((cur) => [...(cur ?? []), created]);
      setFreshSecret(created.secret_full_once);
      setUrl("");
      if (!doneMap.crm) await markChecklist({ crm: true }).catch(() => {});
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      setCreateError(
        // 422 lo devuelve la validación del propio campo (una dirección sin
        // https://, por ejemplo) y 400 el backend cuando la dirección no es
        // pública o el aviso no existe: al dueño le da igual la diferencia.
        status === 400 || status === 422
          ? "Esa dirección no vale: tiene que empezar por https:// y ser accesible desde internet (no una dirección de tu red local)."
          : isTimeout(e)
            ? "Está tardando demasiado. Comprueba tu conexión y vuelve a pulsar «Activar avisos»."
            : "No hemos podido activar los avisos. Comprueba tu conexión e inténtalo de nuevo."
      );
    } finally {
      setCreating(false);
    }
  }, [agencyId, creating, url, events, doneMap.crm, markChecklist]);

  const runTest = useCallback(
    async (subId: string) => {
      if (!agencyId) return;
      setTests((t) => ({ ...t, [subId]: { busy: true } }));
      try {
        const out = await testWebhookSubscription(agencyId, subId);
        if (out.delivered) successHaptic();
        setTests((t) => ({ ...t, [subId]: { busy: false, result: out } }));
      } catch {
        setTests((t) => ({ ...t, [subId]: { busy: false, failed: true } }));
      }
    },
    [agencyId]
  );

  /** La clave solo se enseña una vez (el backend no la devuelve nunca más).
   * Si sigue en pantalla al pulsar «Siguiente», preguntamos antes de perderla
   * para siempre. */
  const guardSecret = useCallback((reason: LeaveReason = "skip"): Promise<boolean> => {
    if (!freshSecret && !url.trim()) return Promise.resolve(true);
    warningHaptic();
    return new Promise((resolve) => {
      Alert.alert(
        freshSecret ? "Todavía no has guardado la clave" : "Tienes una dirección sin guardar",
        freshSecret
          ? "Es la única vez que se muestra. Si sales ahora y la necesitas, habrá que borrar estos avisos y crearlos otra vez."
          : "La dirección del CRM todavía no se ha activado. Si sales ahora, tendrás que volver a escribirla.",
        [
          {
            text: freshSecret ? "Volver a copiarla" : "Seguir aquí",
            style: "cancel",
            onPress: () => resolve(false),
          },
          {
            text:
              reason === "back"
                ? "Volver igualmente"
                : reason === "skip"
                  ? "Saltar igualmente"
                  : "Salir igualmente",
            style: "destructive",
            onPress: () => resolve(true),
          },
        ],
        // Sin salida silenciosa: si el aviso se cerrara sin respuesta, el
        // botón «Siguiente» se quedaría girando para siempre.
        { cancelable: false, onDismiss: () => resolve(false) }
      );
    });
  }, [freshSecret, url]);

  useStepNext(guardSecret);
  // La clave se pierde igual si se sale por «Saltar por ahora», así que el
  // mismo aviso cubre las dos salidas.
  useStepSkip(guardSecret);

  const confirmDelete = useCallback(
    (sub: WebhookSubscription) => {
      warningHaptic();
      Alert.alert(
        "Borrar estos avisos",
        `Tu CRM dejará de recibir los avisos que enviábamos a ${sub.target_url}. Puedes volver a crearlos, pero se generará una clave de firma nueva.`,
        [
          { text: "Cancelar", style: "cancel" },
          {
            text: "Borrar",
            style: "destructive",
            onPress: () => {
              if (!agencyId) return;
              void (async () => {
                try {
                  await deleteWebhookSubscription(agencyId, sub.id);
                  setSubs((cur) => (cur ?? []).filter((s) => s.id !== sub.id));
                } catch {
                  Alert.alert(
                    "No se ha podido borrar",
                    "Inténtalo de nuevo en unos segundos."
                  );
                }
              })();
            },
          },
        ]
      );
    },
    [agencyId]
  );

  return (
    <View style={{ gap: spacing(4) }}>
      <StepLead>
        Dos formas de que selai y tu programa de siempre trabajen juntos, y una
        tercera en camino. Este paso es opcional: si no usas CRM, sáltalo sin miedo.
      </StepLead>

      <Tabs
        items={[
          { key: "notify", label: "Avisos automáticos" },
          { key: "export", label: "Exportar leads" },
          { key: "native", label: "Integración nativa" },
        ]}
        value={tab}
        onChange={setTab}
      />

      {tab === "notify" ? (
        <>
          <Panel>
            <SectionTitle>Avisos automáticos a tu CRM</SectionTitle>
            <Text style={styles.body}>
              Si tu sistema puede recibir avisos por internet, dale aquí su dirección
              de notificaciones y le enviaremos cada novedad al instante: nuevo lead,
              visita agendada, oferta firmada.
            </Text>
          </Panel>

          {freshSecret ? (
            <Panel style={styles.secretPanel}>
              <SectionTitle>Copia ahora tu clave de firma</SectionTitle>
              <Text style={styles.body}>
                Con ella tu CRM comprueba que los avisos vienen de verdad de selai. No
                se volverá a mostrar: si la pierdes habrá que crear los avisos otra vez.
              </Text>
              <View style={styles.secretBox}>
                <Text style={styles.secretText} selectable>
                  {freshSecret}
                </Text>
              </View>
              <View style={styles.secretActions}>
                <CopyButton value={freshSecret} label="la clave de firma" />
                <Pressable
                  accessibilityRole="button"
                  onPress={() => {
                    tapHaptic();
                    setFreshSecret(null);
                  }}
                  style={({ pressed }) => [styles.hideBtn, pressed ? { opacity: 0.8 } : null]}
                >
                  <Text style={styles.hideBtnText}>Ya la he guardado</Text>
                </Pressable>
              </View>
              <HonestNote>
                Va en la cabecera X-Realtor-Signature de cada aviso (HMAC-SHA256 sobre
                el cuerpo). Pásasela a quien lleve tu CRM.
              </HonestNote>
            </Panel>
          ) : null}

          {loadError ? (
            <StepError
              message="No hemos podido cargar tus avisos automáticos."
              onRetry={() => void load()}
            />
          ) : subs === null ? (
            <StepSkeleton rows={1} />
          ) : subs.length > 0 ? (
            <Panel>
              <SectionTitle>Avisos activos</SectionTitle>
              {subs.map((sub) => (
                <SubscriptionRow
                  key={sub.id}
                  sub={sub}
                  test={tests[sub.id]}
                  onTest={() => void runTest(sub.id)}
                  onDelete={() => confirmDelete(sub)}
                />
              ))}
            </Panel>
          ) : null}

          <Panel>
            <SectionTitle>Añadir avisos</SectionTitle>
            <Field
              label="Dirección de notificaciones (URL)"
              value={url}
              onChangeText={setUrl}
              placeholder="https://tu-crm.es/avisos-selai"
              keyboardType="url"
              autoCapitalize="none"
              mono
              hint="Te la da quien lleve tu CRM. Tiene que ser accesible desde internet."
              maxLength={512}
            />
            <Text style={styles.fieldLabel}>¿Qué avisos quieres recibir?</Text>
            <View style={styles.eventsWrap}>
              {WEBHOOK_EVENTS.map((ev) => {
                const active = events.includes(ev.key);
                return (
                  <Pressable
                    key={ev.key}
                    accessibilityRole="checkbox"
                    accessibilityState={{ checked: active }}
                    onPress={() => {
                      tapHaptic();
                      setEvents((cur) =>
                        cur.includes(ev.key)
                          ? cur.filter((k) => k !== ev.key)
                          : [...cur, ev.key]
                      );
                    }}
                    style={({ pressed }) => [
                      styles.eventChip,
                      active ? styles.eventChipActive : null,
                      pressed ? { opacity: 0.85 } : null,
                    ]}
                  >
                    <Ionicons
                      name={active ? "checkmark-circle" : "ellipse-outline"}
                      size={15}
                      color={active ? colors.white : colors.inkSecondary}
                    />
                    <Text
                      style={[styles.eventChipText, active ? styles.eventChipTextActive : null]}
                    >
                      {ev.label}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
            {createError ? <Notice tone="danger">{createError}</Notice> : null}
            <PrimaryButton
              label={creating ? "Activando..." : "Activar avisos"}
              icon="notifications-outline"
              loading={creating}
              onPress={() => void create()}
            />
          </Panel>
        </>
      ) : null}

      {tab === "export" ? (
        <Panel>
          <SectionTitle>Exportar leads</SectionTitle>
          <Text style={styles.body}>
            Todos los leads que capta tu asistente (con teléfono, presupuesto, zona e
            interés) se pueden descargar en un archivo CSV que tu CRM importa
            directamente.
          </Text>
          <Notice tone="info" icon="desktop-outline">
            La exportación completa se hace desde el panel web, en Leads → Exportar
            CSV. Desde el móvil no la tenemos: manejar un archivo así en el teléfono no
            sirve de mucho.
          </Notice>
        </Panel>
      ) : null}

      {tab === "native" ? (
        <Panel>
          <SectionTitle>Integración nativa</SectionTitle>
          <Text style={styles.body}>
            Estamos construyendo la integración nativa con Inmovilla y Witei. Todavía
            no está disponible: está en camino y no queremos venderte humo. Mientras
            llega, los avisos automáticos y la exportación cubren el día a día.
          </Text>
          <HonestNote>
            ¿Usas otro CRM? Escríbenos a{" "}
            <SupportEmail context="Puesta en marcha · Paso 8 (CRM)" /> y lo
            tendremos en cuenta.
          </HonestNote>
        </Panel>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  body: { ...type.body, lineHeight: 22 },
  fieldLabel: { ...type.meta, color: colors.ink, fontSize: 14, fontWeight: "600" },

  subRow: {
    gap: spacing(2),
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.hairline,
    paddingTop: spacing(3),
  },
  subHead: { flexDirection: "row", alignItems: "flex-start", gap: spacing(2) },
  subUrl: { ...type.mono, fontSize: 12 },
  subEvents: { ...type.meta, fontWeight: "400", fontSize: 12, marginTop: 2 },
  iconBtn: {
    width: 36,
    height: 36,
    borderRadius: radius.small,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.dangerSoft,
  },
  testBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing(2),
    paddingVertical: spacing(2),
    borderRadius: radius.small,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    alignSelf: "flex-start",
    paddingHorizontal: spacing(3),
  },
  testBtnText: { fontSize: 13, fontWeight: "600", color: colors.primary },

  secretPanel: { backgroundColor: colors.warningSoft, borderColor: colors.warning },
  secretBox: {
    backgroundColor: colors.ink,
    borderRadius: radius.small,
    padding: spacing(3),
  },
  secretText: { ...type.mono, color: colors.white, fontSize: 12, lineHeight: 18 },
  secretActions: { flexDirection: "row", alignItems: "center", gap: spacing(2) },
  hideBtn: { paddingVertical: spacing(2), paddingHorizontal: spacing(2) },
  hideBtnText: { fontSize: 13, fontWeight: "600", color: colors.warningInk },

  eventsWrap: { flexDirection: "row", flexWrap: "wrap", gap: spacing(2) },
  eventChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(1),
    paddingHorizontal: spacing(3),
    paddingVertical: spacing(2),
    borderRadius: radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    backgroundColor: colors.surface,
  },
  eventChipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  eventChipText: { fontSize: 13, fontWeight: "500", color: colors.inkSecondary },
  eventChipTextActive: { color: colors.white },
});
