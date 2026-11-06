import type { Metadata } from "next";

import { Reveal } from "@/components/marketing/animated";
import { BookCTA } from "@/components/marketing/analytics";
import { BOOK_HREF, BOOK_LABEL, BOOK_TIPO, HAS_CALCOM } from "@/components/marketing/booking";
import { GSM_CODES, OPERATOR_NOTE, OPERATORS } from "@/components/marketing/operator-codes";
import { Container, Pill, Section } from "@/components/marketing/ui";

// NB: "/ayuda" debe estar en las rutas públicas del proxy (isPublic).
// Lo añade el orquestador — no tocar proxy.ts desde aquí.

export const metadata: Metadata = {
  title: "Centro de ayuda",
  description:
    "Guías rápidas de selai: desviar llamadas por operador, preparar el CSV, conectar WhatsApp, recibir los leads de los portales, clonar tu voz, cambiar de plan y ejercer tus derechos RGPD. Respondemos en menos de 4 h laborables (L-V 9:00-18:00). Planes desde 99 €/mes.",
  alternates: { canonical: "/ayuda" },
};

function Code({ children }: { children: React.ReactNode }) {
  return (
    <code className="rounded bg-selai-100 px-1.5 py-0.5 font-mono text-[13px] text-selai-700 whitespace-nowrap">
      {children}
    </code>
  );
}

