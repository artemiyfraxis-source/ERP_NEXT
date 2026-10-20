// СТО типовых дефектов (ФРАК-001-2026) для печати: A4 книжная, шапка таблиц повторяется на каждой странице.
import { cleanCodes, esc } from "./tdPrintSheets";
import type { Sto, StoSection } from "./tdTypes";

const CSS = `
  @page { size: A4 portrait; margin: 12mm; }
  body { font-family: Arial, Helvetica, sans-serif; font-size: 9pt; color: #000; margin: 0; }
  h1 { font-size: 15pt; text-align: center; margin: 0 0 3mm; }
  h2 { font-size: 12pt; margin: 6mm 0 2mm; }
  h3 { font-size: 10.5pt; margin: 4mm 0 1.5mm; }
  .title { text-align: center; margin-bottom: 8mm; }
  .designation { font-size: 12pt; font-weight: bold; }
  p { margin: 1.5mm 0; text-align: justify; }
  table { width: 100%; border-collapse: collapse; margin: 1.5mm 0 3mm; }
  thead { display: table-header-group; }
  tr { page-break-inside: avoid; }
  th, td { border: 0.25mm solid #000; padding: 0.6mm 1.2mm; text-align: left; vertical-align: top; font-size: 8pt; }
  th { background: #eee; }
  td.code { white-space: nowrap; }
  ol { margin: 1.5mm 0 1.5mm 6mm; padding: 0; }
`;

function stoSectionHtml(section: StoSection, level: number): string {
  const tag = level === 0 ? "h2" : "h3";
  const head = `<${tag}>${esc(section.code)} ${esc(cleanCodes(section.title))}</${tag}>` + (section.scope ? `<p>${esc(section.scope)}</p>` : "");
  const table = section.rows.length ?
    `<table><colgroup><col width="9%"><col width="22%"><col width="27%"><col width="10%"><col width="15%"><col width="17%"></colgroup>` +
    `<thead><tr><th>Код</th><th>Дефект</th><th>Признак / критерий отбраковки</th><th>Класс</th><th>Метод контроля</th><th>Действие</th></tr></thead><tbody>` +
    section.rows.map((row) => `<tr><td class="code">${esc(row.code)}</td><td>${esc(row.defect)}</td><td>${esc(row.criterion)}</td><td>${esc(row.class)}</td><td>${esc(row.method)}</td><td>${esc(row.action)}</td></tr>`).join("") +
    `</tbody></table>` : "";
  return head + table + (section.subsections ?? []).map((sub) => stoSectionHtml(sub, level + 1)).join("");
}

export function buildStoHtml(sto: Sto): string {
  const intro = sto.intro;
  const body = `<div class="title"><div class="designation">${esc(sto.designation)}</div><h1>${esc(sto.title)}</h1>` +
    `<div>Редакция ${esc(sto.revision)} · ${esc(sto.date)}</div></div>` +
    `<h2>1 Область применения</h2>${intro.scope.map((paragraph) => `<p>${esc(paragraph)}</p>`).join("")}` +
    `<h2>2 Нормативные ссылки</h2>${intro.references.map((reference) => `<p>${esc(reference)}</p>`).join("")}` +
    `<h2>3 Классы дефектов</h2><table><thead><tr><th>Класс</th><th>Значение</th><th>Для изделия по РВ</th></tr></thead><tbody>` +
    intro.classes.map((item) => `<tr><td>${esc(item.class)}</td><td>${esc(item.meaning)}</td><td>${esc(item.rv)}</td></tr>`).join("") + `</tbody></table>` +
    (intro.classes_note ? `<p>${esc(intro.classes_note)}</p>` : "") +
    `<h2>4 Типовые дефекты, признаки отбраковки и методы контроля</h2>${sto.sections.map((section) => stoSectionHtml(section, 1)).join("")}` +
    `<h2>5 Действия при выявлении дефекта</h2><ol>${sto.actions.map((action) => `<li>${esc(action)}</li>`).join("")}</ol>` +
    `<h2>Лист регистрации изменений</h2><table><thead><tr><th>Изм.</th><th>Извещение</th><th>Изменённые разделы</th><th>Дата</th><th>Подпись</th></tr></thead><tbody>` +
    sto.changes.map((change) => `<tr><td>${esc(change.change)}</td><td>${esc(change.notice)}</td><td>${esc(change.sections)}</td><td>${esc(change.date)}</td><td>${esc(change.signature)}</td></tr>`).join("") + `</tbody></table>`;
  return `<!doctype html><html><head><meta charset="utf-8"><title>${esc(`${sto.designation} ${sto.title}`)}</title><style>${CSS}</style></head><body>${body}</body></html>`;
}
