// Earlier service workers shared authenticated API responses between accounts.
export async function purgeLegacyApiCaches(): Promise<void> {
  if (typeof caches === "undefined") return;
  try {
    const names = await caches.keys();
    await Promise.all(names.filter((name) => name.startsWith("warehouse-api-get")).map((name) => caches.delete(name)));
  } catch {
    // Private browsing may deny CacheStorage; APIs now always use the network.
  }
}
