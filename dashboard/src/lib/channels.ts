/** Types + helpers for the connection channels of an agency.
 *
 * Mirror of the FastAPI contracts (all under /api/v1/agencies/{agencyId}):
 *   GET    /channels                    → ChannelsStatus
 *   POST   /channels/whatsapp           → WhatsappConnectOut (422 whatsapp_verification_failed,
 *                                         422 de validación si falta app_secret,
 *                                         502 whatsapp_provider_error)
 *   POST   /channels/whatsapp/test      → WhatsappTestOut (501 whatsapp_not_connected,
 *                                         422 invalid_phone, 502 whatsapp_send_failed)
 *   DELETE /channels/whatsapp           → { ok: true }
 *   POST   /channels/sms                → SmsConnectOut (422 sms_verification_failed,
 *                                         422 sms_number_not_found,
 *                                         422 sms_number_not_sms_capable,
 *                                         422 invalid_phone, 422 account_sid_invalid,
 *                                         422 auth_token_required,
 *                                         409 sms_number_taken,
 *                                         403 feature_not_in_plan,
 *                                         502 sms_provider_error)
 *   DELETE /channels/sms                → { ok: true }
 *   POST   /channels/phone              → PhoneProvisionOut
 *   POST   /channels/webchat/verify     → WebchatVerifyOut
 *   GET/POST/DELETE /webhook-subscriptions
 *   POST   /webhook-subscriptions/{id}/test → WebhookTestOut
 *
 * Consumed by the /setup wizard and /settings/canales — always through the
 * /api/backend proxy (see lib/api.ts).
 */

export type ChannelsStatus = {
  whatsapp: {
    connected: boolean;
    /** El backend encontró una credencial cifrada que ya no puede abrir.
     * Nunca se trata como conexión válida: hay que reconectar el canal. */
    credentials_unreadable: boolean;
    phone_number: string | null;
    verified_name: string | null;
    /** Lo que la agencia debe pegar en su app de Meta (Webhooks). El token
     * es propio de esta agencia (lo deriva el backend), no un valor común a
     * todos los clientes. */
    webhook_callback_url: string;
    webhook_verify_token: string;
    /** true solo si Meta aceptó la auto-suscripción al conectar. */
    subscribed_apps: boolean;
    /** true si la agencia guardó el secreto de su propia app de Meta. Es la
     * ÚNICA firma que el backend acepta para ese número (app/api/whatsapp.py):
     * si el secreto no es el correcto, no entra ni un mensaje. Obligatorio
     * desde 217, así que `false` = conexión anterior a ese cambio, hoy
     * rechazando todos los webhooks. El secreto en sí nunca sale del backend. */
    app_secret_set: boolean;
    /** true solo si el número va por la app de Meta *de la agencia*. Si va
     * por la nuestra, la firma correcta es la global y no falta nada aunque
     * `app_secret_set` sea false. «Estamos rechazando todo» es exactamente
     * `app_secret_required && !app_secret_set`, nunca `!app_secret_set` a
     * secas. */
    app_secret_required: boolean;
    /** (217) Prueba de que la cadena entrante funciona de verdad: cuántos
     * mensajes entrantes de WhatsApp se han guardado y cuándo llegó el
     * último. Se cuenta sobre mensajes, no sobre conversaciones: una
     * conversación se crea una vez por cliente, así que su fecha contesta
     * «cuándo escribió un cliente nuevo», no «cuándo entró el último
     * mensaje». `inbound_count === 0` significa «no ha entrado ni un
     * mensaje», por muy verde que esté el resto de la tarjeta. */
    inbound_count: number;
    last_inbound_at: string | null;
  };
  sms: {
    /** true solo si están guardadas las TRES credenciales de Twilio (SID,
     * token y número) y se comprobaron contra Twilio al conectar. El token
     * NUNCA sale del backend: aquí solo hay este booleano. */
    configured: boolean;
    /** El token existe pero no se puede descifrar; exige reconexión. */
    credentials_unreadable: boolean;
    phone_number: string | null;
    /** El identificador de la cuenta de Twilio. No es un secreto (es la
     * mitad «usuario» del par y no sirve de nada sin el token); se enseña
     * para que quien tenga varios proyectos sepa cuál está conectado. */
    account_sid: string | null;
    friendly_name: string | null;
    /** Lo que la agencia debe pegar en «A MESSAGE COMES IN» del número, en
     * la consola de Twilio. Sin eso no entra ni un SMS. */
    webhook_url: string;
    /** La única prueba de que la mitad entrante funciona: las credenciales
     * demuestran que podemos ENVIAR, nada más. `inbound_count === 0`
     * significa «no ha entrado ni un SMS», por muy verde que esté el resto
     * de la tarjeta. */
    inbound_count: number;
    last_inbound_at: string | null;
  };
  email: {
    address: string;
    received_count: number;
    last_received_at: string | null;
  };
  phone: {
    number: string | null;
    status: "none" | "requested" | "active";
  };
  webchat: {
    slug: string;
    /** (195) true solo si POST /channels/webchat/verify encontró de verdad el
     * snippet con este slug en la web de la agencia. Marcar la casilla a mano
     * ya no cuenta: un chat que nunca apareció en la web del cliente es baja
     * segura y nadie la ve venir. */
    verified: boolean;
    verified_url: string | null;
    verified_at: string | null;
  };
  webhooks: { count: number };
};

