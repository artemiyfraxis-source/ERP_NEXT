import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  requestsApi,
  type CalcImportPreview,
  type Principal,
  type RequestRow,
  type RequestSchema,
} from "./api";

type Draft = {
  purpose: string;
  project_reference: string;
  delivery_point: string;
  name: string;
  item_type: string;
  quantity: string;
  unit: string;
  category: string;
  quality_document: string;
  procurement_deadline: string;
  planned_date: string;
  comment: string;
};

function dateAfter(days: number) {
  const date = new Date();
  date.setDate(date.getDate() + days);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function emptyDraft(): Draft {
  return {
    purpose: "RND",
    project_reference: "",
    delivery_point: "",
    name: "",
    item_type: "Комплектующие",
    quantity: "1",
    unit: "шт",
    category: "Общепром",
    quality_document: "Нет",
    procurement_deadline: dateAfter(7),
    planned_date: dateAfter(14),
    comment: "",
  };
}

const TITLES = { all: "Заявки", create: "Оформить заявку", mine: "Мои заявки" };
const SUBTITLES = {
  all: "Создание потребности и передача закупщику.",
  create: "Новая позиция: после сохранения она появится в «Моих заявках».",
  mine: "Позиции, которые оформили вы, и на каком они этапе.",
};

const MINE_PAGE_SIZE = 50;

/* RQ-IMPORT-UI «Вставить из Excel»: header labels match the manual form's own field labels (SUPPLYMAP
   rcNormH matches the same way against portalRequestSchema headers). A trailing «*» (required marker,
   as on SUPPLYMAP's downloadable template) is ignored so a copy-pasted template still recognizes. */
const IMPORT_FIELDS: Array<{ key: keyof Draft; label: string; required: boolean }> = [
  { key: "purpose", label: "Назначение", required: true },
  { key: "project_reference", label: "Проект", required: false },
  { key: "delivery_point", label: "Точка отгрузки", required: true },
  { key: "name", label: "Наименование", required: true },
  { key: "item_type", label: "Тип", required: true },
  { key: "category", label: "Категория", required: false },
  { key: "quantity", label: "Количество", required: true },
  { key: "unit", label: "Единица", required: true },
  { key: "quality_document", label: "Документ качества", required: true },
  { key: "procurement_deadline", label: "Срок закупки", required: true },
  { key: "planned_date", label: "Плановая дата", required: true },
  { key: "comment", label: "Комментарий", required: false },
];

function normalizeHeader(value: string): string {
  return value.replace(/\s*\*+\s*$/, "").replace(/\s+/g, " ").trim().toLowerCase();
}

type ExcelParseResult =
  | { rows: Array<Partial<Draft>>; recognized: string[]; unknown: string[] }
  | { error: string };

function parseExcelTsv(text: string): ExcelParseResult {
  const lines = text.replace(/\r/g, "").split("\n");
  while (lines.length && lines[lines.length - 1].trim() === "") lines.pop();
  if (lines.length < 2) return { error: "Нужны строка заголовков и хотя бы одна строка данных." };
  const byNorm = new Map(IMPORT_FIELDS.map((field) => [normalizeHeader(field.label), field]));
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
      if (field) (row as Record<string, string>)[field.key] = (cells[c] ?? "").trim();
    });
    rows.push(row);
  }
  return { rows, recognized, unknown };
}

/* RQ-5 `parseZpk_`: the ЗКП range pasted as TSV (header row + data rows) → a grid of cell strings. */
function tsvToGrid(text: string): string[][] {
  return text
    .replace(/\r/g, "")
    .split("\n")
    .filter((line) => line.trim() !== "")
    .map((line) => line.split("\t"));
}

/* NAV-1: «Оформить заявку» (view="create": the form only) and «Мои заявки» (view="mine": own rows) are separate
   sections, as in SUPPLYMAP; without view it is «Заявки»: form plus every row.
   RQ-MINE: «mine» is loaded server-side by the caller's own actor id (`/api/requests/mine`, paginated), not a
   client-side name match over the shared `/api/requests` list, and each row carries its procurement `stage`.
   RQ-IMPORT-UI: «Оформить заявку» offers the same three ways as SUPPLYMAP's openCreate (Вручную / Вставить из
   Excel / Из расчёта); «Из расчёта» has the operator paste the ЗКП range instead of a Google Sheets link — this
   repo has no Sheets access, only RQ-5's `/api/requests/import/*` over an already-extracted grid
   (решено агентом — проверить).
   NAV-SECTIONS: filing takes `newrequest` (the form here and inside «Заявки»), own rows take `myrequests`, every row
   takes `requests` — so «Оформить заявку» never asks for the whole list. */
