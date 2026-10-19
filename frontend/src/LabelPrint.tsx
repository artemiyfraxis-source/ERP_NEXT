import { useEffect, useRef, useState, type FormEvent } from "react";
import "./labelPrint.css";
import { api, failureText } from "./api";

/* «Печать этикеток ГРН» (stage option warehouse.labels), ported from Roller_Buyer `components/LabelPrint.tsx`: the printer
   settings card, a lot's label (preview exactly as the printer gets it, copies, «Печать») and the labels of what receiving
   has just put on stock. The backend saves every job as a file first; without a printer (or off Windows) it stops there. */

export const LABELS_OPTION = "warehouse.labels";

export type LabelCodeType = "qr" | "code128" | "both";
export type PrintSettings = {
  printer_name: string;
  gap_mm: number;
  density: number;
  speed: number;
  width_mm: number;
  height_mm: number;
  dpi: number;
  code_type: LabelCodeType;
  offset_x_mm: number;
  offset_y_mm: number;
};
export type PrintResult = { status: "printed" | "saved" | "failed"; reason: string | null; file: string; copies: number };
type SettingsAnswer = { settings: PrintSettings; code_types: Record<LabelCodeType, string>; print_dir: string; copies_max: number };

const CODE_TYPES: [LabelCodeType, string][] = [["both", "QR и Code128"], ["qr", "QR-код"], ["code128", "Штрихкод Code128"]];
export const COPIES_MAX = 50;

export const labelsApi = {
  settings: () => api<SettingsAnswer>("/api/stock/labels/settings"),
  save: (settings: PrintSettings) => api<{ settings: PrintSettings }>("/api/stock/labels/settings", { method: "PUT", body: JSON.stringify(settings) }),
  printers: () => api<{ printers: string[]; on_windows: boolean }>("/api/stock/labels/printers"),
  test: () => api<PrintResult>("/api/stock/labels/test", { method: "POST", body: "{}" }),
  printLot: (lotId: string, copies: number) =>
    api<PrintResult>(`/api/stock/batches/${encodeURIComponent(lotId)}/label`, { method: "POST", body: JSON.stringify({ copies }) }),
  findLot: (warehouse: string, grn: string, packageName: string) =>
    api<{ lot_id: string }>(`/api/stock/labels/lot?${new URLSearchParams({ warehouse, grn, package: packageName })}`),
};

export const lotLabelUrl = (lotId: string, stamp: number | string = 0) => `/api/stock/batches/${encodeURIComponent(lotId)}/label.png?t=${stamp}`;

/** Roller_Buyer's toast words: printed, or why not and the saved job's file name. */
export function printResultText(result: PrintResult) {
  return result.status === "printed"
    ? `Отправлено на принтер: ${result.copies} шт.`
    : `${result.reason ?? "Не напечатано."} Задание: ${result.file.split(/[\\/]/).pop()}`;
}

function copiesOf(value: string) {
  const number = Math.trunc(Number(value));
  return number >= 1 && number <= COPIES_MAX ? number : null;
}

/** The outcome of a print, where the toast would be in Roller_Buyer. */
export function PrintOutcome({ result, error }: { result: PrintResult | null; error: string }) {
  if (error) return <div className="error-banner" role="alert">{error}</div>;
  if (!result) return null;
  return <div className={result.status === "printed" ? "success-banner" : "error-banner"} role="status">{printResultText(result)}</div>;
}

