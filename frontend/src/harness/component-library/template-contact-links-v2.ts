import type {
  LogicalContactV2,
  TemplateContentV2,
  TemplateViewV2,
  ViewContactPointV2,
  ViewKindV2,
} from "./template-model-v2";

export interface LogicalContactRepresentationV2 {
  readonly logicalContactId: string;
  readonly pointId: string;
  readonly viewId: string;
  readonly viewName: string;
  readonly viewKind: ViewKindV2;
}

export interface AvailableLogicalContactV2 {
  readonly contact: LogicalContactV2;
  readonly representedIn: readonly LogicalContactRepresentationV2[];
}

export interface ContactCoverageDiagnosticV2 {
  readonly code: "missing_linked_contact_point";
  readonly logicalContactId: string;
  readonly contactNumber: string;
  readonly contactName: string;
  readonly viewId: string;
  readonly viewName: string;
  readonly viewKind: "e4" | "drawing";
  readonly message: string;
}

/** Returns every persisted view point that represents one logical contact. */
export function logicalContactRepresentationsV2(
  content: TemplateContentV2,
  logicalContactId: string,
): readonly LogicalContactRepresentationV2[] {
  const result: LogicalContactRepresentationV2[] = [];
  for (const view of content.views) {
    for (const point of view.contactPoints) {
      if (point.logicalContactId !== logicalContactId) continue;
      result.push({
        logicalContactId,
        pointId: point.id,
        viewId: view.id,
        viewName: view.name,
        viewKind: view.kind,
      });
    }
  }
  return result;
}

/**
 * Lists contacts that can be linked into a view without creating another
 * logical contact. The order follows logicalContacts and is therefore stable.
 */
export function availableLogicalContactsForViewV2(
  content: TemplateContentV2,
  viewId: string,
): readonly AvailableLogicalContactV2[] {
  const view = content.views.find(candidate => candidate.id === viewId);
  if (!view) return [];
  const represented = new Set(view.contactPoints.map(point => point.logicalContactId));
  return content.logicalContacts
    .filter(contact => !represented.has(contact.id))
    .map(contact => ({
      contact,
      representedIn: logicalContactRepresentationsV2(content, contact.id),
    }));
}

/**
 * Advisory completeness check for the two mandatory projections. Additional
 * views are deliberately optional, and a bundle port is not an electrical
 * representation of a contact.
 */
export function collectContactCoverageDiagnosticsV2(
  content: TemplateContentV2,
): readonly ContactCoverageDiagnosticV2[] {
  const requiredViews = content.views.filter(
    (view): view is TemplateViewV2 & { readonly kind: "e4" | "drawing" } =>
      view.kind === "e4" || view.kind === "drawing",
  );
  const diagnostics: ContactCoverageDiagnosticV2[] = [];
  for (const view of requiredViews) {
    const represented = new Set(view.contactPoints.map(point => point.logicalContactId));
    for (const contact of content.logicalContacts) {
      if (represented.has(contact.id)) continue;
      diagnostics.push({
        code: "missing_linked_contact_point",
        logicalContactId: contact.id,
        contactNumber: contact.number,
        contactName: contact.name,
        viewId: view.id,
        viewName: view.name,
        viewKind: view.kind,
        message: `Контакт №${contact.number} не размещён в виде ${view.name}.`,
      });
    }
  }
  return diagnostics;
}

export interface ContactPointWithLogicalV2 {
  readonly point: ViewContactPointV2;
  readonly contact: LogicalContactV2 | null;
}

/** Keeps malformed references visible in the panel until strict validation reports them. */
export function contactPointsWithLogicalV2(
  content: TemplateContentV2,
  viewId: string,
): readonly ContactPointWithLogicalV2[] {
  const view = content.views.find(candidate => candidate.id === viewId);
  if (!view) return [];
  const contacts = new Map(content.logicalContacts.map(contact => [contact.id, contact]));
  return view.contactPoints.map(point => ({ point, contact: contacts.get(point.logicalContactId) ?? null }));
}
