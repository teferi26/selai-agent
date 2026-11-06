import { type Message } from "@/lib/api";
import type { ConversationNote as Note } from "@/lib/agency-settings";
import { getAgencyId, serverApi } from "@/lib/server-api";
import ConversationNote from "./ConversationNote";
import MessageViewport from "./MessageViewport";
import ReplyComposer from "./ReplyComposer";
import ResumeButton from "./ResumeButton";

const ROLE_LABEL: Record<string, string> = {
  lead: "Cliente",
  agent_ai: "Agente IA",
  agent_human: "Agente",
  system: "Sistema",
};

const ROLE_COLOR: Record<string, string> = {
  lead: "bg-slate-100 text-slate-900",
  agent_ai: "bg-blue-50 text-blue-900",
  agent_human: "bg-emerald-50 text-emerald-900",
  system: "bg-amber-50 text-amber-900 italic",
};

// Mismo shape que los items de GET /leads (ver /leads/page.tsx).
type Lead = {
  id: string;
  full_name: string | null;
  phone_e164: string | null;
  email: string | null;
  status: string;
  temperature: "hot" | "warm" | "cold";
  score: number;
  language: string;
  budget_min: number | null;
  budget_max: number | null;
  operation: string | null;
  created_at: string;
};

function formatBudget(l: Lead): string | null {
  if (!l.budget_max && !l.budget_min) return null;
  if (l.budget_max) {
    return `€${(l.budget_min || 0).toLocaleString()}–${l.budget_max.toLocaleString()}`;
  }
  return `desde €${l.budget_min!.toLocaleString()}`;
}

const OPERATION_LABEL: Record<string, string> = {
  buy: "comprar",
  rent: "alquilar",
  sell: "vender",
};

/** GET /agencies/{id}/conversations/{id} — estado de UNA conversación.
 *
 * (13) `handoff_*` son el contexto que la IA dejó al escalar
 * (`escalate_to_human` lo guarda en `conversation.extra`, backend
 * agent/tools.py). Hasta ahora sólo lo leía el aviso por push/email
 * (services/conversation_service.py `build_handoff_summary`): quien abría la
 * conversación en el panel —justo quien va a coger el teléfono— no veía el
 * motivo por ninguna parte. Opcionales a propósito: el endpoint es compartido
 * con la app móvil y una versión del backend que aún no los envíe tiene que
 * seguir pintando la ficha entera. */
type ConversationDetail = {
  id: string;
  status: string;
  channel: "whatsapp" | "email" | "voice" | "sms" | "web";
  lead_id: string;
  assigned_user_id: string | null;
  handoff_reason?: string | null;
  handoff_urgency?: "low" | "normal" | "high" | null;
  handoff_at?: string | null;
};

const URGENCY_LABEL: Record<string, string> = {
  low: "Puede esperar",
  normal: "Normal",
  high: "Urgente",
};

const URGENCY_CLASS: Record<string, string> = {
  low: "bg-slate-100 text-slate-700",
  normal: "bg-amber-100 text-amber-800",
  high: "bg-red-100 text-red-800",
};

