import { calculateCableSheathStrip, calculateWireCutLength, calculateWireStripSteps, type HarnessDesignDocument, type WireInstance, type WireStripStep } from "./model";
import { connectionEndLabel } from "./drawing-documents";

export interface CutDiagramEnd {
  readonly label: string; readonly profileName: string | null; readonly steps: readonly WireStripStep[];
}
export interface CutDiagram {
  readonly objectId: string; readonly memberWireIds: readonly string[]; readonly title: string;
  readonly kind: "wire" | "cable"; readonly material: string | null;
  readonly sourceLengthMm: number | null; readonly correctionA: number; readonly correctionB: number;
  readonly roundingMm: number; readonly cutLengthMm: number | null; readonly unroundedMm: number | null;
  readonly quantity: number; readonly totalMetres: number | null;
  readonly a: CutDiagramEnd; readonly b: CutDiagramEnd;
  readonly sheath: {fromMm:number|null;toMm:number|null;totalMm:number|null;isComplete:boolean} | null;
  readonly conductors: readonly {id:string;a:CutDiagramEnd;b:CutDiagramEnd}[];
  readonly warnings: readonly string[];
}
const end = (d:HarnessDesignDocument,w:WireInstance,side:"from"|"to"):CutDiagramEnd => ({
  label:connectionEndLabel(d,w[side]),profileName:w.stripProfiles?.[side]?.displayName ?? null,
  steps:w.stripProfiles?.[side]?calculateWireStripSteps(w.stripProfiles[side]!.layers):[],
});
export function buildCutDiagram(document:HarnessDesignDocument,objectId:string,quantity:number):CutDiagram|null {
  if(!Number.isSafeInteger(quantity)||quantity<1)throw new Error("Количество должно быть положительным целым.");
  const cable=document.cables.find(c=>c.id===objectId),wire=document.wires.find(w=>w.id===objectId);
  const blank=cable ?? wire;if(!blank)return null;
  // Material cable is the blank. A conductor navigates to its parent's cut sheet.
  const parent=!cable&&document.cables.find(c=>c.memberWireIds.includes(objectId));
  if(parent)return buildCutDiagram(document,parent.id,quantity);
  const calculation=calculateWireCutLength(blank);
  const wires=cable?document.wires.filter(w=>cable.memberWireIds.includes(w.id)):wire?[wire]:[];
  const conductors=wires.map(w=>({id:w.id,a:end(document,w,"from"),b:end(document,w,"to")}));
  const a=wire?end(document,wire,"from"):{label:[...new Set(conductors.map(c=>c.a.label))].join(", "),profileName:null,steps:[]};
  const b=wire?end(document,wire,"to"):{label:[...new Set(conductors.map(c=>c.b.label))].join(", "),profileName:null,steps:[]};
  const warnings:string[]=[];
  if(calculation.cutLengthMm===null)warnings.push("Исходная физическая длина не задана.");
  if(!blank.materialBinding)warnings.push("Материал заготовки не закреплён.");
  const sheath=cable?calculateCableSheathStrip(cable):null;
  if(cable&&!sheath)warnings.push("Снятие общей оболочки не задано.");
  else if(sheath&&!sheath.isComplete)warnings.push("Снятие общей оболочки заполнено не полностью.");
  for(const c of conductors) {
    if(!c.a.steps.length||!c.b.steps.length)warnings.push(`${c.id}: профиль разделки ${!c.a.steps.length&&!c.b.steps.length?"A/B":!c.a.steps.length?"A":"B"} не задан.`);
    if(calculation.cutLengthMm!==null) {
      const stripped=Math.round((c.a.steps.at(-1)?.cumulativeLengthMm ?? 0)*1000)+Math.round((c.b.steps.at(-1)?.cumulativeLengthMm ?? 0)*1000);
      if(stripped>Math.round(calculation.cutLengthMm*1000))warnings.push(`${c.id}: сумма разделки A/B превышает длину заготовки.`);
    }
  }
  return {objectId:blank.id,memberWireIds:wires.map(w=>w.id),title:wire?wire.circuit||wire.id:cable!.id,kind:cable?"cable":"wire",material:blank.materialBinding?.displayName??null,
    sourceLengthMm:calculation.sourceLengthMm,correctionA:calculation.endCorrectionFromMm,correctionB:calculation.endCorrectionToMm,roundingMm:calculation.cutRoundingStepMm,
    cutLengthMm:calculation.cutLengthMm,unroundedMm:calculation.unroundedTotalMm,quantity,totalMetres:calculation.cutLengthMm===null?null:Number(BigInt(Math.round(calculation.cutLengthMm*1000))*BigInt(quantity))/1e6,
    a,b,sheath,conductors,warnings};
}
export function cutBlankIds(document:HarnessDesignDocument):string[] {
  const members=new Set(document.cables.flatMap(c=>c.memberWireIds));
  return [...document.wires.filter(w=>!members.has(w.id)).map(w=>w.id),...document.cables.map(c=>c.id)];
}
