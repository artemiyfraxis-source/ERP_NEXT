import type { ReactNode } from "react";
import { plural, type PinUnit } from "./pinoutCheck";
import { connName, firstFreePin, occupiedPins, type EndValue, type PinConn, type PinRow, type SpliceLine } from "./tcGenPinTable";
import type { Side, WireEnd } from "./tcGenLogic";
import "./pinTable.css";

/* «Распиновка и сростки» (владелец 25.10): одна таблица — строка на жилу, в ней оба конца. Конец выбирается в поле:
   контакт разъёма своей стороны (занятые помечены), сросток («С1» — выбрать тот же значит соединить), свободный конец.
   Под таблицей — сростки: состав, гильза, разъединение. Логика — tcGenPinTable.ts; состояние формы то же, что у схемы. */

const sideRu = (side: Side) => (side === "A" ? "А" : "В");

function Swatch({ color, stripe }: { color: string | null; stripe: string | null }) {
  return <span className="pt-dot" aria-hidden="true" style={{ background: color || "var(--pin-free-bg)", boxShadow: stripe ? `inset 0 0 0 2px ${stripe}` : undefined }} />;
}

export type SleeveControl = {
  /** Гильза сростка: показывается, когда способ — опрессовка гильзой. */
  options: Array<{ id: string; label: string }>;
  valueOf: (spliceId: string) => string;
  onChange: (spliceId: string, compId: string) => void;
};

