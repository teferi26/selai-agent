// Pantalla de error con la marca selai, compartida por los tres error
// boundaries del panel (global-error, (app) y (marketing)).
//
// Los enlaces de salida son <a> y no <Link> a propósito: si hemos llegado aquí
// puede que lo roto sea el propio árbol de rutas, y una navegación completa es
// la salida que siempre funciona.
"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";

import { SelaiLogo } from "@/components/marketing/SelaiLogo";
import { captureError, sentryEnabled } from "@/lib/monitoring";

import { SupportDialog } from "./SupportDialog";

type Props = {
  error: Error & { digest?: string };
  /** `reset` que pasa Next: vuelve a renderizar el segmento que falló. */
  reset: () => void;
  homeHref: string;
  homeLabel: string;
  /** Enlace al centro de ayuda; null para ocultarlo. */
  helpHref?: string | null;
  title?: string;
  description?: string;
  /** false cuando se pinta dentro del shell del panel, que ya ocupa la pantalla. */
  fullScreen?: boolean;
};

type CapturedError = {
  error: Error;
  sentryId: string | null;
};

export function ErrorScreen({
  error,
  reset,
  homeHref,
  homeLabel,
  helpHref = "/ayuda",
  title = "Se nos ha roto esta pantalla",
  description = "No es culpa tuya: algo ha fallado por nuestra parte al cargarla. A veces es un tropiezo puntual y a la segunda entra.",
  fullScreen = true,
}: Props) {
  // En global-error el pathname sigue disponible (el boundary raíz se pinta
  // dentro del proveedor de contexto del router), pero el tipado de Next admite
  // null; si algún día lo fuera, mandamos la ruta del navegador antes que nada.
  const pathname = usePathname();
  const [supportOpen, setSupportOpen] = useState(false);
  const [captured, setCaptured] = useState<CapturedError | null>(null);
  const digest = error.digest ?? null;

  useEffect(() => {
    let active = true;
    // La captura sincroniza con Sentry después del commit. Encadenarla como
    // trabajo asíncrono evita una segunda renderización síncrona dentro del
    // propio effect y permite ignorar una respuesta de un error ya sustituido.
    void Promise.resolve().then(() => {
      const sentryId = captureError(error, { digest });
      if (active) setCaptured({ error, sentryId });
    });
    return () => {
      active = false;
    };
  }, [error, digest]);

  const sentryId = captured?.error === error ? captured.sentryId : null;

  // Lo que identifica el fallo para quien lo va a mirar: el digest es lo que ve
  // el usuario en pantalla y el id de Sentry es lo que lleva a la traza.
  const errorRef =
    [digest, sentryId ? `sentry:${sentryId}` : null]
      .filter((part): part is string => part !== null)
      .join(" · ") || null;

  return (
    <main
      className={`flex items-center justify-center px-6 py-16 text-ink-900 ${
        fullScreen ? "min-h-screen bg-paper" : "min-h-[60vh]"
      }`}
    >
      <div className="w-full max-w-[560px] text-center">
        <div className="mb-8 flex justify-center">
          <SelaiLogo className="text-[30px]" />
        </div>

        <h1 className="font-display text-[28px] font-bold leading-tight md:text-[34px]">
          {title}
        </h1>
        <p className="mt-4 text-[16px] leading-relaxed text-ink-500">{description}</p>

        {/* Honestidad: sin Sentry configurado nadie se entera si el usuario no
            nos lo cuenta, y conviene que lo sepa antes de irse. */}
        <p className="mt-3 text-[14px] leading-relaxed text-ink-500">
          {sentryEnabled
            ? "El fallo ha quedado registrado en nuestro sistema de errores. Si nos cuentas qué estabas haciendo, lo arreglamos antes."
            : "Este fallo no nos llega solo: si no nos lo cuentas, no nos enteramos."}
        </p>

        <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
          <button
            type="button"
            onClick={reset}
            className="rounded-full bg-selai-600 px-5 py-2.5 text-[15px] font-semibold text-white hover:bg-selai-700"
          >
            Reintentar
          </button>
          <a
            href={homeHref}
            className="rounded-full border border-ink-900/10 bg-white px-5 py-2.5 text-[15px] font-semibold text-ink-900 hover:bg-selai-50"
          >
            {homeLabel}
          </a>
          <button
            type="button"
            onClick={() => setSupportOpen(true)}
            className="rounded-full px-5 py-2.5 text-[15px] font-semibold text-selai-700 underline decoration-selai-300 underline-offset-4 hover:text-selai-800"
          >
            Reportar un problema
          </button>
        </div>

        {helpHref && (
          <p className="mt-6 text-[13px] text-ink-500">
            O mira si está resuelto en el{" "}
            <a href={helpHref} className="text-selai-600 underline">
              centro de ayuda
            </a>
            .
          </p>
        )}

        {digest && (
          <p className="mt-8 font-mono text-[11px] text-ink-300">
            Código del fallo: {digest}
          </p>
        )}
      </div>

      <SupportDialog
        open={supportOpen}
        onClose={() => setSupportOpen(false)}
        screen={pathname ?? (typeof window !== "undefined" ? window.location.pathname : null)}
        errorRef={errorRef}
      />
    </main>
  );
}
