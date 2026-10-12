import type { CoveringKind } from "./physical-coverings";
import {validHatchCode} from "../hatching";

export const coveringTextureOptions = ["auto", "none", "Rubber002", "Fabric061", "Metal049A"] as const;
export const coveringHatchOptions = ["none", "parallel", "cross", "dots"] as const;
export type CoveringTexture = typeof coveringTextureOptions[number] | `asset:${string}`;
export const validCoveringTexture=(value:unknown):value is CoveringTexture=>typeof value==="string"&&(coveringTextureOptions.includes(value as typeof coveringTextureOptions[number])||/^asset:[a-f0-9]{64}$/.test(value));
export interface CoveringStyle {
  readonly hatchCode?: string;
  readonly hatchLineWidth?: number;
  readonly texture?: CoveringTexture;
  readonly textureScale?: number;
  readonly textureRotation?: number;
  readonly hatch?: typeof coveringHatchOptions[number];
  readonly hatchColor?: string;
  readonly hatchSpacing?: number;
  readonly hatchRotation?: number;
  readonly lineColor?: string;
  readonly lineWidth?: number;
  readonly textureTint?: string;
}
export const defaultCoveringStyle: Required<CoveringStyle> = {
  hatchCode:"", hatchLineWidth:1,
  texture:"auto", textureScale:1, textureRotation:0, hatch:"none",
  hatchColor:"#34434e", hatchSpacing:8, hatchRotation:45, lineColor:"#34434e", lineWidth:1, textureTint:"#ffffff",
};
export const resolvedCoveringStyle=(style?:CoveringStyle):Required<CoveringStyle>=>({...defaultCoveringStyle,...style,
  // Legacy repeat tiles had a two-unit stroke scaled by spacing / 32.
  hatchLineWidth:style?.hatchLineWidth??(style?.hatch&&style.hatch!=="none"?(style.hatchSpacing??8)/(style.hatch==="dots"?8:16):1),
});
const files:Record<CoveringKind,string>={"heat-shrink":"Rubber002",nylon:"Fabric061",braid:"Fabric061","metal-braid":"Metal049A",tape:"Rubber002",band:"Fabric061"};
export function coveringTextureFile(kind:CoveringKind,style?:CoveringStyle):string|null {
  const value=style?.texture??"auto";return value==="none"?null:value==="auto"?files[kind]:value;
}
export function validCoveringStyle(value:unknown):value is CoveringStyle {
  if(!value||typeof value!=="object"||Array.isArray(value))return false;
  const s=value as CoveringStyle;
  if(s.hatchCode!==undefined&&!validHatchCode(s.hatchCode))return false;
  if(s.hatchLineWidth!==undefined&&(!Number.isFinite(s.hatchLineWidth)||s.hatchLineWidth<.1||s.hatchLineWidth>20))return false;
  if(s.texture!==undefined&&!validCoveringTexture(s.texture)||s.hatch!==undefined&&!coveringHatchOptions.includes(s.hatch))return false;
  for(const key of ["hatchColor","lineColor","textureTint"] as const)if(s[key]!==undefined&&(typeof s[key]!=="string"||!/^#[\da-f]{6}$/i.test(s[key])))return false;
  return ([['textureScale',.1,10],['textureRotation',-180,180],['hatchSpacing',1,100],['hatchRotation',-180,180]] as const)
    .every(([key,min,max])=>s[key]===undefined||typeof s[key]==="number"&&Number.isFinite(s[key])&&s[key]>=min&&s[key]<=max) &&
    (s.lineWidth===undefined || typeof s.lineWidth==="number" && Number.isFinite(s.lineWidth) && s.lineWidth>=.1 && s.lineWidth<=20);
}
