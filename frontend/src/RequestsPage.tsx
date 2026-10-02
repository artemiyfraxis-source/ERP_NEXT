import { useEffect, useRef, useState, type FormEvent } from "react";
import { SuggestInput } from "./SuggestInput";
import { DataTable, type DataColumn } from "./DataTable";
import {
  requestsApi,
  type CalcImportPreview,
  type Principal,
  type RequestRow,
  type RequestSchema,
} from "./api";
import { canAct } from "./navigation";

type Draft = {
  purpose: string;
  project_reference: string;
  delivery_point: string;
  name: string;
  item_type: string;
  design_article: string;
  quantity: string;
  unit: string;
  link: string;
  category: string;
  quality_document: string;
  procurement_deadline: string;
  planned_date: string;
  price: string;
  price_with_vat: string;
  comment: string;
};

/* SUPPLYMAP rcOpenForm RC_BULK_: filled once above the cards and applied to every position. */
const COMMON_KEYS = [
  "purpose",
  "project_reference",
  "delivery_point",
  "category",
  "quality_document",
  "procurement_deadline",
  "planned_date",
] as const;
type CommonKey = (typeof COMMON_KEYS)[number];
type Common = Pick<Draft, CommonKey>;

function dateAfter(days: number) {
  const date = new Date();
  date.setDate(date.getDate() + days);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function emptyCommon(): Common {
  return {
    purpose: "RND",
    project_reference: "",
    delivery_point: "",
    category: "Общепром",
    quality_document: "Нет",
    procurement_deadline: dateAfter(7),
    planned_date: dateAfter(14),
  };
}

function emptyDraft(common: Common = emptyCommon()): Draft {
  return {
    ...common,
    name: "",
    item_type: "Комплектующие",
    design_article: "",
    quantity: "1",
    unit: "шт",
    link: "",
    price: "",
    price_with_vat: "",
    comment: "",
  };
}

/* rcCategoryRule / Schema.gs passportRule_: ОП → «Да» жёстко; ИП → «Да», Общепром → «Нет» по умолчанию. */
function applyCategoryRule<T extends Pick<Draft, "category" | "quality_document">>(value: T): T {
  if (value.category === "ОП") return { ...value, quality_document: "Да" };
  if (value.category === "ИП" && !value.quality_document) return { ...value, quality_document: "Да" };
  if (value.category === "Общепром" && !value.quality_document) return { ...value, quality_document: "Нет" };
  return value;
}

const TITLES = { all: "Заявки", create: "Оформить заявку", mine: "Мои заявки" };

const MINE_PAGE_SIZE = 50;
const HANDOFF_LIMIT = 100;

/* Field labels of the form, in SUPPLYMAP REQUEST_FORM_FIELDS order. Excel headers are recognized by the
   label or by the SUPPLYMAP sheet header (rcNormH); a trailing «*» (required marker on the template) is
   ignored, so a copied template recognizes. */
const IMPORT_FIELDS: Array<{ key: keyof Draft; label: string; aliases: string[]; required: boolean }> = [
  { key: "purpose", label: "Назначение", aliases: [], required: true },
  { key: "project_reference", label: "Цель", aliases: ["Проект", "№ проекта"], required: false },
  { key: "delivery_point", label: "Точка отгрузки", aliases: [], required: true },
  { key: "name", label: "Наименование", aliases: [], required: true },
  { key: "item_type", label: "Тип", aliases: [], required: true },
  { key: "category", label: "Категория", aliases: [], required: false },
  { key: "design_article", label: "Артикул КД", aliases: [], required: false },
  { key: "quantity", label: "Количество", aliases: ["Кол-во"], required: true },
  { key: "unit", label: "Единица", aliases: ["Ед.изм", "Ед. изм."], required: true },
  { key: "link", label: "Ссылка", aliases: [], required: false },
  { key: "quality_document", label: "Документ качества", aliases: ["Док. качества"], required: true },
  { key: "procurement_deadline", label: "Срок закупки", aliases: ["Дедлайн на закупку", "Дедлайн"], required: true },
  { key: "planned_date", label: "Плановая дата", aliases: ["План. дата"], required: true },
  { key: "price", label: "Цена", aliases: [], required: false },
  { key: "price_with_vat", label: "Цена с НДС", aliases: [], required: false },
  { key: "comment", label: "Комментарий", aliases: [], required: false },
];
const LABEL_BY_KEY = Object.fromEntries(IMPORT_FIELDS.map((field) => [field.key, field.label])) as Record<keyof Draft, string>;
const DATE_KEYS = new Set<keyof Draft>(["procurement_deadline", "planned_date"]);

function normalizeHeader(value: string): string {
  return value.replace(/\s*\*+\s*$/, "").replace(/\s+/g, " ").trim().toLowerCase();
}

/* rcDateToISO: «31.12.2026» from Excel → «2026-12-31»; anything else that is not ISO → empty. */
function dateToIso(value: string): string {
  const text = value.trim();
  const match = text.match(/^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$/);
  if (match) return `${match[3]}-${match[2].padStart(2, "0")}-${match[1].padStart(2, "0")}`;
  return /^\d{4}-\d{2}-\d{2}$/.test(text) ? text : "";
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "";
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? `${match[3]}.${match[2]}.${match[1]}` : value;
}

type ExcelParseResult =
  | { rows: Array<Partial<Draft>>; recognized: string[]; unknown: string[] }
  | { error: string };

function parseExcelTsv(text: string): ExcelParseResult {
  const lines = text.replace(/\r/g, "").split("\n");
  while (lines.length && lines[lines.length - 1].trim() === "") lines.pop();
  if (lines.length < 2) return { error: "Нужны строка заголовков и хотя бы одна строка данных." };
  const byNorm = new Map<string, (typeof IMPORT_FIELDS)[number]>();
  for (const field of IMPORT_FIELDS) {
    for (const name of [field.label, ...field.aliases]) byNorm.set(normalizeHeader(name), field);
  }
  const heads = lines[0].split("\t");
  const colField = heads.map((head) => byNorm.get(normalizeHeader(head)) ?? null);
  const recognized: string[] = [];
  const unknown: string[] = [];
  heads.forEach((head, index) => {
    const field = colField[index];
    if (field) recognized.push(field.label);
    else if (head.trim()) unknown.push(head.trim());
  });
  if (!recognized.length) {
    return {
      error:
        "Ни один заголовок не распознан. Названия колонок должны совпадать с полями формы " +
        "(Наименование, Количество, Единица …).",
    };
  }
  const rows: Array<Partial<Draft>> = [];
  for (let r = 1; r < lines.length; r += 1) {
    if (lines[r].trim() === "") continue;
    const cells = lines[r].split("\t");
    const row: Partial<Draft> = {};
    colField.forEach((field, c) => {
      if (!field) return;
      const value = (cells[c] ?? "").trim();
      row[field.key] = DATE_KEYS.has(field.key) ? dateToIso(value) : value;
    });
    rows.push(row);
  }
  return { rows, recognized, unknown };
}

/* rcDownloadTemplate: the header row, required ones marked «*», tab-separated, UTF-16LE with BOM so
   Excel reads Cyrillic without the import wizard. */
const TEMPLATE_HEADERS = IMPORT_FIELDS.map((field) => field.label + (field.required ? " *" : ""));

function templateDataUri(headers: string[]): string {
  const text = "﻿" + headers.join("\t") + "\r\n";
  let binary = "";
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    binary += String.fromCharCode(code & 0xff, (code >> 8) & 0xff);
  }
  return "data:text/csv;charset=utf-16le;base64," + btoa(binary);
}

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* fall back to execCommand */ }
  const area = document.createElement("textarea");
  area.value = text;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(area);
  return ok;
}

