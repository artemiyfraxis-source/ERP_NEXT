import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { generatorRefs } from "./TcGenerator";

/*
 * Миграция 0165 (владелец 28–29.10: «каждая вкладка в БД — отдельная таблица»): наконечники — своя база «lug», виды
 * кабелей — «cab_coax» / «cab_multi» / «cab_lan», монтажные остаются в «prov». Генератор читает листы целиком
 * (`family=true`), поэтому контакты, наконечники и кабели доходят до формы, как до разделения. Сервер здесь отвечает как
 * настоящий: без `family` — строки одной базы.
 */
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const CONTACT = { id: "t1", tab: "ter", ter_kind: "contact", fields: { terArticle: "DF1B-2428SCFA", mm2From: "0,08", mm2To: "0,2" } };
const LUG = { id: "l1", tab: "lug", ter_kind: "lug", fields: { terComponent: "НШВИ", terArticle: "НШВИ 0.25-8", mm2From: "0,2", mm2To: "0,2" } };
const HOOKUP = { id: "p1", tab: "prov", cable_kind: "wire", fields: { "Марка": "МГТФ 0,12", "Число жил": "1" } };
const COAX = { id: "c1", tab: "cab_coax", cable_kind: "coax", fields: { "Марка": "RG-316", "Число жил": "1", "Экран": "оплётка" } };
const BASES: Record<string, { family: string[]; records: unknown[] }> = {
  ter: { family: ["ter", "lug"], records: [CONTACT, LUG] },
  prov: { family: ["prov", "cab_coax", "cab_multi", "cab_lan"], records: [HOOKUP, COAX] },
};
let asked: string[] = [];

beforeEach(() => {
  asked = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [] });
    if (path.startsWith("/api/references/tech-operations?")) {
      asked.push(path);
      const params = new URLSearchParams(path.split("?")[1]);
      const tab = params.get("tab") || "";
      const base = BASES[tab];
      const records = !base ? [] : params.get("family") === "true"
        ? base.records.filter((r) => base.family.includes((r as { tab: string }).tab))
        : base.records.filter((r) => (r as { tab: string }).tab === tab);
      return json({ metadata: null, columns: [], records });
    }
    return json({ metadata: null, columns: [], series: [], records: [] });
  });
});
afterEach(() => vi.restoreAllMocks());

it("lugs and a coax cable come from their own bases: contact/lug lookup, free-end lugs and cable kinds as before the split", async () => {
  const refs = await generatorRefs();
  expect(asked).toContain("/api/references/tech-operations?tab=ter&limit=5000&family=true");
  expect(asked).toContain("/api/references/tech-operations?tab=prov&limit=5000&family=true");
  // БД.ТЕР целиком: контакт ищется среди всех строк листа, наконечник — в «Свободных концах».
  expect(refs.terRecords.map((r) => r.article)).toEqual(["DF1B-2428SCFA", "НШВИ 0.25-8"]);
  expect((refs.lugRecords ?? []).map((r) => [r.article, r.type])).toEqual([["НШВИ 0.25-8", "НШВИ"]]);
  // БД.ПРОВ целиком: монтажный провод и коаксиальный кабель из своей базы, вид — из базы строки.
  expect((refs.cableRecords ?? []).map((r) => [r.mark, r.kind, r.screen])).toEqual([["МГТФ 0,12", "wire", ""], ["RG-316", "coax", "оплётка"]]);
});
