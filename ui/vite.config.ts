import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

const host = process.env.TAURI_DEV_HOST;
const demoMode = process.env.DEMO === "1";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  resolve: demoMode
    ? {
        alias: {
          "../lib/ipc": path.resolve(__dirname, "src/lib/ipc-demo.ts"),
          "./ipc": path.resolve(__dirname, "src/lib/ipc-demo.ts"),
          "@tauri-apps/api/core": path.resolve(__dirname, "src/lib/tauri-shims.ts"),
          "@tauri-apps/api/event": path.resolve(__dirname, "src/lib/tauri-shims.ts"),
          "@tauri-apps/api/path": path.resolve(__dirname, "src/lib/tauri-shims.ts"),
          "@tauri-apps/plugin-dialog": path.resolve(__dirname, "src/lib/tauri-shims.ts"),
          "@tauri-apps/plugin-notification": path.resolve(__dirname, "src/lib/tauri-shims.ts"),
        },
      }
    : undefined,
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host ? { protocol: "ws", host, port: 1421 } : undefined,
    watch: {
      ignored: ["**/src-tauri/**"],
    },
  },
});
