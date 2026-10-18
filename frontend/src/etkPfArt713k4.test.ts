// @vitest-environment node
import { describe, expect, it } from "vitest";
import { etkPfArt } from "./etkPfArt";
import { etk713k4, INPUTS_713K4 } from "./etkPfArt713k4.fixture";

/*
 * Картинки ПФ 713К.4 КБ1 (прод, ревизия 17) на размер карточки схемы ПФ — владелец 18.10: «визуал последних ПФ тоже слабый».
 * Пайка С1…С4 — эскиз на каждый сросток, ТУТ на месте Б — ветви X1–X4 под одной трубкой, изделие — плоский обзор жгута.
 */
const { document, operations, byName } = etk713k4();
const art = (name: string) => {
  const group = byName(name);
  const result = etkPfArt({ group, operations: group.ops.map((position) => operations[position - 1]), inputs: INPUTS_713K4[name], document });
  return { svg: result?.main && "svg" in result.main ? result.main.svg : "", layers: result?.layers ?? "" };
};
const sizes = (svg: string) => [...svg.matchAll(/font-size="([\d.]+)"/g)].map((match) => Number(match[1]));

describe("ПФ 713К.4: картинки на карточке схемы ПФ", () => {
  it("«Пайка С1, С2»: два сростка рядом, по проводам своих цветов, трубка ТТК 3/1 · 10 мм и хвост 65 мм", () => {
    const { svg, layers } = art("Пайка С1, С2");
    expect(svg.match(/data-part="splice-tube"/g)).toHaveLength(2);
    expect(svg).toContain("С1 · 4 провода");
    expect(svg).toContain("С2 · 4 провода");
    expect(svg).toContain("ТТК 3/1 · 10 мм");
    expect(svg).toContain("хвост 65 мм");
    // Концы проводов подписаны разъёмами, куда они идут; С1 — красные провода, С2 — чёрные.
    ["X1", "X5", "X6"].forEach((name) => expect(svg).toContain(`>${name}<`));
    expect(svg).toContain('stroke="#d7322d"');
    expect(svg).toContain('stroke="#222"');
    expect(svg).not.toContain("сросток ×");
    expect(layers).toBe("");
    expect(Math.min(...sizes(svg))).toBeGreaterThanOrEqual(10);
  });

  it("«Пайка С3, С4»: по три провода — две жилы с X2, X3 и хвост, белые у С3 и синие у С4", () => {
    const { svg } = art("Пайка С3, С4");
    expect(svg.match(/data-part="splice-tube"/g)).toHaveLength(2);
    expect(svg).toContain("С3 · 3 провода");
    expect(svg).toContain("С4 · 3 провода");
    expect(svg).toContain(">X2<");
    expect(svg).toContain(">X3<");
    expect(svg).toContain(">хвост<");
  });

  it("ветви X1–X4 (монтаж в разъём, ТТК на ветви) не рисуются трубкой на жгуте — владелец 18.10: «там было всё отлично»", () => {
    ["Ветвь X1 «5», сторона А", "Ветвь X2 «I3», сторона А", "Ветвь X3 «C», сторона А", "Ветвь X4 «A6», сторона А"].forEach((name) => {
      expect(art(name).svg).not.toContain('data-part="harness-tube"');
    });
  });

  it("«ТУТ на месте Б»: ветви X1–X4 сходятся под одну трубку ТТК 12/4 · 30 мм, справа X5 и X6", () => {
    const { svg, layers } = art("ТУТ на месте Б");
    expect(svg.match(/data-part="harness-tube"/g)).toHaveLength(1);
    ["X1", "X2", "X3", "X4", "X5", "X6"].forEach((name) => expect(svg).toContain(`>${name}<`));
    expect(svg).toContain(">ТТК 12/4<");
    expect(svg).toContain(">30 мм<");
    expect(svg).not.toContain("Термоусадка");
    // Трубка уже на рисунке — полосой слоёв под ним не повторяется.
    expect(layers).toBe("");
    expect(Math.min(...sizes(svg))).toBeGreaterThanOrEqual(10);
  });

  it("изделие: плоский обзор — разъёмы X1–X6, сростки С1–С4 под трубками, зона под ТТК 12/4, оплётка и легенда, без 3D", () => {
    const { svg } = art("Кабельная сборка");
    expect(svg.match(/data-part="connector"/g)).toHaveLength(6);
    expect(svg.match(/data-part="splice-tube"/g)).toHaveLength(4);
    expect(svg.match(/data-part="harness-tube"/g)).toHaveLength(1);
    ["X1", "X2", "X3", "X4", "X5", "X6", "С1", "С2", "С3", "С4"].forEach((name) => expect(svg).toContain(`>${name}<`));
    expect(svg).toContain("ТТК 12/4 · 30 мм");
    expect(svg).toContain("оплётка XP-3 · 110 мм на ветвях");
    expect(svg).toContain("ТТК 3/1 · 10 мм на сростках");
    expect(svg).not.toContain("<image");
    expect(Math.min(...sizes(svg))).toBeGreaterThanOrEqual(10);
  });
});
