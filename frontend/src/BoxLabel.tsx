// Код коробки (владелец, 13.10.2026): проект·КБ·дата·код сотрудника·номер — «180К.11·КБ1·13.10·Б07·3». После «Сдать»
// монтажник видит его крупно, копирует или печатает этикетку; в списках коробок код — главный, номер учёта — под ним.
import { Fragment, useEffect, useRef, useState } from "react";
import type { ProductionBox } from "./api";
import { formatMoment } from "./format";
import { openPrintHtml, pageRule } from "./print";
import "./boxLabel.css";

type Named = Pick<ProductionBox, "code" | "number">;

/** Как назвать коробку одной строкой: код с коробки, у старых — номер учёта. */
export function boxTitle(box: Named): string {
  return box.code || box.number || "без номера";
}

const LOOKALIKE: Record<string, string> = { A: "А", B: "В", C: "С", E: "Е", H: "Н", K: "К", M: "М", O: "О", P: "Р", T: "Т", X: "Х", Y: "У" };

/** Ключ поиска по коду — как на сервере (`box_code_key`): без регистра, пробелов и разделителей, латиница-двойник — кириллицей. */
export function boxCodeKey(text: string): string {
  const upper = String(text ?? "").toUpperCase().replace(/[A-Z]/g, (char) => LOOKALIKE[char] ?? char).replaceAll("Ё", "Е");
  return upper.replace(/[\s.·•,;:/_\-–—]+/g, "").replace(/КВ(?=\d)/g, "КБ");
}

/** Коробка подходит к поиску: часть кода (точки и пробелы не важны) или номера. */
export function boxMatches(box: Named, query: string): boolean {
  const key = boxCodeKey(query);
  if (!key) return true;
  return boxCodeKey(box.code ?? "").includes(key) || boxCodeKey(box.number ?? "").includes(key);
}

/** Код с переносом только по разделителю «·», чтобы на узком экране не рвать «13.10». */
function CodeText({ code }: { code: string }) {
  const parts = code.split("·");
  return <>{parts.map((part, index) => <Fragment key={index}>{index > 0 && <>·<wbr /></>}{part}</Fragment>)}</>;
}

/** Название коробки в списке: код крупно, номер учёта мелко под ним; у коробки без кода — номер. */
export function BoxName({ box }: { box: Named }) {
  if (!box.code) return <strong>{box.number ?? "Номер после закрытия"}</strong>;
  return <span className="box-name"><strong><CodeText code={box.code} /></strong><small>№ {box.number}</small></span>;
}

function escapeHtml(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

export interface BoxLabelData {
  code: string;
  number: string;
  quantity: string;
  item: string;
  stage: string;
  closedAt: string | null;
}

const LABEL_CSS = `
  body { font-family: Arial, Helvetica, sans-serif; margin: 0; color: #000; }
  .label { padding: 1mm; }
  .code { font-size: 15pt; font-weight: 700; line-height: 1.15; word-break: keep-all; }
  .row { font-size: 9pt; margin-top: 1.5mm; }
  .qty { font-size: 12pt; font-weight: 700; }
`;

/** Этикетка 58×40 мм (термопринтер или обрезанный лист): код, количество, КБ и этап, номер учёта. */
export function buildBoxLabelHtml(label: BoxLabelData): string {
  const code = escapeHtml(label.code).replaceAll("·", "·<wbr>");
  const when = label.closedAt ? formatMoment(label.closedAt) : "";
  return `<!doctype html><html><head><meta charset="utf-8"><title>Коробка ${escapeHtml(label.code)}</title>` +
    `<style>${LABEL_CSS}${pageRule({ size: "58mm 40mm", marginMm: 2 })}</style></head><body><div class="label">` +
    `<div class="code">${code}</div>` +
    `<div class="row"><span class="qty">${escapeHtml(label.quantity)} шт</span> · ${escapeHtml([label.item, label.stage].filter(Boolean).join(" · "))}</div>` +
    `<div class="row">№ ${escapeHtml(label.number)}${when ? ` · ${escapeHtml(when)}` : ""}</div>` +
    `</div></body></html>`;
}

/** Панель после «Сдать»: «Подпишите коробку» с кодом крупно, количество, «Копировать» и «Печать этикетки». */
export function BoxLabelPanel({ number, box, stage = "", fixed = false, close }: {
  /** Номер учёта из ответа сдачи — коробка в списке может появиться после перезагрузки. */
  number: string;
  box?: ProductionBox;
  stage?: string;
  fixed?: boolean;
  close: () => void;
}) {
  const [copied, setCopied] = useState("");
  const done = useRef<HTMLButtonElement>(null);
  const code = box?.code || "";
  const title = fixed ? "Коробка исправлена — подпишите заново" : "Подпишите коробку";
  useEffect(() => { done.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [close]);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code || number);
      setCopied("Скопировано");
    } catch {
      setCopied("Не скопировалось — выделите код и нажмите Ctrl+C");
    }
  };
  const print = () => {
    if (!box) return;
    openPrintHtml(buildBoxLabelHtml({
      code: code || number, number: box.number ?? number, quantity: box.quantity,
      item: box.item, stage, closedAt: box.closed_at,
    }));
  };
  return <div className="panel kit-modal box-dialog box-label" role="dialog" aria-modal="false" aria-labelledby="box-label-title">
    <h3 id="box-label-title">{title}</h3>
    <output className="box-label-code" aria-label="Код коробки">{code ? <CodeText code={code} /> : number}</output>
    <div className="box-label-facts">
      {box && <span>В коробке <b>{box.quantity} шт</b></span>}
      {box && <span>{[box.item, stage || `этап ${box.operation_position}`].join(" · ")}</span>}
      <span className="box-label-no">№ учёта {number}</span>
    </div>
    <p className="box-label-hint">{fixed ? "Коробка исправлена и снова сдана." : "Коробка сдана. Без подписи её не возьмут дальше."}</p>
    <div className="box-label-actions">
      <button type="button" className="secondary" disabled={!box} onClick={() => void copy()}>Копировать код</button>
      <button type="button" className="secondary" disabled={!box} onClick={print}>Печать этикетки</button>
      <button type="button" className="primary" ref={done} onClick={close}>Подписал</button>
    </div>
    <small className="box-label-status" role="status" aria-live="polite">{copied}</small>
  </div>;
}