/* RQ-5 `parseZpk_`: the ЗКП range pasted as TSV (header row + data rows) → a grid of cell strings. */
function tsvToGrid(text: string): string[][] {
  return text
    .replace(/\r/g, "")
    .split("\n")
    .filter((line) => line.trim() !== "")
    .map((line) => line.split("\t"));
}

function payloadOf(draft: Draft): Record<string, string> {
  return Object.fromEntries(Object.entries(draft).filter(([, value]) => value.trim() !== "")) as Record<string, string>;
}

/* rcSubmit: the first position with an empty required field is named before anything is sent. */
function firstMissing(positions: Draft[]): string {
  for (let index = 0; index < positions.length; index += 1) {
    const missing = IMPORT_FIELDS.filter((field) => field.required && !positions[index][field.key].trim()).map((field) => field.label);
    if (missing.length) return `Позиция ${index + 1}: заполни ${missing.join(", ")}`;
  }
  return "";
}

/* NAV-1: «Оформить заявку» (view="create": the form only) and «Мои заявки» (view="mine": own rows) are separate
   sections, as in SUPPLYMAP; without view it is «Заявки»: form plus every row.
   RQ-MINE: «mine» is loaded server-side by the caller's own actor id (`/api/requests/mine`, paginated), not a
   client-side name match over the shared `/api/requests` list; each row carries its procurement `stage` and the
   purchase columns (закупщик, поставщик, № заказа, ожид. дата, сумма).
   Manual form = SUPPLYMAP rcOpenForm: «Общее для всех позиций» once on top, one card per position, «+ Добавить
   позицию», copy/remove; the whole batch goes in one POST (one НАЗНАЧЕНИЕ.NNN per purpose). «Вставить из Excel»
   recognizes the pasted range and seeds the same cards (as rcOpenForm with seed).
   RQ-IMPORT-UI: «Из расчёта» = SUPPLYMAP openCalc: the operator pastes the LINK to the calculation Google Sheet,
   the server reads its ЗКП/ЗКЗ (`/api/requests/import/calc`, service account); pasting the ЗКП range
   (`/import/parse`) stays only as a fallback when the server has no Google key.
   NAV-SECTIONS: filing takes `newrequest` (the form here and inside «Заявки»), own rows take `myrequests`, every row
   takes `requests` — so «Оформить заявку» never asks for the whole list. */
/** ?calc_url=<ссылка на расчёт>&calc_project=<№> — один раз при открытии, затем убирается из адреса. */
function readCalcPreset(): { url: string; project: string } | null {
  const url = new URL(window.location.href);
  const calc = (url.searchParams.get("calc_url") || "").trim();
  if (!calc) return null;
  const project = (url.searchParams.get("calc_project") || "").trim();
  url.searchParams.delete("calc_url");
  url.searchParams.delete("calc_project");
  window.history.replaceState({}, "", url);
  return { url: calc, project };
}

