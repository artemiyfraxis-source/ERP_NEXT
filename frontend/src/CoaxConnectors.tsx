import { useMemo, useState, type ReactNode } from "react";
import { PageWindow } from "./PageWindow";
import { DataTable, type DataColumn, type DataPreset } from "./DataTable";
import type { TechOperationRecord } from "./api";
import { fieldCellEdit, ownCellEdit } from "./CustomColumn";
import "./coaxConnectors.css";

/* «База данных → Коаксиальные разъёмы» (owner 13.10.2026): БД.КОАКС — разъёмы базы и 311 разъёмов gsm-repiteri.ru —
   одной плотной таблицей по типам (SMA-female, SMA-male, RP-SMA-…, N…), строка = разъём × кабель, как в листе. Клик по строке — карточка
   разъёма окном поверх таблицы (владелец 14.10: правка и карточки — окнами, не панелью сбоку): тип, подходящий кабель, разделка, пин и экран, параметры сайта, ссылка, другие кабели того же
   артикула; правка — кнопкой в карточке. D1–D3 в этой таблице нет (владелец 17.10): они у кабеля, вкладка «Коаксиальные кабели».
   HEX обжима — сразу после L1–L3; ссылка на карточку товара и цена поставщика — своими колонками (владелец 17.10).
   Ячейки с простым текстом и числами (тип, монтаж, пин, экран, L1–L3, производитель…) правятся прямо в таблице тем же
   путём, что окно записи; артикул и кабель (ключ строки), пол, исполнение и поставщик (выводятся) — только в окне. */

type Fields = Record<string, string>;

const pick = (fields: Fields, ...names: string[]) => {
  for (const name of names) {
    const value = String(fields[name] ?? "").trim();
    if (value) return value;
  }
  return "";
};

export const coaxArticle = (fields: Fields) => pick(fields, "coaxArticle", "Артикул");
export const coaxType = (fields: Fields) => pick(fields, "coaxType", "Тип/Серия");
export const coaxWire = (fields: Fields) => pick(fields, "coaxWire", "Провод");

// Серии в порядке, в каком цех их чаще берёт; остальные — по алфавиту после них, «Без серии» — последней.
const SERIES_ORDER = ["SMA", "N", "TNC", "BNC", "UHF", "mini-UHF", "F", "QMA", "FME", "MCX", "MMCX", "SMB", "SMP",
  "U.FL", "MHF1", "Fakra", "TS9", "CRC9", "7/16 DIN", "4.3-10"];
const SERIES_ALIASES: Record<string, string> = { fakra: "Fakra", din: "7/16 DIN", "7/16 din": "7/16 DIN", ipx: "U.FL" };
export const NO_SERIES = "Без серии";

/** Серия разъёма: колонка «Серия», иначе из «Тип/Серия» («RP-SMA-male R/A» → SMA, «N-female flange» → N). */
export function coaxSeries(fields: Fields): string {
  const raw = pick(fields, "Серия") || coaxType(fields);
  if (!raw) return NO_SERIES;
  const base = raw.replace(/^RP[-\s]?/i, "").split(/[-\s](?:male|female|plug|jack)\b/i)[0].replace(/\s*R\/A$/i, "").trim();
  const known = SERIES_ORDER.find((series) => series.toLowerCase() === base.toLowerCase());
  return SERIES_ALIASES[base.toLowerCase()] ?? known ?? (base || NO_SERIES);
}

function seriesRank(series: string) {
  const index = SERIES_ORDER.indexOf(series);
  return series === NO_SERIES ? 10_000 : index >= 0 ? index : 1_000;
}

/** Штекер / гнездо: колонка «Пол», иначе по типу (female проверяется раньше male). */
export function coaxGender(fields: Fields): string {
  const given = pick(fields, "Пол");
  if (given) return given;
  const type = coaxType(fields).toLowerCase();
  if (/female|jack|гнезд/.test(type)) return "гнездо";
  if (/male|plug|штек/.test(type)) return "штекер";
  return "";
}

/** Прямой / угловой: колонка «Исполнение», иначе «R/A» в типе или артикуле. */
export function coaxAngle(fields: Fields): string {
  const given = pick(fields, "Исполнение");
  if (given) return given;
  return /r\/a|угл/i.test(`${coaxType(fields)} ${coaxArticle(fields)}`) ? "угловой" : "прямой";
}

