import { api } from "./api";


export type ClientError = { at: string; ip: string; url: string; message: string; browser: string };

export const devicesApi = {
  /** Open before sign-in. */
  entry: () => api<{ tsd: boolean }>("/api/devices/entry"),
  qrUrl: (target: string) => `/api/devices/qr.svg?url=${encodeURIComponent(target)}`,
  scannerLog: () => api<{ lines: string[] }>("/api/devices/scanner-log"),
  sendScannerLog: (lines: string[]) =>
    api<{ ok: boolean }>("/api/devices/scanner-log", { method: "POST", body: JSON.stringify({ lines }), safe: true }),
  clientErrors: () => api<{ errors: ClientError[] }>("/api/devices/client-errors"),
};

/** The ТСД screen's own address: `?section=tsd` (also reached by the short `/tsd`). */
export function isTsdAddress(location: Pick<Location, "pathname" | "search"> = window.location): boolean {
  return location.pathname === "/tsd" || new URLSearchParams(location.search).get("section") === "tsd";
}
