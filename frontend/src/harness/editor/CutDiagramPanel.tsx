import { buildLiveCutList } from "./live-cut-list";
import { HarnessCutListTable } from "../HarnessCutListPanel";
import { routeCutReadiness } from "../manufacturing/route-cut-readiness";
import { useEffect, useMemo, useRef, useState } from "react";
import { InfoHint } from "../InfoHint";
import type { EditorCommand } from "./commands";
import type { HarnessDesignDocument, WireStripStep } from "./model";
import { buildCutDiagram, cutBlankIds, type CutDiagram, type CutDiagramEnd } from "./cut-diagram";

const mm=(n:number|null)=>n===null?"—":n.toLocaleString("ru-RU",{maximumFractionDigits:3});
function Dimension({x1,x2,y,label}:{x1:number;x2:number;y:number;label:string}) {
  return <g className="he-cut-dimension"><path d={`M${x1} ${y-5}v10m0 -5H${x2}m0 -5v10`} fill="none" stroke="currentColor"/><text x={(x1+x2)/2} y={y-7} textAnchor="middle">{label}</text></g>;
}
function EndSteps({steps,side}:{steps:readonly WireStripStep[];side:"a"|"b"}) {
  const maximum=steps.at(-1)?.cumulativeLengthMm ?? 1,maxDiameter=steps.at(-1)?.diameterMm ?? 1;
  let previous=0;
  return <g>{steps.map((s,i)=>{const from=previous/maximum*150,to=s.cumulativeLengthMm/maximum*150;previous=s.cumulativeLengthMm;const x=side==="a"?60+from:700-to,half=8+s.diameterMm/maxDiameter*14;
    return <g key={s.index}><rect x={x} y={125-half} width={Math.max(1,to-from)} height={half*2} fill={i%2?"#b4c6d1":"#e2b362"} stroke="#426477"/><Dimension x1={side==="a"?60:700-to} x2={side==="a"?60+to:700} y={175+i*25} label={`L${s.index}=${mm(s.cumulativeLengthMm)} мм`}/></g>;})}</g>;
}
export function CutDiagramSvg({diagram,a=diagram.a,b=diagram.b}:{diagram:CutDiagram;a?:CutDiagramEnd;b?:CutDiagramEnd}) {
  const sheath=diagram.kind==="cable"&&a===diagram.a;
  const total=diagram.cutLengthMm;
  const from=diagram.sheath?.fromMm,to=diagram.sheath?.toMm;
  const sheathStart=60+(from===0?0:from!==null&&from!==undefined&&total?Math.min(640,640*from/total):105);
  const sheathEnd=700-(to===0?0:to!==null&&to!==undefined&&total?Math.min(640,640*to/total):105);
  const height=210+Math.max(a.steps.length,b.steps.length)*25;
  return <svg className="he-cut-svg" viewBox={`0 0 760 ${height}`} role="img" aria-label={`Схема резки ${diagram.title}: A ${a.label}, B ${b.label}, длина ${mm(diagram.cutLengthMm)} мм`}>
    <text x="60" y="28">A · {a.label}</text><text x="700" y="28" textAnchor="end">B · {b.label}</text>
    <Dimension x1={60} x2={700} y={70} label={`Заготовка ${mm(diagram.cutLengthMm)} мм`}/>
    <path d="M60 125H700" stroke="#597789" strokeWidth="6"/>
    <rect x={a.steps.length?210:60} y="102" width={640-(a.steps.length?150:0)-(b.steps.length?150:0)} height="46" fill="#e1e9ee" stroke="#426477"/>
    {sheath?<><rect x={sheathStart} y="98" width={Math.max(0,sheathEnd-sheathStart)} height="54" fill="#8da4b2" stroke="#426477"/><Dimension x1={60} x2={sheathStart} y={177} label={`A: ${mm(diagram.sheath?.fromMm ?? null)} мм`}/><Dimension x1={sheathEnd} x2={700} y={177} label={`B: ${mm(diagram.sheath?.toMm ?? null)} мм`}/><text x="380" y="130" textAnchor="middle">Общая оболочка</text></>:<><EndSteps side="a" steps={a.steps}/><EndSteps side="b" steps={b.steps}/>{!a.steps.length&&<text x="65" y="178">Разделка A не задана</text>}{!b.steps.length&&<text x="695" y="178" textAnchor="end">Разделка B не задана</text>}</>}
    <text x="380" y={height-8} textAnchor="middle">Условное изображение · размеры в мм</text>
  </svg>;
}
function LayerTable({label,end}:{label:string;end:CutDiagramEnd}) {
  return <section><strong>{label} · {end.profileName ?? "Профиль не задан"}</strong>{end.steps.length>0&&<table className="he-cut-layers"><thead><tr><th>Слой</th><th>Ø, мм</th><th>L от торца, мм</th><th>Ступень, мм</th></tr></thead><tbody>{end.steps.map(s=><tr key={s.index}><td>{s.index}</td><td>{mm(s.diameterMm)}</td><td>{mm(s.cumulativeLengthMm)}</td><td>{mm(s.stepLengthMm)}</td></tr>)}</tbody></table>}</section>;
}
function CutField({label,value,nullable,onCommit}:{label:string;value:number|null;nullable:boolean;onCommit:(value:string)=>boolean}) {
  const [draft,setDraft]=useState(value===null?"":String(value)),[invalid,setInvalid]=useState(false);
  useEffect(()=>{setDraft(value===null?"":String(value));setInvalid(false);},[value]);
  const commit=()=>{const v=draft.trim().replace(",",".");if(v===String(value??""))return;if(v===""&&!nullable||v!==""&&!Number.isFinite(Number(v))){setInvalid(true);return;}setInvalid(!onCommit(v));};
  return <label>{label}<input type="text" inputMode="decimal" aria-label={label} aria-invalid={invalid} value={draft} onChange={e=>{setDraft(e.target.value);setInvalid(false);}} onBlur={commit} onKeyDown={e=>{if(e.key==="Enter"){e.preventDefault();commit();}}}/>{invalid&&<small role="alert">Проверьте значение и допустимую длину.</small>}</label>;
}

