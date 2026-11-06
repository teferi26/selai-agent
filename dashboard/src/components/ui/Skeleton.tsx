// Esqueletos de carga. Equivalente web de mobile/src/components/ui.tsx:
// bloques grises que ocupan el sitio del contenido para que la página no dé
// saltos cuando llegan los datos.

export function Skeleton({
  width,
  height = 12,
  round = false,
  className = "",
}: {
  width?: number | string;
  height?: number | string;
  round?: boolean;
  className?: string;
}) {
  return (
    <span
      aria-hidden
      className={`block animate-pulse bg-slate-200 ${round ? "rounded-full" : "rounded"} ${className}`}
      style={{ width, height }}
    />
  );
}

/** Fila tipo tarjeta (avatar + dos líneas), como las de la bandeja. */
export function SkeletonRow() {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-4">
      <Skeleton width={36} height={36} round />
      <div className="flex-1 space-y-2">
        <Skeleton width="45%" height={12} />
        <Skeleton width="80%" height={10} />
      </div>
    </div>
  );
}

export function SkeletonList({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-3" role="status" aria-label="Cargando contenido">
      {Array.from({ length: rows }, (_, i) => (
        <SkeletonRow key={i} />
      ))}
    </div>
  );
}

/** Columnas como clases completas: Tailwind necesita verlas literales para
 * generarlas, y concatenarlas dejaría dos `grid-cols-*` peleándose. */
const COLUMNS: Record<2 | 3 | 4, string> = {
  2: "sm:grid-cols-2",
  3: "sm:grid-cols-2 lg:grid-cols-3",
  4: "md:grid-cols-2 lg:grid-cols-4",
};

/** Rejilla de tarjetas de métrica. */
export function SkeletonMetrics({
  cards = 6,
  columns = 3,
}: {
  cards?: number;
  columns?: 2 | 3 | 4;
}) {
  return (
    <div
      className={`grid grid-cols-1 gap-4 ${COLUMNS[columns]}`}
      role="status"
      aria-label="Cargando métricas"
    >
      {Array.from({ length: cards }, (_, i) => (
        <div key={i} className="space-y-3 rounded-xl border border-slate-200 bg-white p-4">
          <Skeleton width={28} height={28} round />
          <Skeleton width="50%" height={24} />
          <Skeleton width="70%" height={10} />
        </div>
      ))}
    </div>
  );
}
