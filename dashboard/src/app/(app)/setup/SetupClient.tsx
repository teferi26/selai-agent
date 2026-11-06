// "Puesta en marcha" — full-screen activation wizard (9 sequential steps).
// Every step EXECUTES its real connection (PUT /voice, POST /channels/phone,
// POST /channels/whatsapp, CSV import, webhook subscriptions, live test chat)
// instead of just explaining it. Progress persists in settings.setup_checklist
// (merged server-side); legacy keys — call_forwarding, whatsapp,
// whatsapp_requested, test_call — keep working.
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type {
  AgencySettings,
  ApplyVoiceOut,
  SetupChecklist,
  VoiceCatalogItem,
} from "@/lib/agency-settings";
import {
  type ChannelsStatus,
  type PhoneProvisionOut,
  type WebhookSubscriptionOut,
  type WebhookTestOut,
  PORTAL_GUIDES,
  WEBHOOK_EVENTS,
  timeAgoEs,
  webchatSnippetFor,
} from "@/lib/channels";
import {
  SETUP_STEP_KEYS,
  SETUP_STEP_TITLES,
  SETUP_TOTAL,
  type SetupStepKey,
  computeSetupDone,
  countSetupDone,
  firstPendingStep,
} from "@/lib/setup-steps";
import ImportCatalog from "../properties/ImportCatalog";
import VoicePreview from "../settings/voz/VoicePreview";
import CopyButton from "./CopyButton";
import WebchatVerify from "./WebchatVerify";
import WhatsAppConnect, {
  InboundStatusNotice,
  WebhookStatusBox,
  whatsappSecretMissing,
} from "./WhatsAppConnect";
import WhatsAppTest from "./WhatsAppTest";
import WizardChat from "./WizardChat";

const CONFETTI_COLORS = ["#5B61F0", "#FF7A59", "#FF9F7A", "#7B83FF", "#10b981", "#f59e0b"];

const GENDER_LABEL: Record<string, string> = {
  male: "Masculina",
  female: "Femenina",
  neutral: "Neutra",
};

// (192) Cada 12 s mientras el paso de portales está abierto. La espera a que
// llegue el primer lead es el único momento muerto del asistente; detectarlo
// en vivo la convierte en la mejor demo del trial.
const PORTAL_POLL_MS = 12_000;

function loadChannels(agencyId: string): Promise<ChannelsStatus> {
  return api<ChannelsStatus>(`/api/v1/agencies/${agencyId}/channels`);
}

function Confetti() {
  return (
    <>
      <style>{`
        @keyframes selai-confetti-fall {
          0% { transform: translateY(-20px) rotate(0deg); opacity: 1; }
          100% { transform: translateY(150px) rotate(540deg); opacity: 0; }
        }
      `}</style>
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
        {Array.from({ length: 14 }).map((_, i) => (
          <span
            key={i}
            className="absolute top-0 block h-2.5 w-1.5 rounded-sm"
            style={{
              left: `${(i * 7 + 3) % 100}%`,
              backgroundColor: CONFETTI_COLORS[i % CONFETTI_COLORS.length],
              animation: `selai-confetti-fall ${2 + (i % 5) * 0.4}s linear ${
                (i % 7) * 0.3
              }s infinite`,
            }}
          />
        ))}
      </div>
    </>
  );
}

function Code({ children }: { children: React.ReactNode }) {
  return (
    <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[13px]">
      {children}
    </code>
  );
}

function DoneBadge({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-center gap-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
      <svg viewBox="0 0 16 16" className="h-4 w-4 flex-shrink-0" fill="none" aria-hidden>
        <path
          d="M3 8.5 6.5 12 13 4.5"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <span>{children}</span>
    </div>
  );
}

