import {useEffect,useState} from "react";
import {validDrawingScale} from "./drawing-scale";
export function DrawingScaleControl({value,onChange,disabled,label}:{value:number;onChange:(value:number)=>void;disabled:boolean;label:string}){
 const [text,setText]=useState(String(Math.round(value*100)));useEffect(()=>setText(String(Math.round(value*100))),[value]);
 const save=()=>{const next=Number(text)/100;if(validDrawingScale(next))onChange(next);else setText(String(Math.round(value*100)));};
 return <span className="drawing-scale-control"><input aria-label={label} type="number" min="5" max="2000" step="5" value={text} disabled={disabled} onChange={e=>setText(e.target.value)} onBlur={save} onKeyDown={e=>{if(e.key==="Enter"){e.preventDefault();e.currentTarget.blur();}}}/><span>%</span></span>;
}
