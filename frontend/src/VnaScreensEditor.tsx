import { useEffect, useMemo, useState } from "react";
import { failureText } from "./api";
import { formatMoment, formatNumber, toNumber } from "./format";
import { PageWindow } from "./PageWindow";
import {
  vnaScreensApi,
  type VnaBenchSession,
  type VnaScreen,
  type VnaScreenFields,
  type VnaScreenFormat,
  type VnaScreenLimit,
  type VnaScreenParam,
  type VnaScreenPurpose,
  type VnaScreenTrace,
} from "./vnaBenchApi";

/* «ОТК → ВАЦ — рабочее место» → «Экраны» (S5b, владелец 19.11: «мне надо, чтобы я мог настраивать экраны»).
   Экраны, которые ERP ставит на S2VNA: расположение графиков, параметр и формат каждого, шкала, линия допуска (из нормы
   СИ, из ЭТК, своё число или без линии). Основной экран назначения рабочее место ставит само; любой включённый можно
   вывести на ВАЦ кнопкой «Показать на ВАЦ» — без развёртки, калибровка остаётся. Превью — на примерных данных. */

export const PURPOSES: Record<VnaScreenPurpose, string> = {
  adapter: "Переход (СИ)", assembly: "Кабельная сборка (КБ)", one_port: "Сборка с одного конца",
  load: "Проверка нагрузкой", flex: "Шевеление кабелей", engineer: "Подробно (инженер)",
};
export const FORMATS: Record<VnaScreenFormat, string> = {
  SWR: "КСВН", MLOG: "Потери (LOGM)", PHAS: "Фаза", GDEL: "ГВЗ", SMIT: "Вольперт-Смит",
};
const LIMITS: Record<VnaScreenLimit, string> = { norm: "из нормы СИ", etk: "из ЭТК", fixed: "своё число", none: "без линии" };
const PARAMS: VnaScreenParam[] = ["S11", "S21", "S12", "S22"];
/** Коды расположения окон S2VNA (DISPlay:WINDow:SPLit). 3, 5, 8 и 15 стоят в заводских экранах и проверены на приборе. */
export const LAYOUTS: { code: number; rows: number; cols: number; label: string }[] = [
  { code: 1, rows: 1, cols: 1, label: "1 график" },
  { code: 2, rows: 1, cols: 2, label: "2 рядом" },
  { code: 3, rows: 2, cols: 1, label: "2 друг под другом" },
  { code: 6, rows: 1, cols: 3, label: "3 рядом" },
  { code: 5, rows: 3, cols: 1, label: "3 друг под другом" },
  { code: 8, rows: 2, cols: 2, label: "4 — 2 × 2" },
  { code: 9, rows: 2, cols: 3, label: "6 — 2 ряда по 3" },
  { code: 10, rows: 3, cols: 2, label: "6 — 3 ряда по 2" },
  { code: 11, rows: 2, cols: 4, label: "8 — 2 ряда по 4" },
  { code: 12, rows: 4, cols: 2, label: "8 — 4 ряда по 2" },
  { code: 13, rows: 3, cols: 3, label: "9 — 3 × 3" },
  { code: 14, rows: 3, cols: 4, label: "12 — 3 ряда по 4" },
  { code: 15, rows: 4, cols: 4, label: "16 — 4 × 4" },
];
const MAX_TRACES = 16;
/** Пример нормы для превью: линия «из нормы СИ» — КСВН 1,06 и потери 0,2 дБ; «из ЭТК» — КСВН 1,30. */
const SAMPLE_NORM = { swr: 1.06, loss: -0.2, etk: 1.3 };

export const layoutOf = (code: number) => LAYOUTS.find((layout) => layout.code === code)
  ?? { code, rows: 1, cols: 1, label: `код S2VNA ${code}` };
export const traceTitle = (trace: Pick<VnaScreenTrace, "param" | "format">) => `${FORMATS[trace.format]} ${trace.param}`;
const plain = (value: number | null | undefined) => (value === null || value === undefined ? "" : formatNumber(value, { digits: 6 }));

// ---- черновик формы: числа — строками, как их набирают (запятая или точка) ------------------------------------------

