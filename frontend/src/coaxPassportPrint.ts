// «Паспорт кабельной сборки» (ОТК): изделие, серийный номер, ЭТК, кабель и разъёмы, норма с основанием и результаты
// проверок ОТК. Данные — ответ сервера `.../serials/{n}/passport`. Проверки идут списком `checks`: КСВН по ВАЦ — первая,
// другие разделы ОТК добавляются сюда же (`CHECK_RENDERERS`), а проверка неизвестного вида печатается общей строкой.
// Печать — тот же скрытый iframe, что и у остальных бланков (print.ts → openPrintHtml): бланк чёрно-белый, на A4.
import type { CableAssemblyPassport, PassportCheck, PassportEnd } from "./api";
import { formatDate, formatDay, formatNumber } from "./format";
import { openPrintHtml } from "./print";
import { vswrChartSvg } from "./VswrChart";

function escapeHtml(value: string | number | null | undefined): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

const DASH = "—";
const num = (value: number | null | undefined, digits = 2) => (value === null || value === undefined ? DASH : formatNumber(value, { digits }));

export const PASSPORT_CSS = `
  @page { size: A4 portrait; margin: 14mm; }
  body { margin: 0; color: #000; background: #fff; font-family: Arial, Helvetica, sans-serif; font-size: 10.5pt; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  h1 { font-size: 17pt; margin: 0 0 2mm; }
  h2 { font-size: 11.5pt; margin: 5mm 0 1.5mm; padding-bottom: 0.6mm; border-bottom: 1.5px solid #000; }
  .lead { margin: 0 0 3mm; font-size: 11pt; }
  table { width: 100%; border-collapse: collapse; }
  th, td { border: 1px solid #000; padding: 1.2mm 2mm; text-align: left; vertical-align: top; font-weight: normal; }
  th { background: #e6e6e6; font-weight: bold; }
  .facts th { width: 38%; background: #f2f2f2; font-weight: normal; }
  .facts td { font-weight: bold; }
  .check { page-break-inside: avoid; }
  .verdict { font-size: 13pt; font-weight: bold; }
  .verdict.fail { text-decoration: underline; }
  .chart { margin: 2mm 0; page-break-inside: avoid; }
  .chart svg { width: 100%; height: auto; }
  .sign { margin-top: 12mm; page-break-inside: avoid; }
  .sign table td { height: 11mm; vertical-align: bottom; }
  .sign .line { border: 0; border-bottom: 1px solid #000; width: 45mm; }
  .sign .plain { border: 0; }
`;

function factsTable(rows: [string, string][]): string {
  return `<table class="facts"><tbody>${rows.map(([name, value]) => `<tr><th>${escapeHtml(name)}</th><td>${escapeHtml(value || DASH)}</td></tr>`).join("")}</tbody></table>`;
}

