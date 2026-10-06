import type {ConnectorInstance} from "./model";

/** Display numbers never participate in electrical identity. Keep the existing
 * numeric range; alphanumeric contact designations require a separate contract. */
export function templateContactNumbers(sourceNumbers: readonly string[]): readonly number[] {
  const numbers = sourceNumbers.map(source => {
    if (!/^[0-9]+$/.test(source) || !Number.isSafeInteger(Number(source)) || Number(source) < 1 || Number(source) > 300)
      throw new Error(`Номер контакта «${source}» должен быть целым числом от 1 до 300. Исправьте номер в библиотеке перед размещением.`);
    return Number(source);
  });
  if (new Set(numbers).size !== numbers.length)
    throw new Error("Исходные номера контактов неоднозначны: после числового преобразования есть совпадения. Исправьте номера в библиотеке.");
  return numbers;
}

export function templateContactNumberingWarning(connector: ConnectorInstance): string | null {
  const binding = connector.libraryBinding;
  if (binding?.mode !== "template" || binding.contactNumbering === "source-v1") return null;
  try { templateContactNumbers(binding.snapshot.contacts.map(contact => contact.sourceNumber)); }
  catch (error) {
    return `Сохранена прежняя нумерация контактов; подключения не изменены. ${error instanceof Error ? error.message : String(error)}`;
  }
  return null;
}

/** Only migrate a validated legacy materialization, atomically, without reordering
 * contacts or touching IDs, wire endpoints, snapshots or graph coordinates. */
export function restoreTemplateContactNumbers(connector: ConnectorInstance): ConnectorInstance {
  const binding = connector.libraryBinding;
  if (binding?.mode !== "template" || binding.contactNumbering) return connector;
  let numbers: readonly number[];
  try { numbers = templateContactNumbers(binding.snapshot.contacts.map(contact => contact.sourceNumber)); }
  catch { return connector; } // Retain readable legacy data and surface its diagnostic in the inspector.
  const sourceByLogicalId = new Map(binding.snapshot.contacts.map((contact, index) => [contact.logicalContactId, numbers[index]!])) as Map<string, number>;
  if (connector.contacts.some(contact => !contact.logicalContactId || !sourceByLogicalId.has(contact.logicalContactId)) ||
      sourceByLogicalId.size !== connector.contacts.length) return connector;
  return {...connector, libraryBinding: {...binding, contactNumbering: "source-v1"},
    contacts: connector.contacts.map(contact => ({...contact, number: sourceByLogicalId.get(contact.logicalContactId!)!}))};
}
