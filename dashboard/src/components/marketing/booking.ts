// Reserva de demo — fuente única de verdad (backlog #158).
//
// Honestidad radical: si no hay agenda pública configurada
// (NEXT_PUBLIC_CALCOM_LINK) NO se pinta un calendario decorativo ni se deja
// un ancla muerta tipo "#cta-book": el botón abre un email con el asunto ya
// escrito, que sí llega a una persona.

// Solo aceptamos una URL absoluta http(s). Si alguien deja la variable a
// medias ("cal.com/selai") el navegador la resolvería como ruta relativa y el
// botón llevaría a una página inexistente: preferimos caer al email.
function readCalcomLink(raw: string | undefined): string | null {
  const value = raw?.trim();
  if (!value) return null;
  return /^https?:\/\/\S+$/i.test(value) ? value : null;
}

const CALCOM_LINK = readCalcomLink(process.env.NEXT_PUBLIC_CALCOM_LINK);

/** ¿Hay agenda pública real donde el visitante elige hueco él solo? */
export const HAS_CALCOM = CALCOM_LINK !== null;

export const SALES_EMAIL = "hola@selai.es";

const DEMO_SUBJECT = "Quiero ver selai en 20 minutos";
const DEMO_BODY = [
  "Hola:",
  "",
  "Somos [nombre de la inmobiliaria] y nos gustaría ver selai.",
  "Nos vienen bien estos huecos: [dinos dos]",
  "Teléfono de contacto: [tu número]",
  "",
  "Gracias.",
].join("\n");

/** mailto con asunto y cuerpo prellenados. */
export const DEMO_MAILTO = `mailto:${SALES_EMAIL}?subject=${encodeURIComponent(
  DEMO_SUBJECT,
)}&body=${encodeURIComponent(DEMO_BODY)}`;

/** Destino real de todo botón de "reservar demo" en la web. */
export const BOOK_HREF: string = CALCOM_LINK ?? DEMO_MAILTO;

/** Para etiquetar el evento de analítica y decidir si abre en pestaña nueva. */
export const BOOK_TIPO: "calcom" | "email" = HAS_CALCOM ? "calcom" : "email";

/** Texto del botón: no prometemos "reservar" si no hay agenda que reservar. */
export const BOOK_LABEL = HAS_CALCOM ? "Reservar demo" : "Pedir demo por email";
