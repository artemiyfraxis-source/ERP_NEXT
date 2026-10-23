// Рисунок «Норма | Брак» по коду строки СТО ФРАК-001-2026, ред. 2 (backend/data/sto-frak-001-2026.json, 320 строк).
// Таблица составлена вручную строка за строкой: у пункта «4.3.14 Высота обжима вне допуска» название в тексте ЭТК
// стоит после кода, а по одним словам название опознаётся неверно («Деформация» — у провода, у контакта, у корпуса).
// Рисунки — из библиотеки etkDefectArt.ts; одна сцена обслуживает несколько строк. Проверка полноты — etkDefectArt.test.ts.

export const STO_SCENES: Record<string, string> = {
  // 4.1 Резка
  "4.1.1": "length", "4.1.2": "insDamage", "4.1.3": "insDamage", "4.1.4": "insCrush", "4.1.5": "cxJacketCrush",
  "4.1.6": "braidHairs", "4.1.7": "coaxPin", "4.1.8": "insRagged", "4.1.9": "skew", "4.1.10": "pulled",
  "4.1.11": "unlay", "4.1.12": "burrWire", "4.1.13": "tubeFlat", "4.1.14": "tubeSkew", "4.1.15": "tubeRagged",
  "4.1.16": "tubeSlit", "4.1.17": "tubeLength", "4.1.18": "tubeShrunkEnd", "4.1.19": "fray", "4.1.20": "insMelt",
  "4.1.21": "fray",
  // 4.2 Зачистка и разделка
  "4.2.1": "nick", "4.2.2": "strandBreak", "4.2.3": "birdcage", "4.2.4": "unlay", "4.2.5": "insCrush",
  "4.2.6": "insDamage", "4.2.7": "insMelt", "4.2.8": "insRagged", "4.2.9": "fray", "4.2.10": "insResidue",
  "4.2.11": "stripLen", "4.2.12": "stripLen", "4.2.13": "nick", "4.2.14": "insDamage", "4.2.15": "insDamage",
  "4.2.16": "foil", "4.2.17": "braidHairs", "4.2.18": "braidHairs", "4.2.19": "coaxSteps", "4.2.20": "fray",
  "4.2.21": "drain", "4.2.22": "stripLen", "4.2.23": "insCrush", "4.2.24": "insDamage", "4.2.25": "foil",
  "4.2.26": "foil", "4.2.27": "pairs", "4.2.28": "cxCoreNick", "4.2.29": "cxCoreSplay", "4.2.30": "cxDielCut",
  "4.2.31": "cxDielDeform", "4.2.32": "cxBraidResidue", "4.2.33": "cxBraidCut", "4.2.34": "braidHairs",
  "4.2.35": "braidHairs", "4.2.36": "coaxSteps",
  // 4.3 Опрессовка (обжим) контактов и наконечников
  "4.3.1": "crimpMatch", "4.3.2": "bellmouth", "4.3.3": "brushShort", "4.3.4": "brush", "4.3.5": "strayCrimp",
  "4.3.6": "crimpIns", "4.3.7": "insGrip", "4.3.8": "insBite", "4.3.9": "insBite", "4.3.10": "banana",
  "4.3.11": "lance", "4.3.12": "housingCrack", "4.3.13": "carrierStrip", "4.3.14": "crimpHeight", "4.3.15": "pull",
  "4.3.16": "ohms", "4.3.17": "crimpMarks", "4.3.18": "tinInCrimp", "4.3.19": "lugDepth", "4.3.20": "brush",
  "4.3.21": "crimpIns", "4.3.22": "birdcage", "4.3.23": "housingCrack", "4.3.24": "housingCrack", "4.3.25": "insGrip",
  "4.3.26": "ferrule", "4.3.27": "crimpMarks", "4.3.28": "housingCrack", "4.3.29": "lugDepth", "4.3.30": "crimpMarks",
  "4.3.31": "housingGap", "4.3.32": "idc", "4.3.33": "nick", "4.3.34": "latch", "4.3.35": "cxSleeveHairs",
  "4.3.36": "cxSeat", "4.3.37": "cxHexLoose", "4.3.38": "cxHexFlash", "4.3.39": "cxBodyDent", "4.3.40": "spliceLoose",
  "4.3.41": "housingCrack", "4.3.42": "spliceStrands", "4.3.43": "spliceIns", "4.3.44": "spliceSize", "4.3.45": "lugDepth",
  "4.3.46": "housingCrack", "4.3.47": "cxBent", "4.3.48": "coaxPin", "4.3.49": "cxHexLoose", "4.3.50": "cxSleeveHairs",
  "4.3.51": "cxBodyDent", "4.3.52": "cxStrays", "4.3.53": "latch",
  // 4.4 Лужение и пайка
  "4.4.1.1": "tnExcess", "4.4.1.2": "tnLack", "4.4.1.3": "tnCold", "4.4.1.4": "tnLack", "4.4.1.5": "tnKink",
  "4.4.1.6": "stripLen", "4.4.1.7": "tnWick", "4.4.1.8": "tnSplay", "4.4.1.9": "flux",
  "4.4.2.1": "solderCold", "4.4.2.2": "solderLack", "4.4.2.3": "solderExcess", "4.4.2.4": "icicle", "4.4.2.5": "wicking",
  "4.4.2.6": "insMelt", "4.4.2.7": "birdcage", "4.4.2.8": "insInCup", "4.4.2.9": "gapIns", "4.4.2.10": "solderNoHook",
  "4.4.2.11": "tnLack", "4.4.2.12": "cupDepth", "4.4.2.13": "missing", "4.4.2.14": "solderCold", "4.4.2.15": "compBurn",
  "4.4.2.16": "connMelt", "4.4.2.17": "flux", "4.4.2.18": "flux", "4.4.2.19": "pinout", "4.4.2.20": "pad",
  "4.4.3.1": "bridge", "4.4.3.2": "loop", "4.4.3.3": "crimpHeight", "4.4.3.4": "coaxPin", "4.4.3.5": "solderCold",
  "4.4.3.6": "bridge", "4.4.3.7": "pairs", "4.4.3.8": "missing", "4.4.3.9": "insMelt", "4.4.3.10": "cupDepth",
  "4.4.3.11": "banana", "4.4.3.12": "housingGap", "4.4.3.13": "lugRing", "4.4.3.14": "wicking", "4.4.3.15": "insGrip",
  "4.4.3.16": "insCrush", "4.4.3.17": "solderExcess",
  "4.4.4.1": "solderNoHook", "4.4.4.2": "shrinkShift", "4.4.4.3": "missing", "4.4.4.4": "shrinkBurn", "4.4.4.5": "cxBraidCut",
  "4.4.4.6": "shieldSpread", "4.4.4.7": "shieldSpread",
  "4.4.5.1": "cxExcess", "4.4.5.2": "cxMelt", "4.4.5.3": "cxGap", "4.4.5.4": "cxBent", "4.4.5.5": "cxStrays",
  "4.4.5.6": "cxLack", "4.4.5.7": "flux", "4.4.5.8": "cxShift", "4.4.5.9": "cxShift", "4.4.5.10": "wicking",
  "4.4.5.11": "connMelt", "4.4.5.12": "connMelt", "4.4.5.13": "shellSeam", "4.4.5.14": "wicking", "4.4.5.15": "braidHairs",
  // 4.5 Термоусадка и изоляция
  "4.5.1": "shrinkLoose", "4.5.2": "shrinkBurn", "4.5.3": "shrinkShift", "4.5.4": "shrinkShift", "4.5.5": "shrinkWrinkle",
  "4.5.6": "cxBraidResidue", "4.5.7": "shrinkJacketMelt", "4.5.8": "shrinkJacketMelt", "4.5.9": "glue", "4.5.10": "missing",
  "4.5.11": "size", "4.5.12": "tubeOrder", "4.5.13": "tubeSlit", "4.5.14": "missing", "4.5.15": "tubeOrder",
  "4.5.16": "tape", "4.5.17": "tape", "4.5.18": "twist", "4.5.19": "fray", "4.5.20": "length",
  "4.5.21": "insDamage", "4.5.22": "cover", "4.5.23": "twist", "4.5.24": "ferrule", "4.5.25": "size",
  "4.5.26": "endCap", "4.5.27": "shrinkBurn", "4.5.28": "endCap",
  // 4.6 Маркировка
  "4.6.1": "markText", "4.6.2": "missing", "4.6.3": "markText", "4.6.4": "printBlur", "4.6.5": "tagText",
  "4.6.6": "tagShift", "4.6.7": "markShift", "4.6.8": "markShift", "4.6.9": "markShift", "4.6.10": "tagLoose",
  "4.6.11": "flagTear", "4.6.12": "tubeLen", "4.6.13": "autoSize", "4.6.14": "autoShift", "4.6.15": "autoCut",
  "4.6.16": "autoDirt", "4.6.17": "autoMix", "4.6.18": "tubeLoose", "4.6.19": "tubeHeat", "4.6.20": "ringOrder",
  "4.6.21": "ringMissing", "4.6.22": "insDamage", "4.6.23": "insDamage",
  // 4.7 Установка контактов в корпуса и фиксаторы, оконцовка вилок RJ
  "4.7.1": "dirt", "4.7.2": "missing", "4.7.3": "seat", "4.7.4": "pull", "4.7.5": "seat", "4.7.6": "lance",
  "4.7.7": "housingCrack", "4.7.8": "pinout", "4.7.9": "pinout", "4.7.10": "insDamage", "4.7.11": "housingGap",
  "4.7.12": "shieldSpread", "4.7.13": "pinch", "4.7.14": "seat", "4.7.15": "insGrip", "4.7.16": "seat",
  "4.7.17": "latch", "4.7.18": "pinout", "4.7.19": "rjDepth", "4.7.20": "rjBlade", "4.7.21": "clamp",
  "4.7.22": "drain", "4.7.23": "pairs",
  // 4.8 Коаксиальные разъёмы
  "4.8.1": "cxSeat", "4.8.2": "cxBent", "4.8.3": "shieldSpread", "4.8.4": "cxBraidStrands", "4.8.5": "housingGap",
  "4.8.6": "housingCrack", "4.8.7": "coaxPin", "4.8.8": "latch", "4.8.9": "pinch", "4.8.10": "cxExcess",
  "4.8.11": "cxClampLoose", "4.8.12": "cxNutOver", "4.8.13": "cxNutUnder", "4.8.14": "cxGasketMissing",
  "4.8.15": "cxBraidStrands", "4.8.16": "vswr",
  // 4.9 Вязка, укладка, стяжки, гофра на жгуте
  "4.9.1": "tieSpacing", "4.9.2": "tieTight", "4.9.3": "tieTail", "4.9.4": "tieLoose", "4.9.5": "bendRadius",
  "4.9.6": "cover", "4.9.7": "pinch", "4.9.8": "tieTight", "4.9.9": "corrCut", "4.9.10": "tieLoose",
  "4.9.11": "corrOpen", "4.9.12": "corrOpen", "4.9.13": "tieLoose",
  // 4.10 Свивка
  "4.10.1": "twistPitch", "4.10.2": "twist", "4.10.3": "twistPitch", "4.10.4": "twist", "4.10.5": "loop",
  "4.10.6": "tieTight", "4.10.7": "insDamage", "4.10.8": "pairs",
  // 4.11 Литьё, герметизация, плазменная обработка, ультразвуковая сварка
  "4.11.1": "time", "4.11.2": "mold", "4.11.3": "mold", "4.11.4": "mold", "4.11.5": "length", "4.11.6": "housingGap",
  "4.11.7": "insMelt", "4.11.8": "housingCrack", "4.11.9": "seal", "4.11.10": "seal", "4.11.11": "insDamage",
  "4.11.12": "time", "4.11.13": "wetting", "4.11.14": "missing", "4.11.15": "time", "4.11.16": "dirt",
  "4.11.17": "weldLow", "4.11.18": "weldHigh", "4.11.19": "weldSize", "4.11.20": "pull", "4.11.21": "housingCrack",
  "4.11.22": "birdcage", "4.11.23": "weldGap", "4.11.24": "pinout", "4.11.25": "insDamage",
  // 4.12 Подготовка жилы
  "4.12.1": "fold", "4.12.2": "fold", "4.12.3": "nick", "4.12.4": "birdcage", "4.12.5": "unlay", "4.12.6": "tinInCrimp",
};
