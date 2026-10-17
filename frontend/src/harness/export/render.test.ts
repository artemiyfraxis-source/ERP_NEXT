import { describe, expect, it } from "vitest";
import type { HarnessExportPort } from "./port";
import { EXPORT_MARGIN, ExportError, frameOf, PRINT_MARGIN, pngSize, renderPng, renderSvg, SVG_ZOOM, svgDocument } from "./render";
import { buildSheetHtml, exportFileBase, NOT_TO_SCALE_NOTE, printSheet } from "./sheet-html";
import { placeOnSheet } from "./sheet";

/** Порт, который рисует то же, что поле чертежа: белый фон, линию и узор из плитки — и запоминает камеру. */
function fakePort(bounds: HarnessExportPort["bounds"], realSizeDenominator: number | null = null) {
  const seen: { zoom: number; offsetX: number; offsetY: number; ratio: number; width: number; height: number }[] = [];
  const port: HarnessExportPort = {
    view: "drawing", bounds, realSizeDenominator,
    paint: (target, camera, ratio) => {
      seen.push({ ...camera, ratio, width: target.clientWidth, height: target.clientHeight });
      const context = target.getContext("2d")!;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.fillStyle = "#ffffff"; context.fillRect(0, 0, target.clientWidth, target.clientHeight);
      context.save(); context.translate(camera.offsetX, camera.offsetY); context.scale(camera.zoom, camera.zoom);
      context.fillStyle = context.createPattern({ width: 8, height: 8, toDataURL: () => "data:image/png;base64,AAAA" } as unknown as CanvasImageSource, "repeat")!;
      context.beginPath(); context.rect(0, 0, 100, 40); context.fill();
      context.strokeStyle = "#000"; context.beginPath(); context.moveTo(0, 0); context.lineTo(100, 40); context.stroke();
      context.restore();
    },
  };
  return { port, seen };
}

describe("кадр выгрузки", () => {
  it("кадр = габариты чертежа плюс поле, пустой чертёж не выгружается", () => {
    expect(frameOf({ minX: 10, minY: 20, maxX: 110, maxY: 60 })).toEqual({ x: 10 - EXPORT_MARGIN, y: 20 - EXPORT_MARGIN, width: 100 + 2 * EXPORT_MARGIN, height: 40 + 2 * EXPORT_MARGIN });
    expect(() => frameOf(null)).toThrow(ExportError);
    expect(() => frameOf({ minX: 0, minY: 0, maxX: Number.NaN, maxY: 1 })).toThrow("нет объектов");
  });

  it("PNG слишком большого кадра отклоняется с советом взять меньшее разрешение", () => {
    const frame = frameOf({ minX: 0, minY: 0, maxX: 5000, maxY: 3000 });
    expect(pngSize(frame, 2)).toEqual({ width: frame.width * 2, height: frame.height * 2 });
    expect(() => pngSize(frame, 4)).toThrow(/PNG 4×.*Выберите 2×/);
    expect(() => pngSize(frameOf({ minX: 0, minY: 0, maxX: 20000, maxY: 100 }), 1)).toThrow(/Выгрузите его в PDF/);
  });
});