/** A lot's label as the printer gets it, copies and «Печать». `readOnly` — preview only (view mode). */
export function LotLabel({ lotId, readOnly = false, onPrinted }: { lotId: string; readOnly?: boolean; onPrinted?: (result: PrintResult) => void }) {
  const [copies, setCopies] = useState("1");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<PrintResult | null>(null);
  const [error, setError] = useState("");
  // The balance may change between two prints: the preview is asked again after each one.
  const [stamp, setStamp] = useState(() => Date.now());

  async function print(event: FormEvent) {
    event.preventDefault();
    const count = copiesOf(copies);
    if (count === null) { setError(`Копий — от 1 до ${COPIES_MAX}`); return; }
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const done = await labelsApi.printLot(lotId, count);
      setResult(done);
      setStamp(Date.now());
      onPrinted?.(done);
    } catch (reason) {
      setError(failureText(reason, "Этикетка не напечатана"));
    } finally {
      setBusy(false);
    }
  }

  return <div className="label-print">
    <img className="label-preview" src={lotLabelUrl(lotId, stamp)} alt="Этикетка партии" />
    {!readOnly && <form className="label-print-form" onSubmit={(event) => void print(event)}>
      <label>Копий<input className="label-copies" aria-label="Копий" inputMode="numeric" value={copies} onChange={(event) => setCopies(event.target.value)} /></label>
      <button className="primary" disabled={busy}>{busy ? "Печатаю…" : "Печать"}</button>
    </form>}
    <PrintOutcome result={result} error={error} />
  </div>;
}

/** «Оборудование» → «Печать этикеток ГРН»: which Windows queue, label size, code, gap from the calibration, heat, speed,
 *  offset; the preview of a sample label refreshes after «Сохранить»; «Пробная этикетка» prints it. */
export function PrinterSettingsCard({ readOnly = false }: { readOnly?: boolean }) {
  const [answer, setAnswer] = useState<SettingsAnswer | null>(null);
  const [form, setForm] = useState<Record<keyof PrintSettings, string> | null>(null);
  const [printers, setPrinters] = useState<{ printers: string[]; on_windows: boolean } | null>(null);
  const [stamp, setStamp] = useState(0);
  const [busy, setBusy] = useState<"" | "save" | "test">("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [result, setResult] = useState<PrintResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    labelsApi.settings().then((loaded) => {
      if (cancelled) return;
      setAnswer(loaded);
      setForm(asForm(loaded.settings));
    }, (reason) => { if (!cancelled) setError(failureText(reason, "Настройки печати не загрузились")); });
    labelsApi.printers().then((list) => { if (!cancelled) setPrinters(list); }, () => { if (!cancelled) setPrinters({ printers: [], on_windows: false }); });
    return () => { cancelled = true; };
  }, []);

  if (!form || !answer) return <section className="panel equipment-card" aria-label="Печать этикеток ГРН">
    <h2>Печать этикеток ГРН</h2>
    {error ? <div className="error-banner" role="alert">{error}</div> : <p className="page-sub">Загрузка…</p>}
  </section>;

  const set = (key: keyof PrintSettings) => (event: { target: { value: string } }) => setForm({ ...form, [key]: event.target.value });
  const known = printers?.printers ?? [];
  // A saved printer that Windows no longer lists stays selectable — the settings must not change by themselves.
  const choices = form.printer_name && !known.includes(form.printer_name) ? [form.printer_name, ...known] : known;

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    setBusy("save");
    setError("");
    setSaved("");
    setResult(null);
    try {
      const { settings } = await labelsApi.save(fromForm(form));
      setForm(asForm(settings));
      setStamp(Date.now());
      setSaved("Настройки сохранены");
    } catch (reason) {
      setError(failureText(reason, "Настройки не сохранились"));
    } finally {
      setBusy("");
    }
  }

  async function test() {
    setBusy("test");
    setError("");
    setSaved("");
    setResult(null);
    try {
      setResult(await labelsApi.test());
    } catch (reason) {
      setError(failureText(reason, "Пробная этикетка не напечатана"));
    } finally {
      setBusy("");
    }
  }

  return <section className="panel equipment-card" aria-label="Печать этикеток ГРН">
    <h2>Печать этикеток ГРН</h2>
    <form className="printer-settings" onSubmit={(event) => void save(event)}>
      <fieldset className="printer-fields" disabled={readOnly}>
        <label className="printer-name">Принтер
          <select value={form.printer_name} onChange={set("printer_name")}>
            <option value="">— не выбран (задание сохраняется файлом) —</option>
            {choices.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
        </label>
        <label>Код
          <select value={form.code_type} onChange={set("code_type")}>
            {CODE_TYPES.map(([key, label]) => <option key={key} value={key}>{answer.code_types[key] ?? label}</option>)}
          </select>
        </label>
        <label>Ширина × высота, мм
          <span className="printer-pair">
            <input aria-label="Ширина, мм" inputMode="decimal" value={form.width_mm} onChange={set("width_mm")} />
            <span>×</span>
            <input aria-label="Высота, мм" inputMode="decimal" value={form.height_mm} onChange={set("height_mm")} />
          </span>
        </label>
        <label>dpi
          <select value={form.dpi} onChange={set("dpi")}>
            <option value="300">300 (TSC MB341)</option>
            <option value="203">203</option>
          </select>
        </label>
        <label>Зазор, мм<input inputMode="decimal" value={form.gap_mm} onChange={set("gap_mm")} /></label>
        <label>Нагрев 0–15<input inputMode="numeric" value={form.density} onChange={set("density")} /></label>
        <label>Скорость<input inputMode="numeric" value={form.speed} onChange={set("speed")} /></label>
        <label>Сдвиг X / Y, мм
          <span className="printer-pair">
            <input aria-label="Сдвиг X, мм" inputMode="decimal" value={form.offset_x_mm} onChange={set("offset_x_mm")} />
            <span>/</span>
            <input aria-label="Сдвиг Y, мм" inputMode="decimal" value={form.offset_y_mm} onChange={set("offset_y_mm")} />
          </span>
        </label>
      </fieldset>
      <div className="label-print">
        <img className="label-preview" src={`/api/stock/labels/sample.png?t=${stamp}`} alt="Пробная этикетка" />
        {!readOnly && <div className="header-actions">
          <button className="primary" disabled={Boolean(busy)}>{busy === "save" ? "Сохраняю…" : "Сохранить"}</button>
          <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => void test()}>{busy === "test" ? "Печатаю…" : "Пробная этикетка"}</button>
        </div>}
      </div>
    </form>
    {saved && <div className="success-banner" role="status">{saved}</div>}
    <PrintOutcome result={result} error={error} />
    <p className="page-sub">
      Зазор между этикетками покажет встроенная калибровка принтера (зажать кнопку FEED при включении). Если оттиск съехал — поправьте
      сдвигом, макет трогать не нужно. Предпросмотр — ровно те точки, что уйдут в принтер; он обновляется после «Сохранить».
    </p>
    <p className="page-sub">
      {printers && !printers.on_windows
        ? "Сервер ERP работает не на Windows: в очередь печати ничего не отправляется, задания только сохраняются файлами"
        : "Принтеры — из Windows компьютера, на котором работает сервер ERP (принтер подключён к нему по USB)"}
      {` · задания: ${answer.print_dir}`}
    </p>
  </section>;
}

