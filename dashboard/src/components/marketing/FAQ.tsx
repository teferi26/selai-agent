"use client";

import { useState } from "react";

import { BookCTA } from "./analytics";
import { Reveal } from "./animated";
import { BOOK_HREF, BOOK_LABEL, BOOK_TIPO, HAS_CALCOM } from "./booking";
import { FAQ_ITEMS } from "./faq-items";
import { IArrow, IPlus } from "./icons";
import { Container, Pill, Section } from "./ui";

export function FAQ() {
  const [open, setOpen] = useState<number>(0);
  return (
    <Section id="faq" className="py-24 md:py-32 bg-paper">
      <Container>
        <div className="grid lg:grid-cols-[0.9fr_1.1fr] gap-12 items-start">
          {/* Sticky on desktop so the title + CTA stay visible while you
              scroll through the questions. Eliminates the dead whitespace
              that the previous layout produced under "Reservar demo". */}
          <Reveal className="lg:sticky lg:top-24">
            <Pill tone="selai" className="mb-5">
              FAQ
            </Pill>
            <h2
              className="font-display font-bold text-[40px] md:text-[52px] leading-[1.05] text-ink-900"
              style={{ textWrap: "balance" } as React.CSSProperties}
            >
              Las dudas razonables.
            </h2>
            <p className="mt-5 text-[16px] text-ink-500 leading-relaxed">
              Si te falta alguna, escribe a{" "}
              <a
                href="mailto:hola@selai.es"
                className="text-selai-600 underline decoration-dotted"
              >
                hola@selai.es
              </a>{" "}
              o pídenos una demo. Sin guion, sin presión.
            </p>
            <div className="mt-8 hidden lg:block">
              <BookCTA
                href={BOOK_HREF}
                origen="faq"
                tipo={BOOK_TIPO}
                external={HAS_CALCOM}
                variant="dark"
              >
                {BOOK_LABEL}
                <IArrow size={14} />
              </BookCTA>
            </div>
          </Reveal>
          <Reveal delay={0.05}>
            <div className="space-y-2">
              {FAQ_ITEMS.map(([q, a], i) => {
                const isOpen = open === i;
                return (
                  <div
                    key={i}
                    className={`rounded-2xl ring-1 transition ${
                      isOpen ? "ring-selai-200 bg-white" : "ring-ink-900/5 bg-white/60 hover:bg-white"
                    }`}
                  >
                    <button
                      onClick={() => setOpen(isOpen ? -1 : i)}
                      className="w-full flex items-center justify-between gap-4 px-5 py-4 text-left"
                    >
                      <span className={`text-[15px] font-medium ${isOpen ? "text-ink-900" : "text-ink-700"}`}>
                        {q}
                      </span>
                      <span
                        className={`flex-shrink-0 w-7 h-7 rounded-full flex items-center justify-center transition-colors duration-300 ${
                          isOpen ? "bg-selai-500 text-white" : "bg-ink-900/5 text-ink-700"
                        }`}
                      >
                        <IPlus
                          size={14}
                          className={`transition-transform duration-300 ${
                            isOpen ? "rotate-45" : "rotate-0"
                          }`}
                        />
                      </span>
                    </button>
                    <div
                      className="grid transition-all duration-300"
                      style={{ gridTemplateRows: isOpen ? "1fr" : "0fr" }}
                    >
                      <div className="overflow-hidden">
                        <p className="px-5 pb-5 text-[14px] text-ink-500 leading-relaxed">{a}</p>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </Reveal>
        </div>
      </Container>
    </Section>
  );
}
