import { useEffect, useMemo, useRef, useState } from "react";
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
import { RequestGrid } from "./RequestGrid";
import { RequestFormV2 } from "./RequestFormV2";
import { usePurposeLabel } from "./purposeLabels";
import { useStageOption } from "./StageOptions";
import {
  GRID_LIMIT,
  parseExcelTsv,
  TEMPLATE_HEADERS,
  applyCategoryRule,
  applyPaste,
  emptyCommon,
  emptyDraft,
  isBlankRow,
  payloadOf,
  positionsWord,
  validateRow,
  COMMON_KEYS,
  dateAfter,
  type Common,
  type CommonKey,
  type Draft,
  type GridKey,
} from "./requestGridLogic";

const TITLES = { all: "Заявки", create: "Оформить заявку", mine: "Мои заявки" };

function formatDate(value: string | null | undefined): string {
  if (!value) return "";
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? `${match[3]}.${match[2]}.${match[1]}` : value;
}

const MINE_PAGE_SIZE = 50;
const HANDOFF_LIMIT = 100;

/* rcDownloadTemplate: the header row, required ones marked «*», tab-separated, UTF-16LE with BOM so
   Excel reads Cyrillic without the import wizard. */

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

/* A split request carries the joined statuses of its procurement lines («Готово / В пути»). */
function stageParts(stage: string | null | undefined): string[] {
  return String(stage ?? "").split(" / ").map((part) => part.trim()).filter(Boolean);
}

