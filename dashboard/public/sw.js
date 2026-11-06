// Cache only Next.js assets whose content hash makes them immutable.
// Application HTML, API responses, the manifest and icons always come from
// the network so deploys and branding changes cannot get stuck indefinitely.
const CACHE_PREFIX = "selai-";
const CACHE = `${CACHE_PREFIX}next-static-v2`;
const LEGACY_CACHES = new Set(["realtor-v1"]);

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      const staleShellCaches = keys.filter((key) => LEGACY_CACHES.has(key));
      await Promise.all(
        keys
          .filter(
            (key) =>
              key !== CACHE && (key.startsWith(CACHE_PREFIX) || LEGACY_CACHES.has(key))
          )
          .map((key) => caches.delete(key))
      );
      await self.clients.claim();

      // The original worker cached `/` and `/inbox` forever. On its first
      // visit after a deploy it can therefore render old HTML whose hashed
      // CSS/JS files no longer exist. Once that cache has been removed, reload
      // only those legacy-controlled windows so users recover automatically
      // instead of seeing an unstyled page until they refresh by hand.
      if (staleShellCaches.length > 0) {
        const windows = await self.clients.matchAll({ type: "window" });
        const staleShellPaths = new Set(["/", "/inbox"]);
        await Promise.allSettled(
          windows
            .filter((client) => staleShellPaths.has(new URL(client.url).pathname))
            .map((client) => client.navigate(client.url))
        );
      }
    })()
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Never cache navigations or backend responses. This prevents stale deploys
  // from referring to deleted Next.js chunks and avoids retaining leads,
  // conversations or billing data after sign-out.
  if (request.mode === "navigate" || url.pathname.startsWith("/api/")) return;

  const isImmutableNextAsset = url.pathname.startsWith("/_next/static/");
  if (!isImmutableNextAsset) return;

  event.respondWith(
    caches.match(request).then(async (cached) => {
      if (cached) return cached;

      const response = await fetch(request);
      if (response.ok && (response.type === "basic" || response.type === "cors")) {
        const cache = await caches.open(CACHE);
        await cache.put(request, response.clone());
      }
      return response;
    })
  );
});
