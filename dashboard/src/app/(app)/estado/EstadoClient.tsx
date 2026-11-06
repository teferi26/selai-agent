// "Estado del sistema".
//
// Antes pingaba /healthz, que por diseño no toca ninguna dependencia: contesta
// "ok" mientras el proceso siga vivo, así que la página decía "Operativo" con
// la base de datos caída. Ahora pregunta a /readyz, que sí comprueba Postgres,
// Redis y los procesos en segundo plano, y pintamos el detalle uno a uno.
//
// /readyz contesta 503 cuando Postgres no responde: hay que leer el cuerpo
// igualmente, porque es justo cuando más información lleva.
//
// La consulta va por el proxy del propio panel (/api/backend/...), no contra
// https://api.selai.es a pelo. Tres razones, todas vividas:
//   - una petición del navegador a otro dominio depende de CORS. La lista de
//     orígenes permitidos vive en el backend y no cubre un dominio nuevo ni un
//     preview con dominio propio; cualquier bloqueo (o un bloqueador de
//     anuncios) hacía que esta página gritara "Incidencia" con todo en orden.
//   - una URL de producción escrita a mano miente en local y en pruebas:
//     comprobaba la salud de producción mientras el panel hablaba con otro
//     backend. Es la misma mentira que esta página vino a quitar.
//   - el proxy conserva el código de estado y el cuerpo, así que el 503 con
//     detalle sigue llegando entero.
//
// Desde (15) la página es PÚBLICA (proxy.ts). Lo que se pinta sin sesión
// tiene que ser exactamente lo que un desconocido puede saber: si el servicio
// responde. Qué integraciones tiene puestas la plataforma no lo es —
// `showPlatformConfig` lo decide el componente de servidor (page.tsx).
"use client";

import { useCallback, useEffect, useState } from "react";

const READY_URL = "/api/backend/readyz";

// Sin esto la página se queda en "Comprobando…" para siempre (y con el botón
// de reintentar deshabilitado) si el backend acepta la conexión y no contesta.
const TIMEOUT_MS = 8000;

/** Cuerpo de /readyz. `null` en celery significa "no se ha podido comprobar". */
type Readiness = {
  postgres: boolean;
  redis: boolean;
  celery: boolean | null;
  meta_configured: boolean;
  vapi_configured: boolean;
  stripe_configured: boolean;
};

/** ¿Esto es de verdad una respuesta de /readyz?
 *
 * El proxy contesta `{"detail":"unauthenticated"}` con 401 cuando la sesión ha
 * caducado, y cualquier intermediario puede colar su propio JSON. Sin esta
 * comprobación `!ready.postgres` era `!undefined` y la página declaraba una
 * incidencia por una sesión caducada. */
function isReadiness(body: unknown): body is Readiness {
  if (typeof body !== "object" || body === null) return false;
  const b = body as Record<string, unknown>;
  return typeof b.postgres === "boolean" && typeof b.redis === "boolean";
}

type Check = { key: keyof Readiness; label: string; detail: string };

// Sin jerga: lo que le importa a una inmobiliaria es qué deja de funcionar.
const CHECKS: Check[] = [
  {
    key: "postgres",
    label: "Base de datos",
    detail: "Donde viven tus leads, conversaciones y visitas.",
  },
  {
    key: "redis",
    label: "Memoria de trabajo",
    detail: "Coordina los avisos y las tareas en curso.",
  },
  {
    key: "celery",
    label: "Tareas en segundo plano",
    detail: "Recordatorios de visita, seguimientos y envíos programados.",
  },
];

const CONFIGURED: Check[] = [
  { key: "meta_configured", label: "WhatsApp", detail: "Número de WhatsApp Business conectado." },
  { key: "vapi_configured", label: "Llamadas de voz", detail: "Servicio de voz conectado." },
  { key: "stripe_configured", label: "Pagos", detail: "Cobros y suscripción conectados." },
];

// "unknown" es distinto de "unreachable": el servicio contestó, pero no con un
// informe de estado (sesión caducada, respuesta de un intermediario). No
// sabemos nada, y decir "Incidencia" sin saberlo es exactamente lo que esta
// página vino a arreglar.
type Phase = "checking" | "loaded" | "unreachable" | "unknown";

type ReadinessResult = {
  phase: Exclude<Phase, "checking">;
  ready: Readiness | null;
};