/* Closed = every procurement line is «Готово» or «Отменено». */
function stageClosed(stage: string | null | undefined): boolean {
  const parts = stageParts(stage);
  return parts.length > 0 && parts.every((part) => part === "Готово" || part === "Отменено");
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
  const [showErrors, setShowErrors] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [serverQuery, setServerQuery] = useState("");
  const canEdit = principal.permissions.newrequest === "edit";
  const canHandoff = canAct(principal, "procurement");

  const [createMode, setCreateMode] = useState<"manual" | "excel" | "calc">("manual");
  // Stage option «Новая форма заявки»: off = the form below as it was.
  const formV2 = useStageOption("requests.form");
  const purposeText = usePurposeLabel();
  const [formSeed, setFormSeed] = useState<{ id: number; text: string } | null>(null);

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
  const [calcRows, setCalcRows] = useState<Draft[]>([]);
  const [calcShowErrors, setCalcShowErrors] = useState(false);
  const [calcPreview, setCalcPreview] = useState<CalcImportPreview | null>(null);
  const [calcPurpose, setCalcPurpose] = useState("");
  const [calcPoint, setCalcPoint] = useState("");
  const [calcDeadline, setCalcDeadline] = useState(dateAfter(7));
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
    setPositions((current) => (current.length >= GRID_LIMIT ? current : [...current, seed ? { ...seed } : emptyDraft(common)]));
  }

  function removePosition(index: number) {
    setPositions((current) => (current.length > 1 ? current.filter((_, i) => i !== index) : [emptyDraft(common)]));
  }

  function duplicatePosition(index: number) {
    setPositions((current) => (current.length >= GRID_LIMIT ? current : [...current.slice(0, index + 1), { ...current[index] }, ...current.slice(index + 1)]));
  }

  /* A block pasted into any cell: with a header row by column names, otherwise from that cell down and right. */
  function pasteIntoGrid(index: number, key: GridKey, text: string) {
    const result = applyPaste(positions, index, key, text, emptyDraft(common));
    if ("error" in result) {
      setError(result.error);
      return;
    }
    setPositions(result.positions);
    setShowErrors(true);
    setError(result.dropped ? `Не вошло строк: ${result.dropped} — в одной заявке не больше ${GRID_LIMIT} позиций` : "");
    setNotice(
      `Вставлено позиций: ${result.filled}` +
        (result.unknown.length ? `. Не распознаны (пропущены): ${result.unknown.join(", ")}` : ""),
    );
  }

  /* Whole-request fields are checked on every row (an Excel row may carry its own); blank rows are not sent. */
  const gridErrors = useMemo(
    () => positions.map((item) => (isBlankRow(item) ? {} : validateRow(item, schema?.categories ?? []))),
    [positions, schema],
  );
  const sendRows = positions.filter((item) => !isBlankRow(item));

  async function submit() {
    setError("");
    setNotice("");
    setShowErrors(true);
    const bad = gridErrors.filter((rowErrors) => Object.keys(rowErrors).length).length;
    if (bad) {
      setError(`${positionsWord(bad)} с ошибкой — исправьте отмеченные ячейки`);
      return;
    }
    if (!sendRows.length) {
      setError("Добавьте хотя бы одну позицию");
      return;
    }
    setBusy(true);
    try {
      const result = await requestsApi.createMany(sendRows.map(payloadOf), creationKey.current);
      creationKey.current = crypto.randomUUID();
      const ids = result.requests.map((item) => item.legacy_id);
      setNotice(ids.length === 1 ? `Создана заявка ${ids[0]}` : `Создано заявок: ${ids.length} (${ids.join(", ")})`);
      setPositions([emptyDraft(common)]);
      setShowErrors(false);
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
            ? "Заявка передана в закупку: строка в блоке «В работе» на странице «Закупка»"
            : `Передано в закупку: ${ids.length} — строки в блоке «В работе» на странице «Закупка»`,
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
    if (formV2) {
      setFormSeed({ id: Date.now(), text: excelText });
      setExcelText("");
      setCreateMode("manual");
      setNotice(`Из Excel распознано позиций: ${parsed.rows.length}. Колонки: ${parsed.recognized.join(", ")}. Проверьте таблицу и оформите.`);
      return;
    }
    const result = applyPaste(positions, 0, "name", excelText, emptyDraft(common));
    if ("error" in result) {
      setExcelError(result.error);
      return;
    }
    setPositions(result.positions);
    setShowErrors(true);
    setExcelText("");
    setCreateMode("manual");
    setError(result.dropped ? `Не вошло строк: ${result.dropped} — в одной заявке не больше ${GRID_LIMIT} позиций` : "");
    setNotice(
      `Из Excel распознано позиций: ${parsed.rows.length}. Колонки: ${parsed.recognized.join(", ")}` +
        (parsed.unknown.length ? `. Не распознаны (пропущены): ${parsed.unknown.join(", ")}` : "") +
        ". Проверьте таблицу и оформите.",
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
    setCalcShowErrors(false);
    setCalcRows(preview.items.map((item) => ({
      ...emptyDraft(),
      name: item.name,
      item_type: item.item_type ?? "",
      quantity: item.quantity ?? "",
      unit: item.unit ?? "",
      link: item.link ?? "",
      price: item.price ?? "",
      price_with_vat: item.price_with_vat ?? "",
      category: "Общепром",
      quality_document: "Нет",
      planned_date: "",
    })));
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
  function setCalcField(index: number, key: GridKey, value: string) {
    setCalcRows((current) => current.map((item, i) => {
      if (i !== index) return item;
      const next = { ...item, [key]: value };
      return key === "category" ? applyCategoryRule(next) : next;
    }));
  }

  function bulkApplyCategory(category: string) {
    setCalcRows((current) => current.map((item) => applyCategoryRule({ ...item, category })));
  }

  function bulkApplyQuality(value: string) {
    setCalcRows((current) => current.map((item) => (item.category === "ОП" ? item : { ...item, quality_document: value })));
  }

  function bulkApplyPlanned(value: string) {
    setCalcRows((current) => current.map((item) => ({ ...item, planned_date: value })));
  }

  const calcSeed = (): Draft => ({
    ...emptyDraft(), unit: "", purpose: calcPurpose, delivery_point: calcPoint, procurement_deadline: calcDeadline,
    category: "Общепром", quality_document: "Нет", planned_date: "",
  });
  const CALC_COLUMNS: GridKey[] = ["name", "item_type", "quantity", "unit", "category", "quality_document", "planned_date", "price", "price_with_vat"];

  function pasteIntoCalc(index: number, key: GridKey, text: string) {
    const result = applyPaste(calcRows, index, key, text, calcSeed(), CALC_COLUMNS);
    if ("error" in result) {
      setCalcError(result.error);
      return;
    }
    setCalcRows(result.positions);
    setCalcShowErrors(true);
  }

  const calcErrors = useMemo(
    () => calcRows.map((item) => (isBlankRow(item) ? {} : validateRow(
      { ...item, purpose: calcPurpose, delivery_point: calcPoint, procurement_deadline: calcDeadline },
      schema?.categories ?? [],
    ))),
    [calcRows, calcPurpose, calcPoint, calcDeadline, schema],
  );
  const calcSendRows = calcRows.filter((item) => !isBlankRow(item));

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
    setCalcShowErrors(true);
    if (!calcSendRows.length) {
      setCalcError("Нет позиций для заявок.");
      return;
    }
    const bad = calcErrors.filter((rowErrors) => Object.keys(rowErrors).length).length;
    if (bad) {
      setCalcError(`${positionsWord(bad)} с ошибкой — исправьте отмеченные ячейки`);
      return;
    }
    setBusy(true);
    setCalcError("");
    setNotice("");
    try {
      const items = calcSendRows.map((item) => ({
        item_type: item.item_type,
        name: item.name,
        quantity: item.quantity.replace(",", "."),
        unit: item.unit,
        category: item.category || "Общепром",
        quality_document: item.quality_document,
        planned_date: item.planned_date,
        link: item.link || null,
        price: item.price ? item.price.replace(",", ".") : null,
        price_with_vat: item.price_with_vat ? item.price_with_vat.replace(",", ".") : null,
      }));
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
      setCalcRows([]);
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
    { key: "project_reference", title: "Цель", value: (row) => row.project_reference || purposeText(row.purpose), filter: true },
    { key: "quantity", title: "Кол-во", value: (row) => `${row.quantity} ${row.unit}`, numeric: true },
    { key: "delivery_point", title: formV2 ? "Склад" : "Точка отгрузки", value: (row) => row.delivery_point, filter: true, hidden: true },
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
            <div className="panel-heading"><h2>Новая заявка</h2><span>{formV2 ? "Обязательные поля отмечены *; пустые поля сервер заполнит по умолчанию" : "Обязательные поля отмечены *; остальное заполнено по умолчанию"}</span></div>
            <div className="mode-tabs">
              <button type="button" className={createMode === "manual" ? "secondary small active" : "secondary small"} onClick={() => switchMode("manual")}>Вручную</button>
              <button type="button" className={createMode === "excel" ? "secondary small active" : "secondary small"} onClick={() => switchMode("excel")}>Вставить из Excel</button>
              <button type="button" className={createMode === "calc" ? "secondary small active" : "secondary small"} onClick={() => switchMode("calc")}>Из расчёта</button>
            </div>

            {createMode === "manual" && formV2 && (
              <RequestFormV2
                schema={schema}
                seed={formSeed}
                onError={setError}
                onNotice={setNotice}
                onCreated={async (ids) => {
                  setNotice(ids.length === 1 ? `Создана заявка ${ids[0]}` : `Создано заявок: ${ids.length} (${ids.join(", ")})`);
                  try {
                    await reload();
                  } catch (reason) {
                    setError(reason instanceof Error ? `Заявки созданы, но список не обновился: ${reason.message}` : "Заявки созданы, но список не обновился");
                  }
                }}
              />
            )}

            {createMode === "manual" && !formV2 && (
              <div className="rg-form">
                <div className="rg-common">
                  <div className="rg-common-title">Общее для всех позиций <span>— применяется ко всем строкам таблицы</span></div>
                  <div className="rg-common-grid">
                    <label>Назначение *<select value={common.purpose} onChange={(e) => setCommonField("purpose", e.target.value)}>{schema?.purposes.map((item) => <option key={item}>{item}</option>)}</select></label>
                    <label>Цель (проект)
                      {/* Ручная заявка: номер проекта вписывают руками, пусто — без проекта; реестр только подсказывает. */}
                      <SuggestInput options={projects} value={common.project_reference} onChange={(value) => setCommonField("project_reference", value)} placeholder="пусто — без проекта, например 591К.3" />
                    </label>
                    <label className="rg-point">Точка отгрузки<select value={common.delivery_point} onChange={(e) => setCommonField("delivery_point", e.target.value)}><option value="">Выберите точку</option>{schema?.delivery_points.map((item) => <option key={item}>{item}</option>)}</select></label>
                    <label>Категория<select value={common.category} onChange={(e) => setCommonField("category", e.target.value)}>{schema?.categories.map((item) => <option key={item}>{item}</option>)}</select></label>
                    <label>Документ качества<select value={common.quality_document} disabled={common.category === "ОП"} onChange={(e) => setCommonField("quality_document", e.target.value)}><option>Нет</option><option>Да</option></select></label>
                    <label>Срок закупки<input type="date" value={common.procurement_deadline} onChange={(e) => setCommonField("procurement_deadline", e.target.value)} /></label>
                    <label>Плановая дата<input type="date" value={common.planned_date} onChange={(e) => setCommonField("planned_date", e.target.value)} /></label>
                  </div>
                </div>
                <p className="rg-hint">Вставьте блок из Excel или Таблиц прямо в таблицу (Ctrl+V в любую ячейку) · Enter — вниз, Tab — вправо, на последней строке Enter добавляет строку · в одной заявке до {GRID_LIMIT} позиций</p>
                <RequestGrid
                  positions={positions}
                  schema={schema}
                  errors={showErrors ? gridErrors : null}
                  sendCount={sendRows.length}
                  common={common}
                  busy={busy}
                  onField={(index, key, value) => setPositionField(index, key, value)}
                  onPaste={pasteIntoGrid}
                  onAdd={() => addPosition()}
                  onRemove={removePosition}
                  onDuplicate={duplicatePosition}
                  onSubmit={() => void submit()}
                />
              </div>
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
                  <label>Назначение (на всю пачку) *<select value={calcPurpose} onChange={(e) => setCalcPurpose(e.target.value)}>{schema?.purposes.map((item) => <option key={item} value={item}>{purposeText(item)}</option>)}</select></label>
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
                    <div className="rg-bulk">
                      <label>Категория — всем<select defaultValue="" onChange={(e) => e.target.value && bulkApplyCategory(e.target.value)}><option value="">— всем —</option><option>Общепром</option><option>ИП</option><option>ОП</option></select></label>
                      <label>Документ по качеству — всем<select defaultValue="" onChange={(e) => e.target.value && bulkApplyQuality(e.target.value)}><option value="">— всем —</option><option>Да</option><option>Нет</option></select></label>
                      <label>План. дата — всем<input type="date" onChange={(e) => e.target.value && bulkApplyPlanned(e.target.value)} /></label>
                    </div>
                    <RequestGrid
                      positions={calcRows}
                      columns={CALC_COLUMNS}
                      schema={schema}
                      errors={calcShowErrors ? calcErrors : null}
                      sendCount={calcSendRows.length}
                      busy={busy}
                      onField={setCalcField}
                      onPaste={pasteIntoCalc}
                      onAdd={() => setCalcRows((current) => [...current, calcSeed()])}
                      onRemove={(index) => setCalcRows((current) => current.filter((_, i) => i !== index))}
                      onDuplicate={(index) => setCalcRows((current) => [...current.slice(0, index + 1), { ...current[index] }, ...current.slice(index + 1)])}
                      onSubmit={() => void submitCalc()}
                      removeLabel="Убрать"
                      limit={500}
                    />
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
                { label: "В работе", test: (row) => row.status === "В работе" && !stageClosed(row.stage) },
                { label: "Ждут счет", test: (row) => stageParts(row.stage).includes("Жду счет") },
                { label: "Ждут оплаты", test: (row) => stageParts(row.stage).includes("В оплату") },
                { label: "Выполнено", test: (row) => stageClosed(row.stage) && stageParts(row.stage).includes("Готово") },
                { label: "Отменено", test: (row) => stageClosed(row.stage) && !stageParts(row.stage).includes("Готово") },
              ]}
              defaultPreset="Ждут переноса"
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
