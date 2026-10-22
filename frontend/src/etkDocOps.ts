import type { ProjectTechcard } from "./api";
import { pinCoreColor, withCoreColors } from "./etkCoreColors";
import { etkOperationName, recordCategory } from "./etkOperation";

/* Операции собранного документа ЭТК так, как их показывают схема ПФ и маршрут: техкарта (ProjectEtk) и схема ПФ
   генератора (GenPfScheme) читают их одними функциями. */

/** То, что эти функции читают у операции ЭТК; у экрана техкарты операция шире. */
export type EtkDocOperation = {
  position?: number;
  type?: string;
  code?: string;
  side?: string;
  title?: string;
  connector?: string;
  connector_article?: string;
  edited?: { at?: string; by?: string };
  card?: null | { name?: string };
  fields?: { wires?: Array<{ name?: string; art?: string; pins?: number[] }> };
};

const WITH_COLORS = new WeakMap<object, unknown[]>();

/** Операции документа; провода монтажа несут цвета жил из `build_config` (как в «Схеме связей»), без пересборки карты. */
export function etkDocOperations<T extends EtkDocOperation = EtkDocOperation>(document: ProjectTechcard["document"]): T[] {
  const value = document?.operations;
  if (!Array.isArray(value)) return [];
  const known = WITH_COLORS.get(document as object);
  if (known) return known as T[];
  const list = withCoreColors(value as T[], document);
  WITH_COLORS.set(document as object, list);
  return list;
}

/** Распиновка из операций монтажа — по узлу схемы «артикул разъёма::сторона»: провода видны в своих гнёздах. */
export function harnessPins(list: EtkDocOperation[]): Record<string, Record<number, string>> {
  const out: Record<string, Record<number, string>> = {};
  list.forEach((operation) => {
    const article = String(operation.connector_article || "").trim();
    if (!article) return;
    (operation.fields?.wires ?? []).forEach((wire) => (wire.pins ?? []).forEach((pin) => {
      (out[`${article}::${operation.side || ""}`] ||= {})[pin] = pinCoreColor(wire as Record<string, unknown>, pin) || wire.art || wire.name || "";
    }));
  });
  return out;
}

/** Код карты операции; «БЕЗ КОДА» из старых ТК — это пустой код. */
export function opCode(operation: EtkDocOperation): string {
  const code = String(operation.code || "").trim();
  return /^без кода$/i.test(code) ? "" : code;
}

/** Вторая строка пункта маршрута: код карты операции и сторона/разъём; номер разъёма без букв не показываем. */
export function navSub(operation: EtkDocOperation): string {
  const connector = String(operation.connector || "").trim();
  return [opCode(operation), /^\d+$/.test(connector) ? "" : connector].filter(Boolean).join(" · ");
}

/** Русское название операции: имя карты → название шаблона без кода («CUT_WIRE+1crimp | Автомат резка…») →
 *  категория по коду карты. Код и тип операции (cutWireCrimp) названием не бывают. */
export function etkTitle(operation: EtkDocOperation): string {
  const title = String(operation.title || "").split("|").map((part) => part.trim()).filter(Boolean).at(-1) || "";
  const category = opCode(operation) ? recordCategory(opCode(operation)) : "";
  return etkOperationName(operation.card?.name || "", operation.type) || title || (category && category !== "Прочее" ? category : "") || "Операция";
}
