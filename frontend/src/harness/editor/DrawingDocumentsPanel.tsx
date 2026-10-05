import { routeCutReadiness } from "../manufacturing/route-cut-readiness";
import type { DrawingPerimeters } from "./drawing-object-perimeter";
import { pipeMeasuredWireLength } from "./drawing-dimensions";
import { useEffect, useState, type CSSProperties } from "react";
import { InfoHint } from "../InfoHint";
import { AnchoredPopover } from "../AnchoredPopover";
import type { EditorCommand } from "./commands";
import type { HarnessDesignDocument } from "./model";
import { addDrawingPositions, setDrawingPositionVisibility, moveDrawingAnnotation, buildDrawingBom, connectionEndLabel, connectionTableEndLabel, connectionWireColor, drawingObjectOrigin, emptyDrawingDocuments, reconcileDrawingDocuments, type DrawingDocuments } from "./drawing-documents";
import { connectionTableColumnLabels, useConnectionTableSettings, type ConnectionTableSettings } from "./connection-table-settings";
import { builtInWireOptions, filterWireOptions, normalizeWireChoice, normalizeWireSectionChoice, type WireDatabaseOption } from "./wire-database";
import { builtInWireColors, resolveWireColorHex } from "./wire-reference-catalog";

function compactFieldStyle(value: string, minimum = 7, maximum = 28): CSSProperties {
  const length = Array.from(value.trim()).length + 2;
  return { width: `${Math.max(minimum, Math.min(maximum, length))}ch` };
}

function wireMark(document: HarnessDesignDocument, wire: HarnessDesignDocument["wires"][number]): string {
  const contacts = [wire.from, wire.to].flatMap(end => {
    if (!("connectorId" in end)) return [];
    const connector = document.connectors.find(item => item.id === end.connectorId);
    return connector?.contacts.filter(contact => contact.id === end.contactId) ?? [];
  });
  return contacts.find(contact => contact.wire.trim())?.wire.trim() ?? wire.materialBinding?.sourceKey ?? "";
}

function wireSection(document: HarnessDesignDocument, wire: HarnessDesignDocument["wires"][number]): string {
  const contacts = [wire.from, wire.to].flatMap(end => {
    if (!("connectorId" in end)) return [];
    const connector = document.connectors.find(item => item.id === end.connectorId);
    return connector?.contacts.filter(contact => contact.id === end.contactId) ?? [];
  });
  return contacts.find(contact => contact.wireSection?.trim())?.wireSection?.trim() ?? "";
}

function ConnectionWirePicker({
  document,
  wire,
  options,
  disabled,
  onCommand,
  onSearch,
}: {
  document: HarnessDesignDocument;
  wire: HarnessDesignDocument["wires"][number];
  options: readonly WireDatabaseOption[];
  disabled: boolean;
  onCommand: (command: EditorCommand) => boolean;
  onSearch?: (query: string) => void;
}) {
  const mark = wireMark(document, wire);
  const [query, setQuery] = useState(mark);
  const [searchQuery, setSearchQuery] = useState("");
  const [open, setOpen] = useState(false);
  useEffect(() => setQuery(mark), [mark]);
  const matchedOptions = filterWireOptions(options, searchQuery);
  const marks = [...new Map(matchedOptions.map(option => [option.mark, option])).values()];
  const apply = (option: WireDatabaseOption) => {
    for (const end of [wire.from, wire.to]) {
      if (!("connectorId" in end)) continue;
      const connector = document.connectors.find(item => item.id === end.connectorId);
      if (!connector?.contacts.some(contact => contact.id === end.contactId)) continue;
      onCommand({ type: "update-contact", connectorId: end.connectorId, contactId: end.contactId,
        wire: option.mark, wireSection: "", wireDiameterMm: null, materialBinding: null,
        ...(option.color ? { color: option.color } : {}) });
    }
    setQuery(option.mark);
    setOpen(false);
  };
  return <div className="he-connection-wire-picker">
    <input aria-label={`Марка провода ${wire.id}`} value={query} disabled={disabled}
      placeholder={mark || "Выбрать марку"} autoComplete="off" style={compactFieldStyle(query || mark, 10, 18)}
      onChange={event => { setQuery(event.currentTarget.value); setSearchQuery(event.currentTarget.value); setOpen(true); onSearch?.(event.currentTarget.value); }}
      onFocus={() => { setQuery(mark); setSearchQuery(""); setOpen(true); onSearch?.(""); }} />
    {open && <AnchoredPopover className="e4cce-wire-suggestions he-connection-wire-suggestions" role="listbox" label={`Марки проводов ${wire.id}: ${marks.length} вариантов, список прокручивается`} open onClose={() => setOpen(false)}>
      <small className="he-connection-suggestion-count">{marks.length} {marks.length === 1 ? "марка" : "марок"} · прокрутите список</small>
      {marks.map(option => <button type="button" key={option.mark}
        onPointerDown={event => event.preventDefault()} onClick={() => apply(option)} title={option.detail}>
        {option.mark}
      </button>)}
      {marks.length === 0 && <small role="status">Совпадений нет</small>}
    </AnchoredPopover>}
  </div>;
}

