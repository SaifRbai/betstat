// Service worker de l'application installee.
//
// Le reseau d'abord, toujours : une estimation ou une cote perimee affichee
// comme fraiche serait pire qu'une erreur. Seule la coquille (page, icones)
// et les donnees publiees (donnees/*.json) sont gardees en cache : la version
// en ligne reste lisible hors connexion, avec sa date de calcul affichee. Les
// /api du PC ne sont jamais mises en cache.

const CACHE = "rab3ounstat-v2";
const COQUILLE = ["./", "manifest.webmanifest", "icones/icone-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(COQUILLE)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((cles) => Promise.all(cles.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin
      || url.pathname.includes("/api/")) {
    return;                                   // le navigateur s'en charge seul
  }
  e.respondWith(
    fetch(e.request)
      .then((reponse) => {
        if (reponse.ok) {
          const copie = reponse.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copie));
        }
        return reponse;
      })
      .catch(() => caches.match(e.request))
  );
});
