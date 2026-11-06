"use client";

// Animation primitives: Reveal (in-view fade-up), Counter (tween numbers
// when visible), StaggerWords (per-word entrance). All gated by
// IntersectionObserver and CSS — no runtime motion library.
import { Fragment, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";

type InViewOptions = {
  margin?: string;
  threshold?: number;
  once?: boolean;
};

// useLayoutEffect avisa por consola durante el render en servidor; en el
// servidor no hay nada que medir, así que allí se degrada a useEffect.
const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

function useInView<T extends HTMLElement>({
  margin = "-80px",
  threshold = 0.1,
  once = true,
}: InViewOptions = {}) {
  const ref = useRef<T | null>(null);
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            setInView(true);
            if (once) obs.disconnect();
          }
        });
      },
      { rootMargin: margin, threshold },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [margin, once, threshold]);
  return [ref, inView] as const;
}

/** (19) El estado de `Reveal`, pero al revés que `useInView`: el punto de
 * partida es VISIBLE.
 *
 * Antes el HTML salía con `opacity: 0` y el contenido sólo volvía a aparecer
 * si arrancaba el JS y disparaba el IntersectionObserver. En /ayuda —la página
 * a la que mandamos al cliente precisamente cuando algo se ha roto— eso dejaba
 * el encabezado y la dirección de soporte en blanco ante cualquier bundle que
 * no cargara, un navegador antiguo o un bloqueador demasiado entusiasta. Una
 * animación no puede ser el interruptor de la información.
 *
 * Ahora sólo se oculta lo que el JS, ya montado, ha comprobado que está FUERA
 * de la pantalla: ocultar algo que nadie está viendo no produce parpadeo, y lo
 * que ya se veía en el primer pintado (encabezados, hero) se queda quieto en
 * vez de desaparecer y volver. Sin JS no se oculta nada en ningún caso.
 */
/** `idle`: como salió del servidor, visible y sin animación (también es el
 *  estado definitivo si el JS no arranca). `hidden`: el JS ha comprobado que
 *  está fuera de pantalla y lo ha escondido *de golpe* — con transición sería
 *  un desvanecido que nadie pidió. `shown`: entra en pantalla y ahí sí, la
 *  animación de entrada. */
type RevealPhase = "idle" | "hidden" | "shown";

function useReveal<T extends HTMLElement>({
  margin = "-80px",
  threshold = 0.1,
  once = true,
}: InViewOptions = {}) {
  const ref = useRef<T | null>(null);
  const [phase, setPhase] = useState<RevealPhase>("idle");
  useIsoLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Ya en pantalla: no se toca. Se pierde la animación de entrada, pero lo
    // que importa es el contenido, y ocultar algo que el usuario ya está
    // mirando para volver a enseñárselo es peor que no animar nada.
    const rect = el.getBoundingClientRect();
    if (rect.bottom > 0 && rect.top < window.innerHeight) return;
    setPhase("hidden");
    const obs = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            setPhase("shown");
            if (once) obs.disconnect();
          }
        });
      },
      { rootMargin: margin, threshold },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [margin, once, threshold]);
  return [ref, phase] as const;
}

export function Reveal({
  children,
  delay = 0,
  className = "",
  y = 24,
}: {
  children: ReactNode;
  delay?: number;
  className?: string;
  y?: number;
}) {
  const [ref, phase] = useReveal<HTMLDivElement>();
  const hidden = phase === "hidden";
  return (
    <div
      ref={ref}
      className={className}
      style={{
        opacity: hidden ? 0 : 1,
        transform: hidden ? `translateY(${y}px)` : "translateY(0)",
        // El navegador aplica la transición del estilo *posterior* al cambio,
        // así que declararla sólo en `shown` anima la entrada y deja el
        // ocultado inicial instantáneo.
        transition:
          phase === "shown"
            ? `opacity 0.8s cubic-bezier(0.16,1,0.3,1) ${delay}s, transform 0.8s cubic-bezier(0.16,1,0.3,1) ${delay}s`
            : "none",
      }}
    >
      {children}
    </div>
  );
}

export function Counter({
  to,
  prefix = "",
  suffix = "",
  duration = 1400,
  decimals = 0,
}: {
  to: number;
  prefix?: string;
  suffix?: string;
  duration?: number;
  decimals?: number;
}) {
  const [ref, inView] = useInView<HTMLSpanElement>();
  const [v, setV] = useState(0);
  useEffect(() => {
    if (!inView) return;
    const start = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setV(to * eased);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [inView, to, duration]);
  return (
    <span ref={ref} className="font-mono tabular">
      {prefix}
      {v.toFixed(decimals)}
      {suffix}
    </span>
  );
}

export function StaggerWords({
  text,
  className = "",
  delay = 0,
  wordDelay = 0.045,
}: {
  text: string;
  className?: string;
  delay?: number;
  wordDelay?: number;
}) {
  const words = text.split(" ");
  return (
    <span className={`stagger ${className}`}>
      {words.map((w, i) => (
        <Fragment key={i}>
          <span style={{ animationDelay: `${delay + i * wordDelay}s` } as CSSProperties}>{w}</span>
          {i < words.length - 1 && <span className="space"> </span>}
        </Fragment>
      ))}
    </span>
  );
}

/** Hook re-exported for sections that need their own InView trigger
 *  (e.g. HowItWorks step graphics that toggle the .in-view class). */
export function useStepInView<T extends HTMLElement = HTMLDivElement>(opts: InViewOptions = {}) {
  return useInView<T>(opts);
}
