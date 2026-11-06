"use client";

// Sticky mobile CTA (backlog #87): "Empieza gratis" bar that slides in after
// scrolling past the hero. Mobile only (<768px), discreet, dismissible for
// the session.
import Link from "next/link";
import { useState, useSyncExternalStore } from "react";

import { trackFunnel } from "./analytics";
import { IArrow, IX } from "./icons";

const SESSION_KEY = "selai-sticky-cta-closed";
const DISMISS_EVENT = "selai:sticky-cta-dismissed";

function readDismissed(): boolean {
  try {
    return sessionStorage.getItem(SESSION_KEY) === "1";
  } catch {
    return false;
  }
}

function readServerBoolean(): boolean {
  return false;
}

function subscribeToDismissal(onStoreChange: () => void): () => void {
  const onStorage = (event: StorageEvent) => {
    if (event.key === SESSION_KEY) onStoreChange();
  };
  window.addEventListener("storage", onStorage);
  window.addEventListener(DISMISS_EVENT, onStoreChange);
  return () => {
    window.removeEventListener("storage", onStorage);
    window.removeEventListener(DISMISS_EVENT, onStoreChange);
  };
}

function readPastHero(): boolean {
  return window.scrollY > window.innerHeight * 0.9;
}

function subscribeToViewport(onStoreChange: () => void): () => void {
  window.addEventListener("scroll", onStoreChange, { passive: true });
  window.addEventListener("resize", onStoreChange);
  return () => {
    window.removeEventListener("scroll", onStoreChange);
    window.removeEventListener("resize", onStoreChange);
  };
}

export function StickyCTA() {
  const visible = useSyncExternalStore(
    subscribeToViewport,
    readPastHero,
    readServerBoolean,
  );
  const dismissedInStorage = useSyncExternalStore(
    subscribeToDismissal,
    readDismissed,
    readServerBoolean,
  );
  const [dismissedForThisRender, setDismissedForThisRender] = useState(false);
  const closed = dismissedInStorage || dismissedForThisRender;

  const dismiss = () => {
    setDismissedForThisRender(true);
    try {
      sessionStorage.setItem(SESSION_KEY, "1");
      window.dispatchEvent(new Event(DISMISS_EVENT));
    } catch {
      // storage unavailable (private mode) — dismiss for this render only
    }
  };

  if (closed) return null;

  return (
    <div
      className={`md:hidden fixed inset-x-0 bottom-0 z-40 px-4 pb-4 transition-all duration-300 ${
        visible ? "translate-y-0 opacity-100" : "translate-y-full opacity-0 pointer-events-none"
      }`}
      style={{ paddingBottom: "max(1rem, env(safe-area-inset-bottom))" }}
      aria-hidden={!visible}
    >
      <div className="glass bg-paper/90 ring-1 ring-ink-900/10 shadow-card rounded-2xl p-2.5 flex items-center gap-2.5">
        <div className="pl-2 min-w-0 flex-1">
          <div className="text-[13px] font-semibold text-ink-900 truncate">
            14 días gratis, sin tarjeta
          </div>
          <div className="text-[11px] text-ink-500 truncate">Operativa el primer día</div>
        </div>
        <Link
          href="/sign-up"
          onClick={() => trackFunnel("signup_click", { origen: "sticky_movil" })}
          className="min-h-11 flex-shrink-0 inline-flex items-center gap-1.5 px-4 py-2.5 rounded-xl bg-accent text-white text-[13px] font-medium hover:bg-[#FF6A45]"
        >
          Empieza gratis
          <IArrow size={13} />
        </Link>
        <button
          onClick={dismiss}
          className="flex-shrink-0 w-11 h-11 rounded-lg text-ink-500 hover:bg-ink-900/5 flex items-center justify-center"
          aria-label="Cerrar aviso"
        >
          <IX size={15} />
        </button>
      </div>
    </div>
  );
}
