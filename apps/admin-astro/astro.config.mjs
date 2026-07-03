// @ts-check
import { defineConfig } from "astro/config";
import node from "@astrojs/node";

export default defineConfig({
  output: "server",
  adapter: node({ mode: "standalone" }),
  server: {
    port: Number(process.env.ADMIN_PORT ?? 4321),
    host: process.env.ADMIN_HOST ?? "0.0.0.0",
  },
  security: {
    // Disable Astro's built-in Origin check. The dashboard is
    // server-rendered with cookie-based auth and same-origin
    // forms; the real CSRF defence is the httpOnly session
    // cookie. Turning the check off here avoids the URL-host
    // normalisation quirk (Astro's middleware would otherwise
    // force the URL to "localhost" when no `allowedDomains` are
    // configured, making every cross-port request look cross-site).
    checkOrigin: false,
  },
  vite: {
    server: {
      // Allow local testing through temporary Cloudflare tunnels.
      allowedHosts: [".trycloudflare.com"],
      // Allow the Astro dev server to talk to the FastAPI backend.
      proxy: {
        "/api": {
          target: process.env.API_PROXY_TARGET ?? "http://localhost:8000",
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/api/, ""),
        },
      },
    },
  },
});
