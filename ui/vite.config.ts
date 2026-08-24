import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

const host = process.env.TAURI_DEV_HOST;
const demoMode = process.env.DEMO === "1";
const webMode = process.env.WEB === "1";

// Determine which IPC module to use:
// - WEB=1: HTTP fetch calls to /api/* (for daemon's HTTP server)
// - DEMO=1: mock data (for screenshots)
// - default: Tauri invoke() (for desktop app)
const ipcModule = webMode
  ? "src/lib/ipc-http.ts"
  : demoMode
  ? "src/lib/ipc-demo.ts"
  : null;

const shimTargets = ipcModule
  ? {
      "../lib/ipc": path.resolve(__dirname, ipcModule),
      "./ipc": path.resolve(__dirname, ipcModule),
      // App.tsx sits at src/ and imports "./lib/ipc", which neither of the two
      // specifiers above matches — so in WEB and DEMO builds it was silently
      // getting the *Tauri* module, whose invoke() is shimmed to reject. Every
      // ipc call made directly from App.tsx (emergency stop, the delete-block
      // banner, reconnect) therefore did nothing in the web UI, failing quietly
      // into a .catch(). Found while wiring AuthGate, which is in the same file
      // and would have rendered the app for everyone regardless of sign-in.
      "./lib/ipc": path.resolve(__dirname, ipcModule),
      "@tauri-apps/api/core": path.resolve(__dirname, "src/lib/tauri-shims.ts"),
      "@tauri-apps/api/event": path.resolve(__dirname, "src/lib/tauri-shims.ts"),
      "@tauri-apps/api/path": path.resolve(__dirname, "src/lib/tauri-shims.ts"),
      "@tauri-apps/plugin-dialog": path.resolve(__dirname, "src/lib/tauri-shims.ts"),
      "@tauri-apps/plugin-notification": path.resolve(__dirname, "src/lib/tauri-shims.ts"),
    }
  : undefined;

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  resolve: shimTargets ? { alias: shimTargets } : undefined,
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
