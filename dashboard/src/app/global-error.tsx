// Último cortafuegos: se muestra cuando falla el propio layout raíz (o
// cualquier error que no haya capturado un error.tsx más abajo). Sustituye al
// documento entero, así que trae su propio <html>/<body> y su propio CSS.
"use client";

import "./globals.css";

import { ErrorScreen } from "@/components/ui/ErrorScreen";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    // Sin las variables de next/font que pone el layout raíz, `font-sans` cae
    // en Inter/system-ui — suficiente para una pantalla que ya es la excepción.
    <html lang="es-ES">
      <body className="bg-paper font-sans text-ink-900 antialiased">
        <ErrorScreen
          error={error}
          reset={reset}
          homeHref="/"
          homeLabel="Volver al inicio"
          title="selai no ha podido cargar"
          description="No es culpa tuya: ha fallado algo básico de la aplicación, no solo esta página. Prueba a reintentar."
        />
      </body>
    </html>
  );
}
