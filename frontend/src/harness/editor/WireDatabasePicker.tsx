import { useState } from "react";
import type { WireDatabaseOption } from "./wire-database";
import { normalizeWireChoice, wireMarkChoices } from "./wire-database";

export function WireDatabasePicker({options,currentMark="",currentRecordId,disabled=false,onSelect}: {
  options:readonly WireDatabaseOption[];currentMark?:string;currentRecordId?:string;disabled?:boolean;
  onSelect:(option:WireDatabaseOption)=>void;
}) {
  const [mark,setMark]=useState(currentMark||options.find(option=>option.id===currentRecordId)?.mark||"");
  const chosen=options.filter(option=>normalizeWireChoice(option.mark)===normalizeWireChoice(mark)&&option.materialBinding);
  return <section className="he-context-fields" aria-label="Материал провода из базы">
    <strong>Материал из базы проводов</strong>
    <label>Марка<select aria-label="Марка материала провода" value={mark} disabled={disabled} onChange={event=>setMark(event.target.value)}><option value="">Выберите марку</option>{wireMarkChoices(options,"").map(value=><option key={value} value={value}>{value}</option>)}</select></label>
    <label>Сечение и позиция<select aria-label="Позиция материала провода" value={chosen.some(option=>option.id===currentRecordId)?currentRecordId:""} disabled={disabled||!mark} onChange={event=>{
      const option=chosen.find(item=>item.id===event.target.value);
      if(option)onSelect(option);
    }}><option value="">Выберите позицию</option>{chosen.map(option=><option key={option.id} value={option.id}>{option.section}{option.color?` · ${option.color}`:""}{option.detail?` · ${option.detail}`:""}</option>)}</select></label>
    {!options.length&&<small>База проводов недоступна</small>}
  </section>;
}
