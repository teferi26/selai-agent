// Guided Twilio connection form. Executes the real connection:
// POST /channels/sms checks the credentials against Twilio (the account
// exists, the number is in it and can receive SMS) before saving anything,
// and the auth token never comes back out of the backend.
//
// Componente suelto: lo usa /settings/canales y está listo para enchufarse
// como paso del asistente de Puesta en marcha sin tocar nada más.
"use client";

import { useState } from "react";
import type { SmsConnectOut } from "@/lib/channels";
import { timeAgoEs } from "@/lib/channels";
import CopyButton from "./CopyButton";

type Props = {
  agencyId: string;
  /** Llamado tras una conexión verificada, para que el padre recargue. */
  onConnected: (out: SmsConnectOut) => void;
  /** La URL que hay que pegar en Twilio. La da GET /channels; si el padre
   * aún no la tiene, la guía lo dice en vez de inventarse una. */
  webhookUrl?: string | null;
};

/** (SMS) Lo único que demuestra que la mitad entrante funciona: ha llegado al
 * menos un SMS de verdad. Conectar solo prueba que podemos ENVIAR; recibir
 * depende de que el webhook esté puesto en la consola de Twilio, que es un
 * paso que hace la agencia y nosotros no podemos hacer por ella. */
export function SmsInboundNotice({
  lastInboundAt,
  inboundCount,
}: {
  lastInboundAt: string | null;
  inboundCount: number;
}) {
  if (lastInboundAt) {
    return (
      <div className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3">
        <p className="text-sm font-medium text-emerald-900">
          Nos están entrando SMS
        </p>
        <p className="mt-1 text-sm text-slate-700">
          {inboundCount} SMS {inboundCount === 1 ? "recibido" : "recibidos"} · el
          último entró {timeAgoEs(lastInboundAt)}.
        </p>
      </div>
    );
  }
  return (
    <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3">
      <p className="text-sm font-medium text-amber-900">
        Conectado, pero todavía no nos ha entrado ningún SMS
      </p>
      <p className="mt-1 text-sm text-slate-700">
        Manda un SMS a tu número <strong>desde otro teléfono</strong>, espera unos
        segundos y vuelve a comprobarlo. Hasta que aparezca aquí no podemos
        asegurarte que los mensajes de tus clientes te vayan a llegar: lo que has
        conectado sirve para <strong>escribir</strong>, y recibir depende del
        webhook que tienes que pegar en la consola de Twilio.
      </p>
      <p className="mt-2 text-sm text-slate-700">
        Si lo has enviado y sigue sin aparecer, casi siempre es una de dos: la
        casilla <strong>«A MESSAGE COMES IN»</strong> de tu número no tiene esta
        URL, o la tiene con el método equivocado (tiene que ser{" "}
        <strong>HTTP POST</strong>).
      </p>
    </div>
  );
}

/** La URL del webhook, copiable, con dónde va exactamente. Se enseña siempre
 * que el canal está conectado: es el dato que arregla el fallo más común, y
 * esconderlo en otra pantalla convierte dos minutos de trabajo en una baja. */
export function SmsWebhookBox({ webhookUrl }: { webhookUrl: string }) {
  return (
    <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
      <p className="text-sm font-medium text-slate-900">
        Pega esta URL en tu número, en Twilio
      </p>
      <p className="mt-1 text-sm text-slate-700">
        Consola de Twilio → <strong>Phone Numbers</strong> →{" "}
        <strong>Manage</strong> → <strong>Active numbers</strong> → pulsa tu
        número. En el bloque <strong>Messaging</strong>, en{" "}
        <strong>«A MESSAGE COMES IN»</strong>, elige <strong>Webhook</strong>,
        pega la URL, deja el método en <strong>HTTP POST</strong> y guarda.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <code className="min-w-0 flex-1 overflow-x-auto rounded-md bg-white px-3 py-2 font-mono text-[13px] text-slate-800">
          {webhookUrl}
        </code>
        <CopyButton value={webhookUrl} />
      </div>
      <p className="mt-3 text-xs text-slate-600">
        Comprobamos cada aviso con el <strong>token</strong> de tu propia cuenta
        de Twilio, que es con el que Twilio los firma. Por eso el token se guarda
        y por eso no te lo enseñamos nunca más: si lo cambias en Twilio, vuelve a
        conectar aquí o dejará de entrarte nada.
      </p>
    </div>
  );
}

