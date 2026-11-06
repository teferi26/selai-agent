// Guided WhatsApp Business connection form. Executes the real connection:
// POST /channels/whatsapp verifies the number against Meta before saving and
// tries to subscribe the app to the WABA so the webhook starts delivering.
// Shared by the /setup wizard (paso 4) and /settings/canales.
"use client";

import { useEffect, useState } from "react";
import type {
  ChannelsStatus,
  ConnectedWhatsApp,
  WhatsappConnectOut,
} from "@/lib/channels";
import { timeAgoEs } from "@/lib/channels";
import CopyButton from "./CopyButton";

type Props = {
  agencyId: string;
  /** Called after a verified connection so the parent can update its state. */
  onConnected: (out: ConnectedWhatsApp) => void;
  /** Chip "verificación de Meta en curso" (legacy checklist key). */
  metaVerificationPending?: boolean;
};

/** Datos del webhook que la agencia debe pegar en su app de Meta. */
export type WebhookConfig = {
  callbackUrl: string;
  verifyToken: string;
};

/** Fila "etiqueta + valor copiable" reutilizada por la guía y por Canales. */
export function CopyableRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-slate-600">{label}</p>
      <div className="flex flex-wrap items-center gap-2">
        <code className="min-w-0 flex-1 overflow-x-auto rounded-md bg-slate-100 px-3 py-2 font-mono text-[13px] text-slate-800">
          {value}
        </code>
        <CopyButton value={value} />
      </div>
    </div>
  );
}

/** (217) `true` cuando el número va por la app de Meta de la agencia y no
 * hay secreto guardado: en ese estado rechazamos TODOS los webhooks, así que
 * nada de esta pantalla puede pintarse en verde. Un número servido por
 * nuestra app no necesita secreto propio y no entra aquí. */
export function whatsappSecretMissing(wa: {
  app_secret_set: boolean;
  app_secret_required: boolean;
}): boolean {
  return wa.app_secret_required && !wa.app_secret_set;
}

/** (217) Lo único que demuestra que el canal entrante funciona: ha llegado
 * al menos un mensaje de verdad. «Conectado» solo dice que las credenciales
 * valen para ENVIAR; mientras no haya entrado nada, el paso no puede darse
 * por bueno y esta caja lo dice con todas las letras. */
export function InboundStatusNotice({
  lastInboundAt,
  inboundCount,
  secretMissing = false,
}: {
  lastInboundAt: string | null;
  inboundCount: number;
  /** Falta el secreto de la app propia: hoy no entra nada, hayan entrado o
   * no mensajes en el pasado. */
  secretMissing?: boolean;
}) {
  // (217) Primero lo que pasa AHORA. Un histórico de mensajes recibidos no
  // dice nada del presente, y era la vía por la que una agencia a la que
  // rechazamos todos los webhooks veía la caja verde: los mensajes que la
  // sostenían venían de antes de que se rompiera.
  if (secretMissing) {
    return (
      <div className="mt-4 rounded-lg border border-red-300 bg-red-50 px-4 py-3">
        <p className="text-sm font-medium text-red-900">
          Ahora mismo no te entra ningún mensaje
        </p>
        <p className="mt-1 text-sm text-slate-700">
          {lastInboundAt ? (
            <>
              Llegaron a entrar {inboundCount} mensaje
              {inboundCount === 1 ? "" : "s"}, el último {timeAgoEs(lastInboundAt)},
              pero eso fue antes.{" "}
            </>
          ) : null}
          Falta el secreto de tu app de Meta, así que rechazamos todos los avisos
          que nos manda: vuelve a conectar el número con ese dato y esto se
          arregla al momento.
        </p>
      </div>
    );
  }
  if (lastInboundAt) {
    return (
      <div className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3">
        <p className="text-sm font-medium text-emerald-900">
          Nos están entrando mensajes
        </p>
        <p className="mt-1 text-sm text-slate-700">
          {inboundCount} mensaje{inboundCount === 1 ? "" : "s"} de WhatsApp
          {inboundCount === 1 ? " recibido" : " recibidos"} · el último entró{" "}
          {timeAgoEs(lastInboundAt)}.
        </p>
      </div>
    );
  }
  return (
    <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3">
      <p className="text-sm font-medium text-amber-900">
        Conectado, pero todavía no nos ha entrado ningún mensaje
      </p>
      <p className="mt-1 text-sm text-slate-700">
        Envía un WhatsApp de prueba a tu número{" "}
        <strong>desde otro teléfono</strong>, espera unos segundos y vuelve a
        comprobarlo. Hasta que aparezca aquí no podemos asegurarte que las
        conversaciones de tus clientes te vayan a llegar: lo que has conectado
        sirve para escribir, y recibir depende del webhook de tu app de Meta.
      </p>
      <p className="mt-2 text-sm text-slate-700">
        Si lo has enviado y sigue sin aparecer, casi siempre es una de dos: la{" "}
        <strong>URL de devolución de llamada</strong> de tu app no es la de aquí
        arriba, o el <strong>secreto</strong> que guardaste es el de otra app. En
        ese caso Meta sí nos escribe, pero nosotros no podemos comprobar que sea
        Meta y lo rechazamos — por eso no lo verías aquí.
      </p>
    </div>
  );
}

