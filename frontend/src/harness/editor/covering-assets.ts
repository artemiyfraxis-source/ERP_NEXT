import {useEffect,useState} from "react";
import type {CoveringTextureEntry} from "./covering-library";
import type {EditorSceneObject} from "./editor-types";
import type {CoveringKind} from "./physical-coverings";

export function withCoveringTextureUrls(objects:readonly EditorSceneObject[],urls:Readonly<Record<string,string>>):EditorSceneObject[] {
  return objects.map(o=>{if(o.kind!=="physical-covering")return o;const texture=JSON.parse(o.metadata?.coveringStyle??"{}").texture as string|undefined;
    return {...o,metadata:{...o.metadata,coveringTextureUrl:texture?.startsWith("asset:")?urls[texture.slice(6)]??"":""}};
  });
}

/** A shared covering material: either a hatch (hatchCode) or a texture image pinned into the project. */
export interface GlobalMaterial {
  readonly materialId:string; readonly name:string;
  readonly lineColor:string; readonly lineWidth:number; readonly textureAngle:number; readonly textureScale:number;
  readonly tint:string; readonly backgroundColor?:string; readonly hatchCode?:string|null; readonly hatchLineWidth?:number;
  readonly coveringKind:CoveringKind|null; readonly updatedUtc:string; readonly revision:number;
}

/**
 * Covering materials and project textures. Uploading photos/images is not part of the ERP port;
 * the adapter only lists what already exists and pins a material's texture into the project.
 */
export interface CoveringMaterialApi {
  listMaterials():Promise<readonly GlobalMaterial[]>;
  /** Returns the project texture entry for a texture-based material, pinning it when needed. */
  pinTexture(projectId:string,material:GlobalMaterial):Promise<CoveringTextureEntry>;
  listTextures(projectId:string):Promise<readonly {readonly entry:CoveringTextureEntry;readonly url:string}[]>;
}

export function createInMemoryCoveringMaterialApi(
  materials:readonly GlobalMaterial[]=[],
  textures:Readonly<Record<string,{readonly entry:CoveringTextureEntry;readonly url:string}>>={},
):CoveringMaterialApi {
  return {
    async listMaterials(){return materials;},
    async pinTexture(_projectId,material){
      const found=textures[material.materialId];
      if(!found)throw new Error("Не загружена текстура материала.");
      return {...found.entry,name:material.name};
    },
    async listTextures(){return Object.values(textures);},
  };
}

export function useCoveringAssets(api:CoveringMaterialApi,projectId:string,needed:boolean,version="") {
  const [urls,setUrls]=useState<Readonly<Record<string,string>>>({}),[error,setError]=useState("");
  useEffect(()=>{let cancelled=false;setUrls({});setError("");if(needed)void api.listTextures(projectId).then(items=>{if(!cancelled)setUrls(Object.fromEntries(items.map(a=>[a.entry.sha256,a.url])));}).catch(e=>{if(!cancelled)setError(e instanceof Error?e.message:String(e));});return()=>{cancelled=true;};},[api,projectId,needed,version]);
  return {urls,error};
}
