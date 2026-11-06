// ---------------------------------------------------------------------------
// Los errores de Clerk, en español.
//
// Clerk devuelve `longMessage` en inglés ("Password is incorrect. Try again,
// or use another method.") y hasta ahora eso era lo que veía un agente
// inmobiliario que no consigue entrar. Aquí se traducen los casos que de
// verdad ocurren en un login y se deja un texto genérico digno para el resto:
// nunca se enseña el inglés crudo.
//
// La clave es `code`, no el texto: Clerk reescribe sus mensajes entre
// versiones, pero los códigos son parte de su contrato público
// (https://clerk.com/docs/errors). Cuando aparezca uno que no conocemos, el
// genérico + la vía de soporte es mejor que una frase en inglés.
// ---------------------------------------------------------------------------
import { isClerkAPIResponseError } from "@clerk/expo";

/** Dónde estaba el usuario, para que el texto genérico no mienta. */
export type ClerkFlow = "signin" | "google" | "reset";

const GENERIC: Record<ClerkFlow, string> = {
  signin: "No hemos podido iniciar sesión. Inténtalo de nuevo en un momento.",
  google: "No hemos podido iniciar sesión con Google. Inténtalo de nuevo en un momento.",
  reset:
    "No hemos podido cambiar tu contraseña. Inténtalo de nuevo en un momento.",
};

const NETWORK =
  "No hemos podido conectar. Comprueba tu conexión (prueba a cambiar entre wifi y datos) e inténtalo otra vez.";

/**
 * Códigos de Clerk → español. Cada texto dice qué ha pasado y qué hacer;
 * ninguno termina en "inténtalo de nuevo" si reintentar no puede funcionar.
 */
const BY_CODE: Record<string, string> = {
  // --- Credenciales ---
  form_password_incorrect:
    "La contraseña no es correcta. Revísala, o entra con Google si creaste la cuenta así.",
  form_identifier_not_found:
    "No hay ninguna cuenta con ese email. Comprueba que lo has escrito bien o regístrate en selai.es.",
  // Sin `meta.paramName` no sabemos qué campo ha rechazado Clerk (ver
  // BY_CODE_AND_PARAM): estos dos textos no nombran ninguno a propósito.
  form_param_format_invalid:
    "Alguno de los datos no tiene el formato correcto. Revísalos y vuelve a intentarlo.",
  form_param_nil: "Falta algún dato por rellenar.",
  form_identifier_exists:
    "Ya existe una cuenta con ese email. Inicia sesión en lugar de registrarte.",
  // La cuenta existe pero no tiene contraseña (se creó con Google).
  strategy_for_user_invalid:
    "Esta cuenta no usa contraseña. Entra con «Continuar con Google».",
  form_password_not_set:
    "Esta cuenta todavía no tiene contraseña. Entra con Google, o usa «He olvidado mi contraseña» para crear una.",

  // --- Límite de intentos ---
  too_many_requests:
    "Has hecho demasiados intentos seguidos. Espera unos minutos antes de volver a probar.",
  rate_limit_exceeded:
    "Has hecho demasiados intentos seguidos. Espera unos minutos antes de volver a probar.",

  // --- Código de recuperación por email ---
  form_code_incorrect:
    "Ese código no es correcto. Cópialo tal cual del correo que te hemos enviado.",
  verification_expired:
    "El código ha caducado. Pide uno nuevo y úsalo en los próximos minutos.",
  verification_failed:
    "No hemos podido verificar el código. Pide uno nuevo e inténtalo otra vez.",
  verification_already_verified:
    "Ese código ya se ha usado. Pide uno nuevo si necesitas volver a empezar.",

  // --- Contraseña nueva ---
  form_password_length_too_short:
    "La contraseña es demasiado corta. Usa al menos 8 caracteres.",
  form_password_pwned:
    "Esa contraseña aparece en filtraciones conocidas y no es segura. Elige otra distinta.",
  form_password_validation_failed:
    "Esa contraseña no cumple los requisitos de seguridad. Prueba con una más larga, mezclando letras y números.",
  form_password_size_in_bytes_exceeded:
    "Esa contraseña es demasiado larga. Usa una más corta.",

  // --- Estado de sesión ---
  session_exists:
    "Ya tienes la sesión abierta en este teléfono. Cierra la app y vuelve a abrirla.",
  client_state_invalid:
    "El intento anterior se ha quedado a medias. Cierra la app, vuelve a abrirla y empieza de nuevo.",

  // --- Anti-abuso ---
  captcha_invalid:
    "No hemos podido comprobar que eres una persona. Inténtalo de nuevo; si vuelve a fallar, entra desde selai.es en el ordenador.",
  captcha_unavailable:
    "No hemos podido completar la comprobación de seguridad. Inténtalo de nuevo; si vuelve a fallar, entra desde selai.es en el ordenador.",
};

/**
 * True cuando el fallo es de red y no de credenciales. React Native lanza
 * `TypeError: Network request failed`; Clerk envuelve algunos en
 * ClerkRuntimeError. En los dos casos el consejo es el mismo y NO es
 * "revisa tu contraseña", que es lo que decía el genérico anterior.
 */
function isNetworkFailure(err: unknown): boolean {
  const raw = err instanceof Error ? `${err.name}: ${err.message}` : String(err ?? "");
  return (
    /network request failed/i.test(raw) ||
    /failed to fetch/i.test(raw) ||
    /network error/i.test(raw) ||
    /timeout/i.test(raw)
  );
}

/**
 * Texto en español para enseñar al usuario. Nunca devuelve cadena vacía y
 * nunca devuelve el mensaje original de Clerk.
 */
export function explainClerkError(err: unknown, flow: ClerkFlow): string {
  if (isClerkAPIResponseError(err)) {
    for (const e of err.errors ?? []) {
      const known = BY_CODE[e.code];
      if (known) return known;
    }
    // Un 429 puede llegar sin código reconocible: el estado ya lo dice todo.
    if (err.status === 429) return BY_CODE.too_many_requests;
    return GENERIC[flow];
  }
  // El orden importa: un ClerkAPIResponseError con "network" en el texto sigue
  // siendo un error de la API, no de la conexión.
  if (isNetworkFailure(err)) return NETWORK;
  return GENERIC[flow];
}
