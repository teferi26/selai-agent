"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { SignupCTA } from "./analytics";
import { IArrow, IMenu, IX } from "./icons";
import { SelaiLogo } from "./SelaiLogo";
import { Btn, Container } from "./ui";

const LINKS: ReadonlyArray<readonly [string, string]> = [
  ["Producto", "/#por-que-selai"],
  ["Cómo funciona", "/#como-funciona"],
  ["Cómo se instala", "/instalacion"],
  ["Precios", "/precios"],
  ["FAQ", "/#faq"],
];

export function Header() {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        requestAnimationFrame(() => menuButtonRef.current?.focus());
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  return (
    <header
      className={`fixed top-0 inset-x-0 z-50 transition-all duration-300 ${
        scrolled ? "glass bg-paper/70 border-b border-ink-900/5" : "bg-transparent"
      }`}
    >
      <Container className="flex items-center justify-between h-16 md:h-[72px]">
        <Link href="/" className="flex min-h-11 items-center gap-2" aria-label="selai inicio">
          <SelaiLogo className="text-[26px]" />
        </Link>
        <nav className="hidden md:flex items-center gap-1">
          {LINKS.map(([label, href]) => (
            <Link
              key={href}
              href={href}
              className="px-3 py-2 text-[14px] text-ink-700 hover:text-ink-900 rounded-lg hover:bg-ink-900/5 transition"
            >
              {label}
            </Link>
          ))}
        </nav>
        <div className="hidden md:flex items-center gap-2">
          <Btn variant="ghost" size="sm" href="/inbox">
            Iniciar sesión
          </Btn>
          <SignupCTA origen="header" variant="primary" size="sm">
            Empieza gratis
            <IArrow size={14} />
          </SignupCTA>
        </div>
        <button
          ref={menuButtonRef}
          type="button"
          onClick={() => setOpen(!open)}
          className="md:hidden min-h-11 min-w-11 p-2 -mr-2 text-ink-900 flex items-center justify-center"
          aria-label={open ? "Cerrar menú" : "Abrir menú"}
          aria-expanded={open}
          aria-controls={open ? "mobile-navigation" : undefined}
        >
          {open ? <IX size={22} /> : <IMenu size={22} />}
        </button>
      </Container>
      {open && (
        <div
          id="mobile-navigation"
          className="md:hidden glass bg-paper/95 border-t border-ink-900/5"
        >
          <Container className="py-4 flex flex-col gap-1">
            {LINKS.map(([label, href]) => (
              <Link
                key={href}
                href={href}
                onClick={() => setOpen(false)}
                className="px-3 py-3 text-[15px] text-ink-700 rounded-lg hover:bg-ink-900/5"
              >
                {label}
              </Link>
            ))}
            <div className="flex gap-2 pt-3 border-t border-ink-900/5 mt-2">
              <Btn variant="ghost" size="sm" className="flex-1" href="/inbox">
                Iniciar sesión
              </Btn>
              <SignupCTA origen="header_movil" variant="primary" size="sm" className="flex-1">
                Empieza gratis
              </SignupCTA>
            </div>
          </Container>
        </div>
      )}
    </header>
  );
}
