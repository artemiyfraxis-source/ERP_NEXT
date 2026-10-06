import type { ParameterValueV2, TemplateContentV2 } from "./template-model-v2";
import type {
  ArticleVariantV3,
  TemplateContentV3,
} from "./template-model-v3";
import {
  resolveTemplateParameterValuesV2,
  TemplateRepeatV2Error,
  type TemplateParameterResolutionOptionsV2,
} from "./template-repeat-v2";
import { projectTemplateContentV3CoreToV2 } from "./template-commands-v3";

export type ArticleVariantMaterializationV3ErrorCode =
  | "article_variant_missing"
  | "duplicate_parameter_override"
  | "missing_contact_type_group"
  | "duplicate_contact_group_configuration"
  | "repeat_contact_missing"
  | "repeat_contact_group_missing"
  | "mixed_repeat_contact_groups"
  | "ambiguous_group_repeat"
  | "repeat_parameter_conflict"
  | "contact_count_below_fixed"
  | "contact_count_not_divisible"
  | "repeat_count_out_of_range"
  | "parameter_materialization_failed";

export class ArticleVariantMaterializationV3Error extends Error {
  constructor(
    readonly code: ArticleVariantMaterializationV3ErrorCode,
    message: string,
    readonly path: string,
  ) {
    super(message);
    this.name = "ArticleVariantMaterializationV3Error";
  }
}

export interface MaterializedRepeatCountV3 {
  readonly contactTypeGroupId: string;
  readonly repeatDomainId: string;
  readonly countParameterId: string;
  readonly fixedContactCount: number;
  readonly contactsPerOccurrence: number;
  readonly requestedContactCount: number;
  readonly repeatCount: number;
}

export interface ArticleVariantMaterializationV3 {
  readonly variant: ArticleVariantV3;
  /** A v2-compatible projection accepted by expandTemplateRepeatsV2. */
  readonly repeatContent: TemplateContentV2;
  /** Pass directly as the options argument to expandTemplateRepeatsV2. */
  readonly repeatOptions: TemplateParameterResolutionOptionsV2;
  /** Parameter values from the article followed by derived repeat counts. */
  readonly overrides: Readonly<Record<string, ParameterValueV2>>;
  readonly repeatCounts: readonly MaterializedRepeatCountV3[];
}

interface GroupRepeatDomainV3 {
  readonly contactTypeGroupId: string;
  readonly repeatDomainId: string;
  readonly countParameterId: string;
  readonly stride: number;
}

function variantPath(content: TemplateContentV3, variantId: string): string {
  const index = content.articleVariants.findIndex(variant => variant.id === variantId);
  return index < 0 ? "$.articleVariants" : `$.articleVariants[${index}]`;
}

