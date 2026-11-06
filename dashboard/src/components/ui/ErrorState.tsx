// Bloque de error del panel.
//
// Copia el criterio de la app móvil (mobile/src/components/ui.tsx, ErrorView):
// SIEMPRE hay reintento y, cuando procede, una segunda salida. Si quien lo usa
// no pasa ninguna de las dos, ofrecemos recargar la página — nunca dejamos al
// usuario mirando un mensaje sin nada que pulsar.
"use client";

import type { ReactNode } from "react";

import { Spinner } from "./Spinner";

type Props = {
  title?: string;
  /** Explicación en lenguaje llano. null para dejar solo el título. */
  message?: string | null;
  onRetry?: () => void;
  retryLabel?: string;
  /** True mientras el reintento está en curso. */
  retrying?: boolean;
  /** Segunda salida: o callback, o enlace. */
  exitLabel?: string;
  onExit?: () => void;
  exitHref?: string;
  /** "inline" para dentro de una tarjeta; "page" para ocupar el contenido. */
  variant?: "inline" | "page";
  /** Acciones extra (p. ej. el botón de reportar un problema). */
  children?: ReactNode;
  className?: string;
};

export function ErrorState({
  title = "No hemos podido cargar los datos",
  message = "Comprueba tu conexión e inténtalo de nuevo.",
  onRetry,
  retryLabel = "Reintentar",
  retrying = false,
  exitLabel,
  onExit,
  exitHref,
  variant = "inline",
  children,
  className = "",
}: Props) {
  // Sin reintento ni salida propios, recargar es lo único honesto que podemos
  // ofrecer: al menos vuelve a pedir los datos desde cero.
  const retry = onRetry ?? (() => window.location.reload());
  const label = onRetry ? retryLabel : "Recargar la página";

  const page = variant === "page";

  return (
    <div
      role="alert"
      className={
        page
          ? `flex flex-col items-center justify-center gap-3 rounded-xl border border-slate-200 bg-white px-6 py-14 text-center ${className}`
          : `rounded-md border border-red-200 bg-red-50 px-3 py-3 text-sm ${className}`
      }
    >
      {page && (
        <span
          aria-hidden
          className="flex h-12 w-12 items-center justify-center rounded-full bg-red-50 text-red-500"
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <path d="M12 8v5" />
            <path d="M12 16.5v.5" />
            <circle cx="12" cy="12" r="9" />
          </svg>
        </span>
      )}

      <p className={page ? "text-base font-semibold text-slate-900" : "font-medium text-red-800"}>
        {title}
      </p>
      {message && (
        <p className={page ? "max-w-sm text-sm text-slate-500" : "mt-1 text-red-700"}>{message}</p>
      )}

      <div className={`flex flex-wrap items-center gap-2 ${page ? "mt-2 justify-center" : "mt-3"}`}>
        <button
          type="button"
          onClick={retry}
          disabled={retrying}
          className="inline-flex items-center gap-2 rounded-md bg-selai-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
        >
          {retrying && <Spinner size={14} label={null} />}
          {retrying ? "Reintentando…" : label}
        </button>

        {exitLabel && exitHref && (
          <a
            href={exitHref}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            {exitLabel}
          </a>
        )}
        {exitLabel && !exitHref && onExit && (
          <button
            type="button"
            onClick={onExit}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            {exitLabel}
          </button>
        )}
        {children}
      </div>
    </div>
  );
}
