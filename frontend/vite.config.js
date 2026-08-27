import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// The Python package serves the build output. Vite emits into pytest_deck/static
// (hashed assets + index.html), exactly where server.py serves from.
//
// In dev we proxy /api to the running FastAPI backend (127.0.0.1:8765). The SSE
// endpoint (/api/events) must NOT be buffered, so we disable proxy buffering for
// it via configure() — without this the live stream arrives only at stream end.
export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: "../pytest_deck/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
        // Disable response buffering so SSE chunks flush immediately in dev.
        configure: (proxy) => {
          proxy.on("proxyReq", (proxyReq, req) => {
            if (req.url && req.url.startsWith("/api/events")) {
              proxyReq.setHeader("Accept-Encoding", "identity");
            }
          });
        },
      },
    },
  },
});