type ScaleMode = "auto" | "norm" | "manual";
interface DraftTrace {
  param: VnaScreenParam; format: VnaScreenFormat; mode: ScaleMode; pdiv: string; rlev: string; rpos: string;
  limit: VnaScreenLimit; value: string; hidden: boolean; memory: boolean;
}
interface Draft { name: string; purpose: VnaScreenPurpose; split: number; markers: string; note: string; is_default: boolean; traces: DraftTrace[] }

function draftTrace(trace: VnaScreenTrace): DraftTrace {
  const manual = typeof trace.scale === "object" ? trace.scale : null;
  return {
    param: trace.param, format: trace.format, mode: manual ? "manual" : (trace.scale as "auto" | "norm"),
    pdiv: plain(manual?.pdiv), rlev: plain(manual?.rlev), rpos: plain(manual?.rpos), limit: trace.limit,
    value: plain(trace.fixed?.value), hidden: trace.hidden, memory: trace.memory,
  };
}

export function draftOf(screen: VnaScreenFields): Draft {
  return {
    name: screen.name, purpose: screen.purpose, split: screen.split, note: screen.note, is_default: screen.is_default,
    markers: screen.markers_hz.map((hz) => formatNumber(hz / 1e9, { digits: 3 })).join("; "),
    traces: screen.traces.map(draftTrace),
  };
}

/** Черновик → поля для сервера; ошибка набора — понятной строкой с номером графика. */
/* Те же границы, что у сервера (vna_screens.ScreenDef, протокол v2): отказ виден до «Сохранить». */
export const LIMITS_OF = { name: 120, markers: 15, markerGhz: [0.0001, 18] as const, level: 1e6, captured: ["S11", "S21", "S12", "S22"] };

export function fieldsOf(draft: Draft): { fields: VnaScreenFields } | { error: string } {
  const name = draft.name.trim();
  if (!name) return { error: "Название не задано" };
  if (name.length > LIMITS_OF.name) return { error: `Название — не больше ${LIMITS_OF.name} символов` };
  const missing = LIMITS_OF.captured.filter((param) => !draft.traces.some((trace) => trace.param === param));
  if (draft.traces.length + missing.length > MAX_TRACES) {
    return { error: `Графиков вместе с недостающими скрытыми (${missing.join(", ")}) больше ${MAX_TRACES} — уберите график` };
  }
  if (draft.purpose === "flex" && !draft.traces.some((trace) => trace.memory)) {
    return { error: "Экран шевеления сравнивает с памятью — отметьте «Память» хотя бы у одного графика" };
  }
  const traces: VnaScreenTrace[] = [];
  for (const [index, t] of draft.traces.entries()) {
    const where = `График ${index + 1}`;
    let scale: VnaScreenTrace["scale"] = t.mode === "manual" ? { pdiv: 0, rlev: null, rpos: null } : t.mode;
    if (t.mode === "manual") {
      const pdiv = toNumber(t.pdiv);
      if (pdiv === null || pdiv <= 0) return { error: `${where}: деление — число больше нуля` };
      if (pdiv > LIMITS_OF.level) return { error: `${where}: деление — не больше 1 000 000` };
      if (t.format === "SMIT") scale = { pdiv, rlev: null, rpos: null };
      else {
        const rlev = toNumber(t.rlev);
        const rpos = toNumber(t.rpos);
        if (rlev === null) return { error: `${where}: уровень — число` };
        if (Math.abs(rlev) > LIMITS_OF.level) return { error: `${where}: уровень — от −1 000 000 до 1 000 000` };
        if (rpos === null || !Number.isInteger(rpos) || rpos < 0 || rpos > 10) return { error: `${where}: позиция — целое от 0 до 10` };
        scale = { pdiv, rlev, rpos };
      }
    }
    let fixed: VnaScreenTrace["fixed"] = null;
    if (t.limit === "fixed") {
      const value = toNumber(t.value);
      if (value === null) return { error: `${where}: своё число линии не задано` };
      if (Math.abs(value) > LIMITS_OF.level) return { error: `${where}: число линии — от −1 000 000 до 1 000 000` };
      fixed = { kind: t.format === "MLOG" ? "lower" : "upper", value };
    }
    traces.push({ param: t.param, format: t.format, scale, limit: t.limit, fixed, hidden: t.hidden, memory: t.memory });
  }
  const markers: number[] = [];
  for (const part of draft.markers.split(/[;\s]+/).filter(Boolean)) {
    const ghz = toNumber(part);
    if (ghz === null || ghz <= 0) return { error: `Маркеры: «${part}» — не частота в ГГц` };
    if (ghz < LIMITS_OF.markerGhz[0] || ghz > LIMITS_OF.markerGhz[1]) return { error: `Маркеры: ${part} ГГц — вне диапазона ВАЦ 0,1–18000 МГц` };
    markers.push(Math.round(ghz * 1e9));
  }
  if (markers.length > LIMITS_OF.markers) return { error: `Маркеры: не больше ${LIMITS_OF.markers}` };
  if (markers.some((hz, index) => index > 0 && hz <= markers[index - 1])) return { error: "Маркеры: по возрастанию частоты, без повторов" };
  return { fields: { name, purpose: draft.purpose, split: draft.split, traces, markers_hz: markers,
    note: draft.note.trim(), is_default: draft.is_default } };
}

