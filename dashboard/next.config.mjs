// El panel decide si exige sesión con `clerkEnabled = !!NEXT_PUBLIC_CLERK_
// PUBLISHABLE_KEY` (proxy.ts, server-api.ts). Ese respaldo existe para
// desarrollo local sin Clerk — pero en producción convierte un descuido de
// configuración en una catástrofe silenciosa: sin la variable, el proxy
// exporta un passthrough y TODO el panel (bandeja, leads, conversaciones de
// clientes) queda accesible sin iniciar sesión. Nadie vería ningún error.
//
// Un build de producción sin la clave debe morir aquí, en el build, donde el
// fallo es ruidoso y no ha desplegado nada. `VERCEL_ENV` y no `NODE_ENV`:
// los previews de ramas también hacen build de produccion de Next, y los
// previews sí tienen la clave (entorno Preview de Vercel) — pero si algún
// día no la tuvieran, mejor que caigan también a que salgan abiertos.
if (
  (process.env.VERCEL_ENV === "production" || process.env.VERCEL_ENV === "preview") &&
  !process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
) {
  throw new Error(
    "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY no está definida y este es un build " +
      `de Vercel (${process.env.VERCEL_ENV}). Sin ella el panel se construye ` +
      "SIN autenticación. Añádela en Vercel → Settings → Environment Variables."
  );
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async headers() {
    return [
      {
        source: "/sw.js",
        headers: [
          {
            key: "Cache-Control",
            value: "no-cache, no-store, must-revalidate",
          },
        ],
      },
    ];
  },
  // NB: /api/backend/* is a route handler (src/app/api/backend) that injects
  // the Clerk JWT server-side — do not replace it with a rewrite, a rewrite
  // forwards no auth.
  //
  // Sentry: deliberately NOT wrapped in withSentryConfig(). That wrapper only
  // adds source-map upload and the tunnel route, and both need SENTRY_AUTH_TOKEN
  // + org/project at build time; without them it just slows the build down and
  // prints warnings. Error capture itself lives in src/instrumentation.ts and
  // src/instrumentation-client.ts, which work on their own. If someone adds the
  // auth token later, wrapping here is the next step (stack traces get readable).
};

export default nextConfig;
