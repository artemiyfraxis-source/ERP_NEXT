import { InfoHint } from "../InfoHint";
import { DraftNumberInput } from "./DraftNumberInput";
import { hatchKinds, hatchLabels, type DrawingHatch } from "./drawing-hatch";
import type { TemplateNodeV2 } from "./template-model-v2";
import { drawingStyleLeaves, type SelectionStyle } from "./drawing-selection";

const colors=["#27445a","#000000","#111827","#6b7280","#ffffff","#dc2626","#f59e0b","#16a34a","#2563eb","#7c3aed"];
export function DrawingSelectionProperties({nodes,allNodes=nodes,disabled,change}:{nodes:readonly TemplateNodeV2[];allNodes?:readonly TemplateNodeV2[];disabled:boolean;change:(style:SelectionStyle)=>void}) {
  const leaves=drawingStyleLeaves(nodes,allNodes);
  const common=<T,>(read:(node:TemplateNodeV2)=>T,source:readonly TemplateNodeV2[]=leaves):T|undefined => { if(!source.length)return undefined; const first=read(source[0]!);return source.every(node=>JSON.stringify(read(node))===JSON.stringify(first)) ? first : undefined; };
  const opacity=common(node=>node.opacity,nodes),stroke=common(node=>node.stroke.color),fill=common(node=>node.fill.color),dash=common(node=>node.stroke.dash ?? "solid"),width=common(node=>node.stroke.width.kind==="constant" ? node.stroke.width.value : undefined);
  const numeric=(label:string,value:number|undefined,min:number,max:number,apply:(value:number)=>void,step=1,immediate=false)=><DraftNumber label={label} value={value} min={min} max={max} step={step} disabled={disabled} apply={apply} immediate={immediate}/>;
  return <section className="node-style-panel" aria-label="Общие свойства выделения"><strong>Общие свойства · {nodes.length}</strong><InfoHint>Пустое поле означает разные значения. Меняется только выбранное свойство. Стиль группы применяется к её фигурам, прозрачность — к группе целиком. Ctrl+C / Ctrl+V копируют фигуры со смещением на 12 единиц влево и вверх; Delete удаляет выделение.</InfoHint>
    {numeric("Прозрачность, %",opacity===undefined ? undefined : Math.round((1-opacity)*100),0,100,value=>change({opacity:1-value/100}))}
    <label>Цвет линии<input type="color" value={stroke ?? "#111827"} disabled={disabled} onChange={e=>change({stroke:{color:e.target.value}})} /></label>
    <div className="style-swatches">{colors.map(color=><button type="button" key={color} aria-label={`Общий цвет линии ${color}`} style={{background:color}} disabled={disabled} onClick={()=>change({stroke:{color}})} />)}</div>
    {numeric("Толщина линии",width,0,24,value=>change({stroke:{width:{kind:"constant",value}}}),0.5,true)}
    <label>Штрих линии<select value={dash ?? "mixed"} disabled={disabled} onChange={e=>change({stroke:{dash:e.target.value as "solid"|"dash"|"dot"|"dash-dot"}})}><option value="mixed" disabled>Разные</option><option value="solid">Сплошная</option><option value="dash">Штрих</option><option value="dot">Точки</option><option value="dash-dot">Штрих-точка</option></select></label>
    <label>Цвет заливки<input type="color" value={fill ?? "#ffffff"} disabled={disabled} onChange={e=>change({fill:{color:e.target.value}})} /></label>
    <div className="style-swatches">{colors.map(color=><button type="button" key={color} aria-label={`Общий цвет заливки ${color}`} style={{background:color}} disabled={disabled} onClick={()=>change({fill:{color}})} />)}</div>
    <button type="button" disabled={disabled} onClick={()=>change({fill:{color:null}})}>Без заливки</button>
    <label>Штриховка<select disabled={disabled} value={common(node=>node.fill.hatch?.kind ?? "solid") ?? "mixed"} onChange={e=>e.target.value==="solid" ? change({clearHatch:true}) : change({fill:{hatch:{kind:e.target.value as DrawingHatch["kind"],spacing:8,angle:45}}})}><option value="mixed" disabled>Разные</option><option value="solid">Сплошная</option>{hatchKinds.map(kind=><option key={kind} value={kind}>{hatchLabels[kind]}</option>)}</select></label>
  </section>;
}

function DraftNumber({label,value,min,max,step,disabled,apply,immediate=false}:{label:string;value:number|undefined;min:number;max:number;step:number;disabled:boolean;apply:(value:number)=>void;immediate?:boolean}) {
  return <label>{label}<DraftNumberInput value={value ?? ""} placeholder="Разные" min={min} max={max} step={step} disabled={disabled} immediate={immediate} onValueChange={apply}/></label>;
}
