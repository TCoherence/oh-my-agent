import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { TanStackRouterVite } from "@tanstack/router-plugin/vite";
import path from "node:path";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    // File-based routing — generates routeTree.gen.ts at dev/build time
    // by scanning src/app/**. This runs BEFORE the React plugin so the
    // generated route tree is available when components compile.
    TanStackRouterVite({ routesDirectory: "src/app", generatedRouteTree: "src/routeTree.gen.ts" }),
    react(),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  // Dev-only API proxy so `pnpm dev` against http://localhost:5173 forwards
  // /api/* and /healthz to the FastAPI backend at :8080. Production
  // bundle is served from the same FastAPI app under /app/, so no
  // cross-origin in prod.
  server: {
    proxy: {
      "/api": "http://localhost:8080",
      "/healthz": "http://localhost:8080",
    },
  },
  // Build into the Python package's web_dist/ folder so setuptools
  // package_data ships it with the wheel. Path resolved relative to
  // dashboard-web/.
  build: {
    outDir: "../src/oh_my_agent/dashboard/web_dist",
    emptyOutDir: true,
    // Subpath asset prefix: the SPA is mounted at /app/ on the FastAPI
    // app. Vite needs to know so it emits asset URLs like /app/assets/x.js
    // instead of /assets/x.js (which would 404).
  },
  base: "/app/",
});
