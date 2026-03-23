/**
 * Shims for @tauri-apps APIs when running outside Tauri (demo/screenshot mode).
 */

// @tauri-apps/api/core
export function invoke(): Promise<never> {
  return Promise.reject(new Error("Not in Tauri"));
}

// @tauri-apps/api/event
export type UnlistenFn = () => void;
export function listen(): Promise<UnlistenFn> {
  return Promise.resolve(() => {});
}
export function emit() {}

// @tauri-apps/api/path
export async function homeDir(): Promise<string> {
  return "/home/user";
}

// @tauri-apps/plugin-dialog
export async function open(): Promise<string | null> {
  return null;
}

// @tauri-apps/plugin-notification
export function sendNotification() {}
export function isPermissionGranted(): Promise<boolean> {
  return Promise.resolve(false);
}