export default async function ConversationPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const agencyId = await getAgencyId();

  // El lead y el estado de la conversación son "extra": si el endpoint aún no
  // existe (backend en paralelo) o falla, la página sigue mostrando mensajes.
  const [messages, lead, conversation, note] = await Promise.all([
    serverApi<Message[]>(`/api/v1/agencies/${agencyId}/conversations/${id}/messages`),
    serverApi<Lead>(`/api/v1/agencies/${agencyId}/conversations/${id}/lead`).catch(
      () => null
    ),
    // El estado salía de rebuscar esta conversación en las 200 primeras filas
    // del inbox, que además de traerse 200 previews para usar una fila no
    // encontraba nada en cuanto la agencia tenía más conversaciones abiertas
    // que el tope: sin `channel`, el compositor se negaba a enviar. El
    // endpoint de detalle ya existe (backend api/dashboard.py
    // `conversation_detail`) y responde una sola fila.
    serverApi<ConversationDetail>(
      `/api/v1/agencies/${agencyId}/conversations/${id}`
    ).catch(() => null),
    // La nota puntual para la IA. Si el endpoint falla, la caja sigue
    // apareciendo vacía: escribir una nota nueva no depende de leer la vieja.
    serverApi<Note>(`/api/v1/agencies/${agencyId}/conversations/${id}/note`).catch(
      () => null
    ),
  ]);

  const handedOff = conversation?.status === "handed_off";
  // (13) El motivo sobrevive a un "Devolver a la IA", así que sólo se pinta
  // mientras la conversación siga en manos humanas: un motivo de hace tres
  // semanas encima de una conversación que la IA ya retomó no informa, engaña.
  const handoffReason = handedOff ? conversation?.handoff_reason?.trim() || null : null;
  const handoffUrgency = handoffReason ? conversation?.handoff_urgency ?? null : null;
  const handoffAt = handoffReason ? conversation?.handoff_at ?? null : null;

  // Resumen en 3 líneas: quién es, qué busca, qué falta por saber.
  const who = lead
    ? [lead.full_name, lead.phone_e164 || lead.email].filter(Boolean).join(" · ") ||
      "Sin datos de contacto"
    : null;
  const budget = lead ? formatBudget(lead) : null;
  const wants = lead
    ? [
        lead.operation ? `Quiere ${OPERATION_LABEL[lead.operation] ?? lead.operation}` : null,
        budget ? `presupuesto ${budget}` : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : null;
  const missing = lead
    ? [
        !lead.full_name && "sin nombre",
        !lead.phone_e164 && !lead.email && "sin contacto",
        !lead.operation && "sin operación",
        !lead.budget_min && !lead.budget_max && "sin presupuesto",
      ].filter(Boolean)
    : [];

  return (
    <div className="max-w-3xl">
      <header className="mb-4">
        <h2 className="text-2xl font-semibold">Conversación</h2>
        <p className="text-xs text-slate-500 font-mono">{id}</p>
      </header>

      {/* Tarjeta-resumen del lead */}
      {lead && (
        <div className="mb-4 rounded-xl border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="space-y-1 text-sm">
              <p className="font-medium text-slate-900">{who}</p>
              <p className="text-slate-600">{wants || "Aún no sabemos qué busca"}</p>
              {missing.length > 0 ? (
                <p className="text-amber-700">
                  Falta por saber: {missing.join(", ")}
                </p>
              ) : (
                <p className="text-emerald-700">Ficha completa ✓</p>
              )}
            </div>
            {handedOff && <ResumeButton agencyId={agencyId} conversationId={id} />}
          </div>
        </div>
      )}
      {/* Si no hay lead pero la conversación sigue en manos humanas, el botón
          no puede faltar. */}
      {!lead && handedOff && (
        <div className="mb-4 rounded-xl border border-slate-200 bg-white p-4">
          <ResumeButton agencyId={agencyId} conversationId={id} />
        </div>
      )}

      {/* (13) Por qué la IA se apartó. Es la primera pregunta de quien va a
          retomar el contacto y hasta ahora sólo viajaba en el aviso por email
          o push: si el agente entraba por la bandeja, no la veía nunca. */}
      {handoffReason && (
        <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-medium text-amber-900">
              La IA ha pasado esta conversación a tu equipo
            </p>
            {handoffUrgency && (
              <span
                className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                  URGENCY_CLASS[handoffUrgency] ?? URGENCY_CLASS.normal
                }`}
              >
                {URGENCY_LABEL[handoffUrgency] ?? handoffUrgency}
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-amber-900">
            <span className="font-medium">Por qué:</span> {handoffReason}
          </p>
          {handoffAt && (
            <p className="mt-1 text-xs text-amber-700">
              {new Date(handoffAt).toLocaleString("es-ES")}
            </p>
          )}
        </div>
      )}

      <MessageViewport messageCount={messages.length}>
        {messages.map((m) => (
          <div
            key={m.id}
            className={`rounded-lg px-3 py-2 max-w-[85%] ${
              m.role === "lead" ? "" : "ml-auto"
            }`}
          >
            <div className={`rounded-lg px-3 py-2 ${ROLE_COLOR[m.role]}`}>
              <div className="text-[10px] uppercase tracking-wide text-slate-500 mb-1">
                {ROLE_LABEL[m.role]} ·{" "}
                {new Date(m.created_at).toLocaleString("es-ES")}
              </div>
              <div className="whitespace-pre-wrap">{m.content}</div>
            </div>
          </div>
        ))}
      </MessageViewport>

      {/* (168) El compositor va pegado al histórico: una nota secundaria no
          debe empujarlo fuera de la pantalla en conversaciones largas. */}
      <ReplyComposer
        agencyId={agencyId}
        conversationId={id}
        channel={conversation?.channel ?? null}
        handedOff={handedOff}
        knownMessageIds={messages.map((m) => m.id)}
      />

      {/* Nota puntual para la IA en esta conversación (vale solo aquí). */}
      <ConversationNote agencyId={agencyId} conversationId={id} initial={note} />
    </div>
  );
}
