import type { ReactNode } from "react";
import { plural, type PinUnit } from "./pinoutCheck";
import { connName, firstFreePin, occupiedPins, type EndValue, type PinConn, type PinRow, type SpliceLine } from "./tcGenPinTable";
import { sameWireEnd, type Side, type WireEnd, type TopologyMaterial, type TubePlacement } from "./tcGenLogic";
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

export function PinoutTable({ summary, rows, conns, units, splices, spliceIds, sleeve, methodControl, endDetails, connectorActions, onJumper, onEnd, onLength, onLabel, onSplit, onRemoveSplice, onRenameSplice }: {
  summary: string;
  rows: PinRow[];
  conns: PinConn[];
  units: PinUnit[];
  splices: SpliceLine[];
  /** Сростки по порядку для выбора: id и подпись. */
  spliceIds: Array<{ id: string; label: string }>;
  sleeve: SleeveControl | null;
  methodControl: ReactNode;
  endDetails?: (row: PinRow, side: Side) => ReactNode;
  connectorActions?: (id: string) => ReactNode;
  onJumper?: (row: PinRow) => void;
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
    const sideConns = onJumper ? conns : conns.filter((c) => c.side === side);
    const label = `Конец ${sideRu(side)} провода № ${row.num}`;
    return <div className="pt-end" style={cell.value === "lug" ? { flexDirection: "column", alignItems: "stretch" } : undefined}>
      <select aria-label={label} value={cell.value} className={cell.value === "" && sideConns.length ? "is-open" : undefined}
        onChange={(event) => event.target.value === "jumper" ? onJumper?.(row) : onEnd(row, side, event.target.value)}>
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
        {endDetails && <option value="lug">Наконечник</option>}
        {onJumper && side === "B" && <option value="jumper">Перемычка в одном разъёме</option>}
      </select>
      {cell.extra.length > 0 && <small className="pt-extra" title="Ответвление: провод идёт ещё и на этот контакт">+ {cell.extra.join(", ")}</small>}
    </div>;
  };
  return <div className="pt" aria-label="Распиновка и сростки" role="group">
    <div className="pt-head">
      <span className="pt-summary" role="status" aria-live="polite">{summary}</span>
    </div>
    <div className="pt-conns" role="group" aria-label="Разъёмы по чертежу">
      {conns.map((c) => <div className="pt-conn" key={c.id} id={`pinout-table-${c.id}`}>
        <b className="ep-badge">{c.key}</b>
        <input aria-label={`Обозначение разъёма ${c.key}`} autoComplete="off" spellCheck={false} value={c.designation} placeholder="XS1, XP1…" onChange={(event) => onLabel(c.id, event.target.value)} />
        <span className="pt-conn-art" title={c.conn}>{c.conn || "разъём не выбран"}</span>
        {connectorActions?.(c.id)}
      </div>)}
    </div>
    <div className="pt-scroll">
      <table className="pt-table">
        <thead><tr><th scope="col" className="pt-num">№</th><th scope="col">Провод</th><th scope="col" className="pt-len">Длина, мм</th><th scope="col">Конец А</th><th scope="col">Конец В</th></tr></thead>
        <tbody>
          {rows.map((row) => [
            <tr key={row.key} className={`${row.first ? "pt-first" : ""}${row.problems.length ? " has-problem" : ""}`}>
              <td className="pt-num">{row.num}</td>
              <td className="pt-wire"><div className="pt-wire-in"><Swatch color={row.color} stripe={row.stripe} /><span className="pt-wire-name" title={`${row.name}${row.colorName ? ` · ${row.colorName}` : ""}`}>{row.name || "провод"}{row.colorName && <small>{row.colorName}</small>}</span></div>
                {row.cells.A.value.startsWith("pin:") && row.cells.B.value.startsWith("pin:") && row.cells.A.value.split(":")[1] === row.cells.B.value.split(":")[1] && <small className="tone-info">Перемычка в одном разъёме</small>}
              </td>
              <td className="pt-len">{row.first
                ? <input type="number" min={0} inputMode="numeric" autoComplete="off" aria-label={`Длина провода № ${row.num}`} value={row.length || ""} placeholder="—" onChange={(event) => onLength(row.idx, Number(event.target.value) || 0)} />
                : <span className="pt-same" title="Длина общая для жил одного кабеля">″</span>}</td>
              <td>{select(row, "A")}</td>
              <td>{select(row, "B")}</td>
            </tr>,
            (row.cells.A.value === "lug" || row.cells.B.value === "lug") && <tr key={`${row.key}-lugs`}><td colSpan={5}>
              {endDetails?.(row, "A")}{endDetails?.(row, "B")}
            </td></tr>,
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

export type MaterialOption = { label: string; material: TopologyMaterial };
const materialKey = (m: TopologyMaterial) => "compId" in m ? `kit:${m.compId}` : `art:${m.art}`;

/** Both catalog materials and kit rows retain their identity in the C1 contract. */
export function TopologyMaterialSelect({ label, value, options, onChange }: {
  label: string; value: TopologyMaterial; options: MaterialOption[]; onChange: (value: TopologyMaterial) => void;
}) {
  const key = materialKey(value);
  const choices = [...new Map(options.map((o) => [materialKey(o.material), o])).values()];
  const listed = choices.some((o) => materialKey(o.material) === key);
  return <div className="gfield">
    <select aria-label={label} style={{ minHeight: 40 }} value={listed ? key : "article"}
      onChange={(event) => onChange(choices.find((o) => materialKey(o.material) === event.target.value)?.material ?? { art: "", name: "" })}>
      {choices.map((o) => <option key={materialKey(o.material)} value={materialKey(o.material)}>{o.label}</option>)}
      <option value="article">Указать артикул</option>
    </select>
    {!listed && <>
      <input style={{ minHeight: 40 }} aria-label={`${label}: артикул`} placeholder="Артикул" value={"art" in value ? value.art : ""}
        onChange={(event) => onChange({ art: event.target.value, name: "name" in value ? value.name : "" })} />
      <input style={{ minHeight: 40 }} aria-label={`${label}: название`} placeholder="Название" value={"name" in value ? value.name : ""}
        onChange={(event) => onChange({ art: "art" in value ? value.art : "", name: event.target.value })} />
    </>}
  </div>;
}

type TubeTarget = { label: string; target: TubePlacement["target"]; ends: WireEnd[] };
const targetKey = (t: TubePlacement["target"]) => t.kind === "splice" ? `splice:${t.spliceId}` : `${t.kind}:${t.end.idx}:${t.end.unit}:${t.end.side}`;
const endKey = (e: WireEnd) => `${e.idx}:${e.unit}:${e.side}`;

export function TopologyTubes({ tubes, materials, targets, endLabel, onChange, onAdd }: {
  tubes: TubePlacement[]; materials: MaterialOption[]; targets: TubeTarget[];
  endLabel: (end: WireEnd) => string; onChange: (tubes: TubePlacement[]) => void; onAdd: () => void;
}) {
  const patch = (id: string, change: Partial<TubePlacement>) => onChange(tubes.map((t) => t.id === id ? { ...t, ...change } : t));
  return <section className="gsec full" aria-label="Трубки по месту установки">
    <div className="gsec-head"><h3>Трубки по месту установки</h3>
      <button type="button" className="secondary small" style={{ minHeight: 40 }} disabled={!targets.length} onClick={onAdd}>Добавить трубку</button></div>
    {!tubes.length && <p className="pt-none">Добавьте трубку на ветвь, сросток или паяльную чашку.</p>}
    {tubes.map((tube, i) => {
      const target = targets.find((t) => targetKey(t.target) === targetKey(tube.target));
      const access = tube.preloadEnd ?? (tube.target.kind !== "splice" ? tube.target.end : undefined);
      return <div key={tube.id} className="form-grid" role="group" aria-label={`Трубка ${i + 1}`}>
        <div className="gfield"><span>Материал</span><TopologyMaterialSelect label={`Материал трубки ${i + 1}`} value={tube.material} options={materials} onChange={(material) => patch(tube.id, { material })} /></div>
        <label className="gfield"><span>Длина, мм</span><input style={{ minHeight: 40 }} aria-label={`Длина трубки ${i + 1}, мм`} type="number" min={0.01} step="any" value={tube.lengthMm || ""} onChange={(e) => patch(tube.id, { lengthMm: Number(e.target.value) })} /></label>
        <label className="gfield"><span>Количество</span><input style={{ minHeight: 40 }} aria-label={`Количество трубок ${i + 1}`} type="number" min={1} step={1} value={tube.count || ""} onChange={(e) => patch(tube.id, { count: Number(e.target.value) })} /></label>
        <label className="gfield"><span>Место установки</span><select style={{ minHeight: 40 }} aria-label={`Место трубки ${i + 1}`} value={targetKey(tube.target)} onChange={(e) => {
          const picked = targets.find((t) => targetKey(t.target) === e.target.value);
          if (picked) patch(tube.id, { target: picked.target, preloadEnd: tube.phase === "preload" ? picked.ends[0] : undefined });
        }}>
          {!target && <option value={targetKey(tube.target)}>Место удалено — выберите заново</option>}
          {targets.map((t) => <option key={targetKey(t.target)} value={targetKey(t.target)}>{t.label}</option>)}
        </select></label>
        <label className="gfield"><span>Порядок установки</span><select style={{ minHeight: 40 }} aria-label={`Порядок установки трубки ${i + 1}`} value={tube.phase} onChange={(e) => {
          const phase = e.target.value as TubePlacement["phase"];
          patch(tube.id, { phase, preloadEnd: phase === "preload" ? target?.ends[0] : undefined });
        }}><option value="preload">Надеть до соединения</option><option value="afterJoin">После соединения</option></select></label>
        {tube.phase === "preload" && <label className="gfield"><span>Конец для надевания</span><select style={{ minHeight: 40 }} aria-label={`Конец надевания трубки ${i + 1}`} value={access ? endKey(access) : ""} onChange={(e) => patch(tube.id, { preloadEnd: target?.ends.find((end) => endKey(end) === e.target.value) })}>
          {(!access || !target?.ends.some((end) => sameWireEnd(end, access))) && <option value={access ? endKey(access) : ""}>Выберите конец</option>}
          {(target?.ends || []).map((end) => <option key={endKey(end)} value={endKey(end)}>{endLabel(end)}</option>)}
        </select></label>}
        <button type="button" className="btn-link" style={{ minHeight: 40 }} aria-label={`Удалить трубку ${i + 1}`} onClick={() => onChange(tubes.filter((t) => t.id !== tube.id))}>Удалить трубку</button>
      </div>;
    })}
  </section>;
}