export type WhatsappTestOut = {
  sent: boolean;
  /** Normalizado por el backend (E.164 sin '+', prefijo 34 si faltaba). */
  to_phone: string;
  message: string;
};

/** (195) `reason` explica el «no» para que la UI no diga «no funciona» cuando
 * lo que pasa es que el snippet instalado es de otra agencia. */
export type WebchatVerifyReason =
  | "ok"
  | "widget_not_found"
  | "other_slug"
  | "unreachable"
  | "blocked_url";

export type WebchatVerifyOut = {
  verified: boolean;
  url: string;
  reason: WebchatVerifyReason;
};

export type WebhookTestOut = {
  delivered: boolean;
  status_code: number | null;
  error: string | null;
  is_active: boolean;
  consecutive_failures: number;
};

export type WhatsappConnectOut = {
  connected: boolean;
  /** Meta puede devolver 200 sin estos dos campos (token con permisos justos,
   * número recién creado): el backend los declara `str | None` y aquí valen
   * null. No los pintes sin comprobarlos. */
  phone_number: string | null;
  verified_name: string | null;
  /** true si conseguimos suscribir la app al WABA (webhook ya activo). */
  subscribed_apps: boolean;
  /** true si se guardó el secreto de la app de la agencia (ver arriba). */
  app_secret_set: boolean;
  webhook_callback_url: string;
  webhook_verify_token: string;
};

/** Lo mismo, con los dos textos ya normalizados a cadena: es lo que el
 * formulario de conexión entrega a su pantalla padre, para que nadie tenga
 * que decidir qué pintar cuando Meta se calla el número. */
export type ConnectedWhatsApp = Omit<
  WhatsappConnectOut,
  "phone_number" | "verified_name"
> & {
  phone_number: string;
  verified_name: string;
};

export type SmsConnectOut = {
  connected: boolean;
  /** Tal y como lo escribe Twilio (E.164). Es el mismo texto que llega en el
   * campo `To` del webhook, y por eso es el que se guarda. */
  phone_number: string;
  account_sid: string;
  friendly_name: string | null;
  webhook_url: string;
};

/** El `detail` de los errores de POST /channels/sms. Cada uno manda al usuario
 * a un sitio distinto de la consola de Twilio: decir «error» a secas en todos
 * los casos es una llamada a soporte garantizada.
 *
 * Son cadenas y no la lista de campos de Pydantic a propósito: el 422 de
 * validación devuelve el valor que rechazó, y cuando falta un campo devuelve
 * el cuerpo entero — con el Auth Token dentro. El backend valida a mano para
 * que ninguna respuesta de error repita lo que se le mandó. */
export type SmsConnectError =
  | "sms_verification_failed"
  | "sms_number_not_found"
  | "sms_number_not_sms_capable"
  | "invalid_phone"
  | "account_sid_invalid"
  | "auth_token_required"
  /** 409, no 422: el número ya está conectado en otra agencia. Dos filas con
   * el mismo número dejan al webhook sin saber de quién es el SMS. */
  | "sms_number_taken";

export type PhoneProvisionOut =
  | { status: "active"; number: string }
  | { status: "requested" };

export type WebhookSubscriptionOut = {
  id: string;
  target_url: string;
  events: string[];
  is_active: boolean;
  secret_preview: string;
  /** La clave cifrada ya no puede abrirse. Hay que borrar y recrear esta
   * suscripción antes de que vuelva a emitir avisos firmados. */
  secret_unreadable: boolean;
  consecutive_failures: number;
  /** (194) El secreto completo, SOLO en la respuesta que crea la suscripción.
   * Es lo que permite al CRM verificar la firma HMAC; en cualquier otra
   * lectura llega null y ya no hay forma de recuperarlo. Enséñalo una vez,
   * con copiar y con el aviso de que no se volverá a mostrar. */
  secret_full_once: string | null;
};

