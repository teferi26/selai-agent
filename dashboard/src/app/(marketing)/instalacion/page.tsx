import type { Metadata } from "next";

import { BookCTA, SignupCTA } from "@/components/marketing/analytics";
import { Reveal } from "@/components/marketing/animated";
import {
  BOOK_HREF,
  BOOK_LABEL,
  BOOK_TIPO,
  HAS_CALCOM,
} from "@/components/marketing/booking";
import { IArrow, ICheck } from "@/components/marketing/icons";
import {
  GSM_CODES,
  OPERATOR_NOTE,
  OPERATORS,
} from "@/components/marketing/operator-codes";
import { Btn, Container, Pill, Section } from "@/components/marketing/ui";

export const metadata: Metadata = {
  title: "Puesta en marcha de selai · Operativa el primer día",
  description:
    "El asistente de activación de selai te guía en 9 pasos, botón a botón: voz, número de teléfono, WhatsApp, portales, catálogo y chat web. Operativa el primer día; WhatsApp al 100% cuando Meta verifique tu empresa (1-2 días). 14 días gratis sin tarjeta.",
  alternates: { canonical: "/instalacion" },
};

const STEPS: ReadonlyArray<{
  n: string;
  time: string;
  title: string;
  body: React.ReactNode;
}> = [
  {
    n: "01",
    time: "2 min",
    title: "Datos de tu agencia",
    body: "Te registras en selai.es/sign-up con tu email, pones el nombre y los datos básicos de tu inmobiliaria y aceptas las condiciones. Sin tarjeta: empiezas con 14 días de prueba y el asistente de activación te guía desde el primer segundo, botón a botón.",
  },
  {
    n: "02",
    time: "2 min",
    title: "Elige la voz de tu asistente",
    body: "Escuchas voces naturales en español, masculinas y femeninas, con muestra de audio, y eliges con un clic. Con el plan Agencia también puedes clonar tu propia voz subiendo un par de audios (con consentimiento por escrito si la voz es de otra persona). La cambias siempre que quieras.",
  },
  {
    n: "03",
    time: "1 clic (o <24 h)",
    title: "Activa el número de teléfono de tu IA",
    body: (
      <>
        Tu asistente atiende en su propio número. Pulsas un botón en el panel y, si hay
        números de activación instantánea disponibles, queda activo al momento — ojo, con
        honestidad: esos números son de Estados Unidos (empiezan por +1). Si no hay ninguno
        libre, la solicitud se envía sola y te lo activamos nosotros en menos de 24 horas;
        el panel y tu email te avisan cuando esté listo.
        <br />
        <br />
        ¿Quieres que tus clientes sigan llamando a tu número de siempre? No lo cambias:
        lo desvías. En tu móvil marcas{" "}
        <code className="rounded bg-selai-100 px-1.5 py-0.5 font-mono text-[13px] text-selai-700">
          **21*NÚMERO-DE-TU-IA#
        </code>{" "}
        para desviar todas las llamadas, o{" "}
        <code className="rounded bg-selai-100 px-1.5 py-0.5 font-mono text-[13px] text-selai-700">
          **61*NÚMERO-DE-TU-IA#
        </code>{" "}
        para desviar solo cuando no contestas. Para quitar el desvío, marcas{" "}
        <code className="rounded bg-selai-100 px-1.5 py-0.5 font-mono text-[13px] text-selai-700">
          ##21#
        </code>
        .
        {/* Códigos por operador (backlog #69) */}
        <details className="group mt-4 rounded-2xl ring-1 ring-ink-900/10 bg-paper/60 open:bg-white open:ring-selai-200">
          <summary className="flex items-center justify-between gap-3 px-4 py-3 cursor-pointer list-none [&::-webkit-details-marker]:hidden text-[14px] font-medium text-ink-900">
            Códigos por operador
            <span
              className="text-selai-600 transition-transform duration-300 group-open:rotate-45 text-[18px] leading-none"
              aria-hidden
            >
              +
            </span>
          </summary>
          <div className="px-4 pb-4">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[480px] text-left text-[13px] border-collapse">
                <thead>
                  <tr className="text-[11px] uppercase tracking-wider text-ink-500 font-mono">
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Operador
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Desviar todo
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Solo si no contestas
                    </th>
                    <th scope="col" className="py-2 font-medium">
                      Quitar desvío
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {OPERATORS.map((op) => (
                    <tr key={op} className="border-t border-ink-900/5">
                      <th scope="row" className="py-2 pr-3 font-medium text-ink-900 text-left">
                        {op}
                      </th>
                      <td className="py-2 pr-3 font-mono text-selai-700">
                        {GSM_CODES.forwardAll}
                      </td>
                      <td className="py-2 pr-3 font-mono text-selai-700">
                        {GSM_CODES.forwardUnanswered}
                      </td>
                      <td className="py-2 font-mono text-selai-700">{GSM_CODES.cancelAll}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-3 text-[12px] text-ink-500 leading-relaxed">{OPERATOR_NOTE}</p>
          </div>
        </details>
      </>
    ),
  },
  {
    n: "04",
    time: "15 min + Meta",
    title: "Conecta WhatsApp",
    body: "Conexión guiada con Meta desde tu panel: el asistente te lleva pantalla a pantalla y son unos 15 minutos de tu parte. Ojo, con honestidad: si Meta aún no ha verificado tu empresa, su revisión tarda 1-2 días — depende de Meta, no de nosotros, y el panel te muestra en qué punto está. Mientras tanto, el resto de canales ya funcionan.",
  },
  {
    n: "05",
    time: "5 min",
    title: "Conecta los portales (Idealista, Fotocasa, Habitaclia)",
    body: "Te damos una dirección de email única para tu agencia (la ves en el panel, con botón de copiar). Entras en tu cuenta del portal y reenvías a esa dirección los avisos de nuevos contactos. Desde ese momento, cada lead del portal entra en selai al instante y la IA lo atiende. Funciona desde hoy, sin esperas.",
  },
  {
    n: "06",
    time: "10 min",
    title: "Importa tu catálogo de propiedades",
    body: "Para que la IA hable de tus pisos con datos reales: descargas la plantilla CSV (incluida, con las columnas exactas), la rellenas en Excel o exportas desde tu CRM o Idealista, y la subes. El panel te avisa fila a fila si algo no cuadra. En segundos tienes toda tu cartera dentro.",
  },
  {
    n: "07",
    time: "2 min",
    title: "Pon el chat en tu web",
    body: "El panel te da un pequeño fragmento de código con botón de copiar. Lo pegas en tu web (o se lo mandas a quien te la lleve, tal cual) y tu web pasa a tener un botón de chat atendido por tu asistente las 24 horas, con tu catálogo delante para responder con datos reales.",
  },
  {
    n: "08",
    time: "opcional · 5 min",
    title: "Conecta tu CRM",
    body: "Lo que hay hoy, sin adornos: exportas todos tus leads a CSV en un clic (para importarlos en tu CRM cuando quieras) y, si tu CRM admite avisos automáticos, en el panel tienes la dirección de notificaciones lista para copiar — cada lead nuevo le llega solo. Las integraciones nativas con Inmovilla y Witei están en camino; cuando estén, aparecerán como un botón más en este paso.",
  },
  {
    n: "09",
    time: "2 min",
    title: "Prueba tu asistente",
    body: "Llama al número de tu IA o escríbele por el chat y haz de cliente: pregunta por un piso, pon pegas, pide una visita. Comprobarás cómo suena, cómo responde y cómo agenda. Si algo no te convence, ajustas la voz o el catálogo y vuelves a probar.",
  },
];

const FAQ: ReadonlyArray<{ q: string; a: React.ReactNode }> = [
  {
    q: "¿Cuánto tarda todo el proceso?",
    a: "Tu parte es más o menos una hora, repartida como quieras, y la IA queda operativa el primer día. La única excepción es WhatsApp: si tu empresa aún no está verificada por Meta, su revisión tarda 1-2 días y ese canal se activa cuando Meta termina. Todo lo demás funciona desde el primer momento.",
  },
  {
    q: "¿Cuánto tarda WhatsApp exactamente?",
    a: "La conexión guiada desde el panel son unos 15 minutos de tu parte. Si tu empresa ya está verificada por Meta, queda funcionando ese mismo día. Si no, Meta revisa tu empresa en 1-2 días (es su plazo, no el nuestro) y el panel te va diciendo en qué punto está. Mientras, llamadas, portales, email y chat web ya atienden.",
  },
  {
    q: "¿Cómo llegan los leads de Idealista o Fotocasa?",
    a: "Por reenvío de email, automático y al instante: reenvías los avisos del portal a la dirección única de tu agencia (te la damos en el panel) y cada nuevo contacto entra en selai en el momento, ya atendido por la IA. No dependemos de que el portal nos apruebe nada: funciona desde el primer día.",
  },
  {
    q: "¿Puedo seguir usando mi propio número?",
    a: "Sí. Tu número de siempre sigue siendo tuyo: lo desvías al número de tu IA marcando **21*NÚMERO-DE-TU-IA# en tu móvil (o **61*… para desviar solo cuando no contestas) y lo quitas cuando quieras con ##21#. Son códigos estándar de la red móvil; si tu tarifa los restringe, tu operador te activa el desvío gratis por teléfono.",
  },
  {
    q: "¿Puedo usar mi propia voz?",
    a: "Sí, con el plan Agencia. Subes entre 1 y 3 audios de 30 segundos a 2 minutos leyendo cualquier texto con naturalidad, confirmas que la voz es tuya (o que tienes consentimiento por escrito de la persona) y selai crea una copia de tu voz. Tus clientes oirán «tu» voz aunque estés enseñando un piso.",
  },
  {
    q: "¿Y si me atasco en algún paso?",
    a: (
      <>
        Escríbenos a{" "}
        <a href="mailto:hola@selai.es" className="text-selai-600 underline">
          hola@selai.es
        </a>{" "}
        o reserva una videollamada de 20 minutos y lo hacemos contigo. Nadie se queda a
        medias.
      </>
    ),
  },
];

export default function InstalacionPage() {
  return (
    <>
      <Section className="pt-32 md:pt-44 pb-12">
        <Container>
          <Reveal>
            <Pill tone="selai" className="mb-5">
              Puesta en marcha
            </Pill>
            <h1
              className="font-display font-bold text-[44px] md:text-[64px] leading-[1.04] text-ink-900 max-w-3xl"
              style={{ textWrap: "balance" } as React.CSSProperties}
            >
              Operativa el primer día. Sin técnicos.
            </h1>
            <p
              className="mt-6 text-[18px] text-ink-500 leading-relaxed max-w-[640px]"
              style={{ textWrap: "pretty" } as React.CSSProperties}
            >
              Al registrarte, un asistente de activación te guía en 9 pasos dentro del panel,
              botón a botón: voz, número de teléfono, WhatsApp, portales, catálogo y chat web.
              Cada paso con su tiempo real — y si algo depende de un tercero (como la
              verificación de Meta para WhatsApp), te lo decimos claro.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <SignupCTA origen="instalacion_hero" variant="primary" size="lg">
                Empezar gratis
                <IArrow size={16} />
              </SignupCTA>
              <Btn variant="ghost" size="lg" href="/#precios">
                Ver precios
              </Btn>
            </div>
          </Reveal>
        </Container>
      </Section>

      {/* Pasos */}
      <Section className="py-16 md:py-24">
        <Container>
          <div className="max-w-3xl mb-12">
            <Reveal>
              <h2
                className="font-display font-bold text-[36px] md:text-[44px] leading-tight text-ink-900"
                style={{ textWrap: "balance" } as React.CSSProperties}
              >
                Los 9 pasos, uno a uno.
              </h2>
              <p className="mt-4 text-[16px] text-ink-500 leading-relaxed">
                Son los mismos que verás en tu panel al entrar por primera vez, con el tiempo
                real que lleva cada uno. Tu parte suma más o menos una hora y ningún paso tiene
                coste: solo pagas tu plan (desde 99 €/mes, tras 14 días de prueba
                gratis). La IA queda operativa el primer día; WhatsApp al 100% cuando Meta
                verifique tu empresa (1-2 días).
              </p>
            </Reveal>
          </div>

          <div className="space-y-3">
            {STEPS.map((s, i) => (
              <Reveal key={s.n} delay={i * 0.04}>
                <div className="group bg-white rounded-3xl ring-1 ring-ink-900/5 shadow-card p-7 md:p-8 grid md:grid-cols-[80px_1fr_auto] gap-6 md:gap-8 items-start hover:-translate-y-0.5 transition-transform duration-300">
                  <div>
                    <div className="font-display font-bold text-[40px] leading-none text-selai-500">
                      {s.n}
                    </div>
                  </div>
                  <div>
                    <h3 className="font-display font-bold text-[22px] leading-tight text-ink-900 mb-2">
                      {s.title}
                    </h3>
                    <div className="text-[15px] text-ink-500 leading-relaxed">{s.body}</div>
                  </div>
                  <div className="md:text-right space-y-3">
                    <div>
                      <div className="text-[11px] uppercase tracking-wider text-ink-500 font-mono">
                        tiempo
                      </div>
                      <div className="font-mono text-[14px] text-ink-700 mt-1">{s.time}</div>
                    </div>
                    <div>
                      <div className="text-[11px] uppercase tracking-wider text-ink-500 font-mono">
                        coste
                      </div>
                      <div className="font-mono text-[14px] text-ink-700 mt-1">0 €</div>
                    </div>
                  </div>
                </div>
              </Reveal>
            ))}
          </div>
        </Container>
      </Section>

      {/* Qué tienes al acabar */}
      <Section className="py-16 md:py-24 bg-paper">
        <Container>
          <Reveal>
            <div className="bg-ink-900 grain rounded-3xl p-8 md:p-12 text-selai-50 relative overflow-hidden">
              <div className="absolute -top-20 right-0 w-72 h-72 rounded-full bg-selai-500/30 blur-3xl pointer-events-none" />
              <div className="relative">
                <Pill tone="dark" className="mb-5">
                  Al acabar los 9 pasos
                </Pill>
                <h2
                  className="font-display font-bold text-[32px] md:text-[40px] leading-tight text-selai-50 max-w-2xl"
                  style={{ textWrap: "balance" } as React.CSSProperties}
                >
                  Esto es lo que tienes funcionando.
                </h2>
                <ul className="mt-8 grid md:grid-cols-2 gap-x-8 gap-y-3 text-[14px]">
                  {[
                    "Un asistente con la voz que has elegido (o la tuya clonada, con el plan Agencia)",
                    "Un número propio para tu IA — y tus llamadas desviadas a él si quieres",
                    "Los leads de Idealista, Fotocasa y Habitaclia entrando al instante",
                    "Tu catálogo completo cargado: la IA habla de tus pisos con datos reales",
                    "Un chat en tu web atendido por tu asistente las 24 horas",
                    "Cada conversación resumida en tu bandeja, con el lead cualificado",
                    "Tus leads exportables a CSV y avisos automáticos hacia tu CRM",
                    "WhatsApp conectado — al 100% en cuanto Meta verifique tu empresa (1-2 días)",
                  ].map((t) => (
                    <li key={t} className="flex items-start gap-2.5 text-selai-100/85">
                      <div className="w-5 h-5 rounded-full bg-selai-500/30 flex items-center justify-center flex-shrink-0 mt-0.5">
                        <ICheck size={11} className="text-selai-200" />
                      </div>
                      {t}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </Reveal>
        </Container>
      </Section>

      {/* FAQ */}
      <Section className="py-16 md:py-24">
        <Container>
          <div className="max-w-3xl">
            <Reveal>
              <Pill tone="selai" className="mb-5">
                Preguntas frecuentes
              </Pill>
              <h2
                className="font-display font-bold text-[36px] md:text-[44px] leading-tight text-ink-900"
                style={{ textWrap: "balance" } as React.CSSProperties}
              >
                Las dudas típicas antes de empezar.
              </h2>
            </Reveal>
            <div className="mt-10 space-y-3">
              {FAQ.map((f, i) => (
                <Reveal key={f.q} delay={i * 0.04}>
                  <div className="bg-white rounded-2xl ring-1 ring-ink-900/5 shadow-card p-6">
                    <h3 className="font-display font-bold text-[18px] text-ink-900">{f.q}</h3>
                    <p className="mt-2 text-[15px] text-ink-500 leading-relaxed">{f.a}</p>
                  </div>
                </Reveal>
              ))}
            </div>
          </div>
        </Container>
      </Section>

      {/* CTA final */}
      <Section className="py-20 md:py-28">
        <Container>
          <Reveal>
            <div className="text-center max-w-2xl mx-auto">
              <h2
                className="font-display font-bold text-[36px] md:text-[48px] leading-tight text-ink-900"
                style={{ textWrap: "balance" } as React.CSSProperties}
              >
                Empieza ahora o pídenos 20 minutos.
              </h2>
              <p className="mt-4 text-[16px] text-ink-500 leading-relaxed">
                Puedes hacerlo todo tú desde el panel, o te acompañamos por videollamada paso a
                paso. Sin tarjeta y sin compromiso.
              </p>
              <div className="mt-7 flex flex-wrap gap-3 justify-center">
                <SignupCTA origen="instalacion_final" variant="primary" size="lg">
                  Empezar gratis
                  <IArrow size={16} />
                </SignupCTA>
                <BookCTA
                  href={BOOK_HREF}
                  origen="instalacion_final"
                  tipo={BOOK_TIPO}
                  external={HAS_CALCOM}
                  variant="ghost"
                  size="lg"
                >
                  {BOOK_LABEL}
                </BookCTA>
              </div>
            </div>
          </Reveal>
        </Container>
      </Section>
    </>
  );
}
