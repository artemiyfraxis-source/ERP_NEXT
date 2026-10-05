import {validCoveringTexture,type CoveringTexture} from "./covering-style";
import {validateCoveringMaterial,type CoveringKind,type CoveringMaterial,type PhysicalCovering} from "./physical-coverings";
export const coveringKinds:readonly (readonly [CoveringKind,string])[]=[["heat-shrink","Термоусадка"],["nylon","Нейлонка"],["braid","Оплётка"],["metal-braid","Металлическая плетёнка"],["tape","Обмотка"],["band","Нитевый бандаж"]];
export interface CoveringTextureEntry {readonly sha256:string;readonly name:string}
export interface CoveringPreference {readonly texture:CoveringTexture;readonly material?:CoveringMaterial}
export interface CoveringLibrary {readonly textures:readonly CoveringTextureEntry[];readonly defaults:Partial<Record<CoveringKind,CoveringPreference>>}
export const emptyCoveringLibrary=():CoveringLibrary=>({textures:[],defaults:{}});
export function validateCoveringLibrary(value:unknown):void {
  const fail=():never=>{throw new Error("Некорректные настройки материалов чертежа.");};
  if(!value||typeof value!=="object"||Array.isArray(value))return fail();
  const library=value as CoveringLibrary;
  if(!Array.isArray(library.textures)||library.textures.length>1000||!library.defaults||typeof library.defaults!=="object"||Array.isArray(library.defaults))return fail();
  const hashes=new Set<string>();
  for(const t of library.textures){if(!t||typeof t.sha256!=="string"||!/^([a-f0-9]{64})$/.test(t.sha256)||hashes.has(t.sha256)||typeof t.name!=="string"||!t.name.trim()||t.name.length>255)return fail();hashes.add(t.sha256);}
  for(const [kind,p] of Object.entries(library.defaults)){
    if(!coveringKinds.some(k=>k[0]===kind)||!p||typeof p!=="object"||!validCoveringTexture(p.texture)||p.texture.startsWith("asset:")&&!hashes.has(p.texture.slice(6)))return fail();
    if(p.material!==undefined)validateCoveringMaterial(p.material);
  }
}
/** Preferences affect newly placed materials; changing them does not rebind existing sleeves. */
export function applyCoveringPreference(cover:PhysicalCovering,library?:CoveringLibrary):PhysicalCovering {
  const p=library?.defaults[cover.kind??"braid"];return p?{...cover,style:{...cover.style,texture:p.texture},...(p.material?{material:p.material}:{} )}:cover;
}
export function textureChoices(library?:CoveringLibrary):readonly {value:CoveringTexture;label:string}[] {
  return [{value:"auto",label:"По типу оболочки"},{value:"none",label:"Без текстуры"},{value:"Rubber002",label:"Резина"},{value:"Fabric061",label:"Ткань"},{value:"Metal049A",label:"Металл"},...(library?.textures.map(t=>({value:`asset:${t.sha256}` as const,label:t.name}))??[])];
}
