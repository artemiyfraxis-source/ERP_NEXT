import type { ReactNode } from "react";

/** Чертёж провода в карточке ОБ.019 (владелец 19.11.2026): один провод — изоляция посередине, зачищенная жила на концах,
 *  стянутая изоляция у торца; поля ввода стоят на своих местах чертежа. Только раскладка: значения, их проверка и
 *  запись — в Ob019SettingsPanel.tsx. */

export interface WireSizes {
  strip_front: number | null;
  pull_front: number | null;
  strip_back: number | null;
  pull_back: number | null;
}

/** Ширина зачистки на чертеже, % ширины провода: растёт с размером, но не уже поля и не шире трети. */
const STRIP_MIN = 20;
const STRIP_MAX = 30;
const STRIP_PER_MM = 1;

function stripShare(strip: number | null): number {
  const value = strip && strip > 0 ? strip : 0;
  return Math.min(STRIP_MAX, Math.max(STRIP_MIN, STRIP_MIN + value * STRIP_PER_MM));
}

/** Доля стянутой изоляции (остаётся на торце) в зоне зачистки: 0 — снята целиком. */
function slugShare(strip: number | null, pull: number | null): number {
  if (!strip || strip <= 0 || !pull || pull <= 0 || pull >= strip) return 0;
  return Math.min(0.7, Math.max(0.25, (strip - pull) / strip));
}

export function wireZones(sizes: WireSizes) {
  const front = stripShare(sizes.strip_front);
  const back = stripShare(sizes.strip_back);
  return { front, back, middle: 100 - front - back,
    slugFront: slugShare(sizes.strip_front, sizes.pull_front), slugBack: slugShare(sizes.strip_back, sizes.pull_back) };
}

function Blade() {
  return <svg className="wire-blade" viewBox="0 0 20 20" aria-hidden="true" focusable="false">
    <path d="M3 2h14v7l-7 9-7-9z" />
    <path d="M3 9h14" />
  </svg>;
}

/** Метки маркировки на изоляции: «ends» — у начала и у конца, «full» — `count` меток по всей длине. */
export interface WireMarks {
  mode: "none" | "ends" | "full" | null;
  count: number;
}

/** Ширина метки на чертеже (условные единицы из 1000) и отступ от края изоляции. */
const MARK_W = 34;
const MARK_GAP = 24;

/** Сколько меток рисовать «по всей длине»: длина / шаг, от 2 до 8 (чертёж не в масштабе); без данных — 4. */
export function markCount(length: number | null, step: number | null): number {
  if (!length || !step || length <= 0 || step <= 0) return 4;
  return Math.min(8, Math.max(2, Math.round(length / step)));
}

function Mark({ x }: { x: number }) {
  return <g className="wire-mark">
    {[0, 12, 24].map((dx) => <rect key={dx} x={x + dx} y={19} width={8} height={10} />)}
  </g>;
}

/** `field(key, label)` рисует поле ввода с живым значением станка; чертёж только ставит его на место. `markRow` — поля
 *  маркировки под проводом, `marks` — её метки на изоляции. */
export function Ob019WireDrawing({ sizes, middle, field, marks, markRow }: {
  sizes: WireSizes; middle: boolean; field: (key: string, label: string) => ReactNode; marks?: WireMarks; markRow?: ReactNode;
}) {
  const zones = wireZones(sizes);
  const W = 1000;
  const front = (zones.front / 100) * W;
  const back = W - (zones.back / 100) * W;
  const slugFront = front * zones.slugFront;
  const slugBack = (W - back) * zones.slugBack;
  const first = front + MARK_GAP;
  const last = back - MARK_GAP - MARK_W;
  const xs = marks?.mode === "ends" ? [first, last]
    : marks?.mode === "full" ? Array.from({ length: marks.count }, (_, index) => first + (index * (last - first)) / Math.max(1, marks.count - 1))
    : [];
  return <section className="wire-drawing" aria-label="Провод: длина и зачистка"
    style={{ gridTemplateColumns: `${zones.front}% minmax(0, 1fr) ${zones.back}%` }}>
    <div className="wire-cell wire-front-top">{field("strip_front_mm", "Зачистка начала")}</div>
    <div className="wire-cell wire-knife">
      <Blade />
      {field("diameter_mm", "Диаметр провода")}
      {field("knife_back_mm", "Отвод ножа")}
    </div>
    <div className="wire-cell wire-back-top">{field("strip_back_mm", "Зачистка конца")}</div>
    <svg className="wire-svg" viewBox={`0 0 ${W} 48`} preserveAspectRatio="none" aria-hidden="true" focusable="false">
      <rect className="wire-core" x={0} y={18} width={W} height={12} />
      <rect className="wire-insulation" x={front} y={8} width={back - front} height={32} />
      {middle && <rect className="wire-core" x={W / 2 - 30} y={18} width={60} height={12} />}
      {middle && <rect className="wire-insulation-cut" x={W / 2 - 30} y={8} width={60} height={32} />}
      {slugFront > 0 && <rect className="wire-slug" x={0} y={10} width={slugFront} height={28} />}
      {slugBack > 0 && <rect className="wire-slug" x={W - slugBack} y={10} width={slugBack} height={28} />}
      {xs.map((x, index) => <Mark key={`${index}-${x}`} x={x} />)}
      <line className="wire-cut" x1={front} y1={0} x2={front} y2={48} />
      <line className="wire-cut" x1={back} y1={0} x2={back} y2={48} />
    </svg>
    <div className="wire-cell wire-front-bottom">{field("pull_front_mm", "Стягивание начала")}</div>
    <div className="wire-cell wire-middle-bottom">
      {middle && <span className="machine-muted">Средняя зачистка включена — задаётся на пульте</span>}
    </div>
    <div className="wire-cell wire-back-bottom">{field("pull_back_mm", "Стягивание конца")}</div>
    {markRow}
    <div className="wire-dim">
      <span className="wire-dim-line" aria-hidden="true" />
      {field("length_mm", "Длина")}
      <span className="wire-dim-line" aria-hidden="true" />
    </div>
  </section>;
}