function repeatDomainsByGroup(
  content: TemplateContentV3,
  basePath: string,
): { readonly byGroup: Map<string, GroupRepeatDomainV3[]>; readonly repeatedContactIds: Set<string> } {
  const contacts = new Map(content.logicalContacts.map(contact => [contact.id, contact]));
  const groups = new Set(content.contactTypeGroups.map(group => group.id));
  const repeatedContactIds = new Set<string>();
  const byGroup = new Map<string, GroupRepeatDomainV3[]>();

  content.repeaters.forEach((domain, domainIndex) => {
    if (domain.logicalContactIds.length === 0) return;
    let domainGroupId: string | null = null;
    for (const logicalContactId of domain.logicalContactIds) {
      const contact = contacts.get(logicalContactId);
      if (!contact) {
        throw new ArticleVariantMaterializationV3Error(
          "repeat_contact_missing",
          `Домен повтора ссылается на отсутствующий контакт ${logicalContactId}.`,
          `$.repeaters[${domainIndex}].logicalContactIds`,
        );
      }
      repeatedContactIds.add(contact.id);
      if (!contact.contactTypeGroupId || !groups.has(contact.contactTypeGroupId)) {
        throw new ArticleVariantMaterializationV3Error(
          "repeat_contact_group_missing",
          `Контакт №${contact.number} из домена повтора не принадлежит группе типов.`,
          `$.logicalContacts[id=${contact.id}].contactTypeGroupId`,
        );
      }
      if (domainGroupId === null) domainGroupId = contact.contactTypeGroupId;
      else if (domainGroupId !== contact.contactTypeGroupId) {
        throw new ArticleVariantMaterializationV3Error(
          "mixed_repeat_contact_groups",
          "Один домен повтора содержит контакты разных групп типов.",
          `$.repeaters[${domainIndex}].logicalContactIds`,
        );
      }
    }
    if (!domainGroupId || domain.logicalContactIds.length === 0) {
      throw new ArticleVariantMaterializationV3Error(
        "repeat_contact_group_missing",
        "Домен повтора не содержит контактов с определённой группой типов.",
        `$.repeaters[${domainIndex}].logicalContactIds`,
      );
    }
    const entries = byGroup.get(domainGroupId) ?? [];
    entries.push({
      contactTypeGroupId: domainGroupId,
      repeatDomainId: domain.id,
      countParameterId: domain.countParameterId,
      stride: domain.logicalContactIds.length,
    });
    byGroup.set(domainGroupId, entries);
  });

  for (const [groupId, domains] of byGroup) {
    if (domains.length > 1) {
      throw new ArticleVariantMaterializationV3Error(
        "ambiguous_group_repeat",
        "Для одной группы типов найдено несколько доменов повтора; количество нельзя сопоставить однозначно.",
        `${basePath}.contactGroups[contactTypeGroupId=${groupId}]`,
      );
    }
  }
  return { byGroup, repeatedContactIds };
}

function parameterOverrides(
  content: TemplateContentV3,
  variant: ArticleVariantV3,
  basePath: string,
): Record<string, ParameterValueV2> {
  const result: Record<string, ParameterValueV2> = Object.create(null) as Record<string, ParameterValueV2>;
  const seen = new Set<string>();
  variant.parameterValues.forEach((entry, index) => {
    if (seen.has(entry.parameterId)) {
      throw new ArticleVariantMaterializationV3Error(
        "duplicate_parameter_override",
        `Параметр ${entry.parameterId} повторяется в варианте артикула.`,
        `${basePath}.parameterValues[${index}].parameterId`,
      );
    }
    seen.add(entry.parameterId);
    result[entry.parameterId] = entry.value;
  });
  return result;
}

/**
 * Converts a selected article variant into a v2 repeat-engine input without
 * changing the template or the variant. Explicit contact-group totals define
 * repeat counts; null leaves every repeat count at its parameter default.
 */
