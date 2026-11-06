// Indicador de carga del panel. El CSS global ya respeta
// prefers-reduced-motion, así que aquí no hace falta nada extra.

export function Spinner({
  size = 20,
  className = "",
  /** Texto para lectores de pantalla. null cuando el contexto ya lo explica. */
  label = "Cargando",
}: {
  size?: number;
  className?: string;
  label?: string | null;
}) {
  return (
    <svg
      className={`animate-spin ${className}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      role={label ? "status" : "presentation"}
      aria-label={label ?? undefined}
      aria-hidden={label ? undefined : true}
    >
      <circle
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="3"
        className="opacity-20"
      />
      <path
        d="M22 12a10 10 0 0 0-10-10"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** Carga a pantalla completa dentro del área de contenido. */
export function LoadingState({ label = "Cargando…" }: { label?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-slate-500">
      <Spinner size={28} className="text-selai-600" label={null} />
      <p className="text-sm">{label}</p>
    </div>
  );
}
