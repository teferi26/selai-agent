// Formatting helpers. UX copy in Spanish per project conventions.

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diffMs = Date.now() - then;
  const min = Math.floor(diffMs / 60_000);
  if (min < 1) return "ahora";
  if (min < 60) return `hace ${min} min`;
  const hours = Math.floor(min / 60);
  if (hours < 24) return `hace ${hours} h`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "ayer";
  if (days < 7) return `hace ${days} d`;
  const d = new Date(iso);
  return d.toLocaleDateString("es-ES", { day: "2-digit", month: "2-digit" });
}

export function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("es-ES", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatEuros(n: number | null | undefined): string {
  if (n === null || n === undefined) return "";
  return new Intl.NumberFormat("es-ES", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 0,
  }).format(n);
}

export function formatBudget(
  min: number | null | undefined,
  max: number | null | undefined
): string {
  if (min == null && max == null) return "Sin presupuesto";
  if (min != null && max != null) return `${formatEuros(min)} – ${formatEuros(max)}`;
  if (min != null) return `Desde ${formatEuros(min)}`;
  return `Hasta ${formatEuros(max)}`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return "—";
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  const min = Math.floor(s / 60);
  const rest = s % 60;
  if (min < 60) return rest > 0 ? `${min} min ${rest} s` : `${min} min`;
  const h = Math.floor(min / 60);
  const restMin = min % 60;
  return restMin > 0 ? `${h} h ${restMin} min` : `${h} h`;
}

// ---------------------------------------------------------------------------
// Fechas en la zona horaria de la AGENCIA.
//
// `scheduled_at` de una visita es un instante absoluto y GET /visits manda
// junto a él el nombre IANA de la zona de la agencia. Pintarlo con la zona del
// teléfono coloca una visita de las 00:30 en Madrid el día anterior y a las
// 22:30 para un agente que esté de viaje.
//
// Todo pasa por `safeFormat`: si el motor de JS de este móvil no trae la base
// de datos de zonas horarias, `timeZone` lanza. Preferimos la hora del
// teléfono a una pantalla rota.
// ---------------------------------------------------------------------------

function safeFormat(
  iso: string,
  timeZone: string,
  options: Intl.DateTimeFormatOptions,
  locale = "es-ES"
): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  try {
    return new Intl.DateTimeFormat(locale, { ...options, timeZone }).format(d);
  } catch {
    return new Intl.DateTimeFormat(locale, options).format(d);
  }
}

/** "09:30" en la zona de la agencia. */
export function formatTimeInZone(iso: string, timeZone: string): string {
  return safeFormat(iso, timeZone, { hour: "2-digit", minute: "2-digit" });
}

/** Clave estable de día ("2026-07-31") para agrupar la agenda. en-CA da
 * justo el formato ISO sin tener que reconstruirlo a mano. */
export function dayKeyInZone(iso: string, timeZone: string): string {
  return safeFormat(
    iso,
    timeZone,
    { year: "numeric", month: "2-digit", day: "2-digit" },
    "en-CA"
  );
}

/** "jueves, 31 de julio" — o "Hoy" / "Mañana" cuando toca. */
export function formatDayHeading(iso: string, timeZone: string): string {
  const key = dayKeyInZone(iso, timeZone);
  const now = new Date();
  const today = dayKeyInZone(now.toISOString(), timeZone);
  const tomorrow = dayKeyInZone(
    new Date(now.getTime() + 24 * 60 * 60 * 1000).toISOString(),
    timeZone
  );
  if (key && key === today) return "Hoy";
  if (key && key === tomorrow) return "Mañana";
  const label = safeFormat(iso, timeZone, {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
  return label ? label.charAt(0).toUpperCase() + label.slice(1) : "";
}

const OPERATION_LABEL: Record<string, string> = {
  buy: "Compra",
  sale: "Venta",
  sell: "Venta",
  rent: "Alquiler",
  rental: "Alquiler",
};

export function formatOperation(op: string | null | undefined): string {
  if (!op) return "—";
  return OPERATION_LABEL[op.toLowerCase()] ?? op;
}
