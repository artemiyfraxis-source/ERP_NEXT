import { GRN_CATEGORIES, type GrnCategory } from "./grn";
import { PrintOutcome, type PrintResult } from "./LabelPrint";
import "./grn.css";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import "./labelDesigner.css";
import { api, ApiError, failureText, isAborted } from "./api";
import { PageWindow } from "./PageWindow";

/* «Шаблон этикетки» (owner 23.10.2026: «переработать шаблон печати, желательно сделать конструктор»): the label of a
   kind (партия склада, ГП приёмки) per format, drawn over the server's preview — exactly the dots the printer gets. */

export type LabelKind = "lot" | "intake" | "instrument";
export const LABEL_KINDS: [LabelKind, string][] = [["lot", "Партия склада"], ["intake", "Приёмка с производства"], ["instrument", "СИ и принадлежности"]];
export type LabelFormat = "58x30" | "58x40" | "100x50";
export const LABEL_FORMATS: LabelFormat[] = ["58x30", "58x40", "100x50"];
export type ElementKind = "field" | "text" | "qr" | "code128" | "line" | "box" | "logo";
export type TemplateElement = {
  id: string; kind: ElementKind; field?: string; text?: string;
  x_mm: number; y_mm: number; w_mm: number; h_mm: number;
  font_mm: number; bold: boolean; align: "left" | "center" | "right"; max_lines: number; fit: "shrink" | "ellipsis"; mono: boolean;
};
export type LabelTemplate = { elements: TemplateElement[] };
export type TemplateVersion = number | string | null;
export type FormMetadata = { form_id: string; form_revision: string; form_effective_date: string };
export type TemplateScope = { category?: GrnCategory | ""; printer_name?: string };
export type FormatTemplate = { form?: FormMetadata | null; template: LabelTemplate; is_default: boolean; version: TemplateVersion };
export type LabelField = { key: string; label: string };
export type TemplatesAnswer = { kind: LabelKind; fields: LabelField[]; formats: Record<LabelFormat, FormatTemplate>; current_format: LabelFormat };

const path = (kind: LabelKind, format?: LabelFormat, scope: TemplateScope = {}, action = "", version?: TemplateVersion) => {
  const query = new URLSearchParams();
  if (scope.category && kind !== "instrument") query.set("category", scope.category);
  if (scope.printer_name && kind !== "instrument") query.set("printer_name", scope.printer_name);
  if (version != null) query.set("version", String(version));
  return `/api/stock/labels/templates/${kind}${format ? `/${format}` : ""}${action}${query.size ? `?${query}` : ""}`;
};
export const templatesApi = {
  load: (kind: LabelKind, scope: TemplateScope = {}) => api<TemplatesAnswer>(path(kind, undefined, scope)),
  save: (kind: LabelKind, format: LabelFormat, template: LabelTemplate, version: TemplateVersion, scope: TemplateScope = {}, form?: FormMetadata | null) =>
    api<FormatTemplate>(path(kind, format, scope), { method: "PUT", body: JSON.stringify({ template, version, ...(form !== undefined ? {form} : {}) }) }),
  reset: (kind: LabelKind, format: LabelFormat, scope: TemplateScope = {}, version?: TemplateVersion) => api<FormatTemplate>(path(kind, format, scope, "", scope.category ? version : undefined), { method: "DELETE" }),
  preview: (kind: LabelKind, format: LabelFormat, template: LabelTemplate, signal: AbortSignal, scope: TemplateScope = {}, form?: FormMetadata | null) =>
    api<Blob>(path(kind, format, scope, "/preview"), { method: "POST", body: JSON.stringify({ template, sample: null, ...(form !== undefined ? {form} : {}) }), safe: true, blob: true, signal }),
  test: (kind: LabelKind, format: LabelFormat, scope: TemplateScope) => api<PrintResult>(path(kind, format, scope, "/test"), {method: "POST", body: JSON.stringify({copies: 1})}),
};

export const PREVIEW_DELAY_MS = 500;
/** px per mm when the canvas can't be measured (tests, a hidden tab). */
export const FALLBACK_SCALE = 8;
const CANVAS_MAX_H = 480;
const STEP = 0.5;
// A line of text takes 1.676 × the capitals' height plus whole-dot rounding (backend labels/defaults.py `_height`).
const CAP_TO_PITCH = 1.676;
const SLACK_PER_LINE = 0.25;
/** The box height (mm) that holds `lines` lines of capitals `font_mm` high — the server breaks a text into as many lines as
    the box's height holds, «Строк» at most. */
