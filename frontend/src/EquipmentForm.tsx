import { useEffect, useMemo, useRef, useState } from "react";
import { referencesApi, type TechOperationRecord } from "./api";
import { headerLabel, valueLabel } from "./referenceLabels";
import {
  APPLICATOR_TYPE, CODE_KEY, CRIMP_TOOL_TYPE, CRIMP_TYPES, INV_KEY, MATRIX_TYPE, TARGETS_KEY, TOOL_KEY, invOf, makerOf, nameOf, placeOf, typeOf,
} from "./equipmentModel";

/** «Новая запись» / «Правка» in БД.ОБ (owner 12.10.2026: «очень сложно… чтобы быстро»): the six things a person knows
    about a machine come first — the type, name, article, maker, place and the inventory number the server proposes;
    the rest (cost, power, link…) waits under «Остальные поля». */

const FIRST = ["Тип", "Наменование", "Артикул", "Производитель", "Расположение", INV_KEY];
// Computed by the server or by the sheet's formulas — not typed.
const HIDDEN = new Set(["№", "Для базы", "baseValue", "obType", "Полное наименование ", "Полное наименование"]);
const FORMULA_LIKE = new Set(["Стоимость 1 сек. эксплуатации без НДС"]);

export function EquipmentForm({ values, isNew, records, onChange }: {
  values: Record<string, string>;
  isNew: boolean;
  records: TechOperationRecord[];
  onChange: (values: Record<string, string>) => void;
}) {
  const types = useMemo(() => {
    const found = new Set<string>(["Оборудование", "Инструмент", MATRIX_TYPE, CRIMP_TOOL_TYPE, APPLICATOR_TYPE]);
    records.forEach((record) => { const type = typeOf(record); if (type !== "Без типа") found.add(type); });
    if (values["Тип"]) found.add(values["Тип"]);
    return [...found];
  }, [records, values]);
  const places = useMemo(() => [...new Set([...records.map(placeOf), values["Расположение"] ?? ""].filter(Boolean).map((place) => valueLabel("Расположение", place)))], [records, values]);
  const makers = useMemo(() => [...new Set(records.map(makerOf).filter(Boolean))].sort((a, b) => a.localeCompare(b, "ru")), [records]);
  const tools = useMemo(() => records.filter((record) => ["Инструмент", CRIMP_TOOL_TYPE].includes(typeOf(record)) && invOf(record)), [records]);
  const touchedInv = useRef(!isNew);
  const [proposal, setProposal] = useState("");
  const type = values["Тип"] ?? "";

  // The proposed inventory number follows the chosen type until the person types their own.
  useEffect(() => {
    if (!isNew || !type) return;
    let live = true;
    referencesApi.equipmentNumber(type).then((result) => {
      if (!live) return;
      setProposal(result.inventory);
      if (!touchedInv.current) onChange({ ...values, [INV_KEY]: result.inventory });
    }).catch(() => undefined);
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isNew, type]);

  const set = (key: string, value: string) => onChange({ ...values, [key]: value });
  const rest = Object.keys(values).filter((key) => !FIRST.includes(key) && !HIDDEN.has(key) && !FORMULA_LIKE.has(key)
    && key !== CODE_KEY && key !== TOOL_KEY && key !== TARGETS_KEY);
  const crimpType = CRIMP_TYPES.includes(type);

  return <div className="eq-form">
    <div className="form-grid">
      <label><small>Тип</small>
        <select aria-label="Тип" value={type} onChange={(event) => set("Тип", event.target.value)}>
          {!type && <option value="">— выберите —</option>}
          {types.map((item) => <option key={item} value={item}>{item}</option>)}
        </select></label>
      <label className="half"><small>Наименование</small>
        <input aria-label="Наименование" value={values["Наменование"] ?? ""} autoFocus onChange={(event) => set("Наменование", event.target.value)} /></label>
      <label><small>Артикул</small><input aria-label="Артикул" value={values["Артикул"] ?? ""} onChange={(event) => set("Артикул", event.target.value)} /></label>
      <label><small>Производитель</small>
        <input aria-label="Производитель" list="eq-makers" value={values["Производитель"] ?? ""} onChange={(event) => set("Производитель", event.target.value)} />
        <datalist id="eq-makers">{makers.map((maker) => <option key={maker} value={maker} />)}</datalist></label>
      <label><small>Расположение</small>
        <select aria-label="Расположение" value={values["Расположение"] ? valueLabel("Расположение", values["Расположение"]) : ""} onChange={(event) => set("Расположение", event.target.value)}>
          <option value="">— не указано —</option>
          {places.map((place) => <option key={place} value={place}>{place}</option>)}
        </select></label>
      <label><small>Инв. номер{isNew && proposal ? " (подставлен сам)" : ""}</small>
        <input aria-label="Инв. номер" className="tnum" value={values[INV_KEY] ?? ""} placeholder={proposal || "назначится сам"}
          onChange={(event) => { touchedInv.current = true; set(INV_KEY, event.target.value); }} /></label>
      {crimpType && <label><small>Код в базе терминалов</small>
        <input aria-label="Код в базе терминалов" className="tnum" placeholder={type === MATRIX_TYPE ? "M31" : type === APPLICATOR_TYPE ? "A8" : "S10"} value={values[CODE_KEY] ?? ""} onChange={(event) => set(CODE_KEY, event.target.value)} /></label>}
      {type === MATRIX_TYPE && <label><small>Для инструмента</small>
        <select aria-label="Для инструмента" value={values[TOOL_KEY] ?? ""} onChange={(event) => set(TOOL_KEY, event.target.value)}>
          <option value="">— не указан —</option>
          {tools.map((tool) => <option key={tool.id} value={invOf(tool)}>{invOf(tool)} · {nameOf(tool)}</option>)}
        </select></label>}
    </div>
    {rest.length > 0 && <details className="eq-rest">
      <summary>Остальные поля <span className="muted">({rest.length})</span></summary>
      <div className="form-grid">{rest.map((key) => <label key={key}><small>{headerLabel(key)}</small>
        <input aria-label={`Поле ${key}`} value={values[key] ?? ""} onChange={(event) => set(key, event.target.value)} /></label>)}</div>
    </details>}
  </div>;
}