async function loadReadiness(): Promise<ReadinessResult> {
  try {
    const res = await fetch(READY_URL, {
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    // 503 es una respuesta con contenido, no un fallo de red.
    const body: unknown = await res.json();
    return isReadiness(body)
      ? { ready: body, phase: "loaded" }
      : { ready: null, phase: "unknown" };
  } catch {
    return { ready: null, phase: "unreachable" };
  }
}

function Dot({ color }: { color: string }) {
  return <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${color}`} aria-hidden />;
}

function stateOf(value: boolean | null): { color: string; text: string } {
  if (value === true) return { color: "bg-emerald-500", text: "Funcionando" };
  if (value === false) return { color: "bg-red-500", text: "No responde" };
  return { color: "bg-slate-300", text: "No se ha podido comprobar" };
}

export default function EstadoClient({
  showPlatformConfig,
}: {
  /** (15) ¿Enseñamos el bloque "Canales conectados"?
   *
   * Son los tres `*_configured` de /readyz: si la PLATAFORMA tiene puestas
   * las credenciales de Meta, Vapi y Stripe. No es de ninguna agencia, pero
   * tampoco es de nadie de fuera: es el inventario de qué mitades de nuestra
   * infraestructura están sin conectar, y hasta ahora vivía detrás del login.
   * Al abrir la página lo publicábamos a internet entera sin querer, y encima
   * con un rótulo ("Canales conectados") que a quien no tiene cuenta le suena
   * a los suyos. Con sesión se enseña igual que siempre. */
  showPlatformConfig: boolean;
}) {
  const [phase, setPhase] = useState<Phase>("checking");
  const [ready, setReady] = useState<Readiness | null>(null);
  const [checkedAt, setCheckedAt] = useState<Date | null>(null);

  const check = useCallback(async () => {
    const result = await loadReadiness();
    setReady(result.ready);
    setPhase(result.phase);
    setCheckedAt(new Date());
  }, []);

  useEffect(() => {
    let active = true;
    void loadReadiness().then((result) => {
      if (!active) return;
      setReady(result.ready);
      setPhase(result.phase);
      setCheckedAt(new Date());
    });
    return () => {
      active = false;
    };
  }, []);

  // Sin Postgres no se puede atender ni una petición: eso es una incidencia.
  // Sin Redis o sin procesos en segundo plano el panel funciona pero los
  // recordatorios y los envíos se quedan parados; decirlo "Operativo" sería
  // lo mismo que mentía la versión anterior.
  let headline: { color: string; text: string; help: string };
  if (phase === "checking") {
    headline = { color: "bg-slate-300 animate-pulse", text: "Comprobando…", help: "" };
  } else if (phase === "unknown") {
    headline = {
      color: "bg-slate-400",
      text: "No hemos podido comprobarlo",
      help: "El servicio ha contestado, pero no con el informe de estado. Vuelve a entrar o recarga la página.",
    };
  } else if (phase === "unreachable" || ready === null) {
    headline = {
      color: "bg-red-500",
      text: "Incidencia",
      help: "No hemos conseguido contactar con el servicio.",
    };
  } else if (!ready.postgres) {
    headline = {
      color: "bg-red-500",
      text: "Incidencia",
      help: "La base de datos no responde. Estamos en ello.",
    };
  } else if (ready.redis === false || ready.celery === false) {
    headline = {
      color: "bg-amber-500",
      text: "Funcionando con limitaciones",
      help: "Puedes trabajar con normalidad, pero los avisos y envíos automáticos pueden retrasarse.",
    };
  } else {
    headline = { color: "bg-emerald-500", text: "Operativo", help: "" };
  }

  return (
    <div className="max-w-xl">
      <header className="mb-6">
        <h2 className="text-2xl font-semibold">Estado del sistema</h2>
        <p className="text-sm text-slate-500">
          Comprobamos en directo cada pieza del servicio, no solo que el servidor esté
          encendido.
        </p>
      </header>

      <div className="rounded-xl border border-slate-200 bg-white p-6">
        <div className="flex items-center gap-3">
          <span className={`h-3 w-3 rounded-full ${headline.color}`} aria-hidden />
          <span className="text-lg font-medium">{headline.text}</span>
        </div>
        {headline.help && <p className="mt-2 text-sm text-slate-600">{headline.help}</p>}

        {ready && (
          <ul className="mt-5 divide-y divide-slate-100 border-t border-slate-100">
            {CHECKS.map((check_) => {
              const state = stateOf(ready[check_.key] as boolean | null);
              return (
                <li key={check_.key} className="flex items-start gap-3 py-3">
                  <span className="mt-1.5">
                    <Dot color={state.color} />
                  </span>
                  <div className="flex-1">
                    <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                      <span className="text-sm font-medium">{check_.label}</span>
                      <span className="text-sm text-slate-500">{state.text}</span>
                    </div>
                    <p className="text-xs text-slate-500">{check_.detail}</p>
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        {ready && showPlatformConfig && (
          <div className="mt-5 border-t border-slate-100 pt-4">
            <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
              Canales conectados
            </p>
            <p className="mt-1 text-xs text-slate-500">
              Aquí solo comprobamos que las credenciales están puestas. Para saber si un
              canal responde de verdad, míralo en su propia pantalla de ajustes.
            </p>
            <ul className="mt-3 space-y-2">
              {CONFIGURED.map((item) => (
                <li key={item.key} className="flex items-center gap-3 text-sm">
                  <Dot color={ready[item.key] ? "bg-emerald-500" : "bg-slate-300"} />
                  <span className="font-medium">{item.label}</span>
                  <span className="text-slate-500">
                    {ready[item.key] ? "Configurado" : "Sin configurar"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {checkedAt && (
          <p className="mt-4 text-sm text-slate-500">
            Última comprobación:{" "}
            {checkedAt.toLocaleTimeString("es-ES", {
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
            })}
          </p>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            onClick={() => {
              setPhase("checking");
              void check();
            }}
            disabled={phase === "checking"}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-50 disabled:opacity-60"
          >
            Comprobar de nuevo
          </button>
        </div>

        <p className="mt-5 border-t border-slate-100 pt-4 text-sm text-slate-600">
          Si ves un problema o algo no te funciona, escríbenos a{" "}
          <a href="mailto:hola@selai.es" className="text-selai-600 underline">
            hola@selai.es
          </a>{" "}
          y lo miramos enseguida.
        </p>
      </div>
    </div>
  );
}
