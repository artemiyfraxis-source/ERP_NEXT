// What the page loads together with the editor: the ERP catalog (reference books + component library) and the editor's
// port types. In-memory test doubles live in `testing/in-memory.ts`.
export {
  parseHarnessDesignSnapshot,
  HarnessDesignApiError,
  type HarnessDesignApi,
  type HarnessDesignResource,
} from "./editor/design-api";
export {
  type ComponentPlacementApi,
  type PlaceComponentRequest,
  type ComponentPlacementResult,
  type ProjectComponentPlacementGraph,
} from "./editor/component-placement-api";
export {
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
export { type HarnessEditorCatalogApi } from "./editor/HarnessDesignEditor";
export type { HarnessDesignDocument } from "./editor/model";
// ERP reference books (H-T4): our БД.ТЕР, БД.ПРОВ, СПР.КАБ, БД.КОАКС, БД.МАТ and connector base.
export {
  createErpHarnessEditorCatalog,
  createErpReferenceCatalog,
  fetchErpHarnessCatalog,
  findMissingReferences,
  NOT_IN_REFERENCES,
  type ErpHarnessCatalog,
  type MissingReference,
} from "./erpCatalog";
