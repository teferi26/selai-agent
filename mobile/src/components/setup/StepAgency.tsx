// Paso 1 · Tu agencia — PATCH /settings (business_name, zones, schedule_text,
// website) y marca la clave `agency_profile` del checklist al pulsar Siguiente.
import { useCallback, useState } from "react";
import { Alert, View } from "react-native";
import { isTimeout } from "@/lib/api";
import {
  type LeaveReason,
  useSetupWizard,
  useStepNext,
  useStepSkip,
} from "@/context/SetupWizardContext";
import { asText, asTextList } from "@/lib/setup-api";
import {
  ChipInput,
  Field,
  HonestNote,
  Notice,
  Panel,
  StepLead,
} from "@/components/setup/kit";
import { successHaptic, warningHaptic } from "@/lib/haptics";
import { spacing } from "@/lib/theme";

/** Igualdad de listas respetando el orden: las zonas se guardan y se muestran
 * en el orden en el que se añadieron, así que reordenarlas también es un
 * cambio sin guardar. */
function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

export default function StepAgency() {
  const { settings, agencyName, saveSettings } = useSetupWizard();
  const s = settings?.settings;

  const [businessName, setBusinessName] = useState(asText(s?.business_name));
  const [zones, setZones] = useState<string[]>(asTextList(s?.zones));
  const [schedule, setSchedule] = useState(asText(s?.schedule_text));
  const [website, setWebsite] = useState(asText(s?.website));
  const [error, setError] = useState<string | null>(null);

  const save = useCallback(async () => {
    setError(null);
    try {
      await saveSettings({
        business_name: businessName.trim() || null,
        zones,
        schedule_text: schedule.trim() || null,
        website: website.trim() || null,
        setup_checklist: { agency_profile: true },
      });
      successHaptic();
      return true;
    } catch (e) {
      setError(
        isTimeout(e)
          ? "Está tardando demasiado y no hemos podido guardar. Comprueba tu conexión y vuelve a pulsar «Siguiente»: lo que has escrito sigue aquí."
          : "No hemos podido guardar tus datos. Comprueba tu conexión y vuelve a pulsar «Siguiente»."
      );
      return false;
    }
  }, [businessName, zones, schedule, website, saveSettings]);

  useStepNext(save);

  /** Lo escrito en pantalla ya no coincide con lo último que aceptó el
   * servidor. Se compara contra `settings`, que `saveSettings` actualiza, así
   * que después de guardar vuelve a ser false sin tocar nada más. */
  const isDirty = useCallback(
    () =>
      businessName.trim() !== asText(s?.business_name).trim() ||
      schedule.trim() !== asText(s?.schedule_text).trim() ||
      website.trim() !== asText(s?.website).trim() ||
      !sameList(zones, asTextList(s?.zones)),
    [businessName, schedule, website, zones, s]
  );

  /**
   * "Saltar por ahora" solo guardaba en el manejador de «Siguiente», así que
   * saltar con el formulario relleno tiraba lo escrito sin decir nada.
   *
   * Preguntamos en vez de guardar en silencio: «Saltar por ahora» significa
   * "esto todavía no", y guardar sin permiso podría dejar al asistente
   * presentándose con un nombre comercial a medio escribir. Tampoco se
   * descarta en silencio, que era el fallo. Quien sí quiera guardar lo tiene
   * en la misma pregunta, sin volver atrás.
   */
  const skip = useCallback((reason: LeaveReason): Promise<boolean> => {
    if (!isDirty()) return Promise.resolve(true);
    warningHaptic();
    const isSkipping = reason === "skip";
    const destructiveLabel =
      reason === "back"
        ? "Volver sin guardar"
        : reason === "close"
          ? "Salir sin guardar"
          : "Saltar y perderlos";
    return new Promise((resolve) => {
      Alert.alert(
        "Has escrito datos sin guardar",
        isSkipping
          ? "«Saltar por ahora» no los guarda. Puedes guardarlos ahora y seguir, o dejar este paso para más tarde y volver a escribirlos."
          : "Si sales de este paso ahora, se perderán. Puedes guardarlos antes de continuar o salir sin guardar.",
        [
          { text: "Seguir aquí", style: "cancel", onPress: () => resolve(false) },
          {
            text: destructiveLabel,
            style: "destructive",
            onPress: () => resolve(true),
          },
          {
            // `save()` ya pinta su propio error y devuelve false: si el PATCH
            // falla, el usuario se queda en el paso con su texto intacto.
            text: isSkipping ? "Guardar y seguir" : "Guardar y salir",
            onPress: () => void save().then(resolve),
          },
        ],
        // Sin salida silenciosa: un cierre sin respuesta dejaría el botón
        // «Saltar» girando para siempre.
        { cancelable: false, onDismiss: () => resolve(false) }
      );
    });
  }, [isDirty, save]);

  useStepSkip(skip);

  return (
    <View style={{ gap: spacing(4) }}>
      <StepLead>
        Tu asistente usará estos datos al hablar con tus clientes: cómo se presenta,
        qué zonas conoce y cuándo estáis abiertos.
      </StepLead>

      <Panel>
        <Field
          label="Nombre comercial"
          value={businessName}
          onChangeText={setBusinessName}
          placeholder={agencyName || "Inmobiliaria Ejemplo"}
          hint="Así se presentará por teléfono y por WhatsApp."
          maxLength={120}
        />
        <ChipInput
          label="Zonas donde trabajáis"
          placeholder="Chamberí"
          hint="Escribe una zona y pulsa el botón para añadirla. Toca una zona para quitarla."
          values={zones}
          onChange={setZones}
        />
        <Field
          label="Horario de atención"
          value={schedule}
          onChangeText={setSchedule}
          placeholder="Lunes a viernes de 9:00 a 19:00"
          hint="En texto normal: es lo que dirá si alguien pregunta cuándo estáis."
          maxLength={200}
        />
        <Field
          label="Página web"
          value={website}
          onChangeText={setWebsite}
          placeholder="https://www.tuinmobiliaria.es"
          keyboardType="url"
          autoCapitalize="none"
          optional
          maxLength={200}
        />
      </Panel>

      {error ? <Notice tone="danger" title="No se ha guardado">{error}</Notice> : null}

      <HonestNote>
        Se guardan al pulsar «Siguiente». Puedes cambiarlos cuando quieras volviendo
        a este paso.
      </HonestNote>
    </View>
  );
}