export function PinoutTable({ summary, rows, conns, units, splices, spliceIds, sleeve, methodControl, onEnd, onLength, onLabel, onSplit, onRemoveSplice, onRenameSplice }: {
  summary: string;
  rows: PinRow[];
  conns: PinConn[];
  units: PinUnit[];
  splices: SpliceLine[];
  /** Сростки по порядку для выбора: id и подпись. */
  spliceIds: Array<{ id: string; label: string }>;
  sleeve: SleeveControl | null;
  methodControl: ReactNode;
  onEnd: (row: PinRow, side: Side, value: EndValue) => void;
  onLength: (idx: number, mm: number) => void;
  /** Обозначение разъёма по чертежу («XS1»). */
  onLabel: (epId: string, label: string) => void;
  onSplit: (end: WireEnd) => void;
  onRemoveSplice: (id: string) => void;
  /** Своё название сростка по чертежу («Spl1»); пустое — по порядку («С1»). */
  onRenameSplice: (id: string, name: string) => void;
}) {
  const select = (row: PinRow, side: Side) => {
    const cell = row.cells[side];
    const sideConns = conns.filter((c) => c.side === side);
    const label = `Конец ${sideRu(side)} провода № ${row.num}`;
    return <div className="pt-end">
      <select aria-label={label} value={cell.value} className={cell.value === "" && sideConns.length ? "is-open" : undefined}
        onChange={(event) => onEnd(row, side, event.target.value)}>
        <option value="">{sideConns.length ? "— не задан —" : "— свободный —"}</option>
        {sideConns.map((c) => {
          const busy = occupiedPins(c, units);
          const free = firstFreePin(c, units);
          const count = c.count > 0 ? c.count : Math.max(0, ...busy.keys()) + 1;
          const own = row.cells[side].value.startsWith(`pin:${c.id}:`) ? Number(row.cells[side].value.split(":")[2]) : 0;
          return <optgroup key={c.id} label={`${connName(c)}${c.conn ? ` · ${c.conn}` : ""}`}>
            {free != null && <option value={`pin:${c.id}:auto`}>{`${connName(c)} · первый свободный (${free})`}</option>}
            {Array.from({ length: count }, (_, k) => k + 1).map((pin) => {
              const who = (busy.get(pin) || []).filter((n) => !(pin === own && n === row.num));
              return <option key={pin} value={`pin:${c.id}:${pin}`}>{`${connName(c)} · контакт ${pin}${who.length ? ` — занят № ${who.join(", ")}` : ""}`}</option>;
            })}
          </optgroup>;
        })}
        <optgroup label="Сростки">
          {spliceIds.map((s) => <option key={s.id} value={`sp:${s.id}`}>{`Сросток ${s.label}`}</option>)}
          <option value="new">+ Новый сросток</option>
        </optgroup>
        <option value="tail">Свободный конец</option>
      </select>
      {cell.extra.length > 0 && <small className="pt-extra" title="Ответвление: провод идёт ещё и на этот контакт">+ {cell.extra.join(", ")}</small>}
    </div>;
  };
  return <div className="pt" aria-label="Распиновка и сростки" role="group">
    <div className="pt-head">
      <span className="pt-summary" role="status" aria-live="polite">{summary}</span>
    </div>
    <div className="pt-conns" role="group" aria-label="Разъёмы по чертежу">
      {conns.map((c) => <label className="pt-conn" key={c.id}>
        <b className="ep-badge">{c.key}</b>
        <input aria-label={`Обозначение разъёма ${c.key}`} autoComplete="off" spellCheck={false} value={c.designation} placeholder="XS1, XP1…" onChange={(event) => onLabel(c.id, event.target.value)} />
        <span className="pt-conn-art" title={c.conn}>{c.conn || "разъём не выбран"}</span>
      </label>)}
    </div>
    <div className="pt-scroll">
      <table className="pt-table">
        <thead><tr><th scope="col" className="pt-num">№</th><th scope="col">Провод</th><th scope="col" className="pt-len">Длина, мм</th><th scope="col">Конец А</th><th scope="col">Конец В</th></tr></thead>
        <tbody>
          {rows.map((row) => [
            <tr key={row.key} className={`${row.first ? "pt-first" : ""}${row.problems.length ? " has-problem" : ""}`}>
              <td className="pt-num">{row.num}</td>
              <td className="pt-wire"><div className="pt-wire-in"><Swatch color={row.color} stripe={row.stripe} /><span className="pt-wire-name" title={`${row.name}${row.colorName ? ` · ${row.colorName}` : ""}`}>{row.name || "провод"}{row.colorName && <small>{row.colorName}</small>}</span></div></td>
              <td className="pt-len">{row.first
                ? <input type="number" min={0} inputMode="numeric" autoComplete="off" aria-label={`Длина провода № ${row.num}`} value={row.length || ""} placeholder="—" onChange={(event) => onLength(row.idx, Number(event.target.value) || 0)} />
                : <span className="pt-same" title="Длина общая для жил одного кабеля">″</span>}</td>
              <td>{select(row, "A")}</td>
              <td>{select(row, "B")}</td>
            </tr>,
            row.problems.length > 0 && <tr key={`${row.key}!`} className="pt-note"><td /><td colSpan={4}>{row.problems.map((text) => <span className="tone-danger" key={text}>{text}</span>)}</td></tr>,
          ])}
        </tbody>
      </table>
    </div>
    <div className="pt-splices">
      <div className="pt-splices-head"><b>Сростки</b>{methodControl}</div>
      {!splices.length && <p className="pt-none">Сростков нет. Выберите «+ Новый сросток» у конца провода, затем «Сросток С1» у остальных концов — они соединятся.</p>}
      {splices.map((line) => <div className="pt-splice" key={line.id}>
        <input className="pt-splice-name" aria-label={`Название сростка ${line.label}`} title="Название сростка по чертежу (Spl1); пусто — по порядку" value={line.name} placeholder={line.label}
          onChange={(event) => onRenameSplice(line.id, event.target.value)} />
        <span className="pt-splice-count">{line.ends.length} {plural(line.ends.length, "конец", "конца", "концов")}</span>
        {sleeve ? <select className="pt-sleeve" aria-label={`Гильза ${line.label}`} value={sleeve.valueOf(line.id)} onChange={(event) => sleeve.onChange(line.id, event.target.value)}>
          <option value="">— гильза не выбрана —</option>
          {sleeve.options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
        </select> : <span />}
        <button type="button" className="btn-remove" title={`Разъединить ${line.label}: концы станут не заданными`} aria-label={`Разъединить ${line.label}`} onClick={() => onRemoveSplice(line.id)}>×</button>
        <span className="pt-splice-ends">{line.ends.map((item) => <span className="pt-chip" key={`${item.end.idx}.${item.end.unit}${item.end.side}`}>
          <span>№ {item.num} {item.text} · {sideRu(item.end.side)}</span>{item.where && <small>{item.where}</small>}
          <button type="button" className="btn-remove" title={`Убрать из ${line.label}`} aria-label={`Убрать № ${item.num} конец ${sideRu(item.end.side)} из ${line.label}`} onClick={() => onSplit(item.end)}>×</button>
        </span>)}</span>
        {line.problem && <small className="tone-danger pt-splice-problem">{line.problem}</small>}
      </div>)}
    </div>
  </div>;
}
