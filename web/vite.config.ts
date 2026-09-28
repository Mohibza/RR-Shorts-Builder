import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built once and shipped with the app (web/dist); the Python server serves it on 127.0.0.1.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "dist", assetsDir: "assets", sourcemap: false, chunkSizeWarningLimit: 900 },
  server: { proxy: { "/api": "http://127.0.0.1:8765", "/media": "http://127.0.0.1:8765", "/fonts": "http://127.0.0.1:8765" } },
});