export function CutDiagramPanel({document,quantity,revision,unsaved,relatedIds,onReveal,onCommand,sourceFingerprint,embedded=false}:{sourceFingerprint?:string;embedded?:boolean;document:HarnessDesignDocument;quantity:number;revision:number;unsaved:boolean;relatedIds:readonly string[];onReveal:(id:string)=>void;onCommand:(c:EditorCommand)=>boolean}) {
  const [open,setOpen]=useState(embedded),[selected,setSelected]=useState("");
  const trigger=useRef<HTMLButtonElement>(null);
  const close=()=>{setOpen(false);trigger.current?.focus();};
  const cutReady=routeCutReadiness(document,sourceFingerprint,unsaved);
  const ids=[...cutBlankIds(document),...(document.physicalTopology?.coverings??[]).map(c=>c.id)];
  const live=useMemo(()=>buildLiveCutList(document,"","",quantity),[document,quantity]);
  const relatedSource=relatedIds.find(id=>document.wires.some(w=>w.id===id)||document.cables.some(c=>c.id===id));
  const related=relatedSource?buildCutDiagram(document,relatedSource,quantity)?.objectId:undefined;
  const active=ids.includes(selected)?selected:related ?? ids[0] ?? "";
  const activeCovering=document.physicalTopology?.coverings?.find(c=>c.id===active);
  const diagram=useMemo(()=>buildCutDiagram(document,active,quantity),[document,active,quantity]);
  const commit=(field:"lengthMm"|"endCorrectionFromMm"|"endCorrectionToMm"|"cutRoundingStepMm",value:string)=>{
    if(!diagram)return false;const patch={[field]:value===""&&field==="lengthMm"?null:Number(value)};
    return onCommand(diagram.kind==="cable"?{type:"update-cable",cableId:diagram.objectId,...patch}:{type:"update-wire",wireId:diagram.objectId,...patch});
  };
  if(!cutReady.ready)return <section className="he-relations"><p role="status">{cutReady.message}</p></section>;
  return <section className="he-relations">{!embedded&&<button ref={trigger} className="ui-control" type="button" disabled={!ids.length} onClick={()=>{setSelected(related ?? ids[0] ?? "");setOpen(true);}}>Схема резки / разделки</button>}
    {open&&<div className={embedded?"":"he-cut-backdrop"}><section className={embedded?"he-cut-embedded":"he-cut-dialog"} role={embedded?"region":"dialog"} aria-modal={embedded?undefined:true} aria-label="Схема резки и разделки" onKeyDown={e=>{
        if(!embedded&&e.key==="Escape"){e.preventDefault();e.stopPropagation();close();}
        if(e.key==="Delete"||e.key==="Backspace")e.stopPropagation();
        if(!embedded&&e.key==="Tab") {const controls=Array.from(e.currentTarget.querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),select:not([disabled]),summary,[tabindex="0"]')).filter(n=>n.getClientRects().length);const first=controls[0],last=controls.at(-1);if(e.shiftKey&&window.document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&window.document.activeElement===last){e.preventDefault();first?.focus();}}
      }}>
      <header className="ui-section-heading"><strong>Резка и разделка</strong><InfoHint>Заготовка = физическая длина + поправки A/B, затем округление вверх до шага. Масштаб изображения условный. Разделка обрабатывает ту же заготовку и не создаёт второй расход. Кабель учитывается целиком; профили его жил показаны ниже. Профиль без данных не считается нулевой разделкой.</InfoHint>{!embedded&&<button className="ui-control" type="button" autoFocus onClick={close}>Закрыть</button>}</header>
      <div className="he-relations-actions"><select aria-label="Заготовка" value={active} onChange={e=>setSelected(e.target.value)}>{ids.map((id,index)=><option key={id} value={id}>{document.physicalTopology?.coverings?.find(c=>c.id===id)?.name || document.wires.find(w=>w.id===id)?.circuit || (document.cables.some(c=>c.id===id)?"Кабель ":"Провод ")+(index+1)}</option>)}</select><span role="status">{unsaved?`Текущий документ · не сохранён · база r${revision}`:`Сохранённая ревизия r${revision}`}</span><button className="ui-control" type="button" disabled={!diagram&&!activeCovering} onClick={()=>{if(diagram||activeCovering)onReveal(diagram?.objectId??activeCovering!.id);if(!embedded)close();}}>На чертеже</button></div>
      {embedded&&<HarnessCutListTable cutList={live} onReveal={id=>{setSelected(id);onReveal(id);}} highlightedIds={[active]}/>}
      {diagram?<><strong>{diagram.material ?? "Материал не закреплён"}</strong><div className="he-cut-fields">{([
        ["lengthMm","Исходная длина, мм",diagram.sourceLengthMm], ["endCorrectionFromMm","Поправка A, мм",diagram.correctionA], ["endCorrectionToMm","Поправка B, мм",diagram.correctionB], ["cutRoundingStepMm","Шаг округления, мм",diagram.roundingMm],
      ] as const).map(([key,label,value])=><CutField key={`${diagram.objectId}:${key}`} label={label} value={value} nullable={key==="lengthMm"} onCommit={value=>commit(key,value)}/>)}</div>
        <p>{mm(diagram.sourceLengthMm)} + ({mm(diagram.correctionA)}) + ({mm(diagram.correctionB)}) = {mm(diagram.unroundedMm)} мм → <strong>{mm(diagram.cutLengthMm)} мм</strong> · {diagram.quantity} шт. · {diagram.totalMetres===null?"—":diagram.totalMetres.toLocaleString("ru-RU",{maximumFractionDigits:6})} м</p>
        {diagram.warnings.length>0&&<details open><summary>Проверить · {diagram.warnings.length}</summary><ul>{diagram.warnings.map(w=><li key={w}>{w}</li>)}</ul></details>}
        <CutDiagramSvg diagram={diagram}/>
        {diagram.kind==="wire"?<div className="he-cut-end-tables"><LayerTable label="A" end={diagram.a}/><LayerTable label="B" end={diagram.b}/></div>:<>{diagram.conductors.map((c,index)=><details key={c.id}><summary>Жила {index+1} · A {c.a.label} → B {c.b.label}</summary><CutDiagramSvg diagram={diagram} a={c.a} b={c.b}/><div className="he-cut-end-tables"><LayerTable label="A" end={c.a}/><LayerTable label="B" end={c.b}/></div></details>)}</>}
      </>:activeCovering?<p>Оболочка · {activeCovering.name}. Длина определяется заданными размерами в мм; разделка проводов к оболочке не применяется.</p>:<p>Заготовка удалена или отсутствует.</p>}
    </section></div>}
  </section>;
}
