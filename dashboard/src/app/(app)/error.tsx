// Pantalla de error del panel. Cubre las páginas de (app): hoy varias son
// server components que llaman a serverApi y dejan escapar ApiError, así que
// sin esto el usuario veía el "Application error" gris de Next.
// El menú lateral sigue visible: este boundary vive dentro de (app)/layout.
"use client";

import { ErrorScreen } from "@/components/ui/ErrorScreen";

export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <ErrorScreen
      error={error}
      reset={reset}
      homeHref="/inbox"
      homeLabel="Ir a la bandeja"
      description="No es culpa tuya: no hemos podido cargar esta parte del panel. A veces es un tropiezo puntual y a la segunda entra."
      fullScreen={false}
    />
  );
}
