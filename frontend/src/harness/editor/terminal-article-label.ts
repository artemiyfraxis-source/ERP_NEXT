/** БД.ТЕР keys encode manufacturer, reel article, piece article and series.
 * Decode only a complete key; persisted identities and option values stay intact. */
export function terminalArticleLabel(key: string): string {
  const parts: string[] = [];
  let offset = 0;
  while (offset < key.length) {
    const colon = key.indexOf(":", offset);
    if (colon < 0 || !/^\d+$/.test(key.slice(offset, colon))) return key;
    const length = Number(key.slice(offset, colon));
    const end = colon + 1 + length;
    if (!Number.isSafeInteger(length) || end > key.length) return key;
    parts.push(key.slice(colon + 1, end));
    if (end === key.length) return parts.length === 4 ? parts[1] || parts[2] || key : key;
    if (key[end] !== "|") return key;
    offset = end + 1;
  }
  return key;
}