export function textBoxHeight(font_mm: number, lines: number): number {
  return Math.ceil((lines * (CAP_TO_PITCH * font_mm + SLACK_PER_LINE) + 0.05) * 10 - 1e-6) / 10;
}
/** How many lines a box `h_mm` high holds at `font_mm`. */
function linesHeld(font_mm: number, h_mm: number): number {
  return Math.max(0, Math.floor((h_mm - 0.05 + 1e-6) / (CAP_TO_PITCH * font_mm + SLACK_PER_LINE)));
}
const STALE = "Шаблон изменили — обновите";

export const formatSize = (format: LabelFormat): [number, number] => {
  const [width, height] = format.split("x").map(Number);
  return [width, height];
};
export const formatText = (format: LabelFormat) => format.replace("x", " × ");
export const snap = (value: number) => Math.round(value / STEP) * STEP;
const clamp = (value: number, low: number, high: number) => Math.min(Math.max(value, low), Math.max(low, high));
const decimal = (value: number) => String(Math.round(value * 100) / 100).replace(".", ",");

const KIND_NAMES: Record<ElementKind, string> = { field: "Поле", text: "Текст", qr: "QR-код", code128: "Code128", line: "Линия", box: "Рамка", logo: "Логотип" };
const ALIGNS: [TemplateElement["align"], string][] = [["left", "слева"], ["center", "по центру"], ["right", "справа"]];

/** A new element of `kind`, placed top-left inside a `width × height` label. */
export function newElement(kind: ElementKind, elements: TemplateElement[], [width, height]: [number, number], field = ""): TemplateElement {
  let n = elements.length + 1;
  while (elements.some((element) => element.id === `el${n}`)) n += 1;
  const wide = Math.max(10, width - 4);
  const size: Record<ElementKind, [number, number]> = {
    field: [wide, 4], text: [wide, 4], qr: [Math.min(14, height - 4), Math.min(14, height - 4)], code128: [Math.min(36, wide), 8],
    line: [wide, 0.5], box: [Math.min(24, wide), Math.min(12, height - 4)], logo: [Math.min(14, wide), 4],
  };
  const [w, h] = size[kind];
  return {
    id: `el${n}`, kind, ...(kind === "field" || kind === "qr" || kind === "code128" ? { field } : {}), ...(kind === "text" ? { text: "Текст" } : {}),
    x_mm: 2, y_mm: 2, w_mm: w, h_mm: h, font_mm: 3, bold: false, align: "left", max_lines: 1, fit: "shrink", mono: false,
  };
}

/** A text's placeholders: «{grn}» is stored, «{ГРН}» is shown and typed (Russian labels only on the screen). */
export const textShown = (text: string, fields: LabelField[]) =>
  text.replace(/\{([^{}]+)\}/g, (all, key: string) => { const field = fields.find((item) => item.key === key); return field ? `{${field.label}}` : all; });
export const textStored = (text: string, fields: LabelField[]) =>
  text.replace(/\{([^{}]+)\}/g, (all, name: string) => {
    const field = fields.find((item) => item.label.toLowerCase() === name.trim().toLowerCase()) ?? fields.find((item) => item.key === name);
    return field ? `{${field.key}}` : all;
  });

/** What the element shows, in words: the field's Russian name, the text, «QR-код: ГРН». */
export function elementCaption(element: TemplateElement, fields: LabelField[]) {
  const field = fields.find((item) => item.key === element.field)?.label ?? (element.field ? "поле не найдено" : "поле не выбрано");
  if (element.kind === "field") return field;
  if (element.kind === "text") return textShown(element.text ?? "", fields).trim() || "Пустой текст";
  if (element.kind === "qr" || element.kind === "code128") return `${KIND_NAMES[element.kind]}: ГРН`;
  return KIND_NAMES[element.kind];
}

type Slot = { saved: LabelTemplate; draft: LabelTemplate; version: TemplateVersion; isDefault: boolean; savedForm: FormMetadata | null; form: FormMetadata | null };
const slotKey = (kind: string, format: LabelFormat) => `${kind}|${format}`;
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const asSlot = (answer: FormatTemplate): Slot => ({ saved: answer.template, draft: answer.template, version: answer.version, isDefault: answer.is_default, form: answer.form ?? null, savedForm: answer.form ?? null });

