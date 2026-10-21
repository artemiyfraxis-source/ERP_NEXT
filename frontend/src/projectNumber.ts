/* The project number as the backend reads it (app/shared/project_number.py): same normalization, same «project from
   НАЗНАЧЕНИЕ». Used where a line has no project reference but its designation starts with one («708К.4.012»). */

const HOMOGLYPHS: Record<string, string> = { K: "К", C: "С", E: "Е", O: "О", P: "Р", A: "А", H: "Н", T: "Т", M: "М", B: "В", X: "Х", Y: "У" };

/** Spaces out, upper case, Latin look-alikes → Cyrillic, «2000К» ≡ «2000К.1». */
export function normalizeProjectNumber(value: string | null | undefined): string {
  const normalized = String(value ?? "").trim().toUpperCase().replace(/\s+/g, "").replace(/[KCEOPAHTMBXY]/g, (letter) => HOMOGLYPHS[letter]);
  if (/^[1-9]\d*(?:К[А-Я]?|МФ)(?:\.[А-Я]{1,3})?\.?$/.test(normalized)) return normalized.replace(/\.$/, "") + ".1";
  return normalized;
}

/** «708К.4 · КБ1 · Жгут» → «708К.4»; «708К.4.012» → «708К.4»; a designation that is not a project («Склад.003») → "". */
export function projectFromDesignation(value: string | null | undefined): string {
  const purpose = String(value ?? "").trim().replace(/[\s·,;:\-—]*КБ\s*\d+[\s\S]*$/i, "");
  const token = (purpose ? purpose.split(/[\s·,;]+/)[0] : "").replace(/\.\d{3,}$/, "");
  const number = token ? normalizeProjectNumber(token) : "";
  // Only a real project number counts: «708К.4», «2000К.1», «ВП-001».
  return /^[1-9]\d*(?:К[А-Я]?|МФ)/.test(number) || /^ВП-\d{3,}$/.test(number) ? number : "";
}
