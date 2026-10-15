import { describe, expect, it } from "vitest";
import { DEFECT_SCENES, defectArt, defectContext, defectGroupCode, defectOperationCode, defectSceneArt, defectSceneId } from "./etkDefectArt";
import markingCatalog from "../../backend/data/etk-marking.json";

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

  it("draws the coax pin soldering defects on the pin itself (SDR_COAX_pin)", () => {
    const cases: Array<[string, string]> = [
      ["«Холодная» пайка / Непрогрев", "cxCold"],
      ["Недостаток припоя", "cxLack"],
      ["Избыток припоя / Наплывы", "cxExcess"],
      ["Нарушение фиксации", "cxShift"],
      ["Оплавление диэлектрика", "cxMelt"],
      ["Нарушение изоляционного зазора", "cxGap"],
      ["Деформация контакта", "cxBent"],
      ["Выступание проволочек", "cxStrays"],
    ];
    for (const [name, scene] of cases) expect([name, defectSceneId(name, "", "SDR_COAX_pin")]).toEqual([name, scene]);
    expect(defectContext("SDR_COAX_pin_R/A")).toBe("solder");
    expect(defectSceneId("Оплавление диэлектрика", "", "SDR_DB")).toBe("connMelt");
  });

  it("draws the tinning defects on the tinned wire end itself (SDR_tin), not in a contact", () => {
    const cases: Array<[string, string]> = [
      ["Избыток припоя", "tnExcess"],
      ["Недостаток припоя (Непропай)", "tnLack"],
      ["Матовая, зернистая поверхность", "tnCold"],
      ["Оплавление / Обугливание изоляции", "tnMelt"],
      ["Затекание припоя (Wicking)", "tnWick"],
      ["Распушение", "tnSplay"],
    ];
    for (const [name, scene] of cases) expect([name, defectSceneId(name, "", "SDR_tin")]).toEqual([name, scene]);
    expect(defectContext("SDR_tin")).toBe("tin");
    // Лужение внутри резки с обжимом (CUT_WIRE+1crimp+tin) рисуется как лужение, опрессовка — как опрессовка.
    expect(defectGroupCode("CUT_WIRE+1crimp+tin", "Лужение — дефекты покрытия (смачиваемости)")).toBe("SDR_tin");
    expect(defectGroupCode("CUT_WIRE+1crimp+tin", "Опрессовка — нарушение геометрии зоны обжатия жилы")).toBe("CUT_WIRE+1crimp+tin");
    expect(defectSceneId("Затекание припоя (Wicking)", "", "SDR_DB")).toBe("wicking");
  });

  it("draws every marking defect by the mark kind of its group, never the warning sign", () => {
    // Дефекты «Маркировки» по видам меток собирает backend (etk_marking) из этого каталога; рисунок — по группе.
    for (const kind of markingCatalog.types) {
      const code = defectGroupCode("INS_MARK", kind.title);
      for (const [name, cause] of kind.defects) {
        expect([kind.title, name, defectSceneId(name, cause, code)]).not.toEqual([kind.title, name, "generic"]);
        expect(defectArt(name, cause, code)).not.toContain('data-scene="generic"');
      }
    }
    const scene = (title: string, name: string) => defectSceneId(name, "", defectGroupCode("INS_MARK", title));
    expect(scene("Хлястик", "Хлястик плохо закреплён, болтается")).toBe("flagLoose");
    expect(scene("Хлястик", "Текст хлястика не соответствует КД")).toBe("flagText");
    expect(scene("Термоусадочная трубка", "Неверная длина отрезка трубки")).toBe("tubeLen");
    expect(scene("Термоусадочная трубка", "Трубка не усажена или усажена не полностью")).toBe("tubeLoose");
    expect(scene("ПВХ-трубка", "Трубка сползает с провода")).toBe("tubeSlide");
    expect(scene("Бирка", "Бирка не в позиции по КД")).toBe("tagShift");
    expect(scene("Маркировочные кольца", "Пропущено кольцо")).toBe("ringMissing");
    // Хлястик и трубка — разные рисунки одного и того же дефекта.
    expect(scene("Хлястик", "Текст хлястика нечитаем или смазан")).not.toBe(scene("Термоусадочная трубка", "Текст на трубке нечитаем"));
    // Группа вида — только в маркировке; «Прочие метки» и чужие операции — по коду операции.
    expect(defectGroupCode("INS_MARK", "Прочие метки")).toBe("INS_MARK");
    expect(defectGroupCode("SDR_DB", "Хлястик")).toBe("SDR_DB");
    expect(defectContext("MARK:markFlag")).toBe("markFlag");
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

  it("draws the coax ETK (796К.2 КБ1, owner 15.10) on the coax cable, the TUT and the connector — not on a bare wire", () => {
    // Резка коаксиала идёт по карте провода CUT_WIRE_auto — рисунки берутся по виду операции генератора.
    const cut = defectOperationCode("CUT_WIRE_auto", "coaxCut");
    expect(defectContext(cut)).toBe("coax");
    expect(defectOperationCode("CUT_WIRE_auto", "cutWire")).toBe("CUT_WIRE_auto");
    expect(defectOperationCode("STRIP_COAX_auto", "coaxStrip")).toBe("STRIP_COAX_auto");
    const scenes = (code: string, names: string[]) => names.map((name) => defectSceneId(name, "", code));
    expect(scenes(cut, ["Надрезы, царапины, потертости", "Смятие (сплющивание) изоляции", "Задиры на торце", "Косой рез",
      "Вытягивание жил", "Деформация скрутки", "Заусенцы", "Длина вне допуска"]))
      .toEqual(["cxJacketNick", "cxJacketCrush", "cxFaceRagged", "cxSkew", "cxPulled", "cxFaceSplay", "cxBurr", "cxLength"]);
    expect(scenes("STRIP_COAX_auto", ["Надрезы, царапины или обрывы", "Скручивание или распушение", "Порезы, надрывы, трещины",
      "Смятие или вытягивание", "Остатки экрана", "Чрезмерное повреждение прядей", "Неровный срез оплетки (бахрома)",
      "Распушение оплетки", "Смятие изоляции", "Нарушение размеров ступеней"]))
      .toEqual(["cxCoreNick", "cxCoreSplay", "cxDielCut", "cxDielDeform", "cxBraidResidue", "cxBraidCut", "braidHairs",
        "braidHairs", "cxJacketCrush", "coaxSteps"]);
    expect(scenes("CUT_TUT_short", ["Рваный край / Заусенцы", "Продольные надрезы и микротрещины на торце", "Косой рез",
      "Сплющивание (слипание) трубки", "Длина вне допуска"]))
      .toEqual(["tubeRagged", "tubeSlit", "tubeSkew", "tubeFlat", "tubeLength"]);
    expect(scenes("INS_TUT", ["Неверный порядок трубок"])).toEqual(["tubeOrder"]);
    expect(scenes("HEAT_TYT_short", ["Термическое повреждение (расплавление) внешней оболочки самого кабеля"])).toEqual(["shrinkJacketMelt"]);
    expect(defectContext("INS_housing_COAX")).toBe("coaxAsm");
    expect(scenes("INS_housing_COAX", ["«Недосад» корпуса", "Деформация или изгиб пина при вводе в разъем",
      "Заворачивание проволочек экрана внутрь корпуса"])).toEqual(["cxSeat", "cxBent", "shieldSpread"]);
    expect(defectContext("PRS_SLEEVE_coax")).toBe("coaxCrimp");
    expect(scenes("PRS_SLEEVE_coax", ["Торчащие волоски экрана из-под краев гильзы", "«недожим» гильзы",
      "«пережим» или выдавливание облоя («ушей») металла между матрицами",
      "Деформация самого корпуса разъема из-за неправильного позиционирования в прессе"]))
      .toEqual(["cxSleeveHairs", "cxHexLoose", "cxHexFlash", "cxBodyDent"]);
    // Норма и брак различаются в самом месте дефекта.
    for (const id of ["cxJacketNick", "cxFaceSplay", "cxCoreNick", "tubeSlit", "cxHexLoose", "cxSeat"]) {
      const panels = [...defectSceneArt(id).matchAll(/<svg x="[^"]+" y="1" [^>]*>(.*?)<\/svg>/g)].map((match) => match[1]);
      expect(panels).toHaveLength(2);
      expect(panels[0]).not.toEqual(panels[1]);
    }
  });
});
