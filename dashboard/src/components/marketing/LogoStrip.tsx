import { Container } from "./ui";

// Ticker de capacidades reales del producto. Cuando haya inmobiliarias
// clientes con permiso para publicar su marca, este strip puede volver a ser
// un logo strip clásico — hasta entonces, nada de clientes inventados.
const LOGOS = [
  "WhatsApp Business",
  "Llamadas con voz natural",
  "Idealista",
  "Fotocasa",
  "Habitaclia",
  "Email",
  "Chat en tu web",
  "Catálogo por CSV",
  "Export y avisos a tu CRM",
  "5 idiomas",
  "RGPD · datos en la UE",
  "Visitas agendadas solas",
];

export function LogoStrip() {
  return (
    <section className="py-14 border-y border-ink-900/5 bg-white/40">
      <Container>
        <p className="text-center text-[12px] uppercase tracking-[0.18em] text-ink-500 font-mono mb-7">
          Todos tus canales, un solo asistente
        </p>
        <div className="overflow-hidden mask-fade">
          <div className="flex gap-12 ticker w-max">
            {[...LOGOS, ...LOGOS].map((l, i) => (
              <div
                key={i}
                className="flex-shrink-0 flex items-center gap-2.5 text-ink-500/70 hover:text-ink-700 font-display font-bold text-[20px] tracking-tight whitespace-nowrap transition-colors"
              >
                <div className="w-2.5 h-2.5 rounded-sm bg-ink-300/50" />
                {l}
              </div>
            ))}
          </div>
        </div>
      </Container>
    </section>
  );
}
