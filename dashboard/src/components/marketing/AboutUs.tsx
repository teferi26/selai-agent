import { Reveal } from "./animated";
import { Container, Pill, Section } from "./ui";

const TEAM = [
  { name: "Cofundador · Producto", role: "Producto y clientes", from: "from-selai-300 to-selai-500" },
  { name: "Cofundador · IA", role: "IA y sistemas", from: "from-accent to-amber-400" },
  { name: "Ingeniero principal", role: "Diseño y web", from: "from-emerald-300 to-selai-400" },
  { name: "Asesor inmobiliario", role: "Experto del sector", from: "from-rose-300 to-accent" },
];

export function AboutUs() {
  return (
    <Section id="sobre-nosotros" className="py-24 md:py-32">
      <Container>
        <div className="grid lg:grid-cols-[0.95fr_1.05fr] gap-12 items-start">
          <Reveal>
            <Pill tone="selai" className="mb-5">
              Sobre nosotros
            </Pill>
            <h2
              className="font-display font-bold text-[40px] md:text-[52px] leading-[1.05] text-ink-900"
              style={{ textWrap: "balance" } as React.CSSProperties}
            >
              Lo construimos porque nadie nos contestaba al WhatsApp.
            </h2>
            <div
              className="mt-6 space-y-4 text-[16px] text-ink-500 leading-[1.65]"
              style={{ textWrap: "pretty" } as React.CSSProperties}
            >
              <p>
                Estábamos buscando piso en Alicante. Cinco inmobiliarias no contestaron al WhatsApp
                en 3 días. Pensamos: si nos pasa a nosotros, le pasa a todos.
              </p>
              <p>
                Construimos selai en la Universidad de Alicante con la convicción de que un agente
                comercial no debería perder leads por dormir.
              </p>
              <p>
                Seguimos en Alicante, construyéndolo cada día con dueños de inmobiliarias que nos
                cuentan lo que falta.
              </p>
            </div>
            <div className="mt-7 flex flex-wrap gap-2 font-mono text-[12px] text-ink-500">
              <span className="px-2.5 py-1 rounded-md bg-ink-900/5">Univ. Alicante</span>
              <span className="px-2.5 py-1 rounded-md bg-ink-900/5">2026 →</span>
              <span className="px-2.5 py-1 rounded-md bg-ink-900/5">Sin inversores</span>
              <span className="px-2.5 py-1 rounded-md bg-ink-900/5">Hecho en Alicante</span>
            </div>
          </Reveal>
          <Reveal delay={0.1}>
            {/* Sin fotos por ahora (no hay fotos reales y los huecos de
                degradado quedaban peor que nada): tarjetas compactas con una
                línea de color como único acento. Cuando haya fotos de verdad,
                recuperar aquí el bloque de imagen. */}
            {/* En pantalla grande van apiladas, no en rejilla 2×2: sin las
                fotos las tarjetas miden un tercio de lo que mide la columna
                de texto, y la rejilla dejaba medio bloque vacío. */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-1 gap-4">
              {TEAM.map((p, i) => (
                <div
                  key={i}
                  className="flex items-center gap-4 bg-white rounded-2xl p-5 ring-1 ring-ink-900/5 shadow-card"
                >
                  <div
                    className={`w-1 self-stretch min-h-[38px] rounded-full bg-gradient-to-b ${p.from} flex-shrink-0`}
                  />
                  <div>
                    <div className="font-medium text-[15px] text-ink-900">{p.name}</div>
                    <div className="text-[12px] text-ink-500 font-mono">{p.role}</div>
                  </div>
                </div>
              ))}
            </div>
          </Reveal>
        </div>
      </Container>
    </Section>
  );
}