/** The canvas's px per mm: fits the measured width and at most CANVAS_MAX_H high. */
function useScale(format: LabelFormat) {
  const box = useRef<HTMLDivElement | null>(null);
  const observer = useRef<ResizeObserver | null>(null);
  const [width, setWidth] = useState(0);
  // A callback ref: the canvas appears only once the templates have loaded (and resizes with the window).
  const attach = useCallback((node: HTMLDivElement | null) => {
    observer.current?.disconnect();
    observer.current = null;
    box.current = node;
    if (!node) return;
    setWidth(node.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    observer.current = new ResizeObserver(() => setWidth(node.clientWidth));
    observer.current.observe(node);
  }, []);
  const [w, h] = formatSize(format);
  const scale = width > 0 ? Math.min((width - 32) / w, CANVAS_MAX_H / h, 16) : FALLBACK_SCALE;
  return { box, attach, scale };
}

/** The preview PNG as a data: URL — the page's CSP lets images come from 'self' and data: only. */
async function dataUrl(blob: Blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return `data:${blob.type || "image/png"};base64,${btoa(text)}`;
}

/** «Шаблон этикетки»: kind and format tabs, the label canvas with element boxes, the palette, the properties.
 *  The API owns the effective format per kind; printer settings updates invalidate those cached answers. */
export function LabelDesigner({ initialKind, usedFormat = null, settingsVersion = 0, readOnly = false, onDirty, initialCategory = "", printerName = "" }: {
  initialKind: LabelKind; usedFormat?: LabelFormat | null; settingsVersion?: number; readOnly?: boolean; onDirty?: (dirty: boolean) => void; initialCategory?: GrnCategory | ""; printerName?: string;
}) {
  const [kind, setKind] = useState<LabelKind>(initialKind);
  const [category, setCategory] = useState<GrnCategory | "">(initialCategory);
  // The API takes a printer profile only together with a GRN category (422 otherwise): historical and instrument
  // labels use the shared printer settings.
  const grnScope = kind !== "instrument" && Boolean(category);
  const scope: TemplateScope = {category: grnScope ? category : "", printer_name: grnScope ? printerName : ""};
  const scopeKey = `${kind}|${scope.category}|${scope.printer_name}`;
  const [testResult, setTestResult] = useState<PrintResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [format, setFormat] = useState<LabelFormat>(usedFormat ?? "58x40");
  const formatChosen = useRef(false);
  const settingsKey = `${settingsVersion}:${usedFormat ?? ""}`;
  const [effectiveFormats, setEffectiveFormats] = useState<Partial<Record<string, { format: LabelFormat; settingsKey: string }>>>({});
  const currentFormat = effectiveFormats[scopeKey]?.settingsKey === settingsKey ? effectiveFormats[scopeKey]?.format : null;
  const [fields, setFields] = useState<Partial<Record<string, LabelField[]>>>({});
  const [slots, setSlots] = useState<Record<string, Slot>>({});
  const [loadError, setLoadError] = useState("");
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState<"" | "save" | "reset" | "reload">("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirmReset, setConfirmReset] = useState(false);
  const [preview, setPreview] = useState<{ url: string; error: string }>({ url: "", error: "" });
  const { box, attach, scale } = useScale(format);
  const drag = useRef<{ id: string; mode: "move" | "resize"; x: number; y: number; start: TemplateElement } | null>(null);

  // Switching kind or saving printer settings follows the server unless the user chose a format explicitly.
  useEffect(() => { if (currentFormat && !formatChosen.current) setFormat(currentFormat); }, [kind, currentFormat]);

  useEffect(() => {
    if (currentFormat) return;
    let cancelled = false;
    setLoadError("");
    templatesApi.load(kind, scope).then((answer) => {
      if (cancelled) return;
      setEffectiveFormats((current) => ({ ...current, [scopeKey]: { format: answer.current_format ?? usedFormat ?? "58x40", settingsKey } }));
      setFields((current) => ({ ...current, [scopeKey]: answer.fields ?? [] }));
      setSlots((current) => {
        const next = { ...current };
        for (const item of LABEL_FORMATS) if (answer.formats?.[item] && !next[slotKey(scopeKey, item)]) next[slotKey(scopeKey, item)] = asSlot(answer.formats[item]);
        return next;
      });
    }, (reason) => { if (!cancelled) setLoadError(failureText(reason, "Шаблоны этикеток не загрузились")); });
    return () => { cancelled = true; };
  }, [scopeKey, settingsKey, currentFormat, usedFormat]);

  const slot = slots[slotKey(scopeKey, format)];
  const kindFields = useMemo(() => fields[scopeKey] ?? [], [fields, scopeKey]);
  const elements = slot?.draft.elements ?? [];
  const current = elements.find((element) => element.id === selected) ?? null;
  const dirty = Boolean(slot && (!same(slot.draft, slot.saved) || !same(slot.form, slot.savedForm)));
  const anyDirty = Object.values(slots).some((item) => (!same(item.draft, item.saved) || !same(item.form, item.savedForm)));
  useEffect(() => { onDirty?.(anyDirty); }, [anyDirty, onDirty]);
  const [width, height] = formatSize(format);

  // The server's picture of the draft: debounced, the last request wins (an older answer is cancelled).
  const draftText = slot ? JSON.stringify(slot.draft) : "";
  useEffect(() => {
    // The server draws a preview for those who may change the warehouse only (every warehouse POST needs edit).
    if (!draftText || readOnly) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      templatesApi.preview(kind, format, JSON.parse(draftText) as LabelTemplate, controller.signal, scope, slot?.form).then(dataUrl).then((url) => {
        if (!controller.signal.aborted) setPreview({ url, error: "" });
      }, (reason) => {
        if (controller.signal.aborted || isAborted(reason)) return;
        setPreview((old) => ({ ...old, error: failureText(reason, "Предпросмотр не получен") }));
      });
    }, PREVIEW_DELAY_MS);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [scopeKey, format, draftText, readOnly, JSON.stringify(slot?.form)]);

  function setDraft(change: (elements: TemplateElement[]) => TemplateElement[]) {
    setSlots((all) => {
      const target = all[slotKey(scopeKey, format)];
      return target ? { ...all, [slotKey(scopeKey, format)]: { ...target, draft: { ...target.draft, elements: change(target.draft.elements) } } } : all;
    });
    setNotice("");
  }
  const update = (id: string, patch: Partial<TemplateElement>) => setDraft((list) => list.map((element) => element.id === id ? { ...element, ...patch } : element));
  const remove = (id: string) => { setDraft((list) => list.filter((element) => element.id !== id)); setSelected(""); };
  function add(elementKind: ElementKind, field = "") {
    if (!slot) return;
    const element = newElement(elementKind, elements, [width, height], field || (elementKind === "field" ? "" : kindFields[0]?.key ?? ""));
    setDraft((list) => [...list, element]);
    setSelected(element.id);
  }
  /** Moved or resized within the label, on the 0.5 mm grid. */
  function place(element: TemplateElement, x: number, y: number, w = element.w_mm, h = element.h_mm): Partial<TemplateElement> {
    const sw = clamp(snap(w), STEP, width), sh = clamp(snap(h), STEP, height);
    return { x_mm: clamp(snap(x), 0, width - sw), y_mm: clamp(snap(y), 0, height - sh), w_mm: sw, h_mm: sh };
  }

  function pointerDown(event: ReactPointerEvent<HTMLElement>, element: TemplateElement, mode: "move" | "resize") {
    event.stopPropagation();
    setSelected(element.id);
    box.current?.focus();
    if (readOnly || event.button > 0) return;
    drag.current = { id: element.id, mode, x: event.clientX, y: event.clientY, start: element };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }
  function pointerMove(event: ReactPointerEvent<HTMLElement>) {
    const moving = drag.current;
    if (!moving) return;
    const dx = (event.clientX - moving.x) / scale, dy = (event.clientY - moving.y) / scale;
    const start = moving.start;
    update(moving.id, moving.mode === "move" ? place(start, start.x_mm + dx, start.y_mm + dy)
      : place(start, start.x_mm, start.y_mm, Math.min(start.w_mm + dx, width - start.x_mm), Math.min(start.h_mm + dy, height - start.y_mm)));
  }
  const pointerUp = () => { drag.current = null; };

  function keyDown(event: KeyboardEvent<HTMLElement>) {
    if (readOnly || !current) return;
    const target = event.target as HTMLElement;
    if (["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName)) return;
    const step = event.shiftKey ? 2 : STEP;
    const moves: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    if (moves[event.key]) {
      event.preventDefault();
      update(current.id, place(current, current.x_mm + moves[event.key][0], current.y_mm + moves[event.key][1]));
    } else if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      remove(current.id);
    }
  }

  async function reloadFormat() {
    setBusy("reload"); setError(""); setNotice("");
    try {
      const answer = await templatesApi.load(kind, scope);
      setFields((all) => ({ ...all, [scopeKey]: answer.fields ?? [] }));
      setSlots((all) => ({ ...all, [slotKey(scopeKey, format)]: asSlot(answer.formats[format]) }));
      setSelected("");
    } catch (reason) {
      setError(failureText(reason, "Шаблон не загрузился"));
    } finally { setBusy(""); }
  }

  async function save() {
    if (!slot) return;
    if (slot.form && Object.values(slot.form).some(value => !value.trim())) {setError("Заполните все реквизиты формы или уберите их"); return;}
    setBusy("save"); setError(""); setNotice("");
    try {
      const answer = await templatesApi.save(kind, format, slot.draft, slot.version, scope, slot.form);
      setSlots((all) => ({ ...all, [slotKey(scopeKey, format)]: asSlot(answer) }));
      setNotice("Шаблон сохранён");
    } catch (reason) {
      setError(reason instanceof ApiError && reason.status === 409 ? STALE : failureText(reason, "Шаблон не сохранился"));
    } finally { setBusy(""); }
  }

  async function reset() {
    setConfirmReset(false);
    setBusy("reset"); setError(""); setNotice("");
    try {
      const answer = await templatesApi.reset(kind, format, scope, slot.version);
      setSlots((all) => ({ ...all, [slotKey(scopeKey, format)]: asSlot(answer) }));
      setSelected("");
      setNotice("Шаблон сброшен к стандартному");
    } catch (reason) {
      setError(failureText(reason, "Шаблон не сброшен"));
    } finally { setBusy(""); }
  }

  const switchKind = (next: LabelKind) => { if(next===kind)return; setTestResult(null); setKind(next); setSelected(""); setError(""); setNotice(""); setPreview({ url: "", error: "" }); };
  const switchFormat = (next: LabelFormat) => { formatChosen.current = true; if(next===format)return; setTestResult(null); setFormat(next); setSelected(""); setError(""); setNotice(""); setPreview({ url: "", error: "" }); };
  const dirtyIn = (item: LabelFormat) => { const other = slots[slotKey(scopeKey, item)]; return Boolean(other && (!same(other.draft, other.saved) || !same(other.form, other.savedForm))); };

  async function testPrint() {
    setTesting(true); setError(""); setTestResult(null);
    try { setTestResult(await templatesApi.test(kind, format, scope)); }
    catch(reason) { setError(failureText(reason, "Пробная этикетка не отправлена")); }
    finally { setTesting(false); }
  }
  const setForm = (field: keyof FormMetadata, value: string) => setSlots(all => {
    const target = all[slotKey(scopeKey,format)];
    return target ? {...all,[slotKey(scopeKey,format)]: {...target,form:{form_id:"",form_revision:"",form_effective_date:"",...target.form,[field]:value}}} : all;
  });
  return <section className="label-designer" aria-label="Шаблон этикетки" onKeyDown={keyDown}>
    <div className="ld-bar">
      {kind !== "instrument" && <label className="ld-category">Категория ГРН<select aria-label="Категория ГРН" value={category} disabled={Boolean(busy) || testing}
        onChange={(event) => { if (event.target.value === category) return; setCategory(event.target.value as GrnCategory | ""); setSelected(""); setError(""); setNotice(""); setPreview({ url: "", error: "" }); setTestResult(null); }}>
        <option value="">Исторические этикетки (ГП, OLD)</option>{GRN_CATEGORIES.map(([code, name]) => <option key={code} value={code}>{name} · {code}</option>)}
      </select></label>}
      <div className="seg-tabs" role="tablist" aria-label="Какая этикетка">
        {LABEL_KINDS.map(([key, label]) => <button key={key} type="button" role="tab" aria-selected={kind === key} className={kind === key ? "on" : undefined}
          onClick={() => switchKind(key)}>{label}</button>)}
      </div>
      <div className="seg-tabs" role="tablist" aria-label="Формат">
        {LABEL_FORMATS.map((item) => <button key={item} type="button" role="tab" aria-selected={format === item} className={format === item ? "on" : undefined}
          onClick={() => switchFormat(item)}>{formatText(item)}{currentFormat === item && <span className="ld-used"> · используется</span>}{dirtyIn(item) && <span className="ld-dot" title="Есть несохранённые изменения" aria-label="есть изменения"> •</span>}</button>)}
      </div>
      {slot && <span className="ld-state muted">{slot.isDefault ? "Стандартный шаблон" : "Свой шаблон"}{dirty ? " · не сохранено" : ""}</span>}
      {!readOnly && slot && <div className="ld-actions">
        <button type="button" className="secondary small" disabled={!dirty || Boolean(busy)} onClick={() => { setSlots(all => ({...all,[slotKey(scopeKey,format)]:{...slot,draft:slot.saved,form:slot.savedForm}})); setSelected(""); }}>Отменить изменения</button>
        <button type="button" className="secondary small" disabled={slot.isDefault || Boolean(busy)} onClick={() => setConfirmReset(true)}>Сбросить к стандартному</button>
        <button type="button" className="primary small" disabled={!dirty || Boolean(busy)} onClick={() => void save()}>{busy === "save" ? "Сохраняю…" : "Сохранить"}</button>
        <button type="button" className="secondary small" disabled={dirty || Boolean(busy) || testing || Boolean(preview.error)} onClick={() => void testPrint()}
          title={dirty ? "Сначала сохраните шаблон" : "Пробная этикетка этой категории и размера"}>{testing ? "Отправляю…" : "Пробная по шаблону"}</button>
      </div>}
    </div>
    {slot && category && <div className="grn-form-metadata">
      <label>Номер утверждённой формы<input disabled={readOnly} maxLength={128} value={slot.form?.form_id ?? ""} onChange={event=>setForm("form_id",event.target.value)}/></label>
      <label>Редакция формы<input disabled={readOnly} maxLength={64} value={slot.form?.form_revision ?? ""} onChange={event=>setForm("form_revision",event.target.value)}/></label>
      <label>Дата введения формы<input disabled={readOnly} type="date" value={slot.form?.form_effective_date ?? ""} onChange={event=>setForm("form_effective_date",event.target.value)}/></label>
      {!readOnly && slot.form && <button type="button" className="secondary small" onClick={()=>setSlots(all=>({...all,[slotKey(scopeKey,format)]:{...slot,form:null}}))}>Убрать реквизиты формы</button>}
    </div>}
    <PrintOutcome result={testResult} error=""/>
    {error && <div className="error-banner" role="alert">{error}{error === STALE && <button type="button" className="secondary small ld-reload" disabled={Boolean(busy)} onClick={() => void reloadFormat()}>Обновить</button>}</div>}
    {notice && <div className="success-banner" role="status">{notice}</div>}
    {loadError ? <div className="error-banner" role="alert">{loadError}</div> : !slot ? <div className="empty">Загрузка…</div> :
      <div className={readOnly ? "ld-body read-only" : "ld-body"}>
        <aside className="ld-side">
          {!readOnly && <div className="ld-palette" aria-label="Добавить">
            <select aria-label="Добавить поле" value="" onChange={(event) => { if (event.target.value) add("field", event.target.value); }}>
              <option value="">+ Поле…</option>
              {kindFields.map((field) => <option key={field.key} value={field.key}>{field.label}</option>)}
            </select>
            {(["text", "qr", "code128", "line", "box", "logo"] as ElementKind[]).map((item) =>
              <button key={item} type="button" className="secondary small" onClick={() => add(item)}>+ {item === "qr" ? "QR" : KIND_NAMES[item]}</button>)}
          </div>}
          <h3>Элементы <span className="page-count">{elements.length}</span></h3>
          {elements.length ? <ol className="ld-list">
            {elements.map((element) => <li key={element.id}><button type="button" aria-pressed={element.id === selected} className={element.id === selected ? "on" : undefined}
              title={`${KIND_NAMES[element.kind]}: ${elementCaption(element, kindFields)}`} onClick={() => setSelected(element.id)}><span className="ld-kind">{KIND_NAMES[element.kind]}</span>{element.kind === "field" || element.kind === "text" ? ` ${elementCaption(element, kindFields)}` : ""}</button></li>)}
          </ol> : <p className="muted ld-empty">Этикетка пустая — добавьте поле.</p>}
        </aside>
        <div className="ld-stage">
          <div ref={attach} className="ld-canvas-wrap" tabIndex={0} role="group" aria-label={`Макет этикетки ${formatText(format)} мм`}
            onPointerDown={() => setSelected("")}>
            <div className={preview.url ? "ld-canvas" : "ld-canvas no-preview"} style={{ width: width * scale, height: height * scale }}>
              {preview.url && <img className="ld-preview" src={preview.url} alt={`Предпросмотр этикетки ${formatText(format)}`} draggable={false} />}
              {elements.map((element) => {
                const caption = elementCaption(element, kindFields);
                const name = element.kind === "field" || element.kind === "text" ? `${KIND_NAMES[element.kind]}: ${caption}` : caption;
                return <div key={element.id} role="button" tabIndex={-1} aria-label={name} aria-pressed={element.id === selected}
                  title={`${name} · ${decimal(element.x_mm)}; ${decimal(element.y_mm)} мм`}
                  className={`ld-el ld-${element.kind}${element.id === selected ? " on" : ""}`}
                  style={{ left: element.x_mm * scale, top: element.y_mm * scale, width: element.w_mm * scale, height: element.h_mm * scale }}
                  onClick={() => setSelected(element.id)}
                  onPointerDown={(event) => pointerDown(event, element, "move")} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={pointerUp}>
                  <span className="ld-cap">{caption}</span>
                  {!readOnly && element.id === selected && <span className="ld-handle" aria-hidden="true" onPointerDown={(event) => pointerDown(event, element, "resize")} />}
                </div>;
              })}
            </div>
          </div>
          <p className="ld-hint muted">{formatText(format)} мм · {readOnly ? "предпросмотр и правка шаблона — у тех, кто может менять склад" : preview.error ? <span className="tone-danger">Предпросмотр: {preview.error}</span> : "фон — ровно то, что напечатает принтер"}
            {!readOnly && " · стрелки — 0,5 мм, Shift — 2 мм, Delete — удалить"}</p>
        </div>
        <ElementProps element={current} fields={kindFields} size={[width, height]} readOnly={readOnly}
          onChange={(patch) => current && update(current.id, patch)} onRemove={() => current && remove(current.id)} />
      </div>}
    {confirmReset && <PageWindow label="Сбросить шаблон" close={() => setConfirmReset(false)} width={480}>
      <section className="panel ld-confirm">
        <h2>Сбросить к стандартному?</h2>
        <p>Шаблон «{LABEL_KINDS.find(([key]) => key === kind)?.[1]}», {formatText(format)} мм станет стандартным. Ваш макет этого формата пропадёт.</p>
        <div className="ld-confirm-actions">
          <button type="button" className="primary" onClick={() => void reset()}>Сбросить</button>
          <button type="button" className="secondary" onClick={() => setConfirmReset(false)}>Отмена</button>
        </div>
      </section>
    </PageWindow>}
  </section>;
}

/** A number in mm (or lines): typed with a comma or a dot, applied as soon as it reads as a number. */
function NumField({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (value: number) => void }) {
  const [text, setText] = useState(decimal(value));
  useEffect(() => { setText((old) => Number(old.replace(",", ".")) === value ? old : decimal(value)); }, [value]);
  return <label>{label}<input inputMode="decimal" value={text} onBlur={() => setText(decimal(value))}
    onChange={(event) => {
      setText(event.target.value);
      const number = Number(event.target.value.replace(",", ".").trim());
      if (event.target.value.trim() !== "" && Number.isFinite(number)) onChange(clamp(number, min, max));
    }} /></label>;
}

function ElementProps({ element, fields, size: [width, height], readOnly, onChange, onRemove }: {
  element: TemplateElement | null; fields: LabelField[]; size: [number, number]; readOnly: boolean;
  onChange: (patch: Partial<TemplateElement>) => void; onRemove: () => void;
}) {
  if (!element) return <aside className="ld-props" aria-label="Свойства элемента"><p className="muted ld-empty">Выберите элемент на макете или в списке.</p></aside>;
  const hasText = element.kind === "field" || element.kind === "text";
  const hasField = element.kind === "field" || element.kind === "qr" || element.kind === "code128";
  // A text wraps only into the lines its box holds: a bigger size or more lines grow the box down (to the label's edge),
  // so «Строк» 2 really gives two lines (owner 26.10.2026: «не работает перенос внутри блока, если не влезает»).
  const room = Math.round((height - element.y_mm) * 10) / 10;
  const tall = (font_mm: number, lines: number) => Math.min(textBoxHeight(font_mm, lines), room);
  const grow = (patch: Partial<TemplateElement>) => {
    const need = tall(patch.font_mm ?? element.font_mm, patch.max_lines ?? element.max_lines);
    onChange(need > element.h_mm ? { ...patch, h_mm: need } : patch);
  };
  const held = hasText ? linesHeld(element.font_mm, element.h_mm) : 0;
  const short = hasText && held < element.max_lines;
  return <aside className="ld-props" aria-label="Свойства элемента">
    <h3>{KIND_NAMES[element.kind]}</h3>
    <fieldset disabled={readOnly} className="ld-grid">
      {hasField && <label className="wide">{element.kind === "field" ? "Поле" : "Данные кода"}
        <select value={element.field ?? ""} onChange={(event) => onChange({ field: event.target.value })}>
          {!fields.some((field) => field.key === element.field) && <option value={element.field ?? ""}>— выберите —</option>}
          {fields.map((field) => <option key={field.key} value={field.key}>{field.label}</option>)}
        </select></label>}
      {element.kind === "text" && <>
        <label className="wide">Текст
          <input value={textShown(element.text ?? "", fields)} placeholder="Приход {Дата прихода}" onChange={(event) => onChange({ text: textStored(event.target.value, fields) })} /></label>
        <label className="wide">Вставить поле в текст
          <select value="" onChange={(event) => { if (event.target.value) onChange({ text: `${element.text ?? ""}{${event.target.value}}` }); }}>
            <option value="">— выберите —</option>
            {fields.map((field) => <option key={field.key} value={field.key}>{field.label}</option>)}
          </select></label>
      </>}
      <NumField label="X, мм" value={element.x_mm} min={0} max={width} onChange={(x_mm) => onChange({ x_mm })} />
      <NumField label="Y, мм" value={element.y_mm} min={0} max={height} onChange={(y_mm) => onChange({ y_mm })} />
      <NumField label="Ширина, мм" value={element.w_mm} min={STEP} max={width} onChange={(w_mm) => onChange({ w_mm })} />
      <NumField label="Высота, мм" value={element.h_mm} min={STEP / 5} max={height} onChange={(h_mm) => onChange({ h_mm })} />
      {hasText && <>
        <NumField label="Кегль, мм" value={element.font_mm} min={1} max={20} onChange={(font_mm) => grow({ font_mm })} />
        <NumField label="Строк" value={element.max_lines} min={1} max={4} onChange={(lines) => grow({ max_lines: Math.round(lines) })} />
        {short && <p className="wide ld-short tone-warn" role="status">
          {held === 0 ? "В эту высоту не помещается ни одна строка" : `В высоту ${decimal(element.h_mm)} мм помещается строк: ${held} из ${element.max_lines}`}
          {tall(element.font_mm, element.max_lines) > element.h_mm
            ? <> · <button type="button" className="link-button" onClick={() => onChange({ h_mm: tall(element.font_mm, element.max_lines) })}>Подогнать высоту</button></>
            : null}
          {textBoxHeight(element.font_mm, element.max_lines) > room && <> · до низа этикетки места мало — уменьшите кегль или поднимите блок</>}
        </p>}
        <label>Выравнивание<select value={element.align} onChange={(event) => onChange({ align: event.target.value as TemplateElement["align"] })}>
          {ALIGNS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label>Не влезает<select value={element.fit} onChange={(event) => onChange({ fit: event.target.value as TemplateElement["fit"] })}>
          <option value="shrink">уменьшать</option><option value="ellipsis">обрезать «…»</option></select></label>
        <label className="check-inline"><input type="checkbox" checked={element.bold} onChange={(event) => onChange({ bold: event.target.checked })} />жирный</label>
        <label className="check-inline"><input type="checkbox" checked={element.mono} onChange={(event) => onChange({ mono: event.target.checked })} />моноширинный</label>
      </>}
    </fieldset>
    {!readOnly && <button type="button" className="secondary small ld-remove" onClick={onRemove}>Удалить элемент</button>}
  </aside>;
}
