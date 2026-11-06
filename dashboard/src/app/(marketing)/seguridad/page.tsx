import type { Metadata } from "next";

import { Reveal } from "@/components/marketing/animated";
import { IChat, ICheck, IShield } from "@/components/marketing/icons";
import { Container, Pill, Section } from "@/components/marketing/ui";

// NB: "/seguridad" debe estar en las rutas públicas del proxy (isPublic).
// Lo añade el orquestador — no tocar proxy.ts desde aquí.

export const metadata: Metadata = {
  title: "Seguridad y privacidad",
  description:
    "Cómo protege selai los datos de tu inmobiliaria: hosting en la UE (Railway y Vercel), cifrado TLS, aislamiento por agencia con tests automáticos, subencargados listados, cero entrenamiento de modelos de IA con tus datos y un asistente que se presenta siempre como IA. Planes desde 99 €/mes.",
  alternates: { canonical: "/seguridad" },
};

const PRACTICES: ReadonlyArray<{ title: string; body: React.ReactNode }> = [
  {
    title: "Datos alojados en la Unión Europea",
    body: "La aplicación y la base de datos corren en Railway (región UE) y la web en Vercel. Tus conversaciones, contactos y catálogo no salen del Espacio Económico Europeo para su almacenamiento.",
  },
  {
    title: "Cifrado en tránsito",
    body: "Toda la comunicación — entre tu navegador y selai, y entre selai y los proveedores que usamos — viaja cifrada con TLS. No hay tráfico en claro.",
  },
  {
    title: "Aislamiento por agencia, verificado con tests",
    body: "selai es multi-agencia: cada consulta a la base de datos filtra por tu agencia, siempre. Ese aislamiento está cubierto por tests automáticos que se ejecutan en cada cambio del código, para que los datos de una inmobiliaria nunca se mezclen con los de otra.",
  },
  {
    title: "No entrenamos modelos de IA con tus datos",
    body: "Tus conversaciones se usan para responder a tus clientes, no para entrenar modelos — ni nuestros ni de terceros. Está recogido en nuestro DPA y en los contratos con nuestros proveedores de IA.",
  },
  {
    title: "Recuperación sin promesas vacías",
    body: "No anunciamos las copias de seguridad como una medida activa hasta tener un procedimiento documentado, monitorizado y con restauraciones probadas. Así, lo publicado coincide siempre con lo que podemos demostrar.",
  },
  {
    title: "Tus datos son tuyos",
    body: "Si te vas, exportas todo (JSON y CSV) y pedimos el borrado: se certifica en 30 días. Sin ataduras.",
  },
];

// Subencargados reales del servicio. Honestidad ante todo: se listan todos.
const SUBPROCESSORS: ReadonlyArray<readonly [string, string]> = [
  ["Railway", "alojamiento de la aplicación y la base de datos (UE)"],
  ["Vercel", "alojamiento de la web y recuento anónimo de visitas, sin cookies"],
  ["Clerk", "autenticación y gestión de usuarios"],
  ["Stripe", "pagos y facturación"],
  ["Anthropic", "modelo de IA conversacional"],
  ["Voyage AI", "representaciones numéricas para búsqueda semántica del catálogo"],
  ["Meta", "WhatsApp Business Cloud API"],
  ["Google", "calendario (agenda de visitas)"],
  ["Vapi", "infraestructura de llamadas de voz"],
  ["Deepgram", "transcripción de voz (STT)"],
  ["Cartesia", "síntesis de voz (TTS)"],
  ["ElevenLabs", "síntesis y clonado de voz"],
  ["Resend", "envío de email"],
  ["Sentry", "monitorización de errores"],
  ["Expo", "notificaciones de la app móvil"],
];

