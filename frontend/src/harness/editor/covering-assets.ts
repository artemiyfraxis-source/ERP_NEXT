import {useEffect,useMemo,useState} from "react";
import {createMutationHeaders,type LocalSession} from "../local-session";
import {buildApiUrl,type RuntimeConfig} from "../runtime-config";
import type {CoveringTextureEntry} from "./covering-library";
import type {EditorSceneObject} from "./editor-types";
export function withCoveringTextureUrls(objects:readonly EditorSceneObject[],urls:Readonly<Record<string,string>>):EditorSceneObject[] {
  return objects.map(o=>{if(o.kind!=="physical-covering")return o;const texture=JSON.parse(o.metadata?.coveringStyle??"{}").texture as string|undefined;
    return {...o,metadata:{...o.metadata,coveringTextureUrl:texture?.startsWith("asset:")?urls[texture.slice(6)]??"":""}};
  });
}

interface TextureAttachment {attachmentId:string;sha256:string;fileName:string;mediaType:string;purpose:string}
export function createCoveringAssetApi(config:RuntimeConfig,session:LocalSession,projectId:string,fetcher:typeof fetch=fetch) {
  const path=`projects/${encodeURIComponent(projectId)}`;
  const request=async(route:string,init?:RequestInit)=>{
    const r=await fetcher(buildApiUrl(config,route),{credentials:"same-origin",cache:"no-store",...init});
    if(!r.ok)throw new Error(r.status===409?"Проект изменился. Повторите загрузку текстуры.":"Не удалось загрузить текстуру проекта.");
    return r.json();
  };
  const parse=(a:TextureAttachment)=>{
    if(!a||typeof a.attachmentId!=="string"||!/^([\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12})$/i.test(a.attachmentId)||typeof a.sha256!=="string"||!/^([a-f0-9]{64})$/.test(a.sha256)||a.mediaType!=="image/png"||a.purpose!=="covering-texture"||typeof a.fileName!=="string")throw new Error("Повреждена запись текстуры.");return a;
  };
  const url=(a:TextureAttachment)=>buildApiUrl(config,`${path}/attachments/${encodeURIComponent(a.attachmentId)}/content`);
  return {
    async list(){const data=await request(`${path}/attachments`);if(!Array.isArray(data.attachments))throw new Error("Повреждён список текстур.");return (data.attachments as TextureAttachment[]).filter(a=>a.purpose==="covering-texture"&&a.mediaType==="image/png").map(a=>({entry:{sha256:parse(a).sha256,name:a.fileName},url:url(a)}));},
    async upload(file:File){
      const {importDrawingImage}=await import("../component-library/image-import");
      const image=await importDrawingImage(file);
      const project=await request(path);if(!Number.isSafeInteger(project.revision)||project.revision<0)throw new Error("Повреждена версия проекта.");
      const result=await request(`${path}/attachments`,{method:"POST",headers:createMutationHeaders(session),body:JSON.stringify({commandId:crypto.randomUUID(),expectedRevision:project.revision,...image,purpose:"covering-texture"})});
      const a=parse(result.attachment);return {entry:{sha256:a.sha256,name:file.name},url:url(a)};
    },
  };
}
export function useCoveringAssets(config:RuntimeConfig,session:LocalSession,projectId:string,needed:boolean,version="") {
  const api=useMemo(()=>createCoveringAssetApi(config,session,projectId),[config,session,projectId]);
  const [urls,setUrls]=useState<Readonly<Record<string,string>>>({}),[error,setError]=useState("");
  useEffect(()=>{let cancelled=false;setUrls({});setError("");if(needed)void api.list().then(items=>{if(!cancelled)setUrls(Object.fromEntries(items.map(a=>[a.entry.sha256,a.url])));}).catch(e=>{if(!cancelled)setError(String(e.message));});return()=>{cancelled=true;};},[api,needed,version]);
  return {urls,error,upload:async(file:File):Promise<CoveringTextureEntry>=>{const value=await api.upload(file);setUrls(old=>({...old,[value.entry.sha256]:value.url}));return value.entry;}};
}
