// Client half of /settings/canales: connection cards fed by GET /channels.
// Reuses the wizard's WhatsAppConnect form and CopyButton so both surfaces
// behave identically.
"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import {
  type ChannelsStatus,
  type PhoneProvisionOut,
  timeAgoEs,
  webchatSnippetFor,
} from "@/lib/channels";
import CopyButton from "../../setup/CopyButton";
import SmsConnect, {
  SmsInboundNotice,
  SmsWebhookBox,
} from "../../setup/SmsConnect";
import WebchatVerify from "../../setup/WebchatVerify";
import WhatsAppConnect, {
  InboundStatusNotice,
  WebhookStatusBox,
  whatsappSecretMissing,
} from "../../setup/WhatsAppConnect";
import WhatsAppTest from "../../setup/WhatsAppTest";

function loadChannels(agencyId: string): Promise<ChannelsStatus> {
  return api<ChannelsStatus>(`/api/v1/agencies/${agencyId}/channels`);
}

function Card({
  title,
  status,
  children,
}: {
  title: string;
  status?: { label: string; tone: "ok" | "warn" | "off" };
  children: React.ReactNode;
}) {
  const toneClass =
    status?.tone === "ok"
      ? "border-emerald-200 bg-emerald-50 text-emerald-800"
      : status?.tone === "warn"
        ? "border-amber-200 bg-amber-50 text-amber-800"
        : "border-slate-200 bg-slate-50 text-slate-500";
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-medium">{title}</h3>
        {status && (
          <span
            className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs ${toneClass}`}
          >
            {status.label}
          </span>
        )}
      </div>
      {children}
    </section>
  );
}

export default function CanalesClient({
  agencyId,
  slug,
}: {
  agencyId: string;
  slug: string;
}) {
  const [channels, setChannels] = useState<ChannelsStatus | null>(null);
  const [loadError, setLoadError] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const out = await loadChannels(agencyId);
      setChannels(out);
      setLoadError(false);
    } catch {
      setLoadError(true);
    }
  }, [agencyId]);

  useEffect(() => {
    let active = true;
    void loadChannels(agencyId).then(
      (out) => {
        if (!active) return;
        setChannels(out);
        setLoadError(false);
      },
      () => {
        if (active) setLoadError(true);
      }
    );
    return () => {
      active = false;
    };
  }, [agencyId]);

  // Aún no sabemos el estado real: no mostramos formularios ni botones que
  // podrían contradecirlo (nada de "Conectar" sobre un WhatsApp ya conectado).
  const loading = channels === null && !loadError;

  // El progreso de "Puesta en marcha" vive en settings.setup_checklist; lo
  // mantenemos en sincronía desde aquí para que el wizard y la barra lateral
  // nunca digan lo contrario que esta página.
  //
  // (217) Solo se limpia. Marcar el paso como hecho es cosa del wizard, y
  // únicamente cuando ha entrado un mensaje de verdad: conectar demuestra que
  // podemos escribir, no que nos llegue nada.
  const markWhatsappStepPending = useCallback(async () => {
    try {
      await api(`/api/v1/agencies/${agencyId}/settings`, {
        method: "PATCH",
        body: JSON.stringify({ setup_checklist: { whatsapp: false } }),
      });
    } catch {
      // Best-effort: solo afecta al contador de progreso, no a la conexión.
    }
  }, [agencyId]);

  // ---- WhatsApp disconnect ----
  const [waBusy, setWaBusy] = useState(false);
  const [waError, setWaError] = useState<string | null>(null);
  const [waConfirm, setWaConfirm] = useState(false);

  async function disconnectWhatsApp() {
    setWaBusy(true);
    setWaError(null);
    try {
      await api<{ ok: boolean }>(`/api/v1/agencies/${agencyId}/channels/whatsapp`, {
        method: "DELETE",
      });
      setWaConfirm(false);
      void markWhatsappStepPending();
      await refresh();
    } catch {
      setWaError("No se pudo desconectar. Inténtalo de nuevo.");
    } finally {
      setWaBusy(false);
    }
  }

  // ---- SMS disconnect ----
  const [smsBusy, setSmsBusy] = useState(false);
  const [smsError, setSmsError] = useState<string | null>(null);
  const [smsConfirm, setSmsConfirm] = useState(false);

  async function disconnectSms() {
    setSmsBusy(true);
    setSmsError(null);
    try {
      await api<{ ok: boolean }>(`/api/v1/agencies/${agencyId}/channels/sms`, {
        method: "DELETE",
      });
      setSmsConfirm(false);
      await refresh();
    } catch {
      setSmsError("No se pudo desconectar. Inténtalo de nuevo.");
    } finally {
      setSmsBusy(false);
    }
  }

  // ---- Phone activation ----
  const [phoneBusy, setPhoneBusy] = useState(false);
  const [phoneError, setPhoneError] = useState<string | null>(null);
  const [phoneRequested, setPhoneRequested] = useState(false);

  async function activatePhone() {
    setPhoneBusy(true);
    setPhoneError(null);
    try {
      const out = await api<PhoneProvisionOut>(
        `/api/v1/agencies/${agencyId}/channels/phone`,
        { method: "POST", body: JSON.stringify({}) }
      );
      if (out.status === "requested") setPhoneRequested(true);
      await refresh();
    } catch {
      setPhoneError("No se pudo activar el número. Inténtalo de nuevo en unos minutos.");
    } finally {
      setPhoneBusy(false);
    }
  }

  // (217) Falta el secreto de la app *propia* de la agencia: hoy rechazamos
  // todos sus webhooks. Un número servido por nuestra app no lo necesita, y
  // decirle a esa agencia que le falta algo sería igual de falso.
  const waSecretMissing = channels
    ? whatsappSecretMissing(channels.whatsapp)
    : false;
  // Solo la que dice el backend. Sin respaldo local: ver el comentario en
  // lib/channels.ts — una dirección de leads equivocada no falla, se copia.
  const leadsEmail = channels?.email.address ?? null;
  const snippet = webchatSnippetFor(slug);
  const phoneStatus =
    channels?.phone.status === "active"
      ? "active"
      : phoneRequested || channels?.phone.status === "requested"
        ? "requested"
        : "none";

  return (
    <div className="max-w-3xl">
      <header className="mb-6">
        <h2 className="text-2xl font-semibold">Canales</h2>
        <p className="text-sm text-slate-500">
          Todo lo que tu asistente tiene conectado: WhatsApp, teléfono, email de
          portales y el chat de tu web. ¿Primera vez? El asistente de{" "}
          <a href="/setup" className="text-selai-600 underline">
            Puesta en marcha
          </a>{" "}
          te guía paso a paso.
        </p>
      </header>

      {loadError && (
        <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          No se pudo cargar el estado de los canales.{" "}
          <button onClick={() => void refresh()} className="underline">
            Reintentar
          </button>
        </div>
      )}

      <div className="space-y-4">
        {/* ── WhatsApp ── */}
        <Card
          title="WhatsApp"
          // (217) La etiqueta la manda la realidad, en este orden: sin
          // secreto no entra nada, sin mensajes recibidos no hay nada
          // demostrado, y solo con mensajes de verdad se puede decir que va.
          status={
            channels
              ? channels.whatsapp.credentials_unreadable
                ? { label: "Requiere reconectar", tone: "warn" }
                : !channels.whatsapp.connected
                ? { label: "Sin conectar", tone: "off" }
                : waSecretMissing
                  ? { label: "Conectado · falta el secreto", tone: "warn" }
                  : !channels.whatsapp.last_inbound_at
                    ? { label: "Conectado · sin mensajes aún", tone: "warn" }
                    : { label: "Recibiendo", tone: "ok" }
              : undefined
          }
        >
          {loading ? (
            <p className="text-sm text-slate-400">Comprobando la conexión…</p>
          ) : channels?.whatsapp.connected ? (
            <div className="text-sm text-slate-600">
              <p>
                Número: <strong>{channels.whatsapp.phone_number}</strong>
                {channels.whatsapp.verified_name
                  ? ` · ${channels.whatsapp.verified_name}`
                  : ""}
              </p>

              <WebhookStatusBox
                subscribed={channels.whatsapp.subscribed_apps}
                appSecretSet={channels.whatsapp.app_secret_set}
                secretMissing={waSecretMissing}
                webhook={{
                  callbackUrl: channels.whatsapp.webhook_callback_url,
                  verifyToken: channels.whatsapp.webhook_verify_token,
                }}
              />

              {/* (217) «Conectado» solo habla de las credenciales guardadas.
                  Esto dice si ha entrado algún mensaje de verdad. */}
              <InboundStatusNotice
                lastInboundAt={channels.whatsapp.last_inbound_at}
                inboundCount={channels.whatsapp.inbound_count}
                secretMissing={waSecretMissing}
              />

              {/* (189) «Conectado» solo dice que las claves existen. Esto
                  manda un mensaje de verdad y lo demuestra. */}
              <WhatsAppTest agencyId={agencyId} />

              {waError && (
                <div className="mt-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-red-700">
                  {waError}
                </div>
              )}
              {waConfirm ? (
                <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2.5">
                  <p className="text-sm text-red-800">
                    Tu asistente dejará de responder WhatsApp al momento. ¿Seguro?
                  </p>
                  <div className="mt-2 flex gap-2">
                    <button
                      onClick={() => void disconnectWhatsApp()}
                      disabled={waBusy}
                      className="rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-60"
                    >
                      {waBusy ? "Desconectando…" : "Sí, desconectar"}
                    </button>
                    <button
                      onClick={() => setWaConfirm(false)}
                      className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-50"
                    >
                      Cancelar
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  onClick={() => setWaConfirm(true)}
                  className="mt-3 text-sm text-red-600 underline hover:text-red-700"
                >
                  Desconectar WhatsApp
                </button>
              )}
            </div>
          ) : (
            <div>
              {channels?.whatsapp.credentials_unreadable && (
                <div className="mb-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
                  La conexión guardada ya no se puede abrir. Vuelve a conectar
                  WhatsApp para sustituir las credenciales sin exponerlas.
                </div>
              )}
              <WhatsAppConnect
                agencyId={agencyId}
                onConnected={() => {
                  void refresh();
                }}
              />
            </div>
          )}
        </Card>

        {/* ── SMS ── */}
        <Card
          title="SMS"
          // Igual que WhatsApp: la etiqueta la manda la realidad. Guardar las
          // claves demuestra que podemos escribir; hasta que no entra un SMS
          // de verdad, el webhook de la consola de Twilio está sin probar.
          status={
            channels
              ? channels.sms.credentials_unreadable
                ? { label: "Requiere reconectar", tone: "warn" }
                : !channels.sms.configured
                ? { label: "Sin conectar", tone: "off" }
                : !channels.sms.last_inbound_at
                  ? { label: "Conectado · sin mensajes aún", tone: "warn" }
                  : { label: "Recibiendo", tone: "ok" }
              : undefined
          }
        >
          {loading ? (
            <p className="text-sm text-slate-400">Comprobando la conexión…</p>
          ) : channels?.sms.configured ? (
            <div className="text-sm text-slate-600">
              <p>
                Número: <strong>{channels.sms.phone_number}</strong>
                {channels.sms.friendly_name ? ` · ${channels.sms.friendly_name}` : ""}
              </p>
              <p className="mt-1 text-xs text-slate-500">
                Cuenta de Twilio <code>{channels.sms.account_sid}</code>. El Auth
                Token se guarda cifrado y no se muestra nunca: si lo cambias en
                Twilio, vuelve a conectar aquí.
              </p>

              <SmsWebhookBox webhookUrl={channels.sms.webhook_url} />

              <SmsInboundNotice
                lastInboundAt={channels.sms.last_inbound_at}
                inboundCount={channels.sms.inbound_count}
              />

              {smsError && (
                <div className="mt-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-red-700">
                  {smsError}
                </div>
              )}
              {smsConfirm ? (
                <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2.5">
                  <p className="text-sm text-red-800">
                    Tu asistente dejará de responder SMS al momento. ¿Seguro?
                  </p>
                  <div className="mt-2 flex gap-2">
                    <button
                      onClick={() => void disconnectSms()}
                      disabled={smsBusy}
                      className="rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-60"
                    >
                      {smsBusy ? "Desconectando…" : "Sí, desconectar"}
                    </button>
                    <button
                      onClick={() => setSmsConfirm(false)}
                      className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-50"
                    >
                      Cancelar
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  onClick={() => setSmsConfirm(true)}
                  className="mt-3 text-sm text-red-600 underline hover:text-red-700"
                >
                  Desconectar SMS
                </button>
              )}
            </div>
          ) : (
            <div>
              {channels?.sms.credentials_unreadable && (
                <div className="mb-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
                  La conexión guardada ya no se puede abrir. Vuelve a conectar SMS
                  para sustituir el token de forma segura.
                </div>
              )}
              <SmsConnect
                agencyId={agencyId}
                webhookUrl={channels?.sms.webhook_url ?? null}
                onConnected={() => {
                  void refresh();
                }}
              />
            </div>
          )}
        </Card>

        {/* ── Teléfono ── */}
        <Card
          title="Teléfono de tu IA"
          status={
            channels
              ? phoneStatus === "active"
                ? { label: "Activo", tone: "ok" }
                : phoneStatus === "requested"
                  ? { label: "Solicitado", tone: "warn" }
                  : { label: "Sin activar", tone: "off" }
              : undefined
          }
        >
          {loading ? (
            <p className="text-sm text-slate-400">Comprobando el estado…</p>
          ) : phoneStatus === "active" && channels?.phone.number ? (
            <div className="text-sm text-slate-600">
              <p className="text-2xl font-semibold tracking-wide text-slate-900">
                {channels.phone.number}
              </p>
              <p className="mt-2">
                Los códigos de desvío de llamadas están en{" "}
                <a href="/settings/voz#numero" className="text-selai-600 underline">
                  Voz del agente
                </a>
                .
              </p>
            </div>
          ) : phoneStatus === "requested" ? (
            <p className="text-sm text-amber-800">
              Solicitado: te lo activamos en menos de 24h y te avisamos por email.
            </p>
          ) : (
            <div className="text-sm text-slate-600">
              <p>
                Actívalo y tu asistente atenderá llamadas en su propio número. Los
                números de activación instantánea son de EE. UU. (+1); si no hay
                disponibles, lo solicitamos por ti y lo tienes en menos de 24 h.
              </p>
              {phoneError && (
                <div className="mt-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-red-700">
                  {phoneError}
                </div>
              )}
              <button
                onClick={() => void activatePhone()}
                disabled={phoneBusy}
                className="mt-3 rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
              >
                {phoneBusy ? "Activando…" : "Activar número"}
              </button>
            </div>
          )}
        </Card>

        {/* ── Email de portales ── */}
        <Card
          title="Leads de portales (email)"
          status={
            channels
              ? channels.email.received_count > 0
                ? { label: "Recibiendo", tone: "ok" }
                : { label: "Sin recepciones", tone: "off" }
              : undefined
          }
        >
          <div className="text-sm text-slate-600">
            <p className="mb-2">
              Configura en Idealista, Fotocasa o Habitaclia el envío de avisos de leads
              a esta dirección (o crea una regla de reenvío en tu correo):
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <code className="rounded-md bg-slate-100 px-3 py-2 font-mono text-sm text-slate-800">
                {leadsEmail ?? "cargando…"}
              </code>
              {leadsEmail && <CopyButton value={leadsEmail} />}
            </div>
            {channels && channels.email.received_count > 0 && (
              <p className="mt-2 text-emerald-700">
                {channels.email.received_count} email
                {channels.email.received_count === 1 ? "" : "s"} recibido
                {channels.email.received_count === 1 ? "" : "s"}
                {channels.email.last_received_at
                  ? ` · el último ${timeAgoEs(channels.email.last_received_at)}`
                  : ""}
                .
              </p>
            )}
          </div>
        </Card>

        {/* ── Chat web ── */}
        <Card
          title="Chat de tu web"
          status={
            channels
              ? channels.webchat.verified
                ? { label: "Comprobado en tu web", tone: "ok" }
                : { label: "Sin comprobar", tone: "off" }
              : undefined
          }
        >
          <div className="text-sm text-slate-600">
            <p className="mb-2">
              Pega esta línea en tu web, antes de{" "}
              <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[13px]">
                &lt;/body&gt;
              </code>
              , y tendrás un chat atendido por tu asistente:
            </p>
            <div className="flex flex-wrap items-start gap-2">
              <pre className="min-w-0 flex-1 overflow-x-auto rounded-md bg-slate-900 px-3 py-2.5 text-[13px] leading-relaxed text-slate-100">
                <code>{snippet}</code>
              </pre>
              <CopyButton value={snippet} />
            </div>
            <a
              href={`/chat/${slug}`}
              target="_blank"
              rel="noreferrer"
              className="mt-2 inline-block text-selai-600 underline hover:text-selai-700"
            >
              Ver cómo queda el chat
            </a>

            {/* (195) Un chat que nunca apareció en la web del cliente es
                baja segura: aquí se comprueba, no se supone. */}
            <WebchatVerify
              agencyId={agencyId}
              verifiedUrl={
                channels?.webchat.verified ? channels.webchat.verified_url : null
              }
              onVerified={() => void refresh()}
            />
          </div>
        </Card>

        {/* ── Notificaciones a tu CRM ── */}
        <Card
          title="Notificaciones a tu CRM"
          status={
            channels
              ? channels.webhooks.count > 0
                ? { label: `${channels.webhooks.count} activas`, tone: "ok" }
                : { label: "Ninguna", tone: "off" }
              : undefined
          }
        >
          <p className="text-sm text-slate-600">
            Avisos automáticos a tu sistema cada vez que hay un lead nuevo, una visita
            agendada… Se gestionan en el paso{" "}
            <a href="/setup?step=crm" className="text-selai-600 underline">
              «Tu CRM» de la Puesta en marcha
            </a>
            .
          </p>
        </Card>
      </div>
    </div>
  );
}