/** Estado + valores del webhook de Meta. Se muestra siempre que WhatsApp está
 * conectado, tanto en la Puesta en marcha como en /settings/canales: son los
 * dos datos que la agencia necesita para arreglar el webhook, y esconderlos
 * en otra pantalla es lo que convierte un fallo de 2 minutos en una baja.
 * El texto es deliberadamente literal: haber suscrito el número a la app NO
 * garantiza que la URL de devolución de llamada de esa app apunte aquí — eso
 * solo puede hacerlo la agencia. */
export function WebhookStatusBox({
  webhook,
  subscribed,
  appSecretSet = false,
  secretMissing = false,
}: {
  webhook: WebhookConfig;
  subscribed: boolean;
  /** La agencia guardó el secreto de su app: es la única firma que aceptamos
   * para su número, así que hay que decirlo aquí y no en letra pequeña. */
  appSecretSet?: boolean;
  /** Hace falta el secreto de su app y no está: hoy no entra nada. Distinto
   * de `!appSecretSet`, que también es cierto para un número servido por
   * nuestra app, donde no falta nada. */
  secretMissing?: boolean;
}) {
  // (217) El color lo mandan las dos condiciones: una suscripción hecha con
  // el secreto ausente sigue siendo un canal por el que no entra nada, y
  // pintarla de verde era parte del engaño.
  const healthy = subscribed && !secretMissing;
  return (
    <div
      className={`mt-4 rounded-lg border px-4 py-3 ${
        healthy
          ? "border-emerald-200 bg-emerald-50"
          : "border-amber-300 bg-amber-50"
      }`}
    >
      <p
        className={`text-sm font-medium ${
          healthy ? "text-emerald-900" : "text-amber-900"
        }`}
      >
        {subscribed
          ? "Suscripción al webhook hecha automáticamente"
          : "Configura el webhook — sin esto no te llega ningún mensaje"}
      </p>
      <p className="mt-1 text-sm text-slate-700">
        {subscribed ? (
          <>
            Suscribimos tu número a tu app de Meta al conectar. Solo queda
            comprobar que la <strong>URL de devolución de llamada</strong> de tu app
            es esta; si ya lo era, no tienes que tocar nada.
          </>
        ) : (
          <>
            No pudimos hacerlo por ti (hace falta el identificador de la cuenta de
            WhatsApp Business y un token con permiso de gestión). Entra en tu app en{" "}
            <strong>developers.facebook.com → WhatsApp → Configuración</strong>, pulsa{" "}
            <strong>Editar</strong> en Webhooks y pega estos dos valores.
          </>
        )}
      </p>
      <div className="mt-3 space-y-3">
        <CopyableRow label="URL de devolución de llamada" value={webhook.callbackUrl} />
        <CopyableRow
          label="Token de verificación"
          value={webhook.verifyToken || "(sin configurar — escríbenos)"}
        />
      </div>
      <p className="mt-3 text-xs text-slate-600">
        Después, en <strong>Campos del webhook</strong>, suscríbete a{" "}
        <code className="rounded bg-white px-1 py-0.5 font-mono">messages</code>. Es el
        que trae los mensajes de tus clientes.
      </p>
      {secretMissing ? (
        // (217) Este caso solo existe en conexiones anteriores a exigir el
        // secreto. Hoy rechazan todos los webhooks, así que aquí no cabe
        // «funciona igual»: hay que decir que está roto y cómo se arregla.
        <div className="mt-3 rounded-md border border-red-300 bg-red-50 px-3 py-2.5 text-sm text-red-800">
          <strong>Falta el secreto de tu app y por eso no entra ningún mensaje.</strong>{" "}
          Meta firma cada aviso con el secreto de <em>tu</em> app; sin él no podemos
          comprobar que viene de Meta y lo rechazamos. Vuelve a conectar el número
          rellenando el campo «Secreto de la app» (lo tienes en{" "}
          <strong>Configuración de la app → Básica</strong>, botón <em>Mostrar</em>).
        </div>
      ) : appSecretSet ? (
        <p className="mt-2 text-xs text-slate-600">
          Guardaste el <strong>secreto de tu app</strong>: comprobamos cada mensaje
          con él y no aceptamos ningún otro. Si no te llega nada y el webhook está
          bien puesto, lo más probable es que el secreto sea de otra app — vuelve a
          conectar el número con el secreto de la app que tiene esta URL de
          devolución de llamada.
        </p>
      ) : (
        // Número servido por nuestra app: la firma correcta es la nuestra y
        // no falta nada. Decirle aquí «te falta el secreto» sería la misma
        // mentira de siempre, apuntando al otro lado.
        <p className="mt-2 text-xs text-slate-600">
          Este número va por nuestra app de Meta, así que comprobamos los mensajes
          con nuestro propio secreto y tú no tienes que guardar ninguno.
        </p>
      )}
    </div>
  );
}

