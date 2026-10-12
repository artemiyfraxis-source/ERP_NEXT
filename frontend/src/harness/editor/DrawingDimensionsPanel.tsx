import { useEffect, useRef, useState } from "react";
import type { HarnessDesignDocument } from "./model";
import type { DrawingDocuments } from "./drawing-documents";
import { InfoHint } from "../InfoHint";
import { setPipeIntervalLength, type DimensionMode } from "./drawing-dimensions";

/** Contextual properties only: dimensions are authored by clicking a pipe. */
export function DrawingDimensionsPanel({document,selectedId,pipeInterval,onChange}:{
  document:HarnessDesignDocument; selectedId:string|null;
  pipeInterval?:{id:string;from:number;to:number}|null;
  onChange:(documents:DrawingDocuments)=>boolean;
}) {
  const docs=document.drawingDocuments,dimension=docs?.dimensions?.find(d=>d.id===selectedId);
  const segment=document.physicalTopology?.segments.find(s=>s.id===selectedId);
  void pipeInterval;
  const from=0;
  const to=(segment?.path.points.length??0)+1;
  const selected=dimension??docs?.dimensions?.find(d=>d.segmentId===segment?.id&&d.from===from&&d.to===to);
  const dirty=useRef(false);
  const [value,setValue]=useState(""),[invalid,setInvalid]=useState(false);
  useEffect(()=>{setValue(selected?.lengthMm==null?"":String(selected.lengthMm));setInvalid(false);dirty.current=false;},[selectedId,from,to,selected?.lengthMm]);
  const edit=(patch:Partial<NonNullable<typeof selected>>)=>!!docs&&!!selected&&onChange({...docs,dimensions:docs.dimensions?.map(d=>d.id===selected.id?{...d,...patch}:d)});
  const commit=()=>{
    if(!dirty.current)return;
    const number=value.trim()===""?null:Number(value.replace(",","."));
    if(number!==null&&(!Number.isFinite(number)||number<0||number>1e7||Math.abs(number*1000-Math.round(number*1000))>1e-5)){setInvalid(true);return;}
    const accepted=segment?onChange(setPipeIntervalLength(document,segment.id,from,to,number)):edit({lengthMm:number});
    setInvalid(!accepted);if(accepted)dirty.current=false;
  };
  if(!segment&&!dimension)return null;
  return <section className="he-relations" aria-label="Длина выбранного участка">
    <strong>{segment?`Пайп · точки ${from+1}–${to+1}`:dimension?.auxiliary?"Вспомогательный размер":"Размер"}</strong>
    <label>Длина, мм<input key={`${selectedId}:${from}:${to}`} aria-label="Размер, мм" inputMode="decimal" value={value} aria-invalid={invalid} onChange={e=>{dirty.current=true;setValue(e.target.value);setInvalid(false);}} onBlur={commit} onKeyDown={e=>{if(e.key==="Enter"){e.preventDefault();commit();}}}/></label>
    {invalid&&<small role="alert">Введите длину от 0 до 10 000 000 мм с точностью до 0,001 мм.</small>}
    {selected&&<label>Отображение<select aria-label="Направление размера" value={selected.modeOverride===false?"global":selected.mode} onChange={e=>edit(e.target.value==="global"?{modeOverride:false}:{mode:e.target.value as DimensionMode,modeOverride:true})}>
      <option value="global">По общей настройке</option><option value="horizontal">Горизонтально</option><option value="vertical">Вертикально</option><option value="path">Вдоль пайпа</option><option value="aligned">Вдоль между точками</option>
    </select></label>}
    <InfoHint>Размеры включаются общей кнопкой. Перетащите размерную линию для выноса; соседние линии притягиваются друг к другу. Вспомогательные размеры не влияют на длины проводов.</InfoHint>
    {selected&&<button type="button" className="ui-control" onClick={()=>docs&&onChange({...docs,dimensions:docs.dimensions?.filter(d=>d.id!==selected.id)})}>Удалить размер</button>}
  </section>;
}
