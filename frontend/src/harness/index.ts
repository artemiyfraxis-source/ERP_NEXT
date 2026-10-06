// Entry point of the harness editor (Э4 schematic + drawing), ported from TECHMAP-GRAPHER.
// Only type exports and lazy loaders live here, so importing this module keeps the editor
// (≈40k lines + styles + textures) out of the main bundle: it loads as its own chunk on first render.
import { lazy } from "react";

export type { HarnessDesignEditorProps } from "./editor/HarnessDesignEditor";
export type { HarnessEditorView } from "./editor/editor-types";
export type {
  ComponentPlacementApi,
  ComponentTemplateApi,
  CoveringMaterialApi,
  HarnessDesignApi,
  HarnessDesignDocument,
  HarnessDesignResource,
  HarnessEditorCatalogApi,
  InMemoryHarnessDesignApi,
  ReferenceCatalogAdapter,
} from "./adapters";

/** The editor component, loaded on demand. Render it inside <Suspense>. */
export const HarnessEditorLazy = lazy(() => import("./HarnessEditor"));

/** Adapter factories (in-memory today), loaded on demand together with the editor's model code. */
export const loadHarnessAdapters = () => import("./adapters");
