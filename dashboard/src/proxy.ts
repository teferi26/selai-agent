/**
 * Auth boundary. Everything under the app surface requires a Clerk session;
 * the marketing site, legal docs and auth pages stay public. When Clerk env
 * vars are absent (local dev), the proxy is a no-op and the backend's
 * dev-header mode takes over.
 */
import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

const clerkEnabled = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

const isPublic = createRouteMatcher([
  "/",
  "/instalacion",
  "/privacidad",
  "/terminos",
  "/precios",
  "/seguridad",
  "/ayuda",
  // (15) Estado del sistema. Estaba detrás del login, que es como poner el
  // parte de averías dentro del edificio al que no se puede entrar: justo
  // cuando el cliente no consigue acceder es cuando necesita saber si el
  // problema es nuestro. No enseña ni un dato de ninguna agencia — sólo si
  // Postgres, Redis y las tareas en segundo plano responden, y qué
  // integraciones tiene puestas la plataforma (ver (app)/estado/page.tsx).
  "/estado",
  // Y su única llamada. Sin esto la página pública pediría /readyz, el
  // middleware la mandaría al login, `fetch` seguiría el 307 y recibiría el
  // HTML de /sign-in con estado 200: `res.json()` revienta y la página
  // anunciaría "Incidencia" con todo el sistema en orden. El proxy sólo deja
  // pasar sin sesión esta ruta concreta (ver api/backend/[...path]/route.ts).
  "/api/backend/readyz",
  "/sign-in(.*)",
  "/sign-up(.*)",
  // Public web chat page opened by the website widget (public/widget.js).
  "/chat(.*)",
  // Landing de una invitación de equipo. Quien la abre normalmente NO tiene
  // cuenta todavía: mandarle al login sin decirle de qué va el enlace es la
  // forma más rápida de que lo tome por spam. La página sólo enseña quién
  // invita y a qué agencia; aceptar sigue exigiendo sesión de Clerk con el
  // email invitado (lo comprueba el backend).
  "/invitacion(.*)",
  // El proxy del formulario de soporte, y solo ese. Sin esta excepción, un
  // visitante sin sesión que nos escribe desde la pantalla de error de la web
  // pública recibe el HTML de /sign-in con estado 200: el diálogo no puede
  // distinguirlo de un fallo cualquiera y le da una explicación que no es. Con
  // ella, el propio route handler responde 401 en JSON y el diálogo le dice la
  // verdad y le ofrece el correo. No abre nada más: sin token el proxy corta
  // antes de reenviar, y el backend exige sesión igualmente.
  "/api/backend/api/v1/support",
  "/sitemap.xml",
  "/robots.txt",
  "/opengraph-image(.*)",
  "/manifest.webmanifest",
  "/sw.js",
]);

function responseWithSecurityHeaders(req: { nextUrl: { pathname: string } }) {
  const response = NextResponse.next();
  const isEmbeddableChat = req.nextUrl.pathname.startsWith("/chat/");

  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set(
    "Strict-Transport-Security",
    "max-age=63072000; includeSubDomains",
  );
  response.headers.set("X-Permitted-Cross-Domain-Policies", "none");
  response.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  response.headers.set(
    "Permissions-Policy",
    "camera=(), geolocation=(), microphone=(), payment=(), usb=(), browsing-topics=()",
  );
  response.headers.set("Cross-Origin-Opener-Policy", "same-origin-allow-popups");
  response.headers.set(
    "Content-Security-Policy",
    isEmbeddableChat
      ? "frame-ancestors https: http://localhost:* http://127.0.0.1:*; base-uri 'self'; object-src 'none'"
      : "frame-ancestors 'none'; base-uri 'self'; object-src 'none'",
  );
  if (!isEmbeddableChat) response.headers.set("X-Frame-Options", "DENY");

  return response;
}

const withClerk = clerkMiddleware(
  async (auth, req) => {
    if (isPublic(req)) return responseWithSecurityHeaders(req);

    // `auth.protect()` on its own sends a signed-out visitor to the sign-in
    // page *only if it can work out its URL*; when it can't, it answers 404.
    // On the production instance it couldn't, so an agency opening a link to
    // the panel while logged out got "página no encontrada" instead of the
    // login — verified live on /inbox. Naming the destination here removes
    // the guesswork, and `redirect_url` returns them to the page they were
    // actually after once they sign in.
    const signInUrl = new URL("/sign-in", req.url);
    signInUrl.searchParams.set("redirect_url", req.url);
    await auth.protect({ unauthenticatedUrl: signInUrl.toString() });
    return responseWithSecurityHeaders(req);
  },
  { signInUrl: "/sign-in", signUpUrl: "/sign-up" }
);

function passthrough(req: { nextUrl: { pathname: string } }) {
  return responseWithSecurityHeaders(req);
}

export default clerkEnabled ? withClerk : passthrough;

export const config = {
  matcher: [
    // Skip Next internals and static assets
    "/((?!_next|.*\\.(?:ico|png|jpg|jpeg|svg|webp|css|js|woff2?)$).*)",
    "/(api|trpc)(.*)",
  ],
};
