// Remove authenticated responses written by older service workers on upgrade.
self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((names) => Promise.all(
    names.filter((name) => name.startsWith("warehouse-api-get")).map((name) => caches.delete(name)),
  )));
});
