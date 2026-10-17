import {useEffect,useState} from "react";
import {validDrawingScale} from "./drawing-scale";
import {REAL_SIZE_SCALE_MAX,REAL_SIZE_SCALE_MIN,validRealSizeScale,type DrawingRealSize} from "./drawing-documents";
export function DrawingScaleControl({value,onChange,disabled,label}:{value:number;onChange:(value:number)=>void;disabled:boolean;label:string}){
 const [text,setText]=useState(String(Math.round(value*100)));useEffect(()=>setText(String(Math.round(value*100))),[value]);
 const save=()=>{const next=Number(text)/100;if(validDrawingScale(next))onChange(next);else setText(String(Math.round(value*100)));};
 return <span className="drawing-scale-control"><input aria-label={label} type="number" min="5" max="2000" step="5" value={text} disabled={disabled} onChange={e=>setText(e.target.value)} onBlur={save} onKeyDown={e=>{if(e.key==="Enter"){e.preventDefault();e.currentTarget.blur();}}}/><span>%</span></span>;
}

const decimal=(value:number,digits=2)=>String(Math.round(value*10**digits)/10**digits).replace(".",",");

/** «Вид» → «Реальные размеры, мм» with the drafting scale М 1:N. */
export function RealSizeControl({value,onChange,disabled}:{value:DrawingRealSize|undefined;onChange:(value:DrawingRealSize)=>void;disabled:boolean}){
 const denominator=value?.scaleDenominator??1,enabled=value?.enabled===true;
 const [text,setText]=useState(decimal(denominator));useEffect(()=>setText(decimal(denominator)),[denominator]);
 const save=()=>{const next=Number(text.trim().replace(",","."));if(validRealSizeScale(next)){if(next!==denominator)onChange({enabled,scaleDenominator:next});}else setText(decimal(denominator));};
 return <div className="he-toolbar-group he-real-size" aria-label="Реальные размеры">
  <label className="he-real-size-toggle" title="Провода, пайпы и соединители — по диаметрам и размерам из каталога; длины остаются по размерам чертежа"><input type="checkbox" checked={enabled} disabled={disabled} onChange={e=>onChange({enabled:e.target.checked,scaleDenominator:denominator})}/><span>Реальные размеры, мм</span></label>
  <label className="he-real-size-scale" title={`Масштаб от 1:${decimal(REAL_SIZE_SCALE_MIN)} до 1:${REAL_SIZE_SCALE_MAX}`}><span>М 1:</span><input aria-label="Масштаб реальных размеров, М 1:N" inputMode="decimal" value={text} disabled={disabled||!enabled} onChange={e=>setText(e.target.value)} onBlur={save} onKeyDown={e=>{if(e.key==="Enter"){e.preventDefault();e.currentTarget.blur();}}}/></label>
 </div>;
}

const barSteps=[.1,.2,.5,1,2,5,10,20,50,100,200,500,1000,2000,5000,10000];
/** Shortest 1-2-5 length in mm whose bar is at least `minimumPx` on screen at М 1:N and this zoom. */
export function scaleBarLength(denominator:number,zoom:number,minimumPx=64):{mm:number;px:number} {
 const pxPerMm=zoom/denominator,mm=barSteps.find(step=>step*pxPerMm>=minimumPx)??barSteps.at(-1)!;
 return {mm,px:mm*pxPerMm};
}

export interface RealSizeStatus { readonly denominator:number; readonly wiresWithoutDiameter:number; readonly connectorsWithoutSize:number }
/** Scale bar, М 1:N, cursor in mm and what is drawn without a catalogue size. */
export function RealSizeScaleBar({status,zoom,cursor}:{status:RealSizeStatus;zoom:number;cursor:{x:number;y:number}|null}){
 const bar=scaleBarLength(status.denominator,zoom),n=status.denominator;
 const unsized=[status.wiresWithoutDiameter?`проводов без диаметра: ${status.wiresWithoutDiameter}`:"",status.connectorsWithoutSize?`соединителей без размеров: ${status.connectorsWithoutSize}`:""].filter(Boolean);
 return <div className="he-real-size-bar" role="status" aria-label="Масштабная линейка">
  <div className="he-real-size-ruler"><span className="he-real-size-line" style={{width:bar.px}}/><b>{decimal(bar.mm,1)} мм</b></div>
  <span className="he-real-size-scale-label">М 1:{decimal(n)} · длины не в масштабе</span>
  <span className="he-real-size-cursor">{cursor?`X ${decimal(cursor.x*n,1)} · Y ${decimal(cursor.y*n,1)} мм`:"X — · Y — мм"}</span>
  {unsized.length>0&&<span className="he-real-size-unsized" title="Нарисованы в условную толщину: в каталоге нет диаметра или у соединителя нет рисунка из библиотеки">{unsized.join(" · ")}</span>}
 </div>;
}
