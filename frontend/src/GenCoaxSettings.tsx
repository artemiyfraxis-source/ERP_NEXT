import { Field, Toggle, UnitInput } from "./GenControls";
import { AutoTextarea } from "./AutoTextarea";
import { coaxCutLength, coaxLengthProblem, type GenState } from "./tcGenLogic";
import { coaxMarkDefaults, type CoaxMark, type ConnColor } from "./tcGenMarks";
import { formatNumber } from "./format";

type Coax = GenState["coax"];
type Props = { coax: Coax; onChange: (patch: Partial<Coax>) => void };

export function CoaxLength({ coax, onChange }: Props) {
  const finished = coax.lengthBasis === "finished";
  const cut = coaxCutLength(coax), problem = coaxLengthProblem(coax);
  return <>
    <Field label="Длина по" className="span2"><select name="coax-length-basis" autoComplete="off" value={coax.lengthBasis ?? "cut"}
      onChange={(event) => onChange({ lengthBasis: event.target.value as Coax["lengthBasis"] })}>
      <option value="finished">готовому изделию (L по КД)</option><option value="cut">резке</option>
    </select></Field>
    {finished ? <>
      <Field label="L по КД, мм"><UnitInput unit="мм" name="coax-finished-length" autoComplete="off" inputMode="decimal" value={coax.finishedLength ?? ""}
        onChange={(event) => onChange({ finishedLength: event.target.value })} /></Field>
      <Field label="Запас А, мм"><UnitInput unit="мм" name="coax-allowance-a" autoComplete="off" inputMode="decimal" value={coax.lPlusA ?? ""}
        onChange={(event) => onChange({ lPlusA: event.target.value })} /></Field>
      <Field label="Запас В, мм"><UnitInput unit="мм" name="coax-allowance-b" autoComplete="off" inputMode="decimal" value={coax.lPlusB ?? ""}
        onChange={(event) => onChange({ lPlusB: event.target.value })} /></Field>
      <output className="gsec-meta span-all coax-cut-length" aria-live="polite">Длина резки = L + А + В = {cut === null ? "—" : formatNumber(cut, { digits: 3 })} мм</output>
      {problem && <div className="warning-inline span-all" role="alert">{problem}</div>}
    </> : <Field label="Длина резки, мм"><UnitInput unit="мм" aria-label="Длина коакс-кабеля" name="coax-cut-length" autoComplete="off" inputMode="decimal" value={coax.len}
      onChange={(event) => onChange({ len: event.target.value })} /></Field>}
  </>;
}

export function CoaxMarking({ coax, onChange, components, labels }: Props & { components: Array<{ type?: string; art?: string; name?: string }>; labels: boolean }) {
  const marks = coax.marks ?? [];
  const color = coax.connColor ?? { method: "none", color: "", code: "", art: "" };
  const setColor = (patch: Partial<ConnColor>) => onChange({ connColor: { ...color, ...patch } });
  const setMark = (index: number, patch: Partial<CoaxMark>) => onChange({ marks: marks.map((mark, i) => i === index ? { ...mark, ...patch } : mark) });
  const defaults = coaxMarkDefaults(components);
  const markerArt = (code: string) => components.map((item) => coaxMarkDefaults([item]).connColor)
    .find((item) => item.method === "paint" && item.code === code)?.art ?? "";
  return <section className="gsec coax-marking full" aria-label="Маркировка коакс-кабеля">
    <div className="gsec-head"><h3>Маркировка</h3></div>
    {labels && <>
    <div className="opt-grid">
      <Field label="Материал меток" className="span2"><select name="coax-mark-material" autoComplete="off" value={coax.markMaterial ?? "tube"}
        onChange={(event) => onChange({ markMaterial: event.target.value as Coax["markMaterial"] })}>
        <option value="tape">Этикетка на ленте</option><option value="tube">Термоусадочная трубка</option>
      </select></Field>
    </div>
    {coax.markMaterial === "tape" && <Toggle checked={!!coax.markProtect} title="Прозрачная ТУТ поверх этикеток" onChange={(markProtect) => onChange({ markProtect })} />}
    <div className="gtable-wrap is-full"><table className="gtable coax-marks">
      <caption>Метки коакс-кабеля</caption>
      <thead><tr><th scope="col">Надпись</th><th scope="col">Место</th><th scope="col" className="num">Не ближе, мм</th><th scope="col" aria-label="Удалить" /></tr></thead>
      <tbody>{marks.map((mark, index) => <tr key={index}>
        <td><AutoTextarea aria-label={`Надпись метки ${index + 1}`} name={`coax-mark-text-${index}`} autoComplete="off" value={mark.text} onChange={(text) => setMark(index, { text })} /></td>
        <td><select aria-label={`Место метки ${index + 1}`} name={`coax-mark-place-${index}`} autoComplete="off" value={mark.place} onChange={(event) => setMark(index, { place: event.target.value as CoaxMark["place"] })}>
          <option value="A">Сторона А</option><option value="middle">Середина</option><option value="B">Сторона В</option>
        </select></td>
        <td><input className="num" type="number" min={100} aria-label={`Не ближе, мм, метка ${index + 1}`} name={`coax-mark-distance-${index}`} autoComplete="off" inputMode="numeric"
          value={mark.minMm || ""} onChange={(event) => setMark(index, { minMm: Number(event.target.value) })} /></td>
        <td><button type="button" className="secondary small btn-x" aria-label={`Удалить метку ${index + 1}`} onClick={() => onChange({ marks: marks.filter((_, i) => i !== index) })}>×</button></td>
      </tr>)}</tbody>
    </table></div>
    {!marks.length && <div className="gempty">Нет меток — добавьте надпись и место.</div>}
    <div><button type="button" className="secondary small" onClick={() => onChange({ marks: [...marks, { text: "", place: "middle", minMm: 100 }] })}>Добавить метку</button></div>
    </>}
    <div className="opt-grid">
      <Field label="Цветовая маркировка разъёмов" className="span2"><select name="coax-color-method" autoComplete="off" value={color.method}
        onChange={(event) => {
          const method = event.target.value as ConnColor["method"];
          setColor({ method, art: method === "paint" ? markerArt(color.code) : "" });
        }}>
        <option value="none">Нет</option><option value="paint">Маркер-краска</option><option value="laser">Лазерная гравировка</option><option value="tape">Цветная лента</option>
      </select></Field>
      {color.method !== "none" && <>
        <Field label="Цвет" className="span2"><select name="coax-color-choice" autoComplete="off" value={color.code === "OG" || color.code === "PK" ? color.code : "custom"}
          onChange={(event) => {
            const code = event.target.value;
            const art = color.method === "paint" ? markerArt(code) : "";
            setColor(code === "custom" ? { color: "", code: "", art: "" } : { code, color: code === "OG" ? "оранжевый" : "розовый", art });
          }}>
          <option value="OG">Оранжевый (OG)</option><option value="PK">Розовый (PK)</option><option value="custom">Другой цвет</option>
        </select></Field>
        {color.code !== "OG" && color.code !== "PK" && <>
          <Field label="Название цвета"><input name="coax-color-name" autoComplete="off" value={color.color} onChange={(event) => setColor({ color: event.target.value })} /></Field>
          <Field label="Код цвета"><input name="coax-color-code" autoComplete="off" value={color.code} onChange={(event) => setColor({ code: event.target.value })} /></Field>
        </>}
      </>}
    </div>
    {defaults.conflict && color.method !== "none" && !color.color && <div className="warning-inline" role="alert">В составе несколько цветов маркера-краски — выберите цвет.</div>}
  </section>;
}