function ConnectionSectionPicker({ document, wire, options, disabled, onCommand }: {
  document: HarnessDesignDocument;
  wire: HarnessDesignDocument["wires"][number];
  options: readonly WireDatabaseOption[];
  disabled: boolean;
  onCommand: (command: EditorCommand) => boolean;
}) {
  const mark = wireMark(document, wire);
  const current = wireSection(document, wire);
  const possible = options.filter(option => option.mark.trim().toLocaleLowerCase("ru-RU") === mark.trim().toLocaleLowerCase("ru-RU"));
  const sections = [...new Set([current, ...possible.map(option => option.section)].filter(Boolean))];
  return <select aria-label={`Сечение провода ${wire.id}`} value={current} disabled={disabled || !mark}
    style={compactFieldStyle(current,7,18)} title={mark ? "Сечения выбранной марки" : "Сначала выберите марку"}
    onChange={event => {
      const section = event.currentTarget.value;
      const option = possible.find(item => item.section === section);
      for (const end of [wire.from, wire.to]) {
        if (!end.connectorId || !end.contactId) continue;
        onCommand({ type: "update-contact", connectorId: end.connectorId, contactId: end.contactId,
          wire: mark, wireSection: section, wireDiameterMm: option?.diameterMm ?? null,
          materialBinding: option?.materialBinding ?? null,
          ...(option?.color ? { color: option.color } : {}) });
      }
    }}><option value="">—</option>{sections.map(section => <option key={section} value={section}>{section}</option>)}</select>;
}