function guideSteps(webhookUrl: string | null): {
  title: string;
  body: React.ReactNode;
}[] {
  return [
    {
      title: "Entra en console.twilio.com",
      body: (
        <>
          Inicia sesión con la cuenta de Twilio de tu inmobiliaria (si no tienes,
          se crea gratis en <strong>twilio.com</strong>). Arriba a la izquierda
          verás el <strong>proyecto</strong> en el que estás: apunta bien cuál es,
          porque el número y las claves tienen que ser del{" "}
          <strong>mismo proyecto</strong>.
        </>
      ),
    },
    {
      title: "Copia el «Account SID»",
      body: (
        <>
          En la página principal de la consola, en el recuadro{" "}
          <strong>«Account Info»</strong>, el primer dato es el{" "}
          <strong>Account SID</strong>: empieza por <code>AC</code> y sigue con
          32 caracteres. Cópialo en el primer campo de aquí abajo.
        </>
      ),
    },
    {
      title: "Copia el «Auth Token»",
      body: (
        <>
          Justo debajo, en el mismo recuadro, está el{" "}
          <strong>Auth Token</strong>: viene tapado y hay que pulsar{" "}
          <strong>Show</strong> (o el icono del ojo) para verlo.
          <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[13px] text-amber-900">
            Hace falta sí o sí: Twilio <strong>firma con él</strong> cada SMS que
            nos reenvía, y es como comprobamos que viene de Twilio y no de un
            impostor. Sin el token no te entraría ni un mensaje. Es la contraseña
            de tu cuenta de Twilio: no lo compartas por email ni por WhatsApp.
          </div>
        </>
      ),
    },
    {
      title: "Copia el número",
      body: (
        <>
          Ve a <strong>Phone Numbers</strong> → <strong>Manage</strong> →{" "}
          <strong>Active numbers</strong> y copia el número tal cual aparece
          (por ejemplo <code>+34910000000</code>). Tiene que ser un número{" "}
          <strong>con SMS activado</strong>: en esa lista Twilio marca las
          capacidades de cada uno, y si el tuyo solo tiene voz no puede recibir
          mensajes. Si no tienes ninguno, se compra en{" "}
          <strong>Phone Numbers → Buy a number</strong> marcando la casilla{" "}
          <strong>SMS</strong>.
        </>
      ),
    },
    {
      title: "Conecta aquí y comprueba",
      body: (
        <>
          Rellena los tres campos y pulsa <strong>Conectar y comprobar</strong>.
          Preguntamos a Twilio si esas claves valen y si ese número está de
          verdad en esa cuenta: si algo falla te decimos exactamente qué, y no se
          guarda nada a medias.
        </>
      ),
    },
    {
      title: "Pega la URL del webhook en tu número",
      body: (
        <>
          Este es el paso que hace que los mensajes lleguen: sin él, el número
          queda conectado pero <strong>ningún SMS entra</strong>. En{" "}
          <strong>Phone Numbers → Manage → Active numbers</strong>, pulsa tu
          número y baja al bloque <strong>Messaging</strong>. En{" "}
          <strong>«A MESSAGE COMES IN»</strong> elige <strong>Webhook</strong>,
          pega la URL y deja el método en <strong>HTTP POST</strong>.
          {webhookUrl ? (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <code className="min-w-0 flex-1 overflow-x-auto rounded-md bg-slate-100 px-3 py-2 font-mono text-[13px] text-slate-800">
                {webhookUrl}
              </code>
              <CopyButton value={webhookUrl} />
            </div>
          ) : (
            <div className="mt-2 text-slate-500">
              (La URL exacta aparece aquí en cuanto conectes; también la tienes
              siempre en Ajustes → Canales.)
            </div>
          )}
          <div className="mt-2">
            Guarda con <strong>Save configuration</strong> y mándate un SMS de
            prueba desde otro teléfono. Solo te diremos que está listo cuando nos
            entre ese primer mensaje de verdad.
          </div>
        </>
      ),
    },
  ];
}

