// Названия разъёмов коакса словами монтажника — общие для эскизов ЭТК (etkDraw) и картинок ПФ (etkPfCoaxArt).
type Row = Record<string, unknown>;
const str = (value: unknown): string => (value == null ? "" : String(value));

/** Разъём конца коакса словами монтажника (владелец 19.10: «какая зачистка к какому разъёму»): тип из БД.КОАКС с большой
 *  Male/Female и русская половина по контактам — вилка (штыри) / розетка (гнёзда), как `etk_builder.coax_connector_kind`.
 *  Берёт готовые `connectorType` / `gender` конца, иначе считает по `type`; типа нет — пустые строки. */
export function coaxEndNames(end: Row): { kind: string; word: string } {
  const given = str(end.connectorType).trim();
  if (given) return { kind: given, word: str(end.gender).trim() };
  const type = str(end.type).replace(/\s+/g, " ").trim();
  if (!type) return { kind: "", word: "" };
  const female = /(?<![a-z])female(?![a-z])/i, male = /(?<![a-z])male(?![a-z])/i;
  return { kind: type.replace(female, "Female").replace(male, "Male"), word: female.test(type) ? "розетка" : male.test(type) ? "вилка" : "" };
}

/** «SMA-Male (вилка)» — разъём без артикула; пусто, когда типа нет. */
export function coaxKindTitle(end: Row): string {
  const { kind, word } = coaxEndNames(end);
  return kind ? `${kind}${word ? ` (${word})` : ""}` : "";
}

/** «Сторона А — SMA-Male (вилка), SMA-C174P»: заголовок рисунка конца (без стороны — только разъём). */
export function coaxEndTitle(side: string, end: Row): string {
  const connector = [coaxKindTitle(end), str(end.connector || end.article).trim()].filter(Boolean).join(", ");
  return [side, connector].filter(Boolean).join(" — ");
}
