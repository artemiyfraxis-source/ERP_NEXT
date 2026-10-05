import type { HarnessDesignDocument } from "./model";

export interface RouteCutReadiness { readonly ready: boolean; readonly code: "ready"; readonly message: string }

/**
 * In TECHMAP-GRAPHER the cut map opened only after the manufacturing route (Route V2) prepared every
 * blank. The route is not part of the ERP port (ERP keeps routes in tech cards), so the cut map is
 * always available here and is built live from the current document.
 */
export function routeCutReadiness(_document: HarnessDesignDocument, _sourceFingerprint?: string, _unsaved = false): RouteCutReadiness {
  return { ready: true, code: "ready", message: "" };
}
