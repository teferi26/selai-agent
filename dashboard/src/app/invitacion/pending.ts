/**
 * Memoria de "estoy aceptando una invitación".
 *
 * El problema que resuelve: quien inicia sesión sin agencia acaba en
 * /onboarding, que CREA una agencia. Un comercial invitado que se registra en
 * Clerk y aterriza en cualquier pantalla del panel antes de aceptar caería
 * justo ahí y se montaría su propia agencia vacía — el bug que toda esta
 * funcionalidad viene a arreglar.
 *
 * Al abrir /invitacion/{token} guardamos el token en esta cookie; mientras
 * exista, `getMe` (src/lib/server-api.ts) manda al invitado de vuelta a su
 * invitación en lugar de al alta. Se borra al aceptar.
 *
 * No es HttpOnly a propósito: la escribe el propio componente cliente de la
 * página de invitación, y su contenido es exactamente el token que el usuario
 * ya tiene en la barra de direcciones. No da acceso a nada por sí sola: el
 * backend exige además sesión de Clerk con el email invitado.
 */
export const PENDING_INVITE_COOKIE = "selai_invitacion_pendiente";

/** Una semana: lo que dura la propia invitación. */
const MAX_AGE_SECONDS = 60 * 60 * 24 * 7;

export function rememberPendingInvite(token: string): void {
  if (typeof document === "undefined") return;
  document.cookie = `${PENDING_INVITE_COOKIE}=${encodeURIComponent(
    token
  )}; path=/; max-age=${MAX_AGE_SECONDS}; SameSite=Lax`;
}

export function forgetPendingInvite(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${PENDING_INVITE_COOKIE}=; path=/; max-age=0; SameSite=Lax`;
}

/** El token recordado, o null. Sólo cliente. */
export function readPendingInvite(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie
    .split("; ")
    .find((row) => row.startsWith(`${PENDING_INVITE_COOKIE}=`));
  if (!match) return null;
  const value = decodeURIComponent(match.slice(PENDING_INVITE_COOKIE.length + 1));
  return value || null;
}
