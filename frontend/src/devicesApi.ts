import { api } from "./api";

/** Стенд, опция warehouse.tsd: вход с устройств (backend app/workflows/devices_api.py). */
export const TSD_OPTION = "warehouse.tsd";

export type DeviceAddress = { url: string; label: string };
export type DevicesStatus = {
  /** «Пускать устройства из сети» is on. */
  enabled: boolean;
  /** The gate answers on `port`. */
  listening: boolean;
  port: number;
  api_port: number;
  addresses: DeviceAddress[];
  https: boolean;
  /** Why it is not listening, or why only http:// works. */
  reason: string;
  host: string;
};
export type ClientError = { at: string; ip: string; url: string; message: string; browser: string };

export const devicesApi = {
  /** Open before sign-in: 404 unless the stage has the ТСД screen. */
  entry: () => api<{ tsd: boolean }>("/api/devices/entry"),
  status: () => api<DevicesStatus>("/api/devices/status"),
  // Starting the gate makes a certificate the first time: give it longer than an ordinary write.
  setNetwork: (enabled: boolean) =>
    api<DevicesStatus>("/api/devices/network", { method: "PUT", body: JSON.stringify({ enabled }), timeoutMs: 60_000 }),
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
