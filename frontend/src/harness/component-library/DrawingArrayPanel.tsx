import { DraftNumberInput } from "./DraftNumberInput";
import { useState } from "react";
import { InfoHint } from "../InfoHint";
import type { TemplateContentV3 } from "./template-model-v3";
import { setDrawingArray, type DrawingArrayInput } from "./drawing-array-commands";

export function DrawingArrayPanel({ content, viewId, layerId, selectedIds, onChange, onError }: {
  content:TemplateContentV3; viewId:string; layerId:string; selectedIds:readonly string[];
  onChange:(content:TemplateContentV3)=>void; onError:(message:string)=>void;
}) {
  const placements=content.views.find(v=>v.id===viewId)!.repeatPlacements.filter(p=>p.arrayLayout);
  const [domainId,setDomainId]=useState("");
  const [input,setInput]=useState<DrawingArrayInput>({rows:1,direction:"long-side",numbering:"new-row",countSource:"article",count:2,pitchX:40,pitchY:40});
  function choose(id:string) {
    setDomainId(id);
    const p=placements.find(p=>p.repeatDomainId===id);
    if (!p?.arrayLayout) return;
    const domain=content.repeaters.find(d=>d.id===id)!;
    setInput({...p.arrayLayout,numbering:p.arrayLayout.numbering === "snake" ? "snake" : "new-row",count:Number(content.parameters.find(p=>p.id===domain.countParameterId)!.defaultValue),pitchX:p.step.x.kind==="constant"?p.step.x.value:40,pitchY:p.step.y.kind==="constant"?p.step.y.value:40});
  }
  return <div className="array-layout-toolbar" aria-label="Массив рисунка">
    <label>Массив<select value={placements.some(p=>p.repeatDomainId===domainId)?domainId:""} onChange={e=>choose(e.target.value)}><option value="">Из выделения</option>{placements.map((p,i)=><option key={p.repeatDomainId} value={p.repeatDomainId}>Массив {i+1}</option>)}</select></label>
    <label>Рядов<select value={input.rows} onChange={e=>setInput({...input,rows:Number(e.target.value)})}>{[1,2,3,4].map(n=><option key={n}>{n}</option>)}</select></label>
    <label>Нумерация<select value={input.direction} onChange={e=>setInput({...input,direction:e.target.value as DrawingArrayInput["direction"]})}><option value="long-side">Вдоль рядов</option><option value="short-side">Поперёк рядов</option></select></label>
    <label>Переход<select value={input.numbering} onChange={e=>setInput({...input,numbering:e.target.value as DrawingArrayInput["numbering"]})}><option value="new-row">С начала ряда</option><option value="snake">Змейка</option></select></label>
    <label>Количество<select value={input.countSource} onChange={e=>setInput({...input,countSource:e.target.value as DrawingArrayInput["countSource"]})}><option value="article">По артикулу</option><option value="parameter">Вручную</option></select></label>
    <label>{input.countSource==="article"?"Без артикула":"Элементов"}<DraftNumberInput aria-label="Элементов массива" min={1} max={1000} value={input.count} onValueChange={count=>setInput({...input,count})}/></label>
    {(["pitchX","pitchY"] as const).map(key=><label key={key}>Шаг {key==="pitchX"?"X":"Y"}<DraftNumberInput aria-label={`Шаг массива ${key==="pitchX"?"X":"Y"}`} min={0.1} max={10000} step={0.1} value={input[key]} onValueChange={value=>setInput({...input,[key]:value})}/></label>)}
    <button type="button" onClick={()=>{try{const next=setDrawingArray(content,viewId,layerId,selectedIds,input,domainId||undefined);onChange(next);setDomainId(next.views.find(v=>v.id===viewId)!.repeatPlacements.at(-1)!.repeatDomainId);}catch(error){onError((error as Error).message);}}}>Применить</button>
    <InfoHint>Выделите фигуры и точки контактов одного элемента (Ctrl — несколько), затем примените массив. Количество повторов задаётся артикулом или полем «Элементов»; изменение геометрического масштаба не меняет число контактов. Точки последовательно связываются со строками таблицы, лишние точки неполного элемента скрываются. Добавьте текст {"{{n}}"} в прототип для номеров 1…N. Изменения прототипа распространяются на все артикулы. X идёт вдоль рядов, Y — между рядами. Для комбинированного корпуса размещайте группы вручную. Неравное число элементов образует неполный последний ряд; сверяйте вид и контакт 1 с чертежом корпуса.</InfoHint>
  </div>;
}
