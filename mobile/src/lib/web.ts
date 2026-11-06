// ---------------------------------------------------------------------------
// Las direcciones del panel web.
//
// Viven juntas porque la app manda al navegador para todo lo que no puede
// hacer ella (registrarse, pagar) y la base tiene que ser la misma en los tres
// sitios: si se apunta a un despliegue de pruebas, se apunta entero.
// ---------------------------------------------------------------------------

/** Env var para poder apuntar a un despliegue de pruebas, igual que
 * EXPO_PUBLIC_API_URL con la API. */
export const PANEL_BASE = process.env.EXPO_PUBLIC_PANEL_URL || "https://www.selai.es";

export const PANEL_BILLING_URL = `${PANEL_BASE}/billing`;

/** Alta de cuenta. El registro no se hace desde el móvil: crea la agencia, la
 * suscripción y el tenant, y eso vive en el panel (dashboard, ruta
 * `(auth)/sign-up`). Desde aquí se abre, no se imita. */
export const SIGN_UP_URL = `${PANEL_BASE}/sign-up`;
