import type { DrawingPerimeters } from "./drawing-object-perimeter";
import { useRef, useState, type CSSProperties } from "react";
import type { EditorCamera } from "./editor-types";
import type { HarnessDesignDocument } from "./model";
import type { EditorCommand } from "./commands";
import { emptyDrawingDocuments, type DrawingDocuments, type DrawingTable } from "./drawing-documents";
import { DrawingDocumentsPanel } from "./DrawingDocumentsPanel";
import { CutDiagramPanel } from "./CutDiagramPanel";
import { InfoHint } from "../InfoHint";
import type { WireDatabaseOption } from "./wire-database";
import type { ConnectionTableSettings } from "./connection-table-settings";

export function tableWindowPosition(table:DrawingTable,camera:EditorCamera):CSSProperties {
  if(table.dock) return table.dock==="right"?{right:8,top:8}:table.dock==="bottom"?{left:8,bottom:8}:{left:8,top:8};
  return {left:table.position.x*camera.zoom+camera.offsetX,top:table.position.y*camera.zoom+camera.offsetY};
}
export function tableWindowStyle(table:DrawingTable,camera:EditorCamera):CSSProperties {
  return {...tableWindowPosition(table,camera),width:table.width??760,height:table.height??380};
}
export function resizeTableWindow(size:{width:number;height:number},dx:number,dy:number,dock?:DrawingTable["dock"]){
  return {width:Math.max(280,Math.min(4000,Math.round(size.width+(dock==="right"?-dx:dx)))),height:Math.max(160,Math.min(4000,Math.round(size.height+(dock==="bottom"?-dy:dy))))};
}
export function DrawingTableWindows(props:{connectionTableSettings?:ConnectionTableSettings;sourceFingerprint?:string;perimeters?:DrawingPerimeters;view?:"e4"|"drawing";document:HarnessDesignDocument;camera:EditorCamera;quantity:number;revision:number;unsaved:boolean;selectedIds:readonly string[];onChange:(d:DrawingDocuments)=>boolean;onCommand:(c:EditorCommand)=>boolean;onReveal:(ids:readonly string[])=>void;wireOptions?:readonly WireDatabaseOption[];wireDatabaseOptions?:readonly WireDatabaseOption[];onWireSearch?:(query:string)=>void}) {
  const d=props.document.drawingDocuments??emptyDrawingDocuments();
  return <>{d.tables.filter(table=>props.view!=="e4"||table.kind==="connections").map(table=><TableWindow key={table.id} {...props} table={table}/>)}</>;
}
function TableWindow({table,sourceFingerprint,perimeters,camera,document,quantity,revision,unsaved,selectedIds,onChange,onCommand,onReveal,wireOptions,wireDatabaseOptions,onWireSearch,connectionTableSettings}:Parameters<typeof DrawingTableWindows>[0]&{table:DrawingTable}) {
  const drag=useRef<{x:number;y:number}|null>(null);
  const windowRef=useRef<HTMLElement|null>(null);
  const resize=useRef<{x:number;y:number;width:number;height:number}|null>(null);
  const [size,setSize]=useState<{width:number;height:number}|null>(null);
  const [delta,setDelta]=useState({x:0,y:0});
  const d=document.drawingDocuments!;
  const update=(patch:Partial<DrawingTable>)=>onChange({...d,tables:d.tables.map(t=>t.id===table.id?{...t,...patch}:t)});
  const cancelResize=()=>{resize.current=null;setSize(null);};
  const title=table.kind==="bom"?"Спецификация":table.kind==="cut"?"Резка и разделка":"Таблица соединений";
  return <section ref={windowRef} className="he-table-window" aria-label={title+" на поле"} style={{...tableWindowStyle(table,camera),...size,transform:`translate(${delta.x}px,${delta.y}px)`}}
    onPointerDown={e=>e.stopPropagation()} onWheel={e=>e.stopPropagation()} onKeyDown={e=>{if(!((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="z"))e.stopPropagation();}}>
    <header onPointerDown={e=>{if(table.dock||e.button!==0||(e.target as HTMLElement).closest("button,select"))return;drag.current={x:e.clientX,y:e.clientY};e.currentTarget.setPointerCapture(e.pointerId);}}
      onPointerMove={e=>{if(drag.current)setDelta({x:e.clientX-drag.current.x,y:e.clientY-drag.current.y});}}
      onPointerUp={e=>{if(!drag.current)return;const start=drag.current;drag.current=null;setDelta({x:0,y:0});if(start.x!==e.clientX||start.y!==e.clientY)update({position:{x:table.position.x+(e.clientX-start.x)/camera.zoom,y:table.position.y+(e.clientY-start.y)/camera.zoom}});}}
      onPointerCancel={()=>{drag.current=null;setDelta({x:0,y:0});}}>
      <strong>{title}</strong><InfoHint>Перетаскивайте окно за заголовок, растягивайте за угловой маркер. На маркере работают стрелки, Shift увеличивает шаг. Положение и размер сохраняются; Ctrl+Z отменяет растяжение целиком. Закреплённое окно остаётся у края при перемещении и масштабировании чертежа.</InfoHint>
      <select aria-label={`Закрепить: ${title}`} value={table.dock??""} onChange={e=>update({dock:(e.target.value||undefined) as DrawingTable["dock"],...(!e.target.value?{position:{x:(24-camera.offsetX)/camera.zoom,y:(24-camera.offsetY)/camera.zoom}}:{})})}>
        <option value="">На поле</option><option value="left">Слева</option><option value="right">Справа</option><option value="top">Сверху</option><option value="bottom">Снизу</option>
      </select><button type="button" className="ui-control" aria-label={`Закрыть: ${title}`} onClick={()=>onChange({...d,tables:d.tables.filter(t=>t.id!==table.id)})}>×</button>
    </header>
    <div className={`he-table-window-body ${table.kind!=="cut"?"he-table-window-document":""}`}>{table.kind==="cut"?<CutDiagramPanel sourceFingerprint={sourceFingerprint} embedded document={document} quantity={quantity} revision={revision} unsaved={unsaved} relatedIds={selectedIds} onCommand={onCommand} onReveal={id=>onReveal([id])}/>:<DrawingDocumentsPanel connectionTableSettings={connectionTableSettings} sourceFingerprint={sourceFingerprint} unsaved={unsaved} perimeters={perimeters} mode={table.kind} document={document} quantity={quantity} selectedId={null} selectedIds={selectedIds} onChange={onChange} onCommand={onCommand} onReveal={onReveal} wireOptions={wireOptions} wireDatabaseOptions={wireDatabaseOptions} onWireSearch={onWireSearch}/>}</div>
    <button type="button" className={`he-table-window-resize ${table.dock==="right"?"at-left":table.dock==="bottom"?"at-top":""}`} aria-label={`Изменить размер: ${title}`}
      onPointerDown={e=>{if(e.button!==0||!windowRef.current)return;e.preventDefault();e.currentTarget.focus();const rect=windowRef.current.getBoundingClientRect();resize.current={x:e.clientX,y:e.clientY,width:rect.width,height:rect.height};e.currentTarget.setPointerCapture(e.pointerId);}}
      onPointerMove={e=>{const start=resize.current;if(start)setSize(resizeTableWindow(start,e.clientX-start.x,e.clientY-start.y,table.dock));}}
      onPointerUp={e=>{const start=resize.current;if(!start)return;cancelResize();if(start.x!==e.clientX||start.y!==e.clientY)update(resizeTableWindow(start,e.clientX-start.x,e.clientY-start.y,table.dock));}}
      onPointerCancel={cancelResize} onLostPointerCapture={cancelResize}
      onKeyDown={e=>{if(!["ArrowLeft","ArrowRight","ArrowUp","ArrowDown"].includes(e.key))return;e.preventDefault();const step=e.shiftKey?40:10;update(resizeTableWindow({width:table.width??760,height:table.height??380},e.key==="ArrowLeft"?-step:e.key==="ArrowRight"?step:0,e.key==="ArrowUp"?-step:e.key==="ArrowDown"?step:0,table.dock));}}
    ><svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M2 10 10 2M6 10l4-4" fill="none" stroke="currentColor"/></svg></button>
  </section>;
}
