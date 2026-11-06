// Bloque final de conversión (backlog #158).
//
// Antes había aquí un calendario decorativo: un mes fijo con días y horas
// clicables que no hacían absolutamente nada. Ya no. Ahora:
//   · con NEXT_PUBLIC_CALCOM_LINK  → botón que abre la agenda real, sin
//     calendario de atrezzo;
//   · sin agenda configurada       → tarjeta honesta con qué pasa en esos
//     20 minutos + email con asunto prellenado.
// Nada clicable que no haga nada.
import { BookCTA, SignupCTA } from "./analytics";
import { BOOK_HREF, BOOK_LABEL, BOOK_TIPO, HAS_CALCOM, SALES_EMAIL } from "./booking";
import { IArrow, ICheck, IClock } from "./icons";
import { Container, Pill, Section } from "./ui";

const AGENDA: ReadonlyArray<string> = [
  "Te enseñamos el panel por dentro, con datos de una agencia de ejemplo.",
  "Te contamos qué hace la IA con tus leads y qué no hace todavía.",
  "Miramos juntos si tus portales y tu número encajan, y qué te costaría.",
];

export function CTABook() {
  return (
    <Section
      id="cta-book"
      className="py-24 md:py-32 bg-ink-900 text-selai-50 grain relative overflow-hidden"
    >
      <div className="absolute -top-40 left-1/2 -translate-x-1/2 w-[700px] h-[700px] rounded-full bg-selai-500/30 blur-3xl pointer-events-none" />
      <div className="absolute bottom-0 right-0 w-[400px] h-[400px] rounded-full bg-accent/20 blur-3xl pointer-events-none" />
      <Container className="relative">
        <div className="grid lg:grid-cols-[1.1fr_0.9fr] gap-10 items-center">
          <div>
            <Pill tone="dark" className="mb-5">
              <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
              20 minutos
            </Pill>
            <h2
              className="font-display font-bold text-[44px] md:text-[64px] leading-[1.02] text-selai-50"
              style={{ textWrap: "balance" } as React.CSSProperties}
            >
              20 minutos para ver si selai vende por ti.
            </h2>
            <p
              className="mt-5 text-[17px] text-selai-100/70 leading-relaxed max-w-[560px]"
              style={{ textWrap: "pretty" } as React.CSSProperties}
            >
              Sin guion, sin presión, sin &ldquo;vamos a explorar sinergias&rdquo;. Te enseñamos
              selai con datos de ejemplo y respondemos lo que sea.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <BookCTA
                href={BOOK_HREF}
                origen="cta_book"
                tipo={BOOK_TIPO}
                external={HAS_CALCOM}
                variant="primary"
                size="lg"
              >
                {BOOK_LABEL}
                <IArrow size={16} />
              </BookCTA>
              <SignupCTA origen="cta_book" variant="ghostDark" size="lg">
                O empieza gratis ahora
              </SignupCTA>
            </div>
            <p className="mt-5 text-[13px] text-selai-100/55">
              ¿Prefieres escribir sin más? {SALES_EMAIL} — contestamos personas, en horario
              español.
            </p>
          </div>

          {/* Tarjeta honesta: qué pasa de verdad en esos 20 minutos. */}
          <div className="bg-ink-800/60 rounded-3xl p-7 ring-1 ring-white/10 backdrop-blur">
            <div className="flex items-center gap-2 text-[12px] text-selai-100/70 font-mono uppercase tracking-wider">
              <IClock size={14} />
              20 min · videollamada
            </div>
            <h3 className="mt-4 font-display font-bold text-[24px] leading-tight text-selai-50">
              Te enseñamos selai en 20 minutos.
            </h3>
            <ul className="mt-5 space-y-3.5">
              {AGENDA.map((item) => (
                <li key={item} className="flex items-start gap-3 text-[14px] text-selai-100/85">
                  <span className="mt-0.5 w-4 h-4 rounded-full bg-selai-500/30 text-selai-100 flex items-center justify-center flex-shrink-0">
                    <ICheck size={11} />
                  </span>
                  {item}
                </li>
              ))}
            </ul>
            <div className="mt-6 pt-5 border-t border-white/10">
              <BookCTA
                href={BOOK_HREF}
                origen="cta_book_tarjeta"
                tipo={BOOK_TIPO}
                external={HAS_CALCOM}
                variant="ghostDark"
                size="md"
                className="w-full"
              >
                {BOOK_LABEL}
                <IArrow size={14} />
              </BookCTA>
              <p className="mt-3 text-[12px] text-selai-100/55 text-center">
                {HAS_CALCOM
                  ? "Eliges hueco tú mismo. Sin llamadas de seguimiento."
                  : "Nos dices dos huecos que te vengan bien y te confirmamos el mismo día."}
              </p>
            </div>
          </div>
        </div>
      </Container>
    </Section>
  );
}
