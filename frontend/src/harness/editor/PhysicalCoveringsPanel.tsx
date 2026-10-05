import {applyCoveringPreference} from "./covering-library";
import {useState} from "react";
import "./covering-properties.css";
import {prunePipeBundles} from "./pipe-bundle-editing";
import { DraftNumberInput } from "../component-library/DraftNumberInput";
import { InfoHint } from "../InfoHint";
import { CoveringStyleFields } from "./CoveringStyleFields";
import type { PhysicalTopology } from "./physical-topology-model";
import type { HarnessDesignDocument } from "./model";
import { coveringMeasuredLength, coveringControlFractions, coveringKind, resolvedCoveringSpan, type CoveringKind } from "./physical-coverings";

export function PhysicalCoveringsPanel({ document, topology: t, selectedIds, onChange, onReveal, compact=false }: {
  compact?:boolean;
  document: HarnessDesignDocument; topology: PhysicalTopology; selectedIds: readonly string[]; onChange: (t: PhysicalTopology) => boolean; onReveal: (id: string) => void;
}) {
  const [tab,setTab]=useState("main");
  const segments = [...t.segments,...t.joiningPipes??[]].filter(s => selectedIds.includes(s.id));
  const selected = t.coverings?.find(c => selectedIds.includes(c.id));
  const update = (patch: Partial<NonNullable<PhysicalTopology["coverings"]>[number]>) => selected && onChange({ ...t, coverings: t.coverings?.map(c => c.id === selected.id ? { ...c, ...patch } : c) });
  return <section className="he-relations" aria-label="Оболочки и защита"><details open={!!selected}>
    <summary hidden={compact}>Оболочки и защита · {t.coverings?.length ?? 0}</summary>
    {!compact&&<>
    <header className="ui-section-heading"><strong>Оболочки</strong><InfoHint>Выберите участки и создайте общую оболочку. Для материала защиты дважды щёлкните позицию каталога «Защита». Начало и конец — проценты каждого участка. Ширина условная; расход задаётся отдельной длиной в миллиметрах.</InfoHint><button className="ui-control" type="button" aria-label="Создать оболочку" disabled={!segments.length} onClick={() => onChange({ ...t, coverings: [...t.coverings ?? [], applyCoveringPreference({ id: crypto.randomUUID(), name: "Оболочка", kind:"braid", width: 0, color: "#84959f", lengthMm: null, spans: segments.map(s => ({ segmentId: s.id, from: 0, to: 1 })) },document.drawingDocuments?.coveringLibrary)] })}>＋</button></header>
    {t.coverings?.map(c => <button key={c.id} type="button" className="ui-control" aria-pressed={selected?.id === c.id} onClick={() => onReveal(c.id)}>{c.name}</button>)}
    </>}
    {selected && <div className="he-covering-properties">
      <nav className="he-covering-tabs" aria-label="Свойства оболочки">{[["main","Основное"],["style","Оформление"],["anchors","Привязки"]].map(([id,label])=><button key={id} type="button" className="ui-control" aria-pressed={tab===id} onClick={()=>setTab(id!)}>{label}</button>)}</nav>
      {tab==="main"&&<div className="he-physical-fields">
      <label>Название<input aria-label="Название оболочки" value={selected.name} onChange={e => update({ name: e.target.value })} /></label>
      <label>Тип<InfoHint>При смене типа назначается его текстура: сначала из настроек чертежа, затем из библиотеки материалов. Геометрия и длина сохраняются.</InfoHint><select aria-label="Тип покрытия" value={coveringKind(selected)} onChange={e=>update({kind:e.target.value as CoveringKind})}>{([['heat-shrink','Термоусадка'],['nylon','Нейлонка'],['braid','Оплётка'],['metal-braid','Металлическая плетёнка'],['tape','Обмотка'],['band','Нитевый бандаж']] as const).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>
      <label>Ширина<InfoHint>0 — плотно по пайпу и нижним покрытиям. Положительное значение задаёт наибольший диаметр; меньшие диаметры меняются по глобальному отношению 1:x. Верхняя небандажная оболочка ОП отображается с самостоятельным припуском 2%; её изменение не меняет соседние оболочки. Толщина масштабируется вместе с чертежом.</InfoHint><DraftNumberInput aria-label="Ширина оболочки" min={0} max={10000000} step="any" immediate value={selected.width} onValueChange={width=>update({width})}/></label>
      {selected.bundle&&<label>Укладка<InfoHint>Состав: {selected.bundle.members.map(m=>m.kind==='segment'?`S${t.segments.findIndex(s=>s.id===m.id)+1}`:t.coverings?.find(c=>c.id===m.id)?.name??m.id).join(', ')}</InfoHint><select aria-label="Укладка сохранённой группы" value={selected.bundle.mode} onChange={e=>update({bundle:{...selected.bundle!,mode:e.target.value as 'flat'|'round'}})}><option value="flat">Плоская</option><option value="round">Объёмная</option></select></label>}
      <label>Расчёт длины<select aria-label="Расчёт длины оболочки" value={selected.lengthMode==='manual'||selected.lengthMode===undefined&&selected.lengthMm!==null?'manual':'auto'} onChange={e=>update({lengthMode:e.target.value as 'manual'|'auto'})}><option value="auto">По размерам пайпа</option><option value="manual">Вручную</option></select></label>
      <label>Длина, мм<DraftNumberInput aria-label="Длина защиты, мм" min={0} step="0.001" value={coveringMeasuredLength(document,selected) ?? ""} onValueChange={lengthMm=>update({lengthMode:'manual',lengthMm})} onEmpty={()=>update({lengthMode:'manual',lengthMm:null})}/></label>
      {selected.material&&<small>Материал: {selected.material.displayName}</small>}
      </div>}
      {tab==="style"&&<CoveringStyleFields style={selected.style} color={selected.color} kind={coveringKind(selected)} library={document.drawingDocuments?.coveringLibrary} onChange={style=>update({style})} onColorChange={color=>update({color})}/>}
      {tab==="anchors"&&<div className="he-covering-anchors"><header className="ui-section-heading"><strong>Границы покрытия</strong><InfoHint>Тяните поверхность вдоль пайпа, торцы — для растяжения. Зелёный торец привязан к точке. Два привязанных торца берут длину из размеров пайпа.</InfoHint></header>{selected.spans.map((span,index) => <div className="he-physical-fields" key={`${span.segmentId}:${index}`}><span>{t.joiningPipes?.some(p=>p.id===span.segmentId)?`ОП${t.joiningPipes.findIndex(p=>p.id===span.segmentId)+1}`:`П${t.segments.findIndex(s=>s.id===span.segmentId)+1}`}</span>
        {(["from", "to"] as const).map(key => <label key={key}>{key === "from" ? "Начало" : "Конец"}<select aria-label={`${key === "from" ? "Привязка начала" : "Привязка конца"} покрытия ${span.segmentId}`} value={span[key==='from'?'fromAnchor':'toAnchor']??'free'} onChange={e=>update({spans:selected.spans.map(s=>s===span?{...resolvedCoveringSpan(document,s),[key==='from'?'fromAnchor':'toAnchor']:e.target.value==='free'?undefined:Number(e.target.value)}:s)})}><option value="free">Свободно</option>{coveringControlFractions(document,span.segmentId).map((f,i)=><option key={i} value={i} disabled={key==='from'?f>=resolvedCoveringSpan(document,span).to:f<=resolvedCoveringSpan(document,span).from}>Точка {i+1}</option>)}</select></label>)}
      </div>)}</div>}
      <footer className="he-covering-footer"><button className="ui-control" type="button" aria-label="На передний план" disabled={t.coverings?.at(-1)?.id===selected.id} onClick={()=>onChange({...t,coverings:[...t.coverings?.filter(c=>c.id!==selected.id)??[],selected]})}>↑ Вперёд</button>
      <button className="ui-control" type="button" aria-label="На задний план" disabled={t.coverings?.[0]?.id===selected.id} onClick={()=>onChange({...t,coverings:[selected,...t.coverings?.filter(c=>c.id!==selected.id)??[]]})}>↓ Назад</button>
      <button className="ui-control" type="button" aria-label="Удалить оболочку" onClick={() => onChange({ ...t, coverings: prunePipeBundles(t.coverings?.filter(c => c.id !== selected.id),t.segments,t.joiningPipes?.map(p=>p.id)) })}>Удалить</button></footer>
    </div>}
  </details></section>;
}
