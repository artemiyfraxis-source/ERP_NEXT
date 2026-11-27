import { useState } from "react";
import { Field, Toggle, UnitInput } from "./GenControls";
import { AutoTextarea } from "./AutoTextarea";
import { coaxCutLength, coaxLengthProblem, coaxLengthConfirmation, type GenState } from "./tcGenLogic";
import { COAX_COLORS, COAX_COVER_EXTRA_MM, COAX_SERIAL_TUBE_MM, coaxCoverLengths, coaxMarkDefaults, type CoaxLabelProfile, type CoaxMark, type ConnColor } from "./tcGenMarks";
import { parseDecimal_ } from "./tcGenLogic";
import { DataTable, type DataColumn } from "./DataTable";
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
        onChange={(event) => onChange({ finishedLength: event.target.value, finishedLengthFrom: "user" })} /></Field>
      {coaxLengthConfirmation(coax) && <button type="button" className="secondary small" style={{ minHeight: 40 }}
        onClick={() => onChange({ finishedLengthFrom: "user" })}>Подтвердить L</button>}
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

/** «Заводской номер на изделии»: `kb` — «КБ<n>» карты, `onSerial` — включить/выключить или сменить обозначение изделия
 *  (coaxSerialPatch), `profiles` — профили этикеток ОБ.102 (null — загружаются), `fakra` — FAKRA/HFM со своими метками. */
export type CoaxSerialProps = {
  kb: string; fakra: boolean; profiles: CoaxLabelProfile[] | null; profilesError?: string;
  onSerial: (on: boolean, serialProduct?: string) => void;
};

