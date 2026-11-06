// 404 global en español (backlog #146). Vive fuera de (marketing) para
// cubrir cualquier ruta inexistente, así que trae su propia mini-cabecera en
// lugar del Header/Footer del layout de marketing.
import type { Metadata } from "next";
import Link from "next/link";

import { SelaiLogo } from "@/components/marketing/SelaiLogo";

export const metadata: Metadata = {
  title: "Página no encontrada",
  robots: { index: false },
};

const LINKS: ReadonlyArray<readonly [string, string, string]> = [
  ["Ir a la portada", "/", "Qué es selai y qué hace por tu inmobiliaria"],
  ["Ver precios", "/precios", "Desde 99 €/mes, sin permanencia"],
  ["Cómo se instala", "/instalacion", "Tu IA atendiendo llamadas en ~20 minutos"],
];

export default function NotFound() {
  return (
    <main className="min-h-screen bg-paper text-ink-900 flex items-center justify-center px-6 py-16">
      <div className="max-w-[560px] w-full text-center">
        <div className="flex justify-center mb-8">
          <Link href="/" aria-label="selai inicio">
            <SelaiLogo className="text-[30px]" />
          </Link>
        </div>
        <div className="font-display font-bold text-[96px] md:text-[120px] leading-none text-selai-500">
          404
        </div>
        <h1 className="mt-4 font-display font-bold text-[28px] md:text-[34px] leading-tight text-ink-900">
          Esta página no existe (o se ha mudado).
        </h1>
        <p className="mt-4 text-[16px] text-ink-500 leading-relaxed">
          Como un piso que ya se ha vendido: el enlace apunta a un sitio donde ya no hay nada.
          Prueba por aquí:
        </p>
        <div className="mt-8 space-y-2 text-left">
          {LINKS.map(([label, href, sub]) => (
            <Link
              key={href}
              href={href}
              className="block rounded-2xl bg-white ring-1 ring-ink-900/5 shadow-card px-5 py-4 hover:ring-selai-300 hover:-translate-y-0.5 transition-all duration-200"
            >
              <span className="block text-[15px] font-semibold text-ink-900">{label} →</span>
              <span className="block mt-0.5 text-[13px] text-ink-500">{sub}</span>
            </Link>
          ))}
        </div>
        <p className="mt-8 text-[14px] text-ink-500">
          ¿Crees que esto es un error nuestro? Escríbenos a{" "}
          <a href="mailto:hola@selai.es" className="text-selai-600 underline">
            hola@selai.es
          </a>{" "}
          y lo miramos.
        </p>
      </div>
    </main>
  );
}
