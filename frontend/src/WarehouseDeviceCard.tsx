import { LabelDesigner } from "./LabelDesigner";
import { useCanAct } from "./rights";
import { useCallback, useRef, useState, type ReactNode } from "react";
import { failureText } from "./api";
import { EMPTY, formatMoment } from "./format";
import { usePolled, type MachineRow, type Tone } from "./machinesApi";
import { PageWindow } from "./PageWindow";
import {
  warehouseDevicesApi, type DocumentPrint, type LabelJob, type LabelPrinterView, type MfpView, type PlainScan, type ScanColor,
  type WarehouseStation,
} from "./warehouseDevicesApi";

/* Карточки «Принтеры склада» (владелец 20.11.2026: «по принтерам тоже функционала нет никакого»; про МФУ: «это не только
   сканер, я хочу и печатать на нём»). Оболочка карточки станка: строка состояния → главное действие → последние
   задания → свёрнутое «Подключение». */

type Note = { text: string; tone: Tone } | null;

const LABEL_STATUS: Record<string, [string, Tone]> = {
  pending: ["В очереди", "neutral"], taken: ["Печатает", "neutral"], printed: ["Напечатано", "ok"], spooled: ["Отправлено", "ok"],
  failed: ["Ошибка", "danger"], unknown: ["Не подтверждено", "warn"], cancelled: ["Отменено", "neutral"],
};
const SCAN_STATUS: Record<PlainScan["status"], [string, Tone]> = {
  pending: ["Ждёт ПК", "neutral"], claimed: ["Сканирует", "neutral"], done: ["Готово", "ok"], failed: ["Ошибка", "danger"],
};
const PRINT_STATUS: Record<DocumentPrint["status"], [string, Tone]> = {
  pending: ["В очереди", "neutral"], taken: ["Печатает", "neutral"], spooled: ["Отправлено на МФУ", "ok"], failed: ["Ошибка", "danger"],
  unknown: ["Не подтверждено", "warn"], cancelled: ["Отменено", "neutral"],
};
const COLORS: [ScanColor, string][] = [["gray", "Ч/б"], ["color", "Цветной"]];
const DPIS = [150, 300, 600];
const COPIES_MAX = 50;
/** Агент, который печатает документы на МФУ (tools/print-agent, 2.12.0). */
export const DOCUMENT_AGENT = "2.12.0";

const toneText = (tone: Tone) => (tone === "neutral" ? "" : `tone-${tone}`);
const moment = (value: string | null | undefined) => (value ? formatMoment(value) : EMPTY);

function olderThan(version: string, wanted: string): boolean {
  const parts = (text: string) => (text.match(/\d+/g) ?? []).map(Number);
  const have = parts(version), need = parts(wanted);
  for (let i = 0; i < need.length; i++) {
    if ((have[i] ?? 0) !== need[i]) return (have[i] ?? 0) < need[i];
  }
  return false;
}

function StatusStrip({ title, tone, link, hint, label }: { title: string; tone: Tone; link: string; hint?: string; label: string }) {
  return <div className={`machine-status${tone === "warn" || tone === "danger" ? ` is-${tone}` : ""}`} role="status" aria-label={label}>
    <strong className={`machine-status-title ${toneText(tone)}`}>{title}</strong>
    <span className="machine-status-link">{link}</span>
    {hint && <span className="machine-status-hint">{hint}</span>}
  </div>;
}

function Connection({ facts }: { facts: [string, string][] }) {
  return <details className="machine-fold machine-connection">
    <summary>Подключение</summary>
    <dl className="machine-summary">
      {facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value || EMPTY}</dd></div>)}
    </dl>
  </details>;
}

function NoteLine({ note }: { note: Note }) {
  return <p className={`machine-cmd-note device-note ${note ? toneText(note.tone) : ""}`} role="status">{note?.text ?? ""}</p>;
}

function Shell({ title, close, width = 1100, children }: { title: string; close: () => void; width?: number; children: ReactNode }) {
  return <PageWindow label={title} close={close} width={width} dismissable>
    <section className="panel machine-card device-card">
      <header className="machine-card-head">
        <h2>{title}</h2>
        <button type="button" className="secondary small" onClick={close}>Закрыть</button>
      </header>
      {children}
    </section>
  </PageWindow>;
}

/** Ключ повтора печати: тот же запрос после потерянного ответа идёт с тем же ключом (сервер вернёт уже поставленное
    задание), другой запрос или повтор после успеха — с новым. */
