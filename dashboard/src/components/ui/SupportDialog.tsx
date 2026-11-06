// Formulario de soporte del panel.
//
// Por qué modal y no una página /soporte: este formulario existe sobre todo
// para el momento en que algo se ha roto. Si para escribirnos hubiera que
// navegar a otra ruta, perderíamos justo lo que hace útil el aviso (el digest
// del fallo y la pantalla donde estaba el usuario) y dependeríamos de que la
// navegación funcione, que es precisamente lo que puede estar fallando. Como
// modal se abre encima de lo que haya —incluida la pantalla de error— sin
// tocar el router y sin perder contexto.
"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import {
  SUPPORT_EMAIL,
  SUPPORT_MESSAGE_MAX,
  SUPPORT_MESSAGE_MIN,
  buildSupportMailto,
  buildSupportText,
  sendSupportRequest,
  statusOf,
} from "@/lib/support";

type Props = {
  open: boolean;
  onClose: () => void;
  /** Ruta actual (usePathname en quien abre el diálogo). */
  screen?: string | null;
  /** Digest de Next o id de Sentry cuando venimos de una pantalla de error. */
  errorRef?: string | null;
};

type Status = "idle" | "sending" | "sent" | "error";
type CopyState = "idle" | "done" | "failed";

export function SupportDialog({ open, onClose, screen = null, errorRef = null }: Props) {
  const [message, setMessage] = useState("");
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [errorText, setErrorText] = useState<string | null>(null);
  const [copied, setCopied] = useState<CopyState>("idle");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // El diálogo no se desmonta al cerrarlo: conserva el estado. Eso es lo que
  // queremos cuando el envío ha fallado (el usuario vuelve a abrirlo y su texto
  // sigue ahí), pero tras un envío correcto dejaba el "mensaje recibido"
  // pegado y no había forma de reportar un segundo problema sin recargar. Al
  // cerrar desde ese estado, y solo desde ese, empezamos de cero.
  const close = useCallback(() => {
    if (status === "sent") {
      setMessage("");
      setEmail("");
      setErrorText(null);
      setCopied("idle");
      setStatus("idle");
    }
    onClose();
  }, [status, onClose]);

  useEffect(() => {
    if (!open) return;
    textareaRef.current?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") close();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, close]);

  if (!open) return null;

  const tooShort = message.trim().length < SUPPORT_MESSAGE_MIN;
  // Mismo contenido que habría viajado en el POST, por si hay que sacarlo a
  // mano. Se calcula siempre para que la salida por correo esté lista en el
  // instante en que el envío falla, sin otra petición de por medio.
  const context = { message, screen, errorRef, contactEmail: email };
  const mailto = buildSupportMailto(context);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (tooShort) {
      setErrorText("Escribe un poco más para que podamos entender qué ha pasado.");
      return;
    }
    setStatus("sending");
    setErrorText(null);
    try {
      await sendSupportRequest({
        message,
        screen,
        errorRef,
        contactEmail: email,
      });
      setStatus("sent");
    } catch (err) {
      setStatus("error");
      setErrorText(explain(err));
    }
  }

  async function copyToClipboard() {
    try {
      await navigator.clipboard.writeText(buildSupportText(context));
      setCopied("done");
    } catch {
      // Sin permiso o sin contexto seguro. No pasa nada: el texto sigue en el
      // formulario, que no se ha ido a ninguna parte.
      setCopied("failed");
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-ink-900/40 p-4 sm:items-center"
      // Cerrar al pulsar fuera; el clic dentro no debe propagarse.
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="support-title"
        className="max-h-full w-full max-w-md overflow-y-auto rounded-2xl bg-white p-6 shadow-card"
      >
        {status === "sent" ? (
          <>
            <h2 id="support-title" className="text-lg font-semibold text-slate-900">
              Mensaje recibido
            </h2>
            <p className="mt-2 text-sm text-slate-600">
              Lo lee una persona y respondemos en menos de 4 h laborables (L-V, de 9:00
              a 18:00). Si necesitamos más datos te escribimos
              {email.trim() ? ` a ${email.trim()}` : " al correo de tu cuenta"}.
            </p>
            {/* El servidor contesta "ok" en cuanto acepta el aviso, incluso si
                luego falla el envío del correo interno (queda en su registro).
                Como desde aquí no podemos distinguirlo, no prometemos que haya
                llegado a una bandeja: damos la vía alternativa por si acaso. */}
            <p className="mt-2 text-sm text-slate-500">
              Si pasado ese plazo no sabes nada de nosotros, escríbenos a{" "}
              <a className="text-selai-600 underline" href={`mailto:${SUPPORT_EMAIL}`}>
                {SUPPORT_EMAIL}
              </a>
              : querrá decir que el aviso se ha quedado por el camino.
            </p>
            <button
              type="button"
              onClick={close}
              className="mt-5 w-full rounded-md bg-selai-600 px-3 py-2 text-sm font-medium text-white hover:bg-selai-700"
            >
              Cerrar
            </button>
          </>
        ) : (
          <form onSubmit={submit}>
            <h2 id="support-title" className="text-lg font-semibold text-slate-900">
              Reportar un problema
            </h2>
            <p className="mt-1 text-sm text-slate-600">
              Cuéntanos qué estabas haciendo y qué ha pasado. Cuanto más concreto, antes
              lo arreglamos.
            </p>

            <label htmlFor="support-message" className="mt-4 block text-sm font-medium text-slate-700">
              Qué ha pasado
            </label>
            <textarea
              id="support-message"
              ref={textareaRef}
              value={message}
              onChange={(event) => {
                setMessage(event.target.value.slice(0, SUPPORT_MESSAGE_MAX));
                // "Copiado ✓" dejaría de ser verdad en cuanto cambia el texto.
                setCopied("idle");
              }}
              rows={5}
              required
              placeholder="Por ejemplo: he pulsado en una conversación de la bandeja y la pantalla se ha quedado en blanco."
              className="mt-1 w-full resize-y rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-selai-500 focus:outline-none"
            />
            <div className="mt-1 text-right text-xs text-slate-400">
              {message.length}/{SUPPORT_MESSAGE_MAX}
            </div>

            <label htmlFor="support-email" className="mt-3 block text-sm font-medium text-slate-700">
              Correo para responderte <span className="font-normal text-slate-400">(opcional)</span>
            </label>
            <input
              id="support-email"
              type="email"
              value={email}
              onChange={(event) => {
                setEmail(event.target.value);
                setCopied("idle");
              }}
              placeholder="Si prefieres que te escribamos a otra dirección"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-selai-500 focus:outline-none"
            />

            {/* Lo que se manda además del texto, dicho sin rodeos. */}
            <p className="mt-3 text-xs text-slate-500">
              Enviamos también la pantalla en la que estabas
              {screen ? ` (${screen})` : ""}
              {errorRef ? ` y el código del fallo (${errorRef})` : ""}.
            </p>

            {/* Fallo de validación nuestro: no hace falta plan B, basta con
                corregir lo escrito. */}
            {errorText && status !== "error" && (
              <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                {errorText}
              </div>
            )}

            {/* El envío ha fallado. Es el escenario para el que existe este
                formulario, así que la salida por correo pasa a ser la acción
                principal y lleva ya escrito lo mismo que iba en el envío. El
                texto sigue arriba, en el propio campo, por si el correo
                tampoco funciona. */}
            {status === "error" && (
              <div className="mt-4 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
                <p className="font-medium">{errorText}</p>
                <p className="mt-1">
                  Tu mensaje no se ha perdido. Mándanoslo por correo y lo leemos igual.
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <a
                    href={mailto}
                    className="rounded-md bg-selai-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-selai-700"
                  >
                    Escribir por correo
                  </a>
                  <button
                    type="button"
                    onClick={() => void copyToClipboard()}
                    className="rounded-md border border-amber-300 bg-white px-3 py-1.5 text-sm font-medium text-amber-900 hover:bg-amber-100"
                  >
                    {copied === "done" ? "Copiado ✓" : "Copiar el mensaje"}
                  </button>
                </div>
                <p className="mt-2 text-xs">
                  {copied === "failed"
                    ? `Tu navegador no nos deja copiarlo. Selecciona el texto de arriba y escríbenos a ${SUPPORT_EMAIL}.`
                    : `Si no se abre tu programa de correo, cópialo y escríbenos a ${SUPPORT_EMAIL}.`}
                </p>
              </div>
            )}

            <div className="mt-5 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={close}
                className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
              >
                Cancelar
              </button>
              <button
                type="submit"
                disabled={status === "sending" || tooShort}
                className="rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
              >
                {status === "sending" ? "Enviando…" : "Enviar"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

/** Qué decirle al usuario según lo que haya respondido el servidor. */
function explain(err: unknown): string {
  const status = statusOf(err);
  if (status === 401 || status === 403) {
    return (
      "Para enviarlo desde aquí hay que haber iniciado sesión, y ahora mismo no " +
      "hay ninguna sesión válida."
    );
  }
  if (status === 429) {
    // El backend limita a cinco mensajes cada diez minutos por usuario
    // (app/api/support.py). Decir "inténtalo otra vez" aquí sería mandar al
    // usuario contra la misma pared.
    return "Has enviado varios mensajes seguidos y no admite más de momento.";
  }
  if (status === 422) {
    return "El mensaje no ha pasado la validación del servidor.";
  }
  if (status !== null && status >= 500) {
    return "Nuestro servidor ha fallado al recibirlo.";
  }
  // Sin código de respuesta no sabemos si es la conexión, el servidor o el
  // camino entre los dos; decir cuál sería inventárselo.
  return "No hemos podido enviarlo y no sabemos por qué.";
}
