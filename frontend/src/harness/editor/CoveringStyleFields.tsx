import { DraftNumberInput } from "../component-library/DraftNumberInput";
import { InfoHint } from "../InfoHint";
import { resolvedCoveringStyle,type CoveringStyle } from "./covering-style";
import {textureChoices,type CoveringLibrary} from "./covering-library";
import type {CoveringKind} from "./physical-coverings";
import {hatchingCatalog} from "../hatching";

const colors=["#27445a","#000000","#111827","#6b7280","#ffffff","#dc2626","#f59e0b","#16a34a","#2563eb","#7c3aed"];
export function ColorField({label,value,onChange}:{label:string;value:string;onChange:(value:string)=>void}) {
  return <label>{label}<input type="color" aria-label={label} value={value} onChange={e=>onChange(e.target.value)}/>
    <span className="he-material-colors">{colors.map(color=><button key={color} type="button" aria-label={`${label}: ${color}`} title={color} style={{background:color}} onClick={()=>onChange(color)}/>)}</span></label>;
}
export function CoveringStyleFields({style,color,library,kind,onChange,onColorChange}:{style?:CoveringStyle;color:string;library?:CoveringLibrary;kind?:CoveringKind;onChange:(style:CoveringStyle)=>void;onColorChange:(color:string)=>void}) {
  const value=resolvedCoveringStyle(style),edit=(patch:CoveringStyle)=>onChange({...style,...patch});
  const preferred=library?.defaults[kind??"braid"]?.texture??"auto",options=textureChoices(library);
  return <div className="he-covering-style he-context-fields">
    <label className="he-covering-full">Текстура<InfoHint>Фон, текстура, штриховка и контур настраиваются независимо. Масштаб и углы не меняют размеры материала.</InfoHint><select aria-label="Текстура оболочки" value={value.texture} onChange={e=>edit({texture:e.target.value as CoveringStyle['texture']})}>
      {!options.some(t=>t.value===value.texture)&&<option value={value.texture}>Текстура вне списка</option>}{options.map(t=><option key={t.value} value={t.value}>{t.label}{t.value===preferred?" · по умолчанию":""}</option>)}
    </select></label><label>Масштаб текстуры<DraftNumberInput aria-label="Масштаб текстуры" min={.1} max={10} step={.1} immediate value={value.textureScale} onValueChange={textureScale=>edit({textureScale})}/></label>
    <label>Угол текстуры, °<DraftNumberInput aria-label="Угол текстуры" min={-180} max={180} step={15} immediate value={value.textureRotation} onValueChange={textureRotation=>edit({textureRotation})}/></label>
    <ColorField label="Цвет фона" value={color} onChange={onColorChange}/><ColorField label="Цвет текстуры" value={value.textureTint} onChange={textureTint=>edit({textureTint})}/><ColorField label="Цвет контура" value={value.lineColor} onChange={lineColor=>edit({lineColor})}/>
    <label>Толщина контура<DraftNumberInput aria-label="Толщина контура" min={.1} max={20} step={.1} immediate value={value.lineWidth} onValueChange={lineWidth=>edit({lineWidth})}/></label>
    <label>Штриховка<select aria-label="Штриховка оболочки" value={value.hatchCode||value.hatch} onChange={e=>edit(e.target.value.startsWith("H")?{hatchCode:e.target.value,hatch:"none"}:{hatchCode:"",hatch:e.target.value as CoveringStyle['hatch']})}>
      <option value="none">Нет</option><option value="parallel">Параллельная</option><option value="cross">Перекрёстная</option><option value="dots">Точки</option>
      {hatchingCatalog.map(h=><option key={h.code} value={h.code}>{h.code} · {h.title}</option>)}
    </select></label>
    {(value.hatch!=="none"||value.hatchCode)&&<><ColorField label="Цвет штриховки" value={value.hatchColor} onChange={hatchColor=>edit({hatchColor})}/>
      <label>Толщина линии<DraftNumberInput aria-label="Толщина линии штриховки" min={.1} max={20} step={.1} immediate value={value.hatchLineWidth} onValueChange={hatchLineWidth=>edit({hatchLineWidth})}/></label>
      <label>Шаг штриховки<DraftNumberInput aria-label="Шаг штриховки" min={1} max={100} step="any" immediate value={value.hatchSpacing} onValueChange={hatchSpacing=>edit({hatchSpacing})}/></label>
      <label>Угол штриховки, °<DraftNumberInput aria-label="Угол штриховки" min={-180} max={180} step={15} immediate value={value.hatchRotation} onValueChange={hatchRotation=>edit({hatchRotation})}/></label></>}
  </div>;
}
