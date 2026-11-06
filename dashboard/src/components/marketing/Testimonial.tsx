import { Reveal } from "./animated";
import { Container, Section } from "./ui";

export function Testimonial() {
  return (
    <Section className="py-24 md:py-32 bg-gradient-to-b from-paper to-selai-50/40">
      <Container>
        <Reveal>
          <figure className="max-w-4xl mx-auto text-center">
            <div className="font-display text-[80px] md:text-[120px] leading-none text-selai-200 select-none">
              &ldquo;
            </div>
            <blockquote
              className="font-display font-medium text-[28px] md:text-[44px] leading-[1.18] text-ink-900 -mt-10 md:-mt-14"
              style={{ textWrap: "balance" } as React.CSSProperties}
            >
              Que ninguna inmobiliaria vuelva a perder un lead por dormir. Tu mejor agente,
              trabajando a las 3 a.m. — sin quejarse.
            </blockquote>
            <figcaption className="mt-8 flex items-center justify-center gap-3">
              <div className="w-11 h-11 rounded-full bg-gradient-to-br from-selai-300 to-accent ring-2 ring-white shadow-card" />
              <div className="text-left">
                <div className="font-medium text-[14px] text-ink-900">La idea detrás de selai</div>
                <div className="text-[12px] text-ink-500 font-mono">
                  Equipo selai · Alicante
                </div>
              </div>
            </figcaption>
          </figure>
        </Reveal>
      </Container>
    </Section>
  );
}