function useRetryKey() {
  const last = useRef<{ request: string; key: string } | null>(null);
  return useRef({
    key(request: string) {
      if (last.current?.request !== request) last.current = { request, key: crypto.randomUUID() };
      return last.current.key;
    },
    done() { last.current = null; },
  }).current;
}

/** Действие с итогом рядом с кнопкой: «Отправлено 10:05» или причина отказа. */
function useAction(reload: () => void) {
  const [busy, setBusy] = useState("");
  const run = useCallback(async (key: string, action: () => Promise<unknown>, done: string, setNote: (note: Note) => void) => {
    setBusy(key);
    try {
      await action();
      setNote({ text: `${done} ${formatMoment(new Date().toISOString())}`, tone: "ok" });
      reload();
    } catch (reason) {
      setNote({ text: failureText(reason, "Не получилось — попробуйте ещё раз"), tone: "danger" });
    } finally {
      setBusy("");
    }
  }, [reload]);
  return { busy, run };
}

// --- принтер этикеток ------------------------------------------------------------------------------------------

function labelStatus(view: LabelPrinterView): { title: string; tone: Tone; hint?: string } {
  const queue = view.agent.queue;
  if (!view.online) {
    return { title: "Нет связи с ПК склада", tone: "danger",
      hint: `Включите ПК склада и проверьте, что агент ERP запущен${queue ? ` — в очереди ${queue}` : ""}` };
  }
  if (view.agent.last_error) return { title: "Ошибка печати", tone: "danger", hint: view.agent.last_error };
  if (queue) return { title: `Печатает · в очереди ${queue}`, tone: "neutral" };
  return { title: "Готов", tone: "neutral" };
}

export function LabelPrinterCardView({ view, reload }: { view: LabelPrinterView; reload: () => void }) {
  const [note, setNote] = useState<Note>(null);
  const [chosen, setChosen] = useState<LabelJob | null>(null);
  const [copies, setCopies] = useState(1);
  const { busy, run } = useAction(reload);
  const status = labelStatus(view);
  const choose = (job: LabelJob) => {
    if (!job.lot_id) return;
    setChosen(job);
    setCopies(job.copies);
  };
  const link = [view.agent.last_seen ? `Связь ${formatMoment(view.agent.last_seen)}` : "Агент ещё не выходил на связь", view.agent.host]
    .filter(Boolean).join(" · ");
  return <>
    <StatusStrip title={status.title} tone={status.tone} link={link} hint={status.hint} label="Состояние принтера" />
    <div className="device-actions">
      <div className="machine-cmd">
        <button type="button" className="secondary" disabled={!!busy}
          onClick={() => void run("test", () => warehouseDevicesApi.testLabel(), "Пробная этикетка отправлена", setNote)}>
          Пробная этикетка</button>
      </div>
      <div className="device-reprint" aria-label="Перепечатать">
        {chosen?.lot_id
          ? <>
            <span className="device-reprint-what"><strong>{chosen.what}</strong>{chosen.article && <span className="machine-mono"> · {chosen.article}</span>}</span>
            <label className="device-copies">Копий
              <input type="number" inputMode="numeric" min={1} max={COPIES_MAX} value={copies} name="label-copies"
                onChange={(event) => setCopies(Math.min(COPIES_MAX, Math.max(1, Number(event.target.value) || 1)))} /></label>
            <button type="button" className="primary" disabled={!!busy}
              onClick={() => void run("reprint", () => warehouseDevicesApi.reprintLot(chosen.lot_id as string, copies),
                `${chosen.what}: этикетка отправлена`, setNote)}>Перепечатать</button>
            <button type="button" className="secondary small" onClick={() => setChosen(null)}>Снять выбор</button>
          </>
          : <span className="machine-muted">Перепечатать: выберите партию в списке</span>}
      </div>
    </div>
    <NoteLine note={note} />
    <section aria-label="Последние этикетки">
      <h3 className="device-h3">Последние этикетки</h3>
      {view.jobs.length === 0
        ? <p className="empty">Этикеток ещё не печатали — нажмите «Пробная этикетка»</p>
        : <div className="device-table-wrap"><table className="machine-table device-table">
          <thead><tr><th>Когда</th><th>Что</th><th>Склад</th><th>Артикул</th><th>Наименование</th><th className="num">Копий</th><th>Кто</th><th>Состояние</th></tr></thead>
          <tbody>{view.jobs.map((job) => {
            const [text, tone] = LABEL_STATUS[job.status] ?? [job.status, "neutral"];
            const pickable = Boolean(job.lot_id);
            return <tr key={job.id} className={`${pickable ? "is-pickable" : ""}${chosen?.id === job.id ? " is-chosen" : ""}`}
              tabIndex={pickable ? 0 : undefined} aria-selected={pickable ? chosen?.id === job.id : undefined}
              onClick={() => choose(job)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); choose(job); } }}>
              <td className="machine-mono nowrap">{moment(job.at)}</td>
              <td className="nowrap"><strong>{job.what}</strong></td>
              <td>{job.warehouse || EMPTY}</td>
              <td className="machine-mono">{job.article || EMPTY}</td>
              <td className="device-name" title={job.name}>{job.name || EMPTY}</td>
              <td className="num">{job.copies}</td>
              <td>{job.by || EMPTY}</td>
              <td className={toneText(tone)} title={job.error || undefined}>{text}{job.error && <small className="device-error">{job.error}</small>}</td>
            </tr>;
          })}</tbody>
        </table></div>}
    </section>
    <Connection facts={[["ПК", view.agent.host], ["Очередь Windows", view.agent.printer],
      ["Последний сигнал", view.agent.last_seen ? formatMoment(view.agent.last_seen) : ""]]} />
  </>;
}

