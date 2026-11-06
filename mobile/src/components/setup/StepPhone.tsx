// Paso 3 · El teléfono de tu IA — POST /channels/phone de verdad, con los tres
// estados que devuelve el backend (active / requested / error) y los códigos de
// desvío copiables una vez hay número.
import { useCallback, useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { isTimeout } from "@/lib/api";
import { useSetupWizard } from "@/context/SetupWizardContext";
import { provisionPhone } from "@/lib/setup-api";
import { PrimaryButton, SecondaryButton, withSupportEmail } from "@/components/ui";
import {
  CheckRow,
  CopyRow,
  HonestNote,
  Notice,
  NoticeText,
  Panel,
  ResultLine,
  SectionTitle,
  StepError,
  StepLead,
  StepSkeleton,
} from "@/components/setup/kit";
import { successHaptic } from "@/lib/haptics";
// Interpolada, no escrita a mano: `withSupportEmail` parte el texto por esta
// misma constante para volver la dirección pulsable.
import { SUPPORT_EMAIL } from "@/lib/support";
import { colors, radius, spacing, type } from "@/lib/theme";

export default function StepPhone() {
  const {
    agencyId,
    channels,
    channelsLoading,
    channelsError,
    refreshChannels,
    refreshSettings,
    checklist,
    markChecklist,
  } = useSetupWizard();

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forwardingBusy, setForwardingBusy] = useState(false);
  const [forwardingError, setForwardingError] = useState(false);
  const [checking, setChecking] = useState(false);
  const [checkedStill, setCheckedStill] = useState(false);

  const activate = useCallback(async () => {
    if (!agencyId || busy) return;
    setBusy(true);
    setError(null);
    try {
      const out = await provisionPhone(agencyId);
      await Promise.all([refreshChannels(), refreshSettings()]);
      if (out.status === "active") successHaptic();
    } catch (e) {
      setError(
        isTimeout(e)
          ? "Está tardando más de lo normal y no sabemos si ha llegado a activarse. Pulsa «Comprobar si ya está activo» dentro de un minuto antes de volver a intentarlo."
          : `No hemos podido activar el número ahora mismo. Inténtalo otra vez en un minuto; si sigue fallando, escríbenos a ${SUPPORT_EMAIL}.`
      );
    } finally {
      setBusy(false);
    }
  }, [agencyId, busy, refreshChannels, refreshSettings]);

  /** El estado que pinta este paso sale de /channels, pero el progreso del
   * asistente se cuenta con `ai_phone_number` de /settings: hay que refrescar
   * los dos o la barra de arriba se queda con el número sin ver. */
  const checkPhone = useCallback(async () => {
    if (checking) return;
    setChecking(true);
    setCheckedStill(false);
    await Promise.all([refreshChannels(), refreshSettings()]);
    setChecking(false);
    setCheckedStill(true);
  }, [checking, refreshChannels, refreshSettings]);

  const toggleForwarding = useCallback(async () => {
    if (forwardingBusy) return;
    setForwardingBusy(true);
    setForwardingError(false);
    try {
      await markChecklist({ call_forwarding: !checklist["call_forwarding"] });
    } catch {
      // El estado real lo manda el servidor: si falla, la casilla no cambia,
      // y hay que decirlo o el usuario cree que la app no responde.
      setForwardingError(true);
    } finally {
      setForwardingBusy(false);
    }
  }, [forwardingBusy, checklist, markChecklist]);

  if (channelsError && !channels) {
    return (
      <View style={{ gap: spacing(4) }}>
        <StepLead>Tu asistente atiende llamadas en su propio número, 24 horas.</StepLead>
        <StepError
          message="No hemos podido consultar el estado de tu teléfono."
          onRetry={() => void refreshChannels()}
        />
      </View>
    );
  }

  if (channelsLoading && !channels) {
    return (
      <View style={{ gap: spacing(4) }}>
        <StepLead>Tu asistente atiende llamadas en su propio número, 24 horas.</StepLead>
        <StepSkeleton rows={2} />
      </View>
    );
  }

  const status = channels?.phone.status ?? "none";
  const number = channels?.phone.number ?? null;

  return (
    <View style={{ gap: spacing(4) }}>
      <StepLead>
        Tu asistente atiende llamadas en su propio número, 24 horas al día. Tú sigues
        usando el teléfono de siempre.
      </StepLead>

      {status === "active" && number ? (
        <Panel>
          <View style={styles.numberRow}>
            <View style={styles.numberIcon}>
              <Ionicons name="call" size={18} color={colors.successInk} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.numberLabel}>Tu número está activo</Text>
              <Text style={styles.number} selectable>
                {number}
              </Text>
            </View>
          </View>
          <CopyRow value={number} />
          <HonestNote>
            Llámalo ahora mismo y saluda a tu asistente. En el último paso lo probarás
            a fondo por chat.
          </HonestNote>
        </Panel>
      ) : null}

      {status === "requested" ? (
        <Notice tone="warning" title="Solicitado: te lo activamos en menos de 24 h">
          No había ningún número libre para darte al instante, así que lo hemos pedido
          por ti. Te avisaremos por email en cuanto esté listo. Mientras tanto puedes
          seguir con los demás pasos: nada de esto depende del teléfono.
        </Notice>
      ) : null}

      {status === "none" ? (
        <Panel>
          <SectionTitle>Activar el número</SectionTitle>
          <Text style={styles.body}>
            Pulsa el botón y te asignamos uno al momento si hay disponibles. Si no,
            lo solicitamos por ti y te lo activamos en menos de 24 horas, sin que
            tengas que hacer nada más.
          </Text>
          <Notice tone="warning" icon="globe-outline">
            Los números que se activan al instante son de Estados Unidos (empiezan por
            +1). Si necesitas un número español, lo pedimos a mano y tarda algo más.
          </Notice>
          <PrimaryButton
            label={busy ? "Activando..." : "Activar número"}
            icon="call-outline"
            loading={busy}
            onPress={() => void activate()}
          />
        </Panel>
      ) : null}

      {error ? (
        <View style={{ gap: spacing(3) }}>
          <Notice tone="danger" title="No se ha podido activar">
            <NoticeText tone="danger">
              {withSupportEmail(error, "Puesta en marcha · Paso 3 (teléfono)")}
            </NoticeText>
          </Notice>
          <SecondaryButton
            label="Reintentar"
            icon="refresh-outline"
            onPress={() => void activate()}
          />
        </View>
      ) : null}

      {status === "requested" ? (
        <View style={{ gap: spacing(2) }}>
          <SecondaryButton
            label={checking ? "Comprobando..." : "Comprobar si ya está activo"}
            icon="refresh-outline"
            disabled={checking}
            onPress={() => void checkPhone()}
          />
          {checkedStill ? (
            <ResultLine tone="warning">
              Todavía no está activo. Te avisaremos por email en cuanto lo esté; no
              hace falta que te quedes mirando.
            </ResultLine>
          ) : null}
          {channelsError ? (
            <ResultLine tone="danger">
              No hemos podido comprobarlo: revisa tu conexión y vuelve a pulsar.
            </ResultLine>
          ) : null}
        </View>
      ) : null}

      <Panel>
        <SectionTitle>Para que conteste cuando tú no puedes: desvía tus llamadas</SectionTitle>
        <Text style={styles.body}>
          Marca estos códigos desde el teléfono de tu agencia. Funcionan en Movistar,
          Vodafone, Orange y en casi todos los operadores.
        </Text>
        <CopyRow
          label="Desviar todas las llamadas"
          value={`**21*${number ?? "NUMERO-DE-TU-IA"}#`}
        />
        <CopyRow
          label="Desviar solo si no contestas"
          value={`**61*${number ?? "NUMERO-DE-TU-IA"}#`}
        />
        <CopyRow label="Quitar el desvío" value="##21#" />
        {!number ? (
          <HonestNote>
            Los códigos llevan tu número de IA: activa el número arriba y aquí
            aparecerán ya listos para copiar.
          </HonestNote>
        ) : null}
        <CheckRow
          label="Ya he configurado el desvío"
          checked={!!checklist["call_forwarding"]}
          busy={forwardingBusy}
          onToggle={() => void toggleForwarding()}
        />
        {forwardingError ? (
          <ResultLine tone="danger">
            No hemos podido guardarlo. Comprueba tu conexión y vuelve a tocar la
            casilla.
          </ResultLine>
        ) : null}
      </Panel>
    </View>
  );
}

const styles = StyleSheet.create({
  body: { ...type.body, lineHeight: 22 },
  numberRow: { flexDirection: "row", alignItems: "center", gap: spacing(3) },
  numberIcon: {
    width: 40,
    height: 40,
    borderRadius: radius.pill,
    backgroundColor: colors.successSoft,
    alignItems: "center",
    justifyContent: "center",
  },
  numberLabel: { ...type.meta, color: colors.successInk },
  number: { ...type.title, fontSize: 22, letterSpacing: 0.5 },
});