function asForm(settings: PrintSettings): Record<keyof PrintSettings, string> {
  return Object.fromEntries(Object.entries(settings).map(([key, value]) => [key, String(value)])) as Record<keyof PrintSettings, string>;
}

function fromForm(form: Record<keyof PrintSettings, string>): PrintSettings {
  const number = (value: string) => Number(value.replace(",", ".").trim());
  return {
    printer_name: form.printer_name,
    code_type: form.code_type as LabelCodeType,
    width_mm: number(form.width_mm),
    height_mm: number(form.height_mm),
    dpi: number(form.dpi),
    gap_mm: number(form.gap_mm),
    density: number(form.density),
    speed: number(form.speed),
    offset_x_mm: number(form.offset_x_mm),
    offset_y_mm: number(form.offset_y_mm),
  };
}

/** One GRN lot that receiving has just put on stock (or is putting): the lot is looked up by ГРН + пакет, since the
 *  warehouse takes the receipt a moment later in the background. */
export type ReceiptLot = { key: string; warehouse: string; grn: string; package: string; title: string; quantity: string };

/** «Печать этикеток» right after receiving: copies per lot, «Печать» per lot or for all, a preview on demand. */
export function ReceiptLabels({ lots, onClose }: { lots: ReceiptLot[]; onClose?: () => void }) {
  const [copies, setCopies] = useState<Record<string, string>>({});
  // ГРН + пакет → lot id, once found (a ref: «Печать всех» runs several lookups in one go).
  const found = useRef<Record<string, string>>({});
  const [shown, setShown] = useState("");
  const [stamp, setStamp] = useState(0);
  const [busy, setBusy] = useState("");
  const [outcome, setOutcome] = useState<Record<string, { result?: PrintResult; error?: string }>>({});

  async function lotIdOf(lot: ReceiptLot) {
    const known = found.current[lot.key];
    if (known) return known;
    const { lot_id } = await labelsApi.findLot(lot.warehouse, lot.grn, lot.package);
    found.current[lot.key] = lot_id;
    return lot_id;
  }

  async function preview(lot: ReceiptLot) {
    if (shown === lot.key) { setShown(""); return; }
    try {
      await lotIdOf(lot);
      setShown(lot.key);
    } catch (reason) {
      setOutcome((current) => ({ ...current, [lot.key]: { error: failureText(reason, "Партия не найдена") } }));
    }
  }

  async function printOne(lot: ReceiptLot) {
    const count = copiesOf(copies[lot.key] ?? "1");
    if (count === null) {
      setOutcome((current) => ({ ...current, [lot.key]: { error: `Копий — от 1 до ${COPIES_MAX}` } }));
      return;
    }
    try {
      const result = await labelsApi.printLot(await lotIdOf(lot), count);
      setOutcome((current) => ({ ...current, [lot.key]: { result } }));
      setStamp(Date.now());
    } catch (reason) {
      setOutcome((current) => ({ ...current, [lot.key]: { error: failureText(reason, "Этикетка не напечатана") } }));
    }
  }

  async function run(key: string, targets: ReceiptLot[]) {
    if (busy) return;
    setBusy(key);
    try {
      for (const lot of targets) await printOne(lot);
    } finally {
      setBusy("");
    }
  }

  if (!lots.length) return null;
  return <section className="receipt-labels" aria-label="Печать этикеток">
    <div className="receipt-labels-head">
      <h3>Печать этикеток</h3>
      <div className="header-actions">
        {lots.length > 1 && <button type="button" className="primary small" disabled={Boolean(busy)} onClick={() => void run("all", lots)}>{busy === "all" ? "Печатаю…" : `Печать всех (${lots.length})`}</button>}
        {onClose && <button type="button" className="link-button" onClick={onClose}>Скрыть</button>}
      </div>
    </div>
    <div className="table-wrap">
      <table className="grid compact receipt-labels-table">
        <thead><tr><th>ГРН</th><th className="grow">Позиция</th><th>Кол-во</th><th>Копий</th><th /></tr></thead>
        <tbody>
          {lots.map((lot) => {
            const done = outcome[lot.key];
            return [
              <tr key={lot.key}>
                <td className="mono">{lot.grn}{lot.package ? ` / ${lot.package}` : ""}</td>
                <td className="grow">{lot.title}</td>
                <td className="num">{lot.quantity}</td>
                <td><input className="label-copies" aria-label={`Копий: ${lot.grn}${lot.package ? ` / ${lot.package}` : ""}`} inputMode="numeric"
                  value={copies[lot.key] ?? "1"} onChange={(event) => setCopies((current) => ({ ...current, [lot.key]: event.target.value }))} /></td>
                <td className="receipt-labels-actions">
                  <button type="button" className="secondary small" onClick={() => void preview(lot)}>{shown === lot.key ? "Скрыть" : "Этикетка"}</button>
                  <button type="button" className="primary small" disabled={Boolean(busy)} onClick={() => void run(lot.key, [lot])}>{busy === lot.key ? "Печатаю…" : "Печать"}</button>
                </td>
              </tr>,
              shown === lot.key || done ? <tr key={`${lot.key}:more`} className="receipt-labels-more"><td colSpan={5}>
                {shown === lot.key && found.current[lot.key] && <img className="label-preview" src={lotLabelUrl(found.current[lot.key], stamp)} alt={`Этикетка ${lot.grn}`} />}
                {done && <PrintOutcome result={done.result ?? null} error={done.error ?? ""} />}
              </td></tr> : null,
            ];
          })}
        </tbody>
      </table>
    </div>
  </section>;
}
