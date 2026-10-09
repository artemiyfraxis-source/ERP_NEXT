// «Скачать» of a drawing table window (ERP): the tables as the window shows them, to an Excel file. Read from the
// rendered window, so the file is exactly what is on screen (the column settings, the BOM order, the edited texts)
// and never touches the drawing document. Same format as the ERP tables' «Excel» (DataTable): tab-separated text,
// UTF-16LE with BOM, CRLF — Excel opens Cyrillic without the import wizard. The harness editor imports nothing from
// the app around it, hence the few lines of its own.

const SKIPPED = ".he-relations-actions, .he-position-toggle, svg, [aria-hidden='true']";

function nodeText(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? "";
  if (!(node instanceof Element) || node.matches(SKIPPED)) return "";
  if (node instanceof HTMLSelectElement) return node.selectedOptions[0]?.text ?? node.value;
  if (node instanceof HTMLInputElement) return node.type === "checkbox" || node.type === "radio" ? "" : node.value;
  if (node instanceof HTMLTextAreaElement) return node.value;
  return Array.from(node.childNodes, nodeText).join(" ");
}

export function cellText(cell: Element): string {
  return nodeText(cell).replace(/↗/g, "").replace(/\s+/g, " ").trim();
}

/** Every table under `root`, one line per row, a blank line between tables. */
export function tablesText(root: ParentNode): string {
  const tables = Array.from(root.querySelectorAll("table"));
  return tables.map(table => Array.from(table.rows, row => Array.from(row.cells, cellText).join("\t")).join("\r\n"))
    .filter(Boolean).join("\r\n\r\n") + "\r\n";
}

export function utf16WithBom(text: string): Uint8Array<ArrayBuffer> {
  const bytes = new Uint8Array(2 + text.length * 2);
  bytes[0] = 0xff;
  bytes[1] = 0xfe;
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    bytes[2 + i * 2] = code & 0xff;
    bytes[3 + i * 2] = code >> 8;
  }
  return bytes;
}

export function tableFileName(prefix: string, title: string): string {
  return `${[prefix.trim(), title].filter(Boolean).join(" — ")}.xls`.replace(/[\\/:*?"<>|]+/g, "_");
}

export function downloadTables(root: ParentNode, fileName: string): void {
  const url = URL.createObjectURL(new Blob([utf16WithBom(tablesText(root))], { type: "application/vnd.ms-excel" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.click();
  URL.revokeObjectURL(url);
}