export default function SmsConnect({ agencyId, onConnected, webhookUrl }: Props) {
  const [accountSid, setAccountSid] = useState("");
  const [authToken, setAuthToken] = useState("");
  const [phoneNumber, setPhoneNumber] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<React.ReactNode | null>(null);
  const [guideOpen, setGuideOpen] = useState(false);

  async function connect(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    // El backend lo rechaza igualmente, pero un espacio en blanco pasaría el
    // `required` del navegador y el usuario vería un error genérico en vez de
    // saber qué campo le falta.
    if (!authToken.trim()) {
      setError(
        <>
          <strong>Falta el Auth Token.</strong> Sin él no podemos comprobar que
          los avisos vienen de Twilio y no te entraría ningún SMS. Lo tienes en la
          página principal de la consola, en <em>Account Info</em>, pulsando{" "}
          <em>Show</em> (paso 3 de la guía).
        </>
      );
      return;
    }
    setBusy(true);
    setError(null);
    try {
      // fetch directo (no lib/api) para poder distinguir cada 422 del 502.
      const res = await fetch(`/api/backend/api/v1/agencies/${agencyId}/channels/sms`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          account_sid: accountSid.trim(),
          auth_token: authToken.trim(),
          phone_number: phoneNumber.trim(),
        }),
        cache: "no-store",
      });
      if (res.ok) {
        const out = (await res.json()) as SmsConnectOut;
        // El token no vuelve a salir del backend nunca: lo soltamos aquí
        // también, para no dejarlo en memoria del navegador más de la cuenta.
        setAuthToken("");
        onConnected(out);
        return;
      }
      const detail: unknown = await res
        .json()
        .then((b) => b?.detail)
        .catch(() => null);
      if (res.status === 403) {
        setError(
          <>
            <strong>Tu plan no incluye SMS.</strong> El canal de SMS entra en los
            planes Pro y Agencia. Puedes cambiarlo en{" "}
            <a href="/settings/facturacion" className="underline">
              Facturación
            </a>
            .
          </>
        );
      } else if (detail === "sms_verification_failed") {
        setError(
          <>
            <strong>Twilio no reconoce esas claves.</strong> Revisa el{" "}
            <em>Account SID</em> y el <em>Auth Token</em>: los dos están juntos en
            el recuadro <em>Account Info</em> de la página principal de la consola,
            y tienen que ser del <strong>mismo proyecto</strong> que el número.
          </>
        );
      } else if (detail === "sms_number_not_found") {
        setError(
          <>
            <strong>Ese número no está en esa cuenta de Twilio.</strong> Las claves
            valen, pero el número no aparece en{" "}
            <em>Phone Numbers → Manage → Active numbers</em> de este proyecto. Suele
            pasar cuando el número está en otro proyecto o en una subcuenta:
            cámbiate a ese proyecto arriba a la izquierda y copia sus claves.
          </>
        );
      } else if (detail === "sms_number_not_sms_capable") {
        setError(
          <>
            <strong>Ese número no puede recibir SMS.</strong> Twilio dice que es
            solo de voz. Necesitas uno con la capacidad <em>SMS</em>: en{" "}
            <em>Phone Numbers → Buy a number</em>, marca la casilla{" "}
            <strong>SMS</strong> antes de comprarlo.
          </>
        );
      } else if (detail === "invalid_phone") {
        setError(
          <>
            <strong>Ese número no tiene buena pinta.</strong> Cópialo tal cual
            aparece en Twilio, con el prefijo del país — por ejemplo{" "}
            <code>+34910000000</code>.
          </>
        );
      } else if (detail === "sms_number_taken") {
        setError(
          <>
            <strong>Ese número ya está conectado en otra cuenta de selai.</strong>{" "}
            Un número solo puede atender a una oficina: si los dos entraran por
            el mismo, no habría forma de saber de quién es cada SMS. Desconéctalo
            en la otra cuenta, o usa aquí un número distinto.
          </>
        );
      } else if (detail === "auth_token_required") {
        setError(
          <>
            <strong>Falta el Auth Token.</strong> Lo tienes en la página
            principal de la consola, en <em>Account Info</em>, pulsando{" "}
            <em>Show</em> (paso 3 de la guía). Copia solo el token, sin el texto
            que lo acompaña.
          </>
        );
      } else if (detail === "account_sid_invalid") {
        setError(
          <>
            <strong>Eso no es un Account SID.</strong> Empieza por{" "}
            <code>AC</code> y tiene 34 caracteres; si el tuyo empieza por{" "}
            <code>SK</code> es una API Key. Los dos están en{" "}
            <em>Account Info</em>, en la página principal de la consola.
          </>
        );
      } else if (res.status === 422) {
        setError(
          <>
            <strong>Faltan datos o están mal escritos.</strong> El{" "}
            <em>Account SID</em> empieza por <code>AC</code> y tiene 34
            caracteres; si el tuyo empieza por <code>SK</code> es una API Key, no
            el Account SID.
          </>
        );
      } else if (res.status === 502) {
        setError(
          <>
            <strong>Twilio no responde ahora mismo.</strong> No es culpa tuya: sus
            servidores no han contestado a la comprobación. Espera un par de
            minutos y vuelve a pulsar «Conectar y comprobar».
          </>
        );
      } else {
        setError("No se pudo conectar. Inténtalo de nuevo en unos minutos.");
      }
    } catch {
      setError("No se pudo conectar. Comprueba tu conexión e inténtalo de nuevo.");
    } finally {
      setBusy(false);
    }
  }

  const steps = guideSteps(webhookUrl ?? null);

  return (
    <div>
      {/* Guía plegable */}
      <div className="mb-4 rounded-lg border border-selai-100 bg-selai-50">
        <button
          type="button"
          onClick={() => setGuideOpen((o) => !o)}
          className="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-medium text-slate-800"
        >
          ¿De dónde saco estos datos en Twilio? Guía paso a paso
          <span className="text-slate-400" aria-hidden>
            {guideOpen ? "▴" : "▾"}
          </span>
        </button>
        {guideOpen && (
          <div className="border-t border-selai-100 px-4 py-3">
            <ol className="space-y-3">
              {steps.map((g, i) => (
                <li key={i} className="flex gap-3">
                  <span
                    className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full bg-selai-600 text-xs font-semibold text-white"
                    aria-hidden
                  >
                    {i + 1}
                  </span>
                  <div className="min-w-0 text-sm text-slate-700">
                    <p className="font-medium text-slate-900">{g.title}</p>
                    {/* div, no <p>: algunos pasos llevan bloques copiables y un
                        <div> dentro de un <p> rompe la hidratación. */}
                    <div className="mt-0.5 leading-relaxed">{g.body}</div>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>

      <p className="mb-4 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
        El SMS lo pagas tú directamente a Twilio, con tu propia cuenta y tu propio
        número: nosotros no cobramos nada por mensaje ni tocamos tu saldo. Por eso
        te pedimos tus claves en vez de darte un número nuestro.
      </p>

      <form onSubmit={connect} className="space-y-4">
        <div>
          <label className="mb-1 block text-sm font-medium" htmlFor="sms-account-sid">
            Account SID
          </label>
          <input
            id="sms-account-sid"
            required
            value={accountSid}
            onChange={(e) => setAccountSid(e.target.value)}
            placeholder="AC00000000000000000000000000000000"
            autoComplete="off"
            className="w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm"
          />
          <p className="mt-1 text-xs text-slate-500">
            Página principal de la consola → recuadro «Account Info» (paso 2 de la
            guía). Empieza por <code>AC</code>.
          </p>
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium" htmlFor="sms-auth-token">
            Auth Token
          </label>
          <input
            id="sms-auth-token"
            required
            type="password"
            value={authToken}
            onChange={(e) => setAuthToken(e.target.value)}
            placeholder="••••••••••••••••"
            autoComplete="off"
            className="w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm"
          />
          <p className="mt-1 text-xs text-slate-500">
            Mismo recuadro, botón «Show» (paso 3). Twilio firma con él cada SMS que
            nos reenvía:{" "}
            <strong>sin el token correcto no te llegará ningún mensaje</strong>. Lo
            guardamos para comprobar esa firma y no se muestra nunca más.
          </p>
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium" htmlFor="sms-number">
            Tu número de Twilio
          </label>
          <input
            id="sms-number"
            required
            value={phoneNumber}
            onChange={(e) => setPhoneNumber(e.target.value)}
            placeholder="+34 910 00 00 00"
            className="w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm"
          />
          <p className="mt-1 text-xs text-slate-500">
            Phone Numbers → Manage → Active numbers (paso 4). Tiene que tener la
            capacidad <strong>SMS</strong> y estar en este mismo proyecto.
          </p>
        </div>

        {error && (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={busy}
          className="rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
        >
          {busy ? "Comprobando con Twilio…" : "Conectar y comprobar"}
        </button>
        <p className="text-xs text-slate-500">
          Al pulsar, preguntamos a Twilio si esas claves valen y si el número está
          en esa cuenta. Esa comprobación demuestra que puedes{" "}
          <strong>enviar</strong>; para <strong>recibir</strong> hace falta además
          pegar la URL del webhook en tu número, y solo te diremos que está listo
          cuando nos entre tu primer SMS de verdad.
        </p>
      </form>
    </div>
  );
}
