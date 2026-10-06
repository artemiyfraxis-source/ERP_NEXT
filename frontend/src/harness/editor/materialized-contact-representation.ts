import { drawingLocalDirection, drawingLocalPoint } from "./drawing-scale";
import type {
  ComponentTemplateContactRepresentationSnapshot,
  ComponentTemplateContactSnapshot,
  ConnectorInstance,
  EditorView,
  Point,
} from "./model";

export type MaterializedContactViewKind = Extract<EditorView, "e4" | "drawing">;
export type MaterializedContactDirection = ComponentTemplateContactRepresentationSnapshot["direction"];

export interface MaterializedContactWorldRepresentation {
  readonly position: Point;
  readonly direction: MaterializedContactDirection;
  readonly representation: ComponentTemplateContactRepresentationSnapshot;
}

function snapshotContactFor(
  connector: ConnectorInstance,
  contactIdOrLogicalContactId: string,
): ComponentTemplateContactSnapshot | null {
  const binding = connector.libraryBinding;
  if (binding?.mode !== "template") return null;
  const contact = connector.contacts.find(candidate => candidate.id === contactIdOrLogicalContactId) ??
    connector.contacts.find(candidate => candidate.logicalContactId === contactIdOrLogicalContactId);
  if (!contact?.logicalContactId) return null;
  return binding.snapshot.contacts.find(candidate =>
    candidate.logicalContactId === contact.logicalContactId) ?? null;
}

/**
 * Selects the pinned representation for an actual connector contact. Repeated
 * rows are matched by their materialized occurrence key; a prototype point is
 * never substituted for another occurrence. Null tells the editor to use its
 * ordinary connector-table fallback.
 */
export function selectMaterializedContactRepresentation(
  connector: ConnectorInstance,
  contactIdOrLogicalContactId: string,
  viewKind: MaterializedContactViewKind,
): ComponentTemplateContactRepresentationSnapshot | null {
  if (viewKind === "e4" && connector.e4TableMode) return null;
  const contact = snapshotContactFor(connector, contactIdOrLogicalContactId);
  if (!contact) return null;
  const repeated = contact.logicalContactId !== contact.prototypeLogicalContactId;
  return contact.representations.find(representation =>
    representation.viewKind === viewKind &&
    (connector.e4TableMode || (repeated
      ? representation.occurrenceKey === contact.logicalContactId
      : representation.occurrenceKey === undefined))) ?? null;
}

/** Translates template-local contact geometry into the selected harness view. */
export function materializedContactWorldRepresentation(
  connector: ConnectorInstance,
  contactIdOrLogicalContactId: string,
  viewKind: MaterializedContactViewKind,
): MaterializedContactWorldRepresentation | null {
  const representation = selectMaterializedContactRepresentation(
    connector,
    contactIdOrLogicalContactId,
    viewKind,
  );
  if (!representation) return null;
  const origin = connector.positions[viewKind];
  const local=viewKind==="drawing"?drawingLocalPoint(representation,connector.drawingPlacements):representation;
  return {
    position: {
      x: origin.x + local.x,
      y: origin.y + local.y,
    },
    direction: viewKind === "drawing"
      ? drawingLocalDirection(representation.direction, connector.drawingPlacements)
      : representation.direction,
    representation,
  };
}