export function RequestsPage({ principal, view }: { principal: Principal; view?: "create" | "mine" }) {
  const creationKey = useRef(crypto.randomUUID());
  const batchKey = useRef(crypto.randomUUID());
  const [schema, setSchema] = useState<RequestSchema | null>(null);
  const [rows, setRows] = useState<RequestRow[]>([]);
  const [common, setCommon] = useState<Common>(emptyCommon);
  const [positions, setPositions] = useState<Draft[]>(() => [emptyDraft()]);
  const [fromExcel, setFromExcel] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [serverQuery, setServerQuery] = useState("");
  const canEdit = principal.permissions.newrequest === "edit";
  const canHandoff = canAct(principal, "procurement", ["buyer", "admin"]);

  const [createMode, setCreateMode] = useState<"manual" | "excel" | "calc">("manual");

  const [mineRows, setMineRows] = useState<RequestRow[]>([]);
  const [mineTotal, setMineTotal] = useState(0);
  const [mineLoading, setMineLoading] = useState(false);

  const [excelText, setExcelText] = useState("");
  const [excelError, setExcelError] = useState("");
  const [templateNote, setTemplateNote] = useState("");

  const [calcUrl, setCalcUrl] = useState("");
  const [calcLoading, setCalcLoading] = useState(false);
  const [calcText, setCalcText] = useState("");
  const [calcWarnings, setCalcWarnings] = useState<string[]>([]);
  const [calcUnits, setCalcUnits] = useState<Record<string, string>>({});
  const [calcCategory, setCalcCategory] = useState<Record<string, string>>({});
  const [calcPreview, setCalcPreview] = useState<CalcImportPreview | null>(null);
  const [calcSelected, setCalcSelected] = useState<boolean[]>([]);
  const [calcPurpose, setCalcPurpose] = useState("");
  const [calcPoint, setCalcPoint] = useState("");
  const [calcDeadline, setCalcDeadline] = useState(dateAfter(7));
  const [calcQuality, setCalcQuality] = useState<Record<string, string>>({});
  const [calcPlanned, setCalcPlanned] = useState<Record<string, string>>({});
  const [calcError, setCalcError] = useState("");
  // «Оформить заявку из расчёта» в карточке проекта: ссылка и № проекта приходят в адресе (?calc_url=&calc_project=).
  const calcPreset = useRef<ReturnType<typeof readCalcPreset> | undefined>(undefined);
  if (calcPreset.current === undefined) calcPreset.current = readCalcPreset();

  const projects = schema?.projects ?? [];

  async function reload(query = serverQuery) {
    const [nextSchema, result] = await Promise.all([
      requestsApi.schema(),
      view === "create" ? Promise.resolve(null) : requestsApi.list(query),
    ]);
    setSchema(nextSchema);
    if (result) setRows(result.requests);
    const point = nextSchema.delivery_points[0] || "";
    const purpose = nextSchema.purposes[0] || "";
    setCommon((current) => ({
      ...current,
      purpose: current.purpose || purpose,
      delivery_point: current.delivery_point || point,
    }));
    setPositions((current) => current.map((item) => ({
      ...item,
      purpose: item.purpose || purpose,
      delivery_point: item.delivery_point || point,
    })));
    setCalcPurpose((current) => current || nextSchema.purposes.find((item) => item === "Обеспечение производства") || nextSchema.purposes[0] || "");
    setCalcPoint((current) => current || point);
  }

  async function loadMine(offset: number, query = serverQuery) {
    setMineLoading(true);
    try {
      const result = await requestsApi.mine(MINE_PAGE_SIZE, offset, query);
      setMineRows((current) => (offset === 0 ? result.requests : [...current, ...result.requests]));
      setMineTotal(result.total);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Ошибка загрузки");
    } finally {
      setMineLoading(false);
    }
  }

  useEffect(() => {
    const preset = calcPreset.current;
    if (view !== "create" || !preset) return;
    setCreateMode("calc");
    setCalcUrl(preset.url);
    loadCalc(preset.url);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (view === "mine") {
      void loadMine(0);
      return;
    }
    reload().catch((reason) => setError(reason instanceof Error ? reason.message : "Ошибка загрузки"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  function searchServer(query: string) {
    setServerQuery(query);
    setError("");
    if (view === "mine") void loadMine(0, query);
    else reload(query).catch((reason) => setError(reason instanceof Error ? reason.message : "Ошибка загрузки"));
  }

  /* rcBulkApply_: a common value goes into every position; rcCategoryRule follows the category. */
  function setCommonField(name: CommonKey, value: string) {
    const next = name === "category" ? applyCategoryRule({ ...common, category: value }) : { ...common, [name]: value };
    setCommon(next);
    if (!value && name !== "project_reference") return; // пустой выбор ничего не стирает
    setPositions((current) => current.map((item) => {
      const patched = { ...item, [name]: value };
      if (name === "category") patched.quality_document = next.quality_document;
      return patched;
    }));
  }

  function setPositionField(index: number, name: keyof Draft, value: string) {
    setPositions((current) => current.map((item, i) => (i === index ? { ...item, [name]: value } : item)));
  }

  function addPosition(seed?: Draft) {
    setPositions((current) => [...current, seed ? { ...seed } : emptyDraft(common)]);
  }

  function removePosition(index: number) {
    setPositions((current) => (current.length > 1 ? current.filter((_, i) => i !== index) : current));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setNotice("");
    const missing = firstMissing(positions);
    if (missing) {
      setError(missing);
      return;
    }
    setBusy(true);
    try {
      const result = await requestsApi.createMany(positions.map(payloadOf), creationKey.current);
      creationKey.current = crypto.randomUUID();
      const ids = result.requests.map((item) => item.legacy_id);
      setNotice(
        fromExcel
          ? `Создано заявок из Excel: ${ids.length}`
          : ids.length === 1
            ? `Создана заявка ${ids[0]}`
            : `Создано заявок: ${ids.length} (${ids.join(", ")})`,
      );
      setPositions([emptyDraft(common)]);
      setFromExcel(false);
      try {
        await reload();
      } catch (reason) {
        setError(
          reason instanceof Error
            ? `Заявки созданы, но список не обновился: ${reason.message}`
            : "Заявки созданы, но список не обновился",
        );
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось создать заявку");
    } finally {
      setBusy(false);
    }
  }

  async function handoff(ids: string[], retransfer = false) {
    if (retransfer && !window.confirm("Перенести заявку в закупку повторно? Появится новая строка со следующей ревизией, прежняя останется.")) return;
    if (ids.length > HANDOFF_LIMIT) {
      setError(`За один раз можно перенести не больше ${HANDOFF_LIMIT} заявок`);
      throw new Error("limit");
    }
    setBusy(true);
    setError("");
    try {
      await requestsApi.handoff(ids, retransfer);
      setNotice(
        retransfer
          ? "Заявка перенесена повторно: в закупке новая ревизия"
          : ids.length === 1
            ? "Заявка передана в очередь закупки"
            : `Передано в очередь закупки: ${ids.length}`,
      );
      if (view === "mine") await loadMine(0);
      else await reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось передать заявку");
      throw reason;
    } finally {
      setBusy(false);
    }
  }

  function switchMode(mode: "manual" | "excel" | "calc") {
    setCreateMode(mode);
    setError("");
    setNotice("");
  }

  /* ── «Вставить из Excel»: template → paste → recognize → the same position cards → one batch. ── */
  async function copyHeaders() {
    const ok = await copyText(TEMPLATE_HEADERS.join("\t"));
    setTemplateNote(
      ok
        ? "Заголовки скопированы (обязательные — со звёздочкой) — вставь в Excel в строку 1, заполни строки под ними."
        : "Не удалось скопировать автоматически — выдели заголовки вручную.",
    );
  }

  function downloadTemplate() {
    const link = document.createElement("a");
    link.href = templateDataUri(TEMPLATE_HEADERS);
    link.download = "Шаблон заявки.csv";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTemplateNote("Шаблон скачан — открой в Excel, заполни строки под шапкой, скопируй диапазон и вставь ниже.");
  }

  function recognizeExcel() {
    setExcelError("");
    const parsed = parseExcelTsv(excelText);
    if ("error" in parsed) {
      setExcelError(parsed.error);
      return;
    }
    if (!parsed.rows.length) {
      setExcelError("Не нашёл строк с данными.");
      return;
    }
    setPositions(parsed.rows.map((row) => {
      const filled = Object.fromEntries(Object.entries(row).filter(([, value]) => value !== ""));
      return applyCategoryRule({ ...emptyDraft(common), ...filled });
    }));
    setFromExcel(true);
    setExcelText("");
    setCreateMode("manual");
    setNotice(
      `Из Excel распознано позиций: ${parsed.rows.length}. Колонки: ${parsed.recognized.join(", ")}` +
        (parsed.unknown.length ? `. Не распознаны (пропущены): ${parsed.unknown.join(", ")}` : "") +
        ". Проверь значения и создай заявки.",
    );
  }

  /* ── «Из расчёта» (RQ-5): recognize the pasted ЗКП range, fill the required-per-batch fields,
     bulk-set Ед.изм/Документ качества/План. дата per row, create the selected positions. ── */
  /* rcCalcLoad → rcCalcRender: every row starts «Общепром», so its Документ качества pre-fills «Нет»
     (rcCalcApplyCatRule); Ед.изм comes pre-filled by type and stays editable. */
  function showCalcPreview(loaded: CalcImportPreview) {
    const preview = calcPreset.current?.project ? { ...loaded, project: calcPreset.current.project } : loaded;
    setCalcWarnings(preview.warnings ?? []);
    if (!preview.items.length) {
      setCalcError("Позиций не найдено");
      setCalcPreview(null);
      return;
    }
    setCalcPreview(preview);
    setCalcSelected(preview.items.map(() => true));
    setCalcUnits(Object.fromEntries(preview.items.map((item, i) => [i, item.unit ?? ""])));
    setCalcCategory(Object.fromEntries(preview.items.map((_, i) => [i, "Общепром"])));
    setCalcQuality(Object.fromEntries(preview.items.map((_, i) => [i, "Нет"])));
    setCalcPlanned({});
  }

  function loadCalc(fromUrl?: string) {
    setCalcError("");
    const url = (fromUrl ?? calcUrl).trim();
    if (!url) {
      setCalcError("Вставь ссылку");
      return;
    }
    setCalcLoading(true);
    requestsApi
      .importFromLink(url)
      .then(showCalcPreview)
      .catch((reason) => setCalcError(reason instanceof Error ? reason.message : "Не удалось прочитать расчёт"))
      .finally(() => setCalcLoading(false));
  }

  function recognizeCalc() {
    setCalcError("");
    const grid = tsvToGrid(calcText);
    if (grid.length < 2) {
      setCalcError("Нужны строка заголовков и хотя бы одна строка данных.");
      return;
    }
    requestsApi
      .importParse(grid)
      .then(showCalcPreview)
      .catch((reason) => setCalcError(reason instanceof Error ? reason.message : "Не удалось распознать"));
  }

  /* rcCalcApplyCatRule: ОП → «Да» and locked; ИП → «Да» if empty; Общепром → «Нет» if empty. */
  function setRowCategory(index: number, category: string) {
    setCalcCategory((current) => ({ ...current, [index]: category }));
    setCalcQuality((current) => {
      const now = current[index] ?? "";
      const next = category === "ОП" ? "Да" : now || (category === "ИП" ? "Да" : "Нет");
      return { ...current, [index]: next };
    });
  }

  function bulkApplyCategory(category: string) {
    if (!calcPreview) return;
    calcPreview.items.forEach((_, i) => setRowCategory(i, category));
  }

  function bulkApplyQuality(value: string) {
    if (!calcPreview) return;
    setCalcQuality(Object.fromEntries(calcPreview.items.map((_, i) => [i, value])));
  }

  function bulkApplyPlanned(value: string) {
    if (!calcPreview) return;
    setCalcPlanned(Object.fromEntries(calcPreview.items.map((_, i) => [i, value])));
  }

  async function submitCalc() {
    if (!calcPreview) {
      setCalcError("Сначала загрузи");
      return;
    }
    if (!calcDeadline) {
      setCalcError("Укажи дедлайн — без него заявки не создаются.");
      return;
    }
    if (!calcPoint) {
      setCalcError("Выбери точку отгрузки — без неё заявки не создаются.");
      return;
    }
    if (!calcPurpose) {
      setCalcError("Выбери назначение — без него заявки не создаются.");
      return;
    }
    const selectedIndexes = calcPreview.items.map((_, i) => i).filter((i) => calcSelected[i]);
    if (!selectedIndexes.length) {
      setCalcError("Отметь галочками хотя бы одну позицию.");
      return;
    }
    const missingUnit = selectedIndexes.filter((i) => !String(calcUnits[i] || "").trim());
    const missingQuality = selectedIndexes.filter((i) => !String(calcQuality[i] || "").trim());
    const missingPlanned = selectedIndexes.filter((i) => !String(calcPlanned[i] || "").trim());
    if (missingUnit.length) {
      setCalcError(`Заполни Ед.изм: ${missingUnit.map((i) => calcPreview.items[i].name).join(", ")}`);
      return;
    }
    if (missingQuality.length) {
      setCalcError(`Выбери Документ по качеству (Да/Нет): ${missingQuality.map((i) => calcPreview.items[i].name).join(", ")}`);
      return;
    }
    if (missingPlanned.length) {
      setCalcError(`Укажи план. дату поступления: ${missingPlanned.map((i) => calcPreview.items[i].name).join(", ")}`);
      return;
    }
    setBusy(true);
    setCalcError("");
    setNotice("");
    try {
      const items = selectedIndexes.map((i) => {
        const item = calcPreview.items[i];
        return {
          item_type: item.item_type,
          name: item.name,
          quantity: item.quantity,
          unit: calcUnits[i],
          category: calcCategory[i] || "Общепром",
          quality_document: calcQuality[i],
          planned_date: calcPlanned[i],
          link: item.link || null,
          price: item.price,
          price_with_vat: item.price_with_vat,
        };
      });
      const result = await requestsApi.importCreate(
        {
          project: calcPreview.project,
          purpose: calcPurpose,
          delivery_point: calcPoint,
          procurement_deadline: calcDeadline,
          items,
        },
        batchKey.current,
      );
      batchKey.current = crypto.randomUUID();
      const warningText = result.warnings.length ? ` Предупреждение: ${result.warnings.join("; ")}` : "";
      setNotice(`Создано заявок из расчёта: ${result.requests.length}.${warningText}`);
      setCalcText("");
      setCalcUrl("");
      setCalcWarnings([]);
      setCalcPreview(null);
      setCalcSelected([]);
      setCreateMode("manual");
      await reload();
    } catch (reason) {
      setCalcError(reason instanceof Error ? reason.message : "Не удалось создать заявки");
    } finally {
      setBusy(false);
    }
  }

  /* ── Tables (shared DataTable): «Мои заявки» with the purchase columns, «Заявки» with every request column. ── */
  const baseColumns: DataColumn<RequestRow>[] = [
    {
      key: "legacy_id",
      title: "ID",
      value: (row) => row.legacy_id,
      render: (row) => <><strong>{row.legacy_id}</strong><small>{row.designation}</small></>,
    },
    { key: "designation", title: "Назначение", value: (row) => row.designation, filter: true, hidden: true },
    { key: "created_at", title: "Дата запроса", value: (row) => formatDate(row.created_at), hidden: view === "mine" },
    { key: "initiator", title: "Инициатор", value: (row) => row.initiator, filter: true, hidden: view === "mine" },
    { key: "name", title: "Наименование", value: (row) => row.name, render: (row) => <>{row.name}<small>{row.item_type}</small></>, wrap: true },
    { key: "item_type", title: "Тип", value: (row) => row.item_type, filter: true, hidden: true },
    { key: "design_article", title: "Артикул КД", value: (row) => row.design_article ?? "", hidden: view === "mine" },
    { key: "purpose", title: "Назначение (база)", value: (row) => row.purpose, hidden: true },
    { key: "project_reference", title: "Цель", value: (row) => row.project_reference || row.purpose, filter: true },
    { key: "quantity", title: "Кол-во", value: (row) => `${row.quantity} ${row.unit}`, numeric: true },
    { key: "delivery_point", title: "Точка отгрузки", value: (row) => row.delivery_point, filter: true, hidden: true },
    { key: "category", title: "Категория", value: (row) => row.category, filter: true, hidden: view === "mine" },
    { key: "quality_document", title: "Док. качества", value: (row) => row.quality_document ?? "", hidden: true },
    { key: "procurement_deadline", title: "Дедлайн", value: (row) => formatDate(row.procurement_deadline), hidden: view === "mine" },
    { key: "planned_date", title: "План. дата", value: (row) => formatDate(row.planned_date), hidden: view === "mine" },
    { key: "priority", title: "Приоритет", value: (row) => row.priority, filter: true },
    { key: "link", title: "Ссылка", value: (row) => row.link ?? "", render: (row) => (row.link ? <a href={row.link} target="_blank" rel="noreferrer">ссылка</a> : ""), hidden: true },
    { key: "price_with_vat", title: "Цена с НДС", value: (row) => row.price_with_vat ?? "", numeric: true, hidden: true },
    { key: "line_sum", title: "Сумма заявки", value: (row) => row.line_sum ?? "", numeric: true, hidden: view === "mine" },
    { key: "comment", title: "Комментарий", value: (row) => row.comment ?? "", wrap: true, hidden: true },
    { key: "stage", title: "Статус", value: (row) => row.stage, render: (row) => <span className="status">{row.stage}</span> },
  ];
  const mineColumns: DataColumn<RequestRow>[] = [
    ...baseColumns,
    { key: "buyer", title: "Закупщик", value: (row) => row.buyer ?? "" },
    { key: "supplier", title: "Поставщик", value: (row) => row.supplier ?? "", wrap: true },
    { key: "order_number", title: "№ заказа", value: (row) => row.order_number ?? "" },
    { key: "expected_arrival", title: "Ожид. дата", value: (row) => formatDate(row.expected_arrival) },
    { key: "procurement_sum", title: "Сумма", value: (row) => row.procurement_sum ?? "", numeric: true },
  ];
  const allColumns: DataColumn<RequestRow>[] = [
    ...baseColumns,
    {
      key: "actions",
      title: "",
      value: () => "",
      render: (row) => (
        <>
          {row.status === "Новый" && canHandoff && <button className="secondary small row-action" disabled={busy} onClick={() => void handoff([row.id]).catch(() => undefined)}>В закупку</button>}
          {row.status === "В работе" && canHandoff && <button className="secondary small row-action" disabled={busy} onClick={() => void handoff([row.id], true).catch(() => undefined)}>Повторно в закупку</button>}
        </>
      ),
    },
  ];

  /* One search box: typing filters the rows on screen, «Найти»/Enter searches every request on the server. */
  const serverSearch = {
    onSearch: (query: string) => searchServer(query),
    searchLabel: view === "mine" ? "Искать во всех моих заявках" : "Искать среди всех заявок",
    searchPlaceholder: view === "mine" ? "Поиск по моим заявкам — «Найти» ищет во всех" : "Поиск по заявкам — «Найти» ищет во всех",
  };

  const shownCount = view === "mine" ? mineTotal : rows.length;

  return (
    <section className="page">
      <header className="page-head">
        <h1>{TITLES[view ?? "all"]}</h1>
        {view !== "create" && <span className="page-count">{shownCount}</span>}
      </header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}
      {/* «Заявки» и «Мои заявки» — только список; новая заявка оформляется в своём разделе «Оформить заявку». */}
      <div className={view === "create" ? "single-layout" : "list-layout"}>
        {view !== "create" ? null : canEdit ? (
          <div className="panel request-form">
            <div className="panel-heading"><h2>Новая заявка</h2><span>Обязательные поля отмечены *</span></div>
            <div className="mode-tabs">
              <button type="button" className={createMode === "manual" ? "secondary small active" : "secondary small"} onClick={() => switchMode("manual")}>Вручную</button>
              <button type="button" className={createMode === "excel" ? "secondary small active" : "secondary small"} onClick={() => switchMode("excel")}>Вставить из Excel</button>
              <button type="button" className={createMode === "calc" ? "secondary small active" : "secondary small"} onClick={() => switchMode("calc")}>Из расчёта</button>
            </div>

            {createMode === "manual" && (
              <form onSubmit={submit}>
                <fieldset className="form-grid">
                  <legend>Общее для всех позиций</legend>
                  <label className="half">Назначение *<select value={common.purpose} onChange={(e) => setCommonField("purpose", e.target.value)}>{schema?.purposes.map((item) => <option key={item}>{item}</option>)}</select></label>
                  <label className="half">Цель (проект)
                    {/* Ручная заявка: номер проекта вписывают руками, пусто — без проекта; реестр только подсказывает. */}
                    <SuggestInput options={projects} value={common.project_reference} onChange={(value) => setCommonField("project_reference", value)} placeholder="пусто — без проекта, например 591К.3" />
                  </label>
                  <label className="wide">Точка отгрузки *<select value={common.delivery_point} onChange={(e) => setCommonField("delivery_point", e.target.value)}><option value="">Выберите точку</option>{schema?.delivery_points.map((item) => <option key={item}>{item}</option>)}</select></label>
                  <label>Категория<select value={common.category} onChange={(e) => setCommonField("category", e.target.value)}>{schema?.categories.map((item) => <option key={item}>{item}</option>)}</select></label>
                  <label>Документ качества *<select value={common.quality_document} disabled={common.category === "ОП"} onChange={(e) => setCommonField("quality_document", e.target.value)}><option>Нет</option><option>Да</option></select></label>
                  <label>Срок закупки *<input type="date" value={common.procurement_deadline} onChange={(e) => setCommonField("procurement_deadline", e.target.value)} /></label>
                  <label>Плановая дата *<input type="date" value={common.planned_date} onChange={(e) => setCommonField("planned_date", e.target.value)} /></label>
                </fieldset>
                {positions.map((position, index) => {
                  const own = COMMON_KEYS.filter((key) => position[key] && position[key] !== common[key]);
                  return (
                    <fieldset className="form-grid request-position" key={index}>
                      <legend>Позиция {index + 1}</legend>
                      <div className="wide form-actions">
                        <button type="button" className="secondary small" onClick={() => addPosition(position)}>Копия</button>
                        <button type="button" className="secondary small" disabled={positions.length === 1} onClick={() => removePosition(index)}>Убрать</button>
                      </div>
                      {own.length > 0 && (
                        <p className="wide page-sub">Своё у позиции: {own.map((key) => `${LABEL_BY_KEY[key]} — ${position[key]}`).join("; ")}</p>
                      )}
                      <label className="wide">Наименование *<input value={position.name} onChange={(e) => setPositionField(index, "name", e.target.value)} /></label>
                      <label>Тип *<SuggestInput options={schema?.item_types ?? []} placeholder="выбрать/вписать" value={position.item_type} onChange={(value) => setPositionField(index, "item_type", value)} /></label>
                      <label>Артикул КД<input value={position.design_article} onChange={(e) => setPositionField(index, "design_article", e.target.value)} /></label>
                      <label>Количество *<input type="number" min="0.000001" step="any" value={position.quantity} onChange={(e) => setPositionField(index, "quantity", e.target.value)} /></label>
                      <label>Единица *<select value={position.unit} onChange={(e) => setPositionField(index, "unit", e.target.value)}><option value="">—</option>{schema?.units.map((item) => <option key={item}>{item}</option>)}</select></label>
                      <label className="wide half">Ссылка<input value={position.link} onChange={(e) => setPositionField(index, "link", e.target.value)} placeholder="https://…" /></label>
                      <label>Цена<input type="number" min="0" step="0.01" value={position.price} onChange={(e) => setPositionField(index, "price", e.target.value)} /></label>
                      <label>Цена с НДС<input type="number" min="0" step="0.01" value={position.price_with_vat} onChange={(e) => setPositionField(index, "price_with_vat", e.target.value)} /></label>
                      <label className="wide">Комментарий<textarea value={position.comment} onChange={(e) => setPositionField(index, "comment", e.target.value)} rows={2} /></label>
                    </fieldset>
                  );
                })}
                <div className="form-actions">
                  <button type="button" className="secondary" onClick={() => addPosition()}>+ Добавить позицию</button>
                  <button className="primary" disabled={busy || !schema}>
                    {busy ? "Сохраняем…" : positions.length === 1 ? "Создать заявку" : `Создать заявки (${positions.length})`}
                  </button>
                </div>
              </form>
            )}

            {createMode === "excel" && (
              <div className="import-panel">
                <p className="page-sub">
                  Заполните строки по шаблону ниже, скопируйте диапазон вместе со строкой заголовков и вставьте в поле — «Распознать».
                </p>
                <div className="rc-tpl-box">
                  <p className="page-sub">
                    Шаблон колонок заявки: со звёздочкой (*) — обязательные, остальные — по желанию.
                  </p>
                  <p className="chips">{TEMPLATE_HEADERS.join(" · ")}</p>
                  <div className="form-actions">
                    <button type="button" className="secondary small" onClick={() => void copyHeaders()}>Скопировать заголовки</button>
                    <button type="button" className="secondary small" onClick={downloadTemplate}>Скачать шаблон (.csv)</button>
                  </div>
                  {templateNote && <p className="page-sub">{templateNote}</p>}
                </div>
                <textarea
                  className="rc-tsv"
                  rows={4}
                  placeholder="Вставь сюда диапазон из Excel (со строкой заголовков)…"
                  value={excelText}
                  onChange={(e) => setExcelText(e.target.value)}
                />
                <div className="form-actions">
                  <button type="button" className="secondary" onClick={recognizeExcel}>Распознать</button>
                </div>
                {excelError && <div className="error-banner" role="alert">{excelError}</div>}
              </div>
            )}

            {createMode === "calc" && (
              <div className="import-panel">
                <p className="page-sub">Вставьте ссылку на Google-таблицу расчёта — позиции листа ЗКП станут заявками.</p>
                <div className="form-grid">
                  <label className="wide">Ссылка на расчёт<input type="text" placeholder="https://docs.google.com/spreadsheets/d/…" value={calcUrl} onChange={(e) => { calcPreset.current = null; setCalcUrl(e.target.value); }} /></label>
                  <label>Назначение (на всю пачку) *<select value={calcPurpose} onChange={(e) => setCalcPurpose(e.target.value)}>{schema?.purposes.map((item) => <option key={item}>{item}</option>)}</select></label>
                  <label>Дедлайн (на всю пачку) *<input type="date" value={calcDeadline} onChange={(e) => setCalcDeadline(e.target.value)} /></label>
                  <label className="wide">Точка отгрузки (на всю пачку) *<select value={calcPoint} onChange={(e) => setCalcPoint(e.target.value)}><option value="">— выбери адрес —</option>{schema?.delivery_points.map((item) => <option key={item}>{item}</option>)}</select></label>
                </div>
                <div className="form-actions">
                  <button type="button" className="secondary" disabled={calcLoading} onClick={() => loadCalc()}>{calcLoading ? "Читаю…" : "Загрузить"}</button>
                </div>
                <details>
                  <summary>Сервер не читает Google? Вставить диапазон листа ЗКП вручную</summary>
                  <textarea
                    className="rc-tsv"
                    rows={4}
                    placeholder="Вставь сюда диапазон листа ЗКП (со строкой заголовков)…"
                    value={calcText}
                    onChange={(e) => setCalcText(e.target.value)}
                  />
                  <div className="form-actions">
                    <button type="button" className="secondary" onClick={recognizeCalc}>Распознать</button>
                  </div>
                </details>
                {calcError && <div className="error-banner" role="alert">{calcError}</div>}
                {calcPreview && (
                  <>
                    <p className="page-sub">Проект: <b>{calcPreview.project || "—"}</b> · Позиций: <b>{calcPreview.items.length}</b></p>
                    {calcWarnings.map((warning) => <p key={warning} className="page-sub" role="status">Предупреждение: {warning}</p>)}
                    <p className="page-sub">Поправьте Ед.изм / Док. по качеству / План. дату при необходимости и нажмите «Создать заявки».</p>
                    <div className="form-actions">
                      <label>Категория — всем<select defaultValue="" onChange={(e) => e.target.value && bulkApplyCategory(e.target.value)}><option value="">— всем —</option><option>Общепром</option><option>ИП</option><option>ОП</option></select></label>
                      <label>Документ по качеству — всем<select defaultValue="" onChange={(e) => e.target.value && bulkApplyQuality(e.target.value)}><option value="">— всем —</option><option>Да</option><option>Нет</option></select></label>
                      <label>План. дата — всем<input type="date" onChange={(e) => e.target.value && bulkApplyPlanned(e.target.value)} /></label>
                    </div>
                    <div className="table-scroll">
                      <table>
                        <thead><tr><th /><th>Тип</th><th>Наименование</th><th>Кол-во</th><th>Ед.изм *</th><th>Категория</th><th>Документ качества *</th><th>План. дата *</th><th>С НДС</th></tr></thead>
                        <tbody>
                          {calcPreview.items.map((item, index) => (
                            <tr key={index}>
                              <td><input type="checkbox" checked={calcSelected[index] ?? true} onChange={(e) => setCalcSelected((current) => current.map((value, i) => (i === index ? e.target.checked : value)))} /></td>
                              <td>{item.item_type}</td>
                              <td>{item.name}</td>
                              <td>{item.quantity ?? "—"}</td>
                              <td><input aria-label={`Ед.изм: ${item.name}`} value={calcUnits[index] ?? ""} onChange={(e) => setCalcUnits((current) => ({ ...current, [index]: e.target.value }))} /></td>
                              <td>
                                <select aria-label={`Категория: ${item.name}`} value={calcCategory[index] ?? "Общепром"} onChange={(e) => setRowCategory(index, e.target.value)}>
                                  <option>Общепром</option><option>ИП</option><option>ОП</option>
                                </select>
                              </td>
                              <td>
                                <select aria-label={`Документ качества: ${item.name}`} value={calcQuality[index] ?? ""} disabled={calcCategory[index] === "ОП"} onChange={(e) => setCalcQuality((current) => ({ ...current, [index]: e.target.value }))}>
                                  <option value="">—</option><option>Да</option><option>Нет</option>
                                </select>
                              </td>
                              <td><input type="date" value={calcPlanned[index] ?? ""} onChange={(e) => setCalcPlanned((current) => ({ ...current, [index]: e.target.value }))} /></td>
                              <td>{item.price_with_vat ?? ""}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="form-actions">
                      <button type="button" className="primary" disabled={busy} onClick={() => void submitCalc()}>
                        {busy ? "Создаём…" : "Создать заявки"}
                      </button>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        ) : <div className="panel readonly-panel"><h2>Только просмотр</h2><p>У вас нет права создавать заявки.</p></div>}
        {view !== "create" && <div className="panel table-panel">
          <div className="panel-heading"><h2>{view === "mine" ? "Мои позиции" : "Последние заявки"}</h2><span>{view === "mine" ? `Всего ${mineTotal}` : "До 200 строк"}</span></div>
          {view === "mine" ? (
            <DataTable
              rows={mineRows}
              columns={mineColumns}
              rowKey={(row) => row.id}
              storageKey="requests-mine"
              label="Мои заявки"
              exportName="Мои заявки"
              {...serverSearch}
              emptyText={mineLoading ? "Загружаю…" : "Ваших заявок пока нет."}
            />
          ) : (
            <DataTable
              rows={rows}
              columns={allColumns}
              rowKey={(row) => row.id}
              storageKey="requests-all"
              label="Заявки"
              exportName="Заявки"
              {...serverSearch}
              presets={[
                { label: "Ждут переноса", test: (row) => row.status === "Новый" },
                { label: "В работе", test: (row) => row.status === "В работе" },
              ]}
              bulkActions={canHandoff ? [{
                label: "Перенести в закупку",
                disabled: (selected) => selected.length > HANDOFF_LIMIT || selected.some((row) => row.status !== "Новый"),
                run: (selected) => handoff(selected.map((row) => row.id)),
              }] : []}
            />
          )}
          {view === "mine" && mineRows.length < mineTotal && (
            <div className="form-actions">
              <button className="secondary" disabled={mineLoading} onClick={() => void loadMine(mineRows.length)}>
                {mineLoading ? "Загружаю…" : "Показать ещё"}
              </button>
            </div>
          )}
        </div>}
      </div>
    </section>
  );
}
