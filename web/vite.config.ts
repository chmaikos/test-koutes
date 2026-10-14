import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import path from "node:path";

export default defineConfig({
  plugins: [
    react(),
    // Precache the app shell; authenticated warehouse APIs always need the
    // network so a shared device cannot reuse another account's responses.
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
        importScripts: ["purge-api-caches.js"],
        runtimeCaching: [
          {
            // Warehouse data and identity are authorization-sensitive.
            urlPattern: ({ url }) => url.pathname.startsWith("/api/"),
            handler: "NetworkOnly",
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