const ARTICLES: ReadonlyArray<{ title: string; body: React.ReactNode }> = [
  {
    title: "Desviar tus llamadas al número de tu IA (por operador)",
    body: (
      <>
        <p>
          No cambias de número: desvías el tuyo. En tu móvil marcas{" "}
          <Code>{GSM_CODES.forwardAll}</Code> para desviar todas las llamadas, o{" "}
          <Code>{GSM_CODES.forwardUnanswered}</Code> para desviar solo cuando no contestas.
          Para quitar el desvío: <Code>{GSM_CODES.cancelAll}</Code> (o{" "}
          <Code>{GSM_CODES.cancelUnanswered}</Code> si era «solo si no contesto»).
        </p>
        <p className="mt-3">
          Funciona igual en {OPERATORS.slice(0, -1).join(", ")} y {OPERATORS.at(-1)}: son códigos
          estándar de la red móvil. {OPERATOR_NOTE}
        </p>
      </>
    ),
  },
  {
    title: "Preparar el CSV de tu catálogo",
    body: (
      <>
        <p>
          En la sección «Catálogo» de tu panel descargas la plantilla CSV con las columnas
          exactas (referencia, dirección, precio, habitaciones, etc.). La rellenas en Excel o
          Google Sheets — una fila por propiedad — y la guardas como CSV.
        </p>
        <p className="mt-3">
          Consejos: no cambies los nombres de las columnas, usa números sin puntos de miles en
          el precio y revisa que cada propiedad tenga referencia única. Al subirla, el panel te
          avisa fila a fila si algo no cuadra.
        </p>
      </>
    ),
  },
  {
    title: "Importar propiedades desde tu CRM o Idealista",
    body: (
      <>
        <p>
          Exporta tu cartera desde tu CRM (Inmovilla, Witei, HubSpot…) o desde Idealista a
          Excel, ajusta las columnas a la plantilla de selai y súbela como CSV en «Catálogo».
          En segundos tienes todas tus propiedades dentro y la IA habla de ellas con datos
          reales.
        </p>
        <p className="mt-3">
          La conexión automática con Idealista está en camino; mientras tanto, el CSV es la
          vía más rápida.
        </p>
      </>
    ),
  },
  {
    title: "Elegir la voz de tu asistente — o clonar la tuya",
    body: (
      <>
        <p>
          En «Voz del agente» escuchas voces naturales en español, masculinas y femeninas, y
          eliges con un clic. Puedes cambiarla cuando quieras.
        </p>
        <p className="mt-3">
          Para clonar tu propia voz (plan Agencia): subes 1-3 audios de 30 segundos a 2 minutos
          leyendo cualquier texto con naturalidad. Importante: confirmas que la voz es tuya o
          que tienes consentimiento por escrito de la persona — sin ese consentimiento no se
          puede clonar una voz.
        </p>
      </>
    ),
  },
  {
    title: "Qué pasa durante (y después de) los 14 días de prueba",
    body: (
      <>
        <p>
          Al registrarte tienes 14 días con todas las funciones del plan Pro, sin tarjeta. Los
          usas para poner en marcha tu IA, hacer llamadas de prueba y ver conversaciones
          reales.
        </p>
        <p className="mt-3">
          Al acabar, eliges plan y pones la tarjeta — o tu cuenta se pausa. Nunca se cobra
          solo: como no pedimos tarjeta para probar, es imposible un cargo que no hayas
          decidido tú. Si no sigues, tus datos se borran.
        </p>
      </>
    ),
  },
  {
    title: "Conectar WhatsApp",
    body: (
      <>
        <p>
          Desde el paso «WhatsApp» del asistente de activación de tu panel: es una conexión
          guiada con Meta, pantalla a pantalla, y son unos 15 minutos de tu parte.
        </p>
        <p className="mt-3">
          Ten en cuenta que, si Meta aún no ha verificado tu empresa, su revisión tarda 1-2
          días — es un plazo de Meta, no nuestro, y el panel te muestra en qué punto está. Se
          hace en paralelo y no te bloquea: mientras tanto tu IA ya atiende llamadas, los
          leads de los portales y el chat de tu web. Si te atascas, escríbenos a{" "}
          <a href="mailto:hola@selai.es" className="text-selai-600 underline">
            hola@selai.es
          </a>{" "}
          y lo hacemos contigo.
        </p>
      </>
    ),
  },
  {
    title: "Recibir los leads de Idealista, Fotocasa y Habitaclia",
    body: (
      <>
        <p>
          En el paso «Portales» del asistente de activación, el panel te da una dirección de
          email única para tu agencia, con botón de copiar. Entras en tu cuenta del portal y
          reenvías a esa dirección los avisos de nuevos contactos (en Idealista, por ejemplo,
          añadiéndola como email de aviso).
        </p>
        <p className="mt-3">
          Desde ese momento, cada lead del portal entra en selai al instante y la IA lo
          atiende y lo deja cualificado en tu bandeja. Funciona desde el primer día, sin
          esperar la aprobación de nadie.
        </p>
      </>
    ),
  },
  {
    title: "Tomar el control de una conversación",
    body: (
      <>
        <p>
          Cuando un cliente pide hablar con una persona (o la IA nota que está molesto), la
          conversación pasa a tu equipo: te llega un aviso, la IA se aparta y en tu bandeja
          tienes todo el historial con la ficha del lead resumida en 3 líneas — quién es, qué
          busca y qué falta por saber — para retomar el contacto tú mismo.
        </p>
        <p className="mt-3">
          También decides cuándo atiende la IA, desde «Mi agente IA» en tu panel: siempre
          (24/7) o solo fuera de tu horario, para que dentro de él conteste tu equipo. Y al
          terminar, un clic en «Devolver a la IA» y el asistente retoma la conversación.
        </p>
      </>
    ),
  },
  {
    title: "Cambiar de plan o cancelar",
    body: (
      <>
        <p>
          Desde la sección «Facturación» de tu panel, en un clic y sin llamar a nadie. Subir o
          bajar de plan se prorratea; la cancelación es efectiva al final del periodo que ya
          has pagado.
        </p>
        <p className="mt-3">
          No hay permanencia ni penalización. Si te vas, te llevas una copia completa de tus
          datos (JSON y CSV) y pedimos el borrado certificado en 30 días.
        </p>
      </>
    ),
  },
  {
    title: "Cómo ejercer tus derechos RGPD (y los de tus clientes)",
    body: (
      <>
        <p>
          Acceso, rectificación, supresión, oposición, limitación y portabilidad: escríbenos a{" "}
          <a href="mailto:hola@selai.es" className="text-selai-600 underline">
            hola@selai.es
          </a>{" "}
          desde el email de tu cuenta y lo tramitamos. Si un cliente tuyo ejerce sus derechos
          ante ti, te ayudamos a localizar y exportar o borrar sus conversaciones.
        </p>
        <p className="mt-3">
          Tus datos se alojan en la UE y no se usan para entrenar modelos de IA. Más detalle en{" "}
          <a href="/seguridad" className="text-selai-600 underline">
            Seguridad y privacidad
          </a>{" "}
          y en la{" "}
          <a href="/privacidad" className="text-selai-600 underline">
            política de privacidad
          </a>
          .
        </p>
      </>
    ),
  },
  {
    title: "Contacto y horario de soporte",
    body: (
      <>
        <p>
          Escríbenos a{" "}
          <a href="mailto:hola@selai.es" className="text-selai-600 underline">
            hola@selai.es
          </a>
          . Respondemos en menos de 4 h laborables, de lunes a viernes de 9:00 a 18:00
          (hora peninsular). Los clientes del plan Agencia tienen soporte prioritario.
        </p>
        {/* (18) Antes esto prometía "reserva una videollamada" sin enlace ni
            botón: la única acción posible era escribir otra vez al mismo email
            de arriba. `booking.ts` ya resuelve el destino real — la agenda de
            Cal.com si está configurada, y si no un email con el asunto ya
            escrito — así que aquí sólo hay que enseñarlo, con el verbo que
            corresponda: sin agenda pública no se "reserva" nada. */}
        <p className="mt-3">
          {HAS_CALCOM
            ? "Si prefieres verlo en directo, reserva una videollamada de 20 minutos y hacemos la puesta en marcha contigo."
            : "Si prefieres verlo en directo, pídenos una videollamada de 20 minutos y hacemos la puesta en marcha contigo. Nos dices dos huecos que te vengan bien y te confirmamos el mismo día."}
        </p>
        <p className="mt-4">
          <BookCTA
            href={BOOK_HREF}
            origen="ayuda_soporte"
            tipo={BOOK_TIPO}
            external={HAS_CALCOM}
            variant="selai"
            size="sm"
          >
            {BOOK_LABEL}
          </BookCTA>
        </p>
      </>
    ),
  },
];

