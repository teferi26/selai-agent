// Conexión con Google Calendar. Componente suelto, compartido por la
// "Puesta en marcha" y por /settings/calendario, igual que WhatsAppConnect.
//
// El flujo real es OAuth de Google, así que la conexión ocurre FUERA del
// panel: pedimos al backend la URL de consentimiento
// (GET /calendar/authorize), la abrimos en una ventana emergente y, cuando
// Google devuelve a nuestro callback, esa ventana se cierra sola. Aquí no
// llega ningún dato de Google: mientras la ventana está abierta preguntamos
// el estado cada dos segundos y pintamos lo que diga el backend.
//
// El refresh token nunca sale del backend; esta pantalla solo ve
// `connected`, el calendario y (cuando se sabe) la cuenta de Google.
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "@/lib/api";

/** GET /api/v1/agencies/{id}/calendar — espejo de CalendarStatusOut. */
export type CalendarStatus = {
  connected: boolean;
  /** false = este despliegue no tiene credenciales de Google configuradas.
   * No es culpa del cliente y no hay botón que pueda arreglarlo. */
  configured: boolean;
  calendar_id: string | null;
  /** Solo se conoce después de crear el primer evento: el permiso que
   * pedimos (`calendar.events`) no autoriza a preguntar quién es el usuario. */
  account_email: string | null;
  connected_at: string | null;
  /** El cliente nos quitó el acceso desde su cuenta de Google. No es lo mismo
   * que "nunca conectado": hay que decírselo y pedirle que lo reconecte. */
  revoked: boolean;
  revoked_at: string | null;
  /** Sigue conectado, pero la última visita no se pudo copiar. Código corto
   * nuestro (`http_403`, `google_unreachable`…), nunca el texto de Google.
   * Un calendario que deja de funcionar en silencio es peor que uno
   * desconectado: aquí lo contamos. */
  sync_error: string | null;
  sync_error_at: string | null;
};

const POLL_MS = 2000;
// Techo del sondeo: si alguien deja la ventana de Google abierta y se va a
// comer, no seguimos preguntando al backend indefinidamente.
const POLL_TIMEOUT_MS = 3 * 60 * 1000;

function loadCalendar(agencyId: string): Promise<CalendarStatus> {
  return api<CalendarStatus>(`/api/v1/agencies/${agencyId}/calendar`);
}

export function calendarStateLabel(status: CalendarStatus | null): {
  label: string;
  tone: "ok" | "warn" | "off";
} {
  if (!status) return { label: "Cargando…", tone: "off" };
  // Conectado pero fallando: la etiqueta no puede decir "Conectado" a secas o
  // nadie mira la advertencia que hay debajo.
  if (status.connected && status.sync_error)
    return { label: "Con incidencias", tone: "warn" };
  if (status.connected) return { label: "Conectado", tone: "ok" };
  if (status.revoked) return { label: "Acceso revocado", tone: "warn" };
  if (!status.configured) return { label: "No disponible", tone: "off" };
  return { label: "Sin conectar", tone: "off" };
}

/** Qué le decimos al cliente según por qué falló la copia. Sin jerga y sin
 * el código: lo que necesita saber es si tiene que hacer algo. */
function syncErrorMessage(code: string): string {
  if (code === "http_403")
    return "Google ha rechazado el permiso para escribir en el calendario. Vuelve a conectarlo y deja marcada la casilla del calendario.";
  if (code === "http_404")
    return "El calendario que elegiste ya no existe. Vuelve a conectarlo para elegir otro.";
  if (code === "google_unreachable")
    return "No hemos podido hablar con Google. Suele arreglarse solo; si mañana sigue igual, vuelve a conectarlo.";
  return "Google ha devuelto un error al crear el evento. Si vuelve a pasar, desconecta y vuelve a conectar el calendario.";
}

function formatMoment(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("es-ES", {
    day: "numeric",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
  });
}

type Props = {
  agencyId: string;
  /** Se llama cada vez que el estado cambia (conectar / desconectar), para
   * que la pantalla que lo envuelve refresque su propio progreso. */
  onChange?: (status: CalendarStatus) => void;
};

