// «Типовые дефекты» по СТО ФРАК-001-2026, ред. 2 (релиз 22.10, #643): разбор текста карточки и рисунок на каждый пункт.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { DEFECT_SCENES, defectArt, defectSceneArt, defectSceneId } from "./etkDefectArt";
import { parseDefects, STO_ROW } from "./etkOperation";
import { STO_SCENES } from "./etkStoScenes";

type Sto = { sections: StoNode[] };
type StoNode = { rows?: Array<{ code: string; defect: string }>; subsections?: StoNode[] };
type Template = { code: string; record: { defects: string } };

const root = `${process.cwd()}/..`;
const sto = JSON.parse(readFileSync(`${root}/backend/data/sto-frak-001-2026.json`, "utf8") as string) as Sto;
// Тексты после перезаписи 22.10 — снимок прода из репозитория (docs/data/operation-templates-2026-10-22).
const templates = JSON.parse(readFileSync(`${root}/docs/data/operation-templates-2026-10-22/templates-after.json`, "utf8") as string) as Template[];

function stoRows(node: StoNode): Array<{ code: string; defect: string }> {
  return [...(node.rows ?? []), ...(node.subsections ?? []).flatMap(stoRows)];
}
const rows = sto.sections.flatMap(stoRows);
const codesIn = (text: string) => text.split("\n").flatMap((line) => line.split(/;\s*/)).filter((part) => STO_ROW.test(part.trim())).length;

describe("parseDefects: текст по СТО ФРАК-001-2026", () => {
  const text = "ТИПОВЫЕ ДЕФЕКТЫ — по СТО ФРАК-001-2026, ред. 2, разд. 4.2 «Зачистка и разделка»:\n1. Критические — не допускаются:\n" +
    "4.2.13 Кабель, Ethernet, USB: надрезы и обрывы жил; 4.2.15 Кабель: оголение внутренней жилы.\n2. Значительные — не допускаются:\n" +
    "4.2.14 Кабель: повреждение изоляции внутренних жил без оголения.\n" +
    "Критерии, метод контроля и действие — по СТО ФРАК-001-2026, ред. 2; при расхождении приоритет у КД и ДИ изготовителя.";

  it("keeps the trailing «Критерии…» line out of the items and splits every STO row, colon inside the name included", () => {
    const parsed = parseDefects(text);
    expect(parsed.intro).toHaveLength(2);
    expect(parsed.intro[1]).toMatch(/^Критерии, метод контроля и действие/);
    expect(parsed.groups.map((group) => group.title)).toEqual(["Критические — не допускаются", "Значительные — не допускаются"]);
    expect(parsed.groups[0].items.map((item) => item.name)).toEqual([
      "4.2.13 Кабель, Ethernet, USB: надрезы и обрывы жил", "4.2.15 Кабель: оголение внутренней жилы",
    ]);
    expect(parsed.groups[1].items.map((item) => item.name)).toEqual(["4.2.14 Кабель: повреждение изоляции внутренних жил без оголения"]);
  });

  it("drops «Критерии, метод контроля…» in an old-format text too", () => {
    const parsed = parseDefects("Типовые дефекты (NG):\nНеверная распиновка: Порядок проводов не по чертежу.\nКритерии, метод контроля и действие — по СТО.");
    expect(parsed.groups.flatMap((group) => group.items.map((item) => item.name))).toEqual(["Неверная распиновка"]);
  });
});

describe("рисунки по строкам СТО ФРАК-001-2026, ред. 2", () => {
  it("has a picture for every one of the STO rows and no stray entries in the table", () => {
    expect(rows).toHaveLength(320);
    expect(Object.keys(STO_SCENES).sort()).toEqual(rows.map((row) => row.code).sort());
    const missing = rows.filter((row) => {
      const id = defectSceneId(`${row.code} ${row.defect}`);
      return id === "generic" || !DEFECT_SCENES.includes(id) || id !== STO_SCENES[row.code];
    });
    expect(missing.map((row) => row.code)).toEqual([]);
  });

  it("draws different «Норма» and «Брак» in every scene the table uses", () => {
    for (const id of new Set(Object.values(STO_SCENES))) {
      const panels = [...defectSceneArt(id).matchAll(/<svg x="[^"]+" y="1" [^>]*>(.*?)<\/svg>/g)].map((match) => match[1]);
      expect([id, panels.length]).toEqual([id, 2]);
      expect([id, panels[0] !== panels[1]]).toEqual([id, true]);
      expect(panels.join("")).not.toMatch(/undefined|NaN/);
    }
  });

  it("falls back to the word rules for a name without an STO code", () => {
    expect(defectSceneId("Косой рез", "", "CUT_WIRE_hand")).toBe("skew");
    expect(defectSceneId("4.9 Раздел без пункта")).toBe("generic");
  });
});

describe("тексты 111 шаблонов операций после 22.10", () => {
  it("parse into exactly one item per STO code, with no «Критерии…» garbage items", () => {
    expect(templates).toHaveLength(111);
    const wrong: string[] = [];
    for (const template of templates) {
      const text = template.record.defects;
      const items = parseDefects(text).groups.flatMap((group) => group.items);
      if (items.length !== codesIn(text) || items.some((item) => !STO_ROW.test(item.name))) wrong.push(`${template.code}: ${items.length}/${codesIn(text)}`);
    }
    expect(wrong).toEqual([]);
  });

  it("get a library picture for every item — no «!» sign", () => {
    let total = 0;
    const generic: string[] = [];
    for (const template of templates) {
      for (const group of parseDefects(template.record.defects).groups) {
        for (const item of group.items) {
          total += 1;
          if (defectArt(item.name, item.text, template.code).includes('data-scene="generic"')) generic.push(`${template.code}: ${item.name}`);
        }
      }
    }
    expect(total).toBe(1496);
    expect(generic).toEqual([]);
  });
});
