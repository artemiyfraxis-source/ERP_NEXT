import {applyCoveringPreference} from "./covering-library";
import { InfoHint } from "../InfoHint";
import { DraftNumberInput } from "../component-library/DraftNumberInput";
import type { EditorCommand } from "./commands";
import type { HarnessDesignDocument } from "./model";
import { PhysicalTopologyPanel } from "./PhysicalTopologyPanel";
import { PhysicalCoveringsPanel } from "./PhysicalCoveringsPanel";
import { DrawingRotationControl } from "./DrawingRotationControl";
import { DrawingScaleControl } from "./DrawingScaleControl";
import { DRAWING_VIEW_PLACEMENT_ID,drawingRotation,drawingScale } from "./drawing-scale";
import { projectComponentTemplateView,type ComponentTemplateViewInstance } from "./component-template-view-renderer";
import { resolveWireColorHex } from "./wire-reference-catalog";
import type { WireDatabaseOption } from "./wire-database";
import { WireDatabasePicker } from "./WireDatabasePicker";
import { type DrawingGraphic, emptyDrawingDocuments } from "./drawing-documents";

/** State and actions for the optional E4 contact-side companion view.
 *
 * The inspector intentionally owns no placement/model logic: the editor wires
 * these callbacks to its command layer. This keeps the card usable while the
 * view is being created, selected, moved, or hidden by the canvas.
 */
export type ContactSideViewState = {
  readonly exists: boolean;
  readonly visible?: boolean;
  readonly scale?: number;
};

export type ContactSideViewActions = {
  readonly onAdd?: () => void;
  readonly onSelect?: () => void;
  readonly onShow?: () => void;
  readonly onHide?: () => void;
  readonly onRemove?: () => void;
  readonly onScaleChange?: (scale: number) => void;
};

export function ContactSideViewCard({state,actions}: {state?: ContactSideViewState; actions?: ContactSideViewActions}) {
  const exists=state?.exists===true;
  const visible=state?.visible!==false;
  const scale=state?.scale ?? 1;
  return <section className="he-contact-view-card" aria-label="Вид со стороны контактов" style={{border:"1px solid #b7c6cd",borderRadius:6,padding:"8px 9px",background:"#f7fafb",display:"grid",gap:6}}>
    <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:8}}>
      <strong style={{fontSize:12,color:"#284957"}}>Вид со стороны контактов</strong>
      <span className="he-contact-view-status" data-status={exists ? (visible ? "on-drawing" : "hidden") : "not-added"} style={{fontSize:11,color:exists ? (visible ? "#28734a" : "#687b84") : "#687b84"}}>{exists ? (visible ? "На чертеже" : "Скрыт") : "Не добавлен"}</span>
    </div>
    {exists&&<label>Масштаб<DrawingScaleControl value={scale} label="Масштаб вида со стороны контактов" disabled={!actions?.onScaleChange} onChange={value=>actions?.onScaleChange?.(value)}/></label>}
    <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
      {!exists&&<button type="button" className="ui-control" onClick={()=>actions?.onAdd?.()}>Добавить вид со стороны контактов</button>}
      {exists&&<>
        <button type="button" className="ui-control" onClick={()=>actions?.onSelect?.()}>Выделить</button>
        {!visible&&<button type="button" className="ui-control" onClick={()=>actions?.onShow?.()}>Показать</button>}
        {visible&&<button type="button" className="ui-control" onClick={()=>actions?.onHide?.()}>Скрыть</button>}
        {actions?.onRemove&&<button type="button" className="ui-control" onClick={actions.onRemove}>Удалить</button>}
      </>}
    </div>
  </section>;
}

function WireMaterialPicker({document,wireId,options,onCommand}: {document:HarnessDesignDocument;wireId:string;options:readonly WireDatabaseOption[];onCommand:(command:EditorCommand)=>boolean}) {
  const wire=document.wires.find(item=>item.id===wireId)!;
  const contact=[wire.from,wire.to].flatMap(end=>"connectorId" in end
    ? document.connectors.find(item=>item.id===end.connectorId)?.contacts.filter(item=>item.id===end.contactId)??[] : []).find(item=>item.wire.trim());
  return <WireDatabasePicker key={wireId} options={options} currentMark={contact?.wire} currentRecordId={wire.materialBinding?.recordId} onSelect={option=>{
      if(!option.materialBinding)return;
      const end=[wire.from,wire.to].find(endpoint=>"connectorId" in endpoint);
      if(end&&"connectorId" in end)onCommand({type:"update-contact",connectorId:end.connectorId,contactId:end.contactId,
        wire:option.mark,wireSection:option.section,wireDiameterMm:option.diameterMm??null,
        materialBinding:option.materialBinding,...(option.color?{color:option.color}:{})});
      else onCommand({type:"update-wire",wireId,materialBinding:option.materialBinding});
    }}/>;
}

