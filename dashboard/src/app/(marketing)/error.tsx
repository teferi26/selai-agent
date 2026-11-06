// Pantalla de error de la web pública. Ojo: aquí el visitante puede no haber
// iniciado sesión y el formulario de soporte exige sesión, así que para él el
// envío siempre acaba en 401 y la vía real es el correo que ofrece el diálogo
// (con su mensaje ya redactado). Que ese 401 llegue como JSON y no como el
// HTML del login depende de la excepción de `/api/backend/api/v1/support` en
// proxy.ts: si alguien la quita, el diálogo deja de saber qué contar.
"use client";

import { ErrorScreen } from "@/components/ui/ErrorScreen";

export default function MarketingError({
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
      homeHref="/"
      homeLabel="Volver a la portada"
      description="No es culpa tuya: algo ha fallado por nuestra parte al cargar esta página. A veces es un tropiezo puntual y a la segunda entra."
    />
  );
}
