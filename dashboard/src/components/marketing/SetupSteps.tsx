// "Operativa el primer día" — the real 9-step activation wizard, compressed
// into a compact numbered strip. Complements HowItWorks (which explains what
// selai does); this one explains how you get it running, with honest times.
import { SignupCTA } from "./analytics";
import { Reveal } from "./animated";
import { Btn, Container, EyebrowH2, Section } from "./ui";
import { IArrow } from "./icons";

const STEPS: ReadonlyArray<{ n: string; time: string; title: string; body: string }> = [
  {
    n: "1",
    time: "2 min",
    title: "Crea tu agencia",
    body: "Te registras sin tarjeta, con 14 días de prueba, y el asistente del panel te guía botón a botón.",
  },
  {
    n: "2",
    time: "2 min + 1 clic",
    title: "Voz y número de tu IA",
    body: "Eliges una voz (o clonas la tuya, con el plan Agencia) y activas su número en un clic — o lo solicitas y lo tienes en <24 h.",
  },
  {
    n: "3",
    time: "15 + 5 min",
    title: "WhatsApp y portales",
    body: "WhatsApp con conexión guiada de Meta (si tu empresa no está verificada, Meta tarda 1-2 días). Los leads de Idealista, Fotocasa y Habitaclia entran al instante reenviando sus avisos.",
  },
  {
    n: "4",
    time: "10 + 2 min",
    title: "Catálogo y chat web",
    body: "Subes tus propiedades con la plantilla CSV incluida y pegas el código del chat en tu web.",
  },
  {
    n: "5",
    time: "listo",
    title: "Pruébala — y a atender",
    body: "Llamada de prueba y en marcha: responde, cualifica y agenda visitas 24/7, también en festivos.",
  },
];

export function SetupSteps() {
  return (
    <Section id="puesta-en-marcha" className="py-24 md:py-32">
      <Container>
        <Reveal>
          <EyebrowH2
            eyebrow="Puesta en marcha"
            title="Operativa el primer día."
            sub="Sin técnicos, sin instalaciones y sin cambiar de número. Un asistente de activación en tu panel te guía paso a paso, botón a botón."
          />
        </Reveal>

        <div className="mt-14 grid gap-4 md:grid-cols-5">
          {STEPS.map((s, i) => (
            <Reveal key={s.n} delay={i * 0.06}>
              <div className="relative h-full rounded-3xl bg-white p-6 ring-1 ring-ink-900/5 shadow-card transition-all duration-300 hover:-translate-y-1 hover:shadow-soft">
                <div className="mb-4 flex items-center justify-between">
                  <div className="flex h-10 w-10 items-center justify-center rounded-full bg-selai-500 font-display text-[18px] font-bold text-white">
                    {s.n}
                  </div>
                  <span className="rounded-md bg-selai-100 px-2 py-1 font-mono text-[11px] text-selai-600">
                    {s.time}
                  </span>
                </div>
                <h3 className="font-display text-[19px] font-bold leading-tight text-ink-900">
                  {s.title}
                </h3>
                <p className="mt-2 text-[14px] leading-relaxed text-ink-500">{s.body}</p>
              </div>
            </Reveal>
          ))}
        </div>

        <Reveal delay={0.18}>
          <p className="mt-8 text-[14px] text-ink-700">
            <span className="font-mono text-selai-600">Tu parte: ~1 hora, repartida como quieras.</span>{" "}
            Operativa el primer día; WhatsApp al 100% cuando Meta verifique tu empresa (1-2 días).
            Coste de la puesta en marcha: 0 €. Solo pagas tu plan.
          </p>
        </Reveal>

        <Reveal delay={0.2}>
          <div className="mt-6 flex flex-wrap items-center gap-4">
            <SignupCTA origen="puesta_en_marcha" variant="primary" size="lg">
              Empezar gratis
              <IArrow size={16} />
            </SignupCTA>
            <Btn variant="link" href="/instalacion">
              Ver la guía completa de puesta en marcha
            </Btn>
          </div>
        </Reveal>
      </Container>
    </Section>
  );
}
