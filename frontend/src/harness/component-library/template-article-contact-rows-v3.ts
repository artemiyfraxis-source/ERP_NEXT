import { evaluateNumericExpressionV2 } from "./template-commands-v2";
import {
  repeatOccurrenceKeyV2,
  TEMPLATE_V2_LIMITS,
  type ContactDirectionV2,
  type ViewKindV2,
} from "./template-model-v2";
import {
  expandTemplateRepeatsV2,
  resolveTemplateParameterValuesV2,
  TemplateRepeatV2Error,
} from "./template-repeat-v2";
import {
  materializeArticleVariantV3,
} from "./template-article-materialization-v3";
import type {
  ArticleKeyV3,
  ArticleVariantV3,
  LogicalContactV3,
  TemplateContentV3,
} from "./template-model-v3";

export interface MaterializedContactRepresentationV3 {
  readonly viewId: string;
  readonly viewName: string;
  readonly viewKind: ViewKindV2;
  /** ID of the contact point stored in the reusable template. */
  readonly pointId: string;
  /** Stable actual-point key for a repeated representation. */
  readonly occurrenceKey?: string;
  readonly x: number;
  readonly y: number;
  readonly direction: ContactDirectionV2;
}

export interface MaterializedArticleContactRowV3 {
  /** Logical-contact ID for fixed rows; repeat occurrence key for repeated rows. */
  readonly key: string;
  readonly prototypeLogicalContactId: string;
  readonly contactTypeGroupId: string | null;
  readonly number: string;
  readonly name: string;
  readonly circuitText: string | null;
  readonly allowedTerminalArticleKeys: readonly ArticleKeyV3[];
  readonly representations: readonly MaterializedContactRepresentationV3[];
}

function repeatedNumber(prototype: string, occurrenceIndex: number, stride: number): string {
  if (/^(0|[1-9]\d*)$/.test(prototype)) {
    const numeric = Number(prototype);
    const result = numeric + occurrenceIndex * stride;
    if (Number.isSafeInteger(result)) return String(result);
  }
  return `${prototype}-${occurrenceIndex + 1}`;
}

function immutableArticleKeys(keys: readonly ArticleKeyV3[]): readonly ArticleKeyV3[] {
  return Object.freeze(keys.map(key => Object.freeze({ ...key })));
}

function immutableRepresentations(
  representations: readonly MaterializedContactRepresentationV3[],
): readonly MaterializedContactRepresentationV3[] {
  return Object.freeze(representations.map(representation => Object.freeze({ ...representation })));
}

function row(
  key: string,
  contact: LogicalContactV3,
  number: string,
  allowedTerminalArticleKeys: readonly ArticleKeyV3[],
  representations: readonly MaterializedContactRepresentationV3[],
): MaterializedArticleContactRowV3 {
  return Object.freeze({
    key,
    prototypeLogicalContactId: contact.id,
    contactTypeGroupId: contact.contactTypeGroupId,
    number,
    name: contact.name,
    circuitText: contact.circuitText,
    allowedTerminalArticleKeys: immutableArticleKeys(allowedTerminalArticleKeys),
    representations: immutableRepresentations(representations),
  });
}

/**
 * Produces the actual contact rows of one selected series article. A logical
 * contact remains one row even when E4, drawing and additional views all have a
 * representation. Bundle ports are deliberately outside this electrical model.
 */
export function materializeArticleContactRowsV3(
  content: TemplateContentV3,
  variantOrId: ArticleVariantV3 | string,
): readonly MaterializedArticleContactRowV3[] {
  const materialized = materializeArticleVariantV3(content, variantOrId);
  const parameterValues = resolveTemplateParameterValuesV2(
    materialized.repeatContent,
    materialized.repeatOptions,
  );
  const expandedViews = expandTemplateRepeatsV2(
    materialized.repeatContent,
    materialized.repeatOptions,
  );
  const contactsById = new Map(content.logicalContacts.map(contact => [contact.id, contact]));
  const repeatedContactIds = new Set(content.repeaters.flatMap(domain => domain.logicalContactIds));
  const terminalKeysByGroup = new Map(
    (materialized.variant.contactGroups ?? []).map(group => [
      group.contactTypeGroupId,
      group.allowedTerminalArticleKeys,
    ]),
  );

  const fixedRepresentations = new Map<string, MaterializedContactRepresentationV3[]>();
  for (const view of content.views) {
    for (const point of view.contactPoints) {
      if (repeatedContactIds.has(point.logicalContactId)) continue;
      const representations = fixedRepresentations.get(point.logicalContactId) ?? [];
      representations.push({
        viewId: view.id,
        viewName: view.name,
        viewKind: view.kind,
        pointId: point.id,
        x: evaluateNumericExpressionV2(point.x, parameterValues),
        y: evaluateNumericExpressionV2(point.y, parameterValues),
        direction: point.direction,
      });
      fixedRepresentations.set(point.logicalContactId, representations);
    }
  }

  const repeatedRepresentations = new Map<string, MaterializedContactRepresentationV3[]>();
  for (const expandedView of expandedViews) {
    const view = content.views.find(candidate => candidate.id === expandedView.viewId);
    if (!view) continue;
    for (const placement of expandedView.placements) {
      for (const occurrence of placement.occurrences) {
        for (const point of occurrence.contactPoints) {
          const representations = repeatedRepresentations.get(point.key) ?? [];
          representations.push({
            viewId: view.id,
            viewName: view.name,
            viewKind: view.kind,
            pointId: point.prototypeContactPointId,
            occurrenceKey: point.key,
            x: point.x,
            y: point.y,
            direction: point.direction,
          });
          repeatedRepresentations.set(point.key, representations);
        }
      }
    }
  }

  const result: MaterializedArticleContactRowV3[] = [];
  for (const contact of content.logicalContacts) {
    if (repeatedContactIds.has(contact.id)) continue;
    result.push(row(
      contact.id,
      contact,
      contact.number,
      contact.contactTypeGroupId === null ? [] : terminalKeysByGroup.get(contact.contactTypeGroupId) ?? [],
      fixedRepresentations.get(contact.id) ?? [],
    ));
  }

  for (const domain of content.repeaters) {
    const count = parameterValues.get(domain.countParameterId);
    if (!Number.isSafeInteger(count) || Number(count) < 1) {
      throw new TemplateRepeatV2Error(
        "repeat_count",
        "Количество повторов должно быть целым числом от 1 до 1000.",
        domain.countParameterId,
      );
    }
    const stride = domain.logicalContactIds.length;
    for (let occurrenceIndex = 0; occurrenceIndex < Number(count); occurrenceIndex += 1) {
      for (const logicalContactId of domain.logicalContactIds) {
        const contact = contactsById.get(logicalContactId);
        if (!contact) continue;
        const key = repeatOccurrenceKeyV2(domain.id, occurrenceIndex, contact.id);
        result.push(row(
          key,
          contact,
          repeatedNumber(contact.number, occurrenceIndex, stride),
          contact.contactTypeGroupId === null ? [] : terminalKeysByGroup.get(contact.contactTypeGroupId) ?? [],
          repeatedRepresentations.get(key) ?? [],
        ));
      }
    }
  }

  if (result.length > TEMPLATE_V2_LIMITS.contacts) {
    throw new TemplateRepeatV2Error(
      "article_contact_row_budget",
      `Артикул не может содержать больше ${TEMPLATE_V2_LIMITS.contacts} материализованных строк контактов.`,
      "articleVariants.contactGroups",
    );
  }

  return Object.freeze(result);
}