export function DrawingObjectProperties({document,objectId,selectedIds,onCommand,onSelect,instances,onBundleEdit,wireMaterialOptions=[],contactSideView,contactSideViewActions}: {
  document:HarnessDesignDocument;objectId:string;onCommand:(command:EditorCommand)=>boolean;
  selectedIds:readonly string[];
  onSelect:(id:string)=>void;instances:readonly ComponentTemplateViewInstance[];
  onBundleEdit?:(id:string)=>void;
  wireMaterialOptions?:readonly WireDatabaseOption[];
  contactSideView?: ContactSideViewState;
  contactSideViewActions?: ContactSideViewActions;
}) {
  const t=document.physicalTopology,connector=document.connectors.find(c=>c.id===objectId),wire=document.wires.find(w=>w.id===objectId),graphic=document.drawingDocuments?.graphics?.find(item=>item.id===objectId);
  const joining=t?.joiningPipes?.find(p=>p.id===objectId), topology=t&&(t.nodes.some(n=>n.id===objectId)||t.segments.some(s=>s.id===objectId)||!!joining||wire);
  const cover=t?.coverings?.some(c=>c.id===objectId);
  const changeGraphic=(patch:Partial<DrawingGraphic>)=>{
    if(!graphic)return;
    const documents=document.drawingDocuments??emptyDrawingDocuments();
    onCommand({type:"set-drawing-documents",documents:{...documents,graphics:(documents.graphics??[]).map(item=>item.id===graphic.id?{...item,...patch}:item)}});
  };
  return <>
    {graphic?.kind==="text"&&<section className="he-context-fields" aria-label="Свойства текста">
      <strong>Текст</strong>
      <label>Текст<textarea aria-label="Текст фигуры" value={graphic.text??""} onChange={event=>changeGraphic({text:event.target.value})}/></label>
      <label>Шрифт<select aria-label="Шрифт текста" value={graphic.fontFamily??"Arial"} onChange={event=>changeGraphic({fontFamily:event.target.value})}><option>Arial</option><option>Calibri</option><option>Times New Roman</option><option>Courier New</option></select></label>
      <label>Размер<input aria-label="Размер шрифта" type="number" min={6} max={144} value={graphic.fontSize??16} onChange={event=>changeGraphic({fontSize:Number(event.target.value)})}/></label>
      <label>Цвет<input aria-label="Цвет текста" type="color" value={graphic.color??"#253b4a"} onChange={event=>changeGraphic({color:event.target.value})}/></label>
    </section>}
    {connector&&<section className="he-context-fields" aria-label="Рисунок соединителя">
      <strong>{connector.designation}</strong>
      <label>Масштаб<DrawingScaleControl value={drawingScale(connector.drawingPlacements)} label="Масштаб рисунка на чертеже" disabled={connector.libraryBinding?.mode!=="template"} onChange={scale=>onCommand({type:"set-drawing-placement",connectorId:objectId,drawingId:DRAWING_VIEW_PLACEMENT_ID,scale})}/></label>
      <DrawingRotationControl value={drawingRotation(connector.drawingPlacements)} disabled={connector.libraryBinding?.mode!=="template"} onChange={rotationDegrees=>{
        const instance=instances.find(i=>i.objectId===objectId),bounds=instance?projectComponentTemplateView(instance,"drawing",connector.positions.drawing)?.bounds:undefined;
        onCommand({type:"set-drawing-placement",connectorId:objectId,drawingId:DRAWING_VIEW_PLACEMENT_ID,rotationDegrees,...(bounds?{rotationCenter:{x:(bounds.minX+bounds.maxX)/2,y:(bounds.minY+bounds.maxY)/2}}:{})});
      }}/><InfoHint>Масштаб и поворот доступны библиотечному рисунку и применяются вместе с контактами и направлениями выходов. Для редактирования самого рисунка откройте библиотеку.</InfoHint>
    </section>}
    {connector?.libraryBinding?.mode==="template"&&<ContactSideViewCard state={contactSideView} actions={contactSideViewActions}/>}
    {wire&&<section className="he-context-fields" aria-label="Свойства провода">
      <label>Цепь<input aria-label="Цепь провода" value={wire.circuit} onChange={e=>onCommand({type:"update-wire",wireId:objectId,circuit:e.target.value})}/></label>
      <label>Цвет<input type="color" aria-label="Цвет провода" value={resolveWireColorHex(wire.color)} onChange={e=>onCommand({type:"update-wire",wireId:objectId,color:e.target.value})}/></label>
      <label>Поправка A, мм<DraftNumberInput aria-label="Поправка A, мм" value={wire.endCorrectionFromMm} onValueChange={endCorrectionFromMm=>onCommand({type:"update-wire",wireId:objectId,endCorrectionFromMm})}/></label>
      <label>Поправка B, мм<DraftNumberInput aria-label="Поправка B, мм" value={wire.endCorrectionToMm} onValueChange={endCorrectionToMm=>onCommand({type:"update-wire",wireId:objectId,endCorrectionToMm})}/></label>
      <InfoHint>Цвет и цепь общие с Э4. Поправки учитываются в длине заготовки; размеры участков задаются на поле.</InfoHint>
    </section>}
    {wire&&<WireMaterialPicker document={document} wireId={wire.id} options={wireMaterialOptions} onCommand={onCommand}/>}
    {topology&&<PhysicalTopologyPanel mode="object" document={document} selectedId={objectId} selectedIds={selectedIds.includes(objectId)?selectedIds:[objectId]} onChange={topology=>onCommand({type:"set-physical-topology",topology})} onSelect={onSelect}/>}
    {t?.segments.some(s=>s.id===objectId)&&<button type="button" className="ui-control" onClick={()=>{
      const segments=t.segments.filter(s=>s.id===objectId||selectedIds.includes(s.id)),id=crypto.randomUUID();
      if(onCommand({type:"set-physical-topology",topology:{...t,coverings:[...t.coverings??[],applyCoveringPreference({id,name:"Оболочка",kind:"braid",width:0,color:"#84959f",lengthMm:null,spans:segments.map(s=>({segmentId:s.id,from:0,to:1}))},document.drawingDocuments?.coveringLibrary)]}}))onSelect(id);
    }}>Оболочка на выделенные пайпы</button>}
    {joining&&<section className="he-context-fields" aria-label="Свойства объединяющего пайпа">
      <strong>ОП</strong><label>Ширина<DraftNumberInput aria-label="Ширина объединяющего пайпа" min={0} max={10000000} step="any" immediate value={joining.width??0} onValueChange={width=>onCommand({type:"update-joining-pipe",pipeId:joining.id,width})}/></label>
      <label>Цвет<input aria-label="Цвет объединяющего пайпа" type="color" value={joining.color??"#aebfc9"} onChange={e=>onCommand({type:"update-joining-pipe",pipeId:joining.id,color:e.target.value})}/></label>
      <label>Укладка<select aria-label="Укладка объединяющего пайпа" value={joining.mode} onChange={e=>onCommand({type:"update-joining-pipe",pipeId:joining.id,mode:e.target.value as "flat"|"round"})}><option value="flat">Плоская</option><option value="round">Объёмная</option></select></label>
      <button type="button" className="ui-control" onClick={()=>onCommand({type:"remove-physical-segment",segmentId:joining.id})}>Удалить ОП</button>
    </section>}
    {t?.segments.some(s=>s.id===objectId)&&<button type="button" className="ui-control" onClick={()=>onCommand({type:"remove-physical-segment",segmentId:objectId})}>Удалить пайп</button>}
    {(cover||joining||t?.segments.some(s=>s.id===objectId))&&onBundleEdit&&<button type="button" className="ui-control" onClick={()=>onBundleEdit(objectId)}><svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M2 3h4l4 5h4M2 13h4l4-5M2 8h12" fill="none" stroke="currentColor" strokeWidth="1.5"/></svg> Объединить</button>}
    {cover&&t&&<PhysicalCoveringsPanel compact document={document} topology={t} selectedIds={[objectId]} onChange={topology=>onCommand({type:"set-physical-topology",topology})} onReveal={onSelect}/>}
  </>;
}

