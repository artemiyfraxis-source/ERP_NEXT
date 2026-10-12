import type { TemplateAsset } from "./component-template-api";

export const MAXIMUM_TEMPLATE_ASSET_BYTES = 10 * 1024 * 1024;
export const MAXIMUM_TEMPLATE_IMAGE_DIMENSION = 16_384;
export const MAXIMUM_TEMPLATE_IMAGE_PIXELS = 100_000_000;
export const MAXIMUM_TEMPLATE_IMAGE_DECODED_BYTES = 256 * 1024 * 1024;
export const TEMPLATE_ASSET_MEDIA_TYPES: readonly TemplateAsset["mediaType"][] = [
  "image/png",
];

export function isTemplateAssetMediaType(value: string): value is TemplateAsset["mediaType"] {
  return TEMPLATE_ASSET_MEDIA_TYPES.includes(value as TemplateAsset["mediaType"]);
}

export function bytesToBase64(bytes: Uint8Array): string {
  const chunkSize = 0x8000;
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
}

export async function readTemplateAsset(file: File): Promise<{
  fileName: string;
  mediaType: TemplateAsset["mediaType"];
  contentBase64: string;
}> {
  if (!isTemplateAssetMediaType(file.type)) {
    throw new Error("Поддерживаются изображения PNG.");
  }
  if (file.size <= 0 || file.size > MAXIMUM_TEMPLATE_ASSET_BYTES) {
    throw new Error("Размер изображения должен быть от 1 байта до 10 МиБ.");
  }
  const fileName = file.name.trim();
  if (!fileName || fileName.length > 255 || /[\\/:\u0000-\u001f]/.test(fileName)) {
    throw new Error("Имя PNG должно быть непустым, не длиннее 255 символов и без пути.");
  }
  const bytes = new Uint8Array(await file.arrayBuffer());
  await validateTemplatePng(bytes);
  return {
    fileName,
    mediaType: file.type,
    contentBase64: bytesToBase64(bytes),
  };
}

export async function validateTemplatePng(bytes: Uint8Array): Promise<void> {
  const invalid = () => new Error("PNG повреждён, имеет неподдерживаемую структуру или слишком большой размер.");
  const signature = [137, 80, 78, 71, 13, 10, 26, 10];
  if (bytes.length < 45 || signature.some((value, index) => bytes[index] !== value)) throw invalid();

  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let offset = 8, width = 0, height = 0, bitDepth = 0, colorType = -1;
  let seenHeader = false, seenPalette = false, seenImageData = false, endedImageData = false, seenEnd = false;
  const compressedChunks: Uint8Array[] = [];
  let compressedLength = 0;

  while (offset < bytes.length) {
    if (bytes.length - offset < 12) throw invalid();
    const length = view.getUint32(offset, false);
    if (length > bytes.length - offset - 12) throw invalid();
    const type = bytes.subarray(offset + 4, offset + 8);
    if (![...type].every(isPngChunkTypeByte) || (type[2]! & 0x20) !== 0) throw invalid();
    const data = bytes.subarray(offset + 8, offset + 8 + length);
    if (pngCrc(type, data) !== view.getUint32(offset + 8 + length, false)) throw invalid();
    const name = String.fromCharCode(...type);

    if (!seenHeader) {
      if (name !== "IHDR" || length !== 13) throw invalid();
      width = new DataView(data.buffer, data.byteOffset, data.byteLength).getUint32(0, false);
      height = new DataView(data.buffer, data.byteOffset, data.byteLength).getUint32(4, false);
      bitDepth = data[8]!; colorType = data[9]!;
      const validDepth = colorType === 0 ? [1, 2, 4, 8, 16].includes(bitDepth)
        : colorType === 2 ? [8, 16].includes(bitDepth)
          : colorType === 3 ? [1, 2, 4, 8].includes(bitDepth)
            : colorType === 4 || colorType === 6 ? [8, 16].includes(bitDepth) : false;
      if (!validDepth || data[10] !== 0 || data[11] !== 0 || data[12] !== 0) throw invalid();
      validateTemplateImageDimensions(width, height, invalid);
      seenHeader = true;
    } else if (name === "IHDR") throw invalid();

    const critical = (type[0]! & 0x20) === 0;
    if (critical && !["IHDR", "PLTE", "IDAT", "IEND"].includes(name)) throw invalid();
    if (name === "PLTE") {
      if (seenPalette || seenImageData || colorType === 0 || colorType === 4 || length < 3 || length > 768 || length % 3 !== 0 || colorType === 3 && length / 3 > 2 ** bitDepth) throw invalid();
      seenPalette = true;
    } else if (name === "IDAT") {
      if (endedImageData || length === 0) throw invalid();
      seenImageData = true; compressedChunks.push(data); compressedLength += data.length;
    } else if (seenImageData && name !== "IEND") endedImageData = true;

    offset += length + 12;
    if (name === "IEND") {
      if (length !== 0 || !seenImageData || colorType === 3 && !seenPalette) throw invalid();
      seenEnd = true; break;
    }
  }
  if (!seenHeader || !seenEnd || offset !== bytes.length) throw invalid();

  const channels = colorType === 0 || colorType === 3 ? 1 : colorType === 2 ? 3 : colorType === 4 ? 2 : 4;
  const rowBytes = Math.ceil(width * channels * bitDepth / 8);
  const expectedBytes = (rowBytes + 1) * height;
  if (rowBytes <= 0 || expectedBytes > MAXIMUM_TEMPLATE_IMAGE_DECODED_BYTES) throw invalid();
  const compressed = new Uint8Array(compressedLength);
  let targetOffset = 0;
  for (const chunk of compressedChunks) { compressed.set(chunk, targetOffset); targetOffset += chunk.length; }
  try {
    const reader = new Blob([compressed]).stream().pipeThrough(new DecompressionStream("deflate")).getReader();
    let decodedOffset = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (decodedOffset + value.length > expectedBytes) throw invalid();
      for (let index = 0; index < value.length; index++) {
        if ((decodedOffset + index) % (rowBytes + 1) === 0 && value[index]! > 4) throw invalid();
      }
      decodedOffset += value.length;
    }
    if (decodedOffset !== expectedBytes) throw invalid();
  } catch {
    throw invalid();
  }
}

function validateTemplateImageDimensions(width: number, height: number, invalid: () => Error) {
  if (width <= 0 || height <= 0 || width > MAXIMUM_TEMPLATE_IMAGE_DIMENSION || height > MAXIMUM_TEMPLATE_IMAGE_DIMENSION || width * height > MAXIMUM_TEMPLATE_IMAGE_PIXELS) throw invalid();
}

function isPngChunkTypeByte(value: number) {
  return value >= 65 && value <= 90 || value >= 97 && value <= 122;
}

function pngCrc(type: Uint8Array, data: Uint8Array): number {
  let crc = 0xffffffff;
  for (const bytes of [type, data]) for (const value of bytes) {
    crc ^= value;
    for (let bit = 0; bit < 8; bit++) crc = (crc & 1) !== 0 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1;
  }
  return (~crc) >>> 0;
}