export function DrawingDocumentsPanel({document,quantity,selectedId,selectedIds,onChange,onCommand,onReveal,mode="controls", perimeters, readOnly=false, availableKinds=["bom","connections","cut"], wireOptions = builtInWireOptions, wireDatabaseOptions, onWireSearch,sourceFingerprint,unsaved=false,connectionTableSettings}: {
  sourceFingerprint?:string;unsaved?:boolean;perimeters?:DrawingPerimeters;readOnly?:boolean;availableKinds?:readonly ("bom"|"connections"|"cut")[];
  connectionTableSettings?: ConnectionTableSettings;
  mode?:"controls"|"bom"|"connections";document:HarnessDesignDocument;quantity:number;selectedId:string|null;selectedIds:readonly string[];
  onChange:(d:DrawingDocuments)=>boolean;onCommand:(c:EditorCommand)=>boolean;onReveal:(ids:readonly string[])=>void;
  wireOptions?: readonly WireDatabaseOption[]; wireDatabaseOptions?: readonly WireDatabaseOption[]; onWireSearch?: (query: string) => void;
}) {
  const cutReady=routeCutReadiness(document,sourceFingerprint,unsaved);
  const [filter,setFilter]=useState("");
  const [onlyRelated,setOnlyRelated]=useState(false);
  const d=document.drawingDocuments ?? emptyDrawingDocuments(),rows=buildDrawingBom(document,quantity);
  const liveConnectionSettings = useConnectionTableSettings();
  const connectionSettings = connectionTableSettings ?? liveConnectionSettings;
  const connectionColumns = connectionSettings.columns.filter(column => column.visible);
  const colorCatalogOptions = wireDatabaseOptions ?? wireOptions;
  const databaseColors = (wire: HarnessDesignDocument["wires"][number]) => {
    const color = connectionWireColor(document, wire);
    const hasSchemeColor = [wire.from, wire.to].some(end => "connectorId" in end &&
      document.connectors.find(connector => connector.id === end.connectorId)?.contacts
        .find(contact => contact.id === end.contactId)?.color.trim()) || wire.color !== "#334155";
    if (color.label === "—" || !hasSchemeColor) return color;
    const mark = wireMark(document, wire);
    const section = wireSection(document, wire);
    const primaryLabel = color.label.split(" / ")[0]!.trim();
    const primaryHex = color.hex.toLocaleUpperCase();
    const found = builtInWireColors.some(reference => reference.hex.toLocaleUpperCase() === primaryHex ||
      normalizeWireChoice(reference.name) === normalizeWireChoice(primaryLabel)) || colorCatalogOptions.some(option => {
      if (normalizeWireChoice(option.mark) !== normalizeWireChoice(mark) ||
          normalizeWireSectionChoice(option.section) !== normalizeWireSectionChoice(section) || !option.color) return false;
      const optionHex = resolveWireColorHex(option.color, builtInWireColors, "").toLocaleUpperCase();
      return optionHex === primaryHex || normalizeWireChoice(option.color) === normalizeWireChoice(primaryLabel);
    });
    return { ...color, label: found ? color.label : `${color.label} (в базе не найден)` };
  };
  const orphanLeaders=d.leaders.filter(l=>!drawingObjectOrigin(document,l.objectId)||!rows.some(r=>r.key===l.rowKey&&r.objectIds.includes(l.objectId)));
  const leader=d.leaders.find(l=>l.id===selectedId||`${l.id}:anchor`===selectedId);
  const addTable=(kind:"bom"|"connections"|"cut")=>onChange({...d,tables:[...d.tables,{id:crypto.randomUUID(),kind,position:{x:40+d.tables.length*30,y:40+d.tables.length*30}}]});
  const editBom=(key:string,field:"index"|"designation"|"name"|"note",value:string)=>onChange({...d,bomText:{...d.bomText,[key]:{...d.bomText?.[key],[field]:value}}});
  const updateLeader=(key:"anchorOffset"|"circle",axis:"x"|"y",value:number)=>{
    if(!leader)return; const point={...leader[key],[axis]:value};
    const origin=drawingObjectOrigin(document,leader.objectId);if(!origin)return;
    const docs=moveDrawingAnnotation(document,key==="circle"?leader.id:`${leader.id}:anchor`,key==="circle"?{x:point.x-12,y:point.y-12}:{x:origin.x+point.x-4,y:origin.y+point.y-4},perimeters);
    if(docs)onChange(docs);
  };
  const Content = mode === "controls" ? "details" : "div";
  return <section className="he-relations" aria-label={mode==="connections"?"Таблица соединений":"Документы схемы"}><Content className={mode!=="controls"?"he-document-content":undefined} {...(mode==="controls"?{open:!!leader}:{})}>{mode==="controls"&&<summary>{availableKinds.length===1?"Таблица соединений":"Таблицы и выноски"}</summary>}
    <header className="ui-section-heading"><InfoHint>{mode==="connections"||availableKinds.length===1 ? "Соединения взяты из текущего документа жгута. Фильтр не меняет данные. В Э4 и Чертеже таблицу можно открыть на поле, передвинуть и закрепить у края. В Маршруте доступен просмотр." : <>Количество спецификации рассчитано для всего количества жгутов. Фильтр меняет показ, но не расход. Кружок перемещается свободно, якорь — по периметру своего объекта; номер следует позиции спецификации. Таблицы на поле перетаскиваются за заголовок. «Добавить позиции» размещает все номера. Кнопка с глазом показывает или скрывает позицию сразу у всех её обозначений; новые номера располагаются рядом по горизонтали.</>}</InfoHint></header>
    {mode==="controls" && <div className="he-relations-actions">{availableKinds.map(kind=><button key={kind} className="ui-control" type="button" disabled={kind==="cut"&&!cutReady.ready} title={kind==="cut"?cutReady.message:undefined} onClick={()=>{if(kind!=="cut"||cutReady.ready)addTable(kind);}}>{kind==="bom"?"Спецификация +":kind==="cut"?"Резка +":"Соединения +"}</button>)}</div>}
    {mode==="controls" && d.tables.filter(t=>availableKinds.includes(t.kind)).map(t=><div className="he-relations-actions" key={t.id}><span>{t.kind==="bom"?"Спецификация":t.kind==="cut"?"Резка":"Соединения"}</span><button className="ui-control" type="button" onClick={()=>onChange({...d,tables:d.tables.filter(i=>i.id!==t.id)})}>Убрать с поля</button></div>)}
    {mode!=="controls" && <><div className="he-document-tools"><input aria-label="Поиск строки документа" placeholder="Поиск" value={filter} onChange={e=>setFilter(e.target.value)}/>
    <label><input type="checkbox" checked={onlyRelated} onChange={e=>setOnlyRelated(e.target.checked)}/>Только связанные</label>
    {mode==="bom" && <button className="ui-control" type="button" disabled={readOnly} onClick={()=>onChange(addDrawingPositions(document,perimeters))}>Добавить позиции</button>}{mode==="bom" && <small>Потребность на {quantity} жгут(а)</small>}</div>
    {mode==="bom" && <div className="he-document-table"><table aria-label="Спецификация"><thead><tr>{["Поз.","Индекс","Обозначение","Наименование","Кол-во","Примечание"].map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>{rows.filter(r=>(!onlyRelated||r.objectIds.some(id=>selectedIds.includes(id)))&&`${r.index} ${r.designation} ${r.name}`.toLocaleLowerCase().includes(filter.toLocaleLowerCase())).map(r=><tr key={r.key} ><td><button className="ui-control" type="button" onClick={()=>onReveal(r.objectIds)}>{r.position}</button><button className="ui-control he-position-toggle" type="button" disabled={readOnly||!r.objectIds.some(id=>drawingObjectOrigin(document,id))} aria-label={`${d.leaders.some(l=>l.rowKey===r.key&&!l.hidden)?"Скрыть":"Показать"} позицию ${r.position} у всех обозначений`} title="Показать / скрыть у всех обозначений" aria-pressed={d.leaders.some(l=>l.rowKey===r.key&&!l.hidden)} onClick={()=>onChange(setDrawingPositionVisibility(document,r.key,!d.leaders.some(l=>l.rowKey===r.key&&!l.hidden),perimeters))}><svg width="16" height="16" viewBox="0 0 20 20" aria-hidden="true"><path d="M2 10s3-5 8-5 8 5 8 5-3 5-8 5-8-5-8-5Z" fill="none" stroke="currentColor"/><circle cx="10" cy="10" r="2.5" fill="none" stroke="currentColor"/>{!d.leaders.some(l=>l.rowKey===r.key&&!l.hidden)&&<path d="m3 3 14 14" stroke="currentColor"/>}</svg></button></td><td><input style={compactFieldStyle(r.index)} readOnly={readOnly} aria-label={`Индекс позиции ${r.position}`} value={r.index} onChange={e=>editBom(r.key,"index",e.target.value)}/></td><td><input style={compactFieldStyle(r.designation)} readOnly={readOnly} aria-label={`Обозначение позиции ${r.position}`} value={r.designation} onChange={e=>editBom(r.key,"designation",e.target.value)}/></td><td><input style={compactFieldStyle(r.name,12,34)} readOnly={readOnly} aria-label={`Наименование позиции ${r.position}`} value={r.name} onChange={e=>editBom(r.key,"name",e.target.value)}/></td><td>{r.amount ?? "—"} {r.unit}</td><td className="he-bom-note-cell"><input style={compactFieldStyle(r.note,10,30)} readOnly={readOnly} aria-label={`Примечание позиции ${r.position}`} value={r.note} onChange={e=>editBom(r.key,"note",e.target.value)}/><div className="he-relations-actions"><button className="ui-control" type="button" onClick={()=>{const i=rows.findIndex(row=>row.key===r.key),keys=rows.map(row=>row.key);if(i>0){[keys[i-1],keys[i]]=[keys[i]!,keys[i-1]!];onChange({...d,bomOrder:keys});}}}>↑</button></div>

    </td></tr>)}</tbody></table></div>}
    {mode==="connections" && <><div className="he-document-table he-connections-table"><table aria-label="Таблица соединений"><thead><tr>{connectionColumns.map(column => <th key={column.id} data-column={column.id}>{connectionTableColumnLabels[column.id]}</th>)}</tr></thead><tbody>{document.wires.filter(w=>(!onlyRelated||selectedIds.includes(w.id))&&`${wireMark(document,w)} ${wireSection(document,w)} ${w.circuit} ${connectionEndLabel(document,w.from)} ${connectionEndLabel(document,w.to)}`.toLowerCase().includes(filter.toLowerCase())).map(w=><tr key={w.id} >{connectionColumns.map(column => <td key={column.id} data-column={column.id} className={column.id === "color" ? "he-connection-color-cell" : undefined} style={column.id === "color" ? (()=>{const color=databaseColors(w); const hex=color.hex.replace("#",""); const rgb=[0,2,4].map(i=>parseInt(hex.slice(i,i+2),16)); const contrast=(rgb[0]!*.299+rgb[1]! * .587+rgb[2]! * .114)>160?"#163548":"#fff"; return {backgroundColor:color.hex,color:contrast};})() : undefined}>{column.id === "index" && `W${document.wires.indexOf(w)+1}`}{column.id === "mark" && <ConnectionWirePicker document={document} wire={w} options={wireOptions} disabled={readOnly} onCommand={onCommand} onSearch={onWireSearch}/>} {column.id === "section" && <ConnectionSectionPicker document={document} wire={w} options={wireOptions} disabled={readOnly} onCommand={onCommand}/>} {column.id === "marking" && <input style={compactFieldStyle(w.circuit,9,24)} aria-label={`Маркировка ${w.id}`} readOnly={readOnly} value={w.circuit} onChange={e=>onCommand({type:"update-wire",wireId:w.id,circuit:e.target.value})}/>} {column.id === "from" && connectionTableEndLabel(document,w.from)}{column.id === "to" && connectionTableEndLabel(document,w.to)}{column.id === "color" && databaseColors(w).label}{column.id === "length" && <input style={compactFieldStyle(w.lengthMm === null ? "" : String(w.lengthMm),8,12)} aria-label={`Длина ${w.id}`} title={pipeMeasuredWireLength(document,w.id).managed?"Длина по размерам пайпов; индивидуальные добавки — в поправках провода":undefined} readOnly={readOnly||pipeMeasuredWireLength(document,w.id).managed||document.drawingDocuments?.dimensions?.some(d=>d.wireId===w.id)} type="number" min="0" value={w.lengthMm ?? ""} onChange={e=>onCommand({type:"update-wire",wireId:w.id,lengthMm:e.target.value===""?null:Number(e.target.value)})}/>} {column.id === "route" && (document.physicalTopology?.routes.find(r=>r.wireId===w.id)?.steps.length ? "Задан" : "Не задан")}</td>)}</tr>)}</tbody></table></div></>}</>}    {leader && <div className="he-physical-fields"><strong>Выноска</strong>{(["anchorOffset","circle"] as const).flatMap(key=>(["x","y"] as const).map(axis=><label key={`${key}${axis}`}>{key==="circle"?"Круг":"Якорь"} {axis}<input aria-label={`${key} ${axis}`} type="number" value={leader[key][axis]} onChange={e=>updateLeader(key,axis,Number(e.target.value))}/></label>))}<button className="ui-control" type="button" onClick={()=>onChange({...d,leaders:d.leaders.filter(l=>l.id!==leader.id)})}>Удалить выноску</button></div>}
    {mode==="controls" && orphanLeaders.length>0 && <div role="alert">Устаревшие выноски: {orphanLeaders.length}. <button type="button" className="ui-control" disabled={readOnly} onClick={()=>onChange(reconcileDrawingDocuments(document,document).drawingDocuments!)}>Удалить</button></div>}
  </Content></section>;
}