function endsTable(ends: PassportEnd[]): string {
  if (!ends.length) return "";
  const rows = ends.map((end) => `<tr><td>${escapeHtml(end.side ?? DASH)}</td><td>${escapeHtml(end.article ?? DASH)}</td>` +
    `<td>${escapeHtml(end.vswr_text || (end.vswr !== null ? `≤ ${num(end.vswr)}` : DASH))}</td>` +
    `<td>${escapeHtml(end.max_ghz !== null ? `до ${num(end.max_ghz, 3)} ГГц` : DASH)}</td></tr>`).join("");
  return `<table><thead><tr><th>Конец</th><th>Разъём</th><th>КСВ разъёма</th><th>Рабочая частота</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function instrumentRows(check: PassportCheck): string {
  const item = check.instrument;
  if (!item) return `<tr><th>Средство измерения</th><td>${DASH}</td></tr>`;
  return `<tr><th>Средство измерения</th><td>${escapeHtml(`${item.name}, ${item.instrument_type}, инв. № ${item.inventory_number}`)}</td></tr>` +
    `<tr><th>Поверка</th><td>${escapeHtml(`свидетельство № ${item.verification_number} от ${formatDay(item.verified_at)}, действительно до ${formatDay(item.verification_due)}`)}</td></tr>`;
}

function verdictHtml(check: PassportCheck): string {
  return `<span class="verdict${check.verdict === "fail" ? " fail" : ""}">${escapeHtml(check.verdict_name)}</span>`;
}

function inspectorRows(check: PassportCheck): string {
  return `<tr><th>Контролёр ОТК</th><td>${escapeHtml(check.inspector.name)}</td></tr>` +
    `<tr><th>Дата и время проверки</th><td>${escapeHtml(formatDate(check.inspected_at))}</td></tr>`;
}

function vswrCheck(check: PassportCheck, passport: CableAssemblyPassport): string {
  const measurement = check.measurement;
  const band = measurement?.band_mhz ?? [null, null];
  const normMax = passport.norm.status === "ok" ? passport.norm.vswr_max : measurement?.norm_vswr_max ?? null;
  const rows = measurement
    ? `<tr><th>Максимальный КСВН</th><td>${num(measurement.max_vswr)} на частоте ${num(measurement.max_vswr_mhz, 1)} МГц</td></tr>` +
      `<tr><th>Полоса замера</th><td>${num(band[0], 1)} … ${num(band[1], 1)} МГц, точек ${measurement.points}</td></tr>` +
      `<tr><th>Попытка</th><td>${measurement.attempt} (всего ${check.attempts})</td></tr>` +
      `<tr><th>Файл замера</th><td>${escapeHtml(measurement.file_name)}</td></tr>` +
      (measurement.verdict_manual ? `<tr><th>Вердикт назначен вручную</th><td>${escapeHtml(measurement.verdict_reason)}</td></tr>` : "")
    : "";
  const chart = measurement?.trace?.length
    ? `<div class="chart">${vswrChartSvg({ trace: measurement.trace, normMax, band: [band[0], band[1]], maxVswr: measurement.max_vswr, maxMhz: measurement.max_vswr_mhz })}</div>`
    : "";
  return `<table class="facts"><tbody>${rows}<tr><th>Результат</th><td>${verdictHtml(check)}</td></tr>${instrumentRows(check)}${inspectorRows(check)}</tbody></table>${chart}`;
}

function genericCheck(check: PassportCheck): string {
  return `<table class="facts"><tbody><tr><th>Результат</th><td>${verdictHtml(check)}</td></tr>${instrumentRows(check)}${inspectorRows(check)}</tbody></table>`;
}

/** Изделие вне выборки «1 из N» без своего замера: КСВН подтверждает выборка партии — её изделия и последние вердикты. */
function vswrSampleSection(sample: NonNullable<CableAssemblyPassport["vswr_sample"]>): string {
  const lead = `КСВН: контроль выборочный 1 из ${sample.every} — изделие в выборку не входило.`;
  const rows = sample.units.map((unit) => `<tr><td>${unit.position}</td><td>${escapeHtml(unit.serial_number ?? DASH)}</td>` +
    `<td>${escapeHtml(unit.verdict_name ?? "не измерено")}</td><td>${unit.max_vswr !== null ? num(unit.max_vswr) : DASH}</td></tr>`).join("");
  return `<section class="check"><h2>КСВН по ВАЦ</h2><p>${escapeHtml(lead)} Выборка партии:</p>` +
    `<table><thead><tr><th>№ в партии</th><th>Серийный номер</th><th>Результат</th><th>Макс. КСВН</th></tr></thead><tbody>${rows}</tbody></table></section>`;
}

/** Раздел паспорта по виду проверки; новый вид проверки ОТК — новая запись здесь. */
const CHECK_RENDERERS: Record<string, (check: PassportCheck, passport: CableAssemblyPassport) => string> = { vswr: vswrCheck };

export function buildCoaxPassportHtml(passport: CableAssemblyPassport): string {
  const { product, etk, norm } = passport;
  const title = `Паспорт кабельной сборки ${product.serial_number ?? ""}`.trim();
  const customer = norm.status === "ok" && norm.source === "customer";
  const normText = norm.status === "ok" ? `КСВН не более ${num(norm.vswr_max)}${customer ? " (норма заказчика)" : ""}` : `не определена — ${norm.reason || "причина не указана"}`;
  const etkText = etk.techcard_id ? `${etk.item_index}, ревизия ${etk.revision ?? DASH}` : "ЭТК не найдена";
  const productRows: [string, string][] = [
    ["Изделие", product.name],
    ["Серийный номер", product.serial_number ?? ""],
    ["Задание", product.task_number],
    ["Проект", `${product.project}${product.item ? ` / ${product.item}` : ""}`],
    ["ЭТК и ревизия", etkText],
    ["Кабель", product.cable.name ? `${product.cable.name}${product.cable.loss_db_m !== null ? `, затухание ${num(product.cable.loss_db_m)} дБ/м` : ""}` : ""],
    ["Длина, м", product.length_m !== null ? num(product.length_m, 3) : ""],
  ];
  const normRows: [string, string][] = [
    ["Норма", normText],
    ["Основание нормы", norm.basis],
  ];
  if (norm.band_mhz && norm.band_mhz[0] !== null) normRows.push(["Полоса нормы, МГц", `${num(norm.band_mhz[0], 1)} … ${num(norm.band_mhz[1], 1)}`]);
  if (customer && norm.components) {
    const components = norm.components;
    normRows.push(["Норма по комплектующим (справочно)", components.status === "ok" && components.vswr_max !== null
      ? `КСВН не более ${num(components.vswr_max)}` : `не определена${components.reason ? ` — ${components.reason}` : ""}`]);
  }
  if (passport.control?.mode === "sample") normRows.push(["Контроль КСВН", `выборочный, 1 из ${passport.control.every} (первое изделие партии и каждое ${passport.control.every}-е)`]);
  const checks = passport.checks.length
    ? passport.checks.map((check) => `<section class="check"><h2>${escapeHtml(check.title)}</h2>${(CHECK_RENDERERS[check.kind] ?? genericCheck)(check, passport)}</section>`).join("")
    : passport.vswr_sample ? vswrSampleSection(passport.vswr_sample) : `<p>Проверки ОТК по изделию ещё не проводились.</p>`;
  const overall = passport.verdict === null ? DASH : passport.verdict === "pass" ? "Годно" : "Не годно";
  const inspector = passport.inspector?.name ?? "";
  return `<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtml(title)}</title><style>${PASSPORT_CSS}</style></head><body>` +
    `<h1>Паспорт кабельной сборки</h1>` +
    `<p class="lead">${escapeHtml(product.name)}, серийный номер <b>${escapeHtml(product.serial_number ?? DASH)}</b></p>` +
    `<h2>Изделие</h2>${factsTable(productRows)}${endsTable(product.ends)}` +
    `<h2>Норма</h2>${factsTable(normRows)}` +
    checks +
    `<h2>Заключение ОТК</h2>${factsTable([["Заключение", overall], ["Контролёр ОТК", inspector], ["Дата", passport.inspected_at ? formatDate(passport.inspected_at) : ""]])}` +
    `<div class="sign"><table><tbody><tr><td class="plain">Контролёр ОТК</td><td class="line"></td><td class="plain">/ ${escapeHtml(inspector || "______________")} /</td><td class="plain">Дата: ____________</td></tr></tbody></table></div>` +
    `</body></html>`;
}

export function printCoaxPassport(passport: CableAssemblyPassport): void {
  openPrintHtml(buildCoaxPassportHtml(passport));
}
