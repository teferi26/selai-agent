// Paso 7 · El chat de tu web — snippet copiable, envío por el compartir nativo
// y comprobación real: POST /channels/webchat/verify carga la web y busca el
// snippet con TU slug, en vez de creerse un "ya lo he puesto".
import { useCallback, useEffect, useState } from "react";
import { Share, StyleSheet, Text, View } from "react-native";
import { isTimeout } from "@/lib/api";
import { useSetupWizard } from "@/context/SetupWizardContext";
import { asText, verifyWebchat, webchatSnippetFor } from "@/lib/setup-api";
import { PrimaryButton, SecondaryButton } from "@/components/ui";
import {
  CodeBlock,
  CopyButton,
  DoneBadge,
  Field,
  HonestNote,
  Notice,
  Panel,
  SectionTitle,
  StepLead,
} from "@/components/setup/kit";
import { useUnsavedStepGuard } from "@/components/setup/useUnsavedStepGuard";
import { successHaptic } from "@/lib/haptics";
import { spacing, type } from "@/lib/theme";

const REASON_TEXT: Record<string, string> = {
  widget_not_found:
    "Hemos cargado tu web pero no encontramos el chat. Comprueba que la línea está pegada justo antes de </body> y que has guardado y publicado los cambios.",
  other_slug:
    "En esa web hay un chat de selai, pero es de otra agencia. Revisa que la línea que pegaste sea la que aparece aquí arriba: la parte data-selai tiene que ser la tuya.",
  unreachable:
    "No hemos podido abrir esa dirección. Comprueba que está bien escrita y que la web es accesible desde internet.",
  blocked_url:
    "Esa dirección no es una web pública que podamos consultar. Escribe la dirección completa, empezando por https://",
};

