import {InfoHint} from "../InfoHint";
import type {HarnessDesignDocument} from "./model";
import type {PhysicalTopology} from "./physical-topology-model";
import {createJoiningPipe} from "./physical-joining-pipes";
import {pipeBundlePaths} from "./pipe-bundle-model";
import {parsePhysicalTopology} from "./physical-topology-validation";

export interface JoiningPipeDraft {readonly id:string;readonly sourceId:string;readonly paths:readonly (readonly string[])[];readonly mode:"flat"|"round"}
function candidatePaths(t:PhysicalTopology,id:string):readonly (readonly string[])[] {
  if(t.segments.some(s=>s.id===id))return [[id]];
  const cover=t.coverings?.find(c=>c.id===id);if(!cover)return [];
  return cover.bundle?pipeBundlePaths(cover,t.coverings??[]):cover.spans.filter(s=>t.segments.some(p=>p.id===s.segmentId)).map(s=>[s.segmentId]);
}
export function beginJoiningPipe(document:HarnessDesignDocument,id:string,selected:readonly string[]=[]):JoiningPipeDraft|null {
  const t=document.physicalTopology;if(!t)return null;
  const coating=t.coverings?.find(c=>c.id===id);
  const support=coating?.spans.length===1?t.joiningPipes?.find(p=>p.id===coating.spans[0]!.segmentId):undefined;
  if(support)return beginJoiningPipe(document,support.id,selected);
  const pipe=t.joiningPipes?.find(p=>p.id===id);
  if(pipe)return {id,sourceId:id,paths:pipe.members.map(m=>m.segmentIds),mode:pipe.mode};
  const paths=[...candidatePaths(t,id)];if(!paths.length)return null;
  for(const candidate of selected.flatMap(id=>candidatePaths(t,id)))if(!paths.some(p=>p.some(id=>candidate.includes(id))))paths.push(candidate);
  return {id:crypto.randomUUID(),sourceId:id,paths,mode:"flat"};
}
export function toggleJoiningPipeMember(document:HarnessDesignDocument,draft:JoiningPipeDraft,id:string):JoiningPipeDraft {
  const t=document.physicalTopology;if(!t)return draft;
  const candidates=candidatePaths(t,id);if(!candidates.length)return draft;
  const ids=new Set(candidates.flat()),has=draft.paths.some(p=>p.some(id=>ids.has(id)));
  return {...draft,paths:has?draft.paths.filter(p=>!p.some(id=>ids.has(id))):[...draft.paths,...candidates]};
}
export function joiningPipeDraftTopology(document:HarnessDesignDocument,draft:JoiningPipeDraft):PhysicalTopology {
  const t=document.physicalTopology!;
  const existing=t.joiningPipes?.find(p=>p.id===draft.id),cover=t.coverings?.find(c=>c.id===draft.sourceId);
  const fresh=createJoiningPipe(document,draft.paths,draft.id,existing?undefined:cover);
  const pipe=existing?{...existing,mode:draft.mode,members:fresh.members.map(m=>existing.members.find(old=>JSON.stringify(old.segmentIds)===JSON.stringify(m.segmentIds))??m)}:{...fresh,mode:draft.mode};
  const topology={...t,joiningPipes:[...t.joiningPipes?.filter(p=>p.id!==pipe.id)??[],pipe],coverings:t.coverings?.map(c=>c.id===cover?.id?{...c,bundle:undefined,spans:[{segmentId:pipe.id,from:0,to:1}]}:c)};
  return parsePhysicalTopology(topology,document)!;
}
export const joiningPipeDraftHighlights=(_document:HarnessDesignDocument,draft:JoiningPipeDraft)=>[draft.sourceId,...draft.paths.flat()];
export function JoiningPipeEditor({document,draft,onChange,onSave,onCancel}:{document:HarnessDesignDocument;draft:JoiningPipeDraft;onChange:(draft:JoiningPipeDraft)=>void;onSave:()=>void;onCancel:()=>void}) {
  const t=document.physicalTopology!;let error="";
  try{joiningPipeDraftTopology(document,draft);}catch(e){error=e instanceof Error?e.message:"Проверьте состав ОП.";}
  const owned=new Set(t.joiningPipes?.filter(p=>p.id!==draft.id).flatMap(p=>p.members.flatMap(m=>m.segmentIds))??[]);
  const label=(id:string)=>`П${t.segments.findIndex(s=>s.id===id)+1}`;
  return <section className="he-bundle-editor" aria-label="Объединение пайпов" onKeyDown={e=>{if(e.key==="Escape"){e.stopPropagation();onCancel();}}}>
    <strong>Объединяющий пайп</strong><InfoHint>Выберите пайпы на поле или в списке. ОП задаёт общую ось и имеет собственные концы и перегибы. Оболочки накладываются на ОП отдельно. Сохранить применяет состав одним шагом отмены.</InfoHint>
    <select aria-label="Укладка группы" value={draft.mode} onChange={e=>onChange({...draft,mode:e.target.value as "flat"|"round"})}><option value="flat">Плоская</option><option value="round">Объёмная</option></select>
    <select aria-label="Добавить участника группы" value="" onChange={e=>onChange(toggleJoiningPipeMember(document,draft,e.target.value))}><option value="">Добавить пайп…</option>{t.segments.filter(s=>!owned.has(s.id)&&!draft.paths.flat().includes(s.id)).map(s=><option key={s.id} value={s.id}>{label(s.id)}</option>)}</select>
    <div className="he-bundle-members" aria-label="Состав группы">{draft.paths.map(path=><button type="button" className="ui-control" key={path[0]} onClick={()=>onChange(toggleJoiningPipeMember(document,draft,path[0]!))}>{path.map(label).join(" → ")} ×</button>)}</div>
    <span>{draft.paths.length} пайпов</span><button type="button" className="ui-control" disabled={!!error} onClick={onSave}>Сохранить</button><button type="button" className="ui-control" onClick={onCancel}>Отмена</button>
    {error&&<span role="status">{error}</span>}
  </section>;
}