describe("SVG для печати", () => {
  const bounds = { minX: 50, minY: 10, maxX: 150, maxY: 50 };

  it("viewBox равен кадру: габариты чертежа плюс поле, в пикселях записи", () => {
    const { port, seen } = fakePort(bounds);
    const sheet = renderSvg(port);
    const frame = frameOf(bounds, PRINT_MARGIN);
    expect(sheet.frame).toEqual(frame);
    expect([sheet.width, sheet.height]).toEqual([Math.ceil(frame.width * SVG_ZOOM), Math.ceil(frame.height * SVG_ZOOM)]);
    expect(svgDocument(sheet)).toContain(`viewBox="0 0 ${sheet.width} ${sheet.height}"`);
    // Камера ставит левый верхний угол кадра в начало координат, пиксельное соотношение 1.
    expect(seen[0]).toMatchObject({ zoom: SVG_ZOOM, offsetX: -frame.x * SVG_ZOOM, offsetY: -frame.y * SVG_ZOOM, ratio: 1 });
  });

  it("нет внешних адресов: узоры и картинки только data:, ссылки только на свои определения", () => {
    const svg = svgDocument(renderSvg(fakePort(bounds).port));
    const hrefs = [...svg.matchAll(/href="([^"]*)"/g)].map((match) => match[1]!);
    expect(hrefs.length).toBeGreaterThan(0);
    expect(hrefs.every((href) => href.startsWith("data:") || href.startsWith("#"))).toBe(true);
    expect(svg.replace("http://www.w3.org/2000/svg", "")).not.toMatch(/https?:\/\//);
    for (const [, id] of svg.matchAll(/url\(#([^)]+)\)/g)) expect(svg).toContain(`id="${id}"`);
  });

  it("белый фон записан первым и закрывает весь кадр", () => {
    const sheet = renderSvg(fakePort(bounds).port);
    expect(sheet.markup).toMatch(/^<defs>.*<\/defs><path d="M0 0L\d+ 0L\d+ \d+L0 \d+Z" fill="#ffffff"/);
  });
});

describe("PNG", () => {
  it("рисует кадр с полем в разрешении 1×/2×/4×: камера 1:1, пиксельное соотношение = выбранное", async () => {
    const calls: unknown[][] = [];
    const real = document.createElement.bind(document);
    const canvas = real("canvas");
    (canvas as { toBlob: unknown }).toBlob = (done: (blob: Blob | null) => void) => done(new Blob(["png"], { type: "image/png" }));
    (canvas as { getContext: unknown }).getContext = () => ({ setTransform: (...args: unknown[]) => calls.push(args), fillRect() {}, save() {}, restore() {}, translate() {}, scale() {}, beginPath() {}, rect() {}, fill() {}, moveTo() {}, lineTo() {}, stroke() {}, createPattern: () => ({}) });
    const spy = (name: string) => name === "canvas" ? canvas : real(name);
    const original = document.createElement;
    document.createElement = spy as typeof document.createElement;
    try {
      const { port, seen } = fakePort({ minX: 0, minY: 0, maxX: 200, maxY: 100 });
      const blob = await renderPng(port, 4);
      expect(blob.type).toBe("image/png");
      const frame = frameOf({ minX: 0, minY: 0, maxX: 200, maxY: 100 });
      expect(seen[0]).toMatchObject({ zoom: 1, ratio: 4, width: frame.width, height: frame.height });
      expect(calls[0]).toEqual([4, 0, 0, 4, 0, 0]);
    } finally { document.createElement = original; }
  });
});

describe("печатная страница", () => {
  const data = { position: "КБ1 Жгут питания", project: "708К.4", version: 7, date: "10.10.2026", realSizeOn: true };
  const svg = renderSvg(fakePort({ minX: 0, minY: 0, maxX: 300, maxY: 150 }, 4).port);

  it("@page задаёт лист и альбомную ориентацию, без полей браузера", () => {
    for (const id of ["A4", "A3", "A2", "A1"] as const) {
      const placement = placeOnSheet({ width: svg.frame.width, height: svg.frame.height }, id, { kind: "fit" }, { denominator: 4 });
      if (!placement.ok) throw new Error("помещается");
      const html = buildSheetHtml({ svg, placement, data, title: "t" }).html;
      expect(html).toContain(`@page { size: ${id} landscape; margin: 0mm; }`);
      expect(html).toContain(`#sheet { width: ${placement.sheet.widthMm}mm;`);
    }
  });

  it("основная надпись: позиция, проект, версия, дата, масштаб и «длины не в масштабе»", () => {
    const placement = placeOnSheet({ width: svg.frame.width, height: svg.frame.height }, "A3", { kind: "true" }, { denominator: 4 });
    if (!placement.ok) throw new Error("помещается");
    const html = buildSheetHtml({ svg, placement, data, title: "708К.4-КБ1-в7" }).html;
    for (const text of ["КБ1 Жгут питания", "708К.4", ">7<", "10.10.2026", "М 1:4", NOT_TO_SCALE_NOTE]) expect(html).toContain(text);
    expect(html).toContain("<title>708К.4-КБ1-в7</title>");
    expect(html).toContain(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${svg.width} ${svg.height}"`);
    // Чертёж лежит на листе в миллиметрах: 1 мм на единицу чертежа.
    expect(html).toContain(`width="${svg.frame.width}mm" height="${svg.frame.height}mm"`);
  });

  it("без реальных размеров заметки о длинах нет, текст экранируется, несохранённая версия названа", () => {
    const placement = placeOnSheet({ width: svg.frame.width, height: svg.frame.height }, "A4", { kind: "fit" }, null);
    if (!placement.ok) throw new Error("помещается");
    const html = buildSheetHtml({ svg, placement, title: "t", data: { ...data, realSizeOn: false, version: null, position: "<b>КБ</b>" } }).html;
    expect(html).not.toContain(NOT_TO_SCALE_NOTE);
    expect(html).toContain("не сохранена");
    expect(html).toContain("&lt;b&gt;КБ&lt;/b&gt;");
    expect(html).toContain("по листу");
  });

  it("имя файла: проект-позиция-в<версия> без запрещённых символов", () => {
    expect(exportFileBase("708К.4", "КБ1", 7)).toBe("708К.4-КБ1-в7");
    expect(exportFileBase("П/1: «тест»", "КБ 1*", null)).toBe("П1_«тест»-КБ_1-в0");
  });

  it("без встроенных style-атрибутов (CSP страницы), правила уходят в окно печати через CSSOM", () => {
    const placement = placeOnSheet({ width: svg.frame.width, height: svg.frame.height }, "A3", { kind: "fit" }, { denominator: 4 });
    if (!placement.ok) throw new Error("помещается");
    const page = buildSheetHtml({ svg, placement, data, title: "t" });
    expect(page.html).not.toMatch(/\sstyle=/);
    const adopted: unknown[] = [];
    const written: string[] = [];
    class FakeSheet { text = ""; replaceSync(text: string) { this.text = text; } }
    const win = { CSSStyleSheet: FakeSheet, focus() {}, setTimeout() { return 0; },
      document: { open() {}, write: (html: string) => written.push(html), close() {}, adoptedStyleSheets: adopted } };
    const open = window.open;
    window.open = (() => win) as unknown as typeof window.open;
    try { expect(printSheet(page)).toBe(true); } finally { window.open = open; }
    expect(written).toEqual([page.html]);
    expect(win.document.adoptedStyleSheets).toHaveLength(1);
    expect((win.document.adoptedStyleSheets[0] as FakeSheet).text).toContain("@page { size: A3 landscape; margin: 0mm; }");
  });

  it("всплывающее окно заблокировано — печать сообщает false и ничего не падает", () => {
    const open = window.open;
    window.open = (() => null) as typeof window.open;
    try { expect(printSheet({ html: "<html></html>", css: "" })).toBe(false); } finally { window.open = open; }
  });
});