/** Поставщик разъёма: своё поле, иначе домен страницы на сайте поставщика или из «Источник данных» («gsm-repiteri.ru, 12.10.2026»). */
export function coaxSupplier(fields: Fields): string {
  const given = pick(fields, "Поставщик", "supplier", "Сайт");
  if (given) return given;
  const link = pick(fields, "Ссылка");
  if (link) {
    try { return new URL(link).hostname.replace(/^www\./, ""); } catch { /* не адрес — ниже источник */ }
  }
  const source = pick(fields, "Источник данных").split(",")[0].trim();
  return /^[\w-]+(\.[\w-]+)+$/.test(source) ? source : "";
}
export const coaxMaker = (fields: Fields) => pick(fields, "coaxMfr", "Производитель");

const joinDots = (values: string[]) => (values.some(Boolean) ? values.map((value) => value || "—").join(" · ") : "");
const stripping = (fields: Fields) => joinDots([pick(fields, "L1", "l1"), pick(fields, "L2", "l2"), pick(fields, "L3", "l3")]);

const reversed = (fields: Fields) => /^RP[-\s]?/i.test(coaxType(fields));
const GENDER_WORDS = ["female", "male", "plug", "jack"];
const genderWord = (fields: Fields) => coaxType(fields).match(/(female|male|plug|jack)/i)?.[1].toLowerCase() ?? "";

// Исполнения, которые идут своей группой за прямыми, в этом порядке.
const VARIANTS = ["bulkhead", "flange", "R/A"];
function coaxVariants(fields: Fields): string[] {
  const type = coaxType(fields).toLowerCase();
  return VARIANTS.filter((item) => (item === "R/A" ? coaxAngle(fields) === "угловой" : type.includes(item)));
}
const variantRank = (fields: Fields) => coaxVariants(fields).reduce((rank, item) => rank + (1 << VARIANTS.indexOf(item)), 0);

/** Группа таблицы — тип разъёма (владелец 19.10: «сортировка по типу SMA-female и т. д.»): серия, полярность и пол —
    «SMA-female», «RP-SMA-male», «Fakra-female»; проходные, фланцевые и угловые — своими группами следом за прямыми
    («SMA-female bulkhead», «SMA-female flange», «SMA-male R/A», владелец 19.10). */
export function coaxTypeGroup(fields: Fields): string {
  const series = coaxSeries(fields);
  if (series === NO_SERIES) return NO_SERIES;
  const word = genderWord(fields);
  return `${reversed(fields) ? "RP-" : ""}${series}${word ? `-${word}` : ""}${coaxVariants(fields).map((item) => ` ${item}`).join("")}`;
}

// Серия (порядок цеха) → обычная полярность раньше RP → female, male → прямые, проходные, фланцевые, угловые → полный тип →
// артикул → кабель.
function sortRows(records: TechOperationRecord[]) {
  const genderRank = (fields: Fields) => { const at = GENDER_WORDS.indexOf(genderWord(fields)); return at < 0 ? GENDER_WORDS.length : at; };
  return [...records].sort((a, b) => {
    const sa = coaxSeries(a.fields);
    const sb = coaxSeries(b.fields);
    return seriesRank(sa) - seriesRank(sb) || sa.localeCompare(sb, "ru")
      || Number(reversed(a.fields)) - Number(reversed(b.fields))
      || genderRank(a.fields) - genderRank(b.fields)
      || variantRank(a.fields) - variantRank(b.fields)
      || coaxType(a.fields).localeCompare(coaxType(b.fields), "ru")
      || coaxArticle(a.fields).localeCompare(coaxArticle(b.fields), "ru", { numeric: true })
      || coaxWire(a.fields).localeCompare(coaxWire(b.fields), "ru", { numeric: true });
  });
}

const PRESETS: DataPreset<TechOperationRecord>[] = [
  { label: "Штекеры", test: (record) => coaxGender(record.fields) === "штекер" },
  { label: "Гнёзда", test: (record) => coaxGender(record.fields) === "гнездо" },
  { label: "Угловые", test: (record) => coaxAngle(record.fields) === "угловой" },
  { label: "Без L1–L3", test: (record) => !stripping(record.fields) },
];

