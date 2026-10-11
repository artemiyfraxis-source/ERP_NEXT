import { useEffect, useRef, useState } from "react";
import "./export-menu.css";
import type { HarnessExportPort } from "./port";
import { ExportError, PNG_SCALES, renderPng, renderSvg, type PngScale } from "./render";
import { buildSheetHtml, exportFileBase, formatPrintDate, printSheet } from "./sheet-html";
import { placeOnSheet, SHEETS, type SheetId } from "./sheet";

const DEFAULT_SCALE: PngScale = 2;
const DEFAULT_SHEET: SheetId = "A3";

/** Меню «Выгрузить» в шапке страницы чертежа: PNG (растр) и PDF (печать листа браузером). Доступно и в режиме просмотра.
 *  `port` отдаёт порт отрисовки чертежа (null — редактор ещё грузится), `version` — номер сохранённой версии. */
export function ExportMenu({ project, index, heading, port, version }: {
  project: string; index: string; heading: string; port: () => HarnessExportPort | null; version: () => number | null;
}) {
  const [open, setOpen] = useState(false);
  const [pngScale, setPngScale] = useState<PngScale>(DEFAULT_SCALE);
  const [sheet, setSheet] = useState<SheetId>(DEFAULT_SHEET);
  const [mode, setMode] = useState<"fit" | "true">("fit");
  const [message, setMessage] = useState<{ text: string; suggestion: SheetId | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  const current = open ? port() : null;
  const denominator = current && current.view === "drawing" ? current.realSizeDenominator : null;

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);

  const fail = (reason: unknown) => setMessage({ text: reason instanceof ExportError ? reason.message : "Не удалось выгрузить чертёж", suggestion: null });

  async function downloadPng() {
    const live = port();
    if (!live) return;
    setBusy(true); setMessage(null);
    try {
      const blob = await renderPng(live, pngScale);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url; link.download = `${exportFileBase(project, index, version())}.png`;
      document.body.appendChild(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
      setOpen(false);
    } catch (reason) { fail(reason); } finally { setBusy(false); }
  }

  function printPdf() {
    const live = port();
    if (!live) return;
    setMessage(null);
    try {
      const svg = renderSvg(live);
      const effective = live.view === "drawing" ? live.realSizeDenominator : null;
      const placement = placeOnSheet({ width: svg.frame.width, height: svg.frame.height }, sheet, { kind: mode }, effective === null ? null : { denominator: effective });
      if (!placement.ok) { setMessage({ text: placement.hint, suggestion: placement.suggestion }); return; }
      const saved = version();
      const html = buildSheetHtml({
        svg, placement, title: exportFileBase(project, index, saved),
        data: { position: heading, project, version: saved, date: formatPrintDate(new Date()), realSizeOn: effective !== null },
      });
      if (!printSheet(html)) { setMessage({ text: "Браузер заблокировал окно печати — разрешите всплывающие окна для этой страницы.", suggestion: null }); return; }
      setOpen(false);
    } catch (reason) { fail(reason); }
  }

  const trueScaleOff = denominator === null;
  return <span className="harness-export" ref={root}>
    <button type="button" className="secondary small" aria-haspopup="dialog" aria-expanded={open} onClick={() => { setOpen(!open); setMessage(null); }}
      title="Скачать чертёж картинкой или напечатать листом">Выгрузить ▾</button>
    {open && <div className="harness-export-panel" role="dialog" aria-label="Выгрузка чертежа">
      <section>
        <h3>PNG</h3>
        <div className="harness-export-row" role="radiogroup" aria-label="Разрешение PNG">
          {PNG_SCALES.map((value) => <button key={value} type="button" role="radio" aria-checked={pngScale === value}
            className={`secondary small${pngScale === value ? " active" : ""}`} onClick={() => setPngScale(value)}>{value}×</button>)}
        </div>
        <button type="button" className="primary small" disabled={busy || !current} onClick={() => void downloadPng()}>Скачать PNG</button>
      </section>
      <section>
        <h3>PDF</h3>
        <label>Лист
          <select value={sheet} onChange={(event) => { setSheet(event.target.value as SheetId); setMessage(null); }}>
            {SHEETS.map((item) => <option key={item.id} value={item.id}>{item.label}, {item.widthMm} × {item.heightMm} мм</option>)}
          </select>
        </label>
        <label>Масштаб
          <select value={trueScaleOff ? "fit" : mode} onChange={(event) => { setMode(event.target.value as "fit" | "true"); setMessage(null); }}>
            <option value="fit">По листу</option>
            <option value="true" disabled={trueScaleOff}>{denominator === null ? "В масштабе М 1:N (нужны реальные размеры)" : `В масштабе М 1:${denominator}`}</option>
          </select>
        </label>
        <button type="button" className="primary small" disabled={!current} onClick={printPdf}>Печать / Сохранить как PDF</button>
      </section>
      {message && <div className="harness-export-message" role="alert">
        {message.text}
        {message.suggestion && <button type="button" className="secondary small" onClick={() => { setSheet(message.suggestion!); setMessage(null); }}>
          Взять {SHEETS.find((item) => item.id === message.suggestion)!.label}</button>}
      </div>}
    </div>}
  </span>;
}