export function CoaxMarking({ coax, onChange, components, labels, serial }: Props & { components: Array<{ type?: string; art?: string; name?: string }>; labels: boolean; serial: CoaxSerialProps }) {
  const marks = coax.marks ?? [];
  const serialOn = !!coax.serialMark;
  const tape = coax.markMaterial === "tape";
  // Обычный коакс печатает метки только на ленте или — с номером — на маркировочной трубке.
  const printed = tape || (serialOn && !serial.fakra);
  const custom = coax.serialProduct !== undefined && coax.serialProduct.trim() !== "";
  const [ownChoice, setOwnProduct] = useState<boolean | null>(null);
  const ownProduct = ownChoice ?? custom;
  const printedMm = tape ? coax.labelProfile?.lengthMm ?? 0 : parseDecimal_(coax.markTubeLen) || COAX_SERIAL_TUBE_MM;
  const cover = coaxCoverLengths(printedMm > 0 ? printedMm : null);
  const profiles = serial.profiles ?? [];
  const known = !coax.labelProfile || profiles.some((item) => item.id === coax.labelProfile!.id);
  const color = coax.connColor ?? { method: "none", color: "", code: "", art: "" };
  const setColor = (patch: Partial<ConnColor>) => onChange({ connColor: { ...color, ...patch } });
  const setMark = (index: number, patch: Partial<CoaxMark>) => onChange({ marks: marks.map((mark, i) => i === index ? { ...mark, ...patch } : mark) });
  const defaults = coaxMarkDefaults(components);
  const knownColor = COAX_COLORS.some((item) => item.code === color.code);
  const markerArt = (code: string) => components.map((item) => coaxMarkDefaults([item]).connColor)
    .find((item) => item.method === "paint" && item.code === code)?.art ?? "";
  return <section className="gsec coax-marking full" aria-label="Маркировка коакс-кабеля">
    <div className="gsec-head"><h3>Маркировка</h3></div>
    <Toggle checked={serialOn} title="Заводской номер на изделии" hint="FRX.<проект>.<изделие>.<№> в середине, номер — при печати"
      onChange={(on) => serial.onSerial(on)} />
    {serialOn && <div className="opt-grid coax-serial">
      <Field label="Изделие в номере" className="span2"><select name="coax-serial-product-kind" autoComplete="off" value={ownProduct ? "custom" : "kb"}
        onChange={(event) => {
          const own = event.target.value === "custom";
          setOwnProduct(own);
          if (!own) serial.onSerial(true, "");
        }}>
        <option value="kb">{serial.kb}</option><option value="custom">Своё обозначение</option>
      </select></Field>
      {ownProduct && <Field label="Обозначение" className="span2"><input name="coax-serial-product" autoComplete="off" placeholder="34.226 или 120907/AA"
        value={coax.serialProduct ?? ""} onChange={(event) => serial.onSerial(true, event.target.value)} /></Field>}
      <Field label="Носитель" className="span2"><select name="coax-mark-material" autoComplete="off" value={coax.markMaterial ?? "tube"}
        onChange={(event) => onChange({ markMaterial: event.target.value as Coax["markMaterial"] })}>
        <option value="tape">Этикетка на ленте</option><option value="tube">Термоусадочная трубка</option>
      </select></Field>
      <div className="span-all"><Toggle checked={!!marks.find((mark) => mark.serial)?.qr} title="QR-код с номером"
        onChange={(qr) => onChange({ marks: marks.map((mark) => mark.serial ? { ...mark, qr } : mark) })} /></div>
    </div>}
    {labels && <>
    {!serialOn && <div className="opt-grid">
      <Field label="Материал меток" className="span2"><select name="coax-mark-material" autoComplete="off" value={coax.markMaterial ?? "tube"}
        onChange={(event) => onChange({ markMaterial: event.target.value as Coax["markMaterial"] })}>
        <option value="tape">Этикетка на ленте</option><option value="tube">Термоусадочная трубка</option>
      </select></Field>
    </div>}
    {tape && <div className="opt-grid">
      <Field label="Профиль этикетки" className="span2"><select name="coax-label-profile" autoComplete="off" value={coax.labelProfile?.id ?? ""}
        disabled={serial.profiles === null}
        onChange={(event) => onChange({ labelProfile: profiles.find((item) => item.id === event.target.value) })}>
        <option value="">{serial.profiles === null ? "Загрузка…" : "Не выбран"}</option>
        {!known && coax.labelProfile && <option value={coax.labelProfile.id}>{coax.labelProfile.name}</option>}
        {profiles.map((item) => <option key={item.id} value={item.id}>{item.name}, длина {item.lengthMm} мм</option>)}
      </select></Field>
      <small className="gsec-meta span2">Лента Vell VL-S641, 18 мм · ZCUT-RT660 (ОБ.102)</small>
      {serial.profilesError && <div className="warning-inline span-all" role="alert">{serial.profilesError}</div>}
    </div>}
    {!tape && serialOn && !serial.fakra && <div className="opt-grid">
      <Field label="Длина трубки, мм"><UnitInput unit="мм" name="coax-mark-tube-length" autoComplete="off" inputMode="decimal" placeholder={String(COAX_SERIAL_TUBE_MM)}
        value={coax.markTubeLen ?? ""} onChange={(event) => onChange({ markTubeLen: event.target.value })} /></Field>
      <small className="gsec-meta span2">Печать трубки — Supvan TP80E (ОБ.057)</small>
    </div>}
    {printed && <Toggle checked={!!coax.markProtect} title={tape ? "Прозрачная ТУТ поверх этикеток" : "Прозрачная ТУТ поверх трубки"} onChange={(markProtect) => onChange({ markProtect })}>
      <small className="gsec-meta coax-cover-length">{printedMm > 0
        ? `Длина прозрачной ТУТ ${cover.middle} мм = ${tape ? "этикетка" : "трубка"} ${printedMm} + ${COAX_COVER_EXTRA_MM}`
        : `Профиль не выбран: концевые ${cover.A} мм, центральная ${cover.middle} мм`}</small>
    </Toggle>}
    <div className="gtable-wrap is-full"><table className="gtable coax-marks">
      <caption>Метки коакс-кабеля</caption>
      <thead><tr><th scope="col">Надпись</th><th scope="col">Место</th><th scope="col" className="num">Не ближе, мм</th><th scope="col" aria-label="Удалить" /></tr></thead>
      <tbody>{marks.map((mark, index) => mark.serial ? <tr key={index} className="coax-mark-serial">
        <td><b>{mark.text}</b><small>номер — при печати{mark.qr ? ", QR-код" : ""}</small></td>
        <td>Середина</td>
        <td><input className="num" type="number" min={100} aria-label={`Не ближе, мм, метка ${index + 1}`} name={`coax-mark-distance-${index}`} autoComplete="off" inputMode="numeric"
          value={mark.minMm || ""} onChange={(event) => setMark(index, { minMm: Number(event.target.value) })} /></td>
        <td />
      </tr> : <tr key={index}>
        <td><AutoTextarea aria-label={`Надпись метки ${index + 1}`} name={`coax-mark-text-${index}`} autoComplete="off" value={mark.text} onChange={(text) => setMark(index, { text })} /></td>
        <td><select aria-label={`Место метки ${index + 1}`} name={`coax-mark-place-${index}`} autoComplete="off" value={mark.place} onChange={(event) => setMark(index, { place: event.target.value as CoaxMark["place"] })}>
          <option value="A">Сторона А</option><option value="middle">Середина</option><option value="B">Сторона В</option>
        </select></td>
        <td><input className="num" type="number" min={100} aria-label={`Не ближе, мм, метка ${index + 1}`} name={`coax-mark-distance-${index}`} autoComplete="off" inputMode="numeric"
          value={mark.minMm || ""} onChange={(event) => setMark(index, { minMm: Number(event.target.value) })} /></td>
        <td><button type="button" className="secondary small btn-x" aria-label={`Удалить метку ${index + 1}`} onClick={() => onChange({ marks: marks.filter((_, i) => i !== index) })}>×</button></td>
      </tr>)}</tbody>
    </table></div>
    <small className="gsec-meta">{"{№} — номер сборки 1…N"}</small>
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
        <Field label="Цвет" className="span2"><select name="coax-color-choice" autoComplete="off" value={knownColor ? color.code : "custom"}
          onChange={(event) => {
            const code = event.target.value;
            const art = color.method === "paint" ? markerArt(code) : "";
            setColor(code === "custom" ? { color: "", code: "", art: "" } : { code, color: COAX_COLORS.find((item) => item.code === code)!.color, art });
          }}>
          {COAX_COLORS.map(({ code, color: name }) => <option key={code} value={code}>{name[0].toUpperCase() + name.slice(1)} ({code})</option>)}
          <option value="custom">Другой</option>
        </select></Field>
        {!knownColor && <>
          <Field label="Название цвета"><input name="coax-color-name" autoComplete="off" value={color.color} onChange={(event) => setColor({ color: event.target.value })} /></Field>
          <Field label="Код цвета"><input name="coax-color-code" autoComplete="off" value={color.code} onChange={(event) => setColor({ code: event.target.value })} /></Field>
        </>}
      </>}
    </div>
    {defaults.conflict && color.method !== "none" && !color.color && <div className="warning-inline" role="alert">В составе несколько цветов маркера-краски — выберите цвет.</div>}
  </section>;
}