export function RequestsPage({ principal, view }: { principal: Principal; view?: "create" | "mine" }) {
  const creationKey = useRef(crypto.randomUUID());
  const batchKey = useRef(crypto.randomUUID());
  const [schema, setSchema] = useState<RequestSchema | null>(null);
  const [rows, setRows] = useState<RequestRow[]>([]);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const canEdit = principal.permissions.newrequest === "edit";
  const canHandoff =
    principal.permissions.procurement === "edit" &&
    principal.roles.some((role) => role === "buyer" || role === "admin");

  const [createMode, setCreateMode] = useState<"manual" | "excel" | "calc">("manual");

  const [mineRows, setMineRows] = useState<RequestRow[]>([]);
  const [mineTotal, setMineTotal] = useState(0);
  const [mineOffset, setMineOffset] = useState(0);
  const [mineLoading, setMineLoading] = useState(false);

  const [excelText, setExcelText] = useState("");
  const [excelRows, setExcelRows] = useState<Draft[]>([]);
  const [excelUnknown, setExcelUnknown] = useState<string[]>([]);
  const [excelError, setExcelError] = useState("");

  const [calcText, setCalcText] = useState("");
  const [calcPreview, setCalcPreview] = useState<CalcImportPreview | null>(null);
  const [calcSelected, setCalcSelected] = useState<boolean[]>([]);
  const [calcPurpose, setCalcPurpose] = useState("");
  const [calcPoint, setCalcPoint] = useState("");
  const [calcDeadline, setCalcDeadline] = useState(dateAfter(7));
  const [calcQuality, setCalcQuality] = useState<Record<string, string>>({});
  const [calcPlanned, setCalcPlanned] = useState<Record<string, string>>({});
  const [calcError, setCalcError] = useState("");

  const shown = view === "mine" ? mineRows : rows;
  const rowCount = view === "mine" ? mineTotal : shown.length;

  async function reload() {
    const [nextSchema, result] = await Promise.all([
      requestsApi.schema(),
      view === "create" ? Promise.resolve(null) : requestsApi.list(),
    ]);
    setSchema(nextSchema);
    if (result) setRows(result.requests);
    setDraft((current) => ({
      ...current,
      purpose: current.purpose || nextSchema.purposes[0] || "",
      delivery_point: current.delivery_point || nextSchema.delivery_points[0] || "",
    }));
    setCalcPurpose((current) => current || nextSchema.purposes.find((item) => item === "Обеспечение производства") || nextSchema.purposes[0] || "");
    setCalcPoint((current) => current || nextSchema.delivery_points[0] || "");
  }

  async function loadMine(offset: number) {
    setMineLoading(true);
    try {
      const result = await requestsApi.mine(MINE_PAGE_SIZE, offset);
      setMineRows((current) => (offset === 0 ? result.requests : [...current, ...result.requests]));
      setMineTotal(result.total);
      setMineOffset(offset);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Ошибка загрузки");
    } finally {
      setMineLoading(false);
    }
  }

  useEffect(() => {
    if (view === "mine") {
      void loadMine(0);
      return;
    }
    reload().catch((reason) => setError(reason instanceof Error ? reason.message : "Ошибка загрузки"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  function field<K extends keyof Draft>(name: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [name]: value }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const payload = Object.fromEntries(
        Object.entries(draft).filter(([, value]) => value !== ""),
      );
      const result = await requestsApi.create(payload, creationKey.current);
      creationKey.current = crypto.randomUUID();
      setNotice(`Создана заявка ${result.requests[0].legacy_id}`);
      setDraft((current) => ({ ...emptyDraft(), delivery_point: current.delivery_point }));
      try {
        await reload();
      } catch (reason) {
        setError(
          reason instanceof Error
            ? `Заявка создана, но список не обновился: ${reason.message}`
            : "Заявка создана, но список не обновился",
        );
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось создать заявку");
    } finally {
      setBusy(false);
    }
  }

  async function handoff(id: string, retransfer = false) {
    if (retransfer && !window.confirm("Перенести заявку в закупку повторно? Появится новая строка со следующей ревизией, прежняя останется.")) return;
    setBusy(true);
    setError("");
    try {
      await requestsApi.handoff([id], retransfer);
      setNotice(retransfer ? "Заявка перенесена повторно: в закупке новая ревизия" : "Заявка передана в очередь закупки");
      if (view === "mine") await loadMine(0);
      else await reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось передать заявку");
    } finally {
      setBusy(false);
    }
  }

  function switchMode(mode: "manual" | "excel" | "calc") {
    setCreateMode(mode);
    setError("");
    setNotice("");
  }

  /* ── «Вставить из Excel»: paste → recognize → review each row → create the whole batch. ── */
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
    setExcelRows(
      parsed.rows.map((row) => ({
        ...emptyDraft(),
        purpose: draft.purpose,
        delivery_point: draft.delivery_point,
        ...row,
      })),
    );
    setExcelUnknown(parsed.unknown);
  }

  function editExcelRow(index: number, patch: Partial<Draft>) {
    setExcelRows((current) => current.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  function removeExcelRow(index: number) {
    setExcelRows((current) => current.filter((_, i) => i !== index));
  }

  async function submitExcel() {
    if (!excelRows.length) {
      setExcelError("Нет позиций для создания.");
      return;
    }
    setBusy(true);
    setExcelError("");
    setNotice("");
    try {
      const payloads = excelRows.map((row) =>
        Object.fromEntries(Object.entries(row).filter(([, value]) => value !== "")),
      );
      const result = await requestsApi.createMany(payloads, batchKey.current);
      batchKey.current = crypto.randomUUID();
      setNotice(`Создано заявок из Excel: ${result.requests.length}`);
      setExcelText("");
      setExcelRows([]);
      setExcelUnknown([]);
      setCreateMode("manual");
      await reload();
    } catch (reason) {
      setExcelError(reason instanceof Error ? reason.message : "Не удалось создать заявки");
    } finally {
      setBusy(false);
    }
  }

  /* ── «Из расчёта» (RQ-5): recognize the pasted ЗКП range, fill the required-per-batch fields,
     bulk-set Ед.изм/Документ качества/План. дата per row, create the selected positions. ── */
  function recognizeCalc() {
    setCalcError("");
    const grid = tsvToGrid(calcText);
    if (grid.length < 2) {
      setCalcError("Нужны строка заголовков и хотя бы одна строка данных.");
      return;
    }
    requestsApi
      .importParse(grid)
      .then((preview) => {
        if (!preview.items.length) {
          setCalcError("Позиций не найдено — проверь, что вставлен диапазон со строкой заголовков листа ЗКП.");
          setCalcPreview(null);
          return;
        }
        setCalcPreview(preview);
        setCalcSelected(preview.items.map(() => true));
        setCalcQuality({});
        setCalcPlanned({});
      })
      .catch((reason) => setCalcError(reason instanceof Error ? reason.message : "Не удалось распознать"));
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
      setCalcError("Сначала загрузи и распознай диапазон.");
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
    const missingUnit = selectedIndexes.filter((i) => !String(calcPreview.items[i].unit || "").trim());
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
          unit: item.unit,
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

  return (
    <section className="page">
      <header className="page-header">
        <div><span className="eyebrow">Снабжение</span><h1>{TITLES[view ?? "all"]}</h1><p>{SUBTITLES[view ?? "all"]}</p></div>
        {view !== "create" && <span className="counter">{rowCount} строк</span>}
      </header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}
      <div className={view ? "single-layout" : "split-layout"}>
        {view === "mine" ? null : canEdit ? (
          <div className="panel request-form">
            <div className="panel-heading"><h2>Новая заявка</h2><span>Обязательные поля отмечены *</span></div>
            <div className="mode-tabs">
              <button type="button" className={createMode === "manual" ? "secondary small active" : "secondary small"} onClick={() => switchMode("manual")}>✍️ Вручную</button>
              <button type="button" className={createMode === "excel" ? "secondary small active" : "secondary small"} onClick={() => switchMode("excel")}>📥 Вставить из Excel</button>
              <button type="button" className={createMode === "calc" ? "secondary small active" : "secondary small"} onClick={() => switchMode("calc")}>🧮 Из расчёта</button>
            </div>

            {createMode === "manual" && (
              <form onSubmit={submit}>
                <div className="form-grid">
                  <label>Назначение *<select value={draft.purpose} onChange={(e) => field("purpose", e.target.value)}>{schema?.purposes.map((item) => <option key={item}>{item}</option>)}</select></label>
                  <label>Проект<input value={draft.project_reference} onChange={(e) => field("project_reference", e.target.value)} placeholder="Например, 591К.3" /></label>
                  <label className="wide">Точка отгрузки *<select value={draft.delivery_point} onChange={(e) => field("delivery_point", e.target.value)} required><option value="">Выберите точку</option>{schema?.delivery_points.map((item) => <option key={item}>{item}</option>)}</select></label>
                  <label className="wide">Наименование *<input value={draft.name} onChange={(e) => field("name", e.target.value)} required /></label>
                  <label>Тип *<input value={draft.item_type} onChange={(e) => field("item_type", e.target.value)} required /></label>
                  <label>Категория<select value={draft.category} onChange={(e) => field("category", e.target.value)}>{schema?.categories.map((item) => <option key={item}>{item}</option>)}</select></label>
                  <label>Количество *<input type="number" min="0.000001" step="any" value={draft.quantity} onChange={(e) => field("quantity", e.target.value)} required /></label>
                  <label>Единица *<select value={draft.unit} onChange={(e) => field("unit", e.target.value)}>{schema?.units.map((item) => <option key={item}>{item}</option>)}</select></label>
                  <label>Документ качества *<select value={draft.quality_document} onChange={(e) => field("quality_document", e.target.value)}><option>Нет</option><option>Да</option></select></label>
                  <label>Срок закупки *<input type="date" value={draft.procurement_deadline} onChange={(e) => field("procurement_deadline", e.target.value)} required /></label>
                  <label>Плановая дата *<input type="date" value={draft.planned_date} onChange={(e) => field("planned_date", e.target.value)} required /></label>
                  <label className="wide">Комментарий<textarea value={draft.comment} onChange={(e) => field("comment", e.target.value)} rows={3} /></label>
                </div>
                <button className="primary" disabled={busy || !schema}>{busy ? "Сохраняем…" : "Создать заявку"}</button>
              </form>
            )}

            {createMode === "excel" && (
              <div className="import-panel">
                <p className="page-sub">
                  Скопируй диапазон из Excel вместе со строкой заголовков (заголовки — как в форме: Наименование,
                  Количество, Единица …) и вставь ниже.
                </p>
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
                {excelRows.length > 0 && (
                  <>
                    {excelUnknown.length > 0 && (
                      <p className="page-sub">Не распознаны (пропущены): {excelUnknown.join(", ")}</p>
                    )}
                    <div className="table-scroll">
                      <table>
                        <thead><tr><th>Наименование</th><th>Тип</th><th>Кол-во</th><th>Ед.изм</th><th>Назначение</th><th>Срок закупки</th><th>Плановая дата</th><th /></tr></thead>
                        <tbody>
                          {excelRows.map((row, index) => (
                            <tr key={index}>
                              <td><input value={row.name} onChange={(e) => editExcelRow(index, { name: e.target.value })} /></td>
                              <td><input value={row.item_type} onChange={(e) => editExcelRow(index, { item_type: e.target.value })} /></td>
                              <td><input type="number" min="0.000001" step="any" value={row.quantity} onChange={(e) => editExcelRow(index, { quantity: e.target.value })} /></td>
                              <td><input value={row.unit} onChange={(e) => editExcelRow(index, { unit: e.target.value })} /></td>
                              <td>
                                <select value={row.purpose} onChange={(e) => editExcelRow(index, { purpose: e.target.value })}>
                                  {schema?.purposes.map((item) => <option key={item}>{item}</option>)}
                                </select>
                              </td>
                              <td><input type="date" value={row.procurement_deadline} onChange={(e) => editExcelRow(index, { procurement_deadline: e.target.value })} /></td>
                              <td><input type="date" value={row.planned_date} onChange={(e) => editExcelRow(index, { planned_date: e.target.value })} /></td>
                              <td><button type="button" className="secondary small" onClick={() => removeExcelRow(index)}>Удалить</button></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="form-actions">
                      <button type="button" className="primary" disabled={busy} onClick={() => void submitExcel()}>
                        {busy ? "Создаём…" : `Создать заявки (${excelRows.length})`}
                      </button>
                    </div>
                  </>
                )}
              </div>
            )}

            {createMode === "calc" && (
              <div className="import-panel">
                <p className="page-sub">Вставь диапазон листа ЗКП (со строкой заголовков «Обозначение» / «Без НДС») — позиции станут заявками.</p>
                <textarea
                  className="rc-tsv"
                  rows={4}
                  placeholder="Вставь сюда диапазон листа ЗКП (со строкой заголовков)…"
                  value={calcText}
                  onChange={(e) => setCalcText(e.target.value)}
                />
                <div className="form-grid">
                  <label>Назначение (на всю пачку) *<select value={calcPurpose} onChange={(e) => setCalcPurpose(e.target.value)}>{schema?.purposes.map((item) => <option key={item}>{item}</option>)}</select></label>
                  <label>Дедлайн (на всю пачку) *<input type="date" value={calcDeadline} onChange={(e) => setCalcDeadline(e.target.value)} /></label>
                  <label className="wide">Точка отгрузки (на всю пачку) *<select value={calcPoint} onChange={(e) => setCalcPoint(e.target.value)}><option value="">— выбери адрес —</option>{schema?.delivery_points.map((item) => <option key={item}>{item}</option>)}</select></label>
                </div>
                <div className="form-actions">
                  <button type="button" className="secondary" onClick={recognizeCalc}>Распознать</button>
                </div>
                {calcError && <div className="error-banner" role="alert">{calcError}</div>}
                {calcPreview && (
                  <>
                    <p className="page-sub">Проект: <b>{calcPreview.project || "—"}</b> · Позиций: <b>{calcPreview.items.length}</b></p>
                    <div className="form-actions">
                      <label>Документ по качеству — всем<select defaultValue="" onChange={(e) => e.target.value && bulkApplyQuality(e.target.value)}><option value="">— всем —</option><option>Да</option><option>Нет</option></select></label>
                      <label>План. дата — всем<input type="date" onChange={(e) => e.target.value && bulkApplyPlanned(e.target.value)} /></label>
                    </div>
                    <div className="table-scroll">
                      <table>
                        <thead><tr><th /><th>Тип</th><th>Наименование</th><th>Кол-во</th><th>Ед.изм</th><th>Документ качества *</th><th>План. дата *</th></tr></thead>
                        <tbody>
                          {calcPreview.items.map((item, index) => (
                            <tr key={index}>
                              <td><input type="checkbox" checked={calcSelected[index] ?? true} onChange={(e) => setCalcSelected((current) => current.map((value, i) => (i === index ? e.target.checked : value)))} /></td>
                              <td>{item.item_type}</td>
                              <td>{item.name}</td>
                              <td>{item.quantity ?? "—"}</td>
                              <td>{item.unit ?? "—"}</td>
                              <td>
                                <select value={calcQuality[index] ?? ""} onChange={(e) => setCalcQuality((current) => ({ ...current, [index]: e.target.value }))}>
                                  <option value="">—</option><option>Да</option><option>Нет</option>
                                </select>
                              </td>
                              <td><input type="date" value={calcPlanned[index] ?? ""} onChange={(e) => setCalcPlanned((current) => ({ ...current, [index]: e.target.value }))} /></td>
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
          <div className="table-scroll">
            <table><thead><tr><th>ID</th><th>Наименование</th><th>Назначение</th><th>Кол-во</th><th>Приоритет</th><th>Статус</th><th /></tr></thead>
              <tbody>{shown.map((row) => <tr key={row.id}><td><strong>{row.legacy_id}</strong><small>{row.designation}</small></td><td>{row.name}<small>{row.item_type}</small></td><td>{row.project_reference || row.purpose}</td><td>{row.quantity} {row.unit}</td><td>{row.priority}</td><td><span className="status">{row.stage}</span></td><td>{row.status === "Новый" && canHandoff && <button className="secondary small" disabled={busy} onClick={() => handoff(row.id)}>В закупку</button>}{row.status === "В работе" && canHandoff && <button className="secondary small" disabled={busy} onClick={() => handoff(row.id, true)}>Повторно в закупку</button>}</td></tr>)}</tbody>
            </table>
          </div>
          {view === "mine" && !mineLoading && !shown.length && <div className="empty">Ваших заявок пока нет.</div>}
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
