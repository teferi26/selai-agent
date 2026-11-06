"use client";

import { type ReactNode, useLayoutEffect, useRef } from "react";

/**
 * El histórico vive en su propio panel desplazable: al abrir una conversación
 * se muestra el mensaje más reciente sin empujar el compositor fuera de la
 * pantalla. Si el servidor incorpora un mensaje tras responder, volvemos al
 * final; leer mensajes antiguos a mano no dispara ningún salto.
 */
export default function MessageViewport({
  children,
  messageCount,
}: {
  children: ReactNode;
  messageCount: number;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    viewport.scrollTop = viewport.scrollHeight;
  }, [messageCount]);

  return (
    <div
      ref={viewportRef}
      className="h-[42vh] min-h-64 max-h-[34rem] overflow-y-auto rounded-xl border border-slate-200 bg-slate-50/60 p-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-selai-500 focus-visible:ring-offset-2"
      role="region"
      aria-label="Historial de mensajes. Se muestra primero el mensaje más reciente."
      tabIndex={0}
    >
      <div className="space-y-3">{children}</div>
    </div>
  );
}
