import { describe, expect, it } from "vitest";
import { DEFECT_SCENES, defectArt, defectContext, defectSceneArt, defectSceneId } from "./etkDefectArt";

describe("defect picture library", () => {
  it("picks the picture by defect name and operation", () => {
    const cases: Array<[string, string, string]> = [
      ["Подрезка жил", "CUT_WIRE+1strip", "nick"],
      ["Вытягивание жил", "CUT_WIRE_auto", "pulled"],
      ["Распушение жил («ёрш»)", "SDR_USB_A_2.0", "birdcage"],
      ["Смятие (сплющивание) изоляции", "CUT_WIRE_auto", "insCrush"],
      ["Косой рез", "CUT_WIRE_hand", "skew"],
      ["Длина зачистки вне допуска", "CUT_WIRE+2strip", "stripLen"],
      ["Длина зачистки вне допуска", "CUT_COAX+1strip", "coaxSteps"],
      ["Изоляция в зоне обжатия жилы", "PRS_TERMINAL_auto", "crimpIns"],
      ["Отсутствие зажима изоляции", "PRS_TERMINAL_hand", "insGrip"],
      ["Эффект «банана»", "PRS_TERMINAL_multi", "banana"],
      ["Несоответствие высоты опрессовки или усилия на разрыв", "PRS_TERMINAL_auto", "crimpHeight"],
      ["Сползание наконечника (недопрессовка)", "PRS_НШВИ", "ferrule"],
      ["«Холодная» пайка", "SDR_DB", "solderCold"],
      ["Недолив припоя", "SDR_DB", "solderLack"],
      ["Недолив/раковины/пустоты", "MOLD_auto", "mold"],
      ["Затекание припоя", "SDR_XT60", "wicking"],
      ["Недопустимый вылет жил", "SDR_GX12", "gapIns"],
      ["Отсутствие или чрезмерный вылет жил", "PRS_TERMINAL_auto", "brush"],
      ["Короткое замыкание / Перемычка (Solder Bridge)", "SDR_USB_A_2.0", "bridge"],
      ["«Недоусадка»", "HEAT_TYT_long", "shrinkLoose"],
      ["Прожог ТУТ", "HEAT_TYT_short", "shrinkBurn"],
      ["Смещение ТУТ по длине", "HEAT_TYT_short", "shrinkShift"],
      ["Нечитаемый текст", "INS_MARK", "markText"],
      ["Неверная распиновка (Перепутывание)", "INS_terminal_2", "pinout"],
      ["Сломанный стопорный усик", "INS_terminal_2", "lance"],
      ["Неполная посадка (Недосыл)", "INS_terminal_1.5", "seat"],
      ["Чрезмерная раскрутка", "STRIP_F/FTP_ethernet", "pairs"],
      ["Деформация скрутки", "CUT_WIRE_auto", "unlay"],
      ["Пережатие изоляции стяжкой", "INS_стяжки", "tieTight"],
      ["Торцы оплётки не оплавлены, разлохмачены", "INS_BRAID", "fray"],
      ["Отслоение печатных площадок", "SDR_USB_C_2.0", "pad"],
    ];
    for (const [name, code, scene] of cases) expect([name, defectSceneId(name, "", code)]).toEqual([name, scene]);
  });

  it("falls back to the explanation, then to a warning sign", () => {
    expect(defectSceneId("Дефект А", "Припой затёк под изоляцию, затекание", "SDR_DB")).toBe("wicking");
    expect(defectSceneId("Что-то новое")).toBe("generic");
    expect(defectArt("Что-то новое")).toContain('data-scene="generic"');
  });

  it("draws a Норма | Брак pair for every scene", () => {
    expect(DEFECT_SCENES.length).toBeGreaterThan(60);
    const art = defectArt("Затекание припоя", "", "SDR_DB");
    expect(art).toMatch(/^<svg viewBox="0 0 240 86"/);
    expect(art).toContain("✓ Норма");
    expect(art).toContain("✗ Брак");
    expect(art).not.toMatch(/undefined|NaN/);
  });

  it("keeps every caption inside its panel", () => {
    // Ширина подписи оценивается как 0,58 кегля на символ — с запасом для кириллицы sans-serif.
    const clipped: string[] = [];
    for (const id of DEFECT_SCENES) {
      for (const panel of defectSceneArt(id).matchAll(/<svg x="[^"]+" y="1" [^>]*viewBox="([^"]+)">(.*?)<\/svg>/g)) {
        const [minX, , width] = panel[1].split(" ").map(Number);
        for (const label of panel[2].matchAll(/<text x="([^"]+)"[^>]*font-size="([^"]+)"[^>]*text-anchor="(\w+)"[^>]*>([^<]*)<\/text>/g)) {
          const [x, size, anchor, value] = [Number(label[1]), Number(label[2]), label[3], label[4]];
          const span = [...value].length * size * 0.58;
          const left = anchor === "start" ? x : anchor === "end" ? x - span : x - span / 2;
          if (left < minX - 0.5 || left + span > minX + width + 0.5) clipped.push(`${id}: ${value}`);
        }
      }
    }
    expect(clipped).toEqual([]);
  });

  it("reads the operation kind from the card code", () => {
    expect(defectContext("CUT_WIRE_auto")).toBe("cut");
    expect(defectContext("CUT_WIRE+1crimp")).toBe("crimp");
    expect(defectContext("CUT_COAX+2strip")).toBe("coax");
    expect(defectContext("PRS_НШВИ")).toBe("lug");
    expect(defectContext("SDR_XT60")).toBe("solder");
    expect(defectContext("HEAT_TTK_long")).toBe("shrink");
  });
});