/** Что можно выбрать в графике при данном формате: «по норме» — только КСВН; линия из нормы — КСВН и потери; из ЭТК — КСВН. */
export function allowed(format: VnaScreenFormat) {
  return {
    modes: (format === "SWR" ? ["auto", "norm", "manual"] : ["auto", "manual"]) as ScaleMode[],
    limits: (format === "SMIT" ? ["none"] : format === "SWR" ? ["norm", "etk", "fixed", "none"]
      : format === "MLOG" ? ["norm", "fixed", "none"] : ["fixed", "none"]) as VnaScreenLimit[],
  };
}

function retyped(trace: DraftTrace, format: VnaScreenFormat): DraftTrace {
  const can = allowed(format);
  const next = { ...trace, format };
  if (!can.modes.includes(next.mode)) next.mode = "auto";
  if (!can.limits.includes(next.limit)) next.limit = "none";
  if (format === "SMIT") { next.rlev = ""; next.rpos = ""; }
  return next;
}

// ---- превью на примерных данных ---------------------------------------------------------------------------------

const PREVIEW_POINTS = 121;
const BAND_GHZ: [number, number] = [0.01, 18];

function sample(param: VnaScreenParam, format: VnaScreenFormat): { x: number; y: number }[] {
  const reflection = param === "S11" || param === "S22";
  const shift = param === "S22" || param === "S12" ? 0.7 : 0;
  return Array.from({ length: PREVIEW_POINTS }, (_, index) => {
    const ghz = BAND_GHZ[0] + ((BAND_GHZ[1] - BAND_GHZ[0]) * index) / (PREVIEW_POINTS - 1);
    const gamma = 0.008 + 0.0012 * ghz + 0.004 * Math.abs(Math.sin(ghz * 1.3 + shift));
    let y: number;
    if (format === "SWR") y = reflection ? (1 + gamma) / (1 - gamma) : 1.01 + 0.002 * ghz;
    else if (format === "MLOG") y = reflection ? 20 * Math.log10(gamma) : -(0.02 + 0.03 * Math.sqrt(ghz)) - 0.004 * Math.sin(ghz * 2 + shift);
    else if (format === "PHAS") y = ((((-ghz * 0.6 * 360) % 360) + 540) % 360) - 180;
    else y = 0.6 + 0.01 * Math.sin(ghz * 3 + shift);   // ГВЗ, нс
    return { x: ghz, y };
  });
}

function sampleLine(trace: DraftTrace): number | null {
  if (trace.limit === "fixed") return toNumber(trace.value);
  if (trace.limit === "etk") return SAMPLE_NORM.etk;
  if (trace.limit === "norm") return trace.format === "SWR" ? SAMPLE_NORM.swr : SAMPLE_NORM.loss;
  return null;
}

/** Нижняя и верхняя граница по шкале графика: 10 делений, как на S2VNA. */
export function yRange(trace: DraftTrace, values: number[]): [number, number] {
  if (trace.mode === "norm") {
    const top = Math.ceil(Math.round(Math.max(SAMPLE_NORM.swr * 1.15, 1.2) * 20 * 1e6) / 1e6) / 20;
    return [1, top];
  }
  if (trace.mode === "manual") {
    const pdiv = toNumber(trace.pdiv) ?? 0;
    const rlev = toNumber(trace.rlev) ?? 0;
    const rpos = toNumber(trace.rpos) ?? 0;
    if (pdiv > 0) return [rlev - rpos * pdiv, rlev + (10 - rpos) * pdiv];
  }
  const low = Math.min(...values);
  const high = Math.max(...values);
  const pad = (high - low || Math.abs(high) || 1) * 0.1;
  return [low - pad, high + pad];
}