/** (192) La ruta exacta de menús de cada portal para configurar el email de
 * aviso de leads. Cada uno lo esconde en un sitio distinto, y adivinar es
 * justo lo que un usuario no técnico no puede hacer. */
export const PORTAL_GUIDES: {
  key: string;
  name: string;
  path: string[];
  note: string;
}[] = [
  {
    key: "idealista",
    name: "Idealista",
    path: [
      "Entra en idealista.com/pro con tu usuario profesional",
      "Menú de tu nombre (arriba a la derecha) → «Configuración»",
      "«Datos de la oficina» → apartado «Contacto de los anuncios»",
      "Campo «Email para recibir los contactos»",
    ],
    note: "Idealista suele permitir varios emails separados por comas: añade el tuyo de selai sin quitar el que ya tenías.",
  },
  {
    key: "fotocasa",
    name: "Fotocasa",
    path: [
      "Entra en el Área Profesional de Fotocasa",
      "«Mi cuenta» → «Datos de contacto»",
      "Apartado «Email de recepción de leads»",
    ],
    note: "Fotocasa admite un único email. Si necesitas conservar el tuyo, deja el de siempre y crea la regla de reenvío que te explicamos abajo.",
  },
  {
    key: "habitaclia",
    name: "Habitaclia",
    path: [
      "Entra en el panel profesional de Habitaclia",
      "«Configuración» → «Datos de la agencia»",
      "Campo «Email de contacto para peticiones»",
    ],
    note: "En Habitaclia el email puede estar además en cada anuncio: si alguno tiene uno propio, cámbialo también.",
  },
];

/** Events supported by the backend dispatcher (SUPPORTED_EVENTS in
 * app/services/event_dispatcher.py), with plain-Spanish labels. */
export const WEBHOOK_EVENTS: { key: string; label: string }[] = [
  { key: "lead.created", label: "Nuevo lead" },
  { key: "lead.qualified", label: "Lead cualificado" },
  { key: "lead.temperature_changed", label: "Cambio de interés del lead" },
  { key: "visit.scheduled", label: "Visita agendada" },
  { key: "visit.completed", label: "Visita realizada" },
  { key: "offer.created", label: "Oferta creada" },
  { key: "offer.signed", label: "Oferta firmada" },
];

/** El código HTTP de un error de `api()`, que lo lanza como
 * `Error("API 501: …")`. Devuelve null si no hay forma de saberlo.
 *
 * Hace falta porque varios endpoints de canales distinguen el «no se puede»
 * del «no está conectado» solo por el código, y decirle al usuario «error»
 * cuando lo que pasa es que le falta un paso es una llamada a soporte. */
export function apiErrorStatus(err: unknown): number | null {
  const message = err instanceof Error ? err.message : String(err ?? "");
  const match = /^API (\d{3})\b/.exec(message);
  return match ? Number(match[1]) : null;
}

// La dirección de leads la decide el backend (`settings.email_leads_domain`,
// servida en `GET /channels` como `email.address`). Aquí NO hay copia a
// propósito: el dominio depende del plan del proveedor de correo entrante y
// puede cambiar sin tocar el panel.
//
// Antes había un `leadsEmailFor(slug)` con el dominio escrito a mano, usado
// como respaldo mientras cargaba la petición. El respaldo es justo el peligro:
// una dirección desactualizada no da error, se copia igual de bien, y la
// agencia la pega en Idealista para no recibir jamás un lead.

/** Snippet the agency pastes in its website (loads public/widget.js). */
export function webchatSnippetFor(slug: string): string {
  return `<script src="https://www.selai.es/widget.js" data-selai="${slug}" async></script>`;
}

/** "hace 5 min" / "hace 2 h" / "hace 3 días" for timestamps like
 * email.last_received_at. Falls back to the date for anything older. */
export function timeAgoEs(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 1) return "hace un momento";
  if (mins < 60) return `hace ${mins} min`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `hace ${hours} h`;
  const days = Math.round(hours / 24);
  if (days < 30) return `hace ${days} día${days === 1 ? "" : "s"}`;
  return new Date(iso).toLocaleDateString("es-ES");
}