function guideSteps(webhook: WebhookConfig | null): {
  title: string;
  body: React.ReactNode;
}[] {
  return [
    {
      title: "Entra en developers.facebook.com",
      body: (
        <>
          Inicia sesión con la cuenta de Facebook/Meta de tu empresa y pulsa{" "}
          <strong>Mis apps → Crear app</strong>. Elige el tipo <strong>Empresa</strong>,
          ponle un nombre (por ejemplo, el de tu inmobiliaria) y créala.
        </>
      ),
    },
    {
      title: "Añade WhatsApp a la app",
      body: (
        <>
          En el panel de la app verás una lista de productos. Busca{" "}
          <strong>WhatsApp</strong> y pulsa <strong>Configurar</strong>. Meta te pedirá
          vincular (o crear) tu cuenta de <strong>Meta Business</strong>.
        </>
      ),
    },
    {
      title: "Copia el ID del número de teléfono",
      body: (
        <>
          En <strong>WhatsApp → Configuración de la API</strong> verás una tabla con tu
          número. Justo debajo del número aparece{" "}
          <strong>«Identificador del número de teléfono»</strong> (una cifra larga).
          Cópiala en el primer campo de aquí abajo.
        </>
      ),
    },
    {
      title: "Copia el ID de la cuenta de WhatsApp Business",
      body: (
        <>
          En la misma pantalla, un poco más arriba, está el{" "}
          <strong>«Identificador de la cuenta de WhatsApp Business»</strong>. Cópialo en
          el segundo campo. Es opcional, pero sin él no podemos activarte el webhook
          automáticamente y tendrás que hacer el paso 7 entero a mano.
        </>
      ),
    },
    {
      title: "Genera un token permanente",
      body: (
        <>
          El token de prueba de esa pantalla caduca en 24 horas — necesitas uno
          permanente. Ve a{" "}
          <strong>business.facebook.com → Configuración del negocio → Usuarios → Usuarios
          del sistema</strong>, crea un usuario del sistema (rol administrador), asígnale
          tu app y pulsa <strong>Generar token</strong> marcando los permisos{" "}
          <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[12px]">
            whatsapp_business_messaging
          </code>{" "}
          y{" "}
          <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[12px]">
            whatsapp_business_management
          </code>
          . Copia el token en el tercer campo.
        </>
      ),
    },
    {
      title: "Copia el secreto de la app",
      body: (
        <>
          En <strong>Configuración de la app → Básica</strong> tienes el{" "}
          <strong>«Secreto de la app»</strong> (pulsa <em>Mostrar</em>). Meta firma con
          él cada mensaje que nos manda, y es como comprobamos que viene de Meta y no
          de un impostor.
          <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[13px] text-amber-900">
            Hace falta sí o sí: <strong>sin ese secreto no te entra ni un mensaje</strong>
            . Y tiene que ser el de <strong>esta misma app</strong> — la que vas a
            apuntar a nuestra URL de devolución de llamada en el paso 7. Si te
            equivocas de app, tampoco entrará nada; se arregla volviendo a conectar el
            número con el secreto correcto.
          </div>
        </>
      ),
    },
    {
      title: "Configura los Webhooks de tu app",
      body: (
        <>
          Este es el paso que hace que los mensajes lleguen: sin él, tu número queda
          conectado pero <strong>ninguna conversación entra</strong>. En tu app, en{" "}
          <strong>WhatsApp → Configuración</strong>, sección <strong>Webhooks</strong>,
          pulsa <strong>Editar</strong> y pega:
          {webhook ? (
            <div className="mt-2 space-y-3">
              <CopyableRow
                label="URL de devolución de llamada"
                value={webhook.callbackUrl}
              />
              <CopyableRow
                label="Token de verificación"
                value={webhook.verifyToken || "(sin configurar — escríbenos)"}
              />
            </div>
          ) : (
            <div className="mt-2 text-slate-500">
              (Los dos valores exactos aparecen aquí en cuanto carguemos tu
              configuración; también los tienes siempre en Ajustes → Canales.)
            </div>
          )}
          <div className="mt-2">
            Verifica y guarda, y después, en <strong>Campos del webhook</strong>,
            suscríbete a{" "}
            <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[12px]">
              messages
            </code>
            . Si rellenaste el identificador de la cuenta de WhatsApp Business,
            intentamos suscribir ese campo por ti al conectar y puede estar ya hecho —
            te lo decimos en Ajustes → Canales.
          </div>
        </>
      ),
    },
  ];
}

