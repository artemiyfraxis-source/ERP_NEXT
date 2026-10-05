import { useState } from "react";
import { InfoHint } from "../InfoHint";
import { DraftNumberInput } from "../component-library/DraftNumberInput";
import { emptyDrawingDocuments, type DrawingDocuments, type DrawingSpecificationItem } from "./drawing-documents";
export function SpecificationItemsPanel({documents,selectedId,onChange,onSelect}:{documents?:DrawingDocuments;selectedId:string|null;onChange:(d:DrawingDocuments)=>boolean;onSelect:(id:string)=>void}) {
 const d=documents??emptyDrawingDocuments(),items=d.specificationItems??[],selected=items.find(i=>i.id===selectedId);
 const [type,setType]=useState("Материал");
 const update=(patch:Partial<DrawingSpecificationItem>)=>selected&&onChange({...d,specificationItems:items.map(i=>i.id===selected.id?{...i,...patch}:i)});
 const add=(draw:boolean)=>{const id=crypto.randomUUID();if(onChange({...d,specificationItems:[...items,{id,kind:draw?"abstract":"manual",type:type.trim()||"Материал",name:type.trim()||"Материал",designation:"",amount:1,unit:"шт.",note:"",...(draw?{position:{x:250,y:200}}:{})}]}))onSelect(id);};
 return <section className="he-relations" aria-label="Позиции спецификации"><details open={!!selected}><summary>Позиции спецификации · {items.length}</summary><header className="ui-section-heading"><InfoHint>Добавьте объект по типу на чертёж или материал только в спецификацию, например клей. Выберите позицию, затем дважды щёлкните материал в каталоге для закрепления записи базы. Обозначение и наименование можно задать вручную. Количество задаётся на один жгут; скрытие рисунка не удаляет расход.</InfoHint></header>
 <div className="he-physical-fields"><input aria-label="Тип новой позиции" value={type} onChange={e=>setType(e.target.value)}/><button type="button" className="ui-control" onClick={()=>add(true)}>На чертёж +</button><button type="button" className="ui-control" onClick={()=>add(false)}>Материал +</button></div>
 <select aria-label="Позиция спецификации" value={selected?.id??""} onChange={e=>onSelect(e.target.value)}><option value="">Выберите позицию</option>{items.map(i=><option value={i.id} key={i.id}>{i.designation||i.name}</option>)}</select>
 {selected&&<div className="he-physical-fields">{([ ["type","Тип"],["designation","Обозначение / артикул"],["name","Наименование"],["note","Примечание"] ] as const).map(([key,label])=><label key={key}>{label}<input aria-label={label+" позиции"} value={selected[key]} onChange={e=>update({[key]:e.target.value})}/></label>)}
 <label>Количество<DraftNumberInput aria-label="Количество позиции" min={0} max={1e9} step="any" value={selected.amount??""} onEmpty={()=>update({amount:null})} onValueChange={amount=>update({amount})}/></label>
 <select aria-label="Единица позиции" value={selected.unit} onChange={e=>update({unit:e.target.value as DrawingSpecificationItem["unit"]})}>{["шт.","м","г","кг","л"].map(u=><option key={u}>{u}</option>)}</select>
 <label><input type="checkbox" checked={!!selected.position} onChange={e=>update({position:e.target.checked?{x:250,y:200}:undefined})}/>На чертеже</label>
 <small>{selected.sourceIdentity?"Запись базы закреплена":"Материал не привязан к базе"}</small>
 <button type="button" className="ui-control" onClick={()=>onChange({...d,specificationItems:items.filter(i=>i.id!==selected.id)})}>Удалить позицию</button></div>}
 </details></section>;
}
