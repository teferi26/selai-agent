"use client";

// Aviso de cookies mínimo y honesto (backlog #138). selai solo usa cookies
// técnicas (sesión), así que no hay gestor de consentimiento: solo una barra
// informativa que se cierra y se recuerda en localStorage.
//
// Medimos visitas con Vercel Web Analytics (backlog #156), que es cookieless
// y agregado: no guarda cookies ni identificadores del visitante. El texto lo
// dice explícitamente para no prometer menos de lo que hacemos.
import Link from "next/link";
import { useState, useSyncExternalStore } from "react";

const STORAGE_KEY = "selai-cookies-ok";
const STORAGE_CHANGE_EVENT = "selai:cookie-notice-change";

function readNoticeOpen(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) !== "1";
  } catch {
    return false;
  }
}

function readServerNoticeOpen(): boolean {
  return false;
}

function subscribeToNotice(onStoreChange: () => void): () => void {
  const onStorage = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY) onStoreChange();
  };
  window.addEventListener("storage", onStorage);
  window.addEventListener(STORAGE_CHANGE_EVENT, onStoreChange);
  return () => {
    window.removeEventListener("storage", onStorage);
    window.removeEventListener(STORAGE_CHANGE_EVENT, onStoreChange);
  };
}

export function CookieNotice() {
  const noticeOpen = useSyncExternalStore(
    subscribeToNotice,
    readNoticeOpen,
    readServerNoticeOpen,
  );
  const [dismissedForThisRender, setDismissedForThisRender] = useState(false);
  const show = noticeOpen && !dismissedForThisRender;

  const accept = () => {
    setDismissedForThisRender(true);
    try {
      localStorage.setItem(STORAGE_KEY, "1");
      window.dispatchEvent(new Event(STORAGE_CHANGE_EVENT));
    } catch {
      // Storage unavailable: the local state still dismisses this instance.
    }
  };

  if (!show) return null;

  return (
    <div
      role="region"
      aria-label="Aviso de cookies"
      className="fixed inset-x-0 bottom-0 z-50 px-4 pb-3"
      style={{ paddingBottom: "max(0.75rem, env(safe-area-inset-bottom))" }}
    >
      <div className="mx-auto max-w-[720px] glass bg-ink-900/95 text-selai-50 rounded-2xl px-4 py-3 md:px-5 flex flex-col sm:flex-row sm:items-center gap-3 shadow-card">
        <p className="text-[13px] leading-snug text-selai-100/90 flex-1">
          Solo usamos cookies técnicas imprescindibles (tu sesión). Contamos las visitas de
          forma anónima y agregada, sin cookies. Sin rastreo personal y sin publicidad.{" "}
          <Link href="/privacidad" className="underline decoration-dotted hover:text-white">
            Más detalle en la política de privacidad
          </Link>
          .
        </p>
        <button
          onClick={accept}
          className="flex-shrink-0 self-start sm:self-auto min-h-11 px-4 py-2 rounded-xl bg-white text-ink-900 text-[13px] font-medium hover:bg-selai-100 transition"
        >
          Entendido
        </button>
      </div>
    </div>
  );
}