function LabelPrinterCard({ title, close }: { title: string; close: () => void }) {
  const canEdit = useCanAct("warehouse");
  const [dirty,setDirty] = useState(false);
  const [confirmClose,setConfirmClose] = useState(false);
  const tryClose = () => dirty ? setConfirmClose(true) : close();
  const load = useCallback((signal: AbortSignal) => warehouseDevicesApi.labels(signal), []);
  const { data, error, reload } = usePolled(load, 5000, "Не удалось загрузить принтер этикеток");
  return <Shell title={title} close={tryClose} width={1440}>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!data && !error && <p className="empty">Загрузка…</p>}
    {data && <><LabelPrinterCardView view={data} reload={reload} />
      <section aria-label="Шаблоны этикеток"><h3>Шаблоны этикеток</h3><LabelDesigner initialKind={new URLSearchParams(location.search).get("label_kind")==="intake"?"intake":"lot"} initialCategory="PPM" printerName={data.agent.printer} readOnly={!canEdit} onDirty={setDirty}/></section>
    </>}
    {confirmClose && <PageWindow label="Несохранённый шаблон" close={()=>setConfirmClose(false)} width={480}><section className="panel"><h2>Закрыть без сохранения?</h2><p>В шаблонах этикеток есть несохранённые изменения.</p><div className="header-actions"><button className="primary" onClick={()=>setConfirmClose(false)}>Вернуться к шаблонам</button><button className="secondary" onClick={close}>Закрыть без сохранения</button></div></section></PageWindow>}
  </Shell>;
}

// --- МФУ -------------------------------------------------------------------------------------------------------

function mfpStatus(view: MfpView): { title: string; tone: Tone; hint?: string } {
  const pc = view.pc.name;
  if (!view.online) return { title: "Нет связи с ПК", tone: "danger", hint: `Включите ПК ${pc} — МФУ подключено к нему, и проверьте, что агент ERP запущен` };
  const scanning = view.scans.find((scan) => scan.status === "pending" || scan.status === "claimed");
  if (scanning) return { title: "Сканирует", tone: "neutral", hint: "Лист на стекле МФУ, крышка закрыта — скан появится ниже" };
  const printing = view.prints.find((job) => job.status === "pending" || job.status === "taken");
  if (printing) return { title: `Печатает «${printing.title}»`, tone: "neutral", hint: `${printing.pages} стр. × ${printing.copies}` };
  const scanner = view.scanners.some((item) => item.present);
  const printer = view.printers.some((item) => item.present);
  if (!scanner && view.scanners.length) return { title: "Сканер МФУ не найден", tone: "warn", hint: "Проверьте, что МФУ включено и подключено к ПК кабелем USB" };
  if (!printer) {
    return { title: "Готово к скану · печать не настроена", tone: "warn",
      hint: olderThan(view.pc.version, DOCUMENT_AGENT)
        ? `Обновите агент ERP на ПК ${pc} до ${DOCUMENT_AGENT} — тогда ERP увидит принтер МФУ`
        : "Агент не нашёл очередь МФУ в Windows — проверьте, что принтер МФУ установлен на ПК" };
  }
  return { title: "Готово", tone: "neutral" };
}

