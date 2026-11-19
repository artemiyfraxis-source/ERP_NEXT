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

/** `field(key, label)` рисует поле ввода с живым значением станка; чертёж только ставит его на место. */
export function Ob019WireDrawing({ sizes, middle, field }: {
  sizes: WireSizes; middle: boolean; field: (key: string, label: string) => ReactNode;
}) {
  const zones = wireZones(sizes);
  const W = 1000;
  const front = (zones.front / 100) * W;
  const back = W - (zones.back / 100) * W;
  const slugFront = front * zones.slugFront;
  const slugBack = (W - back) * zones.slugBack;
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
      <line className="wire-cut" x1={front} y1={0} x2={front} y2={48} />
      <line className="wire-cut" x1={back} y1={0} x2={back} y2={48} />
    </svg>
    <div className="wire-cell wire-front-bottom">{field("pull_front_mm", "Стягивание начала")}</div>
    <div className="wire-cell wire-middle-bottom">
      {middle && <span className="machine-muted">Средняя зачистка включена — задаётся на пульте</span>}
    </div>
    <div className="wire-cell wire-back-bottom">{field("pull_back_mm", "Стягивание конца")}</div>
    <div className="wire-dim">
      <span className="wire-dim-line" aria-hidden="true" />
      {field("length_mm", "Длина")}
      <span className="wire-dim-line" aria-hidden="true" />
    </div>
  </section>;
}