// Поля, у которых в таблице своя колонка: такой же «свой столбец» («+ Столбец») второй раз не показывается.
const BUILT_IN = new Set(["Артикул поставщика", "HEX обжима, мм", "Ссылка", "Цена, ₽", "Дата цены", "Монтаж", "Производитель",
  "Волновое сопротивление, Ом", "Частота до, ГГц", "КСВ", "Наименование", "Источник данных", "Тип пина", "Тип экрана"].map((name) => name.toLowerCase()));

/** Ссылка на карточку товара: видно сайт, полный адрес — в подсказке; клик открывает карточку, строку не выбирает. */
function SiteLink({ href }: { href: string }) {
  if (!/^https?:\/\//i.test(href)) return <>{href || "—"}</>;
  let host = href;
  try { host = new URL(href).hostname.replace(/^www\./, ""); } catch { /* не адрес — как есть */ }
  return <a href={href} target="_blank" rel="noreferrer" title={href} onClick={(event) => event.stopPropagation()}>{host}</a>;
}

export function CoaxConnectors({ records, canEdit, onEdit, toolbar, ownColumns = [], onEdited, removeColumn }: {
  records: TechOperationRecord[];
  canEdit: boolean;
  onEdit: (record: TechOperationRecord) => void;
  toolbar?: ReactNode;
  /** Столбцы, добавленные владельцем («+ Столбец»): видны сразу, правятся в ячейках. */
  ownColumns?: string[];
  onEdited?: () => Promise<unknown> | void;
  /** «Удалить столбец» своего столбца (окно подтверждения — у страницы). */
  removeColumn?: (name: string) => (() => void) | undefined;
}) {
  const rows = useMemo(() => sortRows(records), [records]);
  const [selectedId, setSelectedId] = useState("");
  const selected = rows.find((record) => record.id === selectedId) ?? null;

  const text = (name: string, ...more: string[]) => (record: TechOperationRecord) => pick(record.fields, name, ...more);
  // Ячейка пишет колонку листа (сервер переносит её в ключ схемы, который читает генератор); нет колонки у записи — нет правки.
  const cell = (name: string) => canEdit ? fieldCellEdit<TechOperationRecord>(name, (record) => record) : undefined;
  // Поле карточки (частота, КСВ) правится и у строки, где его ещё нет (сервер принимает поля карточки БД.КОАКС).
  const cardCell = (name: string) => canEdit ? fieldCellEdit<TechOperationRecord>(name, (record) => record, true) : undefined;
  const length = (name: "L1" | "L2" | "L3"): DataColumn<TechOperationRecord> => ({
    key: name, title: name, hint: `Разделка ${name}, мм`, numeric: true, value: text(name, name.toLowerCase()), edit: cell(name),
  });
  const columns: DataColumn<TechOperationRecord>[] = [
    // Длинные артикулы, типы и кабели («1/2″ SuperFlex (⌀13,5 мм)») переносятся, а не раздвигают таблицу за край ноутбука.
    { key: "article", title: "Артикул", value: (record) => coaxArticle(record.fields), render: (record) => <strong>{coaxArticle(record.fields) || "—"}</strong>, wrap: true, maxWidth: 170 },
    { key: "type", title: "Тип", value: (record) => coaxType(record.fields), wrap: true, maxWidth: 130, filter: true, edit: cell("Тип/Серия") },
    { key: "wire", title: "Кабель", value: (record) => coaxWire(record.fields), wrap: true, maxWidth: 100 },
    { key: "gender", title: "Пол", value: (record) => coaxGender(record.fields), hidden: true },
    { key: "angle", title: "Исполнение", value: (record) => coaxAngle(record.fields) },
    // Монтаж — в карточке и в «Колонках»; карточка открывается окном, таблица занимает всю ширину.
    { key: "mount", title: "Монтаж", value: text("Монтаж"), hidden: true, edit: cell("Монтаж") },
    { key: "pin", title: "Тип пина", value: text("Тип пина", "pinType"), wrap: true, maxWidth: 80, edit: cell("Тип пина") },
    { key: "shield", title: "Тип экрана", value: text("Тип экрана", "shieldType"), edit: cell("Тип экрана") },
    // Разделка — тремя колонками: каждую длину можно вписать прямо в таблицу; HEX обжима втулки — сразу за ними.
    length("L1"), length("L2"), length("L3"),
    { key: "hex", title: "HEX, мм", hint: "HEX обжима втулки экрана, мм", numeric: true, value: text("HEX обжима, мм"), edit: cell("HEX обжима, мм") },
    // Частотный диапазон и КСВ из карточки — сразу за HEX (владелец 19.10).
    { key: "ghz", title: "Частота, ГГц", hint: "Рабочая частота до, ГГц — из карточки разъёма", numeric: true, value: text("Частота до, ГГц"),
      render: (record) => pick(record.fields, "Частота до, ГГц") ? `до ${pick(record.fields, "Частота до, ГГц")}` : "—", edit: cardCell("Частота до, ГГц") },
    { key: "vswr", title: "КСВ", hint: "КСВН по карточке поставщика", value: text("КСВ"), wrap: true, maxWidth: 140, edit: cardCell("КСВ") },
    { key: "mfr", title: "Производитель", value: (record) => coaxMaker(record.fields), render: (record) => coaxMaker(record.fields) || "—", wrap: true, maxWidth: 120, edit: cell("Производитель") },
    { key: "supplier", title: "Поставщик", value: (record) => coaxSupplier(record.fields), render: (record) => coaxSupplier(record.fields) || "—", wrap: true, maxWidth: 130 },
    { key: "vendorArticle", title: "Артикул поставщика", value: text("Артикул поставщика"), wrap: true, maxWidth: 160, edit: cell("Артикул поставщика") },
    { key: "link", title: "Карточка на сайте", value: text("Ссылка"), render: (record) => <SiteLink href={pick(record.fields, "Ссылка")} />, edit: cell("Ссылка") },
    {
      key: "price", title: "Цена, ₽", numeric: true, value: text("Цена, ₽"), edit: cell("Цена, ₽"),
      render: (record) => pick(record.fields, "Цена, ₽")
        ? <span title={pick(record.fields, "Дата цены") ? `Цена на ${pick(record.fields, "Дата цены")}` : undefined}>{pick(record.fields, "Цена, ₽")}</span> : "—",
    },
    { key: "priceDate", title: "Дата цены", value: text("Дата цены"), hidden: true, edit: cell("Дата цены") },
    { key: "ohm", title: "Ом", value: text("Волновое сопротивление, Ом"), numeric: true, hidden: true, edit: cell("Волновое сопротивление, Ом") },
    { key: "name", title: "Наименование", value: text("Наименование"), hidden: true, edit: cell("Наименование") },
    { key: "source", title: "Источник данных", value: text("Источник данных"), hidden: true, edit: cell("Источник данных") },
    ...ownColumns.filter((name) => !BUILT_IN.has(name.trim().toLowerCase())).map((name): DataColumn<TechOperationRecord> => ({
      key: `own:${name}`, title: name, value: text(name), wrap: true, maxWidth: 140,
      edit: canEdit ? ownCellEdit<TechOperationRecord>(name, (record) => record) : undefined,
      remove: removeColumn?.(name),
    })),
  ];

  return <div className="coax-db">
    <div className="coax-db-table">
      <DataTable
        label="Коаксиальные разъёмы"
        rows={rows}
        columns={columns}
        rowKey={(record) => record.id}
        storageKey="db:coax-connectors"
        exportName="Коаксиальные разъёмы"
        presets={PRESETS}
        groupBy={(record) => coaxTypeGroup(record.fields)}
        onRowClick={(record) => setSelectedId(record.id === selectedId ? "" : record.id)}
        rowTitle={(record) => `Карточка: ${coaxArticle(record.fields)} · ${coaxWire(record.fields)}`}
        rowClassName={(record) => (record.id === selectedId ? "coax-row-active" : "")}
        emptyText="Разъёмов нет — загрузите их или добавьте «+ Запись»."
        pageSize={1000}
        onEdited={onEdited}
        toolbar={toolbar}
      />
    </div>
    {selected && <CoaxCard
      record={selected}
      siblings={rows.filter((record) => record.id !== selected.id && coaxArticle(record.fields) === coaxArticle(selected.fields))}
      canEdit={canEdit}
      onEdit={() => { setSelectedId(""); onEdit(selected); }}
      onSelect={(record) => setSelectedId(record.id)}
      onClose={() => setSelectedId("")}
    />}
  </div>;
}

// Карточка: строки «название — значение»; пустое не показывается, кроме того, что заполняет технолог (разделка,
// пин, экран) — там «не задано», чтобы было видно, чего не хватает генератору.
const CARD_ROWS: Array<[string, (fields: Fields) => string, boolean?]> = [
  ["Серия", coaxSeries],
  ["Тип", coaxType],
  ["Пол", coaxGender],
  ["Исполнение", coaxAngle],
  ["Полярность", (fields) => pick(fields, "Полярность")],
  ["Разделка L1 · L2 · L3", stripping, true],
  ["L+ · L−", (fields) => joinDots([pick(fields, "L+", "lPlus"), pick(fields, "L-", "L−", "lMinus")])],
  ["HEX обжима", (fields) => (pick(fields, "HEX обжима, мм") ? `${pick(fields, "HEX обжима, мм")} мм` : "")],
  ["Тип пина", (fields) => pick(fields, "Тип пина", "pinType"), true],
  ["Тип экрана", (fields) => pick(fields, "Тип экрана", "shieldType"), true],
  ["Монтаж", (fields) => pick(fields, "Монтаж")],
  ["Центральный контакт", (fields) => pick(fields, "Центральный контакт")],
  ["Крепление экрана", (fields) => pick(fields, "Крепление экрана")],
  ["Волновое сопротивление", (fields) => (pick(fields, "Волновое сопротивление, Ом") ? `${pick(fields, "Волновое сопротивление, Ом")} Ом` : "")],
  ["Частота до", (fields) => (pick(fields, "Частота до, ГГц") ? `${pick(fields, "Частота до, ГГц")} ГГц` : "")],
  ["КСВ", (fields) => pick(fields, "КСВ")],
  ["Материал корпуса", (fields) => pick(fields, "Материал корпуса")],
  ["Покрытие", (fields) => pick(fields, "Покрытие")],
  ["Габариты", (fields) => pick(fields, "Габариты")],
  ["Производитель", coaxMaker],
  ["Поставщик", coaxSupplier],
  ["Артикул поставщика", (fields) => pick(fields, "Артикул поставщика")],
  ["Цена", (fields) => (pick(fields, "Цена, ₽") ? `${pick(fields, "Цена, ₽")} ₽${pick(fields, "Дата цены") ? ` на ${pick(fields, "Дата цены")}` : ""}` : "")],
  ["Программа", (fields) => pick(fields, "program", "Программа")],
  ["Примечание", (fields) => pick(fields, "Примечание")],
  ["Источник данных", (fields) => pick(fields, "Источник данных")],
];

function CoaxCard({ record, siblings, canEdit, onEdit, onSelect, onClose }: {
  record: TechOperationRecord;
  siblings: TechOperationRecord[];
  canEdit: boolean;
  onEdit: () => void;
  onSelect: (record: TechOperationRecord) => void;
  onClose: () => void;
}) {
  const fields = record.fields;
  const wire = coaxWire(fields);
  const name = pick(fields, "Наименование");
  const link = pick(fields, "Ссылка");
  return <PageWindow label={`Карточка разъёма ${coaxArticle(fields)}`} width={560} dismissable close={onClose}><div className="panel coax-card">
    <div className="coax-card-head">
      <h2>{coaxArticle(fields) || "Без артикула"}</h2>
      <button type="button" className="secondary small" onClick={onClose} aria-label="Закрыть карточку">Закрыть</button>
    </div>
    {name && <p className="coax-card-name">{name}</p>}
    <dl className="coax-card-grid">
      <dt>Кабель</dt>
      <dd><strong>{wire || "—"}</strong></dd>
      {CARD_ROWS.map(([label, value, required]) => {
        const shown = value(fields);
        if (!shown && !required) return null;
        return [<dt key={`t-${label}`}>{label}</dt>,
          <dd key={`v-${label}`}>{shown || <span className="coax-missing">не задано</span>}</dd>];
      })}
    </dl>
    {siblings.length > 0 && <div className="coax-card-siblings">
      <span>Тот же разъём на других кабелях</span>
      <div>{siblings.map((other) => <button key={other.id} type="button" className="secondary small" onClick={() => onSelect(other)}>{coaxWire(other.fields) || "—"}</button>)}</div>
    </div>}
    <div className="coax-card-actions">
      {link && <a href={link} target="_blank" rel="noreferrer">Страница на сайте поставщика</a>}
      {canEdit && <button type="button" className="primary small" onClick={onEdit}>Править запись</button>}
    </div>
  </div></PageWindow>;
}
