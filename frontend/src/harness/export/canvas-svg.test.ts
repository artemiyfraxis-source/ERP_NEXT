import { describe, expect, it } from "vitest";
import { SvgRecordingContext } from "./canvas-svg";

const externalHrefs = (svg: string) => [...svg.matchAll(/(?:xlink:)?href="([^"]*)"/g)].map((match) => match[1]!).filter((href) => !href.startsWith("data:") && !href.startsWith("#"));
const tile = (width = 8, height = 8) => ({ width, height, toDataURL: () => "data:image/png;base64,AAAA" });

describe("запись рисования в SVG", () => {
  it("линия: контур в координатах после преобразования, ширина пера и пунктир в масштабе", () => {
    const context = new SvgRecordingContext();
    context.save();
    context.translate(10, 20); context.scale(2, 2);
    context.strokeStyle = "#112233"; context.lineWidth = 3; context.lineCap = "round"; context.setLineDash([4, 2]);
    context.beginPath(); context.moveTo(0, 0); context.lineTo(5, 0); context.stroke();
    context.restore();
    const svg = context.markup();
    expect(svg).toContain('d="M10 20L20 20"');
    expect(svg).toContain('stroke="#112233"');
    expect(svg).toContain('stroke-width="6"');
    expect(svg).toContain('stroke-linecap="round"');
    expect(svg).toContain('stroke-dasharray="8 4"');
  });

  it("save/restore возвращает перо и преобразование, прозрачность идёт в opacity", () => {
    const context = new SvgRecordingContext();
    context.fillStyle = "red";
    context.save(); context.translate(100, 0); context.fillStyle = "blue"; context.globalAlpha = 0.5;
    context.fillRect(0, 0, 1, 1);
    context.restore();
    context.fillRect(0, 0, 1, 1);
    const svg = context.markup();
    expect(svg).toContain('d="M100 0L101 0L101 1L100 1Z" fill="blue" opacity="0.5"');
    expect(svg).toContain('d="M0 0L1 0L1 1L0 1Z" fill="red"/>');
  });

  it("обрезка живёт до restore: группы закрыты, <g> и </g> парные", () => {
    const context = new SvgRecordingContext();
    context.save(); context.beginPath(); context.rect(0, 0, 10, 10); context.clip();
    context.save(); context.beginPath(); context.arc(5, 5, 3, 0, Math.PI * 2); context.clip();
    context.fillRect(0, 0, 10, 10);
    context.restore(); context.restore();
    context.save(); context.beginPath(); context.rect(0, 0, 1, 1); context.clip(); // не закрыта — закроет markup()
    const svg = context.markup();
    expect(svg.match(/<g /g)).toHaveLength(3);
    expect(svg.match(/<\/g>/g)).toHaveLength(3);
    expect(svg).toContain("<clipPath");
  });

  it("дуга: полный круг собирается из кривых, круг начинается на оси; arcTo закругляет угол в нужную сторону", () => {
    const circle = new SvgRecordingContext();
    circle.beginPath(); circle.arc(0, 0, 10, 0, Math.PI * 2); circle.fill();
    expect(circle.markup().match(/C/g)).toHaveLength(4);
    expect(circle.markup()).toContain("M10 0");

    const corner = new SvgRecordingContext();
    corner.beginPath(); corner.moveTo(0, 0); corner.arcTo(10, 0, 10, 10, 5); corner.stroke();
    // касательные точки скругления: (5,0) и (10,5), дуга по часовой — центр (5,5)
    const path = /d="([^"]+)"/.exec(corner.markup())![1]!;
    expect(path).toContain("L5 0");
    expect(path.endsWith("10 5")).toBe(true);
  });

  it("текст: позиция, начертание, выравнивание и экранирование", () => {
    const context = new SvgRecordingContext();
    context.font = 'bold 12px "Segoe UI"'; context.textAlign = "center"; context.textBaseline = "middle"; context.fillStyle = "#000";
    context.translate(3, 4);
    context.fillText("A<B & \"C\"", 1, 2);
    const svg = context.markup();
    expect(svg).toContain('x="1" y="2" transform="matrix(1 0 0 1 3 4)"');
    expect(svg).toContain('font-family="&quot;Segoe UI&quot;" font-size="12" font-weight="bold"');
    expect(svg).not.toContain("style="); // CSP страницы запрещает встроенные стили
    expect(svg).toContain('text-anchor="middle"');
    expect(svg).toContain('dominant-baseline="central"');
    expect(svg).toContain(">A&lt;B &amp; &quot;C&quot;</text>");
  });

  it("узор-плитка и картинка уходят в SVG как data:-адреса, внешних ссылок нет", () => {
    const context = new SvgRecordingContext();
    const pattern = context.createPattern(tile(16, 16));
    pattern.setTransform({ a: 2, b: 0, c: 0, d: 2, e: 0, f: 0 });
    context.fillStyle = pattern;
    context.translate(5, 5);
    context.fillRect(0, 0, 20, 20);
    context.drawImage(tile(10, 10), 1, 1, 4, 4);
    const gradient = context.createLinearGradient(0, 0, 10, 0);
    gradient.addColorStop(0, "#fff"); gradient.addColorStop(1, "#000");
    context.fillStyle = gradient; context.fillRect(0, 0, 5, 5);
    const svg = context.markup();
    expect(svg).toContain('<pattern id="p');
    expect(svg).toContain('patternTransform="matrix(2 0 0 2 5 5)"');
    expect(svg).toContain('<image href="data:image/png;base64,AAAA" x="1"');
    expect(svg).toContain("<linearGradient");
    expect(externalHrefs(svg)).toEqual([]);
    expect(svg.match(/fill="url\(#/g)).toHaveLength(2);
  });

  it("плитку, которую нельзя прочитать, не выдаёт за заливку", () => {
    const context = new SvgRecordingContext();
    context.fillStyle = context.createPattern({ width: 4, height: 4, toDataURL: () => { throw new Error("tainted"); } });
    context.fillRect(0, 0, 1, 1);
    expect(context.markup()).toContain('fill="none"');
  });

  it("getTransform отдаёт текущую матрицу (она нужна расчёту толщины линий)", () => {
    const context = new SvgRecordingContext();
    context.scale(3, 3); context.translate(1, 2);
    expect(context.getTransform()).toMatchObject({ a: 3, d: 3, e: 3, f: 6 });
  });
});