const GLOSSARY: ReadonlyArray<readonly [string, string]> = [
  [
    "Lead",
    "Un cliente potencial: cualquier persona que te escribe o llama interesándose por una propiedad (desde Idealista, WhatsApp, tu web…).",
  ],
  [
    "CSV",
    "Un archivo de tabla sencillo (se abre con Excel) donde cada fila es una propiedad. Es el formato con el que subes tu catálogo a selai.",
  ],
  [
    "Desvío",
    "La función de tu operador móvil que manda tus llamadas entrantes a otro número — en este caso, al número de tu IA. Se activa y desactiva marcando un código.",
  ],
  [
    "CRM",
    "El programa donde tu agencia guarda contactos y operaciones (Inmovilla, Witei, HubSpot…). Hoy selai te deja exportar tus leads a CSV y avisar a tu CRM automáticamente de cada lead nuevo; las integraciones nativas con Inmovilla y Witei están en camino.",
  ],
  [
    "IA",
    "Inteligencia artificial: el «cerebro» que lee o escucha a tu cliente y responde como lo haría un comercial de tu agencia, con tu catálogo delante y el tono que tú elijas.",
  ],
];

export default function AyudaPage() {
  return (
    <>
      <Section className="pt-32 md:pt-44 pb-10">
        <Container>
          <Reveal>
            <Pill tone="selai" className="mb-5">
              Centro de ayuda
            </Pill>
            <h1
              className="font-display font-bold text-[44px] md:text-[64px] leading-[1.04] text-ink-900 max-w-3xl"
              style={{ textWrap: "balance" } as React.CSSProperties}
            >
              Las respuestas, sin dar vueltas.
            </h1>
            <p
              className="mt-6 text-[18px] text-ink-500 leading-relaxed max-w-[640px]"
              style={{ textWrap: "pretty" } as React.CSSProperties}
            >
              Las guías que resuelven el 95% de las dudas. Si la tuya no está, escríbenos a{" "}
              <a href="mailto:hola@selai.es" className="text-selai-600 underline">
                hola@selai.es
              </a>{" "}
              — respondemos en menos de 4 h laborables (L-V, 9:00-18:00).
            </p>
          </Reveal>
        </Container>
      </Section>

      <Section className="py-10 md:py-14">
        <Container>
          <div className="max-w-[820px] space-y-2">
            {ARTICLES.map(({ title, body }) => (
              <details
                key={title}
                className="group rounded-2xl ring-1 ring-ink-900/5 bg-white open:ring-selai-200 shadow-card"
              >
                <summary className="flex items-center justify-between gap-4 px-6 py-5 cursor-pointer list-none [&::-webkit-details-marker]:hidden">
                  <h2 className="font-display font-bold text-[17px] md:text-[19px] text-ink-900 leading-snug">
                    {title}
                  </h2>
                  <span
                    className="flex-shrink-0 w-7 h-7 rounded-full bg-ink-900/5 text-ink-700 group-open:bg-selai-500 group-open:text-white flex items-center justify-center transition-colors duration-300"
                    aria-hidden
                  >
                    <svg
                      width={14}
                      height={14}
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth={1.6}
                      strokeLinecap="round"
                      className="transition-transform duration-300 group-open:rotate-45"
                    >
                      <path d="M12 5v14M5 12h14" />
                    </svg>
                  </span>
                </summary>
                <div className="px-6 pb-6 text-[15px] text-ink-500 leading-relaxed">{body}</div>
              </details>
            ))}
          </div>

          {/* Mini-glosario (backlog #116) */}
          <Reveal delay={0.05}>
            <div className="mt-14 max-w-[820px] bg-white rounded-3xl ring-1 ring-ink-900/5 shadow-card p-7 md:p-9">
              <h2 className="font-display font-bold text-[24px] text-ink-900">
                Mini-glosario, en llano
              </h2>
              <dl className="mt-5 space-y-4">
                {GLOSSARY.map(([term, def]) => (
                  <div key={term} className="grid sm:grid-cols-[110px_1fr] gap-x-4 gap-y-1">
                    <dt className="font-semibold text-[15px] text-selai-700">{term}</dt>
                    <dd className="text-[14px] text-ink-500 leading-relaxed">{def}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </Reveal>
        </Container>
      </Section>
    </>
  );
}
