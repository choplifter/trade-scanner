import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    // Vite 6+ refuses any Host header other than localhost (and IPs) with
    // "Blocked request. This host is not allowed." Opening the dashboard as
    // http://beewin:5173 -- this machine's hostname -- needs it listed.
    // A list rather than `true`: `true` accepts any Host, which is what a
    // DNS-rebinding attack needs, and a LAN dev server has no reason to.
    allowedHosts: ["beewin"],
    proxy: {
      "/api": "http://localhost:8000",
      "/analytics": "http://localhost:8000",
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
      },
    },
  },
});
