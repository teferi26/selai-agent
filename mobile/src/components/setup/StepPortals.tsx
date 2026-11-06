// Paso 5 · Portales inmobiliarios — dirección única de leads copiable, la ruta
// de menús de cada portal y sondeo real de GET /channels cada 12 s mientras el
// paso está en pantalla: la llegada del primer aviso se detecta sola.
import { useCallback, useEffect, useRef, useState } from "react";
import { AppState, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useSetupWizard } from "@/context/SetupWizardContext";
import { PORTAL_GUIDES } from "@/lib/setup-api";
import { SecondaryButton, SupportEmail } from "@/components/ui";
import {
  CopyRow,
  HonestNote,
  Notice,
  NumberedList,
  Panel,
  ResultLine,
  SectionTitle,
  StepError,
  StepLead,
  StepSkeleton,
  Strong,
  Tabs,
} from "@/components/setup/kit";
import { timeAgo } from "@/lib/format";
import { successHaptic } from "@/lib/haptics";
import { colors, spacing, type } from "@/lib/theme";

/** Cada 12 s mientras el paso está abierto: la espera del primer lead es el
 * único momento muerto del asistente y detectarlo en vivo lo cambia todo. */
const POLL_MS = 12_000;

export default function StepPortals() {
  const {
    channels,
    channelsLoading,
    channelsError,
    refreshChannels,
    doneMap,
    markChecklist,
  } = useSetupWizard();

  const [tab, setTab] = useState(PORTAL_GUIDES[0].key);
  const [checking, setChecking] = useState(false);
  const [justArrived, setJustArrived] = useState(false);
  const markedRef = useRef(false);
  /** Cuántos avisos había cuando abrimos el paso, tomado del primer dato REAL
   * (no del estado inicial vacío): solo celebramos lo que llega mientras
   * miramos. Quien ya recibía leads no ha estrenado nada hoy. Misma regla que
   * el panel web (portalBaseline en dashboard SetupClient). */
  const baselineRef = useRef<number | null>(null);

  const received = channels?.email.received_count ?? 0;
  const lastAt = channels?.email.last_received_at ?? null;

  useEffect(() => {
    if (!channels) return;
    const count = channels.email.received_count;
    if (baselineRef.current === null) baselineRef.current = count;
    if (count > baselineRef.current && !justArrived) {
      setJustArrived(true);
      successHaptic();
    }
    // Misma clave que el panel web, para que el progreso cuente igual.
    if (count > 0 && !markedRef.current && !doneMap.portals) {
      markedRef.current = true;
      void markChecklist({ portals: true }).catch(() => {
        // Reintentaremos en el siguiente sondeo: el progreso no se pierde.
        markedRef.current = false;
      });
    }
  }, [channels, justArrived, doneMap.portals, markChecklist]);

  // Sondeo mientras el paso está EN PANTALLA y la app EN PRIMER PLANO. Si el
  // usuario se va a otra app (a mirar el correo del portal, precisamente), el
  // intervalo se para: nada de gastar datos y batería de fondo.
  useEffect(() => {
    if (received > 0) return;
    let timer: ReturnType<typeof setInterval> | null = null;
    const start = () => {
      if (timer === null) timer = setInterval(() => void refreshChannels(), POLL_MS);
    };
    const stop = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };
    if (AppState.currentState === "active") start();
    const sub = AppState.addEventListener("change", (state) => {
      if (state === "active") {
        void refreshChannels();
        start();
      } else {
        stop();
      }
    });
    return () => {
      stop();
      sub.remove();
    };
  }, [received, refreshChannels]);

  const checkNow = useCallback(async () => {
    setChecking(true);
    await refreshChannels();
    setChecking(false);
  }, [refreshChannels]);

  // Solo tapamos el paso si NO tenemos datos. Un sondeo que falla con la
  // dirección ya en pantalla no puede borrarle las instrucciones al usuario
  // mientras las está siguiendo: se avisa dentro de la comprobación en vivo.
  if (channelsError && !channels) {
    return (
      <View style={{ gap: spacing(4) }}>
        <StepLead>Haz que los avisos de los portales lleguen a tu asistente.</StepLead>
        <StepError
          message="No hemos podido consultar tu dirección de leads."
          onRetry={() => void refreshChannels()}
        />
      </View>
    );
  }

  if (channelsLoading && !channels) {
    return (
      <View style={{ gap: spacing(4) }}>
        <StepLead>Haz que los avisos de los portales lleguen a tu asistente.</StepLead>
        <StepSkeleton rows={2} />
      </View>
    );
  }

  const leadsEmail = channels?.email.address ?? "";
  const guide = PORTAL_GUIDES.find((p) => p.key === tab) ?? PORTAL_GUIDES[0];

  return (
    <View style={{ gap: spacing(4) }}>
      <StepLead>
        Cuando alguien pregunta por un piso en Idealista, Fotocasa o Habitaclia, el
        portal te avisa por email. Haz que ese aviso llegue también a tu asistente y
        responderá al momento.
      </StepLead>

      <Panel>
        <SectionTitle>Tu dirección única para leads</SectionTitle>
        <CopyRow value={leadsEmail} label="dirección de leads" />
        <HonestNote>
          Es tuya y solo tuya. Todo lo que llegue aquí lo lee tu asistente y contesta
          en tu nombre.
        </HonestNote>
      </Panel>

      <Panel>
        <SectionTitle>Dónde ponerla en cada portal</SectionTitle>
        <Tabs
          items={PORTAL_GUIDES.map((p) => ({ key: p.key, label: p.name }))}
          value={tab}
          onChange={setTab}
        />
        <NumberedList
          items={[
            ...guide.path,
            <Text key="paste" style={styles.body}>
              Pega ahí <Strong>{leadsEmail}</Strong> y guarda.
            </Text>,
          ]}
        />
        <HonestNote>{guide.note}</HonestNote>
      </Panel>

      <Panel>
        <SectionTitle>¿El portal solo admite un email?</SectionTitle>
        <Text style={styles.body}>
          Deja el tuyo de siempre y crea en tu correo una regla de reenvío automático
          de esos avisos a tu dirección de leads. En Gmail: Ajustes → Reenvío y correo
          POP/IMAP. Recibirás el aviso igual que hasta ahora y tu asistente también.
        </Text>
      </Panel>

      <Panel>
        <SectionTitle>Comprobación en vivo</SectionTitle>
        {received > 0 ? (
          <Notice
            tone="success"
            icon="mail-open-outline"
            title={justArrived ? "¡Ha llegado tu primer lead de portal!" : "Ya recibes leads de portales"}
          >
            {`${received} ${received === 1 ? "aviso recibido" : "avisos recibidos"}${
              lastAt ? ` · el último ${timeAgo(lastAt)}` : ""
            }. Tu asistente ya ha contestado: míralo en tu bandeja.`}
          </Notice>
        ) : (
          <>
            <Text style={styles.body}>
              Cuando lo tengas puesto, envía un lead de prueba desde el portal (o
              reenvía a mano un aviso antiguo). Lo detectamos solos: puedes dejar esta
              pantalla abierta.
            </Text>
            <View style={styles.waitingRow}>
              <View style={styles.waitingDot} />
              <Text style={styles.waitingText}>Esperando el primer aviso...</Text>
            </View>
            <SecondaryButton
              label={checking ? "Comprobando..." : "Comprobar ahora"}
              icon="refresh-outline"
              disabled={checking}
              onPress={() => void checkNow()}
            />
            {channelsError ? (
              <ResultLine tone="warning">
                La última comprobación no ha llegado al servidor. Seguimos
                intentándolo; también puedes pulsar «Comprobar ahora».
              </ResultLine>
            ) : null}
            <HonestNote>
              Los portales tardan unos minutos en enviar el aviso. Si no llega, puedes
              saltar este paso y volver cuando lo hayas configurado.
            </HonestNote>
          </>
        )}
      </Panel>

      <View style={styles.footNote}>
        <Ionicons name="information-circle-outline" size={16} color={colors.inkSecondary} />
        <Text style={styles.footNoteText}>
          Los portales rediseñan sus paneles de vez en cuando. Si no encuentras la
          opción donde te decimos, busca «email de contacto» o «recepción de leads» en
          la configuración de tu cuenta, o escríbenos a{" "}
          <SupportEmail context="Puesta en marcha · Paso 5 (portales)" />.
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  body: { ...type.body, lineHeight: 22 },
  waitingRow: { flexDirection: "row", alignItems: "center", gap: spacing(2) },
  waitingDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: colors.primary,
  },
  waitingText: { ...type.meta },
  footNote: { flexDirection: "row", gap: spacing(2), paddingHorizontal: spacing(1) },
  footNoteText: {
    ...type.meta,
    fontWeight: "400",
    fontSize: 12,
    lineHeight: 18,
    flex: 1,
  },
});