/** Ширина графика в единицах рисунка ≈ его ширине в окне (1320 px): подписи остаются 10–11 px при любом расположении. */
const PREVIEW_WIDTH = 1240;

function Graph({ traces, number, cols }: { traces: DraftTrace[]; number: number; cols: number }) {
  const SVG_W = Math.round(PREVIEW_WIDTH / cols);
  const SVG_H = cols === 1 ? 240 : 170;
  const shown = traces.filter((trace) => !trace.hidden);
  const lead = shown[0];
  if (!lead) return <div className="vna-screen-graph empty-graph"><span className="muted-text">График {number}: пусто</span></div>;
  const left = 44, right = 8, top = 8, bottom = 18;
  const w = SVG_W - left - right, h = SVG_H - top - bottom;
  const title = shown.map(traceTitle).join(", ");
  if (lead.format === "SMIT") {
    const full = toNumber(lead.pdiv) || 1;
    const r = Math.min(w, h) / 2, cx = left + w / 2, cy = top + h / 2;
    const points = sample(lead.param, "SWR").map((point, index) => {
      const gamma = (point.y - 1) / (point.y + 1);
      const angle = -index / 8;
      return `${(cx + (gamma * Math.cos(angle) / full) * r).toFixed(1)},${(cy - (gamma * Math.sin(angle) / full) * r).toFixed(1)}`;
    }).join(" ");
    return <figure className="vna-screen-graph"><figcaption>{number}. {title}</figcaption>
      <svg viewBox={`0 0 ${SVG_W} ${SVG_H}`} role="img" aria-label={`График ${number}: ${title}, пример`}>
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="#9aa5b1" />
        <line x1={cx - r} y1={cy} x2={cx + r} y2={cy} stroke="#c9d1d9" />
        <circle cx={cx + r / 2} cy={cy} r={r / 2} fill="none" stroke="#c9d1d9" />
        <polyline points={points} fill="none" stroke="#1f6feb" strokeWidth="1.5" />
      </svg></figure>;
  }
  const data = sample(lead.param, lead.format);
  const line = sampleLine(lead);
  const [low, high] = yRange(lead, [...data.map((p) => p.y), ...(line === null ? [] : [line])]);
  const span = high - low || 1;
  const y = (value: number) => top + h - ((value - low) / span) * h;
  const x = (ghz: number) => left + ((ghz - BAND_GHZ[0]) / (BAND_GHZ[1] - BAND_GHZ[0])) * w;
  const clip = (value: number) => Math.min(Math.max(y(value), top), top + h);
  const digits = span < 0.5 ? 3 : span < 5 ? 2 : 0;
  const series = shown.filter((trace) => trace.format === lead.format).map((trace, index) => ({
    key: index, color: index === 0 ? "#1f6feb" : "#8250df",
    points: sample(trace.param, trace.format).map((p) => `${x(p.x).toFixed(1)},${clip(p.y).toFixed(1)}`).join(" "),
  }));
  return <figure className="vna-screen-graph"><figcaption>{number}. {title}</figcaption>
    <svg viewBox={`0 0 ${SVG_W} ${SVG_H}`} role="img" aria-label={`График ${number}: ${title}, пример`}>
      {Array.from({ length: 11 }, (_, i) => <line key={i} x1={left} x2={left + w} y1={top + (h * i) / 10} y2={top + (h * i) / 10}
        stroke={i === 0 || i === 10 ? "#9aa5b1" : "#e1e6eb"} />)}
      <line x1={left} x2={left} y1={top} y2={top + h} stroke="#9aa5b1" />
      <line x1={left + w} x2={left + w} y1={top} y2={top + h} stroke="#9aa5b1" />
      <text x={left - 4} y={top + 4} textAnchor="end" fontSize="12" fill="#57606a">{formatNumber(high, { digits })}</text>
      <text x={left - 4} y={top + h + 3} textAnchor="end" fontSize="12" fill="#57606a">{formatNumber(low, { digits })}</text>
      <text x={left} y={SVG_H - 4} fontSize="12" fill="#57606a">0,01 ГГц</text>
      <text x={left + w} y={SVG_H - 4} textAnchor="end" fontSize="12" fill="#57606a">18 ГГц</text>
      {series.map((s) => <polyline key={s.key} points={s.points} fill="none" stroke={s.color} strokeWidth="1.5" />)}
      {line !== null && line >= low && line <= high && <line x1={left} x2={left + w} y1={y(line)} y2={y(line)} stroke="#c00000"
        strokeWidth="1.5" strokeDasharray="5 3" />}
    </svg></figure>;
}

