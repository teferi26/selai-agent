// Paso 4 · WhatsApp — POST /channels/whatsapp (Meta verifica las credenciales
// ANTES de guardarlas), datos del webhook copiables y mensaje de prueba real
// con POST /channels/whatsapp/test.
import { useCallback, useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { ApiError, isTimeout } from "@/lib/api";
import { useSetupWizard } from "@/context/SetupWizardContext";
import { connectWhatsApp, sendWhatsAppTest } from "@/lib/setup-api";
import { PrimaryButton } from "@/components/ui";
import {
  Collapsible,
  CopyRow,
  Field,
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
} from "@/components/setup/kit";
import { useUnsavedStepGuard } from "@/components/setup/useUnsavedStepGuard";
import { successHaptic } from "@/lib/haptics";
import { colors, radius, spacing, type } from "@/lib/theme";

function MetaGuide({ callbackUrl, verifyToken }: { callbackUrl?: string; verifyToken?: string }) {
  return (
    <Collapsible title="¿De dónde saco estos datos? Guía paso a paso">
      <NumberedList
        items={[
          <Text key="1" style={styles.guideText}>
            <Strong>Entra en developers.facebook.com</Strong> con la cuenta de Facebook
            de tu empresa y pulsa «Mis apps» → «Crear app». Elige el tipo «Empresa»,
            ponle el nombre de tu inmobiliaria y créala.
          </Text>,
          <Text key="2" style={styles.guideText}>
            <Strong>Añade WhatsApp a la app.</Strong> En la lista de productos busca
            «WhatsApp» y pulsa «Configurar». Meta te pedirá vincular (o crear) tu
            cuenta de Meta Business.
          </Text>,
          <Text key="3" style={styles.guideText}>
            <Strong>Copia el identificador del número.</Strong> En «WhatsApp →
            Configuración de la API» verás tu número y, justo debajo, el
            «Identificador del número de teléfono»: una cifra larga. Va en el primer
            campo de abajo.
          </Text>,
          <Text key="4" style={styles.guideText}>
            <Strong>Copia el identificador de la cuenta de WhatsApp Business.</Strong>{" "}
            Está en esa misma pantalla, un poco más arriba. Es opcional, pero sin él no
            podemos activarte el aviso de mensajes automáticamente.
          </Text>,
          <Text key="5" style={styles.guideText}>
            <Strong>Genera un token permanente.</Strong> El de prueba caduca en 24
            horas. Ve a business.facebook.com → Configuración del negocio → Usuarios →
            Usuarios del sistema, crea uno con rol de administrador, asígnale tu app y
            pulsa «Generar token» con los permisos whatsapp_business_messaging y
            whatsapp_business_management.
          </Text>,
          <Text key="6" style={styles.guideText}>
            <Strong>Copia el secreto de la app (opcional).</Strong> Está en
            «Configuración de la app → Básica». Sirve para comprobar que cada mensaje
            viene de verdad de Meta. Si lo guardas, pasa a ser la única comprobación
            válida para tu número: tiene que ser el de esta misma app.
          </Text>,
          <View key="7" style={{ gap: spacing(2) }}>
            <Text style={styles.guideText}>
              <Strong>Configura el aviso de mensajes de tu app.</Strong> Sin este paso
              tu número queda conectado pero no entra ninguna conversación. En tu app,
              en «WhatsApp → Configuración», sección «Webhooks», pulsa «Editar» y pega
              estos dos valores:
            </Text>
            <CopyRow
              label="URL de devolución de llamada"
              value={callbackUrl || "(se muestra en cuanto carguemos tu configuración)"}
            />
            <CopyRow
              label="Token de verificación"
              value={verifyToken || "(se muestra en cuanto carguemos tu configuración)"}
            />
            <Text style={styles.guideText}>
              Guarda y, en «Campos del webhook», suscríbete a <Strong>messages</Strong>.
              Es el que trae los mensajes de tus clientes. Si rellenaste el
              identificador de la cuenta de WhatsApp Business, lo intentamos por ti al
              conectar y te decimos si ya está hecho.
            </Text>
          </View>,
        ]}
      />
    </Collapsible>
  );
}

function TestSender({ agencyId }: { agencyId: string }) {
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<
    { tone: "success" | "danger"; text: string } | null
  >(null);

  const send = useCallback(async () => {
    if (busy || !phone.trim()) return;
    setBusy(true);
    setResult(null);
    try {
      const out = await sendWhatsAppTest(agencyId, phone.trim());
      successHaptic();
      setResult({
        tone: "success",
        text: `Enviado al ${out.to_phone}. Mira tu WhatsApp: debería llegarte en unos segundos. Si respondes, tu asistente contestará solo.`,
      });
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      setResult({
        tone: "danger",
        text:
          status === 422
            ? "Ese número no parece válido. Escríbelo con prefijo (+34 600 00 00 00) o los 9 dígitos si es español."
            : status === 501
              ? "Tu WhatsApp aún no está conectado, así que no hay desde dónde enviarlo."
              : isTimeout(e)
                ? "Meta está tardando demasiado. Mira tu WhatsApp por si acaso ha llegado y, si no, prueba otra vez en un minuto."
                : "No hemos podido enviarlo. Suele ser que el token ya no vale o que Meta no responde. Inténtalo de nuevo en un minuto.",
      });
    } finally {
      setBusy(false);
    }
  }, [agencyId, busy, phone]);

  return (
    <Panel>
      <SectionTitle>Envíame un WhatsApp de prueba</SectionTitle>
      <Text style={styles.body}>
        Es la única comprobación que demuestra que el canal funciona de verdad:
        mandamos un mensaje desde tu número al tuyo.
      </Text>
      <Field
        label="Tu móvil"
        value={phone}
        onChangeText={setPhone}
        placeholder="+34 600 00 00 00"
        keyboardType="phone-pad"
        autoCapitalize="none"
        hint="Si es español puedes escribir solo los 9 dígitos."
        maxLength={32}
      />
      <PrimaryButton
        label={busy ? "Enviando..." : "Enviarme el mensaje"}
        icon="paper-plane-outline"
        loading={busy}
        disabled={!phone.trim()}
        onPress={() => void send()}
      />
      {result ? <ResultLine tone={result.tone}>{result.text}</ResultLine> : null}
    </Panel>
  );
}

export default function StepWhatsApp() {
  const {
    agencyId,
    channels,
    channelsLoading,
    channelsError,
    refreshChannels,
    markChecklist,
  } = useSetupWizard();

  const [phoneNumberId, setPhoneNumberId] = useState("");
  const [wabaId, setWabaId] = useState("");
  const [token, setToken] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connectionSaved, setConnectionSaved] = useState(false);
  const wa = channels?.whatsapp;
  const connected = !!wa?.connected;

  useUnsavedStepGuard(
    !connected &&
      !connectionSaved &&
      [phoneNumberId, wabaId, token, appSecret].some((value) => value.trim().length > 0),
    "Las credenciales de WhatsApp aún no se han comprobado ni guardado."
  );

  const connect = useCallback(async () => {
    if (!agencyId || busy) return;
    if (!phoneNumberId.trim() || !token.trim()) {
      setError("Necesitamos al menos el identificador del número y el token permanente.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await connectWhatsApp(agencyId, {
        phone_number_id: phoneNumberId.trim(),
        waba_id: wabaId.trim() || null,
        access_token: token.trim(),
        app_secret: appSecret.trim() || null,
      });
      // A partir de aquí Meta YA ha validado y el backend YA lo ha guardado:
      // ninguna de estas dos llamadas puede acabar diciéndole al usuario que
      // la conexión ha fallado. Se intentan y, si fallan, se recuperan solas
      // (el progreso al volver al paso, el estado al recargar el asistente).
      successHaptic();
      setConnectionSaved(true);
      setToken("");
      setAppSecret("");
      await markChecklist({ whatsapp: true }).catch(() => {});
      await refreshChannels();
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      setError(
        status === 422
          ? "Meta no reconoce esos datos. Revisa el identificador del número (la cifra larga que sale debajo de tu número) y el token: tiene que ser el permanente del usuario del sistema, no el de prueba."
          : status === 502
            ? "Meta no responde ahora mismo. No es culpa tuya: espera un par de minutos y vuelve a pulsar «Conectar y comprobar»."
            : isTimeout(e)
              ? "Meta está tardando demasiado en contestar. Espera un minuto y vuelve a pulsar «Conectar y comprobar»: no se ha guardado nada a medias."
              : "No hemos podido conectarlo. Comprueba tu conexión e inténtalo de nuevo."
      );
    } finally {
      setBusy(false);
    }
  }, [agencyId, busy, phoneNumberId, wabaId, token, appSecret, markChecklist, refreshChannels]);

  const metaWarning = (
    <Notice tone="warning" title="Meta tiene que verificar tu empresa">
      Para que tu asistente pueda escribir a cualquier cliente sin límite, Meta exige
      verificar tu empresa (subes el CIF o la escritura en business.facebook.com →
      Configuración del negocio → Centro de seguridad). Esa revisión la hace Meta y
      suele tardar 1-2 días laborables: no depende de selai. Mientras tanto puedes
      conectar el número hoy y probar con hasta 5 destinatarios.
    </Notice>
  );

  if (channelsError && !channels) {
    return (
      <View style={{ gap: spacing(4) }}>
        <StepLead>Tu asistente responderá los WhatsApp de tus clientes.</StepLead>
        {metaWarning}
        <StepError
          message="No hemos podido consultar el estado de tu WhatsApp."
          onRetry={() => void refreshChannels()}
        />
      </View>
    );
  }

  if (channelsLoading && !channels) {
    return (
      <View style={{ gap: spacing(4) }}>
        <StepLead>Tu asistente responderá los WhatsApp de tus clientes.</StepLead>
        {metaWarning}
        <StepSkeleton rows={2} />
      </View>
    );
  }

  return (
    <View style={{ gap: spacing(4) }}>
      <StepLead>
        Tu asistente responderá los WhatsApp de tus clientes desde el número de tu
        agencia. Necesitas una cuenta de WhatsApp Business API de Meta; la guía te
        lleva de la mano.
      </StepLead>

      {metaWarning}

      {connected && wa ? (
        <>
          <Panel>
            <View style={styles.connectedRow}>
              <View style={styles.connectedIcon}>
                <Ionicons name="logo-whatsapp" size={20} color={colors.successInk} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.connectedLabel}>WhatsApp conectado</Text>
                <Text style={styles.connectedNumber} selectable>
                  {wa.phone_number || "Número verificado"}
                </Text>
                {wa.verified_name ? (
                  <Text style={styles.connectedName}>{wa.verified_name}</Text>
                ) : null}
              </View>
              <Ionicons name="checkmark-circle" size={22} color={colors.success} />
            </View>
            <HonestNote>
              Comprobado contra Meta al conectar. Para cambiar de número o
              desconectarlo, entra en el panel web → Ajustes → Canales.
            </HonestNote>
          </Panel>

          <Panel>
            <SectionTitle>
              {wa.subscribed_apps
                ? "Aviso de mensajes: hecho automáticamente"
                : "Configura el aviso de mensajes"}
            </SectionTitle>
            <Text style={styles.body}>
              {wa.subscribed_apps
                ? "Suscribimos tu número a tu app de Meta al conectar. Solo queda comprobar que la URL de devolución de llamada de tu app es esta; si ya lo era, no tienes que tocar nada."
                : "No hemos podido hacerlo por ti: hace falta el identificador de la cuenta de WhatsApp Business y un token con permiso de gestión. Sin esto tu número queda conectado pero no te llega ningún mensaje. Entra en developers.facebook.com → WhatsApp → Configuración, pulsa «Editar» en Webhooks y pega estos dos valores."}
            </Text>
            <CopyRow label="URL de devolución de llamada" value={wa.webhook_callback_url} />
            <CopyRow
              label="Token de verificación"
              value={wa.webhook_verify_token || "(sin configurar — escríbenos)"}
            />
            <HonestNote>
              Después, en «Campos del webhook», suscríbete a messages: es el que trae
              los mensajes de tus clientes.
            </HonestNote>
            {wa.app_secret_set ? (
              <Notice tone="warning" icon="shield-checkmark-outline">
                Guardaste el secreto de tu app: comprobamos cada mensaje con él y no
                aceptamos ningún otro. Si no te llega nada y el aviso está bien puesto,
                lo más probable es que el secreto sea de otra app.
              </Notice>
            ) : null}
          </Panel>

          {agencyId ? <TestSender agencyId={agencyId} /> : null}
        </>
      ) : (
        <>
          <MetaGuide
            callbackUrl={wa?.webhook_callback_url}
            verifyToken={wa?.webhook_verify_token}
          />

          <Panel>
            <Field
              label="Identificador del número de teléfono"
              value={phoneNumberId}
              onChangeText={setPhoneNumberId}
              placeholder="106540352242922"
              autoCapitalize="none"
              mono
              hint="La cifra larga que aparece debajo de tu número en WhatsApp → Configuración de la API."
              maxLength={64}
            />
            <Field
              label="Identificador de la cuenta de WhatsApp Business"
              value={wabaId}
              onChangeText={setWabaId}
              placeholder="102290129340398"
              autoCapitalize="none"
              mono
              optional
              hint="Si lo rellenas, intentamos activarte el aviso de mensajes automáticamente."
              maxLength={64}
            />
            <Field
              label="Token permanente"
              value={token}
              onChangeText={setToken}
              placeholder="EAAG..."
              autoCapitalize="none"
              secureTextEntry
              mono
              hint="El del usuario del sistema (paso 5 de la guía). Lo guardamos cifrado y solo se usa para tus mensajes."
            />
            <Field
              label="Secreto de la app"
              value={appSecret}
              onChangeText={setAppSecret}
              placeholder="••••••••••••"
              autoCapitalize="none"
              secureTextEntry
              mono
              optional
              hint="Si lo rellenas pasa a ser la única comprobación válida para tu número."
              maxLength={256}
            />
            {error ? <Notice tone="danger">{error}</Notice> : null}
            <PrimaryButton
              label={busy ? "Comprobando con Meta..." : "Conectar y comprobar"}
              icon="link-outline"
              loading={busy}
              onPress={() => void connect()}
            />
            <HonestNote>
              Al pulsar comprobamos el número directamente contra Meta. Si algo falla te
              decimos qué revisar y no se guarda nada a medias.
            </HonestNote>
          </Panel>
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  body: { ...type.body, lineHeight: 22 },
  guideText: { ...type.body, fontSize: 14, lineHeight: 21 },
  connectedRow: { flexDirection: "row", alignItems: "center", gap: spacing(3) },
  connectedIcon: {
    width: 40,
    height: 40,
    borderRadius: radius.pill,
    backgroundColor: colors.successSoft,
    alignItems: "center",
    justifyContent: "center",
  },
  connectedLabel: { ...type.meta, color: colors.successInk },
  connectedNumber: { ...type.heading, fontSize: 18 },
  connectedName: { ...type.meta, fontWeight: "400" },
});
