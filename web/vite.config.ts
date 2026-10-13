import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon.svg"],
      manifest: {
        name: "FoodWise",
        short_name: "FoodWise",
        description: "Cook the right amount, hear what students think, and rescue surplus food.",
        theme_color: "#1C2A4A",
        background_color: "#EDF0EE",
        display: "standalone",
        start_url: "/",
        icons: [{ src: "/favicon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" }],
      },
      workbox: {
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api\//, /^\/health/],
        globPatterns: ["**/*.{js,css,html,svg,woff2}"],
        maximumFileSizeToCacheInBytes: 4 * 1024 * 1024,
        runtimeCaching: [
          {
            // The kitchen sheet keeps working on a patchy connection: serve the last copy if offline.
            urlPattern: ({ url }) => url.pathname.startsWith("/api/v1/logs/sheet"),
            handler: "NetworkFirst",
            options: { cacheName: "kitchen-sheet", networkTimeoutSeconds: 4 },
          },
          {
            // Dish pictures are versioned and immutable: keep them for the kitchen screens offline.
            urlPattern: ({ url }) => url.pathname.startsWith("/api/v1/media/"),
            handler: "CacheFirst",
            options: { cacheName: "dish-pictures", expiration: { maxEntries: 300, maxAgeSeconds: 60 * 86400 } },
          },
          {
            urlPattern: ({ url }) => url.hostname.endsWith("tile.openstreetmap.org"),
            handler: "CacheFirst",
            options: { cacheName: "map-tiles", expiration: { maxEntries: 400, maxAgeSeconds: 7 * 86400 } },
          },
        ],
      },
    }),
  ],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
      "/health": { target: "http://127.0.0.1:8000" },
    },
  },
  build: {
    target: "es2022",
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks: {
          charts: ["recharts"],
          maps: ["leaflet", "react-leaflet"],
        },
      },
    },
  },
});
