import { SelaiLogo } from "./SelaiLogo";
import { Container } from "./ui";

const COLUMNS: ReadonlyArray<readonly [string, ReadonlyArray<readonly [string, string]>]> = [
  [
    "Producto",
    [
      ["Cómo funciona", "/#como-funciona"],
      ["Instalación", "/instalacion"],
      ["Canales", "/#por-que-selai"],
      ["Precios", "/precios"],
      ["Punto de equilibrio", "/#calculadora-roi"],
    ],
  ],
  [
    "Empresa",
    [
      ["Sobre nosotros", "#sobre-nosotros"],
      ["Centro de ayuda", "/ayuda"],
      // (15) /estado se sacó del login precisamente para el cliente que no
      // consigue entrar, pero el único enlace vivía en la barra lateral del
      // panel — es decir, detrás del login. Una puerta abierta sin cartel en
      // la calle no la usa nadie: aquí sale en todas las páginas públicas,
      // /ayuda incluida, que es a donde se acude cuando algo se ha roto.
      ["Estado del sistema", "/estado"],
    ],
  ],
  [
    "Legal",
    [
      ["Términos", "/terminos"],
      ["Privacidad", "/privacidad"],
      ["Seguridad", "/seguridad"],
      ["DPA", "mailto:hola@selai.es?subject=Solicitud%20de%20DPA"],
    ],
  ],
  [
    "Contacto",
    [
      ["hola@selai.es", "mailto:hola@selai.es"],
      ["Alicante, España", "/#sobre-nosotros"],
    ],
  ],
];

export function Footer() {
  return (
    <footer className="bg-paper border-t border-ink-900/5 pt-16 pb-10">
      <Container>
        <div className="grid md:grid-cols-[1.4fr_0.8fr_0.8fr_0.8fr_0.8fr] gap-10 mb-12">
          <div>
            <SelaiLogo className="text-[28px]" />
            <p className="mt-4 text-[14px] text-ink-500 leading-relaxed max-w-[280px]">
              El asistente comercial 24/7 para tu inmobiliaria. Hecho en Alicante.
            </p>
          </div>
          {COLUMNS.map(([title, items]) => (
            <div key={title}>
              <div className="text-[12px] uppercase tracking-[0.14em] text-ink-500 font-mono mb-4">
                {title}
              </div>
              <ul className="space-y-2.5">
                {items.map(([label, href]) => (
                  <li key={label}>
                    <a className="text-[14px] text-ink-700 hover:text-ink-900" href={href}>
                      {label}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="pt-6 border-t border-ink-900/5 flex flex-wrap items-center justify-between gap-3 text-[12px] text-ink-500 font-mono">
          <div>© 2026 selai · Hecho con cabeza en Alicante</div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            {/* Badge de confianza (backlog #136) */}
            <a
              href="/seguridad"
              className="inline-flex items-center gap-1.5 rounded-lg bg-selai-50 ring-1 ring-selai-200 px-2.5 py-1 text-selai-700 hover:bg-selai-100 font-sans"
            >
              <span role="img" aria-label="Unión Europea">
                🇪🇺
              </span>
              Datos alojados en la UE · RGPD · No entrenamos modelos de IA con tus datos
            </a>
            <span>v1.0</span>
          </div>
        </div>
      </Container>
    </footer>
  );
}
