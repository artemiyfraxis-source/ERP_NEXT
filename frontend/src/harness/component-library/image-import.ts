import { MAXIMUM_TEMPLATE_ASSET_BYTES, MAXIMUM_TEMPLATE_IMAGE_DIMENSION, MAXIMUM_TEMPLATE_IMAGE_PIXELS, readTemplateAsset } from "./template-assets";
import { removeStrictWhiteBackground } from "./image-background";

export const IMAGE_IMPORT_ACCEPT=".png,.jpg,.jpeg,.bmp,.svg,image/png,image/jpeg,image/bmp,image/svg+xml";
/** Normalize locally; published templates continue to contain validated PNG only. */
export async function importDrawingImage(file:File, options: { removeWhiteBackground?: boolean } = {}) {
  if(file.size<=0 || file.size>MAXIMUM_TEMPLATE_ASSET_BYTES) throw new Error("Размер изображения должен быть от 1 байта до 10 МиБ.");
  const isPng = file.type === "image/png" || /\.png$/i.test(file.name);
  const isSvg = file.type === "image/svg+xml" || /\.svg$/i.test(file.name);
  const removeBackground = options.removeWhiteBackground === true && !isSvg;
  if(isPng && !removeBackground) return readTemplateAsset(new File([file],file.name,{type:"image/png"}));
  // Validate original PNG before decoding and re-encoding it.
  if(isPng) await readTemplateAsset(new File([file],file.name,{type:"image/png"}));
  let blob:Blob=file;
  if(!isPng && !/\.(jpe?g|bmp|svg)$/i.test(file.name) && !["image/jpeg","image/bmp","image/svg+xml"].includes(file.type)) throw new Error("Поддерживаются PNG, JPG, BMP и SVG.");
  if(/\.svg$/i.test(file.name) || file.type==="image/svg+xml") {
    const xml=new DOMParser().parseFromString(await file.text(),"image/svg+xml");
    if(xml.querySelector("parsererror") || xml.documentElement.localName!=="svg") throw new Error("SVG повреждён.");
    // Keep vector paint local: scripts, foreign content and remote resources are never rendered.
    for(const element of Array.from(xml.querySelectorAll("script,foreignObject,iframe,object,embed"))) element.remove();
    for(const element of Array.from(xml.querySelectorAll("*"))) for(const attribute of Array.from(element.attributes)) {
      if(attribute.name.startsWith("on") || /(?:href|src)$/.test(attribute.name) && !attribute.value.startsWith("#") || /url\(\s*['"]?(?!#)/i.test(attribute.value)) element.removeAttribute(attribute.name);
    }
    for(const style of Array.from(xml.querySelectorAll("style"))) if(/@import|url\(\s*['"]?(?!#)/i.test(style.textContent??"")) style.remove();
    blob=new Blob([new XMLSerializer().serializeToString(xml)],{type:"image/svg+xml"});
  }
  const url=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(new Error("Не удалось прочитать изображение."));reader.readAsDataURL(blob);});
  try {
    const image=new Image();image.src=url;await image.decode();
    const width=image.naturalWidth,height=image.naturalHeight;
    if(!width||!height||width>MAXIMUM_TEMPLATE_IMAGE_DIMENSION||height>MAXIMUM_TEMPLATE_IMAGE_DIMENSION||width*height>MAXIMUM_TEMPLATE_IMAGE_PIXELS||width*height*4>256*1024*1024) throw new Error("Изображение слишком большое.");
    const canvas=document.createElement("canvas");canvas.width=width;canvas.height=height;
    const context = canvas.getContext("2d")!;
    context.drawImage(image,0,0);
    if(removeBackground) {
      const pixels = context.getImageData(0,0,width,height);
      if(removeStrictWhiteBackground(pixels.data)) context.putImageData(pixels,0,0);
    }
    const png=await new Promise<Blob>((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):reject(new Error("Не удалось преобразовать изображение.")),"image/png"));
    return readTemplateAsset(new File([png],file.name.replace(/\.[^.]+$/,"")+".png",{type:"image/png"}));
  } finally { /* Data URLs contain only the local image and are not retained. */ }
}