export default function SeguridadPage() {
  return (
    <>
      <Section className="pt-32 md:pt-44 pb-12">
        <Container>
          <Reveal>
            <Pill tone="selai" className="mb-5">
              <IShield size={13} />
              Seguridad y privacidad
            </Pill>
            <h1
              className="font-display font-bold text-[44px] md:text-[64px] leading-[1.04] text-ink-900 max-w-3xl"
              style={{ textWrap: "balance" } as React.CSSProperties}
            >
              Tus datos, en la UE y solo para trabajar para ti.
            </h1>
            <p
              className="mt-6 text-[18px] text-ink-500 leading-relaxed max-w-[640px]"
              style={{ textWrap: "pretty" } as React.CSSProperties}
            >
              Esto es exactamente lo que hacemos con los datos de tu inmobiliaria y de tus
              clientes: dónde se guardan, quién los toca y qué no haremos nunca con ellos. Sin
              letra pequeña.
            </p>
          </Reveal>
        </Container>
      </Section>

      <Section className="py-12 md:py-16">
        <Container>
          <div className="grid md:grid-cols-2 gap-5">
            {PRACTICES.map((p, i) => (
              <Reveal key={p.title} delay={i * 0.04}>
                <div className="h-full bg-white rounded-3xl ring-1 ring-ink-900/5 shadow-card p-7">
                  <div className="w-9 h-9 rounded-xl bg-selai-100 text-selai-600 flex items-center justify-center mb-4">
                    <ICheck size={16} />
                  </div>
                  <h2 className="font-display font-bold text-[20px] text-ink-900 mb-2">
                    {p.title}
                  </h2>
                  <p className="text-[14px] text-ink-500 leading-relaxed">{p.body}</p>
                </div>
              </Reveal>
            ))}
          </div>

          {/* Sello de identificación de la IA (backlog #174). El asistente se
              presenta como asistente virtual en el primer mensaje de cada
              conversación, en todos los canales. */}
          <Reveal delay={0.08}>
            <div className="mt-12 rounded-3xl bg-ink-900 text-selai-50 grain p-7 md:p-9">
              <div className="flex items-start gap-4">
                <div className="w-10 h-10 rounded-xl bg-selai-500/25 text-selai-100 flex items-center justify-center flex-shrink-0">
                  <IChat size={18} />
                </div>
                <div>
                  <h2 className="font-display font-bold text-[24px] text-selai-50">
                    IA identificada como IA
                  </h2>
                  <p className="mt-3 text-[15px] leading-relaxed text-selai-100/85 max-w-[680px]">
                    En el primer mensaje de cada conversación, el asistente dice que es un
                    asistente virtual: por WhatsApp, por email, en el chat de tu web y al
                    descolgar el teléfono. No hay que preguntárselo ni buscarlo en la letra
                    pequeña.
                  </p>
                  <p className="mt-3 text-[15px] leading-relaxed text-selai-100/85 max-w-[680px]">
                    Y si alguien pregunta directamente si está hablando con una persona, se lo
                    dice: el asistente tiene prohibido negar que es una IA, hacerse pasar por
                    alguien del equipo o inventarse un nombre humano.
                  </p>
                  <p className="mt-3 text-[15px] leading-relaxed text-selai-100/85 max-w-[680px]">
                    Lo pide el Reglamento europeo de IA (artículo 50, aplicable desde agosto de
                    2026), pero sobre todo funciona mejor así: el cliente que sabe con quién
                    habla pregunta más y se molesta menos. Y cuando quiere una persona, la pide
                    y se la pasamos.
                  </p>
                </div>
              </div>
            </div>
          </Reveal>

          <Reveal delay={0.1}>
            <div className="mt-8 bg-white rounded-3xl ring-1 ring-ink-900/5 shadow-card p-7 md:p-9">
              <h2 className="font-display font-bold text-[24px] text-ink-900">
                Subencargados del tratamiento
              </h2>
              <p className="mt-2 text-[14px] text-ink-500 leading-relaxed max-w-[640px]">
                Para prestar el servicio nos apoyamos en estos proveedores. Cada uno trata solo
                los datos imprescindibles para su función y está cubierto por contrato de
                encargo (DPA):
              </p>
              <ul className="mt-6 grid sm:grid-cols-2 lg:grid-cols-3 gap-x-6 gap-y-3">
                {SUBPROCESSORS.map(([name, role]) => (
                  <li key={name} className="flex items-baseline gap-2 text-[14px]">
                    <span className="font-semibold text-ink-900">{name}</span>
                    <span className="text-ink-500">— {role}</span>
                  </li>
                ))}
              </ul>
            </div>
          </Reveal>

          <Reveal delay={0.12}>
            <div className="mt-8 rounded-3xl bg-selai-50 ring-1 ring-selai-200 p-7 md:p-9">
              <h2 className="font-display font-bold text-[22px] text-ink-900">
                Lo que NO te vamos a contar
              </h2>
              <p className="mt-2 text-[14px] text-ink-700 leading-relaxed max-w-[680px]">
                No tenemos certificaciones ISO 27001 ni ENS, y no vamos a fingir que sí: somos un
                equipo pequeño y preferimos contarte con precisión lo que hacemos de verdad. Si
                tu agencia necesita documentación concreta para su evaluación de proveedores,
                escríbenos y te la preparamos.
              </p>
              <div className="mt-5 flex flex-wrap gap-x-6 gap-y-2 text-[14px]">
                <a href="/privacidad" className="text-selai-600 underline decoration-dotted">
                  Política de privacidad
                </a>
                <a href="/terminos" className="text-selai-600 underline decoration-dotted">
                  Términos del servicio
                </a>
                <a
                  href="mailto:hola@selai.es?subject=Solicitud%20de%20DPA"
                  className="text-selai-600 underline decoration-dotted"
                >
                  Pedir el DPA por email
                </a>
              </div>
            </div>
          </Reveal>
        </Container>
      </Section>
    </>
  );
}
