/** Tiny typed fetch wrapper for CLIENT components.
 *
 * Goes through /api/backend (a route handler that injects the Clerk JWT
 * server-side). Server components should use `serverApi` from
 * `@/lib/server-api` instead — it talks to the backend directly.
 */
const BASE = "/api/backend";
const DEFAULT_TIMEOUT_MS = 30_000;

/** Error de una llamada a la API con el código HTTP a mano.
 *
 * Antes se lanzaba un `Error` pelado y quien llamaba solo tenía el texto, así
 * que no podía distinguir un fallo real de una respuesta con significado
 * (facturación necesita saber si el backend contestó 409 = "este cambio no se
 * puede hacer, hay que pasar por el checkout"). Extiende `Error` y conserva el
 * mismo `message`, así que los `catch` que ya existían siguen igual.
 */
export class ApiError extends Error {
  constructor(
    public status: number,
    public body: string,
  ) {
    super(`API ${status}: ${body}`);
    this.name = "ApiError";
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  // FormData bodies must NOT get a manual Content-Type — the browser sets the
  // multipart boundary itself. JSON bodies keep the explicit header.
  const isFormData = typeof FormData !== "undefined" && init?.body instanceof FormData;
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        ...(isFormData ? {} : { "Content-Type": "application/json" }),
        ...(init?.headers || {}),
      },
      cache: "no-store",
      signal: init?.signal ?? AbortSignal.timeout(DEFAULT_TIMEOUT_MS),
    });
  } catch (error) {
    if (error instanceof Error && ["AbortError", "TimeoutError"].includes(error.name)) {
      throw new ApiError(504, "backend_timeout");
    }
    throw error;
  }
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.json();
}

export type InboxItem = {
  conversation_id: string;
  channel: "whatsapp" | "email" | "voice" | "sms" | "web";
  status: "active" | "snoozed" | "handed_off" | "closed";
  lead_id: string;
  lead_name: string | null;
  lead_temperature: "hot" | "warm" | "cold";
  last_message_at: string;
  last_message_role: "lead" | "agent_ai" | "agent_human" | "system";
  last_message_preview: string;
  unread: boolean;
  assigned_user_id: string | null;
};

export type Kpis = {
  range_days: number;
  leads_total: number;
  leads_new: number;
  leads_qualified: number;
  visits_scheduled: number;
  visits_completed: number;
  handoffs: number;
  avg_first_response_seconds: number | null;
  agent_input_tokens: number;
  agent_output_tokens: number;
};

export type Message = {
  id: string;
  role: "lead" | "agent_ai" | "agent_human" | "system";
  content: string;
  created_at: string;
};

/** GET /agencies/{id}/calls — una fila por llamada de voz terminada. */
export type CallItem = {
  conversation_id: string;
  lead_id: string;
  lead_name: string | null;
  lead_phone: string | null;
  call_id: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  recording_url: string | null;
  transcript_summary: string | null;
};

/** GET /agencies/{id}/visits?upcoming=&from=&to= — agenda de visitas. */
export type VisitItem = {
  id: string;
  /** Instante absoluto (ISO con zona). Para pintar la hora o agrupar por día
   * hay que usar `timezone`: con la del servidor, una visita a las 00:30 de
   * Madrid cae en el día anterior y se muestra como las 22:30. */
  scheduled_at: string;
  status:
    | "requested"
    | "confirmed"
    | "rescheduled"
    | "cancelled"
    | "no_show"
    | "completed";
  lead_name: string | null;
  lead_phone: string | null;
  property_title: string | null;
  property_id: string;
  briefing: string;
  /** Zona horaria IANA de la agencia, p. ej. "Europe/Madrid". */
  timezone: string;
};

/** Partes de una fecha en una zona horaria concreta. `en-CA` da los números
 * en orden ISO y `h23` evita el "24:00" de algunos locales. */
function partsIn(date: Date, timeZone: string): Record<string, string> {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  return Object.fromEntries(
    formatter.formatToParts(date).map((p) => [p.type, p.value])
  );
}

/** "YYYY-MM-DD" del día natural en esa zona horaria. */
export function dayKeyIn(date: Date, timeZone: string): string {
  const p = partsIn(date, timeZone);
  return `${p.year}-${p.month}-${p.day}`;
}

/** "HH:MM" en esa zona horaria. */
export function timeIn(date: Date, timeZone: string): string {
  const p = partsIn(date, timeZone);
  return `${p.hour}:${p.minute}`;
}

/** POST /agencies/{id}/conversations/{id}/reply — respuesta manual enviada. */
export type ManualReply = {
  id: string;
  role: "agent_human";
  content: string;
  created_at: string;
  external_message_id: string | null;
  handed_off: boolean;
};

/** Segundos → "3 min 12 s" / "45 s". Null cuando el proveedor no la dio. */
export function formatDuration(seconds: number | null | undefined): string | null {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return null;
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total} s`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return rest === 0 ? `${minutes} min` : `${minutes} min ${rest} s`;
}