export default function CalendarConnect({ agencyId, onChange }: Props) {
  const [status, setStatus] = useState<CalendarStatus | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [waiting, setWaiting] = useState(false);

  // Guardamos el callback en una ref: si el padre lo redefine en cada render
  // (lo normal con una función inline), meterlo en las dependencias de
  // `refresh` reinicia el sondeo en bucle.
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  const refresh = useCallback(async (): Promise<CalendarStatus | null> => {
    try {
      const out = await loadCalendar(agencyId);
      setStatus(out);
      setLoadError(false);
      onChangeRef.current?.(out);
      return out;
    } catch {
      setLoadError(true);
      return null;
    }
  }, [agencyId]);

  useEffect(() => {
    let active = true;
    void loadCalendar(agencyId).then(
      (out) => {
        if (!active) return;
        setStatus(out);
        setLoadError(false);
        onChangeRef.current?.(out);
      },
      () => {
        if (active) setLoadError(true);
      }
    );
    return () => {
      active = false;
    };
  }, [agencyId]);

  async function connect() {
    setError(null);
    // La ventana se abre AQUÍ, dentro del clic: si esperásemos a que llegue
    // la URL del backend, el navegador ya no lo consideraría una acción del
    // usuario y la bloquearía como emergente.
    const popup = window.open("", "selai-google-calendar", "width=520,height=680");
    setBusy(true);
    try {
      const out = await api<{ authorization_url: string }>(
        `/api/v1/agencies/${agencyId}/calendar/authorize`
      );
      if (popup) {
        popup.location.href = out.authorization_url;
      } else {
        // Emergentes bloqueadas: seguimos en la misma pestaña. Al volver de
        // Google el usuario ve la página del callback y vuelve al panel.
        window.location.assign(out.authorization_url);
        return;
      }
      void pollUntilConnected(popup);
    } catch (e) {
      popup?.close();
      if (e instanceof ApiError && e.status === 503) {
        setError(
          "La conexión con Google Calendar no está disponible en este momento. Escríbenos y lo activamos."
        );
      } else if (e instanceof ApiError && e.status === 403) {
        setError("Solo el propietario de la cuenta puede conectar el calendario.");
      } else {
        setError("No se ha podido iniciar la conexión. Inténtalo de nuevo.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function pollUntilConnected(popup: Window) {
    setWaiting(true);
    const started = Date.now();
    try {
      // Sondeamos en lugar de escuchar a la ventana: el callback lo sirve el
      // backend, que está en otro origen, así que no puede hablarnos por
      // postMessage sin abrir un canal que no necesitamos.
      while (Date.now() - started < POLL_TIMEOUT_MS) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        const out = await refresh();
        if (out?.connected) return;
        if (popup.closed) {
          // Una última comprobación: la ventana puede cerrarse justo después
          // de guardar la conexión.
          await refresh();
          return;
        }
      }
    } finally {
      setWaiting(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/v1/agencies/${agencyId}/calendar`, { method: "DELETE" });
      await refresh();
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 403
          ? "Solo el propietario de la cuenta puede desconectar el calendario."
          : "No se ha podido desconectar. Inténtalo de nuevo."
      );
    } finally {
      setBusy(false);
    }
  }

  if (loadError && status === null) {
    return (
      <p className="text-sm text-slate-500">
        No hemos podido consultar el estado del calendario. Recarga la página.
      </p>
    );
  }

  if (status === null) {
    return <p className="text-sm text-slate-500">Cargando…</p>;
  }

  return (
    <div>
      {status.connected ? (
        <>
          {status.sync_error && (
            <div className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3">
              <p className="text-sm font-medium text-amber-900">
                La última visita no se ha copiado al calendario
              </p>
              <p className="mt-1 text-sm text-slate-700">
                {syncErrorMessage(status.sync_error)}
              </p>
              <p className="mt-1 text-sm text-slate-600">
                Las visitas se siguen agendando con normalidad
                {formatMoment(status.sync_error_at)
                  ? ` (último intento: ${formatMoment(status.sync_error_at)})`
                  : ""}
                .
              </p>
              <button
                type="button"
                onClick={connect}
                disabled={busy || waiting}
                className="mt-3 rounded-lg border border-amber-400 bg-white px-3 py-1.5 text-sm font-medium text-amber-900 hover:bg-amber-100 disabled:opacity-50"
              >
                {waiting ? "Esperando a Google…" : "Volver a conectar"}
              </button>
            </div>
          )}
          <p className="text-sm text-slate-700">
            Cada visita que agendes se creará también en tu Google Calendar, con el
            nombre del cliente, la propiedad y el teléfono al que llamar.
          </p>
          <dl className="mt-3 space-y-1 text-sm text-slate-600">
            {status.account_email && (
              <div className="flex gap-2">
                <dt className="text-slate-500">Cuenta:</dt>
                <dd className="font-medium text-slate-800">{status.account_email}</dd>
              </div>
            )}
            <div className="flex gap-2">
              <dt className="text-slate-500">Calendario:</dt>
              <dd className="font-medium text-slate-800">
                {status.calendar_id === "primary"
                  ? "el principal de esa cuenta"
                  : status.calendar_id}
              </dd>
            </div>
          </dl>
          <button
            type="button"
            onClick={disconnect}
            disabled={busy}
            className="mt-4 rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            {busy ? "Desconectando…" : "Desconectar"}
          </button>
        </>
      ) : (
        <>
          {status.revoked && (
            <div className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3">
              <p className="text-sm font-medium text-amber-900">
                Google nos ha retirado el acceso
              </p>
              <p className="mt-1 text-sm text-slate-700">
                {status.account_email
                  ? `Se ha revocado el permiso de ${status.account_email}. `
                  : "Se ha revocado el permiso desde la cuenta de Google. "}
                Las visitas se siguen agendando con normalidad, pero han dejado de
                aparecer en el calendario. Vuelve a conectarlo para recuperarlo.
              </p>
            </div>
          )}

          {status.configured ? (
            <>
              <p className="text-sm text-slate-700">
                Conecta tu cuenta de Google y las visitas que agende tu asistente
                aparecerán en el calendario del equipo. Solo pedimos permiso para
                crear eventos: no leemos tu agenda.
              </p>
              <button
                type="button"
                onClick={connect}
                disabled={busy || waiting}
                className="mt-4 rounded-lg bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-50"
              >
                {waiting
                  ? "Esperando a Google…"
                  : status.revoked
                    ? "Volver a conectar"
                    : "Conectar Google Calendar"}
              </button>
              {waiting && (
                <p className="mt-2 text-xs text-slate-500">
                  Termina el proceso en la ventana de Google. Esta página se
                  actualizará sola.
                </p>
              )}
            </>
          ) : (
            <p className="text-sm text-slate-500">
              La conexión con Google Calendar todavía no está disponible en tu
              cuenta. Escríbenos y te la activamos.
            </p>
          )}
        </>
      )}

      {error && (
        <p className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}