export default function WhatsAppConnect({
  agencyId,
  onConnected,
  metaVerificationPending = false,
}: Props) {
  const [phoneNumberId, setPhoneNumberId] = useState("");
  const [wabaId, setWabaId] = useState("");
  const [accessToken, setAccessToken] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<React.ReactNode | null>(null);
  const [guideOpen, setGuideOpen] = useState(false);
  const [webhook, setWebhook] = useState<WebhookConfig | null>(null);

  // Los valores del webhook los da el backend (GET /channels). Los pedimos
  // solo al abrir la guía: quien no la abre no paga la llamada.
  useEffect(() => {
    if (!guideOpen || webhook) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/backend/api/v1/agencies/${agencyId}/channels`, {
          cache: "no-store",
        });
        if (!res.ok) return;
        const out = (await res.json()) as ChannelsStatus;
        if (cancelled) return;
        setWebhook({
          callbackUrl: out.whatsapp.webhook_callback_url,
          verifyToken: out.whatsapp.webhook_verify_token,
        });
      } catch {
        // Sin valores mostramos el paso 6 sin los datos copiables; el texto
        // ya dice dónde encontrarlos.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [guideOpen, webhook, agencyId]);

  async function connect(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    // El backend lo rechaza igualmente, pero un espacio en blanco pasaría el
    // `required` del navegador y el usuario vería un error genérico en vez de
    // saber qué campo le falta.
    if (!appSecret.trim()) {
      setError(
        <>
          <strong>Falta el secreto de tu app.</strong> Sin él no podemos comprobar
          que los mensajes vienen de Meta y no te entraría ninguno. Lo tienes en{" "}
          <em>Configuración de la app → Básica → «Secreto de la app»</em> (paso 6 de
          la guía).
        </>
      );
      return;
    }
    setBusy(true);
    setError(null);
    try {
      // fetch directo (no lib/api) para poder distinguir 422 de 502.
      const res = await fetch(
        `/api/backend/api/v1/agencies/${agencyId}/channels/whatsapp`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            phone_number_id: phoneNumberId.trim(),
            waba_id: wabaId.trim() || null,
            access_token: accessToken.trim(),
            app_secret: appSecret.trim(),
          }),
          cache: "no-store",
        }
      );
      if (res.ok) {
        const out = (await res.json()) as WhatsappConnectOut;
        // Meta puede no devolver el número o el nombre verificado; el padre
        // solo pinta textos, así que se los damos ya normalizados.
        onConnected({
          ...out,
          phone_number: out.phone_number ?? "",
          verified_name: out.verified_name ?? "",
        });
        return;
      }
      // Dos 422 distintos: el nuestro de validación (`detail` es una lista de
      // campos) y el de Meta rechazando las credenciales. Decir «Meta no
      // reconoce esos datos» cuando lo que falta es un campo manda al usuario
      // a revisar lo que está bien.
      const detail: unknown = await res
        .json()
        .then((b) => b?.detail)
        .catch(() => null);
      if (res.status === 422 && detail === "whatsapp_verification_failed") {
        setError(
          <>
            <strong>Meta no reconoce esos datos.</strong> Revisa el token o el ID del
            número: el ID es la cifra larga que aparece debajo de tu número en{" "}
            <em>WhatsApp → Configuración de la API</em>, y el token debe ser el
            permanente del usuario del sistema (el de prueba caduca en 24 h). Corrige y
            vuelve a probar.
          </>
        );
      } else if (res.status === 422) {
        setError(
          <>
            <strong>Faltan datos o están mal escritos.</strong> El identificador del
            número, el token permanente y el secreto de la app son obligatorios: sin
            el secreto no podríamos comprobar los mensajes que te manda Meta y no te
            entraría ninguno.
          </>
        );
      } else if (res.status === 502) {
        setError(
          <>
            <strong>Meta no responde ahora mismo.</strong> No es culpa tuya: sus
            servidores no han contestado a la comprobación. Espera un par de minutos y
            pulsa «Conectar y comprobar» otra vez.
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

  const steps = guideSteps(webhook);

  return (
    <div>
      {metaVerificationPending && (
        <div className="mb-4 inline-flex items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs text-amber-800">
          <span className="h-1.5 w-1.5 rounded-full bg-amber-500" aria-hidden />
          Verificación de empresa de Meta en curso · suele tardar 1-2 días
        </div>
      )}

      {/* Guía plegable */}
      <div className="mb-4 rounded-lg border border-selai-100 bg-selai-50">
        <button
          type="button"
          onClick={() => setGuideOpen((o) => !o)}
          className="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-medium text-slate-800"
        >
          ¿De dónde saco estos datos y cómo activo el webhook? Guía paso a paso
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
                    {/* div, no <p>: el paso 6 incluye bloques copiables y un
                        <div> dentro de un <p> rompe la hidratación. */}
                    <div className="mt-0.5 leading-relaxed">{g.body}</div>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>

      {/* Aviso honesto SIEMPRE visible (no dentro de la guía plegable): la
          verificación de empresa de Meta tarda 1-2 días laborables. */}
      <p className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
        Para que tu asistente pueda escribir a cualquier cliente sin límite, Meta
        exige <strong>verificar tu empresa</strong> (subes el CIF o la escritura en
        business.facebook.com → Configuración del negocio → Centro de seguridad).
        Esa revisión la hace Meta y suele tardar <strong>1-2 días laborables</strong>{" "}
        — no depende de nosotros. Mientras tanto puedes conectar el número hoy mismo
        y probar con hasta 5 destinatarios.
      </p>

      <form onSubmit={connect} className="space-y-4">
        <div>
          <label className="mb-1 block text-sm font-medium" htmlFor="wa-phone-id">
            Identificador del número de teléfono
          </label>
          <input
            id="wa-phone-id"
            required
            value={phoneNumberId}
            onChange={(e) => setPhoneNumberId(e.target.value)}
            placeholder="Ej. 106540352242922"
            className="w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm"
          />
          <p className="mt-1 text-xs text-slate-500">
            La cifra larga que aparece debajo de tu número en WhatsApp → Configuración
            de la API.
          </p>
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium" htmlFor="wa-waba-id">
            Identificador de la cuenta de WhatsApp Business{" "}
            <span className="font-normal text-slate-400">(opcional)</span>
          </label>
          <input
            id="wa-waba-id"
            value={wabaId}
            onChange={(e) => setWabaId(e.target.value)}
            placeholder="Ej. 102290129340398"
            className="w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm"
          />
          <p className="mt-1 text-xs text-slate-500">
            Si lo rellenas, intentamos activarte el webhook automáticamente al conectar.
          </p>
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium" htmlFor="wa-token">
            Token permanente
          </label>
          <input
            id="wa-token"
            required
            type="password"
            value={accessToken}
            onChange={(e) => setAccessToken(e.target.value)}
            placeholder="EAAG…"
            autoComplete="off"
            className="w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm"
          />
          <p className="mt-1 text-xs text-slate-500">
            El token del usuario del sistema (paso 5 de la guía). Lo guardamos cifrado y
            solo se usa para enviar y recibir tus mensajes.
          </p>
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium" htmlFor="wa-app-secret">
            Secreto de la app
          </label>
          <input
            id="wa-app-secret"
            required
            type="password"
            value={appSecret}
            onChange={(e) => setAppSecret(e.target.value)}
            placeholder="••••••••••••••••"
            autoComplete="off"
            className="w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm"
          />
          <p className="mt-1 text-xs text-slate-500">
            Configuración de la app → Básica → «Secreto de la app» (paso 6 de la guía).
            Es la única comprobación válida para tu número:{" "}
            <strong>sin él no te llegará ningún mensaje</strong>. Tiene que ser el
            secreto de la misma app cuyo webhook apunta a nosotros.
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
          {busy ? "Comprobando con Meta…" : "Conectar y comprobar"}
        </button>
        <p className="text-xs text-slate-500">
          Al pulsar, comprobamos el número directamente contra Meta. Si algo falla te
          decimos exactamente qué revisar — no se guarda nada a medias. Esa
          comprobación demuestra que puedes <strong>enviar</strong>; para{" "}
          <strong>recibir</strong> hace falta además el webhook del paso 7, y solo te
          diremos que está listo cuando nos entre tu primer mensaje de verdad.
        </p>
      </form>
    </div>
  );
}
