// «Типовые дефекты» по СТО ФРАК-001-2026 (ред. 2 — релиз 22.10, #643; ред. 3 — 23.10): разбор текста карточки и рисунок на каждый пункт.
// @ts-ignore — типы node в проекте не подключены, файлы читаются напрямую из репозитория
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { defectsDraft, serializeDefects } from "./etkDefectEdit";
import { DEFECT_SCENES, defectArt, defectSceneArt, defectSceneId } from "./etkDefectArt";
import { parseDefects, STO_ROW } from "./etkOperation";
import { STO_SCENES } from "./etkStoScenes";

declare const process: { cwd(): string };

type Sto = { sections: StoNode[] };
type StoNode = { rows?: Array<{ code: string; defect: string }>; subsections?: StoNode[] };
type Template = { code: string; record: { defects: string } };

const root = `${process.cwd()}/..`;
const sto = JSON.parse(readFileSync(`${root}/backend/data/sto-frak-001-2026.json`, "utf8") as string) as Sto;
// Тексты шаблонов после записи 23.10 (docs/data/operation-templates-2026-10-23/payload.json; 111 правленых + 10 новых кодов).
const templates = JSON.parse(readFileSync(`${root}/docs/data/operation-templates-2026-10-23/payload.json`, "utf8") as string) as Template[];

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

describe("рисунки по строкам СТО ФРАК-001-2026, ред. 3", () => {
  it("has a picture for every one of the STO rows and no stray entries in the table", () => {
    expect(rows).toHaveLength(351);
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

describe("тексты 121 шаблона операций после 23.10", () => {
  it("parse into exactly one item per STO code, with no «Критерии…» garbage items", () => {
    expect(templates).toHaveLength(121);
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
    expect(total).toBe(1617);
    expect(generic).toEqual([]);
  });
});

describe("форма правки: тексты по СТО ФРАК-001-2026 туда и обратно", () => {
  const trim = (text: string) => text.replace(/[ \t]+$/gm, "").trimEnd();

  it("saves every one of the 121 templates unchanged when nothing was edited", () => {
    const changed = templates.filter((template) => {
      const text = template.record.defects;
      return trim(serializeDefects(defectsDraft(text), text)) !== trim(text);
    });
    expect(changed.map((template) => template.code)).toEqual([]);
  });

  it("writes every template back from the form model alone in the same form — groups, «Критерии…» line and the colon in names included", () => {
    const changed = templates.filter((template) => {
      const draft = defectsDraft(template.record.defects);
      draft.groups.forEach((group) => { group.origin = null; });
      return trim(serializeDefects(draft, "")) !== trim(template.record.defects);
    });
    expect(changed.map((template) => template.code)).toEqual([]);
  });

  it("keeps the «4.2.13 Кабель, Ethernet, USB: надрезы…» form, the class groups and both service lines when a row is edited", () => {
    const original = templates.find((template) => template.code === "STRIP_CABLE")!.record.defects;
    const draft = defectsDraft(original);
    draft.groups[0].items[0].name = "4.2.13 Кабель, Ethernet, USB: надрезы, обрывы жил и экрана";
    draft.groups[1].items.push({ name: "4.2.99 Кабель: новый пункт", text: "" });
    const saved = serializeDefects(draft, original);
    const lines = saved.split("\n");
    expect(lines[0]).toBe(original.split("\n")[0]);
    expect(lines[1]).toBe("1. Критические — не допускаются:");
    expect(lines[2].startsWith("4.2.13 Кабель, Ethernet, USB: надрезы, обрывы жил и экрана; 4.2.15 Кабель: оголение внутренней жилы; ")).toBe(true);
    expect(lines[2].endsWith(".")).toBe(true);
    expect(lines[3]).toBe("2. Значительные — не допускаются:");
    expect(lines[4].endsWith("; 4.2.99 Кабель: новый пункт.")).toBe(true);
    expect(lines[5]).toBe(original.split("\n").at(-1));
    const reread = parseDefects(saved);
    expect(reread.intro).toHaveLength(2);
    expect(reread.groups.map((group) => group.items.map((item) => item.name))).toEqual(
      draft.groups.map((group) => group.items.map((item) => item.name)));
  });
});
