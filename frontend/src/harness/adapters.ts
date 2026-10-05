// Adapters the harness editor talks to. Today every one has an in-memory implementation;
// the ERP backend implementations replace them later without touching the editor.
export {
  createInMemoryHarnessDesignApi,
  parseHarnessDesignSnapshot,
  HarnessDesignApiError,
  type HarnessDesignApi,
  type HarnessDesignResource,
  type InMemoryHarnessDesignApi,
} from "./editor/design-api";
export {
  createInMemoryComponentPlacementApi,
  type ComponentPlacementApi,
  type PlaceComponentRequest,
  type ComponentPlacementResult,
  type ProjectComponentPlacementGraph,
} from "./editor/component-placement-api";
export {
  createInMemoryComponentTemplateCatalog,
  type ComponentTemplate,
  type ComponentTemplateApi,
  type ComponentTemplateSummary,
} from "./component-library/component-template-api";
export {
  createInMemoryReferenceCatalog,
  ReferenceCatalogApiError,
  type ReferenceCatalogAdapter,
  type ReferenceCatalogSnapshot,
  type ReferenceCatalogRecord,
} from "./reference-catalog";
export {
  createInMemoryCoveringMaterialApi,
  type CoveringMaterialApi,
  type GlobalMaterial,
} from "./editor/covering-assets";
export {
  createInMemoryHarnessEditorCatalog,
  type HarnessEditorCatalogApi,
} from "./editor/HarnessDesignEditor";
export type { HarnessDesignDocument } from "./editor/model";
