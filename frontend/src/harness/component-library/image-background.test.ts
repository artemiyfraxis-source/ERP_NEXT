import { expect, it, vi, afterEach } from "vitest";
import { removeStrictWhiteBackground } from "./image-background";
import { importDrawingImage } from "./image-import";
import { readTemplateAsset } from "./template-assets";
vi.mock("./template-assets", () => ({
  MAXIMUM_TEMPLATE_ASSET_BYTES: 10*1024*1024, MAXIMUM_TEMPLATE_IMAGE_DIMENSION: 16384,
  MAXIMUM_TEMPLATE_IMAGE_PIXELS: 100000000,
  readTemplateAsset: vi.fn(async (file: File) => ({ fileName: file.name, mediaType: file.type, contentBase64: "png" })),
}));
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

it("removes only exact opaque white without touching near-white, colors or partial transparency", () => {
  const pixels = new Uint8ClampedArray([255,255,255,255, 254,255,255,255, 255,255,255,127, 0,0,0,255, 255,255,255,0]);
  expect(removeStrictWhiteBackground(pixels)).toBe(1);
  expect([...pixels]).toEqual([255,255,255,0, 254,255,255,255, 255,255,255,127, 0,0,0,255, 255,255,255,0]);
  expect(removeStrictWhiteBackground(pixels)).toBe(0);
});

it.each(["image/png", "image/jpeg", "image/bmp"])("normalizes library %s white to transparent PNG", async type => {
  const pixels = { data: new Uint8ClampedArray([255,255,255,255]) };
  const context = { drawImage: vi.fn(), getImageData: vi.fn(() => pixels), putImageData: vi.fn() };
  vi.stubGlobal("FileReader", class { result = "data:image/png;base64,test"; onload?: () => void; readAsDataURL() { this.onload?.(); } });
  vi.stubGlobal("Image", class { naturalWidth = 1; naturalHeight = 1; async decode() {} });
  vi.stubGlobal("document", { createElement: () => ({ getContext: () => context, toBlob: (cb: (blob: Blob) => void) => cb(new Blob(["png"])) }) });
  await importDrawingImage(new File(["test"], "drawing." + type.split("/")[1], { type }), { removeWhiteBackground: true });
  expect(pixels.data[3]).toBe(0);
  expect(context.putImageData).toHaveBeenCalledWith(pixels, 0, 0);
  expect(readTemplateAsset).toHaveBeenLastCalledWith(expect.objectContaining({ type: "image/png" }));
});

it("preserves the original PNG for material textures", async () => {
  const file = new File(["white texture"], "texture.png", { type: "image/png" });
  await importDrawingImage(file);
  expect(readTemplateAsset).toHaveBeenCalledTimes(1);
});

it("preserves explicit SVG fills even with library background removal enabled", async () => {
  const getImageData = vi.fn();
  vi.stubGlobal("DOMParser", class { parseFromString() { return { querySelector: () => null, documentElement: { localName: "svg" }, querySelectorAll: () => [] }; } });
  vi.stubGlobal("XMLSerializer", class { serializeToString() { return '<svg><rect fill="white" /></svg>'; } });
  vi.stubGlobal("FileReader", class { result = "data:image/svg+xml,test"; onload?: () => void; readAsDataURL() { this.onload?.(); } });
  vi.stubGlobal("Image", class { naturalWidth = 1; naturalHeight = 1; async decode() {} });
  vi.stubGlobal("document", { createElement: () => ({ getContext: () => ({ drawImage: vi.fn(), getImageData }), toBlob: (cb: (blob: Blob) => void) => cb(new Blob(["png"])) }) });
  await importDrawingImage(new File(["<svg/>"], "drawing.svg", { type: "image/svg+xml" }), { removeWhiteBackground: true });
  expect(getImageData).not.toHaveBeenCalled();
});
