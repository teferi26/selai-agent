// Códigos de desvío de llamadas por operador (backlog #69). Son los códigos
// GSM estándar — funcionan igual en todas las redes españolas; se listan por
// operador para dar confianza, no porque cambien. No inventamos menús de
// apps ni números de atención al cliente.

export const GSM_CODES = {
  forwardAll: "**21*NÚMERO#",
  forwardUnanswered: "**61*NÚMERO#",
  cancelAll: "##21#",
  cancelUnanswered: "##61#",
} as const;

export const OPERATORS: ReadonlyArray<string> = [
  "Movistar",
  "Vodafone",
  "Orange",
  "Yoigo",
  "MásMóvil",
  "Digi",
];

export const OPERATOR_NOTE =
  "Estos códigos son el estándar GSM: se marcan igual en cualquier operador español. Si alguno no te funciona (algunas tarifas lo restringen), llama gratis a la atención al cliente de tu operador y te activan el desvío al momento, sin coste.";
