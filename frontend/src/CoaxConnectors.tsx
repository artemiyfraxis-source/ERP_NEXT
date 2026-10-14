import { useMemo, useState, type ReactNode } from "react";
import { PageWindow } from "./PageWindow";
import { DataTable, type DataColumn, type DataPreset } from "./DataTable";
import type { TechOperationRecord } from "./api";
import "./coaxConnectors.css";

/* «База данных → Коаксиальные разъёмы» (owner 13.10.2026): БД.КОАКС — разъёмы базы и 311 разъёмов gsm-repiteri.ru —
   одной плотной таблицей по сериям (SMA, N, TNC…), строка = разъём × кабель, как в листе. Клик по строке — карточка
   разъёма окном поверх таблицы (владелец 14.10: правка и карточки — окнами, не панелью сбоку): тип, кабель с его D1–D3, разделка, пин и экран, параметры сайта, ссылка, другие кабели того же
   артикула; правка — кнопкой в карточке. D1–D3 — у кабеля (вкладка «Коаксиальные кабели»), не у разъёма. */

type Fields = Record<string, string>;
type CableDia = (record: TechOperationRecord) => Fields | undefined;

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
const cableDia = (dia: Fields | undefined) => (dia ? joinDots([dia.D1 ?? "", dia.D2 ?? "", dia.D3 ?? ""]) : "");

// Внутри серии: тип без «RP-», обычная полярность раньше обратной (SMA-female, затем RP-SMA-female).
const typeOrder = (fields: Fields) => coaxType(fields).replace(/^RP[-\s]?/i, "") + (/^RP/i.test(coaxType(fields)) ? " ~RP" : "");

function sortRows(records: TechOperationRecord[]) {
  return [...records].sort((a, b) => {
    const sa = coaxSeries(a.fields);
    const sb = coaxSeries(b.fields);
    return seriesRank(sa) - seriesRank(sb) || sa.localeCompare(sb, "ru")
      || typeOrder(a.fields).localeCompare(typeOrder(b.fields), "ru")
      || coaxArticle(a.fields).localeCompare(coaxArticle(b.fields), "ru", { numeric: true })
      || coaxWire(a.fields).localeCompare(coaxWire(b.fields), "ru", { numeric: true });
  });
}

const PRESETS: DataPreset<TechOperationRecord>[] = [
  { label: "Штекеры", test: (record) => coaxGender(record.fields) === "штекер" },
  { label: "Гнёзда", test: (record) => coaxGender(record.fields) === "гнездо" },
  { label: "Угловые", test: (record) => coaxAngle(record.fields) === "угловой" },
  { label: "Без разделки", test: (record) => !stripping(record.fields) },
];

