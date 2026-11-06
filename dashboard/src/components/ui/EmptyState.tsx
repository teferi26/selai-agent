// Estado vacío del panel: mismo criterio que EmptyView en la app móvil —
// decir qué falta y, si hay algo que hacer al respecto, ofrecerlo.
import type { ReactNode } from "react";

type Props = {
  title: string;
  subtitle?: string;
  /** Icono opcional; por defecto una bandeja vacía. */
  icon?: ReactNode;
  ctaLabel?: string;
  ctaHref?: string;
  onCta?: () => void;
  /** Acciones secundarias junto al CTA. */
  children?: ReactNode;
  className?: string;
};

const DEFAULT_ICON = (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 13h4l1.5 3h5L16 13h4" />
    <path d="M5.5 5.5h13l1.5 7.5v4a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17v-4z" />
  </svg>
);

export function EmptyState({
  title,
  subtitle,
  icon = DEFAULT_ICON,
  ctaLabel,
  ctaHref,
  onCta,
  children,
  className = "",
}: Props) {
  return (
    <div
      className={`flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-slate-300 bg-white px-6 py-14 text-center ${className}`}
    >
      <span
        aria-hidden
        className="flex h-12 w-12 items-center justify-center rounded-full bg-selai-50 text-selai-600"
      >
        {icon}
      </span>
      <p className="text-base font-semibold text-slate-900">{title}</p>
      {subtitle && <p className="max-w-md text-sm text-slate-500">{subtitle}</p>}
      {(ctaLabel || children) && (
        <div className="mt-2 flex flex-wrap items-center justify-center gap-3">
          {ctaLabel && ctaHref && (
            <a
              href={ctaHref}
              className="rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700"
            >
              {ctaLabel}
            </a>
          )}
          {ctaLabel && !ctaHref && onCta && (
            <button
              type="button"
              onClick={onCta}
              className="rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700"
            >
              {ctaLabel}
            </button>
          )}
          {children}
        </div>
      )}
    </div>
  );
}