export default function StepWebchat() {
  const { agencyId, slug, settings, channels, refreshSettings, refreshChannels, checklist, markChecklist, doneMap } =
    useSetupWizard();

  const snippet = webchatSnippetFor(slug);
  const verified = !!channels?.webchat.verified;
  const verifiedUrl = channels?.webchat.verified_url ?? null;

  const savedWebsite = asText(settings?.settings.website);
  const persistedUrl = verifiedUrl || savedWebsite;
  const [url, setUrl] = useState(persistedUrl);
  const [urlTouched, setUrlTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "success" | "danger" | "warning"; text: string } | null>(null);
  const [markBusy, setMarkBusy] = useState(false);

  useEffect(() => {
    if (!urlTouched && !url && persistedUrl) setUrl(persistedUrl);
  }, [persistedUrl, url, urlTouched]);

  useUnsavedStepGuard(
    urlTouched && url.trim() !== persistedUrl.trim(),
    "La dirección de la web todavía no se ha comprobado ni guardado."
  );

  const share = useCallback(async () => {
    try {
      await Share.share({
        message: `Para poner el chat de selai en nuestra web, pega esta línea justo antes de </body>:\n\n${snippet}`,
      });
    } catch {
      // El usuario cerró el compartir: no hay nada que informar.
    }
  }, [snippet]);

  const verify = useCallback(async () => {
    if (!agencyId || busy) return;
    const target = url.trim();
    if (!target) {
      setMessage({ tone: "warning", text: "Escribe la dirección de tu web para comprobarla." });
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const out = await verifyWebchat(agencyId, target);
      if (out.verified) {
        successHaptic();
        setUrl(out.url);
        setUrlTouched(false);
        setMessage({
          tone: "success",
          text: `El chat está funcionando en ${out.url}. Ya atiende a quien visite tu web.`,
        });
        await Promise.all([refreshChannels(), refreshSettings()]);
      } else {
        setMessage({
          tone: "warning",
          text: REASON_TEXT[out.reason] ?? "No hemos podido comprobarlo. Inténtalo de nuevo.",
        });
      }
    } catch (e) {
      setMessage({
        tone: "danger",
        text: isTimeout(e)
          ? "Tu web ha tardado demasiado en responder y hemos cortado la comprobación. Vuelve a intentarlo; si se repite, es que la página tarda mucho en cargar."
          : "No hemos podido hacer la comprobación. Comprueba tu conexión e inténtalo de nuevo.",
      });
    } finally {
      setBusy(false);
    }
  }, [agencyId, busy, url, refreshChannels, refreshSettings]);

  const markInstalled = useCallback(async () => {
    if (markBusy) return;
    setMarkBusy(true);
    try {
      await markChecklist({ webchat: true });
    } catch {
      setMessage({
        tone: "danger",
        text: "No hemos podido guardarlo. Inténtalo de nuevo.",
      });
    } finally {
      setMarkBusy(false);
    }
  }, [markBusy, markChecklist]);

  return (
    <View style={{ gap: spacing(4) }}>
      <StepLead>
        Una sola línea de código añade a tu web un botón de chat atendido por tu
        asistente, 24 horas al día.
      </StepLead>

      {verified ? (
        <DoneBadge>
          Chat comprobado en tu web{verifiedUrl ? ` (${verifiedUrl})` : ""}.
        </DoneBadge>
      ) : doneMap.webchat || checklist["webchat"] ? (
        <Notice tone="info">
          Lo has marcado como instalado, pero todavía no lo hemos comprobado. Usa
          «Comprobar mi web» de abajo para asegurarte de que funciona de verdad.
        </Notice>
      ) : null}

      <Panel>
        <SectionTitle>Copia esta línea y pégala justo antes de &lt;/body&gt;</SectionTitle>
        <CodeBlock value={snippet} />
        <View style={styles.actionsRow}>
          <CopyButton value={snippet} label="el código del chat" />
          <SecondaryButton
            label="Enviárselo a tu informático"
            icon="share-outline"
            onPress={() => void share()}
            style={styles.shareBtn}
          />
        </View>
        <HonestNote>
          En WordPress se pega en el pie de página con un plugin del tipo «Insert
          Headers and Footers». El botón aparece en cuanto la página se recarga y no
          cambia nada más de tu web.
        </HonestNote>
      </Panel>

      <Panel>
        <SectionTitle>Comprobar mi web</SectionTitle>
        <Text style={styles.body}>
          Cargamos tu página y miramos si el chat está puesto. Es un hecho, no una
          casilla de confianza.
        </Text>
        <Field
          label="Dirección de tu web"
          value={url}
          onChangeText={(value) => {
            setUrlTouched(true);
            setUrl(value);
          }}
          placeholder="https://www.tuinmobiliaria.es"
          keyboardType="url"
          autoCapitalize="none"
          maxLength={512}
        />
        <PrimaryButton
          label={busy ? "Comprobando..." : "Comprobar mi web"}
          icon="search-outline"
          loading={busy}
          onPress={() => void verify()}
        />
        {message ? (
          <Notice tone={message.tone === "success" ? "success" : message.tone === "danger" ? "danger" : "warning"}>
            {message.text}
          </Notice>
        ) : null}
      </Panel>

      {!verified && !checklist["webchat"] ? (
        <SecondaryButton
          label={markBusy ? "Guardando..." : "Ya lo he puesto en mi web"}
          icon="checkmark-outline"
          disabled={markBusy}
          onPress={() => void markInstalled()}
        />
      ) : null}

      <HonestNote>
        Marcarlo a mano cuenta para el progreso, pero no demuestra nada: si la línea
        quedó mal pegada, el chat no aparecerá y no nos enteraremos.
      </HonestNote>
    </View>
  );
}

const styles = StyleSheet.create({
  body: { ...type.body, lineHeight: 22 },
  actionsRow: { flexDirection: "row", alignItems: "center", gap: spacing(2) },
  shareBtn: { flex: 1, height: 44, paddingHorizontal: spacing(3) },
});