export function CoaxConnectors({ records, cableOf, canEdit, onEdit, toolbar }: {
  records: TechOperationRecord[];
  cableOf: CableDia;
  canEdit: boolean;
  onEdit: (record: TechOperationRecord) => void;
  toolbar?: ReactNode;
}) {
  const rows = useMemo(() => sortRows(records), [records]);
  const [selectedId, setSelectedId] = useState("");
  const selected = rows.find((record) => record.id === selectedId) ?? null;

  const text = (name: string, ...more: string[]) => (record: TechOperationRecord) => pick(record.fields, name, ...more);
  const columns: DataColumn<TechOperationRecord>[] = [
    // Длинные артикулы, типы и кабели («1/2″ SuperFlex (⌀13,5 мм)») переносятся, а не раздвигают таблицу за край ноутбука.
    { key: "article", title: "Артикул", value: (record) => coaxArticle(record.fields), render: (record) => <strong>{coaxArticle(record.fields) || "—"}</strong>, wrap: true, maxWidth: 170 },
    { key: "type", title: "Тип", value: (record) => coaxType(record.fields), wrap: true, maxWidth: 130 },
    { key: "wire", title: "Кабель", value: (record) => coaxWire(record.fields), wrap: true, maxWidth: 100 },
    { key: "gender", title: "Пол", value: (record) => coaxGender(record.fields), hidden: true },
    { key: "angle", title: "Исполнение", value: (record) => coaxAngle(record.fields) },
    // Монтаж — в карточке и в «Колонках»; карточка открывается окном, таблица занимает всю ширину.
    { key: "mount", title: "Монтаж", value: text("Монтаж"), hidden: true },
    { key: "pin", title: "Тип пина", value: text("Тип пина", "pinType"), wrap: true, maxWidth: 80 },
    { key: "shield", title: "Тип экрана", value: text("Тип экрана", "shieldType") },
    {
      key: "dia", title: "D1 · D2 · D3 кабеля", value: (record) => cableDia(cableOf(record)),
      render: (record) => cableDia(cableOf(record))
        || <span className="muted" title={`У кабеля «${coaxWire(record.fields)}» нет D1–D3 — задайте их во вкладке «Коаксиальные кабели»`}>нет у кабеля</span>,
    },
    { key: "strip", title: "Разделка L1 · L2 · L3", value: (record) => stripping(record.fields) },
    { key: "mfr", title: "Производитель", value: (record) => coaxMaker(record.fields), render: (record) => coaxMaker(record.fields) || "—", wrap: true, maxWidth: 120 },
    { key: "supplier", title: "Поставщик", value: (record) => coaxSupplier(record.fields), render: (record) => coaxSupplier(record.fields) || "—", wrap: true, maxWidth: 130 },
    { key: "ohm", title: "Ом", value: text("Волновое сопротивление, Ом"), numeric: true, hidden: true },
    { key: "ghz", title: "До, ГГц", value: text("Частота до, ГГц"), numeric: true, hidden: true },
    { key: "name", title: "Наименование", value: text("Наименование"), hidden: true },
    { key: "source", title: "Источник данных", value: text("Источник данных"), hidden: true },
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
        groupBy={(record) => coaxSeries(record.fields)}
        onRowClick={(record) => setSelectedId(record.id === selectedId ? "" : record.id)}
        rowTitle={(record) => `Карточка: ${coaxArticle(record.fields)} · ${coaxWire(record.fields)}`}
        rowClassName={(record) => (record.id === selectedId ? "coax-row-active" : "")}
        emptyText="Разъёмов нет — загрузите их или добавьте «+ Запись»."
        pageSize={1000}
        toolbar={toolbar}
      />
    </div>
    {selected && <CoaxCard
      record={selected}
      siblings={rows.filter((record) => record.id !== selected.id && coaxArticle(record.fields) === coaxArticle(selected.fields))}
      dia={cableDia(cableOf(selected))}
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
  ["Тип пина", (fields) => pick(fields, "Тип пина", "pinType"), true],
  ["Тип экрана", (fields) => pick(fields, "Тип экрана", "shieldType"), true],
  ["Монтаж", (fields) => pick(fields, "Монтаж")],
  ["Центральный контакт", (fields) => pick(fields, "Центральный контакт")],
  ["Крепление экрана", (fields) => pick(fields, "Крепление экрана")],
  ["Волновое сопротивление", (fields) => (pick(fields, "Волновое сопротивление, Ом") ? `${pick(fields, "Волновое сопротивление, Ом")} Ом` : "")],
  ["Частота до", (fields) => (pick(fields, "Частота до, ГГц") ? `${pick(fields, "Частота до, ГГц")} ГГц` : "")],
  ["Материал корпуса", (fields) => pick(fields, "Материал корпуса")],
  ["Покрытие", (fields) => pick(fields, "Покрытие")],
  ["Габариты", (fields) => pick(fields, "Габариты")],
  ["Производитель", coaxMaker],
  ["Поставщик", coaxSupplier],
  ["Программа", (fields) => pick(fields, "program", "Программа")],
  ["Примечание", (fields) => pick(fields, "Примечание")],
  ["Источник данных", (fields) => pick(fields, "Источник данных")],
];

function CoaxCard({ record, siblings, dia, canEdit, onEdit, onSelect, onClose }: {
  record: TechOperationRecord;
  siblings: TechOperationRecord[];
  dia: string;
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
      <dt>D1 · D2 · D3 кабеля</dt>
      <dd>{dia || <span className="coax-missing">нет у кабеля</span>}</dd>
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
