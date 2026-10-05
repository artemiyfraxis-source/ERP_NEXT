import {createMaterialLibraryApi,type GlobalMaterial} from "../material-library-api";
import {createCoveringAssetApi} from "./covering-assets";
import type {RuntimeConfig} from "../runtime-config";
import type {LocalSession} from "../local-session";
import {coveringKind,type PhysicalCovering} from "./physical-coverings";
import type {HarnessDesignDocument} from "./model";
import type {EditorCommand} from "./commands";
import type {CoveringTextureEntry} from "./covering-library";
import {applyCoveringPreference} from "./covering-library";
export function applyGlobalMaterial(cover:PhysicalCovering,material:GlobalMaterial,entry?:CoveringTextureEntry):PhysicalCovering {
 if(!material.hatchCode&&!entry)throw new Error("Не загружена текстура материала.");
 return {...cover,color:material.backgroundColor??cover.color,style:{...cover.style,texture:material.hatchCode?"none":`asset:${entry!.sha256}`,hatchCode:material.hatchCode??"",hatchLineWidth:material.hatchLineWidth??1,...(material.hatchCode?{hatch:"none" as const,hatchColor:material.tint,hatchRotation:material.textureAngle,hatchSpacing:material.textureScale*10}:{}),lineColor:material.lineColor,lineWidth:material.lineWidth,textureScale:material.textureScale,textureRotation:material.textureAngle,textureTint:material.tint}};
}
/** A deliberate type change resolves its new material just like placement.
 * Geometry-only edits never refresh an existing, pinned texture. */
export function coveringMaterialChanged(document:HarnessDesignDocument,cover:PhysicalCovering):boolean {
 const previous=document.physicalTopology?.coverings?.find(c=>c.id===cover.id);
 return !previous||coveringKind(previous)!==coveringKind(cover);
}
export async function prepareGlobalCoverings(document:HarnessDesignDocument,command:Extract<EditorCommand,{type:"set-physical-topology"}>,list:()=>Promise<GlobalMaterial[]>,pin:(material:GlobalMaterial)=>Promise<CoveringTextureEntry>):Promise<typeof command> {
 const changed=(command.topology.coverings??[]).filter(c=>coveringMaterialChanged(document,c));
 const replacements=new Map<string,PhysicalCovering>();
 for(const c of changed){
  const previous=document.physicalTopology?.coverings?.find(old=>old.id===c.id);
  if(previous)replacements.set(c.id,applyCoveringPreference({...c,style:{...c.style,texture:"auto",hatchCode:""}},document.drawingDocuments?.coveringLibrary));
 }
 const replace=()=>({...command,topology:{...command.topology,coverings:command.topology.coverings?.map(c=>replacements.get(c.id)??c)}});
 const added=changed.filter(c=>!document.drawingDocuments?.coveringLibrary?.defaults[coveringKind(c)]);
 if(!added.length)return replacements.size?replace():command;
 const materials=await list(),entries:CoveringTextureEntry[]=[];
 for(const c of added){const material=materials.find(m=>m.coveringKind===coveringKind(c));if(!material)continue;const entry=material.hatchCode?undefined:await pin(material);if(entry)entries.push(entry);replacements.set(c.id,applyGlobalMaterial(c,material,entry));}
 if(!entries.length)return replacements.size?replace():command;
 const library=document.drawingDocuments?.coveringLibrary??{textures:[],defaults:{}};
 return {...command,coveringLibrary:{...library,textures:[...new Map([...library.textures,...entries].map(e=>[e.sha256,e])).values()]},topology:{...command.topology,coverings:command.topology.coverings!.map(c=>replacements.get(c.id)??c)}};
}
export function createGlobalCoveringPreparer(config:RuntimeConfig,session:LocalSession,projectId:string){
 const library=createMaterialLibraryApi(config,session),assets=createCoveringAssetApi(config,session,projectId);
 return async(document:HarnessDesignDocument,command:Extract<EditorCommand,{type:"set-physical-topology"}>)=>{
  let existing:Awaited<ReturnType<typeof assets.list>>|undefined;const cache=new Map<string,CoveringTextureEntry>();
  return prepareGlobalCoverings(document,command,library.list,async m=>{
   const cached=cache.get(m.materialId);if(cached)return cached;
   const full=await library.get(m.materialId);
   if(full.revision!==m.revision)throw new Error("Материал изменился. Повторите добавление оболочки.");
   const bytes=Uint8Array.from(atob(full.imageBase64),c=>c.charCodeAt(0));
   const hash=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",bytes)),b=>b.toString(16).padStart(2,"0")).join("");
   existing??=await assets.list();
   const entry=existing.find(a=>a.entry.sha256===hash)?.entry??(await assets.upload(new File([bytes],"material.png",{type:"image/png"}))).entry;
   const named={...entry,name:m.name};cache.set(m.materialId,named);return named;
  });
 };
}