function ScanBlock({ view, reload, machineId }: { view: MfpView; reload: () => void; machineId: string }) {
  const [color, setColor] = useState<ScanColor>("gray");
  const [dpi, setDpi] = useState(300);
  const [scanner, setScanner] = useState("");
  const [note, setNote] = useState<Note>(null);
  const [copies, setCopies] = useState(1);
  const { busy, run } = useAction(reload);
  const retry = useRetryKey();
  const live = view.scanners.filter((item) => item.present);
  const chosen = live.find((item) => item.id === scanner)?.id ?? live[0]?.id ?? "";
  const open = view.scans.find((scan) => scan.status === "pending" || scan.status === "claimed");
  const last = view.scans.find((scan) => scan.status === "done" && scan.file);
  const failed = !open && view.scans[0]?.status === "failed" ? view.scans[0] : null;
  const printer = view.printers.find((item) => item.present)?.name ?? "";
  const why = !view.online ? "нет связи с ПК" : !chosen ? "сканер не найден" : open ? "идёт скан" : "";
  const start = (append?: string) => void run("scan", () => warehouseDevicesApi.scan(machineId, {
    scanner: chosen, color, dpi, ...(append ? { append_to: append } : {}) }), append ? "Следующая страница — сканирую" : "Сканирую", setNote);
  return <section className="device-block" aria-label="Сканирование">
    <h3 className="device-h3">Сканировать</h3>
    <div className="device-options">
      <div className="seg-tabs sub" role="radiogroup" aria-label="Цвет">
        {COLORS.map(([id, label]) => <button key={id} type="button" role="radio" aria-checked={color === id} className={color === id ? "on" : ""}
          onClick={() => setColor(id)}>{label}</button>)}
      </div>
      <div className="seg-tabs sub" role="radiogroup" aria-label="Разрешение">
        {DPIS.map((value) => <button key={value} type="button" role="radio" aria-checked={dpi === value} className={dpi === value ? "on" : ""}
          onClick={() => setDpi(value)}>{value} dpi</button>)}
      </div>
      {live.length > 1 && <label className="device-field">Сканер
        <select value={chosen} onChange={(event) => setScanner(event.target.value)} name="mfp-scanner">
          {live.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>}
    </div>
    <div className="device-actions">
      <button type="button" className="primary device-main" disabled={!!busy || !!why} onClick={() => start()}>Сканировать</button>
      {last && <button type="button" className="secondary device-main" disabled={!!busy || !!why} onClick={() => start(last.id)}
        title={`Новая страница попадёт в конец скана от ${moment(last.at)}`}>Добавить страницу к скану {moment(last.at)}</button>}
      {open && <button type="button" className="secondary device-main" disabled={!!busy}
        onClick={() => void run("cancel", () => warehouseDevicesApi.cancelScan(open.id), "Скан отменён", setNote)}>Отменить скан</button>}
    </div>
    {why && !open && <p className="machine-cmd-note machine-muted">Сканировать нельзя: {why}</p>}
    <NoteLine note={failed ? { text: `Скан не получился: ${failed.error}`, tone: "danger" } : note} />
    {last && <div className="device-last" aria-label="Последний скан">
      <span>Скан {moment(last.at)} · {last.pages ?? 1} стр. · {last.by}</span>
      <a className="device-link" href={last.file} target="_blank" rel="noreferrer">Открыть</a>
      <a className="device-link" href={`${last.file}?download=1`}>Скачать PDF</a>
      {printer && <span className="device-inline">
        <label className="device-copies">Копий<input type="number" inputMode="numeric" min={1} max={COPIES_MAX} value={copies} name="scan-copies"
          onChange={(event) => setCopies(Math.min(COPIES_MAX, Math.max(1, Number(event.target.value) || 1)))} /></label>
        <button type="button" className="secondary small" disabled={!!busy}
          onClick={() => void run("copy", async () => {
            await warehouseDevicesApi.printScan(last.id, printer, copies, retry.key(JSON.stringify(["copy", last.id, printer, copies])));
            retry.done();
          }, "Копия скана отправлена на МФУ", setNote)}>
          Напечатать копию</button>
      </span>}
    </div>}
  </section>;
}

function PrintBlock({ view, reload, machineId }: { view: MfpView; reload: () => void; machineId: string }) {
  const [file, setFile] = useState<File | null>(null);
  const [copies, setCopies] = useState(1);
  const [printer, setPrinter] = useState("");
  const [note, setNote] = useState<Note>(null);
  const picker = useRef<HTMLInputElement>(null);
  const { busy, run } = useAction(reload);
  const retry = useRetryKey();
  const testRetry = useRetryKey();
  const live = view.printers.filter((item) => item.present);
  const chosen = live.find((item) => item.name === printer)?.name ?? live[0]?.name ?? "";
  const why = !view.online ? "нет связи с ПК" : !chosen ? "принтер МФУ не найден" : "";
  const send = () => {
    if (!file) return;
    void run("print", async () => {
      const request = JSON.stringify(["print", chosen, copies, file.name, file.size, file.lastModified]);
      await warehouseDevicesApi.print(machineId, chosen, copies, file, retry.key(request));
      retry.done();
      setFile(null);
      if (picker.current) picker.current.value = "";
    }, `«${file.name}» отправлен на МФУ`, setNote);
  };
  return <section className="device-block" aria-label="Печать">
    <h3 className="device-h3">Печать</h3>
    <div className="device-options">
      <div className="device-field device-file">
        <span>Файл — PDF, JPG или PNG, до 20 МБ</span>
        <span className="device-file-pick">
          <button type="button" className="secondary" onClick={() => picker.current?.click()} disabled={!!busy}>Выбрать файл</button>
          <span className={file ? "device-file-name" : "device-file-name machine-muted"} title={file?.name}>{file ? file.name : "Файл не выбран"}</span>
        </span>
        <input ref={picker} type="file" hidden name="mfp-file" aria-label="Файл для печати"
          accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
      </div>
      <label className="device-copies">Копий
        <input type="number" inputMode="numeric" min={1} max={COPIES_MAX} value={copies} name="mfp-copies"
          onChange={(event) => setCopies(Math.min(COPIES_MAX, Math.max(1, Number(event.target.value) || 1)))} /></label>
      {live.length > 1 && <label className="device-field">Принтер
        <select value={chosen} onChange={(event) => setPrinter(event.target.value)} name="mfp-printer">
          {live.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
        </select></label>}
    </div>
    <div className="device-actions">
      <button type="button" className="primary device-main" disabled={!!busy || !!why || !file} onClick={send}>
        {file ? `Печатать${copies > 1 ? ` · ${copies} коп.` : ""}` : "Печатать — выберите файл"}</button>
      <button type="button" className="secondary device-main" disabled={!!busy || !!why}
        onClick={() => void run("test", async () => {
          await warehouseDevicesApi.testPage(machineId, chosen, testRetry.key(JSON.stringify(["test", chosen])));
          testRetry.done();
        }, "Пробная страница отправлена", setNote)}>
        Пробная страница</button>
    </div>
    {why && <p className="machine-cmd-note machine-muted">Печатать нельзя: {why}</p>}
    <NoteLine note={note} />
  </section>;
}

function ScanList({ scans }: { scans: PlainScan[] }) {
  return <section aria-label="Последние сканы">
    <h3 className="device-h3">Последние сканы</h3>
    {scans.length === 0 ? <p className="empty">Сканов ещё нет — положите лист на стекло и нажмите «Сканировать»</p>
      : <div className="device-table-wrap"><table className="machine-table device-table">
        <thead><tr><th>Когда</th><th>Кто</th><th className="num">Стр.</th><th>Режим</th><th>Состояние</th><th>PDF</th></tr></thead>
        <tbody>{scans.map((scan) => {
          const [text, tone] = SCAN_STATUS[scan.status] ?? [scan.status, "neutral"];
          return <tr key={scan.id}>
            <td className="machine-mono nowrap">{moment(scan.at)}</td>
            <td>{scan.by || EMPTY}</td>
            <td className="num">{scan.pages ?? EMPTY}</td>
            <td className="nowrap">{scan.color === "color" ? "Цветной" : "Ч/б"}{scan.dpi ? ` · ${scan.dpi} dpi` : ""}</td>
            <td className={toneText(tone)}>{text}{scan.status === "failed" && scan.error && <small className="device-error">{scan.error}</small>}</td>
            <td className="nowrap">{scan.file ? <><a className="device-link" href={scan.file} target="_blank" rel="noreferrer">Открыть</a>{" · "}
              <a className="device-link" href={`${scan.file}?download=1`}>Скачать</a></> : EMPTY}</td>
          </tr>;
        })}</tbody>
      </table></div>}
  </section>;
}

function PrintList({ prints, reload }: { prints: DocumentPrint[]; reload: () => void }) {
  const [error, setError] = useState("");
  const cancel = async (id: string) => {
    try { await warehouseDevicesApi.cancelPrint(id); setError(""); reload(); }
    catch (reason) { setError(failureText(reason, "Не удалось отменить")); }
  };
  return <section aria-label="Последние задания печати">
    <h3 className="device-h3">Последние задания печати</h3>
    {error && <p className="error-banner" role="alert">{error}</p>}
    {prints.length === 0 ? <p className="empty">Ничего не печатали — выберите файл и нажмите «Печатать»</p>
      : <div className="device-table-wrap"><table className="machine-table device-table">
        <thead><tr><th>Когда</th><th>Документ</th><th className="num">Стр. × коп.</th><th>Кто</th><th>Состояние</th></tr></thead>
        <tbody>{prints.map((job) => {
          const [text, tone] = PRINT_STATUS[job.status] ?? [job.status, "neutral"];
          return <tr key={job.id}>
            <td className="machine-mono nowrap">{moment(job.at)}</td>
            <td className="device-name" title={job.title}>{job.title}{job.source === "scan" && <span className="machine-muted"> · копия скана</span>}</td>
            <td className="num nowrap">{job.pages} × {job.copies}</td>
            <td>{job.by || EMPTY}</td>
            <td className={toneText(tone)}>{text}
              {job.status === "pending" && <button type="button" className="secondary small device-cancel" onClick={() => void cancel(job.id)}>Отменить</button>}
              {(job.status === "failed" || job.status === "unknown") && job.error && <small className="device-error">{job.error}</small>}
            </td>
          </tr>;
        })}</tbody>
      </table></div>}
  </section>;
}

export function MfpCardView({ view, reload, machineId }: { view: MfpView; reload: () => void; machineId: string }) {
  const status = mfpStatus(view);
  const link = [`ПК ${view.pc.name}`, view.pc.last_seen ? `связь ${formatMoment(view.pc.last_seen)}` : "агент ещё не выходил на связь"].join(" · ");
  return <>
    <StatusStrip title={status.title} tone={status.tone} link={link} hint={status.hint} label="Состояние МФУ" />
    <div className="device-columns">
      <ScanBlock view={view} reload={reload} machineId={machineId} />
      <PrintBlock view={view} reload={reload} machineId={machineId} />
    </div>
    <ScanList scans={view.scans} />
    <PrintList prints={view.prints} reload={reload} />
    <Connection facts={[["ПК", view.pc.name], ["Версия агента", view.pc.version],
      ["Последний сигнал", view.pc.last_seen ? formatMoment(view.pc.last_seen) : ""],
      ["Сканер", view.scanners.map((item) => `${item.name}${item.present ? "" : " (не найден)"}`).join("; ")],
      ["Принтер", view.printers.map((item) => `${item.name}${item.driver && item.driver !== item.name ? ` · ${item.driver}` : ""}${item.present ? "" : " (не найден)"}`).join("; ")
        || (olderThan(view.pc.version, DOCUMENT_AGENT) ? `агент до ${DOCUMENT_AGENT} принтер МФУ не сообщает` : "")]]} />
  </>;
}

function MfpCard({ title, machineId, close }: { title: string; machineId: string; close: () => void }) {
  const load = useCallback((signal: AbortSignal) => warehouseDevicesApi.mfp(machineId, signal), [machineId]);
  const { data, error, reload } = usePolled(load, 3000, "Не удалось загрузить МФУ");
  return <Shell title={title} close={close} width={1280}>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!data && !error && <p className="empty">Загрузка…</p>}
    {data && <MfpCardView view={data} reload={reload} machineId={machineId} />}
  </Shell>;
}

/** Карточка плитки «Принтеры склада»: принтер этикеток или МФУ (`row.station` от сервера). */
export function WarehouseDeviceCard({ row, station, close }: { row: MachineRow; station: WarehouseStation; close: () => void }) {
  const title = [row.inventory, row.name].filter(Boolean).join(" · ");
  return station.kind === "labels"
    ? <LabelPrinterCard title={title} close={close} />
    : <MfpCard title={title} machineId={station.machine_id} close={close} />;
}