const RESEED_LABELS: Partial<Record<keyof Coax, string>> = {
  fakraCutStrip: "Резка с разделкой", lengthBasis: "Длина по", finishedLength: "L по КД, мм",
  finishedLengthFrom: "Источник длины", lPlusA: "Запас А, мм", lPlusB: "Запас В, мм",
  markMaterial: "Материал меток", markProtect: "Прозрачная ТУТ поверх этикеток", marks: "Метки",
  tutLen: "Длина отрезка ТУТ, мм", connColor: "Цветовая маркировка разъёмов",
};
const RESEED_VALUES: Record<string, string> = { finished: "готовому изделию", cut: "резке", name: "наименование сборки", calc: "расчёт — подтвердите L",
  bom: "состав — подтвердите L", user: "подтверждено технологом", tube: "термоусадочная трубка", tape: "этикетка на ленте" };
function reseedText(key: keyof Coax, value: unknown): string {
  if (value === undefined || value === null || value === "") return "—";
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  if (key === "marks") return (value as CoaxMark[]).map((mark) => `${mark.place === "middle" ? "Середина" : mark.place === "A" ? "А" : "В"}: ${mark.text} (≥ ${mark.minMm} мм)`).join("; ") || "—";
  if (key === "connColor") {
    const color = value as ConnColor;
    return color.method === "none" ? "Нет" : `${{ paint: "Маркер-краска", laser: "Лазерная гравировка", tape: "Цветная лента" }[color.method]}: ${color.color || "выберите цвет"}${color.art ? `, ${color.art}` : ""}`;
  }
  return RESEED_VALUES[String(value)] || String(value);
}
type ReseedRow = { key: string; field: string; before: string; after: string };
const RESEED_COLUMNS: DataColumn<ReseedRow>[] = [
  { key: "field", title: "Поле", value: (row) => row.field },
  { key: "before", title: "Сейчас", value: (row) => row.before, wrap: true, grow: true },
  { key: "after", title: "После заполнения", value: (row) => row.after, wrap: true, grow: true },
];

export function CoaxReseed({ coax, onChange, propose }: Props & { propose: () => Partial<Coax> }) {
  const [patch, setPatch] = useState<Partial<Coax> | null>(null);
  const rows: ReseedRow[] = patch ? (Object.keys(patch) as Array<keyof Coax>)
    .filter((key) => JSON.stringify(coax[key]) !== JSON.stringify(patch[key]))
    .map((key) => ({ key, field: RESEED_LABELS[key] || "", before: reseedText(key, coax[key]), after: reseedText(key, patch[key]) })) : [];
  return <div className="coax-reseed" role="region" aria-label="Заполнение по составу и КД">
    <div className="gsec-head"><button type="button" className="secondary" onClick={() => setPatch(propose())}>Заполнить по составу и КД</button></div>
    {patch && <>
      {rows.length ? <DataTable columns={RESEED_COLUMNS} rows={rows} rowKey={(row) => row.key} /> : <p>Изменений нет.</p>}
      <div className="actions">
        <button type="button" className="primary" disabled={!rows.length} onClick={() => { onChange(patch); setPatch(null); }}>Применить заполнение</button>
        <button type="button" className="secondary" onClick={() => setPatch(null)}>Отмена</button>
      </div>
    </>}
  </div>;
}