export default function SetupClient({
  agencyId,
  agencyName,
  slug,
  settings,
  propertiesCount,
  propertiesCountExact = true,
  catalog,
  initialStepKey = null,
}: {
  agencyId: string;
  agencyName: string;
  slug: string;
  settings: AgencySettings;
  propertiesCount: number;
  /** (4) false cuando `propertiesCount` es sólo el tamaño de la página que
   *  devolvió el listado (tope del endpoint), no el total real: entonces el
   *  paso 6 dice "más de N" en vez de dar el tope por bueno. */
  propertiesCountExact?: boolean;
  catalog: VoiceCatalogItem[];
  /** Deep link (?step=crm) — opens the wizard on a specific step. */
  initialStepKey?: SetupStepKey | null;
}) {
  const s = settings.settings;
  const hasProperties = propertiesCount > 0;

  // ---- estado persistido -------------------------------------------------
  const [checklist, setChecklist] = useState<SetupChecklist>(s.setup_checklist ?? {});
  const [checklistError, setChecklistError] = useState<string | null>(null);

  const saveChecklist = useCallback(
    async (patch: Record<string, boolean>) => {
      setChecklistError(null);
      const prev = checklist;
      setChecklist({ ...prev, ...patch });
      try {
        await api<AgencySettings>(`/api/v1/agencies/${agencyId}/settings`, {
          method: "PATCH",
          body: JSON.stringify({ setup_checklist: patch }),
        });
      } catch {
        setChecklist(prev);
        setChecklistError("No se pudo guardar el progreso. Inténtalo de nuevo.");
      }
    },
    [agencyId, checklist]
  );

  // ---- estado de canales (GET /channels, refrescable) --------------------
  const [channels, setChannels] = useState<ChannelsStatus | null>(null);

  const refreshChannels = useCallback(async (): Promise<ChannelsStatus | null> => {
    try {
      const out = await loadChannels(agencyId);
      setChannels(out);
      return out;
    } catch {
      return null;
    }
  }, [agencyId]);

  useEffect(() => {
    let active = true;
    void loadChannels(agencyId).then(
      (out) => {
        if (active) setChannels(out);
      },
      () => {
        // La carga se reintenta desde los controles de cada paso.
      }
    );
    return () => {
      active = false;
    };
  }, [agencyId]);

  // ---- overrides locales tras ejecutar conexiones ------------------------
  const [selectedVoiceId, setSelectedVoiceId] = useState<string | null>(s.voice_id);
  const [aiPhone, setAiPhone] = useState<string | null>(s.ai_phone_number);
  const [waLocal, setWaLocal] = useState<{ number: string; name: string } | null>(null);
  // El número del backend es estado derivado, no una segunda copia local. La
  // copia local solo cubre la activación que acaba de ocurrir en esta sesión.
  const effectiveAiPhone =
    aiPhone ??
    (channels?.phone.status === "active" ? channels.phone.number : null);

  // (217) El tick de la barra lateral tiene que decir lo mismo que el paso: la
  // marca del checklist sigue a «nos ha entrado al menos un mensaje», no a
  // «hay credenciales guardadas». Antes se marcaba al conectar, así que el
  // progreso daba por terminado un canal que podía llevar semanas mudo — y
  // marcarlo es justo lo que impedía al dueño enterarse.
  // Las tres condiciones son la misma frase: hay número conectado, no estamos
  // rechazando sus webhooks, y ha entrado al menos un mensaje. Sin la primera
  // el paso se volvía a marcar solo después de desconectar (las
  // conversaciones antiguas siguen ahí); sin la segunda quedaba marcado
  // mientras rechazábamos todo lo que Meta nos mandaba.
  const whatsappReceiving = channels
    ? channels.whatsapp.connected &&
      !whatsappSecretMissing(channels.whatsapp) &&
      !!channels.whatsapp.last_inbound_at
    : null;
  const whatsappChecklistDone = !!checklist["whatsapp"];
  useEffect(() => {
    if (
      whatsappReceiving === null ||
      whatsappReceiving === whatsappChecklistDone
    ) {
      return;
    }
    const controller = new AbortController();
    let active = true;
    void api<AgencySettings>(`/api/v1/agencies/${agencyId}/settings`, {
      method: "PATCH",
      body: JSON.stringify({
        setup_checklist: { whatsapp: whatsappReceiving },
      }),
      signal: controller.signal,
    }).then(
      () => {
        if (!active) return;
        setChecklist((current) => ({
          ...current,
          whatsapp: whatsappReceiving,
        }));
        setChecklistError(null);
      },
      () => {
        if (active) {
          setChecklistError("No se pudo guardar el progreso. Inténtalo de nuevo.");
        }
      }
    );
    return () => {
      active = false;
      controller.abort();
    };
  }, [agencyId, whatsappReceiving, whatsappChecklistDone]);

  const doneMap = computeSetupDone(
    {
      ...s,
      voice_id: selectedVoiceId ?? s.voice_id,
      ai_phone_number: effectiveAiPhone,
    },
    checklist,
    hasProperties
  );
  const doneCount = countSetupDone(doneMap);

  // ---- navegación --------------------------------------------------------
  const [step, setStep] = useState(() =>
    initialStepKey !== null
      ? SETUP_STEP_KEYS.indexOf(initialStepKey)
      : firstPendingStep(doneMap)
  );
  const [finished, setFinished] = useState(false);
  const [nextBusy, setNextBusy] = useState(false);
  const stepKey = SETUP_STEP_KEYS[step];
  const isLast = step === SETUP_TOTAL - 1;

  // ---- paso 1: perfil de la agencia --------------------------------------
  const [businessName, setBusinessName] = useState(s.business_name ?? agencyName);
  const [zonesText, setZonesText] = useState((s.zones ?? []).join(", "));
  const [scheduleText, setScheduleText] = useState(s.schedule_text ?? "");
  const [website, setWebsite] = useState(s.website ?? "");
  const [profileError, setProfileError] = useState<string | null>(null);

  async function saveProfile() {
    await api<AgencySettings>(`/api/v1/agencies/${agencyId}/settings`, {
      method: "PATCH",
      body: JSON.stringify({
        business_name: businessName.trim() || agencyName,
        zones: zonesText
          .split(",")
          .map((z) => z.trim())
          .filter(Boolean),
        schedule_text: scheduleText.trim(),
        website: website.trim(),
        setup_checklist: { agency_profile: true },
      }),
    });
    setChecklist((c) => ({ ...c, agency_profile: true }));
  }

  async function next() {
    if (nextBusy) return;
    if (stepKey === "agency") {
      setProfileError(null);
      setNextBusy(true);
      try {
        await saveProfile();
      } catch {
        setProfileError("No se pudieron guardar los datos. Inténtalo de nuevo.");
        setNextBusy(false);
        return;
      }
      setNextBusy(false);
    }
    if (isLast) setFinished(true);
    else setStep((n) => Math.min(n + 1, SETUP_TOTAL - 1));
  }

  function skip() {
    if (isLast) setFinished(true);
    else setStep((n) => Math.min(n + 1, SETUP_TOTAL - 1));
  }

  // ---- paso 2: voz -------------------------------------------------------
  const [savingVoice, setSavingVoice] = useState<string | null>(null);
  const [voiceError, setVoiceError] = useState<string | null>(null);

  async function chooseVoice(v: VoiceCatalogItem) {
    if (savingVoice) return;
    setSavingVoice(v.id);
    setVoiceError(null);
    try {
      await api<ApplyVoiceOut>(`/api/v1/agencies/${agencyId}/voice`, {
        method: "PUT",
        body: JSON.stringify({ provider: v.provider, voice_id: v.id, voice_name: v.name }),
      });
      setSelectedVoiceId(v.id);
    } catch {
      setVoiceError("No se pudo guardar la voz. Inténtalo de nuevo.");
    } finally {
      setSavingVoice(null);
    }
  }

  // ---- paso 3: número de teléfono ----------------------------------------
  const [phoneBusy, setPhoneBusy] = useState(false);
  const [phoneRequested, setPhoneRequested] = useState(false);
  const [phoneError, setPhoneError] = useState<string | null>(null);

  const phoneStatus: "active" | "requested" | "none" = effectiveAiPhone
    ? "active"
    : phoneRequested || channels?.phone.status === "requested"
      ? "requested"
      : "none";

  async function activatePhone() {
    setPhoneBusy(true);
    setPhoneError(null);
    try {
      const out = await api<PhoneProvisionOut>(
        `/api/v1/agencies/${agencyId}/channels/phone`,
        { method: "POST", body: JSON.stringify({}) }
      );
      if (out.status === "active") setAiPhone(out.number);
      else setPhoneRequested(true);
    } catch {
      setPhoneError(
        "No se pudo activar el número ahora mismo. Inténtalo de nuevo en unos minutos o escríbenos a hola@selai.es."
      );
    } finally {
      setPhoneBusy(false);
    }
  }

  // ---- paso 4: whatsapp ---------------------------------------------------
  // (217) Cuando /channels ha respondido, manda /channels y nada más. El
  // checklist solo sirve mientras no ha llegado: como ahora se deriva de «nos
  // ha entrado algún mensaje», seguía valiendo `true` después de desconectar
  // el número (las conversaciones antiguas no se borran) y el paso pintaba
  // una conexión que ya no existía.
  const waConnected =
    waLocal !== null ||
    (channels ? channels.whatsapp.connected : !!checklist["whatsapp"]);
  const waNumber = waLocal?.number ?? channels?.whatsapp.phone_number ?? null;
  const waName = waLocal?.name ?? channels?.whatsapp.verified_name ?? null;
  // (217) Falta el secreto de su propia app ⇒ hoy rechazamos todos los
  // webhooks. Los mensajes que consten son de antes de romperse, así que no
  // pueden dar el paso por bueno.
  const waSecretMissing = channels
    ? whatsappSecretMissing(channels.whatsapp)
    : false;
  // (217) Lo único que prueba que el canal ENTRANTE funciona: ha llegado al
  // menos un webhook real y hoy seguiría entrando. Deliberadamente no se
  // apoya en `waLocal` ni en el checklist — los dos hablan de lo que el
  // usuario acaba de hacer, no de lo que Meta nos ha entregado.
  const waInbound = !!channels?.whatsapp.last_inbound_at && !waSecretMissing;

  // ---- paso 5: portales ---------------------------------------------------
  const [portalBusy, setPortalBusy] = useState(false);
  const [portalCheck, setPortalCheck] = useState<"idle" | "ok" | "none" | "error">(
    "idle"
  );
  const [portalTab, setPortalTab] = useState(PORTAL_GUIDES[0].key);
  // Cuántos emails había al abrir el paso: sirve para saber si el que acaba
  // de llegar es el primero *de esta sesión* y merece celebración, en vez de
  // volver a felicitar por uno de la semana pasada cada vez que se abre.
  const portalBaseline = useRef<number | null>(null);
  const [portalArrived, setPortalArrived] = useState(false);
  // Solo la que dice el backend. Sin respaldo local: ver el comentario en
  // lib/channels.ts — aquí es donde la agencia la copia para pegarla en el
  // portal, así que una dirección equivocada se pierde todos sus leads.
  const leadsEmail = channels?.email.address ?? null;

  const applyPortalStatus = useCallback(
    (out: ChannelsStatus | null) => {
      if (!out) return false;
      if (portalBaseline.current === null) {
        portalBaseline.current = out.email.received_count;
      }
      if (out.email.received_count > 0 && !checklist["portals"]) {
        void saveChecklist({ portals: true });
      }
      if (out.email.received_count > portalBaseline.current) {
        setPortalArrived(true);
        return true;
      }
      return false;
    },
    [checklist, saveChecklist]
  );

  async function checkPortal() {
    setPortalBusy(true);
    const out = await refreshChannels();
    applyPortalStatus(out);
    if (!out) setPortalCheck("error");
    else if (out.email.received_count > 0) setPortalCheck("ok");
    else setPortalCheck("none");
    setPortalBusy(false);
  }

  // La referencia se fija al abrir el paso, no en el primer sondeo: si no,
  // un email que llegara en esos 12 s quedaría dentro de la línea base y la
  // celebración —que es justo lo que queremos que vea— no saltaría.
  useEffect(() => {
    if (stepKey !== "portals" || !channels || portalBaseline.current !== null) return;
    portalBaseline.current = channels.email.received_count;
  }, [stepKey, channels]);

  // (192) Sondeo automático mientras el paso está abierto. Se detiene solo
  // en cuanto llega el primero: a partir de ahí no hay nada que esperar, y
  // dejar un intervalo corriendo contra el backend porque sí no está bien.
  useEffect(() => {
    if (stepKey !== "portals" || portalArrived) return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      void refreshChannels().then((out) => {
        if (!cancelled) applyPortalStatus(out);
      });
    }, PORTAL_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [stepKey, portalArrived, refreshChannels, applyPortalStatus]);

  // ---- paso 7: chat web ---------------------------------------------------
  const [webchatVerifiedLocal, setWebchatVerifiedLocal] = useState(false);
  const webchatVerified =
    webchatVerifiedLocal ||
    (channels?.webchat.verified ?? false) ||
    !!checklist["webchat_verified"];

  // ---- paso 8: CRM --------------------------------------------------------
  const [crmTab, setCrmTab] = useState<"export" | "notify" | "native">("export");
  const [subs, setSubs] = useState<WebhookSubscriptionOut[] | null>(null);
  const [subsError, setSubsError] = useState<string | null>(null);
  const [subsLoadError, setSubsLoadError] = useState<string | null>(null);
  const [subUrl, setSubUrl] = useState("");
  const [subEvents, setSubEvents] = useState<string[]>([]);
  const [subBusy, setSubBusy] = useState(false);
  // (194) El secreto completo llega UNA vez, en la respuesta que crea la
  // suscripción. Se guarda solo en memoria y desaparece al recargar: es lo
  // que promete el aviso que se enseña junto a él.
  const [freshSecret, setFreshSecret] = useState<{ id: string; secret: string } | null>(
    null
  );
  const [subTest, setSubTest] = useState<
    Record<string, { busy: boolean; result: WebhookTestOut | null; failed: boolean }>
  >({});

  async function testSub(id: string) {
    setSubTest((cur) => ({
      ...cur,
      [id]: { busy: true, result: null, failed: false },
    }));
    try {
      const out = await api<WebhookTestOut>(
        `/api/v1/agencies/${agencyId}/webhook-subscriptions/${id}/test`,
        { method: "POST", body: JSON.stringify({}) }
      );
      setSubTest((cur) => ({ ...cur, [id]: { busy: false, result: out, failed: false } }));
      setSubs((cur) =>
        (cur ?? []).map((s0) =>
          s0.id === id
            ? {
                ...s0,
                is_active: out.is_active,
                consecutive_failures: out.consecutive_failures,
              }
            : s0
        )
      );
    } catch {
      setSubTest((cur) => ({ ...cur, [id]: { busy: false, result: null, failed: true } }));
    }
  }

  useEffect(() => {
    if (stepKey !== "crm" || crmTab !== "notify" || subs !== null) return;
    let cancelled = false;
    api<WebhookSubscriptionOut[]>(`/api/v1/agencies/${agencyId}/webhook-subscriptions`)
      .then((out) => {
        if (!cancelled) setSubs(out);
      })
      .catch(() => {
        if (!cancelled) {
          setSubs([]);
          setSubsLoadError("No se pudieron cargar tus notificaciones.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [stepKey, crmTab, subs, agencyId]);

  async function createSub(e: React.FormEvent) {
    e.preventDefault();
    if (subBusy || subEvents.length === 0) return;
    setSubBusy(true);
    setSubsError(null);
    try {
      const out = await api<WebhookSubscriptionOut>(
        `/api/v1/agencies/${agencyId}/webhook-subscriptions`,
        {
          method: "POST",
          body: JSON.stringify({ target_url: subUrl.trim(), events: subEvents }),
        }
      );
      setSubs((cur) => [...(cur ?? []), out]);
      if (out.secret_full_once) {
        setFreshSecret({ id: out.id, secret: out.secret_full_once });
      }
      setSubUrl("");
      setSubEvents([]);
      if (!checklist["crm"]) void saveChecklist({ crm: true });
    } catch {
      setSubsError(
        "No se pudo crear. Comprueba que la dirección empieza por https:// e inténtalo de nuevo."
      );
    } finally {
      setSubBusy(false);
    }
  }

  async function deleteSub(id: string) {
    setSubsError(null);
    const prev = subs;
    setSubs((cur) => (cur ?? []).filter((x) => x.id !== id));
    try {
      // fetch directo: el DELETE responde 204 sin cuerpo (lib/api espera JSON).
      const res = await fetch(
        `/api/backend/api/v1/agencies/${agencyId}/webhook-subscriptions/${id}`,
        { method: "DELETE", cache: "no-store" }
      );
      if (!res.ok) throw new Error();
    } catch {
      setSubs(prev);
      setSubsError("No se pudo borrar. Inténtalo de nuevo.");
    }
  }

  const snippet = webchatSnippetFor(slug);

  // ---- pantalla final -----------------------------------------------------
  if (finished) {
    const pending = SETUP_STEP_KEYS.filter((k) => !doneMap[k]);
    return (
      <div className="fixed inset-0 z-40 flex flex-col items-center justify-center overflow-y-auto bg-slate-50 p-6">
        <div className="relative w-full max-w-xl overflow-hidden rounded-2xl border border-selai-200 bg-gradient-to-b from-selai-50 to-white p-8 text-center shadow-card">
          {pending.length === 0 && <Confetti />}
          <h2 className="text-2xl font-semibold text-selai-900">
            {pending.length === 0
              ? "🎉 ¡Enhorabuena! Tu asistente está en marcha."
              : "¡Buen trabajo! Ya casi lo tienes."}
          </h2>
          <p className="mt-2 text-sm text-slate-600">
            Has completado {doneCount} de {SETUP_TOTAL} pasos.
            {pending.length === 0
              ? " A partir de ahora, cada llamada, WhatsApp o email de un cliente aparecerá en tu bandeja."
              : " Puedes volver a este asistente cuando quieras desde «Puesta en marcha» en el menú."}
          </p>
          {pending.length > 0 && (
            <p className="mt-3 text-sm text-slate-500">
              Pendientes: {pending.map((k) => SETUP_STEP_TITLES[k]).join(" · ")}
            </p>
          )}
          <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
            <a
              href="/inbox"
              className="inline-flex items-center rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:opacity-90"
            >
              Ver tu bandeja
            </a>
            {pending.length > 0 && (
              <button
                onClick={() => {
                  setFinished(false);
                  setStep(firstPendingStep(doneMap));
                }}
                className="inline-flex items-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium hover:bg-slate-50"
              >
                Seguir con los pasos
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-40 flex flex-col bg-slate-50">
      {/* ── Cabecera: progreso ── */}
      <header className="border-b border-slate-200 bg-white px-4 py-3 sm:px-8">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-4">
          <span className="text-lg font-semibold text-selai-900">selai</span>
          <div className="flex-1">
            <div className="flex items-baseline justify-between">
              <span className="text-sm font-medium">
                Paso {step + 1} de {SETUP_TOTAL} · {SETUP_STEP_TITLES[stepKey]}
              </span>
              <span className="hidden text-xs text-slate-500 sm:inline">
                {doneCount} completados
              </span>
            </div>
            <div className="mt-1.5 h-2 rounded-full bg-slate-100">
              <div
                className="h-2 rounded-full bg-accent transition-all duration-500"
                style={{ width: `${Math.round(((step + 1) / SETUP_TOTAL) * 100)}%` }}
              />
            </div>
          </div>
          {/* Honesto: el progreso se guarda solo al completar cada paso; los
              campos del paso 1 se guardan con «Siguiente», no aquí. */}
          <a
            href="/inbox"
            className="text-xs text-slate-400 underline hover:text-slate-600"
          >
            Salir (el progreso queda guardado)
          </a>
        </div>
        {/* Puntos de paso (saltables) */}
        <div className="mx-auto mt-2 flex max-w-3xl items-center gap-1.5 overflow-x-auto pb-1">
          {SETUP_STEP_KEYS.map((k, i) => {
            const done = doneMap[k];
            const current = i === step;
            return (
              <button
                key={k}
                type="button"
                title={SETUP_STEP_TITLES[k]}
                onClick={() => setStep(i)}
                className={`flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full text-xs font-semibold transition-colors ${
                  current
                    ? "bg-selai-600 text-white ring-2 ring-selai-200"
                    : done
                      ? "bg-emerald-500 text-white"
                      : "border border-slate-300 bg-white text-slate-500 hover:border-selai-300"
                }`}
                aria-label={`Ir al paso ${i + 1}: ${SETUP_STEP_TITLES[k]}`}
                aria-current={current ? "step" : undefined}
              >
                {done && !current ? (
                  <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" aria-hidden>
                    <path
                      d="M3 8.5 6.5 12 13 4.5"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                ) : (
                  i + 1
                )}
              </button>
            );
          })}
        </div>
      </header>

      {/* ── Contenido del paso ── */}
      <main className="flex-1 overflow-y-auto px-4 py-6 sm:px-8">
        <div className="mx-auto max-w-3xl">
          {checklistError && (
            <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {checklistError}
            </div>
          )}

          {/* ════ Paso 1 · Tu agencia ════ */}
          {stepKey === "agency" && (
            <section>
              <h2 className="text-xl font-semibold">Cuéntanos sobre tu agencia</h2>
              <p className="mt-1 text-sm text-slate-500">
                Tu asistente usará estos datos al hablar con tus clientes: cómo se
                presenta, qué zonas conoce y cuándo estáis abiertos.
              </p>
              <div className="mt-5 space-y-4 rounded-xl border border-slate-200 bg-white p-5">
                <div>
                  <label className="mb-1 block text-sm font-medium" htmlFor="ag-name">
                    Nombre comercial
                  </label>
                  <input
                    id="ag-name"
                    value={businessName}
                    onChange={(e) => setBusinessName(e.target.value)}
                    placeholder={agencyName}
                    className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium" htmlFor="ag-zones">
                    Zonas donde operáis
                  </label>
                  <input
                    id="ag-zones"
                    value={zonesText}
                    onChange={(e) => setZonesText(e.target.value)}
                    placeholder="Ej. Chamberí, Tetuán, Alcobendas"
                    className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                  />
                  <p className="mt-1 text-xs text-slate-500">Separa las zonas con comas.</p>
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium" htmlFor="ag-hours">
                    Horario de atención
                  </label>
                  <input
                    id="ag-hours"
                    value={scheduleText}
                    onChange={(e) => setScheduleText(e.target.value)}
                    placeholder="Ej. Lunes a viernes de 9:00 a 19:00"
                    className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium" htmlFor="ag-web">
                    Página web
                  </label>
                  <input
                    id="ag-web"
                    type="url"
                    value={website}
                    onChange={(e) => setWebsite(e.target.value)}
                    placeholder="https://www.tuinmobiliaria.es"
                    className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                  />
                </div>
                {profileError && (
                  <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                    {profileError}
                  </div>
                )}
                <p className="text-xs text-slate-500">
                  Se guardan al pulsar «Siguiente». Podrás cambiarlos cuando quieras.
                </p>
              </div>
            </section>
          )}

          {/* ════ Paso 2 · Voz ════ */}
          {stepKey === "voice" && (
            <section>
              <h2 className="text-xl font-semibold">Elige la voz de tu IA</h2>
              <p className="mt-1 text-sm text-slate-500">
                Así sonará tu asistente al teléfono. Haz clic en la que más te guste —
                se activa al momento y puedes cambiarla cuando quieras. En las voces
                que tienen «Escuchar» puedes oír una muestra antes de decidir.
              </p>
              {doneMap.voice && (
                <div className="mt-3">
                  <DoneBadge>Voz elegida. Puedes cambiarla cuando quieras.</DoneBadge>
                </div>
              )}
              {voiceError && (
                <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {voiceError}
                </div>
              )}
              {catalog.length === 0 ? (
                <p className="mt-4 rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-500">
                  Aún no hay voces disponibles. Escríbenos a hola@selai.es y te ayudamos.
                </p>
              ) : (
                <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {catalog.map((v) => {
                    const isSelected = v.id === selectedVoiceId;
                    return (
                      <div
                        key={v.id}
                        role="button"
                        tabIndex={0}
                        onClick={() => chooseVoice(v)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            chooseVoice(v);
                          }
                        }}
                        className={`cursor-pointer rounded-xl border bg-white p-4 text-left transition-colors ${
                          savingVoice !== null ? "opacity-70" : ""
                        } ${
                          isSelected
                            ? "border-selai-500 ring-1 ring-selai-500"
                            : "border-slate-200 hover:border-selai-300"
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-medium">{v.name}</span>
                          {isSelected && (
                            <span className="rounded-full bg-selai-50 px-2 py-0.5 text-xs text-selai-700">
                              Tu voz ✓
                            </span>
                          )}
                          {savingVoice === v.id && (
                            <span className="text-xs text-slate-400">Guardando…</span>
                          )}
                        </div>
                        <p className="mt-1 text-sm text-slate-600">{v.description}</p>
                        <div className="mt-2 flex flex-wrap items-center gap-1.5">
                          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
                            {v.language}
                          </span>
                          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
                            {GENDER_LABEL[v.gender] ?? v.gender}
                          </span>
                          {v.preview_url && (
                            <audio
                              controls
                              preload="none"
                              src={v.preview_url}
                              className="mt-1 h-8 w-full"
                              onClick={(e) => e.stopPropagation()}
                            />
                          )}
                          {v.provider === "11labs" && (
                            <VoicePreview agencyId={agencyId} voiceId={v.id} />
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
              <p className="mt-4 text-sm text-slate-600">
                ¿Prefieres que suene como tú?{" "}
                <a
                  href="/settings/voz"
                  target="_blank"
                  rel="noreferrer"
                  className="text-selai-600 underline hover:text-selai-700"
                >
                  Clona tu propia voz
                </a>{" "}
                subiendo un par de audios (se abre en otra pestaña; este asistente se
                queda como está).
              </p>
            </section>
          )}

          {/* ════ Paso 3 · Teléfono ════ */}
          {stepKey === "phone" && (
            <section>
              <h2 className="text-xl font-semibold">Activa el teléfono de tu IA</h2>
              <p className="mt-1 text-sm text-slate-500">
                Tu asistente atiende llamadas en su propio número, disponible 24/7.
              </p>

              <div className="mt-5 rounded-xl border border-slate-200 bg-white p-5">
                {phoneStatus === "active" && (
                  <>
                    <DoneBadge>Tu número está activo.</DoneBadge>
                    <p className="text-3xl font-semibold tracking-wide text-slate-900">
                      {effectiveAiPhone}
                    </p>
                    <p className="mt-2 text-sm text-slate-600">
                      Llámalo ahora mismo y saluda a tu asistente. En el último paso lo
                      probarás a fondo.
                    </p>
                  </>
                )}
                {phoneStatus === "requested" && (
                  <div className="flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2.5 text-sm text-amber-900">
                    <span className="h-2 w-2 flex-shrink-0 rounded-full bg-amber-500" aria-hidden />
                    <span>
                      <strong>Solicitado: te lo activamos en menos de 24h.</strong> Te
                      avisaremos por email en cuanto tu número esté listo. Puedes seguir
                      con los demás pasos.
                    </span>
                  </div>
                )}
                {phoneStatus === "none" && (
                  <>
                    <p className="text-sm text-slate-600">
                      Pulsa el botón y te asignamos un número al momento si hay
                      disponibles. Los números de activación instantánea son de Estados
                      Unidos (empiezan por +1); si no hay ninguno libre, lo solicitamos
                      por ti y te lo activamos en menos de 24 horas — sin que tengas que
                      hacer nada más.
                    </p>
                    {phoneError && (
                      <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                        {phoneError}
                      </div>
                    )}
                    <button
                      onClick={activatePhone}
                      disabled={phoneBusy}
                      className="mt-4 rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
                    >
                      {phoneBusy ? "Activando…" : "Activar número"}
                    </button>
                  </>
                )}
              </div>

              {/* Desvío de llamadas */}
              <div className="mt-4 rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-600">
                <p className="mb-2 font-medium text-slate-800">
                  Para que conteste cuando tú no puedes: desvía tus llamadas
                </p>
                <p className="mb-3">
                  Marca estos códigos desde el teléfono de tu agencia (funcionan en
                  Movistar, Vodafone, Orange y la mayoría de operadores):
                </p>
                <ul className="space-y-1.5">
                  <li>
                    Desviar todas las llamadas:{" "}
                    <Code>**21*{effectiveAiPhone ?? "NÚMERO-DE-TU-IA"}#</Code>
                  </li>
                  <li>
                    Desviar solo si no contestas:{" "}
                    <Code>**61*{effectiveAiPhone ?? "NÚMERO-DE-TU-IA"}#</Code>
                  </li>
                  <li>
                    Quitar el desvío: <Code>##21#</Code>
                  </li>
                </ul>
                <label className="mt-4 flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={!!checklist["call_forwarding"]}
                    onChange={(e) => void saveChecklist({ call_forwarding: e.target.checked })}
                  />
                  <span>Ya he configurado el desvío</span>
                </label>
              </div>
            </section>
          )}

          {/* ════ Paso 4 · WhatsApp ════ */}
          {stepKey === "whatsapp" && (
            <section>
              <h2 className="text-xl font-semibold">Conecta tu WhatsApp</h2>
              <p className="mt-1 text-sm text-slate-500">
                Tu asistente responderá los WhatsApp de tus clientes desde el número de
                tu agencia. Necesitas una cuenta gratuita de WhatsApp Business API de
                Meta — la guía de abajo te lleva de la mano.
              </p>

              <div className="mt-5 rounded-xl border border-slate-200 bg-white p-5">
                {waConnected ? (
                  <>
                    {/* (217) El verde solo cuando ha entrado un mensaje de
                        verdad. Tener credenciales válidas prueba que podemos
                        escribir, no que nos llegue nada: dar el paso por
                        hecho antes de eso es la mentira que costaba los leads
                        de las primeras semanas. */}
                    {waInbound ? (
                      <DoneBadge>
                        WhatsApp funcionando
                        {waNumber ? (
                          <>
                            {" "}
                            en <strong>{waNumber}</strong>
                            {waName ? ` (${waName})` : ""}
                          </>
                        ) : null}
                        : ya nos han llegado mensajes.
                      </DoneBadge>
                    ) : (
                      <div className="mb-4 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                        <strong>
                          {waSecretMissing
                            ? "Número conectado, pero no te entra nada."
                            : "Número conectado, canal sin estrenar."}
                        </strong>{" "}
                        {waNumber ? (
                          <>
                            Comprobamos <strong>{waNumber}</strong>
                            {waName ? ` (${waName})` : ""} contra Meta: escribir a
                            tus clientes ya puedes.{" "}
                          </>
                        ) : null}
                        Recibir es lo que falta por demostrar, y lo confirmamos aquí
                        abajo.
                      </div>
                    )}
                    <p className="text-sm text-slate-600">
                      Puedes desconectarlo o cambiar de número cuando quieras desde{" "}
                      <a href="/settings/canales" className="text-selai-600 underline">
                        Ajustes → Canales
                      </a>
                      .
                    </p>

                    {/* (217) La URL de devolución de llamada y el token son lo
                        que hace que los mensajes entren; vivían solo en Ajustes
                        → Canales, así que quien se quedaba a medias aquí no
                        tenía a mano lo que necesitaba para terminar. */}
                    {channels && (
                      <>
                        <WebhookStatusBox
                          subscribed={channels.whatsapp.subscribed_apps}
                          appSecretSet={channels.whatsapp.app_secret_set}
                          secretMissing={waSecretMissing}
                          webhook={{
                            callbackUrl: channels.whatsapp.webhook_callback_url,
                            verifyToken: channels.whatsapp.webhook_verify_token,
                          }}
                        />
                        <InboundStatusNotice
                          lastInboundAt={channels.whatsapp.last_inbound_at}
                          inboundCount={channels.whatsapp.inbound_count}
                          secretMissing={waSecretMissing}
                        />
                        <button
                          type="button"
                          onClick={() => void refreshChannels()}
                          className="mt-2 text-sm text-selai-600 underline hover:text-selai-700"
                        >
                          Ya he enviado el mensaje de prueba, comprobar
                        </button>
                      </>
                    )}

                    {/* (189) Sin esta prueba la agencia descubre que el canal
                        no funciona cuando ya ha perdido leads reales. */}
                    <WhatsAppTest agencyId={agencyId} />
                  </>
                ) : (
                  <WhatsAppConnect
                    agencyId={agencyId}
                    metaVerificationPending={!!checklist["whatsapp_requested"]}
                    onConnected={(out) => {
                      setWaLocal({ number: out.phone_number, name: out.verified_name });
                      // (217) Sin `saveChecklist({whatsapp: true})`: conectar
                      // no demuestra que se reciba nada. Lo marca el efecto de
                      // arriba cuando /channels dice que ha entrado un mensaje.
                      void refreshChannels();
                    }}
                  />
                )}
              </div>
            </section>
          )}

          {/* ════ Paso 5 · Portales ════ */}
          {stepKey === "portals" && (
            <section>
              <h2 className="text-xl font-semibold">
                Recibe los leads de los portales
              </h2>
              {/* (6) "y responderá al momento" no siempre es verdad: cuando el
                  aviso del portal solo trae el teléfono del cliente no hay a
                  dónde contestar (backend api/email.py: se suprime la respuesta
                  y se te avisa a ti). Lo que sí pasa siempre es que el lead
                  entra en la bandeja. */}
              <p className="mt-1 text-sm text-slate-500">
                Cuando alguien pregunta por un piso en Idealista, Fotocasa o Habitaclia,
                el portal te avisa por email. Haz que esos avisos lleguen también a tu
                asistente: el lead entra en tu bandeja al instante y, si el aviso trae
                el email del cliente, tu asistente le contesta solo.
              </p>

              <div className="mt-5 rounded-xl border border-slate-200 bg-white p-5">
                <p className="mb-1 text-sm font-medium text-slate-800">
                  Tu dirección única para leads
                </p>
                <div className="flex flex-wrap items-center gap-2">
                  <code className="rounded-md bg-slate-100 px-3 py-2 font-mono text-sm text-slate-800">
                    {leadsEmail ?? "cargando…"}
                  </code>
                  {leadsEmail && <CopyButton value={leadsEmail} />}
                </div>
              </div>

              {/* (192) Cada portal esconde el email de aviso en un sitio
                  distinto; una lista genérica obligaba a adivinar. */}
              <div className="mt-4 rounded-xl border border-slate-200 bg-white p-5">
                <p className="mb-3 text-sm font-medium text-slate-800">
                  Dónde ponerla en cada portal
                </p>
                <div
                  role="tablist"
                  aria-label="Portales inmobiliarios"
                  className="flex flex-wrap gap-1.5"
                >
                  {PORTAL_GUIDES.map((p) => (
                    <button
                      key={p.key}
                      type="button"
                      role="tab"
                      aria-selected={portalTab === p.key}
                      onClick={() => setPortalTab(p.key)}
                      className={`rounded-full px-3 py-1 text-sm transition-colors ${
                        portalTab === p.key
                          ? "bg-selai-600 text-white"
                          : "border border-selai-200 bg-white text-slate-600 hover:border-selai-400"
                      }`}
                    >
                      {p.name}
                    </button>
                  ))}
                </div>

                {PORTAL_GUIDES.filter((p) => p.key === portalTab).map((p) => (
                  <div key={p.key} className="mt-4">
                    <ol className="list-decimal space-y-1.5 pl-5 text-sm text-slate-600">
                      {p.path.map((line) => (
                        <li key={line}>{line}</li>
                      ))}
                      <li>
                        Pega ahí <Code>{leadsEmail ?? "cargando…"}</Code> y guarda.
                      </li>
                    </ol>
                    <p className="mt-3 text-sm text-slate-500">{p.note}</p>
                  </div>
                ))}

                <p className="mt-4 border-t border-slate-100 pt-3 text-xs text-slate-500">
                  Los portales rediseñan sus paneles de vez en cuando. Si no
                  encuentras la opción donde te decimos, busca «email de contacto»
                  o «recepción de leads» en la configuración de tu cuenta, o
                  escríbenos a hola@selai.es y lo miramos contigo.
                </p>
              </div>

              <div className="mt-4 rounded-xl border border-slate-200 bg-white p-5">
                <p className="mb-2 text-sm font-medium text-slate-800">
                  ¿El portal solo admite un email?
                </p>
                <p className="text-sm text-slate-600">
                  Deja el tuyo de siempre y crea en tu correo una{" "}
                  <strong>regla de reenvío automático</strong> de esos avisos a{" "}
                  <Code>{leadsEmail ?? "cargando…"}</Code> (en Gmail: Ajustes → Reenvío y correo
                  POP/IMAP). Recibirás el aviso igual que hasta ahora y tu
                  asistente también.
                </p>
              </div>

              <div className="mt-4 rounded-xl border border-slate-200 bg-white p-5">
                <p className="mb-3 text-sm text-slate-600">
                  Cuando lo tengas, envía un lead de prueba desde el portal (o
                  reenvía a mano un aviso antiguo). Lo detectamos solos:{" "}
                  <strong>puedes dejar esta pantalla abierta</strong>.
                </p>
                <div className="flex flex-wrap items-center gap-3">
                  <button
                    onClick={checkPortal}
                    disabled={portalBusy}
                    className="rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
                  >
                    {portalBusy ? "Comprobando…" : "Comprobar ahora"}
                  </button>
                  {!portalArrived && (
                    <span
                      className="inline-flex items-center gap-1.5 text-sm text-slate-500"
                      aria-live="polite"
                    >
                      <span className="h-2 w-2 animate-pulse rounded-full bg-selai-400" aria-hidden />
                      Esperando el primer aviso…
                    </span>
                  )}
                </div>

                <div aria-live="polite">
                  {portalArrived && (
                    <div className="relative mt-3 overflow-hidden rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2.5 text-sm text-emerald-800">
                      <Confetti />
                      <strong>🎉 ¡Ha llegado tu primer lead de portal!</strong>{" "}
                      {channels?.email.last_received_at
                        ? `Recibido ${timeAgoEs(channels.email.last_received_at)}.`
                        : ""}{" "}
                      Ya lo tienes en tu bandeja.{" "}
                      {/* (6) Antes ponía "tu asistente ya le ha contestado", y
                          desde aquí no hay forma de saberlo: /channels solo
                          cuenta emails recibidos, no respuestas enviadas. Con un
                          aviso que solo trae el teléfono la IA no contesta a
                          nadie y el backend te avisa para que llames tú
                          (api/email.py, `email.reply_suppressed.noreply`), así
                          que celebrar una respuesta que no existe deja al lead
                          esperando. */}
                      <span className="block mt-1">
                        Si el aviso traía el email del cliente, tu asistente ya le ha
                        contestado. Si solo traía el teléfono, no hay a dónde responder:
                        te avisamos para que le llames tú.
                      </span>
                    </div>
                  )}
                  {!portalArrived &&
                    (doneMap.portals || portalCheck === "ok") &&
                    channels &&
                    channels.email.received_count > 0 && (
                      <p className="mt-3 text-sm text-emerald-800">
                        Ya has recibido {channels.email.received_count} email
                        {channels.email.received_count === 1 ? "" : "s"} de portales
                        {channels.email.last_received_at
                          ? ` · el último ${timeAgoEs(channels.email.last_received_at)}`
                          : ""}
                        .
                      </p>
                    )}
                  {portalCheck === "none" && !portalArrived && (
                    <p className="mt-3 text-sm text-slate-500">
                      Aún no ha llegado nada. Los portales tardan unos minutos en
                      enviar el aviso — seguimos mirando por ti.
                    </p>
                  )}
                  {portalCheck === "error" && (
                    <p className="mt-3 text-sm text-amber-700">
                      No hemos podido comprobarlo ahora mismo. Inténtalo de nuevo en
                      un minuto.
                    </p>
                  )}
                </div>
              </div>
            </section>
          )}

          {/* ════ Paso 6 · Catálogo ════ */}
          {stepKey === "properties" && (
            <section>
              <h2 className="text-xl font-semibold">Sube tu catálogo</h2>
              <p className="mt-1 text-sm text-slate-500">
                Con tus pisos cargados, el asistente puede responder dudas concretas,
                filtrar por presupuesto y proponer visitas.
              </p>
              {hasProperties && (
                <div className="mt-3">
                  <DoneBadge>
                    Ya tienes {propertiesCountExact ? "" : "más de "}
                    {propertiesCount.toLocaleString("es-ES")} propiedad
                    {propertiesCount === 1 ? "" : "es"} en tu catálogo. Puedes subir más
                    cuando quieras.
                  </DoneBadge>
                </div>
              )}
              <div className="mt-4">
                <ImportCatalog agencyId={agencyId} defaultOpen />
              </div>
              <p className="text-sm text-slate-600">
                ¿Tu programa de gestión puede enviarnos los datos automáticamente?
                Conéctalo en el paso 8 (Tu CRM).
              </p>
            </section>
          )}

          {/* ════ Paso 7 · Chat web ════ */}
          {stepKey === "webchat" && (
            <section>
              <h2 className="text-xl font-semibold">Pon el chat en tu web</h2>
              <p className="mt-1 text-sm text-slate-500">
                Una sola línea de código añade a tu web un botón de chat atendido por tu
                asistente, 24 horas al día.
              </p>

              <div className="mt-5 rounded-xl border border-slate-200 bg-white p-5">
                <p className="mb-2 text-sm font-medium text-slate-800">
                  Copia esta línea y pégala en tu web, justo antes de{" "}
                  <Code>&lt;/body&gt;</Code>
                </p>
                <div className="flex flex-wrap items-start gap-2">
                  <pre className="min-w-0 flex-1 overflow-x-auto rounded-md bg-slate-900 px-3 py-2.5 text-[13px] leading-relaxed text-slate-100">
                    <code>{snippet}</code>
                  </pre>
                  <CopyButton value={snippet} />
                </div>
                <ul className="mt-3 space-y-1.5 text-sm text-slate-600">
                  <li>
                    · En <strong>WordPress</strong>: pégala en el pie de página con un
                    plugin tipo «Insert Headers and Footers», o pásasela a quien te
                    hizo la web — sabrá qué hacer en 2 minutos.
                  </li>
                  <li>
                    · El botón aparece en cuanto la página se recarga. No cambia nada
                    más de tu web.
                  </li>
                </ul>
                <div className="mt-4 flex flex-wrap items-center gap-3">
                  <a
                    href={`/chat/${slug}`}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-50"
                  >
                    Ver cómo queda el chat
                  </a>
                  {webchatVerified ? (
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-sm text-emerald-800">
                      Comprobado en tu web ✓
                    </span>
                  ) : doneMap.webchat ? (
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-sm text-slate-600">
                      Marcado como instalado (sin comprobar)
                    </span>
                  ) : (
                    <button
                      onClick={() => void saveChecklist({ webchat: true })}
                      className="inline-flex items-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-50"
                    >
                      Ya lo he puesto en mi web
                    </button>
                  )}
                </div>

                {/* (195) La comprobación de verdad: el botón de honor de
                    arriba no detecta una instalación rota. */}
                <WebchatVerify
                  agencyId={agencyId}
                  defaultUrl={website || s.website}
                  verifiedUrl={
                    webchatVerified ? channels?.webchat.verified_url : null
                  }
                  onVerified={() => {
                    setWebchatVerifiedLocal(true);
                    setChecklist((c) => ({
                      ...c,
                      webchat: true,
                      webchat_verified: true,
                    }));
                    void refreshChannels();
                  }}
                />
              </div>
            </section>
          )}

          {/* ════ Paso 8 · CRM ════ */}
          {stepKey === "crm" && (
            <section>
              <h2 className="text-xl font-semibold">Conecta tu CRM</h2>
              <p className="mt-1 text-sm text-slate-500">
                Tres formas de que selai y tu programa de siempre trabajen juntos. Este
                paso es opcional — si no usas CRM, sáltalo sin miedo.
              </p>

              <div className="mt-4 flex flex-wrap gap-1.5">
                {(
                  [
                    ["export", "Exportar leads"],
                    ["notify", "Notificaciones automáticas"],
                    ["native", "Integración nativa"],
                  ] as const
                ).map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setCrmTab(key)}
                    className={`rounded-full px-3 py-1 text-sm transition-colors ${
                      crmTab === key
                        ? "bg-selai-600 text-white"
                        : "border border-selai-200 bg-white text-slate-600 hover:border-selai-400"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>

              <div className="mt-4 rounded-xl border border-slate-200 bg-white p-5">
                {crmTab === "export" && (
                  <div className="text-sm text-slate-600">
                    <p>
                      Todos los leads que capta tu asistente (con teléfono, presupuesto,
                      zona e interés) se pueden descargar en un archivo CSV que tu CRM
                      importa directamente.
                    </p>
                    <a
                      href="/leads"
                      target="_blank"
                      rel="noreferrer"
                      className="mt-3 inline-flex items-center rounded-md bg-selai-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-selai-700"
                    >
                      Ir a Leads → Exportar CSV
                    </a>
                  </div>
                )}

                {crmTab === "notify" && (
                  <div>
                    <p className="text-sm text-slate-600">
                      Si tu sistema puede recibir avisos por internet, dale aquí su{" "}
                      <strong>dirección de notificaciones (URL)</strong> y le enviaremos
                      cada novedad al instante: nuevo lead, visita agendada… Es lo que
                      tu informático llamaría un «webhook».
                    </p>

                    {/* Estado de carga / error de carga con reintento */}
                    {subs === null && !subsLoadError && (
                      <p className="mt-4 text-sm text-slate-400">
                        Cargando tus notificaciones…
                      </p>
                    )}
                    {subsLoadError && (
                      <div className="mt-4 flex flex-wrap items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                        <span>{subsLoadError}</span>
                        <button
                          type="button"
                          onClick={() => {
                            setSubsLoadError(null);
                            setSubs(null);
                          }}
                          className="underline"
                        >
                          Reintentar
                        </button>
                      </div>
                    )}

                    {/* (194) El secreto completo, una sola vez. Sin él, el
                        CRM no puede verificar la firma HMAC de ningún aviso:
                        se enseñaban 8 caracteres y no servía de nada. */}
                    {freshSecret && (
                      <div className="mt-4 rounded-md border border-amber-300 bg-amber-50 p-3">
                        <p className="text-sm font-medium text-amber-900">
                          Copia ahora tu clave de firma
                        </p>
                        <p className="mt-1 text-sm text-amber-900">
                          Con ella tu CRM comprueba que los avisos vienen de
                          verdad de selai (cabecera{" "}
                          <Code>X-Realtor-Signature</Code>, HMAC-SHA256 sobre el
                          cuerpo).{" "}
                          <strong>
                            No se volverá a mostrar: si la pierdes habrá que
                            crear la suscripción otra vez.
                          </strong>
                        </p>
                        <div className="mt-2 flex flex-wrap items-start gap-2">
                          <pre className="min-w-0 flex-1 overflow-x-auto rounded-md bg-slate-900 px-3 py-2 text-[13px] text-slate-100">
                            <code>{freshSecret.secret}</code>
                          </pre>
                          <CopyButton value={freshSecret.secret} />
                        </div>
                        <button
                          type="button"
                          onClick={() => setFreshSecret(null)}
                          className="mt-2 text-xs text-amber-900 underline"
                        >
                          Ya la he guardado, ocúltala
                        </button>
                      </div>
                    )}

                    {/* Lista actual */}
                    {subs && subs.length > 0 && (
                      <ul className="mt-4 space-y-2">
                        {subs.map((sub) => {
                          const test = subTest[sub.id];
                          return (
                            <li
                              key={sub.id}
                              className="rounded-md border border-slate-200 px-3 py-2 text-sm"
                            >
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <div className="min-w-0">
                                  <p className="truncate font-mono text-[13px] text-slate-800">
                                    {sub.target_url}
                                  </p>
                                  <p className="text-xs text-slate-500">
                                    {sub.events
                                      .map(
                                        (ev) =>
                                          WEBHOOK_EVENTS.find((w) => w.key === ev)
                                            ?.label ?? ev
                                      )
                                      .join(" · ")}
                                    {!sub.is_active && " · en pausa"}
                                  </p>
                                  {sub.secret_unreadable && (
                                    <p className="mt-1 text-xs font-medium text-red-700">
                                      La clave de firma ya no se puede abrir. Borra esta
                                      suscripción y créala de nuevo.
                                    </p>
                                  )}
                                </div>
                                <div className="flex items-center gap-3">
                                  <button
                                    onClick={() => void testSub(sub.id)}
                                    disabled={test?.busy}
                                    className="rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium hover:bg-slate-50 disabled:opacity-60"
                                  >
                                    {test?.busy ? "Enviando…" : "Enviar evento de prueba"}
                                  </button>
                                  <button
                                    onClick={() => void deleteSub(sub.id)}
                                    className="text-xs text-red-600 underline hover:text-red-700"
                                  >
                                    Borrar
                                  </button>
                                </div>
                              </div>
                              {test?.result && (
                                <p
                                  className={`mt-2 text-xs ${
                                    test.result.delivered
                                      ? "text-emerald-700"
                                      : "text-amber-800"
                                  }`}
                                  aria-live="polite"
                                >
                                  {test.result.delivered
                                    ? `Entregado. Tu sistema respondió ${test.result.status_code}.`
                                    : `No se ha podido entregar${
                                        test.result.status_code
                                          ? ` (respondió ${test.result.status_code})`
                                          : " (no contestó)"
                                      }. Comprueba la dirección con quien lleve tu CRM.`}
                                </p>
                              )}
                              {test?.failed && (
                                <p className="mt-2 text-xs text-red-600" aria-live="polite">
                                  No hemos podido enviar la prueba. Inténtalo de nuevo.
                                </p>
                              )}
                            </li>
                          );
                        })}
                      </ul>
                    )}

                    <form onSubmit={createSub} className="mt-4 space-y-3">
                      <div>
                        <label className="mb-1 block text-sm font-medium" htmlFor="sub-url">
                          Dirección de notificaciones (URL)
                        </label>
                        <input
                          id="sub-url"
                          required
                          type="url"
                          value={subUrl}
                          onChange={(e) => setSubUrl(e.target.value)}
                          placeholder="https://tu-crm.es/avisos-selai"
                          className="w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm"
                        />
                      </div>
                      <fieldset>
                        <legend className="mb-1.5 text-sm font-medium">
                          ¿Qué avisos quieres recibir?
                        </legend>
                        <div className="flex flex-wrap gap-x-4 gap-y-1.5">
                          {WEBHOOK_EVENTS.map((ev) => (
                            <label key={ev.key} className="flex items-center gap-1.5 text-sm">
                              <input
                                type="checkbox"
                                checked={subEvents.includes(ev.key)}
                                onChange={(e) =>
                                  setSubEvents((cur) =>
                                    e.target.checked
                                      ? [...cur, ev.key]
                                      : cur.filter((x) => x !== ev.key)
                                  )
                                }
                              />
                              {ev.label}
                            </label>
                          ))}
                        </div>
                      </fieldset>
                      {subsError && (
                        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                          {subsError}
                        </div>
                      )}
                      <button
                        type="submit"
                        disabled={subBusy || subEvents.length === 0}
                        className="rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
                      >
                        {subBusy ? "Creando…" : "Activar notificaciones"}
                      </button>
                    </form>
                  </div>
                )}

                {crmTab === "native" && (
                  <p className="text-sm text-slate-600">
                    Estamos construyendo la integración nativa con{" "}
                    <strong>Inmovilla</strong> y <strong>Witei</strong> — todavía no
                    está disponible y no queremos venderte humo. Mientras llega, la
                    exportación CSV y las notificaciones automáticas cubren el día a
                    día. Si usas otro CRM, escríbenos a hola@selai.es y lo tendremos en
                    cuenta.
                  </p>
                )}
              </div>
            </section>
          )}

          {/* ════ Paso 9 · Prueba final ════ */}
          {stepKey === "test_call" && (
            <section>
              <h2 className="text-xl font-semibold">Prueba tu asistente</h2>
              <p className="mt-1 text-sm text-slate-500">
                Habla con tu IA como lo haría un cliente: pregunta por un piso, pide una
                visita, ponle pegas. Es tu agente real — la conversación aparecerá en tu
                bandeja y puede crear un lead de prueba.
              </p>
              {doneMap.test_call && (
                <div className="mt-3">
                  <DoneBadge>Prueba hecha. Tu asistente respondió.</DoneBadge>
                </div>
              )}
              <div className="mt-4">
                <WizardChat
                  slug={slug}
                  onFirstReply={() => {
                    if (!checklist["test_call"]) void saveChecklist({ test_call: true });
                  }}
                />
              </div>
              {effectiveAiPhone && (
                <p className="mt-3 text-sm text-slate-600">
                  ¿Prefieres oírlo? Llama ahora al <strong>{effectiveAiPhone}</strong> —
                  es tu asistente al teléfono, con la voz que elegiste.
                </p>
              )}
            </section>
          )}
        </div>
      </main>

      {/* ── Pie: navegación ── */}
      <footer className="border-t border-slate-200 bg-white px-4 py-3 sm:px-8">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-3">
          <button
            onClick={() => setStep((n) => Math.max(0, n - 1))}
            disabled={step === 0}
            className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium hover:bg-slate-50 disabled:opacity-40"
          >
            ← Anterior
          </button>
          <div className="flex items-center gap-3">
            <button
              onClick={skip}
              className="text-sm text-slate-400 underline hover:text-slate-600"
            >
              Saltar por ahora
            </button>
            <button
              onClick={() => void next()}
              disabled={nextBusy}
              className="rounded-md bg-selai-600 px-5 py-2 text-sm font-semibold text-white hover:bg-selai-700 disabled:opacity-60"
            >
              {nextBusy ? "Guardando…" : isLast ? "Terminar" : "Siguiente →"}
            </button>
          </div>
        </div>
      </footer>
    </div>
  );
}
