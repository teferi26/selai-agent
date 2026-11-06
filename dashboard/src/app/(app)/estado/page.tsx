// "Estado del sistema" — mitad de servidor.
//
// La página es pública desde (15) (proxy.ts: "/estado" y su única
// llamada, "/api/backend/readyz"). Todo lo que se pinta vive en EstadoClient,
// que consulta /readyz desde el navegador; aquí sólo se decide UNA cosa: si
// quien mira tiene sesión.
//
// Por qué importa: el cuerpo de /readyz (backend/app/api/health.py) no trae
// ni un dato de ninguna agencia, pero sus tres últimos campos
// (`meta_configured`, `vapi_configured`, `stripe_configured`) sí dicen qué
// credenciales tiene puestas la plataforma. Eso estaba detrás del login y
// tiene que seguir estándolo: a un visitante le sirve saber si el servicio
// responde, no de qué integraciones andamos cojos.
import EstadoClient from "./EstadoClient";

export const metadata = { title: "Estado del sistema" };

const clerkEnabled = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

/** ¿Hay sesión de Clerk?
 *
 * `auth()` funciona también en rutas públicas (el proxy corre para
 * todas), y devuelve `userId: null` cuando no hay sesión. Ante cualquier
 * fallo, la respuesta es "no": esconder de más cuesta un bloque informativo;
 * enseñar de más no se puede deshacer.
 */
async function hasSession(): Promise<boolean> {
  // Desarrollo sin Clerk: no hay sesión que comprobar y el panel entero es
  // local, así que se ve todo (el comportamiento de antes de (15)).
  if (!clerkEnabled) return true;
  try {
    const { auth } = await import("@clerk/nextjs/server");
    const { userId } = await auth();
    return !!userId;
  } catch {
    return false;
  }
}

export default async function EstadoPage() {
  return <EstadoClient showPlatformConfig={await hasSession()} />;
}