export function materializeArticleVariantV3(
  content: TemplateContentV3,
  variantOrId: ArticleVariantV3 | string,
): ArticleVariantMaterializationV3 {
  const variant = typeof variantOrId === "string"
    ? content.articleVariants.find(candidate => candidate.id === variantOrId)
    : content.articleVariants.find(candidate => candidate.id === variantOrId.id);
  if (!variant) {
    const id = typeof variantOrId === "string" ? variantOrId : variantOrId.id;
    throw new ArticleVariantMaterializationV3Error(
      "article_variant_missing",
      `Вариант артикула ${id} не найден в шаблоне.`,
      "$.articleVariants",
    );
  }
  const basePath = variantPath(content, variant.id);
  const overrides = parameterOverrides(content, variant, basePath);
  const repeatCounts: MaterializedRepeatCountV3[] = [];

  if (variant.contactGroups !== null) {
    const configuredGroups = new Map<string, number>();
    variant.contactGroups.forEach((configuration, index) => {
      if (!content.contactTypeGroups.some(group => group.id === configuration.contactTypeGroupId)) {
        throw new ArticleVariantMaterializationV3Error(
          "missing_contact_type_group",
          `Группа типов ${configuration.contactTypeGroupId} не найдена.`,
          `${basePath}.contactGroups[${index}].contactTypeGroupId`,
        );
      }
      if (configuredGroups.has(configuration.contactTypeGroupId)) {
        throw new ArticleVariantMaterializationV3Error(
          "duplicate_contact_group_configuration",
          "Группа контактов повторяется в варианте артикула.",
          `${basePath}.contactGroups[${index}].contactTypeGroupId`,
        );
      }
      configuredGroups.set(configuration.contactTypeGroupId, configuration.contactCount);
    });

    const domains = repeatDomainsByGroup(content, basePath);
    const fixedCounts = new Map(content.contactTypeGroups.map(group => [group.id, 0]));
    for (const contact of content.logicalContacts) {
      if (contact.contactTypeGroupId && !domains.repeatedContactIds.has(contact.id))
        fixedCounts.set(contact.contactTypeGroupId, (fixedCounts.get(contact.contactTypeGroupId) ?? 0) + 1);
    }

    for (const group of content.contactTypeGroups) {
      const requested = configuredGroups.get(group.id) ?? 0;
      const fixed = fixedCounts.get(group.id) ?? 0;
      const domain = domains.byGroup.get(group.id)?.[0];
      if (!domain) {
        if (requested !== fixed) {
          throw new ArticleVariantMaterializationV3Error(
            "contact_count_not_divisible",
            `Для группы «${group.name}» задано ${requested} контактов, но шаблон содержит ${fixed} фиксированных контактов и не имеет повтора.`,
            `${basePath}.contactGroups[contactTypeGroupId=${group.id}].contactCount`,
          );
        }
        continue;
      }
      if (Object.prototype.hasOwnProperty.call(overrides, domain.countParameterId)) {
        throw new ArticleVariantMaterializationV3Error(
          "repeat_parameter_conflict",
          `Параметр количества повтора группы «${group.name}» уже задан в parameterValues.`,
          `${basePath}.parameterValues`,
        );
      }
      if (requested < fixed) {
        throw new ArticleVariantMaterializationV3Error(
          "contact_count_below_fixed",
          `Для группы «${group.name}» нельзя получить ${requested} контактов: фиксированных контактов уже ${fixed}.`,
          `${basePath}.contactGroups[contactTypeGroupId=${group.id}].contactCount`,
        );
      }
      const repeatedContacts = requested - fixed;
      if (repeatedContacts % domain.stride !== 0) {
        throw new ArticleVariantMaterializationV3Error(
          "contact_count_not_divisible",
          `Количество ${requested} для группы «${group.name}» не согласуется с ${fixed} фиксированными контактами и шагом повтора ${domain.stride}.`,
          `${basePath}.contactGroups[contactTypeGroupId=${group.id}].contactCount`,
        );
      }
      const repeatCount = repeatedContacts / domain.stride;
      if (!Number.isSafeInteger(repeatCount) || repeatCount < 1 || repeatCount > 1_000) {
        throw new ArticleVariantMaterializationV3Error(
          "repeat_count_out_of_range",
          `Для группы «${group.name}» требуется ${repeatCount} повторов; допустимо от 1 до 1000.`,
          `${basePath}.contactGroups[contactTypeGroupId=${group.id}].contactCount`,
        );
      }
      overrides[domain.countParameterId] = repeatCount;
      repeatCounts.push(Object.freeze({
        contactTypeGroupId: group.id,
        repeatDomainId: domain.repeatDomainId,
        countParameterId: domain.countParameterId,
        fixedContactCount: fixed,
        contactsPerOccurrence: domain.stride,
        requestedContactCount: requested,
        repeatCount,
      }));
    }
  }

  const immutableOverrides = Object.freeze({ ...overrides });
  const repeatContent = projectTemplateContentV3CoreToV2(content);
  try {
    resolveTemplateParameterValuesV2(repeatContent, { overrides: immutableOverrides });
  } catch (caught) {
    const detail = caught instanceof TemplateRepeatV2Error ? caught.message : "Параметры варианта не удалось вычислить.";
    throw new ArticleVariantMaterializationV3Error(
      "parameter_materialization_failed",
      detail,
      `${basePath}.parameterValues`,
    );
  }
  return Object.freeze({
    variant,
    repeatContent,
    repeatOptions: Object.freeze({ overrides: immutableOverrides }),
    overrides: immutableOverrides,
    repeatCounts: Object.freeze(repeatCounts),
  });
}