export function ScreenPreview({ draft }: { draft: Draft }) {
  const layout = layoutOf(draft.split);
  const graphs = layout.rows * layout.cols;
  const slots: DraftTrace[][] = Array.from({ length: graphs }, () => []);
  draft.traces.forEach((trace, index) => slots[index % graphs].push(trace));
  return <div className="vna-screen-preview" style={{ gridTemplateColumns: `repeat(${layout.cols}, minmax(0, 1fr))` }}
    aria-label="Превью экрана на примерных данных">
    {slots.map((traces, index) => <Graph key={index} traces={traces} number={index + 1} cols={layout.cols} />)}
  </div>;
}

// ---- окно экрана --------------------------------------------------------------------------------------------------

function ScreenWindow({ screen, canEdit, canShow, showHint, onClose, onSaved, show }: {
  screen: VnaScreen | null; canEdit: boolean; canShow: boolean; showHint: string;
  onClose: () => void; onSaved: (screen: VnaScreen, message: string) => void; show: (id: string) => Promise<string>;
}) {
  const initial = useMemo(() => draftOf(screen ?? NEW_SCREEN), [screen]);
  const [draft, setDraft] = useState<Draft>(initial);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial);
  const readOnly = !canEdit;
  const patch = (change: Partial<Draft>) => { setDraft((current) => ({ ...current, ...change })); setNotice(""); };
  const patchTrace = (index: number, change: Partial<DraftTrace> | ((trace: DraftTrace) => DraftTrace)) =>
    setDraft((current) => ({ ...current, traces: current.traces.map((trace, i) => i !== index ? trace
      : typeof change === "function" ? change(trace) : { ...trace, ...change }) }));

  async function run(operation: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try { await operation(); } catch (reason) { setError(failureText(reason, "Экран не сохранён")); } finally { setBusy(false); }
  }
  const save = () => run(async () => {
    const parsed = fieldsOf(draft);
    if ("error" in parsed) { setError(parsed.error); return; }
    const saved = screen ? await vnaScreensApi.save(screen.id, screen.version, parsed.fields) : await vnaScreensApi.create(parsed.fields);
    onSaved(saved, screen ? `Экран «${saved.name}» сохранён, версия ${saved.version}` : `Экран «${saved.name}» добавлен`);
  });
  const toggle = () => screen && run(async () => {
    const saved = await vnaScreensApi.setActive(screen.id, screen.version, !screen.active);
    onSaved(saved, saved.active ? `Экран «${saved.name}» включён` : `Экран «${saved.name}» отключён`);
  });
  const showOnVna = () => screen && run(async () => {
    const refusal = await show(screen.id);
    if (refusal) setError(refusal); else setNotice(`«${screen.name}» отправлен на ВАЦ — S2VNA покажет его через несколько секунд`);
  });

  return <PageWindow label={screen ? `Экран ВАЦ ${screen.name}` : "Новый экран ВАЦ"} width={1320} close={onClose}>
    <form className="panel vna-screen-window" onSubmit={(event) => { event.preventDefault(); if (canEdit) save(); }}>
      <div className="vna-screen-head">
        <h2>{screen ? `Экран «${screen.name}»` : "Новый экран"}</h2>
        {screen && <span className="muted-text num">версия {screen.version}{screen.updated_by ? `, ${screen.updated_by}` : ""}
          {screen.updated_at ? `, ${formatMoment(screen.updated_at)}` : ""}</span>}
        <button type="button" className="secondary small" onClick={onClose}>Закрыть</button>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {notice && <div className="success-banner" role="status">{notice}</div>}
      <div className="vna-screen-fields">
        <label>Название<input value={draft.name} maxLength={120} disabled={readOnly} required
          onChange={(event) => patch({ name: event.target.value })} /></label>
        <label>Назначение<select value={draft.purpose} disabled={readOnly || Boolean(screen?.is_default)}
          onChange={(event) => patch({ purpose: event.target.value as VnaScreenPurpose })}>
          {Object.entries(PURPOSES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
        <label>Расположение графиков<select value={draft.split} disabled={readOnly}
          onChange={(event) => patch({ split: Number(event.target.value) })}>
          {!LAYOUTS.some((layout) => layout.code === draft.split) && <option value={draft.split}>{layoutOf(draft.split).label}</option>}
          {LAYOUTS.map((layout) => <option key={layout.code} value={layout.code}>{layout.label}</option>)}
        </select></label>
        <label>Маркеры, ГГц<input value={draft.markers} disabled={readOnly} inputMode="decimal" placeholder="1; 3; 6; 12; 18"
          onChange={(event) => patch({ markers: event.target.value })} /></label>
        <label className="wide">Описание<input value={draft.note} maxLength={500} disabled={readOnly}
          onChange={(event) => patch({ note: event.target.value })} /></label>
        <label className="vna-screen-check"><input type="checkbox" checked={draft.is_default}
          disabled={readOnly || Boolean(screen?.is_default) || (screen ? !screen.active : false)}
          onChange={(event) => patch({ is_default: event.target.checked })} />
          Основной для «{PURPOSES[draft.purpose]}» — рабочее место ставит его само</label>
      </div>
      <table className="vna-screen-traces" aria-label="Графики экрана">
        <thead><tr>
          <th scope="col" className="num">№</th><th scope="col">Параметр</th><th scope="col">Формат</th><th scope="col">Шкала</th>
          <th scope="col">Деление</th><th scope="col">Уровень</th><th scope="col">Позиция</th><th scope="col">Линия допуска</th>
          <th scope="col">Число линии</th><th scope="col">Скрыт</th><th scope="col">Память</th>{canEdit && <th scope="col" aria-label="Убрать график" />}
        </tr></thead>
        <tbody>{draft.traces.map((trace, index) => {
          const can = allowed(trace.format);
          const manual = trace.mode === "manual";
          const smith = trace.format === "SMIT";
          const n = index + 1;
          return <tr key={index}>
            <td className="num">{n}</td>
            <td><select aria-label={`График ${n}: параметр`} value={trace.param} disabled={readOnly}
              onChange={(event) => patchTrace(index, { param: event.target.value as VnaScreenParam })}>
              {PARAMS.map((param) => <option key={param} value={param}>{param}</option>)}</select></td>
            <td><select aria-label={`График ${n}: формат`} value={trace.format} disabled={readOnly}
              onChange={(event) => patchTrace(index, (t) => retyped(t, event.target.value as VnaScreenFormat))}>
              {Object.entries(FORMATS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></td>
            <td><select aria-label={`График ${n}: шкала`} value={trace.mode} disabled={readOnly}
              onChange={(event) => patchTrace(index, { mode: event.target.value as ScaleMode })}>
              {can.modes.map((mode) => <option key={mode} value={mode}>{mode === "auto" ? "Авто" : mode === "norm" ? "По норме" : "Вручную"}</option>)}
            </select></td>
            <td><input className="num" aria-label={`График ${n}: деление`} inputMode="decimal" value={manual ? trace.pdiv : ""}
              disabled={readOnly || !manual} onChange={(event) => patchTrace(index, { pdiv: event.target.value })} /></td>
            <td><input className="num" aria-label={`График ${n}: уровень`} inputMode="decimal" value={manual && !smith ? trace.rlev : ""}
              disabled={readOnly || !manual || smith} onChange={(event) => patchTrace(index, { rlev: event.target.value })} /></td>
            <td><input className="num" aria-label={`График ${n}: позиция`} inputMode="numeric" value={manual && !smith ? trace.rpos : ""}
              disabled={readOnly || !manual || smith} onChange={(event) => patchTrace(index, { rpos: event.target.value })} /></td>
            <td><select aria-label={`График ${n}: линия допуска`} value={trace.limit} disabled={readOnly}
              onChange={(event) => patchTrace(index, { limit: event.target.value as VnaScreenLimit })}>
              {can.limits.map((limit) => <option key={limit} value={limit}>{LIMITS[limit]}</option>)}</select></td>
            <td><input className="num" aria-label={`График ${n}: число линии`} inputMode="decimal"
              value={trace.limit === "fixed" ? trace.value : ""} disabled={readOnly || trace.limit !== "fixed"}
              onChange={(event) => patchTrace(index, { value: event.target.value })} /></td>
            <td className="center"><input type="checkbox" aria-label={`График ${n}: скрыт`} checked={trace.hidden} disabled={readOnly}
              onChange={(event) => patchTrace(index, { hidden: event.target.checked })} /></td>
            <td className="center"><input type="checkbox" aria-label={`График ${n}: память`} checked={trace.memory} disabled={readOnly}
              onChange={(event) => patchTrace(index, { memory: event.target.checked })} /></td>
            {canEdit && <td><button type="button" className="secondary small" aria-label={`Убрать график ${n}`}
              disabled={draft.traces.length <= 1}
              onClick={() => setDraft((current) => ({ ...current, traces: current.traces.filter((_, i) => i !== index) }))}>Убрать</button></td>}
          </tr>;
        })}</tbody>
      </table>
      {canEdit && <button type="button" className="secondary small vna-screen-add" disabled={draft.traces.length >= MAX_TRACES}
        onClick={() => setDraft((current) => ({ ...current, traces: [...current.traces, { ...NEW_TRACE }] }))}>Добавить график</button>}
      <h3>Превью на примерных данных</h3>
      <p className="muted-text">Линия «из нормы СИ» показана для примера нормы КСВН 1,06 и потерь 0,2 дБ, «из ЭТК» — КСВН 1,30;
        на приборе — норма объекта замера.</p>
      <ScreenPreview draft={draft} />
      <div className="form-actions vna-screen-actions">
        {canEdit && <button type="submit" className="primary" disabled={busy || (Boolean(screen) && !dirty)}>Сохранить</button>}
        {screen && <button type="button" className="secondary" disabled={busy || !canShow || dirty || !screen.active}
          title={dirty ? "Сначала сохраните изменения" : showHint || undefined} onClick={() => void showOnVna()}>Показать на ВАЦ</button>}
        {screen && canEdit && <button type="button" className="secondary" disabled={busy || screen.is_default || dirty}
          onClick={() => void toggle()}>{screen.active ? "Отключить" : "Включить"}</button>}
        {screen && (dirty ? <span className="muted-text">Есть несохранённые изменения</span>
          : !canShow && showHint ? <span className="muted-text">{showHint}</span> : null)}
      </div>
    </form>
  </PageWindow>;
}

const NEW_TRACE: DraftTrace = { param: "S11", format: "SWR", mode: "auto", pdiv: "", rlev: "", rpos: "", limit: "none", value: "",
  hidden: false, memory: false };
const NEW_SCREEN: VnaScreenFields = {
  name: "", purpose: "engineer", split: 8, note: "", is_default: false, markers_hz: [1e9, 3e9, 6e9, 12e9, 18e9],
  traces: [
    { param: "S11", format: "SWR", scale: "auto", limit: "none", fixed: null, hidden: false, memory: false },
    { param: "S21", format: "MLOG", scale: "auto", limit: "none", fixed: null, hidden: false, memory: false },
    { param: "S12", format: "MLOG", scale: "auto", limit: "none", fixed: null, hidden: false, memory: false },
    { param: "S22", format: "SWR", scale: "auto", limit: "none", fixed: null, hidden: false, memory: false },
  ],
};

/** «Показать на ВАЦ» можно в своём откалиброванном сеансе, пока прибор свободен. */
export function showBlock(session: VnaBenchSession | null, calibrationValid: boolean): string {
  if (!session) return "Показать на ВАЦ можно в открытом сеансе — откройте его на вкладке «Замер»";
  if (session.owned === false) return "Сеанс открыл другой сотрудник";
  if (!calibrationValid) return "Сначала калибровка АКМ и нагрузка на обоих портах";
  if (session.command) return "ВАЦ выполняет команду — дождитесь её окончания";
  return "";
}

// ---- вкладка «Экраны» ---------------------------------------------------------------------------------------------

export function VnaScreensEditor({ session, calibrationValid, canEdit, show }: {
  session: VnaBenchSession | null; calibrationValid: boolean; canEdit: boolean; show: (id: string) => Promise<string>;
}) {
  const [screens, setScreens] = useState<VnaScreen[] | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [open, setOpen] = useState<VnaScreen | "new" | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    vnaScreensApi.list().then((data) => { if (active) { setScreens(data.screens); setError(""); } })
      .catch((reason) => { if (active) setError(failureText(reason, "Не удалось прочитать экраны ВАЦ")); });
    return () => { active = false; };
  }, [attempt]);

  const block = showBlock(session, calibrationValid);
  const onSaved = (saved: VnaScreen, text: string) => {
    setMessage(text);
    setOpen(saved);
    setAttempt((value) => value + 1);
  };
  const onScreen = session?.view_screen_id ?? null;

  return <div className="panel vna-screens">
    <div className="vna-screens-head">
      <h2>Экраны ВАЦ</h2>
      {canEdit && <button type="button" className="primary small" disabled={!screens} onClick={() => { setMessage(""); setOpen("new"); }}>Новый экран</button>}
    </div>
    {message && <div className="success-banner" role="status">{message}</div>}
    {error && <div className="error-banner" role="alert">{error}
      <button type="button" className="secondary small" onClick={() => setAttempt((value) => value + 1)}>Повторить</button></div>}
    {!screens && !error && <p className="empty">Загрузка…</p>}
    {screens && screens.length === 0 && <p className="empty">Экранов нет — добавьте первый кнопкой «Новый экран».</p>}
    {screens && screens.length > 0 && <table className="vna-screens-list" aria-label="Экраны ВАЦ">
      <thead><tr><th scope="col">Название</th><th scope="col">Назначение</th><th scope="col">Графики</th>
        <th scope="col">Расположение</th><th scope="col">Состояние</th><th scope="col" className="num">Версия</th><th scope="col">Изменён</th></tr></thead>
      <tbody>{screens.map((screen) => <tr key={screen.id} className={`clickable${screen.active ? "" : " inactive"}`} tabIndex={0}
        onClick={() => { setMessage(""); setOpen(screen); }}
        onKeyDown={(event) => { if (event.key === "Enter") { setMessage(""); setOpen(screen); } }}>
        <td><strong>{screen.name}</strong></td>
        <td>{PURPOSES[screen.purpose]}</td>
        <td className="wrap">{screen.traces.filter((trace) => !trace.hidden).map(traceTitle).join(" · ")}</td>
        <td>{layoutOf(screen.split).label}</td>
        <td>{!screen.active ? <span className="muted-text">отключён</span>
          : screen.is_default ? <span className="tone-ok">основной</span> : <span className="muted-text">для просмотра</span>}
          {onScreen === screen.id && <span className="tone-info"> · на ВАЦ</span>}</td>
        <td className="num">{screen.version}</td>
        <td className="num">{screen.updated_by ? `${screen.updated_by}, ${formatMoment(screen.updated_at)}` : "—"}</td>
      </tr>)}</tbody>
    </table>}
    {open && <ScreenWindow key={open === "new" ? "new" : `${open.id}:${open.version}`} screen={open === "new" ? null : open}
      canEdit={canEdit} canShow={!block} showHint={block} onClose={() => setOpen(null)} onSaved={onSaved} show={show} />}
  </div>;
}

/** На вкладке «Замер»: вывести на ВАЦ другой сохранённый экран во время работы (только смена вида, без развёртки). */
export function ScreenSwitcher({ session, disabled, show }: {
  session: VnaBenchSession; disabled: boolean; show: (id: string) => Promise<string>;
}) {
  const [screens, setScreens] = useState<VnaScreen[]>([]);
  const [chosen, setChosen] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    vnaScreensApi.list().then((data) => { if (active) setScreens(data.screens.filter((screen) => screen.active)); })
      .catch(() => { if (active) setScreens([]); });
    return () => { active = false; };
  }, []);
  if (!screens.length) return null;
  const value = chosen || session.view_screen_id || "";
  return <div className="panel vna-screen-switch">
    <label>Вид на ВАЦ — сохранённый экран, без развёртки<select value={value} onChange={(event) => { setChosen(event.target.value); setError(""); }}>
      <option value="" disabled>Выберите экран</option>
      {screens.map((screen) => <option key={screen.id} value={screen.id}>{screen.name}</option>)}
    </select></label>
    <button type="button" className="secondary small" disabled={disabled || !value}
      onClick={() => void show(value).then(setError)}>Показать на ВАЦ</button>
    {error && <span className="tone-danger" role="alert">{error}</span>}
  </div>;
}
