import { useRef, type ReactNode } from "react";
import { UnitInput } from "./GenControls";
import { JACKET_STRIP_MM_, duplicateWire, fillLengthBelow, parseDecimal_, updateWire, type GenState, type WireEntry } from "./tcGenLogic";
import "./complexCables.css";

/** Строки «Кабели» на «Сложной сборке»: длина у каждого кабеля своя; «⧉» копирует строку, «↓ ниже» раздаёт длину
 *  пустым строкам ниже, Enter в длине переходит к длине следующего кабеля (владелец 14.10: «10 раз не вводить длину»). */
export function ComplexCables({ wires, anyCores, coresOf, select, update }: {
  wires: WireEntry[];
  anyCores: boolean;
  coresOf: (w: WireEntry) => number;
  select: (w: WireEntry, n: number) => ReactNode;
  update: (patch: (current: GenState) => GenState) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  // «Жил» и «Разделка» — только когда в списке есть кабель с жилами: пустые колонки сжимали название провода до «НВ4» (владелец 18.10).
  const showCores = anyCores && wires.some((w) => coresOf(w) > 1 || !!w.cores);
  const cls = `cc-row${showCores ? " cores" : ""}`;
  const focusLength = (n: number) => {
    const next = box.current?.querySelectorAll<HTMLInputElement>("input[data-cc-length]")[n];
    next?.focus(); next?.select();
  };
  return <div className="wire-list" ref={box}>
    {wires.length > 0 && <div className={`${cls} head`} aria-hidden="true"><span>Кабель</span><span>Длина по чертежу</span><span>Кол-во</span>{showCores && <><span>Жил</span><span>Разделка</span></>}<span /><span /><span /></div>}
    {!wires.length && <div className="gempty">Нет кабелей — добавьте кабель из состава</div>}
    {wires.map((w, n) => {
      const cores = coresOf(w);
      const hasCores = cores > 1 || !!w.cores;
      const canFill = w.length > 0 && wires.slice(n + 1).some((x) => !(x.length > 0));
      return <div className={cls} key={w.idx}>
        {select(w, n)}
        <UnitInput unit="мм" aria-label={`Длина кабеля ${n + 1}`} data-cc-length="" type="number" min={1} placeholder="—" value={w.length || ""}
          onChange={(event) => update((current) => updateWire(current, w.idx, { length: parseDecimal_(event.target.value) || 0 }))}
          onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); focusLength(n + 1); } }} />
        <input aria-label={`Кол-во кабеля ${n + 1}`} type="number" min={1} value={w.qty || 1} onChange={(event) => update((current) => updateWire(current, w.idx, { qty: parseDecimal_(event.target.value) || 1 }))} />
        {showCores && (hasCores ? <>
          <input aria-label={`Жил в кабеле ${n + 1}`} type="number" min={1} value={cores} onChange={(event) => update((current) => updateWire(current, w.idx, { cores: parseDecimal_(event.target.value) || 1 }))} />
          <UnitInput unit="мм" aria-label={`Разделка кабеля ${n + 1}`} type="number" min={1} value={w.jacket || JACKET_STRIP_MM_} onChange={(event) => update((current) => updateWire(current, w.idx, { jacket: event.target.value.trim() }))} />
        </> : <><span /><span /></>)}
        <button type="button" className="cc-act" title="Копировать кабель строкой ниже" aria-label={`Копировать кабель ${n + 1}`} onClick={() => update((current) => duplicateWire(current, w.idx))}>⧉</button>
        <button type="button" className="cc-act" title="Длину всем ниже: в строки ниже с пустой длиной" aria-label={`Длину всем ниже кабеля ${n + 1}`} disabled={!canFill} onClick={() => update((current) => fillLengthBelow(current, w.idx))}>↓ ниже</button>
        <button type="button" className="btn-remove" title="Удалить кабель" aria-label={`Удалить кабель ${n + 1}`} onClick={() => update((current) => ({ ...current, wires: current.wires.filter((x) => x.idx !== w.idx) }))}>×</button>
      </div>;
    })}
  </div>;
}
