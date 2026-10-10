// Entry point of the harness editor (Э4 schematic + drawing). Only type exports and lazy loaders live here, so importing
// this module keeps the editor (≈40k lines + styles + textures) out of the main bundle: it loads as its own chunk.
import { lazy } from "react";

export type { HarnessDesignEditorProps, HarnessEditorCatalogApi } from "./editor/HarnessDesignEditor";
export type { HarnessEditorView } from "./editor/editor-types";
export type { HarnessDrawingPorts, DrawingSaveState } from "./drawing";

/** The editor component, loaded on demand. Render it inside <Suspense>. */
export const HarnessEditorLazy = lazy(() => import("./HarnessEditor"));

/** The drawing of a position and the ERP catalog (reference books, component library, БД.МАТ coverings), loaded on
 *  demand together with the editor's model code. */
export const loadHarnessDrawing = () => Promise.all([import("./drawing"), import("./erpCatalog")])
  .then(([drawing, catalog]) => ({ harnessDrawing: drawing.harnessDrawing, createErpHarnessEditorCatalog: catalog.createErpHarnessEditorCatalog }));
