import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import path from "node:path";

export default defineConfig({
  plugins: [
    react(),
    // Workbox-backed PWA: precaches the bundled app shell, runtime-caches
    // GET /api/* with NetworkFirst (so a poor warehouse Wi-Fi still shows
    // the last-loaded list), and explicitly opts the SSE endpoint and the
    // MSAL redirect URLs out so they aren't ever served from cache.
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: [
        "favicon.svg",
        "favicon.ico",
        "apple-touch-icon-180x180.png",
      ],
      manifest: {
        name: "Warehouse Box Tracker",
        short_name: "Boxes",
        description:
          "Track box inventory, status, and alerts across your warehouses.",
        theme_color: "#1d4ed8",
        background_color: "#f8fafc",
        display: "standalone",
        orientation: "portrait",
        start_url: "/",
        scope: "/",
        icons: [
          {
            src: "pwa-64x64.png",
            sizes: "64x64",
            type: "image/png",
          },
          {
            src: "pwa-192x192.png",
            sizes: "192x192",
            type: "image/png",
          },
          {
            src: "pwa-512x512.png",
            sizes: "512x512",
            type: "image/png",
          },
          {
            src: "maskable-icon-512x512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      workbox: {
        // Precache the bundle. The app shell is small enough that the
        // default 2 MB limit is plenty.
        globPatterns: ["**/*.{js,css,html,ico,png,svg,woff,woff2}"],
        // Long-poll SSE must never be served from cache; intercepting
        // it here would break live updates and pin the SW worker.
        navigateFallback: "index.html",
        navigateFallbackDenylist: [
          /^\/api\//,
          /\/api\/stream/,
          // MSAL drops users back at "/?code=..." or "/?error=...";
          // those should hit the network so MSAL can parse the response.
          /[?&](code|state|error)=/,
        ],
        runtimeCaching: [
          {
            // Long-poll stream: never cache. Without this Workbox would
            // try to range-fetch a request that never completes.
            urlPattern: ({ url }) => url.pathname.startsWith("/api/stream"),
            handler: "NetworkOnly",
          },
          {
            // Read APIs: NetworkFirst keeps the SPA snappy when online and
            // gives us a last-known-good payload offline. Mutations are
            // explicitly excluded -- writes always need the network.
            urlPattern: ({ url, request }) =>
              url.pathname.startsWith("/api/") && request.method === "GET",
            handler: "NetworkFirst",
            options: {
              cacheName: "warehouse-api-get",
              networkTimeoutSeconds: 3,
              expiration: {
                maxEntries: 100,
                maxAgeSeconds: 60 * 60 * 24, // 1 day
              },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
        ],
      },
      devOptions: {
        // Disabled in dev so Vite's HMR isn't competing with a SW that
        // serves stale chunks. Enable temporarily when debugging the SW.
        enabled: false,
      },
    }),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
